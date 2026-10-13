"""login-page — the admin fallback's login form (ADR-0003 §3). Served only on the TLS
listener; no JavaScript, the message is escaped, and the password is never echoed back."""
from __future__ import annotations

from html import escape

from ..interface import banner, page

STYLE = """
.login { max-width:24rem; margin:3rem auto; background:var(--surface); border:1px solid var(--line);
  border-radius:10px; padding:20px; display:grid; gap:10px; }
.login h1 { font-size:22px; }
.login .sub { color:var(--muted); margin:0; }
.login .msg { color:var(--crit); margin:0; }
.login form { display:grid; gap:8px; }
.login label { font-size:13px; color:var(--muted); }
.login input[type=password] { font-size:15px; padding:8px 10px; }
.login .btn { justify-self:start; font-size:14px; padding:6px 14px; }
"""


class LoginPage:
    path = "/login"
    content_type = "text/html; charset=utf-8"

    def render(self, snapshot: dict) -> str:
        message = snapshot.get("message") or ""
        note = f'<p class="msg" role="alert">{escape(message)}</p>' if message else ""
        body = f"""<section class="login">
<h1>Admin login</h1>
<p class="sub">The admin fallback, for when the tailnet is unavailable.</p>
{note}
<form method="post" action="/login">
<label for="password">Admin password</label>
<input id="password" name="password" type="password" autocomplete="current-password" required autofocus>
<button class="btn primary" type="submit">Log in</button>
</form>
</section>"""
        theme = snapshot.get("theme")
        return page(title="Admin login · commissioning-node", body=body, style=STYLE, theme=theme,
                    banner_html=banner(name="commissioning-node", theme=theme, back=self.path))
