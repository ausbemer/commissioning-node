# dashboard Inward Interface — v0.11.0

The stable contract between this module's core and its features (spec §4).
Additive rules apply: MINOR adds optional things, MAJOR is a rare planned event.

**Two versions, two meanings (spec §4 dual versioning).** `__version__` (here,
`0.11.0`) is the *inward contract* version: it bumps only when this `interface/`
package changes. `VERSION` is the module's *release* version: it bumps with features and
implementation. A module shipping features behind an unchanged interface bumps `VERSION`
only.

## Package exports (`interface/__init__.py`)

This package is the ONLY thing a feature may import from (spec §4: features never import
`core` internals). It exports:

- `MODULE` — the module name constant (kebab-case, e.g. `"dashboard"`), used for
  capability flags and logging. Lives here, not in `core/`, precisely so features can use
  the module identity without reaching into core.
- `VERSION` — the module release version (`0.11.0`).
- `__version__` — the inward interface semver (`0.11.0`).
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
| `DashboardState` | 0.1.0 | Thread-safe bounded latest-value store shared by collector and HTTP core (§4 "features meet in interface objects"). 0.5.0 adds the kind `tailnet_apply`; `tailnet` and `tailnet_apply` hold one entry, keyed `self` |
| `PeerDirectory`, `PeerIdentity`, `Unresolved` (`identity.py`) | 0.2.0 | The one tailnet caller resolver (ADR-0003 §2): latest peer table, fresh by the producer's cadence, fails toward deny with a reason (`stale`, `unknown`, `ambiguous`, `invalid`) |
| `View` (protocol, `views.py`) | 0.1.0 | A feature-supplied page: `path`, `content_type`, pure `render(snapshot)`. Since 0.3.0 the snapshot passed to an HTML view may carry `viewer` (`label`, `door`, `csrf`); since 0.10.0 also `tailnet_endpoints` (address → `{node_name}`, from `PeerDirectory.endpoints()`; node names only, no logins) |
| `parse_address` (`identity.py`) | 0.3.0 | Socket address → IP (zone stripped, IPv4-mapped unwrapped); the one parser every door check uses |
| `Action`, `ActionResult` (protocol, `actions.py`) | 0.3.0 | A feature-supplied write: `name`, POST `path`, `may_disturb` (rung-4 warning or None), `run(form, caller)`. Core calls `run` only after `authorize()` |
| `classify`, `host_allowed`, `same_origin`, `authorize`, `Caller`, `Refusal`, `Decision`, door names (`doors.py`) | 0.3.0 | The three doors and the one ownership check (ADR-0003 §1, §4). Pure functions. `host_allowed` accepts the arrival address, `localhost` on loopback, and only exact `node_names` (rebinding) |
| `ConfigSurface`, `ConfigFieldState`, `ConfigWriteResult` (protocols, `config.py`) | 0.4.0 | Seam to the config registry's operator surface (ADR-0004): list fields, read state (a secret has no value), set from operator text, clear. Injected by `main.py`; the dashboard never imports the registry |
| `config_key`, `set_path`, `clear_path`, `config_rows`, `CONFIG_ACTIONS` (`config.py`) | 0.4.0 | Pure helpers: a field's key and action paths, and its rows for a View's snapshot (`snapshot["config"]`, present only when a registry is wired) |
| `Invoker` (protocol), `TAILNET_WRITER`, `TAILNET_OPERATIONS` (`config.py`) | 0.5.0 | The §5 invoke seam (`main.invoke`, injected), and the tailnet operations' paths and may-disturb warnings (ADR-0005), shared by `tailnet-apply` and `config-page` |
| `SITE_SCANNER`, `SITE_OPERATIONS` (`config.py`) | 0.9.0 | The capability a site scan needs and the Scan's POST path and may-disturb warning (ADR-0007 D6), as `TAILNET_OPERATIONS`. `DashboardState` gains the kinds `site_hosts`, `site_scan` and `site_request`, one entry each, keyed `self` |
| `page`, `banner`, `slider`, `STYLE`, `NAV`, `tailnet_badge`, `THEME_PATH`, `THEME_COOKIE`, `THEMES`, `theme_choice`, `theme_return`, `SCRIPT`, `STATIC_PATH`, `op_form` (`chrome.py`) | 0.7.0; `op_form` 0.8.0 (0.9.0: site operations, `marker`) | The frame every HTML view shares: day/night colour tokens, base type, the banner (node, page links, signed-in caller and door, day/night slider) and common components. Pure; no script, no outside resource (CSP); values escaped. The slider links to `THEME_PATH`; core keeps the choice in `THEME_COOKIE` (ADR-0003 amendment) and HTML views receive it as `snapshot["theme"]`. Every page loads `SCRIPT` (ADR-0006: first-party, progressive enhancement); `refresh_s` reloads by script, or by a `<noscript>` meta refresh |
| `AdminAuth`, `Session` (`admin.py`) | 0.3.0 | Fallback login: scrypt credential (re-read on change; rotation ends sessions), in-memory sessions with idle/absolute expiry and a CSRF token; failures limited per IPv4 address or IPv6 /64; verifications run one at a time, first come first served (no global refusal, so an attacker cannot lock the operator out). Fails closed |

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

- v0.11.0 — `name_redirect()` (`doors.py`): where a GET on the node's MagicDNS name goes, the tailnet IP, or None; the name is never served (ADR-0008) (additive).
- v0.10.0 — `PeerDirectory.endpoints()`: fresh peers by their direct address, for the site map only, never for identity (ADR-0007 step 4) (additive).
- v0.9.0 — `SITE_SCANNER`, `SITE_OPERATIONS`; `op_form` takes a site operation, and `marker` (the outcome marker a confirmed slide waits on); state kinds `site_hosts`, `site_scan`, `site_request` (the last `site/scan` seen, with `received_at`) (additive, ADR-0007).
- v0.8.0 — `chrome.op_form`: a tailnet operation's may-disturb form, shared by the overview (Join/Leave as a slider) and `/config` (Rename and Routes beside their fields) (additive).
- v0.7.0 — `chrome.py`: the shared page frame (`page`, `banner`) for the dashboard redesign, and the day/night choice (`THEME_PATH`, `THEME_COOKIE`, `theme_choice`, `theme_return`); HTML views may receive `theme`, and `query` (the page URL's query, first value per key: display state a page keeps across its own auto-refresh); `SCRIPT` and `STATIC_PATH` for the module's own scripts (ADR-0006) (additive).
- v0.6.0 — `TAILNET_OPERATIONS` gains `routes`; the Join warning now says the key is kept (additive).
- v0.5.0 — `Invoker`, `TAILNET_WRITER`, `TAILNET_OPERATIONS`; state kind `tailnet_apply` (additive). The `tailnet` kind is keyed `self`, not by hostname (a rename no longer leaves a stale row).
- v0.4.0 — `ConfigSurface` and its helpers (`config.py`); HTML views may receive `config` (additive).
- v0.3.0 — `Action`, `ActionResult`, `doors` (`classify`, `host_allowed`, `same_origin`,
  `authorize`), `AdminAuth`, `Session`, `parse_address`; views may receive `viewer` (additive).
- v0.2.0 — `PeerDirectory`, `PeerIdentity`, `Unresolved` (additive).
- v0.1.0 — initial interface.
