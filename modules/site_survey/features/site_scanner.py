"""scanner — decides whether a site scan may run now, and publishes what it found (ADR-0007).

A scan runs only on an operator's `site/scan` (Safety 2): nothing here starts one on its own.
The networks come from tailnet-gateway, their authority (`TailnetStatus.site_networks`), and
only while that status is fresh: a stale status may describe a site the node has left, so
the scan fails toward not running (§5, rule 10). Freshness is the tick's declared cadence
through `freshness`, as stream-relay judges the same feed.

Every refusal and every outcome is a `SiteScanResult`, so the operator always learns why
(§11 rung 7). A completed scan also publishes the `SiteHosts` snapshot; a failed one leaves
the previous snapshot standing. Hosts live in memory only (Safety 6): this feature keeps no
copy at all once published.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from pydantic import ValidationError

from freshness import is_fresh
from interfaces import (SITE_HOSTS, SITE_SCAN, SITE_SCAN_RESULT, SYSTEM_TICK, TAILNET_STATUS,
                        SiteHosts, SiteScan, SiteScanResult, TailnetStatus, Tick,
                        __version__ as CONTRACT_VERSION)

from ..interface import MODULE, SCAN_OUTCOME, ScanOutcome, ScanPlan, ScanSlot, scan_problem

log = logging.getLogger(MODULE)


class SiteScanner:
    name = "scanner"
    subscriptions = [SITE_SCAN, TAILNET_STATUS, SYSTEM_TICK, SCAN_OUTCOME]

    def __init__(self, slot: ScanSlot, clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], float] = time.time) -> None:
        self._slot, self._clock, self._now = slot, clock, now
        self._networks: list[str] = []
        self._status_at: float | None = None
        self._period_s: float | None = None

    def handle(self, topic: str, payload: dict) -> list[tuple[str, dict]]:
        try:
            if topic == TAILNET_STATUS:
                self._networks = list(TailnetStatus.model_validate(payload).site_networks)
                self._status_at = self._clock()
            elif topic == SYSTEM_TICK:
                self._period_s = Tick.model_validate(payload).period_s
            elif topic == SITE_SCAN:
                return self._request(SiteScan.model_validate(payload))
            elif topic == SCAN_OUTCOME:
                return self._finished(ScanOutcome.model_validate(payload))
        except ValidationError as exc:
            log.warning("ignoring malformed %s: %s", topic, exc.errors(include_url=False))
        return []

    def _request(self, cmd: SiteScan) -> list[tuple[str, dict]]:
        if not is_fresh(self._status_at, self._period_s, self._clock()):
            return [self._result(cmd, ok=False, message="scan refused: the site networks are unknown "
                                                        "(no fresh status from tailnet-gateway)")]
        problem = scan_problem(self._networks)
        if problem:
            return [self._result(cmd, ok=False, message=f"scan refused: {problem}")]
        plan = ScanPlan(request_id=cmd.request_id, requested_by=cmd.requested_by, door=cmd.door,
                        networks=self._networks)
        running = self._slot.submit(plan)
        if running is not None:
            return [self._result(cmd, ok=False, message=f"scan refused: a scan is already running "
                                                        f"(requested by {running.requested_by})")]
        return []

    def _finished(self, outcome: ScanOutcome) -> list[tuple[str, dict]]:
        plan, reply = outcome.plan, outcome.reply
        out = []
        if reply.ok:
            out.append((SITE_HOSTS, SiteHosts.stamped(
                CONTRACT_VERSION, scanned_at=outcome.finished_at, networks=plan.networks,
                hosts=reply.hosts, self_addresses=reply.self_addresses, request_id=plan.request_id,
                requested_by=plan.requested_by, origin=f"{MODULE}/{self.name}").model_dump()))
        out.append((SITE_SCAN_RESULT, SiteScanResult.stamped(
            CONTRACT_VERSION, request_id=plan.request_id, ok=reply.ok, message=reply.message,
            requested_by=plan.requested_by, door=plan.door, finished_at=outcome.finished_at,
            host_count=len(reply.hosts) if reply.ok else 0).model_dump()))
        return out

    def _result(self, cmd: SiteScan, ok: bool, message: str) -> tuple[str, dict]:
        return (SITE_SCAN_RESULT, SiteScanResult.stamped(
            CONTRACT_VERSION, request_id=cmd.request_id, ok=ok, message=message,
            requested_by=cmd.requested_by, door=cmd.door, finished_at=self._now()).model_dump())
