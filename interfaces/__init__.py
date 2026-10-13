"""commissioning-node shared contract (future `commissioning-node-interfaces` repo).

Pure data shapes and constants — no logic, imports nothing internal (spec §3, §4).
`__version__` is the contract semver and is stamped on the wire as `schema_version`.
"""
from .envelope import Envelope, Shape
from .models import (
    CameraAnnouncement,
    CameraRegistration,
    ConfigField,
    ModuleInfo,
    NodeCard,
    StreamAnnouncement,
    StreamEndpoint,
    TailnetApply,
    TailnetApplyResult,
    TailnetPeer,
    TailnetPeers,
    TailnetStatus,
    Tick,
)
from .topics import (
    CAMERA_ANNOUNCED,
    CAMERA_REGISTER,
    STREAM_ANNOUNCED,
    SYSTEM_CARD,
    SYSTEM_TICK,
    TAILNET_APPLY,
    TAILNET_APPLY_RESULT,
    TAILNET_PEERS,
    TAILNET_STATUS,
)

__version__ = "0.5.0"

__all__ = [
    "__version__",
    "Envelope",
    "Shape",
    "Tick",
    "ModuleInfo",
    "NodeCard",
    "CameraRegistration",
    "CameraAnnouncement",
    "StreamEndpoint",
    "StreamAnnouncement",
    "TailnetStatus",
    "TailnetPeer",
    "TailnetPeers",
    "ConfigField",
    "TailnetApply",
    "TailnetApplyResult",
    "SYSTEM_TICK",
    "SYSTEM_CARD",
    "CAMERA_REGISTER",
    "CAMERA_ANNOUNCED",
    "STREAM_ANNOUNCED",
    "TAILNET_STATUS",
    "TAILNET_PEERS",
    "TAILNET_APPLY",
    "TAILNET_APPLY_RESULT",
]
