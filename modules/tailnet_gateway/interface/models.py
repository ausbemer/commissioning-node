"""tailnet-gateway inward models — vendor-neutral, so features never see Tailscale's JSON."""
from __future__ import annotations

from interfaces import Shape

#: Inward topic: core hands each fresh overlay reading to features under this name.
#: Never published on the bus.
OVERLAY_SNAPSHOT = "tailnet-gateway/overlay-snapshot"


class OverlaySnapshot(Shape):
    """One reading of the overlay network's state, as the adapter understood it."""

    hostname: str
    #: True when logged in and the overlay link is up.
    connected: bool
    #: This node's overlay addresses.
    addresses: list[str] = []
    #: Subnet routes (CIDR) this node is currently serving.
    served_routes: list[str] = []
