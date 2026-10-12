"""tailnet-gateway inward interface — the contract between this module's core and features.

The ONLY package features may import from this module (spec §4).
"""
from .feature import Feature
from .models import OVERLAY_SNAPSHOT, OverlayPeer, OverlaySnapshot
from .seams import OverlayStatusSource

#: Module identity (kebab-case) for capability flags and logging. Lives here, not in core.
MODULE = "tailnet-gateway"
#: Release version — bumps with features/implementation (§4 dual versioning).
VERSION = "0.2.0"
#: Inward contract version — bumps only when this interface/ package changes.
__version__ = "0.2.0"

__all__ = ["MODULE", "VERSION", "__version__", "Feature", "OverlayPeer", "OverlaySnapshot",
           "OverlayStatusSource", "OVERLAY_SNAPSHOT"]
