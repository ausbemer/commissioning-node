# Ledger: renewing tailscaled's operator (ADR-0002 A4)

Re-derive: `python tools/ledgers/tailscale-operator/derive.py`. Add a row to
`data/observations.csv` for each timing taken from the node's journal. Use
`journalctl -o short-monotonic` across a boot: the node's clock can be wrong until NTP syncs.

| Constant | Value | Where | Status |
|----------|-------|-------|--------|
| `OPERATOR_WAIT_S` | `30` | `ops/tailscale-operator.sh` | Provisional; 1 boot measured |
| `OPERATOR_RENEW_S` | `300` | `ops/systemd/commissioning-node-tailscale-operator.timer` (`OnUnitActiveSec`) | Provisional, a choice, not a measurement |

## `OPERATOR_WAIT_S`

The unit is ordered `After=tailscaled.service`, which is `Type=notify`, so its local API
normally answers before the script runs; the wait covers a slow or restarting tailscaled. One
boot (2026-10-25): 0.45 s from `Program starting` to tailscaled serving. 30 s is ~67x that.
Not tightened: a failed wait only costs one run (the timer runs again), and too short a wait
would turn a slow boot into a missed renewal.

## `OPERATOR_RENEW_S`

The worst case once both edges (tailscaled start, the state-file `.path`) are missed: a Join
fails, with a plain message, for up to this long. The edges are meant to make it moot: the
fastest Join after a Leave seen so far came 8 s later (2026-10-25). 300 s keeps a no-op run to
288 a day, and `LogLevelMax=notice` keeps them out of the journal. To measure in the A4
rehearsal: Leave → `OperatorUser` back (the `.path`), and how often the `.path` fires in a day.
