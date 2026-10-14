# shellcheck shell=bash
# ops/pins.sh — the pinned host dependencies (spec §4 ops tier; ADR-0002). Sourced by
# install.sh and doctor.sh. Bumping a pin = edit here + re-verify every docs/quirks.md
# entry for that dependency + re-run ops/check_mediamtx_config.sh (for MediaMTX).
# shellcheck disable=SC2034  # variables are consumed by the scripts that source this file

# Tailscale — from Tailscale's signed apt repo, exact version, apt-mark hold.
TAILSCALE_VERSION="1.102.5"
TAILSCALE_REPO_URL="https://pkgs.tailscale.com/stable/debian"
TAILSCALE_SUITE="trixie"
# "Tailscale Inc. (Package repository signing key) <info@tailscale.com>", rsa4096, 2020-02-25.
TAILSCALE_KEY_FPR="2596A99EAAB33821893C0A79458CA832957F5868"

# MediaMTX — GitHub release tarball, sha256-verified, installed under /opt/mediamtx/<version>.
MEDIAMTX_VERSION="v1.21.1"
MEDIAMTX_ASSET="mediamtx_${MEDIAMTX_VERSION}_linux_arm64.tar.gz"
MEDIAMTX_URL="https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/${MEDIAMTX_ASSET}"
MEDIAMTX_SHA256="6a3aa635fb60ea9b8d566ec306f0a42ff1b6b52a3942bc2baffbe55880d4c3dd"

# nmap — Debian's own package (trixie main), exact version, apt-mark hold. Used unprivileged
# by site-survey's scanner (ADR-0007); the node never runs it as root.
NMAP_VERSION="7.95+dfsg-3"

# The host the pins are for. install.sh refuses anything else.
TARGET_CODENAME="trixie"
TARGET_ARCH="arm64"
