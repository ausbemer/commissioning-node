"""The Action protocol — how features contribute writes to the HTTP surface (ADR-0003 §4).

An Action is a named POST endpoint. Core does the I/O and passes every request through the
one `authorize()` (doors.py) before calling `run`; an Action never checks who is calling.
`may_disturb` is the §11 rung-4 warning: when set, the caller must confirm (`confirm=yes`)
and sees the warning first. The same fields feed the action manifest (§5) when it exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .doors import Caller


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    #: For people: what happened, or why not (§11 rung 7: refusals are in-band).
    message: str


@runtime_checkable
class Action(Protocol):
    #: Stable identifier, e.g. "tailnet.set-hostname". Recorded with every write.
    name: str
    #: POST path this action answers, e.g. "/actions/tailnet/hostname".
    path: str
    #: Rung-4 warning if this action may disturb the node or the caller's session; else None.
    may_disturb: str | None

    def run(self, form: Mapping[str, str], caller: "Caller") -> ActionResult:
        """Do the write. Only ever called after `authorize()` allowed it."""
        ...
