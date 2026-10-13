"""The three doors, the one write gate, and the admin fallback (ADR-0003 §1, §3, §4).

Everything here fails toward deny: each test names a way in and checks it is refused, with a
reason, unless every condition holds. The HTTP tests run the real listeners; the tailnet door
is exercised through this host's own non-loopback address, which is what a tailnet request
looks like to the socket.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import shutil
import socket
import ssl
import subprocess
import sys
from pathlib import Path
from typing import Mapping
from urllib.parse import urlencode

import pytest

import interfaces
import main
from bus import Bus
from modules.dashboard import FallbackConfig
from modules.dashboard.interface import (FALLBACK, LOCAL, TAILNET, ActionResult, AdminAuth,
                                         Caller, PeerDirectory, Refusal, Session, authorize,
                                         classify, host_allowed, same_origin)
from modules.dashboard.interface import admin as admin_mod

V = interfaces.__version__
REPO = Path(__file__).resolve().parent.parent
PASSWORD = "correct horse battery staple"


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def peers_with(*addresses: str, login: str = "alice@example.com", clock=None) -> PeerDirectory:
    d = PeerDirectory(clock=clock or Clock())
    d.update(interfaces.TailnetPeers.stamped(V, observed_at=0.0, period_s=5.0, peers=[
        interfaces.TailnetPeer(addresses=list(addresses), node_name="alice.ts.net", login=login)]))
    return d


def write_cred(path: Path, password: str = PASSWORD, n: int = 2**4) -> None:
    """A credential in the ops format, cheap to verify (tests only; ops uses n=2**15)."""
    salt = b"0123456789abcdef"
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=8, p=1, dklen=32)
    path.write_text(json.dumps({"format": 1, "kdf": "scrypt", "n": n, "r": 8, "p": 1, "dklen": 32,
                                "salt": base64.b64encode(salt).decode(),
                                "hash": base64.b64encode(digest).decode()}))


class Noop:
    name = "test.noop"
    path = "/actions/noop"
    may_disturb = None

    def __init__(self) -> None:
        self.calls: list[tuple[dict, Caller]] = []

    def run(self, form: Mapping[str, str], caller: Caller) -> ActionResult:
        self.calls.append((dict(form), caller))
        return ActionResult(True, f"done for {caller.label}")


class Disturbing(Noop):
    name = "test.disturb"
    path = "/actions/disturb"
    may_disturb = "Changing the hostname restarts the tailnet link."


# --- classify: which door ----------------------------------------------------------------

def test_loopback_is_the_local_door_whatever_the_peer_table_says() -> None:
    stale = PeerDirectory(clock=Clock())                      # never received
    for local in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
        assert classify(local, "127.0.0.1", stale) == Caller(LOCAL, "local")


def test_non_loopback_needs_a_resolved_peer() -> None:
    peers = peers_with("100.64.0.10")
    assert classify("100.64.0.7", "::ffff:100.64.0.10", peers) == Caller(TAILNET, "alice@example.com")
    unknown = classify("100.64.0.7", "100.64.0.99", peers)
    assert isinstance(unknown, Refusal) and (unknown.status, unknown.reason) == (403, "unknown")
    # A site-LAN caller in CGNAT space is still nobody: membership is resolution, not range.
    lan = classify("192.168.36.153", "100.64.0.11", peers)
    assert isinstance(lan, Refusal) and lan.status == 403


def test_stale_peer_table_is_503() -> None:
    clock = Clock()
    peers = peers_with("100.64.0.10", clock=clock)
    clock.now += 16.0
    out = classify("100.64.0.7", "100.64.0.10", peers)
    assert isinstance(out, Refusal) and (out.status, out.reason) == (503, "stale")


# --- host_allowed: DNS rebinding ---------------------------------------------------------

@pytest.mark.parametrize("host,local,ok", [
    ("100.64.0.7:8080", "100.64.0.7", True),
    ("[fd7a:115c:a1e0::7]:8080", "fd7a:115c:a1e0::7", True),
    ("100.64.0.8:8080", "100.64.0.7", False),             # some other address
    ("localhost:8080", "127.0.0.1", True),
    ("localhost:8080", "100.64.0.7", False),              # localhost only means loopback
    ("127.0.0.1:8080", "::ffff:127.0.0.1", True),
    # Names are where rebinding lives: a LAN attacker answers LLMNR/mDNS/site DNS for bare
    # names, and a hostile resolver can answer ts.net for a client without MagicDNS.
    ("cn-01:8080", "100.64.0.7", False),
    ("cn-01.tail1234.ts.net:8080", "100.64.0.7", False),
    ("evil.example:8080", "100.64.0.7", False),
    ("", "100.64.0.7", False),
    (None, "127.0.0.1", False),
])
def test_host_must_be_this_nodes_address(host, local, ok) -> None:
    assert host_allowed(host, local) is ok


def test_vouched_names_match_exactly() -> None:
    fqdn = {"cn-01.tail1234.ts.net"}
    assert host_allowed("CN-01.tail1234.ts.net:8080", "100.64.0.7", fqdn)
    assert not host_allowed("cn-01:8080", "100.64.0.7", fqdn)
    assert not host_allowed("cn-01.tail1234.ts.net.evil.example", "100.64.0.7", fqdn)


def test_same_origin() -> None:
    assert same_origin("http://100.64.0.7:8080", "http", "100.64.0.7:8080")
    assert not same_origin("https://evil.example", "http", "100.64.0.7:8080")
    assert not same_origin(None, "http", "100.64.0.7:8080")
    assert not same_origin("http://100.64.0.7:8080", "https", "100.64.0.7:8080")


# --- authorize: the one write gate -------------------------------------------------------

def test_authorize_needs_a_caller_and_same_origin() -> None:
    assert authorize(None, Noop(), {}, True).status == 401
    tailnet = Caller(TAILNET, "alice@example.com")
    assert authorize(tailnet, Noop(), {}, False).reason == "cross-origin"
    ok = authorize(tailnet, Noop(), {}, True)
    assert ok.allowed and "alice@example.com" in ok.message and "tailnet" in ok.message


def test_fallback_writes_need_the_session_token() -> None:
    admin = Caller(FALLBACK, "admin@fallback", csrf="tok")
    assert authorize(admin, Noop(), {}, True).reason == "csrf"
    assert authorize(admin, Noop(), {"csrf": "nope"}, True).reason == "csrf"
    assert authorize(admin, Noop(), {"csrf": "tok"}, True).allowed
    assert authorize(Caller(FALLBACK, "admin@fallback"), Noop(), {"csrf": ""}, True).reason == "csrf"


def test_may_disturb_needs_confirmation_and_warns_the_tailnet_caller() -> None:
    tailnet = authorize(Caller(TAILNET, "a@x"), Disturbing(), {}, True)
    assert (tailnet.status, tailnet.reason) == (409, "unconfirmed")
    assert "restarts the tailnet link" in tailnet.message and ":8443" in tailnet.message
    local = authorize(Caller(LOCAL, "local"), Disturbing(), {}, True)
    assert local.status == 409 and ":8443" not in local.message
    assert authorize(Caller(LOCAL, "local"), Disturbing(), {"confirm": "yes"}, True).allowed


# --- AdminAuth ---------------------------------------------------------------------------

def test_the_ops_script_credential_verifies(tmp_path: Path) -> None:
    # Pins the file format across the tier boundary: written by ops, read by the dashboard.
    cred = tmp_path / "admin.cred"
    subprocess.run([sys.executable, "-I", str(REPO / "ops" / "admin_cred.py"), "set", "--path",
                    str(cred), "--password-stdin"], input=PASSWORD + "\n", text=True, check=True,
                   capture_output=True)
    auth = AdminAuth(str(cred), clock=Clock())
    assert auth.status() == (True, "ok")
    assert isinstance(auth.login("192.168.36.20", PASSWORD), Session)
    assert isinstance(auth.login("192.168.36.21", PASSWORD + "x"), Refusal)


@pytest.mark.parametrize("content", [None, "", "{}", '{"format": 1, "kdf": "md5"}',
                                     '{"format": 1, "kdf": "scrypt", "n": 3, "r": 8, "p": 1, '
                                     '"salt": "AAAAAAAAAAA=", "hash": "AAAA"}'])
def test_missing_or_malformed_credential_is_off(tmp_path: Path, content) -> None:
    cred = tmp_path / "admin.cred"
    if content is not None:
        cred.write_text(content)
    auth = AdminAuth(str(cred), clock=Clock())
    usable, why = auth.status()
    assert not usable and why
    out = auth.login("192.168.36.20", PASSWORD)
    assert isinstance(out, Refusal) and (out.status, out.reason) == (503, "disabled")


def test_per_source_limit(tmp_path: Path) -> None:
    write_cred(tmp_path / "c")
    clock = Clock()
    auth = AdminAuth(str(tmp_path / "c"), clock=clock)
    for _ in range(admin_mod.LOGIN_FAILS_PER_SRC):
        assert auth.login("192.168.36.20", "wrong").reason == "bad-password"
    # That source is now out, even with the right password; another source is not.
    out = auth.login("::ffff:192.168.36.20", PASSWORD)
    assert out.reason == "rate-limited" and "try again in" in out.message
    assert isinstance(auth.login("192.168.36.21", PASSWORD), Session)
    clock.now += admin_mod.LOGIN_WINDOW_S
    assert isinstance(auth.login("192.168.36.20", PASSWORD), Session)


def test_ipv6_sources_count_per_64(tmp_path: Path) -> None:
    write_cred(tmp_path / "c")
    auth = AdminAuth(str(tmp_path / "c"), clock=Clock())
    for i in range(admin_mod.LOGIN_FAILS_PER_SRC):
        assert auth.login(f"fe80::{i + 1}", "wrong").reason == "bad-password"   # rotating
    assert auth.login("fe80::99", PASSWORD).reason == "rate-limited"            # same /64
    assert isinstance(auth.login("192.168.36.20", PASSWORD), Session)           # IPv4 is not


def test_many_attacking_sources_never_lock_the_operator_out(tmp_path: Path) -> None:
    # Review finding: a global "wait after any failure" let an attacker failing once a second
    # from rotating addresses refuse every operator login. Now nothing global refuses.
    write_cred(tmp_path / "c")
    clock = Clock()
    auth = AdminAuth(str(tmp_path / "c"), clock=clock)
    for i in range(300):
        auth.login(f"10.{i // 250}.{i % 250}.1", "wrong")
        auth.login(f"2001:db8:{i:x}::1", "wrong")
        clock.now += 0.01
        if i % 30 == 0:
            assert isinstance(auth.login("192.168.36.20", PASSWORD), Session)


def test_concurrent_logins_take_turns_and_all_finish(tmp_path: Path) -> None:
    import threading
    write_cred(tmp_path / "c")
    auth = AdminAuth(str(tmp_path / "c"), clock=Clock())
    results: list = []
    threads = [threading.Thread(target=lambda i=i: results.append(
        auth.login(f"10.0.0.{i}", PASSWORD if i % 2 else "wrong"))) for i in range(1, 21)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(results) == 20
    assert sum(isinstance(r, Session) for r in results) == 10


def test_unusable_credential_detail_stays_out_of_the_reply(tmp_path: Path) -> None:
    (tmp_path / "c").write_text('{"format": 1, "kdf": "scrypt", "n": "SECRET-DETAIL"}')
    out = AdminAuth(str(tmp_path / "c"), clock=Clock()).login("10.0.0.1", PASSWORD)
    assert out.reason == "disabled" and "SECRET-DETAIL" not in out.message


def test_sessions_expire_idle_and_absolute(tmp_path: Path) -> None:
    write_cred(tmp_path / "c")
    clock = Clock()
    auth = AdminAuth(str(tmp_path / "c"), clock=clock)
    s = auth.login("10.0.0.1", PASSWORD)
    assert auth.session(s.token) is s and auth.session("forged") is None and auth.session(None) is None
    clock.now += admin_mod.SESSION_IDLE_S + 1
    assert auth.session(s.token) is None

    s = auth.login("10.0.0.1", PASSWORD)
    for _ in range(int(admin_mod.SESSION_MAX_S // admin_mod.SESSION_IDLE_S) + 1):
        clock.now += admin_mod.SESSION_IDLE_S - 1            # active, never idle
        alive = auth.session(s.token)
    assert alive is None                                      # but not forever
    s = auth.login("10.0.0.1", PASSWORD)
    auth.logout(s.token)
    assert auth.session(s.token) is None


def test_rotating_the_password_ends_sessions(tmp_path: Path) -> None:
    cred = tmp_path / "c"
    write_cred(cred)
    auth = AdminAuth(str(cred), clock=Clock())
    s = auth.login("10.0.0.1", PASSWORD)
    write_cred(cred, password="a different password")
    assert auth.session(s.token) is None
    cred.unlink()
    assert auth.login("10.0.0.1", "a different password").reason == "disabled"


def test_session_count_is_bounded(tmp_path: Path) -> None:
    write_cred(tmp_path / "c")
    auth = AdminAuth(str(tmp_path / "c"), clock=Clock())
    tokens = [auth.login("10.0.0.1", PASSWORD).token for _ in range(admin_mod.MAX_SESSIONS + 3)]
    assert auth.session(tokens[0]) is None and auth.session(tokens[-1]) is not None


# --- HTTP: the real listeners ------------------------------------------------------------

def call(conn: http.client.HTTPConnection, method: str, path: str, form: dict | None = None,
         headers: dict | None = None) -> tuple[int, dict, str]:
    body = urlencode(form) if form is not None else None
    hdrs = dict(headers or {})
    if body is not None:
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    conn.request(method, path, body=body, headers=hdrs)
    resp = conn.getresponse()
    return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read().decode()


@pytest.fixture
def local_node():
    action = Noop()
    node = main.compose(Bus(), dashboard_port=0, dashboard_host="127.0.0.1",
                        actions=[action, Disturbing()])
    _, port = node.dashboard.start()
    yield node, port, action
    node.dashboard.stop()


def test_local_write_passes_the_gate(local_node) -> None:
    _, port, action = local_node
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    origin = {"Origin": f"http://127.0.0.1:{port}"}
    assert call(conn, "POST", "/actions/noop", {"x": "1"}, origin)[0] == 200
    assert action.calls[-1][1] == Caller(LOCAL, "local")
    assert call(conn, "POST", "/actions/noop", {"x": "1"})[0] == 403             # no Origin
    assert call(conn, "POST", "/actions/noop", {"x": "1"},
                {"Origin": "http://evil.example"})[0] == 403
    assert call(conn, "POST", "/actions/disturb", {}, origin)[0] == 409
    assert call(conn, "POST", "/actions/disturb", {"confirm": "yes"}, origin)[0] == 200
    assert len(action.calls) == 1                                                # refusals never ran


def test_rebinding_host_is_refused_even_on_loopback(local_node) -> None:
    _, port, _ = local_node
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    status, _, body = call(conn, "GET", "/api/state", headers={"Host": f"evil.example:{port}"})
    assert status == 421 and "IP address" in body


def test_hardening_headers_and_body_limits(local_node) -> None:
    _, port, _ = local_node
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    _, headers, _ = call(conn, "GET", "/")
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["x-frame-options"] == "DENY" and headers["cache-control"] == "no-store"
    conn.request("POST", "/actions/noop", body="x" * 20000,
                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert conn.getresponse().status == 413


def _routable_local_ip() -> str | None:
    """This host's own non-loopback address (the source it would use toward the world)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))        # TEST-NET-1; UDP connect sends nothing
            ip = s.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith("127.") else ip


def test_tailnet_door_end_to_end() -> None:
    ip = _routable_local_ip()
    if ip is None:
        pytest.skip("no non-loopback address on this host")
    action = Noop()
    node = main.compose(Bus(), dashboard_port=0, dashboard_host="::", actions=[action])
    _, port = node.dashboard.start()
    try:
        conn = http.client.HTTPConnection(ip, port, timeout=5)
        status, _, body = call(conn, "GET", "/api/state")
        assert status == 503 and "stale" in body                      # no peer table yet
        node.bus.publish(interfaces.TAILNET_PEERS, interfaces.TailnetPeers.stamped(
            V, observed_at=0.0, period_s=5.0, peers=[interfaces.TailnetPeer(
                addresses=["100.64.0.99"], node_name="bob.ts.net", login="bob@example.com")]
        ).model_dump())
        assert call(conn, "GET", "/api/state")[0] == 403                # not a known peer
        node.bus.publish(interfaces.TAILNET_PEERS, interfaces.TailnetPeers.stamped(
            V, observed_at=0.0, period_s=5.0, peers=[interfaces.TailnetPeer(
                addresses=[ip], node_name="alice.ts.net", login="alice@example.com")]
        ).model_dump())
        status, _, html = call(conn, "GET", "/")
        assert status == 200 and "alice@example.com" in html and "tailnet door" in html
        assert call(conn, "POST", "/actions/noop", {}, {"Origin": f"http://{ip}:{port}"})[0] == 200
        assert action.calls[-1][1] == Caller(TAILNET, "alice@example.com")
    finally:
        node.dashboard.stop()


# --- HTTP: the TLS fallback --------------------------------------------------------------

def tls_pair(tmp_path: Path) -> tuple[Path, Path]:
    if shutil.which("openssl") is None:
        pytest.skip("openssl not installed")
    cert, key = tmp_path / "fallback.crt", tmp_path / "fallback.key"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt",
                    "ec_paramgen_curve:P-256", "-nodes", "-keyout", str(key), "-out", str(cert),
                    "-days", "1", "-subj", "/CN=test"], check=True, capture_output=True)
    return cert, key


@pytest.fixture
def fallback_node(tmp_path: Path):
    cert, key = tls_pair(tmp_path)
    cred = tmp_path / "admin.cred"
    write_cred(cred)
    action = Noop()
    node = main.compose(Bus(), dashboard_port=0, dashboard_host="127.0.0.1", actions=[action],
                        fallback=FallbackConfig(0, str(cert), str(key)), admin_cred=str(cred))
    node.dashboard.start()
    ctx = ssl.create_default_context(cafile=str(cert))
    ctx.check_hostname = False
    port = node.dashboard.fallback_address[1]
    yield (lambda: http.client.HTTPSConnection("127.0.0.1", port, context=ctx, timeout=5)), port, action, cred
    node.dashboard.stop()


def test_fallback_login_session_csrf_logout(fallback_node) -> None:
    connect, port, action, _ = fallback_node
    origin = {"Origin": f"https://127.0.0.1:{port}"}
    conn = connect()
    status, headers, _ = call(conn, "GET", "/")
    assert status == 303 and headers["location"] == "/login"
    assert call(conn, "GET", "/api/state")[0] == 401
    assert call(conn, "POST", "/actions/noop", {})[0] == 401
    assert call(conn, "POST", "/login", {"password": PASSWORD})[0] == 403          # no Origin
    status, _, page = call(conn, "POST", "/login", {"password": "wrong"}, origin)
    assert status == 401 and "wrong password" in page
    status, headers, _ = call(conn, "POST", "/login", {"password": PASSWORD}, origin)
    assert status == 303
    cookie = headers["set-cookie"]
    for attr in ("Secure", "HttpOnly", "SameSite=Strict"):
        assert attr in cookie
    jar = {"Cookie": cookie.split(";", 1)[0]}

    status, _, html = call(conn, "GET", "/", headers=jar)
    assert status == 200 and "admin@fallback" in html and 'action="/logout"' in html
    csrf = html.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    assert call(conn, "POST", "/actions/noop", {}, {**jar, **origin})[0] == 403    # no token
    assert call(conn, "POST", "/actions/noop", {"csrf": csrf}, jar)[0] == 403      # no Origin
    assert call(conn, "POST", "/actions/noop", {"csrf": csrf}, {**jar, **origin})[0] == 200
    assert action.calls[-1][1].label == "admin@fallback"

    status, headers, _ = call(conn, "POST", "/logout", {"csrf": csrf}, {**jar, **origin})
    assert status == 303 and "Max-Age=0" in headers["set-cookie"]
    assert call(conn, "GET", "/api/state", headers=jar)[0] == 401


def test_plain_http_to_the_fallback_gets_nothing(fallback_node) -> None:
    _, port, _, _ = fallback_node
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    with pytest.raises((ConnectionError, http.client.HTTPException, OSError)):
        call(conn, "GET", "/api/state")


@pytest.mark.parametrize("content", [None, "{}"])
def test_no_usable_credential_no_listener(tmp_path: Path, content) -> None:
    cert, key = tls_pair(tmp_path)                 # TLS is fine: only the password is missing
    cred = tmp_path / "admin.cred"
    if content is not None:
        cred.write_text(content)
    node = main.compose(Bus(), dashboard_port=0, dashboard_host="127.0.0.1",
                        fallback=FallbackConfig(0, str(cert), str(key)), admin_cred=str(cred))
    node.dashboard.start()
    try:
        assert node.dashboard.fallback_address is None
    finally:
        node.dashboard.stop()


def test_bad_tls_files_keep_the_fallback_off_and_the_dashboard_up(tmp_path: Path) -> None:
    write_cred(tmp_path / "c")
    node = main.compose(Bus(), dashboard_port=0, dashboard_host="127.0.0.1",
                        fallback=FallbackConfig(0, "/nonexistent.crt", "/nonexistent.key"),
                        admin_cred=str(tmp_path / "c"))
    _, port = node.dashboard.start()
    try:
        assert node.dashboard.fallback_address is None
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        assert call(conn, "GET", "/api/state")[0] == 200
    finally:
        node.dashboard.stop()


def test_referrer_policy_keeps_the_browser_origin():
    """`no-referrer` makes browsers send `Origin: null` on same-origin POSTs, which the
    same-origin check refuses, so no browser could log in or write (found on the node)."""
    from modules.dashboard.core.http_surface import SECURITY_HEADERS
    assert SECURITY_HEADERS["Referrer-Policy"] == "same-origin"
