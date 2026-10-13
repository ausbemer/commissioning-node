# Ledger: admin fallback authentication (ADR-0003 §3)

Re-derive: `python tools/ledgers/admin-auth/derive.py`. Re-measure on a new host class:
`python tools/ledgers/admin-auth/derive.py measure` (writes `data/<hostname>.csv`, 7 runs per N).

| Constant | Value | Where | Status |
|----------|-------|-------|--------|
| `SCRYPT_N` | `2**15` | `ops/admin_cred.py` | Measured |
| `SCRYPT_R`, `SCRYPT_P` | `8`, `1` | `ops/admin_cred.py` | Fixed: the scrypt paper's interactive-login values |
| `LOGIN_BUDGET_S` | `0.25` | `derive.py` | Judgement, not measured (below) |
| `MIN_LENGTH` | `12` | `ops/admin_cred.py` | Provisional (ADR-0003 Consequences) |
| `SESSION_IDLE_S`, `SESSION_MAX_S`, `LOGIN_FAILS_PER_SRC`, `LOGIN_WINDOW_S` | 900, 28800, 5, 900 | dashboard (step 4) | Provisional, not yet in code |

## `SCRYPT_N`

Measured on the RevPi (`RevPi136506`, 2026-10-13, Python 3.13 / OpenSSL 3.5, 3.7 GiB RAM):

| N | median | memory |
|---|--------|--------|
| 2^13 | 0.046 s | 8 MiB |
| 2^14 | 0.092 s | 16 MiB |
| **2^15** | **0.223 s** | **32 MiB** |
| 2^16 | 0.448 s | 64 MiB |
| 2^17 | 0.901 s | 128 MiB |

Rule: the largest N whose median on the slowest measured host fits `LOGIN_BUDGET_S`.

**Why 0.25 s:** this is a judgement, not a measurement. One login costs one core for this
long. The fallback rate-limits failed logins per source and globally (step 4), so the hash
cost is defence in depth against a stolen `admin.cred`, not the main brute-force control,
and at 32 MiB per guess it already makes offline guessing memory-bound. Doubling
N would double the cost of both an attacker's guess and a burst of logins on a 4-core node
that also relays video. Revisit if the node gets faster hardware or the fallback ever
accepts logins without the rate limit.

The stored record carries its own parameters, so changing N affects only passwords set
afterwards; existing credentials keep verifying.
