"""The writer worker — runs one approved plan off the bus thread (ADR-0005 §1). I/O only.

The feature decides; this does. Each accepted plan gets its own short-lived thread (no idle
thread waits between requests), and the `WriterSlot` guarantees at most one runs at a time.
The outcome goes back into the slot, and `input.attach` drains it on the next tick, so
nothing here touches the bus.

The join record (ADR-0005 A2) follows the outcome: written after a successful join or
Routes, deleted after a successful Leave. The auth key is kept (A1). Every path frees the
slot, even an unexpected exception.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from ..interface import (AUTH_KEY, JOIN, LEAVE, MODULE, RENAME, ROUTES, ConfigView, JoinRecord,
                         OverlayWriter, WriteOutcome, WritePlan, WriterReply, WriterSlot)
from .join_record import JoinRecordStore

log = logging.getLogger(MODULE)


def _thread(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name="tailnet-writer", daemon=True).start()


class WriterWorker:
    def __init__(self, slot: WriterSlot, writer: OverlayWriter, config: ConfigView | None,
                 record: JoinRecordStore | None = None,
                 spawn: Callable[[Callable[[], None]], None] = _thread,
                 now: Callable[[], float] = time.time) -> None:
        """`spawn` runs the work; tests pass one that runs it inline."""
        self._slot, self._writer, self._config, self._record = slot, writer, config, record
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
            reply = self._writer.join(plan.hostname, plan.tags, plan.routes, plan.key_path)
            if reply.ok:
                reply = self._recorded(reply, JoinRecord(joined_at=self._now(), routes=plan.routes))
            if reply.ok and plan.clear_key_on_success and self._config is not None:
                cleared = self._config.clear_secret(AUTH_KEY.name)
                note = ("the saved auth key was deleted" if cleared.ok else
                        f"the saved auth key could NOT be deleted ({cleared.message}); delete it on /config")
                reply = WriterReply(ok=True, message=f"{reply.message}; {note}")
            return reply
        if plan.operation == RENAME and plan.hostname is not None:
            return self._writer.rename(plan.hostname)
        if plan.operation == ROUTES:
            reply = self._writer.set_routes(plan.routes)
            current = self._record.read() if self._record is not None else None
            if reply.ok and current is not None:
                reply = self._recorded(reply, current.model_copy(update={"routes": plan.routes}))
            return reply
        if plan.operation == LEAVE:
            reply = self._writer.leave()
            if reply.ok and self._record is not None:
                try:
                    self._record.delete()
                except OSError as exc:
                    return WriterReply(ok=True, message=f"{reply.message}; the join record could NOT "
                                                        f"be deleted ({exc.strerror}), so the node may "
                                                        "rejoin: switch off auto-rejoin")
            return reply
        return WriterReply(ok=False, message=f"{plan.operation} failed: the plan is incomplete")

    def _recorded(self, reply: WriterReply, record: JoinRecord) -> WriterReply:
        if self._record is None:
            return reply
        try:
            self._record.write(record)
        except OSError as exc:
            return WriterReply(ok=True, message=f"{reply.message}; the join record could NOT be "
                                                f"saved ({exc.strerror}), so it will not rejoin on its own")
        return reply
