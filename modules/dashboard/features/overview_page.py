"""overview-page — the operator's one-page view of the node (config + ops surface).

Server-rendered HTML with no JavaScript: nothing ships unparsed (§8.1). Every value is
HTML-escaped. The only form is "log out" on the fallback door. Config is edited on its own
page (`/config`, config-page), which does not auto-refresh under a half-typed value.
"""
from __future__ import annotations

from html import escape

from interfaces import __version__ as CONTRACT_VERSION

#: Browser auto-refresh, seconds. A display preference, not a behaviour constant.
REFRESH_S = 5


def _table(headers: list[str], rows: list[list[str]], empty: str) -> str:
    if not rows:
        return f'<p class="empty">{escape(empty)}</p>'
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in row) + "</tr>" for row in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _yes(value: object) -> str:
    return "yes" if value else "no"


class OverviewPage:
    path = "/"
    content_type = "text/html; charset=utf-8"

    def render(self, snapshot: dict) -> str:
        nodes = list(snapshot.get("nodes", {}).values())
        module_rows = [
            [m.get("name", ""), m.get("release_version", ""), m.get("inward_version", ""),
             ", ".join(m.get("capabilities", [])) or "none"]
            for node in nodes for m in node.get("modules", [])
        ]
        tailnet_rows = [
            [t.get("hostname", ""), _yes(t.get("connected")), ", ".join(t.get("addresses", [])) or "none",
             ", ".join(t.get("served_routes", [])) or "none"]
            for t in snapshot.get("tailnet", {}).values()
        ]
        camera_rows = [
            [c.get("camera_id", ""), c.get("label") or "", c.get("address", ""),
             c.get("model") or "not probed", c.get("firmware") or "not probed"]
            for c in snapshot.get("cameras", {}).values()
        ]
        stream_rows = [
            [s.get("camera_id", ""), s.get("path", ""), _yes(s.get("live")),
             ", ".join(e.get("url", "") for e in s.get("endpoints", [])) or "none (no fresh tailnet address)"]
            for s in snapshot.get("streams", {}).values()
        ]
        wire_rows = [
            [topic, version, CONTRACT_VERSION, "ok" if version == CONTRACT_VERSION else "SKEW"]
            for topic, version in sorted(snapshot.get("wire", {}).items())
        ]
        host = escape(nodes[0].get("hostname", "")) if nodes else "unknown node"
        viewer = snapshot.get("viewer") or {}
        who = ""
        if viewer:
            who = (f'<p class="sub">Signed in as {escape(viewer.get("label", ""))} '
                   f'via the {escape(viewer.get("door", ""))} door.</p>')
            if viewer.get("csrf"):
                who += ('<form method="post" action="/logout"><input type="hidden" name="csrf" '
                        f'value="{escape(viewer["csrf"])}"><button type="submit">Log out</button></form>')
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{REFRESH_S}">
<title>commissioning-node · {host}</title>
<style>
:root {{ --bg:#f7f7f5; --fg:#1d1d1b; --muted:#6b6b66; --line:#d9d9d4; --warn:#a33a00; color-scheme: light; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#18181a; --fg:#ececea; --muted:#9a9a95; --line:#34343a; --warn:#ff9a5c; color-scheme: dark; }}
}}
body {{ background:var(--bg); color:var(--fg); font:14px/1.45 system-ui, sans-serif; margin:0; padding:1.5rem 1rem; }}
main {{ max-width:70rem; margin:0 auto; }}
h1 {{ font-size:1.3rem; margin:0 0 .25rem; }} h2 {{ font-size:1rem; margin:2rem 0 .5rem; }}
.sub, .empty {{ color:var(--muted); }}
.scroll {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; }}
th, td {{ text-align:left; padding:.35rem .6rem; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ color:var(--muted); font-weight:600; }}
</style></head><body><main>
<h1>commissioning-node · {host}</h1>
<p class="sub">Operator view. Contract v{escape(CONTRACT_VERSION)}. Refreshes every {REFRESH_S}s.
<a href="/config">Config</a></p>
{who}
<h2>Tailnet</h2>
{_table(["Host", "Connected", "Addresses", "Served routes"], tailnet_rows, "No tailnet status yet.")}
<h2>Cameras</h2>
{_table(["Camera", "Label", "Address", "Model", "Firmware"], camera_rows, "No cameras registered.")}
<h2>Streams</h2>
{_table(["Camera", "Relay path", "Live", "Endpoints"], stream_rows, "No streams planned.")}
<h2>Modules</h2>
{_table(["Module", "Release", "Inward contract", "Capabilities"], module_rows, "No node card yet.")}
<h2>Wire versions (§8.5)</h2>
{_table(["Topic", "Seen on wire", "Pinned contract", ""], wire_rows, "Nothing received yet.")}
</main></body></html>
"""
