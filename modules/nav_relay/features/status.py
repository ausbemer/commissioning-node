"""status — reports the link, the fix, the sources and the destinations once per tick
(ADR-0010 D5).

It is latest-value state for the operator (§6), declaring the tick's cadence as its own. The fix
reported is exactly what the sender would send now: the same `current_fix`, over the same
readings, on the same clock.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from pydantic import ValidationError

from interfaces import NAV_STATUS, SYSTEM_TICK, NavStatus, Tick, __version__ as CONTRACT_VERSION

from ..interface import INTERFACE, MODULE, DestinationTable, NavState, current_fix

log = logging.getLogger(MODULE)


class StatusFeature:
    name = "status"
    subscriptions = [SYSTEM_TICK]

    def __init__(self, state: NavState, table: DestinationTable,
                 clock: Callable[[], float] = time.monotonic, interface: str = INTERFACE) -> None:
        self._state, self._table, self._clock, self._ifname = state, table, clock, interface

    def handle(self, topic: str, payload: dict) -> list[tuple[str, dict]]:
        try:
            period = Tick.model_validate(payload).period_s
        except ValidationError as exc:
            log.warning("ignoring malformed %s: %s", topic, exc.errors(include_url=False))
            return []
        link = self._state.link()
        fix, sources = current_fix(self._state.readings(), self._clock())
        return [(NAV_STATUS, NavStatus.stamped(
            CONTRACT_VERSION, link=link.state, link_detail=link.detail, interface=self._ifname,
            fix=fix, sources=sources,
            destinations=self._table.states(), period_s=period,
            origin=f"{MODULE}/{self.name}").model_dump())]
