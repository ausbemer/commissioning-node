"""Vendor-neutral seams (spec §4): protocols here, one adapter per vendor in core/.

A second overlay (or a fake for tests) is one new adapter and zero feature changes.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import OverlaySnapshot


@runtime_checkable
class OverlayStatusSource(Protocol):
    """Reads the overlay's current state. Read-only: never changes anything (§11 rung 1).

    Must not raise: an unavailable overlay is reported as `connected=False`.
    """

    def read(self) -> OverlaySnapshot: ...
