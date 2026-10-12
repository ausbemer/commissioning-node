"""Subscriptions — what this module reads from the bus (spec §4). I/O only."""
from __future__ import annotations

from typing import Callable

from .output import Output
from .registry import Registry

Subscribe = Callable[[str, Callable[[str, dict], None]], None]


def attach(registry: Registry, subscribe: Subscribe, output: Output) -> None:
    """Subscribe once per pattern the enabled features asked for; publish what they return."""
    patterns = sorted({p for f in registry.enabled() for p in f.subscriptions})
    for pattern in patterns:
        def deliver(topic: str, payload: dict, _pattern: str = pattern) -> None:
            output.publish_all(registry.route(topic, payload, _pattern))

        subscribe(pattern, deliver)
