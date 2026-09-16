#!/usr/bin/env python3
"""Expected per-sublevel graft statuses for the android13-5.15 GKI baselines.

The engine gates purely on text anchors -- ``ctx.sub_level`` never takes part
in a comparison -- so a group whose upstream commit is already in the target
baseline correctly reports ``already_present`` instead of ``applied``.  That
makes "applied" the wrong universal assertion: on 5.15.194 the fd-table
conventions (5.15.191) are already in the tree.

This table records, per sublevel, which groups are expected to arrive
pre-applied.  Anything not listed must report ``applied`` on a pristine tree;
nothing may ever report ``blocked_by_shape``.

The display child is a revert: on baselines that never carried the 5.15.185
valid-clones check (167/178) the tree is already in the fixed form, so its
group reports ``already_present`` there — recorded in PRE_APPLIED just like a
forward graft whose upstream commit the baseline already carries.

Verified against the real AOSP branches:
  167 -> deprecated/android13-5.15-2024-11
  178 -> deprecated/android13-5.15-2025-03
  194 -> android13-5.15-2025-12
"""

from __future__ import annotations

# child id -> total registered groups
GROUP_COUNTS = {
    # 24 Batch-1..13 groups + the three Batch-14 zram writeback groups
    # (zram_wb_teardown, zram_writeback_bounds, zram_wb_limit_align) + the three
    # Batch-15 absorbed fs/pid hot-path groups (pid_alloc_hotpath_phase2,
    # fd_alloc_hotpath, close_range_hotpath) + the two absorbed MM hot-path
    # groups (slab_alloc_free_hotpath, hugepage_fault_alloc_fastpath) + the three
    # Batch-17 zram writeback groups (zram_writeback_batching,
    # zram_wb_batch_size, zram_compressed_writeback).  The Batch-17 three apply
    # on every baseline too: they are re-anchored onto the 5.15 writeback shape
    # (ZRAM_UNDER_WB/ZRAM_IDLE rather than pp-slots), and the compressed-read
    # group probes for the field the batching group adds, so a missing shape
    # degrades instead of half-patching.
    # The 5.15.167 exception for zram_wb_teardown lives in KNOWN_DEBT below:
    # that baseline predates the group's precondition be48c412f6eb (see the
    # comment there).
    #
    # The three Batch-15 groups apply on every baseline: the part of the suite's
    # fd work that a baseline might already carry (the alloc_fdtable()
    # slots_wanted capacity rewrite) is deliberately NOT ported -- this child's
    # fdtable_alloc_conventions owns that text, and the suite's helper name is
    # one of this module's suite-detection markers.  What is ported
    # (abk_expand_files_needed() and the open_fds walk) is absent from all four.
    # The Batch-24 recompression cap (zram_recompress_max_pages) is a
    # second-pass group: it edits the text zram_recompression and
    # zram_async_recompress generate, both of which now probe their own payload.
    # The Batch-30 readahead_mmap_miss_race
    # (e338d8353154, mm/filemap.c do_async_mmap_readahead) applies on every
    # baseline: no Cc: stable, so no 5.15 tree carries it, and its anchor is
    # byte-identical on all four.
    # Batch 31 adds the arm64 pte_mkwrite() dirty guard (5.15.196), the module's
    # first arch/arm64 C source.
    # Batch 32 (zram_wb_slot_preserve) is another second-pass group: it edits
    # the zram_writeback_complete() that zram_writeback_batching generates
    # (that group probes zram_account_writeback_submit now) and the
    # zram_free_page() huge block, which is identical on every baseline.
    #
    # The Batch-33 zsmalloc free path (zsmalloc_free_zspage_out_of_lock) applies
    # on every baseline: android13-5.15 has no pool->lock in mm/zsmalloc.c on
    # any of them, so the mainline series patches 1-2 are inapplicable and only
    # the page-free-outside-class->lock hunk is ported.  Its anchors are
    # pristine text no other group writes.
    #
    # Batch 34 (arm64_lse_percpu_load_atomics, mainline 535fdfc5a228) applies
    # on every baseline: the commit never reached linux-5.15.y (gregkh compare,
    # diverged), so the rolling lts row cannot arrive pre-applied either -- and
    # its percpu.h anchors are byte-identical to the upstream old form on all
    # four trees.  Applies via arm64's own header, not a Kconfig: the LSE
    # encoding is an ARM64_LSE_ATOMIC_INSN alternative patched at boot, so the
    # non-LSE fallback path is untouched on cores without FEAT_LSE.
    # The Batch-35 FUSE write-path prefault (fuse_prefault_out_of_write_path)
    # applies on every baseline too: faa794dd2e17 is a v6.16 performance change
    # with no Cc: stable, so no 5.15 tree carries it, and its two anchors are
    # byte-identical on all four.  It is also the module's first group in
    # fs/fuse/, so nothing else writes the file.  The count is Batch 33 + the
    # two groups that landed in parallel as "Batch 34" (this one renamed to 35
    # because the arm64 LSE one merged first).
    "stable_backport_core": 42,
    # 13 Batch-1..13 groups + the three absorbed EEVDF groups
    # (sched_eevdf_pick_logic, sched_eevdf_core_fields,
    # sched_eevdf_modern_fields), the two absorbed
    # scheduler refinements (nohz_field_refinement, avg_idle_preemption_mode),
    # the absorbed blk_mq_async_depth and blk_mq_quiesced_elevator_switch (the
    # last upstream 5.15.y backlog item, 5.15.209), plus
    # sched_steal_time_excess_drop (5.15.179, the other one), plus the Batch-21
    # psi_cgroup_pressure_switch (android14-6.1 cgroup.pressure) and
    # psi_oncpu_state_mask (android14-6.1: TSK_ONCPU becomes a state-mask bit).
    # The EEVDF trio is registered pick_logic-first so the sched_entity slot
    # claim and the cfs_rq accumulators only happen once the fair.c logic has
    # really landed; the three PSI
    # groups are registered in dependency order -- psi_irq_tracking first (the
    # switch patches the walk it appends), psi_cgroup_pressure_switch next (the
    # ONCPU group edits its disabled branch).
    "stable_perf_backport": 23,
    "stable_display_fix": 1,
}

# sublevel -> child id -> groups whose upstream commit the baseline already has
PRE_APPLIED = {
    "167": {
        "stable_backport_core": set(),
        "stable_perf_backport": set(),
        # The 5.15.185 valid-clones check never existed on 167: the tree is
        # already in the fixed (pre-185) form.
        "stable_display_fix": {"drm_valid_clones_revert"},
    },
    "178": {
        "stable_backport_core": set(),
        # 5.15.174 NOHZ series landed in the 2025-03 baseline.
        "stable_perf_backport": {"sched_nohz_idle_balance_series"},
        "stable_display_fix": {"drm_valid_clones_revert"},
    },
    "194": {
        # 5.15.191 fd-table conventions, 5.15.191 cpuset bail-out and the
        # 5.15.194 cgroup destroy-wq split are all in the 2025-12 baseline.
        "stable_backport_core": {
            "fdtable_alloc_conventions",
            "pagealloc_cpuset_bailout",
            "cgroup_destroy_wq_split",
        },
        # 5.15.174 NOHZ series, 5.15.179 excess steal time and 5.15.180
        # semaphore wake_q.
        "stable_perf_backport": {
            "sched_nohz_idle_balance_series",
            "sched_steal_time_excess_drop",
            "semaphore_wake_q",
        },
        # The 2025-12 baseline carries the 5.15.185 valid-clones check: the
        # revert really applies here.
        "stable_display_fix": set(),
    },
    # android13-5.15-lts: not a CI combination yet, but tracked so the local
    # tree (Makefile SUBLEVEL 216 as of the 2026-09 re-fetch; row recorded
    # against 5.15.211, expectations re-proven on the .216 tree) can be
    # audited and drift surfaces before the baseline ships.
    # This is a rolling branch, so re-check these two sets -- and re-key this
    # row to the new Makefile SUBLEVEL -- when re-fetching it.
    # Both .211 blockers are closed on this row (CHANGELOG.md#batch-19, and the
    # plan.md index line for Batch 5): the kstack KABI slot probe learned the
    # slot-1-taken 2..8 RESERVE shape (so the group really applies here), and
    # the blk-mq suspend path arrived upstream-first so it is recorded in
    # PRE_APPLIED instead of KNOWN_DEBT.
    "216": {
        "stable_backport_core": {
            "fdtable_alloc_conventions",
            "fdtable_replace_fd_errno",
            "pagealloc_thisnode_thp_noreclaim",
            "pagealloc_cpuset_bailout",
            "pagealloc_high_fraction_lockfree",
            "cgroup_destroy_wq_split",
            # 5.15.196 pte_mkwrite() dirty guard: the rolling branch is past
            # that point, so the helper already has the guarded clear.
            "arm64_pte_mkwrite_clean",
        },
        "stable_perf_backport": {
            "sched_nohz_idle_balance_series",
            "sched_steal_time_excess_drop",
            "release_sock_cond_resched",
            "semaphore_wake_q",
            # The lts branch now carries all three 5.15.202 RT hunks (the
            # rto_next_cpu self-IPI skip and the PREEMPT_RT-only RT_PUSH_IPI
            # default) and the .212 dst-group stats fix.
            "sched_rt_optimizations",
            "sched_dst_group_allowed_stats",
            # 5.15.198 blk-mq suspend abort: the branch has the payload but
            # wraps its <linux/suspend.h> include in an __GENKSYMS__ guard, so
            # the include-pair anchor cannot match.  The group probes the
            # payload itself and reports already_present.
            "blk_mq_suspend_wakeup_abort",
            # 5.15.209 quiesced elevator switch: same story -- the branch has
            # the post-commit shape (static elevator_switch_mq() plus the
            # renamed non-static elevator_switch()), so the group probes for
            # the renamed call site and writes nothing.
            "blk_mq_quiesced_elevator_switch",
        },
        # The lts branch carries the valid-clones check; the revert applies.
        "stable_display_fix": set(),
    },
}

# sublevel -> child -> group key -> expected degraded status.
# Disjoint from PRE_APPLIED: a group either arrives upstream-clean or is a
# tracked debt; never both.
#
# Empty for the three Batch-14 zram writeback groups: each one lands on a
# pristine 167/178/194/216 tree.  zram_wb_teardown does not depend on the series
# partner be48c412f6eb (zero-sized backing device rejection, 5.15.168+) -- that
# hunk is not editable input to the fix -- and the leak is closed in
# zram_remove() rather than by deleting zram_reset_device()'s early return, so
# 5.15.167 is covered like the rest.
# Empty since the 5.15.211+ lts row was closed: randomize_kstack_pertask now
# really applies there (the KABI slot probe learned the slot-1-taken 2..8
# RESERVE shape) and blk_mq_suspend_wakeup_abort reports already_present (the
# branch carries 8fe7de5d1c7f upstream-first behind an __GENKSYMS__ include
# guard, which the payload probe now recognises).  Every group of every child
# therefore lands on every supported baseline.
KNOWN_DEBT = {}

SUPPORTED = tuple(PRE_APPLIED)
DEFAULT_SUB_LEVEL = "167"


def pre_applied(sub_level, child):
    """Groups expected to be ``already_present`` on a pristine ``sub_level`` tree."""
    try:
        return PRE_APPLIED[sub_level][child]
    except KeyError:
        raise SystemExit(
            f"no expectation recorded for sublevel {sub_level!r} child {child!r}; "
            f"supported sublevels: {', '.join(SUPPORTED)}"
        ) from None


def debt(sub_level, child):
    """Expected degraded statuses on a pristine ``sub_level`` tree."""
    return KNOWN_DEBT.get(sub_level, {}).get(child, {})


def status_summary(sub_level, child):
    """The ``status_summary`` dict a first pass must produce."""
    total = GROUP_COUNTS[child]
    present = len(pre_applied(sub_level, child))
    debts = debt(sub_level, child)
    degraded = {}
    for group_key, status in debts.items():
        if group_key not in pre_applied(sub_level, child):
            degraded[status] = degraded.get(status, 0) + 1
    applied = total - present - sum(degraded.values())
    summary = {}
    if applied:
        summary["applied"] = applied
    if present:
        summary["already_present"] = present
    for status, count in degraded.items():
        summary[status] = summary.get(status, 0) + count
    return summary


def idempotent_summary(sub_level, child):
    """The ``status_summary`` dict a second pass must produce."""
    summary = {"already_present": GROUP_COUNTS[child]}
    for group_key, status in debt(sub_level, child).items():
        if status in ("blocked_by_shape", "blocked_by_missing_anchor"):
            # A non-edit debt stays degraded on the second pass too.
            summary["already_present"] -= 1
            summary[status] = summary.get(status, 0) + 1
        # An "applied"-valued debt is a partial-apply drift: on the second
        # pass every step is already_present, so it flips to already_present.
    return summary


def applies(sub_level, child, group_key):
    """True when ``group_key`` really rewrites the ``sub_level`` baseline.

    A group the baseline already carries leaves no ``ABK stable_515_backport:``
    marker, so marker assertions must be gated on this.
    """
    return group_key not in pre_applied(sub_level, child)


def _main():
    import json
    import sys

    if len(sys.argv) < 2:
        raise SystemExit(
            "usage: sublevel_matrix.py <sublevel> [child]\n"
            "  no child: JSON of {child: {pass1, pass2}}\n"
            "  with child: JSON of {pass1, pass2}"
        )
    sub_level = sys.argv[1]
    if len(sys.argv) > 2:
        child = sys.argv[2]
        print(json.dumps({
            "pass1": status_summary(sub_level, child),
            "pass2": idempotent_summary(sub_level, child),
        }, sort_keys=True))
        return
    print(json.dumps({
        child: {
            "pass1": status_summary(sub_level, child),
            "pass2": idempotent_summary(sub_level, child),
        }
        for child in GROUP_COUNTS
    }, sort_keys=True))


if __name__ == "__main__":
    _main()
