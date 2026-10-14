# site-survey — Module Context

> Module of the commissioning-node monolith
> following the spec §4 structure standard.

## Role in the ecosystem

Bridge to nmap and the kernel's neighbour table (ADR-0007): the only module that scans the site network. A scan runs only on an operator's `site/scan` (never on start, on a timer or on joining a site), over the networks tailnet-gateway reports in a fresh `TailnetStatus.site_networks`, one at a time, with the gentle profile: unprivileged connect probes of ports 80, 443 and 554, a rate cap, per-host and total timeouts, reverse DNS. The neighbour table adds MACs and the hosts that dropped every probe (quirks Q-006); the MAC prefix gives the maker (Q-007) and an Axis hint. It never writes to a device. Hosts are site data: in memory only, published once, never persisted. Identification belongs to each vendor's bridge (camera-config: VAPIX; tailnet-gateway: peer endpoints), not here.

## Interfaces

Distinguish the two directions explicitly. **Outward** topics cross service boundaries and
their payloads conform to the ecosystem contract (`commissioning-node-interfaces`). **Inward**
topics/types are local to this module and defined by its own `interface/`.

- **Consumes — outward (from the bus):** `site/scan` (`SiteScan`, command, via `main.invoke()`), `tailnet/status` (`TailnetStatus`, for `site_networks`), `system/tick` (`Tick`, the cadence that judges the status fresh, and the drain of finished scans)
- **Consumes — inward (core → features):** `site-survey/scan-outcome` → `ScanOutcome`, routed from the `ScanSlot` on the tick after a scan finishes; never subscribed on the bus
- **Produces — outward (to the bus):** `site/hosts` (`SiteHosts`, after a completed scan), `site/scan-result` (`SiteScanResult`, every refusal and outcome) — conforms to the ecosystem
  contract (`interfaces/` v0.6.0)
- **Produces — inward (features → core):** `ScanPlan`s into the `ScanSlot` (`scanner` → `core/scan_worker.py`)
- **Versions (spec §4 dual versioning):** release `VERSION` 0.1.0; inward
  contract `__version__` 0.1.0. Both live in `interface/__init__.py`; see
  `interface/README.md`. The inward contract follows the same additive rules as the ecosystem
  contract, with a one-repo blast radius.

## Features

| Feature | Status | Built on interface | Notes |
|---------|--------|--------------------|-------|
| `scanner` | enabled | 0.1.0 | Refuses a scan without a fresh status, with no or unscannable networks (`scan_problem`), or while one runs; otherwise hands one `ScanPlan` to core's worker. Publishes `SiteHosts` for a completed scan and a `SiteScanResult` for every request. Keeps no hosts. |

## Rules for agents working here

1. Core is I/O only; all logic in `features/`, behind the `Feature` protocol.
2. Features never import each other or the core's internals — only `interface/`. This
   includes the module name constant `MODULE`, which lives in `interface/__init__.py`
   (import `from ..interface import MODULE`; package path `modules.site_survey`), never in `core/`.
3. Need new fields? Bump interface MINOR **on dev first**, then branch (spec §4).
4. Merge early, register disabled (spec §8.4) beats long feature branches.
5. Watch for the middleman smell (spec §4): if this module starts carrying data only
   one feature needs, that feature is a graduation candidate.
6. External I/O goes behind a vendor-neutral seam (spec §4): a protocol in `interface/`,
   one adapter per vendor in `core/`.
7. A derived fact other modules also need is computed in ONE neutral canonical authority and
   imported, never recomputed here (§4). The site networks are tailnet-gateway's.
8. The scan touches devices the site owns: ADR-0007's Safety section binds every change.
   A new port, probe type, script or privilege changes what the operator confirms, so it
   changes the warning and needs an ADR amendment (the privileged profile gets its own ADR).
