"""NMEA 0183 wire format — the sentences sent to a destination (ADR-0010 D3). Pure.

A vendor wire format, so it lives in core (rule 16). What is fresh enough to send is decided
by `interface.current_fix`. This only spells out the result:

- `$GPGGA` always: quality 1 with the position, or quality 0 with the position fields empty
  when it isn't fresh (an explicit "no fix", never the last position).
- `$GPVTG` always: course true and speed, each empty when stale (mode N when both are).
- `$HEHDT` when a true heading is known (true, or magnetic + a fresh variation). Nothing else:
  a magnetic-only heading is not sent (ADR-0010 amendment, 2026-11-10).

Each sentence is at most 82 characters including CRLF, with its checksum.
"""
from __future__ import annotations

import time

from interfaces import NavFix

MAX_LEN = 82


def checksum(body: str) -> str:
    c = 0
    for ch in body.encode("ascii"):
        c ^= ch
    return f"{c:02X}"


def _sentence(body: str) -> str:
    s = f"${body}*{checksum(body)}\r\n"
    if len(s) > MAX_LEN:
        raise ValueError(f"NMEA 0183 sentence over {MAX_LEN} characters: {s!r}")
    return s


def _dm(v: float, width: int) -> str:
    """Degrees and minutes to 1e-4 minute, rounded once, so 59.99999' carries into the
    degree instead of printing as 60.0000."""
    ticks = round(abs(v) * 600000)            # 1e-4 minute per tick
    d, rest = divmod(ticks, 600000)
    return f"{d:0{width}d}{rest // 10000:02d}.{rest % 10000:04d}"


def _lat(v: float) -> tuple[str, str]:
    return _dm(v, 2), "N" if v >= 0 else "S"


def _lon(v: float) -> tuple[str, str]:
    return _dm(v, 3), "E" if v >= 0 else "W"


def _angle(v: float | None) -> str:
    """A bearing to 0.1°, in [0, 360): 359.96 rounds to 0.0, never 360.0."""
    return "" if v is None else f"{round(v, 1) % 360.0:.1f}"


def _num(v: float | None, places: int = 1) -> str:
    return "" if v is None else f"{v:.{places}f}"


def _hhmmss(utc: float | None) -> str:
    if utc is None:
        return ""
    return time.strftime("%H%M%S", time.gmtime(utc)) + f".{int((utc % 1) * 100):02d}"


def gga(fix: NavFix) -> str:
    """Quality 1 with a fresh position, 0 with empty position fields without one. Satellites,
    HDOP and altitude aren't among the PGNs read (D2), so they stay empty."""
    if fix.lat is None or fix.lon is None:
        return _sentence(f"GPGGA,{_hhmmss(fix.utc)},,,,,0,,,,,,,,")
    lat, ns = _lat(fix.lat)
    lon, ew = _lon(fix.lon)
    return _sentence(f"GPGGA,{_hhmmss(fix.utc)},{lat},{ns},{lon},{ew},1,,,,,,,,")


def vtg(fix: NavFix) -> str:
    """Course true and speed in knots and km/h; the magnetic course is left empty. Mode N
    with every field empty when neither is fresh."""
    if fix.sog_kn is None and fix.cog_true_deg is None:
        return _sentence("GPVTG,,T,,M,,N,,K,N")
    kmh = None if fix.sog_kn is None else fix.sog_kn * 1.852
    return _sentence(f"GPVTG,{_angle(fix.cog_true_deg)},T,,M,{_num(fix.sog_kn)},N,{_num(kmh)},K,A")


def hdt(fix: NavFix) -> str | None:
    if fix.heading_true_deg is None:
        return None
    return _sentence(f"HEHDT,{_angle(fix.heading_true_deg)},T")


def datagram(fix: NavFix) -> bytes:
    """Everything for one send: GGA, VTG, then HDT if a true heading is known."""
    parts = [gga(fix), vtg(fix)]
    h = hdt(fix)
    if h:
        parts.append(h)
    return "".join(parts).encode("ascii")
