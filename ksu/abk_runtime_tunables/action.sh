#!/system/bin/sh
# action.sh - manual entry point, also what the KernelSU action button runs.
#
#   action.sh [status]   what the module owns, what the kernel exposes (default)
#   action.sh pass       re-assert the zram policy and run one age-marked pass
#   action.sh takeover   rewrite zram now (algorithms, cap, swap, writeback),
#                        then print the status
#   action.sh unlock     print how the kernel-side lock is undone (boot cmdline)
#   action.sh launch [warm|drop] [iters]
#                        cold-launch instrument: force-stop and am start -W each
#                        app, and say whether the launch window was bound by the
#                        placer, by the cpuset, or by storage
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
    "primary=$ABK_ZRAM_PRIMARY secondary=$ABK_ZRAM_SECONDARY mem_limit=${ABK_ZRAM_MEM_LIMIT_PCT}% swap=ROM value"

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
    abk_show "recomp pass cap" "$(abk_cfg zram.recomp.max_pages 131072) attempted entries (0 = unbounded; needs the kernel max_pages graft)"
    abk_show "recomp mark cadence" "$(abk_cfg zram.recomp.mark_interval_sec 3600)s (sweeps in between pass --no-mark, so a capped sweep keeps advancing instead of re-draining its prefix)"
    if [ -n "$(abk_zram_secondary)" ]; then
      abk_show "recompression armed" "yes"
    else
      abk_show "recompression armed" "NO (every pass would be a no-op)"
    fi
    if abk_zram_writeback_capable; then
      abk_show "zram writeback" "supported (want=$(abk_zram_writeback_mode))"
      abk_show "boot_completed" "$(abk_getprop sys.boot_completed)"
      _st_wb_dev="$(abk_zram_backing_dev)"
      abk_show "backing_dev" "${_st_wb_dev:-none}"
      # "attached, but the file is gone" is the shape a per-boot wipe leaves
      # behind (init deletes /data/per_boot under the running loop), and it is
      # invisible in every node except this one: backing_dev still reads as a
      # device, while every writeback page returns -EIO.
      if [ -n "$_st_wb_dev" ]; then
        if abk_zram_backing_dev_ours "$_st_wb_dev"; then
          if [ -e "$ABK_ZRAM_WB_FILE" ]; then
            abk_show "backing_dev owner" "module ($ABK_ZRAM_WB_FILE)"
          else
            abk_show "backing_dev owner" "module, file gone -- writeback can move nothing"
          fi
        else
          abk_show "backing_dev owner" "ROM/foreign (left alone)"
        fi
      fi
      abk_show "writeback_limit" \
        "$(abk_read "$ABK_ZRAM_DIR/writeback_limit" | tr -d '\n') (enable $(abk_read "$ABK_ZRAM_DIR/writeback_limit_enable" | tr -d '\n'))"
      # bd_stat is the only honest verdict on this path: the kernel-domain rule
      # the module adds lives inside the policy, not in a readable node, so
      # "backing device attached, bd_stat 0 0 0" is exactly the shape of a
      # writeback that is being denied while reporting success.
      abk_show "bd_stat" "$(abk_read "$ABK_ZRAM_DIR/bd_stat" | tr -d '\n')"
      _sr_rule="not shipped"
      [ -f "$MODDIR/sepolicy.rule" ] && _sr_rule="shipped"
      abk_show "selinux" "$(getenforce 2>/dev/null || echo unknown) (kernel-domain rule $_sr_rule)"
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
  # Everything cpufreq/scheduler used to be here: the abk_sf_/abk_sc_
  # nodes, the governor enforcement, /proc/fas and the fas health probe.
  # Those moved to sailboat addon 2 (ksu/sailboat_addon_2), which owns
  # them and reports them from its own action.sh status.  Reading a node
  # this module no longer writes would report a knob nobody owns.
  abk_show "scheduler / cpufreq" \
    "owned by sailboat addon 2 (ksu/sailboat_addon_2): governor, abk_sf_* floor, abk_sc_* cap"
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

  # kcompressd is the kernel thread that takes the first swap-out write off the
  # reclaim path; from here its queue is visible only as the difference of its
  # three counters.  depth small while SwapCached stays large is the
  # written-but-not-dropped shape the supervisor warns about, so the two belong
  # in one place.  vm.page-cluster is printed by the loop above; it sizes the
  # swap readahead window (2^value pages) that faults pages back out of here.
  echo "-- kcompressd and swap-in --"
  _sr_kcd="$(abk_kcompressd_state_line)"
  abk_show "kcompressd" "${_sr_kcd#kcompressd: }"
  _sr_swc="$(abk_meminfo_kb SwapCached)"
  case "$_sr_swc" in
    ''|*[!0-9]*) abk_show "SwapCached" "absent" ;;
    *) abk_show "SwapCached" "$_sr_swc kB" ;;
  esac
  abk_show "kcompressd monitor" \
    "interval=$(abk_cfg kcompressd.monitor_interval_sec 600)s swapcached_warn=$(abk_cfg kcompressd.swapcached_warn_mb 256)MiB"
  abk_show_or_absent "swap.vma_ra_enabled" \
    "$ABK_SYS_ROOT/kernel/mm/swap/vma_ra_enabled"

  # The grafts this round added, exposed as module parameters (namespace =
  # the object's own name, so the sysfs path is module/<name>/parameters/ and
  # the cmdline form is <name>.<param>=...).  Absent on a kernel without the
  # graft, printed rather than treated as an error.
  echo "-- swap-in readahead (namespace readahead) --"
  for _sr_ra_par in swapin_window_enable swapin_boost_enable \
      swapin_boost_pages swapin_shrink_enable swapin_shrink_pages \
      swapin_window_background_only swapin_vma_ra; do
    abk_show_or_absent "$_sr_ra_par" \
      "$ABK_SYS_ROOT/module/readahead/parameters/$_sr_ra_par"
  done
  echo "-- fault-around (namespace memory) --"
  for _sr_mem_par in fault_around_enable fault_around_sys_uid \
      fault_around_app_only fault_around_app; do
    abk_show_or_absent "$_sr_mem_par" \
      "$ABK_SYS_ROOT/module/memory/parameters/$_sr_mem_par"
  done

  # Per-cgroup PSI: what the policy is meant to do, and what the tree says it
  # did.  The second is the honest one -- a refused write leaves the first a
  # statement of intent, which is how Batch 16's dark RCU graft looked healthy.
  echo "-- per-cgroup pressure (PSI) --"
  abk_show "psi.cgroup" "$(abk_psi_mode) (protect=$(abk_psi_protect), interval=$(abk_psi_interval)s)"
  _sr_psi_tool="${ABK_PSI_TOOL:-$MODDIR/bin/abk_psi_policy.sh}"
  if [ -f "$_sr_psi_tool" ]; then
    _sr_psi_n=0
    sh "$_sr_psi_tool" --status --cgroot "$ABK_CGROOT" 2>&1 | while IFS= read -r _sr_psi_line; do
      [ -n "$_sr_psi_line" ] || continue
      # One label, then the tool's follow-up hint (a refusal is a different
      # failure from a missing node, and the tool says which one it saw).
      if [ "$_sr_psi_n" = 0 ]; then
        abk_show "cgroup pressure nodes" "$_sr_psi_line"
        _sr_psi_n=1
      else
        printf '%-26s %s\n' "" "$_sr_psi_line"
      fi
    done
  else
    abk_show "cgroup pressure nodes" "absent ($_sr_psi_tool)"
  fi

  echo "-- supervisors --"
  abk_supervisor_state zram "zram sweeps"
  abk_supervisor_state cfr "proactive reclaim"
  abk_supervisor_state psi "per-cgroup PSI policy"
  abk_supervisor_state lru_gen "MGLRU reassert"
  abk_supervisor_state iorap-watch "launch record/prefetch"

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
  # Same fallbacks the supervisor uses (zram-policy.sh), so a manual pass and the
  # automatic one agree when a key is empty or absent from tunables.conf.
  _ap_age="$(abk_cfg zram.recomp.idle_age_sec 900)"
  _ap_threshold="$(abk_cfg zram.recomp.threshold 0)"
  _ap_max_pages="$(abk_cfg zram.recomp.max_pages 131072)"
  _ap_mode="$(abk_cfg zram.recomp.mode async)"
  _ap_tool="$MODDIR/bin/zram_recompress_trigger.sh"

  if [ ! -f "$_ap_tool" ]; then
    echo "trigger tool missing: $_ap_tool" >&2
    return 1
  fi

  sh "$_ap_tool" --sys-root "$ABK_SYS_ROOT" --device "$ABK_ZRAM_DEV" \
    --idle-age "$_ap_age" --mode "$_ap_mode" --threshold "$_ap_threshold" \
    --max-pages "$_ap_max_pages"
}

# The cold-launch instrument lives in tools/ and is shipped verbatim, so this is
# only a wrapper: the verdict rule and the screen guard belong to the tool, and
# duplicating either here is how the two copies start disagreeing.  `--iters` is
# passed straight through because a single launch cannot say anything about a
# median, and the arm chooses itself -- warm is what a user feels, drop is the
# storage's share of it.
abk_action_launch() {
  _al_tool="$MODDIR/bin/abk_launch_bench.sh"
  if [ ! -f "$_al_tool" ]; then
    echo "launch bench missing: $_al_tool" >&2
    return 1
  fi
  _al_mode="${2:-warm}"
  _al_iters="${3:-8}"
  sh "$_al_tool" --mode "$_al_mode" --iters "$_al_iters"
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
  launch)
    abk_action_launch "$@"
    ;;
  *)
    echo "usage: action.sh [status|pass|takeover|unlock|launch [warm|drop] [iters]]" >&2
    exit 2
    ;;
esac