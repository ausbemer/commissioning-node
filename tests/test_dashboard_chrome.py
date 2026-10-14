"""The shared page frame (dashboard interface 0.7.0): escaped, inside the CSP, one banner."""
from __future__ import annotations

import re

from modules.dashboard.features.config_page import ConfigPage
from modules.dashboard.features.login_page import LoginPage
from modules.dashboard.features.overview_page import OverviewPage
import http.client

from modules.dashboard.core.http_surface import SECURITY_HEADERS, STATIC_DIR

import main
from bus import Bus
from modules.dashboard.interface import banner, page

HOSTILE = '"><script>alert(1)</script>'

SNAPSHOT = {
    "tailnet": {"self": {"hostname": HOSTILE, "connected": True, "state": "Running",
                         "addresses": ["100.64.0.1"], "tags": [HOSTILE],
                         "advertised_routes": ["10.0.0.0/24"], "served_routes": []}},
    "cameras": {"c1": {"camera_id": HOSTILE, "label": HOSTILE, "address": "10.0.0.5"}},
    "streams": {"c1": {"camera_id": HOSTILE, "live": False, "endpoints": []}},
    "wire": {"system/tick": "0.0.1"},
    "viewer": {"label": HOSTILE, "door": "local", "csrf": None},
}


def _pages() -> list[str]:
    return [OverviewPage().render(dict(SNAPSHOT)), ConfigPage().render(dict(SNAPSHOT)),
            LoginPage().render({"message": HOSTILE})]


def test_every_page_escapes_and_stays_inside_the_csp() -> None:
    shipped = {f.name for f in STATIC_DIR.iterdir()}
    for html in _pages():
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S)
        assert all(body == "" for body in scripts)                  # no inline script (ADR-0006),
                                                                    # so an unescaped value fails here
        for src in re.findall(r'<script src="([^"]+)"', html):
            assert src.startswith("/static/") and src[len("/static/"):] in shipped
        assert not re.search(r"\son[a-z]+=", html)                  # no event handlers
        assert not re.search(r"(src|href)=\"(https?:)?//", html)     # nothing loaded from outside
        assert "@import" not in html and "url(" not in html


def test_banner_marks_the_current_page_and_the_door() -> None:
    html = OverviewPage().render(dict(SNAPSHOT))
    assert '<a href="/" aria-current="page">Overview</a>' in html
    assert '<a href="/config">Config</a>' in html and "via the local door" in html
    assert '<a href="/theme/light?then=/" title="Day">' in html          # nothing chosen yet
    assert 'href="/theme/dark?then=/config"' in ConfigPage().render(dict(SNAPSHOT))
    config = ConfigPage().render(dict(SNAPSHOT))
    assert '<a href="/config" aria-current="page">Config</a>' in config
    assert "refresh" not in config.lower()                          # never wipes a half-typed value


def test_overview_shows_states_in_form() -> None:
    html = OverviewPage().render(dict(SNAPSHOT))
    assert '<span class="chip warn">No endpoint</span>' in html     # no fresh tailnet address
    assert '<span class="chip warn">1 awaiting approval</span>' in html
    assert '<section class="system" id="system">' in html           # a skew is never folded away
    assert "Show details" not in html


def test_system_stays_open_across_the_auto_refresh() -> None:
    calm = {**SNAPSHOT, "wire": {}}
    closed = OverviewPage().render(dict(calm))
    assert '<a href="/?system=open">Show details</a>' in closed and "<table>" not in closed
    opened = OverviewPage().render({**calm, "query": {"system": "open"}})
    assert '<a href="/">Hide details</a>' in opened and 'href="/theme/dark?then=/%3Fsystem%3Dopen"' in opened and "<caption>Modules</caption>" not in opened
    assert "no node card yet" in opened                               # the tables' empty states
    assert "Show details" in OverviewPage().render({**calm, "query": {"system": '"><x'}})


def test_logout_only_with_a_session_and_theme_only_when_known() -> None:
    assert 'action="/logout"' not in banner(name="n", viewer={"label": "a", "door": "local"})
    assert 'action="/logout"' in banner(name="n", viewer={"label": "a", "door": "fallback", "csrf": "t"})
    assert '<html lang="en" data-theme="dark">' in page(title="t", body="", banner_html="", theme="dark")
    assert '<html lang="en">' in page(title="t", body="", banner_html="", theme='x" onload="y')


def test_theme_choice_is_a_cookie_and_returns_only_to_a_served_page(tmp_path) -> None:
    node = main.compose(Bus(), dashboard_port=0, dashboard_host="127.0.0.1", config_root=str(tmp_path))
    _, port = node.dashboard.start()
    try:
        def get(path: str, cookie: str = "") -> http.client.HTTPResponse:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", path, headers={"Cookie": cookie} if cookie else {})
            resp = conn.getresponse()
            resp.body = resp.read().decode()
            return resp

        r = get("/theme/dark?then=/config")
        assert r.status == 303 and r.getheader("Location") == "/config"
        cookie = r.getheader("Set-Cookie")
        assert cookie.startswith("cn-theme=dark;") and "HttpOnly" in cookie and "SameSite=Strict" in cookie
        assert "Secure" not in cookie                                   # plain http on 8080
        for then in ("https://evil.example/", "//evil.example", "/nowhere", ""):
            assert get(f"/theme/light?then={then}").getheader("Location") == "/"
        assert get("/theme/purple").status == 404
        back = get("/theme/dark?then=%2F%3Fsystem%3Dopen")              # the slider keeps System open
        assert back.getheader("Location") == "/?system=open"
        assert get("/theme/dark?then=/config%3Fx%3D%0d%0aSet-Cookie:a").getheader("Location") == \
            "/config?x=Set-Cookie%3Aa"                                  # CR/LF dropped, no header split
        for then in ("/nowhere?system=open", "http://x/?system=open", "//x/?a=b"):
            assert get(f"/theme/dark?then={then}").getheader("Location") == "/"
        assert "Hide details" in get("/?system=open").body              # the refresh keeps the query
        page_html = get("/config", "cn-theme=dark").body
        assert '<html lang="en" data-theme="dark">' in page_html
        assert 'title="Night" aria-current="true"' in page_html
        assert '<html lang="en">' in get("/", 'cn-theme="><x').body       # junk is ignored
    finally:
        node.dashboard.stop()


def test_csp_allows_only_this_nodes_own_script() -> None:
    csp = SECURITY_HEADERS["Content-Security-Policy"]
    assert "script-src 'self';" in csp and "unsafe-eval" not in csp
    assert "unsafe-inline" not in csp.split("script-src", 1)[1].split(";", 1)[0]
    # The script may call back to this node only (the refresh and the theme, ADR-0006
    # amendment); never anywhere else.
    assert "connect-src 'self';" in csp
    assert re.findall(r"connect-src[^;]*", csp) == ["connect-src 'self'"]


def test_every_page_has_the_parts_the_refresh_swaps() -> None:
    for html in _pages():
        assert html.count('<header class="banner">') == 1 and html.count("<main") == 1


def test_leave_is_the_overviews_one_slider() -> None:
    rows = [{"key": "tailnet-gateway.hostname", "value": None, "is_set": False,
             "set_path": "/s", "clear_path": "/c",
             "field": {"module": "tailnet-gateway", "name": "hostname", "kind": "str", "scope": "site"}}]
    writer = {"nodes": {"n": {"modules": [{"capabilities": ["tailnet-gateway:tailnet-writer"]}]}}}
    html = OverviewPage().render({**SNAPSHOT, **writer, "config": rows})
    assert html.count("<form") == html.count('action="/actions/tailnet/leave"') == 1
    leave = html.split('action="/actions/tailnet/leave"', 1)[1].split("</form>", 1)[0]
    assert leave.startswith(" data-slide>") and 'name="confirm" value="yes" required' in leave
    assert '<input type="hidden" name="then" value="/">' in leave and ">Leave the Tailnet</button>" in leave
    assert "Tailnet actions" not in html and 'class="notes"' not in html
    assert '<noscript><meta http-equiv="refresh" content="5"></noscript>' in html
    assert '<body data-refresh="5">' in html
    # Without the writer there is nothing to slide, only the fact that it is off.
    off = OverviewPage().render({**SNAPSHOT, "config": rows})
    assert "/actions/tailnet/" not in off and "Tailnet writer off" in off
    config = ConfigPage().render({**SNAPSHOT, **writer, "config": rows})
    assert "/actions/tailnet/leave" not in config and "data-refresh" not in config


def test_static_files_are_an_allowlist_behind_the_doors(tmp_path) -> None:
    node = main.compose(Bus(), dashboard_port=0, dashboard_host="127.0.0.1", config_root=str(tmp_path))
    _, port = node.dashboard.start()
    try:
        def get(path: str):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp, resp.read().decode()

        resp, body = get("/static/dashboard.js")
        assert resp.status == 200 and resp.getheader("Content-Type").startswith("text/javascript")
        assert "data-slide" in body
        for path in ("/static/../core/http_surface.py", "/static/%2e%2e/interface/admin.py",
                     "/static/", "/static/nope.js"):
            assert get(path)[0].status == 404
    finally:
        node.dashboard.stop()


def test_service_restart_is_a_confirmed_write_on_config(tmp_path) -> None:
    asked: list[bool] = []
    node = main.compose(Bus(), dashboard_port=0, dashboard_host="127.0.0.1",
                        config_root=str(tmp_path), restart=lambda: asked.append(True))
    _, port = node.dashboard.start()
    origin = {"Origin": f"http://127.0.0.1:{port}",
              "Content-Type": "application/x-www-form-urlencoded"}
    try:
        def call(method: str, path: str, body: str | None = None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(method, path, body=body, headers=origin if body is not None else {})
            resp = conn.getresponse()
            return resp, resp.read().decode()

        _, config = call("GET", "/config")
        form = config.split('action="/actions/node/restart"', 1)[1].split("</form>", 1)[0]
        assert form.startswith(" data-slide data-await-restart>") and ">Restart service</button>" in form
        assert config.index('class="index"') < config.index("/actions/node/restart") < config.index('class="block"')
        resp, _ = call("POST", "/actions/node/restart", "then=%2Fconfig")
        assert resp.status == 409 and asked == []                  # may-disturb: confirm first
        resp, _ = call("POST", "/actions/node/restart", "confirm=yes&then=%2Fconfig")
        assert resp.status == 303 and resp.getheader("Location") == "/config" and asked == [True]
    finally:
        node.dashboard.stop()
    # Not wired (tests, one-shot modes): no path, so no control.
    bare = main.compose(Bus(), dashboard_port=0, config_root=str(tmp_path))
    assert "/actions/node/restart" not in bare.dashboard._actions
    rows = [{"key": "tailnet-gateway.hostname", "value": None, "is_set": False,
             "set_path": "/s", "clear_path": "/c",
             "field": {"module": "tailnet-gateway", "name": "hostname", "kind": "str", "scope": "site"}}]
    assert "/actions/node/restart" not in ConfigPage().render({"config": rows, "writes": ["/s", "/c"]})
    assert "/actions/node/restart" in ConfigPage().render({"config": rows, "writes": ["/actions/node/restart"]})


def test_restart_exit_status_matches_the_unit() -> None:
    from pathlib import Path
    unit = (Path(__file__).resolve().parent.parent / "ops/systemd/commissioning-node.service").read_text()
    assert f"RestartForceExitStatus={main.RESTART_EXIT}" in unit
    assert f"SuccessExitStatus={main.RESTART_EXIT}" in unit


def test_login_page_loads_no_script() -> None:
    # The fallback serves /static/ only after a login, so the login page must not ask for it.
    assert "<script" not in LoginPage().render({"message": "", "theme": None})
