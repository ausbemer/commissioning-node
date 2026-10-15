"""status-report — publishes this node's tailnet status from each overlay snapshot.

Read-only (§11 rung 1). It also reports the detected site networks and the routes the saved
config wants (`auto` expanded, ADR-0005 A3), which the operator confirms through the writer.
"""
from __future__ import annotations

from interfaces import TAILNET_STATUS, TailnetStatus, __version__ as CONTRACT_VERSION

from ..interface import OVERLAY_SNAPSHOT, ConfigView, OverlaySnapshot, resolve_routes


class StatusReport:
    name = "status-report"
    subscriptions = [OVERLAY_SNAPSHOT]

    def __init__(self, config: ConfigView | None = None) -> None:
        self._config = config

    def handle(self, topic: str, payload: dict) -> list[tuple[str, dict]]:
        snap = OverlaySnapshot.model_validate(payload)
        status = TailnetStatus.stamped(
            CONTRACT_VERSION,
            hostname=snap.hostname,
            connected=snap.connected,
            addresses=snap.addresses,
            served_routes=snap.served_routes,
            tags=snap.tags,
            state=snap.state,
            dns_name=snap.dns_name,
            site_networks=snap.site_networks,
            # The same resolution the writer applies (ADR-0005 A3).
            wanted_routes=resolve_routes(self._config.get("advertise_routes"), snap.site_networks)
            if self._config is not None else [],
            advertised_routes=snap.join_record.routes if snap.join_record else [],
        )
        return [(TAILNET_STATUS, status.model_dump())]
