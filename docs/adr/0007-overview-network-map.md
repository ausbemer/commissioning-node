# ADR 0007: Overview network map — from an operator-run site scan

- **Status:** Accepted (2026-10-13)
- **Date:** 2026-10-13
- **Deciders:** Austin Emerson
- **Shape:** standard, with a Safety section (spec §7, §11): the scan sends traffic to devices
  the site owns.

## Context

Mockup 3 ("Network Map", Dashboard Directions artifact, 2026-10-13) opens the overview on a map
of site LAN → gateway → tailnet. A first draft of this ADR built the map only from what the bus
already carries (registered cameras, routes, streams). The operator asked for something rawer
to start with (2026-10-13): **scan the site network, draw every device found, and flag the ones
we recognise.** Recognised means an Axis camera (identified over VAPIX) or a tailnet node
(identified by Tailscale). Everything else still shows what the scan learned, such as its
hostname and maker, even though no module acts on it.

What the node has to work with, measured 2026-10-13:

- **nmap is not installed.** It would be pinned in `ops/pins.sh` and installed like Tailscale
  and MediaMTX.
- **The daemon is unprivileged** (`User=` the checkout owner, `NoNewPrivileges=yes`). Without raw
  sockets nmap discovers hosts by TCP connect only: no ARP sweep, no MAC addresses, no OS or
  device-type fingerprint (`-O`).
- **The kernel fills in the MACs anyway.** A TCP connect to a host on the same link makes the
  kernel resolve its MAC, so after the sweep `ip -j neigh` holds a MAC for every host that
  answered ARP, even when it refused the connection. The MAC prefix gives the maker
  (`nmap-mac-prefixes` ships the table). Axis prefixes mark a host as a *likely* Axis device
  before VAPIX confirms it.
- **Tailscale knows which peers sit on the site LAN.** `tailscale status --json` gives each
  peer's current direct endpoint (`CurAddr`, e.g. `192.168.36.238:41641`), which is a site
  address when the peer reaches this node directly over the LAN. A peer relayed through DERP, or
  behind another NAT, has no LAN endpoint and won't match. That is a limit, not an error.
- **Axis has an unauthenticated read for identification.** VAPIX
  `/axis-cgi/basicdeviceinfo.cgi` `getAllUnrestrictedProperties` returns product, serial and
  firmware without credentials. It is a read: rule 14 (the first *write* is confirmed) is not
  triggered.
- **The firewall needs no change.** `inet commissioning_node` has no output chain, and replies
  to the node's own connections are accepted.
- **A commissioning site is often an industrial network.** Some PLCs and older field devices
  misbehave under aggressive scans, and site IT may treat an unannounced scan as an attack.

Operator rulings (2026-10-13): **gentle scan now, a privileged profile later** under its own
ADR; **a scan runs only when the operator presses Scan.**

## Decision

### D1 — A new bridge module, `site-survey`, is the only thing that scans

`modules/site_survey/` (spec §4 layout) is the bridge to nmap and the neighbour table, behind a
`SiteScanner` seam in its `interface/`, with the adapter in `core/` (rule 16). It owns site
*discovery*; it never writes to a device. Identification stays with each vendor's bridge:
camera-config speaks VAPIX, tailnet-gateway speaks Tailscale. Nobody else runs nmap.

### D2 — What a scan does (the gentle profile)

- **Scope:** the IPv4 networks the node is on now, i.e. `TailnetStatus.site_networks`
  (tailnet-gateway is their authority, ADR-0005 A3). The command carries **no targets**, like
  `TailnetApply`: a dashboard request can't point the scanner anywhere else. A network wider than
  `MAX_PREFIX` (/22, 1022 hosts) is refused with a reason. Every detected network must pass, or
  none is scanned: a node also on a /16 (or a docker bridge) scans nothing until that network is
  gone, and the refusal says which one. The tailnet and the node's own
  addresses are never scanned.
- **Probes:** unprivileged nmap host discovery and a connect scan of a short port list:
  `80, 443, 554` (web and RTSP, the ones that matter to cameras), with reverse DNS through the
  site resolver. Polite timing: one scan at a time, a rate cap (`MAX_RATE` packets/s), a total
  timeout. No version probes, no scripts, no UDP.
- **Then** read `ip -j neigh` on the scanned interface: it gives the MACs, and it also lists
  hosts that answered ARP but dropped every TCP probe (quirks Q-006: 4 of 14 on the first
  site, including a tailnet peer). Each MAC prefix is looked up for the maker; a locally
  administered MAC reads "private MAC" (Q-007).
- **Result:** nmap's hosts ∪ live neighbour entries, one per address, with address, MAC, maker,
  hostname, open ports from the list, whether its maker is Axis, and how it was found
  (`tcp` or `arp`).

### D3 — Recognition: each vendor's bridge flags its own

| Flag | Authority | How |
|------|-----------|-----|
| **This gateway** | site-survey | an address of the node itself |
| **Axis camera, identified** | camera-config | after a scan, a VAPIX `basicdeviceinfo` read of each Axis-maker host with 80 or 443 open; publishes product, serial, firmware |
| **Axis, by maker only** | site-survey | MAC prefix only; VAPIX not tried, or it didn't answer |
| **Registered camera** | camera-config | the address of an announced camera (`camera/announced`) |
| **Tailnet node** | tailnet-gateway | a peer whose direct endpoint is that address (new `TailnetPeer` field) |

The map joins these by address. That join is a display fact that only the dashboard needs, so
the dashboard does it (rule 9 applies when a second consumer appears). Identification proposes;
it never registers a camera. Adopting one is the discovery feature in TODO (rung 2).

### D4 — Contract 0.6.0 (additive)

- `site/scan`: `SiteScan` (command, via `main.invoke()`): `request_id`, `requested_by`, `door`.
  No targets, no options.
- `site/hosts`: `SiteHosts`: `scanned_at`, `networks: list[str]`, `hosts: list[SiteHost]`,
  `origin`. A snapshot, published only after a scan that completed. `SiteHost` (`Shape`):
  `address`, `mac`, `maker`, `hostname`, `open_ports: list[int]`, `axis_maker: bool`,
  `found_by: str` (`"tcp"` or `"arp"`; open vocabulary).
- `site/scan-result`: `SiteScanResult`: `request_id`, `ok`, `message`, `requested_by`, `door`,
  `finished_at`, `host_count`. A failed scan keeps the previous `site/hosts`.
- `camera/identified`: `CameraIdentification`: `address`, `serial`, `model`, `firmware`,
  `origin`, `observed_at`. It says what a device claimed to be. It is not a registration.
- `TailnetPeer.endpoint: str | None`: the peer's current direct endpoint address (no port), when
  it has one.

### D5 — The map

- **Layout:** one backbone per scanned network (its CIDR is the label), the gateway on it,
  joined to a tailnet band above. Hosts hang off the backbone as tiles, **flagged first**
  (gateway, cameras, tailnet nodes), then the rest sorted by address. Each tile shows the
  address, hostname or maker, and its flags as text plus an icon, never colour alone.
- **Scale:** up to `MAP_TILES = 24` tiles. Past that, unflagged hosts fold into one "N other
  hosts" tile, and the full list is a table below the map. Flagged hosts are never folded.
  `MAP_TILES` is a display preference, not a behaviour constant.
- **Age, not liveness:** a scan is a snapshot, so the map never claims a device is up. It says
  "Scanned 14 min ago by alice@…" and greys out once older than an hour (a display choice).
  "Not scanned yet" until the first scan on a site. The Scan control sits beside the age.
- **Rendering:** server-rendered inline SVG in `overview-page` (CSP unchanged, all text
  escaped), wide and tall drawings switched by CSS, coloured from the page tokens. It updates
  with the existing refresh. While a scan runs, the page says so, and the result row shows the
  outcome like a tailnet apply.
- The three columns (cameras, gateway, streams) stay below the map.

### D6 — Scan is a may-disturb action

Scan is a dashboard `Action` behind `authorize()`, may-disturb, a slide to confirm, with the
warning: *"Probes every address on the site network (ports 80, 443, 554). Some site security
tools report scans; check with site IT first."* It records who asked and through which door,
and sends `site/scan` through `invoke()` with `requires=site-survey:scanner`.

## Safety (spec §11)

1. **Rung 1 (read-only first):** the scan and the VAPIX read write nothing to any device.
2. **Rung 3 (operator-confirmed):** no scan runs unless an operator presses Scan. Nothing runs
   on start, on joining a site, or on a timer.
3. **Rung 4 (warning in the manifest):** D6's warning travels with the action.
4. **Rung 9 (one writer):** one scan at a time. A second request while one runs is refused with
   "a scan is already running".
5. **Bounded:** scope from detected networks only, capped by `MAX_PREFIX`; a rate cap; a timeout
   that kills nmap (`SCAN_TIMEOUT_S`, then `KILL_MARGIN_S`); output capped (`OUTPUT_CAP`); at most
   `MAX_HOSTS` kept. All provisional, each measured on a real site and ledgered
   (`tools/ledgers/site-survey/`).
6. **Site data is site data:** hosts live in memory only, are never written to disk, and are gone
   on restart or teardown. They are shown only behind the doors.
7. **Fails safe:** if nmap is missing, a network is too wide, or the scan times out, the result
   says why, and the map keeps the last good scan, labelled with its age.

## Options considered

- **Map only from bus facts** (this ADR's first draft): honest but sparse, and blind to every
  device we don't manage. Its finding still stands and is kept as a separate step:
  *the overview never judges `TailnetStatus` freshness* (it would say "connected" forever if
  tailnet-gateway went quiet). Fix: the collector also takes `system/tick` and asks `freshness`,
  as stream-relay does.
- **Privileged scan now** (`CAP_NET_RAW` scan unit, ARP sweep, `-O`): richer device types,
  noisier, more ops work. Deferred by ruling; its own ADR.
- **Periodic scanning:** rejected by ruling. It probes a network we don't own without anyone
  asking.
- **Scan inside camera-config or tailnet-gateway:** rejected. Discovery serves every flag, so
  putting it in one vendor's bridge would make that module the middleman for the others (§4).
- **mDNS / NetBIOS names:** useful hostnames on networks without reverse DNS. Deferred: they need
  NSE scripts or another tool, and the gentle profile is deliberately small.

## Consequences

- The map shows the site as it is, including devices nobody registered. That is the point.
- Hosts that answer neither TCP nor ARP are invisible to the gentle scan. The map says "hosts
  that answered", never "all devices".
- Device type is only as good as maker + ports + hostname until the privileged profile.
- A new module, four topics and one optional field: contract 0.6.0. tailnet-gateway and
  camera-config gain read-only features.
- Every silent nmap / neighbour-table behaviour measured goes in `docs/quirks.md`.

## Implementation

1. **Ops:** pin nmap in `pins.sh`, install it, doctor checks the version. Run one gentle scan by
   hand on this site and record timing, packet rate and what it found (ledger, quirks).
   *Done 2026-10-13:* nmap 7.95+dfsg-3 pinned and held; a /24 took 30.7 s at 100 packets/s,
   10 hosts by TCP plus 4 only in the neighbour table (Q-006, Q-007,
   `tools/ledgers/site-survey/`). Still open: a second scan to separate the host timeout from
   the address count: done, 23.6 s with a 15 s host timeout (ledger).
2. **Contract 0.6.0** and the `site-survey` module: seam, nmap + neighbour adapter, the scanner
   feature, tests against recorded nmap XML. Capability `site-survey:scanner`.
   *Done 2026-10-13* (`feature/site-survey`): contract 0.6.0 (with `SiteHosts.self_addresses`,
   so the gateway is placed on the map without being scanned); the module, 33 tests; one run
   of the real adapter on this site (14 hosts, 27.6 s, ledger).
3. **Dashboard:** the Scan action and result row, then the map from `site/hosts` alone (gateway
   and maker flags).
   *Done 2026-10-13* (dashboard 0.9.0): the Scan slider and its result, the map. One change from
   D5: the map is HTML tiles on CSS "wires", not two SVG drawings. It reflows from phone to wide
   screen without a second drawing, its text stays selectable and readable by screen readers,
   and it needs no script either. The dashboard also records the `site/scan` it sent, so while a
   scan runs the map reads "Scanning since …" in place of the slider (and says so if no result
   came within 320 s, the scan's timeout plus a tick); the page keeps refreshing meanwhile.
4. **tailnet-gateway:** `TailnetPeer.endpoint`; the map flags tailnet nodes.
   *Done 2026-10-13* (`feature/tailnet-endpoints`): `CurAddr` → `TailnetPeer.endpoint`;
   `PeerDirectory.endpoints()` gives fresh peers by endpoint, for display only, never for a
   caller's identity, and a stale peer table flags nobody. One limit, measured (quirks Q-008):
   `CurAddr` is empty for an idle peer, so only a peer talking to this node directly right now
   is flagged.
5. **camera-config:** VAPIX identification (read-only) and `camera/identified`; the map flags
   cameras.
6. **Separately:** the `TailnetStatus` freshness fix (Options, first bullet).
7. Versions, CLAUDE.md (module table, topics), TODO.md.
