#!/usr/bin/env bash
# ops/set-admin-password.sh — set or rotate the LAN fallback's admin password (ADR-0003 §3).
# Prompts twice without echo, then writes an scrypt hash (never the password) to
# /etc/commissioning-node/admin.cred, mode 0600, owned by the daemon user. Atomic: a failed
# run leaves the old credential in place. There is no default password: until this has run,
# the fallback listener stays off.
#
# Usage:  sudo ops/set-admin-password.sh
set -euo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$OPS_DIR")"
# shellcheck source=ops/paths.sh
. "$OPS_DIR/paths.sh"

refuse() { echo "refusing: $*" >&2; exit 1; }
case "${1:-}" in
  "") ;;
  -h | --help) sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "unknown argument: $1 (try --help)" >&2; exit 2 ;;
esac
((EUID == 0)) || refuse "run as root (sudo ops/set-admin-password.sh)"
# Interactive only, so the password never passes through a pipe, argv or shell history.
[[ -t 0 ]] || refuse "run from a terminal; the password is read from a no-echo prompt"
[[ "$RUN_USER" != root ]] || refuse "$REPO_ROOT is owned by root; the daemon must not run as root"

exec python3 -I "$OPS_DIR/admin_cred.py" set --path "$ADMIN_CRED" --owner "$RUN_USER"
