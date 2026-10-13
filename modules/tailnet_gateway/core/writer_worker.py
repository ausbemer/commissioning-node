"""The writer worker — runs one approved plan off the bus thread (ADR-0005 §1). I/O only.

The feature decides; this does. Each accepted plan gets its own short-lived thread (no idle
thread waits between requests), and the `WriterSlot` guarantees at most one runs at a time.
The outcome goes back into the slot, and `input.attach` drains it on the next tick, so
nothing here touches the bus.

After a successful join the stored auth key is deleted through the module's `ConfigView`
(ADR-0005 D3). Every path frees the slot, even an unexpected exception.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from ..interface import (AUTH_KEY, JOIN, LEAVE, MODULE, RENAME, ConfigView, OverlayWriter,
                         WriteOutcome, WritePlan, WriterReply, WriterSlot)

log = logging.getLogger(MODULE)


def _thread(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name="tailnet-writer", daemon=True).start()


class WriterWorker:
    def __init__(self, slot: WriterSlot, writer: OverlayWriter, config: ConfigView | None,
                 spawn: Callable[[Callable[[], None]], None] = _thread,
                 now: Callable[[], float] = time.time) -> None:
        """`spawn` runs the work; tests pass one that runs it inline."""
        self._slot, self._writer, self._config = slot, writer, config
        self._spawn, self._now = spawn, now
        slot.bind(lambda plan: self._spawn(lambda: self._run(plan)))

    def _run(self, plan: WritePlan) -> None:
        reply = WriterReply(ok=False, message=f"{plan.operation} failed: internal error; see the journal")
        try:
            reply = self._execute(plan)
        except Exception:  # noqa: BLE001 — the slot must always be freed
            log.exception("tailnet %s (request %s) raised", plan.operation, plan.request_id)
        finally:
            self._slot.finish(WriteOutcome(plan=plan, ok=reply.ok, message=reply.message,
                                           finished_at=self._now()))

    def _execute(self, plan: WritePlan) -> WriterReply:
        if plan.operation == JOIN:
            if plan.key_path is None:
                return WriterReply(ok=False, message="join failed: no auth key path in the plan")
            reply = self._writer.join(plan.hostname, plan.tags, plan.key_path)
            if reply.ok and plan.clear_key_on_success and self._config is not None:
                cleared = self._config.clear_secret(AUTH_KEY.name)
                note = ("the saved auth key was deleted" if cleared.ok else
                        f"the saved auth key could NOT be deleted ({cleared.message}); delete it on /config")
                reply = WriterReply(ok=True, message=f"{reply.message}; {note}")
            return reply
        if plan.operation == RENAME and plan.hostname is not None:
            return self._writer.rename(plan.hostname)
        if plan.operation == LEAVE:
            return self._writer.leave()
        return WriterReply(ok=False, message=f"{plan.operation} failed: the plan is incomplete")
