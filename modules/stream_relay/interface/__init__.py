"""stream-relay inward interface — the contract between this module's core and features.

The ONLY package features may import from this module (spec §4).
"""
from .feature import Feature

#: Module identity (kebab-case) for capability flags and logging. Lives here, not in core.
MODULE = "stream-relay"
#: Release version — bumps with features/implementation (§4 dual versioning).
VERSION = "0.1.0"
#: Inward contract version — bumps only when this interface/ package changes.
__version__ = "0.1.0"

__all__ = ["MODULE", "VERSION", "__version__", "Feature"]
