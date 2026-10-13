"""The operator doors and the one ownership check (ADR-0003 §1, §4; spec §11 rungs 10, 13).

Pure functions; the HTTP core does the I/O and calls them for every request.

- `classify()` sorts a request on the 8080 listener by the LOCAL address it arrived on:
  loopback is the *local* door; anything else must resolve to a tailnet peer, or it is
  refused (403, or 503 while the peer table is stale). Never by address range (a site LAN
  can use CGNAT space too).
- `host_allowed()` refuses a Host header that does not name this node, so a DNS-rebinding
  page in a tailnet user's browser cannot read or write through that user's identity.
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


def host_allowed(host_header: str | None, local_addr: str, node_names: Iterable[str]) -> bool:
    """True if Host names this node: the address the request arrived on, `localhost` on
    loopback, one of its host names, or `<name>.<tailnet>.ts.net` for one of them."""
    if not host_header:
        return False
    name = _host_name(host_header)
    local = parse_address(local_addr)
    as_ip = parse_address(name) if name else None
    if as_ip is not None:
        return local is not None and (as_ip == local or (as_ip.is_loopback and local.is_loopback))
    if name == "localhost":
        return local is not None and local.is_loopback
    names = {n.lower() for n in node_names if n}
    if name in names:
        return True
    # MagicDNS: only Tailscale answers for ts.net, so a page cannot rebind one to this node.
    return name.endswith(".ts.net") and name.split(".", 1)[0] in names


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
