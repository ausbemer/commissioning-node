#!/usr/bin/env bash
# ops/update.sh — the root updater (ADR-0009 D1, D3, D4).
#
# Run as root by commissioning-node-update.service when the daemon leaves a request
# (commissioning-node-update.path). NOT run from a tree: ops/install.sh installs a root-owned
# copy with the @...@ paths filled in, because a root unit must never run a file the daemon
# user can write. The request carries no code: one expected commit. Root fetches into its own
# release checkout, re-checks that commit is still the channel's target with that commit's own
# rule and adapter, installs it, restarts the daemon and runs the doctor, and puts the last
# release back if anything FAILs. It never runs a developer tree's code.
#
#   update           act on the pending request (if any)
#   update --after   the unit's ExecStopPost: mark a run that stopped early as unfinished
#
# Every step writes the result file (root's directory; the daemon reads it).
set -uo pipefail

TREE="@UPDATE_TREE@"
REPO_URL="@UPDATE_REPO_URL@"
STATE="@CN_STATE@"
RESULTS="@UPDATE_RESULT_DIR@"
HELPER="@UPDATE_PY@"
RUNNING="@RUNNING_FILE@"
DAEMON=commissioning-node.service

# git as root, hermetic: no system or global config, never a prompt.
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 LC_ALL=C

say() { echo "update: $*"; }
helper() { python3 -I "$HELPER" "$@"; }
result() { helper result --dir "$RESULTS" "$@"; }
now() { date +%s; }
field() { helper field "$RUNNING" "$1"; }
first_fail() { grep -m1 -E '^\s*FAIL' | sed -E 's/^\s*FAIL\s+//' | cut -c1-300; }
git_tree() { git -C "$TREE" "$@"; }

# install COMMIT REF WHO — check out COMMIT in root's tree, install it, restart the daemon, run
# the doctor. Output goes to the journal. Returns 0 only with a clean doctor; DOCTOR_FAIL holds
# the first FAIL line otherwise.
install_commit() {
  local commit="$1" ref="$2" who="$3" out
  DOCTOR_FAIL=""
  if ! { git_tree checkout --quiet --force --detach "$commit" && git_tree clean -ffdxq; }; then
    DOCTOR_FAIL="couldn't check out ${commit:0:12}"; return 1
  fi
  result phase=installing
  # install.sh exits with its closing doctor's verdict; a refusal says "refusing: ...".
  out="$(CN_INSTALL_REF="$ref" CN_INSTALLED_BY="$who" "$TREE/ops/install.sh" < /dev/null 2>&1)"
  local irc=$?
  echo "$out"
  systemctl restart "$DAEMON"
  if ((irc != 0)); then
    DOCTOR_FAIL="$(echo "$out" | grep -m1 -E '^refusing:|^\s*FAIL' | sed -E 's/^\s*(FAIL\s+|refusing:\s*)//' | cut -c1-300)"
    DOCTOR_FAIL="install.sh failed: ${DOCTOR_FAIL:-exit $irc}"
    return 1
  fi
  result phase=checking
  # The daemon needs a moment before its dashboard answers the doctor.
  for _ in $(seq 1 20); do curl -sf -o /dev/null http://127.0.0.1:8080/api/state && break; sleep 1; done
  out="$("$TREE/ops/doctor.sh" 2>&1)"
  local rc=$?
  echo "$out"
  if ((rc != 0)); then DOCTOR_FAIL="$(echo "$out" | first_fail)"; DOCTOR_FAIL="${DOCTOR_FAIL:-the doctor failed}"; return 1; fi
  return 0
}

finish() {  # finish OK MESSAGE [KEY=VALUE...]
  local ok="$1" message="$2"; shift 2
  result done=true phase= ok="$ok" message="$message" finished_at="$(now)" "$@"
  say "$message"
}

after() {
  # ExecStopPost: a run that never wrote done=true was stopped (timeout, kill). Its state is
  # unknown: say so, and don't touch anything (a rollback could hang the same way).
  [[ "$(helper field "$RESULTS/result.json" "done")" == false ]] || return 0
  finish false "the update stopped before it finished (${SERVICE_RESULT:-stopped}); the node's state is unknown: run sudo ops/doctor.sh"
}

main() {
  if [[ "${1:-}" == --after ]]; then after; return 0; fi
  local req rc
  req="$(helper read-request --state "$STATE" 2>&1)"; rc=$?
  if ((rc == 3)); then return 0; fi
  if ((rc != 0)); then
    say "refused a request: $req"
    result --reset done=true ok=false message="refused: $req" finished_at="$(now)"
    return 0
  fi
  local id channel expected who door
  { read -r id; read -r channel; read -r expected; read -r who; read -r door; } <<< "$req"
  local by="update by $who ($door)"
  local prev_commit prev_ref prev_mode prev_describe
  prev_commit="$(field commit)"; prev_ref="$(field ref)"; prev_mode="$(field mode)"; prev_describe="$(field describe)"
  say "request $id by $who ($door): $channel @ ${expected:0:12} (running ${prev_describe:-${prev_commit:0:12}}, $prev_mode)"
  result --reset request_id="$id" done=false ok=false phase=fetching requested_by="$who" door="$door" \
    started_at="$(now)" from_commit="$prev_commit" from_ref="$prev_ref" from_describe="$prev_describe" \
    to_commit="$expected" message=

  # Root's own checkout, over HTTPS (no key involved).
  if [[ ! -d "$TREE/.git" ]]; then
    if ! { rm -rf "$TREE.new" && git clone --quiet --no-checkout "$REPO_URL" "$TREE.new" \
      && chmod 0755 "$TREE.new" && mv "$TREE.new" "$TREE"; }; then
      finish false "couldn't clone $REPO_URL"; return 0
    fi
  fi
  git_tree remote set-url origin "$REPO_URL"
  if ! git_tree fetch --quiet --force --prune --tags origin '+refs/heads/*:refs/remotes/origin/*'; then
    finish false "couldn't fetch from GitHub"; return 0
  fi
  if ! git_tree cat-file -e "${expected}^{commit}" 2>/dev/null; then
    finish false "GitHub doesn't have ${expected:0:12}"; return 0
  fi

  # Is it still the channel's target, by that commit's own rule? Nothing is installed yet.
  result phase=verifying
  local here target
  here="$(git_tree rev-parse -q --verify HEAD 2>/dev/null || true)"
  git_tree checkout --quiet --force --detach "$expected" && git_tree clean -ffdxq
  if ! target="$(helper resolve --tree "$TREE" --channel "$channel" --running "$prev_commit" 2>&1)"; then
    [[ -n "$here" ]] && git_tree checkout --quiet --force --detach "$here"
    finish false "not installed: $target"; return 0
  fi
  local t_commit t_ref t_relation
  { read -r t_commit; read -r t_ref; read -r t_relation; } <<< "$target"
  [[ -n "$here" ]] && git_tree checkout --quiet --force --detach "$here"
  if [[ "$t_commit" != "$expected" ]]; then
    finish false "not installed: the channel moved since you looked (it now offers ${t_ref} ${t_commit:0:12}); check again"
    return 0
  fi
  case "$t_relation" in
    ahead | diverged | unknown) ;;
    behind) finish false "not installed: ${t_ref} is older than what runs"; return 0 ;;
    identical) finish false "not installed: ${t_ref} is what runs already"; return 0 ;;
    *) finish false "not installed: ${t_ref} relates to what runs as '$t_relation'"; return 0 ;;
  esac
  result to_ref="$t_ref"

  if install_commit "$expected" "$t_ref" "$by"; then
    finish true "updated to $t_ref ${expected:0:12}; doctor clean" to_describe="$(field describe)"
    return 0
  fi
  local why="$DOCTOR_FAIL"
  say "update to ${expected:0:12} failed: $why"

  # D4: put the last release back. Never a developer tree: root would run that tree's code.
  if [[ "$prev_mode" != release || -z "$prev_commit" ]]; then
    finish false "the switch from the developer tree failed ($why). There is no automatic way back to a developer tree: run sudo ops/install.sh from it"
    return 0
  fi
  result phase=rolling-back
  if install_commit "$prev_commit" "$prev_ref" "rollback after a failed $by"; then
    finish false "rolled back to ${prev_describe:-${prev_commit:0:12}}: the update failed ($why)" rolled_back=true
  else
    finish false "the update failed ($why) and so did the rollback to ${prev_commit:0:12} ($DOCTOR_FAIL); fix over SSH" rolled_back=true
  fi
}

main "$@"
exit 0
