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
