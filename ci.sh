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

echo "==> CI OK"
