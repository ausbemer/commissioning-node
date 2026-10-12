#!/usr/bin/env python3
"""Additivity checker — machine-enforcement of spec §3 / §8.1.

Snapshots the JSON schema of every public Pydantic model in the contract package — and of
every nested model they reference (their `$defs`) — and verifies that the current schemas
are a strict superset of the snapshot:

  ERROR  – removed model, removed field, changed field type, newly-required field,
           removed enum value; an UNDECLARED untyped field (see below)
  WARN   – added enum value (breaking for exhaustive matchers; treat as MAJOR unless
           consumers are mandated to handle unknown values — spec §3); a declared blind
           spot that is no longer untyped (remove its declaration)

Untyped fields are the checker's blind spot (spec §3 "Type nested shapes from the first
field"): a bare `dict` / `list[dict]` / `dict[str, Any]` / `Any` schema says nothing about
its inner keys, so a removed, renamed or retyped inner key is invisible here. Type nested
shapes as models (subclass `Shape` from envelope.py). Any untyped field that must remain is
declared, one per line, in `schemas/blind_spots.txt` as `Model.field  # why + plan`; an
undeclared one fails the check. Typed-value mappings (`dict[str, int]`, `dict[str, Model]`)
are fine — their values are checked.

Usage:
  python tools/check_additivity.py snapshot      # write schemas/ baseline (run at release)
  python tools/check_additivity.py check         # compare current models to baseline (CI)
  python tools/check_additivity.py blind-spots   # list untyped fields (to seed the file)

Configuration: set CONTRACT_PACKAGE below (default "interfaces").
Exit codes: 0 ok, 1 violations found, 2 usage/import error.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

CONTRACT_PACKAGE = "interfaces"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = PROJECT_ROOT / "schemas"
BLIND_SPOTS_FILE = SNAPSHOT_DIR / "blind_spots.txt"
DEFS_PREFIX = "$defs/"
sys.path.insert(0, str(PROJECT_ROOT))  # so the contract package imports from any cwd


def collect_schemas() -> dict[str, dict]:
    """{name: schema} for every public model, plus `$defs/<Name>` for each nested model."""
    try:
        pkg = importlib.import_module(CONTRACT_PACKAGE)
    except ImportError as exc:
        print(f"cannot import contract package '{CONTRACT_PACKAGE}': {exc}", file=sys.stderr)
        sys.exit(2)
    try:
        from pydantic import BaseModel
    except ImportError:
        print("pydantic v2 required", file=sys.stderr)
        sys.exit(2)

    schemas: dict[str, dict] = {}
    nested: dict[str, dict] = {}
    for name in getattr(pkg, "__all__", dir(pkg)):
        obj = getattr(pkg, name, None)
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
            schema = obj.model_json_schema()
            for def_name, def_schema in schema.pop("$defs", {}).items():
                nested.setdefault(DEFS_PREFIX + def_name, def_schema)
            schemas[name] = schema
    if not schemas:
        print(f"no public models found in '{CONTRACT_PACKAGE}' — check __all__", file=sys.stderr)
        sys.exit(2)
    return {**schemas, **nested}


def snapshot() -> None:
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    schemas = collect_schemas()
    out = SNAPSHOT_DIR / "baseline.json"
    out.write_text(json.dumps(schemas, indent=2, sort_keys=True))
    print(f"snapshot: {len(schemas)} models (incl. nested) -> {out}")


def _props(schema: dict) -> dict:
    return schema.get("properties", {})


def _type_of(prop: dict) -> str:
    # Normalize enough for comparison; anyOf/oneOf compared structurally.
    keys = ("type", "anyOf", "oneOf", "allOf", "items", "$ref", "enum", "additionalProperties")
    return json.dumps({k: prop[k] for k in keys if k in prop}, sort_keys=True)


def _untyped(prop: dict) -> bool:
    """True if this (sub)schema hides its inner shape from the checker."""
    if any(k in prop for k in ("anyOf", "oneOf", "allOf")):
        return any(_untyped(p) for k in ("anyOf", "oneOf", "allOf") for p in prop.get(k, []))
    if "$ref" in prop or "enum" in prop or "const" in prop:
        return False
    kind = prop.get("type")
    if kind is None:
        return True                                   # Any
    if kind == "array":
        return _untyped(prop.get("items", {}))
    if kind == "object" and "properties" not in prop:
        extra = prop.get("additionalProperties", True)
        return extra in (True, {}) or (isinstance(extra, dict) and _untyped(extra))
    return False


def untyped_fields(schemas: dict[str, dict]) -> list[str]:
    return sorted(f"{model}.{field}"
                  for model, schema in schemas.items()
                  for field, prop in _props(schema).items() if _untyped(prop))


def declared_blind_spots() -> set[str]:
    if not BLIND_SPOTS_FILE.exists():
        return set()
    lines = (line.split("#", 1)[0].strip() for line in BLIND_SPOTS_FILE.read_text().splitlines())
    return {line for line in lines if line}


def check() -> None:
    baseline_file = SNAPSHOT_DIR / "baseline.json"
    if not baseline_file.exists():
        print("no baseline snapshot — run 'snapshot' first", file=sys.stderr)
        sys.exit(2)
    old_all: dict[str, dict] = json.loads(baseline_file.read_text())
    new_all = collect_schemas()

    errors: list[str] = []
    warnings: list[str] = []

    for model, old in old_all.items():
        new = new_all.get(model)
        if new is None:
            errors.append(f"{model}: model removed")
            continue
        old_req, new_req = set(old.get("required", [])), set(new.get("required", []))
        for field, old_prop in _props(old).items():
            new_prop = _props(new).get(field)
            if new_prop is None:
                errors.append(f"{model}.{field}: field removed")
                continue
            if _type_of(old_prop) != _type_of(new_prop):
                # enum diffs get finer treatment below
                o_enum, n_enum = old_prop.get("enum"), new_prop.get("enum")
                if o_enum and n_enum and set(o_enum) <= set(n_enum) and \
                        _type_of({**old_prop, "enum": None}) == _type_of({**new_prop, "enum": None}):
                    added = set(n_enum) - set(o_enum)
                    warnings.append(f"{model}.{field}: enum values added {sorted(added)} "
                                    "(breaking for exhaustive matchers — spec §3)")
                else:
                    errors.append(f"{model}.{field}: type changed\n  old: {_type_of(old_prop)}\n  new: {_type_of(new_prop)}")
        for field in new_req - old_req:
            if field in _props(old):
                errors.append(f"{model}.{field}: previously-optional field is now required")
            else:
                errors.append(f"{model}.{field}: new field is required — new fields must be optional")

    untyped, declared = set(untyped_fields(new_all)), declared_blind_spots()
    for field in sorted(untyped - declared):
        errors.append(f"{field}: untyped nested shape — the checker cannot see inside it. Type it "
                      f"as a model (§3), or declare it in {BLIND_SPOTS_FILE.name} with a reason")
    for field in sorted(declared - untyped):
        warnings.append(f"{field}: declared blind spot is no longer untyped — remove the declaration")

    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    if errors:
        print(f"\nFAILED: {len(errors)} additivity violation(s). "
              "Additive releases (MINOR) may only add optional fields/models (spec §3, §8.1).")
        sys.exit(1)
    print(f"OK: {len(new_all)} models (incl. nested) are a strict superset of the baseline"
          + (f"; {len(declared & untyped)} declared blind spot(s)" if declared & untyped else "")
          + (f" ({len(warnings)} warning(s))" if warnings else ""))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "snapshot":
        snapshot()
    elif cmd == "check":
        check()
    elif cmd == "blind-spots":
        for field in untyped_fields(collect_schemas()):
            print(f"{field}  # TODO: why untyped + plan to type it")
    else:
        print(__doc__)
        sys.exit(2)
