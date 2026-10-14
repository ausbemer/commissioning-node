"""Page chrome — the frame every dashboard HTML view shares (0.7.0).

Pure functions and one stylesheet: colour tokens for day and night, the base type, the
banner (node identity, page navigation, who is signed in and via which door, the day/night
slider) and the components both pages use. Features build their page body and hand it to
`page()`; they never import each other, so the shared frame lives here.

Everything stays inside the dashboard's CSP: no inline script, no outside font or image,
inline CSS, every value escaped. Pages load the one first-party script (`SCRIPT`, ADR-0006)
and work without it. The day/night slider is two links to `THEME_PATH`; core
remembers the choice in a cookie (ADR-0003 amendment, 2026-10-13) and the page renders it
as `data-theme` on the root. With no choice made the page follows the device's
`prefers-color-scheme`.
"""
from __future__ import annotations

from html import escape
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from .config import SITE_OPERATIONS, TAILNET_OPERATIONS

#: Pages the banner links to, in order: (path, label).
NAV = (("/", "Overview"), ("/config", "Config"))

#: Static files core serves from the module's `static/` directory (ADR-0006).
STATIC_PATH = "/static/"
#: The one script every page loads; progressive enhancement only.
SCRIPT = STATIC_PATH + "dashboard.js"

#: The slider's links are `THEME_PATH/<theme>?then=<page>`; the choice lives in THEME_COOKIE.
THEME_PATH = "/theme"
THEME_COOKIE = "cn-theme"
#: Valid choices, as `data-theme` values: (theme, label).
THEMES = (("light", "Day"), ("dark", "Night"))


def theme_choice(value: str | None) -> str | None:
    """A cookie or path value → a known theme, or None (follow the device)."""
    return value if value in dict(THEMES) else None


def theme_return(then: str | None, pages: set[str]) -> str:
    """Where the slider sends the browser back to: only a page this surface serves, with its
    display query (e.g. `/?system=open`) re-encoded; never an open redirect, and anything
    else goes to the overview."""
    try:
        parts = urlsplit(then or "")
    except ValueError:
        return "/"
    if parts.scheme or parts.netloc or parts.path not in pages:
        return "/"
    query = urlencode(parse_qsl(parts.query))
    return f"{parts.path}?{query}" if query else parts.path

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
.theme a {{ position:relative; z-index:2; display:grid; place-items:center; color:var(--muted); border-radius:12px; }}
.theme a:hover, .theme a[aria-current="true"] {{ color:var(--ink); }}
.theme a:focus-visible {{ outline-offset:-2px; }}
.theme svg {{ width:14px; height:14px; }}
.theme .thumb {{ position:absolute; z-index:1; top:2px; left:2px; width:26px; height:24px; border-radius:12px;
  background:var(--surface); border:1px solid var(--line-strong); transition:transform .18s ease; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) .theme .thumb {{ transform:translateX(32px); }} }}
:root[data-theme="dark"] .theme .thumb {{ transform:translateX(32px); }}
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

.op {{ border:1px solid var(--line-strong); border-radius:8px; padding:10px 12px; background:var(--surface); margin:0; }}
.op.danger {{ border-color:var(--crit); }}
.op h3 {{ font-size:15px; margin-bottom:6px; }}
.op.bare {{ border:0; padding:0; background:none; }}
.confirm {{ display:flex; gap:8px; align-items:flex-start; font-size:13px; margin-bottom:10px; }}
.confirm input {{ margin-top:3px; accent-color:var(--accent); flex:none; }}
.op.danger .confirm input {{ accent-color:var(--crit); }}
.js-slide .confirm, .js-slide button[type="submit"] {{ display:none; }}
.slide {{ position:relative; height:44px; border-radius:22px; background:var(--surface-2);
  border:1px solid var(--line-strong); overflow:hidden; touch-action:none; user-select:none; }}
.op.danger .slide {{ border-color:var(--crit); }}
.slide-fill {{ position:absolute; inset:0 auto 0 0; width:22px; background:var(--accent-soft); }}
.op.danger .slide-fill {{ background:var(--crit-soft); }}
.slide-text {{ position:absolute; inset:0; display:grid; place-items:center; font-size:13px;
  font-weight:600; color:var(--muted); padding:0 12px 0 40px; pointer-events:none;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.slide-knob {{ position:absolute; top:2px; left:2px; width:38px; height:38px; border-radius:19px;
  display:grid; place-items:center; background:var(--accent); color:var(--accent-ink); font-weight:700;
  cursor:grab; touch-action:none; }}
.op.danger .slide-knob {{ background:var(--crit); color:var(--surface); }}
.slide-knob:active {{ cursor:grabbing; }}
.slide.snap .slide-knob, .slide.snap .slide-fill {{ transition:transform .2s ease, width .2s ease; }}
.slide.done .slide-text {{ color:var(--accent); }}
.slide.failed .slide-text, .op.danger .slide.failed .slide-text {{ color:var(--crit); }}
.op.danger .slide.done .slide-text {{ color:var(--crit); }}
.slide:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
@media (prefers-reduced-motion: reduce) {{ .slide.snap .slide-knob, .slide.snap .slide-fill {{ transition:none; }} }}

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

_ICONS = {"light": _SUN, "dark": _MOON}


def slider(back: str, theme: str | None = None) -> str:
    """The day/night slider: one link per theme, each returning to `back`."""
    then, chosen = quote(back, safe="/"), ' aria-current="true"'
    links = "".join(
        f'<a href="{THEME_PATH}/{t}?then={then}" title="{label}"{chosen if t == theme else ""}>'
        f'{_ICONS[t]}<span class="sr">{label}</span></a>'
        for t, label in THEMES)
    return f'<nav class="theme" aria-label="Day or night">{links}<span class="thumb" aria-hidden="true"></span></nav>'


def tailnet_badge(status: dict | None) -> tuple[str, str]:
    """(css class, text) for the banner's tailnet state."""
    if not status:
        return "", "No tailnet status"
    if status.get("connected"):
        return "ok", "On tailnet"
    return "warn", f"Off tailnet ({status.get('state') or 'not connected'})"


def banner(*, name: str, status: dict | None = None, current: str | None = None,
           viewer: dict | None = None, theme: str | None = None, back: str | None = None) -> str:
    """Node identity, page links (`current` is the path being shown; None hides the
    links), who is signed in, and the day/night slider (returning to `back`, default
    `current`, and marking `theme` as chosen)."""
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
    return f'<header class="banner"><div class="wrap">{ident}{nav}{who}{slider(back or current or "/", theme)}</div></header>'


def page(*, title: str, body: str, banner_html: str, style: str = "",
         refresh_s: int | None = None, theme: str | None = None, script: bool = True) -> str:
    """A whole HTML document. `body` and `banner_html` are already-escaped markup; `style`
    is the page's own CSS, added after the shared sheet. `refresh_s` reloads the page: by
    the script (which waits out a slide in progress), or by a <noscript> meta refresh."""
    root = f' data-theme="{theme}"' if theme_choice(theme) else ""
    refresh = (f'<noscript><meta http-equiv="refresh" content="{int(refresh_s)}"></noscript>\n'
               if refresh_s else "")
    data = f' data-refresh="{int(refresh_s)}"' if refresh_s else ""
    js = f'<script src="{SCRIPT}" defer></script>\n' if script else ""
    return f"""<!doctype html>
<html lang="en"{root}><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
{refresh}<title>{escape(title)}</title>
{js}<style>{STYLE}{style}</style></head><body{data}>
{banner_html}
<main class="wrap">
{body}
</main></body></html>
"""


#: Operations that take something away from tailnet users: drawn in the danger colour.
DANGER_OPERATIONS = {"leave", "routes-off"}


def op_form(operation: str, label: str, csrf: str | None, then: str, *, slide: bool = False,
            marker: str | None = None, await_s: int | None = None) -> str:
    """One tailnet or site operation (`TAILNET_OPERATIONS`, `SITE_OPERATIONS`) as a may-disturb
    form posting back to `then`: its warning as the required confirm, and a submit button
    named `label`. With `slide`, script swaps the confirm and button for a slider reading
    `label` (ADR-0006), on a bare form; the warning then shows only without script, where it
    is the confirm. A confirmed slide waits for the page's `data-last-apply` marker to change,
    for up to 30 s; `marker` names another marker attribute and `await_s` another wait (0.9.0)."""
    path, warning = TAILNET_OPERATIONS.get(operation) or SITE_OPERATIONS[operation]
    danger = operation in DANGER_OPERATIONS
    classes = "op" + (" danger" if danger else "") + (" bare" if slide else "")
    token = f'<input type="hidden" name="csrf" value="{escape(csrf)}">' if csrf else ""
    waits = (f' data-marker="{escape(marker)}"' if marker else "") + \
        (f' data-await-s="{int(await_s)}"' if await_s else "")
    return (f'<form class="{classes}" method="post" action="{escape(path)}"{" data-slide" if slide else ""}{waits}>'
            f'{token}<input type="hidden" name="then" value="{escape(then)}">'
            f'<label class="confirm"><input type="checkbox" name="confirm" value="yes" required> '
            f'<span>{escape(warning)}</span></label>'
            f'<button class="btn {"danger" if danger else "primary"}" type="submit">{escape(label)}</button></form>')
