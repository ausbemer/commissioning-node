"""tailnet-gateway inward models — vendor-neutral, so features never see Tailscale's JSON."""
from __future__ import annotations

from interfaces import Shape

#: Inward topic: core hands each fresh overlay reading to features under this name.
#: Never published on the bus.
OVERLAY_SNAPSHOT = "tailnet-gateway/overlay-snapshot"


class OverlayPeer(Shape):
    """Another node on the overlay, as the adapter understood it."""

    addresses: list[str] = []
    #: Unique name on the overlay (MagicDNS name for Tailscale), or the OS host name.
    node_name: str
    #: Owning user's login; None for a tagged (non-user-owned) node.
    login: str | None = None
    tags: list[str] = []
    #: The address (no port) this node reaches the peer at directly right now, e.g. a site-LAN
    #: address; None when the peer is relayed or the overlay doesn't say (0.7.0, ADR-0007).
    endpoint: str | None = None


class OverlaySnapshot(Shape):
    """One reading of the overlay network's state, as the adapter understood it."""

    hostname: str
    #: True when logged in and the overlay link is up.
    connected: bool
    #: This node's overlay addresses.
    addresses: list[str] = []
    #: Subnet routes (CIDR) this node is currently serving.
    served_routes: list[str] = []
    #: ACL tags this node carries. Empty when untagged, or when the overlay doesn't say.
    tags: list[str] = []
    #: The overlay's state word (tailscaled's BackendState, e.g. "NeedsLogin"); "" unknown.
    state: str = ""
    #: This node's name on the overlay (MagicDNS FQDN for Tailscale, no trailing dot); ""
    #: when the overlay reports none (0.8.0, ADR-0008).
    dns_name: str = ""
    #: Stamped by core: IPv4 networks the node is on now (`SiteNetworkSource`, ADR-0005 A3).
    site_networks: list[str] = []
    #: Stamped by core: the writer's record of the last join, None when there is none.
    join_record: "JoinRecord | None" = None
    #: Every other node visible on the overlay. Empty when disconnected.
    peers: list[OverlayPeer] = []
    #: Stamped by core, not the adapter: when the reading was taken (Unix seconds) and the
    #: tick cadence it rides (§11 rung 11). None only in adapter-level tests.
    observed_at: float | None = None
    period_s: float | None = None


class JoinRecord(Shape):
    """What the operator confirmed when they joined this node to the site tailnet (ADR-0005
    A2). Written by core's worker after a successful join, updated by Rename and Routes,
    deleted BEFORE a Leave runs. Its presence is the intent auto-rejoin acts on, and
    auto-rejoin applies exactly these values, never what is merely saved since. Site-scoped:
    teardown wipes it."""

    joined_at: float
    #: The routes the writer last advertised.
    routes: list[str] = []
    #: The hostname and tags the confirmed join applied.
    hostname: str | None = None
    tags: list[str] = []
    #: SHA-256 of the auth key file the confirmed join used. Auto-rejoin refuses a key that
    #: no longer matches; None (a record from before this field) never auto-rejoins.
    key_sha256: str | None = None


OverlaySnapshot.model_rebuild()
