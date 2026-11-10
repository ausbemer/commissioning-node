"""NMEA 0183 wire format — the sentences sent to a destination (ADR-0010 D3). Pure.

A vendor wire format, so it lives in core (rule 16). What is fresh enough to send is decided
by `interface.current_fix`. This only spells out the result:

- `$GPRMC` always: status A with position, speed and course, or status V with every one of them
  empty when the position isn't fresh (an explicit "no fix", never the last position).
- `$HEHDT` when a true heading is known.
- `$HCHDG` when only a magnetic heading is known.

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


def _ew(v: float | None) -> tuple[str, str]:
    if v is None:
        return "", ""
    return f"{abs(v):.1f}", "E" if v >= 0 else "W"


def rmc(fix: NavFix) -> str:
    hhmmss = ddmmyy = ""
    if fix.utc is not None:
        t = time.gmtime(fix.utc)
        hhmmss = time.strftime("%H%M%S", t) + f".{int((fix.utc % 1) * 100):02d}"
        ddmmyy = time.strftime("%d%m%y", t)
    var, var_ew = _ew(fix.variation_deg)
    if fix.lat is None or fix.lon is None:
        return _sentence(f"GPRMC,{hhmmss},V,,,,,,,{ddmmyy},{var},{var_ew},N")
    lat, ns = _lat(fix.lat)
    lon, ew = _lon(fix.lon)
    return _sentence(f"GPRMC,{hhmmss},A,{lat},{ns},{lon},{ew},{_num(fix.sog_kn)},"
                     f"{_angle(fix.cog_true_deg)},{ddmmyy},{var},{var_ew},A")


def heading(fix: NavFix) -> str | None:
    if fix.heading_true_deg is not None:
        return _sentence(f"HEHDT,{_angle(fix.heading_true_deg)},T")
    if fix.heading_mag_deg is not None:
        var, var_ew = _ew(fix.variation_deg)
        return _sentence(f"HCHDG,{_angle(fix.heading_mag_deg)},,,{var},{var_ew}")
    return None


def datagram(fix: NavFix) -> bytes:
    """Everything for one send: RMC, then the heading sentence if there is one."""
    parts = [rmc(fix)]
    h = heading(fix)
    if h:
        parts.append(h)
    return "".join(parts).encode("ascii")
