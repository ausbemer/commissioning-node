"""tailnet-gateway's config: the fields it declares, and the seam it reads them through.

ADR-0004. The composition root builds the config registry from `CONFIG_FIELDS` and, with the
writer (ADR-0003 step 6), hands core an object satisfying `ConfigView`. This module never
imports the registry. Nothing reads these fields yet: they are declared ahead of the writer
(§8.4 merge early), so the operator can stage a site's values.

All three are site-scoped: each belongs to one site's tailnet and is wiped at teardown.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from interfaces import ConfigField

from . import MODULE

HOSTNAME = ConfigField(
    module=MODULE, name="hostname", kind="str", label="Tailscale hostname",
    help="This node's name on the site's tailnet. One DNS label: a-z, 0-9 and '-'.",
    max_len=63, pattern=r"[a-z0-9]([a-z0-9-]*[a-z0-9])?")
TAGS = ConfigField(
    module=MODULE, name="tags", kind="str_list", default=[], label="Tailscale tags",
    help="ACL tags to advertise, e.g. tag:commissioning. Separate with spaces or commas.",
    max_items=16, max_len=64, pattern=r"tag:[a-z0-9-]+")
AUTH_KEY = ConfigField(
    module=MODULE, name="auth_key", kind="str", secret=True, label="Tailscale auth key",
    help="Pasted from the site tailnet's admin console. Use an ephemeral key (ADR-0005 D4), "
         "ideally single-use and tagged. Deleted once the node has joined.",
    pattern=r"tskey-[A-Za-z0-9-]+")

#: Every field this module declares (ADR-0004 §4).
CONFIG_FIELDS = [HOSTNAME, TAGS, AUTH_KEY]


@runtime_checkable
class ConfigView(Protocol):
    """Read access to this module's own fields. A secret has no readable value: only its
    file path, for `tailscale up --auth-key=file:<path>` (ADR-0003 §5)."""

    def get(self, name: str) -> object: ...

    def is_set(self, name: str) -> bool: ...

    def secret_path(self, name: str) -> str | None: ...

    def clear_secret(self, name: str) -> object: ...
