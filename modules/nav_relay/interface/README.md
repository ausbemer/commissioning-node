# nav-relay Inward Interface — v0.2.0

The stable contract between this module's core and its features (spec §4).
Additive rules apply: MINOR adds optional things, MAJOR is a rare planned event.

`__version__` (`0.2.0`) is the inward contract version; `VERSION` is the module release version
(spec §4 dual versioning). Both live in `interface/__init__.py`. Features import only from this
package: `from ..interface import MODULE, Feature, …`.

## Models and shared objects

| Name | Since | Purpose |
|------|-------|---------|
| `NavSource` (protocol) | 0.1.0 | The NMEA 2000 seam: `check_link()`, `receive(timeout_s)`, `close()`. **No send, by design** (rung 8). Adapter: `core/socketcan_source.py` |
| `SentenceSink` (protocol) | 0.1.0 | The NMEA 0183 seam: `send(address, port, payload)`. Adapter: `core/udp_sink.py` |
| `Reading`, `LinkReport` | 0.1.0 | One decoded frame (SI units, None for "not available", receive time on the node's monotonic clock); the link's state and why |
| `NavState` | 0.1.0 | Latest `Reading` per (PGN, source), capped at `MAX_SOURCES`, and the last `LinkReport`. Written by core's reader |
| `current_fix` | 0.1.0 | THE policy for what may be sent: freshness through `freshness` (`RAPID_MIN_S`), lowest fresh source per PGN, magnetic → true only with a fresh variation. Returns the contract's `NavFix` and `NavFeedSource`s |
| `destination_problem` | 0.1.0 | THE rule for who may receive: a host from the last scan, on a scanned network, not this node, not a broadcast/multicast address; or (0.2.0, optional `peers`) a tailnet address exactly one peer of a fresh peer table holds (ADR-0010 A2) |
| `tailnet_peer` | 0.2.0 | The node name of the one peer holding a tailnet address, or None |
| `Destination`, `DestinationTable` | 0.1.0 | Where nav data goes (≤ `MAX_DESTINATIONS`), memory only. The `sender` feature adds and removes; core's sender records sends |
| `ALLOWLIST`, `NOMINAL_PERIOD_S`, `PGN_*` | 0.1.0 | The crossing allowlist (§6) and each PGN's nominal period |
| `INTERFACE`, `LINK_*`, `OUTPUT_PERIOD_S`, `DEFAULT_PORT`, `MAX_*` | 0.1.0 | The link name ops gives the adapter; link states; provisional constants (`tools/ledgers/nav-relay/`) |

## Deprecations

| Field | Deprecated in | Replacement | Earliest removal (MAJOR) |
|-------|---------------|-------------|--------------------------|
| — | | | |

## Changelog

- v0.2.0 — `destination_problem` takes an optional `peers`; `tailnet_peer` (ADR-0010 A2).
- v0.1.0 — initial interface (ADR-0010).
