"""Ledger: the admin fallback's scrypt cost (ADR-0003 §3, spec §8.11).

    python tools/ledgers/admin-auth/derive.py measure   # time scrypt here -> data/<host>.csv
    python tools/ledgers/admin-auth/derive.py           # re-derive N from every data file

Rule: N is the largest power of two whose median derivation on the slowest measured host
fits LOGIN_BUDGET_S, with r=8, p=1 (128·N·r bytes of memory must stay well under the host's
RAM). R and P are fixed at the scrypt paper's interactive-login values.
"""
from __future__ import annotations

import csv
import hashlib
import os
import socket
import statistics
import sys
import time
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
LOGIN_BUDGET_S = 0.25          # one login, server side; see README for why
R, P = 8, 1
NS = [2**k for k in range(13, 18)]
RUNS = 7


def measure() -> Path:
    DATA.mkdir(exist_ok=True)
    out = DATA / f"{socket.gethostname()}.csv"
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["n", "r", "p", "seconds"])
        for n in NS:
            for _ in range(RUNS):
                salt = os.urandom(16)
                t = time.perf_counter()
                hashlib.scrypt(b"ledger timing password", salt=salt, n=n, r=R, p=P, dklen=32,
                               maxmem=256 * n * R)
                w.writerow([n, R, P, f"{time.perf_counter() - t:.4f}"])
    return out


def derive() -> int:
    worst: dict[int, float] = {}
    for path in sorted(DATA.glob("*.csv")):
        by_n: dict[int, list[float]] = {}
        with path.open() as f:
            for row in csv.DictReader(f):
                by_n.setdefault(int(row["n"]), []).append(float(row["seconds"]))
        for n, secs in by_n.items():
            med = statistics.median(secs)
            print(f"{path.stem:>16}  n=2^{n.bit_length() - 1:<2}  median {med:.3f} s  "
                  f"mem {128 * n * R // 2**20} MiB")
            worst[n] = max(worst.get(n, 0.0), med)
    fits = [n for n, s in worst.items() if s <= LOGIN_BUDGET_S]
    if not fits:
        sys.exit("no measured N fits the budget")
    n = max(fits)
    print(f"SCRYPT_N = 2**{n.bit_length() - 1}  (worst median {worst[n]:.3f} s "
          f"<= budget {LOGIN_BUDGET_S} s)")
    return n


if __name__ == "__main__":
    if sys.argv[1:] == ["measure"]:
        print(f"wrote {measure()}")
    derive()
