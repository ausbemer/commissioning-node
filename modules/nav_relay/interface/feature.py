"""Reference Feature protocol — copy into each module's `interface/feature.py`.

This is the inward contract between a module's `core/` and its features (spec §4).
The core discovers features, routes matching messages to them, and publishes whatever
they return. Features implement this protocol and depend ONLY on `interface/` — never
on `core/` internals or on another feature.

A feature is intentionally tiny: a name, the topics it wants, and a pure-ish `handle`
that turns an incoming (topic, payload) into zero or more outgoing (topic, payload)
publications. Keeping `handle` free of I/O makes features trivial to unit-test.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Feature(Protocol):
    #: Unique, stable identifier — used by the registry and as a capability flag (spec §5).
    name: str

    #: Topic patterns this feature wants routed to it (wildcards allowed, e.g. "sensor/*").
    subscriptions: list[str]

    def handle(self, topic: str, payload: dict) -> list[tuple[str, dict]]:
        """React to one message.

        Receives the raw dict payload (terminal consumers deserialize through their own
        pinned models — spec §8.2). Returns a list of (topic, payload) pairs to publish;
        return an empty list to consume without emitting anything.
        """
        ...
