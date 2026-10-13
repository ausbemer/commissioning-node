# commissioning-node — Workflow

Day-to-day rituals. The spec (`META-ARCHITECTURE.md`) says *why*; this says *how, here*.

## Branches

Work happens on `dev`. `master` holds validated states and receives promotions only.

## Start a feature

```bash
git checkout dev
git checkout -b feature/<feature-name>
# implement the Feature protocol from modules/<module_pkg>/interface/feature.py
# register it in modules/<module_pkg>/__init__.py build() with enabled=False until ready
```

Need new data fields? Add them (optional!) on `dev` first, bump the interface MINOR
(`__version__`), then branch from that dev. Shipping a feature behind an unchanged
interface bumps the module's release `VERSION` only (spec §4 dual versioning).
Contract-package changes additionally require:

```bash
python3 tools/check_additivity.py check   # must pass before any contract commit
```

A nested shape is a model (subclass `Shape`), never a bare `dict`. If the checker reports
an untyped field you truly cannot type yet, declare it in `schemas/blind_spots.txt`
(`Model.field  # why + plan`). `python3 tools/check_additivity.py blind-spots` lists them.

## New package or new dependency

- **New top-level package:** pick its §4 tier (neutral, bus-coupled, ops) and declare it in
  `tools/check_boundaries.py`. The boundary check fails until you do.
- **New derived fact other modules need:** one canonical authority (a neutral package),
  imported everywhere. If it ports a reference algorithm, its tests execute the original
  and diff the outputs (§4).
- **Pinning a foreign runtime/vendor library:** contain it behind one bridge module (§6),
  start or extend `docs/quirks.md`, and re-verify every quirk entry on each pin bump (§7).
- **Codegen or verbatim-shipped artifacts:** add a `--check` freshness gate and a
  compile/execute gate to `ci.sh`. Conditional gates skip loudly (§8.1).

## Behaviour and safety changes

See `tools/ledgers/README.md` (spec §8.11/§8.12). Provisional constants waiting for a
measurement are listed in `TODO.md` → Evidence.

- New or changed threshold/timeout/gain: measure it, add it to `tools/ledgers/` with the
  re-derivation script, and link the ledger from the ADR (§8.11).
- Behavioural change: replay the corpus in `tests/replay/` and add any new episode (§8.12).
- Actuation: the ADR fills the Safety section (§11 ladder, rungs 1–13). Here that means
  any Tailscale state change, any VAPIX write to a site camera (first write
  operator-confirmed, rung 3), or any MediaMTX config write. Each has exactly one writer
  module (ADR-0001).

## Release / promote

Stage 0: promotion is just merging validated dev to master and tagging the monolith.
Refresh the additivity baseline at each tagged release:

```bash
git checkout master && git merge --ff-only dev
python3 tools/check_additivity.py snapshot && git add schemas/ && git commit -m "Baseline for vX.Y.Z"
git tag -a vX.Y.Z -m "commissioning-node vX.Y.Z"
git checkout dev && git merge --ff-only master
```

Before tagging, run one inline review over the whole release diff, hunting only what
per-merge reviews can't see: cross-feature interactions and semantic drift (§8.7).

## Run it

```bash
python3 main.py                       # daemon: ticks every 5 s; dashboard doors (ADR-0003):
                                      #   http://127.0.0.1:8080/       local
                                      #   http://<tailnet name>:8080/  tailnet peers
                                      #   https://<node LAN IP>:8443/  admin fallback (needs a password)
CN_DASHBOARD_PORT=8099 python3 main.py
ssh -L 8080:127.0.0.1:8080 pi@<node>  # the local door from elsewhere
```

## Host install (ops tier, ADR-0002)

```bash
ops/install.sh --dry-run      # what would change; needs no root and no network
sudo ops/install.sh           # apply: Tailscale + MediaMTX at the ops/pins.sh versions,
                              # the firewall, and the systemd units. Safe to re-run.
sudo ops/set-admin-password.sh  # enable the LAN admin fallback (interactive; no default)
tailscale up --operator=<user>  # join the tailnet (manual: the auth key is a secret)
sudo ops/doctor.sh            # read-only health check (without sudo, root checks SKIP)
```

Bumping a pin: edit `ops/pins.sh`, re-check every entry for that dependency in
`docs/quirks.md`, run `ops/check_mediamtx_config.sh <new binary>` (MediaMTX), then re-run
the installer. Never `apt upgrade` tailscale past its hold by hand.

## Tests

```bash
python3 -m pytest -q          # smoke + additivity + boundaries; keep green on every dev commit
```

## Before you push

```bash
./ci.sh                      # tests + additivity + boundaries + pin check — same gate as CI.
                             # Green here == green in GitHub Actions (.github/workflows/ci.yml).
                             # Release tags are NOT gated here; that is release-contract.sh.
```

## Guards

```bash
# Detached-HEAD guard — install once per repo (and per submodule at Stage 1):
cp tools/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit

# Pin-references-a-real-tag check (no-op at Stage 0; wired into ci.sh for Stage 1):
python3 tools/check_pins.py
```

## Graduation (Stage 0 → Stage 1)

Extract a module only when a spec §10 trigger fires: second independent owner,
divergent deploy cadence, divergent runtime, or the middleman smell. Mechanics:
spec §10 "Extraction mechanics", or ask an agent to run the ecosystem-init skill,
which detects the existing scaffold and offers guided graduation.

A single lever (a real broker, an out-of-process component, a second language) can be
pulled early behind one module seam, with its own ADR, without the repo split (§10).

## Spec upgrades

This project vendors the spec at the version in ADR-0001. To adopt a newer one, copy the
new `META-ARCHITECTURE.md` over the old one unedited, then add an ADR-0001 amendment
recording the new version and what you adopted from it. The spec is additive, so
fast-forwarding is safe (§12).

## Non-goals (from init interview)

None stated yet. Record each one here and in `TODO.md` as it comes up.
