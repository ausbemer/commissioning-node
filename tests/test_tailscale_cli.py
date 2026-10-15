"""The read-only Tailscale adapter degrades instead of crashing (§5) and maps status JSON.

NOTE: the sample below follows the documented `tailscale status --json` field names; it was
NOT captured from a pinned tailscale on this node. Replace it with a captured sample once
tailscale is installed (docs/quirks.md).
"""
from __future__ import annotations

import json
import subprocess

from modules.tailnet_gateway.core.tailscale_cli import TailscaleCliStatusSource

SAMPLE = {
    "BackendState": "Running",
    "Self": {"HostName": "cn-01", "DNSName": "cn-01.tail1234.ts.net.", "TailscaleIPs": ["100.64.0.7", "fd7a:115c::7"],
             "PrimaryRoutes": ["192.168.10.0/24"], "Tags": ["tag:commissioning"]},
}


def test_missing_binary_reports_disconnected() -> None:
    src = TailscaleCliStatusSource(which=lambda _: None)
    snap = src.read()
    assert snap.connected is False and snap.addresses == []


def test_status_json_is_mapped() -> None:
    def run(cmd, **kw):
        assert cmd[1:] == ["status", "--json"]          # read-only command only
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(SAMPLE), stderr="")

    snap = TailscaleCliStatusSource(run=run, which=lambda b: "/usr/bin/" + b).read()
    assert snap.hostname == "cn-01" and snap.connected is True
    assert snap.addresses == ["100.64.0.7", "fd7a:115c::7"]
    assert snap.served_routes == ["192.168.10.0/24"]
    assert snap.tags == ["tag:commissioning"]
    assert snap.dns_name == "cn-01.tail1234.ts.net"     # trailing dot stripped (ADR-0008)


def test_failing_or_garbage_output_reports_disconnected() -> None:
    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 5)

    def garbage(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout="not json", stderr="")

    for run in (boom, garbage):
        assert TailscaleCliStatusSource(run=run, which=lambda b: b).read().connected is False


def test_status_report_publishes_the_dns_name() -> None:
    from modules.tailnet_gateway.features.status_report import StatusReport
    from modules.tailnet_gateway.interface import OVERLAY_SNAPSHOT, OverlaySnapshot

    snap = OverlaySnapshot(hostname="cn-01", connected=True, dns_name="cn-01.tail1234.ts.net")
    [(topic, status)] = StatusReport().handle(OVERLAY_SNAPSHOT, snap.model_dump())
    assert topic == "tailnet/status" and status["dns_name"] == "cn-01.tail1234.ts.net"
