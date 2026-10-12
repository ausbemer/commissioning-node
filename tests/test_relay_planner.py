"""relay-planner's enrichment fails toward caution (§5): no fresh tailnet status, no endpoints."""
from __future__ import annotations

import interfaces
from modules.stream_relay.features.relay_planner import RelayPlanner, path_for

V = interfaces.__version__


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


def tick(period_s: float) -> dict:
    return interfaces.Tick.stamped(V, at=0.0, period_s=period_s).model_dump()


def status(connected: bool = True) -> dict:
    return interfaces.TailnetStatus.stamped(V, hostname="cn", connected=connected,
                                            addresses=["fd7a::1", "100.64.0.9"]).model_dump()


def camera() -> dict:
    return interfaces.CameraAnnouncement.stamped(V, camera_id="ACCC8E-12:34", address="10.0.0.2",
                                                 origin="test").model_dump()


def endpoints(out: list[tuple[str, dict]]) -> list[str]:
    assert out and out[-1][0] == interfaces.STREAM_ANNOUNCED
    return [e["url"] for e in out[-1][1]["endpoints"]]


def test_path_names_are_sanitized() -> None:
    assert path_for("ACCC8E-12:34") == "cam-accc8e-12-34"


def test_no_tick_yet_means_unknown_cadence_means_no_endpoints() -> None:
    planner = RelayPlanner(clock=Clock())
    planner.handle(interfaces.TAILNET_STATUS, status())
    assert endpoints(planner.handle(interfaces.CAMERA_ANNOUNCED, camera())) == []


def test_fresh_status_advertises_tailnet_ipv4_then_stale_withdraws() -> None:
    clock = Clock()
    planner = RelayPlanner(clock=clock)
    planner.handle(interfaces.SYSTEM_TICK, tick(5.0))                 # window = 15 s
    planner.handle(interfaces.CAMERA_ANNOUNCED, camera())
    out = planner.handle(interfaces.TAILNET_STATUS, status())        # host appears -> re-announce
    assert endpoints(out) == ["rtsp://100.64.0.9:8554/cam-accc8e-12-34"]

    clock.now = 14.0
    assert planner.handle(interfaces.SYSTEM_TICK, tick(5.0)) == []    # still fresh, nothing changed
    clock.now = 16.0
    assert endpoints(planner.handle(interfaces.SYSTEM_TICK, tick(5.0))) == []


def test_disconnected_status_advertises_nothing() -> None:
    planner = RelayPlanner(clock=Clock())
    planner.handle(interfaces.SYSTEM_TICK, tick(5.0))
    planner.handle(interfaces.TAILNET_STATUS, status(connected=False))
    assert endpoints(planner.handle(interfaces.CAMERA_ANNOUNCED, camera())) == []


def test_malformed_payload_is_ignored_not_raised() -> None:
    planner = RelayPlanner(clock=Clock())
    assert planner.handle(interfaces.CAMERA_ANNOUNCED, {"schema_version": V}) == []
