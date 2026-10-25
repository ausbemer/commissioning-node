"""dashboard inward models.

`DashboardState` is the shared live-state object between the collector feature (writes)
and the HTTP core (reads) — "features meet in interface objects" (spec §4). Core
constructs it and hands it to both. It is bounded latest-value-per-key state (§8.8).
"""
from __future__ import annotations

import copy
import threading

#: Kinds of entry the state holds, each keyed by its entity id.
KINDS = ("nodes", "tailnet", "tailnet_apply", "cameras", "streams", "site_hosts", "site_scan", "site_request",
         "node_update")
#: Bound per kind (§8.8). PROVISIONAL — unmeasured (TODO.md, §8.11).
MAX_PER_KIND = 256


class DashboardState:
    """Thread-safe latest-value store: kind -> key -> payload dict, plus wire versions."""

    def __init__(self, max_per_kind: int = MAX_PER_KIND) -> None:
        self._lock = threading.Lock()
        self._max = max_per_kind
        self._data: dict[str, dict[str, dict]] = {kind: {} for kind in KINDS}
        self._wire: dict[str, str] = {}

    def put(self, kind: str, key: str, value: dict) -> None:
        with self._lock:
            table = self._data[kind]
            table.pop(key, None)                 # re-insert so the newest is last
            if len(table) >= self._max:
                table.pop(next(iter(table)))     # evict the least recently updated
            table[key] = value

    def note_wire(self, topic: str, schema_version: str) -> None:
        """Record the schema_version last seen on the wire for a topic (§8.5)."""
        with self._lock:
            self._wire[topic] = schema_version

    def snapshot(self) -> dict:
        """A deep copy, safe to read and serialize outside the lock."""
        with self._lock:
            return copy.deepcopy({**self._data, "wire": self._wire})
