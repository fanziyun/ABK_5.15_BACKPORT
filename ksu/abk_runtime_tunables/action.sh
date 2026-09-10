#!/system/bin/sh
# action.sh - manual entry point, also what the KernelSU action button runs.
#
#   action.sh [status]   what the module owns, what the kernel exposes (default)
#   action.sh pass       re-assert the zram policy and run one age-marked pass
#   action.sh takeover   rewrite zram now (algorithms, cap, swap, writeback),
#                        then print the status
#   action.sh unlock     print how the kernel-side lock is undone (boot cmdline)
set -u

MODDIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
. "$MODDIR/common.sh"
. "$MODDIR/zram-policy.sh"

ABK_STDOUT=1

abk_show() {
  printf '%-26s %s\n' "$1" "$2"
}

abk_show_or_absent() { # <label> <path>
  if [ -e "$2" ]; then
    abk_show "$1" "$(abk_read "$2" | tr -d '\n')"
  else
    abk_show "$1" "absent"
  fi
}

abk_supervisor_state() { # <pid name> <label>
  if abk_pid_live "$1"; then
    abk_show "$2" "running (pid $(abk_state_get "$1.pid"))"
  elif [ -f "$(abk_state_path "$1.pid")" ]; then
    abk_show "$2" "dead (stale pid $(abk_state_get "$1.pid"))"
  else
    abk_show "$2" "not running"
  fi
}

abk_status_report() {
  echo "== ABK 5.15 Runtime Tunables $ABK_VERSION =="
  abk_show "kernel" "$(uname -r 2>/dev/null)"
  abk_show "policy (fixed, no knob)" \
    "primary=$ABK_ZRAM_PRIMARY secondary=$ABK_ZRAM_SECONDARY mem_limit=${ABK_ZRAM_MEM_LIMIT_PCT}%swap=ROM value"

  if [ -f "$ABK_SYS_ROOT/fs/cgroup/cgroup.controllers" ]; then
    abk_show "cgroup" "v2 ($ABK_SYS_ROOT/fs/cgroup)"
  fi
  if [ -e "$ABK_MEMCG_ROOT/memory.reclaim" ]; then
    abk_show "cgroup v1 reclaim" "$ABK_MEMCG_ROOT/memory.reclaim present"
  fi

  echo "-- zram$ABK_ZRAM_DEV --"
  if abk_zram_present; then
    abk_show "disksize" "$(abk_zram_disksize)"
    abk_show "initstate" "$(abk_zram_initstate)"
    abk_show "primary (comp_algorithm)" "$(abk_zram_primary)"
    abk_show "secondary (recomp)" "$(abk_zram_secondary)"
    if abk_zram_swap_on; then
      abk_show "swap" "on (prio $(abk_zram_swap_prio), used $(abk_zram_swap_used_kb) KiB)"
    else
      abk_show "swap" "not mounted"
    fi
    abk_show "mem_limit (mm_stat f4)" "$(abk_zram_mem_limit)"
    abk_show "mm_stat" "$(abk_read "$ABK_ZRAM_DIR/mm_stat" | tr -d '\n')"
    abk_show "io_stat" "$(abk_read "$ABK_ZRAM_DIR/io_stat" | tr -d '\n')"
    if [ -n "$(abk_zram_secondary)" ]; then
      abk_show "recompression armed" "yes"
    else
      abk_show "recompression armed" "NO (every pass would be a no-op)"
    fi
    if abk_zram_writeback_capable; then
      abk_show "zram writeback" "supported (want=$(abk_zram_writeback_mode))"
      abk_show "backing_dev" "$(abk_zram_backing_dev)"
      abk_show "writeback_limit" \
        "$(abk_read "$ABK_ZRAM_DIR/writeback_limit" | tr -d '\n') (enable $(abk_read "$ABK_ZRAM_DIR/writeback_limit_enable" | tr -d '\n'))"
    else
      abk_show "zram writeback" "not built in (CONFIG_ZRAM_WRITEBACK off)"
    fi
    if abk_zram_need_rewrite; then
      abk_show "policy" "not in force -- a rewrite is pending"
    else
      abk_show "policy" "in force"
    fi
  else
    abk_show "zram$ABK_ZRAM_DEV" "absent ($ABK_ZRAM_DIR)"
  fi

  echo "-- kernel grafts --"
  if abk_zram_kernel_locked; then
    abk_show "zram.abk_lock_algo" \
      "$(abk_read "$ABK_ZRAM_LOCK_PARAM" | tr -d '\n') (runtime algorithm writes are no-ops)"
  else
    abk_show "zram.abk_lock_algo" "absent (module enforces the policy instead)"
  fi
  abk_show_or_absent "zram.abk_comp_algo" "$ABK_ZRAM_COMP_PARAM"
  abk_show_or_absent "zram.abk_recomp_algo" \
    "$ABK_SYS_ROOT/module/zram/parameters/abk_recomp_algo"
  abk_show_or_absent "dynamic_readahead" \
    "$ABK_SYS_ROOT/module/readahead/parameters/dynamic_readahead"
  abk_show_or_absent "abk_sf_enable" \
    "$ABK_SYS_ROOT/module/cpufreq_schedutil/parameters/abk_sf_enable"
  abk_show_or_absent "abk_sf_floor_pct" \
    "$ABK_SYS_ROOT/module/cpufreq_schedutil/parameters/abk_sf_floor_pct"
  abk_show "pelt multiplier" \
    "$(sed -n 's/.*sysctl\.kernel\.sched_pelt_multiplier=\([0-9]*\).*/\1/p' /proc/cmdline 2>/dev/null)"
  abk_show "mmd.setup_complete" "$(abk_getprop mmd.setup_complete)"

  echo "-- reclaim and vm knobs --"
  for _sr_node in swappiness page-cluster watermark_scale_factor min_free_kbytes; do
    abk_show_or_absent "vm.$_sr_node" "/proc/sys/vm/$_sr_node"
  done
  abk_show_or_absent "lru_gen/enabled" "$ABK_SYS_ROOT/kernel/mm/lru_gen/enabled"
  abk_show_or_absent "lru_gen/min_ttl_ms" "$ABK_SYS_ROOT/kernel/mm/lru_gen/min_ttl_ms"
  abk_show_or_absent "transparent_hugepage" \
    "$ABK_SYS_ROOT/kernel/mm/transparent_hugepage/enabled"

  echo "-- supervisors --"
  abk_supervisor_state zram "zram sweeps"
  abk_supervisor_state cfr "proactive reclaim"

  # logcat is best-effort on some ROMs, so the module keeps its own log.
  echo "-- module log --"
  _sr_log="$(abk_log_file)"
  if [ -s "$_sr_log" ]; then
    abk_show "log file" "$_sr_log"
    tail -n 12 "$_sr_log"
  else
    abk_show "log file" "$_sr_log (empty)"
  fi
  return 0
}

abk_action_pass() {
  abk_cfg_lint
  abk_zram_reassert || true
  _ap_age="$(abk_cfg zram.recomp.idle_age_sec 3600)"
  _ap_threshold="$(abk_cfg zram.recomp.threshold 0)"
  _ap_mode="$(abk_cfg zram.recomp.mode async)"
  _ap_tool="$MODDIR/bin/zram_recompress_trigger.sh"

  if [ ! -f "$_ap_tool" ]; then
    echo "trigger tool missing: $_ap_tool" >&2
    return 1
  fi

  sh "$_ap_tool" --sys-root "$ABK_SYS_ROOT" --device "$ABK_ZRAM_DEV" \
    --idle-age "$_ap_age" --mode "$_ap_mode" --threshold "$_ap_threshold"
}

# The lock is a read-only kernel parameter: only the boot cmdline can turn it
# off, which is the point (nothing running on the device can).
abk_action_unlock() {
  echo "the compressor lock is a read-only kernel parameter (0444):"
  echo "  /sys/module/zram/parameters/abk_lock_algo   (zram.abk_lock_algo=0)"
  echo "  /sys/module/zram/parameters/abk_comp_algo   (zram.abk_comp_algo=lz4)"
  echo "change it on the kernel cmdline, not here -- a runtime write is ignored"
  echo "by design, and the module re-asserts the policy on the next tick anyway."
}

case "${1:-status}" in
  status)
    abk_status_report
    ;;
  pass)
    abk_action_pass
    abk_status_report
    ;;
  takeover)
    abk_zram_takeover
    abk_status_report
    ;;
  unlock)
    abk_action_unlock
    ;;
  *)
    echo "usage: action.sh [status|pass|takeover|unlock]" >&2
    exit 2
    ;;
esac
