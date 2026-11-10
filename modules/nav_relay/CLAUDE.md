# nav-relay — Module Context

> Module of the commissioning-node monolith
> following the spec §4 structure standard.

## Role in the ecosystem

Bridge between a vessel's NMEA 2000 bus and an NMEA 0183 reader on the site LAN (ADR-0010). It is the only module that touches the CAN link and the only one that sends nav data.

- **Reading:** a core reader thread reads `n2k0` (a Kvaser Leaf Lite v2 on the mainline `kvaser_usb` driver), only while the link is up and listen-only. Kernel filters pass exactly four PGNs (129025 position, 129026 COG/SOG, 127250 heading, 126992 time), and the reader keeps the latest reading per (PGN, source).
- **Sending:** a core sender thread runs every `OUTPUT_PERIOD_S` and sends one UDP datagram (`$GPGGA` + `$GPVTG`, and `$HEHDT` when a true heading is known; nothing else, ADR-0010 amendment 2026-11-10) to each destination an operator picked from the last site scan.
- **When data is stale:** a field older than its freshness window is not sent. Position falls back to GGA quality 0 with empty position fields; a stale course and speed to VTG mode `N`; a magnetic-only heading is not sent.
- **Never transmits on the NMEA 2000 bus** (spec §11 rung 8): the seam has no send, and the link is listen-only, set by ops and re-checked here.
- **Memory only:** nothing is persisted. A restart sends to nobody until an operator picks again (R1).

## Interfaces

Distinguish the two directions explicitly. **Outward** topics cross service boundaries and
their payloads conform to the ecosystem contract (`commissioning-node-interfaces`). **Inward**
topics/types are local to this module and defined by its own `interface/`.

- **Consumes — outward (from the bus):** `nav/destination` (`NavDestination`, command, via `main.invoke()`), `site/hosts` (`SiteHosts`, the last scan, to check a destination against), `system/tick` (`Tick`, the status cadence)
- **Consumes — inward (core → features):** `NavState` (latest readings and link report, written by core's reader), `DestinationTable` (sends recorded by core's sender)
- **Produces — outward (to the bus):** `nav/destination-result` (`NavDestinationResult`, every request), `nav/status` (`NavStatus`, every tick) — conforms to the ecosystem contract (`interfaces/` v0.10.0). The sentences themselves never ride the bus (§6: data plane).
- **Produces — inward (features → core):** destinations into the `DestinationTable`
- **Versions (spec §4 dual versioning):** release `VERSION` 0.2.0; inward
  contract `__version__` 0.1.0. Both live in `interface/__init__.py`; see
  `interface/README.md`.

## Features

| Feature | Status | Built on interface | Notes |
|---------|--------|--------------------|-------|
| `sender` | enabled | 0.1.0 | Adds a destination only if `destination_problem` passes against the last `SiteHosts`, up to `MAX_DESTINATIONS`; stops one or all. A `NavDestinationResult` for every request. |
| `status` | enabled | 0.1.0 | On each tick: link, `current_fix` (exactly what the sender would send now), sources with the selected one marked, destinations with sent counts and last error. |

Core: `n2k.py` (NMEA 2000 decode, kernel filters), `nmea0183.py` (sentences), `socketcan_source.py` (the `NavSource` adapter), `udp_sink.py` (the `SentenceSink` adapter), `workers.py` (reader and sender threads; started only by `build(spawn=…)`, which main's serve passes).

## Rules for agents working here

1. Core is I/O only; all logic in `features/`, behind the `Feature` protocol. The shared
   policy (`current_fix`, `destination_problem`) is in `interface/`, so the sender thread and
   the features run one definition.
2. Features never import each other or the core's internals — only `interface/`.
3. Need new fields? Bump interface MINOR **on dev first**, then branch (spec §4).
4. **Nothing is ever transmitted on the NMEA 2000 bus.** No send method on `NavSource`, no
   `send` call in the adapter (a test checks both), listen-only enforced. TX needs its own ADR,
   a safety review and an independent hardware kill (§11 rung 8).
5. **The crossing allowlist is the audited boundary (§6).** A new PGN changes `ALLOWLIST`,
   the kernel filters and ADR-0010 D2. A fast-packet PGN (e.g. 129029) needs reassembly and
   an amendment.
6. A new sentence, a faster rate or another destination kind changes what the operator
   confirms, so it changes `NAV_OPERATIONS`' warning and needs an ADR-0010 amendment.
7. Freshness goes through the `freshness` authority (`RAPID_MIN_S` floor), never a local window.
