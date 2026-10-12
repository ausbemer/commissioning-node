"""status-report — publishes this node's tailnet status from each overlay snapshot.

Read-only (§11 rung 1). Auto-configuration (detect the site subnet, propose routes for
operator confirmation, rung 2) is a later feature in this module.
"""
from __future__ import annotations

from interfaces import TAILNET_STATUS, TailnetStatus, __version__ as CONTRACT_VERSION

from ..interface import OVERLAY_SNAPSHOT, OverlaySnapshot


class StatusReport:
    name = "status-report"
    subscriptions = [OVERLAY_SNAPSHOT]

    def handle(self, topic: str, payload: dict) -> list[tuple[str, dict]]:
        snap = OverlaySnapshot.model_validate(payload)
        status = TailnetStatus.stamped(
            CONTRACT_VERSION,
            hostname=snap.hostname,
            connected=snap.connected,
            addresses=snap.addresses,
            served_routes=snap.served_routes,
        )
        return [(TAILNET_STATUS, status.model_dump())]
