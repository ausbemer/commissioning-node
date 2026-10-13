"""Tailscale adapter for the OverlayWriter seam: join, rename, leave (ADR-0005 §2).

The only code in the project that runs a mutating tailscale command (§11 rung 9). Core
calls it from the writer worker, one call at a time, never on the bus thread.

- Argument lists only, never a shell. `up` repeats `--operator=<daemon user>`: tailscale
  refuses an `up` that would silently drop a non-default setting (docs/quirks.md).
- The auth key is passed as `--auth-key=file:<path>`, so it is never in argv (ADR-0003 §5).
- Two timeouts: tailscale's own `--timeout` (its default blocks forever, measured on
  1.102.5), and a process timeout a little longer, after which the CLI is killed.
- Output is capped and scrubbed of anything shaped like an auth key before it becomes a
  message (§8.9).
- Inputs are re-checked here even though the registry validated them (rung 13): this is the
  last consumer before the site's tailnet.
"""
from __future__ import annotations

import logging
import os
import pwd
import re
import shutil
import stat
import subprocess
from typing import Callable

from ..interface import (ADVERTISE_ROUTES, AUTH_KEY, HOSTNAME, MODULE, TAGS, WriterReply,
                         route_problem)

log = logging.getLogger(MODULE)

#: PROVISIONAL (ADR-0005 §2; TODO.md Evidence): measure on the first real join.
UP_TIMEOUT_S = 60
SET_TIMEOUT_S = 15
LOGOUT_TIMEOUT_S = 15
#: Extra time the process gets beyond tailscale's own --timeout before it is killed.
KILL_MARGIN_S = 10
#: Characters of tailscale's output kept for a message (§8.8).
OUTPUT_CAP = 4096

_KEY_RE = re.compile(r"tskey-[A-Za-z0-9-]+")


def scrub(text: str) -> str:
    """Blank anything shaped like an auth key, THEN cap (§8.9): capping first could cut a key's
    "tskey-" prefix off and leave the rest unrecognised."""
    return _KEY_RE.sub("tskey-…", text)[-OUTPUT_CAP:].strip()


def _daemon_user() -> str:
    return pwd.getpwuid(os.geteuid()).pw_name


class TailscaleCliWriter:
    def __init__(self, binary: str = "tailscale",
                 run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                 which: Callable[[str], str | None] = shutil.which,
                 operator: Callable[[], str] = _daemon_user) -> None:
        self._binary = binary
        self._run = run
        self._which = which
        self._operator = operator

    # -- the seam ------------------------------------------------------------------------

    def join(self, hostname: str | None, tags: list[str], key_path: str,
             routes: list[str] = ()) -> WriterReply:
        routes = list(routes)
        problem = (self._check_hostname(hostname) if hostname is not None else None) \
            or self._check_tags(tags) or self._check_routes(routes) or self._check_key(key_path)
        if problem:
            return WriterReply(ok=False, message=f"join refused: {problem}")
        argv = [self._binary, "up", f"--operator={self._operator()}",
                f"--auth-key=file:{key_path}", f"--timeout={UP_TIMEOUT_S}s",
                # Always passed, empty included: `up` refuses to drop a non-default setting
                # it isn't told about (ADR-0005 A3).
                f"--advertise-routes={','.join(routes)}"]
        if hostname is not None:
            argv.append(f"--hostname={hostname}")
        if tags:
            argv.append(f"--advertise-tags={','.join(tags)}")
        named = f" as {hostname}" if hostname else ""
        return self._call("join", argv, UP_TIMEOUT_S, f"joined the tailnet{named}")

    def rename(self, hostname: str) -> WriterReply:
        problem = self._check_hostname(hostname)
        if problem:
            return WriterReply(ok=False, message=f"rename refused: {problem}")
        argv = [self._binary, "set", f"--hostname={hostname}"]
        return self._call("rename", argv, SET_TIMEOUT_S, f"renamed to {hostname}")

    def set_routes(self, routes: list[str]) -> WriterReply:
        problem = self._check_routes(routes)
        if problem:
            return WriterReply(ok=False, message=f"routes refused: {problem}")
        argv = [self._binary, "set", f"--advertise-routes={','.join(routes)}"]
        shown = ", ".join(routes) or "none"
        return self._call("routes", argv, SET_TIMEOUT_S, f"now advertising {shown}")

    def leave(self) -> WriterReply:
        return self._call("leave", [self._binary, "logout"], LOGOUT_TIMEOUT_S, "left the tailnet")

    # -- running -------------------------------------------------------------------------

    def _call(self, op: str, argv: list[str], timeout_s: int, done: str) -> WriterReply:
        if self._which(self._binary) is None:
            return WriterReply(ok=False, message=f"{op} failed: the tailscale CLI is not installed")
        log.info("tailscale %s: running %s", op, " ".join(argv[1:2]))   # never the arguments
        try:
            proc = self._run(argv, capture_output=True, text=True, errors="replace",
                             timeout=timeout_s + KILL_MARGIN_S)
        except subprocess.TimeoutExpired:
            return WriterReply(ok=False, message=f"{op} timed out after {timeout_s + KILL_MARGIN_S}s; "
                                                 "the node's tailnet state is unknown until the next "
                                                 "status read")
        except OSError as exc:
            return WriterReply(ok=False, message=f"{op} failed: {exc.strerror}")
        except ValueError as exc:                  # the seam must not raise
            return WriterReply(ok=False, message=f"{op} failed: {type(exc).__name__}")
        if proc.returncode != 0:
            detail = scrub((proc.stderr or "") + (proc.stdout or "")) or "no output"
            return WriterReply(ok=False, message=f"{op} failed (exit {proc.returncode}): {detail}")
        return WriterReply(ok=True, message=done)

    # -- re-checks (rung 13) -------------------------------------------------------------

    @staticmethod
    def _check_hostname(hostname: str) -> str | None:
        if not isinstance(hostname, str) or len(hostname) > (HOSTNAME.max_len or 63) \
                or not re.fullmatch(HOSTNAME.pattern, hostname):
            return "the hostname is not a DNS label"
        return None

    @staticmethod
    def _check_tags(tags: list[str]) -> str | None:
        if len(tags) > (TAGS.max_items or 0):
            return "too many tags"
        for tag in tags:
            if not isinstance(tag, str) or not re.fullmatch(TAGS.pattern, tag):
                return "a tag is not of the form tag:name"
        return None

    @staticmethod
    def _check_routes(routes: list[str]) -> str | None:
        if len(routes) > (ADVERTISE_ROUTES.max_items or 0):
            return "too many routes"
        for cidr in routes:
            problem = route_problem(cidr) if isinstance(cidr, str) else "a route is not text"
            if problem:
                return problem
        return None

    @staticmethod
    def _check_key(key_path: str) -> str | None:
        """Same rule as the registry: a regular file, 0600 or tighter, owned by the daemon."""
        try:
            st = os.lstat(key_path)
        except OSError:
            return f"the {AUTH_KEY.label.lower()} is no longer stored"
        if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077 or st.st_uid != os.geteuid():
            return f"the stored {AUTH_KEY.label.lower()} is not a private file"
        return None
