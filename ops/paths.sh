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

# The config registry (ADR-0004): systemd creates it (StateDirectory=, 0700, owned by the
# daemon user). site/ is wiped when the node leaves a site; node/ survives the move.
CN_STATE=/var/lib/commissioning-node

# The daemon runs as the checkout's owner, and owns its secrets (0600).
RUN_USER="$(stat -c %U "$REPO_ROOT")"
