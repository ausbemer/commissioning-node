"""The reader and sender threads (ADR-0010 D1, D3). I/O only. The policy is `interface`'s.

- **Reader:** takes allowlisted frames off the `NavSource`, decodes them, and keeps the latest
  per (PGN, source) in `NavState`. Every `LINK_CHECK_S` it re-checks the link, so a link that
  vanished, went down or lost listen-only stops being read.
- **Sender:** every `OUTPUT_PERIOD_S`, on wall-clock deadlines rather than the tick (§8.8), it
  asks `current_fix` what is fresh and sends one datagram to each destination, recording how each
  send went. While there are no destinations it encodes and sends nothing (§8.8).

Neither touches the bus. The `status` feature reports both on the tick. Each loop survives its
own exceptions: one bad frame or failed send never stops the feed.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from ..interface import (LINK_DOWN, LINK_UP, MODULE, OUTPUT_PERIOD_S, DestinationTable, LinkReport,
                         NavSource, NavState, Reading, SentenceSink, current_fix)
from . import n2k, nmea0183

log = logging.getLogger(MODULE)

#: How often the reader re-checks the link, seconds. PROVISIONAL (§8.11).
LINK_CHECK_S = 5.0
#: How long one receive waits, so link checks and stop requests are never late by more.
RECEIVE_WAIT_S = 0.5
#: Longest an error text kept per destination.
ERROR_CAP = 120


def thread(name: str, target: Callable[[], None]) -> None:
    threading.Thread(target=target, name=name, daemon=True).start()


class Reader:
    def __init__(self, source: NavSource, state: NavState,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._source, self._state, self._clock = source, state, clock
        self._checked_at: float | None = None

    def step(self) -> None:
        """One link check if due, then at most one frame."""
        now = self._clock()
        if self._checked_at is None or now - self._checked_at >= LINK_CHECK_S:
            self._checked_at = now
            report = self._source.check_link()
            if report != self._state.link():
                log.info("link %s%s", report.state, f": {report.detail}" if report.detail else "")
            self._state.set_link(report)
        if self._state.link().state != LINK_UP:
            return
        try:
            frame = self._source.receive(RECEIVE_WAIT_S)
        except OSError as exc:
            self._source.close()
            self._state.set_link(LinkReport(LINK_DOWN, f"read failed: {exc.strerror or exc}"))
            self._checked_at = None
            return
        if frame is None:
            return
        can_id, data = frame
        pgn = n2k.pgn_of(can_id)
        values = n2k.decode(pgn, data)
        if values is not None:
            self._state.put(Reading(pgn=pgn, source=n2k.source_of(can_id), values=values,
                                    received_at=self._clock()))

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.step()
            except Exception:  # noqa: BLE001 — the feed must outlive one bad frame
                log.exception("reader step failed")
                stop.wait(1.0)
            if self._state.link().state != LINK_UP:
                stop.wait(1.0)
        self._source.close()


class Sender:
    def __init__(self, state: NavState, table: DestinationTable, sink: SentenceSink,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._state, self._table, self._sink, self._clock = state, table, sink, clock

    def step(self) -> None:
        targets = self._table.targets()
        if not targets:
            return
        fix, _ = current_fix(self._state.readings(), self._clock())
        payload = nmea0183.datagram(fix)
        for address, port in targets:
            try:
                self._sink.send(address, port, payload)
                self._table.record(address, port, None)
            except OSError as exc:
                self._table.record(address, port, str(exc.strerror or exc)[:ERROR_CAP])

    def run(self, stop: threading.Event) -> None:
        deadline = self._clock()
        while not stop.is_set():
            try:
                self.step()
            except Exception:  # noqa: BLE001 — one failed send never stops the feed
                log.exception("sender step failed")
            deadline = max(deadline + OUTPUT_PERIOD_S, self._clock())
            stop.wait(max(0.0, deadline - self._clock()))
