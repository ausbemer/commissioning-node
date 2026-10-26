"""node-updater — the module's composition point (spec §4): wires core and features.

Without a `source` nothing is checked: the feature is registered disabled and nothing talks to
GitHub. The composition root passes the GitHub adapter in daemon mode only, so tests never
reach the network. Tests pass a fake source and running record and an inline `spawn`.
"""
from __future__ import annotations

import time
from typing import Callable

from .core.check_worker import CheckWorker
from .core.github_source import GitHubReleaseSource
from .core.input import Subscribe, attach
from .core.output import Output, Publish
from .core.registry import Registry
from .core.running_record import RunningFile
from .core.update_files import RequestFile, ResultFile
from .features.update_apply import UpdateApply
from .features.update_check import UpdateCheck
from .interface import (CheckSlot, ConfigView, ReleaseSource, ResultSource, RunningSource,
                        UpdateRequester)

__all__ = ["build", "GitHubReleaseSource"]


def build(subscribe: Subscribe, publish: Publish, source: ReleaseSource | None = None,
          running: RunningSource | None = None, config: ConfigView | None = None,
          clock: Callable[[], float] = time.monotonic, spawn: Callable | None = None,
          apply: bool = False, requester: UpdateRequester | None = None,
          results: ResultSource | None = None) -> Registry:
    """`apply` enables the confirmed update (`update-apply`, ADR-0009 D3): registered disabled
    otherwise, and then neither the request nor the result file is touched."""
    registry = Registry()
    slot = CheckSlot()
    if source is not None:
        CheckWorker(slot, source, running or RunningFile(), **({"spawn": spawn} if spawn is not None else {}))
    registry.register(UpdateCheck(slot, config, clock), enabled=source is not None)
    registry.register(UpdateApply(requester or RequestFile(), clock), enabled=apply)
    attach(registry, subscribe, Output(publish), slot, (results or ResultFile()) if apply else None)
    return registry
