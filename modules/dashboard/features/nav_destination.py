"""nav-destination — start and stop sending nav data to a site host, as dashboard writes
(ADR-0010 D4, D7).

Send is may-disturb (`NAV_OPERATIONS`), so `authorize()` makes the caller confirm the warning.
The form carries the host the operator picked from the scan and a UDP port. That pick is only a
request: nav-relay re-checks the address against its own copy of the last scan (rung 13), so a
crafted form can't point the sender anywhere else. Stop needs no confirm.

Each run sends `nav/destination` through the injected `Invoker` (§5). The answer is
"requested"; the outcome arrives on `nav/destination-result`, which the overview shows.
"""
from __future__ import annotations

import uuid
from typing import Mapping

from interfaces import NAV_DESTINATION, NavDestination, __version__ as CONTRACT_VERSION

from ..interface import NAV_OPERATIONS, NAV_SENDER, ActionResult, Caller, Invoker

DEFAULT_PORT = 2947


class NavAction:
    def __init__(self, operation: str, invoke: Invoker) -> None:
        self.operation, self._invoke = operation, invoke
        self.name = f"nav.{operation}"
        self.path, self.may_disturb = NAV_OPERATIONS[operation]

    def run(self, form: Mapping[str, str], caller: Caller) -> ActionResult:
        address = (form.get("address") or "").strip() or None
        port = DEFAULT_PORT
        if self.operation == "send":
            if address is None:
                return ActionResult(False, "pick a host from the last scan")
            raw = (form.get("port") or "").strip()
            if raw:
                if not raw.isdigit() or not 1 <= int(raw) <= 65535:
                    return ActionResult(False, f"port must be 1–65535, not {raw[:16]!r}")
                port = int(raw)
        command = NavDestination.stamped(CONTRACT_VERSION, request_id=uuid.uuid4().hex[:12],
                                         requested_by=caller.label, door=caller.door,
                                         operation=self.operation, address=address, port=port)
        ok, message = self._invoke(NAV_DESTINATION, command.model_dump(), NAV_SENDER)
        if not ok:
            return ActionResult(False, message)
        return ActionResult(True, f"nav {self.operation} request sent (request {command.request_id}); "
                                  "the outcome appears on the overview")


def nav_actions(invoke: Invoker) -> list[NavAction]:
    return [NavAction(op, invoke) for op in NAV_OPERATIONS]
