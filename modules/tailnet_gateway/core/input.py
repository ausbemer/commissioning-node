"""Subscriptions — what this module reads (spec §4). I/O only.

On every orchestration tick (§8.8) core reads the overlay through the injected
`OverlayStatusSource`, stamps the reading with the time it was taken and the tick's declared
cadence, and routes the snapshot to features on the inward topic. Features never call the
overlay themselves.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from pydantic import ValidationError

from interfaces import SYSTEM_TICK, Tick

from ..interface import MODULE, OVERLAY_SNAPSHOT, OverlayStatusSource
from .output import Output
from .registry import Registry

log = logging.getLogger(MODULE)

Subscribe = Callable[[str, Callable[[str, dict], None]], None]


def attach(registry: Registry, subscribe: Subscribe, output: Output, source: OverlayStatusSource) -> None:
    def on_tick(topic: str, payload: dict) -> None:
        try:
            tick = Tick.model_validate(payload)
        except ValidationError as exc:
            # No cadence, no reading this tick: status and peers simply aren't renewed, so
            # their consumers' windows lapse (fail toward deny), and nothing raises.
            log.warning("ignoring malformed %s: %s", topic, exc.errors(include_url=False))
            return
        snapshot = source.read().model_copy(update={"observed_at": time.time(),
                                                    "period_s": tick.period_s})
        output.publish_all(registry.route(OVERLAY_SNAPSHOT, snapshot.model_dump()))

    subscribe(SYSTEM_TICK, on_tick)
