"""ops/admin_cred.py — write the LAN fallback's admin credential (ADR-0003 §3).

Run by ops/set-admin-password.sh (as root, interactive). It is ops tier: run, never imported.

The file is a JSON object holding an scrypt hash and its parameters, never the password:

    {"format": 1, "kdf": "scrypt", "n": 32768, "r": 8, "p": 1, "dklen": 32,
     "salt": "<base64>", "hash": "<base64>", "set_at": "<ISO 8601 UTC>"}

The dashboard verifies a login by re-deriving with the stored parameters, so they can be
raised later without invalidating an existing file. The write is atomic (temp file in the
same directory, fsync, rename), mode 0600, owned by the daemon user (§8.9).

Usage: admin_cred.py set --path PATH [--owner USER] [--password-stdin]
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import pwd
import secrets
import sys
import tempfile
from datetime import datetime, timezone

FORMAT = 1
# Ledgered in tools/ledgers/admin-auth/ (spec §8.11): N measured on the RevPi.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
DKLEN = 32
SALT_BYTES = 16
MIN_LENGTH = 12


def derive(password: str, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
    # scrypt needs 128·n·r bytes (32 MiB here) and OpenSSL's default ceiling is exactly that,
    # so leave headroom or the derivation fails with "memory limit exceeded".
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=dklen,
                          maxmem=128 * n * r * 2)


def record(password: str) -> dict:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = derive(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P, DKLEN)
    return {
        "format": FORMAT, "kdf": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
        "dklen": DKLEN, "salt": base64.b64encode(salt).decode(),
        "hash": base64.b64encode(digest).decode(),
        "set_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def write_atomic(path: str, data: dict, owner: str | None) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, mode=0o755, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".admin.cred.")   # created 0600
    try:
        if owner is not None:
            pw = pwd.getpwnam(owner)
            os.fchown(fd, pw.pw_uid, pw.pw_gid)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    dfd = os.open(parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("New admin password: ")
    if getpass.getpass("Repeat it: ") != first:
        raise SystemExit("refusing: the passwords do not match; nothing was changed")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    cmd = sub.add_parser("set", help="hash a new admin password and write it")
    cmd.add_argument("--path", required=True)
    cmd.add_argument("--owner", help="user to own the file (the daemon user); needs root")
    cmd.add_argument("--password-stdin", action="store_true",
                     help="read one line from stdin instead of prompting (tests, automation)")
    args = parser.parse_args(argv)

    password = read_password(args.password_stdin)
    if len(password) < MIN_LENGTH:
        raise SystemExit(f"refusing: the password must be at least {MIN_LENGTH} characters; "
                         "nothing was changed")
    write_atomic(args.path, record(password), args.owner)
    print(f"admin password set: {args.path} (scrypt n={SCRYPT_N} r={SCRYPT_R} p={SCRYPT_P})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
