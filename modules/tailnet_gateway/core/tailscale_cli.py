"""Tailscale adapter for the OverlayStatusSource seam — the module's bridge to tailscaled (§6).

READ-ONLY: runs `tailscale status --json` and nothing else. Any command that changes
Tailscale state (up/down/set, routes, auth keys) is a separate writer with its own Safety
ADR, and this module stays its single writer (§11 rung 9).

Field mapping, against tailscale 1.102.5 on this node (2026-10-12, logged out):
- VERIFIED present: top-level BackendState, Self, Peer, User; Self.HostName, Self.DNSName,
  Self.TailscaleIPs, Self.UserID. Peer and User are null when logged out.
- NOT YET VERIFIED (needs a logged-in node): Self.PrimaryRoutes, Self.Tags and per-peer Tags (all
  omitted when empty), the shape of Peer entries, and User[str(UserID)].LoginName.
  docs/quirks.md tracks this.
"""
from __future__ import annotations

import json
import logging
import shutil
import socket
import subprocess
from typing import Callable

from ..interface import MODULE, VERSION, OverlayPeer, OverlaySnapshot

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
            tags=[str(t) for t in me.get("Tags") or []],
            state=str(data.get("BackendState") or ""),
            peers=self._peers(data),
        )

    @staticmethod
    def _peers(data: dict) -> list[OverlayPeer]:
        users = data.get("User") or {}
        peers = []
        for p in (data.get("Peer") or {}).values():
            tags = [str(t) for t in p.get("Tags") or []]
            # A tagged node is owned by its tags; its UserID points at Tailscale's
            # "tagged-devices" pseudo-user, which is not a person, so it gets no login.
            login = None if tags else (users.get(str(p.get("UserID"))) or {}).get("LoginName")
            name = (p.get("DNSName") or "").rstrip(".") or p.get("HostName") or ""
            if not name:
                continue
            peers.append(OverlayPeer(addresses=[str(a) for a in p.get("TailscaleIPs") or []],
                                     node_name=name, login=login, tags=tags))
        return peers

    @staticmethod
    def _disconnected() -> OverlaySnapshot:
        return OverlaySnapshot(hostname=socket.gethostname(), connected=False)
