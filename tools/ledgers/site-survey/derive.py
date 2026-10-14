"""Ledger: site-survey's scan bounds (ADR-0007 Safety 5, spec §8.11).

    python tools/ledgers/site-survey/derive.py   # summarise every scan in data/

Each row is one gentle scan run on a real site, with nmap's own `runstats/finished/@elapsed`.
Counts only: the hosts themselves are site data and never leave the site (ADR-0007 Safety 6).
No rule derives the constants yet; this prints the observed seconds per address and what that
predicts for the widest network allowed (MAX_PREFIX).
"""
from __future__ import annotations

import csv
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
MAX_PREFIX = 22                      # ADR-0007 D2 (provisional)


def derive() -> None:
    worst = 0.0
    for path in sorted(DATA.glob("*.csv")):
        with path.open() as f:
            for row in csv.DictReader(f):
                per = float(row["elapsed_s"]) / int(row["addresses"])
                worst = max(worst, per)
                print(f"{row['date']}  /{row['prefix']}  {row['addresses']:>5} addrs  "
                      f"{float(row['elapsed_s']):7.2f} s  {per * 1000:6.1f} ms/addr  "
                      f"tcp_up={row['tcp_up']} neigh_only={row['neigh_only']}  "
                      f"rate={row['max_rate']} host_timeout={row['host_timeout_s']} s")
    if not worst:
        raise SystemExit("no scans recorded")
    addrs = 2 ** (32 - MAX_PREFIX) - 2
    print(f"worst {worst * 1000:.1f} ms/addr -> /{MAX_PREFIX} ({addrs} addrs) ~ {worst * addrs:.0f} s")


if __name__ == "__main__":
    derive()
