#!/usr/bin/env bash
# ops/doctor.sh — read-only health check of the host install (spec §4 ops tier; ADR-0002).
# Changes nothing. Run with sudo to include the checks that need root (firewall table,
# which process owns which socket); without it those are reported as SKIP.
#
# Exit 0 = no FAIL (WARN and SKIP allowed), 1 = at least one FAIL.
set -uo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$OPS_DIR")"
# shellcheck source=ops/pins.sh
. "$OPS_DIR/pins.sh"
# shellcheck source=ops/paths.sh
. "$OPS_DIR/paths.sh"

fails=0
pass() { printf '  OK    %s\n' "$*"; }
warn() { printf '  WARN  %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*"; fails=$((fails + 1)); }
skip() { printf '  SKIP  %s\n' "$*"; }
section() { printf '\n%s\n' "$*"; }
active() { systemctl is-active --quiet "$1" 2>/dev/null; }
# check OK_MSG FAIL_MSG COMMAND... — OK if COMMAND succeeds, else FAIL.
check() { local ok="$1" bad="$2"; shift 2; if "$@"; then pass "$ok"; else fail "$bad"; fi; }
# check_warn OK_MSG WARN_MSG COMMAND... — OK if COMMAND succeeds, else WARN.
check_warn() { local ok="$1" bad="$2"; shift 2; if "$@"; then pass "$ok"; else warn "$bad"; fi; }
held() { apt-mark showhold 2>/dev/null | grep -qx "${1:-tailscale}"; }
stock_nftables_disabled() { ! systemctl is-enabled --quiet nftables.service 2>/dev/null; }
quiet() { "$@" > /dev/null 2>&1; }
owner_mode() { stat -c '%U %a' "$1" 2>/dev/null; }
# The registry's rule for a secret: owned by the daemon user, no group/other bits (0600, 0400).
private_to_daemon() { local u m; read -r u m <<< "$(owner_mode "$1")"; [[ "$u" == "$RUN_USER" && "${m: -2}" == "00" ]]; }
listening() { [[ -n "$(ss -Htln "sport = :$1" 2>/dev/null)" ]]; }
not_listening() { ! listening "$1"; }
enabled_active() { systemctl is-enabled --quiet "$1" 2>/dev/null && active "$1"; }
helper_matches() { sed -e "s|@RUN_USER@|$RUN_USER|g" "$OPS_DIR/tailscale-operator.sh" | cmp -s - "$OPERATOR_HELPER"; }
update_rendered() {
  sed -e "s|@UPDATE_TREE@|$UPDATE_TREE|g" -e "s|@UPDATE_REPO_URL@|$UPDATE_REPO_URL|g" \
    -e "s|@CN_STATE@|$CN_STATE|g" -e "s|@UPDATE_RESULT_DIR@|$UPDATE_RESULT_DIR|g" \
    -e "s|@UPDATE_PY@|$UPDATE_PY|g" -e "s|@RUNNING_FILE@|$RUNNING_FILE|g" "$OPS_DIR/update.sh"
}
updater_matches() { update_rendered | cmp -s - "$UPDATE_HELPER" && cmp -s "$OPS_DIR/update_helper.py" "$UPDATE_PY"; }
pref() { tailscale debug prefs 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('$1',''))" 2>/dev/null; }

section "Host"
# shellcheck source=/dev/null
. /etc/os-release
arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"
if [[ "${VERSION_CODENAME:-}" == "$TARGET_CODENAME" && "$arch" == "$TARGET_ARCH" ]]; then
  pass "Debian $VERSION_CODENAME on $arch"
else
  fail "host is ${PRETTY_NAME:-unknown} on $arch; pins are for Debian $TARGET_CODENAME on $TARGET_ARCH"
fi
pydantic="$(python3 -I -c 'import pydantic; print(pydantic.VERSION)' 2>/dev/null || true)"
check "python3 has pydantic $pydantic (the daemon's runtime, requirements.txt)" \
  "python3 has no pydantic 2${pydantic:+ (found $pydantic)} — run sudo ops/install.sh" test "${pydantic%%.*}" == 2

section "Provenance (ADR-0009 D1)"
# running.json is root's record of the last install; the tree is what the daemon runs from its
# next start. Either can move without the other (a `git pull` with no install), so both show.
if [[ -s "$DAEMON_USER_FILE" ]]; then
  check "daemon user $RUN_USER recorded in $DAEMON_USER_FILE (root 644)" \
    "$DAEMON_USER_FILE is '$(owner_mode "$DAEMON_USER_FILE")', want 'root 644' — re-run sudo ops/install.sh" \
    test "$(owner_mode "$DAEMON_USER_FILE")" == "root 644"
else
  warn "no $DAEMON_USER_FILE (installed before ADR-0009) — re-run sudo ops/install.sh"
fi
running() { python3 -I -c "import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], ''))" "$RUNNING_FILE" "$1" 2>/dev/null; }
if [[ ! -s "$RUNNING_FILE" ]]; then
  warn "no $RUNNING_FILE: nothing records what this node runs — re-run sudo ops/install.sh"
elif [[ -z "$(running commit)" ]]; then
  fail "$RUNNING_FILE is unreadable — re-run sudo ops/install.sh"
else
  rec_commit="$(running commit)"
  dirty=""; [[ "$(running clean)" == True ]] || dirty=", dirty"
  pass "installed $(running describe) ($(running mode) $(running tree)$dirty) at $(running installed_at) by $(running installed_by)"
  if [[ "$(running tree)" != "$REPO_ROOT" ]]; then
    warn "this doctor is $REPO_ROOT's, but the node was installed from $(running tree)"
  else
    head="$(tree_git rev-parse HEAD)"
    if [[ -z "$head" ]]; then skip "can't read $REPO_ROOT's commit"
    elif [[ "$head" != "$rec_commit" ]]; then
      warn "$REPO_ROOT is at ${head:0:12}, not the installed ${rec_commit:0:12}: the daemon runs it from its next restart, unrecorded — re-run sudo ops/install.sh"
    else
      pass "$REPO_ROOT is still at the installed commit"
    fi
  fi
fi

section "One-click update (ADR-0009)"
# Root runs these, so they must be root's alone and match this tree. The release checkout and
# the result directory are root's too: the daemon user may read them, never write them.
check "the root updater matches the repo" "$UPDATE_HELPER or $UPDATE_PY differs from the repo or is missing — re-run sudo ops/install.sh" \
  updater_matches
check "the root updater is root's alone" "the root updater's copies are not root-owned 0755/0644 — re-run sudo ops/install.sh" \
  test "$(stat -c '%U %a' "$UPDATE_HELPER" 2>/dev/null) $(stat -c '%U %a' "$UPDATE_PY" 2>/dev/null)" == "root 755 root 644"
check "$UPDATE_UNIT.path active (waits for a request)" "$UPDATE_UNIT.path not enabled and active — re-run sudo ops/install.sh" \
  enabled_active "$UPDATE_UNIT.path"
check "$UPDATE_RESULT_DIR is root's (root 755)" "$UPDATE_RESULT_DIR is '$(owner_mode "$UPDATE_RESULT_DIR")', want 'root 755' — re-run sudo ops/install.sh" \
  test "$(owner_mode "$UPDATE_RESULT_DIR")" == "root 755"
if [[ -e "$UPDATE_TREE" ]]; then
  check "$UPDATE_TREE is root's (root 755)" "$UPDATE_TREE is '$(owner_mode "$UPDATE_TREE")', want 'root 755': root installs from it" \
    test "$(owner_mode "$UPDATE_TREE")" == "root 755"
fi
last_update="$UPDATE_RESULT_DIR/result.json"
if [[ -s "$last_update" ]]; then
  lu() { python3 -I -c "import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], ''))" "$last_update" "$1" 2>/dev/null; }
  if [[ "$(lu "done")" != True ]]; then warn "an update is running (phase $(lu phase), by $(lu requested_by))"
  elif [[ "$(lu ok)" == True ]]; then pass "last update: $(lu message)"
  else warn "last update didn't succeed: $(lu message)"; fi
fi

section "Tailscale (pinned $TAILSCALE_VERSION)"
have="$(dpkg-query -W -f='${Version}' tailscale 2>/dev/null || true)"
if [[ -z "$have" ]]; then fail "tailscale not installed — run sudo ops/install.sh"
elif [[ "$have" == "$TAILSCALE_VERSION" ]]; then pass "tailscale $have"
else fail "tailscale $have installed, pinned $TAILSCALE_VERSION"; fi
if [[ -n "$have" ]]; then
  check_warn "held" "not held — apt upgrade may move it off the pin" held
  check "tailscaled active" "tailscaled not active" active tailscaled.service
  # Running with a pending AuthURL is a node control refused, on its cached netmap (Q-005).
  state="$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d.get("BackendState",""); print("Refused" if s == "Running" and d.get("AuthURL") else s)' 2>/dev/null || true)"
  case "$state" in
    Refused) warn "tailscaled says Running, but control refused the node (removed while off, quirks Q-005) — Join on the dashboard's overview" ;;
    Running) pass "logged in: $(tailscale ip -4 2>/dev/null | head -1)" ;;
    NeedsLogin) warn "not logged in to a tailnet — save the auth key on the dashboard's /config, then Join on its overview (ADR-0005)" ;;
    "") warn "could not read tailscale status" ;;
    *) warn "tailscale state: $state" ;;
  esac
  # A Leave deletes the operator with the profile (Q-010); a root oneshot renews it (ADR-0002 A4).
  op="$(pref OperatorUser)"
  check "operator is $RUN_USER (the daemon can run tailscale without root)" \
    "operator is '${op:-none}', not $RUN_USER — every Join fails; sudo systemctl start $OPERATOR_UNIT, or re-run sudo ops/install.sh" \
    test "$op" == "$RUN_USER"
  check "$OPERATOR_UNIT.service renews it on tailscaled start" \
    "$OPERATOR_UNIT.service not enabled — re-run sudo ops/install.sh" \
    quiet systemctl is-enabled "$OPERATOR_UNIT.service"
  for kind in path timer; do
    check "$OPERATOR_UNIT.$kind active" "$OPERATOR_UNIT.$kind not enabled and active — re-run sudo ops/install.sh" \
      enabled_active "$OPERATOR_UNIT.$kind"
  done
  check "$OPERATOR_HELPER matches the repo" "$OPERATOR_HELPER differs from the repo or is missing — re-run sudo ops/install.sh" \
    helper_matches
  check "$OPERATOR_HELPER is root's alone (the daemon user can't change what root runs)" \
    "$OPERATOR_HELPER is not root-owned 0755 — re-run sudo ops/install.sh" \
    test "$(stat -c '%U %a' "$OPERATOR_HELPER" 2>/dev/null)" == "root 755"
fi

section "Firewall"
check "commissioning-node-firewall active" \
  "commissioning-node-firewall not active — MediaMTX will not start without it" \
  active commissioning-node-firewall.service
check_warn "Debian nftables.service disabled (cannot flush our table)" \
  "Debian nftables.service is enabled: its 'flush ruleset' wipes our table if restarted" \
  stock_nftables_disabled
check "$FW_FILE matches the repo" "$FW_FILE differs from the repo — re-run the installer" \
  cmp -s "$OPS_DIR/firewall/commissioning-node.nft" "$FW_FILE"
if ((EUID == 0)); then
  table="$(nft list table inet commissioning_node 2>/dev/null || true)"
  check "nftables table inet commissioning_node loaded" "table inet commissioning_node not loaded" \
    test -n "$table"
  check "dashboard 8080 dropped on the site LAN (live rule)" \
    "no live 8080 drop rule — the dashboard's tailnet door is open to the site LAN" \
    grep -q 'tcp dport 8080 drop' <<< "$table"
  if [[ "$(sysctl -n net.ipv4.ip_forward 2>/dev/null)" == 1 ]]; then
    check "forwarding fenced: nothing forwarded but from or into the tailnet (live rule)" \
      "IPv4 forwarding is ON but the forward chain is missing — the site LAN can route through the node" \
      grep -q 'nothing else is forwarded' <<< "$table"
  fi
else
  skip "nftables table check needs root (sudo ops/doctor.sh)"
fi
fwd="$(sysctl -n net.ipv4.ip_forward 2>/dev/null)"
check_warn "IPv4 forwarding on (subnet routes can be served, ADR-0005)" \
  "IPv4 forwarding is off — advertised routes will not carry traffic; check commissioning-node-firewall" \
  test "$fwd" == 1
if active firewalld.service; then
  # The RevPi image's firewalld rejects what it doesn't list, after our table accepts it
  # (docs/quirks.md Q-009). The loopback probes below never pass through it, so check it here.
  ts_zone() { firewall-cmd "$@" --get-zone-of-interface=tailscale0 2>/dev/null || echo none; }
  check "tailscale0 in firewalld's trusted zone" \
    "tailscale0 is in firewalld zone '$(ts_zone)', not trusted — it rejects the tailnet door, MediaMTX and forwarded routes; re-run sudo ops/install.sh" \
    test "$(ts_zone)" == trusted
  if ((EUID == 0)); then
    site_zones="$({ firewall-cmd --get-default-zone; firewall-cmd --get-active-zones | awk '!/^[[:space:]]/ {print $1}'; } \
      | grep -vx trusted | sort -u)"
    port_open() { firewall-cmd -q --zone="$1" --query-port="$FALLBACK_PORT/tcp" && firewall-cmd -q --permanent --zone="$1" --query-port="$FALLBACK_PORT/tcp"; }
    check "tailscale0 in firewalld's trusted zone after reboot (permanent)" \
      "tailscale0 is in permanent firewalld zone '$(ts_zone --permanent)', not trusted — lost at the next reboot; re-run sudo ops/install.sh" \
      test "$(ts_zone --permanent)" == trusted
    for zone in $site_zones; do
      check "firewalld zone $zone accepts $FALLBACK_PORT/tcp (runtime + permanent)" \
        "firewalld zone $zone rejects $FALLBACK_PORT/tcp (runtime or after reboot) — the admin fallback is unreachable from the site LAN; re-run sudo ops/install.sh" \
        port_open "$zone"
    done
    # ADR-0002 A3: the site LAN into the tailnet. Targets are readable only from the permanent config.
    pol_forwards() {
      [[ "$(firewall-cmd --permanent --policy="$FIREWALLD_POLICY" --get-target 2>/dev/null)" == ACCEPT ]] || return 1
      local cfg
      for cfg in "" --permanent; do
        firewall-cmd -q ${cfg:+"$cfg"} --policy="$FIREWALLD_POLICY" --query-egress-zone=trusted 2>/dev/null || return 1
        firewall-cmd -q ${cfg:+"$cfg"} --policy="$FIREWALLD_POLICY" --query-ingress-zone="$1" 2>/dev/null || return 1
      done
    }
    for zone in $site_zones; do
      check "firewalld forwards zone $zone into the tailnet (policy $FIREWALLD_POLICY, runtime + permanent)" \
        "firewalld rejects forwarding from zone $zone into tailscale0 (runtime or after reboot) — site hosts can't reach the tailnet; re-run sudo ops/install.sh" \
        pol_forwards "$zone"
    done
  else
    skip "firewalld permanent config, the admin fallback's port and the site-to-tailnet policy need root (sudo ops/doctor.sh)"
  fi
else
  pass "firewalld not running (only our table filters)"
fi
check "no sysctl drop-in forwards at boot before the fence" \
  "$LEGACY_SYSCTL_FILE exists: it enables forwarding at boot before the firewall loads — re-run sudo ops/install.sh" \
  test ! -e "$LEGACY_SYSCTL_FILE"

section "nmap (pinned $NMAP_VERSION)"
have="$(dpkg-query -W -f='${Version}' nmap 2>/dev/null || true)"
if [[ -z "$have" ]]; then fail "nmap not installed — run sudo ops/install.sh"
elif [[ "$have" == "$NMAP_VERSION" ]]; then pass "nmap $have"
else fail "nmap $have installed, pinned $NMAP_VERSION"; fi
[[ -z "$have" ]] || check_warn "held" "not held — apt upgrade may move it off the pin" held nmap

section "MediaMTX (pinned $MEDIAMTX_VERSION)"
bin=/opt/mediamtx/current/mediamtx
if [[ ! -x "$bin" ]]; then fail "$bin missing — run sudo ops/install.sh"
else
  got="$("$bin" --version 2>/dev/null)"
  check "mediamtx $got" "mediamtx $got, pinned $MEDIAMTX_VERSION" test "$got" == "$MEDIAMTX_VERSION"
fi
check "/etc/mediamtx/mediamtx.yml matches the repo" \
  "/etc/mediamtx/mediamtx.yml differs from the repo — re-run the installer" \
  cmp -s "$OPS_DIR/mediamtx/mediamtx.conf.yml" /etc/mediamtx/mediamtx.yml
check "mediamtx active" "mediamtx not active" active mediamtx.service
check "API answers on 127.0.0.1:9997" "API not answering on 127.0.0.1:9997" \
  quiet curl -sf http://127.0.0.1:9997/v3/config/global/get
if ((EUID == 0)); then
  # Exactly RTSP on 8554 and the API on loopback 9997 — anything else is a default-on
  # server the config doesn't switch off (docs/quirks.md Q-001).
  listeners="$(ss -Htlnup | awk '/"mediamtx"/ {print $1, $5}' | sort -u)"
  unexpected="$(grep -Ev '^tcp (\*|0\.0\.0\.0|\[::\]):8554$|^tcp 127\.0\.0\.1:9997$' <<< "$listeners" || true)"
  if [[ -z "$listeners" ]]; then fail "mediamtx has no listeners"
  elif [[ -n "$unexpected" ]]; then fail "unexpected mediamtx listeners: $(tr '\n' ' ' <<< "$unexpected")"
  else pass "listeners: RTSP :8554, API 127.0.0.1:9997 only"; fi
else
  skip "listener ownership check needs root (sudo ops/doctor.sh)"
fi

section "Admin fallback (ADR-0003)"
if [[ -f "$TLS_CERT" && -f "$TLS_KEY" ]]; then
  pass "TLS certificate $TLS_CERT"
  # Compare this with what the browser shows on the first visit to :8443 (trust on first use).
  echo "        SHA-256 $(openssl x509 -in "$TLS_CERT" -noout -fingerprint -sha256 | cut -d= -f2)"
  check_warn "certificate valid for 30+ days" "certificate expires within 30 days: $(openssl x509 -in "$TLS_CERT" -noout -enddate)" \
    quiet openssl x509 -in "$TLS_CERT" -noout -checkend $((30 * 86400))
  check "TLS key owned by $RUN_USER, mode 600" "TLS key is '$(owner_mode "$TLS_KEY")', want '$RUN_USER 600' — re-run sudo ops/install.sh" \
    test "$(owner_mode "$TLS_KEY")" == "$RUN_USER 600"
  if [[ -r "$TLS_KEY" ]]; then
    check "TLS key matches the certificate" "TLS key does not match the certificate" \
      test "$(openssl x509 -in "$TLS_CERT" -noout -pubkey)" == "$(openssl pkey -in "$TLS_KEY" -pubout 2>/dev/null)"
  else
    skip "key/certificate match needs read access to the key (sudo ops/doctor.sh)"
  fi
else
  fail "TLS key pair missing in $TLS_DIR — run sudo ops/install.sh"
fi
valid_cred() { python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(not (d.get("format")==1 and d.get("kdf")=="scrypt" and d.get("hash") and d.get("salt")))' "$ADMIN_CRED" 2>/dev/null; }
if [[ -f "$ADMIN_CRED" ]]; then
  check "admin credential owned by $RUN_USER, mode 600" \
    "admin credential is '$(owner_mode "$ADMIN_CRED")', want '$RUN_USER 600' — re-run sudo ops/set-admin-password.sh" \
    test "$(owner_mode "$ADMIN_CRED")" == "$RUN_USER 600"
  if [[ -r "$ADMIN_CRED" ]]; then
    check "admin credential is a well-formed scrypt record" \
      "admin credential is malformed — re-run sudo ops/set-admin-password.sh" valid_cred
  else
    skip "admin credential format check needs read access (sudo ops/doctor.sh)"
  fi
  check "fallback listening on :8443" \
    "fallback not listening on :8443 — restart commissioning-node, then see its journal" \
    listening 8443
  check "fallback answers TLS on 127.0.0.1:8443" "fallback not answering TLS on 127.0.0.1:8443" \
    quiet curl -sk -o /dev/null https://127.0.0.1:8443/login
else
  warn "no admin password, so the LAN fallback is off (fail closed) — sudo ops/set-admin-password.sh"
  # Fail closed: without a credential nothing may answer on the fallback port.
  check "nothing listening on :8443 without a credential" ":8443 is listening but no admin credential exists" \
    not_listening 8443
fi

section "Config registry (ADR-0004)"
check "unit declares StateDirectory=commissioning-node" \
  "unit has no StateDirectory= — config cannot be saved; re-run sudo ops/install.sh" \
  test "$(systemctl show commissioning-node -p StateDirectory --value 2>/dev/null)" == "commissioning-node"
if [[ ! -d "$CN_STATE" ]]; then
  if active commissioning-node.service; then fail "$CN_STATE missing although the daemon is running"
  else warn "$CN_STATE missing — systemd creates it when commissioning-node starts"; fi
else
  check "$CN_STATE owned by $RUN_USER, mode 700" \
    "$CN_STATE is '$(owner_mode "$CN_STATE")', want '$RUN_USER 700'" \
    test "$(owner_mode "$CN_STATE")" == "$RUN_USER 700"
  if [[ -r "$CN_STATE" && -x "$CN_STATE" ]]; then
    # The registry treats an unsafe secret file as unset; say which one and why.
    bad_secrets=0
    for scope in site node; do
      dir="$CN_STATE/$scope/secrets"
      [[ -d "$dir" ]] || continue
      while IFS= read -r -d '' f; do
        if [[ -L "$f" || ! -f "$f" ]]; then fail "secret $f is not a regular file (ignored by the daemon)"
        elif ! private_to_daemon "$f"; then
          fail "secret $f is '$(owner_mode "$f")', want owner $RUN_USER and no group/other access (ignored by the daemon)"
        else continue; fi
        bad_secrets=$((bad_secrets + 1))
      done < <(find "$dir" -mindepth 1 -maxdepth 1 -print0)
    done
    ((bad_secrets)) || pass "secret files are regular, owned by $RUN_USER, private"
    site_secrets="$(find "$CN_STATE/site/secrets" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l)"
    # Expected on a joined node: the auth key is kept so the node can rejoin (ADR-0005 A1).
    ((site_secrets == 0)) || pass "$site_secrets site secret(s) stored — the auth key is kept for auto-rejoin (ADR-0005 A1); wiped at teardown"
    malformed="$(find "$CN_STATE" -maxdepth 2 -name 'config.json.malformed-*' 2>/dev/null)"
    if [[ -n "$malformed" ]]; then warn "malformed config set aside (defaults in use): $(tr '\n' ' ' <<< "$malformed")"
    else pass "no malformed config set aside"; fi
  else
    skip "secret file checks need read access to $CN_STATE (sudo ops/doctor.sh)"
  fi
fi

section "commissioning-node daemon"
check "commissioning-node active" "commissioning-node not active" active commissioning-node.service
check "dashboard answers on 127.0.0.1:8080 (local door)" "dashboard not answering on 127.0.0.1:8080" \
  quiet curl -sf http://127.0.0.1:8080/api/state
# The tailnet door listens on every address; the firewall's 8080 rule (above) is what keeps
# the site LAN off it, and the app refuses any caller that is not a resolved tailnet peer.
check_warn "dashboard listening on all addresses (local + tailnet doors)" \
  "dashboard not listening on all addresses — still on the pre-ADR-0003 code? restart commissioning-node" \
  quiet grep -Eq '(\*|\[::\]|0\.0\.0\.0):8080$' <<< "$(ss -Htln 'sport = :8080' | awk '{print $4}')"

echo
if ((fails)); then echo "doctor: $fails FAIL(s)"; exit 1; fi
echo "doctor: no failures"
