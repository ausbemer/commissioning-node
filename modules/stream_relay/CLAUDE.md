# stream-relay — Module Context

> Module of the commissioning-node monolith
> following the spec §4 structure standard.

## Role in the ecosystem

Bridge to MediaMTX (spec §6): MediaMTX is the provisioned data plane, and this module is the only writer of its config (§11 rung 9). Today it plans one relay path per announced camera and announces it with `live=false`, advertising endpoints **only on a fresh tailnet address**. The safe default is no endpoints, never a site-LAN address (§5, ADR-0001). Freshness comes from the tick's declared cadence. Next it gets the MediaMTX API adapter, which writes paths and flips `live` once they are actually serving.

## Interfaces

Distinguish the two directions explicitly. **Outward** topics cross service boundaries and
their payloads conform to the ecosystem contract (`commissioning-node-interfaces`). **Inward**
topics/types are local to this module and defined by its own `interface/`.

- **Consumes — outward (from the bus):** `camera/announced` (`CameraAnnouncement`), `tailnet/status` (`TailnetStatus`), `system/tick` (`Tick`)
- **Consumes — inward (core → features):** none yet
- **Produces — outward (to the bus):** `stream/announced` (`StreamAnnouncement`, `origin="stream-relay/relay-planner"`) — conforms to the ecosystem
  contract (`interfaces/` v0.1.0)
- **Produces — inward (features → core):** none yet
- **Versions (spec §4 dual versioning):** release `VERSION` 0.1.0; inward
  contract `__version__` 0.1.0. Both live in `interface/__init__.py`; see
  `interface/README.md`. The inward contract follows the same additive rules as the ecosystem
  contract, with a one-repo blast radius.

## Features

| Feature | Status | Built on interface | Notes |
|---------|--------|--------------------|-------|
| `relay-planner` | enabled | 0.1.0 | Camera → planned path + tailnet RTSP endpoint. Terminal consumer of its inputs; builds new announcements (§8.2 producer). |

## Rules for agents working here

1. Core is I/O only; all logic in `features/`, behind the `Feature` protocol.
2. Features never import each other or the core's internals — only `interface/`. This
   includes the module name constant `MODULE`, which lives in `interface/__init__.py`
   (import `from ..interface import MODULE`; package path `modules.stream_relay`), never in `core/`.
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
