"""node-update — the operator's "Check now" for updates, as a dashboard write (ADR-0009 D5).

An `Action` like any other: `authorize()` decides who may, from which door. It only asks
node-updater to read GitHub now, so it disturbs nothing and has no warning. `run` sends a
`node/update-check` through the injected `Invoker` (§5), naming the caller and carrying no
values: the channel is the node's own config. The finding arrives on `node/update-status`.
"""
from __future__ import annotations

import uuid
from typing import Mapping

from interfaces import NODE_UPDATE_CHECK, NodeUpdateCheck, __version__ as CONTRACT_VERSION

from ..interface import NODE_UPDATE_CHECKER, UPDATE_CHECK_PATH, ActionResult, Caller, Invoker


class NodeUpdateCheckAction:
    name = "node.update-check"
    path = UPDATE_CHECK_PATH
    may_disturb = None

    def __init__(self, invoke: Invoker) -> None:
        self._invoke = invoke

    def run(self, form: Mapping[str, str], caller: Caller) -> ActionResult:
        command = NodeUpdateCheck.stamped(CONTRACT_VERSION, request_id=uuid.uuid4().hex[:12],
                                          requested_by=caller.label, door=caller.door)
        ok, message = self._invoke(NODE_UPDATE_CHECK, command.model_dump(), NODE_UPDATE_CHECKER)
        if not ok:
            return ActionResult(False, message)
        return ActionResult(True, "update check requested; the finding appears on the overview")
