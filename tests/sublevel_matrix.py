#!/usr/bin/env python3
"""Expected graft statuses for the one supported android13-5.15 baseline.

The engine gates purely on text anchors -- ``ctx.sub_level`` never takes part
in a comparison -- so a group whose upstream commit is already in the target
baseline correctly reports ``already_present`` instead of ``applied``.  That
makes "applied" the wrong universal assertion.

This table records, per sublevel, which groups are expected to arrive
pre-applied.  Anything not listed must report ``applied`` on a pristine tree;
nothing may ever report ``blocked_by_shape``.

The display child is a revert: the supported baseline carries the 5.15.185
valid-clones check, so its group reports ``applied`` and its PRE_APPLIED entry
stays empty.

Since Batch 44 there is exactly **one** supported baseline, the rolling
``android13-5.15-lts`` branch, and the row key is its **Makefile SUBLEVEL**:

  216 -> android13-5.15-lts   (rolling; re-key on every re-fetch)

The three release baselines that used to be listed here
(167 -> deprecated/android13-5.15-2024-11, 178 -> -2025-03,
194 -> android13-5.15-2025-12) were dropped in Batch 44.  See `pre_applied()`
for why a roll fails loudly instead of silently reusing stale expectations, and
`AGENTS.md` / `docs/porting_policy.md` "Lts-only maintenance" for the recipe.
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
    # The Batch-33 zsmalloc free path (zsmalloc_free_zspage_out_of_lock) applies
    # on every baseline: android13-5.15 has no pool->lock in mm/zsmalloc.c on
    # any of them, so the mainline series patches 1-2 are inapplicable and only
    # the page-free-outside-class->lock hunk is ported.  Its anchors are
    # pristine text no other group writes.
    # Batch 34 (arm64_lse_percpu_load_atomics, mainline 535fdfc5a228) applies
    # on every baseline: the commit never reached linux-5.15.y (gregkh compare,
    # diverged), so the rolling lts row cannot arrive pre-applied either -- and
    # its percpu.h anchors are byte-identical to the upstream old form on all
    # four trees.  Applies via arm64's own header, not a Kconfig: the LSE
    # encoding is an ARM64_LSE_ATOMIC_INSN alternative patched at boot, so the
    # non-LSE fallback path is untouched on cores without FEAT_LSE.
    # Batch 35 adds four groups, all landing on every baseline: the two
    # page-cache shadow-entry sweeps (61c663e020d2, then d3db2c042591 which
    # refactors its partner's helper) and the MADV_DONTNEED pair
    # (6375e95f381e's empty-PTE-page reclaim, then 43c4cfde7e37's batched TLB
    # flush).  None of the four has a Cc: stable, and android13-5.15 carries
    # none of the substrate they touch.  The three readahead/filemap commits of
    # the same area are *not* registered -- no baseline has a carrier for them
    # (see the exclusion record in plan.md).
    # Batch 37 adds six MGLRU v4 groups, all landing on every baseline: the
    # baseline MGLRU is the 6.1 "minimal implementation" backport (page-based
    # lru_gen_struct/lists, sort_page/scan_pages/evict_pages) and carries none
    # of the 6.2-6.14 MGLRU evolution the series assumes -- verified identical
    # shape on all four trees, so nothing arrives pre-applied.  3af0191a594d
    # (workingset accounting) is NOT registered: the ACK backport already
    # carries its accounting (verified line-by-line in lru_gen_refault()),
    # which is why mglru_rework_refault_detection anchors the post-fix shape.
    # mglru_rework_type_selection is a second-pass group: it rewrites
    # read_ctrl_pos()/isolate_pages() and consumes the evictable_min_seq/
    # for_each_evictable_type macros and the reindexed protected[] that
    # mglru_rework_aging_feedback generates.
    # 47 on the merged base (Batch 35's 45 + the two parallel Batch 36s:
    # memcg_stats_percpu_slim and the FUSE write-path prefault) + this batch's
    # six MGLRU groups = 59, minus the two MADV_DONTNEED page-table groups
    # removed in v0.42.0 (madvise_pt_reclaim + madvise_batch_tlb_flush: they
    # freed empty PTE pages under mmap_read_lock and raced the smaps/reclaim
    # page-table walkers on 5.15, which panicked in smaps_pte_range) = 57.
    # Batch 38 added six (huge_memory_imap_split_uaf,
    # swap_readahead_lru_add_drain, vmscan_tasks_rcu_qs,
    # filemap_mmap_miss_tried, memcg_dying_bailout, buddyinfo_nolock) = 63.
    # Batch 39 adds one: pagealloc_batch_clear (v7.2 b001cf7d16dd).  It lands
    # on every baseline -- the anchor is pristine 5.15 text on all four, and
    # unlike the i_mmap split UAF no baseline carries the commit, because it
    # is a v7.2 performance change with no Cc: stable and no 5.15.y backport.
    # Batch 40 adds one: erofs_readahead_relaxed_gfp (v6.9 d9281660ff3f), the
    # module's first fs/erofs/ group.  It lands on every baseline for the same
    # reason -- a v6.9 performance change with no Cc: stable -- and its eight
    # anchors are byte-identical on all four (the pcluster struct it edits is
    # in fs/erofs/zdata.h on 5.15, which is why that file joined the fixture).
    # Batch 41 adds one: vm_kcompressd_swapout (Kcompressd-Unofficial 0.5 by
    # firelzrd, no upstream 5.15 patch exists, so nothing can pre-apply).  It
    # lands on every baseline, and it is the module's first mm/page_io.c group.
    # Its three anchors are byte-identical pristine text on all four -- the
    # one place the baselines disagree, android13-5.15-lts's
    # android_vh_shrink_page_lock_owner_clear() call inside swap_writepage()'s
    # frontswap_store() block, is resolved by a *text* probe in the group's
    # _apply (ms/vmstat.c comment), not by a per-sublevel expectation: the
    # group probes the shape, emits the matching engine variant and reports
    # applied either way.  That same hook is also why the group cannot be
    # registered to degrade: a baseline without the hook must not get a call to
    # an undeclared vendor hook, so an unknown shape is blocked_by_shape here
    # rather than silently half-applied -- which is a third status the matrix
    # would need if any baseline ever matched neither shape.  None does.
    # Batch 46 adds one: erofs_readmore_past_eof (v6.5 936aa701d82d), the
    # module's second fs/erofs/ group.  It cannot arrive pre-applied -- a v6.5
    # performance change with a Fixes: tag but no Cc: stable -- and its single
    # three-line anchor is unique in fs/erofs/zdata.c, a file Batch 40 already
    # put in the fixture, and outside every one of that batch's steps there.
    "stable_backport_core": 67,
    # 41 on the merged base (Batch 34 arm64_lse_percpu_load_atomics took it
    # from 40 to 41) + Batch 35 (four page-cache/page-table groups) + the
    # Batch-36 FUSE write-path prefault below = 46.
    # The Batch-36 FUSE write-path prefault (fuse_prefault_out_of_write_path)
    # applies on every baseline too: faa794dd2e17 is a v6.16 performance change
    # with no Cc: stable, so no 5.15 tree carries it, and its two anchors are
    # byte-identical on all four.  It is also the module's first group in
    # fs/fuse/, so nothing else writes the file.  Renumbered twice: the arm64
    # LSE group took "Batch 34" and the page-cache/page-table one took 35 while
    # this branch was open, so this one is 36.
    # Batch 37 adds the six-group memory-reclaim chain
    # (proactive_reclaim_batch_fidelity, proactive_reclaim_decaying_batches,
    # reclaim_swappiness_defines, proactive_reclaim_swappiness_arg,
    # proactive_reclaim_suspend_abort, lru_add_drain_dead_folios).  All six
    # apply on every baseline -- none of the commits has a Cc: stable, and
    # 5.15 has neither memory_reclaim() nor user_proactive_reclaim() nor the
    # post-5.17 folio_batch form of mm/swap.c -- but the first three rewrite
    # text memcg_memory_reclaim generates, so the count moved for a *chain* of
    # groups rather than for six independent ones.  The 194/lts swappiness
    # vendor hook is handled by a probed step variant, not by a per-sublevel
    # expectation, which is why PRE_APPLIED stays empty here.  This branch
    # numbered it 34 while main numbered the arm64 LSE group 34, so the merge
    # renumbered it after main's Batch 36.
    # 46 (the merged main line) + Batch 37's six = 52.
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
    # Batch 42 adds one: schedutil_smart_cap, the freq_cap[] -> min(freq, cap)
    # clamp that Batch 10-2 scoped and never landed.  It lands on every
    # baseline for the same reason -- it is this module's own payload, grafted
    # on the android_vh_cpufreq_resolve_freq() hook, so no baseline can carry it
    # -- and it shares its only two anchors with schedutil_smart_policy (the
    # shared include block plus the cpufreq_governor_init() line this payload is
    # grafted *in front of*), both byte-identical pristine text on all four
    # trees.  Registered immediately after schedutil_smart_policy because
    # registration order IS execution order for android_vh probes and the cap
    # has to clamp before the floor raises; see abk_stable_perf.py.
    "stable_perf_backport": 24,
    "stable_display_fix": 1,
}

# sublevel -> child id -> groups whose upstream commit the baseline already has
#
# Single-baseline world since Batch 44: the three release baselines (5.15.167 /
# .178 / .194) were dropped and only the rolling android13-5.15-lts branch is
# maintained.  See CHANGELOG.md#batch-44 -- the discard is deliberate, and the
# CI compile gate already built lts only, so the matrix now matches reality
# instead of claiming four-way coverage nothing checked.
PRE_APPLIED = {
    # The one supported baseline.  Keyed to the fetched tree's Makefile
    # SUBLEVEL, which is a **rolling** value: re-fetching a rolled lts tree
    # re-keys this row, and every set below must be re-proven on that tree.
    #
    # That is intentional, and it is the whole drift defence.  A roll to a new
    # SUBLEVEL makes every audit fail loudly here (see pre_applied()), which
    # forces a conscious re-key instead of letting stale expectations ride.
    #
    # Why the CI compile gate does not substitute for it: the gate treats
    # `already_present` as a GOOD status and never consults this matrix.  If the
    # rolling branch absorbs a commit whose group is missing from this row, the
    # group flips to already_present on CI and stays green.  The local
    # step_audit / implementation_audit / smoke run is the only thing that
    # catches that, so it has to be run after every lts re-fetch.  See
    # docs/porting_policy.md "Lts-only maintenance".
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
            # 5.15.220-era i_mmap-split UAF fix (mainline e923bd21058e, v7.2;
            # linux-5.15.y backport f87c08060818, 2026-08-19).  Verified on the
            # fetched lts tree: mm/huge_memory.c already carries the
            # `pgoff_t end, struct address_space *mapping` signature, the
            # early i_mmap_unlock_read() and the `mapping = NULL` call site,
            # so the group writes nothing here and reports already_present.
            "huge_memory_imap_split_uaf",
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
# Empty: every group of every child lands on the supported baseline.  The
# three Batch-14 zram writeback groups are covered (zram_wb_teardown does not
# depend on the series partner be48c412f6eb -- zero-sized backing device
# rejection, 5.15.168+ -- which is not editable input to the fix, and the leak
# is closed in zram_remove() rather than by deleting zram_reset_device()'s
# early return).  The two .211 lts blockers are closed rather than tracked as
# debt: randomize_kstack_pertask grew the slot-1-taken 2..8 RESERVE shape
# (AOSP owns slot 1 for `user_dumpable`, so the free RESERVE run is 2..8) and
# blk_mq_suspend_wakeup_abort now probes the payload instead of the
# __GENKSYMS__-guarded include.
KNOWN_DEBT = {}

SUPPORTED = tuple(PRE_APPLIED)

# The rolling branch is the only baseline, so there is no "oldest" fallback to
# speak of: this is the SUBLEVEL the single matrix row is keyed to.  It is used
# as the ABK_TEST_SUB_LEVEL / missing-Makefile default, and audit_tree() treats
# a tree that disagrees with it as an error rather than a downgrade.
DEFAULT_SUB_LEVEL = "216"


def pre_applied(sub_level, child):
    """Groups expected to be ``already_present`` on a pristine ``sub_level`` tree."""
    try:
        return PRE_APPLIED[sub_level][child]
    except KeyError:
        raise SystemExit(
            f"no expectation recorded for sublevel {sub_level!r} child {child!r}; "
            f"supported sublevels: {', '.join(SUPPORTED)}.\n"
            f"Batch 44 dropped the 5.15.167/.178/.194 release baselines, so the "
            f"only row is the rolling android13-5.15-lts fixture, keyed to the "
            f"Makefile SUBLEVEL it was proven against.\n"
            f"If {sub_level!r} is a freshly fetched lts tree, the branch has "
            f"rolled: re-key this row to {sub_level!r} and re-prove every set "
            f"on that tree (docs/porting_policy.md 'Lts-only maintenance'). "
            f"ABK_TEST_SUB_LEVEL does not override that requirement."
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
