"""tailnet-gateway's writer (ADR-0005): the CLI adapter, the decision rules, the one-at-a-time
worker, the auth key's lifetime, and the dashboard's Join/Rename/Leave through the real doors.

The adapter is tested with a fake `run`: the argv per operation is a golden (§8.12: what we
send to the world is pinned). Everything above it runs the real code with a fake writer.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import subprocess
from urllib.parse import urlencode

import pytest

import interfaces
import main
from bus import Bus
from modules.tailnet_gateway.core import tailscale_writer as tw
from modules.tailnet_gateway.core.tailscale_writer import TailscaleCliWriter, scrub
from modules.tailnet_gateway.interface import OverlaySnapshot, WriterReply

KEY = "tskey-auth-kAbC123-DEFghi456"


# -- the adapter ---------------------------------------------------------------------------

class Runner:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "", raises=None):
        self.calls: list[tuple[list[str], dict]] = []
        self.returncode, self.stdout, self.stderr, self.raises = returncode, stdout, stderr, raises

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)


@pytest.fixture
def keyfile(tmp_path):
    path = tmp_path / "tailnet-gateway.auth_key"
    path.write_text(KEY)
    path.chmod(0o600)
    return str(path)


def adapter(run: Runner) -> TailscaleCliWriter:
    return TailscaleCliWriter(run=run, which=lambda b: "/usr/bin/" + b, operator=lambda: "admin")


def test_join_argv_golden(keyfile) -> None:
    run = Runner()
    reply = adapter(run).join("site-a-gw", ["tag:commissioning", "tag:site-a"], keyfile)
    argv, kw = run.calls[0]
    assert argv == ["tailscale", "up", "--operator=admin", f"--auth-key=file:{keyfile}",
                    f"--timeout={tw.UP_TIMEOUT_S}s", "--hostname=site-a-gw",
                    "--advertise-tags=tag:commissioning,tag:site-a"]
    assert kw["timeout"] == tw.UP_TIMEOUT_S + tw.KILL_MARGIN_S and "shell" not in kw
    assert reply.ok and reply.message == "joined the tailnet as site-a-gw"
    assert KEY not in " ".join(argv)


def test_join_without_hostname_or_tags_leaves_them_out(keyfile) -> None:
    run = Runner()
    adapter(run).join(None, [], keyfile)
    assert run.calls[0][0] == ["tailscale", "up", "--operator=admin", f"--auth-key=file:{keyfile}",
                               f"--timeout={tw.UP_TIMEOUT_S}s"]


def test_rename_and_leave_argv_golden() -> None:
    run = Runner()
    assert adapter(run).rename("gw-2").ok
    assert adapter(run).leave().ok
    assert [c[0] for c in run.calls] == [["tailscale", "set", "--hostname=gw-2"], ["tailscale", "logout"]]


@pytest.mark.parametrize("hostname, tags, spoil, why", [
    ("Bad_Name", [], None, "DNS label"),
    ("-gw", [], None, "DNS label"),
    ("gw", ["eng"], None, "tag:name"),
    ("gw", [f"tag:t{i}" for i in range(17)], None, "too many tags"),
    ("gw", [], "missing", "no longer stored"),
    ("gw", [], "chmod", "not a private file"),
    ("gw", [], "symlink", "not a private file"),
])
def test_join_rechecks_its_inputs_and_never_runs(tmp_path, keyfile, hostname, tags, spoil, why) -> None:
    if spoil == "missing":
        os.unlink(keyfile)
    elif spoil == "chmod":
        os.chmod(keyfile, 0o644)
    elif spoil == "symlink":
        os.rename(keyfile, tmp_path / "real")
        os.symlink(tmp_path / "real", keyfile)
    run = Runner()
    reply = adapter(run).join(hostname, tags, keyfile)
    assert not reply.ok and why in reply.message and run.calls == []


def test_failure_output_is_scrubbed_and_capped(keyfile) -> None:
    run = Runner(returncode=1, stderr=f"backend error: invalid key: {KEY}\n" + "x" * 10_000)
    reply = adapter(run).join("gw", [], keyfile)
    assert not reply.ok and "exit 1" in reply.message
    assert KEY not in reply.message and len(reply.message) < tw.OUTPUT_CAP + 100
    assert scrub(f"a {KEY} b") == "a tskey-… b"


def test_timeout_and_missing_cli_are_in_band(keyfile) -> None:
    reply = adapter(Runner(raises=subprocess.TimeoutExpired("tailscale", 70))).join("gw", [], keyfile)
    assert not reply.ok and "timed out" in reply.message and "unknown" in reply.message
    gone = TailscaleCliWriter(run=Runner(), which=lambda b: None, operator=lambda: "admin")
    assert "not installed" in gone.leave().message


# -- the decision rules and the worker, through the real node ------------------------------

class FakeOverlay:
    def __init__(self, connected: bool = False, hostname: str = "Commissioning-node-pi5",
                 tags: list[str] | None = None) -> None:
        self.snapshot = OverlaySnapshot(hostname=hostname, connected=connected, tags=tags or [])

    def read(self) -> OverlaySnapshot:
        return self.snapshot


class FakeWriter:
    def __init__(self, ok: bool = True, raises: bool = False) -> None:
        self.calls: list[tuple] = []
        self.ok, self.raises = ok, raises

    def _reply(self, call: tuple) -> WriterReply:
        self.calls.append(call)
        if self.raises:
            raise RuntimeError("boom")
        return WriterReply(ok=self.ok, message=f"{call[0]} {'ok' if self.ok else 'failed: denied'}")

    def join(self, hostname, tags, key_path):
        return self._reply(("join", hostname, tuple(tags), key_path))

    def rename(self, hostname):
        return self._reply(("rename", hostname))

    def leave(self):
        return self._reply(("leave",))


class Deferred:
    """A spawn that holds the work until the test runs it: a join 'in progress'."""

    def __init__(self) -> None:
        self.pending: list = []

    def __call__(self, work) -> None:
        self.pending.append(work)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def node_with(tmp_path, overlay=None, writer=None, enabled=True, spawn=None, clock=None):
    node = main.compose(Bus(), overlay_source=overlay or FakeOverlay(), dashboard_port=0,
                        config_root=str(tmp_path), tailnet_writer=enabled,
                        overlay_writer=writer or FakeWriter(), clock=clock or Clock(),
                        writer_spawn=spawn or (lambda work: work()))
    results: list[dict] = []
    node.bus.subscribe(interfaces.TAILNET_APPLY_RESULT, lambda t, p: results.append(p))
    return node, results


def save(node, **values) -> None:
    surface = node.config.operator_surface()
    for name, text in values.items():
        assert surface.set(f"tailnet-gateway.{name}", text, "test").ok


def apply(node, operation: str, who: str = "alice@example.com"):
    cmd = interfaces.TailnetApply.stamped(interfaces.__version__, request_id=f"r-{operation}",
                                          operation=operation, requested_by=who, door="tailnet")
    return main.invoke(node, interfaces.TAILNET_APPLY, cmd.model_dump(),
                       requires="tailnet-gateway:tailnet-writer")


def test_disabled_by_default_and_refused_in_band(tmp_path) -> None:
    writer = FakeWriter()
    node, results = node_with(tmp_path, writer=writer, enabled=False)
    main.tick(node)
    ok, msg = apply(node, "leave")
    assert not ok and msg == ("tailnet-gateway:tailnet-writer not available — service "
                              "tailnet-gateway at v0.4.0 has it installed but disabled.")
    assert writer.calls == [] and results == []
    assert "tailnet-gateway:tailnet-writer" not in node.capabilities()
    assert main.compose(Bus(), dashboard_port=0).capabilities() == node.capabilities()


def test_join_applies_the_saved_values_then_deletes_the_key(tmp_path) -> None:
    writer = FakeWriter()
    node, results = node_with(tmp_path, writer=writer)
    save(node, hostname="site-a-gw", tags="tag:commissioning", auth_key=KEY)
    key_path = node.config.owner_view("tailnet-gateway").secret_path("auth_key")
    main.tick(node)
    assert apply(node, "join")[0] and results == []           # accepted: no result yet
    main.tick(node)                                           # drained on the next tick
    assert writer.calls == [("join", "site-a-gw", ("tag:commissioning",), key_path)]
    result = interfaces.TailnetApplyResult.model_validate(results[-1])
    assert result.ok and result.request_id == "r-join" and result.requested_by == "alice@example.com"
    assert "auth key was deleted" in result.message
    assert node.config.owner_view("tailnet-gateway").secret_path("auth_key") is None


def test_a_failed_join_keeps_the_key_and_is_not_retried(tmp_path) -> None:
    writer = FakeWriter(ok=False)
    node, results = node_with(tmp_path, writer=writer)
    save(node, auth_key=KEY)
    main.tick(node)
    apply(node, "join")
    for _ in range(3):
        main.tick(node)
    assert len(writer.calls) == 1 and not results[-1]["ok"]
    assert node.config.owner_view("tailnet-gateway").secret_path("auth_key") is not None


@pytest.mark.parametrize("operation, overlay, values, tick, why", [
    ("join", FakeOverlay(), {}, True, "no auth key is saved"),
    ("join", FakeOverlay(connected=True), {"auth_key": KEY}, True, "already on a tailnet"),
    ("join", FakeOverlay(), {"auth_key": KEY}, False, "not known yet"),
    ("rename", FakeOverlay(), {"hostname": "gw"}, True, "not on a tailnet"),
    ("rename", FakeOverlay(connected=True), {}, True, "no hostname is saved"),
    ("rename", FakeOverlay(connected=True, hostname="gw"), {"hostname": "gw"}, True, "already named gw"),
    ("reboot", FakeOverlay(connected=True), {}, True, "unknown operation"),
])
def test_refusals_are_results_and_nothing_runs(tmp_path, operation, overlay, values, tick, why) -> None:
    writer = FakeWriter()
    node, results = node_with(tmp_path, overlay=overlay, writer=writer)
    save(node, **values)
    if tick:
        main.tick(node)
    apply(node, operation)
    assert writer.calls == []
    assert not results[-1]["ok"] and why in results[-1]["message"]


def test_a_stale_reading_refuses_join_but_never_traps_leave(tmp_path) -> None:
    clock = Clock()
    writer = FakeWriter()
    node, results = node_with(tmp_path, overlay=FakeOverlay(connected=True), writer=writer, clock=clock)
    save(node, auth_key=KEY, hostname="gw")
    main.tick(node)
    clock.now += 3600                                        # the status read stopped
    apply(node, "rename")
    assert "not known yet" in results[-1]["message"]
    apply(node, "leave")
    main.tick(node)
    assert writer.calls == [("leave",)]


def test_rename_runs_when_joined_and_the_name_differs(tmp_path) -> None:
    writer = FakeWriter()
    node, results = node_with(tmp_path, overlay=FakeOverlay(connected=True, hostname="old"), writer=writer)
    save(node, hostname="new-gw")
    main.tick(node)
    apply(node, "rename")
    main.tick(node)
    assert writer.calls == [("rename", "new-gw")] and results[-1]["ok"]


def test_one_operation_at_a_time_no_queue(tmp_path) -> None:
    spawn = Deferred()
    writer = FakeWriter()
    node, results = node_with(tmp_path, overlay=FakeOverlay(connected=True), writer=writer, spawn=spawn)
    main.tick(node)
    apply(node, "leave", who="alice")
    apply(node, "leave", who="bob")
    assert "busy" in results[-1]["message"] and "alice" in results[-1]["message"]
    spawn.pending.pop()()                                     # alice's run finishes
    main.tick(node)
    assert writer.calls == [("leave",)] and results[-1]["ok"]
    apply(node, "leave", who="bob")                           # the slot is free again
    assert len(spawn.pending) == 1


def test_a_writer_that_raises_still_frees_the_slot(tmp_path, caplog) -> None:
    node, results = node_with(tmp_path, overlay=FakeOverlay(connected=True), writer=FakeWriter(raises=True))
    main.tick(node)
    with caplog.at_level(logging.ERROR):
        apply(node, "leave")
    main.tick(node)
    assert not results[-1]["ok"] and "internal error" in results[-1]["message"]
    apply(node, "leave")
    main.tick(node)
    assert "busy" not in results[-1]["message"]


def test_no_value_or_secret_ever_rides_the_bus(tmp_path) -> None:
    node, _ = node_with(tmp_path)
    seen: list[str] = []
    node.bus.subscribe("*", lambda t, p: seen.append(json.dumps(p)))
    node.bus.subscribe("*/*", lambda t, p: seen.append(json.dumps(p)))
    save(node, hostname="site-a-gw", auth_key=KEY)
    main.tick(node)
    apply(node, "join")
    main.tick(node)
    wire = "\n".join(seen)
    assert KEY not in wire and "auth_key" not in wire
    command = next(s for s in seen if '"operation": "join"' in s and "request_id" in s)
    assert "site-a-gw" not in command                          # values come from the registry


def test_env_switch(monkeypatch, caplog) -> None:
    for raw, on in (("", False), ("0", False), ("1", True), ("yes", False)):
        monkeypatch.setenv("CN_TAILNET_WRITER", raw)
        assert main._writer_from_env() is on
    assert "stays disabled" in caplog.text


# -- the dashboard: Join/Rename/Leave through the doors ------------------------------------

def call(conn, method, path, form=None, headers=None):
    body = urlencode(form) if form is not None else None
    hdrs = dict(headers or {})
    if body is not None:
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    conn.request(method, path, body=body, headers=hdrs)
    resp = conn.getresponse()
    return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read().decode()


@pytest.fixture
def served(tmp_path):
    writer = FakeWriter()
    node = main.compose(Bus(), overlay_source=FakeOverlay(), dashboard_port=0,
                        dashboard_host="127.0.0.1", config_root=str(tmp_path), tailnet_writer=True,
                        overlay_writer=writer, writer_spawn=lambda work: work())
    _, port = node.dashboard.start()
    main.tick(node)
    yield node, writer, http.client.HTTPConnection("127.0.0.1", port, timeout=5), \
        {"Origin": f"http://127.0.0.1:{port}"}
    node.dashboard.stop()


def test_panel_offers_join_only_with_a_key(served) -> None:
    node, _, conn, origin = served
    page = call(conn, "GET", "/config")[2]
    assert "On a tailnet: <strong>no</strong>" in page and 'action="/actions/tailnet/join"' not in page
    assert "save an ephemeral auth key" in page and "not enabled yet" not in page
    call(conn, "POST", "/actions/config/tailnet-gateway/auth_key", {"value": KEY}, origin)
    page = call(conn, "GET", "/config")[2]
    assert 'action="/actions/tailnet/join"' in page and 'name="confirm"' in page
    assert 'action="/actions/tailnet/leave"' not in page and KEY not in page


def test_join_from_the_page_needs_confirmation_and_reports_back(served, caplog) -> None:
    node, writer, conn, origin = served
    call(conn, "POST", "/actions/config/tailnet-gateway/auth_key", {"value": KEY}, origin)
    with caplog.at_level(logging.DEBUG):
        status, _, body = call(conn, "POST", "/actions/tailnet/join", {"then": "/config"}, origin)
        assert status == 409 and "ephemeral" in body and writer.calls == []
        status, headers, _ = call(conn, "POST", "/actions/tailnet/join",
                                  {"confirm": "yes", "then": "/config"}, origin)
        assert status == 303 and headers["location"] == "/config"
        main.tick(node)
        page = call(conn, "GET", "/config")[2]
    assert writer.calls and writer.calls[0][0] == "join"
    assert "Last: join done" in page and "requested by local via the local door" in page
    assert KEY not in caplog.text and KEY not in page


def test_disabled_writer_is_said_on_the_page_and_refused(tmp_path) -> None:
    node = main.compose(Bus(), overlay_source=FakeOverlay(connected=True), dashboard_port=0,
                        dashboard_host="127.0.0.1", config_root=str(tmp_path))
    _, port = node.dashboard.start()
    try:
        main.tick(node)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        assert "not enabled yet" in call(conn, "GET", "/config")[2]
        status, _, body = call(conn, "POST", "/actions/tailnet/leave", {"confirm": "yes"},
                               {"Origin": f"http://127.0.0.1:{port}"})
        assert status == 400 and "not available" in body
    finally:
        node.dashboard.stop()
