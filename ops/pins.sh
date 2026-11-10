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

# kvaser_usb — the mainline SocketCAN driver for the Kvaser Leaf Lite v2 (ADR-0010 D6). This
# node's kernel is built without it (CONFIG_CAN_KVASER_USB unset), so install.sh builds it
# with DKMS from these upstream files at the tag matching the kernel series, each sha256-checked.
# Verified 2026-11-10: builds against 6.18.50+rpt-rpi-2712, aliases usb:v0BFDp0120 (Leaf Lite v2).
# Bumping the tag = fetch, check the build against the new kernel, update every hash.
KVASER_USB_SRC_TAG="v6.18.50"
KVASER_USB_SRC_URL="https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/plain/drivers/net/can/usb/kvaser_usb"
KVASER_USB_SRC_SHA256=(
  "Makefile 16a6d08b2eefbf4897be4570df7d221616c4e7c1748703f03a625bc184c1db88"
  "kvaser_usb.h c8e57ac02cea54df60a58385e80c938e2e5be8a618ba46e649cba83328f35d40"
  "kvaser_usb_core.c 772b7a1b8466a19f5e9ab77e194aab389132c8359505ddf4921bd64ff8bff096"
  "kvaser_usb_devlink.c 873d85f6f041d098ad24a68c5da783cf763605fd4a0564fbb319a79616f975cc"
  "kvaser_usb_hydra.c 7620938b41b8fa6ecddf8016c9d599b013c622d4b465f9ac0879d99851311045"
  "kvaser_usb_leaf.c 152075281a990ab8d8b6cacf0cf50000501016325a342127bd471c3ef2dcefa2"
)

# can-utils — Debian's own package, exact version, apt-mark hold. The doctor and the replay
# corpus (candump -l) use it; the daemon does not (ADR-0010).
CAN_UTILS_VERSION="2023.03-1+b2"

# The host the pins are for. install.sh refuses anything else.
TARGET_CODENAME="trixie"
TARGET_ARCH="arm64"
