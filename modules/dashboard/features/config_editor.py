"""config-editor — the config registry's fields as dashboard writes (ADR-0004 §3).

Two `Action`s per declared field: set (`<path>`) and clear (`<path>/clear`). Core runs them
only after `authorize()`; they never check the caller. They record who made the change and
through which door, and pass the operator's text to the registry, which validates it. A
secret's text goes straight to the registry and is never logged or echoed (§8.9).
"""
from __future__ import annotations

from typing import Mapping

from interfaces import ConfigField

from ..interface import (ActionResult, Caller, ConfigSurface, clear_path, config_key,
                         set_path)


def _who(caller: Caller) -> str:
    return f"{caller.label} via the {caller.door} door"


class SetConfig:
    def __init__(self, surface: ConfigSurface, field: ConfigField) -> None:
        self._surface, self._key = surface, config_key(field)
        self.name = f"config.set:{self._key}"
        self.path = set_path(field)
        self.may_disturb = field.may_disturb

    def run(self, form: Mapping[str, str], caller: Caller) -> ActionResult:
        result = self._surface.set(self._key, form.get("value", ""), _who(caller))
        return ActionResult(result.ok, result.message)


class ClearConfig:
    def __init__(self, surface: ConfigSurface, field: ConfigField) -> None:
        self._surface, self._key = surface, config_key(field)
        self.name = f"config.clear:{self._key}"
        self.path = clear_path(field)
        self.may_disturb = field.may_disturb

    def run(self, form: Mapping[str, str], caller: Caller) -> ActionResult:
        result = self._surface.clear(self._key, _who(caller))
        return ActionResult(result.ok, result.message)


def config_actions(surface: ConfigSurface) -> list:
    """Set and clear for every declared field."""
    actions: list = []
    for field in surface.fields():
        actions += [SetConfig(surface, field), ClearConfig(surface, field)]
    return actions
