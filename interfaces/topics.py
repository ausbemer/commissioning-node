"""Topic name constants — part of the outward contract (spec §3).

Topic *strings* are slash-separated and kebab-case; the Python identifiers that hold them
are snake/upper case. Broker choice is undecided (ADR-0001), so these stay generic; a
broker adapter maps them to its own subject syntax at Stage 1 / when that lever is pulled.

Adding a topic is MINOR. Renaming or removing one is MAJOR (spec §3).
"""

#: Orchestration tick from the composition root (§8.8). Payload: `Tick`.
SYSTEM_TICK = "system/tick"
#: This node's read-only self-description card (§5). Payload: `NodeCard`.
SYSTEM_CARD = "system/card"

#: Command: operator asks camera-config to take on a camera. Payload: `CameraRegistration`.
CAMERA_REGISTER = "camera/register"
#: A camera this node knows about. Payload: `CameraAnnouncement`.
CAMERA_ANNOUNCED = "camera/announced"

#: A relay path for one camera's stream, and where to pull it. Payload: `StreamAnnouncement`.
STREAM_ANNOUNCED = "stream/announced"

#: This node's tailnet status. Payload: `TailnetStatus`.
TAILNET_STATUS = "tailnet/status"
