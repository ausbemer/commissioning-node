"""dashboard — the module's composition point (spec §4): wires core and features.

Returns the registry (bus side) and the HTTP surface (operator side). The composition root
decides when to start the surface and passes the fallback's files (ADR-0003).
"""
from __future__ import annotations

from .core.http_surface import FallbackConfig, HttpSurface
from .core.input import Subscribe, attach
from .core.output import Output, Publish
from .core.registry import Registry
from .features.config_editor import config_actions
from .features.config_page import ConfigPage
from .features.login_page import LoginPage
from .features.overview_page import OverviewPage
from .features.peer_directory import PeerDirectoryFeed
from .features.state_collector import StateCollector
from .features.service_restart import ServiceRestartAction
from .features.tailnet_apply import tailnet_apply_actions
from typing import Callable

from .interface import Action, AdminAuth, ConfigSurface, DashboardState, Invoker, PeerDirectory

__all__ = ["build", "FallbackConfig", "HttpSurface"]


def build(subscribe: Subscribe, publish: Publish, host: str = "::", port: int = 8080,
          fallback: FallbackConfig | None = None, admin_cred: str | None = None,
          actions: list[Action] | None = None,
          config: ConfigSurface | None = None,
          invoke: Invoker | None = None,
          restart: Callable[[], None] | None = None) -> tuple[Registry, HttpSurface]:
    """`fallback` + `admin_cred` enable the TLS admin door; without them it never starts.
    `config` is the config registry's operator surface (ADR-0004): it adds the /config page
    and a set and clear `Action` per field. `invoke` (main.invoke, §5) adds the tailnet Join,
    Rename and Leave actions (ADR-0005). `restart` (main's) adds the service restart on
    /config. `actions` are any further writes."""
    state = DashboardState()
    peers = PeerDirectory()
    registry = Registry()
    registry.register(StateCollector(state))
    registry.register(PeerDirectoryFeed(peers, state))
    attach(registry, subscribe, Output(publish))
    admin = AdminAuth(admin_cred) if admin_cred else None
    writes = list(actions or []) + (config_actions(config) if config is not None else [])
    writes += tailnet_apply_actions(invoke) if invoke is not None else []
    writes += [ServiceRestartAction(restart)] if restart is not None else []
    return registry, HttpSurface(state, [OverviewPage(), ConfigPage()], host, port, peers,
                                 actions=writes, admin=admin, fallback=fallback,
                                 login_view=LoginPage(), config=config)
