"""camera-registry — turns an operator's camera registration into an announcement.

Read-only toward the world (§11 rung 1): it touches no camera. A later VAPIX probe feature
fills `model` / `firmware`; any VAPIX *write* is a separate feature with its own Safety ADR
(rung 3: the first write to a site-owned camera is operator-confirmed).
"""
from __future__ import annotations

import logging

from pydantic import ValidationError

from interfaces import (
    CAMERA_ANNOUNCED,
    CAMERA_REGISTER,
    CameraAnnouncement,
    CameraRegistration,
    __version__ as CONTRACT_VERSION,
)

from ..interface import MODULE

log = logging.getLogger(MODULE)


class CameraRegistry:
    name = "camera-registry"
    subscriptions = [CAMERA_REGISTER]

    def handle(self, topic: str, payload: dict) -> list[tuple[str, dict]]:
        try:
            reg = CameraRegistration.model_validate(payload)
        except ValidationError as exc:
            log.warning("ignoring malformed %s: %s", topic, exc.errors(include_url=False))
            return []
        announcement = CameraAnnouncement.stamped(
            CONTRACT_VERSION,
            camera_id=reg.camera_id,
            address=reg.address,
            label=reg.label,
            origin=f"{MODULE}/{self.name}",
        )
        return [(CAMERA_ANNOUNCED, announcement.model_dump())]
