"""relay-planner — plans one MediaMTX relay path per announced camera and announces it.

Read-only toward the world (§11 rung 1): nothing is written to MediaMTX yet, so every
announcement says `live=False`. Writing the path into MediaMTX is a later feature with its
own Safety ADR, and this module stays the only writer of MediaMTX config (rung 9).

Endpoint enrichment from the tailnet feed declares the direction it fails (§5):
  safe default = advertise NO endpoints. Only a fresh, connected TailnetStatus produces an
  endpoint, and only on a tailnet address — never a site-LAN address, because the tailnet is
  the trust boundary (ADR-0001). Freshness is derived from the producer's declared tick
  cadence: stale after clamp(3 × period_s, STALE_MIN_S, STALE_MAX_S) without a status.

State is bounded latest-value-per-camera (§8.8).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Callable

from pydantic import ValidationError

from interfaces import (
    CAMERA_ANNOUNCED,
    STREAM_ANNOUNCED,
    SYSTEM_TICK,
    TAILNET_STATUS,
    CameraAnnouncement,
    StreamAnnouncement,
    StreamEndpoint,
    TailnetStatus,
    Tick,
    __version__ as CONTRACT_VERSION,
)

from ..interface import MODULE

log = logging.getLogger(MODULE)

#: MediaMTX's default RTSP listener port. Must match the ops-provisioned mediamtx.yml
#: (vendor default, not a tuned constant — see docs/quirks.md).
MEDIAMTX_RTSP_PORT = 8554
#: Bounds on the derived staleness window, seconds. PROVISIONAL — not yet measured (TODO.md, §8.11).
STALE_MIN_S = 5.0
STALE_MAX_S = 120.0
#: Bound on remembered cameras (§8.8). PROVISIONAL — a commissioning site's camera count, unmeasured.
MAX_CAMERAS = 512


def path_for(camera_id: str) -> str:
    """Relay path name for a camera: "cam-" + the id, lower-case, [a-z0-9-] only."""
    return "cam-" + re.sub(r"[^a-z0-9]+", "-", camera_id.lower()).strip("-")


class RelayPlanner:
    name = "relay-planner"
    subscriptions = [CAMERA_ANNOUNCED, TAILNET_STATUS, SYSTEM_TICK]

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._cameras: dict[str, CameraAnnouncement] = {}
        self._status: TailnetStatus | None = None
        self._status_at: float | None = None
        self._stale_after_s: float | None = None    # unknown until the first tick declares a cadence
        self._advertised_host: str | None = None

    def handle(self, topic: str, payload: dict) -> list[tuple[str, dict]]:
        try:
            if topic == CAMERA_ANNOUNCED:
                camera = CameraAnnouncement.model_validate(payload)
                if camera.camera_id not in self._cameras and len(self._cameras) >= MAX_CAMERAS:
                    log.warning("camera table full (%d); not planning %s", MAX_CAMERAS, camera.camera_id)
                    return []
                self._cameras[camera.camera_id] = camera
                return [self._announce(camera)]
            if topic == TAILNET_STATUS:
                self._status = TailnetStatus.model_validate(payload)
                self._status_at = self._clock()
            elif topic == SYSTEM_TICK:
                period = Tick.model_validate(payload).period_s
                self._stale_after_s = min(max(3 * period, STALE_MIN_S), STALE_MAX_S)
        except ValidationError as exc:
            log.warning("ignoring malformed %s: %s", topic, exc.errors(include_url=False))
            return []
        # Status or freshness may have moved the advertisable host: re-announce if it did.
        if self._host() != self._advertised_host:
            return [self._announce(c) for c in self._cameras.values()]
        return []

    def _host(self) -> str | None:
        """The tailnet IPv4 address to advertise, or None (the safe default)."""
        s, at, window = self._status, self._status_at, self._stale_after_s
        if s is None or at is None or window is None or not s.connected:
            return None
        if self._clock() - at > window:
            return None
        return next((a for a in s.addresses if ":" not in a), None)

    def _announce(self, camera: CameraAnnouncement) -> tuple[str, dict]:
        host = self._host()
        self._advertised_host = host
        path = path_for(camera.camera_id)
        endpoints = [StreamEndpoint(protocol="rtsp", url=f"rtsp://{host}:{MEDIAMTX_RTSP_PORT}/{path}")] if host else []
        announcement = StreamAnnouncement.stamped(
            CONTRACT_VERSION,
            camera_id=camera.camera_id,
            path=path,
            live=False,
            endpoints=endpoints,
            origin=f"{MODULE}/{self.name}",
        )
        return (STREAM_ANNOUNCED, announcement.model_dump())
