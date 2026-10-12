#!/usr/bin/env python3
"""Import-boundary checker — machine-enforcement of the spec §4 ontology (§8.1).

The additivity checker guards the contract's *shape*; this guards its *topology*. Every
top-level package in the project belongs to exactly one §4 tier, and each tier has an
import discipline:

  contract        `interfaces/` — imports nothing internal (pure outward contract, §3)
  bus             `bus/` — generic transport; imports nothing internal (not even the contract)
  modules         `modules/{m}/` — never imports another module, `bus`, or bus-coupled infra
                  (the composition root injects the bus). Within a module (§4): features/
                  never import core/ or sibling features; core/ never imports features/.
                  Both may import the module's own interface/. The module's own
                  __init__.py is its composition point and may import both.
  neutral         stateless shared libraries (incl. canonical authorities, §4) — never import
                  modules, `bus`, or bus-coupled infra; may import the contract + other neutral
  bus-coupled     composition-root-wired plumbing — may import the contract, neutral
                  packages and `bus`; never a module
  ops             `ops/` — installer, doctor, service units; not runtime code, so not
                  scanned, and nothing may import it

Declare your neutral and bus-coupled packages in the two sets below — that declaration IS
the ontology, and the checker refuses any top-level package that is not assigned a tier
(an unassigned package is a §4 decision nobody made).

A file that fails to parse is a violation: unparseable code can't be checked, so it can't
be assumed clean. Evasive spellings are resolved (`from .. import core`, multi-name
imports). Exempt: the ROOT main.py (composition root — a main.py inside a package gets no
pass), root conftest.py, and the SKIP_PARTS trees.

Usage:  python tools/check_boundaries.py [root]     exit 0 = clean, 1 = violations
        (root defaults to the project this file lives in; tests pass synthetic trees)
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# ---- configure per project --------------------------------------------------------------
CONTRACT = "interfaces"
BUS = "bus"
MODULES = "modules"
#: ops/provisioning tier (spec §4) — never scanned, never imported
OPS = "ops"
#: neutral shared packages (spec §4) — pure libraries, incl. canonical authorities
NEUTRAL: set[str] = {"freshness"}
#: bus-coupled infra (spec §4) — may hold a bus reference, never imports a module
BUS_COUPLED: set[str] = set()
#: trees never scanned (tests/tools are not runtime code; the rest are not Python packages)
SKIP_PARTS = {"tests", "tools", "schemas", "docs", "scripts", "__pycache__", ".git",
              ".claude", ".venv", "venv", ".github"}
# -----------------------------------------------------------------------------------------

_SYNTAX_ERROR = "<unparseable>"


def internal() -> set[str]:
    return {CONTRACT, BUS, MODULES} | NEUTRAL | BUS_COUPLED


def resolve_relative(file_rel: Path, level: int, module: str | None) -> str:
    """Absolute dotted module for a `from ...x import y` seen in `file_rel`."""
    package = list(file_rel.parts[:-1])
    base = package[: len(package) - (level - 1)] if level > 1 else package
    return ".".join(base + ([module] if module else []))


def imports_of(path: Path, root: Path) -> list[tuple[int, str]]:
    """[(lineno, absolute dotted name)] for every import in `path`.

    `from X import a, b` yields X.a and X.b as well as X — a bare `from .. import core`
    must resolve to the actual subpackage or the layering rules are evadable.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [(exc.lineno or 0, _SYNTAX_ERROR)]
    rel = path.relative_to(root)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = (resolve_relative(rel, node.level, node.module)
                    if node.level else (node.module or ""))
            if base:
                out.append((node.lineno, base))
            out.extend((node.lineno, f"{base}.{alias.name}" if base else alias.name)
                       for alias in node.names if alias.name != "*")
    return out


def violation(zone: tuple[str, ...], target: str) -> str | None:
    """Reason `target` may not be imported from a file under `zone`, or None if legal."""
    parts = target.split(".")
    top = parts[0]
    if top == OPS:
        return f"{OPS}/ is the ops tier — nothing imports it (§4)"
    if top not in internal():
        return None                                   # stdlib / third-party
    if top == zone[0] and zone[0] != MODULES:
        return None                                   # a package's own submodules

    if zone[0] == CONTRACT:
        return f"{CONTRACT}/ is the pure contract and imports nothing internal (got {target})"
    if zone[0] == BUS:
        return f"{BUS}/ is generic transport and imports nothing internal (got {target})"
    if zone[0] in NEUTRAL:
        if top == MODULES or top == BUS or top in BUS_COUPLED:
            return f"neutral package {zone[0]}/ may not import {top} (pure library rule, §4)"
        return None
    if zone[0] in BUS_COUPLED:
        if top == MODULES:
            return f"bus-coupled infra {zone[0]}/ may not import a module (§4)"
        return None

    if zone[0] == MODULES:
        if top == BUS or top in BUS_COUPLED:
            return f"modules never import {top} — the composition root injects it"
        if len(zone) < 2:                             # modules/__init__.py
            return None if top != MODULES else f"{MODULES}/__init__.py composes nothing — keep it empty"
        this_module = zone[1]
        if top == MODULES:
            if len(parts) < 2 or parts[1] != this_module:
                return f"cross-module import into {MODULES}/{this_module} (modules meet only on the bus)"
            sub = zone[2] if len(zone) > 2 else ""
            target_sub = parts[2] if len(parts) > 2 else ""
            if sub == "features" and target_sub == "core":
                return "features/ may not import core/ internals (only interface/)"
            if sub == "features" and target_sub == "features":
                return "features/ may not import sibling features (shared state lives in interface/, §4)"
            if sub == "core" and target_sub == "features":
                return "core/ may not import features/ — it discovers them via the registry"
        return None
    return None


def check(root: Path) -> list[str]:
    problems: list[str] = []
    tiers = internal()
    for child in sorted(root.iterdir()):
        if (child.is_dir() and child.name not in SKIP_PARTS | {OPS} and child.name not in tiers
                and not child.name.startswith(".") and any(child.rglob("*.py"))):
            problems.append(f"{child.name}/: top-level package assigned to no §4 tier — "
                            "declare it in NEUTRAL or BUS_COUPLED (or move it)")
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if rel.parts[0] in SKIP_PARTS | {OPS} or rel in (Path("main.py"), Path("conftest.py")):
            continue
        zone = rel.parts[:-1] or (rel.stem,)
        composing = rel.parts[0] == MODULES and len(rel.parts) == 3 and rel.name == "__init__.py"
        for lineno, target in imports_of(path, root):
            if target == _SYNTAX_ERROR:
                problems.append(f"{rel.as_posix()}:{lineno}: file does not parse — unchecked code is unclean code")
                continue
            reason = violation(zone, target)
            if reason and composing and ("features/ may not" in reason or "core/ may not" in reason):
                continue
            if reason:
                problems.append(f"{rel.as_posix()}:{lineno}: {target} — {reason}")
    return problems


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    problems = check(root)
    if problems:
        print("BOUNDARY VIOLATIONS (spec §4 ontology, §8.1):")
        for p in problems:
            print("  " + p)
        return 1
    print("OK: import boundaries hold (modules meet on the bus, neutral packages pure, contract clean)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
