# shellcheck shell=bash
# ops/paths.sh — the node's host paths and its daemon user, in one place (spec §4 ops tier;
# ADR-0002, ADR-0003). Sourced by install.sh, doctor.sh and set-admin-password.sh, which set
# REPO_ROOT first. The dashboard reads the same files (ADR-0003 step 4).
# shellcheck disable=SC2034  # variables are consumed by the scripts that source this file

CN_ETC=/etc/commissioning-node
FW_FILE="$CN_ETC/firewall.nft"
# Admin credential for the LAN fallback: an scrypt hash, never the password (ADR-0003 §3).
ADMIN_CRED="$CN_ETC/admin.cred"
# The fallback's TLS key pair: born on the node, never regenerated once complete.
TLS_DIR="$CN_ETC/tls"
TLS_KEY="$TLS_DIR/fallback.key"
TLS_CERT="$TLS_DIR/fallback.crt"

# The config registry (ADR-0004): systemd creates it (StateDirectory=, 0700, owned by the
# daemon user). site/ is wiped when the node leaves a site; node/ survives the move.
CN_STATE=/var/lib/commissioning-node

# The daemon runs as the checkout's owner, and owns its secrets (0600).
RUN_USER="$(stat -c %U "$REPO_ROOT")"
