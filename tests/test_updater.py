"""The root updater (ADR-0009 D3, D4): ops/update.sh and ops/update_helper.py, executed.

Ops tier, never imported: the scripts run here as the installer renders them, in a sandbox:
- a local git repo stands in for GitHub; each commit carries a fake `ops/install.sh`, a fake
  `ops/doctor.sh` and a fake GitHub adapter (`modules/node_updater/core/github_source.py`),
  which the updater runs *from the commit it is verifying*, as it runs the real ones;
- `systemctl` and `curl` are shims on PATH that log;
- the request, result and running record live in tmp_path.

No root, no network. What the sandbox can't show (systemd starting the unit, real installs)
is the rehearsal's (ADR-0009 step 4).
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parent.parent / "ops"
HELPER = OPS / "update_helper.py"

FAKE_INSTALL = """#!/usr/bin/env bash
set -u
here="$(cd "$(dirname "$0")/.." && pwd)"
commit="$(git -C "$here" rev-parse HEAD)"
echo "$commit ${CN_INSTALL_REF:-} ${CN_INSTALLED_BY:-}" >> "$SANDBOX/installs.log"
python3 -c 'import json,sys; json.dump({"schema_version": "0.1.0", "mode": "release", "tree": sys.argv[1],
  "commit": sys.argv[2], "ref": sys.argv[3], "describe": "desc-" + sys.argv[2][:7], "clean": True}, open(sys.argv[4], "w"))' \\
  "$here" "$commit" "${CN_INSTALL_REF:-}" "$SANDBOX/running.json"
if [[ -e "$SANDBOX/install_fail_$commit" ]]; then echo "refusing: the host said no"; exit 1; fi
exit 0
"""
FAKE_DOCTOR = """#!/usr/bin/env bash
here="$(cd "$(dirname "$0")/.." && pwd)"
commit="$(git -C "$here" rev-parse HEAD)"
echo "  OK    something"
if [[ -e "$SANDBOX/doctor_fail_$commit" ]]; then echo "  FAIL  the dashboard is down"; exit 1; fi
exit 0
"""
FAKE_ADAPTER = """import json, os
class _V:
    def __init__(self, d): self.commit, self.ref = d["commit"], d["ref"]
class _R:
    def __init__(self, d):
        self.ok, self.message, self.relation = d.get("ok", True), d.get("message", ""), d.get("relation", "ahead")
        self.target = _V(d) if d.get("commit") else None
class GitHubReleaseSource:
    def read(self, channel, running):
        d = json.load(open(os.path.join(os.environ["SANDBOX"], "target.json")))
        with open(os.path.join(os.environ["SANDBOX"], "resolved.log"), "a") as f:
            f.write(f"{channel} {running}\\n")
        return _R(d)
"""


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


class Sandbox:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.repo = root / "github"
        self.tree = root / "release"
        self.state = root / "state"
        self.results = root / "results"
        self.bin = root / "bin"
        for d in (self.repo, self.state / "updater", self.results, self.bin):
            d.mkdir(parents=True)
        _git(self.repo, "init", "-q", "-b", "dev")
        for name, body in (("systemctl", 'echo "systemctl $*" >> "$SANDBOX/systemctl.log"'),
                           ("curl", "exit 0")):
            shim = self.bin / name
            shim.write_text(f"#!/usr/bin/env bash\n{body}\n")
            shim.chmod(0o755)
        rendered = (OPS / "update.sh").read_text()
        for key, value in {"@UPDATE_TREE@": self.tree, "@UPDATE_REPO_URL@": self.repo,
                           "@CN_STATE@": self.state, "@UPDATE_RESULT_DIR@": self.results,
                           "@UPDATE_PY@": HELPER, "@RUNNING_FILE@": root / "running.json"}.items():
            rendered = rendered.replace(key, str(value))
        self.script = root / "update"
        self.script.write_text(rendered)
        self.script.chmod(0o755)

    def commit(self, label: str, adapter: bool = True) -> str:
        (self.repo / "ops").mkdir(exist_ok=True)
        for name, body in (("install.sh", FAKE_INSTALL), ("doctor.sh", FAKE_DOCTOR)):
            path = self.repo / "ops" / name
            path.write_text(body)
            path.chmod(0o755)
        core = self.repo / "modules" / "node_updater" / "core"
        if adapter:
            core.mkdir(parents=True, exist_ok=True)
            for d in (self.repo / "modules", core.parent, core):
                (d / "__init__.py").write_text("")
            (core / "github_source.py").write_text(FAKE_ADAPTER)
        elif core.exists():
            (core / "github_source.py").unlink()
        (self.repo / "VERSION").write_text(label)
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", label)
        return _git(self.repo, "rev-parse", "HEAD")

    def running(self, commit: str, mode: str = "release", ref: str = "v0.1.0") -> None:
        (self.root / "running.json").write_text(json.dumps(
            {"schema_version": "0.1.0", "mode": mode, "commit": commit, "ref": ref, "describe": f"desc-{commit[:7]}"}))

    def target(self, commit: str | None, ref: str = "v0.2.0", relation: str = "ahead", ok: bool = True) -> None:
        (self.root / "target.json").write_text(json.dumps({"commit": commit, "ref": ref, "relation": relation, "ok": ok}))

    def request(self, expected: str, age: float = 0, **kw) -> None:
        req = {"schema_version": "0.1.0", "request_id": "0123456789ab", "channel": "releases",
               "expected_commit": expected, "requested_by": "alice@example.com", "door": "tailnet",
               "requested_at": time.time() - age, **kw}
        (self.state / "updater" / "request.json").write_text(json.dumps(req))

    def run(self, *args: str) -> subprocess.CompletedProcess:
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "SANDBOX": str(self.root), "HOME": str(self.root)}
        return subprocess.run([str(self.script), *args], capture_output=True, text=True, env=env, timeout=120)

    def result(self) -> dict:
        return json.loads((self.results / "result.json").read_text())

    def log(self, name: str) -> list[str]:
        path = self.root / name
        return path.read_text().splitlines() if path.exists() else []


@pytest.fixture
def box(tmp_path) -> Sandbox:
    return Sandbox(tmp_path)


def test_an_update_installs_restarts_and_checks(box) -> None:
    old = box.commit("v0.1.0")
    new = box.commit("v0.2.0")
    box.running(old)
    box.target(new)
    box.request(new)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    r = box.result()
    assert r["done"] is True and r["ok"] is True and r["to_ref"] == "v0.2.0" and r["to_commit"] == new
    assert r["from_commit"] == old and r["requested_by"] == "alice@example.com" and r["door"] == "tailnet"
    assert box.log("installs.log") == [f"{new} v0.2.0 update by alice@example.com (tailnet)"]
    assert box.log("systemctl.log") == ["systemctl restart commissioning-node.service"]
    assert box.log("resolved.log") == [f"releases {old}"]
    assert not (box.state / "updater" / "request.json").exists()        # used once
    assert _git(box.tree, "rev-parse", "HEAD") == new
    assert stat.S_IMODE(box.tree.stat().st_mode) == 0o755


def test_no_request_does_nothing(box) -> None:
    box.commit("v0.1.0")
    assert box.run().returncode == 0
    assert not (box.results / "result.json").exists() and box.log("installs.log") == []


@pytest.mark.parametrize("target, said", [
    ("other", "the channel moved since you looked"),
    ("behind", "is older than what runs"),
    ("identical", "is what runs already"),
    ("nothing", "the channel offers nothing now"),
    ("down", "couldn't check the channel"),
])
def test_root_refuses_unless_it_is_still_the_newer_target(box, target, said) -> None:
    old = box.commit("v0.1.0")
    new = box.commit("v0.2.0")
    other = box.commit("v0.3.0")
    box.running(old)
    if target == "other":
        box.target(other, ref="v0.3.0")
    elif target in ("behind", "identical"):
        box.target(new, relation=target)
    elif target == "nothing":
        box.target(None)
    else:
        box.target(new, ok=False)
    box.request(new)
    box.run()
    r = box.result()
    assert r["done"] is True and r["ok"] is False and said in r["message"]
    assert box.log("installs.log") == [] and box.log("systemctl.log") == []


def test_a_commit_github_does_not_have_is_refused(box) -> None:
    old = box.commit("v0.1.0")
    box.running(old)
    box.request("f" * 40)
    box.run()
    assert "GitHub doesn't have ffffffffffff" in box.result()["message"]


def test_a_version_older_than_the_updater_is_never_installed(box) -> None:
    old = box.commit("v0.1.0")
    legacy = box.commit("legacy", adapter=False)
    box.running(old)
    box.target(legacy)
    box.request(legacy)
    box.run()
    assert "predates the updater" in box.result()["message"] and box.log("installs.log") == []


def test_a_failed_update_rolls_back_to_the_last_release(box) -> None:
    old = box.commit("v0.1.0")
    new = box.commit("v0.2.0")
    box.running(old)
    box.target(new)
    (box.root / f"doctor_fail_{new}").touch()
    box.request(new)
    box.run()
    r = box.result()
    assert r["done"] is True and r["ok"] is False and r["rolled_back"] is True
    assert "rolled back to desc-" in r["message"] and "the dashboard is down" in r["message"]
    installs = box.log("installs.log")
    assert [line.split()[0] for line in installs] == [new, old]
    assert installs[1].startswith(f"{old} v0.1.0 rollback after a failed update by alice@example.com")
    assert _git(box.tree, "rev-parse", "HEAD") == old


def test_an_install_refusal_counts_as_a_failure(box) -> None:
    old = box.commit("v0.1.0")
    new = box.commit("v0.2.0")
    box.running(old)
    box.target(new)
    (box.root / f"install_fail_{new}").touch()
    box.request(new)
    box.run()
    r = box.result()
    assert r["rolled_back"] is True and "install.sh failed: the host said no" in r["message"]


def test_a_failed_rollback_says_so_and_stops(box) -> None:
    old = box.commit("v0.1.0")
    new = box.commit("v0.2.0")
    box.running(old)
    box.target(new)
    (box.root / f"doctor_fail_{new}").touch()
    (box.root / f"doctor_fail_{old}").touch()
    box.request(new)
    box.run()
    r = box.result()
    assert r["ok"] is False and "so did the rollback" in r["message"] and len(box.log("installs.log")) == 2


def test_a_failed_switch_from_a_dev_tree_never_runs_the_dev_tree(box) -> None:
    dev = box.commit("dev work")
    new = box.commit("v0.2.0")
    box.running(dev, mode="dev-tree", ref="local")
    box.target(new, relation="unknown")
    (box.root / f"doctor_fail_{new}").touch()
    box.request(new)
    box.run()
    r = box.result()
    assert r["ok"] is False and r.get("rolled_back") is not True
    assert "no automatic way back to a developer tree" in r["message"]
    assert [line.split()[0] for line in box.log("installs.log")] == [new]


@pytest.mark.parametrize("spoil, said", [
    ("symlink", "not a plain file"),
    ("fifo", "not a regular file"),
    ("dirlink", "not a plain directory"),
    ("big", "too large"),
    ("old", "s old; confirm the update again"),
    ("commit", "expected_commit is not valid"),
    ("channel", "channel is not valid"),
    ("door", "door is not valid"),
    ("json", "not JSON"),
])
def test_odd_requests_are_refused_and_deleted(box, tmp_path, spoil, said) -> None:
    new = box.commit("v0.2.0")
    req = box.state / "updater" / "request.json"
    if spoil == "symlink":
        secret = tmp_path / "secret"
        secret.write_text("root's")
        req.symlink_to(secret)
    elif spoil == "fifo":
        os.mkfifo(req)
    elif spoil == "dirlink":
        (box.state / "updater").rmdir()
        (tmp_path / "elsewhere").mkdir()
        (tmp_path / "elsewhere" / "request.json").write_text("{}")
        (box.state / "updater").symlink_to(tmp_path / "elsewhere")
    elif spoil == "big":
        req.write_text(" " * 5000)
    elif spoil == "old":
        box.request(new, age=3600)
    elif spoil == "json":
        req.write_text("not json")
    else:
        box.request(new, **({"expected_commit": "abc"} if spoil == "commit" else
                            {"channel": "nightly"} if spoil == "channel" else {"door": "lan"}))
    box.run()
    r = box.result()
    assert r["done"] is True and r["ok"] is False and said in r["message"], r["message"]
    assert box.log("installs.log") == []
    if spoil == "symlink":
        assert (tmp_path / "secret").read_text() == "root's" and not req.is_symlink()
    if spoil == "dirlink":
        assert (tmp_path / "elsewhere" / "request.json").exists()      # never followed
    if spoil not in ("dirlink",):
        assert not os.path.lexists(req)


def test_after_marks_a_stopped_run_and_leaves_a_finished_one(box) -> None:
    box.commit("v0.1.0")
    subprocess.run([sys.executable, "-I", str(HELPER), "result", "--dir", str(box.results), "--reset",
                    "request_id=0123456789ab", "done=false", "phase=installing"], check=True)
    env = {"PATH": f"{box.bin}:/usr/bin:/bin", "SANDBOX": str(box.root), "SERVICE_RESULT": "timeout"}
    subprocess.run([str(box.script), "--after"], env=env, check=True)
    r = box.result()
    assert r["done"] is True and r["ok"] is False and "stopped before it finished (timeout)" in r["message"]
    before = (box.results / "result.json").read_text()
    subprocess.run([str(box.script), "--after"], env=env, check=True)
    assert (box.results / "result.json").read_text() == before


def test_the_result_is_written_whole_and_world_readable(box) -> None:
    subprocess.run([sys.executable, "-I", str(HELPER), "result", "--dir", str(box.results), "--reset",
                    "done=true", "message=a b", "finished_at=12.5"], check=True)
    path = box.results / "result.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert json.loads(path.read_text()) == {"done": True, "message": "a b", "finished_at": 12.5,
                                            "schema_version": "0.1.0"}
    assert [p.name for p in box.results.iterdir()] == ["result.json"]      # no temp left


def test_roots_reader_accepts_exactly_what_the_daemon_writes(tmp_path) -> None:
    """The hand-off itself: the daemon's RequestFile, read back by root's helper."""
    from modules.node_updater.core.update_files import RequestFile
    from modules.node_updater.interface import UpdateRequest
    req = UpdateRequest(request_id="0123456789ab", channel="dev", expected_commit="b" * 40,
                        requested_by="alice@example.com", door="fallback", requested_at=time.time())
    assert RequestFile(str(tmp_path)).submit(req) is None
    out = subprocess.run([sys.executable, "-I", str(HELPER), "read-request", "--state", str(tmp_path)],
                         capture_output=True, text=True, check=True).stdout.splitlines()
    assert out == ["0123456789ab", "dev", "b" * 40, "alice@example.com", "fallback"]
    assert not (tmp_path / "updater" / "request.json").exists()
