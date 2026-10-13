"""The registry: declared fields, their stored values, and the two ways in (ADR-0004 §1–§2).

One `ConfigRegistry` per process, built by the composition root from every module's
declarations. It hands out two kinds of handle and nothing else:

- `OwnerView` — one per module, scoped to that module's own fields. Reads values; locates
  (never reads) its secrets; clears a secret it no longer needs.
- `OperatorSurface` — for the dashboard. Lists every field, reads non-secrets, reads
  secrets only as set/unset, and writes. The dashboard puts `authorize()` in front of it.

On disk, under the daemon's state directory:

    <root>/site/config.json      non-secret values, wiped with the site (scope "site")
    <root>/site/secrets/<key>    one file per secret, 0600
    <root>/node/...              the same for scope "node"; survives a move between sites

Every write is temp file → fsync → rename → fsync dir (§8.9). Loading never crashes boot
(§4): anything malformed falls back to the default, loudly, and stays on disk untouched.
"""
from __future__ import annotations

import json
import logging
import os
import stat
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from interfaces import ConfigField

from .values import (MAX_SECRET_LEN, SCOPES, Invalid, Value, check_declaration, key_of, parse,
                     validate)

log = logging.getLogger("config_registry")

#: config.json format. A file with another version is set aside, never overwritten.
FORMAT_VERSION = 1
#: Never decode more than this from config.json (§8.8). PROVISIONAL, with the value bounds.
MAX_FILE_BYTES = 64 * 1024
CONFIG_FILE = "config.json"
SECRETS_DIR = "secrets"
SECRET_SET = "<secret set>"
SECRET_CLEARED = "<secret cleared>"


@dataclass(frozen=True)
class WriteResult:
    ok: bool
    #: For people: what happened, or why not (§11 rung 7). Never contains a secret.
    message: str


@dataclass(frozen=True)
class FieldState:
    field: ConfigField
    #: The current value (the default when unset). Always None for a secret.
    value: Value | None
    #: True when a value is stored; for a secret, when a usable secret file exists.
    is_set: bool


class ConfigRegistry:
    def __init__(self, root: str | os.PathLike, fields: Iterable[ConfigField],
                 uid: int | None = None) -> None:
        self._root = Path(root)
        #: Secret files must belong to this user (the daemon's).
        self._uid = os.geteuid() if uid is None else uid
        self._fields: dict[str, ConfigField] = {}
        for field in fields:
            check_declaration(field)
            key = key_of(field)
            if key in self._fields:
                raise ValueError(f"{key}: declared twice")
            self._fields[key] = field
        self._lock = threading.RLock()
        #: What is on disk, per scope, including keys this build doesn't declare.
        self._stored: dict[str, dict[str, object]] = {scope: {} for scope in SCOPES}
        #: A malformed config.json is renamed aside (whole, untouched) before the first write.
        self._malformed: dict[str, bool] = {scope: False for scope in SCOPES}
        #: Why a scope's config.json can't be read; writes to that scope are refused, so a
        #: file we can't see is never replaced by one holding only the new value.
        self._unreadable: dict[str, str | None] = {scope: None for scope in SCOPES}
        #: Validated, stored, non-secret values.
        self._values: dict[str, Value] = {}
        #: Last reported problem per secret, so a bad file warns once, not every tick.
        self._secret_problem: dict[str, str | None] = {}
        for scope in SCOPES:
            self._sweep(scope)
            self._load(scope)

    # -- handles ---------------------------------------------------------------------------

    def owner_view(self, module: str) -> OwnerView:
        return OwnerView(self, module)

    def operator_surface(self) -> OperatorSurface:
        return OperatorSurface(self)

    # -- reads -----------------------------------------------------------------------------

    def _field(self, key: str) -> ConfigField:
        try:
            return self._fields[key]
        except KeyError:
            raise KeyError(f"{key}: no such config field") from None

    def _value(self, field: ConfigField) -> Value | None:
        with self._lock:
            value = self._values.get(key_of(field), field.default)
        return list(value) if isinstance(value, list) else value

    def _state(self, field: ConfigField) -> FieldState:
        if field.secret:
            return FieldState(field, None, self._secret_ok(field))
        with self._lock:                         # one acquisition: value and is_set agree
            return FieldState(field, self._value(field), key_of(field) in self._values)

    def _secret_file(self, field: ConfigField) -> Path:
        return self._root / field.scope / SECRETS_DIR / key_of(field)

    def _secret_ok(self, field: ConfigField) -> bool:
        """A secret is usable only as a regular file, 0600 or tighter, owned by the daemon."""
        path = self._secret_file(field)
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            self._report_secret(field, None)
            return False
        except OSError as exc:
            self._report_secret(field, f"cannot stat ({exc.strerror})")
            return False
        if not stat.S_ISREG(st.st_mode):
            problem = "is not a regular file"
        elif st.st_mode & 0o077:
            problem = f"has mode {stat.S_IMODE(st.st_mode):04o}, wants 0600"
        elif st.st_uid != self._uid:
            problem = f"is owned by uid {st.st_uid}, wants {self._uid}"
        elif st.st_size > 4 * MAX_SECRET_LEN:    # UTF-8: at most 4 bytes per character
            problem = f"is {st.st_size} bytes, more than a secret may be"
        else:
            problem = None
        self._report_secret(field, problem)
        return problem is None

    def _report_secret(self, field: ConfigField, problem: str | None) -> None:
        key = key_of(field)
        with self._lock:
            if self._secret_problem.get(key) == problem:
                return
            self._secret_problem[key] = problem
        if problem:
            log.warning("config %s: secret file %s %s; treating it as unset", key,
                        self._secret_file(field), problem)

    # -- writes ----------------------------------------------------------------------------

    def _set(self, key: str, text: str, who: str) -> WriteResult:
        try:
            field = self._field(key)
        except KeyError as exc:
            return WriteResult(False, str(exc.args[0]))
        name = field.label or key
        try:
            value = validate(field, parse(field, text))
        except Invalid as exc:
            return WriteResult(False, f"{name} refused: {exc}")
        with self._lock:
            try:
                if field.secret:
                    self._write_secret(field, value)
                    shown = SECRET_SET
                else:
                    old = self._value(field)
                    self._store(field, value)
                    shown = f"{old!r} -> {value!r}"
            except OSError as exc:
                log.error("config %s: could not save (%s)", key, exc.strerror)
                return WriteResult(False, f"{name} not saved: {exc.strerror}")
        log.info("config set %s by %r: %s", key, who, shown)
        return WriteResult(True, f"{name} saved")

    def _clear(self, key: str, who: str) -> WriteResult:
        try:
            field = self._field(key)
        except KeyError as exc:
            return WriteResult(False, str(exc.args[0]))
        name = field.label or key
        with self._lock:
            try:
                if field.secret:
                    path = self._secret_file(field)
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                    else:
                        _fsync_dir(path.parent)
                    self._report_secret(field, None)
                    shown = SECRET_CLEARED
                else:
                    old = self._value(field)
                    self._store(field, None)
                    shown = f"{old!r} -> default {field.default!r}"
            except OSError as exc:
                log.error("config %s: could not clear (%s)", key, exc.strerror)
                return WriteResult(False, f"{name} not cleared: {exc.strerror}")
        log.info("config clear %s by %r: %s", key, who, shown)
        return WriteResult(True, f"{name} cleared")

    def _store(self, field: ConfigField, value: Value | None) -> None:
        """Write the field's scope file with `value` (None removes it). Caller holds the lock.
        Memory changes only after the file is safely on disk."""
        key, scope = key_of(field), field.scope
        stored = dict(self._stored[scope])
        if value is None:
            stored.pop(key, None)
        else:
            stored[key] = value
        if self._unreadable[scope]:
            raise OSError(13, f"{scope}/{CONFIG_FILE} is unreadable ({self._unreadable[scope]}); "
                              "fix its owner or mode, then restart")
        directory = self._root / scope
        _mkdir_private(directory)
        if self._malformed[scope]:
            aside = directory / f"{CONFIG_FILE}.malformed-{int(time.time())}"
            os.replace(directory / CONFIG_FILE, aside)       # whole, as it is on disk
            log.warning("config %s/%s: malformed file kept as %s", scope, CONFIG_FILE, aside.name)
            self._malformed[scope] = False
        body = json.dumps({"schema_version": FORMAT_VERSION, "values": stored},
                          indent=2, sort_keys=True) + "\n"
        _write_atomic(directory / CONFIG_FILE, body.encode())
        self._stored[scope] = stored
        if value is None:
            self._values.pop(key, None)
        else:
            self._values[key] = value

    def _write_secret(self, field: ConfigField, value: str) -> None:
        directory = self._secret_file(field).parent
        _mkdir_private(directory)
        _write_atomic(self._secret_file(field), value.encode())

    # -- load ------------------------------------------------------------------------------

    def _sweep(self, scope: str) -> None:
        """Remove temp files a power cut left mid-write (`.<name>.*`, ours by construction):
        a leftover could hold a secret that clearing it would then miss."""
        for directory in (self._root / scope, self._root / scope / SECRETS_DIR):
            try:
                leftovers = [p for p in directory.iterdir() if p.name.startswith(".") and p.is_file()]
            except OSError:
                continue
            for path in leftovers:
                try:
                    path.unlink()
                    log.warning("config: removed a leftover temp file %s", path)
                except OSError:
                    pass

    def _load(self, scope: str) -> None:
        path = self._root / scope / CONFIG_FILE
        try:
            with open(path, "rb") as f:
                raw = f.read(MAX_FILE_BYTES + 1)
        except FileNotFoundError:
            return
        except OSError as exc:
            log.warning("config %s/%s unreadable (%s); using defaults, and refusing writes to "
                        "this scope until it is fixed", scope, CONFIG_FILE, exc.strerror)
            self._unreadable[scope] = exc.strerror or "error"
            return
        values = _decode(raw)
        if values is None:
            log.warning("config %s/%s is malformed; using defaults (the file is kept)",
                        scope, CONFIG_FILE)
            self._malformed[scope] = True
            return
        self._stored[scope] = values
        for key, value in values.items():
            field = self._fields.get(key)
            if field is None:
                log.info("config %s/%s: %s is not declared by this build; kept", scope,
                         CONFIG_FILE, key)
            elif field.secret or field.scope != scope:
                log.warning("config %s/%s: %s does not belong here; ignored (kept)", scope,
                            CONFIG_FILE, key)
            else:
                try:
                    self._values[key] = validate(field, value)
                except Invalid as exc:
                    log.warning("config %s: stored value refused (%s); using the default",
                                key, exc)


class OwnerView:
    """A module's read-only view of its own fields. Built only by `ConfigRegistry`."""

    def __init__(self, registry: ConfigRegistry, module: str) -> None:
        self._registry = registry
        self._module = module

    def _field(self, name: str) -> ConfigField:
        return self._registry._field(f"{self._module}.{name}")

    def get(self, name: str) -> Value | None:
        """The value, or the declared default when unset. A secret has no readable value."""
        field = self._field(name)
        if field.secret:
            raise KeyError(f"{self._module}.{name} is a secret: use secret_path()")
        return self._registry._value(field)

    def is_set(self, name: str) -> bool:
        return self._registry._state(self._field(name)).is_set

    def secret_path(self, name: str) -> str | None:
        """Where the secret is, for `--auth-key=file:<path>`; None when unset or unusable."""
        field = self._field(name)
        if not field.secret:
            raise KeyError(f"{self._module}.{name} is not a secret")
        return str(self._registry._secret_file(field)) if self._registry._secret_ok(field) else None

    def clear_secret(self, name: str) -> WriteResult:
        """Delete a secret this module no longer needs (e.g. the auth key after a join)."""
        field = self._field(name)
        if not field.secret:
            raise KeyError(f"{self._module}.{name} is not a secret")
        return self._registry._clear(key_of(field), self._module)


class OperatorSurface:
    """Every field, for the operator surface. Put `authorize()` in front of the writes."""

    def __init__(self, registry: ConfigRegistry) -> None:
        self._registry = registry

    def fields(self) -> list[ConfigField]:
        """Declared fields, in declaration order."""
        return list(self._registry._fields.values())

    def state(self, key: str) -> FieldState:
        return self._registry._state(self._registry._field(key))

    def set(self, key: str, text: str, who: str) -> WriteResult:
        """Parse operator text, validate it and store it. `who` goes in the change log."""
        return self._registry._set(key, text, who)

    def clear(self, key: str, who: str) -> WriteResult:
        """Back to the default; for a secret, delete it."""
        return self._registry._clear(key, who)


def _decode(raw: bytes) -> dict[str, object] | None:
    if len(raw) > MAX_FILE_BYTES:
        return None
    try:
        doc = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict) or doc.get("schema_version") != FORMAT_VERSION:
        return None
    values = doc.get("values")
    return values if isinstance(values, dict) else None


def _write_atomic(path: Path, data: bytes) -> None:
    """Temp file (created 0600) → fsync → rename → fsync dir. Never half-written (§8.9)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    _fsync_dir(path.parent)


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _mkdir_private(directory: Path) -> None:
    """Create `directory` and any missing parents at 0700, whatever the umask."""
    missing = []
    for path in [directory, *directory.parents]:
        if path.exists():
            break
        missing.append(path)
    for path in reversed(missing):
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            continue
        os.chmod(path, 0o700)
