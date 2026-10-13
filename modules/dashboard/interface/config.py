"""The config registry as the dashboard sees it (ADR-0004 §3).

`ConfigSurface` is the seam: the composition root hands core an object satisfying it (the
registry's operator surface), and the dashboard never imports the registry. Every write
through it is an `Action` behind `authorize()`; the surface itself checks values, never
callers.

A secret's state carries no value (None) and reads only as set/unset (§8.9).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from interfaces import ConfigField

#: Where the config actions answer: POST CONFIG_ACTIONS/<module>/<name>[/clear].
CONFIG_ACTIONS = "/actions/config"


@runtime_checkable
class ConfigFieldState(Protocol):
    field: ConfigField
    #: The current value (the default when unset). Always None for a secret.
    value: object
    is_set: bool


@runtime_checkable
class ConfigWriteResult(Protocol):
    ok: bool
    #: For people; never contains a secret.
    message: str


@runtime_checkable
class ConfigSurface(Protocol):
    def fields(self) -> list[ConfigField]: ...

    def state(self, key: str) -> ConfigFieldState: ...

    def set(self, key: str, text: str, who: str) -> ConfigWriteResult: ...

    def clear(self, key: str, who: str) -> ConfigWriteResult: ...


def config_key(field: ConfigField) -> str:
    return f"{field.module}.{field.name}"


def set_path(field: ConfigField) -> str:
    return f"{CONFIG_ACTIONS}/{field.module}/{field.name}"


def clear_path(field: ConfigField) -> str:
    return f"{set_path(field)}/clear"


def config_rows(surface: ConfigSurface) -> list[dict]:
    """Every field and its state, as plain data for a View's snapshot. Secrets carry no value."""
    rows = []
    for field in surface.fields():
        state = surface.state(config_key(field))
        rows.append({"field": field.model_dump(), "key": config_key(field),
                     "value": None if field.secret else state.value, "is_set": state.is_set,
                     "set_path": set_path(field), "clear_path": clear_path(field)})
    return rows
