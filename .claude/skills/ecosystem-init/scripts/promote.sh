#!/usr/bin/env bash
# promote.sh — workspace promotion: copy validated dev pins onto master, then tag.
# Spec §2/§8.7: promotion is pin-copying, NEVER `git merge dev`.
#
# Usage: ./promote.sh vX.Y.Z ["release message"]
set -euo pipefail

TAG="${1:?usage: promote.sh vX.Y.Z [message]}"
MSG="${2:-Promote validated set ${TAG}}"

[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "tag must look like v1.2.3"; exit 1; }
git diff --quiet || { echo "working tree dirty — commit or stash first"; exit 1; }

echo "==> Recording validated pins from dev"
git checkout dev
git pull --ff-only 2>/dev/null || true
declare -A PINS
while read -r path; do
  PINS["$path"]=$(git -C "$path" rev-parse HEAD)
  echo "    $path @ ${PINS[$path]}"
done < <(git config --file .gitmodules --get-regexp path | awk '{print $2}')

echo "==> Copying pins onto master (no merge)"
git checkout master
for path in "${!PINS[@]}"; do
  git -C "$path" fetch --quiet origin 2>/dev/null || true
  git -C "$path" checkout --quiet "${PINS[$path]}"
  git add "$path"
done

if git diff --cached --quiet; then
  echo "master already at these pins — nothing to promote"
else
  git commit -m "$MSG"
fi

git tag -a "$TAG" -m "$MSG"
echo "==> Done: master pinned + tagged $TAG. Push with: git push origin master $TAG"
