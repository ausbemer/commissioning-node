"""state-collector — keeps the latest card, tailnet status and apply result, camera and stream
per entity, and the last site scan and its result (ADR-0007; in memory only, Safety 6).

Terminal consumer (§8.2): it deserializes through the pinned contract models and NEVER
re-publishes. If it ever must, revisit — forwarders pass raw payloads.
"""
from __future__ import annotations

import logging

from pydantic import ValidationError

from interfaces import (
    CAMERA_ANNOUNCED,
    SITE_HOSTS,
    SITE_SCAN_RESULT,
    STREAM_ANNOUNCED,
    SYSTEM_CARD,
    TAILNET_APPLY_RESULT,
    TAILNET_STATUS,
    CameraAnnouncement,
    NodeCard,
    SiteHosts,
    SiteScanResult,
    StreamAnnouncement,
    TailnetApplyResult,
    TailnetStatus,
)

from ..interface import MODULE, DashboardState

log = logging.getLogger(MODULE)

#: The key for an entry there is only ever one of: this node's own tailnet status, the last
#: tailnet apply result, the last site scan's hosts and the last scan result (ADR-0007). Not the hostname: a rename would leave the old name standing.
SELF = "self"

#: topic -> (state kind, model, key field, or None for the SELF key)
_ROUTES = {
    SYSTEM_CARD: ("nodes", NodeCard, "hostname"),
    TAILNET_STATUS: ("tailnet", TailnetStatus, None),
    TAILNET_APPLY_RESULT: ("tailnet_apply", TailnetApplyResult, None),
    CAMERA_ANNOUNCED: ("cameras", CameraAnnouncement, "camera_id"),
    STREAM_ANNOUNCED: ("streams", StreamAnnouncement, "camera_id"),
    SITE_HOSTS: ("site_hosts", SiteHosts, None),
    SITE_SCAN_RESULT: ("site_scan", SiteScanResult, None),
}


class StateCollector:
    name = "state-collector"
    subscriptions = list(_ROUTES)

    def __init__(self, state: DashboardState) -> None:
        self._state = state

    def handle(self, topic: str, payload: dict) -> list[tuple[str, dict]]:
        self._state.note_wire(topic, str(payload.get("schema_version", "missing")))
        kind, model, key = _ROUTES[topic]
        try:
            item = model.model_validate(payload)
        except ValidationError as exc:
            log.warning("ignoring malformed %s: %s", topic, exc.errors(include_url=False))
            return []
        self._state.put(kind, getattr(item, key) if key else SELF, item.model_dump())
        return []
