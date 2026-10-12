"""tailnet-gateway — the module's composition point (spec §4): wires core and features.

`source` defaults to the real (read-only) Tailscale CLI adapter; tests inject a fake.
"""
from __future__ import annotations

from .core.input import Subscribe, attach
from .core.output import Output, Publish
from .core.registry import Registry
from .core.tailscale_cli import TailscaleCliStatusSource
from .features.status_report import StatusReport
from .interface import OverlayStatusSource


def build(subscribe: Subscribe, publish: Publish, source: OverlayStatusSource | None = None) -> Registry:
    registry = Registry()
    registry.register(StatusReport())
    attach(registry, subscribe, Output(publish), source or TailscaleCliStatusSource())
    return registry
