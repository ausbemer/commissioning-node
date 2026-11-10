"""nav-relay — the module's composition point (spec §4): wires core and features.

`source` defaults to the SocketCAN adapter on `n2k0`, and `sink` to the UDP adapter
(ADR-0010). The reader and sender threads start only through `spawn`. main's serve passes a real
one. Tests and `compose()` without it get no threads, and drive `Reader.step`/`Sender.step`
themselves.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from .core.input import Subscribe, attach
from .core.output import Output, Publish
from .core.registry import Registry
from .core.socketcan_source import SocketCanSource
from .core.udp_sink import UdpSink
from .core.workers import Reader, Sender
from .features.destinations import DestinationFeature
from .features.status import StatusFeature
from .interface import DestinationTable, NavSource, NavState, SentenceSink

Spawn = Callable[[str, Callable[[], None]], None]


def build(subscribe: Subscribe, publish: Publish, source: NavSource | None = None,
          sink: SentenceSink | None = None, clock: Callable[[], float] = time.monotonic,
          spawn: Spawn | None = None,
          stop: threading.Event | None = None) -> tuple[Registry, Reader, Sender]:
    registry = Registry()
    state, table = NavState(), DestinationTable()
    reader = Reader(source or SocketCanSource(), state, clock)
    sender = Sender(state, table, sink or UdpSink(), clock)
    registry.register(DestinationFeature(table, clock=clock))
    registry.register(StatusFeature(state, table, clock))
    attach(registry, subscribe, Output(publish))
    if spawn is not None:
        halt = stop or threading.Event()
        spawn("nav-reader", lambda: reader.run(halt))
        spawn("nav-sender", lambda: sender.run(halt))
    return registry, reader, sender
