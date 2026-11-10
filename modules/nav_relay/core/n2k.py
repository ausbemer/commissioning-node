"""NMEA 2000 wire format — decode the allowlisted single-frame PGNs (ADR-0010 D2). Pure.

A vendor wire format, so it lives in core (rule 16). The 29-bit CAN id carries priority (3 bits),
the PGN (18 bits) and the source address (8 bits). All four allowlisted PGNs are PDU2 (PF ≥ 240),
so the PGN is the whole 18-bit field, and a kernel filter can match it exactly.

Each field holding the standard's "not available" (all ones), "out of range" or "reserved"
codes decodes to None. Those are the top three values of the field's range, and none of them is
ever a value.
"""
from __future__ import annotations

import math
import struct

from ..interface import (ALLOWLIST, PGN_COG_SOG_RAPID, PGN_POSITION_RAPID, PGN_SYSTEM_TIME,
                         PGN_VESSEL_HEADING)

CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_ERR_FLAG = 0x20000000
CAN_EFF_MASK = 0x1FFFFFFF
_PGN_MASK = 0x3FFFF << 8

#: `struct can_frame`: id, length, 3 pad bytes, 8 data bytes (linux/can.h).
FRAME = struct.Struct("=IB3x8s")
#: `struct can_filter`: id, mask.
_FILTER = struct.Struct("=II")

_REFERENCE = {0: "true", 1: "magnetic"}


def pgn_of(can_id: int) -> int:
    """The PGN in a 29-bit id. A PDU1 PGN (PF < 240) has its destination byte zeroed."""
    pgn = (can_id >> 8) & 0x3FFFF
    if ((pgn >> 8) & 0xFF) < 240:
        pgn &= 0x3FF00
    return pgn


def source_of(can_id: int) -> int:
    return can_id & 0xFF


def kernel_filters() -> bytes:
    """`CAN_RAW_FILTER`s matching exactly the allowlisted PGNs, any priority and source,
    extended data frames only (no RTR). The kernel drops everything else (§8.8)."""
    mask = _PGN_MASK | CAN_EFF_FLAG | CAN_RTR_FLAG
    return b"".join(_FILTER.pack((pgn << 8) | CAN_EFF_FLAG, mask) for pgn in ALLOWLIST)


def parse_frame(raw: bytes) -> tuple[int, bytes] | None:
    """A raw `can_frame` as (29-bit id, data), or None for anything but an extended data frame."""
    if len(raw) < FRAME.size:
        return None
    can_id, length, data = FRAME.unpack_from(raw)
    if not can_id & CAN_EFF_FLAG or can_id & (CAN_RTR_FLAG | CAN_ERR_FLAG):
        return None
    return can_id & CAN_EFF_MASK, data[:min(length, 8)]


def _u(data: bytes, off: int, size: int) -> int | None:
    if len(data) < off + size:
        return None
    v = int.from_bytes(data[off:off + size], "little")
    return None if v >= (1 << (8 * size)) - 3 else v


def _i(data: bytes, off: int, size: int) -> int | None:
    if len(data) < off + size:
        return None
    v = int.from_bytes(data[off:off + size], "little", signed=True)
    return None if v >= (1 << (8 * size - 1)) - 3 else v


def _scaled(v: int | None, scale: float) -> float | None:
    return None if v is None else v * scale


def decode(pgn: int, data: bytes) -> dict | None:
    """The values in one allowlisted frame, or None for a PGN off the allowlist or a short frame."""
    if pgn == PGN_POSITION_RAPID:
        if len(data) < 8:
            return None
        lat, lon = _scaled(_i(data, 0, 4), 1e-7), _scaled(_i(data, 4, 4), 1e-7)
        if lat is not None and not -90 <= lat <= 90:
            lat = None
        if lon is not None and not -180 <= lon <= 180:
            lon = None
        return {"lat": lat, "lon": lon}
    if pgn == PGN_COG_SOG_RAPID:
        if len(data) < 6:
            return None
        cog = _scaled(_u(data, 2, 2), 1e-4)
        return {"reference": _REFERENCE.get(data[1] & 0x03),
                "cog": cog if cog is None or cog < 2 * math.pi + 1e-3 else None,
                "sog": _scaled(_u(data, 4, 2), 1e-2)}
    if pgn == PGN_VESSEL_HEADING:
        if len(data) < 8:
            return None
        heading = _scaled(_u(data, 1, 2), 1e-4)
        return {"heading": heading if heading is None or heading < 2 * math.pi + 1e-3 else None,
                "deviation": _scaled(_i(data, 3, 2), 1e-4),
                "variation": _scaled(_i(data, 5, 2), 1e-4),
                "reference": _REFERENCE.get(data[7] & 0x03)}
    if pgn == PGN_SYSTEM_TIME:
        if len(data) < 8:
            return None
        days, tod = _u(data, 2, 2), _u(data, 4, 4)
        utc = None
        if days is not None and tod is not None and tod < 864000000:
            utc = days * 86400.0 + tod * 1e-4
        return {"utc": utc}
    return None

