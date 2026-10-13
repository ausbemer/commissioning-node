"""tailnet-apply — the Join, Rename and Leave buttons as dashboard writes (ADR-0005 §3).

Each is an `Action` with its own may-disturb warning, so `authorize()` makes the caller
confirm, and adds the LAN-fallback recovery note on the tailnet door. `run` sends a
`tailnet/apply` command through the injected `Invoker` (§5): the command names the operation
and the caller, and carries no values. tailnet-gateway reads the saved ones itself.

The answer is immediate ("requested"); the outcome arrives on `tailnet/apply-result` and is
shown on /config.
"""
from __future__ import annotations

import uuid
from typing import Mapping

from interfaces import TAILNET_APPLY, TailnetApply, __version__ as CONTRACT_VERSION

from ..interface import TAILNET_OPERATIONS, TAILNET_WRITER, ActionResult, Caller, Invoker

class TailnetApplyAction:
    def __init__(self, invoke: Invoker, operation: str) -> None:
        self._invoke = invoke
        self.operation = operation
        self.name = f"tailnet.{operation}"
        self.path, self.may_disturb = TAILNET_OPERATIONS[operation]

    def run(self, form: Mapping[str, str], caller: Caller) -> ActionResult:
        command = TailnetApply.stamped(CONTRACT_VERSION, request_id=uuid.uuid4().hex[:12],
                                       operation=self.operation, requested_by=caller.label,
                                       door=caller.door)
        ok, message = self._invoke(TAILNET_APPLY, command.model_dump(), TAILNET_WRITER)
        if not ok:
            return ActionResult(False, message)
        return ActionResult(True, f"{self.operation} requested (request {command.request_id}); "
                                  "the result appears on /config")


def tailnet_apply_actions(invoke: Invoker) -> list[TailnetApplyAction]:
    return [TailnetApplyAction(invoke, op) for op in TAILNET_OPERATIONS]
