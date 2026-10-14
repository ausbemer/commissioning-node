"""tailnet-gateway inward interface — the contract between this module's core and features.

The ONLY package features may import from this module (spec §4).
"""
#: Module identity (kebab-case) for capability flags and logging. Lives here, not in core.
#: Defined before the imports below: config.py declares its fields under this name.
MODULE = "tailnet-gateway"
#: Release version — bumps with features/implementation (§4 dual versioning).
VERSION = "0.7.1"
#: Inward contract version — bumps only when this interface/ package changes.
__version__ = "0.7.0"

from .config import (ADVERTISE_ROUTES, AUTH_KEY, AUTO, AUTO_REJOIN, CONFIG_FIELDS,  # noqa: E402
                     HOSTNAME, TAGS, ConfigView, resolve_routes, route_problem)
from .feature import Feature  # noqa: E402
from .models import OVERLAY_SNAPSHOT, JoinRecord, OverlayPeer, OverlaySnapshot  # noqa: E402
from .seams import OverlayStatusSource, OverlayWriter, SiteNetworkSource, WriterReply  # noqa: E402
from .writer import (JOIN, LEAVE, OPERATIONS, RENAME, ROUTES, ROUTES_OFF, WRITE_OUTCOME,  # noqa: E402
                     WriteOutcome,
                     WritePlan, WriterSlot)

__all__ = ["MODULE", "VERSION", "__version__", "Feature", "OverlayPeer", "OverlaySnapshot",
           "OverlayStatusSource", "OVERLAY_SNAPSHOT",
           "CONFIG_FIELDS", "HOSTNAME", "TAGS", "AUTH_KEY", "ConfigView",
           "OverlayWriter", "WriterReply", "WriterSlot", "WritePlan", "WriteOutcome",
           "WRITE_OUTCOME", "OPERATIONS", "JOIN", "RENAME", "ROUTES", "ROUTES_OFF", "LEAVE",
           "ADVERTISE_ROUTES", "AUTO", "AUTO_REJOIN", "resolve_routes", "route_problem",
           "JoinRecord", "SiteNetworkSource"]
