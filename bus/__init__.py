"""In-process message bus — the Stage 0 spine.

Deliberately mirrors broker pub/sub semantics so that swapping in a real broker
(MQTT / Redis / NATS / Kafka) at Stage 1 changes this module's internals and
nothing above it. Payloads on the bus are plain dicts (the raw wire form):
publishers serialize once, and only terminal consumers deserialize through their
pinned models (spec §8.2 — the forwarding trap).
"""
from __future__ import annotations

import fnmatch
import logging
import threading
from collections import defaultdict
from typing import Callable

log = logging.getLogger("bus")

Handler = Callable[[str, dict], None]


class Bus:
    """Topic-based publish/subscribe with wildcard support ('a/*' matches 'a/b')."""

    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._lock = threading.RLock()

    def subscribe(self, topic_pattern: str, handler: Handler) -> None:
        with self._lock:
            self._subs[topic_pattern].append(handler)
        log.debug("subscribed %s -> %s", topic_pattern, getattr(handler, "__name__", handler))

    def unsubscribe(self, topic_pattern: str, handler: Handler) -> None:
        with self._lock:
            if handler in self._subs.get(topic_pattern, []):
                self._subs[topic_pattern].remove(handler)

    def publish(self, topic: str, payload: dict) -> int:
        """Deliver payload (a dict — the raw wire form) to all matching subscribers.

        Returns the number of handlers invoked. A handler exception is logged and
        isolated: one bad consumer never breaks delivery to the others.
        """
        if "schema_version" not in payload:
            # Spec §8.5: every envelope carries its schema version on the wire.
            log.warning("payload on %s missing schema_version — publisher bug", topic)
        with self._lock:
            matches = [
                h
                for pattern, handlers in self._subs.items()
                for h in handlers
                if fnmatch.fnmatch(topic, pattern)
            ]
        delivered = 0
        for handler in matches:
            try:
                handler(topic, payload)
                delivered += 1
            except Exception:  # noqa: BLE001 — isolation is the point
                log.exception("handler %s failed on %s", getattr(handler, "__name__", handler), topic)
        return delivered


# A single shared instance; main.py wires modules to it.
bus = Bus()
