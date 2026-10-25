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


def test_firewalld_forward_policy_is_installed_and_checked() -> None:
    """ADR-0002 A3: on the RevPi image firewalld rejects the site LAN into the tailnet unless
    paths.sh's policy forwards the site zones into trusted; install.sh keeps it, doctor.sh checks it."""
    assert shell_var("FIREWALLD_POLICY") == "site-to-tailnet"
    install = (OPS / "install.sh").read_text()
    assert 'pol="$FIREWALLD_POLICY"' in install
    assert "--add-egress-zone=trusted" in install and "--set-target=ACCEPT" in install
    assert '--policy="$FIREWALLD_POLICY" --query-ingress-zone' in (OPS / "doctor.sh").read_text()


def test_forwarding_is_fenced_before_it_is_enabled() -> None:
    """ADR-0005 A3, review 2026-10-13, amendment 2026-10-21: the forward chain lets the tailnet
    open forwarded connections, and the site LAN open them only into the tailnet; it drops the
    rest. The firewall unit owns forwarding: on only after that chain loads, off
    before it is deleted and when the unit fails. No sysctl drop-in turns it on at boot."""
    nft = (OPS / "firewall" / "commissioning-node.nft").read_text()
    chain = nft[nft.index("chain forward"):]
    rules = [line.strip() for line in chain.splitlines()[1:] if line.strip() and not line.strip().startswith("#")]
    assert rules[:5] == ["type filter hook forward priority filter - 10; policy accept;",
                         "ct state established,related accept",
                         'iifname "tailscale0" accept',
                         'oifname "tailscale0" accept comment "forwarding: the site LAN into the tailnet (ADR-0005, 2026-10-21)"',
                         'drop comment "forwarding: nothing else is forwarded (ADR-0005)"']
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


def test_tailscale_operator_is_renewed_by_root_never_from_the_repo() -> None:
    """ADR-0002 A4: a Leave deletes tailscaled's operator (quirks Q-010). A root oneshot renews
    it on tailscaled start, on a state change and on a timer. Root runs a root-owned copy, never
    the repo's file (the daemon user owns the repo), and writes only the operator."""
    unit = shell_var("OPERATOR_UNIT")
    helper = shell_var("OPERATOR_HELPER")
    assert not helper.startswith("/opt/") and helper.startswith("/usr/local/libexec/")
    systemd = OPS / "systemd"
    service = (systemd / f"{unit}.service").read_text()
    assert f"ExecStart={helper}\n" in service
    assert "WantedBy=tailscaled.service" in service and "After=tailscaled.service" in service
    assert "User=" not in service                     # root: `tailscale set --operator` needs it
    assert "PathChanged=/var/lib/tailscale/tailscaled.state" in (systemd / f"{unit}.path").read_text()
    timer = (systemd / f"{unit}.timer").read_text()
    assert "OnUnitActiveSec=300" in timer and "OnActiveSec=" in timer   # OPERATOR_RENEW_S

    script = (OPS / "tailscale-operator.sh").read_text()
    assert 'RUN_USER="@RUN_USER@"' in script
    commands = [line.strip() for line in script.splitlines()
                if "tailscale " in line and not line.lstrip().startswith("#")]
    writes = [c for c in commands if not c.startswith("tailscale debug prefs")
              and "warn " not in c and "note " not in c]
    assert writes == ['if out="$(tailscale set --operator="$RUN_USER" 2>&1)"; then']   # the one write

    install = (OPS / "install.sh").read_text()
    assert '"$OPS_DIR/tailscale-operator.sh" > "$work/tailscale-operator"' in install
    assert 'sync_file "$work/tailscale-operator" "$OPERATOR_HELPER" 0755' in install
    doctor = (OPS / "doctor.sh").read_text()
    assert 'check "operator is $RUN_USER' in doctor     # a FAIL now, not a WARN
    assert '"root 755"' in doctor
