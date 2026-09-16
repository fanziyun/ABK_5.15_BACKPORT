#!/usr/bin/env bash
# End-to-end smoke test for abk_5_15_backport.
#
# Builds a disposable KERNEL_ROOT from a source kernel tree (only the files
# the module touches), runs setup.sh for both children twice (idempotency),
# and asserts the reported statuses and in-tree markers.
#
# Usage:
#   bash tests/smoke.sh /path/to/android13-5.15-common-kernel-tree
#
# The source tree only needs the paths listed in SMOKE_FILES; a full checkout
# works as-is.

set -euo pipefail

MODULE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_TREE="${1:-${SMOKE_SOURCE_TREE:-}}"

if [ -z "$SOURCE_TREE" ]; then
  echo "usage: bash tests/smoke.sh /path/to/kernel-common-tree" >&2
  echo "(or set SMOKE_SOURCE_TREE; a tree at ../linux-common-android13-5.15 is picked up automatically)" >&2
  exit 2
fi
if [ ! -d "$SOURCE_TREE" ]; then
  echo "source tree not found: $SOURCE_TREE" >&2
  exit 2
fi

SMOKE_FILES=(
  Makefile
  Documentation/admin-guide/kernel-parameters.txt
  Documentation/admin-guide/cgroup-v2.rst
  fs/file.c
  mm/page_alloc.c
  mm/compaction.c
  mm/internal.h
  kernel/rcu/Kconfig
  kernel/rcu/tree_nocb.h
  mm/oom_kill.c
  mm/vmscan.c
  mm/memcontrol.c
  include/linux/swap.h
  include/linux/cgroup-defs.h
  include/linux/cpuset.h
  include/linux/mmzone.h
  include/linux/randomize_kstack.h
  include/linux/sched.h
  include/linux/psi_types.h
  include/linux/psi.h
  include/trace/hooks/dtask.h
  include/trace/hooks/mm.h
  include/trace/hooks/rwsem.h
  drivers/android/vendor_hooks.c
  kernel/cgroup/cgroup-internal.h
  kernel/cgroup/cgroup.c
  kernel/cgroup/cpuset.c
  kernel/sched/sched.h
  kernel/sched/core.c
  kernel/sched/fair.c
  kernel/sched/rt.c
  kernel/sched/features.h
  kernel/sched/stats.h
  kernel/sched/psi.c
  kernel/sched/cpufreq_schedutil.c
  kernel/fork.c
  kernel/locking/semaphore.c
  kernel/locking/mutex.c
  kernel/locking/rwsem.c
  init/main.c
  net/core/sock.c
  block/blk-mq.c
  drivers/block/zram/Kconfig
  drivers/block/zram/zram_drv.h
  drivers/block/zram/zram_drv.c
  mm/zsmalloc.c
  include/linux/zsmalloc.h
  arch/arm64/configs/gki_defconfig
  mm/Kconfig
  mm/readahead.c
  mm/filemap.c
  mm/khugepaged.c
  mm/madvise.c
  include/linux/huge_mm.h
  include/uapi/asm-generic/mman-common.h
  drivers/gpu/drm/drm_atomic_helper.c
  # Batch 15: ABK_ABI_PATCH_SUITE absorption.  The absorbed groups anchor in
  # these files; without them smoke's fixture reports blocked_by_shape for
  # pid_alloc_hotpath_phase2, slab_alloc_free_hotpath,
  # hugepage_fault_alloc_fastpath and every blk_mq_async_depth /
  # sched-refinement / EEVDF step.
  kernel/pid.c
  mm/slub.c
  mm/huge_memory.c
  mm/memory.c
  include/linux/blkdev.h
  block/blk-core.c
  block/blk-mq-sched.c
  block/blk-sysfs.c
  # Batch 20: the quiesced elevator switch renames the elevator_switch_mq()
  # declaration in blk.h; without this file the group degrades to
  # blocked_by_shape in smoke only (the reference trees do carry it).
  block/blk.h
  block/elevator.c
  block/mq-deadline.c
  block/bfq-iosched.c
  block/kyber-iosched.c
  include/linux/sched/nohz.h
  include/linux/tick.h
  kernel/time/tick-sched.c
  kernel/sched/idle.c
  # Batch 31: the arm64 pte_mkwrite() dirty guard (5.15.196).
  arch/arm64/include/asm/pgtable.h
)

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
KERNEL_ROOT="$WORK/root"
mkdir -p "$KERNEL_ROOT/common" "$KERNEL_ROOT/build/kernel"

for rel in "${SMOKE_FILES[@]}"; do
  mkdir -p "$KERNEL_ROOT/common/$(dirname "$rel")"
  cp "$SOURCE_TREE/$rel" "$KERNEL_ROOT/common/$rel"
done

export KERNEL_ROOT
export DEFCONFIG="$KERNEL_ROOT/common/arch/arm64/configs/gki_defconfig"
export CUSTOM_EXTERNAL_MODULE_STAGE="after_patch"
unset ABK_MODULE_CHILD_ID || true

python_bin="$(command -v python3 || command -v python)"

# The engine gates on text anchors, so a group whose upstream commit the target
# baseline already carries correctly reports already_present.  Expected counts
# therefore come from tests/sublevel_matrix.py, keyed by the tree's SUBLEVEL.
SUB_LEVEL="${ABK_TEST_SUB_LEVEL:-$(awk '$1 == "SUBLEVEL" && $2 == "=" { print $3; exit }' "$SOURCE_TREE/Makefile")}"
if [ -z "$SUB_LEVEL" ]; then
  echo "could not determine SUBLEVEL from $SOURCE_TREE/Makefile; set ABK_TEST_SUB_LEVEL" >&2
  exit 2
fi
echo "== target baseline: 5.15.$SUB_LEVEL =="

echo "== pass 1: graft =="
bash "$MODULE_DIR/setup.sh" >"$WORK/pass1.log" 2>&1 || { cat "$WORK/pass1.log"; exit 1; }
tail -2 "$WORK/pass1.log"
cp -r "$KERNEL_ROOT/abk_5_15_backport_reports" "$WORK/pass1_reports"

echo "== pass 2: idempotency =="
bash "$MODULE_DIR/setup.sh" >"$WORK/pass2.log" 2>&1 || { cat "$WORK/pass2.log"; exit 1; }
tail -2 "$WORK/pass2.log"

echo "== assertions =="
fail() { echo "FAIL: $*" >&2; exit 1; }

# The per-child JSON assertions below are the authoritative degradation gate:
# they compare the exact status summaries against sublevel_matrix (whose
# KNOWN_DEBT table is empty since the .211 blockers were closed, so every group
# of every child must land on every baseline), so a blanket log grep here would
# wrongly fail a baseline that legitimately reports already_present.  Missing
# report files are guarded inside the loop itself.
for child in stable_backport_core stable_perf_backport stable_display_fix; do
  [ -f "$WORK/pass1_reports/$child/${child}_report.json" ] || fail "missing pass1 report for $child"
  [ -f "$KERNEL_ROOT/abk_5_15_backport_reports/$child/${child}_report.json" ] || fail "missing pass2 report for $child"
  "$python_bin" - "$WORK/pass1_reports/$child/${child}_report.json" \
      "$KERNEL_ROOT/abk_5_15_backport_reports/$child/${child}_report.json" \
      "$MODULE_DIR/tests" "$SUB_LEVEL" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[3])
import sublevel_matrix

p1 = json.load(open(sys.argv[1]))
p2 = json.load(open(sys.argv[2]))
sub_level = sys.argv[4]
child = p1["child"]
exp1 = sublevel_matrix.status_summary(sub_level, child)
exp2 = sublevel_matrix.idempotent_summary(sub_level, child)
assert p1["status_summary"] == exp1, (child, sub_level, "pass1", p1["status_summary"], exp1)
assert p2["status_summary"] == exp2, (child, sub_level, "pass2", p2["status_summary"], exp2)
debts = sublevel_matrix.debt(sub_level, child)
degraded = [g["key"] for g in p1["groups"]
            if g["status"] not in ("applied", "already_present")
            and debts.get(g["key"]) != g["status"]]
assert not degraded, (child, sub_level, "degraded groups", degraded)
print(f"  {child}: pass1={p1['status_summary']} pass2={p2['status_summary']}")
PY
done

# Markers that must be present on every supported baseline: either this module
# grafted them, or the baseline already carried the upstream commit.
grep -q "alloc_fdtable(unsigned int slots_wanted)" "$KERNEL_ROOT/common/fs/file.c" \
  || fail "fdtable conventions marker missing"
# The kstack KABI slot marker only exists where randomize_kstack_pertask really
# lands; on the .211 lts baseline it is a known debt (blocked_by_shape), so the
# marker is legitimately absent there.
if "$python_bin" - "$MODULE_DIR/tests" "$SUB_LEVEL" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import sublevel_matrix
sub = sys.argv[2]
key = "randomize_kstack_pertask"
expected = (key not in sublevel_matrix.pre_applied(sub, "stable_perf_backport")
            and key not in sublevel_matrix.debt(sub, "stable_perf_backport"))
sys.exit(0 if expected else 1)
PY
then
  grep -q "ANDROID_KABI_USE(8" "$KERNEL_ROOT/common/include/linux/sched.h" \
    || fail "kstack KABI slot marker missing"
else
  echo "  kstack KABI slot marker not expected on 5.15.$SUB_LEVEL (known debt)"
fi
grep -q "cgroup_free_wq" "$KERNEL_ROOT/common/kernel/cgroup/cgroup.c" \
  || fail "cgroup wq split marker missing"
grep -q "__raise_softirq_irqoff(SCHED_SOFTIRQ);" "$KERNEL_ROOT/common/kernel/sched/core.c" \
  || fail "nohz core.c marker missing"
grep -q '"reclaim",' "$KERNEL_ROOT/common/mm/memcontrol.c" \
  || fail "memory.reclaim cft entry missing"
grep -q "psi_account_irqtime" "$KERNEL_ROOT/common/kernel/sched/psi.c" \
  || fail "PSI IRQ accounting missing"
grep -q "android_vh_resched_curr_lazy" "$KERNEL_ROOT/common/include/trace/hooks/dtask.h" \
  || fail "lazy preemption hook missing"
grep -q "android_vh_mutex_wakeup_patch" "$KERNEL_ROOT/common/kernel/locking/mutex.c" \
  || fail "mutex wakeup patch hook missing"
# blk_mq_quiesced_elevator_switch is an upstream-shape rewrite with no marker of
# its own, so the check is the rewritten call site itself: this module wrote it
# on 167/178/194 and the baseline already carried it on 216.
grep -q "elevator_switch(q, NULL);" "$KERNEL_ROOT/common/block/blk-mq.c" \
  || fail "quiesced elevator switch call site missing"
# Same shape for sched_steal_time_excess_drop: markerless upstream rewrite, so
# the rewritten accumulator store is the assertion (grafted on 167/178, already
# upstream on 194/216).
grep -q "rq->prev_steal_time_rq = prev_steal;" \
  "$KERNEL_ROOT/common/kernel/sched/core.c" \
  || fail "excess steal time drop missing"
# The smart-freq payload's load-bearing markers.  The group is inert by *default*
# (the knob ships off), so nothing in the pass-1 status proves the gated form is
# the one in the tree -- and an older payload mixed into this file would be a
# duplicate-definition compile failure, not a status the report could show.
if "$python_bin" - "$MODULE_DIR/tests" "$SUB_LEVEL" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import sublevel_matrix
sub = sys.argv[2]
key = "schedutil_smart_policy"
expected = (key not in sublevel_matrix.pre_applied(sub, "stable_perf_backport")
            and key not in sublevel_matrix.debt(sub, "stable_perf_backport"))
sys.exit(0 if expected else 1)
PY
then
  grep -q "static bool abk_sf_dvfs_owned(struct cpufreq_policy \*policy)" \
    "$KERNEL_ROOT/common/kernel/sched/cpufreq_schedutil.c" \
    || fail "smart-freq DVFS-ownership gate missing"
  grep -q "static bool abk_sf_enable = false;" \
    "$KERNEL_ROOT/common/kernel/sched/cpufreq_schedutil.c" \
    || fail "smart-freq policy is not the default-off payload"
  grep -q "module_param_cb(abk_sf_boosting" \
    "$KERNEL_ROOT/common/kernel/sched/cpufreq_schedutil.c" \
    || fail "smart-freq reason state is not observable"
else
  echo "  smart-freq markers not expected on 5.15.$SUB_LEVEL (known debt)"
fi
grep -q "struct psi_trigger_ext" "$KERNEL_ROOT/common/include/linux/psi_types.h" \
  || fail "kernfs polling trigger wrapper missing"
# psi_cgroup_pressure_switch: the knob, the flag it drives, and the KMI promise
# that neither struct grew (a "bool enabled" in psi_group, or a psi_group
# pointer plus psi_files[] in struct cgroup, is the ACK 6.1 shape that cannot be
# ported onto a 5.15 psi_group embedded in struct cgroup).
grep -q 'name = "cgroup.pressure"' "$KERNEL_ROOT/common/kernel/cgroup/cgroup.c" \
  || fail "cgroup.pressure file missing"
grep -q "CGRP_PSI_DISABLED" "$KERNEL_ROOT/common/include/linux/cgroup-defs.h" \
  || fail "cgroup PSI accounting flag missing"
grep -q "psi_group_enabled" "$KERNEL_ROOT/common/kernel/sched/psi.c" \
  || fail "per-cgroup PSI accounting query missing"
grep -q "static ssize_t pressure_write(struct kernfs_open_file \*of, char \*buf," \
  "$KERNEL_ROOT/common/kernel/cgroup/cgroup.c" \
  || fail "trigger writer did not yield the cgroup_pressure_write name"
if grep -q "struct psi_group \*psi;" "$KERNEL_ROOT/common/include/linux/cgroup-defs.h" \
   || grep -q "psi_files\[" "$KERNEL_ROOT/common/include/linux/cgroup-defs.h"; then
  fail "the ACK 6.1 struct cgroup growth leaked into the port"
fi
grep -q "calculate_zspage_chain_size" "$KERNEL_ROOT/common/mm/zsmalloc.c" \
  || fail "zsmalloc chain sizing missing"
# zsmalloc_free_zspage_out_of_lock: a dead zspage's pages must go back to the
# buddy allocator after class->lock is dropped.  Both halves matter -- a renamed
# helper that nothing calls would leave the old locked free exactly where it was.
grep -q "__free_zspage_lockless(struct zs_pool \*pool," "$KERNEL_ROOT/common/mm/zsmalloc.c" \
  || fail "zsmalloc zs_free() page-return was not split out of __free_zspage()"
# [^_] keeps this off __free_zspage(pool, class, zspage);, which contains the
# same call text and still stands in free_zspage().
if grep -q "[^_]free_zspage(pool, class, zspage);" "$KERNEL_ROOT/common/mm/zsmalloc.c"; then
  fail "zs_free() kept the class->lock-held free_zspage() call"
fi
grep -q "config RCU_NOCB_CPU_DEFAULT_ALL" \
  "$KERNEL_ROOT/common/kernel/rcu/Kconfig" \
  || fail "RCU default-all Kconfig option missing"
grep -q "cpumask_setall(rcu_nocb_mask)" \
  "$KERNEL_ROOT/common/kernel/rcu/tree_nocb.h" \
  || fail "RCU default-all mask setup missing"
grep -q "MADV_COLLAPSE" "$KERNEL_ROOT/common/include/uapi/asm-generic/mman-common.h" \
  || fail "MADV_COLLAPSE UAPI missing"
grep -q "madvise_collapse" "$KERNEL_ROOT/common/mm/khugepaged.c" \
  || fail "madvise_collapse implementation missing"
# Both khugepaged_scan_file() definitions (CONFIG_SHMEM on and off) must carry
# the new out-parameter.  A single one left at four parameters still greps as
# "madvise_collapse present" but does not compile -- that is exactly what the
# stub/signature step collision produced.
if grep -q "struct file \*file, pgoff_t start, struct page \*\*hpage)$" \
     "$KERNEL_ROOT/common/mm/khugepaged.c"; then
  fail "a 4-parameter khugepaged_scan_file() definition survived the graft"
fi
scan_file_defs="$(grep -c "^static void khugepaged_scan_file" "$KERNEL_ROOT/common/mm/khugepaged.c")"
[ "$scan_file_defs" = "2" ] \
  || fail "expected 2 khugepaged_scan_file() definitions, found $scan_file_defs"
grep -qE "^CONFIG_ZRAM_MULTI_COMP=y" "$KERNEL_ROOT/common/arch/arm64/configs/gki_defconfig" \
  || fail "defconfig lane did not enable ZRAM_MULTI_COMP"
# Batch 9-1: dynamic readahead registers both android vendor-hook callbacks.
grep -q "register_trace_android_vh_ra_tuning_max_page" \
  "$KERNEL_ROOT/common/mm/readahead.c" \
  || fail "dynamic readahead max-page hook registration missing"
grep -q "register_trace_android_vh_tune_mmap_readaround" \
  "$KERNEL_ROOT/common/mm/readahead.c" \
  || fail "dynamic readahead readaround hook registration missing"
# The mmap read-around shrink is only reachable if the baseline carries the
# vendor-hook call site in mm/filemap.c; pin it so a hook-less baseline fails
# loudly instead of silently no-opping.
grep -q "trace_android_vh_tune_mmap_readaround" \
  "$KERNEL_ROOT/common/mm/filemap.c" \
  || fail "mmap read-around vendor-hook call site missing in mm/filemap.c"
# Batch 30: the mmap_miss decrement in do_async_mmap_readahead() is behind the
# page-lock test (e338d8353154, v6.18).  This module writes it on every
# baseline -- no Cc: stable, so no 5.15 tree can arrive with it -- and the
# grep is on the guarded form, so a run that silently dropped the hunk (or
# landed it unguarded) fails here instead of compiling a no-op.
grep -q "if (likely(!PageLocked(page))) {" \
  "$KERNEL_ROOT/common/mm/filemap.c" \
  || fail "mmap_miss concurrent-fault guard missing in mm/filemap.c"
grep -qE "^CONFIG_ABK_DYNAMIC_READAHEAD=y" \
  "$KERNEL_ROOT/common/arch/arm64/configs/gki_defconfig" \
  || fail "defconfig lane did not enable ABK_DYNAMIC_READAHEAD"
# Batch 10-3: cached freeze reclaim accounting reaches memory.stat.  The
# freezer side reuses the upstream cgroup notify_frozen trace event, so there
# is nothing extra to assert there.
grep -q "cfr_reclaim_reclaimed" "$KERNEL_ROOT/common/mm/vmscan.c" \
  || fail "cached freeze reclaim accounting missing"
grep -q "abk_cfr_reclaim_reclaimed" "$KERNEL_ROOT/common/mm/memcontrol.c" \
  || fail "cached freeze reclaim memory.stat report missing"
# Batch 10-4: the secondary compressor must be registered at device creation
# (otherwise ZRAM_MULTI_COMP recompression is a silent no-op on every
# baseline), and the cgroup-v1 reclaim surface must exist for devices whose
# memory controller is mounted v1 rather than under /sys/fs/cgroup.
grep -q "abk_recomp_algo" "$KERNEL_ROOT/common/drivers/block/zram/zram_drv.c" \
  || fail "zram secondary compressor registration missing"
# The secondary algorithm is zstd and its parameter is read-only (0444): a
# writable lz4hc default is what let the ROM's userspace daemon lock the
# primary onto the dominated algorithm on a 5.15.215 device.
grep -q 'abk_zram_recomp_algo\[CRYPTO_MAX_ALG_NAME\] = "zstd"' \
  "$KERNEL_ROOT/common/drivers/block/zram/zram_drv.c" \
  || fail "zram secondary compressor is not the fixed zstd default"
grep -q 'sizeof(abk_zram_recomp_algo), 0444);' \
  "$KERNEL_ROOT/common/drivers/block/zram/zram_drv.c" \
  || fail "zram secondary compressor parameter is writable"
# Batch 17: the batched writeback sweep, the two fixes it ships with (the
# wb_ctl UAF and the reserved-block leak) and the compressed-writeback halves.
grep -q "zram_writeback_endio" \
  "$KERNEL_ROOT/common/drivers/block/zram/zram_drv.c" \
  || fail "zram writeback bio batching missing"
grep -q "kfree_rcu(wb_ctl, rcu)" \
  "$KERNEL_ROOT/common/drivers/block/zram/zram_drv.c" \
  || fail "wb_ctl use-after-free fix (kfree_rcu) missing"
grep -q "zram_read_from_zspool_raw" \
  "$KERNEL_ROOT/common/drivers/block/zram/zram_drv.c" \
  || fail "zram compressed writeback raw-object read missing"
grep -q "dev_attr_writeback_batch_size.attr" \
  "$KERNEL_ROOT/common/drivers/block/zram/zram_drv.c" \
  || fail "writeback_batch_size attribute missing"
grep -q "dev_attr_compressed_writeback.attr" \
  "$KERNEL_ROOT/common/drivers/block/zram/zram_drv.c" \
  || fail "compressed_writeback attribute missing"
# Negative side: the one-page synchronous submit has to be gone, and the 6.x
# zsmalloc mapping API the upstream patch uses must not have been copied in.
# Both needles carry their call shape: the new read path legitimately waits for
# its own read bio, and the comments name the upstream API.
if grep -q "err = submit_bio_wait(&bio);" \
     "$KERNEL_ROOT/common/drivers/block/zram/zram_drv.c"; then
  fail "zram writeback sweep still submits one page synchronously"
fi
# The needle carries its call shape: the new helper block *documents* the 6.x
# API by name in a comment, so a bare symbol needle would match that comment and
# fail the assertion it exists to protect.
if grep -q "zs_obj_read_begin(zram->mem_pool" \
     "$KERNEL_ROOT/common/drivers/block/zram/zram_drv.c"; then
  fail "zram compressed writeback copied the 6.x zsmalloc mapping API"
fi
# Batch 32: the release of a written-back slot, on both sides of the
# accounting.  The completion must free the object in place and keep the flags
# and attributes the read path needs, and the final release must not decrement
# ->huge_pages a second time -- so the save/restore dance the batching group
# generated has to be *gone*, not merely bypassed.
grep -q "ABK stable_515_backport: b0377ee80429" \
  "$KERNEL_ROOT/common/drivers/block/zram/zram_drv.c" \
  || fail "zram written-back slot release (b0377ee80429) missing"
grep -q "if (!zram_test_flag(zram, index, ZRAM_WB))" \
  "$KERNEL_ROOT/common/drivers/block/zram/zram_drv.c" \
  || fail "zram_free_page() huge-page decrement is not guarded on ZRAM_WB"
# The needle is the dance's restore of the saved object size: it exists only in
# that block (the recompression path legitimately writes obj_size and priority
# after its own zram_free_page(), so those two are ambiguous here -- the
# function-scoped assertions in implementation_audit.py pin all three).
if grep -q "zram_set_obj_size(zram, index, size);" \
     "$KERNEL_ROOT/common/drivers/block/zram/zram_drv.c"; then
  fail "zram writeback still saves slot metadata to restore it after a free"
fi
grep -q 'cfr_reclaim_attempts %ld' "$KERNEL_ROOT/common/mm/memcontrol.c" \
  || fail "cgroup-v1 cfr_reclaim counters missing"
grep -q '.write = memory_reclaim,' "$KERNEL_ROOT/common/mm/memcontrol.c" \
  || fail "cgroup-v1 memory.reclaim entry missing"

# Batch 13: the customize_alloc_gfp hook (declare/call/export) must be in the
# tree and the ABK fast-fail policy must really register on it.  The hook
# lines carry no ABK marker by design (upstream-shape), so assert the exact
# upstream text here instead.
grep -q "DECLARE_HOOK(android_vh_customize_alloc_gfp" \
  "$KERNEL_ROOT/common/include/trace/hooks/mm.h" \
  || fail "customize_alloc_gfp hook declaration missing"
grep -q "trace_android_vh_customize_alloc_gfp(&alloc_gfp, order);" \
  "$KERNEL_ROOT/common/mm/page_alloc.c" \
  || fail "customize_alloc_gfp slowpath call site missing"
grep -q "EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_customize_alloc_gfp);" \
  "$KERNEL_ROOT/common/drivers/android/vendor_hooks.c" \
  || fail "customize_alloc_gfp tracepoint export missing"
grep -q "register_trace_android_vh_customize_alloc_gfp(" \
  "$KERNEL_ROOT/common/mm/page_alloc.c" \
  || fail "gfp fast-fail policy does not register on the hook"
grep -q "abk_gfp_fastfail" "$KERNEL_ROOT/common/mm/page_alloc.c" \
  || fail "gfp fast-fail knobs missing"

# Batch 15: the absorbed ABK_ABI_PATCH_SUITE features.  Two of them are
# load-bearing enough to assert here and not only in the audits:
#   * sched_eevdf_core_fields takes ownership of the sched_entity KABI slots
#     1-4.  It is an anti-drift gate -- if the fair.c logic did not land the
#     group refuses and the slots stay ANDROID_KABI_RESERVE -- so the claim is
#     only expected where the group really applies.
#   * blk_mq_async_depth must carry the CONVERTED bfq/kyber assignments.  The
#     suite's raw form writes a request count into a per-word bit cap, which on
#     any realistic queue depth is >= one word, i.e. a throttle that never
#     throttles.  Asserting the conversion is what keeps that from regressing.
if "$python_bin" - "$MODULE_DIR/tests" "$SUB_LEVEL" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import sublevel_matrix
sys.exit(0 if sublevel_matrix.applies(sys.argv[2], "stable_perf_backport",
                                      "sched_eevdf_core_fields") else 1)
PY
then
  grep -q "ANDROID_KABI_USE(1, u64 deadline);" \
    "$KERNEL_ROOT/common/include/linux/sched.h" \
    || fail "EEVDF did not claim sched_entity slot 1"
  grep -q "ANDROID_KABI_USE(3, s64 vlag);" \
    "$KERNEL_ROOT/common/include/linux/sched.h" \
    || fail "EEVDF did not claim sched_entity slot 3"
  # Batch 16 released slot 4 because the suite's u64 slice was written once and
  # read nowhere.  Batch 28 re-claimed it, because the rebuilt payload really
  # does read se->slice (update_deadline, the yield forfeit, PREEMPT_SHORT).
  grep -q "ANDROID_KABI_USE(4, u64 slice);" \
    "$KERNEL_ROOT/common/include/linux/sched.h" \
    || fail "EEVDF did not re-claim sched_entity slot 4 as u64 slice"
  grep -q "return abk_pick_eevdf(cfs_rq, curr);" \
    "$KERNEL_ROOT/common/kernel/sched/fair.c" \
    || fail "EEVDF selector is not wired into pick_next_entity"
  # The Batch 28 rebuild: accumulators in, quadratic scans out.
  grep -q "abk_avg_vruntime_add(cfs_rq, se);" \
    "$KERNEL_ROOT/common/kernel/sched/fair.c" \
    || fail "EEVDF tree insert does not maintain the cfs_rq accumulators"
  grep -q "abk_eevdf_refresh_deadline(cfs_rq, curr);" \
    "$KERNEL_ROOT/common/kernel/sched/fair.c" \
    || fail "update_curr() does not own the EEVDF deadline refresh"
  grep -q "s64			avg_vruntime;" \
    "$KERNEL_ROOT/common/kernel/sched/sched.h" \
    || fail "struct cfs_rq has no EEVDF accumulator"
  grep -q "SCHED_FEAT(RUN_TO_PARITY, true)" \
    "$KERNEL_ROOT/common/kernel/sched/features.h" \
    || fail "RUN_TO_PARITY is not declared"
  grep -q "return abk_pick_eevdf(cfs_rq, curr);" \
    "$KERNEL_ROOT/common/kernel/sched/fair.c" \
    || fail "EEVDF selector is not wired into pick_next_entity"
fi
if "$python_bin" - "$MODULE_DIR/tests" "$SUB_LEVEL" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import sublevel_matrix
sys.exit(0 if sublevel_matrix.applies(sys.argv[2], "stable_perf_backport",
                                      "blk_mq_async_depth") else 1)
PY
then
  grep -q "async_depth << shift" "$KERNEL_ROOT/common/block/kyber-iosched.c" \
    || fail "kyber async_depth is not unit-converted (throttle never fires)"
  grep -q "async_depth << bt->sb.shift" \
    "$KERNEL_ROOT/common/block/bfq-iosched.c" \
    || fail "bfq async_depth is not unit-converted (throttle never fires)"
fi

# The drm valid-clones revert must leave no trace of the 5.15.185 check on
# any baseline: 167/178 never carried it, 194/lts had it removed by the graft.
if grep -q "drm_atomic_check_valid_clones" \
     "$KERNEL_ROOT/common/drivers/gpu/drm/drm_atomic_helper.c"; then
  fail "drm valid clones check survived the graft"
fi

# The fs/file.c module marker only exists where this module rewrote the file;
# from 5.15.191 the baseline is already in the upstream shape and the fdtable
# conventions group correctly reports already_present without touching it.
if "$python_bin" - "$MODULE_DIR/tests" "$SUB_LEVEL" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import sublevel_matrix
sys.exit(0 if sublevel_matrix.applies(sys.argv[2], "stable_backport_core",
                                      "fdtable_alloc_conventions") else 1)
PY
then
  grep -q "ABK stable_515_backport" "$KERNEL_ROOT/common/fs/file.c" \
    || fail "module marker missing in fs/file.c"
else
  echo "  fs/file.c marker not expected on 5.15.$SUB_LEVEL (baseline already upstream)"
fi

# rollback must restore the pristine tree
bash "$MODULE_DIR/scripts/abk_rollback.sh" "$KERNEL_ROOT/common" --list >/dev/null
bash "$MODULE_DIR/scripts/abk_rollback.sh" "$KERNEL_ROOT/common" --apply >/dev/null
[ -z "$(find "$KERNEL_ROOT/common" -name '*.abk-orig')" ] || fail "rollback left .abk-orig files behind"
if grep -qE "^CONFIG_ZRAM_MULTI_COMP=y" "$KERNEL_ROOT/common/arch/arm64/configs/gki_defconfig"; then
  fail "rollback left the defconfig lane's config enablement behind"
fi
if git -C "$SOURCE_TREE" rev-parse >/dev/null 2>&1 \
   && diff -q "$SOURCE_TREE/fs/file.c" "$KERNEL_ROOT/common/fs/file.c" >/dev/null 2>&1; then
  echo "rollback verified byte-identical for fs/file.c"
fi
if git -C "$SOURCE_TREE" rev-parse >/dev/null 2>&1 \
   && diff -q "$SOURCE_TREE/drivers/gpu/drm/drm_atomic_helper.c" \
        "$KERNEL_ROOT/common/drivers/gpu/drm/drm_atomic_helper.c" >/dev/null 2>&1; then
  echo "rollback verified byte-identical for drivers/gpu/drm/drm_atomic_helper.c"
fi
# Batch 30 is the first group to write mm/filemap.c; rollback has to restore it
# like any other target (the file is otherwise pristine on every baseline).
if git -C "$SOURCE_TREE" rev-parse >/dev/null 2>&1 \
   && diff -q "$SOURCE_TREE/mm/filemap.c" \
        "$KERNEL_ROOT/common/mm/filemap.c" >/dev/null 2>&1; then
  echo "rollback verified byte-identical for mm/filemap.c"
fi

echo "SMOKE OK"
