"""config-page — the operator's form for the config registry (ADR-0004 §3), at /config.

Server-rendered, no JavaScript, every value escaped. It does not auto-refresh, so a value
being typed is never wiped. A secret is a write-only password input, never pre-filled, and
reads only as set or unset (§8.9). Each form posts to a config `Action`, behind
`authorize()`; on success core sends the browser back here (`then`).

At the top, the Tailnet panel (ADR-0005 §3) compares the node's tailnet state with the saved
values and offers only the operations that make sense now, each with its warning as a
required confirm checkbox.

Layout (mockup 3): dense, because this page grows with every module that declares config —
a module index (a side list on wide screens, a strip on phones), one block per module, one
grid row per field.
"""
from __future__ import annotations

import time
from html import escape

from ..interface import TAILNET_OPERATIONS, TAILNET_WRITER, banner, page

PATH = "/config"
GATEWAY = "tailnet-gateway"
TAILNET_ANCHOR = "cfg-tailnet"

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
.block.tailnet { border-color:var(--accent); }
.compare { padding:4px 14px 0; }
.compare td:first-child { color:var(--muted); white-space:nowrap; }
.compare .same { color:var(--ok); font-size:12px; }
.compare .differs { color:var(--warn); font-size:12px; font-weight:600; }
.notes { padding:10px 14px; margin:0; font-size:13px; color:var(--muted); display:grid; gap:6px; }
.notes p { margin:0; padding-left:12px; border-left:2px solid var(--line-strong); }
.notes p.off { border-left-color:var(--warn); color:var(--ink); }
.ops { display:grid; grid-template-columns:repeat(auto-fit, minmax(min(100%, 280px), 1fr)); gap:14px;
  padding:12px 14px 14px; border-top:1px solid var(--line); background:var(--surface-2); border-radius:0 0 10px 10px; }
.op { border:1px solid var(--line-strong); border-radius:8px; padding:10px 12px; background:var(--surface); margin:0; }
.op.danger { border-color:var(--crit); }
.op h3, .last h3 { font-size:15px; margin-bottom:6px; }
.confirm { display:flex; gap:8px; align-items:flex-start; font-size:13px; margin-bottom:10px; }
.confirm input { margin-top:3px; accent-color:var(--accent); flex:none; }
.op.danger .confirm input { accent-color:var(--crit); }
.last { font-size:13px; }
.last p { margin:0; }
.last .hint { color:var(--muted); margin-top:8px; }
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


def _field(row: dict, csrf: str | None) -> str:
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
</li>"""


def _operation(operation: str, label: str, csrf: str | None) -> str:
    path, warning = TAILNET_OPERATIONS[operation]
    danger = operation == "leave"
    return (f'<form class="op{" danger" if danger else ""}" method="post" action="{escape(path)}">'
            f'{_hidden(csrf)}<h3>{escape(label)}</h3>'
            f'<label class="confirm"><input type="checkbox" name="confirm" value="yes" required> '
            f'<span>{escape(warning)}</span></label>'
            f'<button class="btn {"danger" if danger else "primary"}" type="submit">{escape(label)}</button></form>')


def _tailnet_panel(snapshot: dict, rows: list[dict], csrf: str | None) -> str:
    """Apply panel (ADR-0005 §3): the node's tailnet state against the saved values, the
    actions that make sense now, and the last result."""
    saved = {r.get("field", {}).get("name"): r for r in rows
             if r.get("field", {}).get("module") == GATEWAY}
    if not saved:
        return ""
    status = (snapshot.get("tailnet") or {}).get("self") or {}
    joined = bool(status.get("connected"))
    node_tags = list(status.get("tags") or [])
    want_name = (saved.get("hostname") or {}).get("value")
    want_tags = list((saved.get("tags") or {}).get("value") or [])
    key_saved = bool((saved.get("auth_key") or {}).get("is_set"))
    auto_rejoin = (saved.get("auto_rejoin") or {}).get("value")
    # Routes come resolved from tailnet-gateway (the one authority for what "auto" means).
    wanted = list(status.get("wanted_routes") or [])
    advertised = list(status.get("advertised_routes") or [])
    approved = list(status.get("served_routes") or [])
    detected = list(status.get("site_networks") or [])
    capabilities = {c for n in (snapshot.get("nodes") or {}).values()
                    for m in n.get("modules", []) for c in m.get("capabilities", [])}

    def mono(items: list[str]) -> str:
        if not items:
            return '<span class="muted">none</span>'
        return " ".join(f'<span class="mono">{escape(i)}</span>' for i in items)

    def verdict(same: bool) -> str:
        return '<td class="same">same</td>' if same else '<td class="differs">differs</td>'

    state = status.get("state") or ("Running" if joined else "unknown")
    on_tailnet = (f'<span class="chip ok">yes</span> {escape(state)}' if joined
                  else f'<span class="chip warn">no</span> {escape(state)}')
    rows_html = [f'<tr><td>On a tailnet</td><td>{on_tailnet}</td><td></td><td></td></tr>']
    if joined:
        rows_html += [
            f'<tr><td>Name</td><td>{mono([status.get("hostname", "")])}</td>'
            f'<td>{mono([want_name] if want_name else [])}</td>'
            + (verdict(want_name == status.get("hostname")) if want_name else "<td></td>") + "</tr>",
            f'<tr><td>Tags</td><td>{mono(node_tags)}</td><td>{mono(want_tags)}</td>'
            + verdict(sorted(want_tags) == sorted(node_tags)) + "</tr>",
            f'<tr><td>Routes</td><td>advertised {mono(advertised)}<br>approved by the tailnet {mono(approved)}</td>'
            f'<td>to advertise {mono(wanted)}<br><span class="muted">detected now</span> {mono(detected)}</td>'
            + verdict(sorted(wanted) == sorted(advertised)) + "</tr>",
        ]
    else:
        rows_html += [
            f'<tr><td>Name</td><td class="muted">n/a</td><td>{mono([want_name] if want_name else [])}</td><td></td></tr>',
            f'<tr><td>Tags</td><td class="muted">n/a</td><td>{mono(want_tags)}</td><td></td></tr>',
            f'<tr><td>Routes</td><td class="muted">n/a</td><td>to advertise {mono(wanted)}<br>'
            f'<span class="muted">detected now</span> {mono(detected)}</td><td></td></tr>',
        ]
    rows_html += [
        f'<tr><td>Auth key</td><td class="muted">n/a</td><td>{"saved, kept for rejoin" if key_saved else "not saved"}</td><td></td></tr>',
        f'<tr><td>Rejoin automatically</td><td class="muted">n/a</td><td>{"on" if auto_rejoin else "off"}</td><td></td></tr>',
    ]
    compare = ('<div class="compare scroll"><table><thead><tr><th></th><th>On the node</th><th>Saved</th>'
               f'<th></th></tr></thead><tbody>{"".join(rows_html)}</tbody></table></div>')

    notes, ops = [], []
    if TAILNET_WRITER not in capabilities:
        notes.append(("off", "The tailnet writer is installed but not enabled yet (ADR-0005: it is "
                             "enabled after a rehearsal join). Join manually for now; these buttons will refuse."))
    if key_saved:
        notes.append(("", "An auth key is stored and kept, so the node can rejoin after a power cycle. "
                          "It must be reusable and ephemeral; the site teardown wipes it."))
    if not joined:
        if key_saved:
            ops.append(_operation("join", "Join the tailnet", csrf))
        else:
            notes.append(("", "To join: save a reusable ephemeral auth key below, then Join."))
    else:
        if want_name and want_name != status.get("hostname"):
            ops.append(_operation("rename", f"Rename to {want_name}", csrf))
        if sorted(wanted) != sorted(advertised):
            ops.append(_operation("routes", f"Advertise {', '.join(wanted) or 'no routes'}", csrf))
        unapproved = [r for r in advertised if r not in approved]
        if unapproved:
            notes.append(("off", f"Not yet approved by the tailnet: {', '.join(unapproved)}. Approve them in "
                                 "the tailnet's admin console (or with an autoApprovers rule)."))
        if sorted(want_tags) != sorted(node_tags):
            notes.append(("off", "The saved tags differ from the node's. Tags change only on a join: "
                                 "Leave, then Join (the saved key is reused)."))
        ops.append(_operation("leave", "Leave the tailnet", csrf))

    last = (snapshot.get("tailnet_apply") or {}).get("self")
    if last:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last.get("finished_at") or 0))
        chip = '<span class="chip ok">Done</span>' if last.get("ok") else '<span class="chip crit">Failed</span>'
        result = (f'<p>{chip} {escape(last.get("operation", "").capitalize())} at {escape(when)}, '
                  f'requested by {escape(last.get("requested_by", ""))} via the '
                  f'{escape(last.get("door", ""))} door: {escape(last.get("message", ""))}</p>')
    else:
        result = '<p class="muted">Nothing applied since the node started.</p>'
    result = (f'<div class="last"><h3>Last result</h3>{result}'
              '<p class="hint">Reload this page to see the result of a request.</p></div>')
    notes_html = ('<div class="notes">'
                  + "".join(f'<p class="{cls}">{escape(text)}</p>' for cls, text in notes)
                  + "</div>") if notes else ""
    return (f'<section class="block tailnet" id="{TAILNET_ANCHOR}"><header><h2>Tailnet</h2>'
            '<span>What is on the node now, against what is saved</span></header>'
            f'{compare}{notes_html}<div class="ops">{"".join(ops)}{result}</div></section>')


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
            panel = _tailnet_panel(snapshot, rows, csrf)
            index = ([f'<li><a href="#{TAILNET_ANCHOR}">Tailnet <span class="n">apply</span></a></li>']
                     if panel else [])
            index += [f'<li><a href="#{_anchor(m)}">{escape(m)} <span class="n">{len(g)}</span></a></li>'
                      for m, g in modules.items()]
            blocks = "".join(
                f'<section class="block" id="{_anchor(m)}"><header><h2>{escape(m)}</h2>'
                f'<span>{len(g)} field{"s" if len(g) != 1 else ""}</span></header>'
                f'<ul class="fields">{"".join(_field(r, csrf) for r in g)}</ul></section>'
                for m, g in modules.items())
            content = (f'<div class="cfg"><nav class="index" aria-label="Config sections"><ol>'
                       f'{"".join(index)}</ol></nav><div>{panel}{blocks}'
                       '<p class="keyline"><span class="tag site">site</span> wiped when the node leaves '
                       'a site. <span class="tag node">node</span> kept between sites.</p></div></div>')
        body = (f'<div class="cfg-head"><h1>Config</h1><p>Saving only stores a value; the Tailnet '
                f'panel applies the saved values.</p></div>{content}')
        status = (snapshot.get("tailnet") or {}).get("self") or {}
        nodes = list((snapshot.get("nodes") or {}).values())
        name = status.get("hostname") or (nodes[0].get("hostname", "") if nodes else "") or "commissioning-node"
        theme = snapshot.get("theme")
        return page(title=f"Config · {name}", body=body, style=STYLE, theme=theme,
                    banner_html=banner(name=name, status=status or None, current=PATH,
                                       viewer=snapshot.get("viewer"), theme=theme))
