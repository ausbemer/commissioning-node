"""GitHub adapter for the ReleaseSource seam — the module's bridge to GitHub (ADR-0009 D2, §6).

READ-ONLY and unauthenticated: `git ls-remote` over HTTPS for the refs, and the public REST API
for CI results and for how two commits relate. No credential on the node is used, or could be:

- `git` runs hermetic (no system or global config, no prompt, no credential helper), so the
  daemon user's own git config (a URL rewrite to SSH and a deploy key, say) never applies.
- API calls carry no token. The limit is 60 an hour per IP (quirks Q-011); a check makes at most
  `MAX_RELEASES_WALKED` + 1 of them.

Everything from GitHub is checked before it's used (rung 13): a commit must be 40 hex digits and
a ref a plain name; responses are read up to `MAX_RESPONSE_BYTES`. Measured 2026-10-25 against
this repo (quirks Q-011): annotated tags list the tag object, then the commit under `^{}`; tags
get no CI run of their own (the run is the branch push's, found by `head_sha`); `compare`
answers 404 for a commit GitHub doesn't have, and lists files even with `per_page=1`.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import urllib.error
import urllib.request
from typing import Callable

from ..interface import (COMMIT, MODULE, VERSION, ReleaseReading, choose_target)

log = logging.getLogger(MODULE)

#: The repository updates come from (ADR-0009 decision 2: GitHub is the root of trust).
REPO = "ausbemer/commissioning-node"
GIT_URL = f"https://github.com/{REPO}.git"
API = "https://api.github.com"
#: The workflow whose result counts as "CI" (.github/workflows/ci.yml `name:`).
CI_WORKFLOW = "ci"

#: PROVISIONAL — tools/ledgers/node-updater/ (§8.11). ls-remote took 0.46 s on 2026-10-25.
LS_REMOTE_TIMEOUT_S = 20
HTTP_TIMEOUT_S = 15
#: Largest response read: a `compare` was 196 KB on 2026-10-25 (it lists files).
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

_REF = re.compile(r"refs/(heads|tags)/[A-Za-z0-9._/-]{1,200}")
#: A hermetic environment for git: no config but what we pass, never a prompt.
_GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "/bin/false", "SSH_ASKPASS": "/bin/false", "PATH": "/usr/bin:/bin",
            "LC_ALL": "C"}


class SourceError(Exception):
    """A reason the channel couldn't be read, for people."""

    def __init__(self, message: str, rate_limited: bool = False) -> None:
        super().__init__(message)
        self.rate_limited = rate_limited


def parse_ls_remote(text: str) -> dict[str, str]:
    """`git ls-remote` output -> {ref: commit}, annotated tags peeled to their commit.
    Lines that aren't a 40-hex commit and a plain ref are dropped."""
    refs: dict[str, str] = {}
    peeled: dict[str, str] = {}
    for line in text.splitlines():
        sha, _, ref = line.partition("\t")
        if not COMMIT.fullmatch(sha):
            continue
        if ref.endswith("^{}"):
            if _REF.fullmatch(ref[:-3]):
                peeled[ref[:-3]] = sha
        elif _REF.fullmatch(ref):
            refs[ref] = sha
    refs.update({r: c for r, c in peeled.items() if r in refs})
    return refs


def ci_state(runs: list[dict]) -> str:
    """One commit's CI from its workflow runs: "success" if any run passed, "pending" if any
    is still going, "failure" if they all finished otherwise, "" if there is none."""
    mine = [r for r in runs if r.get("name") == CI_WORKFLOW]
    if any(r.get("conclusion") == "success" for r in mine):
        return "success"
    if any(r.get("status") != "completed" for r in mine):
        return "pending"
    return "failure" if mine else ""


def _default_urlopen(request: urllib.request.Request, timeout: float):
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 — fixed https URLs only


class GitHubReleaseSource:
    def __init__(self, run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                 urlopen: Callable = _default_urlopen) -> None:
        self._run = run
        self._urlopen = urlopen

    # -- the seam ------------------------------------------------------------------------

    def read(self, channel: str, running_commit: str | None) -> ReleaseReading:
        try:
            refs = self._refs()
            target, newest, note = choose_target(channel, refs, self._ci, self._last_green)
            if target is None:
                return ReleaseReading(ok=True, message=f"nothing to offer: {note}", newest=newest)
            relation = self._relation(running_commit, target.commit) if running_commit else ""
            return ReleaseReading(ok=True, message=note, target=target, newest=newest, relation=relation)
        except SourceError as exc:
            return ReleaseReading(ok=False, message=str(exc), rate_limited=exc.rate_limited)
        except Exception as exc:  # noqa: BLE001 — the seam must not raise
            log.exception("update check failed")
            return ReleaseReading(ok=False, message=f"check failed: {type(exc).__name__}")

    # -- GitHub --------------------------------------------------------------------------

    def _refs(self) -> dict[str, str]:
        argv = ["git", "-c", "credential.helper=", "-c", "protocol.allow=never",
                "-c", "protocol.https.allow=always", "ls-remote", "--heads", "--tags", GIT_URL]
        try:
            done = self._run(argv, capture_output=True, text=True, errors="replace",
                             timeout=LS_REMOTE_TIMEOUT_S, env=_GIT_ENV)
        except subprocess.TimeoutExpired:
            raise SourceError(f"GitHub didn't answer within {LS_REMOTE_TIMEOUT_S}s (git ls-remote)") from None
        except OSError as exc:
            raise SourceError(f"can't run git: {exc.strerror}") from None
        if done.returncode != 0:
            first = (done.stderr or "").strip().splitlines()[:1]
            raise SourceError(f"git ls-remote failed (exit {done.returncode})"
                              + (f": {first[0][:200]}" if first else ""))
        refs = parse_ls_remote(done.stdout)
        if not refs:
            raise SourceError("GitHub listed no branches or tags")
        return refs

    def _ci(self, commit: str) -> str:
        data = self._api(f"/repos/{REPO}/actions/runs?head_sha={commit}&per_page=20")
        return ci_state(list(data.get("workflow_runs") or []))

    def _last_green(self, branch: str) -> str | None:
        data = self._api(f"/repos/{REPO}/actions/runs?branch={branch}&status=success&per_page=10")
        for run in data.get("workflow_runs") or []:
            sha = run.get("head_sha", "")
            if run.get("name") == CI_WORKFLOW and COMMIT.fullmatch(sha):
                return sha
        return None

    def _relation(self, base: str, head: str) -> str:
        if base == head:
            return "identical"
        if not COMMIT.fullmatch(base):
            return "unknown"
        data = self._api(f"/repos/{REPO}/compare/{base}...{head}?per_page=1", missing_ok=True)
        status = data.get("status") if data else None
        return status if status in ("ahead", "behind", "identical", "diverged") else "unknown"

    def _api(self, path: str, missing_ok: bool = False) -> dict:
        request = urllib.request.Request(API + path, headers={
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"commissioning-node {MODULE}/{VERSION}"})
        try:
            with self._urlopen(request, timeout=HTTP_TIMEOUT_S) as resp:
                body = resp.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and missing_ok:
                return {}
            if exc.code in (403, 429) and exc.headers.get("x-ratelimit-remaining") == "0":
                raise SourceError("GitHub's API limit for this network is used up (60 an hour); "
                                  "the next scheduled check tries again", rate_limited=True) from None
            raise SourceError(f"GitHub's API answered {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise SourceError(f"can't reach GitHub's API ({reason})") from None
        if len(body) > MAX_RESPONSE_BYTES:
            raise SourceError("GitHub's API answer was too large to read")
        try:
            data = json.loads(body)
        except ValueError:
            raise SourceError("GitHub's API answered something that isn't JSON") from None
        if not isinstance(data, dict):
            raise SourceError("GitHub's API answered an unexpected shape")
        return data
