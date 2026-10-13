"""The LAN fallback's admin login: credential, sessions, brute-force limits (ADR-0003 §3).

`AdminAuth` is an interface object (like `PeerDirectory`): the HTTP core holds it for the
fallback listener. It reads the credential that `ops/set-admin-password.sh` writes (format
pinned by tests/test_dashboard_doors.py against the real ops script) and re-reads it when the
file changes, so a rotation ends every existing session without a restart.

Fails closed: with no credential, or a malformed one, nobody logs in. Limits are its own,
never PAM faillock, so attacking the fallback cannot lock the operator out of SSH.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from .doors import Refusal
from .identity import parse_address

#: PROVISIONAL (ADR-0003 §3; TODO.md Evidence): not yet measured.
SESSION_IDLE_S = 900.0
SESSION_MAX_S = 28800.0
LOGIN_FAILS_PER_SRC = 5
LOGIN_WINDOW_S = 900.0
#: Global backoff: after any failed login, every login waits this long. Many sources then
#: get one guess per second between them, while the operator is never locked out.
LOGIN_GLOBAL_GAP_S = 1.0
#: Memory bounds (§8.8).
MAX_SESSIONS = 32
MAX_TRACKED_SOURCES = 4096
MAX_PASSWORD_BYTES = 1024

COOKIE = "cn_session"
LABEL = "admin@fallback"


@dataclass
class Session:
    token: str
    csrf: str
    created: float
    last_seen: float
    cred_id: str


@dataclass(frozen=True)
class _Cred:
    id: str
    n: int
    r: int
    p: int
    salt: bytes
    digest: bytes


def _parse_cred(raw: bytes) -> _Cred:
    d = json.loads(raw)
    if d.get("format") != 1 or d.get("kdf") != "scrypt":
        raise ValueError("not a format-1 scrypt record")
    n, r, p = int(d["n"]), int(d["r"]), int(d["p"])
    if not (2 <= n <= 2**20 and n & (n - 1) == 0 and 1 <= r <= 32 and 1 <= p <= 16):
        raise ValueError("scrypt parameters out of range")
    salt, digest = base64.b64decode(d["salt"], validate=True), base64.b64decode(d["hash"], validate=True)
    if len(salt) < 8 or not 16 <= len(digest) <= 64:
        raise ValueError("salt or hash has the wrong length")
    return _Cred(id=d["hash"], n=n, r=r, p=p, salt=salt, digest=digest)


class AdminAuth:
    def __init__(self, cred_path: str, clock: Callable[[], float] = time.monotonic) -> None:
        self._path = cred_path
        self._clock = clock
        self._lock = threading.Lock()          # state below
        self._login_lock = threading.Lock()    # one login at a time: bounds scrypt CPU
        self._cred: _Cred | None = None
        self._cred_key: tuple | None = None
        self._cred_error = "not loaded"
        self._sessions: dict[str, Session] = {}
        self._fails: dict[str, deque[float]] = {}
        self._last_fail = -math.inf

    # --- credential -------------------------------------------------------------------

    def _load(self) -> _Cred | None:
        """The current credential, re-read only when the file changed. Caller holds _lock."""
        try:
            st = os.stat(self._path)
        except OSError:
            self._cred, self._cred_key, self._cred_error = None, None, "no admin password is set"
            return None
        key = (st.st_ino, st.st_mtime_ns, st.st_size)
        if key != self._cred_key:
            self._cred_key = key
            try:
                with open(self._path, "rb") as f:
                    self._cred = _parse_cred(f.read(4096))
                self._cred_error = ""
            except (OSError, ValueError, KeyError, TypeError, binascii.Error) as exc:
                self._cred, self._cred_error = None, f"the admin credential is unusable ({exc})"
        return self._cred

    def status(self) -> tuple[bool, str]:
        """(usable, reason). The fallback listener starts only when usable."""
        with self._lock:
            return self._load() is not None, self._cred_error or "ok"

    # --- login ------------------------------------------------------------------------

    def login(self, source: str, password: str) -> Session | Refusal:
        src = str(parse_address(source) or source)
        with self._login_lock:
            now = self._clock()
            with self._lock:
                fails = self._fails.get(src)
                while fails and now - fails[0] > LOGIN_WINDOW_S:
                    fails.popleft()
                if fails is not None and len(fails) >= LOGIN_FAILS_PER_SRC:
                    wait = math.ceil(LOGIN_WINDOW_S - (now - fails[0]))
                    return Refusal(429, "rate-limited",
                                   f"too many failed logins from {src}; try again in {wait} s")
                if now - self._last_fail < LOGIN_GLOBAL_GAP_S:
                    return Refusal(429, "rate-limited", "a login just failed; try again in a second")
                cred = self._load()
                if cred is None:
                    return Refusal(503, "disabled", f"the fallback is off: {self._cred_error}")
            raw = password.encode()
            ok = len(raw) <= MAX_PASSWORD_BYTES and hmac.compare_digest(
                hashlib.scrypt(raw, salt=cred.salt, n=cred.n, r=cred.r, p=cred.p,
                               dklen=len(cred.digest), maxmem=256 * cred.n * cred.r),
                cred.digest)
            with self._lock:
                if not ok:
                    self._last_fail = now
                    self._fails.setdefault(src, deque()).append(now)
                    while len(self._fails) > MAX_TRACKED_SOURCES:
                        self._fails.pop(next(iter(self._fails)))
                    return Refusal(401, "bad-password", "wrong password")
                self._fails.pop(src, None)
                session = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(32),
                                  now, now, cred.id)
                self._sessions[session.token] = session
                while len(self._sessions) > MAX_SESSIONS:
                    self._sessions.pop(min(self._sessions.values(), key=lambda s: s.last_seen).token)
                return session

    # --- sessions ---------------------------------------------------------------------

    def session(self, token: str | None) -> Session | None:
        """The live session for a cookie value, renewed; None if absent, expired or the
        password was rotated since it was issued."""
        if not token:
            return None
        with self._lock:
            s = self._sessions.get(token)
            if s is None:
                return None
            now = self._clock()
            cred = self._load()
            if (cred is None or cred.id != s.cred_id or now - s.created > SESSION_MAX_S
                    or now - s.last_seen > SESSION_IDLE_S):
                del self._sessions[token]
                return None
            s.last_seen = now
            return s

    def logout(self, token: str | None) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)
