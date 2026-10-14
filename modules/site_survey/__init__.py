"""site-survey — the module's composition point (spec §4): wires core and features.

`scanner` defaults to the nmap adapter (ADR-0007); tests inject a fake and an inline `spawn`.
"""
from __future__ import annotations

import time
from typing import Callable

from .core.input import Subscribe, attach
from .core.nmap_scanner import NmapScanner
from .core.output import Output, Publish
from .core.registry import Registry
from .core.scan_worker import ScanWorker
from .features.site_scanner import SiteScanner
from .interface import NetworkScanner, ScanSlot


def build(subscribe: Subscribe, publish: Publish, scanner: NetworkScanner | None = None,
          clock: Callable[[], float] = time.monotonic, spawn: Callable | None = None) -> Registry:
    registry = Registry()
    slot = ScanSlot()
    ScanWorker(slot, scanner or NmapScanner(), **({"spawn": spawn} if spawn is not None else {}))
    registry.register(SiteScanner(slot, clock))
    attach(registry, subscribe, Output(publish), slot)
    return registry
