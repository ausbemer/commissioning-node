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


def _daemon_user(tmp_path, file_text: str | None) -> str:
    """Run paths.sh's daemon_user against a temp file and this repo as the tree."""
    import subprocess
    f = tmp_path / "daemon-user"
    if file_text is not None:
        f.write_text(file_text)
    out = subprocess.run(["bash", "-c", f'REPO_ROOT="$1"; . "$2"; daemon_user "$3" "$1"', "_",
                          str(OPS.parent), str(OPS / "paths.sh"), str(f)],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_daemon_user_comes_from_its_file_else_the_checkout_owner(tmp_path) -> None:
    """ADR-0009 D1: a release checkout is root's, so the first install records the daemon user;
    a node installed before that still falls back to the checkout's owner."""
    owner = (OPS.parent).owner()
    assert _daemon_user(tmp_path, None) == owner
    assert _daemon_user(tmp_path, "") == owner
    assert _daemon_user(tmp_path, "svc-node\n") == "svc-node"


def test_provenance_is_recorded_by_root_and_git_never_runs_as_root_in_a_users_tree() -> None:
    """ADR-0009 D1: every install records what the node runs (root, 0644, schema_version), the
    doctor reports it, and root runs git in a tree another user owns only as that user."""
    assert shell_var("DAEMON_USER_FILE") == '"$CN_ETC/daemon-user"'
    assert shell_var("RUNNING_FILE") == '"$CN_ETC/running.json"'
    paths = (OPS / "paths.sh").read_text()
    assert 'runuser -u "$TREE_OWNER" -- git -C "$REPO_ROOT"' in paths
    assert "safe.directory" not in paths
    install = (OPS / "install.sh").read_text()
    assert '"schema_version": "0.1.0"' in install
    assert 'install -D -m 0644 "$work/running.json" "$RUNNING_FILE"' in install
    assert 'install -m 0644 "$work/daemon-user" "$DAEMON_USER_FILE"' in install
    for script in ("install.sh", "doctor.sh"):
        assert "git -C" not in (OPS / script).read_text(), f"{script}: use tree_git"
    doctor = (OPS / "doctor.sh").read_text()
    assert 'section "Provenance (ADR-0009 D1)"' in doctor and "tree_git rev-parse HEAD" in doctor


def test_the_root_updater_runs_only_root_owned_copies() -> None:
    """ADR-0009 D1, D3: root runs copies the installer rendered into /usr/local/libexec, never a
    tree's file; only the .path is enabled; a killed run is marked unfinished; the result is in
    root's directory, the request in the daemon's."""
    unit = shell_var("UPDATE_UNIT")
    helper, py = shell_var("UPDATE_HELPER"), shell_var("UPDATE_PY")
    assert helper.startswith("/usr/local/libexec/") and py.startswith("/usr/local/libexec/")
    assert shell_var("UPDATE_TREE") == "/opt/commissioning-node-release"
    assert shell_var("UPDATE_REPO_URL").startswith("https://github.com/")    # no key involved
    assert shell_var("UPDATE_RESULT_DIR") == "/var/lib/commissioning-node-update"
    service = (OPS / "systemd" / f"{unit}.service").read_text()
    assert f"ExecStart={helper}\n" in service and f"ExecStopPost={helper} --after\n" in service
    assert "[Install]" not in service and "User=" not in service and "TimeoutStartSec=900" in service
    path = (OPS / "systemd" / f"{unit}.path").read_text()
    assert "PathExists=/var/lib/commissioning-node/updater/request.json" in path
    script = (OPS / "update.sh").read_text()
    for placeholder in ("@UPDATE_TREE@", "@UPDATE_REPO_URL@", "@CN_STATE@", "@UPDATE_RESULT_DIR@",
                        "@UPDATE_PY@", "@RUNNING_FILE@"):
        assert placeholder in script
    assert script.rstrip().endswith('main "$@"\nexit 0')                # parsed whole, then run
    assert "GIT_CONFIG_GLOBAL=/dev/null" in script
    install = (OPS / "install.sh").read_text()
    assert 'sync_file "$work/update" "$UPDATE_HELPER" 0755' in install
    assert 'sync_file "$OPS_DIR/update_helper.py" "$UPDATE_PY" 0644' in install
    assert 'ensure_service "$UPDATE_UNIT.path"' in install and 'ensure_service "$UPDATE_UNIT.service"' not in install
    assert 'install -d -o root -m 0755 "$UPDATE_RESULT_DIR"' in install
    doctor = (OPS / "doctor.sh").read_text()
    assert '"root 755 root 644"' in doctor and 'enabled_active "$UPDATE_UNIT.path"' in doctor
