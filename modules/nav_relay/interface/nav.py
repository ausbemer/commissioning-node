"""What nav-relay's core and features share (ADR-0010; §4 "features meet in interface objects").

Three shared objects, each constructed once by the module's `build` and handed to whoever needs it:

- `NavState`: the core reader writes what it decoded from the NMEA 2000 bus. The core sender
  and the `status` feature read it. It is bounded latest-value state per (PGN, source), §8.8.
- `DestinationTable`: the `sender` feature decides which hosts get nav data. The core sender
  thread sends to them and records how each send went. It is the one place the destination
  list lives, so there is one writer (§11 rung 9).
- The policy in `current_fix` and `destination_problem`. These are the single definition of
  "which values are fresh enough to send", "which source wins" and "which host may receive".
  Both the sender thread and the features call them, so no consumer has its own copy (§4, rung 13).

The vendor wire formats (NMEA 2000 frames in, NMEA 0183 sentences out) are adapters in `core/`
(rule 16). Nothing here does I/O.
"""
from __future__ import annotations

import ipaddress
import math
import threading
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from freshness import RAPID_MIN_S, is_fresh
from interfaces import NavDestinationState, NavFeedSource, NavFix, SiteHosts

# -- the crossing allowlist (ADR-0010 D2, §6) -------------------------------------------------

PGN_SYSTEM_TIME = 126992
PGN_VESSEL_HEADING = 127250
PGN_POSITION_RAPID = 129025
PGN_COG_SOG_RAPID = 129026
#: The only PGNs that cross from the NMEA 2000 bus. The adapter sets kernel filters for exactly
#: these; nothing crosses the other way, ever (rung 8).
ALLOWLIST = (PGN_SYSTEM_TIME, PGN_VESSEL_HEADING, PGN_POSITION_RAPID, PGN_COG_SOG_RAPID)
#: Each PGN's nominal transmit period from the NMEA 2000 standard, seconds. These are the
#: "declared cadence" the freshness authority judges against, since the bus declares none
#: itself. PROVISIONAL until measured on a real bus (tools/ledgers/nav-relay/, §8.11).
NOMINAL_PERIOD_S = {
    PGN_SYSTEM_TIME: 1.0,
    PGN_VESSEL_HEADING: 0.1,
    PGN_POSITION_RAPID: 0.1,
    PGN_COG_SOG_RAPID: 0.25,
}

#: The CAN interface the ops udev rule names any `kvaser_usb` netdev (ADR-0010 D6).
INTERFACE = "n2k0"

#: Link states (`NavStatus.link`, open vocabulary).
LINK_UP = "up"
LINK_DOWN = "down"
LINK_NO_ADAPTER = "no-adapter"
LINK_NOT_LISTEN_ONLY = "not-listen-only"

#: At most this many (PGN, source) pairs kept. Past it, the least recently heard pair goes
#: (§8.8). PROVISIONAL (§8.11).
MAX_SOURCES = 16
#: At most this many destinations at once (ADR-0010 D4). PROVISIONAL (§8.11).
MAX_DESTINATIONS = 4
#: How often the sender sends one datagram to each destination, seconds. PROVISIONAL (§8.11).
OUTPUT_PERIOD_S = 1.0
#: The default destination UDP port: what a gpsd `udp://0.0.0.0:2947` source listens on.
DEFAULT_PORT = 2947

#: The address space nothing is ever sent to: the tailnet's (ADR-0010 D4).
TAILNET_V4 = ipaddress.IPv4Network("100.64.0.0/10")

_MS_TO_KN = 3600.0 / 1852.0


# -- what the reader decoded ------------------------------------------------------------------

@dataclass(frozen=True)
class Reading:
    """One decoded allowlisted frame. Values are in SI units straight off the bus (radians,
    m/s, degrees for lat/lon), each None when the frame said "not available"."""
    pgn: int
    source: int
    values: dict
    #: When the node received it, on its own monotonic clock (freshness never trusts a
    #: producer's timestamp).
    received_at: float


@dataclass(frozen=True)
class LinkReport:
    state: str
    detail: str = ""


class NavState:
    """Latest reading per (PGN, source), and the link's last report. Thread-safe."""

    def __init__(self, max_sources: int = MAX_SOURCES) -> None:
        self._lock = threading.Lock()
        self._max = max_sources
        self._readings: dict[tuple[int, int], Reading] = {}
        self._link = LinkReport(LINK_DOWN, "the reader has not started")

    def put(self, reading: Reading) -> None:
        key = (reading.pgn, reading.source)
        with self._lock:
            self._readings.pop(key, None)            # re-insert so the newest is last
            if len(self._readings) >= self._max:
                self._readings.pop(next(iter(self._readings)))
            self._readings[key] = reading

    def readings(self) -> list[Reading]:
        with self._lock:
            return list(self._readings.values())

    def set_link(self, report: LinkReport) -> None:
        with self._lock:
            self._link = report

    def link(self) -> LinkReport:
        with self._lock:
            return self._link


# -- the policy: which values may be sent (ADR-0010 D2, D3) ------------------------------------

def _fresh(r: Reading, now: float) -> bool:
    return is_fresh(r.received_at, NOMINAL_PERIOD_S[r.pgn], now, floor=RAPID_MIN_S)


def _deg(rad: float | None) -> float | None:
    return None if rad is None else math.degrees(rad) % 360.0


def current_fix(readings: list[Reading], now: float) -> tuple[NavFix, list[NavFeedSource]]:
    """The values fresh enough to send now, and every source seen.

    Per PGN, the fresh source with the lowest address wins (R3). A stale field is None, never
    the last known value (rule 10: fails toward "no fix"). A magnetic course or heading becomes
    true only with a fresh variation.
    """
    chosen: dict[int, Reading] = {}
    for r in sorted(readings, key=lambda r: (r.pgn, r.source)):
        if r.pgn not in chosen and _fresh(r, now):
            chosen[r.pgn] = r
    sources = [NavFeedSource(pgn=r.pgn, source=r.source, age_s=round(max(0.0, now - r.received_at), 2),
                             selected=chosen.get(r.pgn) is r)
               for r in sorted(readings, key=lambda r: (r.pgn, r.source))]

    fix = NavFix()
    age = lambda r: round(max(0.0, now - r.received_at), 2)  # noqa: E731

    heading = chosen.get(PGN_VESSEL_HEADING)
    variation = heading.values.get("variation") if heading else None
    if variation is not None:
        fix.variation_deg = round(math.degrees(variation), 2)
    if heading and heading.values.get("heading") is not None:
        fix.heading_age_s = age(heading)
        h = heading.values["heading"]
        if heading.values.get("reference") == "true":
            fix.heading_true_deg = _deg(h)
        elif heading.values.get("reference") == "magnetic":
            mag = h + (heading.values.get("deviation") or 0.0)
            fix.heading_mag_deg = _deg(mag)
            if variation is not None:
                fix.heading_true_deg = _deg(mag + variation)

    pos = chosen.get(PGN_POSITION_RAPID)
    if pos and pos.values.get("lat") is not None and pos.values.get("lon") is not None:
        fix.lat, fix.lon, fix.position_age_s = pos.values["lat"], pos.values["lon"], age(pos)

    cs = chosen.get(PGN_COG_SOG_RAPID)
    if cs:
        sog, cog = cs.values.get("sog"), cs.values.get("cog")
        if sog is not None:
            fix.sog_kn = round(sog * _MS_TO_KN, 2)
        if cog is not None:
            if cs.values.get("reference") == "true":
                fix.cog_true_deg = _deg(cog)
            elif cs.values.get("reference") == "magnetic" and variation is not None:
                fix.cog_true_deg = _deg(cog + variation)
        if fix.sog_kn is not None or fix.cog_true_deg is not None:
            fix.cogsog_age_s = age(cs)

    clock = chosen.get(PGN_SYSTEM_TIME)
    if clock and clock.values.get("utc") is not None:
        # The time of the fix is now: the bus's time plus how long ago we heard it.
        fix.utc = clock.values["utc"] + (now - clock.received_at)
    return fix, sources


# -- destinations (ADR-0010 D4) -----------------------------------------------------------------

def destination_problem(address: str | None, port: int, hosts: SiteHosts | None) -> str | None:
    """Why nav data may not be sent to `address`:`port`, or None. The feature checks before it
    adds a destination. The address must be a host the last completed scan found, on a scanned
    network, and not this node or the tailnet."""
    if not address:
        return "no address given"
    try:
        addr = ipaddress.IPv4Address(address)
    except ValueError:
        return f"{address!r} is not an IPv4 address"
    if not 1 <= port <= 65535:
        return f"port {port} is out of range"
    if addr in TAILNET_V4:
        return f"{addr} is tailnet address space; nav data goes only to site hosts"
    if addr.is_multicast or addr.is_loopback or addr.is_unspecified or addr == ipaddress.IPv4Address("255.255.255.255"):
        return f"{addr} is not a single host"
    if hosts is None:
        return "no site scan yet: scan the site network, then pick a host it found"
    if address in hosts.self_addresses:
        return f"{addr} is this node"
    nets = []
    for raw in hosts.networks:
        try:
            nets.append(ipaddress.IPv4Network(raw, strict=False))
        except ValueError:
            continue
    home = next((n for n in nets if addr in n), None)
    if home is None:
        return f"{addr} is not on a scanned network"
    if home.prefixlen < 31 and addr in (home.network_address, home.broadcast_address):
        return f"{addr} is the network or broadcast address of {home}"
    if not any(h.address == address for h in hosts.hosts):
        return f"{addr} did not answer the last scan; pick a host it found"
    return None


@dataclass
class Destination:
    address: str
    port: int
    mac: str | None
    requested_by: str
    #: Wall-clock time sending started.
    since: float
    sent: int = 0
    last_error: str | None = None


class DestinationTable:
    """The hosts nav data goes to. The `sender` feature adds and removes. The core sender
    thread reads and records. Thread-safe. Memory only: a restart clears it (ADR-0010 R1)."""

    def __init__(self, max_destinations: int = MAX_DESTINATIONS) -> None:
        self._lock = threading.Lock()
        self._max = max_destinations
        self._rows: dict[tuple[str, int], Destination] = {}

    def add(self, dest: Destination) -> str | None:
        """Add `dest` and return None, or say why not."""
        key = (dest.address, dest.port)
        with self._lock:
            if key in self._rows:
                return f"already sending to {dest.address}:{dest.port}"
            if len(self._rows) >= self._max:
                return f"already sending to {self._max} hosts; stop one first"
            self._rows[key] = dest
        return None

    def remove(self, address: str | None) -> int:
        """Stop sending to `address` (every port), or to everyone when None. Returns how many."""
        with self._lock:
            keys = [k for k in self._rows if address is None or k[0] == address]
            for k in keys:
                del self._rows[k]
        return len(keys)

    def targets(self) -> list[tuple[str, int]]:
        with self._lock:
            return list(self._rows)

    def record(self, address: str, port: int, error: str | None) -> None:
        """Core: how one send went. A row removed meanwhile is ignored."""
        with self._lock:
            row = self._rows.get((address, port))
            if row is not None:
                row.sent += error is None
                row.last_error = error

    def states(self) -> list[NavDestinationState]:
        with self._lock:
            return [NavDestinationState(address=d.address, port=d.port, mac=d.mac,
                                        requested_by=d.requested_by, since=d.since,
                                        sent=d.sent, last_error=d.last_error)
                    for d in self._rows.values()]


# -- the seams (rule 16) ------------------------------------------------------------------------

@runtime_checkable
class NavSource(Protocol):
    """The NMEA 2000 side: read-only, by construction. There is no send, and there never will
    be one without its own ADR (rung 8). One adapter: `core/socketcan_source.py`."""

    def check_link(self) -> LinkReport:
        """Check the link (present, up, listen-only), opening or closing the reader to match."""
        ...

    def receive(self, timeout_s: float) -> tuple[int, bytes] | None:
        """One allowlisted frame as (29-bit CAN id, data), or None after `timeout_s`. Raises
        OSError when the link goes away."""
        ...

    def close(self) -> None: ...


@runtime_checkable
class SentenceSink(Protocol):
    """The NMEA 0183 side. One adapter: `core/udp_sink.py`."""

    def send(self, address: str, port: int, payload: bytes) -> None:
        """Send one datagram. Raises OSError when it can't."""
        ...


__all__ = ["ALLOWLIST", "NOMINAL_PERIOD_S", "PGN_SYSTEM_TIME", "PGN_VESSEL_HEADING",
           "PGN_POSITION_RAPID", "PGN_COG_SOG_RAPID", "INTERFACE", "LINK_UP", "LINK_DOWN",
           "LINK_NO_ADAPTER", "LINK_NOT_LISTEN_ONLY", "MAX_SOURCES", "MAX_DESTINATIONS",
           "OUTPUT_PERIOD_S", "DEFAULT_PORT", "Reading", "LinkReport", "NavState",
           "current_fix", "destination_problem", "Destination", "DestinationTable",
           "NavSource", "SentenceSink"]
