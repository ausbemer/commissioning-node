#!/usr/bin/env python3
"""Pin-references-a-real-tag check — enforcement of spec §2 / §8.7 / §9.

The contract pin is the sole interop authority, and "the tag IS the release" (§3): a
service may only pin a contract version that has been cut as a git tag. This check scans
every service's requirements for an `{ecosystem}-interfaces` pin and verifies each
referenced version exists as a tag in the contract repo. A pin to an untagged version is
the one unforgivable versioning crime — it makes the interop authority lie.

Stage 0 note: a single-repo monolith has no cross-service pins, so this is a no-op there
(it exits 0 with "no pins found"). It earns its keep at Stage 1.

Usage:
  python check_pins.py --contract-repo PATH [--glob 'services/*/requirements.txt' ...]

Defaults: contract-repo '.', globs 'services/*/requirements.txt' and
'*/requirements.txt'. Recognizes both pip-style `name-interfaces==1.4.0` and
git-URL `...@v1.4.0` pin forms.
Exit codes: 0 ok / no pins, 1 a pin references a missing tag, 2 usage/setup error.
"""
from __future__ import annotations

import argparse
import glob as globmod
import re
import subprocess
import sys
from pathlib import Path

PIN_RE = re.compile(r"([A-Za-z0-9_.-]*interfaces)\s*(?:==|@\s*v?)\s*(\d+\.\d+\.\d+)")


def existing_tags(contract_repo: str) -> set[str]:
    try:
        out = subprocess.run(
            ["git", "-C", contract_repo, "tag"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"cannot read tags from contract repo '{contract_repo}': {exc}", file=sys.stderr)
        sys.exit(2)
    # normalize: accept both '1.4.0' and 'v1.4.0' tags
    tags = set()
    for t in out.split():
        tags.add(t)
        tags.add(t.lstrip("v"))
    return tags


def find_pins(globs: list[str]) -> list[tuple[Path, str, str]]:
    found = []
    for pattern in globs:
        for path in map(Path, globmod.glob(pattern, recursive=True)):
            if not path.is_file():
                continue
            for pkg, ver in PIN_RE.findall(path.read_text()):
                found.append((path, pkg, ver))
    return found


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract-repo", default=".")
    ap.add_argument("--glob", action="append", dest="globs")
    args = ap.parse_args()
    globs = args.globs or ["services/*/requirements.txt", "*/requirements.txt"]

    pins = find_pins(globs)
    if not pins:
        print("no interfaces pins found — nothing to check (expected at Stage 0).")
        return

    tags = existing_tags(args.contract_repo)
    missing = [(p, pkg, ver) for (p, pkg, ver) in pins if ver not in tags]

    for path, pkg, ver in sorted(set(pins)):
        mark = "MISSING" if ver not in tags else "ok"
        print(f"[{mark:>7}] {pkg} @ {ver}   ({path})")

    if missing:
        print(f"\nFAILED: {len(missing)} pin(s) reference a contract version with no tag.")
        print("The tag IS the release (§3). Cut the tag, or fix the pin.")
        sys.exit(1)
    print(f"\nOK: all {len(set(pins))} pin(s) reference an existing tag.")


if __name__ == "__main__":
    main()
