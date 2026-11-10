"""nav-relay (ADR-0010): the NMEA 2000 decoder, the NMEA 0183 encoder, the freshness and source
policy, the destination rule, and the whole path over the real bus with a fake CAN source and
UDP sink.

The frames are built here from the standard's field layouts. There is no recording from a real
vessel yet (the replay corpus is step 1 of ADR-0010's implementation, on the node). No real
position is recorded here.
"""
from __future__ import annotations

import ast
import calendar
import inspect
import math
import struct

import pytest

import interfaces
import main
from bus import Bus
from modules.nav_relay.core import n2k, nmea0183, socketcan_source
from modules.nav_relay.core.socketcan_source import SocketCanSource, listen_only
from modules.nav_relay.core.workers import Reader, Sender
from modules.nav_relay.interface import (ALLOWLIST, LINK_UP, MAX_DESTINATIONS, LinkReport,
                                         NavSource, Reading, current_fix, destination_problem)
from modules.tailnet_gateway.interface import OverlaySnapshot

NET = "10.20.30.0/24"
SELF = "10.20.30.2"
PLOTTER = "10.20.30.50"


# -- frames, built from the standard's layouts --------------------------------------------------

def can_id(pgn: int, source: int, priority: int = 2) -> int:
    return (priority << 26) | (pgn << 8) | source


def position(lat: float, lon: float) -> bytes:
    return struct.pack("<ii", round(lat * 1e7), round(lon * 1e7))


def cog_sog(cog_deg: float, sog_ms: float, magnetic: bool = False) -> bytes:
    return struct.pack("<BBHHH", 7, 0xFC | (1 if magnetic else 0),
                       round(math.radians(cog_deg) * 1e4), round(sog_ms * 100), 0xFFFF)


def vessel_heading(heading_deg: float, magnetic: bool = False, deviation_deg: float | None = None,
                   variation_deg: float | None = None) -> bytes:
    dev = 0x7FFF if deviation_deg is None else round(math.radians(deviation_deg) * 1e4)
    var = 0x7FFF if variation_deg is None else round(math.radians(variation_deg) * 1e4)
    return struct.pack("<BHhhB", 3, round(math.radians(heading_deg) * 1e4), dev, var,
                       0xFC | (1 if magnetic else 0))


def system_time(unix: float) -> bytes:
    return struct.pack("<BBHI", 1, 0xF0, int(unix // 86400), round((unix % 86400) * 1e4))


class FakeSource:
    """A link that is up, with frames queued by the test."""

    def __init__(self, report: LinkReport = LinkReport(LINK_UP)) -> None:
        self.report, self.frames, self.closed = report, [], 0

    def check_link(self) -> LinkReport:
        return self.report

    def receive(self, timeout_s: float):
        return self.frames.pop(0) if self.frames else None

    def close(self) -> None:
        self.closed += 1


class FakeSink:
    def __init__(self) -> None:
        self.sent: list[tuple[str, int, bytes]] = []
        self.fail: OSError | None = None

    def send(self, address: str, port: int, payload: bytes) -> None:
        if self.fail:
            raise self.fail
        self.sent.append((address, port, payload))


class FakeOverlay:
    def __init__(self, snapshot: OverlaySnapshot) -> None:
        self.snapshot = snapshot

    def read(self) -> OverlaySnapshot:
        return self.snapshot


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


# -- the decoder --------------------------------------------------------------------------------

def test_pgn_and_source_come_from_the_29_bit_id() -> None:
    for pgn in ALLOWLIST:
        assert n2k.pgn_of(can_id(pgn, 35, priority=3)) == pgn
        assert n2k.source_of(can_id(pgn, 35)) == 35
    # A PDU1 PGN carries a destination in its low byte; it isn't part of the PGN.
    assert n2k.pgn_of(can_id(59904, 0) | 0x2100) == 59904


def test_kernel_filters_match_exactly_the_allowlist() -> None:
    raw = n2k.kernel_filters()
    pairs = [struct.unpack_from("=II", raw, i) for i in range(0, len(raw), 8)]
    assert len(pairs) == len(ALLOWLIST)
    for (fid, mask), pgn in zip(pairs, ALLOWLIST):
        assert fid == (pgn << 8) | n2k.CAN_EFF_FLAG
        assert mask & n2k.CAN_RTR_FLAG and mask & n2k.CAN_EFF_FLAG
        assert mask & 0xFF == 0                        # any source
        assert (can_id(pgn, 99) | n2k.CAN_EFF_FLAG) & mask == fid


def test_parse_frame_keeps_only_extended_data_frames() -> None:
    def raw(cid: int, data: bytes = b"\x00" * 8) -> bytes:
        return n2k.FRAME.pack(cid, len(data), data)
    cid = can_id(129025, 1)
    assert n2k.parse_frame(raw(cid | n2k.CAN_EFF_FLAG)) == (cid, b"\x00" * 8)
    assert n2k.parse_frame(raw(0x123)) is None                                    # standard id
    assert n2k.parse_frame(raw(cid | n2k.CAN_EFF_FLAG | n2k.CAN_RTR_FLAG)) is None
    assert n2k.parse_frame(raw(cid | n2k.CAN_EFF_FLAG | n2k.CAN_ERR_FLAG)) is None
    assert n2k.parse_frame(b"\x00" * 4) is None


def test_decode_each_allowlisted_pgn() -> None:
    p = n2k.decode(129025, position(48.1173, -11.5166667))
    assert p["lat"] == pytest.approx(48.1173) and p["lon"] == pytest.approx(-11.5166667)
    c = n2k.decode(129026, cog_sog(84.4, 11.52))
    assert c["reference"] == "true" and math.degrees(c["cog"]) == pytest.approx(84.4, abs=0.01)
    assert c["sog"] == pytest.approx(11.52)
    h = n2k.decode(127250, vessel_heading(270.0, magnetic=True, deviation_deg=-1.5, variation_deg=3.1))
    assert h["reference"] == "magnetic"
    assert math.degrees(h["heading"]) == pytest.approx(270.0, abs=0.01)
    assert math.degrees(h["deviation"]) == pytest.approx(-1.5, abs=0.01)
    assert math.degrees(h["variation"]) == pytest.approx(3.1, abs=0.01)
    t = n2k.decode(126992, system_time(1762790400.5))
    assert t["utc"] == pytest.approx(1762790400.5)


def test_not_available_decodes_to_none_never_a_value() -> None:
    p = n2k.decode(129025, struct.pack("<ii", 0x7FFFFFFF, 0x7FFFFFFF))
    assert p == {"lat": None, "lon": None}
    c = n2k.decode(129026, struct.pack("<BBHHH", 0, 0xFF, 0xFFFF, 0xFFFF, 0xFFFF))
    assert c == {"reference": None, "cog": None, "sog": None}
    h = n2k.decode(127250, struct.pack("<BHhhB", 0, 0xFFFF, 0x7FFF, 0x7FFF, 0xFF))
    assert h == {"heading": None, "deviation": None, "variation": None, "reference": None}
    assert n2k.decode(126992, struct.pack("<BBHI", 0, 0, 0xFFFF, 0xFFFFFFFF)) == {"utc": None}
    # Out of range is not a value either.
    assert n2k.decode(129025, struct.pack("<ii", 95 * 10**7, 0))["lat"] is None


def test_decode_refuses_short_frames_and_other_pgns() -> None:
    assert n2k.decode(129025, b"\x00" * 7) is None
    assert n2k.decode(130306, b"\x00" * 8) is None


# -- the encoder --------------------------------------------------------------------------------

def test_checksum_matches_the_textbook_rmc() -> None:
    assert nmea0183.checksum("GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W") == "6A"


def test_gga_with_a_fix() -> None:
    fix = interfaces.NavFix(lat=48.1173, lon=-11.5166667,
                            utc=calendar.timegm((1994, 3, 23, 12, 35, 19)) + 0.25)
    s = nmea0183.gga(fix)
    body = "GPGGA,123519.25,4807.0380,N,01131.0000,W,1,,,,,,,,"
    assert s == f"${body}*{nmea0183.checksum(body)}\r\n"
    assert len(s) <= nmea0183.MAX_LEN


def test_gga_without_a_fix_is_quality_0_with_nothing_in_it() -> None:
    s = nmea0183.gga(interfaces.NavFix(sog_kn=3.0, cog_true_deg=10.0))
    assert s.startswith("$GPGGA,,,,,,0,,,,,,,,*")


def test_vtg() -> None:
    body = "GPVTG,84.4,T,,M,22.4,N,41.5,K,A"
    s = nmea0183.vtg(interfaces.NavFix(sog_kn=22.4, cog_true_deg=84.4))
    assert s == f"${body}*{nmea0183.checksum(body)}\r\n"
    assert nmea0183.vtg(interfaces.NavFix(sog_kn=1.0)).startswith("$GPVTG,,T,,M,1.0,N,1.9,K,A*")
    assert nmea0183.vtg(interfaces.NavFix()).startswith("$GPVTG,,T,,M,,N,,K,N*")


def test_rounding_never_prints_60_minutes_or_360_degrees() -> None:
    s = nmea0183.gga(interfaces.NavFix(lat=1.99999999, lon=-179.999999999))
    assert ",0200.0000,N,18000.0000,W," in s
    assert nmea0183.vtg(interfaces.NavFix(cog_true_deg=359.96)).startswith("$GPVTG,0.0,T,")
    assert nmea0183.hdt(interfaces.NavFix(heading_true_deg=359.97)).startswith("$HEHDT,0.0,T*")


def test_only_gga_vtg_and_hdt_go_out() -> None:
    assert nmea0183.hdt(interfaces.NavFix(heading_true_deg=274.07)).startswith("$HEHDT,274.1,T*")
    assert nmea0183.hdt(interfaces.NavFix(heading_mag_deg=12.0)) is None    # no HDG
    assert nmea0183.hdt(interfaces.NavFix()) is None
    full = nmea0183.datagram(interfaces.NavFix(lat=1.0, lon=2.0, sog_kn=3.0, cog_true_deg=4.0,
                                               heading_true_deg=5.0, heading_mag_deg=6.0,
                                               variation_deg=-1.0, utc=0.0)).decode()
    assert [line[1:6] for line in full.split("\r\n") if line] == ["GPGGA", "GPVTG", "HEHDT"]
    bare = nmea0183.datagram(interfaces.NavFix()).decode()
    assert [line[1:6] for line in bare.split("\r\n") if line] == ["GPGGA", "GPVTG"]


# -- the policy: freshness and source selection --------------------------------------------------

def reading(pgn: int, values: dict, at: float, source: int = 10) -> Reading:
    return Reading(pgn=pgn, source=source, values=values, received_at=at)


def test_a_fix_lapses_one_second_after_the_last_frame() -> None:
    r = [reading(129025, {"lat": 1.0, "lon": 2.0}, at=100.0)]
    assert current_fix(r, 100.9)[0].lat == 1.0
    assert current_fix(r, 101.1)[0].lat is None          # RAPID_MIN_S floor (R2)
    assert current_fix(r, 99.0)[0].lat is None           # a reading from the future is not fresh


def test_the_lowest_fresh_source_wins() -> None:
    r = [reading(129025, {"lat": 1.0, "lon": 1.0}, at=100.0, source=40),
         reading(129025, {"lat": 2.0, "lon": 2.0}, at=100.0, source=7),
         reading(129025, {"lat": 3.0, "lon": 3.0}, at=90.0, source=3)]   # lower, but stale
    fix, sources = current_fix(r, 100.5)
    assert fix.lat == 2.0
    assert {(s.source, s.selected) for s in sources} == {(3, False), (7, True), (40, False)}


def test_magnetic_becomes_true_only_with_a_fresh_variation() -> None:
    rad = math.radians
    mag = reading(127250, {"heading": rad(100), "deviation": rad(2), "variation": rad(-5),
                           "reference": "magnetic"}, at=100.0)
    fix, _ = current_fix([mag, reading(129026, {"reference": "magnetic", "cog": rad(90), "sog": 5.0}, 100.0)], 100.2)
    assert fix.heading_mag_deg == pytest.approx(102.0)
    assert fix.heading_true_deg == pytest.approx(97.0)
    assert fix.cog_true_deg == pytest.approx(85.0)
    assert fix.sog_kn == pytest.approx(9.72, abs=0.01)
    no_var = reading(127250, {"heading": rad(100), "deviation": None, "variation": None,
                              "reference": "magnetic"}, at=100.0)
    fix, _ = current_fix([no_var, reading(129026, {"reference": "magnetic", "cog": rad(90), "sog": 5.0}, 100.0)], 100.2)
    assert fix.heading_true_deg is None and fix.heading_mag_deg == pytest.approx(100.0)
    assert fix.cog_true_deg is None and fix.sog_kn is not None


# -- the destination rule ---------------------------------------------------------------------

def hosts(*addresses: str) -> interfaces.SiteHosts:
    return interfaces.SiteHosts.stamped(
        interfaces.__version__, scanned_at=1.0, networks=[NET], self_addresses=[SELF],
        hosts=[interfaces.SiteHost(address=a, mac="02:00:00:00:00:50") for a in addresses],
        origin="site-survey/scanner")


@pytest.mark.parametrize("address, port, why", [
    (PLOTTER, 2947, None),
    (None, 2947, "no address"),
    ("plotter.lan", 2947, "not an IPv4"),
    (PLOTTER, 0, "out of range"),
    ("100.101.102.103", 2947, "tailnet"),
    ("224.0.0.1", 2947, "single host"),
    ("255.255.255.255", 2947, "single host"),
    (SELF, 2947, "this node"),
    ("10.20.31.5", 2947, "not on a scanned network"),
    ("10.20.30.255", 2947, "broadcast"),
    ("10.20.30.77", 2947, "did not answer"),
])
def test_destination_problem(address, port, why) -> None:
    problem = destination_problem(address, port, hosts(PLOTTER, "10.20.30.255"))
    assert (problem is None) if why is None else (why in problem)


def test_no_scan_means_no_destination() -> None:
    assert "scan" in destination_problem(PLOTTER, 2947, None)


# -- rung 8: nothing is ever sent on the NMEA 2000 bus ---------------------------------------------

def test_the_can_seam_and_adapter_have_no_way_to_send() -> None:
    assert not any(hasattr(NavSource, m) for m in ("send", "write", "transmit"))
    assert not any(hasattr(SocketCanSource, m) for m in ("send", "write", "transmit"))
    tree = ast.parse(inspect.getsource(socketcan_source))
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not calls & {"send", "sendto", "sendmsg", "sendall", "write"}


def test_listen_only_is_read_from_ip_details() -> None:
    def link(modes):
        return ('[{"ifname":"n2k0","linkinfo":{"info_kind":"can","info_data":{"ctrlmode":%s,'
                '"state":"ERROR-ACTIVE"}}}]' % modes)
    assert listen_only(link('["LISTEN-ONLY"]')) is True
    assert listen_only(link('["3-SAMPLES"]')) is False
    assert listen_only('[{"ifname":"eth0","linkinfo":{"info_kind":"veth"}}]') is None
    assert listen_only("not json") is None


def test_the_adapter_reports_a_missing_adapter(tmp_path) -> None:
    report = SocketCanSource("n2k0", sysfs=str(tmp_path)).check_link()
    assert report.state == "no-adapter" and "kvaser_usb" in report.detail


# -- the whole path, over the real bus ------------------------------------------------------------

def make():
    clock, source, sink = Clock(), FakeSource(), FakeSink()
    overlay = FakeOverlay(OverlaySnapshot(hostname="cn-01", connected=True, addresses=["100.64.0.7"]))
    node = main.compose(Bus(), overlay_source=overlay, clock=clock, dashboard_port=0,
                        nav_source=source, nav_sink=sink)
    reader, sender = node.nav
    return node, clock, source, sink, reader, sender


def record(bus: Bus, topic: str) -> list[dict]:
    seen: list[dict] = []
    bus.subscribe(topic, lambda _t, p: seen.append(p))
    return seen


def ask(node, operation: str, address: str | None = PLOTTER, port: int = 2947) -> tuple[bool, str]:
    cmd = interfaces.NavDestination.stamped(interfaces.__version__, request_id="r1", requested_by="alice",
                                            door="tailnet", operation=operation, address=address, port=port)
    return main.invoke(node, interfaces.NAV_DESTINATION, cmd.model_dump(), "nav-relay:sender")


def feed(reader: Reader, source: FakeSource, frames: list[tuple[int, bytes]]) -> None:
    source.frames.extend(frames)
    for _ in range(len(frames) + 1):
        reader.step()


def test_nothing_is_sent_until_an_operator_picks_a_scanned_host() -> None:
    node, clock, source, sink, reader, sender = make()
    results = record(node.bus, interfaces.NAV_DESTINATION_RESULT)
    feed(reader, source, [(can_id(129025, 10), position(1.5, 2.5))])
    sender.step()
    assert sink.sent == []                                        # rung 3: nobody picked

    ask(node, "send")
    assert results[-1]["ok"] is False and "scan" in results[-1]["message"]
    node.bus.publish(interfaces.SITE_HOSTS, hosts(PLOTTER).model_dump())
    ask(node, "send", address="10.20.30.99")
    assert results[-1]["ok"] is False and "did not answer" in results[-1]["message"]
    ask(node, "send")
    assert results[-1]["ok"] is True
    sender.step()
    (address, port, payload), = sink.sent
    assert (address, port) == (PLOTTER, 2947)
    assert payload.decode().startswith("$GPGGA,,0130.0000,N,00230.0000,E,1,")


def test_a_stale_position_is_sent_as_no_fix() -> None:
    node, clock, source, sink, reader, sender = make()
    node.bus.publish(interfaces.SITE_HOSTS, hosts(PLOTTER).model_dump())
    ask(node, "send")
    feed(reader, source, [(can_id(129025, 10), position(1.5, 2.5)),
                          (can_id(127250, 10), vessel_heading(45.0))])
    clock.t += 1.5
    sender.step()
    text = sink.sent[-1][2].decode()
    assert text.startswith("$GPGGA,,,,,,0,") and "$GPVTG,,T,,M,,N,,K,N*" in text and "HDT" not in text


def test_stop_and_the_destination_limit() -> None:
    node, clock, source, sink, reader, sender = make()
    results = record(node.bus, interfaces.NAV_DESTINATION_RESULT)
    many = [f"10.20.30.{50 + i}" for i in range(MAX_DESTINATIONS + 1)]
    node.bus.publish(interfaces.SITE_HOSTS, hosts(*many).model_dump())
    for a in many:
        ask(node, "send", address=a)
    assert results[-1]["ok"] is False and "stop one first" in results[-1]["message"]
    ask(node, "send", address=many[0])
    assert "already sending" in results[-1]["message"]
    ask(node, "stop", address=many[0])
    sender.step()
    assert len(sink.sent) == MAX_DESTINATIONS - 1 and many[0] not in {a for a, _, _ in sink.sent}
    ask(node, "stop", address=None)
    sink.sent.clear()
    sender.step()
    assert sink.sent == []


def test_status_reports_link_fix_sources_and_send_errors() -> None:
    node, clock, source, sink, reader, sender = make()
    statuses = record(node.bus, interfaces.NAV_STATUS)
    node.bus.publish(interfaces.SITE_HOSTS, hosts(PLOTTER).model_dump())
    ask(node, "send")
    feed(reader, source, [(can_id(129025, 10), position(1.5, 2.5)),
                          (can_id(129025, 20), position(9.0, 9.0))])
    sink.fail = OSError(113, "No route to host")
    sender.step()
    main.tick(node)
    status = interfaces.NavStatus.model_validate(statuses[-1])
    assert status.link == "up" and status.fix.lat == 1.5
    assert [(s.source, s.selected) for s in status.sources] == [(10, True), (20, False)]
    dest, = status.destinations
    assert dest.sent == 0 and dest.last_error == "No route to host" and dest.mac == "02:00:00:00:00:50"
    assert status.period_s == main.TICK_PERIOD_S


def test_a_link_that_is_not_listen_only_is_never_read() -> None:
    node, clock, source, sink, reader, sender = make()
    source.report = LinkReport("not-listen-only", "n2k0 is not listen-only")
    source.frames.append((can_id(129025, 10), position(1.5, 2.5)))
    reader.step()
    assert source.frames and reader._state.readings() == []


def test_the_dashboard_shows_the_feed_and_offers_scanned_hosts() -> None:
    node, clock, source, sink, reader, sender = make()
    node.bus.publish(interfaces.SITE_HOSTS, hosts(PLOTTER).model_dump())
    feed(reader, source, [(can_id(129025, 10), position(1.5, 2.5))])
    main.tick(node)
    from modules.dashboard.features.overview_page import OverviewPage
    snap = node.dashboard._state.snapshot()
    html = OverviewPage().render({**snap, "viewer": {"csrf": "t"}})
    assert "Nav feed" in html and "1.50000°N" in html
    assert f'<option value="{PLOTTER}">' in html and f'<option value="{SELF}"' not in html
    assert 'action="/actions/nav/send"' in html and "data-hold" in html
