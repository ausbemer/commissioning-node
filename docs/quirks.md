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

- `tailscale status --json` field mapping used by `tailscale_cli.py`: `BackendState ==
  "Running"` means connected; `Self.HostName`, `Self.TailscaleIPs`, `Self.PrimaryRoutes`
  (routes this node is currently primary for, which is not the same as the routes it
  *advertises*). Taken from documentation, not from a captured sample on a pinned version.
- MediaMTX's RTSP port 8554 (`relay_planner.MEDIAMTX_RTSP_PORT`) must match `rtspAddress`
  in `ops/mediamtx/mediamtx.conf.yml`. They match today, but nothing checks it yet.
