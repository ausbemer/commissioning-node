"""UDP adapter for the `SentenceSink` seam (ADR-0010 D3). I/O only.

One unconnected IPv4 datagram socket. `SO_BROADCAST` is never set, so the kernel refuses a
broadcast destination even if one got past `destination_problem`.
"""
from __future__ import annotations

import socket


class UdpSink:
    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)

    def send(self, address: str, port: int, payload: bytes) -> None:
        self._sock.sendto(payload, (address, port))
