# Evidence: ledgers and the replay corpus

Created at init because commissioning-node acts on the world (ADR-0001 "Risk profile"). This is
where behaviour and safety numbers come from (spec §8.11, §8.12).

## Ledgers — `tools/ledgers/`

Every constant that changes behaviour or safety (a threshold, timeout, gain, distance) is
backed by a measurement, not chosen by feel. One ledger per decision area:

```
tools/ledgers/{area}/
├── README.md     # each constant: value, the data it came from, why that value, which ADR
├── data/         # the logged inputs (or a pointer to where they are archived)
└── derive.py     # re-computes every number in README.md from data/
```

Changing a constant means re-running `derive.py` and updating the entry, not editing the
number. When several constants are decided together, write the ADR as a **decision
ledger** (spec §7) and link it here.

## Replay corpus — `tests/replay/`

Recorded episodes (real, or rehearsed in simulation mode §13 and labelled as such),
re-run against the live code by a replay test. For behavioural or safety changes this
corpus is an acceptance gate alongside the unit tests. Add an episode whenever the field
produces a new interesting case. The corpus is versioned with the code it gates.

| Area | Ledger | Episodes | Gating test |
|------|--------|----------|-------------|
| Admin fallback auth | `tools/ledgers/admin-auth/` (scrypt cost) | — | — |
