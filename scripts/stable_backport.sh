#!/usr/bin/env bash
# Dispatcher for the ABK 5.15 LTS backport module_set.
#
# Composition contract (all stages are after_patch):
#   1. ABK_F2FS_FIX_MODULE storage rollbacks restore the storage baseline first.
#      Not a courtesy: Batch 43 opened fs/f2fs and drivers/scsi/ufs as graft
#      targets, so this ordering is the only configuration in which a storage
#      graft composes. The rollback is `git apply --reverse --check` of
#      android13-5.15-*-*.patch and breaks on a tree this module already
#      rewrote. See docs/porting_policy.md "Footprint overlap".
#   2. This module grafts upstream 5.15.y feature/optimization commits on top.
#   3. ABK_ABI_PATCH_SUITE runs last; its fd_alloc_hotpath probe detects the
#      upstream fdtable shape landed by stable_backport_core and adapts.

ABK_515_BACKPORT_PUBLIC_CHILDREN="stable_backport_core stable_perf_backport stable_display_fix"

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
    stable_display_fix) printf '%s/abk_stable_display.py\n' "$script_dir" ;;
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
}

abk_stable_backport_preflight_display() {
  local common_dir
  common_dir="$(abk_stable_backport_common_dir)"

  abk_require_file "$common_dir/Makefile"
  abk_require_file "$common_dir/drivers/gpu/drm/drm_atomic_helper.c"
}

# of/address.c ranges-parser revert (file overlay, not a Python graft).
#
# The upstream ranges flags-parsing rework (struct of_bus.has_flags ->
# flag_cells, the new "default-flags" bus and of_bus_default_flags_* helpers,
# the flag-preserving of_translate_one() memset) landed at 5.15.213.  On the
# SM8550 (vermeer) it splits the Qualcomm PCIe root complex's single contiguous
# MMIO ranges window in two, so the WCN (kiwi_v2) WLAN endpoint's 2 MB BAR0 --
# which straddles the split -- can no longer be placed: cnss_pci probe fails
# -EINVAL, msm_pcie tears the link down, and wlan0 never appears (BT unaffected,
# it is on a separate transport).  files/drivers/of/address.c carries the
# pre-rework 5.15.167 parser (with 5.15.216's unrelated __of_get_dma_parent
# of_node_get refcount fix preserved); overlaying it restores the single-window
# layout so BAR0 is assigned and WiFi comes up.  Verified on vermeer by flashing
# a 5.15.216 build carrying only this overlay.
#
# Gated on the rework marker so it is a no-op on any tree already in the target
# form.  That was the 5.15.167/.178/.194 case (dropped in Batch 44); the
# supported android13-5.15-lts tree carries the rework, so the overlay is live
# there.  A tree without the rework keeps its unrelated of_node_get line.  The original file is
# snapshotted to <file>.abk-orig once, matching the Python grafts' rollback
# convention (scripts/abk_rollback.sh restores it).
abk_stable_backport_overlay_of_address() {
  local common_dir target overlay
  common_dir="$(abk_stable_backport_common_dir)"
  target="$common_dir/drivers/of/address.c"
  overlay="$MODULE_DIR/files/drivers/of/address.c"

  abk_require_file "$target"
  abk_require_file "$overlay"

  if ! grep -q 'flag_cells' "$target" && ! grep -q '"default-flags"' "$target"; then
    abk_log "of/address.c: pre-rework baseline, ranges parser already in target form (no overlay)"
    return 0
  fi

  if cmp -s "$overlay" "$target"; then
    abk_log "of/address.c: overlay already in place"
    return 0
  fi

  [ -f "$target.abk-orig" ] || cp -a "$target" "$target.abk-orig"
  cp -a "$overlay" "$target"
  abk_log "of/address.c: overlaid pre-rework ranges parser (reverts the 5.15.213 rework; WLAN BAR0 fix)"
}

# The runtime companion (ksu/abk_runtime_tunables) is a distribution asset, not
# a graft: it registers no PatchGroup and never writes into the kernel tree.  It
# rides inside the AnyKernel3 zip so that flashing a kernel also installs the
# policy that makes the landed zram recompression actually run, and that fixes
# the primary algorithm the ROM's own zram owner leaves on the dominated lz4hc.
abk_stable_backport_bundle_ksu_module() {
  local output

  if [ "${ABK_515_KSU_MODULE:-1}" = "0" ]; then
    abk_log "runtime companion module disabled (ABK_515_KSU_MODULE=0)"
    return 0
  fi

  if ! output="$("$(abk_python)" "$MODULE_DIR/scripts/ak3_bundle_ksu_module.py" \
        inject --ak3-dir auto 2>&1)"; then
    printf '%s\n' "$output" >&2
    abk_die "failed to bundle the runtime companion KernelSU module into the AnyKernel3 tree"
  fi
  case "$output" in
    *"nothing to bundle"*)
      abk_warn "$output"
      abk_warn "this build has no AnyKernel3 tree; flash ksu/abk_runtime_tunables manually"
      return 0
      ;;
  esac

  abk_log "$output"
  "$(abk_python)" "$MODULE_DIR/scripts/ak3_bundle_ksu_module.py" verify --ak3-dir auto >/dev/null \
    || abk_die "the AnyKernel3 tree does not carry a valid runtime companion bundle"
  return 0
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
      stable_display_fix) abk_stable_backport_preflight_display ;;
    esac
    abk_stable_backport_apply_child "$child_id"
    # The of/address.c overlay rides with the core child (both are mm/of core
    # reverts).  Run it once, only when that child is selected, so a per-child
    # invocation does not overlay three times.
    if [ "$child_id" = "stable_backport_core" ]; then
      abk_stable_backport_overlay_of_address
    fi
    return 0
  fi

  abk_log "no ABK_MODULE_CHILD_ID set; running all public children in composition order"
  for child_id in $ABK_515_BACKPORT_PUBLIC_CHILDREN; do
    case "$child_id" in
      stable_backport_core) abk_stable_backport_preflight_core ;;
      stable_perf_backport) abk_stable_backport_preflight_perf ;;
      stable_display_fix) abk_stable_backport_preflight_display ;;
    esac
    abk_stable_backport_apply_child "$child_id"
  done
  # Overlay the pre-rework of/address.c once, after the Python children.
  abk_stable_backport_overlay_of_address
}
