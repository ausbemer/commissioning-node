# tailnet-gateway — Module Context

> Module of the commissioning-node monolith
> following the spec §4 structure standard.

## Role in the ecosystem

Bridge to tailscaled (spec §6): the only module that talks to Tailscale and its single writer (§11 rung 9). It reads the node's tailnet status on every tick through the vendor-neutral `OverlayStatusSource` seam and publishes it. Its writer (ADR-0005) joins, renames, sets advertised routes and leaves the site tailnet on an operator's confirmed request, and rejoins on its own a tailnet the operator joined when the node drops off (A2, with backoff), through the `OverlayWriter` seam (`core/tailscale_writer.py`), on a worker thread, one operation at a time; it is enabled since its rehearsal (2026-10-13); `CN_TAILNET_WRITER=0` in the daemon's environment is the off-switch, and a malformed value also turns it off. It detects the site networks (`SiteNetworkSource`, `core/site_networks.py`) and proposes them as the default advertised routes (`auto`), which the operator confirms (rung 2). Tailscale auth keys are secrets (§8.9).

## Interfaces

Distinguish the two directions explicitly. **Outward** topics cross service boundaries and
their payloads conform to the ecosystem contract (`commissioning-node-interfaces`). **Inward**
topics/types are local to this module and defined by its own `interface/`.

- **Consumes — outward (from the bus):** `system/tick` (`Tick`), `tailnet/apply` (`TailnetApply`, command; ADR-0005)
- **Consumes — inward (core → features):** `tailnet-gateway/overlay-snapshot` → `OverlaySnapshot`, stamped by core with `observed_at` and the tick's `period_s`, produced from the `OverlayStatusSource` adapter (`core/tailscale_cli.py`, read-only `tailscale status --json`)
- **Produces — outward (to the bus):** `tailnet/status` (`TailnetStatus`), `tailnet/peers` (`TailnetPeers`), `tailnet/apply-result` (`TailnetApplyResult`) — conforms to the ecosystem
  contract (`interfaces/` v0.5.0)
- **Produces — inward (features → core):** `WritePlan`s into the `WriterSlot` (`tailnet-writer` → `core/writer_worker.py`); core routes finished `WriteOutcome`s back on `tailnet-gateway/write-outcome` at the next tick
- **Config (ADR-0004):** declares `CONFIG_FIELDS` in `interface/config.py`: `hostname`, `tags`, `auth_key` (secret), `advertise_routes` (default `auto`), `auto_rejoin` (default on), all site-scoped. The writer reads them through the injected `ConfigView`. The key is kept (A1); the worker keeps a join record (`site/tailnet-gateway.join.json`, `core/join_record.py`) that auto-rejoin acts on.
- **Versions (spec §4 dual versioning):** release `VERSION` 0.5.1; inward
  contract `__version__` 0.5.1 (0.5.1: the auth key accepts an OAuth secret's options). Both live in `interface/__init__.py`; see
  `interface/README.md`. The inward contract follows the same additive rules as the ecosystem
  contract, with a one-repo blast radius.

## Features

| Feature | Status | Built on interface | Notes |
|---------|--------|--------------------|-------|
| `status-report` | enabled | 0.1.0 | Overlay snapshot → `TailnetStatus`. Read-only. |
| `tailnet-writer` | enabled (`CN_TAILNET_WRITER=0` disables) | 0.5.0 | Decides join / rename / routes / leave against a fresh overlay reading and the saved config (routes via `resolve_routes`), auto-rejoins a recorded join on `NeedsLogin` with backoff, hands one `WritePlan` at a time to core's worker, publishes every refusal and outcome as `TailnetApplyResult`. Never retries an operator request (ADR-0005). |
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
