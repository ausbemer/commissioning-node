"""Subscriptions — what this module reads (spec §4). I/O only.

Subscribes once per pattern the enabled features asked for. On every tick it first drains the
check worker's finished outcomes and routes them to features on `CHECK_OUTCOME`, so a check's
result is published on the tick after it finishes, from the bus thread.
"""
from __future__ import annotations

from typing import Callable

from interfaces import SYSTEM_TICK

from ..interface import CHECK_OUTCOME, CheckSlot
from .output import Output
from .registry import Registry

Subscribe = Callable[[str, Callable[[str, dict], None]], None]


def attach(registry: Registry, subscribe: Subscribe, output: Output, slot: CheckSlot) -> None:
    def drain() -> None:
        for outcome in slot.drain():
            output.publish_all(registry.route(CHECK_OUTCOME, outcome.model_dump()))

    # The inward outcome topic is routed only from the slot, never subscribed on the bus:
    # nothing published there can pose as a finished check.
    patterns = sorted(({p for f in registry.enabled() for p in f.subscriptions} | {SYSTEM_TICK})
                      - {CHECK_OUTCOME})
    for pattern in patterns:
        def deliver(topic: str, payload: dict, _pattern: str = pattern) -> None:
            if topic == SYSTEM_TICK:
                drain()
            output.publish_all(registry.route(topic, payload, _pattern))

        subscribe(pattern, deliver)
