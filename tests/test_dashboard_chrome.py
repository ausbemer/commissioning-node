"""The shared page frame (dashboard interface 0.7.0): escaped, inside the CSP, one banner."""
from __future__ import annotations

import re

from modules.dashboard.features.config_page import ConfigPage
from modules.dashboard.features.login_page import LoginPage
from modules.dashboard.features.overview_page import OverviewPage
from modules.dashboard.interface import banner, page

HOSTILE = '"><script>alert(1)</script>'

SNAPSHOT = {
    "tailnet": {"self": {"hostname": HOSTILE, "connected": True, "state": "Running",
                         "addresses": ["100.64.0.1"], "tags": [HOSTILE],
                         "advertised_routes": ["10.0.0.0/24"], "served_routes": []}},
    "cameras": {"c1": {"camera_id": HOSTILE, "label": HOSTILE, "address": "10.0.0.5"}},
    "streams": {"c1": {"camera_id": HOSTILE, "live": False, "endpoints": []}},
    "wire": {"system/tick": "0.0.1"},
    "viewer": {"label": HOSTILE, "door": "local", "csrf": None},
}


def _pages() -> list[str]:
    return [OverviewPage().render(dict(SNAPSHOT)), ConfigPage().render(dict(SNAPSHOT)),
            LoginPage().render({"message": HOSTILE})]


def test_every_page_escapes_and_stays_inside_the_csp() -> None:
    for html in _pages():
        assert "<script" not in html
        assert not re.search(r"\son[a-z]+=", html)                  # no event handlers
        assert not re.search(r"(src|href)=\"(https?:)?//", html)     # nothing loaded from outside
        assert "@import" not in html and "url(" not in html


def test_banner_marks_the_current_page_and_the_door() -> None:
    html = OverviewPage().render(dict(SNAPSHOT))
    assert '<a href="/" aria-current="page">Overview</a>' in html
    assert '<a href="/config">Config</a>' in html and "via the local door" in html
    assert 'id="theme-day"' in html and 'id="theme-night"' in html
    config = ConfigPage().render(dict(SNAPSHOT))
    assert '<a href="/config" aria-current="page">Config</a>' in config
    assert "refresh" not in config.lower()                          # never wipes a half-typed value


def test_overview_shows_states_in_form() -> None:
    html = OverviewPage().render(dict(SNAPSHOT))
    assert '<span class="chip warn">No endpoint</span>' in html     # no fresh tailnet address
    assert '<span class="chip warn">1 awaiting approval</span>' in html
    assert '<details class="system" open>' in html                 # a skew is never folded away


def test_logout_only_with_a_session_and_theme_only_when_known() -> None:
    assert 'action="/logout"' not in banner(name="n", viewer={"label": "a", "door": "local"})
    assert 'action="/logout"' in banner(name="n", viewer={"label": "a", "door": "fallback", "csrf": "t"})
    assert '<html lang="en" data-theme="dark">' in page(title="t", body="", banner_html="", theme="dark")
    assert '<html lang="en">' in page(title="t", body="", banner_html="", theme='x" onload="y')
