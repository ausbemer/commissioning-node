"""login-page — the admin fallback's login form (ADR-0003 §3). Served only on the TLS
listener; no JavaScript, the message is escaped, and the password is never echoed back."""
from __future__ import annotations

from html import escape


class LoginPage:
    path = "/login"
    content_type = "text/html; charset=utf-8"

    def render(self, snapshot: dict) -> str:
        message = snapshot.get("message") or ""
        note = f'<p class="msg" role="alert">{escape(message)}</p>' if message else ""
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>commissioning-node · admin login</title>
<style>
:root {{ --bg:#f7f7f5; --fg:#1d1d1b; --muted:#6b6b66; --line:#d9d9d4; --warn:#a33a00; color-scheme: light; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#18181a; --fg:#ececea; --muted:#9a9a95; --line:#34343a; --warn:#ff9a5c; color-scheme: dark; }}
}}
body {{ background:var(--bg); color:var(--fg); font:14px/1.45 system-ui, sans-serif; margin:0; padding:1.5rem 1rem; }}
main {{ max-width:24rem; margin:3rem auto; }}
h1 {{ font-size:1.2rem; margin:0 0 .25rem; }}
.sub {{ color:var(--muted); margin:0 0 1.5rem; }} .msg {{ color:var(--warn); }}
label {{ display:block; margin-bottom:.35rem; }}
input[type=password] {{ width:100%; box-sizing:border-box; padding:.5rem; font:inherit;
  background:var(--bg); color:var(--fg); border:1px solid var(--line); border-radius:4px; }}
button {{ margin-top:1rem; padding:.5rem 1rem; font:inherit; }}
</style></head><body><main>
<h1>commissioning-node</h1>
<p class="sub">Admin fallback, for when the tailnet is unavailable.</p>
{note}
<form method="post" action="/login">
<label for="password">Admin password</label>
<input id="password" name="password" type="password" autocomplete="current-password" required autofocus>
<button type="submit">Log in</button>
</form>
</main></body></html>
"""
