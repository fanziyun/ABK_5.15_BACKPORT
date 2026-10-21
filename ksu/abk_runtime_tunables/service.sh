#!/system/bin/sh
set -u

MODDIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
. "$MODDIR/common.sh"
. "$MODDIR/zram-policy.sh"
. "$MODDIR/iorap-watch.sh"

case "${1:-}" in
  --supervise-zram) abk_zram_supervisor_main; exit 0 ;;
  --supervise-cfr) abk_cfr_supervisor_main; exit 0 ;;
  --supervise-psi) abk_psi_supervisor_main; exit 0 ;;
  --supervise-lru-gen) abk_lru_gen_supervisor_main; exit 0 ;;
  --supervise-iorap) abk_iorap_main; exit 0 ;;
esac

mkdir -p "$ABK_STATE_DIR" "$ABK_RUN_DIR" 2>/dev/null || true

abk_spawn() {
  _sp_arg="$1"
  _sp_name="$2"

  # SIGKILL: a sleeping supervisor defers SIGTERM for up to 30 min; it holds no state.
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

# A full cgroup walk with a blocking write per group: supervisor, not post-fs-data.
if [ "$(abk_psi_mode)" = keep ]; then
  abk_log "psi.cgroup=keep: per-cgroup pressure accounting left as the kernel set it"
else
  abk_spawn --supervise-psi psi
fi

# The ROM's init rewrites lru_gen/enabled to 0 after post-fs-data.
if [ "$(abk_cfg lru_gen.enable 0)" = "1" ] \
  && [ -e "$ABK_SYS_ROOT/kernel/mm/lru_gen/enabled" ]; then
  abk_spawn --supervise-lru-gen lru_gen
fi

# Gated on the knob, not a version: without bg_launch_prefetch the watcher exits.
if [ -e /sys/module/filemap/parameters/abk_lp_enable ] \
  && [ "$(abk_cfg iorap.watch 1)" = "1" ]; then
  abk_spawn --supervise-iorap iorap-watch
fi

abk_log "service: done"
