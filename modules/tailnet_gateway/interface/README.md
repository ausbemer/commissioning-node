# tailnet-gateway Inward Interface — v0.2.0

The stable contract between this module's core and its features (spec §4).
Additive rules apply: MINOR adds optional things, MAJOR is a rare planned event.

**Two versions, two meanings (spec §4 dual versioning).** `__version__` (here,
`0.1.0`) is the *inward contract* version: it bumps only when this `interface/`
package changes. `VERSION` is the module's *release* version: it bumps with features and
implementation. A module shipping features behind an unchanged interface bumps `VERSION`
only.

## Package exports (`interface/__init__.py`)

This package is the ONLY thing a feature may import from (spec §4: features never import
`core` internals). It exports:

- `MODULE` — the module name constant (kebab-case, e.g. `"tailnet-gateway"`), used for
  capability flags and logging. Lives here, not in `core/`, precisely so features can use
  the module identity without reaching into core.
- `VERSION` — the module release version (`0.2.0`).
- `__version__` — the inward interface semver (`0.2.0`).
- The public models below and the `Feature` protocol.

Features import like `from ..interface import MODULE, Feature` — never `from ..core ...`.

## The Feature protocol

Every feature implements `feature.py`'s protocol:

| Member | Purpose |
|--------|---------|
| `name` | Unique feature identifier (used by the registry and capability flags) |
| `subscriptions` | Topic patterns this feature wants routed to it |
| `handle(topic, payload)` | Receives raw dict payloads; returns zero or more `(topic, payload)` publications |

## Models

| Model | Since | Purpose |
|-------|-------|---------|
| `OverlaySnapshot` | 0.1.0 | Vendor-neutral reading of the overlay (hostname, connected, addresses, served routes). 0.2.0 adds `peers`, plus `observed_at` and `period_s`, which core stamps |
| `OverlayPeer` | 0.2.0 | Another node on the overlay: addresses, unique node name, owning login (None if tagged), tags |
| `OverlayStatusSource` (protocol, `seams.py`) | 0.1.0 | Vendor-neutral seam; `core/tailscale_cli.py` is the Tailscale adapter. Must not raise. |
| `OVERLAY_SNAPSHOT` (inward topic) | 0.1.0 | Name core routes snapshots under; never published on the bus |

All models tolerate unknown fields (`extra="ignore"`). Nested shapes are models too, never
bare `dict`s, so their inner keys stay under the additive rules (spec §3).

**Shared live state between features** (a coordinator, an interlock lease, a tuning store)
is a mutable object defined and versioned here, constructed by core and handed to each
feature that needs it. Features never import each other (spec §4 "features meet in
interface objects").

## Deprecations

| Field | Deprecated in | Replacement | Earliest removal (MAJOR) |
|-------|---------------|-------------|--------------------------|
| — | | | |

## Changelog

- v0.2.0 — `OverlayPeer`; `OverlaySnapshot.peers`, `.observed_at`, `.period_s` (additive).
- v0.1.0 — initial interface.
