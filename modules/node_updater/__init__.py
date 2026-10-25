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
from .features.update_check import UpdateCheck
from .interface import CheckSlot, ConfigView, ReleaseSource, RunningSource

__all__ = ["build", "GitHubReleaseSource"]


def build(subscribe: Subscribe, publish: Publish, source: ReleaseSource | None = None,
          running: RunningSource | None = None, config: ConfigView | None = None,
          clock: Callable[[], float] = time.monotonic, spawn: Callable | None = None) -> Registry:
    registry = Registry()
    slot = CheckSlot()
    if source is not None:
        CheckWorker(slot, source, running or RunningFile(), **({"spawn": spawn} if spawn is not None else {}))
    registry.register(UpdateCheck(slot, config, clock), enabled=source is not None)
    attach(registry, subscribe, Output(publish), slot)
    return registry
