"""peer-directory — keeps the tailnet peer table the HTTP core resolves callers against.

Terminal consumer (§8.2): it deserializes through the pinned contract model and NEVER
re-publishes. A malformed snapshot is dropped, so the last good table simply ages out
(ADR-0003 §2: identity fails toward deny when the feed stops).
"""
from __future__ import annotations

import logging

from pydantic import ValidationError

from interfaces import TAILNET_PEERS, TailnetPeers

from ..interface import MODULE, DashboardState, PeerDirectory

log = logging.getLogger(MODULE)


class PeerDirectoryFeed:
    name = "peer-directory"
    subscriptions = [TAILNET_PEERS]

    def __init__(self, directory: PeerDirectory, state: DashboardState) -> None:
        self._directory = directory
        self._state = state

    def handle(self, topic: str, payload: dict) -> list[tuple[str, dict]]:
        self._state.note_wire(topic, str(payload.get("schema_version", "missing")))
        try:
            snapshot = TailnetPeers.model_validate(payload)
        except ValidationError as exc:
            log.warning("ignoring malformed %s: %s", topic, exc.errors(include_url=False))
            return []
        self._directory.update(snapshot)
        return []
