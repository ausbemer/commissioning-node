"""node-updater inward interface — the contract between this module's core and features.

The ONLY package features may import from this module (spec §4).
"""
#: Module identity (kebab-case) for capability flags and logging. Lives here, not in core.
#: Defined before the imports below: config.py declares its field under this name.
MODULE = "node-updater"
#: Release version — bumps with features/implementation (§4 dual versioning).
VERSION = "0.1.0"
#: Inward contract version — bumps only when this interface/ package changes.
__version__ = "0.1.0"

from .config import CONFIG_FIELDS, UPDATE_CHANNEL, ConfigView  # noqa: E402
from .feature import Feature  # noqa: E402
from .source import (CHANNELS, CHECK_OUTCOME, COMMIT, DEV, DEV_BRANCH,  # noqa: E402
                     MAX_RELEASES_WALKED, RELEASE_TAG, RELEASES, CheckOutcome, CheckPlan,
                     CheckSlot, ReleaseReading, ReleaseSource, RunningRecord, RunningSource,
                     choose_target, release_key)

__all__ = ["MODULE", "VERSION", "__version__", "Feature", "CHANNELS", "CHECK_OUTCOME", "COMMIT",
           "DEV", "DEV_BRANCH", "MAX_RELEASES_WALKED", "RELEASE_TAG", "RELEASES", "CheckOutcome",
           "CheckPlan", "CheckSlot", "ReleaseReading", "ReleaseSource", "RunningRecord",
           "RunningSource", "choose_target", "release_key", "CONFIG_FIELDS", "UPDATE_CHANNEL",
           "ConfigView"]
