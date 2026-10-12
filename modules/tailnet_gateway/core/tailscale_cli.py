"""Tailscale adapter for the OverlayStatusSource seam — the module's bridge to tailscaled (§6).

READ-ONLY: runs `tailscale status --json` and nothing else. Any command that changes
Tailscale state (up/down/set, routes, auth keys) is a separate writer with its own Safety
ADR, and this module stays its single writer (§11 rung 9).

The JSON field mapping below (BackendState, Self.HostName, Self.TailscaleIPs,
Self.PrimaryRoutes) has NOT yet been verified against a pinned tailscale version on this
node. Capture a real sample when tailscale is installed and record it in docs/quirks.md.
"""
from __future__ import annotations

import json
import logging
import shutil
import socket
import subprocess
from typing import Callable

from ..interface import MODULE, VERSION, OverlaySnapshot

log = logging.getLogger(MODULE)

#: Upper bound on one `tailscale status` call, seconds. PROVISIONAL — unmeasured (TODO.md, §8.11).
STATUS_TIMEOUT_S = 5.0


class TailscaleCliStatusSource:
    def __init__(self, binary: str = "tailscale",
                 run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                 which: Callable[[str], str | None] = shutil.which) -> None:
        self._binary = binary
        self._run = run
        self._which = which
        self._warned_missing = False

    def read(self) -> OverlaySnapshot:
        if self._which(self._binary) is None:
            if not self._warned_missing:
                log.warning("tailscale CLI not available — service %s at v%s reports the tailnet as "
                            "disconnected until tailscale is installed.", MODULE, VERSION)
                self._warned_missing = True
            return self._disconnected()
        try:
            done = self._run([self._binary, "status", "--json"], capture_output=True, text=True,
                             timeout=STATUS_TIMEOUT_S, check=True)
            data = json.loads(done.stdout)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            log.warning("tailscale status unreadable (%s); reporting disconnected", exc)
            return self._disconnected()
        me = data.get("Self") or {}
        return OverlaySnapshot(
            hostname=me.get("HostName") or socket.gethostname(),
            connected=data.get("BackendState") == "Running",
            addresses=[str(a) for a in me.get("TailscaleIPs") or []],
            served_routes=[str(r) for r in me.get("PrimaryRoutes") or []],
        )

    @staticmethod
    def _disconnected() -> OverlaySnapshot:
        return OverlaySnapshot(hostname=socket.gethostname(), connected=False)
