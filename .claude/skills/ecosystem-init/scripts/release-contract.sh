#!/usr/bin/env bash
# release-contract.sh — release the contract package: verify -> promote -> tag.
# Spec §3/§8.7: the tag IS the release. This script refuses to tag if tests or
# the additivity superset check fail.
#
# Run from the contract repo root, on the dev branch, with the version already
# bumped in the package. Usage: ./release-contract.sh
set -euo pipefail

git diff --quiet || { echo "working tree dirty — commit first"; exit 1; }
[[ "$(git branch --show-current)" == "dev" ]] || { echo "run from dev branch"; exit 1; }

VERSION=$(python3 - <<'EOF'
import importlib
print(importlib.import_module("interfaces").__version__)
EOF
)
TAG="v${VERSION}"
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && { echo "$TAG already exists — bump the version first"; exit 1; }

echo "==> Releasing $TAG"

echo "==> Tests"
python3 -m pytest -q

echo "==> Additivity superset check (spec 8.1)"
python3 tools/check_additivity.py check

echo "==> Promote dev -> master"
git checkout master
git merge --ff-only dev || { echo "master has diverged from dev — resolve manually"; git checkout dev; exit 1; }

echo "==> Tag + refresh baseline snapshot"
git tag -a "$TAG" -m "Contract release $TAG"
python3 tools/check_additivity.py snapshot
git add schemas/ && git commit -m "Baseline snapshot for $TAG" --allow-empty
git checkout dev
git merge --ff-only master

echo "==> Done: $TAG cut. Push with: git push origin master dev $TAG"
echo "    Consumers may now pin interfaces @ $TAG — when they want the new fields."
