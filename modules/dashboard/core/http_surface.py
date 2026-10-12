"""HTTP surface — serves the dashboard's views and a JSON state endpoint (I/O only).

Posture (ADR-0001): until tailnet identity (§15) and the local admin-credential fallback
exist, this surface is READ-ONLY and binds to LOOPBACK ONLY. Both are enforced here:
non-GET methods get 405, and a non-loopback bind address is refused. Lifting either is an
ADR, not a config change.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..interface import MODULE, DashboardState, PeerDirectory, View

log = logging.getLogger(MODULE)

STATE_PATH = "/api/state"


class HttpSurface:
    def __init__(self, state: DashboardState, views: list[View], host: str, port: int,
                 peers: PeerDirectory | None = None) -> None:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError(f"dashboard refuses to bind {host}: loopback only until auth exists (ADR-0001)")
        self._state = state
        #: Tailnet caller resolver (ADR-0003 §2). Held, not yet consulted: the tailnet door
        #: and the loopback guard's lift arrive together in ADR-0003 step 4.
        self._peers = peers
        self._views = {v.path: v for v in views}
        self._host, self._port = host, port
        self._server: ThreadingHTTPServer | None = None

    def start(self) -> tuple[str, int]:
        """Bind and serve on a daemon thread. Returns the bound (host, port)."""
        self._server = ThreadingHTTPServer((self._host, self._port), self._handler())
        threading.Thread(target=self._server.serve_forever, name="dashboard-http", daemon=True).start()
        host, port = self._server.server_address[:2]
        log.info("dashboard serving on http://%s:%d/", host, port)
        return host, port

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        state, views = self._state, self._views

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 — http.server naming
                path = self.path.split("?", 1)[0]
                if path == STATE_PATH:
                    self._send(200, "application/json", json.dumps(state.snapshot(), indent=2))
                elif path in views:
                    view = views[path]
                    self._send(200, view.content_type, view.render(state.snapshot()))
                else:
                    self._send(404, "text/plain; charset=utf-8", "not found\n")

            def _refuse(self) -> None:
                self._send(405, "text/plain; charset=utf-8",
                           "read-only surface: writes need the admin-auth ADR first\n")

            do_POST = do_PUT = do_PATCH = do_DELETE = _refuse

            def _send(self, code: int, content_type: str, body: str) -> None:
                data = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, fmt: str, *args) -> None:
                log.debug("http: " + fmt, *args)

        return Handler
