"""stream-relay — the module's composition point (spec §4): wires core and features."""
from __future__ import annotations

import time
from typing import Callable

from .core.input import Subscribe, attach
from .core.output import Output, Publish
from .core.registry import Registry
from .features.relay_planner import RelayPlanner


def build(subscribe: Subscribe, publish: Publish, clock: Callable[[], float] = time.monotonic) -> Registry:
    registry = Registry()
    registry.register(RelayPlanner(clock=clock))
    attach(registry, subscribe, Output(publish))
    return registry
