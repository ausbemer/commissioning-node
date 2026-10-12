"""Tailnet peer table → caller identity (ADR-0003 §2), and the freshness authority under it.

Identity is a renewed level that fails toward deny: every way the table can be missing, old,
malformed or contradictory must resolve to nobody, with a reason.

NOTE: the adapter sample follows the documented `tailscale status --json` shape for Peer and
User entries; the Peer/User entry shape is NOT yet verified against 1.102.5 on a logged-in
node (docs/quirks.md). The top-level keys were verified logged out.
"""
from __future__ import annotations

import json
import math
import subprocess

import pytest

import interfaces
import main
from bus import Bus
from freshness import STALE_MAX_S, STALE_MIN_S, is_fresh, stale_after
from modules.dashboard.features.peer_directory import PeerDirectoryFeed
from modules.dashboard.interface import DashboardState, PeerDirectory, PeerIdentity, Unresolved
from modules.stream_relay.features.relay_planner import RelayPlanner
from modules.tailnet_gateway.core.tailscale_cli import TailscaleCliStatusSource
from modules.tailnet_gateway.features.peer_report import PeerReport
from modules.tailnet_gateway.interface import OverlayPeer, OverlaySnapshot

V = interfaces.__version__


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def peers_msg(*peers: interfaces.TailnetPeer, period_s: float = 5.0) -> interfaces.TailnetPeers:
    return interfaces.TailnetPeers.stamped(V, observed_at=0.0, period_s=period_s, peers=list(peers))


ALICE = interfaces.TailnetPeer(addresses=["100.64.0.10", "fd7a:115c:a1e0::10"],
                               node_name="alice-laptop.tail1234.ts.net", login="alice@example.com")
CI_BOX = interfaces.TailnetPeer(addresses=["100.64.0.20"], node_name="ci.tail1234.ts.net",
                                tags=["tag:ci"])


# --- freshness: the one staleness authority ---------------------------------------------

def test_window_is_three_periods_clamped() -> None:
    assert stale_after(5.0) == 15.0
    assert stale_after(0.5) == STALE_MIN_S
    assert stale_after(1000.0) == STALE_MAX_S


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, 0.0, -5.0])
def test_nonsense_cadence_gets_the_strictest_window(bad: float) -> None:
    # NaN compares false against every age: without the guard a NaN period never goes stale.
    assert stale_after(bad) == STALE_MIN_S


def test_unknown_receipt_or_cadence_is_not_fresh() -> None:
    assert not is_fresh(None, 5.0, 0.0)
    assert not is_fresh(0.0, None, 0.0)
    assert is_fresh(0.0, 5.0, 15.0) and not is_fresh(0.0, 5.0, 15.1)
    assert not is_fresh(10.0, 5.0, 9.0)        # received "in the future": a clock went backwards


def test_relay_planner_nan_cadence_still_goes_stale() -> None:
    clock = Clock(0.0)
    planner = RelayPlanner(clock=clock)
    planner.handle(interfaces.SYSTEM_TICK,
                   interfaces.Tick.stamped(V, at=0.0, period_s=math.nan).model_dump())
    planner.handle(interfaces.TAILNET_STATUS, interfaces.TailnetStatus.stamped(
        V, hostname="cn", connected=True, addresses=["100.64.0.9"]).model_dump())
    clock.now = STALE_MIN_S + 1
    camera = interfaces.CameraAnnouncement.stamped(V, camera_id="C1", address="10.0.0.2", origin="t")
    out = planner.handle(interfaces.CAMERA_ANNOUNCED, camera.model_dump())
    assert out[-1][1]["endpoints"] == []


# --- tailnet-gateway: adapter and peer-report -------------------------------------------

SAMPLE = {
    "BackendState": "Running",
    "Self": {"HostName": "cn-01", "DNSName": "cn-01.tail1234.ts.net.", "TailscaleIPs": ["100.64.0.7"],
             "UserID": 1},
    "Peer": {
        "nodekey:a": {"HostName": "alice-laptop", "DNSName": "alice-laptop.tail1234.ts.net.",
                      "TailscaleIPs": ["100.64.0.10", "fd7a:115c:a1e0::10"], "UserID": 1},
        "nodekey:b": {"HostName": "ci", "DNSName": "ci.tail1234.ts.net.",
                      "TailscaleIPs": ["100.64.0.20"], "UserID": 99, "Tags": ["tag:ci"]},
        "nodekey:c": {"HostName": "", "DNSName": "", "TailscaleIPs": ["100.64.0.30"], "UserID": 1},
    },
    "User": {"1": {"ID": 1, "LoginName": "alice@example.com"},
             "99": {"ID": 99, "LoginName": "tagged-devices"}},
}


def read(sample: dict) -> OverlaySnapshot:
    def run(cmd, **kw):
        assert cmd[1:] == ["status", "--json"]          # still read-only
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(sample), stderr="")
    return TailscaleCliStatusSource(run=run, which=lambda b: b).read()


def test_adapter_maps_peers_and_tagged_peers_get_no_login() -> None:
    by_name = {p.node_name: p for p in read(SAMPLE).peers}
    assert set(by_name) == {"alice-laptop.tail1234.ts.net", "ci.tail1234.ts.net"}   # nameless skipped
    alice, ci = by_name["alice-laptop.tail1234.ts.net"], by_name["ci.tail1234.ts.net"]
    assert alice.login == "alice@example.com" and alice.tags == []
    assert alice.addresses == ["100.64.0.10", "fd7a:115c:a1e0::10"]
    assert ci.login is None and ci.tags == ["tag:ci"]   # never the "tagged-devices" pseudo-user


def test_logged_out_status_has_no_peers() -> None:
    # Verified on tailscale 1.102.5 logged out: Peer and User are null.
    snap = read({"BackendState": "NeedsLogin", "Self": {"HostName": "cn-01"}, "Peer": None, "User": None})
    assert snap.connected is False and snap.peers == []


def test_peer_report_needs_a_cadence_and_publishes_a_snapshot() -> None:
    peer = OverlayPeer(addresses=["100.64.0.10"], node_name="a.ts.net", login="a@x")
    unstamped = OverlaySnapshot(hostname="cn", connected=True, peers=[peer])
    assert PeerReport().handle("x", unstamped.model_dump()) == []

    stamped = unstamped.model_copy(update={"observed_at": 12.0, "period_s": 5.0})
    [(topic, payload)] = PeerReport().handle("x", stamped.model_dump())
    msg = interfaces.TailnetPeers.model_validate(payload)
    assert topic == interfaces.TAILNET_PEERS and msg.schema_version == V
    assert (msg.observed_at, msg.period_s) == (12.0, 5.0)
    assert [p.login for p in msg.peers] == ["a@x"]


# --- dashboard: PeerDirectory -----------------------------------------------------------

def directory(*peers: interfaces.TailnetPeer) -> tuple[PeerDirectory, Clock]:
    clock = Clock()
    d = PeerDirectory(clock=clock)
    d.update(peers_msg(*peers))
    return d, clock


def test_fresh_known_address_resolves_with_label() -> None:
    d, _ = directory(ALICE, CI_BOX)
    alice = d.resolve("100.64.0.10")
    assert isinstance(alice, PeerIdentity) and alice.label == "alice@example.com"
    assert d.resolve("fd7a:115c:a1e0::10") == alice
    ci = d.resolve("100.64.0.20")
    assert isinstance(ci, PeerIdentity) and ci.login is None and ci.label == "ci.tail1234.ts.net"


def test_socket_address_forms_resolve() -> None:
    d, _ = directory(ALICE)
    assert isinstance(d.resolve("::ffff:100.64.0.10"), PeerIdentity)      # dual-stack socket
    assert isinstance(d.resolve("fd7a:115c:a1e0::10%tailscale0"), PeerIdentity)


@pytest.mark.parametrize("address,reason", [("100.64.0.99", "unknown"), ("not-an-ip", "invalid"),
                                             ("192.168.36.20", "unknown")])
def test_unknown_or_invalid_addresses_resolve_to_nobody(address: str, reason: str) -> None:
    d, _ = directory(ALICE)
    out = d.resolve(address)
    assert isinstance(out, Unresolved) and out.reason == reason and out.message


def test_never_received_is_stale() -> None:
    out = PeerDirectory(clock=Clock()).resolve("100.64.0.10")
    assert isinstance(out, Unresolved) and out.reason == "stale"


def test_table_lapses_after_the_derived_window() -> None:
    d, clock = directory(ALICE)                 # period 5 s -> window 15 s
    clock.now += 15.0
    assert isinstance(d.resolve("100.64.0.10"), PeerIdentity)
    clock.now += 0.1
    out = d.resolve("100.64.0.10")
    assert isinstance(out, Unresolved) and out.reason == "stale"


def test_update_replaces_the_whole_table() -> None:
    d, _ = directory(ALICE, CI_BOX)
    d.update(peers_msg(CI_BOX))                 # alice left the tailnet
    assert isinstance(d.resolve("100.64.0.10"), Unresolved)
    assert isinstance(d.resolve("100.64.0.20"), PeerIdentity)


def test_an_address_claimed_twice_resolves_to_nobody() -> None:
    impostor = interfaces.TailnetPeer(addresses=["100.64.0.10"], node_name="x.ts.net", login="x@y")
    d, _ = directory(ALICE, impostor)
    out = d.resolve("100.64.0.10")
    assert isinstance(out, Unresolved) and out.reason == "ambiguous"
    assert isinstance(d.resolve("fd7a:115c:a1e0::10"), PeerIdentity)      # alice's other address


def test_malformed_snapshot_is_dropped_and_old_table_ages_out() -> None:
    d, clock = directory(ALICE)
    feed = PeerDirectoryFeed(d, DashboardState())
    assert feed.handle(interfaces.TAILNET_PEERS, {"schema_version": V, "peers": "nope"}) == []
    clock.now += 16.0
    assert isinstance(d.resolve("100.64.0.10"), Unresolved)


# --- end to end over the real bus -------------------------------------------------------

class Overlay:
    def read(self) -> OverlaySnapshot:
        return OverlaySnapshot(hostname="cn-test", connected=True, addresses=["100.64.0.7"],
                               peers=[OverlayPeer(addresses=["100.64.0.10"],
                                                  node_name="alice-laptop.tail1234.ts.net",
                                                  login="alice@example.com")])


def test_tick_publishes_peers_and_the_dashboard_resolves_them() -> None:
    node = main.compose(Bus(), overlay_source=Overlay(), clock=Clock(), dashboard_port=0)
    main.tick(node)
    who = node.dashboard._peers.resolve("100.64.0.10")
    assert isinstance(who, PeerIdentity) and who.label == "alice@example.com"
    assert node.dashboard._state.snapshot()["wire"][interfaces.TAILNET_PEERS] == V


def test_malformed_tick_skips_the_reading_without_raising() -> None:
    node = main.compose(Bus(), overlay_source=Overlay(), clock=Clock(), dashboard_port=0)
    seen: list[str] = []
    node.bus.subscribe(interfaces.TAILNET_PEERS, lambda t, p: seen.append(t))
    node.bus.publish(interfaces.SYSTEM_TICK, {"schema_version": V, "at": 0.0})   # no period_s
    assert seen == []
    assert isinstance(node.dashboard._peers.resolve("100.64.0.10"), Unresolved)
