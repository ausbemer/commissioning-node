"""The writer worker — runs one approved plan off the bus thread (ADR-0005 §1). I/O only.

The feature decides; this does. Each accepted plan gets its own short-lived thread (no idle
thread waits between requests), and the `WriterSlot` guarantees at most one runs at a time.
The outcome goes back into the slot, and `input.attach` drains it on the next tick, so
nothing here touches the bus.

The join record (ADR-0005 A2) holds what the operator confirmed: written after a successful
join (hostname, tags, routes and the key's SHA-256), updated by Rename and Routes, and
deleted BEFORE a Leave runs, so a Leave that fails, times out or can't delete it never
leaves an intent to rejoin (fail toward not joining). An automatic rejoin must present the
same key, or it is refused without calling tailscale. The auth key is kept (A1). Every path
frees the slot, even an unexpected exception.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Callable

from ..interface import (AUTH_KEY, JOIN, LEAVE, MODULE, RENAME, ROUTES, ROUTES_OFF, ConfigView, JoinRecord,
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
            fingerprint = _sha256(plan.key_path)
            if plan.expected_key_sha256 is not None and fingerprint != plan.expected_key_sha256:
                return WriterReply(ok=False, message="rejoin refused: the saved auth key is not the "
                                                     "one the operator joined with; press Join to "
                                                     "confirm the new one")
            reply = self._writer.join(plan.hostname, plan.tags, plan.key_path, plan.routes)
            if reply.ok:
                reply = self._recorded(reply, JoinRecord(
                    joined_at=self._now(), routes=plan.routes, hostname=plan.hostname,
                    tags=plan.tags, key_sha256=fingerprint))
            if reply.ok and plan.clear_key_on_success and self._config is not None:
                cleared = self._config.clear_secret(AUTH_KEY.name)
                note = ("the saved auth key was deleted" if cleared.ok else
                        f"the saved auth key could NOT be deleted ({cleared.message}); delete it on /config")
                reply = WriterReply(ok=True, message=f"{reply.message}; {note}")
            return reply
        if plan.operation == RENAME and plan.hostname is not None:
            reply = self._writer.rename(plan.hostname)
            current = self._record.read() if self._record is not None else None
            if reply.ok and current is not None:
                reply = self._recorded(reply, current.model_copy(update={"hostname": plan.hostname}))
            return reply
        if plan.operation in (ROUTES, ROUTES_OFF):
            reply = self._writer.set_routes(plan.routes)
            current = self._record.read() if self._record is not None else None
            if reply.ok and current is not None:
                reply = self._recorded(reply, current.model_copy(update={"routes": plan.routes}))
            return reply
        if plan.operation == LEAVE:
            # The intent goes first: whatever logout does next, nothing is left to rejoin.
            note = ""
            if self._record is not None:
                try:
                    self._record.delete()
                except OSError as exc:
                    # The record outlives a restart, and the in-memory "left" level doesn't:
                    # take away the key instead, so nothing can rejoin (fail toward not joining).
                    cleared = self._config.clear_secret(AUTH_KEY.name) if self._config else None
                    gone = "the saved auth key was deleted so it cannot rejoin" if cleared and cleared.ok \
                        else "it will not rejoin until the daemon restarts; switch off auto-rejoin"
                    note = f"; the join record could NOT be deleted ({exc.strerror}): {gone}"
            reply = self._writer.leave()
            return WriterReply(ok=reply.ok, message=reply.message + note)
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


def _sha256(path: str) -> str | None:
    """The key file's fingerprint; never logged, never published. None when unreadable."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read(64 * 1024)).hexdigest()
    except OSError:
        return None
