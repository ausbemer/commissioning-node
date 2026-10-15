"""The operator doors and the one ownership check (ADR-0003 §1, §4; spec §11 rungs 10, 13).

Pure functions; the HTTP core does the I/O and calls them for every request.

- `classify()` sorts a request on the 8080 listener by the LOCAL address it arrived on:
  loopback is the *local* door; anything else must resolve to a tailnet peer, or it is
  refused (403, or 503 while the peer table is stale). Never by address range (a site LAN
  can use CGNAT space too).
- `host_allowed()` refuses a Host header that is not this node's address (or an exact
  name the core vouches for), so a DNS-rebinding page in a tailnet user's browser cannot
  read or write through that user's identity.
- `name_redirect()` sends a GET that names the node by its MagicDNS name to the node's
  tailnet address instead (ADR-0008). The name is never served, only redirected away from,
  so a rebinding page gains nothing from it.
- `authorize()` is the ONLY gate in front of a write: authenticated caller, same-origin
  request, the fallback session's CSRF token, and rung-4 confirmation. Every door that
  authenticates may write (decision D2).
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Iterable, Mapping

from .actions import Action
from .identity import PeerDirectory, Unresolved, parse_address

LOCAL = "local"
TAILNET = "tailnet"
FALLBACK = "fallback"
DOORS = (LOCAL, TAILNET, FALLBACK)

#: Appended to a may-disturb warning when the caller is on the door the action can cut.
TAILNET_RECOVERY = "This may disconnect you; recovery is the LAN fallback on :8443."


@dataclass(frozen=True)
class Caller:
    door: str
    #: Who a change is recorded against: "local", a tailnet login or node name, or
    #: "admin@fallback".
    label: str
    #: The fallback session's CSRF token; None on the 8080 doors (no cookies there).
    csrf: str | None = None


@dataclass(frozen=True)
class Refusal:
    status: int
    #: Stable code, e.g. "stale", "unknown", "bad-host", "unauthenticated", "rate-limited".
    reason: str
    message: str


@dataclass(frozen=True)
class Decision:
    allowed: bool
    status: int
    reason: str
    message: str


def classify(local_addr: str, peer_addr: str, peers: PeerDirectory) -> Caller | Refusal:
    """The door of a request on the 8080 listener, and who is calling."""
    local = parse_address(local_addr)
    if local is None:
        return Refusal(400, "invalid", f"{local_addr!r} is not an IP address")
    if local.is_loopback:
        return Caller(LOCAL, "local")
    who = peers.resolve(peer_addr)
    if isinstance(who, Unresolved):
        return Refusal(503 if who.reason == "stale" else 403, who.reason, who.message)
    return Caller(TAILNET, who.label)


def _host_name(host_header: str) -> str:
    """The host part of a Host header: IPv6 brackets and the port removed, lowercased."""
    h = host_header.strip().lower()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else ""
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def host_allowed(host_header: str | None, local_addr: str, node_names: Iterable[str] = ()) -> bool:
    """True if Host names this node: the address the request arrived on, `localhost` on
    loopback, or exactly one of `node_names`.

    Names are where rebinding lives. A bare host name resolves through LLMNR, mDNS or the
    site's DNS, all of which a site-LAN attacker can answer; so can a hostile resolver for
    `*.ts.net` when the client is not using MagicDNS. So `node_names` must hold only names
    whose resolution the attacker cannot control. The core passes none: a MagicDNS name is
    redirected to the tailnet IP (`name_redirect()`, ADR-0008), never served."""
    if not host_header:
        return False
    name = _host_name(host_header)
    local = parse_address(local_addr)
    as_ip = parse_address(name) if name else None
    if as_ip is not None:
        return local is not None and (as_ip == local or (as_ip.is_loopback and local.is_loopback))
    if name == "localhost":
        return local is not None and local.is_loopback
    return name in {n.lower() for n in node_names if n}


def name_redirect(host_header: str | None, local_addr: str, port: int, path: str,
                  status: Mapping[str, object]) -> str | None:
    """Where to send a GET whose Host is this node's MagicDNS name (ADR-0008), or None.

    `status` is the latest `TailnetStatus` as a dict. The name matches exactly: the FQDN in
    `dns_name` or its first label (the short name MagicDNS search domains resolve). The
    target is the node's tailnet IPv4, falling back to the address the request arrived on,
    and only when that address is one of the node's tailnet `addresses` (rule 13: never a
    site-LAN address). The page itself is never served on the name: a rebinding page that
    gets this answer only learns a URL on another origin, which it can neither read nor post
    to. So a stale or missing status costs nothing but the convenience: no name, no
    redirect, and the caller gets the usual 421."""
    fqdn = str(status.get("dns_name") or "").rstrip(".").lower()
    if not host_header or not fqdn:
        return None
    if _host_name(host_header).rstrip(".") not in {fqdn, fqdn.split(".", 1)[0]}:
        return None
    local = parse_address(local_addr)
    tailnet = [a for a in (parse_address(str(x)) for x in status.get("addresses") or []) if a]
    if local is None or local not in tailnet:
        return None
    target = next((a for a in tailnet if a.version == 4), local)
    where = f"[{target}]" if target.version == 6 else str(target)
    if not path.startswith("/") or path.startswith("//"):
        path = "/"
    return f"http://{where}:{port}{path}"


def same_origin(origin: str | None, scheme: str, host_header: str | None) -> bool:
    """True if the browser's Origin is this very listener. Browsers send Origin on every
    POST; a non-browser client must send it too."""
    if not origin or not host_header:
        return False
    return origin.strip().lower() == f"{scheme}://{host_header.strip().lower()}"


def authorize(caller: Caller | None, action: Action, form: Mapping[str, str],
              same_origin_ok: bool) -> Decision:
    """The single gate in front of every write (ADR-0003 §4)."""
    if caller is None or caller.door not in DOORS:
        return Decision(False, 401, "unauthenticated", "log in first")
    if not same_origin_ok:
        return Decision(False, 403, "cross-origin",
                        "refused: the request did not come from this dashboard (Origin/Host)")
    if caller.door == FALLBACK:
        supplied = form.get("csrf", "")
        if not caller.csrf or not hmac.compare_digest(caller.csrf.encode(), supplied.encode()):
            return Decision(False, 403, "csrf", "refused: missing or stale form token; reload the page")
    if action.may_disturb and form.get("confirm") != "yes":
        warning = action.may_disturb
        if caller.door == TAILNET:
            warning = f"{warning} {TAILNET_RECOVERY}"
        return Decision(False, 409, "unconfirmed", f"{warning} Confirm to proceed.")
    return Decision(True, 200, "ok", f"{action.name} by {caller.label} via the {caller.door} door")
