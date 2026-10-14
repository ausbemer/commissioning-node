"""nmap + neighbour-table adapter for the NetworkScanner seam (ADR-0007 D2).

The only code in the project that runs nmap. Core calls it from the scan worker, one scan at a
time, never on the bus thread. It sends traffic to devices the site owns, so it is gentle by
construction:

- `--unprivileged` connect scan of `PORTS` only, discovery on the same ports; no version
  probes, no scripts, no UDP. A rate cap and a per-host timeout; one total timeout after which
  nmap's process group is terminated, then killed. Output is read in chunks and never held
  past `OUTPUT_CAP`.
- Networks are re-checked here (`scan_problem`, rung 13), and passed as validated CIDRs in an
  argument list, never through a shell. The node's own addresses are excluded.
- Afterwards `ip -s -j -4 neigh` supplies the MACs, and the hosts that answered ARP but dropped
  every probe (quirks Q-006). Only entries confirmed during this scan count: Linux keeps STALE
  entries for long-gone devices while the table is small, so an older entry proves nothing. The maker comes from nmap's own prefix table, longest prefix
  first; a locally administered MAC is "private MAC" (Q-007).
- Hosts are site data: returned, never written anywhere (ADR-0007 Safety 6). Hostnames come
  from the site's resolver and are untrusted text: control characters are dropped and the
  length capped here; every surface escapes them too.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import selectors
import shutil
import signal
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable

from interfaces import SiteHost

from ..interface import MODULE, PORTS, ScanReply, scan_problem

log = logging.getLogger(MODULE)

#: PROVISIONAL — tools/ledgers/site-survey/ (§8.11).
MAX_RATE = 100                 # packets per second
HOST_TIMEOUT_S = 15            # nmap --host-timeout
SCAN_TIMEOUT_S = 300           # the whole scan, then nmap is terminated
KILL_MARGIN_S = 10             # after terminate, before kill
#: Bytes of nmap output read (a full /22 is about 1 MB of XML), and hosts kept (a /22).
OUTPUT_CAP = 4 * 1024 * 1024
MAX_HOSTS = 1022
#: Upper bound on one `ip` call, seconds (a local netlink read, as tailnet-gateway's).
IP_TIMEOUT_S = 2.0
#: Characters kept of a hostname (DNS's own limit) and of nmap's error text.
HOSTNAME_CAP = 253
MESSAGE_CAP = 400

MAC_PREFIXES = "/usr/share/nmap/nmap-mac-prefixes"
PRIVATE_MAC = "private MAC"
#: Neighbour states that mean the address never answered ARP.
_DEAD = {"FAILED", "INCOMPLETE"}


#: Seconds of slack on "confirmed during the scan": the neighbour read follows nmap's exit.
CONFIRM_SLACK_S = 5


@dataclass
class NmapRun:
    returncode: int
    stdout: bytes
    stderr: bytes
    #: Wall seconds the run took; the neighbour table is read against it.
    elapsed_s: float = 0.0


class OutputTooLarge(Exception):
    pass


def run_nmap(argv: list[str], timeout_s: float | None = None, cap: int | None = None) -> NmapRun:
    """Run nmap in its own process group with the total timeout: terminate the group, then
    kill it. Output is read in chunks; past `cap` the group is killed and OutputTooLarge
    raised. Raises TimeoutExpired when it had to be stopped."""
    timeout_s = SCAN_TIMEOUT_S if timeout_s is None else timeout_s
    cap = OUTPUT_CAP if cap is None else cap
    started = time.monotonic()
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    out, err = bytearray(), bytearray()
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ, out)
    sel.register(proc.stderr, selectors.EVENT_READ, err)
    try:
        while sel.get_map():
            left = started + timeout_s - time.monotonic()
            if left <= 0:
                _stop(proc)
                raise subprocess.TimeoutExpired(argv, timeout_s)
            for key, _ in sel.select(timeout=min(left, 1.0)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    sel.unregister(key.fileobj)
                    continue
                key.data.extend(chunk)
                if len(out) > cap:
                    _stop(proc)
                    raise OutputTooLarge(f"more than {cap} bytes of output")
                del err[:-MESSAGE_CAP]
        proc.wait(timeout=max(0.1, started + timeout_s - time.monotonic()))
    except subprocess.TimeoutExpired:
        _stop(proc)
        raise
    finally:
        sel.close()
        proc.stdout.close()
        proc.stderr.close()
    return NmapRun(proc.returncode, bytes(out), bytes(err), time.monotonic() - started)


def _stop(proc: subprocess.Popen) -> None:
    """Terminate nmap's whole process group, then kill it after KILL_MARGIN_S. Never blocks
    longer than that: a child holding a pipe can't keep the worker waiting."""
    for sig, wait in ((signal.SIGTERM, KILL_MARGIN_S), (signal.SIGKILL, 1.0)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def nmap_argv(binary: str, networks: list[str], exclude: list[str]) -> list[str]:
    ports = ",".join(str(p) for p in PORTS)
    argv = [binary, "--unprivileged", "-sT", f"-PS{ports}", "-p", ports,
            "--max-rate", str(MAX_RATE), "--max-retries", "1",
            "--host-timeout", f"{HOST_TIMEOUT_S}s", "-T3", "-oX", "-"]
    if exclude:
        argv += ["--exclude", ",".join(exclude)]
    return argv + list(networks)


def parse_nmap_xml(data: bytes) -> dict[str, SiteHost]:
    """Every host nmap reports up, keyed by address. Only top-level <host> elements count:
    nmap also writes <hosthint> elements with the same addresses. Raises ET.ParseError."""
    root = ET.fromstring(data)
    hosts: dict[str, SiteHost] = {}
    for host in root.findall("host"):
        status = host.find("status")
        addr = host.find("address[@addrtype='ipv4']")
        if status is None or status.get("state") != "up" or addr is None:
            continue
        name = host.find("hostnames/hostname[@type='PTR']")
        ports = sorted(int(p.get("portid")) for p in host.findall("ports/port")
                       if p.get("protocol") == "tcp" and (p.find("state") is not None)
                       and p.find("state").get("state") == "open"
                       and str(p.get("portid", "")).isdigit())
        address = addr.get("addr", "")
        hosts[address] = SiteHost(address=address, hostname=clean_hostname(name.get("name") if name is not None else None),
                                  open_ports=[p for p in ports if p in PORTS], found_by="tcp")
    return hosts


def clean_hostname(name: str | None) -> str | None:
    if not name:
        return None
    text = "".join(c for c in name if c.isprintable()).strip()[:HOSTNAME_CAP]
    return text or None


def load_prefixes(path: str = MAC_PREFIXES) -> dict[str, str]:
    """nmap's MAC prefix table: hex prefix (6, 7 or 9 digits) -> maker. Empty if unreadable."""
    table: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("#") or " " not in line:
                    continue
                prefix, maker = line.rstrip("\n").split(" ", 1)
                table[prefix.upper()] = maker.strip()
    except OSError as exc:
        log.warning("MAC prefix table unreadable (%s); makers will be unknown", exc)
    return table


def maker_for(mac: str | None, table: dict[str, str]) -> str | None:
    if not mac:
        return None
    digits = mac.replace(":", "").replace("-", "").upper()
    if len(digits) != 12:
        return None
    if int(digits[:2], 16) & 0x02:
        return PRIVATE_MAC
    for n in (9, 7, 6):                  # longest prefix first (MA-S, MA-M, MA-L)
        if digits[:n] in table:
            return table[digits[:n]]
    return None


def is_axis(maker: str | None) -> bool:
    """Axis's own prefixes read "Axis Communications AB" or just "Axis"; "Ace Axis Limited"
    is someone else."""
    return bool(maker) and maker.split()[0] == "Axis"


class NmapScanner:
    def __init__(self, binary: str = "nmap", run: Callable[[list[str]], NmapRun] = run_nmap,
                 run_ip: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                 which: Callable[[str], str | None] = shutil.which,
                 prefixes: Callable[[], dict[str, str]] = load_prefixes) -> None:
        self._binary, self._run, self._run_ip, self._which = binary, run, run_ip, which
        self._prefixes_loader = prefixes
        self._prefixes: dict[str, str] | None = None

    def scan(self, networks: list[str]) -> ScanReply:
        problem = scan_problem(networks)
        if problem:
            return ScanReply(ok=False, message=f"scan refused: {problem}")
        if self._which(self._binary) is None:
            return ScanReply(ok=False, message="scan failed: nmap is not installed (run sudo ops/install.sh)")
        nets = [ipaddress.IPv4Network(n) for n in networks]
        own = [a for a in self._ip_list(["addr", "show", "up"], self._own_addresses)
               if any(ipaddress.IPv4Address(a) in n for n in nets)]
        try:
            # Normalised CIDRs only reach nmap (scan_problem already insists on them).
            done = self._run(nmap_argv(self._binary, [str(n) for n in nets], own))
        except subprocess.TimeoutExpired:
            return ScanReply(ok=False, message=f"scan failed: nmap did not finish within {SCAN_TIMEOUT_S} s and was stopped")
        except OutputTooLarge as exc:
            return ScanReply(ok=False, message=f"scan failed: nmap wrote {exc}; it was stopped")
        except OSError as exc:
            return ScanReply(ok=False, message=f"scan failed: nmap could not run ({exc.strerror})")
        if done.returncode != 0:
            why = done.stderr.decode("utf-8", "replace").strip()[-MESSAGE_CAP:] or f"exit {done.returncode}"
            return ScanReply(ok=False, message=f"scan failed: nmap: {why}")
        try:
            hosts = parse_nmap_xml(done.stdout)
        except ET.ParseError as exc:
            return ScanReply(ok=False, message=f"scan failed: nmap's output was unreadable ({exc})")
        found_tcp = len(hosts)
        fresh_s = (done.elapsed_s or SCAN_TIMEOUT_S) + CONFIRM_SLACK_S
        for address, mac in self._ip_list(["-s", "neigh", "show"], lambda e: self._neighbours(e, fresh_s)):
            ip = ipaddress.IPv4Address(address)
            if address in own or not any(ip in n for n in nets):
                continue
            host = hosts.get(address) or SiteHost(address=address, found_by="arp")
            hosts[address] = host.model_copy(update={"mac": mac})
        if self._prefixes is None:
            self._prefixes = self._prefixes_loader()
        out = []
        for address in sorted(hosts, key=lambda a: ipaddress.IPv4Address(a))[:MAX_HOSTS]:
            host = hosts[address]
            maker = maker_for(host.mac, self._prefixes)
            out.append(host.model_copy(update={"maker": maker, "axis_maker": is_axis(maker)}))
        arp_only = sum(1 for h in out if h.found_by == "arp")
        cut = f"; {len(hosts) - MAX_HOSTS} more not kept" if len(hosts) > MAX_HOSTS else ""
        return ScanReply(ok=True, hosts=out, self_addresses=own,
                         message=f"scanned {', '.join(networks)}: {found_tcp} hosts answered a probe, "
                                 f"{arp_only} more answered only ARP{cut}")

    # -- the neighbour table and the node's own addresses (read-only netlink) --------------

    def _ip_list(self, args: list[str], pick: Callable[[list], list]) -> list:
        """`ip -j -4 <args>` through `pick`; empty (with a warning) if unreadable. Never raises."""
        if self._which("ip") is None:
            log.warning("the ip command is not available; scanning without the neighbour table")
            return []
        try:
            done = self._run_ip(["ip", "-j", "-4", *args], capture_output=True, text=True,
                                timeout=IP_TIMEOUT_S, check=True)
            data = json.loads(done.stdout)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            log.warning("ip %s unreadable (%s)", " ".join(args), exc)
            return []
        return pick(data if isinstance(data, list) else [])

    @staticmethod
    def _own_addresses(links: list) -> list[str]:
        out = []
        for link in links:
            for addr in (link.get("addr_info") or []) if isinstance(link, dict) else []:
                try:
                    out.append(str(ipaddress.IPv4Address(addr.get("local"))))
                except ValueError:
                    continue
        return out

    @staticmethod
    def _neighbours(entries: list, fresh_s: float) -> list[tuple[str, str]]:
        """Entries with a MAC that answered ARP within the last `fresh_s` seconds (`ip -s`'s
        `confirmed` age). No age, no claim: fails toward leaving a host off the map."""
        out = []
        for e in entries:
            if not isinstance(e, dict) or not e.get("lladdr") or set(e.get("state") or []) & _DEAD:
                continue
            confirmed = e.get("confirmed")
            if not isinstance(confirmed, (int, float)) or isinstance(confirmed, bool) or not 0 <= confirmed <= fresh_s:
                continue
            try:
                out.append((str(ipaddress.IPv4Address(e.get("dst"))), str(e["lladdr"]).lower()))
            except ValueError:
                continue
        return out
