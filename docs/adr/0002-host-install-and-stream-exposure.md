# ADR 0002: Host install — bare metal + systemd, pinned vendors, MediaMTX firewall

- **Status:** Accepted
- **Date:** 2026-10-12
- **Deciders:** Austin Emerson
- **Shape:** standard (spec §7)

## Context

The node needs Tailscale and MediaMTX on the host, and the commissioning-node daemon needs to
run as a service (TODO: ops tier). Spec §4 makes deployment form an ADR decision and puts the
installer, doctor and units in the ops tier (`ops/`). The ops tier is never imported, and the
installer is idempotent with a `--dry-run`.

Measured on this node (RevPi, 2026-10-12):

- Debian 13 trixie, arm64, systemd 257, nftables 1.1.3 installed. Debian's
  `nftables.service` is **disabled**, and its `/etc/nftables.conf` begins with
  `flush ruleset`. No firewall rules are loaded.
- The RevPi's stock services already listen on all interfaces: SSH 22, HTTP 80, HTTPS 443,
  and 41443. They predate this project, and this ADR leaves them alone.
- MediaMTX's defaults let **any** client publish and read, and they start RTMP, HLS, WebRTC,
  SRT and (since v1.21) MoQ, all listening on every interface. MoQ also writes a TLS key into
  its working directory. That was observed by running the pinned binary, see
  `docs/quirks.md` Q-001.
- `sudo` needs a password. The installer is run by an operator, not by an agent.

ADR-0001 makes the tailnet the trust boundary. So an RTSP port open to the site LAN would
publish the site's camera streams to that LAN.

## Decision

1. **Bare metal + systemd, no containers.** The node is a single-purpose appliance that
   works with host networking (`tailscale0`, nftables), and a container runtime would add a
   moving part with no benefit at one node. `ops/install.sh` installs three units:
   - `commissioning-node-firewall`
   - `mediamtx` (its own system user, a hardened unit, `/var/lib/mediamtx` as its working
     directory)
   - `commissioning-node` (`main.py`, run as the owner of the repo checkout)
2. **Pins, in `ops/pins.sh`:**
   - **Tailscale 1.102.5.** It comes from Tailscale's signed apt repo for trixie. The
     repo-key fingerprint is pinned (`2596A99EAAB33821893C0A79458CA832957F5868`), the exact
     version is installed, and then `apt-mark hold` stops upgrades.
   - **MediaMTX v1.21.1.** It's the `linux_arm64` release tarball, checked against a pinned
     sha256 and installed under `/opt/mediamtx/<version>`, with a `current` symlink.

   Both were the last patch of a mature line at least about two weeks old. The newest
   releases (1.104.1 and v1.21.2) were under a week old.
3. **The MediaMTX config is ours, and minimal** (`ops/mediamtx/mediamtx.conf.yml`):
   - Protocols: RTSP over TCP only. RTMP, HLS, WebRTC, SRT and MoQ are off, and so are
     metrics, pprof and playback.
   - The API is on `127.0.0.1:9997`.
   - Access: anyone who can reach the server may read, nobody may publish, and only
     loopback may use the API.
   - Paths: none predefined. `stream-relay` adds them later.
   - It holds no credentials, so it is committed.
   - Before it is installed, `ops/check_mediamtx_config.sh` runs it against the pinned
     binary. The check requires that MediaMTX opens exactly the declared listeners, which
     catches the next default-on server.
4. **A scoped firewall.** We use our own nftables table, `inet commissioning_node`, loaded
   by our own oneshot unit. We never use `nftables.service` or `flush ruleset`, which would
   also wipe Tailscale's rules. The table drops inbound traffic to every port MediaMTX opens
   by default unless it arrives on `lo` or `tailscale0`. It touches nothing else.
   `mediamtx.service` `Requires=` the firewall unit, so no firewall means no streams (fail
   closed).
5. **The installer doesn't join the tailnet.** That needs an auth key, which is a secret
   (§8.9). `sudo tailscale up` stays a manual operator step until the tailnet-gateway writer
   feature exists.

## Safety (spec §11)

- **Rungs 1–8:** this is operator-run host provisioning. Nothing writes automatically. The
  installer shows its whole plan under `--dry-run` and refuses an unexpected host, a wrong
  key fingerprint, a wrong sha256, invalid firewall rules, or a config that fails its check.
  Each refusal is printed to the operator, in-band.
- **Rung 9, single writer:** `ops/install.sh` is the only writer of these units, the
  MediaMTX binary and config, and the firewall table. At runtime `stream-relay` will become
  the only writer of MediaMTX *paths*, through its API.
- **Rung 10:** n/a. Host root is the owner.
- **Rung 11:** n/a. There is no continuous actuation.
- **Rung 12:** this removes nothing. It adds a block.
- **Rung 13:** stream exposure is enforced in three places, each on its own inputs:
  - `stream-relay` advertises tailnet addresses only.
  - The firewall drops every non-tailnet interface.
  - `ops/doctor.sh`, as root, fails on any unexpected MediaMTX listener or a missing table.
- **Constants:** `RestartSec=5` is systemd restart pacing, not a safety value. The ports are
  MediaMTX's defaults (vendor facts, Q-001), not tuned numbers.

## Consequences

- **A pin bump is a reviewed edit to `ops/pins.sh`.** For MediaMTX it also means re-running
  `check_mediamtx_config.sh` on the new binary and re-checking every `docs/quirks.md` entry.
- **There's a known hole: a manual restart of Debian's `nftables.service` wipes our table.**
  Our unit is ordered after it at boot, and `ops/doctor.sh` (as root) fails when the table is
  missing. But nothing re-applies the table automatically. Revisit if that service ever needs
  to be enabled.
- **CI gates:** `ci.sh` runs shellcheck (or skips it loudly), the installer's `--dry-run`,
  `nft -c` (when sudo is available), and the config check (when a MediaMTX binary is present).
- **Provenance (§2): the daemon runs whatever is checked out in the repo directory.** That's
  acceptable during commissioning on a dev node. Before a node ships for real, it runs a
  tagged checkout, and the doctor reports which commit is running. That's tracked in TODO.
  *Since ADR-0009 step 1 (2026-10-25):* every install records the commit in
  `/etc/commissioning-node/running.json`, and the doctor reports it.
- **The RevPi's own all-interface services** (80/443/41443, and SSH) are site-LAN exposure
  this ADR doesn't address. Deciding on them belongs to the admin-fallback ADR.

## Amendments

### A1 — 2026-10-14: firewalld is on, and the installer makes two entries in it

- **Correction to Context.** "No firewall rules are loaded" was wrong. The RevPi image
  enables firewalld, and it has run on every boot. Its default zone rejects everything on
  the site LAN except the stock services, and it rejects `tailscale0`, which is in no zone.
  An `accept` in our table does not override its rejects (docs/quirks.md Q-009). So the admin
  fallback (ADR-0003) was unreachable from the site LAN, its one intended audience. Every
  loopback check passed, because loopback skips firewalld.
- **Decision.** We leave firewalld running and add exactly two entries to it, in both its
  runtime and permanent config, from `ops/install.sh`:
  1. `FALLBACK_PORT/tcp` (8443, `ops/paths.sh`) in every zone a site interface can land in:
     the default zone plus the active zones, but not `trusted`. That's the exposure ADR-0003
     already chose, so this changes nothing about what is reachable by design.
  2. `tailscale0` in the `trusted` zone. The tailnet is the boundary (ADR-0001), and our own
     table is what scopes the tailnet ports. Without this entry, firewalld would also reject
     the tailnet door on 8080, MediaMTX, and traffic forwarded through the subnet routes
     (ADR-0005).
  The installer never reloads, disables or replaces firewalld, and touches nothing else in
  it. Decision 4's "it touches nothing else" still holds for our own table.
- **Why not disable firewalld.** It is the RevPi image's own protection for its stock
  services, and Cockpit manages it (`cockpit-revpi-redirect-firewalld`). Turning it off would
  open everything else on the host to the site LAN. That's a wider decision than this fix,
  and it belongs with the stock-services question under Consequences.
- **Checks.** `ops/doctor.sh` FAILs if `tailscale0` is not in `trusted` (runtime, and
  permanent with root), or if a site zone doesn't accept the fallback's port (runtime +
  permanent, needs root). `tests/test_ops_paths.py` checks that `FALLBACK_PORT` matches
  `main.py`.
- **Unverified.** That `trusted` carries the tailnet door, MediaMTX and the forwarded routes
  hasn't been seen on a joined node yet (docs/quirks.md, "Unverified assumptions").

### A2 — 2026-10-15: one command brings up a node (after the git clone)

- **Why.** Bringing up a node should be: clone the repo as the operator user, run
  `sudo ops/install.sh`, then join from the dashboard. Until now a fresh node also needed
  `python3-pydantic` installed by hand (the daemon failed to start without it), a separate
  `set-admin-password.sh`, a separate doctor run, and the summary still pointed at a manual
  `tailscale up`.
- **Decision.** The installer now also:
  1. installs the daemon's Python runtime from Debian (`python3`, `python3-pydantic`, plus
     `openssl` for the TLS step) and refuses a pydantic that isn't major 2. These packages
     are not pinned like the vendor binaries: the code needs pydantic 2, not one build of it
     (`requirements.txt`).
  2. offers to set the admin password when none exists, but only on a terminal. It runs
     the same `set-admin-password.sh`, so the password only ever meets its no-echo prompt.
     Declined, or with no terminal, the fallback stays off (fail closed), as before. A newly
     set password restarts the daemon, because the fallback listener only starts when the
     daemon does.
  3. runs `ops/doctor.sh` at the end and exits with its status.
  4. ends with the next step: the tailnet URL if the node is already joined; otherwise the
     fallback's LAN URLs and certificate fingerprint, from which the operator saves the
     auth key on `/config` and slides Join.
  5. tells a root-owned checkout exactly how to fix it (`chown -R $SUDO_USER: <repo>`).
- **Supersedes decision 5's "until".** The tailnet-gateway writer exists (ADR-0005), so
  joining is the dashboard's Join, not `tailscale up`. The installer still never touches
  the auth key: it is a site secret, and the operator surface on `/config` is its one path
  (§8.9, ADR-0004 D3).

### A3 — 2026-10-23: a third firewalld entry, the site LAN into the tailnet

- **Why.** ADR-0005 (2026-10-21) lets a site-LAN host open forwarded connections into the
  tailnet, and our table accepts them. On the RevPi image firewalld's forward chain then
  rejects them: `lan0` is in `public`, `tailscale0` is in `trusted`, and nothing lets one
  zone forward into the other (docs/quirks.md Q-009). Measured 2026-10-23: a site host's
  ping into the tailnet failed until a policy `public` → `trusted` with target `ACCEPT`
  was added, then got through.
- **Decision.** A1's two entries become three. `ops/install.sh` also keeps a firewalld
  policy `FIREWALLD_POLICY` (`site-to-tailnet`, `ops/paths.sh`): ingress every site zone (the
  same set as entry 1), egress `trusted`, target `ACCEPT`, in the runtime and permanent
  config. It is no wider than our own table: the forward chain's drop still stops
  everything but the tailnet's own connections and the site LAN into `tailscale0`.
- **One reload, amending A1's "never reloads".** firewalld creates a policy only in its
  permanent config, and a policy's target is permanent-only too. So when the policy is
  missing from the runtime config, the installer runs `firewall-cmd --reload` once to load
  it. Entries 1 and 2 are already permanent by then, so they survive. A runtime-only change
  someone else made to firewalld is lost, but only on that first run. Once the policy is
  loaded, a new site zone is added to the runtime and the permanent config with no reload.
- **Checks.** `ops/doctor.sh` (root) FAILs for each site zone the policy doesn't forward
  into `trusted` in the runtime and permanent config, or if its target isn't `ACCEPT`.

### A4 — 2026-10-25: the tailscale operator is a renewed level, not an install step

- **Status:** Accepted 2026-10-25 (Austin Emerson); built the same day, rehearsal pending.

- **Why.** `ops/install.sh` runs `tailscale set --operator=<daemon user>` once (A2), so the
  daemon can run `tailscale` without root (ADR-0003, ADR-0005). That pref doesn't last.
  `OperatorUser` belongs to a tailscaled profile, and `tailscale logout` (the writer's
  Leave) deletes the profile, so the operator goes with it (docs/quirks.md Q-010).
  Measured 2026-10-25: a Leave at 22:39:00, then `profile not found`, then three Joins
  refused with `Access denied: checkprefs access denied`. Every Leave breaks the next Join,
  and a profile lost for any other reason does too (seen 2026-10-21, cause not traced). The
  fix each time was root at a terminal. A grant of authority that one event can drop has
  to be renewed (§5), not set once.
- **Decision.** The ops tier gets a root-run unit that renews the operator, and the
  installer installs and enables it:
  1. `commissioning-node-tailscale-operator.service` (oneshot, root). It reads
     `OperatorUser` from `tailscale debug prefs`. If it isn't `RUN_USER` (`ops/paths.sh`,
     fixed at install), it runs `tailscale set --operator=$RUN_USER` and logs one line.
     Otherwise it does nothing and says nothing. Reading first keeps it idempotent, and stops
     its own write from triggering it again (trigger 2). `LogLevelMax=notice` keeps a no-op
     run out of the journal, systemd's own start and stop lines included. A renewal is one
     notice line and a failure one warning, with tailscale's error in it.
  1a. **Root never runs a file the daemon user can write.** The daemon runs as the checkout's
     owner, who can edit the repo, so a root unit running `ops/tailscale-operator.sh` from
     the repo would let a compromised daemon run anything as root. The installer renders a
     copy with `RUN_USER` filled in (`@RUN_USER@`) to `OPERATOR_HELPER`
     (`/usr/local/libexec/commissioning-node/tailscale-operator`, root, 0755), and the unit
     runs only that copy. The doctor FAILs if the copy differs from the repo or isn't
     `root 755`. A change to the script reaches the node only through `sudo ops/install.sh`.
  2. It has three triggers, each covering a gap in the others:
     - **tailscaled start:** `WantedBy=` and `After=tailscaled.service` (boot, upgrade,
       restart). The script waits up to `OPERATOR_WAIT_S` for tailscaled's socket to answer.
       It doesn't edit tailscaled's own unit (no drop-in).
     - **state change:** `commissioning-node-tailscale-operator.path` with
       `PathChanged=/var/lib/tailscale/tailscaled.state`. A Leave rewrites that file, so the
       operator should be back within a second or two, before the operator's next Join.
       *Unverified:* that logout writes this file, and how fast the path unit fires.
     - **the level:** `commissioning-node-tailscale-operator.timer`,
       `OnActiveSec=60` (the first run after the timer starts; `OnUnitActiveSec` alone never
       fires for a unit that hasn't run), then `OnUnitActiveSec=OPERATOR_RENEW_S`. It renews
       the operator even if both edges above are missed (§5: the level is the guarantee, the edges only make it prompt).
- **No new authority.** The unit writes exactly one pref, to the value the installer
  already sets, for the user ADR-0003 already granted it to. It never logs in, never touches
  routes, tags, the hostname or the auth key, and never runs `up`, `down` or `logout`.
  tailnet-gateway stays the only writer of Tailscale *state* (ADR-0005, rung 9). The
  operator pref is host provisioning, and this unit renews the installer's write. The daemon
  gets no sudo rule and no polkit rule. It never asks for the operator: root renews it
  without being asked.
- **Rejected options:**
  - **A sudo or polkit rule letting the daemon re-set its own operator:** that gives the
    daemon user root-run commands, which the TODO and ADR-0003 rule out.
  - **Leave via `tailscale down` instead of `logout`, keeping the profile:** an ephemeral
    node is then only removed after control's offline delay, not straight away, and
    ADR-0005 D4 relies on Leave removing it at once.
  - **Only the start hook:** misses the main case, a Leave on a running tailscaled.
  - **Only a fast timer (say 10 s):** two systemd journal lines every 10 s, all day, on an
    SD card. The path unit gives the speed and the timer stays slow.
- **Checks.** `ops/doctor.sh` FAILs (not just warns) when the operator isn't `RUN_USER`, or
  when the path or timer unit isn't enabled and active. `ops/install.sh --dry-run` shows the
  three units, the copy, and one run of the service. The doctor also reports a node that
  says `Running` with a pending `AuthURL` as refused by control (Q-005), not as logged in. The
  installer's existing one-shot `set --operator` stays, so a fresh node has
  an operator before the first Join.
- **Companion change (ADR-0005, writer):** a Join or Rename refused with
  `checkprefs access denied` gets a plain Last action (`NO_OPERATOR_MESSAGE` in
  `core/tailscale_writer.py`: the node restores it within 5 minutes, or at once with
  `sudo systemctl start commissioning-node-tailscale-operator`), not the raw CLI text that
  suggests a `sudo tailscale up`, which would bypass the writer's join record.
- **Constants (§8.11, PROVISIONAL until ledgered in `tools/ledgers/tailscale-operator/`):**
  `OPERATOR_RENEW_S = 300` (the worst case once both edges are missed: a Join fails for
  up to 5 min, with a plain message). `OPERATOR_WAIT_S = 30` (tailscaled's socket at boot;
  Q-005 measured `NeedsLogin` 30.4 s after boot, but the socket answers earlier, not yet
  timed). Measure: Leave → operator back (path unit), and boot → operator checked.
- **Rehearsal (§11):** on the node, Leave on `/config`, then check
  `tailscale debug prefs | grep OperatorUser` within 5 s, then Join. Then stop the path
  unit, Leave, and see the timer bring the operator back. Record both in Q-010.
