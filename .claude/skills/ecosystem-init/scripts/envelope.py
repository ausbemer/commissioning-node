"""Reference base model for every published payload — copy into `interfaces/`.

Two load-bearing choices live here, and the whole architecture leans on them:

1. `extra="ignore"` — the Tolerant Reader. A consumer built against an OLD contract
   version silently drops fields a NEWER producer added, instead of erroring. This is
   what makes additive (MINOR) contract growth safe by construction (spec §3).
2. `schema_version` — the version on the wire (spec §8.5). Every envelope carries the
   contract version it was produced under, so skew is observable at runtime and
   "why did this consumer miss a field?" is a lookup, not an investigation.

Every model that crosses a boundary should subclass `Envelope`. Every NESTED shape inside
one (a waypoint in a route, an entry in a list) subclasses `Shape` instead of being a bare
`dict`: an untyped dict hides its inner keys from the additivity checker (spec §3, "Type
nested shapes from the first field"). Do not set `extra="allow"` (that would let unknown
fields leak into re-published payloads and feed the forwarding trap, §8.2) and never
`extra="forbid"` (that breaks additivity — a closed nested model inverts §3 too).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Shape(BaseModel):
    """Base for nested shapes: open to unknown keys, checked field-by-field (§3)."""

    # extra="ignore" is mandatory — see module docstring. Do not change it.
    model_config = ConfigDict(extra="ignore")


class Envelope(Shape):
    """Base for every published payload: a Shape that carries its version on the wire."""

    schema_version: str

    @classmethod
    def stamped(cls, version: str, **data):
        """Build an instance with schema_version set from the contract package version.

        Usage in a producer:
            from interfaces import __version__
            reading = Reading.stamped(__version__, value=22.5)
        """
        return cls(schema_version=version, **data)
