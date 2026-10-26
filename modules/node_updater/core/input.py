"""Subscriptions — what this module reads (spec §4). I/O only.

Subscribes once per pattern the enabled features asked for. On every tick it first drains the
check worker's finished outcomes and routes them to features on `CHECK_OUTCOME`, so a check's
result is published on the tick after it finishes, from the bus thread. It then reads root's
result record, when there is a source for it, and routes that on `RESULT_READING` (ADR-0009).
"""
from __future__ import annotations

from typing import Callable

from interfaces import SYSTEM_TICK

from ..interface import CHECK_OUTCOME, RESULT_READING, CheckSlot, ResultSource
from .output import Output
from .registry import Registry

Subscribe = Callable[[str, Callable[[str, dict], None]], None]


def attach(registry: Registry, subscribe: Subscribe, output: Output, slot: CheckSlot,
           results: ResultSource | None = None) -> None:
    def drain() -> None:
        for outcome in slot.drain():
            output.publish_all(registry.route(CHECK_OUTCOME, outcome.model_dump()))
        record = results.read() if results is not None else None
        if record is not None:
            output.publish_all(registry.route(RESULT_READING, record.model_dump()))

    # The inward topics are routed only from core, never subscribed on the bus: nothing
    # published there can pose as a finished check or as root's result.
    patterns = sorted(({p for f in registry.enabled() for p in f.subscriptions} | {SYSTEM_TICK})
                      - {CHECK_OUTCOME, RESULT_READING})
    for pattern in patterns:
        def deliver(topic: str, payload: dict, _pattern: str = pattern) -> None:
            if topic == SYSTEM_TICK:
                drain()
            output.publish_all(registry.route(topic, payload, _pattern))

        subscribe(pattern, deliver)
