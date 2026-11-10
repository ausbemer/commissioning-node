"""SocketCAN adapter for the `NavSource` seam (ADR-0010 D2, D6). I/O only.

Read-only by construction. It opens one `CAN_RAW` socket with kernel filters for the
allowlisted PGNs only, and it never calls `send`. There is no method that could: rung 8 makes
transmitting on the vessel's bus its own ADR. An unprivileged process may open `CAN_RAW`.

It reads only a link that is up and in listen-only mode. Configuring the link is root's job
(`commissioning-node-can@.service`). This re-checks it (rung 13) with `ip -details -json`,
and closes the socket if the link has changed under it.
"""
from __future__ import annotations

import json
import logging
import os
import select
import socket
import subprocess

from ..interface import (INTERFACE, LINK_DOWN, LINK_NO_ADAPTER, LINK_NOT_LISTEN_ONLY, LINK_UP,
                         MODULE, LinkReport)
from . import n2k

log = logging.getLogger(MODULE)

#: Bounded receive buffer (§8.8): about 2 s of a busy 250 kbit/s bus after the filters.
#: PROVISIONAL (§8.11).
RCVBUF_BYTES = 64 * 1024
IP_TIMEOUT_S = 2.0


def listen_only(link_json: str) -> bool | None:
    """Whether `ip -details -json link show` output says LISTEN-ONLY, or None if it can't tell."""
    try:
        info = json.loads(link_json)[0]["linkinfo"]
    except (ValueError, LookupError, TypeError):
        return None
    if info.get("info_kind") != "can":
        return None
    modes = (info.get("info_data") or {}).get("ctrlmode") or []
    return "LISTEN-ONLY" in modes


class SocketCanSource:
    def __init__(self, interface: str = INTERFACE, sysfs: str = "/sys/class/net") -> None:
        self._ifname, self._sysfs = interface, sysfs
        self._sock: socket.socket | None = None

    def check_link(self) -> LinkReport:
        report = self._inspect()
        if report.state != LINK_UP:
            self.close()
        elif self._sock is None:
            try:
                self._open()
            except OSError as exc:
                self.close()
                return LinkReport(LINK_DOWN, f"could not open {self._ifname}: {exc.strerror or exc}")
        return report

    def _inspect(self) -> LinkReport:
        base = os.path.join(self._sysfs, self._ifname)
        if not os.path.isdir(base):
            return LinkReport(LINK_NO_ADAPTER, f"no {self._ifname}: is the Kvaser plugged in, and "
                                               "is the kvaser_usb driver installed? (ops/doctor.sh)")
        try:
            with open(os.path.join(base, "operstate")) as f:
                oper = f.read().strip()
        except OSError:
            oper = "unknown"
        if oper not in ("up", "unknown"):
            return LinkReport(LINK_DOWN, f"{self._ifname} is {oper} "
                                         f"(commissioning-node-can@{self._ifname}.service brings it up)")
        try:
            out = subprocess.run(["ip", "-details", "-json", "link", "show", "dev", self._ifname],
                                 capture_output=True, text=True, timeout=IP_TIMEOUT_S, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            return LinkReport(LINK_DOWN, f"could not read {self._ifname}'s mode: {exc}")
        mode = listen_only(out.stdout)
        if mode is None:
            return LinkReport(LINK_DOWN, f"could not read {self._ifname}'s mode")
        if not mode:
            # Never read a link that may ACK on the vessel's bus (ADR-0010 D2 guard 1).
            return LinkReport(LINK_NOT_LISTEN_ONLY, f"{self._ifname} is not listen-only; refusing to "
                                                    "read it (restart commissioning-node-can@"
                                                    f"{self._ifname}.service)")
        return LinkReport(LINK_UP)

    def _open(self) -> None:
        if not hasattr(socket, "AF_CAN"):
            raise OSError("this Python has no AF_CAN")
        sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RCVBUF_BYTES)
            sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER, n2k.kernel_filters())
            # Never see frames this host sent (it sends none; belt and braces).
            sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_RECV_OWN_MSGS, 0)
            sock.bind((self._ifname,))
            sock.setblocking(False)
        except OSError:
            sock.close()
            raise
        self._sock = sock
        log.info("reading %s (listen-only, PGNs %s)", self._ifname,
                 ", ".join(str(p) for p in sorted(n2k.ALLOWLIST)))

    def receive(self, timeout_s: float) -> tuple[int, bytes] | None:
        if self._sock is None:
            return None
        ready, _, _ = select.select([self._sock], [], [], timeout_s)
        if not ready:
            return None
        raw = self._sock.recv(n2k.FRAME.size)
        return n2k.parse_frame(raw)

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
            log.info("stopped reading %s", self._ifname)
