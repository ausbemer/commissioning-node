"""tailnet-writer — decides whether a join, rename or leave may run now (ADR-0005).

The decision is all here; the doing is core's worker. On a `tailnet/apply` command it checks
the request against the node's latest overlay reading (a renewed level, fresh by the tick's
declared cadence: freshness) and the saved config (ADR-0004), builds a `WritePlan` with the
saved values, and hands it to the `WriterSlot`. Anything it refuses, and every finished
outcome, goes out as a `TailnetApplyResult`.

The command carries no values (ADR-0005 §1): hostname, tags and the key's path come from
this module's own `ConfigView`. Nothing is ever retried on its own (D1).
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from pydantic import ValidationError

from freshness import is_fresh
from interfaces import (TAILNET_APPLY, TAILNET_APPLY_RESULT, TailnetApply, TailnetApplyResult,
                        __version__ as CONTRACT_VERSION)

from ..interface import (JOIN, LEAVE, MODULE, OPERATIONS, OVERLAY_SNAPSHOT, RENAME,
                         WRITE_OUTCOME, ConfigView, OverlaySnapshot, WriteOutcome, WritePlan,
                         WriterSlot)

log = logging.getLogger(MODULE)


class TailnetWriter:
    name = "tailnet-writer"
    subscriptions = [TAILNET_APPLY, OVERLAY_SNAPSHOT, WRITE_OUTCOME]

    def __init__(self, slot: WriterSlot, config: ConfigView | None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._slot = slot
        self._config = config
        self._clock = clock
        self._snapshot: OverlaySnapshot | None = None
        self._snapshot_at: float | None = None

    def handle(self, topic: str, payload: dict) -> list[tuple[str, dict]]:
        if topic == OVERLAY_SNAPSHOT:
            self._snapshot = OverlaySnapshot.model_validate(payload)
            self._snapshot_at = self._clock()
            return []
        if topic == WRITE_OUTCOME:
            return [self._finished(WriteOutcome.model_validate(payload))]
        try:
            command = TailnetApply.model_validate(payload)
        except ValidationError as exc:
            log.warning("ignoring malformed %s: %s", topic, exc.errors(include_url=False))
            return []
        plan, refusal = self._plan(command)
        if plan is not None:
            running = self._slot.submit(plan)
            if running is None:
                log.info("tailnet %s accepted (request %s, by %s via the %s door)",
                         plan.operation, plan.request_id, plan.requested_by, plan.door)
                return []
            refusal = (f"busy: a {running.operation} requested by {running.requested_by} is "
                       "still running; try again when it has finished")
        log.info("tailnet %s refused (request %s): %s", command.operation, command.request_id, refusal)
        return [self._result(command.request_id, command.operation, False, refusal,
                             command.requested_by, command.door)]

    # -- deciding ------------------------------------------------------------------------

    def _plan(self, cmd: TailnetApply) -> tuple[WritePlan | None, str]:
        if cmd.operation not in OPERATIONS:
            return None, f"unknown operation {cmd.operation!r}; expected one of {', '.join(OPERATIONS)}"
        if self._config is None:
            return None, "the config registry is not available on this node"
        base = dict(request_id=cmd.request_id, operation=cmd.operation,
                    requested_by=cmd.requested_by, door=cmd.door)
        if cmd.operation == LEAVE:
            # Leaving is the safe direction (rung 6: the safe state is "not on the site
            # tailnet"), so it needs no fresh reading: an unreadable status must not trap the
            # node on a tailnet the operator wants it off.
            return WritePlan(**base), ""
        snap = self._snapshot
        period = snap.period_s if snap else None
        if snap is None or not is_fresh(self._snapshot_at, period, self._clock()):
            return None, "the node's tailnet state is not known yet (no fresh status); try again shortly"
        hostname = self._config.get("hostname")
        if cmd.operation == JOIN:
            if snap.connected:
                return None, ("the node is already on a tailnet; Leave first, then Join "
                              "(tags change only on a join)")
            key_path = self._config.secret_path("auth_key")
            if key_path is None:
                return None, "no auth key is saved; paste an ephemeral key on this page first"
            return WritePlan(**base, hostname=hostname, tags=list(self._config.get("tags") or []),
                             key_path=key_path, clear_key_on_success=True), ""
        # RENAME
        if not snap.connected:
            return None, "the node is not on a tailnet; a saved hostname is applied by Join"
        if hostname is None:
            return None, "no hostname is saved"
        if hostname == snap.hostname:
            return None, f"the node is already named {hostname}"
        return WritePlan(**base, hostname=hostname), ""

    # -- reporting -----------------------------------------------------------------------

    def _finished(self, outcome: WriteOutcome) -> tuple[str, dict]:
        p = outcome.plan
        log.info("tailnet %s %s (request %s, by %s via the %s door): %s", p.operation,
                 "succeeded" if outcome.ok else "failed", p.request_id, p.requested_by, p.door,
                 outcome.message)
        return self._result(p.request_id, p.operation, outcome.ok, outcome.message,
                            p.requested_by, p.door, outcome.finished_at)

    @staticmethod
    def _result(request_id: str, operation: str, ok: bool, message: str, requested_by: str,
                door: str, finished_at: float | None = None) -> tuple[str, dict]:
        result = TailnetApplyResult.stamped(
            CONTRACT_VERSION, request_id=request_id, operation=operation, ok=ok, message=message,
            requested_by=requested_by, door=door,
            finished_at=time.time() if finished_at is None else finished_at)
        return TAILNET_APPLY_RESULT, result.model_dump()
