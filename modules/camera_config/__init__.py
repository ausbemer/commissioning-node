"""camera-config — the module's composition point (spec §4): wires core and features.

The composition root calls `build()` and injects the bus functions; the module never
imports the bus itself.
"""
from __future__ import annotations

from .core.input import Subscribe, attach
from .core.output import Output, Publish
from .core.registry import Registry
from .features.camera_registry import CameraRegistry


def build(subscribe: Subscribe, publish: Publish) -> Registry:
    registry = Registry()
    registry.register(CameraRegistry())
    attach(registry, subscribe, Output(publish))
    return registry
