# ADR 0009: One-click node update from the dashboard

- **Status:** Accepted 2026-10-25 (Austin Emerson); steps 1 and 2 built the same day
- **Date:** 2026-10-25
- **Deciders:** Austin Emerson
- **Shape:** standard, with a Safety section (spec §7, §11)

## Context

Updating a node today takes an SSH session: `git pull`, `sudo ops/install.sh` (which ends
with the doctor), and a daemon restart from `/config`. The operator wants the Overview to
say when an update is available and to apply it with one confirmed click: fetch, install,
restart what needs it, run the doctor (operator, 2026-10-25).

What makes this more than a button:

- **The installer runs as root, and the checkout belongs to the daemon user.** The daemon
  runs as `pi`, the owner of `/opt/commissioning-node` (`ops/paths.sh`). If the dashboard
  could make root run `install.sh` from that tree, anything that can write as `pi` (a
  compromised daemon included) could edit `install.sh` and then press Update: root. ADR-0002
  A4 hit the same trap on a smaller scale and answered it with a root-owned copy. Here the
  whole tree is what root runs, so root needs a whole tree of its own.
- **Provenance (ADR-0002 Consequences, TODO):** the daemon runs whatever is checked out, and
  nothing on the node says which commit that is.
- **Nodes are on someone else's uplink.** A fetch can fail or be slow, and an update that
  bumps a pin restarts tailscaled, which drops the operator's tailnet page for a moment.

Operator decisions (2026-10-25):

1. **Channel:** selectable per node, tagged releases or the tip of `dev`.
2. **Trust:** GitHub. A commit on the channel, fetched over HTTPS from
   `github.com/ausbemer/commissioning-node`, is genuine. No signature check.
3. **Layout:** a separate root-owned release checkout. `/opt/commissioning-node` stays the
   developer's checkout as `pi`.

## Decision

### D1 — Root runs only code that root fetched

- **The release checkout.** `UPDATE_TREE` (`/opt/commissioning-node-release`, root, 0755)
  is a clone root made over HTTPS (`UPDATE_REPO_URL`, the public repo: no credential on
  the node is involved). Root alone writes it, and the daemon user can read it. The units
  the updater installs run the daemon from it (`@REPO_ROOT@`).
- **The daemon user stops coming from the checkout's owner.** A root-owned tree would make
  `RUN_USER` root, which the installer refuses. The first install (a human at `sudo`)
  writes `DAEMON_USER_FILE` (`/etc/commissioning-node/daemon-user`, root, 0644). After
  that, `ops/paths.sh` reads it and only falls back to the checkout's owner when it's
  missing. Root is still refused.
- **Two ways to run, one updater.**
  - *Release mode:* the units point at `UPDATE_TREE`. This is where the button leads.
  - *Dev-tree mode:* a human ran `sudo ops/install.sh` from a developer checkout, as today.
    The units point at that tree. The button still works from here, and its effect is to
    switch the node to the release checkout at the channel's target. It never runs the
    developer tree's code as root. Running unpushed code stays a human act at `sudo`.
- **What's running is recorded by root.** Each install writes `RUNNING_FILE`
  (`/etc/commissioning-node/running.json`, root, 0644, with `schema_version`): the tree, the
  commit, the ref it came from (a tag, `dev`, or `local`), whether the tree was clean, and
  when. The daemon reads this file and never runs git on a tree it doesn't own (git refuses
  one owned by another user anyway). This closes the provenance TODO: the doctor and the
  Overview both show it.

### D2 — "Update available": the channel's newest commit whose CI passed

- **The channel** is a node-scoped config field `update_channel` (`releases` | `dev`,
  default `releases`; ADR-0004). It is node-scoped, so it survives a site teardown.
- **The target:**
  - `releases`: the highest `vX.Y.Z` tag.
  - `dev`: the tip of `dev`.
  - In both, the target is only a commit whose GitHub Actions run concluded `success`. A
    newer commit with red or pending CI isn't offered, and the panel says so ("v0.4.0
    exists; CI hasn't passed"). `dev` was red for ten days before 2026-10-25 without anyone
    noticing, and this is the one check that "trust GitHub" still allows.
- **Who checks:** a new bridge module, `node-updater` (`modules/node_updater/`), the only
  code that talks to GitHub. Its `core/` adapter runs `git ls-remote` over HTTPS for refs
  and reads the public Actions API for CI. Both are read-only and unauthenticated, behind a
  `ReleaseSource` seam. It checks every `UPDATE_CHECK_S` and when the operator presses
  Check now, and it publishes what it found.
- **Fails toward "nothing to offer"** (rule 10). If GitHub is unreachable, rate-limited
  (60 API calls an hour per IP, unauthenticated) or answers with something it can't read,
  no update is offered and the panel says why. A stale check is shown as stale: the
  freshness window applies.

### D3 — The click: a request with no code in it

- **The command.** `node/update` (`NodeUpdate`) goes through `main.invoke()` and the
  dashboard's single `authorize()` from any door (the LAN fallback included, for when the
  tailnet is what's broken). It carries the caller and door, and one value:
  `expected_sha`, the commit the operator was shown. No ref, URL, path or argument.
- **The request.** `node-updater`'s core writes it as `UPDATE_REQUEST`
  (`/var/lib/commissioning-node/updater/request.json`: daemon user, 0600,
  `schema_version`, channel, `expected_sha`, who and which door).
- **Root picks it up.** `commissioning-node-update.path` (`PathExists=` on the request)
  starts `commissioning-node-update.service` (root, oneshot). Its script is installed as a
  root-owned copy (`/usr/local/libexec/commissioning-node/update`), never run from a tree,
  as in A4.
- **The updater:**
  1. reads the request with `O_NOFOLLOW` and deletes it (a request is used once), and
     parses it strictly: a known `schema_version`, the channel enum, 40 hex digits;
  2. fetches into `UPDATE_TREE` and resolves the channel's target itself, by D2's rule.
     If the target isn't `expected_sha`, it refuses ("the channel moved since you looked;
     check again"), so the operator confirms exactly what is installed, like ADR-0005's
     `expected_key_sha256`;
  3. checks out that commit (detached) and runs that tree's `ops/install.sh`, then
     restarts the daemon (a code change alone doesn't change its unit file, so
     `install.sh` wouldn't restart it);
  4. runs that tree's `ops/doctor.sh`;
  5. writes `UPDATE_RESULT` and `RUNNING_FILE`.
- **The result lives in a root-owned directory** (`/var/lib/commissioning-node-update/`,
  root, 0755). Root never writes a file in a directory the daemon user owns, because a
  symlink planted there would point root's write anywhere. The daemon reads the result at
  start and on each tick, and publishes it as `node/update-result`. The Overview shows it
  as the Last action, so it survives the daemon's own restart in the middle of the update.

### D4 — A failed update puts the last version back

- If `install.sh` or the doctor FAILs, the updater checks out the commit `RUNNING_FILE`
  had, runs its `install.sh`, restarts the daemon and runs the doctor again. The result
  says "rolled back to <old>: <the first FAIL line>". Vendor pins roll back too: the
  installer installs exact versions with `--allow-downgrades`.
- If the rollback's doctor also FAILs, the updater stops and says so. The node stays in
  that state, and the LAN fallback (8443) is the way in. It never loops.
- If the node was in dev-tree mode, "the last version" is the developer tree. The rollback
  points the units back at it and runs root's copy of the installer logic against it, but
  never that tree's own `install.sh`. *Open:* whether this is worth building, or a failed
  first switch simply stays failed with the fallback as the way in. Decide at the build.

### D5 — The dashboard

- The Overview shows a banner when an update is available: running → target, the channel,
  when it was checked, and an **Update** slider (the Join pattern: slide to confirm). Its
  manifest warning: "Restarts the daemon (the dashboard drops for a few seconds), MediaMTX
  (camera streams drop), and tailscaled if a pin changes (the tailnet drops). Rolls back if
  the doctor fails."
- The banner also says when the node is in dev-tree mode (running `local`, the commit, and
  whether the tree is dirty), when the newest commit's CI hasn't passed, and when the check
  failed.
- `/config` gets the `update_channel` field and a Check now button.

### D6 — Contract (0.8.0, additive)

New topics:
- `node/update-status` (`NodeUpdateStatus`): channel, running (nested `NodeVersion`),
  available (`NodeVersion` or none), checked_at, problem, `period_s`;
- `node/update` (`NodeUpdate`, command);
- `node/update-check` (`NodeUpdateCheck`, command, no values);
- `node/update-result` (`NodeUpdateResult`).

Producer `node-updater`; consumer the dashboard. Nothing existing changes.

## Options considered

| Option | Why not |
|--------|---------|
| Root runs `install.sh` from `/opt/commissioning-node` | The daemon user writes that tree: root for anyone who can write as `pi` (Context). |
| A polkit or sudo rule letting the daemon start the update | Same hole, plus a standing privilege for the daemon; A4 and the TODO rule it out. The `.path` unit needs neither. |
| Signed tags, with the node pinning the operator's key | The best protection against a compromised GitHub account or deploy key. Declined for now (decision 2): it adds key management to every release. Adding it later only means a check in the updater and a pinned key in `ops/pins.sh`. |
| A `local` channel: the button installs the developer tree's committed `HEAD` | Anything running as `pi` could commit and then write the request (the daemon can skip `authorize()`): root. Considered with a sudo-only, expiring opt-in for bench nodes; deferred (operator, 2026-10-25). Until then local code is a human act: `sudo ops/install.sh` from the tree. |
| Make `/opt/commissioning-node` root-owned, one tree | Breaks developing on the node as `pi` (decision 3). |
| A full deploy pipeline (Stage 1 deploy repo, spec §10) | That's what this becomes at graduation. At Stage 0, one repo and one owner, it would be ceremony without a second consumer. |

## Safety (spec §11)

- **Rung 1:** a new writer. Its capability flag, `node-updater:apply`, is registered
  disabled (§8.4) until the rehearsal passes. The check and the banner ship enabled, since
  they're read-only.
- **Rungs 2–3:** the node is owned (ADR-0003), and no site system is touched. Every update
  is an operator act: nothing updates on its own, and there's no auto-update.
- **Rung 4:** may-disturb, confirmed, with its warning on the action for the manifest (D5).
- **Rung 5:** one update at a time. systemd runs one instance of the service. A request
  made during a run is picked up after it, and then finds nothing to do. There's no force
  door. The run is bounded by `UPDATE_TIMEOUT_S` (`TimeoutStartSec=`), and a timed-out run
  is reported as failed and rolls back (D4).
- **Rung 6:** the safe state is "running the last version, doctor clean". D4 returns to it,
  and the LAN fallback is the way in when it can't.
- **Rung 7:** every refusal is in-band with its reason: flag off, check stale, CI not green,
  the channel moved, dev tree dirty (for display only, never a block), busy, timed out,
  rolled back, rollback failed.
- **Rung 9:** the root updater is the only writer of `UPDATE_TREE`, `RUNNING_FILE` and the
  installed units' tree. `node-updater` is the only code that talks to GitHub, and the only
  writer of the request.
- **Rung 10:** `authorize()` is the one door. The request records who and which door, and
  the updater logs both with the outcome.
- **Rung 11:** the update check is a renewed level (freshness), not a one-shot edge. An
  update is an act, not a lease.
- **Rung 12:** this removes "only a human at `sudo` changes the node's code". Its
  replacements ship in the same change: the root-owned tree, the root-owned updater, the
  request with no code in it, the expected-commit check, CI green, the doctor gate,
  rollback, and the flag registered disabled.
- **Rung 13:** root re-checks everything the daemon wrote: the request's shape, the channel,
  and the target against `expected_sha`.
- **Constants (§8.11, PROVISIONAL until ledgered in `tools/ledgers/node-updater/`):**
  `UPDATE_CHECK_S = 1800` (well inside the API's 60 an hour), `UPDATE_TIMEOUT_S = 900` (a
  guess: no install has been timed yet), and the update's own downtime. All are measured in
  the rehearsal.

## Consequences

- **Accepted risk (decision 2): GitHub is the root of trust.** Anyone who can push to `dev`
  or create a tag in the repo can get root on every node following that channel, once an
  operator presses Update. This node's own deploy key has write access, which makes it
  such a credential: one stolen field node could push to every `dev`-channel node.
  Mitigations short of signing:
  - Give field nodes **read-only** deploy keys, or none: the update fetches over HTTPS and
    needs no key.
  - Keep field nodes on `releases`.
  - An update needs a click, and CI must be green.
  - Revisit signed tags (Options) before more than one person can push.
- **This node becomes two trees.** `/opt/commissioning-node` (dev, `pi`) and
  `/opt/commissioning-node-release` (root). Which one runs is in `running.json` and on the
  Overview.
- **Every install now runs as a clone root owns, except a human's `sudo`.** `install.sh`
  learns to write `daemon-user` and `running.json`, and `paths.sh` learns to read
  `daemon-user`.
- **A pin bump through the button restarts tailscaled.** The operator's tailnet page drops,
  then comes back. The result is still there afterwards (D3).
- **Graduation (spec §10):** this updater is the seed of the Stage 1 deploy path, so keep
  its seam (`ReleaseSource`) vendor-neutral.

## Implementation order

1. ~~`paths.sh` / `install.sh`: `daemon-user`, `running.json`, the doctor shows the running
   commit~~ — done 2026-10-25. The installer also refuses a developer checkout whose owner
   isn't the recorded daemon user, and runs git in it only as its owner (`tree_git`).
2. ~~Contract 0.8.0 and `node-updater`, read-only: the check, `node/update-status`, the
   Overview banner (no button yet), `update_channel` on `/config`~~ — done 2026-10-25.
   As built:
   - `node/update-check` is in 0.8.0 too; `node/update` and its result wait for step 3.
   - The one target rule is `choose_target` in node-updater's `interface/`. Step 3's root
     updater must reuse it; if ops can't import it from the release tree, it moves to a
     neutral package first, never a copy.
   - A check makes at most 4 API calls. A rate-limited failure waits the full 30 minutes
     before retrying (quirks Q-011, `tools/ledgers/node-updater/`).
   - Found on the first live check: `v0.2.0`'s commit failed CI, so `releases` offers
     `v0.1.0`, which is older than anything running, so nothing is offered. A clean
     `v0.2.1` tag would make the channel useful.
3. The root updater (`ops/update.sh` → its root-owned copy, `.path` + `.service`), the
   request and result files, rollback; `node/update` behind `node-updater:apply`,
   registered disabled.
4. Rehearsal on this node: dev-tree → release switch, an update across a pin change, a
   forced doctor FAIL → rollback, a refusal because the channel moved. Ledger the timings,
   then enable the flag.
