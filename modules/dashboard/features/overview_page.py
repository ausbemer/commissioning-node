"""overview-page — the operator's one-page view of the node (config + ops surface).

Server-rendered HTML with no JavaScript: nothing ships unparsed (§8.1). Every value is
HTML-escaped. The only form is "log out" on the fallback door (in the banner). Config is
edited on its own page (`/config`, config-page), which does not auto-refresh under a
half-typed value.

Layout (mockup 3, without its map): a one-line summary, then three columns — the site's
cameras, this gateway, the tailnet streams — and the modules and wire versions folded
under "System".

The gateway column also applies the saved tailnet values (ADR-0005 §3): it offers only the
operations that make sense now (join, rename, routes, leave), each a may-disturb `Action`
with its warning as a required confirm. Leave is a slide-to-confirm where script runs
(ADR-0006); the confirm checkbox and button remain without it.
"""
from __future__ import annotations

import time
from html import escape

from interfaces import __version__ as CONTRACT_VERSION

from ..interface import TAILNET_OPERATIONS, TAILNET_WRITER, banner, page

GATEWAY = "tailnet-gateway"
#: Operations that ask for a slide rather than a tap (ADR-0006).
SLIDE_TO_CONFIRM = {"leave"}

#: Browser auto-refresh, seconds. A display preference, not a behaviour constant.
REFRESH_S = 5

STYLE = """
.headline { display:flex; flex-wrap:wrap; align-items:baseline; justify-content:space-between; gap:6px 24px; }
.headline h1 { font-size:clamp(24px, 4vw, 32px); text-wrap:balance; }
.headline p { margin:4px 0 0; color:var(--muted); max-width:62ch; }
.tick { font-size:12px; color:var(--muted); white-space:nowrap; }
.zones { display:grid; grid-template-columns:1.15fr .85fr 1.15fr; gap:20px; margin-top:28px; }
@media (max-width: 960px) { .zones { grid-template-columns:1fr; } }
.zone { min-width:0; }
.zone > h2 { font-size:20px; display:flex; flex-wrap:wrap; align-items:baseline; gap:4px 10px;
  padding-bottom:8px; border-bottom:2px solid var(--line-strong); }
.zone.gw > h2 { border-bottom-color:var(--ink); }
.zone.tail > h2 { border-bottom-color:var(--accent); }
.zone > h2 small { font:13px system-ui, sans-serif; color:var(--muted); }
.zone .empty { padding:14px 2px; margin:0; }
.rows { list-style:none; margin:0; padding:0; }
.rows > li { padding:14px 2px; border-bottom:1px solid var(--line); display:grid;
  grid-template-columns:minmax(0, 1fr) auto; gap:2px 12px; align-items:start; }
.rows .name { font-weight:600; }
.rows .sub { grid-column:1 / -1; font-size:13px; color:var(--muted); overflow-wrap:anywhere; }
.facts { margin:0; display:grid; grid-template-columns:auto minmax(0, 1fr); gap:0 16px; }
.facts dt, .facts dd { padding:11px 0; border-bottom:1px solid var(--line); margin:0; }
.facts dt { color:var(--muted); font-size:13px; }
.facts dd { overflow-wrap:anywhere; }
.apply { margin-top:18px; display:grid; gap:12px; }
.apply h3.sec { font-size:16px; }
.notes { margin:0; font-size:13px; color:var(--muted); display:grid; gap:6px; }
.notes p { margin:0; padding-left:12px; border-left:2px solid var(--line-strong); }
.notes p.off { border-left-color:var(--warn); color:var(--ink); }
.last { font-size:13px; margin:0; }
.note { margin:14px 0 0; padding:10px 12px; border-left:3px solid var(--line-strong);
  background:var(--surface-2); font-size:13px; color:var(--muted); }
.system { margin-top:36px; border-top:1px solid var(--line); }
.system > header { display:flex; flex-wrap:wrap; align-items:baseline; gap:4px 12px; padding:14px 0;
  color:var(--muted); font-size:14px; }
.system > header h2 { font-size:17px; color:var(--ink); }
.system > header a { margin-left:auto; font-size:13px; }
.sys-grid { display:grid; grid-template-columns:1.4fr 1fr; gap:24px; }
@media (max-width: 860px) { .sys-grid { grid-template-columns:1fr; } }
.ver { font-family:ui-monospace, "SF Mono", "Cascadia Mono", Menlo, monospace; font-size:12px; }
.ver.ok { color:var(--ok); }
.ver.skew { color:var(--crit); background:var(--crit-soft); padding:0 6px; border-radius:3px; font-weight:600; }
"""


def _mono(items: list[str], empty: str = "none") -> str:
    if not items:
        return f'<span class="muted">{escape(empty)}</span>'
    return ", ".join(f'<span class="mono">{escape(i)}</span>' for i in items)


def _table(caption: str, headers: list[str], rows: list[str], empty: str) -> str:
    if not rows:
        return f'<p class="empty"><strong>{escape(caption)}</strong>: {escape(empty)}</p>'
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    return (f'<div class="scroll"><table><caption>{escape(caption)}</caption><thead><tr>{head}</tr>'
            f'</thead><tbody>{"".join(rows)}</tbody></table></div>')


def _summary(status: dict, cameras: list[dict], streams: list[dict]) -> str:
    if not status:
        first = "No tailnet status yet."
    elif status.get("connected"):
        addrs = status.get("addresses") or []
        first = f"On the tailnet as {addrs[0]}." if addrs else "On the tailnet."
        approved = status.get("served_routes") or []
        if approved:
            first += f" Routes approved: {', '.join(approved)}."
    else:
        first = f"Not on a tailnet ({status.get('state') or 'not connected'})."
    serving = sum(1 for s in streams if s.get("live"))
    n = len(cameras)
    cams = f"{n} camera{'s' if n != 1 else ''} known" if n else "No cameras registered"
    return f"{first} {cams}; {serving} of {len(streams)} stream{'s' if len(streams) != 1 else ''} serving."


def _cameras(cameras: list[dict]) -> str:
    if not cameras:
        return '<p class="empty">No cameras registered.</p>'
    items = []
    for c in cameras:
        label, cid = c.get("label") or "", c.get("camera_id", "")
        title = (f'<span class="name">{escape(label)}</span> <span class="mono muted">{escape(cid)}</span>'
                 if label else f'<span class="name mono">{escape(cid)}</span>')
        model, firmware = c.get("model"), c.get("firmware")
        chip = (f'<span class="chip ok">{escape(model)}</span>' if model
                else '<span class="chip none">Not probed</span>')
        detail = (f"firmware {escape(firmware)}" if firmware
                  else "model and firmware not probed" if not model else "firmware not probed")
        items.append(f'<li><span>{title}</span>{chip}<span class="sub">'
                     f'<span class="mono">{escape(c.get("address", ""))}</span>, {detail}</span></li>')
    return f'<ul class="rows">{"".join(items)}</ul>'


def _gateway(status: dict, machine: str) -> str:
    if not status:
        return '<p class="empty">No tailnet status yet.</p>'
    connected = bool(status.get("connected"))
    state = status.get("state") or ("Running" if connected else "unknown")
    chip = (f'<span class="chip ok">{escape(state)}, connected</span>' if connected
            else f'<span class="chip warn">{escape(state)}, not connected</span>')
    advertised = list(status.get("advertised_routes") or [])
    approved = list(status.get("served_routes") or [])
    pending = [r for r in advertised if r not in approved]
    match = ""
    if approved and not pending:
        match = ' <span class="chip ok">all approved</span>'
    elif pending:
        match = f' <span class="chip warn">{len(pending)} awaiting approval</span>'
    facts = [("State", chip),
             ("Name", f'<span class="mono">{escape(status.get("hostname", ""))}</span>')]
    if machine:
        facts.append(("Machine", f'<span class="mono">{escape(machine)}</span>'))
    facts += [("Tailnet IP", _mono(list(status.get("addresses") or []))),
              ("Tags", _mono(list(status.get("tags") or []))),
              ("Advertised", _mono(advertised)),
              ("Approved", _mono(approved) + match)]
    return ('<dl class="facts">' + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts) + "</dl>")


def _hidden(csrf: str | None) -> str:
    token = f'<input type="hidden" name="csrf" value="{escape(csrf)}">' if csrf else ""
    return f'{token}<input type="hidden" name="then" value="/">'


def _operation(operation: str, label: str, csrf: str | None) -> str:
    path, warning = TAILNET_OPERATIONS[operation]
    danger = operation == "leave"
    slide = " data-slide" if operation in SLIDE_TO_CONFIRM else ""
    return (f'<form class="op{" danger" if danger else ""}" method="post" action="{escape(path)}"{slide}>'
            f'{_hidden(csrf)}<h3>{escape(label)}</h3>'
            f'<label class="confirm"><input type="checkbox" name="confirm" value="yes" required> '
            f'<span>{escape(warning)}</span></label>'
            f'<button class="btn {"danger" if danger else "primary"}" type="submit">{escape(label)}</button></form>')


def _apply(snapshot: dict, status: dict) -> str:
    """The operations that make sense now, the notes that explain them, the last result."""
    saved = {r.get("field", {}).get("name"): r for r in snapshot.get("config") or []
             if r.get("field", {}).get("module") == GATEWAY}
    if not saved:
        return ""
    csrf = (snapshot.get("viewer") or {}).get("csrf")
    joined = bool(status.get("connected"))
    node_tags = list(status.get("tags") or [])
    want_name = (saved.get("hostname") or {}).get("value")
    want_tags = list((saved.get("tags") or {}).get("value") or [])
    key_saved = bool((saved.get("auth_key") or {}).get("is_set"))
    # Routes come resolved from tailnet-gateway (the one authority for what "auto" means).
    wanted = list(status.get("wanted_routes") or [])
    advertised = list(status.get("advertised_routes") or [])
    approved = list(status.get("served_routes") or [])
    capabilities = {c for n in (snapshot.get("nodes") or {}).values()
                    for m in n.get("modules", []) for c in m.get("capabilities", [])}
    notes, ops = [], []
    if TAILNET_WRITER not in capabilities:
        notes.append(("off", "The tailnet writer is installed but not enabled yet (ADR-0005: it is "
                             "enabled after a rehearsal join). Join manually for now; these buttons will refuse."))
    if not joined:
        if key_saved:
            ops.append(_operation("join", "Join the tailnet", csrf))
        else:
            notes.append(("", "To join: save a reusable ephemeral auth key on the Config page, then Join here."))
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
    if key_saved:
        notes.append(("", "An auth key is stored and kept, so the node can rejoin after a power cycle. "
                          "It must be reusable and ephemeral; the site teardown wipes it."))
    last = (snapshot.get("tailnet_apply") or {}).get("self")
    result = ""
    if last:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last.get("finished_at") or 0))
        chip = '<span class="chip ok">Done</span>' if last.get("ok") else '<span class="chip crit">Failed</span>'
        result = (f'<p class="last">{chip} {escape(last.get("operation", "").capitalize())} at {escape(when)}, '
                  f'requested by {escape(last.get("requested_by", ""))} via the '
                  f'{escape(last.get("door", ""))} door: {escape(last.get("message", ""))}</p>')
    notes_html = ('<div class="notes">' + "".join(f'<p class="{c}">{escape(t)}</p>' for c, t in notes)
                  + "</div>") if notes else ""
    return f'<div class="apply"><h3 class="sec">Tailnet actions</h3>{notes_html}{"".join(ops)}{result}</div>'


def _streams(streams: list[dict]) -> str:
    if not streams:
        return '<p class="empty">No streams planned.</p>'
    items, planned = [], False
    for s in streams:
        endpoints = [e.get("url", "") for e in s.get("endpoints", [])]
        if not endpoints:
            chip = '<span class="chip warn">No endpoint</span>'
            sub = '<span class="sub">No fresh tailnet address, so no endpoint is offered.</span>'
        else:
            if s.get("live"):
                chip = '<span class="chip ok">Serving</span>'
            else:
                chip, planned = '<span class="chip plan">Planned</span>', True
            sub = "".join(f'<span class="sub mono">{escape(u)}</span>' for u in endpoints)
        items.append(f'<li><span class="name mono">{escape(s.get("camera_id", ""))}</span>{chip}{sub}</li>')
    note = ('<p class="note">Planned means the relay path is set out but MediaMTX is not serving it '
            'yet. A stream only gets an endpoint while the node\'s tailnet address is fresh.</p>'
            if planned else "")
    return f'<ul class="rows">{"".join(items)}</ul>{note}'


#: The query that keeps System open across the auto-refresh (a reload resets <details>).
SYSTEM_OPEN = "open"


def _system(nodes: list[dict], wire: dict, want_open: bool) -> str:
    module_rows = [
        f'<tr><td>{escape(m.get("name", ""))}</td><td class="ver">{escape(m.get("release_version", ""))}</td>'
        f'<td class="ver">{escape(m.get("inward_version", ""))}</td>'
        f'<td>{escape(", ".join(m.get("capabilities", [])) or "none")}</td></tr>'
        for node in nodes for m in node.get("modules", [])]
    skew = 0
    wire_rows = []
    for topic, version in sorted(wire.items()):
        ok = version == CONTRACT_VERSION
        skew += not ok
        mark = '<td class="ver ok">ok</td>' if ok else '<td><span class="ver skew">SKEW</span></td>'
        wire_rows.append(f'<tr><td class="mono">{escape(topic)}</td><td class="ver">{escape(version)}</td>'
                         f'<td class="ver">{escape(CONTRACT_VERSION)}</td>{mark}</tr>')
    n = len(module_rows)
    if not wire_rows:
        wire_line = "nothing received yet"
    elif skew:
        wire_line = f"{skew} of {len(wire_rows)} wire topics at a different version"
    else:
        wire_line = f"all {len(wire_rows)} wire topics match"
    summary = (f"{n} module{'s' if n != 1 else ''}, contract v{escape(CONTRACT_VERSION)}, "
               f"{escape(wire_line)}")
    # A skew is never folded away. Otherwise open is a link, not a <details>: the page
    # reloads itself every few seconds and a reload would close it.
    if skew:
        toggle = ""
    elif want_open:
        toggle = '<a href="/">Hide details</a>'
    else:
        toggle = f'<a href="/?system={SYSTEM_OPEN}">Show details</a>'
    head = f'<header><h2>System</h2><span>{summary}</span>{toggle}</header>'
    if not (skew or want_open):
        return f'<section class="system">{head}</section>'
    return (f'<section class="system" id="system">{head}<div class="sys-grid">'
            + _table("Modules", ["Module", "Release", "Inward contract", "Capabilities"],
                     module_rows, "no node card yet")
            + "<div>" + _table("Wire versions (§8.5)", ["Topic", "Seen", "Pinned", ""], wire_rows,
                               "nothing received yet")
            + '<p class="keyline">A topic seen at a different version than pinned is marked '
              '<span class="ver skew">SKEW</span>.</p></div></div></section>')


class OverviewPage:
    path = "/"
    content_type = "text/html; charset=utf-8"

    def render(self, snapshot: dict) -> str:
        nodes = list(snapshot.get("nodes", {}).values())
        status = (snapshot.get("tailnet") or {}).get("self") or {}
        cameras = list(snapshot.get("cameras", {}).values())
        streams = list(snapshot.get("streams", {}).values())
        machine = nodes[0].get("hostname", "") if nodes else ""
        name = status.get("hostname") or machine or "commissioning-node"
        system_open = (snapshot.get("query") or {}).get("system") == SYSTEM_OPEN
        body = f"""<div class="headline">
<div><h1>{escape(name)}</h1><p>{escape(_summary(status, cameras, streams))}</p></div>
<span class="tick">Refreshes every {REFRESH_S} s</span>
</div>
<div class="zones">
<section class="zone lan"><h2>Cameras <small>on the site network</small></h2>{_cameras(cameras)}</section>
<section class="zone gw"><h2>This gateway <small>tailscaled</small></h2>{_gateway(status, machine)}{_apply(snapshot, status)}</section>
<section class="zone tail"><h2>Streams <small>tailnet endpoints</small></h2>{_streams(streams)}</section>
</div>
{_system(nodes, snapshot.get("wire", {}), system_open)}"""
        theme = snapshot.get("theme")
        return page(title=f"{name} · commissioning-node", body=body, style=STYLE, refresh_s=REFRESH_S,
                    theme=theme, banner_html=banner(
                        name=name, status=status or None, current=self.path, viewer=snapshot.get("viewer"),
                        theme=theme, back=f"/?system={SYSTEM_OPEN}" if system_open else None))
