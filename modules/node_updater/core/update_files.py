"""The request and result files (ADR-0009 D3): adapters for UpdateRequester and ResultSource.

The daemon's half of the hand-off to the root updater. The request goes in the daemon's own
state directory (`updater/request.json`, 0600): written whole to a temp file, then linked into
place, so root never sees half a request and one still waiting is never overwritten. Root's
result is in root's directory (`/var/lib/commissioning-node-update/result.json`, 0644); the
daemon only reads it. Paths match ops/paths.sh (tests check them).
"""
from __future__ import annotations

import json
import logging
import os

from pydantic import ValidationError

from ..interface import MODULE, RootResult, UpdateRequest

log = logging.getLogger(MODULE)

#: The daemon's state directory (StateDirectory=; CN_STATE in ops/paths.sh).
STATE_DIR = "/var/lib/commissioning-node"
REQUEST_DIR = "updater"
REQUEST_NAME = "request.json"
#: UPDATE_RESULT_DIR in ops/paths.sh, and the name ops/update_helper.py writes.
RESULT_PATH = "/var/lib/commissioning-node-update/result.json"
MAX_RESULT_BYTES = 64 * 1024


class RequestFile:
    def __init__(self, state_dir: str = STATE_DIR) -> None:
        self._dir = os.path.join(state_dir, REQUEST_DIR)
        self._path = os.path.join(self._dir, REQUEST_NAME)

    def pending(self) -> bool:
        return os.path.lexists(self._path)

    def submit(self, request: UpdateRequest) -> str | None:
        try:
            os.makedirs(self._dir, mode=0o700, exist_ok=True)
            tmp = os.path.join(self._dir, f".{REQUEST_NAME}.{request.request_id}")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(request.model_dump_json())
                    f.flush()
                    os.fsync(f.fileno())
                os.link(tmp, self._path)            # fails if a request is still waiting
            finally:
                os.unlink(tmp)
        except FileExistsError:
            return "an update is already requested; wait for it to finish"
        except OSError as exc:
            log.warning("can't leave the update request in %s (%s)", self._dir, exc.strerror)
            return f"couldn't leave the request for the root updater ({exc.strerror})"
        return None


class ResultFile:
    def __init__(self, path: str = RESULT_PATH) -> None:
        self._path = path
        self._warned = False

    def read(self) -> RootResult | None:
        try:
            with open(self._path, "rb") as f:
                raw = f.read(MAX_RESULT_BYTES + 1)
        except FileNotFoundError:
            return None
        except OSError as exc:
            self._warn(f"can't read {self._path} ({exc.strerror})")
            return None
        try:
            if len(raw) > MAX_RESULT_BYTES:
                raise ValueError("too large")
            data = json.loads(raw)
            if not str(data.get("schema_version", "")).startswith("0."):
                raise ValueError(f"schema_version {data.get('schema_version')!r}")
            self._warned = False
            return RootResult.model_validate(data)
        except (ValueError, AttributeError, ValidationError) as exc:
            self._warn(f"ignoring {self._path}: {exc}")
            return None

    def _warn(self, message: str) -> None:
        # Read every tick: say a problem once, not every five seconds.
        if not self._warned:
            log.warning(message)
            self._warned = True
