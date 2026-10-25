#!/usr/bin/env bash
# ops/tailscale-operator.sh — keep tailscaled's operator the daemon user (ADR-0002 A4).
#
# `tailscale logout` (the writer's Leave) deletes tailscaled's profile, and the operator with
# it (docs/quirks.md Q-010); the daemon then can't run tailscale. This renews it: it reads
# OperatorUser and, only if it isn't the daemon user, sets it back. It writes that one pref
# and nothing else: never up, down, logout, routes, tags, the hostname or a key.
#
# Run as root by commissioning-node-tailscale-operator.service (tailscaled start, its .path on
# a tailscaled state change, its .timer). NOT run from the repo: ops/install.sh installs a
# root-owned copy with @RUN_USER@ filled in, because the daemon user owns the repo and a root
# unit must never run a file that user can write.
#
# Exit 0 = the operator is the daemon user (already, or now); 1 = it couldn't be.
set -uo pipefail

RUN_USER="@RUN_USER@"
#: Seconds to wait for tailscaled's local API. PROVISIONAL (tools/ledgers/tailscale-operator/).
OPERATOR_WAIT_S=30

# Journal priorities (systemd reads the <N> prefix): the unit's LogLevelMax=notice keeps a
# no-op run, and systemd's own start/stop lines for it, out of the journal.
note() { echo "<5>$*"; }
warn() { echo "<4>$*"; }

operator() {
  tailscale debug prefs 2>/dev/null \
    | python3 -I -c 'import json,sys; print(json.load(sys.stdin).get("OperatorUser") or "")'
}

case "$RUN_USER" in
  "" | root | @*) warn "refusing: operator user is '$RUN_USER' (re-run sudo ops/install.sh)"; exit 1 ;;
esac

for ((waited = 0; ; waited++)); do
  if op="$(operator)"; then break; fi
  if ((waited >= OPERATOR_WAIT_S)); then
    warn "tailscaled's local API did not answer in ${OPERATOR_WAIT_S}s; operator not checked"
    exit 1
  fi
  sleep 1
done

# Reading first keeps this idempotent, and keeps its own write (which changes tailscaled's
# state file) from setting the .path off again.
[[ "$op" == "$RUN_USER" ]] && exit 0

# Its output goes into our one line: on its own it would log at info, below LogLevelMax.
if out="$(tailscale set --operator="$RUN_USER" 2>&1)"; then
  note "tailscale operator was '${op:-none}'; set back to $RUN_USER (quirks Q-010)"
else
  warn "tailscale set --operator=$RUN_USER failed (operator is '${op:-none}'): $(echo "$out" | head -1)"
  exit 1
fi
