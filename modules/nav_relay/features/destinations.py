"""sender — decides which site hosts get nav data (ADR-0010 D4). The core thread does the sending.

Nothing is sent until an operator asks (rung 3): a `nav/destination` "send" names a host and a
port, and this feature adds it to the `DestinationTable` only if `destination_problem` passes
against the last completed site scan. That check runs here whatever the dashboard checked
(rung 13). A "stop" removes one host, or every host.

Every request gets a `NavDestinationResult`, a refusal included (rung 7). The table is memory
only, so a restart sends to nobody until someone picks again (R1). The last scan is kept only
as long as the next one replaces it, and only to check addresses. It is never re-published.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from pydantic import ValidationError

from interfaces import (NAV_DESTINATION, NAV_DESTINATION_RESULT, SITE_HOSTS, NavDestination,
                        NavDestinationResult, SiteHosts, __version__ as CONTRACT_VERSION)

from ..interface import MODULE, Destination, DestinationTable, destination_problem

log = logging.getLogger(MODULE)

SEND, STOP = "send", "stop"


class DestinationFeature:
    name = "sender"
    subscriptions = [NAV_DESTINATION, SITE_HOSTS]

    def __init__(self, table: DestinationTable, now: Callable[[], float] = time.time) -> None:
        self._table, self._now = table, now
        self._hosts: SiteHosts | None = None

    def handle(self, topic: str, payload: dict) -> list[tuple[str, dict]]:
        try:
            if topic == SITE_HOSTS:
                self._hosts = SiteHosts.model_validate(payload)
            elif topic == NAV_DESTINATION:
                return [self._request(NavDestination.model_validate(payload))]
        except ValidationError as exc:
            log.warning("ignoring malformed %s: %s", topic, exc.errors(include_url=False))
        return []

    def _request(self, cmd: NavDestination) -> tuple[str, dict]:
        if cmd.operation == SEND:
            problem = destination_problem(cmd.address, cmd.port, self._hosts)
            if problem:
                return self._result(cmd, False, f"not sending: {problem}")
            host = next(h for h in self._hosts.hosts if h.address == cmd.address)
            refused = self._table.add(Destination(address=cmd.address, port=cmd.port, mac=host.mac,
                                                  requested_by=cmd.requested_by, since=self._now()))
            if refused:
                return self._result(cmd, False, f"not sending: {refused}")
            log.info("sending nav data to %s:%d, asked by %s (%s)", cmd.address, cmd.port,
                     cmd.requested_by, cmd.door)
            return self._result(cmd, True, f"sending nav data to {cmd.address}:{cmd.port}")
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
