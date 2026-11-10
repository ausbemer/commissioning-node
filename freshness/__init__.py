"""freshness — the canonical authority for "how old may a renewed level get?" (spec §4, §5).

Neutral package: pure functions, no I/O, imports nothing but the standard library.

A consumer of a periodic feed derives its staleness window from the cadence the PRODUCER
declares (§11 rung 11), never from a constant of its own:

    window = clamp(3 × period_s, STALE_MIN_S, STALE_MAX_S)

Used by stream-relay (tailnet status → stream endpoints), the dashboard (tailnet peer
table → caller identity, ADR-0003) and nav-relay (NMEA 2000 fields → what may be sent,
ADR-0010). One implementation, so they can never drift apart.

A feed far faster than the tick (nav-relay's 10 Hz position) passes `floor=RAPID_MIN_S`: the
same formula with a lower named bound (ADR-0010 R2), never a second formula.

Ages are measured on the consumer's own monotonic clock from when it RECEIVED the message,
not from a producer timestamp: wall-clock skew or a clock step must not make a stale level
look fresh.
"""
from __future__ import annotations

import math

#: Bounds on the derived staleness window, seconds. PROVISIONAL — not yet measured (TODO.md, §8.11).
STALE_MIN_S = 5.0
STALE_MAX_S = 120.0
#: The floor for a rapid field-bus feed (ADR-0010 R2): a vessel at 10 kn moves ~5 m in 1 s,
#: ~25 m in the default 5 s. PROVISIONAL — not yet measured (TODO.md, §8.11).
RAPID_MIN_S = 1.0
#: How many declared periods may pass without a renewal before the level lapses.
PERIODS = 3


def stale_after(period_s: float, floor: float = STALE_MIN_S) -> float:
    """The staleness window for a feed that declares `period_s`.

    A period that is not a finite positive number gets the strictest window: the level
    fails toward lapsing, never toward living forever (NaN would otherwise compare false
    against every age). `floor` is `STALE_MIN_S` or `RAPID_MIN_S`; anything else, or a floor
    that isn't finite and positive, falls back to the strictest of the two.
    """
    if floor not in (STALE_MIN_S, RAPID_MIN_S):
        floor = min(STALE_MIN_S, RAPID_MIN_S)
    if not math.isfinite(period_s) or period_s <= 0:
        return floor
    return min(max(PERIODS * period_s, floor), STALE_MAX_S)


def is_fresh(received_at: float | None, period_s: float | None, now: float,
             floor: float = STALE_MIN_S) -> bool:
    """True while a level received at `received_at` (consumer monotonic clock) still holds.

    Unknown receipt time or unknown cadence is not fresh.
    """
    if received_at is None or period_s is None:
        return False
    age = now - received_at
    return 0 <= age <= stale_after(period_s, floor)
