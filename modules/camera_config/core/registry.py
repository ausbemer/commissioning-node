"""Feature registration + routing (spec §4). I/O plumbing only — no business logic."""
from __future__ import annotations

import fnmatch
import logging

from ..interface import MODULE, VERSION, Feature

log = logging.getLogger(MODULE)


class Registry:
    """Holds this module's features; routes messages to the enabled ones.

    Merge early, register disabled (§8.4): `register(feature, enabled=False)` ships a
    feature's code without routing anything to it or advertising its capability.
    """

    module = MODULE
    version = VERSION

    def __init__(self) -> None:
        self._features: dict[str, Feature] = {}
        self._enabled: set[str] = set()

    def register(self, feature: Feature, enabled: bool = True) -> None:
        if not isinstance(feature, Feature):
            raise TypeError(f"{feature!r} does not implement the Feature protocol")
        if feature.name in self._features:
            raise ValueError(f"duplicate feature name {feature.name!r} in {MODULE}")
        self._features[feature.name] = feature
        if enabled:
            self._enabled.add(feature.name)
        log.info("registered %s:%s (%s)", MODULE, feature.name, "enabled" if enabled else "disabled")

    def enabled(self) -> list[Feature]:
        return [f for name, f in self._features.items() if name in self._enabled]

    def registered(self) -> list[str]:
        """Capability flags of every feature, enabled or not: a registered-disabled one ships
        its code but advertises nothing (§8.4)."""
        return sorted(f"{MODULE}:{name}" for name in self._features)

    def capabilities(self) -> list[str]:
        """Capability flags of enabled features, "module:feature" (§5)."""
        return sorted(f"{MODULE}:{f.name}" for f in self.enabled())

    def route(self, topic: str, payload: dict, pattern: str | None = None) -> list[tuple[str, dict]]:
        """Hand one message to every enabled feature that subscribed to it.

        With `pattern`, only features that subscribed with exactly that pattern get it — the
        bus already matched the topic, and this keeps a topic that matches two patterns from
        reaching a feature twice.
        """
        out: list[tuple[str, dict]] = []
        for feature in self.enabled():
            wanted = (pattern in feature.subscriptions) if pattern is not None else any(
                fnmatch.fnmatch(topic, p) for p in feature.subscriptions)
            if wanted:
                out.extend(feature.handle(topic, payload))
        return out
