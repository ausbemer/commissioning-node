"""Page chrome — the frame every dashboard HTML view shares (0.7.0).

Pure functions and one stylesheet: colour tokens for day and night, the base type, the
banner (node identity, page navigation, who is signed in and via which door, the day/night
slider) and the components both pages use. Features build their page body and hand it to
`page()`; they never import each other, so the shared frame lives here.

Everything stays inside the dashboard's CSP: no script, no outside font or image, inline
CSS only, every value escaped. The slider is two radio inputs; with neither checked the page
follows the device's `prefers-color-scheme`. `theme` ("light"/"dark") sets `data-theme` on
the root for a server-remembered choice.
"""
from __future__ import annotations

from html import escape

#: Pages the banner links to, in order: (path, label).
NAV = (("/", "Overview"), ("/config", "Config"))

_COND = 'Bahnschrift, "DIN Alternate", "Roboto Condensed", "Arial Narrow", sans-serif'
_MONO = 'ui-monospace, "SF Mono", "Cascadia Mono", Menlo, monospace'
_BODY = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'

_LIGHT = ("--bg:#ECEFF3; --surface:#FFFFFF; --surface-2:#F4F6F8; --ink:#17202B; --muted:#566373; "
          "--line:#CDD4DC; --line-strong:#95A2B0; --accent:#0A6F7B; --accent-ink:#FFFFFF; "
          "--accent-soft:#DCEFF1; --ok:#2B7A4B; --ok-soft:#DFF0E5; --warn:#8F5B00; "
          "--warn-soft:#F7ECD4; --crit:#B1271F; --crit-soft:#F8E2DF; color-scheme:light;")
_DARK = ("--bg:#131A22; --surface:#1A232D; --surface-2:#202A35; --ink:#E3E8EE; --muted:#97A4B3; "
         "--line:#2E3A47; --line-strong:#52606F; --accent:#52C4CD; --accent-ink:#0E1A1E; "
         "--accent-soft:#15343A; --ok:#62C08D; --ok-soft:#173024; --warn:#E3AA42; "
         "--warn-soft:#3A2D14; --crit:#F0857B; --crit-soft:#3E1E1B; color-scheme:dark;")

#: Slate neutrals, one teal accent; ok / warn / crit are state colours, never decoration.
STYLE = f"""
:root {{ {_LIGHT} }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ {_DARK} }} }}
:root[data-theme="dark"] {{ {_DARK} }}
:root:has(#theme-night:checked) {{ {_DARK} }}
:root:has(#theme-day:checked) {{ {_LIGHT} }}

* {{ box-sizing:border-box; }}
html {{ -webkit-text-size-adjust:100%; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 {_BODY};
  font-variant-numeric:tabular-nums; }}
h1, h2, h3 {{ font-family:{_COND}; font-weight:600; letter-spacing:.005em; margin:0; line-height:1.2; }}
.mono, code {{ font-family:{_MONO}; font-size:.9em; }}
a {{ color:var(--accent); }}
:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
.sr {{ position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }}
.wrap {{ max-width:1180px; margin:0 auto; padding-inline:16px; }}
.muted, .empty {{ color:var(--muted); }}
main.wrap {{ padding-block:28px 40px; }}

.banner {{ position:sticky; top:env(safe-area-inset-top, 0px); z-index:10; background:var(--surface);
  border-bottom:1px solid var(--line); }}
.banner .wrap {{ display:flex; flex-wrap:wrap; align-items:center; gap:8px 20px; min-height:56px;
  padding-block:8px; }}
.ident {{ display:flex; align-items:center; gap:10px; min-width:0; }}
.ident svg {{ flex:none; }}
.ident strong {{ font-family:{_COND}; font-size:18px; font-weight:600; overflow-wrap:anywhere; }}
.state {{ font-size:13px; display:inline-flex; align-items:center; gap:5px; color:var(--muted); }}
.state::before {{ content:""; width:8px; height:8px; border-radius:50%; background:currentColor; }}
.state.ok {{ color:var(--ok); }} .state.warn {{ color:var(--warn); }}
.nav {{ display:flex; gap:2px; margin-right:auto; }}
.nav a {{ color:var(--muted); text-decoration:none; padding:6px 12px; border-radius:6px 6px 0 0;
  font-family:{_COND}; font-size:16px; border-bottom:2px solid transparent; }}
.nav a:hover {{ color:var(--ink); background:var(--surface-2); }}
.nav a[aria-current="page"] {{ color:var(--ink); border-bottom-color:var(--accent); }}
.who {{ display:flex; align-items:center; gap:10px; font-size:13px; color:var(--muted); }}
.who strong {{ color:var(--ink); font-weight:600; }}
.who form {{ margin:0; }}

.theme {{ position:relative; display:inline-grid; grid-template-columns:1fr 1fr; width:64px; height:30px;
  border:1px solid var(--line-strong); border-radius:15px; background:var(--surface-2); flex:none; }}
.theme input {{ position:absolute; opacity:0; width:1px; height:1px; margin:0; }}
.theme label {{ position:relative; z-index:2; display:grid; place-items:center; cursor:pointer; color:var(--muted); }}
.theme label svg {{ width:14px; height:14px; }}
.theme .thumb {{ position:absolute; z-index:1; top:2px; left:2px; width:26px; height:24px; border-radius:12px;
  background:var(--surface); border:1px solid var(--line-strong); transition:transform .18s ease; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) .theme .thumb {{ transform:translateX(32px); }} }}
:root[data-theme="dark"] .theme .thumb, :root:has(#theme-night:checked) .theme .thumb {{ transform:translateX(32px); }}
:root:has(#theme-day:checked) .theme .thumb {{ transform:translateX(0); }}
.theme input:focus-visible + label {{ outline:2px solid var(--accent); outline-offset:-2px; border-radius:12px; }}
.theme input:checked + label {{ color:var(--ink); }}
@media (prefers-reduced-motion: reduce) {{ .theme .thumb {{ transition:none; }} }}
@media (max-width: 560px) {{
  .who .via {{ display:none; }}
  .nav {{ order:3; margin-right:0; flex:1 0 100%; }}
  .ident {{ margin-right:auto; }}
}}

.btn {{ font:inherit; font-size:13px; cursor:pointer; color:var(--ink); background:var(--surface-2);
  border:1px solid var(--line-strong); border-radius:6px; padding:4px 10px; }}
.btn:hover {{ border-color:var(--ink); }}
.btn.primary {{ background:var(--accent); color:var(--accent-ink); border-color:var(--accent); }}
.btn.danger {{ color:var(--crit); border-color:var(--crit); background:var(--surface); }}
.btn.danger:hover {{ background:var(--crit-soft); }}
input[type="text"], input[type="password"], input[type="number"], select {{
  font:inherit; font-size:13.5px; color:var(--ink); background:var(--surface-2);
  border:1px solid var(--line-strong); border-radius:5px; padding:4px 8px; width:100%; min-width:0; }}
input:focus-visible, select:focus-visible {{ outline:2px solid var(--accent); outline-offset:0; }}

.chip {{ display:inline-block; font-size:12px; font-weight:600; padding:1px 8px; border-radius:10px;
  white-space:nowrap; border:1px solid transparent; }}
.chip.ok {{ color:var(--ok); background:var(--ok-soft); }}
.chip.plan {{ color:var(--accent); background:var(--accent-soft); border:1px dashed var(--accent); }}
.chip.warn {{ color:var(--warn); background:var(--warn-soft); }}
.chip.crit {{ color:var(--crit); background:var(--crit-soft); }}
.chip.none {{ color:var(--muted); border-color:var(--line-strong); }}

.scroll {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:13.5px; }}
th {{ text-align:left; font-weight:600; color:var(--muted); font-size:12.5px; padding:6px 10px 6px 0;
  border-bottom:1px solid var(--line-strong); white-space:nowrap; }}
td {{ padding:7px 10px 7px 0; border-bottom:1px solid var(--line); vertical-align:top; }}
caption {{ text-align:left; font-family:{_COND}; font-weight:600; font-size:16px; padding-bottom:6px; }}
.keyline {{ font-size:12.5px; color:var(--muted); margin:8px 0 0; }}
"""

_SUN = ('<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="3" fill="currentColor"/>'
        '<path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.4 1.4M11.6 11.6L13 13M3 13l1.4-1.4M11.6 4.4L13 3" '
        'stroke="currentColor" stroke-width="1.4" stroke-linecap="round" fill="none"/></svg>')
_MOON = ('<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M11.5 10.5A5 5 0 0 1 5.5 4.5a5 5 0 0 0 '
         '0-2.3A6 6 0 1 0 13.8 10.5a5 5 0 0 1-2.3 0z" fill="currentColor"/></svg>')
_MARK = ('<svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">'
         '<rect x="1" y="7" width="6" height="8" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.6"/>'
         '<rect x="15" y="7" width="6" height="8" rx="1.5" fill="var(--accent)" stroke="var(--accent)" stroke-width="1.6"/>'
         '<path d="M7 11h8" stroke="currentColor" stroke-width="1.6" fill="none"/></svg>')

_SLIDER = f"""<div class="theme" role="radiogroup" aria-label="Day or night">
<input type="radio" name="theme" id="theme-day"><label for="theme-day" title="Day">{_SUN}<span class="sr">Day</span></label>
<input type="radio" name="theme" id="theme-night"><label for="theme-night" title="Night">{_MOON}<span class="sr">Night</span></label>
<span class="thumb" aria-hidden="true"></span>
</div>"""


def tailnet_badge(status: dict | None) -> tuple[str, str]:
    """(css class, text) for the banner's tailnet state."""
    if not status:
        return "", "No tailnet status"
    if status.get("connected"):
        return "ok", "On tailnet"
    return "warn", f"Off tailnet ({status.get('state') or 'not connected'})"


def banner(*, name: str, status: dict | None = None, current: str | None = None,
           viewer: dict | None = None) -> str:
    """Node identity, page links (`current` is the path being shown; None hides the
    links), who is signed in, and the day/night slider."""
    cls, text = tailnet_badge(status)
    ident = (f'<div class="ident">{_MARK}<strong>{escape(name)}</strong>'
             + (f'<span class="state {cls}">{escape(text)}</span>' if current is not None else "")
             + "</div>")
    nav = ""
    if current is not None:
        here = ' aria-current="page"'
        links = "".join(f'<a href="{p}"{here if p == current else ""}>{escape(label)}</a>'
                        for p, label in NAV)
        nav = f'<nav class="nav" aria-label="Pages">{links}</nav>'
    who = ""
    if viewer:
        who = (f'<div class="who"><span><strong>{escape(viewer.get("label", ""))}</strong>'
               f'<span class="via"> via the {escape(viewer.get("door", ""))} door</span></span>')
        if viewer.get("csrf"):
            who += ('<form method="post" action="/logout"><input type="hidden" name="csrf" '
                    f'value="{escape(viewer["csrf"])}"><button class="btn" type="submit">Log out</button></form>')
        who += "</div>"
    return f'<header class="banner"><div class="wrap">{ident}{nav}{who}{_SLIDER}</div></header>'


def page(*, title: str, body: str, banner_html: str, style: str = "",
         refresh_s: int | None = None, theme: str | None = None) -> str:
    """A whole HTML document. `body` and `banner_html` are already-escaped markup; `style`
    is the page's own CSS, added after the shared sheet."""
    root = f' data-theme="{theme}"' if theme in ("light", "dark") else ""
    refresh = f'<meta http-equiv="refresh" content="{int(refresh_s)}">\n' if refresh_s else ""
    return f"""<!doctype html>
<html lang="en"{root}><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
{refresh}<title>{escape(title)}</title>
<style>{STYLE}{style}</style></head><body>
{banner_html}
<main class="wrap">
{body}
</main></body></html>
"""
