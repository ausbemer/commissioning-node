"""peer-report — publishes every peer this node sees on its tailnet, as one snapshot.

Read-only (§11 rung 1). The dashboard resolves tailnet addresses to identities from it
(ADR-0003 §2). Published on every reading, connected or not: a disconnected node publishes
an empty snapshot, so a consumer's view of the tailnet is renewed every tick and fails
toward "nobody resolves" rather than keeping a stale peer table alive.
"""
from __future__ import annotations

from interfaces import TAILNET_PEERS, TailnetPeer, TailnetPeers, __version__ as CONTRACT_VERSION

from ..interface import OVERLAY_SNAPSHOT, OverlaySnapshot


class PeerReport:
    name = "peer-report"
    subscriptions = [OVERLAY_SNAPSHOT]

    def handle(self, topic: str, payload: dict) -> list[tuple[str, dict]]:
        snap = OverlaySnapshot.model_validate(payload)
        if snap.observed_at is None or snap.period_s is None:
            return []   # unstamped: a consumer could not judge its freshness, so don't send it
        peers = TailnetPeers.stamped(
            CONTRACT_VERSION,
            observed_at=snap.observed_at,
            period_s=snap.period_s,
            peers=[TailnetPeer(addresses=p.addresses, node_name=p.node_name, login=p.login,
                               tags=p.tags)
                   for p in snap.peers],
        )
        return [(TAILNET_PEERS, peers.model_dump())]
