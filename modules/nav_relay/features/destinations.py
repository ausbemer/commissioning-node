"""sender — decides which hosts get nav data (ADR-0010 D4, A2). The core thread does the sending.

Nothing is sent until an operator asks (rung 3): a `nav/destination` "send" names a host and a
port, and this feature adds it to the `DestinationTable` only if `destination_problem` passes
against the last completed site scan, or, for a tailnet address, the current peer table
(A2: fresh by its declared `period_s`, so a stale table admits no one). That check runs here
whatever the dashboard checked (rung 13). A "stop" removes one host, or every host.

Every request gets a `NavDestinationResult`, a refusal included (rung 7). The table is memory
only, so a restart sends to nobody until someone picks again (R1). The last scan is kept only
as long as the next one replaces it, and only to check addresses. It is never re-published.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from pydantic import ValidationError

from freshness import is_fresh
from interfaces import (NAV_DESTINATION, NAV_DESTINATION_RESULT, SITE_HOSTS, TAILNET_PEERS,
                        NavDestination, NavDestinationResult, SiteHosts, TailnetPeers,
                        __version__ as CONTRACT_VERSION)

from ..interface import MODULE, Destination, DestinationTable, destination_problem, tailnet_peer

log = logging.getLogger(MODULE)

SEND, STOP = "send", "stop"


class DestinationFeature:
    name = "sender"
    subscriptions = [NAV_DESTINATION, SITE_HOSTS, TAILNET_PEERS]

    def __init__(self, table: DestinationTable, now: Callable[[], float] = time.time,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._table, self._now, self._clock = table, now, clock
        self._hosts: SiteHosts | None = None
        self._peers: TailnetPeers | None = None
        self._peers_at: float | None = None

    def _fresh_peers(self) -> TailnetPeers | None:
        if self._peers is None or not is_fresh(self._peers_at, self._peers.period_s, self._clock()):
            return None
        return self._peers

    def handle(self, topic: str, payload: dict) -> list[tuple[str, dict]]:
        try:
            if topic == SITE_HOSTS:
                self._hosts = SiteHosts.model_validate(payload)
            elif topic == TAILNET_PEERS:
                self._peers, self._peers_at = TailnetPeers.model_validate(payload), self._clock()
            elif topic == NAV_DESTINATION:
                return [self._request(NavDestination.model_validate(payload))]
        except ValidationError as exc:
            log.warning("ignoring malformed %s: %s", topic, exc.errors(include_url=False))
        return []

    def _request(self, cmd: NavDestination) -> tuple[str, dict]:
        if cmd.operation == SEND:
            peers = self._fresh_peers()
            problem = destination_problem(cmd.address, cmd.port, self._hosts, peers)
            if problem:
                return self._result(cmd, False, f"not sending: {problem}")
            peer = tailnet_peer(cmd.address, peers)
            host = None if peer else next(h for h in self._hosts.hosts if h.address == cmd.address)
            refused = self._table.add(Destination(address=cmd.address, port=cmd.port,
                                                  mac=host.mac if host else None,
                                                  requested_by=cmd.requested_by, since=self._now()))
            if refused:
                return self._result(cmd, False, f"not sending: {refused}")
            via = f" (tailnet peer {peer})" if peer else ""
            log.info("sending nav data to %s:%d%s, asked by %s (%s)", cmd.address, cmd.port, via,
                     cmd.requested_by, cmd.door)
            return self._result(cmd, True, f"sending nav data to {cmd.address}:{cmd.port}{via}")
        if cmd.operation == STOP:
            n = self._table.remove(cmd.address)
            who = cmd.address or "every host"
            log.info("stopped nav data to %s (%d), asked by %s (%s)", who, n, cmd.requested_by, cmd.door)
            if n == 0:
                return self._result(cmd, True, f"was not sending to {who}")
            return self._result(cmd, True, f"stopped sending to {who}")
        return self._result(cmd, False, f"unknown operation {cmd.operation!r}")

    def _result(self, cmd: NavDestination, ok: bool, message: str) -> tuple[str, dict]:
        return (NAV_DESTINATION_RESULT, NavDestinationResult.stamped(
            CONTRACT_VERSION, request_id=cmd.request_id, ok=ok, message=message,
            requested_by=cmd.requested_by, door=cmd.door, operation=cmd.operation,
            finished_at=self._now()).model_dump())
