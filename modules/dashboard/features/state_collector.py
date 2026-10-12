"""state-collector — keeps the latest card, tailnet status, camera and stream per entity.

Terminal consumer (§8.2): it deserializes through the pinned contract models and NEVER
re-publishes. If it ever must, revisit — forwarders pass raw payloads.
"""
from __future__ import annotations

import logging

from pydantic import ValidationError

from interfaces import (
    CAMERA_ANNOUNCED,
    STREAM_ANNOUNCED,
    SYSTEM_CARD,
    TAILNET_STATUS,
    CameraAnnouncement,
    NodeCard,
    StreamAnnouncement,
    TailnetStatus,
)

from ..interface import MODULE, DashboardState

log = logging.getLogger(MODULE)

#: topic -> (state kind, model, key field)
_ROUTES = {
    SYSTEM_CARD: ("nodes", NodeCard, "hostname"),
    TAILNET_STATUS: ("tailnet", TailnetStatus, "hostname"),
    CAMERA_ANNOUNCED: ("cameras", CameraAnnouncement, "camera_id"),
    STREAM_ANNOUNCED: ("streams", StreamAnnouncement, "camera_id"),
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
        self._state.put(kind, getattr(item, key), item.model_dump())
        return []
