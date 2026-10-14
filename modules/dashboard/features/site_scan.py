"""site-scan — the operator's Scan as a dashboard write (ADR-0007 D6).

An `Action` with the scan's may-disturb warning, so `authorize()` makes the caller confirm.
`run` sends a `site/scan` command through the injected `Invoker` (§5): the command names the
caller and carries no targets; site-survey scans the networks the node is on.

The answer is immediate ("requested"); the outcome arrives on `site/scan-result` and the hosts
on `site/hosts`, and both are shown on the overview's map.
"""
from __future__ import annotations

import uuid
from typing import Mapping

from interfaces import SITE_SCAN, SiteScan, __version__ as CONTRACT_VERSION

from ..interface import SITE_OPERATIONS, SITE_SCANNER, ActionResult, Caller, Invoker


class SiteScanAction:
    name = "site.scan"

    def __init__(self, invoke: Invoker) -> None:
        self._invoke = invoke
        self.path, self.may_disturb = SITE_OPERATIONS["scan"]

    def run(self, form: Mapping[str, str], caller: Caller) -> ActionResult:
        command = SiteScan.stamped(CONTRACT_VERSION, request_id=uuid.uuid4().hex[:12],
                                   requested_by=caller.label, door=caller.door)
        ok, message = self._invoke(SITE_SCAN, command.model_dump(), SITE_SCANNER)
        if not ok:
            return ActionResult(False, message)
        # Delivered, not accepted: site-survey may still refuse it (busy, stale status), and
        # says so on site/scan-result, which the overview shows.
        return ActionResult(True, f"scan request sent (request {command.request_id}); "
                                  "the outcome appears on the overview")
