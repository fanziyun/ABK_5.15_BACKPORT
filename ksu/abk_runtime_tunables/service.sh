#!/system/bin/sh
# service.sh - late stage of the ABK 5.15 runtime-tunables module.
#
# Waits for the ROM's own zram bring-up to settle, takes the configuration over
# with the fixed best-algorithm policy (see zram-policy.sh), then supervises
# the recompression sweeps.  Also re-enters itself for the long-running
# supervisors, so each of them can record its own pid.
set -u

MODDIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
. "$MODDIR/common.sh"
. "$MODDIR/zram-policy.sh"

case "${1:-}" in
  --supervise-zram) abk_zram_supervisor_main; exit 0 ;;
  --supervise-cfr) abk_cfr_supervisor_main; exit 0 ;;
  --supervise-psi) abk_psi_supervisor_main; exit 0 ;;
esac

mkdir -p "$ABK_STATE_DIR" "$ABK_RUN_DIR" 2>/dev/null || true

# abk_spawn <re-entry argument> <pid name>
abk_spawn() {
  _sp_arg="$1"
  _sp_name="$2"

  # A module update or a second service.sh run can leave an older supervisor
  # behind; it would keep sweeping with the previous copy of the script.  Kill
  # the matching ones first -- this process's own cmdline has no such argument,
  # so it never matches itself.  SIGKILL on purpose: the supervisor spends its
  # life in `sleep 1800`, and a shell holding a foreground child defers SIGTERM
  # until that child exits, so a polite signal leaves it running for half an
  # hour (measured on device).  It owns no state, so there is nothing to flush.
  if command -v pkill >/dev/null 2>&1; then
    pkill -9 -f "service.sh $_sp_arg" 2>/dev/null || true
    sleep 1
  elif abk_pid_live "$_sp_name"; then
    abk_log "$_sp_name supervisor already running (pid $(abk_state_get "$_sp_name.pid"))"
    return 0
  fi

  setsid sh "$MODDIR/service.sh" "$_sp_arg" >/dev/null 2>&1 &
  _sp_n=0
  while [ "$_sp_n" -lt 10 ]; do
    abk_pid_live "$_sp_name" && break
    sleep 1
    _sp_n=$(( _sp_n + 1 ))
  done

  if abk_pid_live "$_sp_name"; then
    abk_log "$_sp_name supervisor started (pid $(abk_state_get "$_sp_name.pid"))"
  else
    abk_warn "$_sp_name supervisor did not start"
  fi
  return 0
}

abk_log "service: kernel $(uname -r 2>/dev/null)"
abk_cfg_lint

abk_zram_wait_ready
if abk_zram_ensure; then
  abk_log "zram policy in force: primary=$(abk_zram_primary) secondary=$(abk_zram_secondary)"
else
  abk_warn "zram policy is not in force yet; the supervisor will re-check"
fi

if [ "$(abk_cfg zram.recomp.enable 1)" = "1" ]; then
  abk_spawn --supervise-zram zram
else
  abk_log "zram.recomp.enable=0: recompression sweeps disabled"
fi

if [ "$(abk_cfg cfr.enable 0)" = "1" ]; then
  abk_spawn --supervise-cfr cfr
fi

# Per-cgroup PSI accounting: a full tree walk with a blocking write per
# group, so it never belongs in post-fs-data -- it belongs to a supervisor that
# can take its time and catch the groups the apps create later.  With
# psi.cgroup=keep nothing is spawned at all: one decision here, no silent loop.
if [ "$(abk_psi_mode)" = keep ]; then
  abk_log "psi.cgroup=keep: per-cgroup pressure accounting left as the kernel set it"
else
  abk_spawn --supervise-psi psi
fi

abk_log "service: done"