"""The admin credential writer (ADR-0003 §3): ops/admin_cred.py, run as the ops script runs it.

The file is the boundary between the ops tier and the dashboard's fallback login (step 4), so
these tests pin its format: re-deriving with the stored parameters must reproduce the hash.
The script is ops tier and is never imported, so it is executed here.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "ops" / "admin_cred.py"
PASSWORD = "correct horse battery staple"


def run(path: Path, password: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(SCRIPT), "set", "--path", str(path),
                           "--password-stdin"], input=password + "\n", capture_output=True,
                          text=True, timeout=60)


def test_writes_an_scrypt_record_that_verifies(tmp_path: Path) -> None:
    cred = tmp_path / "etc" / "admin.cred"
    out = run(cred, PASSWORD)
    assert out.returncode == 0, out.stderr
    assert stat.S_IMODE(cred.stat().st_mode) == 0o600
    raw = cred.read_text()
    assert PASSWORD not in raw and PASSWORD not in out.stdout + out.stderr

    rec = json.loads(raw)
    assert (rec["format"], rec["kdf"]) == (1, "scrypt")
    def derive(pw: str) -> bytes:
        return hashlib.scrypt(pw.encode(), salt=base64.b64decode(rec["salt"]), n=rec["n"],
                              r=rec["r"], p=rec["p"], dklen=rec["dklen"],
                              maxmem=256 * rec["n"] * rec["r"])
    assert derive(PASSWORD) == base64.b64decode(rec["hash"])
    assert derive(PASSWORD + "x") != base64.b64decode(rec["hash"])


def test_rotation_replaces_atomically_with_a_fresh_salt(tmp_path: Path) -> None:
    cred = tmp_path / "admin.cred"
    assert run(cred, PASSWORD).returncode == 0
    first = json.loads(cred.read_text())
    assert run(cred, PASSWORD).returncode == 0
    second = json.loads(cred.read_text())
    assert first["salt"] != second["salt"] and first["hash"] != second["hash"]
    assert sorted(os.listdir(tmp_path)) == ["admin.cred"]          # no temp files left


@pytest.mark.parametrize("bad", ["", "elevenchars"])
def test_short_password_is_refused_and_changes_nothing(tmp_path: Path, bad: str) -> None:
    cred = tmp_path / "admin.cred"
    assert run(cred, PASSWORD).returncode == 0
    before = cred.read_text()
    out = run(cred, bad)
    assert out.returncode != 0 and "at least 12" in out.stderr
    assert cred.read_text() == before
    assert sorted(os.listdir(tmp_path)) == ["admin.cred"]
