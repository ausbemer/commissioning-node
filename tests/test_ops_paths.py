"""The ops tier and the code name the same host paths (§8.1: checked, not promised).

ops/ is never imported, so these read its files as text.
"""
from __future__ import annotations

import re
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent / "ops"


def shell_var(name: str) -> str:
    match = re.search(rf"^{name}=(\S+)$", (OPS / "paths.sh").read_text(), re.MULTILINE)
    assert match, f"{name} not set in ops/paths.sh"
    return match.group(1)


def unit_setting(name: str) -> str:
    unit = (OPS / "systemd" / "commissioning-node.service").read_text()
    match = re.search(rf"^{name}=(\S+)$", unit, re.MULTILINE)
    assert match, f"{name}= missing from commissioning-node.service"
    return match.group(1)


def test_config_state_directory_matches_paths_sh() -> None:
    """ADR-0004 D1: systemd creates /var/lib/<StateDirectory>, 0700; paths.sh names it."""
    assert shell_var("CN_STATE") == f"/var/lib/{unit_setting('StateDirectory')}"
    assert unit_setting("StateDirectoryMode") == "0700"


def test_fallback_port_matches_main() -> None:
    """ADR-0002 A1: the installer opens paths.sh's FALLBACK_PORT in firewalld; it must be the
    port the daemon actually serves the fallback on, or the site LAN still can't reach it."""
    main = (OPS.parent / "main.py").read_text()
    match = re.search(r"^FALLBACK_PORT = (\d+)$", main, re.MULTILINE)
    assert match, "FALLBACK_PORT not set in main.py"
    assert shell_var("FALLBACK_PORT") == match.group(1)


def test_forwarding_is_fenced_before_it_is_enabled() -> None:
    """ADR-0005 A3, review 2026-10-13: the forward chain lets only the tailnet open forwarded
    connections, and the firewall unit owns forwarding: on only after that chain loads, off
    before it is deleted and when the unit fails. No sysctl drop-in turns it on at boot."""
    nft = (OPS / "firewall" / "commissioning-node.nft").read_text()
    chain = nft[nft.index("chain forward"):]
    rules = [line.strip() for line in chain.splitlines()[1:] if line.strip() and not line.strip().startswith("#")]
    assert rules[:4] == ["type filter hook forward priority filter - 10; policy accept;",
                         "ct state established,related accept",
                         'iifname "tailscale0" accept',
                         'drop comment "forwarding: only the tailnet opens connections (ADR-0005)"']
    unit = [line for line in (OPS / "systemd" / "commissioning-node-firewall.service").read_text().splitlines()
            if line.startswith("Exec")]
    assert unit == ["ExecStart=/usr/sbin/nft -f /etc/commissioning-node/firewall.nft",
                    "ExecStartPost=/usr/sbin/sysctl -q -w net.ipv4.ip_forward=1",
                    "ExecStop=/usr/sbin/sysctl -q -w net.ipv4.ip_forward=0",
                    "ExecStop=/usr/sbin/nft delete table inet commissioning_node",
                    "ExecStopPost=/usr/sbin/sysctl -q -w net.ipv4.ip_forward=0"]
    assert not (OPS / "sysctl").exists()
    install = (OPS / "install.sh").read_text()
    assert 'rm -f "$LEGACY_SYSCTL_FILE"' in install and "sysctl -q -p" not in install
    assert shell_var("LEGACY_SYSCTL_FILE") == "/etc/sysctl.d/90-commissioning-node.conf"
