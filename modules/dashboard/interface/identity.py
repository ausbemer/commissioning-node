"""Tailnet caller identity — the dashboard's one resolver (ADR-0003 §2, spec §15).

`PeerDirectory` holds the latest `TailnetPeers` snapshot from tailnet-gateway and answers
"who is calling from this tailnet address?". It is an interface object (spec §4 "features
meet in interface objects"): the peer-directory feature writes it, and the HTTP core will read
it to classify requests on the tailnet door.

Identity is a renewed level, not a grant (§5, CLAUDE.md rule 10). The resolver answers only
while the snapshot is fresh by the producer's declared cadence (`freshness`), and fails
toward deny: stale, missing, unknown and ambiguous all resolve to nobody, each with an in-band
reason (§11 rung 7). It is the only place in this module that turns an address into an
identity. If a second module ever needs it, it moves to a neutral package (CLAUDE.md rule 9).
"""
from __future__ import annotations

import ipaddress
import threading
import time
from dataclasses import dataclass
from typing import Callable

from freshness import is_fresh
from interfaces import TailnetPeers


@dataclass(frozen=True)
class PeerIdentity:
    """A resolved tailnet caller."""

    node_name: str
    #: The owning user's login; None for a tagged node.
    login: str | None
    tags: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        """Who to record a change against: the login, or the node name for a tagged node."""
        return self.login or self.node_name


@dataclass(frozen=True)
class Unresolved:
    """Why an address did not resolve. `reason` is a stable code; `message` is for people."""

    #: "stale" (no fresh peer table: nobody resolves), "unknown" (fresh table, address not
    #: in it), "ambiguous" (two peers claim it), or "invalid" (not an IP address).
    reason: str
    message: str


class PeerDirectory:
    """Thread-safe latest peer table, with freshness. Replaced whole on every update."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._by_addr: dict[ipaddress.IPv4Address | ipaddress.IPv6Address, PeerIdentity | None] = {}
        self._received_at: float | None = None
        self._period_s: float | None = None
        self._by_endpoint: dict[str, PeerIdentity | None] = {}

    def update(self, snapshot: TailnetPeers) -> None:
        by_addr: dict[ipaddress.IPv4Address | ipaddress.IPv6Address, PeerIdentity | None] = {}
        by_endpoint: dict[str, PeerIdentity | None] = {}
        for peer in snapshot.peers:
            who = PeerIdentity(node_name=peer.node_name, login=peer.login, tags=tuple(peer.tags))
            endpoint = parse_address(peer.endpoint) if peer.endpoint else None
            if endpoint is not None:
                key = str(endpoint)
                by_endpoint[key] = who if key not in by_endpoint else None
            for raw in peer.addresses:
                addr = parse_address(raw)
                if addr is None:
                    continue
                # Two peers claiming one address: resolve it to nobody (None marks ambiguous).
                by_addr[addr] = who if addr not in by_addr else None
        with self._lock:
            self._by_addr = by_addr
            self._by_endpoint = by_endpoint
            self._received_at = self._clock()
            self._period_s = snapshot.period_s

    def resolve(self, address: str) -> PeerIdentity | Unresolved:
        addr = parse_address(address)
        if addr is None:
            return Unresolved("invalid", f"{address!r} is not an IP address")
        with self._lock:
            if not is_fresh(self._received_at, self._period_s, self._clock()):
                return Unresolved("stale", "tailnet peer table is stale or not yet received; "
                                           "retry shortly, or use the LAN fallback")
            if addr not in self._by_addr:
                return Unresolved("unknown", f"{addr} is not a known tailnet peer; a peer that "
                                             "just joined resolves within a few seconds")
            who = self._by_addr[addr]
        if who is None:
            return Unresolved("ambiguous", f"{addr} is claimed by more than one tailnet peer")
        return who


    def endpoints(self) -> dict[str, PeerIdentity]:
        """Peers by the address this node reaches them at directly (`TailnetPeer.endpoint`, 0.10.0),
        for display only (ADR-0007: a tailnet node on the site map). Never an identity for a
        request: a site-LAN address proves nothing about who sends from it. Empty unless the
        table is fresh; an endpoint two peers share is left out."""
        with self._lock:
            if not is_fresh(self._received_at, self._period_s, self._clock()):
                return {}
            return {a: who for a, who in self._by_endpoint.items() if who is not None}


def parse_address(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse an address as a socket reports it: strips an IPv6 zone and unwraps IPv4-mapped."""
    try:
        addr = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr
