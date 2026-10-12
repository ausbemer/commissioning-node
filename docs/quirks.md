# Vendor quirk registry (spec §7)

Measured *silent* behaviours of pinned foreign dependencies: things they do (drop, reorder,
ignore, clamp) without an error. **A pin bump re-verifies every entry for that dependency**
before the new version is adopted. The registry is the checklist for "is it safe to bump?"

Pinned dependencies covered here:

| Dependency | Pinned version | Contained behind (bridge module, §6) |
|------------|----------------|--------------------------------------|
| tailscale / tailscaled | not yet installed — pin at first install (ops installer) | `tailnet-gateway` (`modules/tailnet_gateway/core/tailscale_cli.py`) |
| MediaMTX | not yet installed — pin at first install (ops installer) | `stream-relay` (adapter not yet written) |
| Axis VAPIX (camera firmware) | per camera; record model + firmware seen on site | `camera-config` (adapter not yet written) |

## Entries

No entries yet: nothing has been measured. The first entry uses this shape:

```
### Q-001: <short name>
- Dependency / version:
- Behaviour:            what it silently does
- How measured:         the experiment, with a link to the log or test
- Detection signature:  log line / counter / test that shows it happening
- Our accommodation:    what our code does about it (file reference)
- Last verified:        version, date
```

## Unverified assumptions (verify, then turn into entries or delete)

- `tailscale status --json` field mapping used by `tailscale_cli.py`: `BackendState ==
  "Running"` means connected; `Self.HostName`, `Self.TailscaleIPs`, `Self.PrimaryRoutes`
  (routes this node is currently primary for, which is not the same as the routes it
  *advertises*). Taken from documentation, not from a captured sample on a pinned version.
- MediaMTX's default RTSP port 8554 (`relay_planner.MEDIAMTX_RTSP_PORT`) must match the
  provisioned `mediamtx.yml`.
