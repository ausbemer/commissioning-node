# Ledger: nav-relay constants (ADR-0010, Safety 6)

Every constant here is **provisional**: nothing has been measured on a real NMEA 2000 bus yet.
The first measurements come from the node with the Kvaser on the vessel's bus (ADR-0010
implementation step 1):

```
candump -l -L n2k0          # 10 minutes, under way if possible; keep the log OUT of git
                            # until positions are scrubbed (site data, ADR-0010 Safety 7)
```

From that log, record **counts and rates only**: frames per PGN per source, the interval
between frames (median, p99, worst), and how many sources send each PGN.

| Constant | Value | Where | What would confirm it |
|----------|-------|-------|-----------------------|
| `NOMINAL_PERIOD_S` | 129025 0.1, 129026 0.25, 127250 0.1, 126992 1.0 s | `interface/nav.py` | Measured median interval per PGN |
| `freshness.RAPID_MIN_S` | 1.0 s | `freshness` | p99 interval of 129025 well under it, so no "no fix" flaps on a healthy bus |
| `OUTPUT_PERIOD_S` | 1.0 s | `interface/nav.py` | The destination's needs (gpsd is fine at 1 Hz) |
| `MAX_SOURCES` | 16 (PGN, source) pairs | `interface/nav.py` | Sources seen on a real bus × 4 PGNs |
| `MAX_DESTINATIONS` | 4 | `interface/nav.py` | Operator need; no measurement |
| `LINK_CHECK_S` | 5.0 s | `core/workers.py` | How fast an unplug must show (≤ one tick) |
| `RCVBUF_BYTES` | 64 KiB | `core/socketcan_source.py` | No drops (`ip -s link show n2k0`) at the measured frame rate |

## Measurements

| Date | Vessel / bus | Duration | Frames/s (filtered) | Sources per PGN | 129025 interval median / p99 / worst |
|------|--------------|----------|---------------------|-----------------|--------------------------------------|
| — | | | | | |
