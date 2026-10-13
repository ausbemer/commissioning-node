"""tailnet-gateway inward interface — the contract between this module's core and features.

The ONLY package features may import from this module (spec §4).
"""
#: Module identity (kebab-case) for capability flags and logging. Lives here, not in core.
#: Defined before the imports below: config.py declares its fields under this name.
MODULE = "tailnet-gateway"
#: Release version — bumps with features/implementation (§4 dual versioning).
VERSION = "0.3.0"
#: Inward contract version — bumps only when this interface/ package changes.
__version__ = "0.3.0"

from .config import AUTH_KEY, CONFIG_FIELDS, HOSTNAME, TAGS, ConfigView  # noqa: E402
from .feature import Feature  # noqa: E402
from .models import OVERLAY_SNAPSHOT, OverlayPeer, OverlaySnapshot  # noqa: E402
from .seams import OverlayStatusSource  # noqa: E402

__all__ = ["MODULE", "VERSION", "__version__", "Feature", "OverlayPeer", "OverlaySnapshot",
           "OverlayStatusSource", "OVERLAY_SNAPSHOT",
           "CONFIG_FIELDS", "HOSTNAME", "TAGS", "AUTH_KEY", "ConfigView"]
