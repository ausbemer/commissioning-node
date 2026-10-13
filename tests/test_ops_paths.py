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
