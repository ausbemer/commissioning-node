"""Publications — everything this module puts on the bus (spec §4). I/O only.

Payloads must already be contract envelopes serialized to dicts (they carry
`schema_version`, §8.5). One without it is a publisher bug: refused here, loudly.
"""
from __future__ import annotations

import logging
from typing import Callable

from ..interface import MODULE

log = logging.getLogger(MODULE)

Publish = Callable[[str, dict], int]


class Output:
    def __init__(self, publish: Publish) -> None:
        self._publish = publish

    def publish_all(self, publications: list[tuple[str, dict]]) -> None:
        for topic, payload in publications:
            if "schema_version" not in payload:
                log.error("refusing to publish on %s: payload has no schema_version (§8.5)", topic)
                continue
            self._publish(topic, payload)
