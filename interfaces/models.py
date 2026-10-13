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
    #: "join", "rename" or "leave". Open vocabulary: a writer refuses one it doesn't know.
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
