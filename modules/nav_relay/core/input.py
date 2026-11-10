"""Subscriptions — what this module reads (spec §4). I/O only.

Subscribes once per pattern the enabled features asked for, and routes each message to them.
The reader and sender threads never touch the bus (ADR-0010 D1): the `status` feature reports
them on the tick.
"""
from __future__ import annotations

from typing import Callable

from .output import Output
from .registry import Registry

Subscribe = Callable[[str, Callable[[str, dict], None]], None]


def attach(registry: Registry, subscribe: Subscribe, output: Output) -> None:
    patterns = sorted({p for f in registry.enabled() for p in f.subscriptions})
    for pattern in patterns:
        def deliver(topic: str, payload: dict, _pattern: str = pattern) -> None:
            output.publish_all(registry.route(topic, payload, _pattern))

        subscribe(pattern, deliver)
