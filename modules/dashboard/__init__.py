"""dashboard — the module's composition point (spec §4): wires core and features.

Returns the registry (bus side) and the HTTP surface (operator side). The composition root
decides when to start the surface.
"""
from __future__ import annotations

from .core.http_surface import HttpSurface
from .core.input import Subscribe, attach
from .core.output import Output, Publish
from .core.registry import Registry
from .features.overview_page import OverviewPage
from .features.state_collector import StateCollector
from .interface import DashboardState


def build(subscribe: Subscribe, publish: Publish, host: str = "127.0.0.1",
          port: int = 8080) -> tuple[Registry, HttpSurface]:
    state = DashboardState()
    registry = Registry()
    registry.register(StateCollector(state))
    attach(registry, subscribe, Output(publish))
    return registry, HttpSurface(state, [OverviewPage()], host, port)
