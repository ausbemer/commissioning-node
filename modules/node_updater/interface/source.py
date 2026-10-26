"""What the update-check feature and core's worker share (ADR-0009 D2; §4 "features meet in
interface objects").

The feature decides when to check and puts a `CheckPlan` in the `CheckSlot`. Core's worker
takes it off the bus thread, reads the running record (`RunningSource`) and asks the
`ReleaseSource` what the channel offers, and leaves a `CheckOutcome`. On the next tick core
drains it and routes it back to the feature, which publishes the status. The bus never waits
on GitHub (§6).

`choose_target` is the one rule for what a channel offers: the adapter applies it, and the
root updater (ADR-0009 step 3) must apply this same rule, never a copy.
"""
from __future__ import annotations

import re
import threading
from typing import Callable, Protocol, runtime_checkable

from interfaces import NodeVersion, Shape

#: Inward topic: core routes each finished check to features under this name. Never
#: published on the bus.
CHECK_OUTCOME = "node-updater/check-outcome"

RELEASES = "releases"
DEV = "dev"
#: The update channels (ADR-0009 D2).
CHANNELS = (RELEASES, DEV)
#: The branch the dev channel follows.
DEV_BRANCH = "dev"
#: A release tag: exactly vMAJOR.MINOR.PATCH. Any other tag is never offered.
RELEASE_TAG = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
#: A full commit id.
COMMIT = re.compile(r"[0-9a-f]{40}")
#: Release tags walked, newest first, looking for one whose CI passed: each costs a GitHub API
#: call (60 an hour per IP, unauthenticated). PROVISIONAL — tools/ledgers/node-updater/.
MAX_RELEASES_WALKED = 3

SUCCESS = "success"


def release_key(tag: str) -> tuple[int, int, int]:
    """Sort key of a release tag ("v0.10.2" > "v0.9.9")."""
    m = RELEASE_TAG.fullmatch(tag)
    if m is None:
        raise ValueError(f"{tag!r} is not a release tag")
    return int(m[1]), int(m[2]), int(m[3])


def choose_target(channel: str, refs: dict[str, str], ci: Callable[[str], str],
                  last_green: Callable[[str], str | None]) -> tuple[NodeVersion | None, NodeVersion | None, str]:
    """What `channel` offers: (target, newest, note).

    - `target` is the channel's newest commit whose CI passed, the only one ever offered.
    - `newest` is the channel's newest commit when that isn't `target`; else None.
    - `note` says why nothing (or something older) is offered; "" when target is the newest.

    `refs` maps "refs/heads/<b>" and "refs/tags/<t>" to commits (tags already peeled); `ci`
    answers "success", "failure", "pending" or "" for a commit; `last_green(branch)` is the
    newest commit on `branch` whose CI passed, or None. Unknown channels offer nothing.
    """
    if channel == RELEASES:
        tags = sorted((r[len("refs/tags/"):] for r in refs
                       if r.startswith("refs/tags/") and RELEASE_TAG.fullmatch(r[len("refs/tags/"):])),
                      key=release_key, reverse=True)
        if not tags:
            return None, None, "no release tag (vX.Y.Z) on GitHub"
        newest = None
        for tag in tags[:MAX_RELEASES_WALKED]:
            commit = refs[f"refs/tags/{tag}"]
            state = ci(commit)
            version = NodeVersion(commit=commit, ref=tag, ci=state)
            if state == SUCCESS:
                note = "" if newest is None else f"{newest.ref}'s CI {_said(newest.ci)}; offering {tag}"
                return version, newest, note
            newest = newest or version
        return None, newest, (f"no release's CI passed among the newest {MAX_RELEASES_WALKED} "
                              f"({newest.ref}'s CI {_said(newest.ci)})")
    if channel == DEV:
        tip = refs.get(f"refs/heads/{DEV_BRANCH}")
        if tip is None:
            return None, None, f"no {DEV_BRANCH} branch on GitHub"
        state = ci(tip)
        tip_version = NodeVersion(commit=tip, ref=DEV_BRANCH, ci=state)
        if state == SUCCESS:
            return tip_version, None, ""
        green = last_green(DEV_BRANCH)
        if green is None:
            return None, tip_version, f"no {DEV_BRANCH} commit's CI has passed"
        return (NodeVersion(commit=green, ref=DEV_BRANCH, ci=SUCCESS), tip_version,
                f"the newest {DEV_BRANCH} commit's CI {_said(state)}; offering the last that passed")
    return None, None, f"unknown update channel {channel!r}"


def _said(state: str) -> str:
    return {"failure": "failed", "pending": "hasn't finished"}.get(state, "has no result")


class RunningRecord(Shape):
    """The root-written record of the last install (ADR-0009 D1), as core read it."""
    commit: str
    ref: str = ""
    describe: str = ""
    mode: str = ""
    clean: bool | None = None


class ReleaseReading(Shape):
    """What the channel offers, against what runs, or why that couldn't be found out."""
    ok: bool
    message: str
    target: NodeVersion | None = None
    newest: NodeVersion | None = None
    #: target relative to the running commit: "ahead", "behind", "identical", "diverged",
    #: "unknown"; "" when there is no target or nothing runs.
    relation: str = ""
    #: True when GitHub refused for its rate limit: a sooner retry would only spend more of it.
    rate_limited: bool = False


class CheckPlan(Shape):
    """One approved check: the channel, and who asked ("" for a scheduled check)."""
    channel: str
    requested_by: str = ""
    door: str = ""
    request_id: str = ""


class CheckOutcome(Shape):
    plan: CheckPlan
    running: RunningRecord | None = None
    reading: ReleaseReading
    #: Wall-clock time the check finished (Unix seconds).
    finished_at: float


@runtime_checkable
class ReleaseSource(Protocol):
    """Reads what an update channel offers. Read-only. Must not raise: a failure is a reading
    with `ok=False` and the reason."""

    def read(self, channel: str, running_commit: str | None) -> ReleaseReading: ...


@runtime_checkable
class RunningSource(Protocol):
    """Reads the record of what this node runs. None when there is none or it's unreadable."""

    def read(self) -> RunningRecord | None: ...


class CheckSlot:
    """At most one check at a time, with no queue (as site-survey's `ScanSlot`). The feature
    submits; core's worker runs and finishes; core drains finished outcomes on the tick."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: CheckPlan | None = None
        self._done: list[CheckOutcome] = []
        self._start: Callable[[CheckPlan], None] | None = None

    def bind(self, start: Callable[[CheckPlan], None]) -> None:
        self._start = start

    def submit(self, plan: CheckPlan) -> CheckPlan | None:
        """Start `plan`, or return the plan already running (and start nothing)."""
        with self._lock:
            if self._running is not None:
                return self._running
            if self._start is None:
                raise RuntimeError("no worker bound to the check slot")
            self._running = plan
        try:
            self._start(plan)
        except BaseException:
            with self._lock:
                self._running = None
            raise
        return None

    def busy(self) -> bool:
        with self._lock:
            return self._running is not None

    def finish(self, outcome: CheckOutcome) -> None:
        with self._lock:
            self._running = None
            self._done.append(outcome)

    def drain(self) -> list[CheckOutcome]:
        with self._lock:
            done, self._done = self._done, []
            return done


# -- applying an update (ADR-0009 D3; inward 0.2.0) -----------------------------------------

#: Inward topic: core routes root's result record to features under this name, every tick.
#: Never published on the bus.
RESULT_READING = "node-updater/result-reading"
#: Relations an update may be requested for: newer, or a switch (diverged, or a running commit
#: GitHub doesn't have). Never "behind" or "identical". Root applies the same list
#: (ops/update_helper.py ACTIONABLE; tests check they agree).
ACTIONABLE = ("ahead", "diverged", "unknown")
#: The schema of the request file root reads (ops/update_helper.py read-request).
REQUEST_SCHEMA = "0.1.0"


class UpdateRequest(Shape):
    """The request the daemon leaves for the root updater: one expected commit, no code."""
    schema_version: str = REQUEST_SCHEMA
    request_id: str
    channel: str
    expected_commit: str
    requested_by: str
    door: str
    #: Wall-clock time (Unix seconds); root refuses a request much older than this.
    requested_at: float


class RootResult(Shape):
    """The root updater's record of the last update (`result.json`, root's directory), as core
    read it. Flat, as root writes it."""
    request_id: str = ""
    done: bool = False
    ok: bool = False
    phase: str = ""
    message: str = ""
    from_commit: str = ""
    from_ref: str = ""
    from_describe: str = ""
    to_commit: str = ""
    to_ref: str = ""
    to_describe: str = ""
    rolled_back: bool = False
    requested_by: str = ""
    door: str = ""
    started_at: float | None = None
    finished_at: float | None = None


@runtime_checkable
class UpdateRequester(Protocol):
    """Leaves a request for the root updater. Never overwrites one that is still there."""

    def submit(self, request: UpdateRequest) -> str | None:
        """None when the request was left; else why not, for people."""
        ...

    def pending(self) -> bool:
        """True while a request waits for root to pick it up."""
        ...


@runtime_checkable
class ResultSource(Protocol):
    """Reads root's result record. None when there is none or it's unreadable."""

    def read(self) -> RootResult | None: ...
