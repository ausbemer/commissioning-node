"""state-collector — keeps the latest card, tailnet status and apply result, camera and stream
per entity, and the last site scan request, its hosts and its result (ADR-0007; in memory
only, Safety 6). The request is observed, never acted on: the map shows a scan as running.

Terminal consumer (§8.2): it deserializes through the pinned contract models and NEVER
re-publishes. If it ever must, revisit — forwarders pass raw payloads.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from pydantic import ValidationError

from interfaces import (
    CAMERA_ANNOUNCED,
    NODE_UPDATE_RESULT,
    NODE_UPDATE_STATUS,
    SITE_HOSTS,
    SITE_SCAN,
    SITE_SCAN_RESULT,
    STREAM_ANNOUNCED,
    SYSTEM_CARD,
    TAILNET_APPLY_RESULT,
    TAILNET_STATUS,
    CameraAnnouncement,
    NodeCard,
    NodeUpdateResult,
    NodeUpdateStatus,
    SiteHosts,
    SiteScan,
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
    SITE_SCAN: ("site_request", SiteScan, None),
    NODE_UPDATE_STATUS: ("node_update", NodeUpdateStatus, None),
    NODE_UPDATE_RESULT: ("node_update_result", NodeUpdateResult, None),
}
#: Kinds stored with the wall-clock time they arrived (`received_at`), for a payload that
#: carries no time of its own: the map says since when a scan has run, and the overview judges
#: the update status, a renewed level, by when it last arrived (ADR-0009).
_STAMPED = {"site_request", "node_update"}


class StateCollector:
    name = "state-collector"
    subscriptions = list(_ROUTES)

    def __init__(self, state: DashboardState, now: Callable[[], float] = time.time) -> None:
        self._state, self._now = state, now

    def handle(self, topic: str, payload: dict) -> list[tuple[str, dict]]:
        self._state.note_wire(topic, str(payload.get("schema_version", "missing")))
        kind, model, key = _ROUTES[topic]
        try:
            item = model.model_validate(payload)
        except ValidationError as exc:
            log.warning("ignoring malformed %s: %s", topic, exc.errors(include_url=False))
            return []
        value = item.model_dump()
        if kind in _STAMPED:
            value["received_at"] = self._now()
        self._state.put(kind, getattr(item, key) if key else SELF, value)
        return []
