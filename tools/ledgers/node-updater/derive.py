"""Ledger: node-updater's check cadence and GitHub bounds (ADR-0009 D2, spec §8.11).

    python tools/ledgers/node-updater/derive.py   # the measurements against the constants

No rule derives the constants yet; this prints each measurement against them (see README).
"""
from __future__ import annotations

import csv
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
# modules/node_updater/features/update_check.py, core/github_source.py, interface/source.py
UPDATE_CHECK_S, UPDATE_RETRY_S, CHECK_MIN_GAP_S = 1800, 300, 30
LS_REMOTE_TIMEOUT_S, HTTP_TIMEOUT_S, MAX_RESPONSE_BYTES = 20, 15, 2 * 1024 * 1024
MAX_RELEASES_WALKED = 3


def derive() -> None:
    rows = {r["what"]: r for r in csv.DictReader((DATA / "measurements.csv").open())}
    for r in rows.values():
        print(f"{r['date']}  {r['what']}: {r['value']} {r['unit']}")
    ls = float(rows["git ls-remote --heads --tags (repo)"]["value"])
    big = int(rows["compare response (13 commits apart)"]["value"])
    limit = int(rows["unauthenticated API limit"]["value"])
    calls = MAX_RELEASES_WALKED + 1
    print(f"LS_REMOTE_TIMEOUT_S={LS_REMOTE_TIMEOUT_S} is {LS_REMOTE_TIMEOUT_S / ls:.0f}x one ls-remote")
    print(f"MAX_RESPONSE_BYTES is {MAX_RESPONSE_BYTES / big:.1f}x the largest compare seen")
    print(f"scheduled: {3600 / UPDATE_CHECK_S * calls:.0f} API calls/h at most; retrying: "
          f"{3600 / UPDATE_RETRY_S * calls:.0f}/h; limit {limit}/h per IP")


if __name__ == "__main__":
    derive()
