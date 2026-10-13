"""Host adapter for the SiteNetworkSource seam: which IPv4 networks is the node on? (ADR-0005 A3)

READ-ONLY: runs `ip -j -4 addr show up`. Interface names are not assumed (a RevPi measured
`lan0` on 2026-10-12 and `eth0` on 2026-10-13): every interface that is up counts, except
loopback and the overlay's own (`tailscale*`). Host addresses (/32), link-local addresses and
anything inside the tailnet's 100.64.0.0/10 are skipped. Must not raise.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import shutil
import subprocess
from typing import Callable

from ..interface import MODULE, route_problem

log = logging.getLogger(MODULE)

#: Upper bound on one `ip` call, seconds. PROVISIONAL (§8.11): a local netlink read.
IP_TIMEOUT_S = 2.0


class IpSiteNetworks:
    def __init__(self, run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                 which: Callable[[str], str | None] = shutil.which) -> None:
        self._run, self._which = run, which
        self._warned = False

    def read(self) -> list[str]:
        if self._which("ip") is None:
            return self._quiet("the ip command is not available")
        try:
            done = self._run(["ip", "-j", "-4", "addr", "show", "up"], capture_output=True,
                             text=True, timeout=IP_TIMEOUT_S, check=True)
            links = json.loads(done.stdout)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            return self._quiet(f"ip addr unreadable ({exc})")
        out: list[str] = []
        for link in links if isinstance(links, list) else []:
            name = str(link.get("ifname", ""))
            if name == "lo" or name.startswith("tailscale"):
                continue
            for addr in link.get("addr_info") or []:
                try:
                    iface = ipaddress.ip_interface(f"{addr.get('local')}/{addr.get('prefixlen')}")
                except ValueError:
                    continue
                net = iface.network
                if iface.ip.is_link_local or iface.ip.is_loopback or net.prefixlen == 32:
                    continue
                cidr = str(net)
                if cidr not in out and route_problem(cidr) is None:
                    out.append(cidr)
        self._warned = False
        return out

    def _quiet(self, why: str) -> list[str]:
        if not self._warned:
            log.warning("site networks unknown: %s; 'auto' routes resolve to none", why)
            self._warned = True
        return []
