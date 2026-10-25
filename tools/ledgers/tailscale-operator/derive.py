"""Ledger: renewing tailscaled's operator (ADR-0002 A4, spec §8.11).

    python tools/ledgers/tailscale-operator/derive.py   # summarise data/, check the constants

Rows in data/observations.csv are timings from the node's journal. No rule derives the
constants yet; this prints each observation against them (see README).
"""
from __future__ import annotations

import csv
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
OPERATOR_WAIT_S = 30       # ops/tailscale-operator.sh (provisional)
OPERATOR_RENEW_S = 300     # ops/systemd/commissioning-node-tailscale-operator.timer (provisional)


def derive() -> None:
    rows = list(csv.DictReader((DATA / "observations.csv").open()))
    if not rows:
        raise SystemExit("no observations recorded")
    for row in rows:
        print(f"{row['date']}  {row['host']}  {row['tailscale']}  {float(row['seconds']):7.3f} s  {row['event']}")
    api = [float(r["seconds"]) for r in rows if "local API" in r["event"]]
    if api:
        print(f"local API ready: worst {max(api):.3f} s; OPERATOR_WAIT_S={OPERATOR_WAIT_S} "
              f"is {OPERATOR_WAIT_S / max(api):.0f}x it")
    gaps = [float(r["seconds"]) for r in rows if "Leave to" in r["event"]]
    if gaps:
        print(f"Leave -> next Join: fastest {min(gaps):.0f} s. The .path unit must beat it; "
              f"OPERATOR_RENEW_S={OPERATOR_RENEW_S} is only the backstop")


if __name__ == "__main__":
    derive()
