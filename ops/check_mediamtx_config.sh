#!/usr/bin/env bash
# ops/check_mediamtx_config.sh — execute a MediaMTX config against a real MediaMTX binary
# (spec §8.1: artifacts shipped verbatim are executed, not just diffed).
#
# Runs MediaMTX with a copy of CONFIG whose two listen addresses are moved to loopback test
# ports, then asserts:
#   1. the config loads and the API answers, and
#   2. MediaMTX opened EXACTLY those two listeners. Anything else means the binary has a
#      default-on server the config doesn't switch off (docs/quirks.md Q-001: MoQ in v1.21),
#      which would listen on every interface.
#
# Usage: ops/check_mediamtx_config.sh MEDIAMTX_BINARY [CONFIG]
#        CONFIG defaults to ops/mediamtx/mediamtx.conf.yml. Exit 0 = pass.
set -euo pipefail

BIN="${1:?usage: check_mediamtx_config.sh MEDIAMTX_BINARY [CONFIG]}"
CONF="${2:-$(dirname "$0")/mediamtx/mediamtx.conf.yml}"
BIN="$(readlink -f "$BIN")"
API="127.0.0.1:19997"
RTSP="127.0.0.1:18554"

work="$(mktemp -d)"
pid=""
cleanup() { [[ -n "$pid" ]] && kill "$pid" 2>/dev/null; wait 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT

if ! grep -q '^apiAddress: ' "$CONF" || ! grep -q '^rtspAddress: ' "$CONF"; then
  echo "FAIL: $CONF must set apiAddress and rtspAddress explicitly"; exit 1
fi
sed -E -e "s/^apiAddress: .*/apiAddress: $API/" -e "s/^rtspAddress: .*/rtspAddress: $RTSP/" \
  "$CONF" > "$work/test.yml"

# Run inside the temp dir: MediaMTX writes any auto-generated files to its working directory.
(cd "$work" && exec "$BIN" test.yml > "$work/log" 2>&1) &
pid=$!

for _ in $(seq 1 50); do
  curl -sf "http://$API/v3/config/global/get" > /dev/null && break
  kill -0 "$pid" 2>/dev/null || break
  sleep 0.1
done
if ! curl -sf "http://$API/v3/config/global/get" > /dev/null; then
  echo "FAIL: MediaMTX did not come up with $CONF"; cat "$work/log"; exit 1
fi
sleep 0.5   # let every server finish binding before we look

got="$(ss -Htlnup | awk -v p="pid=$pid," 'index($0, p) {print $1, $5}' | sort)"
want="$(printf 'tcp %s\ntcp %s\n' "$API" "$RTSP" | sort)"
if [[ "$got" != "$want" ]]; then
  echo "FAIL: MediaMTX listeners differ from the two the config declares."
  printf '  expected:\n%s\n  got:\n%s\n' "$want" "$got"
  echo "  A new default-on server? Switch it off in $CONF and add a docs/quirks.md entry."
  exit 1
fi
echo "OK: $("$BIN" --version) loads $CONF; only RTSP + API listen."
