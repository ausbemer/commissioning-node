"""tailnet-gateway's config: the fields it declares, and the seam it reads them through.

ADR-0004. The composition root builds the config registry from `CONFIG_FIELDS` and, with the
writer (ADR-0003 step 6), hands core an object satisfying `ConfigView`. This module never
imports the registry. Nothing reads these fields yet: they are declared ahead of the writer
(§8.4 merge early), so the operator can stage a site's values.

All three are site-scoped: each belongs to one site's tailnet and is wiped at teardown.
"""
from __future__ import annotations

import ipaddress
from typing import Protocol, runtime_checkable

from interfaces import ConfigField

from . import MODULE

HOSTNAME = ConfigField(
    module=MODULE, name="hostname", kind="str", label="Tailscale hostname",
    help="This node's name on the site's tailnet. One DNS label: a-z, 0-9 and '-'.",
    max_len=63, pattern=r"[a-z0-9]([a-z0-9-]*[a-z0-9])?")
TAGS = ConfigField(
    module=MODULE, name="tags", kind="str_list", default=[], label="Tailscale tags",
    help="ACL tags to advertise, e.g. tag:commissioning. Separate with spaces or commas.",
    max_items=16, max_len=64, pattern=r"tag:[a-z0-9-]+")
AUTH_KEY = ConfigField(
    module=MODULE, name="auth_key", kind="str", secret=True, label="Tailscale auth key",
    help="A tag-locked OAuth client secret, or a reusable, ephemeral, tagged auth key. It is "
         "kept so the node can rejoin after a power cycle (ADR-0005 A1, A2). An OAuth secret "
         "may end in ?preauthorized=true (and &ephemeral=true) so a rejoin needs no approval.",
    # Tailscale's OAuth-secret options, each at most once (ADR-0005 amendment); baseURL is
    # not accepted.
    pattern=r"tskey-[A-Za-z0-9-]+(\?(ephemeral|preauthorized)=(true|false)"
            r"(&(?!\2)(ephemeral|preauthorized)=(true|false))?)?")
AUTO = "auto"
ADVERTISE_ROUTES = ConfigField(
    module=MODULE, name="advertise_routes", kind="str_list", default=[AUTO],
    label="Advertised routes",
    help="IPv4 networks to offer the tailnet, e.g. 192.168.10.0/24. 'auto' means the networks "
         "the node is on now. Leave empty to advertise none. The tailnet's admin approves them.",
    max_items=8, max_len=18, pattern=r"auto|(\d{1,3}\.){3}\d{1,3}/\d{1,2}")
AUTO_REJOIN = ConfigField(
    module=MODULE, name="auto_rejoin", kind="bool", default=True, label="Rejoin automatically",
    help="If the node drops off the tailnet it joined (power cycle, ephemeral removal), rejoin "
         "with the saved key. Never joins a tailnet the operator has not joined first.")

#: Every field this module declares (ADR-0004 §4).
CONFIG_FIELDS = [HOSTNAME, TAGS, AUTH_KEY, ADVERTISE_ROUTES, AUTO_REJOIN]

#: Routes may never fall inside the tailnet's own address space.
TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")


def route_problem(cidr: str) -> str | None:
    """Why `cidr` can't be advertised, or None. The one rule for a route (ADR-0005 A3)."""
    try:
        net = ipaddress.ip_network(cidr, strict=True)
    except ValueError:
        return f"{cidr} is not a network address (e.g. 192.168.10.0/24)"
    if net.version != 4:
        return f"{cidr} is not IPv4"
    if net.overlaps(TAILNET_V4):
        return f"{cidr} overlaps the tailnet's own 100.64.0.0/10"
    if net.prefixlen < 8 or net.prefixlen > 31:
        return f"{cidr} must be between /8 and /31"
    return None


def resolve_routes(saved: list[str] | None, site_networks: list[str]) -> list[str]:
    """The routes the saved value asks for: "auto" expands to the detected networks; invalid
    entries are dropped; order kept, duplicates removed. The ONE resolution: status-report
    publishes it and the writer applies it."""
    out: list[str] = []
    for item in [AUTO] if saved is None else saved:
        for cidr in site_networks if item == AUTO else [item]:
            if cidr not in out and route_problem(cidr) is None:
                out.append(cidr)
    return out


@runtime_checkable
class ConfigView(Protocol):
    """Read access to this module's own fields. A secret has no readable value: only its
    file path, for `tailscale up --auth-key=file:<path>` (ADR-0003 §5)."""

    def get(self, name: str) -> object: ...

    def is_set(self, name: str) -> bool: ...

    def secret_path(self, name: str) -> str | None: ...

    def clear_secret(self, name: str) -> object: ...
