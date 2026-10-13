"""config-page — the operator's form for the config registry (ADR-0004 §3), at /config.

Server-rendered, no JavaScript, every value escaped. It does not auto-refresh, so a value
being typed is never wiped. A secret is a write-only password input, never pre-filled, and
reads only as set or unset (§8.9). Each form posts to a config `Action`, behind
`authorize()`; on success core sends the browser back here (`then`).

At the top, the Tailnet panel (ADR-0005 §3) compares the node's tailnet state with the saved
values and offers only the operations that make sense now, each with its warning as a
required confirm checkbox.
"""
from __future__ import annotations

import time
from html import escape

from ..interface import TAILNET_OPERATIONS, TAILNET_WRITER

PATH = "/config"
GATEWAY = "tailnet-gateway"


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
    current = "" if not row.get("is_set") or field.get("secret") else _shown(row.get("value"), kind)
    if field.get("secret"):
        return ('<input type="password" name="value" autocomplete="off" spellcheck="false" '
                'placeholder="paste to replace" required>')
    if kind == "bool":
        options = "".join(f'<option value="{v}"{" selected" if current == v else ""}>{v}</option>'
                          for v in ("true", "false"))
        return f'<select name="value">{options}</select>'
    kind_attr = 'type="number"' if kind == "int" else 'type="text" spellcheck="false"'
    return f'<input {kind_attr} name="value" value="{escape(current)}" autocomplete="off">'


def _hidden(csrf: str | None) -> str:
    token = f'<input type="hidden" name="csrf" value="{escape(csrf)}">' if csrf else ""
    return f'{token}<input type="hidden" name="then" value="{PATH}">'


def _field(row: dict, csrf: str | None) -> str:
    field = row.get("field", {})
    secret, is_set = field.get("secret"), row.get("is_set")
    label = escape(field.get("label") or row.get("key", ""))
    if secret:
        status = "set" if is_set else "not set"
    elif is_set:
        status = escape(_shown(row.get("value"), field.get("kind")))
    else:
        status = f'not set (default: {escape(_shown(field.get("default"), field.get("kind")))})'
    warning = field.get("may_disturb")
    confirm = (f'<label class="warn"><input type="checkbox" name="confirm" value="yes"> '
               f'{escape(warning)}</label>') if warning else ""
    clear = ""
    if is_set:
        clear = (f'<form method="post" action="{escape(row.get("clear_path", ""))}">{_hidden(csrf)}'
                 f'{confirm}<button type="submit">{"Delete" if secret else "Reset to default"}'
                 '</button></form>')
    return f"""<section class="field">
<h3>{label} <span class="tag">{escape(field.get("scope", ""))}{" · secret" if secret else ""}</span></h3>
<p class="sub">{escape(field.get("help", ""))}</p>
<p>Current: <strong>{status}</strong></p>
<form method="post" action="{escape(row.get("set_path", ""))}">{_hidden(csrf)}
{_input(field, row)} {confirm}<button type="submit">Save</button></form>
{clear}
</section>"""


def _button(operation: str, label: str, csrf: str | None) -> str:
    path, warning = TAILNET_OPERATIONS[operation]
    return (f'<form method="post" action="{escape(path)}">{_hidden(csrf)}'
            f'<label class="warn"><input type="checkbox" name="confirm" value="yes" required> '
            f'{escape(warning)}</label><button type="submit">{escape(label)}</button></form>')


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

    def shown(items: list[str]) -> str:
        return escape(", ".join(items) or "none")

    state = status.get("state") or ("Running" if joined else "unknown")
    facts = [f"On a tailnet: <strong>{'yes' if joined else 'no'}</strong> ({escape(state)})"]
    if joined:
        facts.append(f"Name: <strong>{escape(status.get('hostname', ''))}</strong>; tags: "
                     f"<strong>{escape(' '.join(node_tags) or 'none')}</strong>")
        facts.append(f"Routes advertised: <strong>{shown(advertised)}</strong>; approved by the "
                     f"tailnet: <strong>{shown(approved)}</strong>")
    facts.append(f"Saved: name <strong>{escape(want_name or 'not set')}</strong>; tags "
                 f"<strong>{escape(' '.join(want_tags) or 'none')}</strong>; auth key "
                 f"<strong>{'saved' if key_saved else 'not saved'}</strong>")
    facts.append(f"Routes to advertise: <strong>{shown(wanted)}</strong> (networks detected now: "
                 f"{shown(detected)}); rejoin automatically: <strong>{'on' if auto_rejoin else 'off'}</strong>")
    notes, buttons = [], []
    if TAILNET_WRITER not in capabilities:
        notes.append("The tailnet writer is installed but not enabled yet (ADR-0005: it is enabled "
                     "after a rehearsal join). Join manually for now; these buttons will refuse.")
    if key_saved:
        notes.append("An auth key is stored and kept, so the node can rejoin after a power cycle. "
                     "It must be reusable and ephemeral; the site teardown wipes it.")
    if not joined:
        if key_saved:
            buttons.append(_button("join", "Join the tailnet", csrf))
        else:
            notes.append("To join: save a reusable ephemeral auth key below, then Join.")
    else:
        if want_name and want_name != status.get("hostname"):
            buttons.append(_button("rename", f"Rename to {want_name}", csrf))
        if sorted(wanted) != sorted(advertised):
            buttons.append(_button("routes", f"Advertise {', '.join(wanted) or 'no routes'}", csrf))
        unapproved = [r for r in advertised if r not in approved]
        if unapproved:
            notes.append(f"Not yet approved by the tailnet: {', '.join(unapproved)}. Approve them in "
                         "the tailnet's admin console (or with an autoApprovers rule).")
        if sorted(want_tags) != sorted(node_tags):
            notes.append("The saved tags differ from the node's. Tags change only on a join: "
                         "Leave, then Join (the saved key is reused).")
        buttons.append(_button("leave", "Leave the tailnet", csrf))

    last = (snapshot.get("tailnet_apply") or {}).get("self")
    result = ""
    if last:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last.get("finished_at") or 0))
        verdict = "done" if last.get("ok") else "failed"
        result = (f'<p class="{"" if last.get("ok") else "warn"}">Last: {escape(last.get("operation", ""))} '
                  f'{verdict} at {escape(when)}, requested by {escape(last.get("requested_by", ""))} '
                  f'via the {escape(last.get("door", ""))} door: {escape(last.get("message", ""))}</p>')
    notes_html = "".join(f'<p class="sub">{escape(n)}</p>' for n in notes)
    return (f'<h2>Tailnet</h2><section class="field">'
            + "".join(f"<p>{f}</p>" for f in facts) + result + notes_html + "".join(buttons)
            + '<p class="sub">Reload this page to see the result of a request.</p></section>')


class ConfigPage:
    path = PATH
    content_type = "text/html; charset=utf-8"

    def render(self, snapshot: dict) -> str:
        rows = snapshot.get("config")
        csrf = (snapshot.get("viewer") or {}).get("csrf")
        if rows is None:
            body = '<p class="empty">The config registry is not available on this node.</p>'
        elif not rows:
            body = '<p class="empty">No module declares any config yet.</p>'
        else:
            modules: dict[str, list[dict]] = {}
            for row in rows:
                modules.setdefault(row.get("field", {}).get("module", ""), []).append(row)
            body = _tailnet_panel(snapshot, rows, csrf) + "".join(
                f"<h2>{escape(module)}</h2>" + "".join(_field(r, csrf) for r in group)
                for module, group in modules.items())
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>commissioning-node · config</title>
<style>
:root {{ --bg:#f7f7f5; --fg:#1d1d1b; --muted:#6b6b66; --line:#d9d9d4; --warn:#a33a00; color-scheme: light; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#18181a; --fg:#ececea; --muted:#9a9a95; --line:#34343a; --warn:#ff9a5c; color-scheme: dark; }}
}}
body {{ background:var(--bg); color:var(--fg); font:14px/1.45 system-ui, sans-serif; margin:0; padding:1.5rem 1rem; }}
main {{ max-width:44rem; margin:0 auto; }}
h1 {{ font-size:1.3rem; margin:0 0 .25rem; }} h2 {{ font-size:1rem; margin:2rem 0 .5rem; }}
h3 {{ font-size:.95rem; margin:0 0 .25rem; }}
.sub, .empty, .tag {{ color:var(--muted); }} .tag {{ font-weight:400; font-size:.85rem; }}
.warn {{ color:var(--warn); display:block; margin:.5rem 0; }}
.field {{ border-top:1px solid var(--line); padding:1rem 0; }}
form {{ margin:.5rem 0 0; }}
input[type=text], input[type=password], input[type=number], select {{ width:100%; max-width:28rem;
  box-sizing:border-box; padding:.45rem; font:inherit; background:var(--bg); color:var(--fg);
  border:1px solid var(--line); border-radius:4px; }}
button {{ margin-top:.5rem; padding:.4rem .9rem; font:inherit; }}
</style></head><body><main>
<h1>Config</h1>
<p class="sub"><a href="/">Back to the overview</a>. Values marked <em>site</em> are wiped when
the node leaves a site. Saving only stores a value; the Tailnet buttons apply the saved ones.</p>
{body}
</main></body></html>
"""
