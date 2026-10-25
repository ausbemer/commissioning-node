# shellcheck shell=bash
# ops/paths.sh — the node's host paths and its daemon user, in one place (spec §4 ops tier;
# ADR-0002, ADR-0003). Sourced by install.sh, doctor.sh and set-admin-password.sh, which set
# REPO_ROOT first. The dashboard reads the same files (ADR-0003 step 4).
# shellcheck disable=SC2034  # variables are consumed by the scripts that source this file

CN_ETC=/etc/commissioning-node
FW_FILE="$CN_ETC/firewall.nft"
# A sysctl drop-in an earlier install placed (2026-10-13). It turned forwarding on at boot,
# before the firewall's forward chain loads, so the installer removes it: the firewall unit
# owns forwarding now (ADR-0005 A3, review 2026-10-13).
LEGACY_SYSCTL_FILE=/etc/sysctl.d/90-commissioning-node.conf
# Admin credential for the LAN fallback: an scrypt hash, never the password (ADR-0003 §3).
ADMIN_CRED="$CN_ETC/admin.cred"
# The fallback's TLS key pair: born on the node, never regenerated once complete.
TLS_DIR="$CN_ETC/tls"
TLS_KEY="$TLS_DIR/fallback.key"
TLS_CERT="$TLS_DIR/fallback.crt"
# The fallback's port (main.py FALLBACK_PORT). The only port of ours open to the site LAN, so
# the installer opens it in firewalld when the RevPi image runs it (docs/quirks.md Q-009).
FALLBACK_PORT=8443
# firewalld policy that lets the site zones forward into trusted (tailscale0): the site LAN
# into the tailnet (ADR-0005 2026-10-21, ADR-0002 A3).
FIREWALLD_POLICY=site-to-tailnet

# The root-owned copy of ops/tailscale-operator.sh that renews tailscaled's operator, and its
# units (ADR-0002 A4). Root runs it, so it is never run from the repo the daemon user owns.
OPERATOR_HELPER=/usr/local/libexec/commissioning-node/tailscale-operator
OPERATOR_UNIT=commissioning-node-tailscale-operator

# The config registry (ADR-0004): systemd creates it (StateDirectory=, 0700, owned by the
# daemon user). site/ is wiped when the node leaves a site; node/ survives the move.
CN_STATE=/var/lib/commissioning-node

# The daemon user, written by the first install (root, 0644; ADR-0009 D1). A release checkout
# is root's, so its owner can't name the daemon user; the file does. Without the file (a node
# installed before ADR-0009, or a dry run on a fresh one) it is the checkout's owner, as before.
DAEMON_USER_FILE="$CN_ETC/daemon-user"
# What this node runs: tree, commit, ref, clean, when and by whom (root, 0644, schema_version;
# ADR-0009 D1). Written by every install; read by the doctor and, later, the dashboard.
RUNNING_FILE="$CN_ETC/running.json"

# daemon_user FILE TREE — the daemon user: FILE's first line, else TREE's owner.
daemon_user() {
  local name=""
  [[ -r "$1" ]] && read -r name < "$1"
  if [[ -n "$name" ]]; then echo "$name"; else stat -c %U "$2"; fi
}
# The checkout's owner: not always the daemon user once a release checkout exists.
TREE_OWNER="$(stat -c %U "$REPO_ROOT")"
# The daemon runs as this user, and owns its secrets (0600).
RUN_USER="$(daemon_user "$DAEMON_USER_FILE" "$REPO_ROOT")"

# tree_git ARGS... — git in the checkout, as the checkout's owner. Never root in a tree another
# user owns: its .git/config (fsmonitor, hooks) would run as root, which is what git's
# "dubious ownership" check stops. Fails (empty output) if it can't.
tree_git() {
  if ((EUID == 0)) && [[ "$TREE_OWNER" != root ]]; then
    runuser -u "$TREE_OWNER" -- git -C "$REPO_ROOT" "$@" 2>/dev/null
  else
    git -C "$REPO_ROOT" "$@" 2>/dev/null
  fi
}
