"""node-updater's config: the field it declares, and the seam it reads it through (ADR-0004).

The update channel is node-scoped: it belongs to the node, not to a site, so it survives a
teardown (ADR-0009 D2).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from interfaces import ConfigField

from . import MODULE
from .source import CHANNELS, RELEASES

UPDATE_CHANNEL = ConfigField(
    module=MODULE, name="update_channel", kind="str", scope="node", default=RELEASES,
    label="Update channel",
    help="Which updates the node offers: 'releases' (tagged versions) or 'dev' (the newest "
         "push to dev). Only a commit whose GitHub CI passed is offered.",
    max_len=16, pattern="|".join(CHANNELS))

#: Every field this module declares (ADR-0004 §4).
CONFIG_FIELDS = [UPDATE_CHANNEL]


@runtime_checkable
class ConfigView(Protocol):
    """Read access to this module's own fields."""

    def get(self, name: str) -> object: ...
