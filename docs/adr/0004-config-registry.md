# ADR 0004: Config registry — declared fields, one store, secrets as files

- **Status:** Accepted
- **Date:** 2026-10-13
- **Deciders:** Austin Emerson
- **Shape:** standard (spec §7)

## Context

ADR-0003 step 5. The dashboard can now authorize writes (`authorize()`, ADR-0003 §4), but it
has nothing to write: `Action`s exist as a protocol and none ship. Step 6, the
`tailnet-gateway` writer, needs three operator-set values first: the Tailscale **hostname**,
the **tags**, and the **auth key** (D3, the first network secret).

Spec §6 sanctions exactly one bypass of the bus: a declarative config registry, where
features declare fields and an operator surface reads and edits them. The registry may be
process-wide shared state, but only as a *documented deal* with its graduation cost named in
advance. §8.9 adds that secrets are flagged at declaration in that registry, masked in every
UI, never on the bus, never logged, and never given a second plumbing path.

Measured on this node (RevPi, 2026-10-13):

- `commissioning-node.service` runs as `admin` with `ProtectSystem=full` and no
  `StateDirectory=`. `/etc` is read-only to the daemon, which is what keeps it from rewriting
  `admin.cred` and the fallback TLS key (ADR-0003 §3). `/var/lib/commissioning-node` does not
  exist.
- `ProtectHome` is not set, so the checkout at `/opt/commissioning-node` is writable by the
  daemon. Writing state there would mix state with code, and ADR-0002 "Provenance" wants
  shipped nodes on a clean tagged checkout.
- Bus delivery is synchronous and in-process (§6), and every module already runs on the
  5 s tick.
- The §4 boundary checker has no tier for stateful shared plumbing. Neutral packages are
  stateless; bus-coupled infra may not be imported by modules.

## Decision

**D1–D3 were decided by the operator on 2026-10-13, taking the recommended option in each.**
The alternatives stay listed as the record of what was weighed. Everything after them is this
ADR's mechanism for those decisions.

### D1 — Where values persist

| Option | Assessment |
|--------|------------|
| **A. `StateDirectory=commissioning-node` → `/var/lib/commissioning-node/`** | systemd creates it 0700, owned by the daemon user, writable under `ProtectSystem=full`. `/etc` stays read-only, so the registry can't touch the admin credential or the TLS key. One unit line plus an install/doctor check. |
| B. `ReadWritePaths=/etc/commissioning-node` | Puts config next to the other node files, but lets the daemon rewrite `admin.cred` and the fallback key. That undoes the separation ADR-0003 relies on. |
| C. In the checkout (`/opt/commissioning-node/state/`) | No unit change, but state lives in the code tree. A `git clean` or a provenance re-checkout wipes it, and the checkout's owner can read it outside the daemon. |
| D. Memory only | Nothing to wipe, but a restart loses the hostname and tags before step 6 can apply them, and an auth key pasted before a crash is gone. |

### D2 — How a site teardown wipes them

- **Every field declares a `scope`, `site` or `node`, and the two scopes live in
  separate directories:**
  - `/var/lib/commissioning-node/site/` — wiped by teardown.
  - `/var/lib/commissioning-node/node/` — survives a move between sites.
- **Teardown is then one directory delete plus a restart**, and it can't miss a field. A new
  site-scoped field needs no change to the teardown procedure.
- **The default scope is `site`.** Carrying a site's data to the next site is the dangerous
  failure. Losing a node setting is the cautious one (§5 "enrichment declares the direction
  it fails").
- **The first three fields are all `site`.** Hostname and tags are per-tailnet, and the auth
  key belongs to one tailnet.
- The full site-teardown procedure (camera inventory, MediaMTX paths, logs) stays its own
  TODO. This ADR only guarantees that registry state is wiped by deleting `site/`.
- Alternative considered: a per-field wipe list in the teardown script. It is rejected
  because it drifts the moment someone adds a field and forgets the list.

### D3 — Accepting the graduation cost (§6 deal)

- **The deal:** at Stage 0 the registry is an in-process object that the composition root
  hands to modules. An extracted (Stage 1) service can't reach it.
- **The named cost at graduation** (per extracted module):
  1. The field *declarations* are already contract types (`ConfigField` in `interfaces/`,
     below). They do not move.
  2. The *store* moves behind the module's own process: each service owns
     `/var/lib/…/<module>/` on its host, read through the same `ConfigView` seam.
  3. The *operator surface* stops reaching the store directly. Non-secret edits become
     commands on the bus (`config/set`, through `invoke()` and capability checks, §5).
     Secrets never take that path. They are written by the owning service's own surface or
     provisioned per host (§8.9: never a second plumbing path, and never the bus).
  4. Estimated size: one adapter per module and one command topic. No feature changes,
     because features only see `ConfigView`.
- **Accepted.** A bus-based config path now would put the auth key on the bus or
  build a second secret path. Both are forbidden by §8.9.

### 1. Shape of the registry

```
interfaces/         ConfigField (declaration shape; additive, contract 0.3.0)
modules/<m>/interface/
                    FIELDS: list[ConfigField]       — what this module declares
                    ConfigView (Protocol)          — what its core/features may read
modules/dashboard/interface/
                    ConfigSurface (Protocol)       — list, masked read, write
config_registry/    the store: validation, atomic writes, secret files, change log
main.py             builds one registry from every module's FIELDS; hands each module its
                    own ConfigView and the dashboard the ConfigSurface
```

- **The declaration is contract.** `ConfigField` goes in `interfaces/` because it crosses a
  module boundary: `tailnet-gateway` declares and the dashboard renders. It is also the
  portable part at graduation (D3) and what the action manifest (§5) will carry. Fields:
  - `module`, `name` — the key is `module.name`, e.g. `tailnet-gateway.hostname`.
  - `kind` — an open `str` vocabulary: `str`, `int`, `bool`, `str_list`.
  - `secret: bool` — §8.9, flagged at declaration and nowhere else.
  - `scope` — `site` (default) or `node` (D2).
  - `label`, `help`, `default`, and constraints such as `max_len` and `pattern`.
  - `may_disturb: str | None` — a §11 rung-4 warning for the edit itself, if the edit
    disturbs anything.
  - No logic. Validation lives in the registry.
- **The store is a new top-level package, `config_registry/`, declared as bus-coupled infra
  in `tools/check_boundaries.py`.** It is stateful, so it is not neutral. It is wired by the
  composition root and imports no module, which is the bus-coupled tier's rule. It holds no
  bus reference: config is not a message. Modules never import it. They depend on the
  `ConfigView` protocol in their own `interface/` (the vendor-neutral seam, §4), and
  `main.py` injects the registry, as it already does for the bus.
- **Ownership:**
  - Each field has one owning module.
  - A module's `ConfigView` sees only its own fields.
  - **Only the owner can locate a secret.** `ConfigView.secret_path(name)` returns the file
    path so the core can pass `file:<path>` to tailscale (ADR-0003 §5). There is no method
    that returns a secret's value to anyone.
  - The dashboard's `ConfigSurface` lists every field and writes any field, but it reads
    secrets only as `set` or `unset`.
- **Reading is a level.** Owners read their `ConfigView` on each tick. There is no change
  callback and no "config changed" event, which fits the synchronous bus (§6) and §5's
  renewed levels. Whether the step-6 writer applies a change on the next tick, or only when
  an operator presses Apply, is step 6's Safety ADR.

### 2. On disk

```
/var/lib/commissioning-node/            0700 admin   (StateDirectory)
├── site/
│   ├── config.json                     non-secret values, all modules, schema_version
│   └── secrets/<module>.<name>         one file per secret, 0600
└── node/
    ├── config.json
    └── secrets/
```

- **Every write is temp file, fsync, rename** (§8.9: never half-written). One writer: the
  registry, behind a lock.
- **Only set values are stored.** An unset field reads as its declared default, so a new
  field with a default needs no migration.
- **On load, malformed is the default, loudly** (§4 defensive config resolution):
  - An unparseable `config.json`, a value that fails its field's validation, or an unknown
    key logs a warning and falls back to the default. Boot never crashes.
  - An unknown key is kept on disk, not deleted. A downgrade must not destroy a newer
    build's settings.
- **Secret files:**
  - They are written with a 0600 umask from the start, never chmod'ed afterwards.
  - CR and LF are stripped, and length is capped, before the write (§8.9 writers sanitize).
  - A secret file that isn't 0600 or isn't owned by the daemon user counts as unset. The
    registry refuses to use it and logs it, and the doctor warns.
  - **Clearing a secret deletes the file.** The owning module calls
    `ConfigView.clear_secret(name)`. Step 6 uses this to delete the auth key once
    tailscaled confirms the join (ADR-0003 §5).

### 3. The operator surface

- **The registry offers one `Action` per field** to the dashboard: `config.set` at
  `/actions/config/<module>/<name>`, and `config.clear` for fields with a default or secrets.
  Every one passes through `authorize()` like any other write (ADR-0003 §4). The registry
  adds no auth logic of its own.
- **The overview page gets a Config section**, grouped by module. Non-secret fields show
  their current value and default. Secret fields show `set` or `unset` and a write-only
  input that is never pre-filled.
- **Every applied change is logged** with who, door, field, old value and new value
  (ADR-0003 §4). A secret logs as `<secret set>` or `<secret cleared>`. The log is the
  journal only; a persisted audit trail is not in scope.
- **A rejected value is an in-band refusal** that says which constraint failed (§11 rung 7).
  The value is never echoed back for a secret.
- **Bounds (§8.11, provisional until ledgered):** `MAX_VALUE_LEN = 256`, `MAX_LIST_ITEMS =
  32`, `MAX_SECRET_LEN = 512`. Tailscale auth keys are around 60 characters. Measure on
  1.102.5 before ledgering.

### 4. The first fields (step 6 consumes them)

| Key | Kind | Secret | Scope | Constraint |
|-----|------|--------|-------|------------|
| `tailnet-gateway.hostname` | `str` | no | site | DNS label: `[a-z0-9-]{1,63}`, no leading/trailing `-` |
| `tailnet-gateway.tags` | `str_list` | no | site | each `tag:[a-z0-9-]+` |
| `tailnet-gateway.auth_key` | `str` | **yes** | site | starts `tskey-`; CR/LF stripped |

Declaring these fields is read-only and safe to merge before step 6. Nothing reads them until
the writer exists (§8.4 "merge early, register disabled").

## Options considered for the registry's structure

- **A. Store injected through per-module protocols (chosen, §1).** One implementation of
  atomic writes, secret handling and masking. Modules stay import-clean.
- **B. Config over the bus (`config/*` topics).** Rejected. §6 says config is not a message,
  and the auth key would cross the bus (§8.9).
- **C. Each module owns its own store and hands the dashboard its own `Action`s.** Rejected.
  Atomic writes, secret files and masking would be copied into every module. That is a second
  secret path (§8.9) and the duplicate-authority problem §4 forbids. The dashboard would also
  have no uniform list to render.
- **D. Registry as a neutral package imported by modules.** Rejected. Neutral packages must
  be stateless, and the store is state.

## Safety (spec §11)

- **Rungs 1–3:** the registry writes only its own files under the node's state directory.
  It has no effect on tailscaled, cameras or MediaMTX. Those stay with their bridge modules'
  writers (rung 9). An edit is not an actuation.
- **Rung 4:** a field may declare `may_disturb`. None of the first three needs it, because
  storing a hostname disturbs nothing. Step 6 puts the warning on the *apply* action, where
  the disturbance happens.
- **Rung 7:** every refusal is in-band with its reason.
- **Rung 10:** every write passes the one `authorize()`. The registry has no back door, and
  `ConfigView` is read-only except `clear_secret`, which only the owner holds.
- **Rung 12:** this ADR adds the first real `Action`s to the dashboard. They ship with their
  replacements already in place: `authorize()`, the change log, and the secret masking.

## Consequences

- **Easier:**
  - The operator can stage a new site's hostname, tags and key from a browser before
    anything is applied.
  - Any later feature (camera credentials, MediaMTX options) declares fields and gets the UI,
    the auth, the masking and the teardown for free.
- **Harder or accepted:**
  - A new tier member (`config_registry/`, bus-coupled) and a stateful object outside the
    bus. This is the one §6 exception, named here; a second one faces the same bar.
  - Secrets sit on the RevPi's SD card as files until cleared. There is no TPM (ADR-0003 §3).
    The auth key is short-lived by design (deleted after join), and ephemeral, single-use
    keys are recommended in the UI.
- **Contract change:** `ConfigField` in `interfaces/` → contract **0.3.0** (additive). Run
  `check_additivity`. Inward-version bumps for `tailnet-gateway` (`FIELDS`, `ConfigView`) and
  `dashboard` (`ConfigSurface`).
- **Ops changes:**
  - `StateDirectory=commissioning-node` and `StateDirectoryMode=0700` in the unit.
  - `ops/paths.sh` gains `CN_STATE=/var/lib/commissioning-node`.
  - The doctor checks the directory's owner and mode, and the secret files' modes.
- **Docs:** CLAUDE.md layout and tier line (`config_registry/`); `interface/README.md` for
  the two modules; TODO.
- **Revisit:**
  - A persisted audit log, if the journal's retention proves too short for a site's record.
  - Per-field apply semantics, in step 6's Safety ADR.
  - D3's cost estimate, if a module is extracted.

## Implementation order

1. ~~Accept this ADR with D1–D3.~~ Done 2026-10-13 (recommendations taken).
2. ~~Contract: `ConfigField` (0.3.0). Then `config_registry/` with its tier declaration and
   tests: atomic writes, malformed-file fallback, secret modes, CR/LF stripping, masking,
   unknown keys kept.~~ Done 2026-10-13.
3. ~~Ops: `StateDirectory=`, `paths.sh`, doctor checks.~~ Done 2026-10-13. Re-run `install.sh` on
   the node.
4. ~~Wiring~~ (done 2026-10-13; `ConfigView` is defined now and injected with step 6's
   writer, its first reader): `tailnet-gateway` declares the three fields and gets `ConfigView`; the dashboard
   gets `ConfigSurface`, the Config section and the `config.*` actions through `authorize()`.
   Security review (§8.10: approval surface and the first stored network secret).
5. Then step 6: the `tailnet-gateway` writer, with its own Safety ADR.
