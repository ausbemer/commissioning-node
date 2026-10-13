"""What the tailnet-writer feature and core's worker share (ADR-0005 §1; §4 "features meet
in interface objects").

The feature decides (is this request allowed now, with which saved values) and puts a
`WritePlan` in the `WriterSlot`. Core's worker takes it off the bus thread, runs it against
the `OverlayWriter`, and leaves a `WriteOutcome`. On the next tick core drains the outcome
and routes it back to the feature, which publishes the result. The bus never waits on
tailscale (§6).

The slot holds ONE plan at a time and has no queue and no force door (§11 rung 5): a second
request while one runs is refused. Each run is bounded by the adapter's timeouts.
"""
from __future__ import annotations

import threading
from typing import Callable

from interfaces import Shape

#: Inward topic: core routes each finished outcome to features under this name. Never
#: published on the bus.
WRITE_OUTCOME = "tailnet-gateway/write-outcome"

JOIN, RENAME, LEAVE = "join", "rename", "leave"
OPERATIONS = (JOIN, RENAME, LEAVE)


class WritePlan(Shape):
    """One approved request, with the saved values it will apply. Never holds a secret:
    only the key's file path."""

    request_id: str
    operation: str
    requested_by: str
    door: str
    hostname: str | None = None
    tags: list[str] = []
    key_path: str | None = None
    #: After a successful join, delete the stored auth key (ADR-0005 D3).
    clear_key_on_success: bool = False


class WriteOutcome(Shape):
    plan: WritePlan
    ok: bool
    message: str
    finished_at: float


class WriterSlot:
    """One plan at a time. `submit` is the feature's side; `start`, `finish` and `drain` are
    core's. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: WritePlan | None = None
        self._done: list[WriteOutcome] = []
        self._on_submit: Callable[[WritePlan], None] | None = None

    def bind(self, on_submit: Callable[[WritePlan], None]) -> None:
        """Core: what to call with an accepted plan (it starts the worker)."""
        self._on_submit = on_submit

    def submit(self, plan: WritePlan) -> WritePlan | None:
        """Accept `plan` and return None, or return the plan already running (busy)."""
        with self._lock:
            if self._running is not None:
                return self._running
            if self._on_submit is None:
                raise RuntimeError("writer slot has no worker bound")
            self._running = plan
        try:
            self._on_submit(plan)
        except BaseException:
            with self._lock:
                self._running = None
            raise
        return None

    def finish(self, outcome: WriteOutcome) -> None:
        """Core: the running plan is done; free the slot and keep the outcome for draining."""
        with self._lock:
            self._running = None
            self._done.append(outcome)

    def drain(self) -> list[WriteOutcome]:
        """Core, on the tick: every outcome not yet published, oldest first."""
        with self._lock:
            done, self._done = self._done, []
        return done
