"""tailnet-gateway — the module's composition point (spec §4): wires core and features.

`source` defaults to the real (read-only) Tailscale CLI adapter, and `writer` to the
Tailscale CLI writer (ADR-0005); tests inject fakes. `config` is this module's view of the
config registry (ADR-0004), injected by the composition root.

The writer feature is registered DISABLED unless `writer_enabled` (§8.4; ADR-0005 rung 1):
its code ships and its commands are wired, but `tailnet-gateway:tailnet-writer` is not
advertised, so `invoke()` refuses every apply in-band until it is enabled.
"""
from __future__ import annotations

import time
from typing import Callable

from .core.input import Subscribe, attach
from .core.join_record import JoinRecordStore
from .core.site_networks import IpSiteNetworks
from .core.output import Output, Publish
from .core.registry import Registry
from .core.tailscale_cli import TailscaleCliStatusSource
from .core.tailscale_writer import TailscaleCliWriter
from .core.writer_worker import WriterWorker
from .features.peer_report import PeerReport
from .features.status_report import StatusReport
from .features.tailnet_writer import TailnetWriter
from .interface import ConfigView, OverlayStatusSource, OverlayWriter, SiteNetworkSource, WriterSlot


def build(subscribe: Subscribe, publish: Publish, source: OverlayStatusSource | None = None,
          config: ConfigView | None = None, writer: OverlayWriter | None = None,
          writer_enabled: bool = False, clock: Callable[[], float] = time.monotonic,
          spawn: Callable | None = None, state_dir: str | None = None,
          networks: SiteNetworkSource | None = None) -> Registry:
    """`spawn` runs writer work; tests pass one that runs it inline. `state_dir` is the
    site-scoped directory for the writer's join record (ADR-0005 A2); without it there is
    no record, so no automatic rejoin. `networks` defaults to the `ip` adapter (A3)."""
    registry = Registry()
    registry.register(StatusReport(config))
    registry.register(PeerReport())
    slot = WriterSlot()
    record = JoinRecordStore(state_dir) if state_dir is not None else None
    extra = {"spawn": spawn} if spawn is not None else {}
    WriterWorker(slot, writer or TailscaleCliWriter(), config, record, **extra)
    registry.register(TailnetWriter(slot, config, clock), enabled=writer_enabled)
    attach(registry, subscribe, Output(publish), source or TailscaleCliStatusSource(), slot,
           networks or IpSiteNetworks(), record)
    return registry
