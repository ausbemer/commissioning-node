"""commissioning-node composition root (spec §4): composes modules onto the bus and owns
the serve loop. Imports everything; imported by nothing (tests excepted).

Daemon mode (`python main.py`): starts the dashboard on loopback, then ticks on a
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
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import interfaces
import modules.camera_config as camera_config
import modules.camera_config.interface as camera_config_if
import modules.dashboard as dashboard
import modules.dashboard.interface as dashboard_if
import modules.stream_relay as stream_relay
import modules.stream_relay.interface as stream_relay_if
import modules.tailnet_gateway as tailnet_gateway
import modules.tailnet_gateway.interface as tailnet_gateway_if
from bus import Bus

log = logging.getLogger("main")

#: Tick cadence, seconds. PROVISIONAL — not yet measured (TODO.md, §8.11).
TICK_PERIOD_S = 5.0
#: Loopback only until tailnet identity + admin auth exist (ADR-0001).
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT_DEFAULT = 8080

INTERFACES = {
    "camera-config": camera_config_if,
    "dashboard": dashboard_if,
    "stream-relay": stream_relay_if,
    "tailnet-gateway": tailnet_gateway_if,
}


@dataclass
class Node:
    bus: Bus
    registries: list = field(default_factory=list)
    dashboard: dashboard.HttpSurface | None = None

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
            dashboard_port: int = DASHBOARD_PORT_DEFAULT) -> Node:
    """Wire every module onto `bus`. Tests pass a fake overlay source and clock."""
    node = Node(bus=bus)
    sub, pub = bus.subscribe, bus.publish
    dash_registry, node.dashboard = dashboard.build(sub, pub, DASHBOARD_HOST, dashboard_port)
    node.registries = [
        tailnet_gateway.build(sub, pub, overlay_source),
        camera_config.build(sub, pub),
        stream_relay.build(sub, pub, clock),
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
        where = f"at v{registry.version} predates support" if registry else "is not running"
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


def serve() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    node = compose(Bus(), dashboard_port=_port_from_env())
    try:
        node.dashboard.start()
    except OSError as exc:
        log.error("dashboard not available — could not bind %s:%s (%s); running without it",
                  DASHBOARD_HOST, _port_from_env(), exc)
    stop = threading.Event()
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
    log.info("commissioning-node stopped")


if __name__ == "__main__":
    serve()
