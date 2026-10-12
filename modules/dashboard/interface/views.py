"""The View protocol — how features contribute pages to the HTTP surface core serves.

Core does the I/O (sockets, headers); a View turns a `DashboardState.snapshot()` into a
response body. Keeping rendering in features keeps core logic-free (spec §4).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class View(Protocol):
    #: URL path this view answers (GET only), e.g. "/".
    path: str
    #: Response Content-Type, e.g. "text/html; charset=utf-8".
    content_type: str

    def render(self, snapshot: dict) -> str:
        """Pure: snapshot in, body out. No I/O."""
        ...
