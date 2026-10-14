"""The overview's site map and the Scan action (ADR-0007 step 3).

The map is rendered from made-up snapshots (no real site's hosts, Safety 6). The Scan runs end
to end through the real doors, `authorize()`, `invoke()` and site-survey with a fake scanner.
"""
from __future__ import annotations

import http.client
import re
from urllib.parse import urlencode

import pytest

import interfaces
import main
from bus import Bus
from modules.dashboard.features import overview_page as ov
from modules.dashboard.features.overview_page import OverviewPage
from modules.site_survey.interface import ScanReply
from modules.tailnet_gateway.interface import OverlayPeer, OverlaySnapshot

NOW = 1_800_000_000.0
NET = "10.20.30.0/24"
CARD = {"cn": {"hostname": "cn", "modules": [{"name": "site-survey", "capabilities": ["site-survey:scanner"]}]}}


def host(address, **kw):
    return {"address": address, "mac": None, "maker": None, "hostname": None, "open_ports": [],
            "axis_maker": False, "found_by": "tcp", **kw}


def snap(hosts=None, scanned_at=NOW - 240, result=None, cameras=None, nodes=CARD, status=None, request=None):
    s = {"nodes": nodes, "tailnet": {"self": status or {"hostname": "cn", "connected": True,
                                                        "addresses": ["100.64.0.7"], "site_networks": [NET]}},
         "cameras": cameras or {}, "streams": {}, "wire": {}, "now": NOW,
         "viewer": {"label": "local", "door": "local", "csrf": None}}
    if hosts is not None:
        s["site_hosts"] = {"self": {"scanned_at": scanned_at, "networks": [NET], "hosts": hosts,
                                    "self_addresses": ["10.20.30.2"], "requested_by": "alice@example.com"}}
    if result is not None:
        s["site_scan"] = {"self": result}
    if request is not None:
        s["site_request"] = {"self": request}
    return s


def render(s) -> str:
    return OverviewPage().render(s)


def tiles(page: str) -> list[str]:
    return re.findall(r'<li class="tile[^"]*"[^>]*>.*?</li>', page)


def test_not_scanned_yet_shows_the_detected_network_and_the_slider() -> None:
    page = render(snap())
    assert "Not scanned yet. A scan probes the site network once, when you ask." in page
    assert f'<span class="cidr mono">{NET}</span>' in page and "Not scanned yet.</li>" in page
    assert 'action="/actions/site/scan" data-slide data-marker="data-last-scan">' in page
    assert "Probes every address on the site network (ports 80, 443, 554)" in page
    assert '<span hidden data-last-scan=":"></span>' in page


def test_scanner_off_has_no_slider() -> None:
    page = render(snap(nodes={"cn": {"hostname": "cn", "modules": []}}))
    assert "/actions/site/scan" not in page and "Scanner off" in page


def test_a_scan_draws_every_host_with_its_flags() -> None:
    page = render(snap([
        host("10.20.30.1", hostname="router.lan", maker="ASUSTek Computer", open_ports=[80]),
        host("10.20.30.21", maker="Axis Communications AB", axis_maker=True, open_ports=[80, 443, 554]),
        host("10.20.30.50", maker="private MAC", found_by="arp"),
    ]))
    assert "Scanned 4 min ago" in page and "by alice@example.com: 3 hosts answered on 10.20.30.0/24." in page
    assert "<strong>cn</strong><span class=\"mono\">10.20.30.2</span>" in page      # the gateway's own address
    t = tiles(page)
    assert len(t) == 3
    assert 'class="tile cam"' in t[0] and "Axis by maker" in t[0] and "10.20.30.21" in t[0]   # flagged first
    assert "router.lan" in t[1] and "80</span>" in t[1]
    assert 'class="tile arp"' in t[2] and "ARP only" in t[2] and "private MAC" in t[2]
    assert '<div class="wire ok"' in page and "100.64.0.7" in page


def test_a_registered_camera_is_flagged_and_missing_when_it_did_not_answer() -> None:
    cams = {"A": {"camera_id": "ACCC8E000021", "address": "10.20.30.21", "label": "Gate 3"},
            "B": {"camera_id": "ACCC8E000022", "address": "10.20.30.22", "label": "Gate 4"}}
    t = tiles(render(snap([host("10.20.30.21", axis_maker=True)], cameras=cams)))
    assert "Registered: Gate 3" in t[0] and "Axis by maker" in t[0] and 'class="tile cam"' in t[0]
    assert 'class="tile cam missing"' in t[1] and "Registered: Gate 4" in t[1] and "Did not answer" in t[1]


def test_past_the_tile_limit_unflagged_hosts_fold_into_a_table() -> None:
    hosts = [host(f"10.20.30.{i}") for i in range(10, 50)] + [host("10.20.30.200", axis_maker=True)]
    page = render(snap(hosts))
    t = tiles(page)
    assert len(t) == ov.MAP_TILES and "Axis by maker" in t[0]
    assert 'class="tile fold"' in t[-1] and f">{41 - (ov.MAP_TILES - 1)} other hosts</a>" in t[-1]
    assert '<div id="site-hosts">' in page and "<caption>Other hosts</caption>" in page
    assert page.count("10.20.30.49") == 1                                            # folded, in the table


def test_hostile_text_is_escaped() -> None:
    page = render(snap([host("10.20.30.9", hostname='<script>alert(1)</script>', maker='"><img src=x>')]))
    assert "<script>alert" not in page and "<img src=x>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_an_old_scan_reads_old_and_a_failure_says_why() -> None:
    page = render(snap([host("10.20.30.1")], scanned_at=NOW - 2 * 3600,
                       result={"ok": False, "finished_at": NOW - 30, "requested_by": "bob",
                               "message": "scan refused: a scan is already running"}))
    assert '<section class="site old"' in page and "Scanned 2 h ago" in page
    assert 'class="fail">Last scan at' in page and "by bob: scan refused: a scan is already running" in page
    assert f'data-last-scan=":{NOW - 30}"' in page


def test_a_failure_older_than_the_scan_is_not_shown() -> None:
    page = render(snap([host("10.20.30.1")], result={"ok": False, "finished_at": NOW - 900,
                                                     "requested_by": "bob", "message": "old news"}))
    assert "old news" not in page


def test_a_running_scan_replaces_the_slider_until_its_result() -> None:
    req = {"request_id": "r2", "requested_by": "bob", "door": "tailnet", "received_at": NOW - 40}
    page = render(snap([host("10.20.30.1")], request=req,
                       result={"request_id": "r1", "ok": True, "finished_at": NOW - 300, "message": "ok"}))
    assert "Scanning since" in page and ", by bob</span>" in page and "/actions/site/scan" not in page
    assert 'data-last-scan="r2:' in page
    page = render(snap([host("10.20.30.1")], request=req,
                       result={"request_id": "r2", "ok": True, "finished_at": NOW - 5, "message": "ok"}))
    assert "Scanning since" not in page and "/actions/site/scan" in page


def test_a_scan_with_no_result_stops_reading_as_running() -> None:
    req = {"request_id": "r2", "requested_by": "bob", "door": "tailnet", "received_at": NOW - ov.SCAN_AWAIT_S - 1}
    page = render(snap(request=req))
    assert "Scanning since" not in page and "No result for the scan requested at" in page
    assert "/actions/site/scan" in page


def test_a_tailnet_node_is_flagged_by_its_direct_endpoint() -> None:
    s = snap([host("10.20.30.9"), host("10.20.30.38", maker="private MAC", found_by="arp")])
    s["tailnet_endpoints"] = {"10.20.30.38": {"node_name": "alice-laptop.tail1234.ts.net",
                                              "login": "alice@example.com", "tags": []}}
    t = tiles(render(s))
    assert 'class="tile tail arp"' in t[0] and "Tailnet: alice-laptop</span>" in t[0]
    assert "alice@example.com" in t[0]                    # in the title, for whoever hovers
    assert "Tailnet:" not in t[1]


def test_tiles_are_in_address_order_after_the_flagged() -> None:
    cams = {"A": {"camera_id": "X", "address": "10.20.30.5", "label": "Lobby"}}
    t = tiles(render(snap([host("10.20.30.9"), host("10.20.30.30")], cameras=cams)))
    assert ["10.20.30.5" in t[0], "10.20.30.9" in t[1], "10.20.30.30" in t[2]] == [True, True, True]


# -- the Scan action through the real doors --------------------------------------------------

class FakeOverlay:
    def read(self) -> OverlaySnapshot:
        return OverlaySnapshot(hostname="cn-test", connected=True, addresses=["100.64.0.7"],
                               peers=[OverlayPeer(addresses=["100.64.0.10"], node_name="alice-laptop.ts.net",
                                                  login="alice@example.com", endpoint="10.20.30.21")])


class FakeNetworks:
    def read(self) -> list[str]:
        return [NET]


class FakeScanner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def scan(self, networks):
        self.calls.append(networks)
        return ScanReply(ok=True, message="scanned", self_addresses=["10.20.30.2"],
                         hosts=[interfaces.SiteHost(address="10.20.30.21", maker="Axis Communications AB",
                                                    axis_maker=True)])


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
    scanner = FakeScanner()
    node = main.compose(Bus(), overlay_source=FakeOverlay(), dashboard_port=0, dashboard_host="127.0.0.1",
                        config_root=str(tmp_path), site_networks=FakeNetworks(), site_scanner=scanner,
                        scan_spawn=lambda work: work())
    _, port = node.dashboard.start()
    main.tick(node)
    yield node, scanner, http.client.HTTPConnection("127.0.0.1", port, timeout=5), \
        {"Origin": f"http://127.0.0.1:{port}"}
    node.dashboard.stop()


def test_scan_needs_confirmation_then_the_map_shows_the_result(served) -> None:
    node, scanner, conn, origin = served
    status, _, body = call(conn, "POST", "/actions/site/scan", {"then": "/"}, origin)
    assert status == 409 and "check with site IT first" in body and scanner.calls == []
    status, headers, _ = call(conn, "POST", "/actions/site/scan", {"confirm": "yes", "then": "/"}, origin)
    assert status == 303 and headers["location"] == "/"
    assert scanner.calls == [[NET]]
    assert "Scanning since" in call(conn, "GET", "/")[2]   # the outcome isn't drained until the tick
    main.tick(node)                                       # the outcome is drained and published
    page = call(conn, "GET", "/")[2]
    assert "by local: 1 host answered on 10.20.30.0/24." in page
    assert "Axis by maker" in page and "10.20.30.21" in page
    assert "Tailnet: alice-laptop</span>" in page          # peer-report → peer-directory → the map
    assert re.search(r'data-last-scan="[0-9a-f]{12}:\d', page) and "Scanning since" not in page


def test_scan_without_the_same_origin_is_refused(served) -> None:
    _, scanner, conn, _ = served
    status, _, _ = call(conn, "POST", "/actions/site/scan", {"confirm": "yes"}, {"Origin": "http://evil.example"})
    assert status == 403 and scanner.calls == []
