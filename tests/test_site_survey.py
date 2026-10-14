"""site-survey (ADR-0007): the nmap adapter, the one-at-a-time worker, and the scanner's rules
over the real bus.

The adapter runs against made-up nmap XML and neighbour tables shaped like the ones measured on
2026-10-13 (quirks Q-006, Q-007). No real site's hosts are recorded here (Safety 6). The argv is
a golden (§8.12: what we send to the world is pinned).
"""
from __future__ import annotations

import json
import subprocess

import pytest

import interfaces
import main
from bus import Bus
from modules.site_survey.core import nmap_scanner as ns
from modules.site_survey.core.nmap_scanner import NmapRun, NmapScanner, is_axis, maker_for, parse_nmap_xml
from modules.site_survey.interface import ScanReply, scan_problem
from modules.tailnet_gateway.interface import OverlaySnapshot

NET = "10.20.30.0/24"
SELF = "10.20.30.2"

XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE nmaprun>
<nmaprun scanner="nmap" version="7.95" xmloutputversion="1.05">
<scaninfo type="connect" protocol="tcp" numservices="3" services="80,443,554"/>
<hosthint><status state="up" reason="unknown-response" reason_ttl="0"/>
<address addr="10.20.30.1" addrtype="ipv4"/><hostnames></hostnames></hosthint>
<host><status state="up" reason="syn-ack" reason_ttl="0"/>
<address addr="10.20.30.1" addrtype="ipv4"/>
<hostnames><hostname name="router.lan" type="PTR"/></hostnames>
<ports><port protocol="tcp" portid="80"><state state="open" reason="syn-ack" reason_ttl="0"/></port>
<port protocol="tcp" portid="443"><state state="closed" reason="conn-refused" reason_ttl="0"/></port>
<port protocol="tcp" portid="554"><state state="closed" reason="conn-refused" reason_ttl="0"/></port></ports>
</host>
<host><status state="up" reason="syn-ack" reason_ttl="0"/>
<address addr="10.20.30.21" addrtype="ipv4"/>
<hostnames><hostname name="cam&#x202E;-gate3" type="PTR"/></hostnames>
<ports><port protocol="tcp" portid="80"><state state="open" reason="syn-ack" reason_ttl="0"/></port>
<port protocol="tcp" portid="443"><state state="open" reason="syn-ack" reason_ttl="0"/></port>
<port protocol="tcp" portid="554"><state state="open" reason="syn-ack" reason_ttl="0"/></port></ports>
</host>
<host><status state="up" reason="conn-refused" reason_ttl="0"/>
<address addr="10.20.30.40" addrtype="ipv4"/><hostnames></hostnames>
<ports><port protocol="tcp" portid="80"><state state="closed" reason="conn-refused" reason_ttl="0"/></port></ports>
</host>
<host><status state="down" reason="no-response" reason_ttl="0"/>
<address addr="10.20.30.99" addrtype="ipv4"/><hostnames></hostnames></host>
<runstats><finished elapsed="23.62" exit="success"/><hosts up="3" down="252" total="255"/></runstats>
</nmaprun>
"""

NEIGH = [
    {"dst": "10.20.30.1", "dev": "eth0", "lladdr": "04:42:1a:00:00:01", "state": ["REACHABLE"]},
    {"dst": "10.20.30.21", "dev": "eth0", "lladdr": "AC:CC:8E:12:34:56", "state": ["REACHABLE"]},
    {"dst": "10.20.30.40", "dev": "eth0", "lladdr": "da:00:00:00:00:40", "state": ["DELAY"]},
    # Answered ARP, dropped every probe (Q-006): only the neighbour table knows it.
    {"dst": "10.20.30.50", "dev": "eth0", "lladdr": "70:b3:d5:98:70:01", "state": ["STALE"]},
    {"dst": "10.20.30.60", "dev": "eth0", "state": ["FAILED"]},
    {"dst": "10.20.30.61", "dev": "eth0", "lladdr": "00:24:24:00:00:61", "state": ["INCOMPLETE"]},
    {"dst": "192.168.99.5", "dev": "wlan0", "lladdr": "00:11:22:33:44:55", "state": ["REACHABLE"]},
]
ADDRS = [{"ifname": "lo", "addr_info": [{"local": "127.0.0.1", "prefixlen": 8}]},
         {"ifname": "eth0", "addr_info": [{"local": SELF, "prefixlen": 24}]},
         {"ifname": "tailscale0", "addr_info": [{"local": "100.64.0.7", "prefixlen": 32}]}]
PREFIXES = {"04421A": "ASUSTek Computer", "ACCC8E": "Axis Communications AB",
            "70B3D5987": "Axis", "002424": "Ace Axis Limited"}


class Nmap:
    def __init__(self, run: NmapRun | None = None, raises: Exception | None = None):
        self.argv: list[list[str]] = []
        self.run = run or NmapRun(0, XML, b"")
        self.raises = raises

    def __call__(self, argv):
        self.argv.append(argv)
        if self.raises:
            raise self.raises
        return self.run


def ip_run(argv, **kw):
    data = NEIGH if "neigh" in argv else ADDRS
    return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")


def adapter(nmap: Nmap, which=lambda b: f"/usr/bin/{b}") -> NmapScanner:
    return NmapScanner(run=nmap, run_ip=ip_run, which=which, prefixes=lambda: PREFIXES)


# -- the adapter ---------------------------------------------------------------------------

def test_argv_is_the_gentle_profile() -> None:
    nmap = Nmap()
    adapter(nmap).scan([NET])
    assert nmap.argv == [["nmap", "--unprivileged", "-sT", "-PS80,443,554", "-p", "80,443,554",
                          "--max-rate", "100", "--max-retries", "1", "--host-timeout", "15s",
                          "-T3", "-oX", "-", "--exclude", SELF, NET]]


def test_parse_reads_hosts_only_not_hosthints_or_down_hosts() -> None:
    hosts = parse_nmap_xml(XML)
    assert sorted(hosts) == ["10.20.30.1", "10.20.30.21", "10.20.30.40"]
    assert hosts["10.20.30.1"].open_ports == [80]
    assert hosts["10.20.30.21"].open_ports == [80, 443, 554]
    assert hosts["10.20.30.40"].open_ports == []
    assert hosts["10.20.30.21"].hostname == "cam-gate3"          # a format character (RTL override) dropped


def test_scan_merges_the_neighbour_table() -> None:
    reply = adapter(Nmap()).scan([NET])
    assert reply.ok, reply.message
    by = {h.address: h for h in reply.hosts}
    assert list(by) == ["10.20.30.1", "10.20.30.21", "10.20.30.40", "10.20.30.50"]   # sorted
    assert reply.self_addresses == [SELF] and SELF not in by
    assert by["10.20.30.1"].maker == "ASUSTek Computer" and not by["10.20.30.1"].axis_maker
    assert by["10.20.30.21"].mac == "ac:cc:8e:12:34:56" and by["10.20.30.21"].axis_maker
    assert by["10.20.30.40"].maker == ns.PRIVATE_MAC                     # Q-007
    assert by["10.20.30.50"].found_by == "arp" and by["10.20.30.50"].axis_maker   # Q-006, MA-S
    assert all(h.found_by == "tcp" for a, h in by.items() if a != "10.20.30.50")
    assert reply.message == f"scanned {NET}: 3 hosts answered a probe, 1 more answered only ARP"


@pytest.mark.parametrize("mac, maker", [
    ("ac:cc:8e:00:00:01", "Axis Communications AB"),
    ("70:b3:d5:98:7f:ff", "Axis"),                  # 9-digit prefix beats nothing
    ("00:24:24:00:00:01", "Ace Axis Limited"),
    ("02:00:00:00:00:01", ns.PRIVATE_MAC),
    ("00:00:00:00:00:01", None),
    ("nonsense", None),
    (None, None),
])
def test_maker_lookup(mac, maker) -> None:
    assert maker_for(mac, PREFIXES) == maker


def test_only_axis_itself_is_axis() -> None:
    assert is_axis("Axis Communications AB") and is_axis("Axis")
    assert not is_axis("Ace Axis Limited") and not is_axis(None) and not is_axis("")


@pytest.mark.parametrize("networks", [[], ["10.0.0.0/16"], ["100.100.1.0/24"], ["8.8.8.0/24"],
                                      ["10.20.30.5/24"], ["-oN/tmp/x"]])
def test_adapter_rechecks_the_networks_and_never_runs_nmap(networks) -> None:
    nmap = Nmap()
    reply = adapter(nmap).scan(networks)
    assert not reply.ok and reply.message.startswith("scan refused: ")
    assert nmap.argv == []


@pytest.mark.parametrize("nmap, which, said", [
    (Nmap(), lambda b: None, "nmap is not installed"),
    (Nmap(raises=subprocess.TimeoutExpired("nmap", 300)), None, "did not finish within 300 s"),
    (Nmap(raises=FileNotFoundError(2, "No such file")), None, "could not run"),
    (Nmap(NmapRun(1, b"", b"Failed to resolve \"x\"")), None, "nmap: Failed to resolve"),
    (Nmap(NmapRun(0, XML[: len(XML) // 2], b"")), None, "unreadable"),
])
def test_failures_say_why(nmap, which, said) -> None:
    reply = (adapter(nmap, which) if which else adapter(nmap)).scan([NET])
    assert not reply.ok and said in reply.message and reply.hosts == []


def test_scan_problem_is_the_one_rule() -> None:
    assert scan_problem([NET]) is None
    assert scan_problem(["192.168.0.0/22"]) is None
    assert "wider than /22" in scan_problem(["192.168.0.0/21"])


# -- the scanner over the real bus ---------------------------------------------------------

class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeOverlay:
    def read(self) -> OverlaySnapshot:
        return OverlaySnapshot(hostname="cn-test", connected=True, addresses=["100.64.0.7"])


class FakeNetworks:
    def __init__(self, nets: list[str]) -> None:
        self.nets = nets

    def read(self) -> list[str]:
        return list(self.nets)


class FakeScanner:
    def __init__(self, reply: ScanReply | None = None) -> None:
        self.calls: list[list[str]] = []
        self.reply = reply or ScanReply(ok=True, message="scanned", self_addresses=[SELF],
                                        hosts=[interfaces.SiteHost(address="10.20.30.21", axis_maker=True)])

    def scan(self, networks: list[str]) -> ScanReply:
        self.calls.append(networks)
        return self.reply


class Deferred:
    """A spawn that holds the work until `run_all`: a scan that is still running."""
    def __init__(self) -> None:
        self.pending: list = []

    def __call__(self, work) -> None:
        self.pending.append(work)

    def run_all(self) -> None:
        while self.pending:
            self.pending.pop(0)()


def make(nets=(NET,), scanner=None, spawn=None):
    clock = Clock()
    scanner = scanner or FakeScanner()
    node = main.compose(Bus(), overlay_source=FakeOverlay(), clock=clock, dashboard_port=0,
                        site_networks=FakeNetworks(list(nets)), site_scanner=scanner,
                        scan_spawn=spawn or (lambda work: work()))
    seen: list[tuple[str, dict]] = []
    node.bus.subscribe("site/*", lambda t, p: seen.append((t, p)))
    return node, clock, scanner, seen


def scan(node, request_id: str = "r1"):
    cmd = interfaces.SiteScan.stamped(interfaces.__version__, request_id=request_id,
                                      requested_by="alice@example.com", door="tailnet")
    return main.invoke(node, interfaces.SITE_SCAN, cmd.model_dump(), requires="site-survey:scanner")


def results(seen, topic=interfaces.SITE_SCAN_RESULT):
    return [p for t, p in seen if t == topic]


def test_a_scan_runs_on_request_and_publishes_on_the_next_tick() -> None:
    node, _, scanner, seen = make()
    main.tick(node)
    ok, msg = scan(node)
    assert ok, msg
    assert scanner.calls == [[NET]]
    assert results(seen) == []                           # finished off the bus; not yet drained
    main.tick(node)
    hosts = interfaces.SiteHosts.model_validate(results(seen, interfaces.SITE_HOSTS)[-1])
    assert hosts.networks == [NET] and hosts.self_addresses == [SELF]
    assert [h.address for h in hosts.hosts] == ["10.20.30.21"]
    assert hosts.requested_by == "alice@example.com" and hosts.origin == "site-survey/scanner"
    result = interfaces.SiteScanResult.model_validate(results(seen)[-1])
    assert result.ok and result.request_id == "r1" and result.host_count == 1 and result.door == "tailnet"


def test_nothing_scans_without_a_request() -> None:
    node, _, scanner, seen = make()
    for _ in range(3):
        main.tick(node)
    assert scanner.calls == [] and results(seen) == []


def test_a_stale_status_refuses_the_scan() -> None:
    node, clock, scanner, seen = make()
    main.tick(node)
    clock.now += 60                                      # past 3 × 5 s without a new status
    scan(node)
    assert scanner.calls == []
    assert "no fresh status" in results(seen)[-1]["message"]


def test_no_status_yet_refuses_the_scan() -> None:
    node, _, scanner, seen = make()
    scan(node)
    assert scanner.calls == [] and not results(seen)[-1]["ok"]


@pytest.mark.parametrize("nets, said", [([], "no IPv4 site network"), (["10.0.0.0/16"], "wider than /22")])
def test_unscannable_networks_are_refused(nets, said) -> None:
    node, _, scanner, seen = make(nets=nets)
    main.tick(node)
    scan(node)
    assert scanner.calls == [] and said in results(seen)[-1]["message"]


def test_one_scan_at_a_time() -> None:
    spawn = Deferred()
    node, _, scanner, seen = make(spawn=spawn)
    main.tick(node)
    scan(node, "r1")
    scan(node, "r2")
    refused = results(seen)[-1]
    assert refused["request_id"] == "r2" and "already running" in refused["message"]
    spawn.run_all()
    main.tick(node)
    assert results(seen)[-1]["request_id"] == "r1" and results(seen)[-1]["ok"]
    scan(node, "r3")                                     # the slot is free again
    assert len(spawn.pending) == 1


def test_a_failed_scan_publishes_no_hosts() -> None:
    node, _, _, seen = make(scanner=FakeScanner(ScanReply(ok=False, message="scan failed: nmap: boom")))
    main.tick(node)
    scan(node)
    main.tick(node)
    assert results(seen, interfaces.SITE_HOSTS) == []
    assert results(seen)[-1]["message"] == "scan failed: nmap: boom" and results(seen)[-1]["host_count"] == 0


def test_a_scanner_that_raises_still_frees_the_slot() -> None:
    class Boom:
        def scan(self, networks):
            raise RuntimeError("boom")

    node, _, _, seen = make(scanner=Boom())
    main.tick(node)
    scan(node, "r1")
    main.tick(node)
    assert "internal error" in results(seen)[-1]["message"]
    scan(node, "r2")
    main.tick(node)
    assert results(seen)[-1]["request_id"] == "r2"


def test_the_outcome_topic_is_not_on_the_bus() -> None:
    node, _, _, seen = make()
    forged = {"schema_version": interfaces.__version__, "plan": {"request_id": "x", "requested_by": "x",
              "door": "x", "networks": [NET]}, "reply": {"ok": True, "message": "forged"}, "finished_at": 1.0}
    assert node.bus.publish("site-survey/scan-outcome", forged) == 0
    main.tick(node)
    assert results(seen) == []
