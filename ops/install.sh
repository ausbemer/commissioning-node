#!/usr/bin/env bash
# ops/install.sh — idempotent host installer for commissioning-node (spec §4 ops tier;
# ADR-0002). Installs, at the versions pinned in ops/pins.sh:
#
#   1. apt prerequisites (ca-certificates, curl, gnupg, nftables, openssl) and the daemon's
#      Python runtime from Debian (python3, python3-pydantic; requirements.txt)
#   2. Tailscale from its signed apt repo (key fingerprint pinned), exact version, held,
#      with the daemon user as its operator (ADR-0003)
#   3. the commissioning-node firewall table + unit (MediaMTX + dashboard ports: tailnet +
#      loopback; forwarding only from the tailnet). The firewall unit also owns IPv4
#      forwarding for the subnet router (ADR-0005): on only after its forward chain loads
#   3b. if firewalld runs (the RevPi image enables it), the admin fallback's port opened in
#      its site zones and tailscale0 in its trusted zone (ADR-0002 A1, docs/quirks.md Q-009)
#   4. MediaMTX from the sha256-pinned release tarball, its system user, config and unit
#   5. the admin fallback's TLS key pair, generated on the node once (ADR-0003)
#   6. the commissioning-node daemon unit (runs main.py); systemd creates its state
#      directory for the config registry (ADR-0004)
#   7. nmap from Debian, exact version, held: site-survey's scanner runs it unprivileged
#      (ADR-0007)
#   8. the admin password, when none is set and a terminal is attached: it offers to run
#      ops/set-admin-password.sh (no-echo prompt; ADR-0002 A2). Without a terminal, or
#      declined, the fallback stays off (fail closed) and the summary says how to set it
#   9. ops/doctor.sh, read-only, whose verdict is the installer's exit status
#
# It does NOT join the tailnet: the auth key is a site secret (spec §8.9), entered on the
# dashboard's /config and applied by its Join (tailnet-gateway writer, ADR-0005). The summary
# says where. It touches no firewall rules but its own table and the two firewalld entries
# in 3b. Re-running is safe: each step checks before changing.
#
# Usage:  sudo ops/install.sh        apply
#         ops/install.sh --dry-run   show what would change; needs no root and no network
set -euo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$OPS_DIR")"
# shellcheck source=ops/pins.sh
. "$OPS_DIR/pins.sh"
# shellcheck source=ops/paths.sh
. "$OPS_DIR/paths.sh"

DRY_RUN=0
case "${1:-}" in
  "") ;;
  --dry-run) DRY_RUN=1 ;;
  -h | --help) sed -n '2,33p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "unknown argument: $1 (try --help)" >&2; exit 2 ;;
esac

TS_KEYRING=/usr/share/keyrings/tailscale-archive-keyring.gpg
TS_LIST=/etc/apt/sources.list.d/tailscale.list
MTX_ROOT=/opt/mediamtx
MTX_DIR="$MTX_ROOT/$MEDIAMTX_VERSION"
MTX_CONF=/etc/mediamtx/mediamtx.yml
UNIT_DIR=/etc/systemd/system

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
if [[ "$RUN_USER" == root ]]; then
  refuse "$REPO_ROOT is owned by root, so the daemon would run as root; give it to the operator user first: chown -R ${SUDO_USER:-<user>}: $REPO_ROOT"
fi
ok "repo $REPO_ROOT, daemon will run as $RUN_USER"

# ---------------------------------------------------------------------------------------
step "apt prerequisites"
missing=()
# python3-pydantic is the daemon's one runtime dependency (requirements.txt); Debian's
# build, because Debian blocks system-wide pip (PEP 668).
PREREQS=(ca-certificates curl gnupg nftables openssl python3 python3-pydantic)
for pkg in "${PREREQS[@]}"; do is_installed "$pkg" || missing+=("$pkg"); done
if ((${#missing[@]})); then
  act "apt-get update" apt_update_all
  act "apt-get install ${missing[*]}" apt-get install -y -qq --no-install-recommends "${missing[@]}"
else
  ok "${PREREQS[*]}"
fi
# requirements.txt says pydantic 2; refuse a Debian that ships another major.
pydantic_major() { python3 -I -c 'import pydantic; print(pydantic.VERSION.split(".")[0])' 2>/dev/null; }
if is_installed python3-pydantic; then
  [[ "$(pydantic_major)" == 2 ]] || refuse "python3-pydantic is $(installed_version python3-pydantic); the daemon needs pydantic 2 (requirements.txt)"
  ok "pydantic $(python3 -I -c 'import pydantic; print(pydantic.VERSION)')"
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

# The daemon user may run tailscale without root, so the tailnet-gateway writer (ADR-0003
# step 6) never needs sudo. It is a tailscaled pref, so tailscaled must be running. A failure
# here warns and carries on: it is a convenience, and must never stop the firewall step.
operator() { tailscale debug prefs 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("OperatorUser",""))' 2>/dev/null; }
set_operator() {
  tailscale set --operator="$RUN_USER" \
    || echo "    WARNING: tailscale set --operator failed; re-run the installer once tailscaled is up" >&2
}
if [[ "$(operator)" == "$RUN_USER" ]]; then ok "tailscale operator is $RUN_USER"
else act "tailscale set --operator=$RUN_USER" set_operator; fi

# ---------------------------------------------------------------------------------------
step "nmap $NMAP_VERSION"
have="$(installed_version nmap)"
if [[ "$have" == "$NMAP_VERSION" ]]; then
  ok "nmap $have installed"
else
  act "apt-get update" apt_update_all
  act "install nmap=$NMAP_VERSION (installed: ${have:-none})" \
    apt-get install -y -qq --no-install-recommends --allow-downgrades --allow-change-held-packages "nmap=$NMAP_VERSION"
fi
if apt-mark showhold 2>/dev/null | grep -qx nmap; then ok "nmap held at its pin"
else act "apt-mark hold nmap" apt-mark hold nmap; fi

# ---------------------------------------------------------------------------------------
step "Firewall (MediaMTX + dashboard ports: tailnet + loopback only)"
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

# The firewall unit owns forwarding (on after its forward chain loads). An earlier install
# put a sysctl drop-in here that enabled it at boot, before the fence: remove it.
if [[ -e "$LEGACY_SYSCTL_FILE" ]]; then
  act "remove $LEGACY_SYSCTL_FILE (forwarding is the firewall unit's now)" rm -f "$LEGACY_SYSCTL_FILE"
else
  ok "no sysctl drop-in enabling forwarding at boot"
fi

# ---------------------------------------------------------------------------------------
step "firewalld (the RevPi image's own firewall; docs/quirks.md Q-009)"
# firewalld rejects everything on the site LAN but the RevPi's stock services, and treats an
# interface in no zone (tailscale0) the same way. Its table is a separate base chain, so our
# table's accepts never override its rejects. Two entries, and nothing else of firewalld's:
# the fallback's port in every zone a site interface can land in (the default zone and the
# active ones, but trusted), and tailscale0 in trusted. Each goes into the runtime and the
# permanent config, so no --reload is needed and no one else's runtime change is lost.
if ! systemctl is-active --quiet firewalld.service 2>/dev/null; then
  ok "firewalld not running"
elif ((EUID != 0)); then
  echo "    would: open $FALLBACK_PORT/tcp in firewalld's site zones, put tailscale0 in its trusted zone (queries need root)"
else
  fw() { firewall-cmd -q "$@"; }
  fw_both() { fw "$@" && fw --permanent "$@"; }
  site_zones="$({ firewall-cmd --get-default-zone; firewall-cmd --get-active-zones | awk '!/^[[:space:]]/ {print $1}'; } \
    | grep -vx trusted | sort -u)"
  for zone in $site_zones; do
    if fw --zone="$zone" --query-port="$FALLBACK_PORT/tcp" && fw --permanent --zone="$zone" --query-port="$FALLBACK_PORT/tcp"; then
      ok "firewalld zone $zone accepts $FALLBACK_PORT/tcp (admin fallback)"
    else
      act "open $FALLBACK_PORT/tcp in firewalld zone $zone (admin fallback, ADR-0003)" fw_both --zone="$zone" --add-port="$FALLBACK_PORT/tcp"
    fi
  done
  if [[ "$(firewall-cmd --get-zone-of-interface=tailscale0 2>/dev/null)" == trusted \
    && "$(firewall-cmd --permanent --get-zone-of-interface=tailscale0 2>/dev/null)" == trusted ]]; then
    ok "tailscale0 in firewalld's trusted zone"
  else
    # --change-interface, not --add-interface: it also moves the interface out of another zone.
    act "put tailscale0 in firewalld's trusted zone (the tailnet is the boundary, ADR-0001)" \
      fw_both --zone=trusted --change-interface=tailscale0
  fi
fi

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
step "Admin fallback TLS key (ADR-0003; born on the node, never regenerated)"
# A complete pair is never touched again: the operator has compared its fingerprint, and a
# new one would look exactly like an interception. An incomplete or mismatched pair (a run
# interrupted between the two renames) can never have served TLS, so it is replaced whole.
gen_tls() {
  local name; name="$(hostname)"
  (umask 077; openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
    -keyout "$work/fallback.key" -out "$work/fallback.crt" -days 3650 \
    -subj "/CN=$name commissioning-node fallback" \
    -addext "subjectAltName=DNS:$name,DNS:$name.local")
  install -d -m 0755 "$TLS_DIR"
  install -m 0644 "$work/fallback.crt" "$TLS_CERT.new"
  install -o "$RUN_USER" -m 0600 "$work/fallback.key" "$TLS_KEY.new"
  mv -f "$TLS_KEY.new" "$TLS_KEY" && mv -f "$TLS_CERT.new" "$TLS_CERT"
}
pair_matches() {
  [[ -f "$TLS_KEY" && -f "$TLS_CERT" ]] || return 1
  # Without root the key may be unreadable (dry run): trust the pair rather than guess.
  [[ -r "$TLS_KEY" ]] || return 0
  local pub; pub="$(openssl x509 -in "$TLS_CERT" -noout -pubkey 2>/dev/null)"
  [[ -n "$pub" && "$pub" == "$(openssl pkey -in "$TLS_KEY" -pubout 2>/dev/null)" ]]
}
if pair_matches; then
  ok "$TLS_CERT ($(openssl x509 -in "$TLS_CERT" -noout -fingerprint -sha256 | cut -d= -f2))"
else
  act "generate a self-signed P-256 key pair in $TLS_DIR (key 0600, owner $RUN_USER)" gen_tls
fi
fix_key() { chown "$RUN_USER" "$TLS_KEY" && chmod 0600 "$TLS_KEY"; }
if [[ -f "$TLS_KEY" && "$(stat -c '%U %a' "$TLS_KEY")" != "$RUN_USER 600" ]]; then
  act "set $TLS_KEY to owner $RUN_USER, mode 0600" fix_key
fi

# ---------------------------------------------------------------------------------------
step "Admin password (the LAN fallback's; ADR-0003, ADR-0002 A2)"
# Asked only on a terminal, through the same script as a rotation, so the password only ever
# meets a no-echo prompt (never argv, a pipe or this script). The daemon reads the credential
# when it starts, so a password set here restarts it below.
PASSWORD_SET=0
if [[ -f "$ADMIN_CRED" ]]; then
  ok "admin password set ($ADMIN_CRED)"
elif ((DRY_RUN)); then
  echo "    would: offer to set the admin password (sudo ops/set-admin-password.sh), on a terminal only"
elif [[ -t 0 ]]; then
  read -r -p "    No admin password, so the LAN fallback is off. Set it now? [Y/n] " answer
  if [[ ! "$answer" =~ ^[Nn] ]]; then
    "$OPS_DIR/set-admin-password.sh" || refuse "the admin password was not set; re-run the installer"
    PASSWORD_SET=1
  else
    echo "    skipped: the LAN fallback stays off until: sudo ops/set-admin-password.sh"
  fi
else
  echo "    note: no terminal, so no prompt; the LAN fallback stays off until: sudo ops/set-admin-password.sh"
fi

# ---------------------------------------------------------------------------------------
step "commissioning-node daemon"
CHANGED=$PASSWORD_SET   # a new password: restart, so the fallback listener starts
sed -e "s|@REPO_ROOT@|$REPO_ROOT|g" -e "s|@RUN_USER@|$RUN_USER|g" \
  "$OPS_DIR/systemd/commissioning-node.service" > "$work/commissioning-node.service"
sync_file "$work/commissioning-node.service" "$UNIT_DIR/commissioning-node.service" 0644
ensure_service commissioning-node.service "$CHANGED"
if [[ -d "$CN_STATE" ]]; then ok "$CN_STATE (config registry, created by systemd)"
else echo "    note: systemd creates $CN_STATE when commissioning-node starts (StateDirectory=)"; fi

# ---------------------------------------------------------------------------------------
if ((DRY_RUN)); then
  step "Done (dry run — nothing was changed)"
  exit 0
fi

step "Doctor (read-only)"
# A daemon (re)started above needs a moment before its dashboard answers.
for _ in $(seq 1 15); do curl -sf -o /dev/null http://127.0.0.1:8080/api/state && break; sleep 1; done
set +e
"$OPS_DIR/doctor.sh" | sed 's/^/    /'
doctor_rc="${PIPESTATUS[0]}"
set -e

# ---------------------------------------------------------------------------------------
step "Next"
# The site LAN addresses an operator's laptop can reach the fallback on (never tailscale0).
lan_ips="$(ip -4 -o addr show scope global 2>/dev/null | awk '$2 != "tailscale0" {sub(/\/.*/, "", $4); print $4}')"
ts_state="$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState",""))' 2>/dev/null || true)"
if [[ "$ts_state" == Running ]]; then
  echo "    On the tailnet: http://$(tailscale ip -4 2>/dev/null | head -1):8080/ (or the node's MagicDNS name)"
elif [[ -f "$ADMIN_CRED" ]]; then
  echo "    Join the tailnet from a laptop on the site LAN, through the admin fallback:"
  for ip in $lan_ips; do echo "      https://$ip:$FALLBACK_PORT/"; done
  [[ -f "$TLS_CERT" ]] && echo "      certificate SHA-256 $(openssl x509 -in "$TLS_CERT" -noout -fingerprint -sha256 | cut -d= -f2)"
  echo "    Log in, save the auth key (and hostname, tags) on /config, then slide Join the Tailnet"
  echo "    on the overview (ADR-0005)."
else
  echo "    Set the admin password (sudo ops/set-admin-password.sh), then re-run this installer:"
  echo "    it prints where to join the tailnet from."
fi
exit "$doctor_rc"
