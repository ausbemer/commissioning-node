"""The dashboard surface really serves on the local door (ADR-0003 §1)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

import interfaces
import main
from bus import Bus


@pytest.fixture
def served():
    node = main.compose(Bus(), dashboard_port=0, dashboard_host="127.0.0.1")
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
    assert "ACCC8E000001" in html and "via the local door" in html
    assert "<script>" not in html                       # everything is escaped
    state = json.loads(urllib.request.urlopen(base + "/api/state").read())
    assert "ACCC8E000001" in state["cameras"]
    assert "viewer" not in state                        # the API carries node state only


def test_no_actions_ship_yet(served) -> None:
    _, base = served
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(urllib.request.Request(base + "/", data=b"x", method="POST"))
    assert err.value.code == 404


def test_other_methods_are_refused(served) -> None:
    _, base = served
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(urllib.request.Request(base + "/", method="DELETE"))
    assert err.value.code == 405


def test_fallback_never_starts_without_its_config(served) -> None:
    node, _ = served
    assert node.dashboard.fallback_address is None
