# Ledger: node-updater's check cadence and GitHub bounds (ADR-0009 D2)

Re-derive: `python tools/ledgers/node-updater/derive.py`. Measurements are in
`data/measurements.csv` (quirks Q-011).

| Constant | Value | Where | Status |
|----------|-------|-------|--------|
| `UPDATE_CHECK_S` | `1800` | `modules/node_updater/features/update_check.py` | Provisional, a choice |
| `UPDATE_RETRY_S` | `300` | same | Provisional, a choice |
| `CHECK_MIN_GAP_S` | `30` | same | Provisional, a choice |
| `MAX_RELEASES_WALKED` | `3` | `modules/node_updater/interface/source.py` | Provisional, a choice |
| `LS_REMOTE_TIMEOUT_S`, `HTTP_TIMEOUT_S` | `20`, `15` | `modules/node_updater/core/github_source.py` | Provisional; one ls-remote measured (0.46 s) |
| `MAX_RESPONSE_BYTES` | `2 MiB` | same | Provisional; largest compare seen 196 KB |

## The cadence and the API limit

GitHub allows 60 unauthenticated API calls an hour per IP, shared with everything else behind
the site's NAT. A check makes at most `MAX_RELEASES_WALKED` + 1 calls (CI of up to 3 tags, then
one compare), so the schedule costs at most 8 an hour, and a run of failures at most 48 (every
`UPDATE_RETRY_S`). A rate-limited failure doesn't retry sooner: it waits the full
`UPDATE_CHECK_S`, since retrying would only spend more of the allowance. So the 48 an hour
only arises from failures that cost calls without being the limit (a check failing after
its first API call); if a site sees that, raise `UPDATE_RETRY_S`. `CHECK_MIN_GAP_S` keeps Check now
from spending the allowance.

## The timeouts and the cap

One `ls-remote` from the RevPi took 0.46 s (2026-10-25); 20 s is ~43× that, chosen for slow site
uplinks, not measured on one. A `compare` lists the changed files, 196 KB for 13 commits; 2 MiB
allows ~10× that before the check fails as "too large" (it then offers nothing).
