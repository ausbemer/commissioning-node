"""dashboard — the module's composition point (spec §4): wires core and features.

Returns the registry (bus side) and the HTTP surface (operator side). The composition root
decides when to start the surface and passes the fallback's files (ADR-0003).
"""
from __future__ import annotations

from .core.http_surface import FallbackConfig, HttpSurface
from .core.input import Subscribe, attach
from .core.output import Output, Publish
from .core.registry import Registry
from .features.login_page import LoginPage
from .features.overview_page import OverviewPage
from .features.peer_directory import PeerDirectoryFeed
from .features.state_collector import StateCollector
from .interface import Action, AdminAuth, DashboardState, PeerDirectory

__all__ = ["build", "FallbackConfig", "HttpSurface"]


def build(subscribe: Subscribe, publish: Publish, host: str = "::", port: int = 8080,
          fallback: FallbackConfig | None = None, admin_cred: str | None = None,
          actions: list[Action] | None = None) -> tuple[Registry, HttpSurface]:
    """`fallback` + `admin_cred` enable the TLS admin door; without them it never starts.
    `actions` are the writes the surface offers; none ship yet (the config registry, step 5)."""
    state = DashboardState()
    peers = PeerDirectory()
    registry = Registry()
    registry.register(StateCollector(state))
    registry.register(PeerDirectoryFeed(peers, state))
    attach(registry, subscribe, Output(publish))
    admin = AdminAuth(admin_cred) if admin_cred else None
    return registry, HttpSurface(state, [OverviewPage()], host, port, peers, actions=actions,
                                 admin=admin, fallback=fallback, login_view=LoginPage())
