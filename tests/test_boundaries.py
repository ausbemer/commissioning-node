"""The import-boundary check must hold on every commit — and must demonstrably CATCH
violations: a checker that flags nothing is indistinguishable from a healthy tree
(spec §8.1 corollary: test the checkers with planted violations).

Planted-violation cases run the real checker against tiny synthetic trees, one per
violation class, including the evasive spellings (bare `from .. import core`, a nested
main.py, a file with a syntax error). Legal shapes must stay legal.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_boundaries", REPO_ROOT / "tools" / "check_boundaries.py")
cb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cb)


def test_import_boundaries_hold_on_this_repo() -> None:
    problems = cb.check(REPO_ROOT)
    assert not problems, "boundary violations:\n" + "\n".join(problems)


# -- planted violations: each class must be CAUGHT ----------------------------------------

@pytest.fixture
def tiers(monkeypatch):
    """Synthetic trees use one neutral package (`geo`) and one bus-coupled one (`relay`)."""
    monkeypatch.setattr(cb, "NEUTRAL", {"geo"})
    monkeypatch.setattr(cb, "BUS_COUPLED", {"relay"})


def plant(root: Path, rel: str, source: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    for parent in target.parents:
        if parent == root:
            break
        (parent / "__init__.py").touch()
    target.write_text(source, encoding="utf-8")


VIOLATIONS = {
    "cross-module absolute": ("modules/alpha/features/f.py", "from modules.beta.interface import MODULE\n"),
    "cross-module bare-name": ("modules/alpha/features/f.py", "from modules.beta import core\n"),
    "module imports bus": ("modules/alpha/core/output.py", "import bus\n"),
    "module imports bus-coupled": ("modules/alpha/core/output.py", "import relay\n"),
    "features imports core (dotted)": ("modules/alpha/features/f.py", "from ..core import registry\n"),
    "features imports core (bare)": ("modules/alpha/features/f.py", "from .. import core\n"),
    "features imports sibling feature": ("modules/alpha/features/f.py", "from .g import G\n"),
    "core imports features": ("modules/alpha/core/input.py", "from ..features.f import F\n"),
    "neutral imports module": ("geo/calc.py", "import modules.alpha\n"),
    "neutral imports bus": ("geo/calc.py", "from bus import Bus\n"),
    "neutral imports bus-coupled": ("geo/calc.py", "import relay\n"),
    "bus-coupled imports module": ("relay/r.py", "from modules.alpha.core import registry\n"),
    "contract imports neutral": ("interfaces/models.py", "import geo\n"),
    "contract imports module": ("interfaces/models.py", "from modules.alpha import setup\n"),
    "bus imports contract": ("bus/core.py", "import interfaces\n"),
    "nested main.py not exempt": ("modules/alpha/main.py", "from modules.beta.core import handler\n"),
    "syntax error is a violation": ("modules/alpha/core/broken.py", "def oops(:\n"),
    "package in no tier": ("stray/thing.py", "x = 1\n"),
    "module imports ops tier": ("modules/alpha/core/output.py", "from ops import doctor\n"),
    "neutral imports ops tier": ("geo/calc.py", "import ops.install\n"),
}


@pytest.mark.parametrize("case", VIOLATIONS)
def test_planted_violation_is_caught(tmp_path, tiers, case) -> None:
    rel, source = VIOLATIONS[case]
    plant(tmp_path, rel, source)
    problems = cb.check(tmp_path)
    attributed = rel.split("/")[0] + "/" if case == "package in no tier" else rel
    assert any(p.startswith(attributed) for p in problems), \
        f"{case}: not caught / not attributed to {attributed}\n" + "\n".join(problems)


# -- and the legal shapes must stay legal --------------------------------------------------

LEGAL = {
    "module uses own interface + neutral + contract": (
        "modules/alpha/features/f.py",
        "from ..interface import MODULE\nimport geo\nimport interfaces\nimport json\n"),
    "module __init__ composes core and features": (
        "modules/alpha/__init__.py", "from .core.registry import Registry\nfrom .features.f import F\n"),
    "neutral imports neutral-self + contract": ("geo/calc.py", "from geo import util\nimport interfaces\n"),
    "bus-coupled imports bus + contract + neutral": ("relay/r.py", "import bus\nimport interfaces\nimport geo\n"),
    "modules namespace init imports contract": ("modules/__init__.py", "from interfaces import Envelope\n"),
    "root main.py composes everything": (
        "main.py", "import bus\nimport relay\nfrom modules.alpha.core import registry\n"),
    "tests and tools are not runtime code": ("tests/test_x.py", "from modules.alpha.core import registry\n"),
    "ops tier is not runtime code": ("ops/doctor.py", "import modules.alpha\nimport bus\n"),
}


@pytest.mark.parametrize("case", LEGAL)
def test_legal_shape_passes(tmp_path, tiers, case) -> None:
    rel, source = LEGAL[case]
    plant(tmp_path, rel, source)
    problems = cb.check(tmp_path)
    assert not problems, f"{case}: false positive\n" + "\n".join(problems)


def test_modules_cannot_import_the_config_registry(tmp_path) -> None:
    """ADR-0004 §1: modules read config through their own interface/, never the store.
    Runs against this repo's real tier declaration, not the synthetic one."""
    assert "config_registry" in cb.BUS_COUPLED
    plant(tmp_path, "config_registry/store.py", "import interfaces\n")
    plant(tmp_path, "modules/alpha/core/output.py", "from config_registry import ConfigRegistry\n")
    problems = cb.check(tmp_path)
    assert any(p.startswith("modules/alpha/core/output.py") for p in problems), "\n".join(problems)
