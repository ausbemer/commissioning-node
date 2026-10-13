"""dashboard inward interface — the contract between this module's core and its features.

The ONLY package features may import from this module (spec §4).
"""
from .actions import Action, ActionResult
from .chrome import banner, page
from .admin import AdminAuth, Session
from .config import (CONFIG_ACTIONS, ConfigFieldState, ConfigSurface, ConfigWriteResult,
                     TAILNET_OPERATIONS, TAILNET_WRITER, Invoker, clear_path, config_key,
                     config_rows, set_path)
from .doors import (DOORS, FALLBACK, LOCAL, TAILNET, Caller, Decision, Refusal, authorize,
                    classify, host_allowed, same_origin)
from .feature import Feature
from .identity import PeerDirectory, PeerIdentity, Unresolved, parse_address
from .models import DashboardState
from .views import View

#: Module identity (kebab-case) for capability flags and logging. Lives here, not in core.
MODULE = "dashboard"
#: Release version — bumps with features/implementation (§4 dual versioning).
VERSION = "0.7.0"
#: Inward contract version — bumps only when this interface/ package changes.
__version__ = "0.7.0"

__all__ = ["MODULE", "VERSION", "__version__", "Feature", "DashboardState", "View",
           "PeerDirectory", "PeerIdentity", "Unresolved", "parse_address",
           "Action", "ActionResult", "AdminAuth", "Session", "banner", "page",
           "DOORS", "LOCAL", "TAILNET", "FALLBACK", "Caller", "Refusal", "Decision",
           "authorize", "classify", "host_allowed", "same_origin",
           "ConfigSurface", "ConfigFieldState", "ConfigWriteResult", "CONFIG_ACTIONS",
           "config_key", "config_rows", "set_path", "clear_path", "Invoker",
           "TAILNET_WRITER", "TAILNET_OPERATIONS"]
