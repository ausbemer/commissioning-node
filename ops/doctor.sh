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

section "Tailscale (pinned $TAILSCALE_VERSION)"
have="$(dpkg-query -W -f='${Version}' tailscale 2>/dev/null || true)"
if [[ -z "$have" ]]; then fail "tailscale not installed — run sudo ops/install.sh"
elif [[ "$have" == "$TAILSCALE_VERSION" ]]; then pass "tailscale $have"
else fail "tailscale $have installed, pinned $TAILSCALE_VERSION"; fi
if [[ -n "$have" ]]; then
  check_warn "held" "not held — apt upgrade may move it off the pin" held
  check "tailscaled active" "tailscaled not active" active tailscaled.service
  state="$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState",""))' 2>/dev/null || true)"
  case "$state" in
    Running) pass "logged in: $(tailscale ip -4 2>/dev/null | head -1)" ;;
    NeedsLogin) warn "not logged in to a tailnet — run: tailscale up --operator=$RUN_USER" ;;
    "") warn "could not read tailscale status" ;;
    *) warn "tailscale state: $state" ;;
  esac
  op="$(pref OperatorUser)"
  check_warn "operator is $RUN_USER (the daemon can run tailscale without root)" \
    "operator is '${op:-none}', not $RUN_USER — re-run sudo ops/install.sh" test "$op" == "$RUN_USER"
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
    check "forwarding fenced: only the tailnet opens forwarded connections (live rule)" \
      "IPv4 forwarding is ON but the forward chain is missing — the site LAN can route through the node" \
      grep -q 'only the tailnet opens' <<< "$table"
  fi
else
  skip "nftables table check needs root (sudo ops/doctor.sh)"
fi
fwd="$(sysctl -n net.ipv4.ip_forward 2>/dev/null)"
check_warn "IPv4 forwarding on (subnet routes can be served, ADR-0005)" \
  "IPv4 forwarding is off — advertised routes will not carry traffic; check commissioning-node-firewall" \
  test "$fwd" == 1
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
    ((site_secrets == 0)) || warn "$site_secrets site secret(s) stored — wiped at teardown; an auth key is deleted after its join"
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
