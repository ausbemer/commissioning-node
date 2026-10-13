"""Ledger: the tailnet writer's join timeout (ADR-0005 §2, spec §8.11).

    python tools/ledgers/tailnet-writer/derive.py   # summarise every join in data/

Each row is one confirmed Join on a real node, timed from the daemon journal:
"tailscale join: running up" to "tailnet join succeeded". No rule derives UP_TIMEOUT_S
yet; this prints the observed worst case against the provisional value (see README).
"""
from __future__ import annotations

import csv
import statistics
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
UP_TIMEOUT_S = 60              # modules/tailnet_gateway/core/tailscale_writer.py (provisional)


def derive() -> None:
    secs: list[float] = []
    for path in sorted(DATA.glob("*.csv")):
        with path.open() as f:
            for row in csv.DictReader(f):
                secs.append(float(row["seconds"]))
                print(f"{row['date']}  {row['host']:>24}  {row['tailscale']}  "
                      f"{float(row['seconds']):6.3f} s")
    if not secs:
        raise SystemExit("no joins recorded")
    worst = max(secs)
    print(f"joins={len(secs)}  median {statistics.median(secs):.3f} s  worst {worst:.3f} s  "
          f"UP_TIMEOUT_S={UP_TIMEOUT_S} is {UP_TIMEOUT_S / worst:.1f}x the worst")


if __name__ == "__main__":
    derive()
