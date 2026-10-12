#!/usr/bin/env bash
# ops/doctor.sh — read-only health check of the host install (spec §4 ops tier; ADR-0002).
# Changes nothing. Run with sudo to include the checks that need root (firewall table,
# which process owns which socket); without it those are reported as SKIP.
#
# Exit 0 = no FAIL (WARN and SKIP allowed), 1 = at least one FAIL.
set -uo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=ops/pins.sh
. "$OPS_DIR/pins.sh"

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
held() { apt-mark showhold 2>/dev/null | grep -qx tailscale; }
stock_nftables_disabled() { ! systemctl is-enabled --quiet nftables.service 2>/dev/null; }
quiet() { "$@" > /dev/null 2>&1; }

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
    NeedsLogin) warn "not logged in to a tailnet — run: sudo tailscale up" ;;
    "") warn "could not read tailscale status" ;;
    *) warn "tailscale state: $state" ;;
  esac
fi

section "Firewall"
check "commissioning-node-firewall active" \
  "commissioning-node-firewall not active — MediaMTX will not start without it" \
  active commissioning-node-firewall.service
check_warn "Debian nftables.service disabled (cannot flush our table)" \
  "Debian nftables.service is enabled: its 'flush ruleset' wipes our table if restarted" \
  stock_nftables_disabled
if ((EUID == 0)); then
  check "nftables table inet commissioning_node loaded" "table inet commissioning_node not loaded" \
    quiet nft list table inet commissioning_node
else
  skip "nftables table check needs root (sudo ops/doctor.sh)"
fi

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

section "commissioning-node daemon"
check "commissioning-node active" "commissioning-node not active" active commissioning-node.service
check "dashboard answers on 127.0.0.1:8080" "dashboard not answering on 127.0.0.1:8080" \
  quiet curl -sf http://127.0.0.1:8080/api/state

echo
if ((fails)); then echo "doctor: $fails FAIL(s)"; exit 1; fi
echo "doctor: no failures"
