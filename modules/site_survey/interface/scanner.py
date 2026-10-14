"""What the scanner feature and core's worker share (ADR-0007; §4 "features meet in interface
objects").

The feature decides (is a scan allowed now, over which networks) and puts a `ScanPlan` in the
`ScanSlot`. Core's worker takes it off the bus thread, runs it against the `NetworkScanner`,
and leaves a `ScanOutcome`. On the next tick core drains the outcome and routes it back to the
feature, which publishes the hosts and the result. The bus never waits on nmap (§6).

The slot holds ONE plan at a time, with no queue and no force door (ADR-0007 Safety 4): a
second request while one runs is refused.
"""
from __future__ import annotations

import ipaddress
import threading
from typing import Callable, Protocol, runtime_checkable

from interfaces import Shape, SiteHost

#: Inward topic: core routes each finished outcome to features under this name. Never
#: published on the bus.
SCAN_OUTCOME = "site-survey/scan-outcome"

#: The widest network a scan may cover (ADR-0007 D2): a /22 is 1022 hosts. PROVISIONAL —
#: tools/ledgers/site-survey/ (§8.11).
MAX_PREFIX = 22
#: The only ports probed: web and RTSP, the ones that matter to cameras (ADR-0007 D2). Part
#: of the may-disturb warning, so a change here changes what the operator confirms.
PORTS = (80, 443, 554)


def scan_problem(networks: list[str]) -> str | None:
    """Why these networks may not be scanned, or None. The one rule, used by the feature
    before it plans and re-checked by the adapter before it runs (rung 13). Every network must
    pass, or none is scanned: a request covers what the node is on, never a subset of it."""
    if not networks:
        return "the node is on no IPv4 site network"
    for raw in networks:
        try:
            net = ipaddress.IPv4Network(raw, strict=True)
        except ValueError:
            return f"{raw!r} is not an IPv4 network"
        if str(net) != raw:
            return f"{raw!r} is not in CIDR form"
        if (net.is_loopback or net.is_reserved or net.is_multicast or net.is_link_local
                or net.subnet_of(ipaddress.IPv4Network("0.0.0.0/8"))):
            return f"{net} is not a site network; refusing to scan it"
        if net.prefixlen < MAX_PREFIX:
            return f"{net} is wider than /{MAX_PREFIX}; refusing to scan it"
        if net.subnet_of(ipaddress.IPv4Network("100.64.0.0/10")):
            return f"{net} is tailnet address space; only site networks are scanned"
        if not net.is_private:
            return f"{net} is not a private network; refusing to scan it"
    return None


class ScanReply(Shape):
    """What the adapter found, or why it found nothing."""
    ok: bool
    message: str
    hosts: list[SiteHost] = []
    #: The node's own addresses on the scanned networks (excluded from the scan).
    self_addresses: list[str] = []


@runtime_checkable
class NetworkScanner(Protocol):
    """The vendor-neutral scan seam (rule 16). One adapter: nmap + the neighbour table."""

    def scan(self, networks: list[str]) -> ScanReply:
        """Scan `networks` once, gently (ADR-0007 D2). Bounded by the adapter's own timeouts;
        never raises for a scan that failed (that is a reply with ok=False)."""
        ...


class ScanPlan(Shape):
    """One approved scan request, with the networks it will cover."""
    request_id: str
    requested_by: str
    door: str
    networks: list[str]


class ScanOutcome(Shape):
    plan: ScanPlan
    reply: ScanReply
    finished_at: float


class ScanSlot:
    """One scan at a time. `submit` is the feature's side; `bind`, `finish` and `drain` are
    core's. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: ScanPlan | None = None
        self._done: list[ScanOutcome] = []
        self._on_submit: Callable[[ScanPlan], None] | None = None

    def bind(self, on_submit: Callable[[ScanPlan], None]) -> None:
        """Core: what to call with an accepted plan (it starts the worker)."""
        self._on_submit = on_submit

    def submit(self, plan: ScanPlan) -> ScanPlan | None:
        """Accept `plan` and return None, or return the plan already running (busy)."""
        with self._lock:
            if self._running is not None:
                return self._running
            if self._on_submit is None:
                raise RuntimeError("scan slot has no worker bound")
            self._running = plan
        try:
            self._on_submit(plan)
        except BaseException:
            with self._lock:
                self._running = None
            raise
        return None

    def finish(self, outcome: ScanOutcome) -> None:
        """Core: the running plan is done; free the slot and keep the outcome for draining."""
        with self._lock:
            self._running = None
            self._done.append(outcome)

    def drain(self) -> list[ScanOutcome]:
        """Core, on the tick: every outcome not yet published, oldest first."""
        with self._lock:
            done, self._done = self._done, []
        return done
