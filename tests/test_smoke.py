"""End-to-end: modules exchange real messages over the bus (no mocks of our own code).

register camera -> camera-config announces -> stream-relay plans a path -> dashboard sees it,
with tailnet status coming from a fake overlay source via the real tick path.
"""
from __future__ import annotations

import interfaces
import main
from bus import Bus
from modules.tailnet_gateway.interface import OverlaySnapshot


class FakeOverlay:
    def __init__(self, snapshot: OverlaySnapshot) -> None:
        self.snapshot = snapshot

    def read(self) -> OverlaySnapshot:
        return self.snapshot


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def record(bus: Bus, pattern: str) -> list[tuple[str, dict]]:
    seen: list[tuple[str, dict]] = []
    bus.subscribe(pattern, lambda t, p: seen.append((t, p)))
    return seen


def make(connected: bool = True):
    clock = Clock()
    overlay = FakeOverlay(OverlaySnapshot(hostname="cn-test", connected=connected,
                                          addresses=["100.64.0.7", "fd7a:115c::7"],
                                          served_routes=["192.168.10.0/24"]))
    node = main.compose(Bus(), overlay_source=overlay, clock=clock, dashboard_port=0)
    return node, clock, overlay


def register(node, camera_id: str = "ACCC8E123456"):
    reg = interfaces.CameraRegistration.stamped(interfaces.__version__, camera_id=camera_id,
                                                address="192.168.10.21", label="Gate 3")
    return main.invoke(node, interfaces.CAMERA_REGISTER, reg.model_dump(),
                       requires="camera-config:camera-registry")


def test_camera_registration_flows_to_a_planned_stream_with_tailnet_endpoint() -> None:
    node, _, _ = make()
    streams = record(node.bus, interfaces.STREAM_ANNOUNCED)
    main.tick(node)                                   # tick -> tailnet status (fake overlay)
    ok, msg = register(node)
    assert ok, msg

    announcement = interfaces.StreamAnnouncement.model_validate(streams[-1][1])
    assert announcement.schema_version == interfaces.__version__
    assert announcement.path == "cam-accc8e123456"
    assert announcement.live is False
    assert [e.url for e in announcement.endpoints] == ["rtsp://100.64.0.7:8554/cam-accc8e123456"]
    assert announcement.origin == "stream-relay/relay-planner"


def test_no_tailnet_means_no_endpoints_safe_default() -> None:
    node, _, _ = make(connected=False)
    streams = record(node.bus, interfaces.STREAM_ANNOUNCED)
    main.tick(node)
    register(node)
    assert interfaces.StreamAnnouncement.model_validate(streams[-1][1]).endpoints == []


def test_dashboard_state_sees_every_kind_and_wire_version() -> None:
    node, _, _ = make()
    main.tick(node)
    register(node)
    snap = node.dashboard._state.snapshot()
    assert set(snap["cameras"]) == {"ACCC8E123456"}
    assert set(snap["streams"]) == {"ACCC8E123456"}
    assert snap["tailnet"]["self"]["connected"] is True               # keyed by node, not name
    card = next(iter(snap["nodes"].values()))
    assert {m["name"] for m in card["modules"]} == {"tailnet-gateway", "camera-config", "stream-relay", "site-survey", "dashboard"}
    assert set(snap["wire"].values()) == {interfaces.__version__}


def test_invoke_refuses_missing_capability_in_band() -> None:
    node, _, _ = make()
    ok, msg = main.invoke(node, interfaces.CAMERA_REGISTER, {"schema_version": "0.1.0"},
                          requires="camera-config:vapix-writer")
    assert not ok
    assert msg == "camera-config:vapix-writer not available — service camera-config at v0.1.0 predates support."


def test_capabilities_are_aggregated() -> None:
    node, _, _ = make()
    assert node.capabilities() == {
        "camera-config:camera-registry",
        "dashboard:peer-directory",
        "dashboard:state-collector",
        "site-survey:scanner",
        "stream-relay:relay-planner",
        "tailnet-gateway:peer-report",
        "tailnet-gateway:status-report",
    }
