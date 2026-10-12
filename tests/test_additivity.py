"""The contract must stay a strict superset of the committed baseline (spec §3, §8.1)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_contract_is_superset_of_baseline() -> None:
    result = subprocess.run([sys.executable, str(ROOT / "tools" / "check_additivity.py"), "check"],
                            capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_published_model_is_tolerant_and_versioned() -> None:
    import interfaces
    from pydantic import BaseModel

    for name in interfaces.__all__:
        obj = getattr(interfaces, name)
        if isinstance(obj, type) and issubclass(obj, BaseModel):
            assert obj.model_config.get("extra") == "ignore", f"{name} must be extra='ignore' (§3)"
            if issubclass(obj, interfaces.Envelope):
                assert "schema_version" in obj.model_fields, f"{name} lacks schema_version (§8.5)"
