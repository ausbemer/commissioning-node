# Vendor quirk registry (spec §7)

Measured *silent* behaviours of pinned foreign dependencies: things they do (drop, reorder,
ignore, clamp) without an error. **A pin bump re-verifies every entry for that dependency**
before the new version is adopted. The registry is the checklist for "is it safe to bump?"

Pinned dependencies covered here:

| Dependency | Pinned version | Contained behind (bridge module, §6) |
|------------|----------------|--------------------------------------|
| tailscale / tailscaled | 1.102.5 (`ops/pins.sh`, apt-held) | `tailnet-gateway` (`modules/tailnet_gateway/core/tailscale_cli.py`) |
| MediaMTX | v1.21.1 linux_arm64 (`ops/pins.sh`, sha256-pinned) | `stream-relay` (adapter not yet written); host install `ops/` (ADR-0002) |
| Axis VAPIX (camera firmware) | per camera; record model + firmware seen on site | `camera-config` (adapter not yet written) |

## Entries

### Q-001: MediaMTX starts servers on all interfaces that our config doesn't mention

- **Dependency / version:** MediaMTX v1.21.1 (MoQ is new in the v1.21 line).
- **Behaviour:** keys left out of the config take MediaMTX's defaults, and the defaults turn
  on RTMP, HLS, WebRTC, SRT and MoQ (Media over QUIC), each listening on every interface.
  MoQ (`:8892` tcp+udp, `:8893` udp) arrived in v1.21. A config written for an older
  version without a `moq:` key therefore gains a new all-interface server on upgrade, with
  no error. MoQ also writes `auto.key` / `auto.crt` into the working directory.
- **How measured:** on 2026-10-12, ran the pinned binary with our config (RTSP and API moved
  to loopback test ports), then listed its sockets with `ss -tlnup`. Before `moq: false` was
  added, it listened on `*:8892` and `*:8893`, and the key and certificate appeared in the
  working directory.
- **Detection signature:** `ops/check_mediamtx_config.sh` fails with "listeners differ"
  (this is a CI gate when a binary is present), and `sudo ops/doctor.sh` fails with
  "unexpected mediamtx listeners".
- **Our accommodation:** `ops/mediamtx/mediamtx.conf.yml` turns every server off explicitly
  (`moq: false` included). The firewall (`ops/firewall/commissioning-node.nft`) drops all of
  MediaMTX's default ports off-tailnet, including the disabled ones. `mediamtx.service` runs
  in `/var/lib/mediamtx`.
- **Last verified:** v1.21.1, 2026-10-12.

### Q-002: `tailscale set` cannot change tags

- **Dependency / version:** tailscale 1.102.5.
- **Behaviour:** `tailscale set` takes `--hostname` but has no `--advertise-tags`. Tags are
  set only by `tailscale up --advertise-tags`, which on a joined node may need a
  re-authentication (not yet measured).
- **How measured:** `tailscale set --help` and `tailscale up --help` on the node, 2026-10-13.
- **Detection signature:** `tailscale set --help | grep -c advertise-tags` prints 0.
- **Our accommodation:** tags are applied only by Join; to retag, Leave and Join with a new
  key (ADR-0005 D2). The writer's argv goldens are in `tests/test_tailnet_writer.py`.
- **Last verified:** 1.102.5, 2026-10-13.

### Q-003: `tailscale up` waits forever by default

- **Dependency / version:** tailscale 1.102.5.
- **Behaviour:** `--timeout` defaults to `0s`, which blocks until tailscaled reaches
  `Running`. Without an auth key, a logged-out node prints a login URL and waits for someone
  to open it.
- **How measured:** `tailscale up --help` on the node, 2026-10-13 ("default (0s) blocks
  forever").
- **Detection signature:** a join that never returns.
- **Our accommodation:** the writer always passes `--timeout=UP_TIMEOUT_S` and kills the
  process `KILL_MARGIN_S` later; Join is refused without a saved key (ADR-0005 §2, D2).
- **Last verified:** 1.102.5, 2026-10-13 (help text only; measure on the rehearsal join).

### Q-004: `tailscale status --json` does not say whether a node is ephemeral

- **Dependency / version:** tailscale 1.102.5.
- **Behaviour:** on a node joined with an ephemeral key, `Self` has no ephemeral field (its
  keys: Active, Addrs, AllowedIPs, CapMap, Capabilities, Created, CurAddr, DNSName, ExitNode,
  ExitNodeOption, HostName, ID, InEngine, InMagicSock, InNetworkMap, LastHandshake, LastSeen,
  LastWrite, NoFileSharingReason, NodeID, OS, Online, PeerAPIURL, PeerRelay, PublicKey,
  Relay, RxBytes, Tags, TaildropTarget, TailscaleIPs, TxBytes, UserID).
- **How measured:** the first rehearsal join, 2026-10-13.
- **Detection signature:** no key containing "phem" in `Self`.
- **Our accommodation:** the node can't verify D4 after a join; the key's help text and the
  Join warning carry the requirement (ADR-0005 amendment).
- **Last verified:** 1.102.5, 2026-10-13.

## Unverified assumptions (verify, then turn into entries or delete)

- `tailscale status --json` field mapping used by `tailscale_cli.py`. **Partly verified**
  on 1.102.5, logged out (2026-10-12): the top-level keys `BackendState` (`NeedsLogin` when
  logged out), `Self`, `Peer` and `User` exist, and `Peer`/`User` are `null` when logged out.
  `Self` has `HostName`, `DNSName`, `TailscaleIPs` and `UserID`. `Tags` and `PrimaryRoutes`
  are omitted when empty. **Still unverified**, because it needs a logged-in node:
  - that `BackendState == "Running"` means connected;
  - `Self.PrimaryRoutes` (routes this node is currently primary for, which is not the same
    as the routes it *advertises*);
  - the shape of `Peer` entries (`DNSName` with a trailing dot, `TailscaleIPs`, `UserID`,
    `Tags`);
  - `User[str(UserID)].LoginName`;
  - that a tagged peer's `UserID` points at a "tagged-devices" pseudo-user.

  The caller identity in ADR-0003 rests on the peer mapping. Capture a sample after the
  first `tailscale up` and replace the fixtures in `tests/test_tailscale_cli.py` and
  `tests/test_tailnet_peers.py`.
- `tailscale up --auth-key=file:<path>` reads the key from a file (from `--help` on
  1.102.5), so the key never appears in argv (ADR-0003 §5). Not yet exercised, and not yet
  known whether the CLI (as the daemon user) or tailscaled reads it. The writer relies on
  the CLI reading it: the file is 0600, owned by the daemon user.
- `tailscale up` exits 0 only once tailscaled is `Running`. The first rehearsal join
  (2026-10-13) returned success and the node was `Running` with its tags on the next status
  read; not yet timed.
- `Self.Tags` lists the node's own tags: **seen** on the rehearsal join (`tag:mace4-comms`).
- `tailscale logout` removes an ephemeral node from the tailnet at once. Verify on the
  rehearsal (ADR-0005 D4).
- An ephemeral node the control plane removed while offline comes back as
  `BackendState == "NeedsLogin"` (auto-rejoin's trigger, ADR-0005 A2). Verify: join, power
  off long enough to be removed, power on, watch the panel.
- `tailscale up` and `tailscale set` accept `--advertise-routes=` (empty) as "no routes"
  (from `--help`). The writer always passes it on `up`.
- Once `install.sh` has run `tailscale set --operator=<daemon user>`, a later `tailscale up`
  that does not repeat `--operator=<user>` is expected to refuse ("requires mentioning all
  non-default flags") rather than silently reset it. This is why the installer and doctor
  print `tailscale up --operator=<user>`. Verified on 1.102.5 (2026-10-13): `tailscale set
  --operator` works while logged out (`NeedsLogin`), and `OperatorUser` is omitted from
  `tailscale debug prefs` when unset.
- MediaMTX's RTSP port 8554 (`relay_planner.MEDIAMTX_RTSP_PORT`) must match `rtspAddress`
  in `ops/mediamtx/mediamtx.conf.yml`. They match today, but nothing checks it yet.
