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
| firewalld | 2.3.1-1+deb13u1, not pinned: the RevPi image enables it | host install `ops/` (ADR-0002 A1) |

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

### Q-005: an ephemeral node removed while offline boots into `NeedsLogin`

- **Dependency / version:** tailscale 1.102.5.
- **Behaviour:** a node joined with an ephemeral key is removed by the control plane while it
  is off. On the next boot tailscaled still has `WantRunning=true` but reports
  `BackendState == "NeedsLogin"` (`NoState -> NeedsLogin`), with no error. It does not come
  back `Running` on its own.
- **How measured:** the node was unplugged for up to ~2 h (after 17:32, booted 19:41:47,
  2026-10-13). tailscaled went `NeedsLogin` 30.4 s after boot; auto-rejoin started
  `tailscale up` at 35.1 s and the node was `Running` at 36.6 s (request `32d632b22031`,
  timed in `tools/ledgers/tailnet-writer/`).
- **Detection signature:** journal `Switching ipn state NoState -> NeedsLogin
  (WantRunning=true, ...)` on a node with a join record.
- **Our accommodation:** this is auto-rejoin's trigger (ADR-0005 A2). A long outage always
  means a rejoin; only a short one (inside the control plane's removal delay, not measured)
  comes back `Running` with no rejoin.
- **Variant (2026-10-25): it can boot `Running` instead.** Off ~2 days (last log
  2026-10-23 19:08, booted 2026-10-25 22:29:48, on a different network), tailscaled loaded
  its cached netmap (`control-netmap ... cached=true`, `active login: <missing-profile>`)
  and went `Starting -> Running`. Control then refused it (`RegisterReq: got response;
  nodeKeyExpired=false, machineAuthorized=false; authURL=true`). `tailscale status --json`
  kept `BackendState == "Running"` with a non-empty top-level `AuthURL`, `Self.Online`
  false and the cached `Peer` table; the console showed no node. Signature: `Running` with
  `AuthURL` set. `core/tailscale_cli.py` maps it to `NeedsLogin` (not connected, no peers),
  so the dashboard tells the truth and auto-rejoin sees its trigger. Not yet measured: whether
  auto-rejoin's `tailscale up` recovers from this state as it does from a plain `NeedsLogin`.
- **Last verified:** 1.102.5, 2026-10-13; the `Running` variant 2026-10-25.

### Q-006: unprivileged nmap misses hosts that drop TCP; the neighbour table still has them

- **Dependency / version:** nmap 7.95+dfsg-3 (Debian trixie), Linux 6.18 (RevPi), run as the
  daemon user without capabilities.
- **Behaviour:** without raw sockets nmap cannot ARP-ping, so host discovery is a TCP connect
  to the probe ports. A host whose firewall silently drops those ports (a Windows PC, some
  IoT devices) is reported down, with no error. The kernel resolved its MAC for the connect
  attempt anyway, so `ip -j neigh` on the scanned interface lists it (`REACHABLE`, `STALE` or
  `DELAY`). Addresses that did not answer ARP show `FAILED`/`INCOMPLETE` and are
  garbage-collected within seconds. The MAC in nmap's own XML is absent unprivileged.
- **How measured:** one scan of a /24 home-lab site, 2026-10-13
  (`-sT -PS80,443,554 -p 80,443,554 --max-rate 100 --max-retries 1 --host-timeout 30s`):
  nmap reported 10 hosts up; the neighbour table held 14 live entries on `eth0`, including
  the operator's PC (a tailnet peer) and 3 IoT devices nmap missed. Before the scan it held 3.
- **Detection signature:** a live neighbour entry for an address absent from nmap's XML.
- **Our accommodation:** site-survey's host list is nmap's hosts ∪ neighbour entries on the
  scanned networks **confirmed during the scan** (`ip -s -j -4 neigh`, `confirmed` ≤ the scan's
  duration + 5 s); a neighbour-only host is marked "answered ARP only" (ADR-0007 D2). The state
  word alone is not enough: Linux does not garbage-collect `STALE` entries while the table is
  below `gc_thresh1` (128), and a connect probe to a long-gone device moves its old entry to
  `DELAY`/`PROBE` with the old MAC still in it. Measured 2026-10-13: 12 of 14 entries were
  `STALE`, last confirmed ~966 s earlier, after the scans; only 2 were confirmed within 30 s
  (pre-merge review finding).
- **Last verified:** nmap 7.95+dfsg-3, 2026-10-13.

### Q-007: random (locally administered) MACs have no maker

- **Dependency / version:** nmap 7.95+dfsg-3, `/usr/share/nmap/nmap-mac-prefixes`.
- **Behaviour:** phones, laptops and travel routers using private Wi-Fi addresses present a
  MAC with the locally administered bit (`0x02` of the first octet) set. Its prefix is not an
  OUI, so the lookup finds nothing, with no error. Some real (globally unique) MACs are also
  missing from the shipped table (one on the measured site).
- **How measured:** same scan as Q-006: 3 of 14 live hosts had locally administered MACs, 1
  had a global MAC with no table entry.
- **Detection signature:** `int(mac[:2], 16) & 2`.
- **Our accommodation:** maker is optional; a locally administered MAC reads "private MAC",
  never a guessed vendor. Axis cameras use global Axis OUIs, so the Axis flag is unaffected.
- **Last verified:** nmap 7.95+dfsg-3, 2026-10-13.

### Q-008: a peer's `CurAddr` is set only while it has an active direct path

- **Dependency / version:** tailscale 1.102.5, `tailscale status --json`, logged in.
- **Behaviour:** `Peer[*].CurAddr` is `"ip:port"` (`"[ip]:port"` for IPv6) for a peer this node
  is talking to directly right now, and `""` otherwise. That includes a peer on the same LAN
  that is merely idle (`Active: false`), with no error. `Relay` is always set: it names the
  peer's home DERP region, not the path in use. `Addrs` was null for both peers.
- **How measured:** 2026-10-13, two peers. The operator's PC, with an open dashboard session
  over the tailnet, gave `CurAddr` = its site-LAN address with `Active: true`. A phone with no
  traffic gave `""` and `Active: false`, with `Relay: "nyc"` on both.
- **Detection signature:** `CurAddr == ""` with `Online: true`.
- **Our accommodation:** `TailnetPeer.endpoint` is the address in `CurAddr`, or None. The site
  map marks a scanned address "Tailnet traffic: <peer>" only while it is a fresh peer's
  endpoint, and the legend says it is where the traffic comes from (the peer, or a NAT in front
  of it), not proof of the device. An idle tailnet device on the site LAN shows as an ordinary
  host.
- **Last verified:** 1.102.5, 2026-10-13.

### Q-009: the RevPi image's firewalld rejects what our table accepts

- **Dependency / version:** firewalld 2.3.1-1+deb13u1, from the RevPi Debian 13 image
  (enabled since the image was built; pulled in again by `cockpit-revpi-redirect-firewalld`).
- **Behaviour:** firewalld runs with `lan0` in its default `public` zone, which accepts only
  22, 80, 443, 41443, mDNS and DHCPv6, and answers everything else on `lan0` with `reject with
  icmpx admin-prohibited`. An interface in no zone (`tailscale0`) falls through to the
  default zone and is rejected the same way, and its forward chain rejects forwarded traffic
  that isn't in a zone. Its `inet firewalld` table is a separate base chain at priority
  `filter + 10`, so an `accept` in our table (`filter - 10`) does not stop it: the packet is
  still rejected. Connections from the node to itself go through `lo` and are accepted, so
  every loopback check passes while the site LAN is refused.
- **How measured:** 2026-10-14. `https://<lan0 address>:8443/login` answered from the node,
  but the operator could not reach it from the site LAN. `sudo nft list ruleset` showed `table inet
  firewalld`, and `nft list chain inet firewalld filter_IN_public_allow` listed only the ports
  above. `systemctl is-enabled firewalld` gave `enabled`, and its journal showed it running on
  every boot. ADR-0002 had recorded "no firewall rules are loaded" (2026-10-12), probably
  from a `nft list ruleset` run without root, which prints nothing.
- **Detection signature:** `firewall-cmd --get-zone-of-interface=tailscale0` is not
  `trusted`, or `sudo firewall-cmd --zone=public --query-port=8443/tcp` prints `no`, or a
  site host reaches the node but not the tailnet while `sudo firewall-cmd --get-policies`
  has no policy from its zone into `trusted`. `sudo ops/doctor.sh` FAILs on each.
- **Our accommodation:** `ops/install.sh` adds exactly three entries to firewalld's runtime
  and permanent config: `8443/tcp` in every zone a site interface can land in (the default
  zone and the active ones, but not `trusted`), and `tailscale0` in `trusted` (ADR-0002 A1),
  and a policy `site-to-tailnet` forwarding those zones into `trusted` (ADR-0002 A3). It
  never disables or replaces firewalld. It reloads it once, only to load that policy.
- **Policies are permanent-only:** `firewall-cmd --new-policy` and `--set-target` refuse
  without `--permanent` ("Option can be used only with --permanent"), so a new policy is
  live only after `--reload`. One call takes only one of `--add-ingress-zone`,
  `--add-egress-zone` ("not allowed with argument") and `--set-target`.
- **Last verified:** 2.3.1-1+deb13u1, 2026-10-14 (the 8443 rejection); 2026-10-23 (the
  forward reject: a site host's ping into the tailnet got through only once the policy
  `public` → `trusted`, target `ACCEPT`, was loaded). That `tailscale0` in
  `trusted` lets the tailnet door, MediaMTX and the subnet routes through is not yet seen on
  a joined node: see "Unverified assumptions".

### Q-010: `tailscale logout` deletes the profile, and the operator with it

- **Dependency / version:** tailscale 1.102.5.
- **Behaviour:** `OperatorUser` is a per-profile pref. `tailscale logout` (the writer's
  Leave) deletes the profile, so the operator goes with it, with no warning. After that,
  every `tailscale up` run as the daemon user fails with `Access denied: checkprefs access
  denied` (exit 1) until root runs `tailscale set --operator=<user>` again. Joining again
  does not bring it back.
- **How measured:** 2026-10-25 on the node. At boot (22:29:48) tailscaled loaded
  `profile-16f6` with `op="pi"`. The dashboard's Leave at 22:39:00 logged
  `localapi: [POST] /localapi/v0/logout`, then `profile data directory: profile not found`
  and `NoState -> NeedsLogin`. `tailscale debug prefs` then had no `OperatorUser`, and three
  Joins (22:39:08, 22:41:32, 22:43:41) failed at `check-prefs`. The same happened on
  2026-10-21 (TODO.md).
- **Detection signature:** journal `localapi: [POST] /localapi/v0/logout` followed by
  `profile not found`. `tailscale debug prefs` has no `OperatorUser`, and
  `sudo ops/doctor.sh` FAILs "operator is 'none'".
- **Our accommodation:** ADR-0002 A4. A root oneshot (`ops/tailscale-operator.sh`,
  installed as a root-owned copy) sets the operator back when tailscaled starts, when
  `/var/lib/tailscale/tailscaled.state` changes, and every 5 minutes. The writer reports
  `checkprefs access denied` as a plain message, not the CLI's `sudo tailscale up` advice.
  Not yet measured: that a logout changes the state file, and Leave → operator back.
- **Last verified:** 1.102.5, 2026-10-25.

## Unverified assumptions (verify, then turn into entries or delete)

- `tailscale status --json` field mapping used by `tailscale_cli.py`. **Partly verified**
  on 1.102.5, logged out (2026-10-12): the top-level keys `BackendState` (`NeedsLogin` when
  logged out), `Self`, `Peer` and `User` exist, and `Peer`/`User` are `null` when logged out.
  `Self` has `HostName`, `DNSName`, `TailscaleIPs` and `UserID`. `Tags` and `PrimaryRoutes`
  are omitted when empty. Logged in (2026-10-15): `Self.DNSName` is the FQDN with a trailing
  dot (`pi5-gateway.tail01d907.ts.net.`); tailnet-gateway strips it (ADR-0008). Logged in (2026-10-13): `Peer[*].CurAddr` and `Relay` (Q-008).
  **Still unverified**, because it needs a logged-in node:
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
- With `tailscale0` in firewalld's `trusted` zone (Q-009), tailnet traffic to 8080 and to
  MediaMTX's RTSP port is accepted, and so is traffic forwarded from the tailnet to the site
  LAN for the subnet routes (`trusted` has target `ACCEPT`). Not yet seen: the node was logged
  out when Q-009 was measured. Verify after the next join with the dashboard over the tailnet
  and a camera reached through an advertised route. Also check that NetworkManager does not
  move `tailscale0` into another zone.
