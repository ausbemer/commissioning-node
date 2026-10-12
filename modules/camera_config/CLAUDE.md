# camera-config — Module Context

> Module of the commissioning-node monolith
> following the spec §4 structure standard.

## Role in the ecosystem

Bridge to Axis VAPIX (spec §6): the only module that talks to cameras and their single writer (§11 rung 9). Today it turns an operator's `camera/register` command into a `camera/announced` message and touches no camera. Next comes a read-only VAPIX probe (model, firmware) and discovery that proposes cameras (rung 2). Writes come after that, with a Safety ADR. The cameras belong to the site, so the first write to each is operator-confirmed (rung 3). Camera credentials are secrets (§8.9) and never appear on the bus.

## Interfaces

Distinguish the two directions explicitly. **Outward** topics cross service boundaries and
their payloads conform to the ecosystem contract (`commissioning-node-interfaces`). **Inward**
topics/types are local to this module and defined by its own `interface/`.

- **Consumes — outward (from the bus):** `camera/register` (`CameraRegistration`)
- **Consumes — inward (core → features):** none yet
- **Produces — outward (to the bus):** `camera/announced` (`CameraAnnouncement`, `origin="camera-config/camera-registry"`) — conforms to the ecosystem
  contract (`interfaces/` v0.1.0)
- **Produces — inward (features → core):** none yet
- **Versions (spec §4 dual versioning):** release `VERSION` 0.1.0; inward
  contract `__version__` 0.1.0. Both live in `interface/__init__.py`; see
  `interface/README.md`. The inward contract follows the same additive rules as the ecosystem
  contract, with a one-repo blast radius.

## Features

| Feature | Status | Built on interface | Notes |
|---------|--------|--------------------|-------|
| `camera-registry` | enabled | 0.1.0 | Registration command → announcement. Read-only toward cameras. |

## Rules for agents working here

1. Core is I/O only; all logic in `features/`, behind the `Feature` protocol.
2. Features never import each other or the core's internals — only `interface/`. This
   includes the module name constant `MODULE`, which lives in `interface/__init__.py`
   (import `from ..interface import MODULE`; package path `modules.camera_config`), never in `core/`.
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
