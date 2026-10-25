"""The running record adapter for the RunningSource seam (ADR-0009 D1). Read-only.

Reads `/etc/commissioning-node/running.json`, which root writes on every install
(`ops/install.sh`, `RUNNING_FILE` in ops/paths.sh). The daemon never writes it and never runs
git on the tree: the record is the authority for what runs. A missing, oversized or malformed
record reads as None (nothing known), never as a guess.
"""
from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from ..interface import COMMIT, MODULE, RunningRecord

log = logging.getLogger(MODULE)

#: Matches RUNNING_FILE in ops/paths.sh (tests/test_ops_paths.py checks it).
RUNNING_PATH = "/etc/commissioning-node/running.json"
#: The record is a few hundred bytes; anything bigger isn't one.
MAX_BYTES = 16 * 1024
#: The record's schema majors this reader understands.
KNOWN_SCHEMA = "0."


class RunningFile:
    def __init__(self, path: str = RUNNING_PATH) -> None:
        self._path = path

    def read(self) -> RunningRecord | None:
        try:
            with open(self._path, "rb") as f:
                raw = f.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return None
        except OSError as exc:
            log.warning("can't read %s (%s)", self._path, exc.strerror)
            return None
        try:
            if len(raw) > MAX_BYTES:
                raise ValueError("too large")
            data = json.loads(raw)
            if not str(data.get("schema_version", "")).startswith(KNOWN_SCHEMA):
                raise ValueError(f"schema_version {data.get('schema_version')!r}")
            record = RunningRecord.model_validate(data)
            if not COMMIT.fullmatch(record.commit):
                raise ValueError("commit is not 40 hex digits")
            return record
        except (ValueError, AttributeError, ValidationError) as exc:
            log.warning("ignoring %s: %s", self._path, exc)
            return None
