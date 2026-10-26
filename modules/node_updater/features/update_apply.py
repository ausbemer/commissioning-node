"""update-apply — the operator's confirmed update, handed to the root updater (ADR-0009 D3).

It decides whether a `node/update` may go to root now, and leaves a request with no code in
it: one expected commit. The rules, each refused in-band with its reason (§11 rung 7):

- the commit is the one the channel offers in a fresh `node/update-status` (a renewed level,
  judged by `freshness` on the period it declares), from a check that worked and isn't
  running, and it is newer than what runs or a switch (`ACTIONABLE`);
- no update is requested or running already (no queue, no force door).

Root checks the same again for itself (rung 13): this only saves a doomed request. It then
publishes root's own record of the update (`RESULT_READING`) whenever it changes, so the outcome
survives the daemon's restart in the middle of an update.

Registered disabled unless main enables it (`CN_NODE_UPDATE=1`, ADR-0009 Safety rung 1).
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from pydantic import ValidationError

from freshness import is_fresh
from interfaces import (NODE_UPDATE, NODE_UPDATE_RESULT, NODE_UPDATE_STATUS, NodeUpdate,
                        NodeUpdateResult, NodeUpdateStatus, NodeVersion,
                        __version__ as CONTRACT_VERSION)

from ..interface import (ACTIONABLE, CHANNELS, COMMIT, MODULE, RESULT_READING, RootResult,
                         UpdateRequest, UpdateRequester)

log = logging.getLogger(MODULE)


class UpdateApply:
    name = "update-apply"
    subscriptions = [NODE_UPDATE, NODE_UPDATE_STATUS, RESULT_READING]

    def __init__(self, requester: UpdateRequester, clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], float] = time.time) -> None:
        self._requester, self._clock, self._now = requester, clock, now
        self._status: NodeUpdateStatus | None = None
        self._status_at: float | None = None
        self._root: RootResult | None = None
        self._published: tuple | None = None

    def handle(self, topic: str, payload: dict) -> list[tuple[str, dict]]:
        try:
            if topic == NODE_UPDATE_STATUS:
                self._status = NodeUpdateStatus.model_validate(payload)
                self._status_at = self._clock()
            elif topic == NODE_UPDATE:
                return [self._request(NodeUpdate.model_validate(payload))]
            elif topic == RESULT_READING:
                return self._root_said(RootResult.model_validate(payload))
        except ValidationError as exc:
            log.warning("ignoring malformed %s: %s", topic, exc.errors(include_url=False))
        return []

    def _request(self, cmd: NodeUpdate) -> tuple[str, dict]:
        problem = self._problem(cmd)
        if problem:
            log.info("update refused for %s (%s): %s", cmd.requested_by, cmd.door, problem)
            return self._result(cmd, done=True, message=f"update refused: {problem}")
        st = self._status
        request = UpdateRequest(request_id=cmd.request_id, channel=st.channel,
                                expected_commit=cmd.expected_commit, requested_by=cmd.requested_by,
                                door=cmd.door, requested_at=self._now())
        problem = self._requester.submit(request)
        if problem:
            return self._result(cmd, done=True, message=f"update refused: {problem}")
        log.warning("update to %s %s requested by %s via the %s door", st.available.ref,
                    cmd.expected_commit[:12], cmd.requested_by, cmd.door)
        return self._result(cmd, done=False, phase="requested",
                            message=f"update to {st.available.ref} requested; the root updater "
                                    "takes it from here, and the dashboard restarts during it")

    def _problem(self, cmd: NodeUpdate) -> str | None:
        st = self._status
        if not COMMIT.fullmatch(cmd.expected_commit):
            return "that is not a commit"
        if st is None or not is_fresh(self._status_at, st.period_s, self._clock()):
            return "there is no fresh update status; check again"
        if st.checking:
            return "a check is running; try again when it finishes"
        if not st.ok or st.available is None:
            return "the channel offers nothing now"
        if st.channel not in CHANNELS:
            return f"unknown update channel {st.channel!r}"
        if st.available.commit != cmd.expected_commit:
            return "the offer changed since you looked; check again"
        if st.relation not in ACTIONABLE:
            return f"{st.available.ref} is not newer than what runs ({st.relation or 'nothing recorded'})"
        if self._requester.pending() or (self._root is not None and not self._root.done):
            return "an update is already requested or running"
        return None

    def _root_said(self, root: RootResult) -> list[tuple[str, dict]]:
        self._root = root
        key = (root.request_id, root.done, root.phase, root.message, root.finished_at)
        if key == self._published:
            return []
        self._published = key
        return [(NODE_UPDATE_RESULT, NodeUpdateResult.stamped(
            CONTRACT_VERSION, request_id=root.request_id, done=root.done, ok=root.ok,
            phase=root.phase, message=root.message, rolled_back=root.rolled_back,
            from_version=_version(root.from_commit, root.from_ref, root.from_describe),
            to_version=_version(root.to_commit, root.to_ref, root.to_describe),
            requested_by=root.requested_by, door=root.door, started_at=root.started_at,
            finished_at=root.finished_at).model_dump())]

    def _result(self, cmd: NodeUpdate, done: bool, message: str, phase: str = "") -> tuple[str, dict]:
        st = self._status
        offered = st.available if st is not None else None
        return (NODE_UPDATE_RESULT, NodeUpdateResult.stamped(
            CONTRACT_VERSION, request_id=cmd.request_id, done=done, ok=False, phase=phase,
            message=message, requested_by=cmd.requested_by, door=cmd.door,
            from_version=st.running if st is not None else None,
            to_version=offered if offered is not None and offered.commit == cmd.expected_commit else None,
            started_at=self._now(), finished_at=self._now() if done else None).model_dump())


def _version(commit: str, ref: str, describe: str) -> NodeVersion | None:
    return NodeVersion(commit=commit, ref=ref, describe=describe) if COMMIT.fullmatch(commit) else None
