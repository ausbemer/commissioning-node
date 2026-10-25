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
#: Every peer this node can see on its tailnet, as one snapshot. Payload: `TailnetPeers`.
TAILNET_PEERS = "tailnet/peers"
#: Command: operator asks tailnet-gateway to join, rename, set or stop routes, or leave (ADR-0005).
#: Payload: `TailnetApply`. Carries no values and no secret.
TAILNET_APPLY = "tailnet/apply"
#: The outcome of one `tailnet/apply`. Payload: `TailnetApplyResult`.
TAILNET_APPLY_RESULT = "tailnet/apply-result"

#: Command: operator asks site-survey to scan the site networks once (ADR-0007).
#: Payload: `SiteScan`. Carries no targets.
SITE_SCAN = "site/scan"
#: Everything the last completed scan found. Payload: `SiteHosts`.
SITE_HOSTS = "site/hosts"
#: The outcome of one `site/scan`. Payload: `SiteScanResult`.
SITE_SCAN_RESULT = "site/scan-result"
#: What a device on the site said it is (an Axis camera over VAPIX). Payload: `CameraIdentification`.
CAMERA_IDENTIFIED = "camera/identified"

#: What this node runs and what its update channel offers (ADR-0009). Payload: `NodeUpdateStatus`.
NODE_UPDATE_STATUS = "node/update-status"
#: Command: operator asks node-updater to check its channel now (ADR-0009). Payload:
#: `NodeUpdateCheck`. Carries no values.
NODE_UPDATE_CHECK = "node/update-check"
