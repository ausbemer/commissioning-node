# Ledger: tailnet writer timeouts (ADR-0005 §2)

Re-derive: `python tools/ledgers/tailnet-writer/derive.py`. Add a row to `data/joins.csv`
for each rehearsed or field Join, timed from the daemon journal ("tailscale join: running up"
to "tailnet join succeeded"). Time a join booted without a real-time clock from the journal's
monotonic stamps (`journalctl -o short-monotonic`): the node's clock can be hours wrong
until its first NTP sync, and may jump in the middle of a join.

| Constant | Value | Where | Status |
|----------|-------|-------|--------|
| `UP_TIMEOUT_S` | `60` | `modules/tailnet_gateway/core/tailscale_writer.py` | Provisional; 3 joins measured |
| `SET_TIMEOUT_S`, `LOGOUT_TIMEOUT_S`, `KILL_MARGIN_S`, `OUTPUT_CAP` | 15, 15, 10, 4096 | same | Provisional, not yet measured |

## `UP_TIMEOUT_S`

Measured on the RevPi (`Commissioning-node-pi5`, Tailscale 1.102.5, 2026-10-13), all joins
on the same network with a pre-authorised key:

| Request | `tailscale up` |
|---------|----------------|
| `e8722e282b23` | 4.408 s |
| `53188d01bdc6` | 5.164 s |
| `32d632b22031` | 4.999 s (auto-rejoin after a power cut, ephemeral node removed) |

60 s is 11.6× the worst observed join. That margin is deliberately not tightened yet: three
joins from one site say nothing about a slow or filtered site uplink (DERP-only, captive
proxies), which is where the timeout matters. Decide the rule (e.g. a multiple of the worst
field join) once joins from at least one other site are recorded.
