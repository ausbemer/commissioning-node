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


@runtime_checkable
class Invoker(Protocol):
    """Publishes a command for an active invoker, refusing in-band when nobody can act on it
    (§5). `requires` is "module:feature". Satisfied by `main.invoke`; injected by main.py."""

    def __call__(self, topic: str, payload: dict, requires: str) -> tuple[bool, str]: ...


#: The capability a tailnet apply needs (§5): advertised only once the writer is enabled.
TAILNET_WRITER = "tailnet-gateway:tailnet-writer"

#: Tailnet operations (ADR-0005): operation -> (POST path, may-disturb warning). The warnings
#: are what the confirm checkbox says and what the action manifest will publish (§5).
TAILNET_OPERATIONS = {
    "join": ("/actions/tailnet/join",
             "Joins the site tailnet with the saved auth key, which is then deleted. Use an "
             "ephemeral key: the node is removed from the tailnet when it goes offline."),
    "rename": ("/actions/tailnet/rename",
               "Changes this node's tailnet name; links using the old name stop working."),
    "leave": ("/actions/tailnet/leave",
              "Disconnects this node from the tailnet. Tailnet sessions to it end; recovery is "
              "the LAN fallback on :8443."),
}
