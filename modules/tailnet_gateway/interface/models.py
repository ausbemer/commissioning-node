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
    #: Every other node visible on the overlay. Empty when disconnected.
    peers: list[OverlayPeer] = []
    #: Stamped by core, not the adapter: when the reading was taken (Unix seconds) and the
    #: tick cadence it rides (§11 rung 11). None only in adapter-level tests.
    observed_at: float | None = None
    period_s: float | None = None
