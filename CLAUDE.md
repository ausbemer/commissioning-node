# commissioning-node — Workspace Context

> Stage 0 modular monolith following the Additive-Contract meta-architecture.
> Binding spec: `META-ARCHITECTURE.md`, **v0.4.0** (recorded in ADR-0001). Never
> edit it, and keep its version line; domain facts live here.

## Domain

commissioning-node runs on a Revolution Pi (Debian 13, Python 3.13). It is a Tailscale network
gateway that is dropped onto a site network for a commissioning and testing timeline, then
pulled off and reused somewhere else. While it is on site it gives remote access over the
tailnet, configures the site's Axis cameras through VAPIX, and relays their streams through a
MediaMTX server. Every deployment is a new network, so Tailscale configuration must be
adjustable by an operator but automatic where possible. A local dashboard is the config and
ops surface.

Trust boundary (ADR-0001): the tailnet, with a local admin-credential fallback for when
the tailnet is unavailable. Both doors exist (ADR-0003): the dashboard serves the local door
(loopback) and the tailnet door on 8080, and the TLS admin fallback on 8443.

## Architecture stage

**Stage 0** — one repo, one deployable, one owner. All spec disciplines are live
(additive contracts, bus-only communication, schema_version on the wire, capability
registry); all polyrepo ceremony is deferred. Graduation to Stage 1 happens when a
spec §10 trigger fires — see WORKFLOW.md → "Graduation".

Stack: Python 3.13 / Pydantic v2 (spec baseline is 3.11+; this node runs 3.13.5). Bus
delivery is synchronous and in-process; the eventual broker is undecided.

## Layout

| Path | Role |
|------|------|
| `interfaces/` | The shared contract package (future `commissioning-node-interfaces` repo). Additive-only; version `0.5.0`. All cross-module types live here and nowhere else. |
| `bus/` | In-process dispatcher. Same pub/sub API a real broker adapter will expose. |
| `modules/` | One directory per module, each with the spec §4 layout (interface/ core/ features/). |
| `main.py` | Composition root: composes modules onto the bus, owns the tick loop, the node card, capability aggregation and `invoke()`. Imports everything; imported by nothing. |
| `config_registry/` | Bus-coupled infra (§4), the one §6 bypass of the bus (ADR-0004): declared `ConfigField`s, values under the daemon's state directory (`site/` wiped at teardown, `node/` kept), secrets as 0600 files only their owner can locate. Wired by `main.py`; no module imports it. |
| `freshness/` | Neutral package (§4): the one staleness-window authority for renewed levels, `clamp(3 × period_s, min, max)`. Used by stream-relay and the dashboard. |
| `tools/check_additivity.py` | Contract shape check (§3/§8.1): strict superset, nested shapes typed or declared in `schemas/blind_spots.txt`. Run `check` in CI, `snapshot` at release. |
| `tools/check_boundaries.py` | Ontology check (§4/§8.1): every top-level package has a tier and keeps that tier's import rules. A new neutral or bus-coupled package is declared in its config sets. |
| `tests/` | Smoke test (bus round-trip), additivity test, boundary test (with planted violations), plus per-module unit tests. Keep all green. |
| `ci.sh` | The whole gate, the same locally and in CI. |
| `tools/ledgers/`, `tests/replay/` | Evidence for behaviour/safety constants and the replay corpus (§8.11/§8.12) — this node acts on the world. |
| `docs/quirks.md` | Vendor quirk registry for tailscaled, MediaMTX and Axis VAPIX (§7). |
| `ops/` | Ops tier (ADR-0002, ADR-0003): `install.sh` (idempotent, `--dry-run`; also the fallback TLS key pair and the Tailscale operator), `doctor.sh` (read-only), `set-admin-password.sh` (+ `admin_cred.py`, the scrypt credential writer), `pins.sh` (Tailscale 1.102.5, MediaMTX v1.21.1), `paths.sh` (host paths, incl. the config registry's state directory, + daemon user), MediaMTX config + its check, the scoped nftables firewall (MediaMTX ports and dashboard 8080; forwarding only from the tailnet; its unit also enables IPv4 forwarding for the subnet router once the table loads), and the systemd units. Never imported. |

**§4 tiers in this repo:** contract `interfaces/` · bus `bus/` · modules `modules/` ·
neutral packages `freshness/` · bus-coupled infra `config_registry/` · ops tier `ops/` · composition root `main.py`.

## Topics (contract v0.5.0)

Not on the wire: `ConfigField` (0.3.0) is a config declaration, shared by the module that
declares a field and the surface that edits it (ADR-0004).

| Topic | Payload | Producer → consumers |
|-------|---------|----------------------|
| `system/tick` | `Tick` (declares `period_s`) | `main.py` → tailnet-gateway, stream-relay |
| `system/card` | `NodeCard` (nested `ModuleInfo`) | `main.py` → dashboard |
| `camera/register` | `CameraRegistration` (command) | operator surface via `main.invoke()` → camera-config |
| `camera/announced` | `CameraAnnouncement` | camera-config → stream-relay, dashboard |
| `stream/announced` | `StreamAnnouncement` (nested `StreamEndpoint`) | stream-relay → dashboard |
| `tailnet/status` | `TailnetStatus` | tailnet-gateway → stream-relay, dashboard |
| `tailnet/peers` | `TailnetPeers` (nested `TailnetPeer`), declares `period_s` | tailnet-gateway → dashboard (caller identity, ADR-0003) |
| `tailnet/apply` | `TailnetApply` (command; no values, no secret) | dashboard via `main.invoke()` → tailnet-gateway writer (ADR-0005) |
| `tailnet/apply-result` | `TailnetApplyResult` | tailnet-gateway → dashboard |

## Modules

| Module | Responsibility | Inward interface version |
|--------|----------------|--------------------------|
| `tailnet-gateway` (`modules/tailnet_gateway/`) | Bridge to tailscaled: reports tailnet status and the peer table now; will auto-detect the site subnet and propose routes for operator confirmation. Sole writer of Tailscale state. Declares its config fields (hostname, tags, auth key; ADR-0004); joins, renames, sets advertised routes (default: the detected site networks) and leaves the site tailnet on a confirmed request, and rejoins a recorded join on its own (ADR-0005; registered disabled until rehearsed). | 0.5.0 |
| `camera-config` (`modules/camera_config/`) | Bridge to Axis VAPIX: knows the site's cameras; will probe and (operator-confirmed) configure them. Sole writer to cameras. | 0.1.0 |
| `stream-relay` (`modules/stream_relay/`) | Bridge to MediaMTX: plans one relay path per camera and announces tailnet-only endpoints. Sole writer of MediaMTX config. | 0.1.0 |
| `dashboard` (`modules/dashboard/`) | Operator config + ops surface: HTTP view of node card, tailnet, cameras, streams and wire versions behind three doors (local, tailnet, TLS admin fallback); every write passes its one `authorize()` (ADR-0003); edits the config registry and applies the tailnet config at `/config` (ADR-0004, ADR-0005). | 0.6.0 |

Kebab-case is the module's name (logs, capability flags, tables); snake_case is its package
directory and import path.

## Rules for agents working here

1. Contracts only grow (spec §3): add optional fields, never rename/remove/retype.
   Run `python tools/check_additivity.py check` before committing contract changes.
2. Cross-module types go in `interfaces/` only. Module-internal types go in that
   module's `interface/`. Modules never import each other's internals.
3. Everything published on the bus is a dict carrying `schema_version`.
4. Cores are I/O only. Business logic lives in features implementing the module's
   `Feature` protocol.
5. Semantics never mutate (spec §8.6). New meaning → new field + deprecate the old.
6. Work on `dev`; `master` receives promotions.
7. Before claiming a cross-module capability exists, check the capability registry.
8. Nested shapes are models (subclass `Shape`), never bare dicts (spec §3). An untyped
   field that must stay is declared in `schemas/blind_spots.txt` with its reason.
9. New top-level package? Decide its §4 tier first and declare it in
   `tools/check_boundaries.py`. Neutral and bus-coupled packages sit at the top level, named
   for what they do; the ops tier lives in `ops/` and nothing imports it (§10). A derived
   fact several modules need gets ONE canonical authority (a neutral package), never a
   second implementation (§4).
10. Grants of liveness or authority are renewed levels, not one-shot edges (§5). Enrichment
    from a droppable feed names its safe default and fails toward it.
11. Acting on the world (hardware, shared stores, other people's systems) follows the §11
    ladder, and every behaviour/safety constant is measured and ledgered (§8.11).
12. Secrets are secrets from birth (§8.9): flagged at declaration, masked, never on the bus
    or in logs.
13. **Domain-specific:** the site LAN is untrusted. Never advertise a stream or surface on a
    site-LAN address; tailnet addresses only (ADR-0001). The one exception is the TLS admin
    fallback on 8443 (ADR-0003). Every request is sorted into a door and every write passes
    the dashboard's single `authorize()`; a new surface or write path goes through the same
    doors, never around them.
14. **Domain-specific:** Axis cameras belong to the site — the first write to any camera is
    operator-confirmed (§11 rung 3), and its warning appears in the action manifest (§5).
15. **Domain-specific:** MediaMTX's ports and the dashboard's 8080 are reachable only from the tailnet and loopback.
    The `inet commissioning_node` nftables table enforces this, and `mediamtx.service`
    refuses to run without it. Its forward chain lets only the tailnet open forwarded
    connections (the node is a subnet router, ADR-0005); forwarding is enabled only after it loads. Never `flush ruleset` and never enable Debian's
    `nftables.service` (ADR-0002). A new MediaMTX server or port also means a firewall entry.
16. Vendor calls (tailscale, VAPIX, MediaMTX) live only in their bridge module's `core/`
    adapter behind an `interface/` seam; every silent vendor behaviour you measure goes in
    `docs/quirks.md`.

## Skills to reach for

Reach for these installed skills at the matching moment (they encode the workflow, not just
capabilities):

| Moment | Skill |
|--------|-------|
| Making or recording a design decision (tech choice, feature graduation, pulling the transport-tiering lever) | `engineering:architecture` — writes the ADR into `docs/adr/` (spec §7) |
| Before merging a feature to `dev` | `engineering:code-review` — check additive-contract compliance and the forwarding-trap rule (§8.2) |
| Designing a new module's boundaries, or a graduation | `engineering:system-design` |
| Planning tests for a feature or a contract change | `engineering:testing-strategy` |
| Writing `interface/README.md`, a CLAUDE.md, or WORKFLOW.md | `engineering:documentation` |
| Deprecations piling up — is it time to schedule a MAJOR? (§8.3) | `engineering:tech-debt` |
| Standing guardrails (nightly "every pin references a real tag", drift checks) | `schedule` |
| Debugging a failure that isn't obvious | `engineering:debug` |

At Stage 1 these also apply: `engineering:deploy-checklist` (releasing via the deploy repo),
`engineering:incident-response` and `engineering:standup` (running services, multiple owners).

## Current focus

See `TODO.md`. Decisions and their reasons: `docs/adr/`.
