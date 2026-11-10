# ADR 0010: Nav relay — NMEA 2000 in (Kvaser, listen-only), NMEA 0183 over UDP out

- **Status:** Accepted (2026-11-10), with the proposed rulings R1–R4
- **Date:** 2026-11-10
- **Deciders:** Austin Emerson
- **Shape:** standard, with a Safety section (spec §7, §11). The node is attached to a vessel's
  NMEA 2000 bus (a safety-critical medium it does not own, §11 rung 8), and it sends data to a
  device the site owns (rung 3).

## Context

The operator asked (2026-11-10) for this: *a Kvaser Leaf Lite v2 on a USB port, pulling data
off an NMEA 2000 bus; send position, speed, course over ground and heading to a device for
its gpsd; pick the destination from the network scan.*

Operator rulings (2026-11-10):

- **The node pushes.** The node sends NMEA 0183 sentences over UDP to the device. The device's
  gpsd reads them as a UDP source (`gpsd udp://0.0.0.0:2947`). Port 2947 is the *device's*
  port. Nothing on the node listens for this feature.
- **The adapter is driven by the mainline `kvaser_usb` SocketCAN driver, built with DKMS.**

What the node has to work with, measured 2026-11-10 on the dev node (Pi 5, kernel
`6.18.50+rpt-rpi-2712`):

- **The kernel has no Kvaser driver.** `CONFIG_CAN_KVASER_USB is not set`. SocketCAN itself is
  there (`can`, `can-raw`, `can-dev`, `vcan` are modules; `gs_usb` and `peak_usb` ship). The
  RevPi image's kernel is unchecked. The driver therefore has to come from DKMS: the mainline
  source pinned to the running kernel's version, rebuilt on every kernel update.
- **`gpsd` 3.25, `python3-can` 4.5.0 and `can-utils` 2023.03 are in trixie.** The node needs
  none of them at runtime: Python's own `socket` speaks `AF_CAN`, and gpsd runs on the
  destination. `can-utils` is for the doctor and for recording the replay corpus.
- **An unprivileged process may open a `CAN_RAW` socket.** The daemon stays `User=` the checkout
  owner with `NoNewPrivileges=yes`. Configuring the link (bitrate, listen-only, up) needs root,
  so that is an ops unit's job, like the firewall.
- **The four PGNs we need are all single-frame PDU2.** None needs fast-packet reassembly (table
  in D2). PGN 129029 (GNSS Position Data) would add fix quality and altitude, but it is
  fast-packet. It is deferred.
- **The tick is 5 s, and delivery on the bus is synchronous.** A nav feed wants about 1 Hz out
  of a 10 Hz bus. Spec §6 ("transport tiering"): *if you could reconstruct the traffic from it,
  it's data plane*. Per-sentence output is data plane. Only the latest-value status belongs on
  the bus.
- **The firewall needs no change.** `inet commissioning_node` has no output chain, and the
  feature opens no listening port.

## Decision

### D1 — A new bridge module, `nav-relay`, is the only thing that touches the NMEA 2000 bus

`modules/nav_relay/` (spec §4 layout) is the bridge between the vessel's NMEA 2000 bus and an
NMEA 0183 consumer on the site LAN. It has two seams in its `interface/` (rule 16): `NavSource`
(SocketCAN in; adapter `core/socketcan_source.py`) and `SentenceSink` (UDP out; adapter
`core/udp_sink.py`). It is one module rather than two because the 1 Hz data path must not cross
the bus (§6). It is the sole reader of the CAN link and the sole sender of nav sentences
(rung 9).

### D2 — What crosses from the NMEA 2000 bus (the allowlist, §6)

**Inbound, and nothing else:** the adapter sets kernel `CAN_RAW_FILTER`s for exactly these PGNs.
Every other frame is dropped in the kernel and never decoded (§8.8).

| PGN | Name | Fields used | Nominal rate |
|-----|------|-------------|--------------|
| 129025 | Position, Rapid Update | lat, lon (int32, 1e-7°) | 10 Hz |
| 129026 | COG & SOG, Rapid Update | COG reference (true/magnetic), COG (1e-4 rad), SOG (1e-2 m/s) | 4 Hz |
| 127250 | Vessel Heading | heading, deviation, variation (1e-4 rad), reference | 10 Hz |
| 126992 | System Time | date (days since 1970), time (1e-4 s) for the RMC time fields | 1 Hz |

A field holding the standard's "not available" sentinel (`0x7FFF…`/`0xFFFF…`) is missing, never
a value. The PGN is taken from the 29-bit ID (`(id >> 8) & 0x3FFFF`; all four are PDU2), and the
source address from its low byte.

**Outbound to the NMEA 2000 bus: nothing, ever.** Rung 8 makes transmitting on a
safety-critical medium its own ADR, with a hardware kill. Three independent guards hold this:

1. **The controller is listen-only.** The link unit sets `listen-only on` and refuses to bring
   the link up if the driver doesn't accept that mode (D6). In listen-only mode the adapter
   doesn't even send ACK bits.
2. **No write path in code.** `NavSource` has no send method, the adapter never calls `send` on
   the CAN socket, and a test checks both.
3. **The doctor checks** that the link is listen-only.

**Source selection** (§4 "selected inputs are facts too"): several devices may send the same PGN.
Per PGN, the fresh source with the lowest source address wins. The status lists every source
seen, and the dashboard warns when a PGN has more than one (ruling R3). The rule lives in one
place, `interface/`.

### D3 — What goes out (the data plane)

Every `OUTPUT_PERIOD_S` (1.0 s, provisional), a core sender thread sends one UDP datagram per
destination. The datagram holds:

- **`$GPRMC`**: time and date from PGN 126992 (empty if no fresh system time), status `A`,
  lat/lon, SOG in knots, COG in degrees true. If COG is magnetic and a fresh variation is known,
  it is corrected to true. Otherwise the COG field is empty.
- **`$HEHDT`**: true heading, when the heading is true, or magnetic with a fresh variation.
- **`$HCHDG`**: magnetic heading with deviation and variation, when only magnetic heading is
  known.

Sentences are at most 82 characters, with checksums, ending in CRLF. The sender runs on
wall-clock deadlines (§8.8), never on the tick. Encoding and decoding are the vendor wire
formats, so they live in `core/` (rule 16). The *policy* lives in `interface/`, so the feature
and the sender share one definition: which fields are fresh enough to send, and what to send
when they aren't.

**Fails toward "no fix" (rule 10, rung 11):** each field is a renewed level, judged on the
node's monotonic receive clock.

- Stale position → `$GPRMC` with status `V` and empty position, speed and course fields. That is
  an explicit "no fix", never the last position repeated.
- Stale COG/SOG → those fields are empty.
- Stale heading → no heading sentence.
- Link down or no adapter → the same `V` sentence. The destination's gpsd sees "no fix" rather
  than silence it might take for a slow link.

The staleness window comes from the `freshness` authority, using each PGN's nominal period
(ruling R2).

### D4 — The destination is a host from the last scan

- **Selection:** in the overview's host table, each scanned host gets "Send nav data here".
  This is a dashboard `Action` behind `authorize()`, may-disturb with a slide to confirm (D7),
  and it asks for the UDP port (default **2947**). It sends `nav/destination` through `invoke()`
  with `requires=nav-relay:sender`. A matching "Stop" sends `nav/destination` with no address.
- **The relay checks the address itself (rung 13).** `nav-relay` subscribes to `site/hosts` and
  refuses any address that:
  - is not a host in the last completed scan;
  - is one of the node's own addresses;
  - is in tailnet space (100.64.0.0/10);
  - is outside the scanned networks.

  So a dashboard request can't point the sender at an arbitrary address (same principle as
  ADR-0007's "no targets"). It records the host's MAC from the scan with the destination.
- **At most `MAX_DESTINATIONS` (4, provisional) at once.** The node never broadcasts and never
  multicasts.
- **Lifetime (ruling R1):** held in memory only. A restart or teardown clears it, and the
  operator picks again after a restart.

### D5 — Contract 0.10.0 (additive)

- `nav/destination`: `NavDestination` (command, via `main.invoke()`): `request_id`,
  `requested_by`, `door`, `operation` (`"send"` or `"stop"`; open vocabulary, an unknown one is
  refused), `address: str | None` (for stop, None stops every destination), `port: int = 2947`.
- `nav/destination-result`: `NavDestinationResult`: `request_id`, `ok`, `message`,
  `requested_by`, `door`, `operation`, `finished_at`.
- `nav/status`: `NavStatus`, once per tick, declares `period_s`:
  - `link` (`"up"`, `"down"`, `"no-adapter"`, `"not-listen-only"`; open vocabulary);
  - `fix: NavFix | None`;
  - `sources: list[NavSource]` (PGN, source address, age);
  - `destinations: list[NavDestinationState]` (address, port, MAC, sent, last error);
  - `origin`.

  `NavFix` (`Shape`) holds `lat`, `lon`, `sog_kn`, `cog_true_deg`, `heading_true_deg`,
  `heading_mag_deg`, each with an age in seconds, all optional. This is bounded latest-value
  state (§6), not a per-sample stream.
- Capability `nav-relay:sender`. Status publishing is the `status` feature.

### D6 — Ops

- **Driver:** `ops/dkms/kvaser_usb/` packages the mainline `drivers/net/can/usb/kvaser_usb/`
  source (GPL-2.0), taken from the tag matching the running kernel series and pinned in
  `pins.sh`. `install.sh` installs `dkms` and the kernel headers (`linux-headers-rpi-2712` here;
  the RevPi row is found on the RevPi), then registers and builds the module. DKMS rebuilds it
  on every kernel update. The doctor checks that `modinfo kvaser_usb` matches `uname -r`. On a
  kernel that already ships the driver, the installer skips DKMS.
- **Link name:** a udev rule names any netdev on the `kvaser_usb` driver `n2k0`. A second CAN
  adapter is never mistaken for the vessel bus.
- **Link unit:** `commissioning-node-can@n2k0.service` (root, oneshot, `BindsTo=` the device, so
  it runs on hot-plug). It sets `bitrate 250000 listen-only on restart-ms 100`, brings the link
  up, then reads `ip -d -j link show n2k0`. If it isn't `LISTEN-ONLY`, it brings the link down
  and fails.
- **Pins:** `can-utils` (doctor, `candump` for the corpus). No gpsd on the node.
- **Firewall:** unchanged (no listener; there is no output chain). Rule 15 is untouched.

### D7 — Sending to a site device is a may-disturb action

The warning on "Send nav data here" (rung 4):

> *Sends this vessel's position, speed, course and heading to `<address>:<port>` as unencrypted
> NMEA 0183 over UDP, once a second. Anyone on the site LAN can read it. Check the device
> expects it before you send.*

The overview gets a **Nav feed** panel showing:

- the link state and its listen-only confirmation;
- the fix with each field's age;
- the sources (with the multi-source warning);
- each destination with sentences sent and last error, and its Stop control.

### D8 — CLAUDE.md rule 13 gains a second, outbound exception

Rule 13 keeps every *surface* and *stream* off the site LAN. This feature serves nothing. It
sends to one operator-confirmed scanned host, outbound only, and nothing on the node listens.
The rule is amended to name it, so the exception is a recorded deal (§6), not a precedent.

## Safety (spec §11)

1. **Rung 1 and rung 8 (read-only, no TX on a safety-critical medium):** D2's three guards. TX
   on the NMEA 2000 bus would need its own ADR, a safety review and an independent hardware kill.
2. **Rung 3 (first write to an unowned device is confirmed):** nothing is sent until an operator
   picks a host and confirms. Nothing is sent on start, after a restart (R1), on a scan, or on a
   timer.
3. **Rung 4:** D7's warning travels with the action.
4. **Rung 9 (one writer):** one sender thread, owned by `nav-relay`. A changed destination list
   is handed over through one interface object, as with site-survey's `ScanSlot`.
5. **Rungs 11 and 13:** freshness is judged by the sender itself over its own receive clock, and
   it fails to an explicit "no fix" (D3). The relay re-checks every destination against the scan
   (D4), whatever the dashboard checked.
6. **Bounded:**
   - at most `MAX_DESTINATIONS`;
   - one datagram per destination per `OUTPUT_PERIOD_S`;
   - latest-value state keyed by (PGN, source), capped at `MAX_SOURCES` (16) pairs;
   - socket receive buffer bounded.

   All are provisional, measured, and ledgered in `tools/ledgers/nav-relay/` (§8.11).
7. **Site data:** the position is never written to disk or logged. It appears only behind the
   doors and on the wire to the confirmed destination.
8. **Replay corpus (§8.12):** a `candump -l` log from the real bus goes in
   `tests/replay/nav-relay/`. Decoder and encoder tests run against it. The expected sentences
   are cross-checked once by feeding them to a real gpsd 3.25 (`gpsd -N udp://…`, `gpspipe -w`).

## Rulings (2026-11-10: the operator took every proposal)

- **R1 — Destination lifetime.** *Proposed:* memory only. After a restart the operator picks
  again. The alternative: persist it under the registry's `site/` and resume after a restart only
  if the neighbour table still shows the recorded MAC at that address. That is more convenient,
  but the node would then start sending without anyone present.
- **R2 — Staleness window for nav fields.** `freshness.stale_after(0.1)` is clamped to its
  5 s floor. A vessel at 10 kn moves about 25 m in 5 s. *Proposed:* add one named bound to the
  authority, `RAPID_MIN_S = 1.0`, used through `stale_after(period_s, floor=RAPID_MIN_S)`, so
  there is still one formula. The alternative is to accept 5 s.
- **R3 — Several sources for one PGN.** *Proposed:* lowest fresh source address wins, and the
  dashboard warns. An operator override (picking a source) comes later if a site needs it.
- **R4 — Sentence set and port.** *Proposed:* RMC + HDT (HDG when only magnetic), UDP port
  2947 by default and editable per destination. Confirm the destination's gpsd version (HDG
  needs a recent gpsd) and that it is started with a `udp://` source.

## Options considered

- **gpsd on the node, with its built-in `nmea2000://` driver.** Rejected: ruled pull, not push
  (2026-11-10), and the gpsd NMEA 2000 driver is marked experimental. It would also open a site
  LAN listener.
- **Kvaser linuxcan / CANlib.** Rejected by ruling: not SocketCAN, and it comes with a
  proprietary SDK.
- **A `gs_usb` adapter instead.** No driver work at all; kept as the fallback if the DKMS build
  proves fragile on the RevPi kernel.
- **Two modules (CAN bridge + 0183 sender) joined by a `nav/fix` topic.** Rejected: the
  per-second fix would ride the bus as a data-plane stream (§6), and core threads can't publish
  off the tick. Revisit if a second consumer of the raw fix appears (then: §6 transport tiering
  ADR).
- **`python-can`.** Not needed: stdlib `socket` covers `AF_CAN`/`CAN_RAW` and the filters.
  That is one less dependency to pin.
- **PGN 129029 for fix quality and altitude.** Deferred: fast-packet reassembly is more code,
  and the operator asked for position, speed, course and heading only.

## Consequences

- The node gains a kernel module it builds itself. A kernel update that DKMS can't build for
  leaves the link `no-adapter`, and the doctor says why.
- A new module, three topics, and contract 0.10.0. The dashboard gains a panel and a per-host
  action.
- The vessel's position crosses the untrusted site LAN in cleartext to one confirmed host. This
  is accepted and warned about (D7, D8).
- Every silent behaviour of `kvaser_usb`, the adapter and the destination's gpsd that we measure
  goes in `docs/quirks.md`.

## Implementation

1. **Ops + measurement (on the node, with the adapter):**
   - the DKMS package, udev rule, link unit and doctor checks;
   - verify the Leaf Lite v2 enumerates (`kvaser_usb`, product id) and accepts listen-only
     (quirk entry either way);
   - record `candump -l` from the vessel bus, measure each PGN's real rate and the source count
     (ledger, corpus).
2. **Contract 0.10.0 and `nav-relay`:**
   - the seams, SocketCAN source, PGN decoders, 0183 encoder, UDP sink and sender thread;
   - the `sender` and `status` features;
   - tests on the corpus plus `vcan0`;
   - boundary and additivity checks green.
3. **Dashboard:** the Nav feed panel and the per-host "Send nav data here" / "Stop" actions.
4. **Rehearsal:**
   - `vcan0` replay → UDP → gpsd on a laptop (`gpsd -N -n udp://0.0.0.0:2947`, `gpspipe -w`);
   - then the real bus to the real device;
   - unplug the adapter mid-run and confirm `V` within the window.
5. **Docs:**
   - CLAUDE.md (module and topic tables, rule 13 per D8, layout's `ops/` row);
   - `docs/quirks.md`;
   - TODO.md.

### Progress

- **2026-11-10, steps 2, 3 and 5 done, and the ops half of step 1** (`dev`, uncommitted on
  writing):
  - contract 0.10.0;
  - `freshness.RAPID_MIN_S`;
  - the `nav-relay` module (35 tests on frames built from the standard's layouts, and the
    sentences cross-checked with pynmea2);
  - dashboard 0.14.0: the Nav feed panel, Send (a slider carrying a host picker and a port,
    whose edits hold the refresh for up to 60 s) and Stop;
  - ops: the DKMS package, udev rule, link unit, can-utils pin and doctor section.

  The pinned `kvaser_usb` sources were built by hand against 6.18.50+rpt-rpi-2712 (quirks
  Q-013).
- **One change from D7:** the warning sits as a note above the Send slider, not only in the
  no-script confirm, so it shows with script too (rung 4).
- **Still to do:**
  - step 1 on the node with the adapter: `sudo ops/install.sh`, plug in, doctor;
  - check listen-only is accepted (quirks, "Unverified assumptions");
  - record `candump`, and fill the ledger;
  - step 4 (rehearsal to a real gpsd).
