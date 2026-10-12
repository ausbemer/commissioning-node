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
