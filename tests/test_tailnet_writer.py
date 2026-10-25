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
from modules.tailnet_gateway.interface import VERSION as GW_VERSION, OverlaySnapshot, WriterReply

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
    reply = adapter(run).join("site-a-gw", ["tag:commissioning", "tag:site-a"], keyfile,
                              ["192.168.137.0/24", "10.20.0.0/16"])
    argv, kw = run.calls[0]
    assert argv == ["tailscale", "up", "--operator=admin", f"--auth-key=file:{keyfile}",
                    f"--timeout={tw.UP_TIMEOUT_S}s",
                    "--advertise-routes=192.168.137.0/24,10.20.0.0/16", "--hostname=site-a-gw",
                    "--advertise-tags=tag:commissioning,tag:site-a"]
    assert kw["timeout"] == tw.UP_TIMEOUT_S + tw.KILL_MARGIN_S and "shell" not in kw
    assert reply.ok and reply.message == "joined the tailnet as site-a-gw"
    assert KEY not in " ".join(argv)


def test_join_without_hostname_or_tags_leaves_them_out_but_always_states_routes(keyfile) -> None:
    run = Runner()
    adapter(run).join(None, [], keyfile)
    assert run.calls[0][0] == ["tailscale", "up", "--operator=admin", f"--auth-key=file:{keyfile}",
                               f"--timeout={tw.UP_TIMEOUT_S}s", "--advertise-routes="]


def test_rename_routes_and_leave_argv_golden() -> None:
    run = Runner()
    assert adapter(run).rename("gw-2").ok
    assert adapter(run).set_routes(["192.168.137.0/24"]).message == "now advertising 192.168.137.0/24"
    assert adapter(run).set_routes([]).ok
    assert adapter(run).leave().ok
    assert [c[0] for c in run.calls] == [["tailscale", "set", "--hostname=gw-2"],
                                         ["tailscale", "set", "--advertise-routes=192.168.137.0/24"],
                                         ["tailscale", "set", "--advertise-routes="],
                                         ["tailscale", "logout"]]


@pytest.mark.parametrize("routes, why", [
    (["192.168.137.5/24"], "not a network address"),
    (["100.100.0.0/16"], "overlaps the tailnet"),
    (["fd00::/64"], "not IPv4"),
    (["4.0.0.0/7"], "between /8 and /31"),
    ([f"10.{i}.0.0/16" for i in range(9)], "too many routes"),
])
def test_routes_are_rechecked_and_never_run(keyfile, routes, why) -> None:
    run = Runner()
    assert why in adapter(run).set_routes(routes).message
    assert why in adapter(run).join("gw", [], keyfile, routes).message
    assert run.calls == []


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
    # A key straddling the cap is still redacted: redact first, then cap.
    long_key = "tskey-auth-" + "k" * (tw.OUTPUT_CAP + 50)
    assert "kkkk" not in scrub("x" + long_key + " tail")


def test_no_operator_is_a_plain_message_not_the_clis_sudo_advice(keyfile) -> None:
    # The CLI's text from 2026-10-25 (quirks Q-010): its advice, a `sudo tailscale up`, would
    # bypass the writer and its join record (ADR-0002 A4, ADR-0005).
    run = Runner(returncode=1, stderr="Access denied: checkprefs access denied\n\nUse 'sudo tailscale "
                                      f"up --operator=pi --auth-key=file:{keyfile}'.")
    reply = adapter(run).join("gw", [], keyfile)
    assert not reply.ok and reply.message == f"join failed: {tw.NO_OPERATOR_MESSAGE}"
    assert "sudo tailscale up" not in reply.message
    assert "commissioning-node-tailscale-operator" in reply.message


def test_timeout_and_missing_cli_are_in_band(keyfile) -> None:
    reply = adapter(Runner(raises=subprocess.TimeoutExpired("tailscale", 70))).join("gw", [], keyfile)
    assert not reply.ok and "timed out" in reply.message and "unknown" in reply.message
    gone = TailscaleCliWriter(run=Runner(), which=lambda b: None, operator=lambda: "admin")
    assert "not installed" in gone.leave().message


# -- the decision rules and the worker, through the real node ------------------------------

class FakeOverlay:
    def __init__(self, connected: bool = False, hostname: str = "Commissioning-node-pi5",
                 tags: list[str] | None = None, state: str | None = None) -> None:
        self.snapshot = OverlaySnapshot(hostname=hostname, connected=connected, tags=tags or [],
                                        state=state or ("Running" if connected else "NeedsLogin"))

    def read(self) -> OverlaySnapshot:
        return self.snapshot


class FakeWriter:
    """Acts like tailscale on a FakeOverlay: a successful join makes it Running, a successful
    leave makes it NeedsLogin, a rename renames it."""

    def __init__(self, ok: bool = True, raises: bool = False, overlay: "FakeOverlay | None" = None) -> None:
        self.calls: list[tuple] = []
        self.ok, self.raises, self.overlay = ok, raises, overlay

    def _reply(self, call: tuple) -> WriterReply:
        self.calls.append(call)
        if self.raises:
            raise RuntimeError("boom")
        if self.ok and self.overlay is not None:
            snap = self.overlay.snapshot
            if call[0] == "join":
                self.overlay.snapshot = snap.model_copy(update={"connected": True, "state": "Running"})
            elif call[0] == "leave":
                self.overlay.snapshot = snap.model_copy(update={"connected": False, "state": "NeedsLogin"})
            elif call[0] == "rename":
                self.overlay.snapshot = snap.model_copy(update={"hostname": call[1]})
        return WriterReply(ok=self.ok, message=f"{call[0]} {'ok' if self.ok else 'failed: denied'}")

    def join(self, hostname, tags, key_path, routes=()):
        return self._reply(("join", hostname, tuple(tags), tuple(routes), key_path))

    def rename(self, hostname):
        return self._reply(("rename", hostname))

    def set_routes(self, routes):
        return self._reply(("routes", tuple(routes)))

    def leave(self):
        return self._reply(("leave",))


class FakeNetworks:
    def __init__(self, networks: list[str] | None = None) -> None:
        self.networks = ["192.168.137.0/24"] if networks is None else networks

    def read(self) -> list[str]:
        return list(self.networks)


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


def node_with(tmp_path, overlay=None, writer=None, enabled=True, spawn=None, clock=None,
              networks=None):
    overlay = overlay or FakeOverlay()
    if writer is not None and writer.overlay is None:
        writer.overlay = overlay
    node = main.compose(Bus(), overlay_source=overlay, dashboard_port=0,
                        config_root=str(tmp_path), tailnet_writer=enabled,
                        overlay_writer=writer or FakeWriter(), clock=clock or Clock(),
                        writer_spawn=spawn or (lambda work: work()),
                        site_networks=networks or FakeNetworks())
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
                              f"tailnet-gateway at v{GW_VERSION} has it installed but disabled.")
    assert writer.calls == [] and results == []
    assert "tailnet-gateway:tailnet-writer" not in node.capabilities()
    assert main.compose(Bus(), dashboard_port=0).capabilities() == node.capabilities()


def test_join_applies_the_saved_values_keeps_the_key_and_records_the_join(tmp_path) -> None:
    writer = FakeWriter()
    node, results = node_with(tmp_path, writer=writer)
    save(node, hostname="site-a-gw", tags="tag:commissioning", auth_key=KEY,
         advertise_routes="auto 10.20.0.0/16")
    key_path = node.config.owner_view("tailnet-gateway").secret_path("auth_key")
    main.tick(node)
    assert apply(node, "join")[0] and results == []           # accepted: no result yet
    main.tick(node)                                           # drained on the next tick
    routes = ("192.168.137.0/24", "10.20.0.0/16")
    assert writer.calls == [("join", "site-a-gw", ("tag:commissioning",), routes, key_path)]
    result = interfaces.TailnetApplyResult.model_validate(results[-1])
    assert result.ok and result.request_id == "r-join" and result.requested_by == "alice@example.com"
    assert node.config.owner_view("tailnet-gateway").secret_path("auth_key") == key_path   # A1
    record = json.loads((tmp_path / "site" / "tailnet-gateway.join.json").read_text())
    assert record["routes"] == list(routes)


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
    ("routes", FakeOverlay(), {}, True, "not on a tailnet"),
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


def test_auth_key_accepts_an_oauth_secret_with_its_options(tmp_path) -> None:
    node, _ = node_with(tmp_path)
    surface = node.config.operator_surface()
    for ok in ("tskey-client-kAbC123-DEF456?preauthorized=true",
               "tskey-client-kAbC123-DEF456?ephemeral=true&preauthorized=true",
               "tskey-client-kAbC123-DEF456?preauthorized=false&ephemeral=true", KEY):
        assert surface.set("tailnet-gateway.auth_key", ok, "test").ok, ok
    for bad in ("tskey-client-k1-X?baseURL=https://evil.example",      # no control-plane swap
                "tskey-client-k1-X?preauthorized=true&preauthorized=false",
                "tskey-client-k1-X?preauthorized=yes", "tskey-client-k1-X?",
                "tskey-client-k1-X?ephemeral=false",                     # D4: always ephemeral
                "tskey-client-k1-X?preauthorized=true&ephemeral=false",
                "tskey-client-k1-X?ephemeral=true&ephemeral=true"):
        assert not surface.set("tailnet-gateway.auth_key", bad, "test").ok, bad


def test_env_switch(monkeypatch, caplog) -> None:
    monkeypatch.delenv("CN_TAILNET_WRITER", raising=False)
    assert main._writer_from_env() is True                     # on by default (rehearsed)
    for raw, on in (("", True), ("1", True), ("0", False), ("yes", False), ("off", False)):
        monkeypatch.setenv("CN_TAILNET_WRITER", raw)
        assert main._writer_from_env() is on
    assert "is not 0 or 1; the tailnet writer is disabled" in caplog.text
    assert "CN_TAILNET_WRITER=0: the tailnet writer is disabled" in caplog.text


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
    overlay = FakeOverlay()
    writer = FakeWriter(overlay=overlay)
    node = main.compose(Bus(), overlay_source=overlay, dashboard_port=0,
                        dashboard_host="127.0.0.1", config_root=str(tmp_path), tailnet_writer=True,
                        overlay_writer=writer, writer_spawn=lambda work: work(),
                        site_networks=FakeNetworks())
    _, port = node.dashboard.start()
    main.tick(node)
    yield node, writer, http.client.HTTPConnection("127.0.0.1", port, timeout=5), \
        {"Origin": f"http://127.0.0.1:{port}"}
    node.dashboard.stop()


def test_overview_offers_join_only_with_a_key(served) -> None:
    node, _, conn, origin = served
    page = call(conn, "GET", "/")[2]
    assert "not connected" in page and 'action="/actions/tailnet/join"' not in page
    assert ">Save an auth key</a> to join." in page and "Tailnet writer off" not in page
    call(conn, "POST", "/actions/config/tailnet-gateway/auth_key", {"value": KEY}, origin)
    page = call(conn, "GET", "/")[2]
    assert 'action="/actions/tailnet/join" data-slide>' in page and 'name="confirm"' in page
    assert ">Join the Tailnet</button>" in page
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
        page = call(conn, "GET", "/")[2]
    assert writer.calls and writer.calls[0][0] == "join"
    assert '<dt>Last action</dt><dd><span class="chip ok">Done</span> Join at' in page and ", by local</dd>" in page
    assert "192.168.137.0/24" in page
    assert KEY not in caplog.text and KEY not in page


def test_disabled_writer_is_said_on_the_page_and_refused(tmp_path) -> None:
    node = main.compose(Bus(), overlay_source=FakeOverlay(connected=True), dashboard_port=0,
                        dashboard_host="127.0.0.1", config_root=str(tmp_path))
    _, port = node.dashboard.start()
    try:
        main.tick(node)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        assert "Tailnet writer off" in call(conn, "GET", "/")[2]
        status, _, body = call(conn, "POST", "/actions/tailnet/leave", {"confirm": "yes"},
                               {"Origin": f"http://127.0.0.1:{port}"})
        assert status == 400 and "not available" in body
    finally:
        node.dashboard.stop()


# -- ADR-0005 amendment: routes, the join record, automatic rejoin -------------------------

def test_site_networks_adapter_skips_loopback_tailnet_link_local_and_hosts() -> None:
    from modules.tailnet_gateway.core.site_networks import IpSiteNetworks
    links = [
        {"ifname": "lo", "addr_info": [{"local": "127.0.0.1", "prefixlen": 8}]},
        {"ifname": "eth0", "addr_info": [{"local": "192.168.137.218", "prefixlen": 24}]},
        {"ifname": "eth1", "addr_info": [{"local": "10.20.3.4", "prefixlen": 16},
                                         {"local": "169.254.1.2", "prefixlen": 16},
                                         {"local": "10.9.9.9", "prefixlen": 32}]},
        {"ifname": "wwan0", "addr_info": [{"local": "100.70.1.2", "prefixlen": 30}]},   # CGNAT
        {"ifname": "tailscale0", "addr_info": [{"local": "100.64.50.113", "prefixlen": 32}]},
    ]

    def run(argv, **kw):
        assert argv == ["ip", "-j", "-4", "addr", "show", "up"]
        return subprocess.CompletedProcess(argv, 0, json.dumps(links), "")

    assert IpSiteNetworks(run=run, which=lambda b: "/usr/sbin/ip").read() == ["192.168.137.0/24", "10.20.0.0/16"]
    broken = IpSiteNetworks(run=Runner(raises=OSError(2, "gone")), which=lambda b: "/usr/sbin/ip")
    assert broken.read() == []


def test_resolve_routes_is_the_one_authority() -> None:
    from modules.tailnet_gateway.interface import resolve_routes
    detected = ["192.168.137.0/24"]
    assert resolve_routes(None, detected) == detected                    # unset = auto
    assert resolve_routes(["auto"], []) == []                             # nothing detected
    assert resolve_routes([], detected) == []                             # advertise none
    assert resolve_routes(["10.0.0.0/8", "auto", "10.0.0.0/8", "10.0.0.1/8"], detected) == \
        ["10.0.0.0/8", "192.168.137.0/24"]                                # dedupe, drop invalid


def test_status_carries_state_networks_wanted_and_advertised(tmp_path) -> None:
    node, _ = node_with(tmp_path, writer=FakeWriter())
    seen: list[dict] = []
    node.bus.subscribe(interfaces.TAILNET_STATUS, lambda t, p: seen.append(p))
    save(node, auth_key=KEY, advertise_routes="auto 10.20.0.0/16")
    main.tick(node)
    status = interfaces.TailnetStatus.model_validate(seen[-1])
    assert status.state == "NeedsLogin" and status.site_networks == ["192.168.137.0/24"]
    assert status.wanted_routes == ["192.168.137.0/24", "10.20.0.0/16"] and status.advertised_routes == []
    apply(node, "join")
    main.tick(node)
    status = interfaces.TailnetStatus.model_validate(seen[-1])
    assert status.state == "Running" and status.advertised_routes == status.wanted_routes


def test_routes_updates_a_joined_node_and_its_record(tmp_path) -> None:
    writer = FakeWriter()
    networks = FakeNetworks()
    node, results = node_with(tmp_path, writer=writer, networks=networks)
    save(node, auth_key=KEY)
    main.tick(node)
    apply(node, "join")
    main.tick(node)
    apply(node, "routes")
    assert "already advertising 192.168.137.0/24" in results[-1]["message"]
    networks.networks = ["192.168.50.0/24"]                   # the node moved networks
    main.tick(node)
    apply(node, "routes")
    main.tick(node)
    assert writer.calls[-1] == ("routes", ("192.168.50.0/24",)) and results[-1]["ok"]
    record = json.loads((tmp_path / "site" / "tailnet-gateway.join.json").read_text())
    assert record["routes"] == ["192.168.50.0/24"]


def test_leave_deletes_the_record_and_keeps_the_key(tmp_path) -> None:
    node, results = node_with(tmp_path, writer=FakeWriter())
    save(node, auth_key=KEY)
    main.tick(node)
    apply(node, "join")
    main.tick(node)
    apply(node, "leave")
    main.tick(node)
    assert results[-1]["ok"] and not (tmp_path / "site" / "tailnet-gateway.join.json").exists()
    assert node.config.owner_view("tailnet-gateway").secret_path("auth_key") is not None


def joined_then_dropped(tmp_path, clock, **values):
    """The operator joined; then the tailnet dropped the node (power cycle, ephemeral removal)."""
    overlay = FakeOverlay()
    writer = FakeWriter(overlay=overlay)
    node, results = node_with(tmp_path, overlay=overlay, writer=writer, clock=clock)
    save(node, auth_key=KEY, **values)
    main.tick(node)
    apply(node, "join")
    main.tick(node)
    writer.calls.clear()
    overlay.snapshot = overlay.snapshot.model_copy(update={"connected": False, "state": "NeedsLogin"})
    return node, results, writer, overlay


def test_auto_rejoin_after_the_node_drops_off(tmp_path) -> None:
    clock = Clock()
    node, results, writer, _ = joined_then_dropped(tmp_path, clock, hostname="gw")
    main.tick(node)
    assert writer.calls == []                                  # held off just after a join
    clock.now += 61
    main.tick(node)
    assert writer.calls and writer.calls[0][:2] == ("join", "gw")
    main.tick(node)                                            # drain
    assert results[-1]["ok"] and results[-1]["requested_by"] == "auto-rejoin"
    assert results[-1]["door"] == "node"


def test_auto_rejoin_backs_off_and_never_hammers(tmp_path) -> None:
    clock = Clock()
    node, results, writer, _ = joined_then_dropped(tmp_path, clock)
    writer.ok = False                                          # e.g. the key expired
    clock.now += 61
    attempts = []
    for _ in range(40 * 60 // 5):                              # 40 minutes of 5 s ticks
        clock.now += 5
        before = len(writer.calls)
        main.tick(node)
        if len(writer.calls) > before:
            attempts.append(clock.now)
    gaps = [b - a for a, b in zip(attempts, attempts[1:])]
    assert len(attempts) <= 6 and all(g >= 60 for g in gaps) and gaps == sorted(gaps)
    assert not results[-1]["ok"] and results[-1]["requested_by"] == "auto-rejoin"


@pytest.mark.parametrize("why", ["no record", "switched off", "no key", "not needs-login", "disabled"])
def test_auto_rejoin_never_acts_without_every_condition(tmp_path, why) -> None:
    clock = Clock()
    if why == "no record":                                     # never joined by the operator
        writer = FakeWriter()
        node, _ = node_with(tmp_path, writer=writer, clock=clock)
        save(node, auth_key=KEY)
    else:
        node, _, writer, overlay = joined_then_dropped(tmp_path, clock)
        if why == "switched off":
            save(node, auto_rejoin="false")
        elif why == "no key":
            node.config.operator_surface().clear("tailnet-gateway.auth_key", "test")
        elif why == "not needs-login":
            overlay.snapshot = overlay.snapshot.model_copy(update={"state": "Stopped"})
        elif why == "disabled":
            node.registries[0]._enabled.discard("tailnet-writer")
    for _ in range(30):
        clock.now += 61
        main.tick(node)
    assert writer.calls == []


def test_teardown_wipes_the_join_record(tmp_path) -> None:
    import shutil
    clock = Clock()
    node, _, _, _ = joined_then_dropped(tmp_path, clock)
    assert (tmp_path / "site" / "tailnet-gateway.join.json").exists()
    shutil.rmtree(tmp_path / "site")
    writer = FakeWriter()
    node, _ = node_with(tmp_path, writer=writer, clock=clock)
    for _ in range(5):
        clock.now += 61
        main.tick(node)
    assert writer.calls == []                                  # no record, no key: never joins


def test_config_routes_slider_toggles_off_and_back_on(served) -> None:
    node, writer, conn, origin = served
    call(conn, "POST", "/actions/config/tailnet-gateway/auth_key", {"value": KEY}, origin)
    call(conn, "POST", "/actions/tailnet/join", {"confirm": "yes"}, origin)
    main.tick(node)
    page = call(conn, "GET", "/")[2]
    assert '<dt>Advertised</dt><dd><span class="mono">192.168.137.0/24</span>' in page
    assert "1 awaiting approval" in page and "/actions/tailnet/routes" not in page
    config = call(conn, "GET", "/config")[2]
    assert "Approved by the tailnet: none" in config
    # Advertising: the slider is the off switch, drawn like Leave; nothing to advertise.
    off = config.split('action="/actions/tailnet/routes-off"', 1)[1].split("</form>", 1)[0]
    assert off.startswith(" data-slide>") and ">Stop advertising routes</button>" in off
    assert '<input type="hidden" name="then" value="/config">' in off
    assert 'class="op danger bare"' in config and 'action="/actions/tailnet/routes"' not in config
    status, headers, _ = call(conn, "POST", "/actions/tailnet/routes-off",
                              {"confirm": "yes", "then": "/config"}, origin)
    assert status == 303 and headers["location"] == "/config"
    main.tick(node)
    assert writer.calls[-1] == ("routes", ())
    # Off: the slider advertises the saved routes again (a custom CIDR here).
    call(conn, "POST", "/actions/config/tailnet-gateway/advertise_routes", {"value": "10.20.0.0/16"}, origin)
    main.tick(node)
    config = call(conn, "GET", "/config")[2]
    assert "Advertised: none" in config and "/actions/tailnet/routes-off" not in config
    on = config.split('action="/actions/tailnet/routes"', 1)[1].split("</form>", 1)[0]
    assert on.startswith(" data-slide>") and ">Advertise 10.20.0.0/16</button>" in on
    call(conn, "POST", "/actions/tailnet/routes", {"confirm": "yes", "then": "/config"}, origin)
    main.tick(node)
    assert writer.calls[-1] == ("routes", ("10.20.0.0/16",))
    assert "/actions/tailnet/routes" not in call(conn, "GET", "/")[2]


def test_pages_mark_the_last_outcome_and_config_shows_a_failure_in_place(served) -> None:
    import re
    node, writer, conn, origin = served
    call(conn, "POST", "/actions/config/tailnet-gateway/auth_key", {"value": KEY}, origin)
    call(conn, "POST", "/actions/tailnet/join", {"confirm": "yes"}, origin)
    main.tick(node)

    def marker(page: str) -> str:
        return re.search(r'data-last-apply="([^"]*)"', page).group(1)

    config = call(conn, "GET", "/config")[2]
    before = marker(config)
    assert before and before == marker(call(conn, "GET", "/")[2])
    # Swappable regions, so the script refreshes them without touching typed input.
    for name in ("hostname", "tags", "advertise_routes"):
        assert f'<div class="extra" data-live id="live-{name}">' in config
    assert '<div data-live id="live-last" hidden' in config
    writer.ok = False
    call(conn, "POST", "/actions/tailnet/routes-off", {"confirm": "yes", "then": "/config"}, origin)
    main.tick(node)
    config = call(conn, "GET", "/config")[2]
    assert marker(config) != before
    region = config.split('id="live-advertise_routes">', 1)[1].split("</div>", 1)[0]
    assert '<p class="fail">Routes-off failed:' in region
    assert ">Stop advertising routes</button>" in region           # still advertising


def test_routes_off_is_what_a_rejoin_restores(tmp_path) -> None:
    clock = Clock()
    overlay = FakeOverlay()
    writer = FakeWriter(overlay=overlay)
    node, results = node_with(tmp_path, overlay=overlay, writer=writer, clock=clock)
    save(node, auth_key=KEY)
    main.tick(node)
    apply(node, "join")
    main.tick(node)
    apply(node, "routes-off")
    main.tick(node)
    assert results[-1]["operation"] == "routes-off" and results[-1]["ok"]
    writer.calls.clear()
    overlay.snapshot = overlay.snapshot.model_copy(update={"connected": False, "state": "NeedsLogin"})
    clock.now += 61
    main.tick(node)
    assert writer.calls and writer.calls[0][0] == "join" and writer.calls[0][3] == ()


def test_rename_sits_beside_the_hostname_on_config(served) -> None:
    node, writer, conn, origin = served
    call(conn, "POST", "/actions/config/tailnet-gateway/auth_key", {"value": KEY}, origin)
    call(conn, "POST", "/actions/tailnet/join", {"confirm": "yes"}, origin)
    main.tick(node)
    assert "/actions/tailnet/rename" not in call(conn, "GET", "/config")[2]
    call(conn, "POST", "/actions/config/tailnet-gateway/hostname", {"value": "site-b-gw"}, origin)
    main.tick(node)
    config = call(conn, "GET", "/config")[2]
    assert 'action="/actions/tailnet/rename"' in config and ">Rename to site-b-gw</button>" in config
    assert "/actions/tailnet/rename" not in call(conn, "GET", "/")[2]


# -- review 2026-10-13: auto-rejoin acts only on what was confirmed, never after a Leave ----

def test_rejoin_uses_the_confirmed_values_not_what_was_saved_since(tmp_path) -> None:
    clock = Clock()
    node, results, writer, _ = joined_then_dropped(tmp_path, clock, hostname="site-a",
                                                   tags="tag:a")
    save(node, hostname="site-b", tags="tag:b", advertise_routes="10.99.0.0/16")  # staged only
    clock.now += 61
    main.tick(node)
    assert writer.calls[0][:4] == ("join", "site-a", ("tag:a",), ("192.168.137.0/24",))


def test_rejoin_refuses_a_key_that_was_swapped_since(tmp_path) -> None:
    clock = Clock()
    node, results, writer, _ = joined_then_dropped(tmp_path, clock)
    save(node, auth_key="tskey-auth-OTHERTAILNET-xyz")
    clock.now += 61
    main.tick(node)
    main.tick(node)
    assert writer.calls == []                                  # tailscale never called
    assert not results[-1]["ok"] and "not the one the operator joined with" in results[-1]["message"]


def test_a_new_interface_is_not_advertised_by_a_rejoin(tmp_path) -> None:
    clock = Clock()
    networks = FakeNetworks()
    overlay = FakeOverlay()
    writer = FakeWriter(overlay=overlay)
    node, _ = node_with(tmp_path, overlay=overlay, writer=writer, clock=clock, networks=networks)
    save(node, auth_key=KEY)
    main.tick(node)
    apply(node, "join")
    main.tick(node)
    writer.calls.clear()
    networks.networks = ["192.168.137.0/24", "10.64.0.0/16"]  # an LTE uplink came up
    overlay.snapshot = overlay.snapshot.model_copy(update={"connected": False, "state": "NeedsLogin"})
    clock.now += 61
    main.tick(node)
    assert writer.calls[0][3] == ("192.168.137.0/24",)


def test_a_node_that_moved_site_does_not_rejoin_with_the_old_routes(tmp_path) -> None:
    # Release review 2026-10-13: powered off at site A, booted at site B with the same reusable
    # key; the ephemeral node was removed meanwhile, so it boots needing a login.
    clock = Clock()
    networks = FakeNetworks()                                  # site A: 192.168.137.0/24
    overlay = FakeOverlay()
    writer = FakeWriter(overlay=overlay)
    node, results = node_with(tmp_path, overlay=overlay, writer=writer, clock=clock, networks=networks)
    save(node, auth_key=KEY)
    main.tick(node)
    apply(node, "join")
    main.tick(node)
    writer.calls.clear()
    networks.networks = ["10.20.30.0/24"]                      # site B
    overlay.snapshot = overlay.snapshot.model_copy(update={"connected": False, "state": "NeedsLogin"})
    clock.now += 61
    main.tick(node)
    main.tick(node)
    assert writer.calls == []                                  # tailscale never called
    refusals = [r for r in results if not r["ok"] and "may have moved site" in r["message"]]
    assert len(refusals) == 1 and "192.168.137.0/24" in refusals[0]["message"]   # said once
    assert refusals[0]["requested_by"] == "auto-rejoin" and refusals[0]["operation"] == "join"
    # The operator's Join applies the routes of where the node is now.
    apply(node, "join")
    main.tick(node)
    assert writer.calls[0][0] == "join" and writer.calls[0][3] == ("10.20.30.0/24",)


def test_a_network_that_comes_up_late_still_rejoins(tmp_path) -> None:
    clock = Clock()
    networks = FakeNetworks()
    overlay = FakeOverlay()
    writer = FakeWriter(overlay=overlay)
    node, results = node_with(tmp_path, overlay=overlay, writer=writer, clock=clock, networks=networks)
    save(node, auth_key=KEY)
    main.tick(node)
    apply(node, "join")
    main.tick(node)
    writer.calls.clear()
    networks.networks = []                                     # booted; DHCP not answered yet
    overlay.snapshot = overlay.snapshot.model_copy(update={"connected": False, "state": "NeedsLogin"})
    clock.now += 61
    main.tick(node)
    assert writer.calls == []
    networks.networks = ["192.168.137.0/24"]                   # the site network came up
    main.tick(node)
    assert writer.calls and writer.calls[0][0] == "join" and writer.calls[0][3] == ("192.168.137.0/24",)


@pytest.mark.parametrize("failure", ["record delete fails", "logout times out"])
def test_a_leave_never_rejoins_whatever_goes_wrong(tmp_path, monkeypatch, failure) -> None:
    from modules.tailnet_gateway.core import join_record
    clock = Clock()
    overlay = FakeOverlay()
    writer = FakeWriter(overlay=overlay)
    node, results = node_with(tmp_path, overlay=overlay, writer=writer, clock=clock)
    save(node, auth_key=KEY)
    main.tick(node)
    apply(node, "join")
    main.tick(node)
    if failure == "record delete fails":
        def refuse(self):
            raise PermissionError(13, "Permission denied")
        monkeypatch.setattr(join_record.JoinRecordStore, "delete", refuse)
    else:
        def timed_out():                                       # tailscaled logged out anyway
            writer.calls.append(("leave",))
            overlay.snapshot = overlay.snapshot.model_copy(update={"connected": False, "state": "NeedsLogin"})
            return WriterReply(ok=False, message="leave timed out")
        writer.leave = timed_out
    apply(node, "leave")
    for _ in range(40):
        clock.now += 61
        main.tick(node)
    assert [c[0] for c in writer.calls] == ["join", "leave"]
    if failure == "record delete fails":
        # The record outlives a restart; the key must not (review round 2).
        assert node.config.owner_view("tailnet-gateway").secret_path("auth_key") is None
        assert "cannot rejoin" in results[-1]["message"]


def test_a_restart_after_a_failed_record_delete_does_not_rejoin(tmp_path, monkeypatch) -> None:
    from modules.tailnet_gateway.core import join_record
    clock = Clock()
    node, results, writer, overlay = joined_then_dropped(tmp_path, clock)
    overlay.snapshot = overlay.snapshot.model_copy(update={"connected": True, "state": "Running"})
    main.tick(node)
    monkeypatch.setattr(join_record.JoinRecordStore, "delete",
                        lambda self: (_ for _ in ()).throw(PermissionError(13, "Permission denied")))
    apply(node, "leave")
    main.tick(node)
    monkeypatch.undo()
    writer2 = FakeWriter(overlay=overlay)                      # the daemon restarts
    node2, _ = node_with(tmp_path, overlay=overlay, writer=writer2, clock=clock)
    for _ in range(10):
        clock.now += 61
        main.tick(node2)
    assert writer2.calls == []


def test_join_record_store_sweeps_only_its_own_temps(tmp_path) -> None:
    from modules.tailnet_gateway.core.join_record import JoinRecordStore
    mine, other = tmp_path / ".tailnet-gateway.join.json.q9", tmp_path / ".config.json.q9"
    mine.write_text("{")
    other.write_text("{")
    JoinRecordStore(tmp_path)
    assert not mine.exists() and other.exists()


def test_a_failed_operator_join_is_not_retried_by_auto_rejoin(tmp_path) -> None:
    clock = Clock()
    node, results, writer, _ = joined_then_dropped(tmp_path, clock)
    clock.now += 30                                            # inside the hold-off
    main.tick(node)                                            # the node is seen dropped
    assert writer.calls == []
    writer.ok = False
    apply(node, "join")                                        # the operator tries; it fails
    main.tick(node)
    assert len(writer.calls) == 1 and results[-1]["requested_by"] == "alice@example.com"
    clock.now += 31                                            # past the original hold-off
    main.tick(node)
    assert len(writer.calls) == 1                              # pushed out by a full backoff


def test_a_record_without_a_key_fingerprint_never_rejoins(tmp_path, caplog) -> None:
    clock = Clock()
    node, _, writer, _ = joined_then_dropped(tmp_path, clock)
    path = tmp_path / "site" / "tailnet-gateway.join.json"
    record = json.loads(path.read_text())
    del record["key_sha256"]                                   # a record from before the fix
    path.write_text(json.dumps(record))
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            clock.now += 61
            main.tick(node)
    assert writer.calls == [] and caplog.text.count("predates confirmed-key fingerprints") == 1
