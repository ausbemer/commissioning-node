# dashboard — Module Context

> Module of the commissioning-node monolith
> following the spec §4 structure standard.

## Role in the ecosystem

Operator config and ops surface: an HTTP view (`/` and `/api/state`) of the node card (module versions, capabilities), tailnet status, cameras, planned streams, and the `schema_version` seen on the wire per topic against the pinned contract (§8.5). It serves three doors (ADR-0003): **local** (8080 on loopback), **tailnet** (8080 on any other address; the caller must resolve through `PeerDirectory`, and the firewall drops 8080 off `lo`/`tailscale0`), and the **admin fallback** (8443, TLS, admin password; starts only when `admin.cred` and the TLS pair are usable). Every write is an `Action` behind the one `authorize()`. The first ones edit the config registry (ADR-0004): a set and a clear per declared field, from the `/config` page. The collector is a terminal consumer (§8.2) and never re-publishes.

## Interfaces

Distinguish the two directions explicitly. **Outward** topics cross service boundaries and
their payloads conform to the ecosystem contract (`commissioning-node-interfaces`). **Inward**
topics/types are local to this module and defined by its own `interface/`.

- **Consumes — outward (from the bus):** `system/card` (`NodeCard`), `tailnet/status`, `tailnet/peers` (`TailnetPeers`), `camera/announced`, `stream/announced`
- **Consumes — inward (core → features):** `DashboardState` (shared live-state object, written by `state-collector`, read by `core/http_surface.py`); `PeerDirectory` (tailnet caller resolver, written by `peer-directory`, held by `core/http_surface.py` for the tailnet door, ADR-0003)
- **Produces — outward (to the bus):** none — conforms to the ecosystem
  contract (`interfaces/` v0.3.0)
- **Produces — inward (features → core):** `View` renderings served by core (`overview-page` at `/`, `login-page` on the fallback, `config-page` at `/config`); `Action`s (`config-editor`: set + clear per config field)
- **Consumes — inward (core):** `ConfigSurface` (the config registry's operator surface, injected by `main.py`; ADR-0004); `AdminAuth` (fallback credential, sessions, login limits) and the pure door functions in `interface/doors.py`
- **Versions (spec §4 dual versioning):** release `VERSION` 0.4.0; inward
  contract `__version__` 0.4.0. Both live in `interface/__init__.py`; see
  `interface/README.md`. The inward contract follows the same additive rules as the ecosystem
  contract, with a one-repo blast radius.

## Features

| Feature | Status | Built on interface | Notes |
|---------|--------|--------------------|-------|
| `state-collector` | enabled | 0.1.0 | Latest-value-per-key store of card/tailnet/camera/stream + wire versions. Terminal consumer. |
| `peer-directory` | enabled | 0.2.0 | Latest `TailnetPeers` → `PeerDirectory`. Resolves a tailnet address to an identity only while fresh (`freshness`); every failure resolves to nobody, with a reason. Terminal consumer. |
| `overview-page` | served | 0.3.0 | Server-rendered HTML `View` at `/`, no JavaScript, all values escaped. Shows who is signed in and via which door; on the fallback, a log-out form. |
| `login-page` | served (fallback only) | 0.3.0 | The admin fallback's login form. No JavaScript; the message is escaped. |
| `config-editor` | served | 0.4.0 | A set and a clear `Action` per declared config field (`/actions/config/<module>/<name>[/clear]`). Records who and which door; the registry validates. A secret's text goes straight to the registry. |
| `config-page` | served | 0.4.0 | `/config`: one form per field, no auto-refresh, no JavaScript, values escaped. Secrets are write-only password inputs that read as set/unset. Success returns here via `then` (only a served page is honoured). |

## Rules for agents working here

1. Core is I/O only; all logic in `features/`, behind the `Feature` protocol.
2. Features never import each other or the core's internals — only `interface/`. This
   includes the module name constant `MODULE`, which lives in `interface/__init__.py`
   (import `from ..interface import MODULE`; package path `modules.dashboard`), never in `core/`.
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
