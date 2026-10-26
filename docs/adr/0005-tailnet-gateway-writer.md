# ADR 0005: tailnet-gateway writer — join, rename and leave the site tailnet

- **Status:** Accepted
- **Date:** 2026-10-13
- **Deciders:** Austin Emerson
- **Shape:** standard, with a Safety section (spec §7, §11)

## Context

ADR-0003 step 6. The operator can now save the Tailscale hostname, tags and auth key on
`/config` (ADR-0004), but nothing applies them. Today the node joins a tailnet only through
a manual `tailscale up` over SSH or the local console. This ADR makes `tailnet-gateway` the
writer of Tailscale state (CLAUDE.md: its sole writer, §11 rung 9). It is the project's
first actuation: the node changes a system outside its own process.

Measured on this node (RevPi, tailscale 1.102.5, logged out, 2026-10-13):

- **`tailscale set` has `--hostname` but no `--advertise-tags`.** Renaming works on a joined
  node; changing tags needs `tailscale up`.
- **`tailscale up --timeout` defaults to `0s`, which blocks forever** waiting for the
  `Running` state. Without an auth key, `up` on a logged-out node prints a login URL and
  waits for someone to open it.
- **`up --auth-key` accepts `file:<path>`** (`--help`). Whether the CLI or the daemon reads
  the file is not yet known, so neither is which user must be able to read it.
- **`up` refuses to drop a non-default setting it isn't told about**, unless given
  `--reset`. The operator (`admin`) is non-default here, so every `up` must repeat
  `--operator=admin` (installer note, `docs/quirks.md`).
- **Prefs now:** `OperatorUser=admin`, `Hostname=""`, `AdvertiseTags=null`,
  `WantRunning=false`, `LoggedOut=true`. The OS hostname is `Commissioning-node-pi5`.
- **Bus delivery is synchronous** (§6). A handler that runs `tailscale up` would stall the
  tick loop, or the HTTP request that triggered it, for as long as the join takes.

## Decision

**D1–D3 were decided by the operator on 2026-10-13, taking the recommended option in each. D4 is
the operator's requirement (2026-10-13).** The alternatives stay listed as the record of what
was weighed.

### D1 — When a saved value reaches tailscaled

| Option | Assessment |
|--------|------------|
| **A. An explicit, confirmed Apply** | Saving stores (ADR-0004). A separate action on `/config` applies the saved values once, after a may-disturb confirmation. A bad value or a dead key fails once, visibly, and is never retried on its own. |
| B. Continuous reconcile | The writer converges tailscaled to the registry on every tick (rung 6, "declared state restored"). It fights any manual `tailscale set`, and a single-use key that fails would be retried every 5 s against the site's control plane. |
| C. Apply on save | Every save is an actuation, so typing a hostname and then the tags would mean two disconnects. It also makes the save action may-disturb, so even a harmless edit needs confirmation. |

### D2 — Which operations ship

- **Three operations:**
  - **Join** (`tailscale up`): on a node that is logged out. It uses the saved auth key,
    hostname and tags. A join without a saved key is refused, never left waiting on a login
    URL.
  - **Rename** (`tailscale set --hostname`): on a joined node. It needs no key.
  - **Leave** (`tailscale logout`): drops the node from the site's tailnet and expires its
    node key. This is the tailnet half of site teardown.
- **Tags are applied at join only.** `set` can't change them (measured), and changing them on
  a joined node through `up` may need a re-authentication that is not yet measured. To retag:
  Leave, save a new key, Join. The Apply panel says this when the saved tags differ from the
  node's.
- Alternative considered: retag in place with `up --advertise-tags`. Deferred until it has
  been measured on a real tailnet, and recorded as a quirk.

### D3 — The auth key's lifetime

- **Decided:**
  - The key is deleted as soon as tailscaled reports `Running` after a join (ADR-0003 §5).
  - The key is **kept after a failed join**, so the operator can fix the hostname or tags and
    retry, or delete it. It is never retried automatically (D1).
  - `/config` shows a warning while a key is stored, and the doctor already warns (ADR-0004).
- Alternative considered: delete the key on any outcome. Rejected. A typo in a tag would then
  cost a fresh key from the site's admin console, and a reusable key would be no safer for
  being deleted.

### D4 — The node is an ephemeral device on every site tailnet

- **Requirement (operator):** the node joins each site as an *ephemeral* Tailscale device.
  The site's tailnet then removes it automatically after it has been offline for a while,
  and immediately on `tailscale logout`. A node pulled off site without a teardown does not
  linger in the site's admin console.
- **Ephemeral is a property of the auth key**, chosen when the key is created in the site's
  admin console. `tailscale up` has no flag for it, and the key string does not reliably say
  whether it is ephemeral. So the node can't enforce it before a join. It enforces it in
  three places:
  - The auth-key field's help and the Join warning say "use an ephemeral key".
  - **After a join, the writer checks whether tailscaled reports the node as ephemeral, if
    the status exposes that** (not yet measured; step 6 rehearsal). If it does and the node
    is not ephemeral, the result says so, and the panel shows a standing warning with the fix:
    Leave, then Join with an ephemeral key.
  - Leave (`tailscale logout`) removes an ephemeral node from the tailnet at once, which is
    the clean teardown.
- **The trade-off with D3, accepted for now:** an outage long enough for the tailnet to
  remove the node (site power cut, LAN outage) leaves it unable to rejoin on its own, because
  the key was deleted after the join. Recovery: the LAN fallback on :8443, a fresh key, Join.
  The alternative is to keep a *reusable* ephemeral key on disk and rejoin automatically
  when tailscaled reports the node gone. That is the continuous reconcile D1 rejected, plus
  a long-lived network secret on the SD card. **Revisit if outages on site make this
  painful.**

### 1. Shape

```
dashboard                         main.py                    tailnet-gateway
/config "Join" ──authorize()──▶ invoke(tailnet/apply, ──bus──▶ tailnet-writer (feature)
  (Action, may_disturb)          requires=...:tailnet-writer)    │ queue one request
                                                                 ▼
                                                           core worker thread ── OverlayWriter
                                                                 │                (tailscale CLI)
dashboard ◀──bus── tailnet/apply-result ◀── drained on the next tick
```

- **The command carries no values.** `tailnet/apply` (`TailnetApply`, a new contract
  command) names the operation, `join`, `rename` or `leave`, plus who asked, through which
  door, and a request id.
  - The writer reads the hostname, tags and key path from its own `ConfigView` (ADR-0004).
  - So a command can't carry a value that bypassed the registry's validation, and the key
    never travels on the bus (§8.9).
- **The dashboard invokes through the §5 seam.** A new `Invoker` protocol in the dashboard's
  `interface/` is satisfied by `main.invoke`. The Apply actions call it with
  `requires="tailnet-gateway:tailnet-writer"`.
  - If the writer is absent or disabled, or nothing is listening, the result is a refusal
    that says so (§5), never a silent success.
- **One request at a time** (rung 5).
  - The feature hands the request to a single worker thread owned by core, which wraps the
    `OverlayWriter` seam.
  - A request that arrives while one is running is refused in-band ("busy: a join started
    by alice is in progress").
  - There is no queue and no force door. Each run is bounded by its own timeout, so a
    stuck run ends on its own.
- **The result is a message.** The worker queues its outcome, and core publishes it on the
  next tick as `tailnet/apply-result` (`TailnetApplyResult`): request id, operation, ok,
  message, finished at.
  - The dashboard's collector keeps the latest result, and `/config` shows it.
  - The Join action itself answers at once: "applying; this page shows the result".
  - The synchronous bus never waits on tailscale (§6, as `main.py` anticipated).

### 2. The adapter (`core/tailscale_writer.py`, behind `OverlayWriter`)

- **Argument lists only, never a shell.** Every call repeats `--operator=<daemon user>`
  (measured: `up` refuses otherwise).
  - **Join:**
    `tailscale up --operator=U --auth-key=file:<path> --hostname=H --advertise-tags=T --timeout=<UP_TIMEOUT_S>s`.
    `--hostname` is left out when unset (the OS name is used), and `--advertise-tags` when
    the list is empty.
  - **Rename:** `tailscale set --hostname=H`.
  - **Leave:** `tailscale logout`.
- **Two timeouts:** tailscale's own `--timeout`, and a process timeout a few seconds longer.
  A run that hits the process timeout is killed and reported as "timed out; state unknown;
  see the Tailnet section".
- **Output is scrubbed before it is used.** stdout and stderr are capped, and anything that
  matches `tskey-[A-Za-z0-9-]+` is replaced with `tskey-…` before the text goes into a
  message or a log (§8.9).
- **Values are re-checked here** (rung 13): hostname, tags, and a key path that is still
  usable. The registry already validated them, but this is the last consumer before the
  world.
- **Bounds (§8.11, provisional until measured on a real join):** `UP_TIMEOUT_S = 60`,
  `SET_TIMEOUT_S = 15`, `LOGOUT_TIMEOUT_S = 15`, `OUTPUT_CAP = 4 KiB`.

### 3. The dashboard

- **An Apply panel at the top of `/config`.** It shows:
  - the node's tailnet state and hostname, from `tailnet/status`;
  - the saved values, and where they differ from the node;
  - the last result;
  - only the actions that make sense now: Join when logged out and a key is saved, Rename
    when joined and the saved hostname differs, Leave when joined.
- **Each action is `may_disturb` with its own warning**, so it needs `confirm=yes`
  (ADR-0003 §4). On the tailnet door, `authorize()` already adds "recovery is the LAN
  fallback on :8443".
  - **Join:** "Joins the site tailnet with the saved key; the key is then deleted."
  - **Rename:** "Changes this node's tailnet name; links using the old name stop working."
  - **Leave:** "Disconnects this node from the tailnet. Tailnet sessions to it end; recovery
    is the LAN fallback on :8443."
- **The action manifest (§5) is still a TODO.** `name`, `may_disturb` and `requires` on these
  actions are what it will publish, so an LLM caller sees the same warnings.

### 4. Contract (0.4.0, additive)

- `TailnetApply` (command) and `TailnetApplyResult`, on the topics `tailnet/apply` and
  `tailnet/apply-result`.
- `TailnetStatus.tags: list[str] = []`, so the panel can compare the saved tags with the
  node's. Read from `Self.Tags`, which is not yet verified on a joined node.
- No secret in any of them.

## Safety (spec §11)

- **Rung 1:** `tailnet-gateway` stops being read-only. Write ability is one capability flag,
  `tailnet-gateway:tailnet-writer`. It is registered disabled (§8.4) until the first
  measured join on a test tailnet passes, then enabled.
- **Rungs 2–3:** the node's own tailscaled is owned (ADR-0003). The site's tailnet is
  someone else's system, so every write is an operator act (D1). Nothing applies on its own,
  and nothing is proposed automatically yet. Subnet-route proposals stay with the
  auto-configuration TODO.
- **Rung 4:** every operation is may-disturb, confirmed, and its warning lives on the action
  for the manifest (§3).
- **Rung 5:** one operation at a time, no force door; each run is bounded by its timeouts
  (§1).
- **Rung 6:**
  - A failed rename or leave leaves tailscaled as it was; the CLI applies a pref or doesn't.
  - A failed join leaves the node logged out, or in whatever state tailscaled reports, and
    the panel shows that state.
  - The safe state is "not on the site tailnet". The LAN fallback is always the way back in.
  - There is nothing to restore in-process.
- **Rung 7:** every refusal is in-band and has a reason: writer unavailable, busy, no key,
  invalid value, unconfirmed, timeout, or tailscale's own (scrubbed) error.
- **Rung 9:** the worker is the only code that runs a mutating tailscale command. The status
  adapter stays read-only.
- **Rung 10:** `authorize()` in the dashboard is the one door. The command records who and
  which door, and the writer logs both with the outcome.
- **Rung 11:** not applicable. A tailnet membership is held by tailscaled and the site's
  control plane, not as a grant this process renews. Our renewed levels stay where they are:
  the status and peer-table freshness.
- **Rung 12:** this removes "tailnet-gateway is read-only" (CLAUDE.md, the module's
  docstrings). Its replacements ship in the same change:
  - the single worker,
  - the confirmation,
  - re-checking values at the adapter,
  - output scrubbing,
  - the capability flag, registered disabled.
- **Rung 13:** values are validated by the registry and checked again at the adapter (§2).

## Consequences

- **Easier:** joining a new site needs only a browser: on the LAN fallback, save the key,
  hostname and tags, then Join and confirm. The key deletes itself.
- **Harder or accepted:**
  - The node now acts on someone else's tailnet. Under ADR-0003 D2 any tailnet user can
    rename the node or make it leave.
  - The first worker thread in the codebase. Its queue-and-drain pattern becomes the
    template for the MediaMTX and VAPIX writers.
  - A join that hits the process timeout leaves the state unknown until the next status
    read.
- **Contract:** 0.4.0 (§4). Run `check_additivity`.
- **Quirks to record:**
  - `set` has no `--advertise-tags` (measured).
  - `up --timeout` defaults to blocking forever (measured).
  - These need a real join to verify: who reads the `file:` key, `up` without `--operator`
    refusing, `Self.Tags`, and `Running` after `up` returns.
- **Revisit:**
  - Retagging in place, once measured (D2).
  - Continuous reconcile (D1 B), if a site ever needs the node to re-assert its name.
  - The login-URL flow, as an alternative to auth keys, if a site can't issue keys.
  - Automatic rejoin with a stored reusable ephemeral key (D4), if site outages make manual
    rejoin painful.

## Implementation order

1. ~~Accept this ADR with D1–D4.~~ Done 2026-10-13.
2. ~~Contract 0.4.0: `TailnetApply`, `TailnetApplyResult`, `TailnetStatus.tags`.~~ Done 2026-10-13.
3. ~~`OverlayWriter` seam and the CLI adapter.~~ Done 2026-10-13. Tests use a fake runner and check the argv golden
   per operation, the key scrubbing, both timeouts, and the re-check refusals.
4. ~~The `tailnet-writer` feature and core's worker~~ (done 2026-10-13; enabled by
   `CN_TAILNET_WRITER=1` in the daemon's environment; on by default since 2026-10-13, see the
   last amendment) (one at a time, queue and drain), plus the
   injected `ConfigView`. Registered **disabled**.
5. ~~Dashboard: the `Invoker` seam~~ (done 2026-10-13), the Apply panel, three may-disturb actions, and the last
   result.
6. Rehearse on a test tailnet from the node with an **ephemeral** key. Measure the timeouts,
   find out whether status reports ephemeral (D4), check that Leave removes the node, record
   the quirks,
   then enable the capability. Run the security review over ADR-0004 and this ADR together,
   then merge.

## Amendments

### 2026-10-13 — rehearsal findings; keep the key and rejoin; advertise the site network

The first rehearsal join succeeded (ephemeral, tagged key; the node came up as `pi5-gateway`,
tag `tag:mace4-comms`). The operator then set two requirements: **the node must survive a
power cycle**, and **it must advertise a subnet route, defaulting to the network it is on.**

Measured on the joined node (tailscale 1.102.5, 2026-10-13):

- **`tailscale status --json` has no ephemeral flag** in `Self`. D4's post-join check is not
  possible from status; the requirement stays enforced by the help text and the Join warning.
- `Self.Tags` lists the node's tags; `PrimaryRoutes` is absent with no approved routes.
- `tailscale set --advertise-routes` exists (unlike `--advertise-tags`, Q-002), and an empty
  value means "no routes".
- Prefs after the join: `WantRunning=true`, `LoggedOut=false`.
- The site interface is now `eth0` (192.168.137.218/24, DHCP, default route), not the `lan0`
  of ADR-0003. Interface names are not stable across nodes, so nothing may assume one.
- `net.ipv4.ip_forward = 0`. A subnet router needs forwarding on.

**A1 — D3 is replaced: the key is kept.** A successful join no longer deletes the saved
auth key. It stays (0600, site-scoped, wiped at teardown) so the node can rejoin on its own
(A2). The Join warning and the key's help now say the key must be **reusable and ephemeral**:
a single-use key cannot rejoin. The operator deletes it on `/config`, and the site teardown
wipes it. This supersedes ADR-0003 §5's "deleted once tailscaled confirms the join".
Consequence: a long-lived network secret on the SD card, for the life of the key (Tailscale
caps auth keys at 90 days). Mitigations: a tagged ephemeral key limits what it can join as,
and the file rules of ADR-0004 apply.

**A2 — Automatic rejoin, a narrow exception to D1.** D1 stays the rule: an operator's first
join is always an explicit, confirmed act (rung 3). The exception is only for *rejoining a
tailnet the operator already joined*:

- **The intent is a level the writer records.** After a successful join the worker writes a
  join record (`site/tailnet-gateway.join.json`: the routes it applied, when). Leave deletes
  it. The record is site-scoped, so teardown wipes it. tailscaled's own `LoggedOut` pref is
  not used: it is read through `tailscale debug prefs`, a debug interface.
- **The trigger:** the record exists, the field `auto_rejoin` is on (default on, site-scoped),
  a key is saved, a *fresh* reading says `BackendState == "NeedsLogin"`, and no write is
  running. Then the feature plans a join with the saved values, requested by `auto-rejoin`
  through the `node` door. A reboot that comes back `Running` triggers nothing: tailscaled
  keeps its node key across restarts.
- **It never hammers.** Attempts back off: 1 min, doubling, capped at 30 min
  (`REJOIN_BACKOFF_*`, provisional, §8.11). Every attempt and outcome is a
  `TailnetApplyResult`, shown on the panel. Success resets the backoff.
- **Fails toward not joining.** If the record, the key or a fresh reading is missing, nothing
  happens. If tailscaled reports a state other than `NeedsLogin`, nothing happens.
- **Not measured yet:** that an ephemeral node removed by the control plane comes back as
  `NeedsLogin`. Rehearse it: join, power the node off long enough to be removed, power it
  on, and watch the panel.
- **Known gap:** a manual `tailscale logout` over SSH leaves the record, so the node rejoins.
  tailnet-gateway is the single writer (rung 9); manual CLI use bypasses it. Leave on
  `/config`, or switch `auto_rejoin` off.

**A3 — Advertised routes, defaulting to the site network (§11 rung 2: detect, then confirm).**

- **A field `advertise_routes`** (`str_list`, site-scoped, default `["auto"]`): IPv4 CIDRs,
  or `auto`. `auto` expands to the networks the node is on now. An empty list advertises
  nothing.
- **Detection:** the IPv4 networks of every interface that is up, except loopback and the
  tailnet, ignoring /32s, link-local addresses and anything inside 100.64.0.0/10. It reads
  `ip -j -4 addr` through a new `SiteNetworkSource` seam in core (rule 16 style: host I/O
  behind a seam). It is stamped onto each overlay reading, and the panel shows it. With
  several interfaces (an LTE uplink beside the site LAN), `auto` includes both. The panel
  shows what `auto` means right now, so the operator sees it before confirming, and can
  replace `auto` with the exact network.
  *2026-10-26:* only interfaces backed by a device count (`/sys/class/net/<name>/device`).
  A virtual one (Docker's `docker0`, veth pairs, container bridges) is the node's own, not
  the site's, and is never proposed or scanned (quirks Q-012).
- **Applied by Join** (`--advertise-routes=…`, always passed, empty included, so a later
  join never trips Q-001-style "mention all non-default flags"), and on a joined node by a
  new confirmed operation, **Routes** (`tailscale set --advertise-routes=…`). The adapter
  re-checks each CIDR with `ipaddress` (strict network, IPv4, outside 100.64.0.0/10).
- **Approval is the site's.** An advertised route serves nothing until the tailnet's admin
  approves it (or an `autoApprovers` ACL does). The panel shows advertised against approved
  (`served_routes`).
- **Forwarding and the firewall (ops, rule 15):**
  - The installer sets `net.ipv4.ip_forward=1` in `/etc/sysctl.d/90-commissioning-node.conf`.
    IPv6 forwarding stays off: turning it on stops the kernel accepting router
    advertisements, which could cost the node its site IPv6.
  - With forwarding on, the node would route between any of its interfaces. The
    `inet commissioning_node` table gains a `forward` chain: established and related
    traffic is accepted, traffic arriving on `tailscale0` is accepted, **everything else is
    dropped**. Only the tailnet can open a forwarded connection; a site-LAN host cannot use
    the node as a path into the tailnet or to another network (ADR-0001: the LAN is
    untrusted).
  - The doctor checks both.

**Contract 0.5.0 (additive):** `TailnetStatus` gains `state` (tailscaled's BackendState),
`advertised_routes` (from the join record) and `site_networks` (detected).

**Safety, for these amendments:** rung 2: routes are detected and proposed, the operator
confirms. Rung 3: the first join stays operator-confirmed; automatic rejoin acts only on a
recorded prior confirmation. Rung 5: auto-rejoin uses the same one-at-a-time slot. Rung 7:
every automatic attempt is a visible result. Rung 12: removes "the key is deleted after a
join"; replaced by the reusable-ephemeral-key requirement, the teardown wipe and the visible
key note. Rung 13: CIDRs are re-checked at the adapter, and forwarding is fenced by the
firewall, not only by Tailscale's own rules.

### 2026-10-13 — pre-merge review: rulings

A three-way review of the branch (HTTP surface and secrets; the writer; store, ops and
contract) found these. Each was reproduced or read from the code before it was fixed, and
each fix has a regression test.

1. **Auto-rejoin acts only on what was confirmed** (was: the currently saved values). The
   join record now holds the confirmed hostname, tags, routes and the key file's SHA-256.
   Auto-rejoin applies exactly those. The worker refuses a rejoin whose key no longer hashes
   to the recorded one, without calling tailscale. Staged edits, a swapped key, or a newly
   detected interface never reach the tailnet without a confirmed Join or Routes. A record
   without a fingerprint, from before this ruling, never auto-rejoins: Leave and Join once.
2. **A Leave always drops the intent.** The record is deleted *before* `logout` runs. The
   feature also holds an "operator left" level from the moment a Leave is accepted until the
   operator's next successful Join. A Leave whose `logout` times out after tailscaled already
   logged out can no longer rejoin, and neither can a reading taken mid-Leave. If the record
   can't be deleted, the saved auth key is deleted instead (round 2): the in-memory level
   doesn't survive a restart, so without the key nothing can rejoin after one.
3. **A failed operator Join is not retried by auto-rejoin.** Any join outcome pushes the next
   automatic attempt out by one backoff.
4. **The firewall unit owns IPv4 forwarding** (was: `/etc/sysctl.d`, which applied at boot
   before the forward chain loaded, and stayed on when the unit stopped). `ExecStartPost`
   turns it on after `nft` loads the table. `ExecStop` turns it off before deleting the
   table, and `ExecStopPost` turns it off on failure. The installer removes the old drop-in;
   the doctor fails if it is present.
5. **`scrub()` redacts before it caps**, so a key cut at the cap is still caught.
6. **`OverlayWriter.join(hostname, tags, key_path, routes=())`**: `routes` moved last with a
   default, so the change is additive for callers.
7. Minor: the CLI adapter decodes output with `errors="replace"` and never raises on bad
   bytes. An unset bool field preselects its default on `/config`.

The forward chain's final `drop` applies to *all* forwarding not from the tailnet. A
container runtime or a Wi-Fi hotspot added to this host later would need its own rule in
that chain; this is recorded here so the drop is not mistaken for a bug.

Round 2, on the fixes: the key deletion above; a malformed `config.json` deleted or fixed
by hand after load is honoured, not set aside or tripped over; and the temp-file sweeps
match only their own names (the registry's and the join record's), leaving other dotfiles.
Round 3: a re-check that failed with anything but "not found" cleared the malformed flag,
so the next write replaced a hand-fixed but unreadable file. The flag now clears only on a
definite outcome; any other read error marks the scope unreadable (writes refused), as at
load.

### 2026-10-13 — rehearsal step 3: a power cut; the key is an OAuth client secret

**Measured (Q-005).** The node lost power for up to ~2 h. The control plane had removed
the ephemeral node, so tailscaled booted into `NeedsLogin`. Auto-rejoin rejoined it with the
recorded values: `tailscale up` took 4.999 s, and the node was `Running` 6 s after
tailscaled's first state change (request `32d632b22031`). This answers A2's "not measured
yet". It also means a long outage always ends in a rejoin, so auto-rejoin is the normal
path after a power cut, not a corner case. The short-outage case (back `Running`, no
rejoin) is still to rehearse.

The node has no battery-backed clock: it booted 2h12m slow and stepped at its first NTP
sync, 37 s after boot, during the rejoin. Nothing in the writer depends on the wall clock
(backoff and freshness are monotonic), but journal times before the sync are wrong. Ledger
rows are timed from monotonic stamps.

**The operator's key is a Tailscale OAuth client secret** (`tskey-client-…`) with the
`auth_keys` scope, locked to the node's tags. The operator keeps one and reuses it from site
to site instead of minting an auth key per visit. Consequences for A1:

- The secret doesn't expire. A1's bound "for the life of the key (Tailscale caps auth keys
  at 90 days)" no longer applies. The bound is now the site teardown wipe, the operator's
  delete on `/config`, and revoking the client in the admin console.
- It is a stronger secret than an auth key: anyone holding it can mint keys for its tags
  until it is revoked. Locking it to tags and the ADR-0004 file rules (0600, never on the bus
  or in logs) are what limit it. If a node is lost with a secret on it, revoke the client.
- Not measured, from Tailscale's documentation (verify, then record in quirks.md): an OAuth
  secret used as an auth key needs `--advertise-tags`, joins ephemeral by default, and is
  *not* pre-authorised by default. On a tailnet with device approval, a rejoin would wait
  for approval unless the key carries `?preauthorized=true`.
- **The `auth_key` field accepts the options:** a key may end in `?ephemeral=true` and/or
  `preauthorized=true|false`, each once. `ephemeral=false` is refused (D4: the node is
  ephemeral on every site), and so is `baseURL`, which would point the join at another
  control plane. The suffix is part of the secret file, so it is in the
  recorded `key_sha256`; changing it is a key change, and auto-rejoin refuses it until a
  confirmed Join.

**The writer is on by default.** The rehearsal joins, the routes and this rejoin passed, so
step 6's "then enable the capability" is done: the daemon enables the writer unless its
environment says `CN_TAILNET_WRITER=0`. The switch is kept as the node's off-switch (no
operator join, leave or rejoin), and a malformed value turns the writer off with a
warning. The rehearsal drop-in (`…/commissioning-node.service.d/tailnet-writer.conf`) is
removed on the node. `auto_rejoin` on `/config` still turns off only the automatic rejoin.

### 2026-10-13 — Routes off

The operator needed to stop advertising routes without editing the saved value. A new
operation, **`routes-off`**, sets the advertised routes to none
(`tailscale set --advertise-routes=`), whatever is saved. The contract needs no change:
`TailnetApply.operation` is an open vocabulary, and an older writer refuses a name it
doesn't know.

- **Safe direction, like Leave:** it offers the tailnet less, so it needs no fresh reading.
  It still takes the one write slot (rung 5), and its outcome is a visible result (rung 7).
- **It is confirmed state:** the join record then holds no routes, so an auto-rejoin
  restores *no routes*, not the saved ones (ruling 1: a rejoin applies only what was
  confirmed). Routes applies the saved ones again.
- **On `/config`** the advertised-routes row has one slider, a toggle: *Stop advertising
  routes* (danger colour) while any are advertised, else *Advertise <saved routes>*. Moving
  from one set to another goes through off.

### 2026-10-13 — release review: no auto-rejoin off the recorded networks

The v0.1.0 release review found an interaction that per-merge reviews couldn't see. The join
record (A2), the reusable OAuth secret carried from site to site (rehearsal step 3 amendment)
and the still-missing teardown procedure (TODO) together let a node moved to a new site
rejoin on its own with the **previous site's routes**. It boots `NeedsLogin` (its ephemeral
node was removed while it was off, Q-005), the key fingerprint still matches, and about 5 s
later it would offer the old site's subnet from the new site's LAN. With route
auto-approval, tailnet traffic for that range would leave on the wrong LAN; with two sites
on the same range, operators would reach the wrong site's devices.

**Ruling (operator, 2026-10-13):** auto-rejoin refuses while any recorded route is not a
network the node is on now (`TailnetStatus.site_networks`, the detected networks). This fails
toward not joining. The refusal is a failed `tailnet/apply-result` from `auto-rejoin`,
published once per episode ("the node may have moved site … press Join") and re-checked on
every reading, so a site network that comes up late at boot still rejoins. An operator's
Join is unaffected: it resolves routes from where the node is now. A record with no routes
has nothing to offer the wrong LAN, so it still rejoins.

Also found, not fixed in v0.1.0 (TODO): a service restart while a Join, Rename or Routes is
still running loses its outcome, and can leave the join record behind what tailscaled did.

### 2026-10-21 — the site LAN may open connections into the tailnet

A3 fenced forwarding so that only the tailnet could open a forwarded connection: a site-LAN
host could not use the node as a path into the tailnet. On site, devices on the node's
network need to reach devices on the tailnet themselves (the reverse of the subnet routes).

**Ruling (operator, 2026-10-21):** this is what the node is for. The forward chain gains one
accept, between the tailnet's accept and the drop: **anything forwarded out `tailscale0`**
(`oifname "tailscale0" accept`). Applied by hand on the node first (with `iifname "eth0"`),
then here.

- **No interface or subnet named.** The site interface is `eth0` on a Pi and `lan0` on the
  RevPi image (Q-009), and the site subnet changes at every site. The rule matches only the
  way out, so it holds on both images and on every site without an edit.
- **What still limits it:**
  - Tailscale's own netfilter rules drop traffic leaving on `tailscale0` with a
    `100.64.0.0/10` source, so a site host can't pose as a tailnet node.
  - A tailnet device accepts a forwarded packet only from a source in the node's
    **approved** routes, and its tailnet policy decides which of them may reach it.
    Which tailnet devices and ports the site reaches is that policy's call (a grant from the
    site subnet), made by the tailnet's admin, not by this node.
  - The drop stays: the node is still never a path from one site network to another, or
    from the site to the internet through an exit.
- **The site's side is the site's.** Site hosts (or the site router) need a route to
  `100.64.0.0/10` via the node, and Linux tailnet targets need `--accept-routes` to answer.
  The node configures neither.
- **ADR-0001 stands:** the site LAN is still untrusted and the node still serves it nothing.
  This opens a path *through* the node, gated by the tailnet's policy, not a surface on it.

**Safety:** rung 2: the opening is fixed in ops, not per site; per-site reach is the
tailnet policy's grant. Rung 13: two independent fences remain (this chain's drop for
everything but the tailnet; Tailscale's source checks and policy for what reaches it).

**RevPi image (2026-10-23):** firewalld's forward chain did reject a site host's packets
into `tailscale0` (Q-009); a policy from the site zones into `trusted` lets them through,
now ADR-0002 A3's third firewalld entry. **Unverified (TODO):** unmeasured on 1.102.5: that a tailnet peer drops a forwarded packet whose
source is outside the approved routes. Record both in quirks.md once seen.
