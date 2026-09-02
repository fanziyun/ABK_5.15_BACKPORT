#!/usr/bin/env bash
# Dispatcher for the ABK 5.15 LTS backport module_set.
#
# Composition contract (all stages are after_patch):
#   1. ABK_F2FS_FIX_MODULE storage rollbacks restore the storage baseline first.
#   2. This module grafts upstream 5.15.y feature/optimization commits on top.
#   3. ABK_ABI_PATCH_SUITE runs last; its fd_alloc_hotpath probe detects the
#      upstream fdtable shape landed by stable_backport_core and adapts.

ABK_515_BACKPORT_PUBLIC_CHILDREN="stable_backport_core stable_perf_backport"

abk_stable_backport_common_dir() {
  printf '%s\n' "$(abk_common_dir)"
}

abk_stable_backport_sub_level() {
  if [ -n "${ABK_BUILD_SUB_LEVEL:-}" ]; then
    printf '%s\n' "$ABK_BUILD_SUB_LEVEL"
    return 0
  fi
  abk_kernel_make_value SUBLEVEL
}

abk_stable_backport_target_family() {
  local android_version kernel_version

  android_version="${ABK_BUILD_ANDROID_VERSION:-}"
  kernel_version="${ABK_BUILD_KERNEL_VERSION:-}"
  if [ -z "$android_version" ] || [ -z "$kernel_version" ]; then
    kernel_version="$(abk_kernel_make_value VERSION).$(abk_kernel_make_value PATCHLEVEL)"
    if [ "$kernel_version" = "5.15" ]; then
      android_version="android13"
    fi
  fi

  if [ "$kernel_version" = "5.15" ]; then
    printf '%s\n' "${android_version:-android13}-5.15"
    return 0
  fi
  return 1
}

abk_stable_backport_report_dir() {
  local child_id="$1"
  printf '%s/abk_5_15_backport_reports/%s\n' "$KERNEL_ROOT" "$child_id"
}

abk_stable_backport_python_script() {
  local child_id="$1"
  local script_dir
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

  case "$child_id" in
    stable_backport_core) printf '%s/abk_stable_core.py\n' "$script_dir" ;;
    stable_perf_backport) printf '%s/abk_stable_perf.py\n' "$script_dir" ;;
    *) return 1 ;;
  esac
}

abk_stable_backport_preflight_core() {
  local common_dir
  common_dir="$(abk_stable_backport_common_dir)"

  abk_require_file "$common_dir/Makefile"
  abk_require_file "$common_dir/fs/file.c"
  abk_require_file "$common_dir/mm/page_alloc.c"
  abk_require_file "$common_dir/kernel/cgroup/cgroup.c"
}

abk_stable_backport_preflight_perf() {
  local common_dir
  common_dir="$(abk_stable_backport_common_dir)"

  abk_require_file "$common_dir/Makefile"
  abk_require_file "$common_dir/kernel/sched/fair.c"
  abk_require_file "$common_dir/kernel/sched/core.c"
  abk_require_file "$common_dir/kernel/sched/rt.c"
  abk_require_file "$common_dir/kernel/sched/features.h"
  abk_require_file "$common_dir/kernel/sched/psi.c"
  abk_require_file "$common_dir/include/linux/randomize_kstack.h"
  abk_require_file "$common_dir/kernel/fork.c"
  abk_require_file "$common_dir/net/core/sock.c"
  abk_require_file "$common_dir/kernel/locking/semaphore.c"
  abk_require_file "$common_dir/block/blk-mq.c"
  abk_require_file "$common_dir/mm/oom_kill.c"
}

abk_stable_backport_apply_child() {
  local child_id="$1"
  local script report_dir sub_level family
  local unsupported_flag

  script="$(abk_stable_backport_python_script "$child_id")" || {
    abk_die "unknown child id for abk_5_15_backport: $child_id (public children: $ABK_515_BACKPORT_PUBLIC_CHILDREN)"
  }

  family="$(abk_stable_backport_target_family)" || {
    abk_warn "target family is not android13-5.15; every group reports report_only and nothing is written (set ABK_515_ALLOW_UNSUPPORTED=1 to override)"
    family="unsupported"
  }
  unsupported_flag=""
  if [ "${ABK_515_ALLOW_UNSUPPORTED:-0}" = "1" ]; then
    unsupported_flag="--allow-unsupported"
  fi
  sub_level="$(abk_stable_backport_sub_level)"
  report_dir="$(abk_stable_backport_report_dir "$child_id")"
  mkdir -p "$report_dir"

  abk_log "child: $child_id (family=$family sublevel=$sub_level)"
  "$(abk_python)" "$script" \
    --common-dir "$(abk_stable_backport_common_dir)" \
    --defconfig "$DEFCONFIG" \
    --report-dir "$report_dir" \
    --sub-level "$sub_level" \
    --family "$family" ${unsupported_flag:+"$unsupported_flag"}
}

abk_stable_backport_apply_selected() {
  local child_id

  if [ -n "${ABK_MODULE_CHILD_ID:-}" ]; then
    child_id="$ABK_MODULE_CHILD_ID"
    case " $ABK_515_BACKPORT_PUBLIC_CHILDREN " in
      *" $child_id "*) ;;
      *)
        abk_die "unknown child id for abk_5_15_backport: $child_id (public children: $ABK_515_BACKPORT_PUBLIC_CHILDREN)"
        ;;
    esac
    case "$child_id" in
      stable_backport_core) abk_stable_backport_preflight_core ;;
      stable_perf_backport) abk_stable_backport_preflight_perf ;;
    esac
    abk_stable_backport_apply_child "$child_id"
    return 0
  fi

  abk_log "no ABK_MODULE_CHILD_ID set; running both children in composition order"
  for child_id in $ABK_515_BACKPORT_PUBLIC_CHILDREN; do
    case "$child_id" in
      stable_backport_core) abk_stable_backport_preflight_core ;;
      stable_perf_backport) abk_stable_backport_preflight_perf ;;
    esac
    abk_stable_backport_apply_child "$child_id"
  done
}
