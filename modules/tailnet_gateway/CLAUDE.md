# tailnet-gateway — Module Context

> Module of the commissioning-node monolith
> following the spec §4 structure standard.

## Role in the ecosystem

Bridge to tailscaled (spec §6): the only module that talks to Tailscale and its single writer (§11 rung 9). Today it reads the node's tailnet status on every tick through the vendor-neutral `OverlayStatusSource` seam and publishes it. Next, it auto-detects the site subnet and *proposes* routes and a hostname for operator confirmation (rung 2). After that it gets a writer feature with its own Safety ADR. Tailscale auth keys are secrets (§8.9).

## Interfaces

Distinguish the two directions explicitly. **Outward** topics cross service boundaries and
their payloads conform to the ecosystem contract (`commissioning-node-interfaces`). **Inward**
topics/types are local to this module and defined by its own `interface/`.

- **Consumes — outward (from the bus):** `system/tick` (`Tick`)
- **Consumes — inward (core → features):** `tailnet-gateway/overlay-snapshot` → `OverlaySnapshot`, stamped by core with `observed_at` and the tick's `period_s`, produced from the `OverlayStatusSource` adapter (`core/tailscale_cli.py`, read-only `tailscale status --json`)
- **Produces — outward (to the bus):** `tailnet/status` (`TailnetStatus`), `tailnet/peers` (`TailnetPeers`) — conforms to the ecosystem
  contract (`interfaces/` v0.3.0)
- **Produces — inward (features → core):** none yet
- **Config (ADR-0004):** declares `CONFIG_FIELDS` in `interface/config.py`: `hostname`, `tags`, `auth_key` (secret), all site-scoped. Reads them through the `ConfigView` seam, injected with the writer (ADR-0003 step 6); nothing reads them yet.
- **Versions (spec §4 dual versioning):** release `VERSION` 0.3.0; inward
  contract `__version__` 0.3.0. Both live in `interface/__init__.py`; see
  `interface/README.md`. The inward contract follows the same additive rules as the ecosystem
  contract, with a one-repo blast radius.

## Features

| Feature | Status | Built on interface | Notes |
|---------|--------|--------------------|-------|
| `status-report` | enabled | 0.1.0 | Overlay snapshot → `TailnetStatus`. Read-only. |
| `peer-report` | enabled | 0.2.0 | Overlay snapshot → `TailnetPeers`, every tick, connected or not (an empty table when disconnected). Feeds the dashboard's caller identity (ADR-0003). Read-only. |

## Rules for agents working here

1. Core is I/O only; all logic in `features/`, behind the `Feature` protocol.
2. Features never import each other or the core's internals — only `interface/`. This
   includes the module name constant `MODULE`, which lives in `interface/__init__.py`
   (import `from ..interface import MODULE`; package path `modules.tailnet_gateway`), never in `core/`.
3. Need new fields? Bump interface MINOR **on dev first**, then branch (spec §4).
4. Merge early, register disabled (spec §8.4) beats long feature branches.
5. Watch for the middleman smell (spec §4): if this module starts carrying data only
   one feature needs, that feature is a graduation candidate.
6. External I/O goes behind a vendor-neutral seam (spec §4): a protocol in `interface/`,
   one adapter per vendor in `core/`. A foreign runtime with its own bus is a sidecar
   reached only through this kind of bridge, with an explicit crossing allowlist (§6).
7. A derived fact other modules also need (a geometry, a mode predicate, "which source is
   the truth") is computed in ONE neutral canonical authority and imported, never
   recomputed here (§4).
8. If this module writes to anything (hardware, shared stores, other systems), its ADR
   follows the §11 ladder: one writer, one ownership door, dead-man levels, leases with no
   force door.
" section}
