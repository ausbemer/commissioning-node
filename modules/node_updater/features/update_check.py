"""update-check — when to check the update channel, and what the node is told (ADR-0009 D2).

Read-only: it never updates anything. It checks on the first tick, then every `UPDATE_CHECK_S`
(`UPDATE_RETRY_S` after a failed check, unless GitHub refused for its rate limit), when the operator changes the channel, and on the
operator's `node/update-check`. One check at a time (the `CheckSlot`); an operator's check
within `CHECK_MIN_GAP_S` of the last is declined, so a busy finger can't spend the network's
GitHub API allowance.

Every tick it publishes `NodeUpdateStatus`, a renewed level. Only a check that worked can
offer anything, and only a commit newer than what runs is an update (`relation` "ahead"): a
failed or partial check fails toward no offer (rule 10).
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from pydantic import ValidationError

from interfaces import (NODE_UPDATE_CHECK, NODE_UPDATE_STATUS, SYSTEM_TICK, NodeUpdateCheck,
                        NodeUpdateStatus, NodeVersion, Tick, __version__ as CONTRACT_VERSION)

from ..interface import (CHANNELS, CHECK_OUTCOME, MODULE, RELEASES, CheckOutcome, CheckPlan,
                         CheckSlot, ConfigView, RunningRecord)

log = logging.getLogger(MODULE)

#: PROVISIONAL — tools/ledgers/node-updater/ (§8.11). Scheduled checks, a retry after a failed
#: one, and the shortest gap an operator's check may follow the last.
UPDATE_CHECK_S = 1800.0
UPDATE_RETRY_S = 300.0
CHECK_MIN_GAP_S = 30.0

#: What a relation means for people, when the check found a target.
_RELATION = {
    "ahead": "an update is available",
    "identical": "up to date",
    "behind": "what runs is newer than the channel's offer",
    "diverged": "what runs and the channel's offer have diverged",
    "unknown": "GitHub doesn't have the commit that runs (unpushed or local changes?)",
}


class UpdateCheck:
    name = "update-check"
    subscriptions = [SYSTEM_TICK, NODE_UPDATE_CHECK, CHECK_OUTCOME]

    def __init__(self, slot: CheckSlot, config: ConfigView | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._slot, self._config, self._clock = slot, config, clock
        self._period_s: float | None = None
        self._started_at: float | None = None
        self._outcome: CheckOutcome | None = None
        self._notice, self._notice_at = "", 0.0

    def handle(self, topic: str, payload: dict) -> list[tuple[str, dict]]:
        try:
            if topic == SYSTEM_TICK:
                self._period_s = Tick.model_validate(payload).period_s
                if self._due():
                    self._submit(CheckPlan(channel=self._channel()))
                return [self._status()]
            if topic == NODE_UPDATE_CHECK:
                self._requested(NodeUpdateCheck.model_validate(payload))
            elif topic == CHECK_OUTCOME:
                self._outcome = CheckOutcome.model_validate(payload)
                return [self._status()]
        except ValidationError as exc:
            log.warning("ignoring malformed %s: %s", topic, exc.errors(include_url=False))
        return []

    # -- deciding ------------------------------------------------------------------------

    def _channel(self) -> str:
        value = self._config.get("update_channel") if self._config is not None else None
        return value if isinstance(value, str) else RELEASES

    def _due(self) -> bool:
        if self._slot.busy():
            return False
        if self._started_at is None or self._outcome is None:
            return self._started_at is None
        if self._outcome.plan.channel != self._channel():
            return True
        reading = self._outcome.reading
        # A rate-limited failure waits the full period: retrying sooner only spends more.
        wait = UPDATE_CHECK_S if reading.ok or reading.rate_limited else UPDATE_RETRY_S
        return self._clock() - self._started_at >= wait

    def _requested(self, cmd: NodeUpdateCheck) -> None:
        if self._slot.busy():
            return
        if self._started_at is not None and self._clock() - self._started_at < CHECK_MIN_GAP_S:
            self._notice, self._notice_at = "checked moments ago; try again in a minute", self._clock()
            return
        self._submit(CheckPlan(channel=self._channel(), requested_by=cmd.requested_by, door=cmd.door,
                               request_id=cmd.request_id))

    def _submit(self, plan: CheckPlan) -> None:
        if plan.channel not in CHANNELS:
            self._notice, self._notice_at = (f"unknown update channel {plan.channel!r}; set releases "
                                             "or dev on /config"), self._clock()
            return
        self._started_at = self._clock()
        self._slot.submit(plan)

    # -- telling -------------------------------------------------------------------------

    def _status(self) -> tuple[str, dict]:
        out = self._outcome
        fields: dict = {}
        if out is None:
            message = "not checked yet"
        else:
            reading = out.reading
            relation = reading.relation if reading.target and out.running else ""
            fields = dict(running=_running(out.running), running_mode=out.running.mode if out.running else "",
                          running_clean=out.running.clean if out.running else None,
                          available=reading.target if reading.ok else None,
                          newest=reading.newest if reading.ok else None, relation=relation,
                          update_available=reading.ok and relation == "ahead", ok=reading.ok,
                          checked_at=out.finished_at)
            if not reading.ok:
                message = reading.message
            elif reading.target is None:
                message = reading.message
            elif out.running is None:
                message = "; ".join(x for x in (reading.message, "nothing records what this node runs "
                                                                 "(re-run sudo ops/install.sh)") if x)
            else:
                message = "; ".join(x for x in (_RELATION.get(relation, relation), reading.message) if x)
        if self._notice and self._clock() - self._notice_at < CHECK_MIN_GAP_S:
            message = f"{message} ({self._notice})"
        return (NODE_UPDATE_STATUS, NodeUpdateStatus.stamped(
            CONTRACT_VERSION, channel=out.plan.channel if out else self._channel(), message=message,
            checking=self._slot.busy(), check_period_s=UPDATE_CHECK_S,
            period_s=self._period_s or 0.0, origin=f"{MODULE}/{self.name}", **fields).model_dump())


def _running(record: RunningRecord | None) -> NodeVersion | None:
    if record is None:
        return None
    return NodeVersion(commit=record.commit, ref=record.ref, describe=record.describe)
