"""Shared message types — every type that crosses a module boundary (spec §3).

Rules for this file (see also CLAUDE.md):
- Contracts only grow: new fields are optional with a default; never rename, remove,
  retype, or re-mean an existing field (§3, §8.6).
- Nested shapes subclass `Shape`, never a bare dict (§3).
- Open string vocabularies (e.g. `StreamEndpoint.protocol`) are plain `str`, not enums:
  consumers MUST tolerate values they don't know, so a new value is additive.
- Secrets never appear here (§8.9). Camera credentials, tailnet auth keys and admin
  passwords do not travel on the bus.
"""
from __future__ import annotations

from .envelope import Envelope, Shape


class Tick(Envelope):
    """Orchestration heartbeat from the composition root (§8.8).

    The producer declares its own cadence (§11 rung 11): consumers derive any timeout from
    `period_s`, never from a constant of their own.
    """

    #: Wall-clock time the tick was emitted (Unix seconds).
    at: float
    #: The cadence the producer intends to tick at, in seconds.
    period_s: float


class ModuleInfo(Shape):
    """One module's identity inside a `NodeCard`."""

    #: Module name, kebab-case (e.g. "tailnet-gateway").
    name: str
    #: Module release version (`VERSION`, §4 dual versioning).
    release_version: str
    #: Module inward-contract version (`__version__`).
    inward_version: str
    #: Enabled capability flags, "module:feature" (§5).
    capabilities: list[str] = []


class NodeCard(Envelope):
    """Read-only self-description of this node (§5 card; §14 when fleets arrive).

    Reading a card never acts, and nothing in a card is an instruction.
    """

    #: Host name of the node publishing the card.
    hostname: str
    #: The `interfaces` contract version this node was built against.
    contract_version: str
    modules: list[ModuleInfo] = []


class CameraRegistration(Envelope):
    """Command: take this camera on. Produced by an operator surface."""

    #: Stable camera identity: the Axis serial number (the MAC without separators).
    camera_id: str
    #: Where the camera is reachable from this node: IP address or host name on the site LAN.
    address: str
    #: Optional human label, e.g. "Gate 3 PTZ".
    label: str | None = None


class CameraAnnouncement(Envelope):
    """A camera this node knows about. Carries no credentials (§8.9)."""

    camera_id: str
    address: str
    label: str | None = None
    #: Product name as the camera reports it, when probed.
    model: str | None = None
    #: Firmware version as the camera reports it, when probed.
    firmware: str | None = None
    #: Discriminator (§3): which producer made this announcement, "module/feature".
    origin: str


class StreamEndpoint(Shape):
    """One place a relayed stream can be pulled from. The URL never carries credentials."""

    #: Transport, e.g. "rtsp", "hls", "webrtc". Open vocabulary: tolerate unknown values.
    protocol: str
    url: str


class StreamAnnouncement(Envelope):
    """A relay path for one camera's stream (§6: announce over reliable, stream over fast).

    The bytes never touch the bus; this message only says where they are.
    """

    camera_id: str
    #: Relay path name on the media server (e.g. "cam-accc8e123456").
    path: str
    #: True only once the media server is actually serving this path.
    live: bool = False
    #: Pullable endpoints. Empty when no trusted (tailnet) address is known.
    endpoints: list[StreamEndpoint] = []
    #: Discriminator (§3): which producer made this announcement, "module/feature".
    origin: str


class TailnetStatus(Envelope):
    """This node's tailnet status, as last read from the overlay."""

    hostname: str
    #: True when the node is logged in and its tailnet link is up.
    connected: bool
    #: This node's tailnet addresses (IPv4 and IPv6).
    addresses: list[str] = []
    #: Subnet routes (CIDR) this node is currently serving on the tailnet.
    served_routes: list[str] = []
    #: ACL tags this node carries, e.g. "tag:commissioning". Empty when untagged or unknown.
    tags: list[str] = []
    #: The overlay's own state word, e.g. "Running", "NeedsLogin" (tailscaled's
    #: BackendState). Open vocabulary; empty when unknown.
    state: str = ""
    #: IPv4 networks (CIDR) the node is on right now, detected locally (ADR-0005 A3).
    site_networks: list[str] = []
    #: Routes the saved config asks for now, with "auto" expanded to `site_networks`.
    wanted_routes: list[str] = []
    #: Routes this node last advertised through its writer (ADR-0005 A3). Advertised is not
    #: served: the tailnet's admin approves a route; `served_routes` lists the approved ones.
    advertised_routes: list[str] = []
    #: This node's MagicDNS name, fully qualified and without the trailing dot, e.g.
    #: "cn-01.tail1234.ts.net" (0.7.0, ADR-0008). Empty when the overlay reports none.
    dns_name: str = ""


class TailnetPeer(Shape):
    """One other node on this node's tailnet, as the overlay last reported it."""

    #: The peer's tailnet addresses (IPv4 and IPv6).
    addresses: list[str] = []
    #: The peer's name on the tailnet: its MagicDNS name, e.g. "alice-laptop.tail1234.ts.net",
    #: or its OS host name when the overlay reports no DNS name.
    node_name: str
    #: Login of the user who owns the peer, e.g. "alice@example.com". None for a tagged
    #: peer: a tagged node is owned by its tags, not by a user.
    login: str | None = None
    #: ACL tags the peer carries, e.g. "tag:ops". Empty for a user-owned peer.
    tags: list[str] = []
    #: The address (no port) this node reaches the peer at directly, e.g. a site-LAN address,
    #: as the overlay last reported it (ADR-0007). None when the peer is relayed or unknown.
    endpoint: str | None = None


class TailnetPeers(Envelope):
    """The complete set of peers this node can see on its tailnet (ADR-0003 §2).

    A snapshot, not a delta: a peer absent from the latest message is not on the tailnet.
    Consumers use it to resolve a tailnet address to an identity, and must treat it as a
    renewed level (§5): once older than their bound, derived from `period_s` (§11 rung 11),
    it no longer resolves anyone.
    """

    #: Wall-clock time the overlay was read (Unix seconds).
    observed_at: float
    #: The cadence the producer intends to publish at, in seconds.
    period_s: float
    peers: list[TailnetPeer] = []



class TailnetApply(Envelope):
    """Command: apply the saved Tailscale config to this node (ADR-0005). Produced by the
    operator surface through `invoke()`; consumed by tailnet-gateway's writer.

    Carries no values: the writer reads the hostname, tags and auth key from its own config
    (ADR-0004), so nothing here can bypass validation, and no secret is ever on the bus.
    """

    #: Correlates the result with this request.
    request_id: str
    #: "join", "rename", "routes", "routes-off" or "leave". Open vocabulary: a writer refuses
    #: one it doesn't know.
    operation: str
    #: Who asked, as the operator surface recorded them (e.g. "alice@example.com").
    requested_by: str
    #: Which door they came through: "local", "tailnet" or "fallback" (ADR-0003).
    door: str


class TailnetApplyResult(Envelope):
    """The outcome of one `TailnetApply`. Never contains a secret (§8.9)."""

    request_id: str
    operation: str
    ok: bool
    #: For people: what happened, or why not (§11 rung 7). Scrubbed of auth keys.
    message: str
    requested_by: str = ""
    door: str = ""
    #: Wall-clock time the operation finished (Unix seconds).
    finished_at: float


class ConfigField(Shape):
    """One operator-settable config field, as its owning module declares it (ADR-0004).

    A declaration, not a value: config is not a message (§6), and no value ever rides in
    this shape. It crosses a module boundary because the owner declares it and the operator
    surface renders it. The action manifest (§5) and Stage 1 graduation reuse it as-is.
    """

    #: Owning module, kebab-case (e.g. "tailnet-gateway"). The field's key is `module.name`.
    module: str
    #: Field name, snake_case, unique within the module (e.g. "auth_key").
    name: str
    #: Value kind: "str", "int", "bool" or "str_list". Open vocabulary: a surface that
    #: doesn't know a kind shows the field read-only rather than guessing.
    kind: str
    #: §8.9: flagged here and nowhere else. A secret is write-only and reads as set/unset.
    secret: bool = False
    #: "site" (wiped when the node leaves a site) or "node" (survives the move). Defaults
    #: to "site": carrying one site's data to the next is the dangerous failure (ADR-0004 D2).
    scope: str = "site"
    #: Short human label, e.g. "Tailscale hostname".
    label: str = ""
    #: One-line help for the operator.
    help: str = ""
    #: Value when unset. Always None for a secret.
    default: str | int | bool | list[str] | None = None
    #: "str" / each "str_list" item: maximum length in characters.
    max_len: int | None = None
    #: "str" / each "str_list" item: a regular expression the whole value must match.
    pattern: str | None = None
    #: "str_list": maximum number of items.
    max_items: int | None = None
    #: "int": inclusive bounds.
    minimum: int | None = None
    maximum: int | None = None
    #: §11 rung-4 warning if *editing* the field disturbs something; else None.
    may_disturb: str | None = None


class SiteScan(Envelope):
    """Command: scan the site networks once (ADR-0007). Produced by the operator surface
    through `invoke()`; consumed by site-survey.

    Carries no targets and no options: the scanner scans the networks the node is on now,
    so nothing here can point it anywhere else.
    """
    #: Correlates the result with this request.
    request_id: str
    #: Who asked, as the operator surface recorded them.
    requested_by: str
    #: Which door they came through: "local", "tailnet" or "fallback" (ADR-0003).
    door: str


class SiteHost(Shape):
    """One address on a site network that answered a scan. Site data: never persisted."""
    address: str
    #: Hardware address, lower-case "aa:bb:cc:dd:ee:ff", when the kernel resolved one.
    mac: str | None = None
    #: Maker from the MAC prefix, "private MAC" for a locally administered one, else None.
    maker: str | None = None
    #: Name from reverse DNS, when the site resolver has one.
    hostname: str | None = None
    #: Ports from the scanner's fixed list that accepted a connection.
    open_ports: list[int] = []
    #: True when the MAC prefix belongs to Axis. A hint, not an identification.
    axis_maker: bool = False
    #: How the host was found: "tcp" (answered a probe) or "arp" (only in the neighbour table,
    #: quirks Q-006). Open vocabulary.
    found_by: str = "tcp"


class SiteHosts(Envelope):
    """Everything one completed scan found (ADR-0007). A snapshot, not a delta, and not
    liveness: a host listed here answered at `scanned_at`, nothing more.
    """
    #: Wall-clock time the scan finished (Unix seconds).
    scanned_at: float
    #: The networks (CIDR) scanned.
    networks: list[str] = []
    hosts: list[SiteHost] = []
    #: This node's own addresses on the scanned networks. Never scanned, never in `hosts`.
    self_addresses: list[str] = []
    #: The request that ran this scan, and who asked.
    request_id: str = ""
    requested_by: str = ""
    #: Discriminator (§3): which producer made this snapshot, "module/feature".
    origin: str


class SiteScanResult(Envelope):
    """The outcome of one `SiteScan`. A failed scan leaves the last `SiteHosts` standing."""
    request_id: str
    ok: bool
    #: For people: what happened, or why not (§11 rung 7).
    message: str
    requested_by: str = ""
    door: str = ""
    #: Wall-clock time the scan finished or was refused (Unix seconds).
    finished_at: float
    #: Hosts found; 0 when not ok.
    host_count: int = 0


class CameraIdentification(Envelope):
    """What a device at a site address said it is, over the vendor's unauthenticated
    identification read (ADR-0007). Not a registration: nothing is adopted by it.
    """
    address: str
    serial: str | None = None
    model: str | None = None
    firmware: str | None = None
    #: Wall-clock time of the read (Unix seconds).
    observed_at: float
    #: Discriminator (§3): which producer made this identification, "module/feature".
    origin: str


class NodeVersion(Shape):
    """One version of this node's code: what it runs, or what its update channel offers
    (ADR-0009)."""
    #: The full git commit (40 hex digits).
    commit: str
    #: Where it comes from: a release tag ("v0.3.0"), "dev", or "local" (a developer tree).
    #: Open vocabulary.
    ref: str = ""
    #: `git describe` of the commit, e.g. "v0.2.0-7-gee0e13d"; "" when unknown.
    describe: str = ""
    #: The commit's GitHub CI: "success", "failure", "pending", or "" unknown. Open vocabulary.
    ci: str = ""


class NodeUpdateStatus(Envelope):
    """What this node runs and what its update channel offers (ADR-0009 D2). Published every
    tick, a renewed level: once older than its bound, derived from `period_s`, it says nothing.
    Reading it never acts: an update is a separate, confirmed command.
    """
    #: The channel checked: "releases" or "dev". Open vocabulary.
    channel: str
    #: What runs, from the root-written record of the last install; None when there is none.
    running: NodeVersion | None = None
    #: "release" or "dev-tree" (ADR-0009 D1); "" unknown. Open vocabulary.
    running_mode: str = ""
    #: Whether the installed tree had no local changes; None when unknown.
    running_clean: bool | None = None
    #: The channel's newest commit whose CI passed: the only one ever offered. None when
    #: there is none, or the last check failed.
    available: NodeVersion | None = None
    #: The channel's newest commit when that is not `available` (its CI hasn't passed).
    newest: NodeVersion | None = None
    #: `available` relative to `running`: "ahead" (newer), "behind", "identical", "diverged",
    #: or "unknown" (e.g. GitHub doesn't have the running commit). Open vocabulary.
    relation: str = ""
    #: True only when `available` is newer than what runs (`relation` "ahead").
    update_available: bool = False
    #: Whether the last check worked. A failed check offers nothing (fails toward no offer).
    ok: bool = False
    #: For people: what the last check found, or why it failed (§11 rung 7).
    message: str = ""
    #: Wall-clock time the last check finished (Unix seconds); None before the first.
    checked_at: float | None = None
    #: True while a check runs.
    checking: bool = False
    #: The cadence of scheduled checks, seconds.
    check_period_s: float
    #: The cadence this status is published at, seconds (§11 rung 11).
    period_s: float
    #: Discriminator (§3): which producer made this status, "module/feature".
    origin: str


class NodeUpdateCheck(Envelope):
    """Command: check the update channel now (ADR-0009 D2). Produced by the operator surface
    through `invoke()`; consumed by node-updater. Carries no values: the channel is the
    node's own config."""
    #: Correlates this request in the logs.
    request_id: str
    #: Who asked, as the operator surface recorded them.
    requested_by: str
    #: Which door they came through: "local", "tailnet" or "fallback" (ADR-0003).
    door: str


class NodeUpdate(Envelope):
    """Command: update this node to the commit the operator was shown (ADR-0009 D3). Produced
    by the operator surface through `invoke()`; consumed by node-updater, which hands it to the
    root updater as a request with no code in it.

    Carries one value, `expected_commit`: root installs the channel's target only if it is
    still exactly this commit. No ref, URL, path or argument.
    """
    #: Correlates the result with this request.
    request_id: str
    #: The commit (40 hex digits) the operator confirmed.
    expected_commit: str
    #: Who asked, as the operator surface recorded them.
    requested_by: str
    #: Which door they came through: "local", "tailnet" or "fallback" (ADR-0003).
    door: str


class NodeUpdateResult(Envelope):
    """Where one `NodeUpdate` is, or how it ended (ADR-0009 D3, D4). Published when it changes;
    the root updater's own record survives the daemon's restart in the middle of an update.
    """
    request_id: str
    #: False while the update runs; True once it ended (done, refused, failed or rolled back).
    done: bool
    #: Whether it ended with the node updated and the doctor clean. False while running.
    ok: bool = False
    #: Where a running update is: "requested", "fetching", "verifying", "installing",
    #: "checking", "rolling-back"; "" once done. Open vocabulary.
    phase: str = ""
    #: For people: what happened, or why not (§11 rung 7).
    message: str = ""
    #: What ran before, and what the update installs.
    from_version: NodeVersion | None = None
    to_version: NodeVersion | None = None
    #: True when a failed update put the previous version back (D4).
    rolled_back: bool = False
    requested_by: str = ""
    door: str = ""
    #: Wall-clock times (Unix seconds); None when not yet.
    started_at: float | None = None
    finished_at: float | None = None
