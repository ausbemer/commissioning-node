"""commissioning-node composition root (spec §4): composes modules onto the bus and owns
the serve loop. Imports everything; imported by nothing (tests excepted).

Daemon mode (`python main.py`): starts the dashboard (8080: local + tailnet doors; 8443: the
TLS admin fallback when a password is set, ADR-0003), then ticks on a
wall-clock deadline (§8.8). Each tick publishes `system/tick` (the cadence is declared in
the payload) and the node card. SIGINT/SIGTERM stop cleanly. There are no one-shot CLI
modes yet. When one arrives, it runs on a private Bus instance (§4).

Bus delivery is SYNCHRONOUS (§6): a handler runs inside `publish`, so a slow handler stalls
the tick loop. The tailnet status read is bounded by its own timeout. The first slow
feature gets a worker thread that queues results and drains them on the next tick.
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import interfaces
import modules.camera_config as camera_config
import modules.camera_config.interface as camera_config_if
import modules.dashboard as dashboard
import modules.dashboard.interface as dashboard_if
import modules.node_updater as node_updater
import modules.node_updater.interface as node_updater_if
import modules.site_survey as site_survey
import modules.site_survey.interface as site_survey_if
import modules.stream_relay as stream_relay
import modules.stream_relay.interface as stream_relay_if
import modules.tailnet_gateway as tailnet_gateway
import modules.tailnet_gateway.interface as tailnet_gateway_if
from bus import Bus
from config_registry import ConfigRegistry

log = logging.getLogger("main")

#: Tick cadence, seconds. PROVISIONAL — not yet measured (TODO.md, §8.11).
TICK_PERIOD_S = 5.0
#: Exit status that asks systemd to start the service again (the dashboard's restart). The
#: unit's Restart=on-failure restarts any non-zero exit; ops marks 75 as a forced restart
#: and a success, so the journal doesn't call it a failure.
RESTART_EXIT = 75
#: Grace between answering the restart request and stopping, so the 303 reaches the browser.
#: Not a behaviour constant: nothing on the node depends on it.
RESTART_GRACE_S = 1.0
#: Every address: the dashboard sorts each request into a door itself, and the firewall
#: drops 8080 off lo/tailscale0 (ADR-0003 §1).
DASHBOARD_HOST = "::"
DASHBOARD_PORT_DEFAULT = 8080
#: The admin fallback (ADR-0003 §3). Paths match ops/paths.sh, which creates the files.
FALLBACK_PORT = 8443
ADMIN_CRED_PATH = "/etc/commissioning-node/admin.cred"
TLS_CERT_PATH = "/etc/commissioning-node/tls/fallback.crt"
TLS_KEY_PATH = "/etc/commissioning-node/tls/fallback.key"
#: The config registry's root (ADR-0004). systemd creates it (StateDirectory=); matches
#: CN_STATE in ops/paths.sh.
CONFIG_ROOT = "/var/lib/commissioning-node"

INTERFACES = {
    "camera-config": camera_config_if,
    "dashboard": dashboard_if,
    "node-updater": node_updater_if,
    "site-survey": site_survey_if,
    "stream-relay": stream_relay_if,
    "tailnet-gateway": tailnet_gateway_if,
}


@dataclass
class Node:
    bus: Bus
    registries: list = field(default_factory=list)
    dashboard: dashboard.HttpSurface | None = None
    config: ConfigRegistry | None = None

    def capabilities(self) -> set[str]:
        """The in-process capability registry (§5): every enabled "module:feature" flag."""
        return {flag for r in self.registries for flag in r.capabilities()}

    def card(self) -> dict:
        """This node's read-only self-description (§5), as a stamped NodeCard dict."""
        mods = [
            interfaces.ModuleInfo(
                name=r.module,
                release_version=r.version,
                inward_version=INTERFACES[r.module].__version__,
                capabilities=r.capabilities(),
            )
            for r in self.registries
        ]
        return interfaces.NodeCard.stamped(
            interfaces.__version__,
            hostname=socket.gethostname(),
            contract_version=interfaces.__version__,
            modules=mods,
        ).model_dump()


def compose(bus: Bus, overlay_source=None, clock: Callable[[], float] = time.monotonic,
            dashboard_port: int = DASHBOARD_PORT_DEFAULT, dashboard_host: str = DASHBOARD_HOST,
            fallback: dashboard.FallbackConfig | None = None, admin_cred: str | None = None,
            actions: list | None = None, config_root: str | None = None,
            tailnet_writer: bool = False, overlay_writer=None, writer_spawn=None,
            site_networks=None, restart: Callable[[], None] | None = None,
            site_scanner=None, scan_spawn=None, release_source=None, running_source=None,
            update_spawn=None) -> Node:
    """Wire every module onto `bus`. Tests pass a fake overlay source, clock and listeners.

    With `config_root`, the config registry (ADR-0004, the one §6 bypass of the bus) is built
    from every module's declared `CONFIG_FIELDS`, and the dashboard gets its operator
    surface. Without it there is no config editing.

    `restart` (serve's) lets the dashboard restart the service.

    `tailnet_writer` enables tailnet-gateway's writer (ADR-0005; registered disabled
    otherwise). Tests pass a fake `overlay_writer` and an inline `writer_spawn`, and a fake
    `site_scanner` with an inline `scan_spawn` (ADR-0007).

    `release_source` turns on node-updater's channel check (ADR-0009). Daemon mode passes the
    GitHub adapter; without one nothing is checked, so tests never reach the network."""
    node = Node(bus=bus)
    sub, pub = bus.subscribe, bus.publish
    if config_root is not None:
        declared = [f for mod in INTERFACES.values() for f in getattr(mod, "CONFIG_FIELDS", [])]
        node.config = ConfigRegistry(config_root, declared)
    surface = node.config.operator_surface() if node.config else None
    dash_registry, node.dashboard = dashboard.build(
        sub, pub, dashboard_host, dashboard_port, fallback, admin_cred, actions, surface,
        invoke=lambda topic, payload, requires: invoke(node, topic, payload, requires),
        restart=restart)
    gateway_config = node.config.owner_view("tailnet-gateway") if node.config else None
    node.registries = [
        tailnet_gateway.build(sub, pub, overlay_source, config=gateway_config,
                              writer=overlay_writer, writer_enabled=tailnet_writer,
                              clock=clock, spawn=writer_spawn, networks=site_networks,
                              # The join record is site state: teardown wipes it (ADR-0005 A2).
                              state_dir=os.path.join(config_root, "site") if config_root else None),
        camera_config.build(sub, pub),
        stream_relay.build(sub, pub, clock),
        site_survey.build(sub, pub, site_scanner, clock, scan_spawn),
        node_updater.build(sub, pub, release_source, running_source,
                           config=node.config.owner_view("node-updater") if node.config else None,
                           clock=clock, spawn=update_spawn),
        dash_registry,
    ]
    return node


def invoke(node: Node, topic: str, payload: dict, requires: str) -> tuple[bool, str]:
    """Publish a command for an active invoker, refusing in-band when nobody can act on it (§5).

    `requires` is "module:feature". Returns (ok, message). Zero subscribers is a refusal,
    never a silent success.
    """
    module = requires.split(":", 1)[0]
    registry = next((r for r in node.registries if r.module == module), None)
    if requires not in node.capabilities():
        if registry is None:
            where = "is not running"
        elif requires in registry.registered():
            where = f"at v{registry.version} has it installed but disabled"
        else:
            where = f"at v{registry.version} predates support"
        msg = f"{requires} not available — service {module} {where}."
        log.warning(msg)
        return False, msg
    delivered = node.bus.publish(topic, payload)
    if delivered == 0:
        msg = f"{requires} not available — service {module} has no listener on {topic}."
        log.warning(msg)
        return False, msg
    return True, f"delivered to {delivered} handler(s)"


def tick(node: Node, period_s: float = TICK_PERIOD_S) -> None:
    """One orchestration tick: the heartbeat (declaring its cadence), then the card."""
    node.bus.publish(interfaces.SYSTEM_TICK,
                     interfaces.Tick.stamped(interfaces.__version__, at=time.time(), period_s=period_s).model_dump())
    node.bus.publish(interfaces.SYSTEM_CARD, node.card())


def _port_from_env() -> int:
    """Defensive config resolution (§4): a malformed value degrades to the default, never crashes boot."""
    raw = os.environ.get("CN_DASHBOARD_PORT", "")
    try:
        port = int(raw) if raw else DASHBOARD_PORT_DEFAULT
    except ValueError:
        log.warning("CN_DASHBOARD_PORT=%r is not an integer; using %d", raw, DASHBOARD_PORT_DEFAULT)
        return DASHBOARD_PORT_DEFAULT
    if not 1 <= port <= 65535:
        log.warning("CN_DASHBOARD_PORT=%d out of range; using %d", port, DASHBOARD_PORT_DEFAULT)
        return DASHBOARD_PORT_DEFAULT
    return port


def _writer_from_env() -> bool:
    """The tailnet writer (ADR-0005) is on since its rehearsal; CN_TAILNET_WRITER=0 is the
    off-switch. Anything but unset, 0 or 1, including a typo, turns it off and says so: a
    mistyped switch fails toward not writing."""
    raw = os.environ.get("CN_TAILNET_WRITER", "")
    if raw not in ("", "0", "1"):
        log.warning("CN_TAILNET_WRITER=%r is not 0 or 1; the tailnet writer is disabled", raw)
    elif raw == "0":
        log.warning("CN_TAILNET_WRITER=0: the tailnet writer is disabled (no join, leave or rejoin)")
    return raw in ("", "1")


def serve() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stop, restarting = threading.Event(), threading.Event()

    def restart() -> None:
        # Answer first: the dashboard's 303 goes out before the loop stops.
        restarting.set()
        threading.Timer(RESTART_GRACE_S, stop.set).start()

    node = compose(Bus(), dashboard_port=_port_from_env(),
                   fallback=dashboard.FallbackConfig(FALLBACK_PORT, TLS_CERT_PATH, TLS_KEY_PATH),
                   admin_cred=ADMIN_CRED_PATH, config_root=CONFIG_ROOT,
                   tailnet_writer=_writer_from_env(), restart=restart,
                   release_source=node_updater.GitHubReleaseSource())
    try:
        node.dashboard.start()
    except OSError as exc:
        log.error("dashboard not available — could not bind %s:%s (%s); running without it",
                  DASHBOARD_HOST, _port_from_env(), exc)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    log.info("commissioning-node up; capabilities: %s", ", ".join(sorted(node.capabilities())))
    deadline = time.monotonic()
    while not stop.is_set():
        tick(node)
        # Wall-clock deadlines, not tick counts (§8.8): a slow tick doesn't stretch the cadence.
        deadline = max(deadline + TICK_PERIOD_S, time.monotonic())
        stop.wait(max(0.0, deadline - time.monotonic()))
    node.dashboard.stop()
    if restarting.is_set():
        log.info("commissioning-node stopped to restart (exit %d)", RESTART_EXIT)
        sys.exit(RESTART_EXIT)
    log.info("commissioning-node stopped")


if __name__ == "__main__":
    serve()
