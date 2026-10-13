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
  1.102.5), so the key never appears in argv (ADR-0003 §5). Not yet exercised.
- Once `install.sh` has run `tailscale set --operator=<daemon user>`, a later `tailscale up`
  that does not repeat `--operator=<user>` is expected to refuse ("requires mentioning all
  non-default flags") rather than silently reset it. This is why the installer and doctor
  print `tailscale up --operator=<user>`. Verified on 1.102.5 (2026-10-13): `tailscale set
  --operator` works while logged out (`NeedsLogin`), and `OperatorUser` is omitted from
  `tailscale debug prefs` when unset.
- MediaMTX's RTSP port 8554 (`relay_planner.MEDIAMTX_RTSP_PORT`) must match `rtspAddress`
  in `ops/mediamtx/mediamtx.conf.yml`. They match today, but nothing checks it yet.
