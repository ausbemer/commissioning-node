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


def test_forwarding_is_fenced_before_it_is_enabled() -> None:
    """ADR-0005 A3: the forward chain lets only the tailnet open forwarded connections, and
    the installer turns forwarding on only once that chain is loaded."""
    nft = (OPS / "firewall" / "commissioning-node.nft").read_text()
    chain = nft[nft.index("chain forward"):]
    rules = [line.strip() for line in chain.splitlines()[1:] if line.strip() and not line.strip().startswith("#")]
    assert rules[:4] == ["type filter hook forward priority filter - 10; policy accept;",
                         "ct state established,related accept",
                         'iifname "tailscale0" accept',
                         'drop comment "forwarding: only the tailnet opens connections (ADR-0005)"']
    sysctl = (OPS / "sysctl" / "90-commissioning-node.conf").read_text()
    assert "net.ipv4.ip_forward = 1" in sysctl and "ipv6" not in sysctl.split("#")[-1]
    install = (OPS / "install.sh").read_text()
    assert install.index("ensure_service commissioning-node-firewall.service") \
        < install.index('sysctl -q -p "$SYSCTL_FILE"')
    assert "elif forward_fenced; then" in install
    assert shell_var("SYSCTL_FILE") == "/etc/sysctl.d/90-commissioning-node.conf"
