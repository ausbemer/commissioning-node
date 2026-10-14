# Ledger: site-survey scan bounds (ADR-0007, Safety 5)

Re-derive: `python tools/ledgers/site-survey/derive.py`. Add a row to `data/scans.csv` for each
gentle scan on a real site, from nmap's XML (`runstats/finished/@elapsed`, hosts up) and the
neighbour table (live entries nmap missed, quirks Q-006). **Counts only**: addresses, MACs and
hostnames are site data and are never committed (ADR-0007 Safety 6).

| Constant | Value | Status |
|----------|-------|--------|
| `MAX_RATE` | 100 packets/s | Provisional; 2 scans, no site complaint (a home lab says little) |
| `MAX_PREFIX` | /22 | Provisional; derived time below |
| `HOST_TIMEOUT_S` | 15 | Provisional; found every host the 30 s scan found, and one more |
| `SCAN_TIMEOUT_S` | 300 | Provisional; 2.4× the /22 projected from the worst ms/address |
| `KILL_MARGIN_S` | 10 | Provisional, as the tailnet writer's (not measured) |
| `OUTPUT_CAP`, `MAX_HOSTS` | 4 MiB, 1022 | Sized to a full /22 (`MAX_HOSTS` = its address count) |

## Scans

| Date | Network | `--host-timeout` | Elapsed | Found by TCP | Neighbour only |
|------|---------|------------------|---------|--------------|----------------|
| 2026-10-13 | /24 (255 addresses), home lab | 30 s | 30.73 s | 10 | 4 |
| 2026-10-13 | same | 15 s | 23.62 s | 11 | 4 |
| 2026-10-13 | same, through `NmapScanner` (incl. `ip` reads) | 15 s | 27.6 s | 10 | 4 |

Halving the host timeout cut 7 s, not half: the host timeout is part of the time, not most of
it. At `MAX_RATE` the probes alone take about 8 s for a /24 (255 addresses × ~3 discovery
probes / 100 per s); the rest is waiting on hosts that never answer. Projecting the worse scan
(120.5 ms/address) to a /22 gives ~123 s; `SCAN_TIMEOUT_S = 300` leaves 2.4× for a slower site.
Re-derive once a scan from a real commissioning site (more hosts, OT gear) is recorded.
