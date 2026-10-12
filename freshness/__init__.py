"""freshness — the canonical authority for "how old may a renewed level get?" (spec §4, §5).

Neutral package: pure functions, no I/O, imports nothing but the standard library.

A consumer of a periodic feed derives its staleness window from the cadence the PRODUCER
declares (§11 rung 11), never from a constant of its own:

    window = clamp(3 × period_s, STALE_MIN_S, STALE_MAX_S)

Used by stream-relay (tailnet status → stream endpoints) and the dashboard (tailnet peer
table → caller identity, ADR-0003). One implementation, so the two can never drift apart.

Ages are measured on the consumer's own monotonic clock from when it RECEIVED the message,
not from a producer timestamp: wall-clock skew or a clock step must not make a stale level
look fresh.
"""
from __future__ import annotations

import math

#: Bounds on the derived staleness window, seconds. PROVISIONAL — not yet measured (TODO.md, §8.11).
STALE_MIN_S = 5.0
STALE_MAX_S = 120.0
#: How many declared periods may pass without a renewal before the level lapses.
PERIODS = 3


def stale_after(period_s: float) -> float:
    """The staleness window for a feed that declares `period_s`.

    A period that is not a finite positive number gets the strictest window: the level
    fails toward lapsing, never toward living forever (NaN would otherwise compare false
    against every age).
    """
    if not math.isfinite(period_s) or period_s <= 0:
        return STALE_MIN_S
    return min(max(PERIODS * period_s, STALE_MIN_S), STALE_MAX_S)


def is_fresh(received_at: float | None, period_s: float | None, now: float) -> bool:
    """True while a level received at `received_at` (consumer monotonic clock) still holds.

    Unknown receipt time or unknown cadence is not fresh.
    """
    if received_at is None or period_s is None:
        return False
    age = now - received_at
    return 0 <= age <= stale_after(period_s)
