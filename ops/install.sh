#!/usr/bin/env bash
# ops/install.sh — idempotent host installer for commissioning-node (spec §4 ops tier;
# ADR-0002). Installs, at the versions pinned in ops/pins.sh:
#
#   1. apt prerequisites (ca-certificates, curl, gnupg, nftables)
#   2. Tailscale from its signed apt repo (key fingerprint pinned), exact version, held
#   3. the commissioning-node firewall table + unit (MediaMTX ports: tailnet + loopback)
#   4. MediaMTX from the sha256-pinned release tarball, its system user, config and unit
#   5. the commissioning-node daemon unit (runs main.py)
#
# It does NOT log Tailscale in (an auth key is a secret, spec §8.9) and touches no
# firewall rules but its own table. Re-running is safe: each step checks before changing.
#
# Usage:  sudo ops/install.sh        apply
#         ops/install.sh --dry-run   show what would change; needs no root and no network
set -euo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$OPS_DIR")"
# shellcheck source=ops/pins.sh
. "$OPS_DIR/pins.sh"

DRY_RUN=0
case "${1:-}" in
  "") ;;
  --dry-run) DRY_RUN=1 ;;
  -h | --help) sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "unknown argument: $1 (try --help)" >&2; exit 2 ;;
esac

TS_KEYRING=/usr/share/keyrings/tailscale-archive-keyring.gpg
TS_LIST=/etc/apt/sources.list.d/tailscale.list
MTX_ROOT=/opt/mediamtx
MTX_DIR="$MTX_ROOT/$MEDIAMTX_VERSION"
MTX_CONF=/etc/mediamtx/mediamtx.yml
FW_FILE=/etc/commissioning-node/firewall.nft
UNIT_DIR=/etc/systemd/system
RUN_USER="$(stat -c %U "$REPO_ROOT")"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

step() { printf '\n==> %s\n' "$*"; }
ok() { printf '    ok: %s\n' "$*"; }
# act DESCRIPTION COMMAND... — run COMMAND, or only describe it under --dry-run.
act() {
  local what="$1"; shift
  if ((DRY_RUN)); then printf '    would: %s\n' "$what"; else printf '    doing: %s\n' "$what"; "$@"; fi
}
refuse() { echo "refusing: $*" >&2; exit 1; }
installed_version() { dpkg-query -W -f='${Version}' "$1" 2>/dev/null || true; }
is_installed() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q 'install ok installed'; }
key_fpr() { GNUPGHOME="$work/gnupg" gpg --show-keys --with-colons "$1" 2>/dev/null | awk -F: '$1 == "fpr" {print $10; exit}'; }

# apt-get update exits non-zero if ANY configured repo fails, and the host's own repos
# (raspberrypi.org, revolutionpi.de) have failed transiently ("no longer has a Release
# file") while reachable seconds later. So: retry the full update once, and refresh the
# Tailscale repo on its own so an unrelated repo can never block the Tailscale install.
apt_update_all() { apt-get update -qq || { echo "    apt-get update failed; retrying in 10s" >&2; sleep 10; apt-get update -qq; }; }
apt_update_tailscale() {
  apt-get update -qq -o Dir::Etc::sourcelist="$TS_LIST" -o Dir::Etc::sourceparts=- \
    -o APT::Get::List-Cleanup=0
}

# sync_file SRC DEST MODE — install SRC at DEST unless identical. Sets CHANGED=1 if it would change.
sync_file() {
  local src="$1" dest="$2" mode="$3"
  if [[ -f "$dest" ]] && cmp -s "$src" "$dest"; then ok "$dest up to date"; return; fi
  act "install $dest" install -D -m "$mode" "$src" "$dest"
  CHANGED=1
}

# ensure_service UNIT CHANGED — enabled, and running the current files (restart if they changed).
ensure_service() {
  local unit="$1" changed="$2"
  ((changed)) && act "systemctl daemon-reload" systemctl daemon-reload
  if systemctl is-enabled --quiet "$unit" 2>/dev/null; then ok "$unit enabled"
  else act "enable $unit" systemctl enable --quiet "$unit"; fi
  if ((changed)); then act "restart $unit" systemctl restart "$unit"
  elif systemctl is-active --quiet "$unit" 2>/dev/null; then ok "$unit active"
  else act "start $unit" systemctl start "$unit"; fi
}

# ---------------------------------------------------------------------------------------
step "Preflight"
# shellcheck source=/dev/null
. /etc/os-release
arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"
if [[ "${VERSION_CODENAME:-}" == "$TARGET_CODENAME" && "$arch" == "$TARGET_ARCH" ]]; then
  ok "Debian $VERSION_CODENAME on $arch"
elif ((DRY_RUN)); then
  echo "    WOULD REFUSE: host is ${PRETTY_NAME:-unknown} on $arch; pins are for Debian $TARGET_CODENAME on $TARGET_ARCH"
else
  refuse "host is ${PRETTY_NAME:-unknown} on $arch; pins are for Debian $TARGET_CODENAME on $TARGET_ARCH"
fi
((DRY_RUN)) || ((EUID == 0)) || refuse "run as root (sudo ops/install.sh), or use --dry-run"
# The daemon runs as the checkout's owner. A checkout made with `sudo mkdir` / `sudo tar` is
# root-owned (this happened on the first node), and the daemon must never run as root.
[[ "$RUN_USER" != root ]] || refuse "$REPO_ROOT is owned by root, so the daemon would run as root; chown it to the operator user first"
ok "repo $REPO_ROOT, daemon will run as $RUN_USER"

# ---------------------------------------------------------------------------------------
step "apt prerequisites"
missing=()
for pkg in ca-certificates curl gnupg nftables; do is_installed "$pkg" || missing+=("$pkg"); done
if ((${#missing[@]})); then
  act "apt-get update" apt_update_all
  act "apt-get install ${missing[*]}" apt-get install -y -qq --no-install-recommends "${missing[@]}"
else
  ok "ca-certificates curl gnupg nftables"
fi

# ---------------------------------------------------------------------------------------
step "Tailscale $TAILSCALE_VERSION"
mkdir -p "$work/gnupg" && chmod 700 "$work/gnupg"
if [[ -f "$TS_KEYRING" && "$(key_fpr "$TS_KEYRING")" == "$TAILSCALE_KEY_FPR" ]]; then
  ok "repo signing key $TAILSCALE_KEY_FPR"
elif ((DRY_RUN)); then
  echo "    would: fetch $TAILSCALE_REPO_URL/$TAILSCALE_SUITE.noarmor.gpg, verify fingerprint, install $TS_KEYRING"
else
  curl -fsSL "$TAILSCALE_REPO_URL/$TAILSCALE_SUITE.noarmor.gpg" -o "$work/ts.gpg"
  got="$(key_fpr "$work/ts.gpg")"
  [[ "$got" == "$TAILSCALE_KEY_FPR" ]] || refuse "Tailscale repo key fingerprint is '$got', pinned '$TAILSCALE_KEY_FPR'"
  install -m 0644 "$work/ts.gpg" "$TS_KEYRING"
  echo "    doing: installed repo signing key (fingerprint verified)"
fi

ts_line="deb [signed-by=$TS_KEYRING] $TAILSCALE_REPO_URL $TAILSCALE_SUITE main"
if [[ -f "$TS_LIST" && "$(cat "$TS_LIST")" == "$ts_line" ]]; then
  ok "$TS_LIST"
else
  write_ts_list() { printf '%s\n' "$ts_line" > "$TS_LIST"; }
  act "write $TS_LIST" write_ts_list
fi

have="$(installed_version tailscale)"
if [[ "$have" == "$TAILSCALE_VERSION" ]]; then
  ok "tailscale $have installed"
else
  # Refreshed whenever an install is due, not only when the list is new: a run that
  # wrote the list and then failed must not leave the next run with a stale index.
  act "apt-get update (Tailscale repo only)" apt_update_tailscale
  act "install tailscale=$TAILSCALE_VERSION (installed: ${have:-none})" \
    apt-get install -y -qq --allow-downgrades --allow-change-held-packages "tailscale=$TAILSCALE_VERSION"
fi
if apt-mark showhold 2>/dev/null | grep -qx tailscale; then ok "tailscale held at its pin"
else act "apt-mark hold tailscale" apt-mark hold tailscale; fi

# ---------------------------------------------------------------------------------------
step "Firewall (MediaMTX ports: tailnet + loopback only)"
CHANGED=0
if ((EUID == 0)) && command -v nft > /dev/null; then
  nft -c -f "$OPS_DIR/firewall/commissioning-node.nft" || refuse "firewall rules do not validate"
  ok "rules validate (nft -c)"
else
  echo "    would: validate rules with nft -c (needs root + nftables)"
fi
sync_file "$OPS_DIR/firewall/commissioning-node.nft" "$FW_FILE" 0644
sync_file "$OPS_DIR/systemd/commissioning-node-firewall.service" "$UNIT_DIR/commissioning-node-firewall.service" 0644
ensure_service commissioning-node-firewall.service "$CHANGED"

# ---------------------------------------------------------------------------------------
step "MediaMTX $MEDIAMTX_VERSION"
CHANGED=0
if id -u mediamtx > /dev/null 2>&1; then ok "system user mediamtx"
else
  act "create system user mediamtx" \
    useradd --system --no-create-home --home-dir /var/lib/mediamtx --shell /usr/sbin/nologin mediamtx
fi

if [[ -x "$MTX_DIR/mediamtx" && "$("$MTX_DIR/mediamtx" --version 2>/dev/null)" == "$MEDIAMTX_VERSION" ]]; then
  ok "$MTX_DIR/mediamtx"
elif ((DRY_RUN)); then
  echo "    would: download $MEDIAMTX_ASSET, verify sha256 $MEDIAMTX_SHA256, unpack to $MTX_DIR"
  CHANGED=1
else
  curl -fsSL "$MEDIAMTX_URL" -o "$work/$MEDIAMTX_ASSET"
  echo "$MEDIAMTX_SHA256  $work/$MEDIAMTX_ASSET" | sha256sum -c --quiet - \
    || refuse "$MEDIAMTX_ASSET does not match the pinned sha256"
  mkdir -p "$work/mtx" && tar -xzf "$work/$MEDIAMTX_ASSET" -C "$work/mtx"
  install -D -m 0755 "$work/mtx/mediamtx" "$MTX_DIR/mediamtx"
  install -m 0644 "$work/mtx/LICENSE" "$MTX_DIR/LICENSE"
  echo "    doing: installed $MTX_DIR/mediamtx (sha256 verified)"
  CHANGED=1
fi
# Check the config against the new binary BEFORE `current` moves to it: a refusal must leave
# the unit starting the old binary with the old config, not the new binary with the old one.
if [[ -x "$MTX_DIR/mediamtx" ]]; then
  "$OPS_DIR/check_mediamtx_config.sh" "$MTX_DIR/mediamtx" "$OPS_DIR/mediamtx/mediamtx.conf.yml" \
    | sed 's/^/    /' || refuse "MediaMTX config failed its check; nothing switched or restarted"
else
  echo "    would: check the config against $MTX_DIR/mediamtx before switching to it"
fi

if [[ "$(readlink "$MTX_ROOT/current" 2>/dev/null)" == "$MTX_DIR" ]]; then ok "$MTX_ROOT/current -> $MTX_DIR"
else act "point $MTX_ROOT/current at $MTX_DIR" ln -sfn "$MTX_DIR" "$MTX_ROOT/current"; CHANGED=1; fi
sync_file "$OPS_DIR/mediamtx/mediamtx.conf.yml" "$MTX_CONF" 0644
sync_file "$OPS_DIR/systemd/mediamtx.service" "$UNIT_DIR/mediamtx.service" 0644
ensure_service mediamtx.service "$CHANGED"

# ---------------------------------------------------------------------------------------
step "commissioning-node daemon"
CHANGED=0
sed -e "s|@REPO_ROOT@|$REPO_ROOT|g" -e "s|@RUN_USER@|$RUN_USER|g" \
  "$OPS_DIR/systemd/commissioning-node.service" > "$work/commissioning-node.service"
sync_file "$work/commissioning-node.service" "$UNIT_DIR/commissioning-node.service" 0644
ensure_service commissioning-node.service "$CHANGED"

# ---------------------------------------------------------------------------------------
step "Done$( ((DRY_RUN)) && echo ' (dry run — nothing was changed)')"
echo "    Next: sudo tailscale up   (joins the tailnet; manual until the tailnet-gateway writer exists)"
echo "          ops/doctor.sh       (read-only health check; sudo for the firewall checks)"
