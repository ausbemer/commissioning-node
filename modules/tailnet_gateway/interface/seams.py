"""Vendor-neutral seams (spec §4): protocols here, one adapter per vendor in core/.

Three seams: `OverlayStatusSource` and `SiteNetworkSource` read (every tick), `OverlayWriter`
writes (ADR-0005).

A second overlay (or a fake for tests) is one new adapter and zero feature changes.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from interfaces import Shape

from .models import OverlaySnapshot


@runtime_checkable
class OverlayStatusSource(Protocol):
    """Reads the overlay's current state. Read-only: never changes anything (§11 rung 1).

    Must not raise: an unavailable overlay is reported as `connected=False`.
    """

    def read(self) -> OverlaySnapshot: ...


class WriterReply(Shape):
    """What an `OverlayWriter` call did. `message` is for people and never holds a secret."""

    ok: bool
    message: str


@runtime_checkable
class OverlayWriter(Protocol):
    """Changes the overlay's state (ADR-0005). The ONLY mutating seam in this module (§11
    rung 9); core runs it on the writer worker, one call at a time.

    Must not raise: every failure, including a timeout, is a reply with `ok=False`. Each
    call re-checks its own inputs before acting (rung 13).
    """

    def join(self, hostname: str | None, tags: list[str], key_path: str,
             routes: list[str] = ()) -> WriterReply: ...

    def rename(self, hostname: str) -> WriterReply: ...

    def set_routes(self, routes: list[str]) -> WriterReply: ...

    def leave(self) -> WriterReply: ...


@runtime_checkable
class SiteNetworkSource(Protocol):
    """The IPv4 networks (CIDR) this host is on now, outside loopback and the overlay
    (ADR-0005 A3). Read-only. Must not raise: unreadable means an empty list."""

    def read(self) -> list[str]: ...
