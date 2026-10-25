# node-updater — Module Context

> Module of the commissioning-node monolith
> following the spec §4 structure standard.

## Role in the ecosystem

Bridge to GitHub (ADR-0009): the only module that talks to it. Read-only and unauthenticated: `git ls-remote` over HTTPS for the refs (hermetic: none of the daemon user's git config applies) and the public REST API for CI results and how two commits relate, through the vendor-neutral `ReleaseSource` seam (`core/github_source.py`). It reads what this node runs from the root-written record (`/etc/commissioning-node/running.json`, `RunningSource`, `core/running_record.py`) and never runs git on the tree. It checks the node's update channel (`update_channel`: `releases` or `dev`) on the first tick, every 30 min, sooner after a failure, when the channel changes and on the operator's Check now, one check at a time on a worker thread, and publishes what it found every tick. Only a commit whose CI passed is ever offered (`choose_target`, the one rule; the root updater of ADR-0009 step 3 must reuse it), and only a newer one is an update. It applies nothing: the confirmed update and the root updater are step 3.

## Interfaces

Distinguish the two directions explicitly. **Outward** topics cross service boundaries and
their payloads conform to the ecosystem contract (`commissioning-node-interfaces`). **Inward**
topics/types are local to this module and defined by its own `interface/`.

- **Consumes — outward (from the bus):** `system/tick` (`Tick`: the cadence, the schedule, and the drain of finished checks), `node/update-check` (`NodeUpdateCheck`, command, via `main.invoke()`)
- **Consumes — inward (core → features):** `node-updater/check-outcome` → `CheckOutcome`, routed from the `CheckSlot` on the tick after a check finishes; never subscribed on the bus
- **Produces — outward (to the bus):** `node/update-status` (`NodeUpdateStatus`, every tick, a renewed level) — conforms to the ecosystem contract (`interfaces/` v0.8.0)
- **Produces — inward (features → core):** `CheckPlan`s into the `CheckSlot` (`update-check` → `core/check_worker.py`)
- **Config (ADR-0004):** `update_channel` (node-scoped, default `releases`), read through the injected `ConfigView`.
- **Versions (spec §4 dual versioning):** release `VERSION` 0.1.0; inward
  contract `__version__` 0.1.0. Both live in `interface/__init__.py`; see
  `interface/README.md`. The inward contract follows the same additive rules as the ecosystem
  contract, with a one-repo blast radius.

## Features

| Feature | Status | Built on interface | Notes |
|---------|--------|--------------------|-------|
| `update-check` | enabled when main passes a release source (daemon mode); registered disabled otherwise | 0.1.0 | Schedules checks (`UPDATE_CHECK_S`, `UPDATE_RETRY_S`), declines an operator's check within `CHECK_MIN_GAP_S` of the last, hands one `CheckPlan` at a time to core's worker, and publishes `NodeUpdateStatus` every tick. A failed check offers nothing; `update_available` only when the offer is newer (`relation` "ahead"). |

## Rules for agents working here

1. Core is I/O only; all logic in `features/`, behind the `Feature` protocol.
2. Features never import each other or the core's internals — only `interface/`. This
   includes the module name constant `MODULE`, which lives in `interface/__init__.py`
   (import `from ..interface import MODULE`; package path `modules.node_updater`), never in `core/`.
3. Need new fields? Bump interface MINOR **on dev first**, then branch (spec §4).
4. Merge early, register disabled (spec §8.4) beats long feature branches.
5. Watch for the middleman smell (spec §4): if this module starts carrying data only
   one feature needs, that feature is a graduation candidate.
6. External I/O goes behind a vendor-neutral seam (spec §4): a protocol in `interface/`,
   one adapter per vendor in `core/`.
7. A derived fact other modules also need is computed in ONE neutral canonical authority and
   imported, never recomputed here (§4). What a channel offers is `choose_target`: the root
   updater (ADR-0009 step 3) reuses it, and if it must move to do so, it moves to a neutral
   package, never a copy.
8. This module is read-only and never authenticates. Applying an update is root's, behind a
   request with no code in it (ADR-0009 D1, D3), and its Safety section binds every change. A
   credential, a write to GitHub or to the tree, or running git on the tree needs an ADR
   amendment first.
