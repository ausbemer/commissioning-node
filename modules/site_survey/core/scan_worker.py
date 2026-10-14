"""The scan worker — runs one approved scan off the bus thread (ADR-0007 Safety 4). I/O only.

The feature decides; this does. Each accepted plan gets its own short-lived thread, and the
`ScanSlot` guarantees at most one runs at a time. The outcome goes back into the slot, and
`input.attach` drains it on the next tick, so nothing here touches the bus. Every path frees
the slot, even an unexpected exception.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from ..interface import MODULE, NetworkScanner, ScanOutcome, ScanPlan, ScanReply, ScanSlot

log = logging.getLogger(MODULE)


def _thread(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name="site-scan", daemon=True).start()


class ScanWorker:
    def __init__(self, slot: ScanSlot, scanner: NetworkScanner,
                 spawn: Callable[[Callable[[], None]], None] = _thread,
                 now: Callable[[], float] = time.time) -> None:
        """`spawn` runs the work; tests pass one that runs it inline."""
        self._slot, self._scanner, self._spawn, self._now = slot, scanner, spawn, now
        slot.bind(lambda plan: self._spawn(lambda: self._run(plan)))

    def _run(self, plan: ScanPlan) -> None:
        reply = ScanReply(ok=False, message="scan failed: internal error; see the journal")
        try:
            log.info("site scan %s by %s (%s): %s", plan.request_id, plan.requested_by, plan.door,
                     ", ".join(plan.networks))
            reply = self._scanner.scan(plan.networks)
        except Exception:  # noqa: BLE001 — the slot must always be freed
            log.exception("site scan %s raised", plan.request_id)
        finally:
            log.info("site scan %s: %s", plan.request_id, reply.message)
            self._slot.finish(ScanOutcome(plan=plan, reply=reply, finished_at=self._now()))
