"""The writer's join record on disk (ADR-0005 A2). I/O only.

One small JSON file in the site-scoped state directory, so teardown wipes it with the rest of
the site. The worker writes it after a successful join (and after Routes), and deletes it
after a successful Leave; core reads it on every tick and stamps it onto the reading. A
missing or unreadable record means "not joined by the operator": auto-rejoin then does
nothing (fails toward not acting).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from pydantic import ValidationError

from ..interface import MODULE, JoinRecord

log = logging.getLogger(MODULE)

FILE = "tailnet-gateway.join.json"
MAX_BYTES = 4096


class JoinRecordStore:
    def __init__(self, directory: str | os.PathLike) -> None:
        self._path = Path(directory) / FILE

    def read(self) -> JoinRecord | None:
        try:
            with open(self._path, "rb") as f:
                raw = f.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return None
        except OSError as exc:
            log.warning("join record unreadable (%s); treating the node as not joined", exc.strerror)
            return None
        try:
            if len(raw) > MAX_BYTES:
                raise ValueError("too large")
            return JoinRecord.model_validate(json.loads(raw))
        except (ValueError, ValidationError):
            log.warning("join record %s is malformed; treating the node as not joined", self._path)
            return None

    def write(self, record: JoinRecord) -> None:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=f".{FILE}.")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(record.model_dump_json())
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    def delete(self) -> None:
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
