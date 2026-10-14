# dashboard — Module Context

> Module of the commissioning-node monolith
> following the spec §4 structure standard.

## Role in the ecosystem

Operator config and ops surface: an HTTP view (`/` and `/api/state`) of the node card (module versions, capabilities), tailnet status, cameras, planned streams, and the `schema_version` seen on the wire per topic against the pinned contract (§8.5). It serves three doors (ADR-0003): **local** (8080 on loopback), **tailnet** (8080 on any other address; the caller must resolve through `PeerDirectory`, and the firewall drops 8080 off `lo`/`tailscale0`), and the **admin fallback** (8443, TLS, admin password; starts only when `admin.cred` and the TLS pair are usable). Every write is an `Action` behind the one `authorize()`. The one GET with an effect, `/theme/<light|dark>`, only sets a display cookie in the caller's browser; it is sorted into a door but is not a write (ADR-0003 amendment). They edit the config registry (ADR-0004): a set and a clear per declared field, from the `/config` page; and they ask tailnet-gateway to join, rename, set routes or leave (ADR-0005) through the injected `Invoker` (`main.invoke`, §5), Join and Leave from the overview's slider, Rename and Routes beside their fields on `/config`. Pages may load the module's own scripts from `static/` (ADR-0006: `script-src 'self'`, `connect-src 'self'` for the refresh and theme, no inline script, every page works without it, CI parses them); core serves `static/` as an allowlist behind the doors. The collector is a terminal consumer (§8.2) and never re-publishes.

## Interfaces

Distinguish the two directions explicitly. **Outward** topics cross service boundaries and
their payloads conform to the ecosystem contract (`commissioning-node-interfaces`). **Inward**
topics/types are local to this module and defined by its own `interface/`.

- **Consumes — outward (from the bus):** `system/card` (`NodeCard`), `tailnet/status` (kept under one key, `self`), `tailnet/peers` (`TailnetPeers`), `tailnet/apply-result` (`TailnetApplyResult`, latest under `self`), `camera/announced`, `stream/announced`, `site/scan` (`SiteScan`, observed so the map can say a scan is running; never acted on), `site/hosts` (`SiteHosts`) and `site/scan-result` (`SiteScanResult`), latest of each under `self`, in memory only (ADR-0007)
- **Consumes — inward (core → features):** `DashboardState` (shared live-state object, written by `state-collector`, read by `core/http_surface.py`); `PeerDirectory` (tailnet caller resolver, written by `peer-directory`, held by `core/http_surface.py` for the tailnet door, ADR-0003)
- **Produces — outward (to the bus):** `tailnet/apply` (`TailnetApply`) and `site/scan` (`SiteScan`), commands, via the injected `Invoker` — conforms to the ecosystem
  contract (`interfaces/` v0.6.0)
- **Produces — inward (features → core):** `View` renderings served by core (`overview-page` at `/`, `login-page` on the fallback, `config-page` at `/config`); `Action`s (`config-editor`: set + clear per config field; `tailnet-apply`: join, rename, leave; `site-scan`: scan)
- **Consumes — inward (core):** `ConfigSurface` (the config registry's operator surface, injected by `main.py`; ADR-0004); `AdminAuth` (fallback credential, sessions, login limits) and the pure door functions in `interface/doors.py`
- **Versions (spec §4 dual versioning):** release `VERSION` 0.10.1 (0.10.1: the map marks a scan from another network); inward
  contract `__version__` 0.10.0 (`chrome.py`, the shared page frame; 0.8.0: `op_form`; 0.9.0: site operations, `SITE_SCANNER`; 0.10.0: `PeerDirectory.endpoints()`). Both live in `interface/__init__.py`; see
  `interface/README.md`. The inward contract follows the same additive rules as the ecosystem
  contract, with a one-repo blast radius.

## Features

| Feature | Status | Built on interface | Notes |
|---------|--------|--------------------|-------|
| `state-collector` | enabled | 0.1.0 | Latest-value-per-key store of card/tailnet/camera/stream + wire versions. Terminal consumer. |
| `peer-directory` | enabled | 0.2.0 | Latest `TailnetPeers` → `PeerDirectory`. Resolves a tailnet address to an identity only while fresh (`freshness`); every failure resolves to nobody, with a reason. Since 0.10.0 it also gives fresh peers by direct endpoint (`endpoints()`), which core passes to views as `snapshot["tailnet_endpoints"]` for the site map (node names only): display only, never a caller's identity. Terminal consumer. |
| `overview-page` | served | 0.9.0 | Server-rendered HTML `View` at `/`, no JavaScript, all values escaped. In the shared frame (`chrome.py`): a summary line, the site map (0.9.0, ADR-0007: tailnet → this gateway → a bus per site network with a tile per host the last scan found, flagged first — registered camera, Axis by maker, tailnet traffic from that address now (0.10.0; the peer or a NAT in front of it, never outranking a camera) — then by address; ARP-only dashed, a registered camera that did not answer dotted; past `MAP_TILES` the rest fold into a table; the scan's age and who ran it, never "up"; a failed or refused scan says why; while a requested scan has no result (up to `SCAN_AWAIT_S`) the slider reads "Scanning since …", so the page keeps refreshing; HTML tiles on CSS wires, no SVG), then cameras / this gateway / streams, states as chips; modules and wire versions folded under System, opened by a link (`/?system=open`, so the 5 s refresh keeps it open) and by itself on a skew. The gateway column has one tailnet control and no notes: a slider reading Leave the Tailnet when joined, Join the Tailnet when not (ADR-0006); the last result is a fact row (Last action). Refresh is by script, swapping the banner and `<main>` in place (scroll kept; paused mid-slide or with a confirm box ticked; ADR-0006 amendment), or `<noscript>` meta refresh. The day/night slider switches in place by script. Who is signed in, and on the fallback a log-out form, are in the banner. |
| `login-page` | served (fallback only) | 0.7.0 | The admin fallback's login form, in the shared frame. No JavaScript; the message is escaped. |
| `config-editor` | served | 0.4.0 | A set and a clear `Action` per declared config field (`/actions/config/<module>/<name>[/clear]`). Records who and which door; the registry validates. A secret's text goes straight to the registry. |
| `tailnet-apply` | served | 0.8.0 | Join, Rename, Routes, Routes off, Leave `Action`s, each may-disturb with its warning (`TAILNET_OPERATIONS`). Sends `tailnet/apply` through `invoke()` with `requires=tailnet-gateway:tailnet-writer`; carries no values. |
| `site-scan` | served | 0.9.0 | `site.scan` `Action` (`SITE_OPERATIONS`), may-disturb with the probe warning. Sends `site/scan` through `invoke()` with `requires=site-survey:scanner`; carries no targets (ADR-0007 D6). |
| `service-restart` | served | 0.8.0 | `node.restart` `Action` (`RESTART_PATH`), may-disturb: asks main to restart the service (it exits 75 and systemd starts it again; ADR-0003 amendment). Only built when main injects `restart`. |
| `config-page` | served | 0.8.0 | `/config`: one form per field, no auto-refresh, no JavaScript, values escaped. Secrets are write-only password inputs that read as set/unset. Success returns here via `then` (only a served page is honoured). 0.5.0: the Tailnet panel (node vs saved, the operations that make sense now, the last result). 0.6.0: routes (to advertise, advertised, approved), auto-rejoin state, the kept-key note. 0.7.0: shared frame; dense layout (module index, one block per module, one grid row per field); the Tailnet panel moved to the overview.. 0.8.0: Rename beside the hostname, Routes beside the advertised routes (with what `auto` means now, advertised and approved; a slider like Leave: Stop advertising routes while any are advertised, else Advertise the saved ones), a tags note; each may-disturb with its warning inline (`op_form`); the routes slider is compact. The tailnet rows are `[data-live]` regions: a confirmed slide posts in the background and swaps them in once the outcome shows (`data-last-apply`); a failure shows under its field. The index (left) holds a compact Restart service slider when `node.restart` is wired (`snapshot["writes"]`); the script waits for the service to go away and come back, then reloads. |

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
