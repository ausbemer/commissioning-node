"""tailnet-writer — decides whether a join, rename or leave may run now (ADR-0005).

The decision is all here; the doing is core's worker. On a `tailnet/apply` command it checks
the request against the node's latest overlay reading (a renewed level, fresh by the tick's
declared cadence: freshness) and the saved config (ADR-0004), builds a `WritePlan` with the
saved values, and hands it to the `WriterSlot`. Anything it refuses, and every finished
outcome, goes out as a `TailnetApplyResult`.

The command carries no values (ADR-0005 §1): hostname, tags, routes and the key's path come
from this module's own `ConfigView`; routes are resolved by `resolve_routes`, the one
authority for what "auto" means (A3).

Automatic rejoin (A2) is the one thing it starts on its own, and only to rejoin a tailnet the
operator already joined: the writer's join record exists, the operator hasn't asked to
leave since, `auto_rejoin` is on, a key is saved, and the fresh reading says the overlay
needs a login. It applies the record's confirmed hostname, tags and routes, and the worker
refuses it unless the saved key is the one confirmed. Attempts back off from
REJOIN_BACKOFF_MIN_S, doubling, to REJOIN_BACKOFF_MAX_S. Operator requests are never retried.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Callable

from pydantic import ValidationError

from freshness import is_fresh
from interfaces import (TAILNET_APPLY, TAILNET_APPLY_RESULT, TailnetApply, TailnetApplyResult,
                        __version__ as CONTRACT_VERSION)

from ..interface import (JOIN, LEAVE, MODULE, OPERATIONS, OVERLAY_SNAPSHOT, RENAME, ROUTES,
                         WRITE_OUTCOME, ConfigView, OverlaySnapshot, WriteOutcome, WritePlan,
                         WriterSlot, resolve_routes)

log = logging.getLogger(MODULE)

#: Auto-rejoin backoff, seconds. PROVISIONAL (ADR-0005 A2; TODO.md Evidence).
REJOIN_BACKOFF_MIN_S = 60.0
REJOIN_BACKOFF_MAX_S = 1800.0
#: The overlay state that means "this node needs to log in again" (tailscaled BackendState).
NEEDS_LOGIN = "NeedsLogin"
#: Who an automatic rejoin is recorded against, and through which door.
AUTO_REJOIN_BY, AUTO_REJOIN_DOOR = "auto-rejoin", "node"


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
        self._next_rejoin = 0.0
        self._backoff = REJOIN_BACKOFF_MIN_S
        #: The operator asked to leave: a level held until their next successful Join, so
        #: no reading taken around a Leave can start a rejoin (review 2026-10-13).
        self._left = False
        self._warned_old_record = False

    def handle(self, topic: str, payload: dict) -> list[tuple[str, dict]]:
        if topic == OVERLAY_SNAPSHOT:
            self._snapshot = OverlaySnapshot.model_validate(payload)
            self._snapshot_at = self._clock()
            self._maybe_rejoin(self._snapshot)
            return []
        if topic == WRITE_OUTCOME:
            outcome = WriteOutcome.model_validate(payload)
            if outcome.plan.operation == JOIN:
                # Any join outcome pushes the next automatic attempt out by one backoff: after
                # a success, tailscaled gets time to report Running; after a failed operator
                # Join, auto-rejoin must not act as its retry.
                if outcome.ok:
                    self._backoff = REJOIN_BACKOFF_MIN_S
                    if outcome.plan.requested_by != AUTO_REJOIN_BY:
                        self._left = False
                self._next_rejoin = max(self._next_rejoin, self._clock() + self._backoff)
            return [self._finished(outcome)]
        try:
            command = TailnetApply.model_validate(payload)
        except ValidationError as exc:
            log.warning("ignoring malformed %s: %s", topic, exc.errors(include_url=False))
            return []
        plan, refusal = self._plan(command)
        if plan is not None:
            running = self._slot.submit(plan)
            if running is None and plan.operation == LEAVE:
                self._left = True
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
                return None, "no auth key is saved; paste a reusable ephemeral key on this page first"
            return self._join_plan(base, snap, key_path), ""
        if cmd.operation == ROUTES:
            if not snap.connected:
                return None, "the node is not on a tailnet; saved routes are applied by Join"
            wanted = self._wanted_routes(snap)
            if snap.join_record is not None and snap.join_record.routes == wanted:
                return None, f"already advertising {', '.join(wanted) or 'no routes'}"
            return WritePlan(**base, routes=wanted), ""
        # RENAME
        if not snap.connected:
            return None, "the node is not on a tailnet; a saved hostname is applied by Join"
        if hostname is None:
            return None, "no hostname is saved"
        if hostname == snap.hostname:
            return None, f"the node is already named {hostname}"
        return WritePlan(**base, hostname=hostname), ""

    def _wanted_routes(self, snap: OverlaySnapshot) -> list[str]:
        return resolve_routes(self._config.get("advertise_routes"), snap.site_networks)

    def _join_plan(self, base: dict, snap: OverlaySnapshot, key_path: str) -> WritePlan:
        # The key is kept after a join (ADR-0005 A1): clear_key_on_success stays False.
        return WritePlan(**base, hostname=self._config.get("hostname"),
                         tags=list(self._config.get("tags") or []),
                         routes=self._wanted_routes(snap), key_path=key_path)

    def _maybe_rejoin(self, snap: OverlaySnapshot) -> None:
        """A2: rejoin a tailnet the operator joined, when the overlay needs a login again,
        with exactly what they confirmed (the join record), never what is merely saved."""
        record = snap.join_record
        if record is None or self._left or self._config is None or snap.state != NEEDS_LOGIN:
            return
        if not self._config.get("auto_rejoin"):
            return
        if record.key_sha256 is None:
            if not self._warned_old_record:
                log.warning("tailnet: the join record predates confirmed-key fingerprints; not "
                            "rejoining on its own. Leave and Join once to record it.")
                self._warned_old_record = True
            return
        now = self._clock()
        if now < self._next_rejoin:
            return
        key_path = self._config.secret_path("auth_key")
        if key_path is None:
            return
        plan = WritePlan(request_id=uuid.uuid4().hex[:12], operation=JOIN,
                         requested_by=AUTO_REJOIN_BY, door=AUTO_REJOIN_DOOR,
                         hostname=record.hostname, tags=list(record.tags),
                         routes=list(record.routes), key_path=key_path,
                         expected_key_sha256=record.key_sha256)
        base = {"request_id": plan.request_id}
        if self._slot.submit(plan) is not None:
            return                                   # an operator request is running
        self._next_rejoin = now + self._backoff
        log.warning("tailnet: the node needs a login again; rejoining with the saved key "
                    "(request %s; next attempt no sooner than %.0fs)", base["request_id"], self._backoff)
        self._backoff = min(self._backoff * 2, REJOIN_BACKOFF_MAX_S)

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
