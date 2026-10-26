"""node-updater (ADR-0009 step 2): the GitHub adapter, the one target rule, the running record,
the check schedule over the real bus, and what the overview shows.

The ls-remote sample and the API shapes were measured against this repo on 2026-10-25 (quirks
Q-011). Nothing here reaches the network: the adapter runs on a fake `run` and `urlopen`, and
compose() without a release source never checks.
"""
from __future__ import annotations

import io
import json
import subprocess
import urllib.error
from email.message import Message

import pytest

import interfaces
import main
from bus import Bus
from modules.dashboard.features.overview_page import OverviewPage
from modules.node_updater.core import github_source as gh
from modules.node_updater.core.github_source import GitHubReleaseSource, ci_state, parse_ls_remote
from modules.node_updater.core.running_record import RunningFile
from modules.node_updater.features import update_check as uc
from modules.node_updater.interface import (MAX_RELEASES_WALKED, ReleaseReading, RootResult,
                                            RunningRecord, choose_target)

A, B, C, D = ("a" * 40, "b" * 40, "c" * 40, "d" * 40)

# Captured 2026-10-25 (quirks Q-011): annotated tags, peeled under ^{}.
LS_REMOTE = """ee0e13df395fa5eb55e8fe8386bb9de1477eaab8\trefs/heads/dev
472fedb37cfb004965bee0b5978657faf7a3ceed\trefs/heads/master
e9573609d3be57a6161ab354f47b86fe85bba0e8\trefs/tags/v0.1.0
438a23bb0330279a73235355010bf2752ab5cf4f\trefs/tags/v0.1.0^{}
21af6c7cd6987eab8cc82d0fdd9166a544b5464d\trefs/tags/v0.2.0
472fedb37cfb004965bee0b5978657faf7a3ceed\trefs/tags/v0.2.0^{}
"""


# -- parsing ----------------------------------------------------------------------------

def test_ls_remote_peels_annotated_tags_and_drops_junk() -> None:
    refs = parse_ls_remote(LS_REMOTE + "nothex\trefs/heads/x\n" + f"{A}\trefs/heads/bad name\n")
    assert refs == {"refs/heads/dev": "ee0e13df395fa5eb55e8fe8386bb9de1477eaab8",
                    "refs/heads/master": "472fedb37cfb004965bee0b5978657faf7a3ceed",
                    "refs/tags/v0.1.0": "438a23bb0330279a73235355010bf2752ab5cf4f",
                    "refs/tags/v0.2.0": "472fedb37cfb004965bee0b5978657faf7a3ceed"}


@pytest.mark.parametrize("runs, state", [
    ([], ""),
    ([{"name": "ci", "status": "completed", "conclusion": "success"}], "success"),
    ([{"name": "ci", "status": "completed", "conclusion": "failure"},
      {"name": "ci", "status": "completed", "conclusion": "success"}], "success"),   # a re-run passed
    ([{"name": "ci", "status": "in_progress", "conclusion": None}], "pending"),
    ([{"name": "ci", "status": "completed", "conclusion": "cancelled"}], "failure"),
    ([{"name": "other", "status": "completed", "conclusion": "success"}], ""),       # not our CI
])
def test_ci_state(runs, state) -> None:
    assert ci_state(runs) == state


# -- the one rule -----------------------------------------------------------------------

def refs(**kw) -> dict[str, str]:
    return {f"refs/{k.replace('__', '/').replace('_', '.')}": v for k, v in kw.items()}


def test_releases_offer_the_newest_tag_whose_ci_passed() -> None:
    r = {"refs/tags/v0.9.0": A, "refs/tags/v0.10.0": B, "refs/tags/v1.0.0-rc1": C, "refs/tags/latest": D}
    target, newest, note = choose_target("releases", r, {A: "success", B: "failure"}.get, lambda b: None)
    assert (target.ref, target.commit, target.ci) == ("v0.9.0", A, "success")
    assert (newest.ref, newest.ci) == ("v0.10.0", "failure")            # 0.10 > 0.9, not string order
    assert note == "v0.10.0's CI failed; offering v0.9.0"


def test_releases_walk_only_so_far_and_offer_nothing_if_none_passed() -> None:
    r = {f"refs/tags/v0.{i}.0": c for i, c in enumerate((A, B, C, D))}
    asked = []
    target, newest, note = choose_target("releases", r, lambda c: asked.append(c) or "pending", lambda b: None)
    assert target is None and newest.ref == "v0.3.0" and len(asked) == MAX_RELEASES_WALKED
    assert "no release's CI passed" in note and "hasn't finished" in note


def test_dev_offers_the_tip_or_the_last_green_commit() -> None:
    r = {"refs/heads/dev": A}
    assert choose_target("dev", r, {A: "success"}.get, lambda b: None)[:2] == (
        interfaces.NodeVersion(commit=A, ref="dev", ci="success"), None)
    target, newest, note = choose_target("dev", r, {A: "failure"}.get, lambda b: B)
    assert target.commit == B and newest.commit == A and "offering the last that passed" in note
    target, newest, _ = choose_target("dev", r, {A: "failure"}.get, lambda b: None)
    assert target is None and newest.commit == A


@pytest.mark.parametrize("channel, r, said", [
    ("releases", {"refs/heads/dev": A}, "no release tag"),
    ("dev", {"refs/tags/v1.0.0": A}, "no dev branch"),
    ("nightly", {"refs/heads/dev": A}, "unknown update channel"),
])
def test_nothing_to_offer_says_why(channel, r, said) -> None:
    target, _, note = choose_target(channel, r, lambda c: "success", lambda b: A)
    assert target is None and said in note


# -- the adapter --------------------------------------------------------------------------

class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeGitHub:
    """`run` and `urlopen` for the adapter: answers by URL, records every call."""

    def __init__(self, ls=LS_REMOTE, api=None, rc=0) -> None:
        self.ls, self.api, self.rc = ls, api or {}, rc
        self.argv, self.env, self.urls = None, None, []

    def run(self, argv, **kw):
        self.argv, self.env = argv, kw.get("env")
        return subprocess.CompletedProcess(argv, self.rc, stdout=self.ls, stderr="fatal: boom\n")

    def urlopen(self, request, timeout):
        url = request.full_url
        self.urls.append(url)
        for part, answer in self.api.items():
            if part in url:
                if isinstance(answer, Exception):
                    raise answer
                return Resp(answer if isinstance(answer, bytes) else json.dumps(answer).encode())
        raise urllib.error.HTTPError(url, 404, "Not Found", Message(), None)


def source(fake: FakeGitHub) -> GitHubReleaseSource:
    return GitHubReleaseSource(run=fake.run, urlopen=fake.urlopen)


def runs(conclusion: str) -> dict:
    return {"workflow_runs": [{"name": "ci", "status": "completed", "conclusion": conclusion, "head_sha": A}]}


def test_git_runs_hermetic_over_https_only() -> None:
    fake = FakeGitHub(api={"head_sha=ee0e13d": runs("success"), "compare/": {"status": "ahead"}})
    reading = source(fake).read("dev", B)
    assert fake.argv == ["git", "-c", "credential.helper=", "-c", "protocol.allow=never",
                         "-c", "protocol.https.allow=always", "ls-remote", "--heads", "--tags",
                         "https://github.com/ausbemer/commissioning-node.git"]
    # The daemon user's own git config (a URL rewrite to SSH and a deploy key) must not apply.
    assert fake.env["GIT_CONFIG_GLOBAL"] == "/dev/null" and fake.env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert fake.env["GIT_TERMINAL_PROMPT"] == "0" and "HOME" not in fake.env
    assert reading.ok and reading.target.commit.startswith("ee0e13d") and reading.relation == "ahead"
    assert all(u.startswith("https://api.github.com/repos/ausbemer/commissioning-node/") for u in fake.urls)


def test_the_live_shape_of_2026_10_25() -> None:
    """releases: v0.2.0's commit failed CI, so v0.1.0 is offered, and it's behind what runs."""
    fake = FakeGitHub(api={"head_sha=472fedb": runs("failure"), "head_sha=438a23b": runs("success"),
                           "compare/": {"status": "behind"}})
    reading = source(fake).read("releases", "ee0e13df395fa5eb55e8fe8386bb9de1477eaab8")
    assert reading.ok and reading.target.ref == "v0.1.0" and reading.newest.ref == "v0.2.0"
    assert reading.relation == "behind" and reading.message == "v0.2.0's CI failed; offering v0.1.0"


def test_a_commit_github_does_not_have_relates_as_unknown() -> None:
    fake = FakeGitHub(api={"head_sha=": runs("success")})          # compare: 404
    assert source(fake).read("dev", B).relation == "unknown"
    assert source(FakeGitHub(api={"head_sha=": runs("success")})).read(
        "dev", "ee0e13df395fa5eb55e8fe8386bb9de1477eaab8").relation == "identical"


def limited() -> urllib.error.HTTPError:
    headers = Message()
    headers["x-ratelimit-remaining"] = "0"
    return urllib.error.HTTPError("u", 403, "rate limited", headers, None)


@pytest.mark.parametrize("fake, said", [
    (FakeGitHub(rc=128), "git ls-remote failed (exit 128): fatal: boom"),
    (FakeGitHub(ls=""), "GitHub listed no branches or tags"),
    (FakeGitHub(api={"head_sha=": limited()}), "API limit for this network is used up"),
    (FakeGitHub(api={"head_sha=": urllib.error.HTTPError("u", 500, "x", Message(), None)}), "answered 500"),
    (FakeGitHub(api={"head_sha=": urllib.error.URLError("no route")}), "can't reach GitHub's API (no route)"),
    (FakeGitHub(api={"head_sha=": b"x" * (gh.MAX_RESPONSE_BYTES + 1)}), "too large"),
    (FakeGitHub(api={"head_sha=": b"<html>"}), "isn't JSON"),
])
def test_failures_are_readings_not_exceptions(fake, said) -> None:
    reading = source(fake).read("dev", B)
    assert not reading.ok and reading.target is None and said in reading.message
    assert reading.rate_limited == ("API limit" in said)


def test_a_git_timeout_is_a_reading() -> None:
    def slow(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 20)
    reading = GitHubReleaseSource(run=slow, urlopen=FakeGitHub().urlopen).read("dev", None)
    assert not reading.ok and "didn't answer within" in reading.message


# -- the running record -------------------------------------------------------------------

@pytest.mark.parametrize("content, ok", [
    (None, False),
    ({"schema_version": "0.1.0", "commit": A, "ref": "local", "mode": "dev-tree", "clean": False}, True),
    ({"schema_version": "0.1.0", "commit": "abc"}, False),
    ({"schema_version": "1.0.0", "commit": A}, False),
    ({"commit": A}, False),
    ("x" * 20000, False),
])
def test_running_record_reads_only_a_well_formed_record(tmp_path, content, ok) -> None:
    path = tmp_path / "running.json"
    if content is not None:
        path.write_text(content if isinstance(content, str) else json.dumps(content))
    record = RunningFile(str(path)).read()
    assert (record is not None) == ok
    if ok:
        assert record.commit == A and record.mode == "dev-tree" and record.clean is False


def test_running_path_matches_ops() -> None:
    from modules.node_updater.core.running_record import RUNNING_PATH
    from tests.test_ops_paths import OPS
    paths = (OPS / "paths.sh").read_text()
    assert 'CN_ETC=/etc/commissioning-node' in paths and 'RUNNING_FILE="$CN_ETC/running.json"' in paths
    assert RUNNING_PATH == "/etc/commissioning-node/running.json"


# -- the schedule, over the real bus ------------------------------------------------------

class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeSource:
    def __init__(self, reading: ReleaseReading | None = None) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.reading = reading or ReleaseReading(
            ok=True, message="", relation="ahead",
            target=interfaces.NodeVersion(commit=B, ref="v0.3.0", ci="success"))

    def read(self, channel, running_commit):
        self.calls.append((channel, running_commit))
        return self.reading


class FakeRunning:
    def __init__(self, record=RunningRecord(commit=A, ref="local", describe="v0.2.0-7-gaaaaaaa",
                                            mode="dev-tree", clean=True)) -> None:
        self.record = record

    def read(self):
        return self.record


class FakeConfig:
    def __init__(self, channel="releases") -> None:
        self.channel = channel

    def get(self, name):
        assert name == "update_channel"
        return self.channel


def make(src=None, running=None, config=None):
    clock = Clock()
    src = src or FakeSource()
    node = main.compose(Bus(), clock=clock, dashboard_port=0, release_source=src,
                        running_source=running or FakeRunning(), update_spawn=lambda work: work())
    if config is not None:
        for r in node.registries:
            if r.module == "node-updater":
                r.enabled()[0]._config = config
    seen: list[dict] = []
    node.bus.subscribe(interfaces.NODE_UPDATE_STATUS, lambda t, p: seen.append(p))
    return node, clock, src, seen


def last(seen) -> interfaces.NodeUpdateStatus:
    return interfaces.NodeUpdateStatus.model_validate(seen[-1])


def test_without_a_source_nothing_checks_and_check_now_is_refused() -> None:
    node = main.compose(Bus(), dashboard_port=0)
    assert "node-updater:update-check" not in node.capabilities()
    cmd = interfaces.NodeUpdateCheck.stamped(interfaces.__version__, request_id="r", requested_by="a", door="local")
    ok, msg = main.invoke(node, interfaces.NODE_UPDATE_CHECK, cmd.model_dump(), "node-updater:update-check")
    assert not ok and "installed but disabled" in msg


def test_first_tick_checks_and_the_next_tick_publishes_it() -> None:
    node, _, src, seen = make()
    main.tick(node)
    assert src.calls == [("releases", A)]
    assert last(seen).message == "not checked yet"            # finished off the bus; not drained
    main.tick(node)
    st = last(seen)
    assert st.ok and st.update_available and st.relation == "ahead"
    assert st.available.ref == "v0.3.0" and st.running.commit == A and st.running_mode == "dev-tree"
    assert st.message == "an update is available" and st.origin == "node-updater/update-check"
    assert st.period_s == main.TICK_PERIOD_S and st.check_period_s == uc.UPDATE_CHECK_S


@pytest.mark.parametrize("reading, offered", [
    (ReleaseReading(ok=False, message="can't reach GitHub's API"), False),
    (ReleaseReading(ok=True, message="", relation="behind",
                    target=interfaces.NodeVersion(commit=B, ref="v0.1.0")), False),
    (ReleaseReading(ok=True, message="", relation="unknown",
                    target=interfaces.NodeVersion(commit=B, ref="dev")), False),
    (ReleaseReading(ok=True, message="nothing to offer: no release tag (vX.Y.Z) on GitHub"), False),
])
def test_only_a_newer_commit_from_a_working_check_is_an_update(reading, offered) -> None:
    node, _, _, seen = make(FakeSource(reading))
    main.tick(node)
    main.tick(node)
    st = last(seen)
    assert st.update_available is offered and st.ok is reading.ok
    if not reading.ok:
        assert st.available is None and st.message == reading.message


def test_no_running_record_offers_nothing() -> None:
    node, _, _, seen = make(running=FakeRunning(None))
    main.tick(node)
    main.tick(node)
    st = last(seen)
    assert not st.update_available and st.running is None and "re-run sudo ops/install.sh" in st.message


def test_checks_run_on_the_schedule_and_retry_sooner_after_a_failure() -> None:
    node, clock, src, _ = make()
    main.tick(node)
    clock.now += uc.UPDATE_CHECK_S - 1
    main.tick(node)
    assert len(src.calls) == 1
    clock.now += 1
    main.tick(node)
    assert len(src.calls) == 2
    src.reading = ReleaseReading(ok=False, message="down")
    clock.now += uc.UPDATE_CHECK_S
    main.tick(node)
    main.tick(node)                                           # drains the failure
    clock.now += uc.UPDATE_RETRY_S
    main.tick(node)
    assert len(src.calls) == 4


def test_a_rate_limited_check_waits_the_full_period() -> None:
    node, clock, src, _ = make(FakeSource(ReleaseReading(ok=False, message="limit", rate_limited=True)))
    main.tick(node)
    main.tick(node)
    clock.now += uc.UPDATE_RETRY_S
    main.tick(node)
    assert len(src.calls) == 1
    clock.now += uc.UPDATE_CHECK_S
    main.tick(node)
    assert len(src.calls) == 2


def test_a_channel_change_checks_again_at_once() -> None:
    config = FakeConfig("releases")
    node, clock, src, seen = make(config=config)
    main.tick(node)
    main.tick(node)
    config.channel = "dev"
    main.tick(node)
    assert src.calls[-1] == ("dev", A)


def test_check_now_runs_once_per_gap() -> None:
    node, clock, src, seen = make()
    main.tick(node)
    cmd = interfaces.NodeUpdateCheck.stamped(interfaces.__version__, request_id="r1",
                                             requested_by="alice@example.com", door="tailnet")
    assert main.invoke(node, interfaces.NODE_UPDATE_CHECK, cmd.model_dump(), "node-updater:update-check")[0]
    assert len(src.calls) == 1                                # too soon after the first check
    main.tick(node)
    assert "checked moments ago" in last(seen).message
    clock.now += uc.CHECK_MIN_GAP_S
    main.invoke(node, interfaces.NODE_UPDATE_CHECK, cmd.model_dump(), "node-updater:update-check")
    assert len(src.calls) == 2
    main.tick(node)
    assert "checked moments ago" not in last(seen).message


def test_a_source_that_raises_still_frees_the_slot() -> None:
    class Boom:
        def read(self, channel, running_commit):
            raise RuntimeError("boom")
    node, clock, _, seen = make(Boom())
    main.tick(node)
    main.tick(node)
    st = last(seen)
    assert not st.ok and "internal error" in st.message and not st.checking


def test_the_dashboard_collects_the_status() -> None:
    node, _, _, _ = make()
    main.tick(node)
    main.tick(node)
    kept = node.dashboard._state.snapshot()["node_update"]["self"]
    assert kept["update_available"] and "received_at" in kept


# -- the overview ---------------------------------------------------------------------------

NOW = 1_800_000_000.0


def snap(status=None, writes=("/actions/node/update-check",)):
    s = {"nodes": {}, "tailnet": {"self": {"hostname": "cn", "connected": True, "addresses": ["100.64.0.7"]}},
         "cameras": {}, "streams": {}, "wire": {}, "now": NOW, "writes": list(writes),
         "viewer": {"label": "local", "door": "local", "csrf": "tok"}}
    if status is not None:
        s["node_update"] = {"self": {"received_at": NOW - 1, "period_s": 5.0, "channel": "releases",
                                     "checked_at": NOW - 180, "ok": True, **status}}
    return s


RUNNING = {"commit": A, "ref": "local", "describe": "v0.2.0-7-gaaaaaaa"}


def test_overview_shows_an_update_banner_only_when_one_is_available() -> None:
    page = OverviewPage().render(snap({"running": RUNNING, "running_mode": "dev-tree", "update_available": True,
                                       "available": {"commit": B, "ref": "v0.3.0"},
                                       "message": "an update is available"}))
    assert "Update available: v0.3.0 (bbbbbbb)" in page and "running v0.2.0-7-gaaaaaaa" in page
    assert "Updating from the dashboard is off on this node" in page
    quiet = OverviewPage().render(snap({"running": RUNNING, "update_available": False, "message": "up to date"}))
    assert "Update available" not in quiet and "releases channel · checked 3 min ago · up to date" in quiet


def test_software_fact_shows_dev_tree_changes_and_check_now() -> None:
    page = OverviewPage().render(snap({"running": RUNNING, "running_mode": "dev-tree", "running_clean": False,
                                       "message": "up to date"}))
    assert '<span class="chip none">dev tree</span>' in page and "local changes" in page
    assert 'action="/actions/node/update-check"' in page and 'name="csrf" value="tok"' in page
    checking = OverviewPage().render(snap({"running": RUNNING, "checking": True}))
    assert "checking now" in checking and 'action="/actions/node/update-check"' not in checking
    no_write = OverviewPage().render(snap({"running": RUNNING}, writes=()))
    assert 'action="/actions/node/update-check"' not in no_write


def test_a_lapsed_status_says_nothing() -> None:
    s = snap({"running": RUNNING, "update_available": True, "available": {"commit": B, "ref": "v0.3.0"}})
    s["node_update"]["self"]["received_at"] = NOW - 600
    page = OverviewPage().render(s)
    assert "Update available" not in page and "no recent update status" in page


def test_github_text_is_escaped() -> None:
    page = OverviewPage().render(snap({"running": RUNNING, "message": "<script>x</script>"}))
    assert "<script>x</script>" not in page and "&lt;script&gt;" in page


# -- the confirmed update (ADR-0009 step 3) ---------------------------------------------------

class FakeRequester:
    def __init__(self, problem=None) -> None:
        self.requests: list = []
        self.problem = problem
        self.waiting = False

    def submit(self, request):
        if self.problem:
            return self.problem
        self.requests.append(request)
        self.waiting = True
        return None

    def pending(self):
        return self.waiting


class FakeResults:
    def __init__(self) -> None:
        self.record = None

    def read(self):
        return self.record


def make_apply(reading=None, running=None):
    clock = Clock()
    requester, results = FakeRequester(), FakeResults()
    node = main.compose(Bus(), clock=clock, dashboard_port=0, release_source=FakeSource(reading),
                        running_source=running or FakeRunning(), update_spawn=lambda work: work(),
                        node_update=True, update_requester=requester, update_results=results)
    seen: list[dict] = []
    node.bus.subscribe(interfaces.NODE_UPDATE_RESULT, lambda t, p: seen.append(p))
    main.tick(node)
    main.tick(node)                     # the check is drained: the status offers v0.3.0 (B)
    return node, clock, requester, results, seen


def update(node, expected=B):
    cmd = interfaces.NodeUpdate.stamped(interfaces.__version__, request_id="r1", expected_commit=expected,
                                        requested_by="alice@example.com", door="tailnet")
    return main.invoke(node, interfaces.NODE_UPDATE, cmd.model_dump(), "node-updater:update-apply")


def test_the_update_is_off_unless_main_enables_it() -> None:
    node, _, _, _ = make()
    assert "node-updater:update-apply" not in node.capabilities()
    ok, msg = update(node)
    assert not ok and "installed but disabled" in msg


def test_a_confirmed_update_leaves_one_request_for_root() -> None:
    node, _, requester, _, seen = make_apply()
    ok, msg = update(node)
    assert ok, msg
    [req] = requester.requests
    assert (req.expected_commit, req.channel, req.requested_by, req.door) == (B, "releases", "alice@example.com", "tailnet")
    assert req.schema_version == "0.1.0" and req.request_id == "r1"
    r = interfaces.NodeUpdateResult.model_validate(seen[-1])
    assert not r.done and r.phase == "requested" and r.to_version.ref == "v0.3.0" and r.from_version.commit == A


@pytest.mark.parametrize("why, setup, said", [
    ("not a commit", lambda n, c, q, res: None, "not a commit"),
    ("moved", lambda n, c, q, res: None, "the offer changed since you looked"),
    ("stale", lambda n, c, q, res: setattr(c, "now", c.now + 3600), "no fresh update status"),
    ("pending", lambda n, c, q, res: setattr(q, "waiting", True), "already requested or running"),
    ("root busy", lambda n, c, q, res: setattr(res, "record", RootResult(request_id="x", done=False)),
     "already requested or running"),
])
def test_an_update_is_refused_with_its_reason(why, setup, said) -> None:
    node, clock, requester, results, seen = make_apply()
    setup(node, clock, requester, results)
    if why == "root busy":
        main.tick(node)
    expected = {"not a commit": "abc", "moved": C}.get(why, B)
    assert update(node, expected)[0]
    r = interfaces.NodeUpdateResult.model_validate(seen[-1])
    assert r.done and not r.ok and said in r.message and requester.requests == []


def test_an_older_offer_is_never_requested() -> None:
    older = ReleaseReading(ok=True, message="", relation="behind",
                           target=interfaces.NodeVersion(commit=B, ref="v0.1.0", ci="success"))
    node, _, requester, _, seen = make_apply(older)
    update(node)
    assert "is not newer than what runs (behind)" in seen[-1]["message"] and requester.requests == []


def test_a_switch_from_a_dev_tree_may_be_requested() -> None:
    switch = ReleaseReading(ok=True, message="", relation="unknown",
                            target=interfaces.NodeVersion(commit=B, ref="dev", ci="success"))
    node, _, requester, _, _ = make_apply(switch)
    assert update(node)[0] and len(requester.requests) == 1


def test_roots_result_is_published_when_it_changes() -> None:
    node, _, _, results, seen = make_apply()
    results.record = RootResult(request_id="r1", done=False, phase="installing", from_commit=A, to_commit=B,
                                to_ref="v0.3.0", requested_by="alice@example.com", door="tailnet", started_at=1.0)
    main.tick(node)
    main.tick(node)
    assert len(seen) == 1 and seen[-1]["phase"] == "installing" and seen[-1]["to_version"]["ref"] == "v0.3.0"
    results.record = RootResult(request_id="r1", done=True, ok=False, rolled_back=True, message="rolled back",
                                from_commit=A, to_commit=B, finished_at=2.0)
    main.tick(node)
    r = interfaces.NodeUpdateResult.model_validate(seen[-1])
    assert len(seen) == 2 and r.done and r.rolled_back and r.message == "rolled back"


def test_the_request_file_is_whole_private_and_never_overwritten(tmp_path) -> None:
    from modules.node_updater.core.update_files import RequestFile
    from modules.node_updater.interface import UpdateRequest
    files = RequestFile(str(tmp_path))
    req = UpdateRequest(request_id="0123456789ab", channel="dev", expected_commit=B,
                        requested_by="a", door="local", requested_at=1.0)
    assert not files.pending() and files.submit(req) is None and files.pending()
    path = tmp_path / "updater" / "request.json"
    assert oct(path.stat().st_mode & 0o777) == "0o600" and oct((tmp_path / "updater").stat().st_mode & 0o777) == "0o700"
    assert json.loads(path.read_text())["expected_commit"] == B
    assert "already requested" in files.submit(req.model_copy(update={"request_id": "ba9876543210"}))
    assert json.loads(path.read_text())["request_id"] == "0123456789ab"
    assert sorted(p.name for p in (tmp_path / "updater").iterdir()) == ["request.json"]


def test_the_result_file_reader_tolerates_junk(tmp_path) -> None:
    from modules.node_updater.core.update_files import ResultFile
    path = tmp_path / "result.json"
    assert ResultFile(str(path)).read() is None
    path.write_text("junk")
    assert ResultFile(str(path)).read() is None
    path.write_text(json.dumps({"schema_version": "0.1.0", "request_id": "r", "done": True, "ok": True}))
    assert ResultFile(str(path)).read().ok


def test_the_daemon_and_root_agree_on_paths_and_rules() -> None:
    import ast
    from modules.node_updater.core import update_files
    from modules.node_updater.interface import ACTIONABLE, REQUEST_SCHEMA
    from tests.test_ops_paths import OPS, shell_var
    assert shell_var("CN_STATE") == update_files.STATE_DIR
    assert shell_var("UPDATE_REQUEST") == '"$CN_STATE/updater/request.json"'
    assert f"{shell_var('UPDATE_RESULT_DIR')}/result.json" == update_files.RESULT_PATH
    assert shell_var("UPDATE_REPO_URL") == gh.GIT_URL
    helper = ast.parse((OPS / "update_helper.py").read_text())
    consts = {t.id: ast.literal_eval(n.value) for n in helper.body if isinstance(n, ast.Assign)
              for t in n.targets if isinstance(t, ast.Name) and t.id in ("ACTIONABLE", "REQUEST_DIR", "REQUEST_NAME")}
    assert consts == {"ACTIONABLE": ACTIONABLE, "REQUEST_DIR": update_files.REQUEST_DIR,
                      "REQUEST_NAME": update_files.REQUEST_NAME}
    assert REQUEST_SCHEMA.startswith("0.")


def test_overview_offers_the_slider_only_with_the_capability() -> None:
    caps = {"cn": {"hostname": "cn", "modules": [{"name": "node-updater", "capabilities": ["node-updater:update-apply"]}]}}
    status = {"running": RUNNING, "update_available": True, "relation": "ahead",
              "available": {"commit": B, "ref": "v0.3.0"}, "message": "an update is available"}
    s = snap(status)
    s["nodes"] = caps
    page = OverviewPage().render(s)
    assert 'action="/actions/node/update" data-slide data-marker="data-last-update">' in page
    assert f'name="expected" value="{B}"' in page and "Update to v0.3.0 (bbbbbbb)" in page
    assert "Rolls back if the doctor fails." in page
    switch = snap({**status, "update_available": False, "relation": "unknown"})
    assert "Update available" not in OverviewPage().render(switch)          # no capability: no noise
    switch["nodes"] = caps
    page = OverviewPage().render(switch)
    assert "The releases channel offers v0.3.0" in page and "Switch to v0.3.0" in page
    assert "your developer tree is left as it is" in page


def test_overview_shows_an_update_in_progress_and_the_last_outcome() -> None:
    s = snap({"running": RUNNING, "message": "up to date"})
    s["node_update_result"] = {"self": {"request_id": "r1", "done": False, "phase": "installing",
                                        "to_version": {"commit": B, "ref": "v0.3.0"},
                                        "requested_by": "alice@example.com", "started_at": NOW - 60}}
    page = OverviewPage().render(s)
    assert "Updating to v0.3.0 (bbbbbbb)" in page and ">installing<" in page
    assert 'data-last-update="r1:installing:False"' in page
    s["node_update_result"]["self"].update(done=True, ok=False, rolled_back=True, finished_at=NOW,
                                            message="rolled back to v0.2.0: the dashboard is down")
    page = OverviewPage().render(s)
    assert "Updating to" not in page and "Rolled back" in page and "the dashboard is down" in page
