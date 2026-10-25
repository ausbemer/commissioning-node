"""The check worker — runs one update check off the bus thread (ADR-0009 D2). I/O only.

The feature decides when; this does. Each accepted plan gets its own short-lived thread, and
the `CheckSlot` guarantees at most one runs at a time. It reads the running record, asks the
release source what the channel offers against it, and leaves the outcome in the slot for
`input.attach` to drain on the next tick. Every path frees the slot.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from ..interface import (MODULE, CheckOutcome, CheckPlan, CheckSlot, ReleaseReading,
                         ReleaseSource, RunningSource)

log = logging.getLogger(MODULE)


def _thread(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name="update-check", daemon=True).start()


class CheckWorker:
    def __init__(self, slot: CheckSlot, source: ReleaseSource, running: RunningSource,
                 spawn: Callable[[Callable[[], None]], None] = _thread,
                 now: Callable[[], float] = time.time) -> None:
        """`spawn` runs the work; tests pass one that runs it inline."""
        self._slot, self._source, self._running, self._spawn, self._now = slot, source, running, spawn, now
        slot.bind(lambda plan: self._spawn(lambda: self._run(plan)))

    def _run(self, plan: CheckPlan) -> None:
        record = None
        reading = ReleaseReading(ok=False, message="check failed: internal error; see the journal")
        try:
            record = self._running.read()
            reading = self._source.read(plan.channel, record.commit if record else None)
        except Exception:  # noqa: BLE001 — the slot must always be freed
            log.exception("update check (%s) raised", plan.channel)
        finally:
            who = f" by {plan.requested_by} ({plan.door})" if plan.requested_by else ""
            log.info("update check %s%s: %s", plan.channel, who,
                     reading.message or (reading.target.ref if reading.target else "nothing"))
            self._slot.finish(CheckOutcome(plan=plan, running=record, reading=reading,
                                           finished_at=self._now()))
