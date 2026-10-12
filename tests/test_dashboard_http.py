"""The dashboard surface really serves, stays read-only, and stays on loopback (ADR-0001)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

import interfaces
import main
from bus import Bus
from modules.dashboard.core.http_surface import HttpSurface
from modules.dashboard.interface import DashboardState


@pytest.fixture
def served():
    node = main.compose(Bus(), dashboard_port=0)
    main.tick(node)
    host, port = node.dashboard.start()
    yield node, f"http://{host}:{port}"
    node.dashboard.stop()


def test_overview_and_state_are_served(served) -> None:
    node, base = served
    reg = interfaces.CameraRegistration.stamped(interfaces.__version__, camera_id="ACCC8E000001",
                                                address="10.0.0.5", label="<script>x</script>")
    node.bus.publish(interfaces.CAMERA_REGISTER, reg.model_dump())
    html = urllib.request.urlopen(base + "/").read().decode()
    assert "ACCC8E000001" in html
    assert "<script>" not in html                       # everything is escaped
    state = json.loads(urllib.request.urlopen(base + "/api/state").read())
    assert "ACCC8E000001" in state["cameras"]


def test_writes_are_refused(served) -> None:
    _, base = served
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(urllib.request.Request(base + "/", data=b"x", method="POST"))
    assert err.value.code == 405


def test_non_loopback_bind_is_refused() -> None:
    with pytest.raises(ValueError):
        HttpSurface(DashboardState(), [], "0.0.0.0", 8080)
