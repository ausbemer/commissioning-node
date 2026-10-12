#!/usr/bin/env bash
# ci.sh — portable CI for a Stage 0 project (spec §8.1 / §8.7).
#
# Runs the SAME checks locally and in CI, so `./ci.sh` before a push is the whole gate.
# It does NOT create tags or gate releases — release strictness lives in
# release-contract.sh. Keep it green on every commit to dev.
#
# Growing this file (spec §8.1): every codegen step gets a `--check` freshness gate here;
# every generated cross-language binding and every artifact shipped verbatim (static JS,
# embedded scripts) gets a gate that COMPILES or EXECUTES it. A gate that needs a toolchain
# not every environment has may be conditional — but it SKIPS LOUDLY:
#
#   if command -v some-compiler >/dev/null 2>&1; then
#     some-compiler check
#   else
#     echo "    SKIP: some-compiler not installed — <artifact> was NOT compiled"
#   fi
#
# Override the interpreter with PYTHON=... if needed.
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"

echo "==> Tests"
"$PY" -m pytest -q

echo "==> Additivity — strict superset, nested shapes typed or declared (§3/§8.1)"
"$PY" tools/check_additivity.py check

echo "==> Import boundaries — the §4 ontology (§8.1)"
"$PY" tools/check_boundaries.py

echo "==> Pin integrity (§2/§8.7; no-op at Stage 0)"
"$PY" tools/check_pins.py

echo "==> Ops scripts — parsed, linted, dry-run (§8.1: shipped scripts are executed)"
for f in ops/*.sh; do bash -n "$f"; done
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -x ops/*.sh ci.sh
else
  echo "    SKIP: shellcheck not installed — ops scripts were NOT linted"
fi
ops/install.sh --dry-run > /dev/null
echo "    ok: install.sh --dry-run"

echo "==> Firewall rules validate (nft -c)"
NFT="$(command -v nft || echo /usr/sbin/nft)"
if [[ -x "$NFT" ]] && sudo -n true 2>/dev/null; then
  sudo -n "$NFT" -c -f ops/firewall/commissioning-node.nft && echo "    ok: firewall rules"
else
  echo "    SKIP: needs nft + passwordless sudo — firewall rules were NOT validated"
fi

echo "==> MediaMTX config executes against the pinned binary"
MTX="${MEDIAMTX_BIN:-/opt/mediamtx/current/mediamtx}"
if [[ -x "$MTX" ]]; then
  ops/check_mediamtx_config.sh "$MTX"
else
  echo "    SKIP: no MediaMTX binary at $MTX (set MEDIAMTX_BIN) — config was NOT executed"
fi

echo "==> CI OK"
