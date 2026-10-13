# ADR 0001: Adopt the Additive-Contract meta-architecture (Stage 0)

- **Status:** Accepted
- **Date:** 2026-10-12
- **Deciders:** Austin Emerson
- **Shape:** standard (spec §7)
- **Spec version adopted:** 0.4.0 — copied from the version line at the top of
  `META-ARCHITECTURE.md`, which stays in the vendored copy (spec §9 step 0, §12)

## Context

commissioning-node runs on a Revolution Pi. It is a Tailscale network gateway that is placed
on a site network for the length of a commissioning and testing timeline, and then removed and
reused on another site. While it is there it gives remote access over the tailnet, configures
the site's Axis cameras through VAPIX, and relays their streams through a MediaMTX server.
Tailscale configuration must be adjustable but automatic where possible, because every
deployment is a new network. There is one owner on day one, and no §10 trigger holds, so the
coordination cost of a polyrepo buys nothing yet (spec §10, Appendix "Why monolith-first?").

## Decision

Adopt the meta-architecture in `META-ARCHITECTURE.md` (spec v0.4.0) at **Stage 0**, a modular
monolith with four modules: `tailnet-gateway`, `camera-config`, `stream-relay` and
`dashboard`. Python 3.13 / Pydantic v2. Live from day one:

- Cross-module types only in `interfaces/` — additive-only, `extra="ignore"`, nested shapes
  typed as models (§3), machine-checked by `tools/check_additivity.py` (§8.1).
- The §4 ontology, machine-checked by `tools/check_boundaries.py` (§8.1): modules meet only
  on the bus; neutral packages stay pure; the contract imports nothing internal.
- `schema_version` on every envelope (§8.5); semantics never mutate (§8.6).
- Capability discovery as an in-process registry lookup (§5): `main.Node.capabilities()`
  aggregates every enabled `module:feature`; `main.invoke()` refuses a command in-band when
  the capability is absent or nothing is subscribed.
- Dual versioning per module: `VERSION` (release) + `__version__` (inward contract) (§4).
- A read-only node card (`NodeCard` on `system/card`, §5) from the first commit, so the
  fleet case (§14) has its card already.

The broker is undecided. Topics are generic slash strings, and the broker adapter maps them
when that lever is pulled.

Deliberately deferred until a §10 trigger fires: submodules, the superproject, per-service
tagging, `promote.sh`, the deploy repo, a real broker, and a second transport. Each graduation
lever may be pulled alone, behind one seam, with its own ADR (§10). The stream bytes already
travel outside the bus: MediaMTX is the provisioned data plane (§6). The bus carries only
`StreamAnnouncement`s.

## Security posture (spec §9 step 0.5)

- **The tailnet is the trust boundary.** Operator access arrives over Tailscale, and identity
  is read from the transport (§15). One function will resolve a tailnet peer to a login, and
  the ownership door (§11 rung 10) consumes it. The application does not re-authenticate
  tailnet users.
- **Local fallback with admin credentials.** A second, deliberate door exists for when the
  tailnet is unavailable. This happens on first boot at a new site, before the node has joined
  the tailnet, and when the tailnet is down. It is a local-network surface gated by admin
  credentials. It is a named exception, not a smuggled one. Its mechanism (where it listens,
  how credentials are stored and rotated, and how it is disabled once the tailnet is up, if it
  is) gets its own ADR before the first non-loopback listener exists.
- **The site LAN is untrusted.** The node reaches out to devices on it; it does not serve
  them. Stream endpoints are advertised on tailnet addresses only (`stream-relay`).
- **Until both doors exist, every surface is loopback-only and read-only.** This is enforced
  in `modules/dashboard/core/http_surface.py`: it refuses non-loopback binds, and returns
  405 for non-GET methods.
- **Secrets follow §8.9 from the first credential:** Tailscale auth keys, camera
  credentials, the admin credential, and anything MediaMTX authenticates with. They are
  flagged at declaration, masked, and never sent on the bus or written to logs. None exist
  yet. The contract models carry no credential fields by design.

## Risk profile

- **Acts on the world? Yes.** It writes to:
  - Host Tailscale state: up/down, advertised subnet routes, exit-node and auth settings.
  - Axis cameras owned by the site, via VAPIX. These are **unowned devices**, so §11 rung 3
    applies: the first write is operator-confirmed.
  - MediaMTX configuration: paths and sources.

  Every module starts read-only (§11 rung 1). Each actuating feature arrives with an ADR that
  fills the Safety section. Single writers (rung 9) are fixed now:
  - `tailnet-gateway` is the only writer of Tailscale state.
  - `camera-config` is the only writer to cameras.
  - `stream-relay` is the only writer of MediaMTX config.

  Evidence scaffolding exists: `tools/ledgers/` and `tests/replay/` (§8.11, §8.12).
- **Foreign runtimes / pinned vendor dependencies:** tailscaled, MediaMTX, and Axis VAPIX.
  None is installed or pinned yet. Each is contained behind one bridge module (§6), and the
  quirk registry is at `docs/quirks.md` (§7).
- **Anticipated later:** many commissioning nodes on one tailnet at the same time (§14,
  fleets of homogeneous peers). The `NodeCard` is the seed. Membership, peer polling and
  shared deterministic rules wait for their own design ADR.
- **Relocation:** the node moves between sites. What site state exists, and how it is wiped
  or preserved between deployments, is an open item in `TODO.md` (spec §10 "disposable,
  never lossy").

## Consequences

Extraction to Stage 1 stays mechanical repo surgery, not a redesign (§10). The contract
checker, boundary checker and CI gate are obligations from the first commit. Revisit this
ADR when a §10 graduation trigger fires, or when adopting a newer spec version (record the
new version here as an amendment).

## Amendments

- **2026-10-12 — the fallback door's mechanism is decided (ADR-0003).** The local fallback is
  an HTTPS listener on the site LAN (:8443) gated by an scrypt-hashed admin password. Tailnet
  identity is resolved from the peer table that `tailnet-gateway` publishes, and any tailnet
  user may edit config. "Every surface is loopback-only and read-only" still holds until
  ADR-0003's replacements ship together (its rung-12 note); then it is lifted, not before.
- **2026-10-13 — the posture is lifted (ADR-0003 step 4).** The replacements shipped in one
  change: door classification, peer resolution, admin sessions, the 8080 firewall entry and
  the single `authorize()`. "Loopback-only and read-only" no longer holds; ADR-0003 §1 is
  the posture now.
