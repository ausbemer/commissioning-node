"""HTTP surface — the dashboard's two listeners and its three doors (I/O only; ADR-0003).

- **8080, all addresses (dual-stack):** the *local* door (request arrived on loopback) and
  the *tailnet* door (any other local address; the caller must resolve to a tailnet peer).
  The firewall drops 8080 off `lo`/`tailscale0` as the second, independent check.
- **8443, TLS, all addresses:** the *fallback* door. Every path but `/login` needs an
  admin session. It starts only when the admin credential and the TLS key pair are usable,
  so a missing password means no listener (fails closed).

Every decision is a pure function from `interface/` (`classify`, `host_allowed`,
`same_origin`, `authorize`, `AdminAuth`); this file only moves bytes and calls them, and
every write passes `authorize()` first. Every refusal is in-band with its reason (rung 7).

Static files (`STATIC_PATH`, ADR-0006) are the module's `static/` directory as it was at
startup: an allowlist by name, never a path joined from the request, and sorted into a door
like a page.

The day/night choice (`THEME_PATH`) is not a write: it changes nothing on the node, only a
cookie in the caller's own browser. It is still sorted into a door like any page, and it
returns only to a page this surface serves (ADR-0003 amendment, 2026-10-13).
"""
from __future__ import annotations

import errno
import json
import logging
import socket
import ssl
import threading
from dataclasses import dataclass
from pathlib import Path
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping
from urllib.parse import parse_qs, parse_qsl

from ..interface import (FALLBACK, MODULE, STATIC_PATH, THEME_COOKIE, THEME_PATH, Action, ActionResult,
                         AdminAuth, Caller, ConfigSurface, DashboardState, PeerDirectory, Refusal,
                         View, authorize, classify, config_rows, host_allowed, same_origin,
                         theme_choice, theme_return)
from ..interface.admin import COOKIE, LABEL

log = logging.getLogger(MODULE)

STATE_PATH = "/api/state"
LOGIN_PATH = "/login"
LOGOUT_PATH = "/logout"
#: Largest POST body read (§8.8). A form is a few fields.
MAX_BODY = 16 * 1024
#: Seconds a connection may sit idle mid-request (a slow client holds one thread only).
CONN_TIMEOUT_S = 15
#: How long a browser keeps the day/night choice. A display preference, not a behaviour constant.
THEME_MAX_AGE_S = 365 * 24 * 3600

#: On every response. No framing (clickjacking a form), no sniffing, no referrer leaks;
#: inline CSS, and script only from this node's own files (ADR-0006): no inline script, no
#: event-handler attributes, no eval, nothing from another origin. Referrer-Policy is `same-origin`, never
#: `no-referrer`: under `no-referrer` browsers send `Origin: null` on every POST, even
#: same-origin (Fetch, "append a request Origin header"), and `same_origin()` refuses them
#: all. `same-origin` still sends nothing to other sites.
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; connect-src 'self'; "
                               "style-src 'unsafe-inline'; "
                               "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}


#: The module's static files and the types they are served as (ADR-0006).
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}


def load_static(directory: Path = STATIC_DIR) -> dict[str, tuple[str, str]]:
    """{file name: (content type, text)} for every servable file, read once at startup."""
    if not directory.is_dir():
        return {}
    return {f.name: (STATIC_TYPES[f.suffix], f.read_text(encoding="utf-8"))
            for f in sorted(directory.iterdir()) if f.is_file() and f.suffix in STATIC_TYPES}


@dataclass(frozen=True)
class FallbackConfig:
    port: int
    cert_path: str
    key_path: str


class _Logout:
    """Ending a session is a write too: it passes the same `authorize()`."""

    name = "session.logout"
    path = LOGOUT_PATH
    may_disturb = None

    def run(self, form: Mapping[str, str], caller: Caller) -> ActionResult:
        return ActionResult(True, "logged out")


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        # A dropped connection or a failed TLS handshake is routine on an untrusted LAN.
        log.debug("connection from %s ended with an error", client_address, exc_info=True)


class _Server6(_Server):
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)   # dual-stack
        super().server_bind()


class _Tls:
    """TLS whose handshake runs on the request thread, not in accept(): a client that
    connects and never handshakes holds one thread until the timeout, not the listener."""

    def __init__(self, addr, handler, context: ssl.SSLContext) -> None:
        self._context = context
        super().__init__(addr, handler)

    def get_request(self):
        sock, addr = super().get_request()
        sock.settimeout(CONN_TIMEOUT_S)
        return self._context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr


class _TlsServer(_Tls, _Server):
    pass


class _TlsServer6(_Tls, _Server6):
    pass


def _bind(host: str, port: int, handler, context: ssl.SSLContext | None = None) -> _Server:
    """Bind `host`; "::" means every address, IPv4 included, falling back to IPv4 alone
    when the host has IPv6 switched off."""
    v6, v4 = (_TlsServer6, _TlsServer) if context else (_Server6, _Server)
    extra = (context,) if context else ()
    if ":" in host:
        try:
            return v6((host, port), handler, *extra)
        except OSError as exc:
            if host != "::" or exc.errno not in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL):
                raise
            log.warning("IPv6 unavailable (%s); dashboard binding IPv4 only", exc)
            host = "0.0.0.0"
    return v4((host, port), handler, *extra)


class HttpSurface:
    """Both listeners. `start()` returns the 8080 listener's bound (host, port)."""

    def __init__(self, state: DashboardState, views: list[View], host: str, port: int,
                 peers: PeerDirectory, actions: list[Action] | None = None,
                 admin: AdminAuth | None = None, fallback: FallbackConfig | None = None,
                 login_view: View | None = None, config: ConfigSurface | None = None,
                 ) -> None:
        self._state = state
        self._peers = peers
        self._views = {v.path: v for v in views}
        self._actions = {a.path: a for a in (actions or [])}
        self._login_view = login_view
        self._config = config
        self._static = load_static()
        reserved = {LOGIN_PATH, LOGOUT_PATH, STATE_PATH}
        paths = set(self._views) | set(self._actions)
        clash = ((set(self._views) & set(self._actions)) | (reserved & paths)
                 | {p for p in paths if p == THEME_PATH or p.startswith((THEME_PATH + "/", STATIC_PATH))})
        if clash:
            raise ValueError(f"dashboard paths claimed twice: {sorted(clash)}")
        self._host, self._port = host, port
        self._admin, self._fallback = admin, fallback
        self._server: _Server | None = None
        self._tls_server: _Server | None = None
        self.fallback_address: tuple[str, int] | None = None

    # --- lifecycle --------------------------------------------------------------------

    def start(self) -> tuple[str, int]:
        self._server = _bind(self._host, self._port, self._handler(tls=False))
        self._serve(self._server, "dashboard-http")
        host, port = self._server.server_address[:2]
        log.info("dashboard serving on port %d (local + tailnet doors)", port)
        self._start_fallback()
        return host, port

    def _start_fallback(self) -> None:
        if self._fallback is None or self._admin is None:
            return
        usable, why = self._admin.status()
        if not usable:
            log.warning("admin fallback OFF: %s (sudo ops/set-admin-password.sh)", why)
            return
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(self._fallback.cert_path, self._fallback.key_path)
            self._tls_server = _bind(self._host, self._fallback.port, self._handler(tls=True), ctx)
        except (OSError, ssl.SSLError) as exc:
            log.error("admin fallback OFF: cannot start TLS listener on %d (%s)",
                      self._fallback.port, exc)
            return
        self._serve(self._tls_server, "dashboard-fallback")
        self.fallback_address = self._tls_server.server_address[:2]
        log.info("admin fallback serving on port %d (TLS)", self.fallback_address[1])

    @staticmethod
    def _serve(server: _Server, name: str) -> None:
        threading.Thread(target=server.serve_forever, name=name, daemon=True).start()

    def stop(self) -> None:
        for server in (self._server, self._tls_server):
            if server is not None:
                server.shutdown()
                server.server_close()
        self._server = self._tls_server = None
        self.fallback_address = None

    # --- requests ---------------------------------------------------------------------

    def _handler(self, tls: bool) -> type[BaseHTTPRequestHandler]:
        surface = self
        scheme = "https" if tls else "http"

        class Handler(BaseHTTPRequestHandler):
            timeout = CONN_TIMEOUT_S
            server_version = "commissioning-node"
            sys_version = ""

            # -- who is calling --------------------------------------------------------

            def _caller(self) -> Caller | Refusal | None:
                """Caller on the 8080 doors (or a refusal); on the fallback, the session's
                caller or None when not logged in."""
                if tls:
                    s = surface._admin.session(self._cookie())
                    return Caller(FALLBACK, LABEL, csrf=s.csrf) if s else None
                local = self.connection.getsockname()[0]
                who = classify(local, self.client_address[0], surface._peers)
                if isinstance(who, Refusal):
                    return who
                if not host_allowed(self.headers.get("Host"), local):
                    return Refusal(421, "bad-host", "refused: open the dashboard by the node's "
                                                    "tailnet IP address (or localhost on the node)")
                return who

            def _cookie(self, name: str = COOKIE) -> str | None:
                try:
                    jar = SimpleCookie(self.headers.get("Cookie", ""))
                except CookieError:
                    return None
                return jar[name].value if name in jar else None

            def _theme(self) -> str | None:
                return theme_choice(self._cookie(THEME_COOKIE))

            # -- GET ---------------------------------------------------------------------

            def do_GET(self) -> None:  # noqa: N802 — http.server naming
                path, _, query = self.path.partition("?")
                if tls and path == LOGIN_PATH:
                    return self._login_page("", 200)
                caller = self._caller()
                if isinstance(caller, Refusal):
                    return self._refuse(caller.status, caller.message)
                if path.startswith(THEME_PATH + "/"):
                    # Allowed before a fallback login too: the login page has the slider.
                    return self._set_theme(path[len(THEME_PATH) + 1:], query)
                if caller is None:
                    if path == STATE_PATH:
                        return self._refuse(401, "log in first")
                    return self._redirect(LOGIN_PATH)
                if path.startswith(STATIC_PATH):
                    found = surface._static.get(path[len(STATIC_PATH):])
                    if found is None:
                        return self._refuse(404, "not found")
                    return self._send(200, *found)
                if path == STATE_PATH:
                    return self._send(200, "application/json",
                                      json.dumps(surface._state.snapshot(), indent=2))
                view = surface._views.get(path)
                if view is None:
                    return self._refuse(404, "not found")
                snap = surface._state.snapshot()
                snap["viewer"] = {"label": caller.label, "door": caller.door, "csrf": caller.csrf}
                snap["theme"] = self._theme()
                # Display state a page keeps across its own auto-refresh (e.g. a section left
                # open); the view reads only the keys it knows and escapes what it shows.
                snap["query"] = {k: v[0] for k, v in parse_qs(query).items()}
                # The write paths this surface answers, so a view offers only what exists.
                snap["writes"] = sorted(surface._actions)
                if surface._config is not None:
                    snap["config"] = config_rows(surface._config)   # secrets carry no value
                # Fresh tailnet peers by direct address, for the site map (display only).
                snap["tailnet_endpoints"] = {a: {"node_name": who.node_name, "login": who.login,
                                                 "tags": list(who.tags)}
                                             for a, who in surface._peers.endpoints().items()}
                self._send(200, view.content_type, view.render(snap))

            # -- POST --------------------------------------------------------------------

            def do_POST(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                form = self._form()
                if form is None:
                    return
                origin_ok = same_origin(self.headers.get("Origin"), scheme, self.headers.get("Host"))
                if tls and path == LOGIN_PATH:
                    return self._login(form, origin_ok)
                caller = self._caller()
                if isinstance(caller, Refusal):
                    return self._refuse(caller.status, caller.message)
                if tls and path == LOGOUT_PATH:
                    action: Action | None = _Logout()
                else:
                    action = surface._actions.get(path)
                if action is None:
                    return self._refuse(404, "no such action")
                decision = authorize(caller, action, form, origin_ok)
                if not decision.allowed:
                    log.info("refused %s (%s) from %s: %s", action.name, decision.reason,
                             caller.label if caller else self.client_address[0], decision.message)
                    return self._refuse(decision.status, decision.message)
                log.info("write: %s", decision.message)     # who, what, which door
                result = action.run(form, caller)
                if isinstance(action, _Logout):
                    surface._admin.logout(self._cookie())
                    return self._redirect(LOGIN_PATH, clear_cookie=True)
                # A browser form names the page to return to; only a page this surface
                # serves is honoured (never an open redirect). Refusals always stay in-band.
                if result.ok and form.get("then") in surface._views:
                    return self._redirect(form["then"])
                self._send(200 if result.ok else 400, "text/plain; charset=utf-8",
                           result.message + "\n")

            def _login(self, form: dict[str, str], origin_ok: bool) -> None:
                if not origin_ok:
                    return self._login_page("Refused: the form did not come from this page.", 403)
                out = surface._admin.login(self.client_address[0], form.get("password", ""))
                if isinstance(out, Refusal):
                    log.warning("fallback login refused for %s: %s", self.client_address[0], out.reason)
                    return self._login_page(out.message, out.status)
                log.info("fallback login: %s from %s", LABEL, self.client_address[0])
                self._redirect("/", cookie=out.token)

            def _form(self) -> dict[str, str] | None:
                try:
                    length = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    self._refuse(411, "Content-Length required")
                    return None
                if not 0 <= length <= MAX_BODY:
                    self._refuse(413, f"body over {MAX_BODY} bytes")
                    return None
                ctype = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                raw = self.rfile.read(length)
                if length and ctype != "application/x-www-form-urlencoded":
                    self._refuse(415, "send application/x-www-form-urlencoded")
                    return None
                try:
                    return dict(parse_qsl(raw.decode("utf-8"), keep_blank_values=True))
                except (UnicodeDecodeError, ValueError):
                    self._refuse(400, "malformed form")
                    return None

            def _refuse_method(self) -> None:
                self._refuse(405, "method not allowed")

            do_PUT = do_PATCH = do_DELETE = _refuse_method

            # -- responses ---------------------------------------------------------------

            def _set_theme(self, name: str, query: str) -> None:
                theme = theme_choice(name)
                if theme is None:
                    return self._refuse(404, "not found")
                pages = set(surface._views) | ({LOGIN_PATH} if tls else set())
                then = (parse_qs(query).get("then") or [None])[0]
                secure = "; Secure" if tls else ""
                self._redirect(theme_return(then, pages),
                               set_cookie=f"{THEME_COOKIE}={theme}; Path=/; Max-Age={THEME_MAX_AGE_S}; "
                                          f"HttpOnly; SameSite=Strict{secure}")

            def _login_page(self, message: str, code: int) -> None:
                view = surface._login_view
                body = view.render({"message": message, "theme": self._theme()}) if view else message
                self._send(code, view.content_type if view else "text/plain; charset=utf-8", body)

            def _redirect(self, to: str, cookie: str | None = None, clear_cookie: bool = False,
                          set_cookie: str | None = None) -> None:
                self.send_response(303)
                self.send_header("Location", to)
                if set_cookie is not None:
                    self.send_header("Set-Cookie", set_cookie)
                if cookie is not None:
                    self.send_header("Set-Cookie", f"{COOKIE}={cookie}; Path=/; Secure; HttpOnly; SameSite=Strict")
                elif clear_cookie:
                    self.send_header("Set-Cookie", f"{COOKIE}=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0")
                self._headers(0)

            def _refuse(self, code: int, message: str) -> None:
                self._send(code, "text/plain; charset=utf-8", message + "\n")

            def _send(self, code: int, content_type: str, body: str) -> None:
                data = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self._headers(len(data))
                self.wfile.write(data)

            def _headers(self, length: int) -> None:
                self.send_header("Content-Length", str(length))
                for k, v in SECURITY_HEADERS.items():
                    self.send_header(k, v)
                self.end_headers()

            def log_message(self, fmt: str, *args) -> None:
                log.debug("http: " + fmt, *args)

        return Handler
