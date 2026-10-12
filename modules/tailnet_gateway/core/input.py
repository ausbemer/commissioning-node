"""Subscriptions — what this module reads (spec §4). I/O only.

On every orchestration tick (§8.8) core reads the overlay through the injected
`OverlayStatusSource` and routes the snapshot to features on the inward topic. Features
never call the overlay themselves.
"""
from __future__ import annotations

from typing import Callable

from interfaces import SYSTEM_TICK

from ..interface import OVERLAY_SNAPSHOT, OverlayStatusSource
from .output import Output
from .registry import Registry

Subscribe = Callable[[str, Callable[[str, dict], None]], None]


def attach(registry: Registry, subscribe: Subscribe, output: Output, source: OverlayStatusSource) -> None:
    def on_tick(topic: str, payload: dict) -> None:
        snapshot = source.read()
        output.publish_all(registry.route(OVERLAY_SNAPSHOT, snapshot.model_dump()))

    subscribe(SYSTEM_TICK, on_tick)
