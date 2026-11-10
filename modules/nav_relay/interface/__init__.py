"""nav-relay inward interface — the contract between this module's core and features.

The ONLY package features may import from this module (spec §4).
"""
from .feature import Feature
from .nav import (ALLOWLIST, DEFAULT_PORT, INTERFACE, LINK_DOWN, LINK_NO_ADAPTER,
                  LINK_NOT_LISTEN_ONLY, LINK_UP, MAX_DESTINATIONS, MAX_SOURCES, NOMINAL_PERIOD_S,
                  OUTPUT_PERIOD_S, PGN_COG_SOG_RAPID, PGN_POSITION_RAPID, PGN_SYSTEM_TIME,
                  PGN_VESSEL_HEADING, Destination, DestinationTable, LinkReport, NavSource,
                  NavState, Reading, SentenceSink, current_fix, destination_problem)

#: Module identity (kebab-case) for capability flags and logging. Lives here, not in core.
MODULE = "nav-relay"
#: Release version — bumps with features/implementation (§4 dual versioning).
VERSION = "0.1.0"
#: Inward contract version — bumps only when this interface/ package changes.
__version__ = "0.1.0"

__all__ = ["MODULE", "VERSION", "__version__", "Feature", "ALLOWLIST", "DEFAULT_PORT",
           "INTERFACE", "LINK_DOWN", "LINK_NO_ADAPTER", "LINK_NOT_LISTEN_ONLY", "LINK_UP",
           "MAX_DESTINATIONS", "MAX_SOURCES", "NOMINAL_PERIOD_S", "OUTPUT_PERIOD_S",
           "PGN_COG_SOG_RAPID", "PGN_POSITION_RAPID", "PGN_SYSTEM_TIME", "PGN_VESSEL_HEADING",
           "Destination", "DestinationTable", "LinkReport", "NavSource", "NavState", "Reading",
           "SentenceSink", "current_fix", "destination_problem"]
