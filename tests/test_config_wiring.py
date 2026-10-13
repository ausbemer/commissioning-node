"""The config registry wired into the node (ADR-0004 step 4): tailnet-gateway's declared
fields, the dashboard's /config page and its actions, through the real listeners.

Every write goes through `authorize()` like any other; a secret never comes back out of the
page, the API or the logs (§8.9).
"""
from __future__ import annotations

import http.client
import json
import logging
import ssl
from urllib.parse import urlencode

import pytest

import main
from bus import Bus
from config_registry import ConfigRegistry
from modules.dashboard import FallbackConfig
from modules.dashboard.features.config_page import ConfigPage
from modules.dashboard.interface import ConfigSurface
from modules.tailnet_gateway.interface import CONFIG_FIELDS, ConfigView
from test_dashboard_doors import PASSWORD, tls_pair, write_cred

KEY = "tskey-auth-kAbC123-DEFghi456"
HOST = "/actions/config/tailnet-gateway/hostname"
TAGS = "/actions/config/tailnet-gateway/tags"
AUTH = "/actions/config/tailnet-gateway/auth_key"


def call(conn, method: str, path: str, form: dict | None = None, headers: dict | None = None):
    body = urlencode(form) if form is not None else None
    hdrs = dict(headers or {})
    if body is not None:
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    conn.request(method, path, body=body, headers=hdrs)
    resp = conn.getresponse()
    return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read().decode()


# -- the seams -----------------------------------------------------------------------------

def test_registry_handles_satisfy_the_module_seams(tmp_path) -> None:
    reg = ConfigRegistry(tmp_path, CONFIG_FIELDS)
    assert isinstance(reg.owner_view("tailnet-gateway"), ConfigView)
    assert isinstance(reg.operator_surface(), ConfigSurface)


@pytest.mark.parametrize("name, text, ok", [
    ("hostname", "site-a-gw", True),
    ("hostname", "-leading", False),
    ("hostname", "has.dot", False),
    ("hostname", "a" * 64, False),
    ("tags", "tag:commissioning, tag:site-a", True),
    ("tags", "commissioning", False),
    ("auth_key", f"{KEY}\n", True),
    ("auth_key", "not-a-key", False),
])
def test_tailnet_gateway_fields_validate(tmp_path, name, text, ok) -> None:
    surface = ConfigRegistry(tmp_path, CONFIG_FIELDS).operator_surface()
    assert surface.set(f"tailnet-gateway.{name}", text, "test").ok is ok


def test_compose_builds_the_registry_from_every_modules_declarations(tmp_path) -> None:
    node = main.compose(Bus(), dashboard_port=0, config_root=str(tmp_path))
    keys = {f"{f.module}.{f.name}" for f in node.config.operator_surface().fields()}
    assert {"tailnet-gateway.hostname", "tailnet-gateway.tags", "tailnet-gateway.auth_key"} <= keys
    assert all(f.scope == "site" for f in CONFIG_FIELDS)


# -- the local door, end to end ------------------------------------------------------------

@pytest.fixture
def local(tmp_path):
    node = main.compose(Bus(), dashboard_port=0, dashboard_host="127.0.0.1",
                        config_root=str(tmp_path))
    _, port = node.dashboard.start()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    yield node, conn, {"Origin": f"http://127.0.0.1:{port}"}, tmp_path
    node.dashboard.stop()


def test_config_page_lists_the_fields(local) -> None:
    _, conn, _, _ = local
    status, headers, page = call(conn, "GET", "/config")
    assert status == 200 and "refresh" not in page.lower()
    assert "Tailscale hostname" in page and "Tailscale auth key" in page
    assert f'action="{AUTH}"' in page and 'type="password"' in page
    assert 'href="/config"' in call(conn, "GET", "/")[2]


def test_set_from_a_browser_form_returns_to_the_page(local, caplog) -> None:
    node, conn, origin, _ = local
    with caplog.at_level(logging.INFO):
        status, headers, _ = call(conn, "POST", HOST, {"value": "site-a-gw", "then": "/config"}, origin)
    assert status == 303 and headers["location"] == "/config"
    assert node.config.owner_view("tailnet-gateway").get("hostname") == "site-a-gw"
    assert "Current: <strong>site-a-gw</strong>" in call(conn, "GET", "/config")[2]
    assert "by 'local via the local door'" in caplog.text


def test_api_clients_get_the_message_and_bad_values_are_refused(local) -> None:
    node, conn, origin, _ = local
    status, _, body = call(conn, "POST", TAGS, {"value": "tag:a tag:b"}, origin)
    assert status == 200 and "saved" in body
    status, _, body = call(conn, "POST", TAGS, {"value": "nope", "then": "/config"}, origin)
    assert status == 400 and "must match" in body
    assert node.config.owner_view("tailnet-gateway").get("tags") == ["tag:a", "tag:b"]
    assert call(conn, "POST", f"{TAGS}/clear", {}, origin)[0] == 200
    assert node.config.owner_view("tailnet-gateway").get("tags") == []


def test_then_is_never_an_open_redirect(local) -> None:
    _, conn, origin, _ = local
    for then in ("https://evil.example/", "//evil.example", "/nowhere"):
        status, headers, _ = call(conn, "POST", HOST, {"value": "gw", "then": then}, origin)
        assert status == 200 and "location" not in headers


def test_config_writes_pass_authorize(local) -> None:
    node, conn, _, _ = local
    assert call(conn, "POST", HOST, {"value": "gw"})[0] == 403                    # no Origin
    assert call(conn, "POST", HOST, {"value": "gw"}, {"Origin": "http://evil.example"})[0] == 403
    assert node.config.owner_view("tailnet-gateway").get("hostname") is None


def test_secret_goes_in_and_never_comes_out(local, caplog) -> None:
    node, conn, origin, root = local
    with caplog.at_level(logging.DEBUG):
        status, _, _ = call(conn, "POST", AUTH, {"value": KEY + "\r\n", "then": "/config"}, origin)
        assert status == 303
        page = call(conn, "GET", "/config")[2]
        state = call(conn, "GET", "/api/state")[2]
        status, _, refusal = call(conn, "POST", AUTH, {"value": "tskey-bad key!"}, origin)
    assert status == 400 and "bad key" not in refusal
    assert "Current: <strong>set</strong>" in page and "Delete" in page
    for text in (page, state, caplog.text):
        assert KEY not in text
    path = node.config.owner_view("tailnet-gateway").secret_path("auth_key")
    assert path and open(path).read() == KEY
    assert call(conn, "POST", f"{AUTH}/clear", {"then": "/config"}, origin)[0] == 303
    assert node.config.owner_view("tailnet-gateway").secret_path("auth_key") is None


# -- the fallback door: the session's CSRF token is required -------------------------------

def test_fallback_config_write_needs_the_csrf_token(tmp_path) -> None:
    cert, key = tls_pair(tmp_path)
    cred = tmp_path / "admin.cred"
    write_cred(cred)
    node = main.compose(Bus(), dashboard_port=0, dashboard_host="127.0.0.1",
                        fallback=FallbackConfig(0, str(cert), str(key)), admin_cred=str(cred),
                        config_root=str(tmp_path / "state"))
    node.dashboard.start()
    try:
        ctx = ssl.create_default_context(cafile=str(cert))
        ctx.check_hostname = False
        port = node.dashboard.fallback_address[1]
        conn = http.client.HTTPSConnection("127.0.0.1", port, context=ctx, timeout=5)
        origin = {"Origin": f"https://127.0.0.1:{port}"}
        _, headers, _ = call(conn, "POST", "/login", {"password": PASSWORD}, origin)
        jar = {"Cookie": headers["set-cookie"].split(";", 1)[0]}
        page = call(conn, "GET", "/config", headers=jar)[2]
        csrf = page.split('name="csrf" value="', 1)[1].split('"', 1)[0]
        assert call(conn, "POST", HOST, {"value": "gw"}, {**jar, **origin})[0] == 403
        status, headers, _ = call(conn, "POST", HOST, {"value": "gw", "csrf": csrf, "then": "/config"},
                                  {**jar, **origin})
        assert status == 303 and headers["location"] == "/config"
        assert node.config.owner_view("tailnet-gateway").get("hostname") == "gw"
    finally:
        node.dashboard.stop()


# -- rendering -----------------------------------------------------------------------------

def test_page_escapes_and_handles_no_registry() -> None:
    page = ConfigPage()
    assert "not available" in page.render({})
    row = {"key": "m.x", "value": '"><script>x</script>', "is_set": True,
           "set_path": "/actions/config/m/x", "clear_path": "/actions/config/m/x/clear",
           "field": {"module": "m", "name": "x", "kind": "str", "label": "<b>X</b>",
                     "help": "<i>h</i>", "scope": "site", "may_disturb": "<warn>"}}
    html = page.render({"config": [row]})
    assert "<script>" not in html and "<b>" not in html and "<warn>" not in html
    assert 'name="confirm"' in html


def test_unset_bool_preselects_its_default() -> None:
    row = {"key": "m.flag", "value": False, "is_set": False,
           "set_path": "/actions/config/m/flag", "clear_path": "/actions/config/m/flag/clear",
           "field": {"module": "m", "name": "flag", "kind": "bool", "default": False, "scope": "site"}}
    html = ConfigPage().render({"config": [row]})
    assert '<option value="false" selected>' in html and '<option value="true" selected>' not in html


def test_without_a_registry_there_are_no_config_actions(tmp_path) -> None:
    node = main.compose(Bus(), dashboard_port=0, dashboard_host="127.0.0.1")
    _, port = node.dashboard.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        assert call(conn, "POST", HOST, {"value": "gw"}, {"Origin": f"http://127.0.0.1:{port}"})[0] == 404
        assert "not available" in call(conn, "GET", "/config")[2]
    finally:
        node.dashboard.stop()


def test_main_config_root_matches_paths_sh() -> None:
    from test_ops_paths import shell_var
    assert main.CONFIG_ROOT == shell_var("CN_STATE")


def test_state_api_has_no_config() -> None:
    # The JSON API carries node state only; config is a page, and secrets never reach either.
    node = main.compose(Bus(), dashboard_port=0, dashboard_host="127.0.0.1")
    _, port = node.dashboard.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        assert "config" not in json.loads(call(conn, "GET", "/api/state")[2])
    finally:
        node.dashboard.stop()
