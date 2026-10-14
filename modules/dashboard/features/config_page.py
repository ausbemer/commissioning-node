"""config-page — the operator's form for the config registry (ADR-0004 §3), at /config.

Server-rendered, no JavaScript, every value escaped. It does not auto-refresh, so a value
being typed is never wiped. A secret is a write-only password input, never pre-filled, and
reads only as set or unset (§8.9). Each form posts to a config `Action`, behind
`authorize()`; on success core sends the browser back here (`then`).

Saving only stores a value. Tailnet operations on a joined node sit beside their field
(ADR-0005 §3): Rename under the hostname, a may-disturb form with its warning inline; and
under the advertised routes (with what `auto` means now, what is advertised and approved) a
slider like the overview's: Stop advertising routes while any are advertised, else Advertise
the saved ones. Join and Leave are the overview's slider.

Layout (mockup 3): dense, because this page grows with every module that declares config —
a module index (a side list on wide screens, a strip on phones), one block per module, one
grid row per field.
"""
from __future__ import annotations

from html import escape

from ..interface import TAILNET_WRITER, banner, op_form, page

PATH = "/config"
GATEWAY = "tailnet-gateway"

STYLE = """
.cfg-head { display:flex; flex-wrap:wrap; align-items:baseline; gap:6px 20px; margin-bottom:18px; }
.cfg-head h1 { font-size:26px; }
.cfg-head p { margin:0; color:var(--muted); font-size:14px; max-width:70ch; }
.cfg { display:grid; grid-template-columns:180px minmax(0, 1fr); gap:24px; align-items:start; }
.index { position:sticky; top:76px; font-size:14px; }
.index ol { list-style:none; margin:0; padding:0; border-left:2px solid var(--line); }
.index a { display:block; padding:5px 12px; color:var(--muted); text-decoration:none; margin-left:-2px;
  border-left:2px solid transparent; }
.index a:hover { color:var(--ink); border-left-color:var(--line-strong); }
.index .n { float:right; font-size:12px; color:var(--muted); }
@media (max-width: 760px) {
  .cfg { grid-template-columns:minmax(0, 1fr); gap:12px; }
  .index { position:static; }
  .index ol { display:flex; gap:4px; overflow-x:auto; border-left:0; border-bottom:2px solid var(--line); }
  .index a { white-space:nowrap; border-left:0; margin:0 0 -2px; border-bottom:2px solid transparent; }
  .index .n { float:none; margin-left:4px; }
}
.block { background:var(--surface); border:1px solid var(--line); border-radius:10px; margin-bottom:18px;
  scroll-margin-top:130px; }
.block > header { display:flex; flex-wrap:wrap; align-items:baseline; gap:4px 12px; padding:12px 14px;
  border-bottom:1px solid var(--line); }
.block > header h2 { font-size:18px; }
.block > header span { font-size:13px; color:var(--muted); }
.fields { list-style:none; margin:0; padding:0; }
.field { display:grid; grid-template-columns:minmax(200px, 1.35fr) minmax(110px, .8fr) minmax(220px, 1.4fr) 128px;
  gap:6px 14px; align-items:start; padding:10px 14px; border-bottom:1px solid var(--line); }
.field:last-child { border-bottom:0; }
.field .lab { font-weight:600; font-size:14px; }
.field .help { font-size:12.5px; color:var(--muted); margin:2px 0 0; line-height:1.4; }
.field .meta { display:flex; flex-wrap:wrap; gap:4px; margin-top:4px; }
.tag { font-size:11.5px; padding:0 6px; border-radius:3px; border:1px solid var(--line-strong); color:var(--muted); }
.tag.site { border-style:dashed; }
.tag.secret { color:var(--ink); border-color:var(--ink); }
.tag.kind { border-color:transparent; padding-left:0; }
.tag.disturb { color:var(--warn); border-color:var(--warn); }
.cur { font-size:13.5px; padding-top:4px; overflow-wrap:anywhere; }
.cur .set { color:var(--ok); font-weight:600; }
.cur .unset { color:var(--muted); font-weight:400; }
.save, .aside { display:flex; gap:6px; margin:0; align-items:flex-start; flex-wrap:wrap; }
.save input:not([type="checkbox"]):not([type="hidden"]), .save select { flex:1 1 140px; width:auto; }
.save .warnbox { flex-basis:100%; display:flex; gap:6px; font-size:12px; color:var(--warn); align-items:flex-start; }
.save .warnbox input { margin-top:2px; accent-color:var(--warn); flex:none; }
.aside .btn { font-size:12.5px; color:var(--muted); background:transparent; border-color:var(--line); }
.aside .btn:hover { color:var(--ink); border-color:var(--line-strong); }
.aside .btn.del { color:var(--crit); }
.field .extra { grid-column:1 / -1; display:grid; gap:8px; font-size:13px; }
.field .extra p { margin:0; color:var(--muted); }
.field .extra:empty { display:none; }
.field .extra p.fail { color:var(--crit); }
/* A compact slider: it sits in a dense row, under the field it applies. */
.field .extra .op.bare { max-width:300px; }
.field .extra .slide { height:32px; border-radius:16px; }
.field .extra .slide-knob { width:26px; height:26px; border-radius:13px; font-size:12px; }
.field .extra .slide-text { font-size:12.5px; padding-left:30px; }
@media (max-width: 1000px) {
  .field { grid-template-columns:minmax(0, 1fr) minmax(0, 1fr); }
  .field .about { grid-column:1 / -1; }
}
@media (max-width: 520px) { .field { grid-template-columns:minmax(0, 1fr); } }
"""


def _shown(value: object, kind: str) -> str:
    if value is None:
        return "none"
    if kind == "str_list":
        return " ".join(value) if value else "none"
    if kind == "bool":
        return "true" if value else "false"
    return str(value)


def _input(field: dict, row: dict) -> str:
    kind = field.get("kind")
    label = escape(field.get("label") or row.get("key", ""))
    current = "" if not row.get("is_set") or field.get("secret") else _shown(row.get("value"), kind)
    if kind == "bool" and not row.get("is_set"):
        # An unset bool preselects its default, never the first option: saving the form
        # unchanged must not flip it (review 2026-10-13).
        current = _shown(field.get("default"), kind)
    if field.get("secret"):
        verb = "replace" if row.get("is_set") else "set"
        return ('<input type="password" name="value" autocomplete="off" spellcheck="false" '
                f'placeholder="paste to {verb}" aria-label="{label}" required>')
    if kind == "bool":
        options = "".join(f'<option value="{v}"{" selected" if current == v else ""}>{v}</option>'
                          for v in ("true", "false"))
        return f'<select name="value" aria-label="{label}">{options}</select>'
    kind_attr = 'type="number"' if kind == "int" else 'type="text" spellcheck="false"'
    default = field.get("default")
    hint = (f' placeholder="{escape(_shown(default, kind))}"'
            if not row.get("is_set") and default not in (None, [], "") else "")
    return (f'<input {kind_attr} name="value" value="{escape(current)}"{hint} autocomplete="off" '
            f'aria-label="{label}">')


def _hidden(csrf: str | None) -> str:
    token = f'<input type="hidden" name="csrf" value="{escape(csrf)}">' if csrf else ""
    return f'{token}<input type="hidden" name="then" value="{PATH}">'


def _anchor(module: str) -> str:
    return "cfg-" + "".join(c if c.isalnum() or c == "-" else "-" for c in module)


def _mono(values: list[str]) -> str:
    return " ".join(f'<span class="mono">{escape(v)}</span>' for v in values) if values else "none"


#: Which field shows a failed operation's reason.
_FAILS_UNDER = {"routes": "advertise_routes", "routes-off": "advertise_routes", "rename": "hostname"}


def _tailnet_extra(name: str, tailnet: dict | None, csrf: str | None) -> str:
    """Beside a tailnet-gateway field: the node's live value and the operation that applies
    the saved one, when there is a difference to apply. `tailnet` is None without a writer.
    Always a [data-live] region (possibly empty), so the script can swap it in place."""
    if tailnet is None:
        return ""
    status, saved = tailnet["status"], tailnet["saved"]
    joined = bool(status.get("connected"))
    parts = []
    last = tailnet.get("last") or {}
    if not last.get("ok", True) and _FAILS_UNDER.get(last.get("operation", "")) == name:
        parts.append(f'<p class="fail">{escape(last.get("operation", "").capitalize())} failed: '
                     f'{escape(last.get("message", ""))}</p>')
    if name == "advertise_routes":
        # Resolved by tailnet-gateway, the one authority for what "auto" means.
        wanted = list(status.get("wanted_routes") or [])
        advertised = list(status.get("advertised_routes") or [])
        approved = list(status.get("served_routes") or [])
        parts.append(f"<p>Site networks now: {_mono(list(status.get('site_networks') or []))}. "
                     f"Saved value means: {_mono(wanted)}.</p>")
        if joined:
            parts.append(f"<p>Advertised: {_mono(advertised)}. Approved by the tailnet: {_mono(approved)}.</p>")
            # One slider, a toggle: off while anything is advertised, else advertise the saved
            # routes. Moving from one set to another goes through off.
            if advertised:
                parts.append(op_form("routes-off", "Stop advertising routes", csrf, PATH, slide=True))
            elif wanted:
                parts.append(op_form("routes", f"Advertise {', '.join(wanted)}", csrf, PATH, slide=True))
        else:
            parts.append("<p>Applied when the node joins.</p>")
    elif name == "hostname" and joined:
        want = (saved.get("hostname") or {}).get("value")
        if want and want != status.get("hostname"):
            parts.append(op_form("rename", f"Rename to {want}", csrf, PATH))
    elif name == "tags" and joined:
        want = sorted((saved.get("tags") or {}).get("value") or [])
        if want != sorted(status.get("tags") or []):
            parts.append(f"<p>The node has {_mono(list(status.get('tags') or []))}. Tags change only on "
                         "a join: Leave, then Join (the saved key is reused).</p>")
    return f'<div class="extra" data-live id="live-{escape(name)}">{"".join(parts)}</div>'


def _field(row: dict, csrf: str | None, extra: str = "") -> str:
    field = row.get("field", {})
    kind = field.get("kind", "")
    secret, is_set = field.get("secret"), row.get("is_set")
    label = escape(field.get("label") or row.get("key", ""))
    if secret:
        status = ('<strong class="set">set</strong> <span class="unset">(hidden)</span>' if is_set
                  else '<strong class="unset">not set</strong>')
    elif is_set:
        status = f'<strong class="set">{escape(_shown(row.get("value"), kind))}</strong>'
    else:
        status = (f'<strong class="unset">not set</strong><br><span class="unset">default: '
                  f'{escape(_shown(field.get("default"), kind))}</span>')
    scope = field.get("scope", "")
    scope_title = {"site": "Wiped when the node leaves a site",
                   "node": "Kept between sites"}.get(scope, "")
    warning = field.get("may_disturb")
    tags = [f'<span class="tag kind">{escape("secret" if secret else kind)}</span>',
            f'<span class="tag {escape(scope)}" title="{escape(scope_title)}">{escape(scope)}</span>']
    if secret:
        tags.append('<span class="tag secret">secret</span>')
    if warning:
        tags.append('<span class="tag disturb">may disturb</span>')
    confirm = (f'<label class="warnbox"><input type="checkbox" name="confirm" value="yes"> '
               f'<span>{escape(warning)}</span></label>') if warning else ""
    clear = ""
    if is_set:
        word, cls = ("Delete", "btn del") if secret else ("Reset to default", "btn")
        clear = (f'<form class="aside" method="post" action="{escape(row.get("clear_path", ""))}">'
                 f'{_hidden(csrf)}{confirm}<button class="{cls}" type="submit">{word}</button></form>')
    return f"""<li class="field">
<div class="about"><div class="lab">{label}</div>
<p class="help">{escape(field.get("help", ""))}</p>
<div class="meta">{"".join(tags)}</div></div>
<div class="cur">{status}</div>
<form class="save" method="post" action="{escape(row.get("set_path", ""))}">{_hidden(csrf)}
{_input(field, row)}<button class="btn primary" type="submit">Save</button>{confirm}</form>
<div>{clear}</div>
{extra}</li>"""


def _tailnet(snapshot: dict, rows: list[dict]) -> dict | None:
    """What the tailnet extras need, or None when the writer is off (nothing to apply)."""
    capabilities = {c for n in (snapshot.get("nodes") or {}).values()
                    for m in n.get("modules", []) for c in m.get("capabilities", [])}
    if not rows or TAILNET_WRITER not in capabilities:
        return None
    return {"status": (snapshot.get("tailnet") or {}).get("self") or {},
            "saved": {r.get("field", {}).get("name"): r for r in rows},
            "last": (snapshot.get("tailnet_apply") or {}).get("self")}


def _extra(module: str, row: dict, tailnet: dict | None, csrf: str | None) -> str:
    if module != GATEWAY:
        return ""
    return _tailnet_extra(row.get("field", {}).get("name", ""), tailnet, csrf)


class ConfigPage:
    path = PATH
    content_type = "text/html; charset=utf-8"

    def render(self, snapshot: dict) -> str:
        rows = snapshot.get("config")
        csrf = (snapshot.get("viewer") or {}).get("csrf")
        if rows is None:
            content = '<p class="empty">The config registry is not available on this node.</p>'
        elif not rows:
            content = '<p class="empty">No module declares any config yet.</p>'
        else:
            modules: dict[str, list[dict]] = {}
            for row in rows:
                modules.setdefault(row.get("field", {}).get("module", ""), []).append(row)
            index = [f'<li><a href="#{_anchor(m)}">{escape(m)} <span class="n">{len(g)}</span></a></li>'
                      for m, g in modules.items()]
            tailnet = _tailnet(snapshot, modules.get(GATEWAY, []))
            blocks = "".join(
                f'<section class="block" id="{_anchor(m)}"><header><h2>{escape(m)}</h2>'
                f'<span>{len(g)} field{"s" if len(g) != 1 else ""}</span></header>'
                f'<ul class="fields">{"".join(_field(r, csrf, _extra(m, r, tailnet, csrf)) for r in g)}'
                '</ul></section>'
                for m, g in modules.items())
            # The last tailnet outcome, for the script: a confirmed slide holds until it changes.
            marker = "" if tailnet is None else (
                '<div data-live id="live-last" hidden data-last-apply="'
                f'{escape(str((tailnet.get("last") or {}).get("finished_at", "")))}"></div>')
            content = (f'<div class="cfg"><nav class="index" aria-label="Config sections"><ol>'
                       f'{"".join(index)}</ol></nav><div>{blocks}{marker}'
                       '<p class="keyline"><span class="tag site">site</span> wiped when the node leaves '
                       'a site. <span class="tag node">node</span> kept between sites.</p></div></div>')
        body = ('<div class="cfg-head"><h1>Config</h1><p>Saving only stores a value. A tailnet change '
                'applies with the button beside its field; Join and Leave are on the '
                f'<a href="/">overview</a>.</p></div>{content}')
        status = (snapshot.get("tailnet") or {}).get("self") or {}
        nodes = list((snapshot.get("nodes") or {}).values())
        name = status.get("hostname") or (nodes[0].get("hostname", "") if nodes else "") or "commissioning-node"
        theme = snapshot.get("theme")
        return page(title=f"Config · {name}", body=body, style=STYLE, theme=theme,
                    banner_html=banner(name=name, status=status or None, current=PATH,
                                       viewer=snapshot.get("viewer"), theme=theme))
