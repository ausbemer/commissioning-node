"""overview-page — the operator's one-page view of the node (config + ops surface).

Server-rendered HTML with no JavaScript: nothing ships unparsed (§8.1). Every value is
HTML-escaped. The only form is "log out" on the fallback door (in the banner). Config is
edited on its own page (`/config`, config-page), which does not auto-refresh under a
half-typed value.

Layout (mockup 3): a one-line summary, the site map (ADR-0007), then three columns — the
site's cameras, this gateway, the tailnet streams — and the modules and wire versions folded
under "System".

The site map draws the tailnet, this gateway, and each site network as a bus with every host
the last operator-run scan found hanging off it. It claims only what the scan saw and when
("Scanned 4 min ago"), never that a device is up. Flags come from their authorities: a
registered camera from camera-config, "Axis by maker" from site-survey's MAC prefix, a
tailnet peer's traffic from tailnet-gateway (a fresh peer whose direct endpoint is that address:
the peer, or a NAT in front of it). The
Scan slider beside the age is a may-disturb action; nothing scans on its own.

The gateway column holds one tailnet control (ADR-0005 §3): Leave the Tailnet when joined,
Join the Tailnet when not (with a saved key). Each is a may-disturb `Action`, a
slide-to-confirm where script runs (ADR-0006); without it, the warning is a required confirm
beside a button. No notes: Rename and Routes are applied on /config beside their fields, and
the last result is one row of the gateway's facts.
"""
from __future__ import annotations

import ipaddress
import time
from html import escape

from interfaces import __version__ as CONTRACT_VERSION

from ..interface import SITE_SCANNER, TAILNET_WRITER, banner, op_form, page

GATEWAY = "tailnet-gateway"
#: Browser auto-refresh, seconds. A display preference, not a behaviour constant.
REFRESH_S = 5
#: Site map display preferences (ADR-0007 D5), not behaviour constants: tiles drawn per
#: network before unflagged hosts fold into one, and the age past which a scan reads as old.
MAP_TILES = 24
SCAN_OLD_S = 3600
#: How long a requested scan reads as running without a result: site-survey's total timeout
#: (300 s) and kill margin (10 s), plus a tick to publish it. After that the map says no result
#: came, rather than "Scanning" forever.
SCAN_AWAIT_S = 320

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
.apply { margin:18px 0 0; }
.facts .sub { display:block; margin-top:4px; font-size:13px; color:var(--muted); }
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
.site { margin-top:28px; }
.site-head { display:flex; flex-wrap:wrap; align-items:center; gap:8px 20px; padding-bottom:8px;
  border-bottom:2px solid var(--line-strong); }
.site-head h2 { font-size:20px; display:flex; align-items:baseline; gap:10px; }
.site-head h2 small { font:13px system-ui, sans-serif; color:var(--muted); }
.site-head .age { margin:0; color:var(--muted); font-size:14px; flex:1 1 280px; }
.site-head .age .fail { display:block; color:var(--crit); }
.site-head .scan { flex:0 1 320px; min-width:240px; }
.map { display:flex; flex-direction:column; align-items:center; margin-top:18px; }
.node { border:2px solid var(--line-strong); border-radius:10px; padding:8px 16px; background:var(--surface);
  text-align:center; display:flex; flex-direction:column; gap:1px; min-width:220px; max-width:100%; }
.node .k { font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.08em; }
.node.tailnet { border-color:var(--accent); background:var(--accent-soft); border-radius:999px; }
.node.gw { border-color:var(--ink); }
.wire { width:3px; height:22px; background:var(--line-strong); flex:none; }
.wire.ok { background:var(--ok); }
.wire.warn { background:repeating-linear-gradient(var(--warn) 0 5px, transparent 5px 9px); }
.wire.none { background:repeating-linear-gradient(var(--line-strong) 0 2px, transparent 2px 6px); }
.lan { align-self:stretch; }
.bus { display:flex; align-items:center; gap:10px; }
.bus::before, .bus::after { content:""; height:4px; background:var(--ink); border-radius:2px; flex:1; }
.bus .cidr { font-size:13px; font-weight:600; }
.tiles { list-style:none; margin:0; padding:14px 12px 4px; display:grid; gap:12px;
  grid-template-columns:repeat(auto-fill, minmax(168px, 1fr)); border:1px solid var(--line);
  border-top:0; border-radius:0 0 10px 10px; background:var(--surface-2); }
.tiles .empty { grid-column:1 / -1; padding:6px 2px 10px; margin:0; }
.tile { border:1px solid var(--line-strong); border-radius:8px; background:var(--surface); padding:8px 10px;
  min-width:0; display:flex; flex-direction:column; gap:2px; }
.tile .addr { font-size:13px; }
.tile .what { font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.tile .tags { display:flex; flex-wrap:wrap; gap:4px; margin-top:2px; }
.tile .tags .chip { font-size:11px; }
.tile.cam { border:2px solid var(--accent); }
.tile.tail { border:2px solid var(--ok); }
.tile.arp { border-style:dashed; }
.tile.missing { border-style:dotted; color:var(--muted); }
.tile.fold { justify-content:center; align-items:center; color:var(--muted); border-style:dashed; }
.site.old .tiles, .site.old .node { opacity:.6; }
.maplegend { list-style:none; display:flex; flex-wrap:wrap; gap:4px 18px; margin:10px 2px 0; padding:0;
  font-size:12px; color:var(--muted); }
.maplegend i { display:inline-block; width:22px; height:12px; border:1px solid var(--line-strong);
  border-radius:3px; vertical-align:-2px; margin-right:6px; }
.maplegend i.cam { border:2px solid var(--accent); }
.maplegend i.tail { border:2px solid var(--ok); }
.maplegend i.arp { border-style:dashed; }
.maplegend i.missing { border-style:dotted; }
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


def _last(last: dict | None) -> str:
    """The last tailnet operation, as one fact: what, when, by whom; the reason if it failed."""
    if not last:
        return ""
    when = time.strftime("%H:%M:%S", time.localtime(last.get("finished_at") or 0))
    chip = '<span class="chip ok">Done</span>' if last.get("ok") else '<span class="chip crit">Failed</span>'
    why = "" if last.get("ok") else f'<span class="sub">{escape(last.get("message", ""))}</span>'
    return (f'{chip} {escape(last.get("operation", "").capitalize())} at {escape(when)}, by '
            f'{escape(last.get("requested_by", ""))}{why}')


def _gateway(status: dict, machine: str, last: dict | None = None) -> str:
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
    if last:
        facts.append(("Last action", _last(last)))
    return ('<dl class="facts">' + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts) + "</dl>")


def last_marker(snapshot: dict) -> str:
    """When the last tailnet outcome finished: the script holds a confirmed slide until it
    changes (or the form goes), so a stale page never shows the old slider again."""
    last = (snapshot.get("tailnet_apply") or {}).get("self") or {}
    return f'<span hidden data-last-apply="{escape(str(last.get("finished_at", "")))}"></span>'


def _capabilities(snapshot: dict) -> set[str]:
    return {c for n in (snapshot.get("nodes") or {}).values()
            for m in n.get("modules", []) for c in m.get("capabilities", [])}


def _apply(snapshot: dict, status: dict) -> str:
    """The one tailnet control that makes sense now: Leave when joined, Join when not."""
    saved = {r.get("field", {}).get("name"): r for r in snapshot.get("config") or []
             if r.get("field", {}).get("module") == GATEWAY}
    if not saved:
        return ""
    if TAILNET_WRITER not in _capabilities(snapshot):
        return '<p class="apply"><span class="chip none">Tailnet writer off</span></p>'
    csrf = (snapshot.get("viewer") or {}).get("csrf")
    if status.get("connected"):
        return f'<div class="apply">{op_form("leave", "Leave the Tailnet", csrf, "/", slide=True)}</div>'
    if (saved.get("auth_key") or {}).get("is_set"):
        return f'<div class="apply">{op_form("join", "Join the Tailnet", csrf, "/", slide=True)}</div>'
    return '<p class="apply"><a href="/config#cfg-tailnet-gateway">Save an auth key</a> to join.</p>'


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


# -- the site map (ADR-0007 D5) ---------------------------------------------------------------

def _ago(seconds: float) -> str:
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    return f"{int(seconds // 3600)} h ago"


def _clock(at: float) -> str:
    return time.strftime("%H:%M", time.localtime(at))


def _in(address: str, network: str) -> bool:
    try:
        return ipaddress.ip_address(address) in ipaddress.ip_network(network, strict=False)
    except ValueError:
        return False


def _tile(host: dict, camera: dict | None, peer: dict | None = None) -> tuple[bool, str]:
    """One host as a tile: (flagged, html). Flagged hosts are never folded away."""
    address = host.get("address", "")
    classes, tags = ["tile"], []
    if camera is not None:
        classes.append("cam")
        label = camera.get("label") or camera.get("camera_id", "")
        tags.append(f'<span class="chip ok">Registered: {escape(label)}</span>')
    if host.get("axis_maker"):
        if camera is None:
            classes.append("cam")
        tags.append('<span class="chip plan">Axis by maker</span>')
    if peer is not None:
        # Where the peer's tailnet traffic comes from now: the peer itself, or a NAT in front
        # of it (quirks Q-008). Not proof of what the device is, so it never outranks a camera.
        if camera is None and not host.get("axis_maker"):
            classes.append("tail")
        node = peer.get("node_name", "")
        tags.append(f'<span class="chip ok">Tailnet traffic: {escape(node.split(".")[0])}</span>')
    if host.get("missing"):
        classes.append("missing")
        tags.append('<span class="chip warn">Did not answer</span>')
    elif host.get("found_by") == "arp":
        classes.append("arp")
        tags.append('<span class="chip none">ARP only</span>')
    ports = host.get("open_ports") or []
    if ports:
        tags.append(f'<span class="mono muted">{escape(" ".join(str(p) for p in ports))}</span>')
    what = host.get("hostname") or host.get("maker") or ("Registered camera" if camera else "Unknown device")
    detail = " · ".join(x for x in (host.get("hostname"), host.get("maker"), host.get("mac"),
                                    (peer or {}).get("node_name")) if x)
    title = f' title="{escape(detail)}"' if detail else ""
    return (camera is not None or peer is not None or bool(host.get("axis_maker")),
            f'<li class="{" ".join(classes)}"{title}><span class="addr mono">{escape(address)}</span>'
            f'<span class="what">{escape(what)}</span><span class="tags">{"".join(tags)}</span></li>')


def _network(network: str, hosts: list[dict], cameras: dict[str, dict], scanned: bool,
             peers: dict[str, dict] | None = None) -> tuple[str, list[dict]]:
    """One site network: its bus and tiles. Returns the html and the hosts folded away."""
    mine = [h for h in hosts if _in(h.get("address", ""), network)]
    seen = {h.get("address") for h in mine}
    # A registered camera the scan didn't find still belongs on its network, marked as such.
    mine += [{"address": a, "missing": scanned} for a in cameras if _in(a, network) and a not in seen]
    mine.sort(key=lambda h: ipaddress.ip_address(h.get("address", "0.0.0.0")))
    tiles = [_tile(h, cameras.get(h.get("address", "")), (peers or {}).get(h.get("address", ""))) for h in mine]
    flagged = [t for f, t in tiles if f]
    rest = [(h, t) for h, (f, t) in zip(mine, tiles) if not f]
    folded: list[dict] = []
    if len(flagged) + len(rest) > MAP_TILES:
        keep = max(0, MAP_TILES - 1 - len(flagged))
        folded = [h for h, _ in rest[keep:]]
        rest = rest[:keep]
    items = flagged + [t for _, t in rest]
    if folded:
        items.append(f'<li class="tile fold"><a href="#site-hosts">{len(folded)} other '
                     f'host{"s" if len(folded) != 1 else ""}</a></li>')
    if not items:
        items = [f'<li class="empty muted">{"No host answered on this network." if scanned else "Not scanned yet."}</li>']
    return (f'<div class="lan"><div class="bus"><span class="cidr mono">{escape(network)}</span></div>'
            f'<ul class="tiles">{"".join(items)}</ul></div>'), folded


def scan_marker(snapshot: dict) -> str:
    """The last scan request and result: a confirmed Scan slide holds until either changes (a
    refusal is a new result; an accepted request turns the slider into "Scanning")."""
    req = (snapshot.get("site_request") or {}).get("self") or {}
    last = (snapshot.get("site_scan") or {}).get("self") or {}
    mark = f'{req.get("request_id", "")}:{last.get("finished_at", "")}'
    return f'<span hidden data-last-scan="{escape(mark)}"></span>'


def _site_map(snapshot: dict, status: dict, cameras: list[dict], name: str, now: float) -> str:
    scan = (snapshot.get("site_hosts") or {}).get("self")
    result = (snapshot.get("site_scan") or {}).get("self")
    by_address = {c.get("address", ""): c for c in cameras if c.get("address")}
    moved = False
    if scan:
        age = now - (scan.get("scanned_at") or 0)
        n, nets = len(scan.get("hosts") or []), ", ".join(scan.get("networks") or [])
        line = (f'Scanned {_ago(age)} ({_clock(scan.get("scanned_at") or 0)}) by '
                f'{escape(scan.get("requested_by") or "unknown")}: {n} host{"s" if n != 1 else ""} '
                f'answered on {escape(nets)}.')
        networks, hosts = list(scan.get("networks") or []), list(scan.get("hosts") or [])
        own = list(scan.get("self_addresses") or [])
        now_on = list(status.get("site_networks") or [])
        if status and sorted(now_on) != sorted(networks):
            # The node is somewhere else now (a move, a DHCP change): the scan is history, and
            # today's peer endpoints must not be pinned onto its hosts.
            line += (f'<span class="fail">The node is on {escape(", ".join(now_on) or "no site network")} '
                     'now; this scan is from before. Scan again.</span>')
            moved = True
    else:
        age, line = 0.0, "Not scanned yet. A scan probes the site network once, when you ask."
        networks, hosts, own = list(status.get("site_networks") or []), [], []
    if result and not result.get("ok") and (result.get("finished_at") or 0) >= ((scan or {}).get("scanned_at") or 0):
        line += (f'<span class="fail">Last scan at {_clock(result.get("finished_at") or 0)}, by '
                 f'{escape(result.get("requested_by", ""))}: {escape(result.get("message", ""))}</span>')
    # A request with no result of its own yet: running, for as long as a scan can take.
    req = (snapshot.get("site_request") or {}).get("self")
    pending = bool(req) and (result or {}).get("request_id") != req.get("request_id")
    since = (req or {}).get("received_at") or 0
    if pending and now - since > SCAN_AWAIT_S:
        line += (f'<span class="fail">No result for the scan requested at {_clock(since)} by '
                 f'{escape(req.get("requested_by", ""))}; see the journal.</span>')
        pending = False
    control = ""
    if pending:
        control = (f'<span class="chip plan">Scanning since {_clock(since)}, by '
                   f'{escape(req.get("requested_by", ""))}</span>')
    elif SITE_SCANNER in _capabilities(snapshot):
        csrf = (snapshot.get("viewer") or {}).get("csrf")
        control = op_form("scan", "Scan the site network", csrf, "/", slide=True, marker="data-last-scan")
    elif snapshot.get("nodes"):
        control = '<span class="chip none">Scanner off</span>'

    if not status:
        wire, tail = "none", '<span class="muted">no status yet</span>'
    elif status.get("connected"):
        addrs = list(status.get("addresses") or [])
        wire, tail = "ok", f'<span class="mono">{escape(addrs[0]) if addrs else "connected"}</span>'
    else:
        wire, tail = "warn", f'<span>{escape(status.get("state") or "not connected")}</span>'
    lans, folded = [], []
    for network in networks:
        html, more = _network(network, hosts, by_address, bool(scan),
                              {} if moved else snapshot.get("tailnet_endpoints") or {})
        lans.append(f'<div class="wire ok" aria-hidden="true"></div>{html}')
        folded += more
    if not networks:
        lans.append('<div class="wire none" aria-hidden="true"></div>'
                    '<p class="muted">The node is on no site network.</p>')
    gw_addr = f'<span class="mono">{escape(", ".join(own))}</span>' if own else ""
    table = ""
    if folded:
        rows = [f'<tr><td class="mono">{escape(h.get("address", ""))}</td><td>{escape(h.get("hostname") or "")}</td>'
                f'<td>{escape(h.get("maker") or "")}</td><td class="mono">{escape(h.get("mac") or "")}</td>'
                f'<td class="mono">{escape(" ".join(str(p) for p in h.get("open_ports") or []))}</td>'
                f'<td>{escape(h.get("found_by", ""))}</td></tr>' for h in folded]
        table = ('<div id="site-hosts">' + _table("Other hosts", ["Address", "Hostname", "Maker", "MAC",
                                                                  "Open ports", "Found by"], rows, "none")
                 + "</div>")
    old = " old" if scan and (age > SCAN_OLD_S or moved) else ""
    return (f'<section class="site{old}" aria-labelledby="site-h"><div class="site-head">'
            f'<h2 id="site-h">Site network <small>last scan</small></h2><p class="age">{line}</p>'
            f'<div class="scan">{control}</div></div>{scan_marker(snapshot)}'
            f'<div class="map"><div class="node tailnet"><span class="k">Tailnet</span>{tail}</div>'
            f'<div class="wire {wire}" aria-hidden="true"></div>'
            f'<div class="node gw"><span class="k">This gateway</span><strong>{escape(name)}</strong>{gw_addr}</div>'
            f'{"".join(lans)}</div>'
            '<ul class="maplegend"><li><i class="cam"></i>Camera: registered, or Axis by maker</li>'
            '<li><i class="tail"></i>Tailnet traffic comes from here now (the peer, or a NAT in front of it)</li>'
            '<li><i class="arp"></i>Answered ARP only</li><li><i class="missing"></i>Registered, did not answer</li>'
            f'<li>A scan shows who answered then, not who is up now.</li></ul>{table}</section>')


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
{_site_map(snapshot, status, cameras, name, snapshot.get("now") or time.time())}
<div class="zones">
<section class="zone lan"><h2>Cameras <small>on the site network</small></h2>{_cameras(cameras)}</section>
<section class="zone gw"><h2>This gateway <small>tailscaled</small></h2>{_gateway(status, machine, (snapshot.get("tailnet_apply") or {}).get("self"))}{_apply(snapshot, status)}{last_marker(snapshot)}</section>
<section class="zone tail"><h2>Streams <small>tailnet endpoints</small></h2>{_streams(streams)}</section>
</div>
{_system(nodes, snapshot.get("wire", {}), system_open)}"""
        theme = snapshot.get("theme")
        return page(title=f"{name} · commissioning-node", body=body, style=STYLE, refresh_s=REFRESH_S,
                    theme=theme, banner_html=banner(
                        name=name, status=status or None, current=self.path, viewer=snapshot.get("viewer"),
                        theme=theme, back=f"/?system={SYSTEM_OPEN}" if system_open else None))
