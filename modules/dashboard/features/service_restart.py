"""service-restart — restart the commissioning-node service from the dashboard.

A write like any other: an `Action` behind `authorize()`, may-disturb, so the caller
confirms. `run` only asks the composition root to restart (the injected `restart`); main
answers the request first, then exits with its restart status, and systemd starts the
service again, running the code checked out now. tailscaled, MediaMTX and the streams are
separate services and keep running.
"""
from __future__ import annotations

import logging
from typing import Callable, Mapping

from ..interface import MODULE, RESTART_PATH, RESTART_WARNING, ActionResult, Caller

log = logging.getLogger(MODULE)


class ServiceRestartAction:
    name = "node.restart"
    path = RESTART_PATH
    may_disturb = RESTART_WARNING

    def __init__(self, restart: Callable[[], None]) -> None:
        self._restart = restart

    def run(self, form: Mapping[str, str], caller: Caller) -> ActionResult:
        log.warning("service restart requested by %s via the %s door", caller.label, caller.door)
        self._restart()
        return ActionResult(True, "restarting; the dashboard is back in about 10 s")
