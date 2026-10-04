"""Batch 38: Linux 7.2 MM/Reclaim grafts selected by the v7.2 survey.

Survey source: ``docs/survey_7_2_mm_reclaim.md`` (this batch's planning
record).  The survey population is the **366 commits that touch ``mm/`` and
are reachable from tag ``v7.2`` but not ``v7.1``** -- derived by comparing
the GitHub commit listing for ``path=mm`` at ``sha=v7.1`` and ``sha=v7.2``,
cross-checked against cgit's ``log/mm/?id=v7.1..v7.2`` range walk.  A
committer-date window is NOT a valid way to delimit a release (subsystem
trees commit weeks before Linus pulls them), and using one here covered only
121 of the 366 while also admitting 432 post-7.2 master commits.

Only two things in that population are grafted in this batch's first group:

  huge_memory_imap_split_uaf   mainline e923bd21058e (v7.2), carried as the
                               upstream 5.15.y backport f87c08060818
                               (2026-08-19, linux-5.15.y)

Upstream-faithful form.  The repo policy (``docs/porting_policy.md`` §2) is
that group content mirrors the **5.15.y backport text**, not the mainline
version, so a tree lands in the exact shape newer 5.15.y sublevels expect.
``e923bd21058e`` has one, so this group is ``f87c08060818`` verbatim: no ABK
marker is added, the target form is its own idempotency probe, and a baseline
that already carries the commit is left byte-identical while reporting
``already_present`` (Batch 31/34 precedent).  Verified: the
android13-5.15-lts baseline (SUBLEVEL 216) already contains all three hunks,
so that row belongs in ``tests/sublevel_matrix.py`` ``PRE_APPLIED``.

What the bug is.  ``split_huge_page_to_list()`` takes ``i_mmap_lock_read()``
(5.15 ``mm/huge_memory.c:2680``) and held it until ``out_unlock``
(``:2758``) -- i.e. across ``__split_huge_page()``, which unlocks and frees
the after-split subpages.  Once those pages are gone, ``mapping`` (and the
inode behind it) can be freed by a concurrent ``evict()``/``iput()``, so the
final ``i_mmap_unlock_read(mapping)`` dereferences freed memory.  Upstream
reached it via ``memory_failure()`` splitting a poisoned shmem-THP tail past
EOF; KASAN reported a slab use-after-free.

Why the fix is safe on this baseline, checked hunk by hunk before grafting
(5.15.194 reference tree):

* ``__split_huge_page()`` has exactly **one** caller (``:2740``), so the
  signature change cannot strand a second call site.
* For an anonymous THP the caller sets ``mapping = NULL`` and takes
  ``anon_vma_lock_write()`` instead (``:2668``), so the new
  ``if (mapping) i_mmap_unlock_read(mapping)`` is a no-op there and the
  outer ``out_unlock`` unlock was already skipped -- no double unlock, no
  missing unlock.
* For a file-backed THP the lock is now released *inside* the helper, and the
  caller immediately sets ``mapping = NULL`` so the ``out_unlock`` release is
  skipped.  Everything the taken branch still touches after that point is
  ``ret = 0``; ``xa_unlock(&mapping->i_pages)`` sits in the ``else``/``fail``
  path, which never reaches this call.
* The unlock happens while the head page is still locked, which is what pins
  the inode -- upstream's own stated precondition, and the reason the fix is
  correct rather than merely earlier.

No KMI impact: pure function-local logic in a non-exported static helper, no
struct layout and no exported symbol touched.  No other group writes
``mm/huge_memory.c`` in this module, so no ordering constraint is needed.

Performance note.  This graft has **no** performance benefit; it is a
stability/UAF fix, carried because the 5.15.y backport text is available
verbatim and the risk is nil.  Recorded here so the survey's performance
framing stays honest.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

HUGE_MEMORY_C = "mm/huge_memory.c"
SWAP_STATE_C = "mm/swap_state.c"
FILEMAP_C = "mm/filemap.c"
VMSCAN_C = "mm/vmscan.c"
VMSTAT_C = "mm/vmstat.c"

T = True

# ---------------------------------------------------------------------------
# Step 1: __split_huge_page() gains the mapping it must release.
# Trailing context (the head/lruvec locals) pins the anchor so the `new`
# block cannot pre-exist in the pristine file.
# ---------------------------------------------------------------------------

_SPLIT_SIG_OLD = (
    "static void __split_huge_page(struct page *page, struct list_head *list,\n"
    "\t\tpgoff_t end)\n"
    "{\n"
    "\tstruct page *head = compound_head(page);\n"
    "\tstruct lruvec *lruvec;\n"
)

_SPLIT_SIG_NEW = (
    "static void __split_huge_page(struct page *page, struct list_head *list,\n"
    "\t\tpgoff_t end, struct address_space *mapping)\n"
    "{\n"
    "\tstruct page *head = compound_head(page);\n"
    "\tstruct lruvec *lruvec;\n"
)

# ---------------------------------------------------------------------------
# Step 2: release the i_mmap lock before the loop that unlocks/frees the
# after-split subpages.  Upstream's comment and wording are carried verbatim.
# The `if (mapping)` guard is load-bearing, not decoration: it is what keeps
# the anon path (mapping == NULL) a no-op.
# ---------------------------------------------------------------------------

_EARLY_UNLOCK_OLD = (
    "\t\tsplit_swap_cluster(entry);\n"
    "\t}\n"
    "\n"
    "\tfor (i = 0; i < nr; i++) {\n"
    "\t\tstruct page *subpage = head + i;\n"
    "\t\tif (subpage == page)\n"
)

_EARLY_UNLOCK_NEW = (
    "\t\tsplit_swap_cluster(entry);\n"
    "\t}\n"
    "\n"
    "\t/*\n"
    "\t * Drop the mapping while the head page is still locked and thus pins\n"
    "\t * the inode. The loop below may free the after-split subpages --\n"
    "\t * including the head, when @page is a tail beyond EOF that the split\n"
    "\t * dropped from the page cache -- which could otherwise let the inode,\n"
    "\t * and @mapping, be freed before this unlock.\n"
    "\t */\n"
    "\tif (mapping)\n"
    "\t\ti_mmap_unlock_read(mapping);\n"
    "\n"
    "\tfor (i = 0; i < nr; i++) {\n"
    "\t\tstruct page *subpage = head + i;\n"
    "\t\tif (subpage == page)\n"
)

# ---------------------------------------------------------------------------
# Step 3: pass the mapping, then clear it so the outer `out_unlock` release
# does not run a second time.  Both halves are required: passing it without
# clearing it is a double i_mmap_unlock_read(), which on a rwsem is a
# warning-and-corrupt, not a no-op.
# ---------------------------------------------------------------------------

_CALLSITE_OLD = (
    "\t\t__split_huge_page(page, list, end);\n"
    "\t\tret = 0;\n"
    "\t} else {\n"
)

_CALLSITE_NEW = (
    "\t\t__split_huge_page(page, list, end, mapping);\n"
    "\t\t/* __split_huge_page() dropped the i_mmap lock */\n"
    "\t\tmapping = NULL;\n"
    "\t\tret = 0;\n"
    "\t} else {\n"
)


def build_steps():
    """Three required steps, verbatim upstream 5.15.y hunks.

    All required.  Step 3's two halves in particular must land together: the
    signature change (step 1) makes the one call site fail to compile if it
    is missed, but passing ``mapping`` without clearing it compiles cleanly
    and double-unlocks -- the exact class of silent half-graft a required
    chain exists to prevent.
    """
    return [
        (HUGE_MEMORY_C, _SPLIT_SIG_OLD, _SPLIT_SIG_NEW, T),
        (HUGE_MEMORY_C, _EARLY_UNLOCK_OLD, _EARLY_UNLOCK_NEW, T),
        (HUGE_MEMORY_C, _CALLSITE_OLD, _CALLSITE_NEW, T),
    ]


def _huge_memory_imap_split_uaf_apply(ctx):
    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# Group 2: swap-in readahead drops its trailing lru_add_drain().
#
# mainline a4519e5b648a (v7.2, Barry Song).  Two of the three lru_add_drain()
# calls in mm/swap_state.c are deleted; the third (5.15 :317, in the
# free_page_and_swap_cache caller chain) is a different function and is
# deliberately left alone.
#
# Why it is safe on 5.15.  The pages read here are put on the LRU by the
# caller (do_swap_page() -> lru_cache_add()), which appends to the per-CPU
# pagevec batch.  That batch is drained by the next allocation's
# lru_add_drain()/lru_add_drain_all(), so LRU residency is asynchronous by
# construction.  The unconditional flush only adds a lruvec_lock acquisition
# (and a possible IPI) per swap-in readahead -- exactly the contention
# upstream measured at ~28k calls/min on a 176-CPU host under memory pressure.
# Nothing between the two points reads LRU residency, so no correctness
# dependency is removed.
#
# 5.15 shape rewrite.  Upstream's hunks sit in the folio-era
# swap_cluster_readahead()/swap_vma_readahead() (swap_read_unplug(),
# swap_cache_read_folio()).  5.15 is still page-shaped
# (read_swap_cache_async(), no swap_read_unplug), so the anchors are
# re-derived from the 5.15 tree rather than copied.  Both `new` blocks are
# checked NOT to be substrings of their `old`: a line deletion whose `new` is
# a prefix of `old` short-circuits to already_present and never lands
# (docs/group_recipe.md trap 1), which is why the trailing `skip:` label and
# the blank-line/comment context are carried in both.
#
# Interaction with Batch 37: lru_add_drain_dead_folios already rewrote the
# lru_add batch path, so this lands on the post-Batch-37 shape.
# ---------------------------------------------------------------------------

_DRAIN_CLUSTER_OLD = (
    "\tblk_finish_plug(&plug);\n"
    "\n"
    "\tlru_add_drain();\t/* Push any new pages onto the LRU now */\n"
    "skip:\n"
)

_DRAIN_CLUSTER_NEW = (
    "\tblk_finish_plug(&plug);\n"
    "\n"
    "skip:\n"
)

_DRAIN_VMA_OLD = (
    "\t}\n"
    "\tblk_finish_plug(&plug);\n"
    "\tlru_add_drain();\n"
    "skip:\n"
)

_DRAIN_VMA_NEW = (
    "\t}\n"
    "\tblk_finish_plug(&plug);\n"
    "skip:\n"
)


def _swap_readahead_lru_add_drain_apply(ctx):
    steps = [
        (SWAP_STATE_C, _DRAIN_CLUSTER_OLD, _DRAIN_CLUSTER_NEW, T),
        (SWAP_STATE_C, _DRAIN_VMA_OLD, _DRAIN_VMA_NEW, T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# Group 3: shrink_lruvec() reports Tasks-RCU quiescent states.
#
# mainline 25f52e812168 (v7.2, Roman Gushchin); grafted as the upstream
# 5.15.y backport 4cdc1bdf4094 (2026-09-14, linux-5.15.y) per the
# upstream-faithful-form policy, so a baseline carrying it stays
# byte-identical.
#
# What it fixes.  shrink_lruvec()'s scan loop only called cond_resched(),
# which is a no-op on a PREEMPTION kernel, and involuntary preemption is not
# a Tasks-RCU quiescent state.  A task stuck in a long reclaim therefore
# becomes an rcu_tasks holdout and stalls grace periods for minutes (upstream
# showed a KVM tdp_mmu_zap_leafs stack).  cond_resched_tasks_rcu_qs() reports
# the quiescent state *and* still cond_resched()s.
#
# Safety.  In 5.15 the macro is defined unconditionally in
# include/linux/rcupdate.h as
#     do { rcu_tasks_qs(current, false); cond_resched(); } while (0)
# -- there is no CONFIG_TASKS_RCU gate, so it always compiles.  rcu_tasks_qs()
# itself is
#     do { rcu_tasks_classic_qs(t, preempt); rcu_tasks_trace_qs(t); } while (0)
# under CONFIG_TASKS_RCU_GENERIC and a no-op outside it, and
# rcu_tasks_classic_qs() clears t->rcu_tasks_holdout only under
# CONFIG_TASKS_RCU.
#
# The first draft of this group claimed the graft was a no-op on this target
# ("GKI enables CONFIG_TASKS_TRACE_RCU, not CONFIG_TASKS_RCU").  **That was
# wrong and the device check refuted it**: the running vermeer kernel reports
#     CONFIG_TASKS_RCU_GENERIC=y  CONFIG_TASKS_RCU=y  CONFIG_TASKS_TRACE_RCU=y
# and exports real call_rcu_tasks/synchronize_rcu_tasks symbols (the #else
# branch would alias them to call_rcu/synchronize_rcu), so the classic
# holdout clear IS compiled in.  A reclaim task that becomes a holdout now
# reports its quiescent state on every scan iteration, which is the fix
# working as upstream intended -- not a no-op.  No speedup is *claimed* for
# the device because none was measured, but "compiles back to cond_resched()"
# is not true here and must not be repeated.
# ---------------------------------------------------------------------------

_TASKS_QS_OLD = (
    "\t\tcond_resched();\n"
    "\n"
    "\t\tif (nr_reclaimed < nr_to_reclaim || proportional_reclaim)\n"
    "\t\t\tcontinue;\n"
)

_TASKS_QS_NEW = (
    "\t\tcond_resched_tasks_rcu_qs();\n"
    "\n"
    "\t\tif (nr_reclaimed < nr_to_reclaim || proportional_reclaim)\n"
    "\t\t\tcontinue;\n"
)


def _vmscan_tasks_rcu_qs_apply(ctx):
    status, _results, detail = apply_steps(
        ctx, [(VMSCAN_C, _TASKS_QS_OLD, _TASKS_QS_NEW, T)])
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# Group 4: a FAULT_FLAG_TRIED retry must not be counted as an mmap hit.
#
# mainline 9b0fcac3cfe7 (v7.2, Barry Song), 1/2 of a pair with 0b9c0aeba938
# (deliberately NOT grafted: it deletes the decrement from
# filemap_map_folio_range()/filemap_map_order0_folio(), neither of which
# exists on 5.15, and the 5.15-native equivalent is a hand-written
# "only the faulting address" guard that is easy to get subtly wrong).
#
# What it fixes.  filemap_map_pages() decrements ra->mmap_miss for every PTE
# it maps.  When the synchronous-mmap-readahead fault returns VM_FAULT_RETRY
# and the retry finds the folio the same miss brought in, the decrement
# cancels the miss it should have recorded, so the counter stays below its
# increment side, the "this file is random, stop read-ahead" test stops
# firing, and evicted pages are read in again and again.  5.15's
# filemap_fault() carries its own comment acknowledging the same
# over-crediting ("we'll come back to filemap_fault() non-speculative case
# which will update mmap_miss a second time.  This is not ideal"), but that
# is the *other* decrement and is left alone -- see "Anchor choice" below.
#
# Upstream numbers (20 GiB larger-than-memory, random access): 223.4 GiB
# paged in / 101.3 s -> 1.01 GiB / 4.79 s (~21x less I/O); stride-2053
# 409.6 GiB / 193.7 s -> 0.97 GiB / 3.69 s; stride-4099 406.5 GiB / 134.2 s
# -> 0.98 GiB / 3.50 s.  Sequential access unchanged.  Those are x86 server
# measurements; no Snapdragon number exists and none is claimed.
#
# Anchor choice.  5.15 carries TWO mmap_miss decrements and they are not
# interchangeable:
#
#   * filemap_map_pages()'s per-PTE one -- `if (mmap_miss > 0) mmap_miss--;`
#     on a function-local counter written back once at the end.  This is the
#     site 9b0fcac3cfe7 patches, and the one this group edits.
#   * filemap_fault()'s one -- the `!(VM_RAND_READ) && ra->ra_pages` block
#     writing ra->mmap_miss directly.  On mainline this block lives in
#     do_async_mmap_readahead() (mm/filemap.c:3477 in today's tree) and
#     9b0fcac3cfe7 does NOT touch it.
#
# The two are easy to conflate: both read ra->mmap_miss, both are guarded on
# the readahead state, and the filemap_fault() block's own comment ("we'll
# come back to filemap_fault() non-speculative case which will update
# mmap_miss a second time") describes the same over-crediting.  Editing that
# one instead produces a plausible-looking patch with the wrong provenance,
# so the anchor is the per-PTE decrement and nothing else.  It is unique in
# the file: the filemap_fault() decrement is `WRITE_ONCE(ra->mmap_miss,
# --mmap_miss)` and the increment side is `++mmap_miss`.
#
# Scope limit.  mainline's block also carries `(map_ret & VM_FAULT_NOPAGE)`
# and `!folio_test_workingset(folio)` terms, and 5.15's unconditional
# per-PTE decrement has neither -- it decrements for every page in the
# do-while, including ones whose PTE was already present.  Only the new
# FAULT_FLAG_TRIED term is portable without restructuring the loop, so only
# that term is added; the pre-existing over-decrement stays as upstream
# found it.  The edit can only ever *skip* a decrement (the `mmap_miss > 0`
# bound check is preserved), so it cannot introduce an underflow.
#
# Batch 30's readahead_mmap_miss_race already touched this file's mmap_miss
# accounting; this is the continuation, not a conflict -- that group edits
# the do_async_mmap_readahead() call site, this one filemap_map_pages().
# ---------------------------------------------------------------------------

_MMAP_HIT_OLD = (
    "\t\tif (mmap_miss > 0)\n"
    "\t\t\tmmap_miss--;\n"
)

_MMAP_HIT_NEW = (
    "\t\tif (mmap_miss > 0 && !(vmf->flags & FAULT_FLAG_TRIED))\n"
    "\t\t\tmmap_miss--;\n"
)


def _filemap_mmap_miss_tried_apply(ctx):
    status, _results, detail = apply_steps(
        ctx, [(FILEMAP_C, _MMAP_HIT_OLD, _MMAP_HIT_NEW, T)])
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# Group 5: memcg reclaim writers bail out once the memcg is dying.
#
# mainline Jiayuan Chen series (v7.2): 0beeaf14e7b9 (memory.high),
# e13f634f50d5 (memory.max), 757dd8193f6c (proactive reclaim),
# 10228e0a5123 (memcg v1: limit_in_bytes / memsw.limit_in_bytes /
# force_empty).
#
# What it fixes.  Each of these handlers reclaims synchronously in the
# writer's own context while holding a kernfs active reference.  If a
# concurrent cgroup_rmdir() gets there first, that path blocks in
# kernfs_drain() with cgroup_mutex held -- and because the writer is still
# reclaiming, it never finishes, so everything else queues behind
# cgroup_mutex.  Upstream measured a 159 s cgdelete and a 182 s hang for an
# unrelated /proc/<pid>/cgroup reader.  On a phone this is exactly the
# app-teardown stall: the length of the reclaim is unbounded (swap I/O or
# thrashing), and MAX_RECLAIM_RETRIES' no-progress guard does not cover the
# slow-device case.
#
# Why it is safe on 5.15.  The bail-out only fires when the css is already
# marked dying, i.e. cgroup_rmdir() is committed to tearing it down and its
# pages are going to be reparented anyway -- so stopping reclaim early loses
# nothing.  5.15's kill_css() (kernel/cgroup/cgroup.c:5720) sets
# `css->flags |= CSS_DYING` *before* css_clear_dir(), which is the ordering
# the fix depends on; verified on the 2025-12 baseline.
#
# New helper.  5.15 has neither memcg_is_dying() nor css_is_dying(); only the
# CSS_DYING flag in include/linux/cgroup-defs.h.  Upstream defines
# memcg_is_dying() in include/linux/memcontrol.h (CONFIG_MEMCG block plus an
# #else stub returning false), and that is where it goes here, calling the
# flag directly.  memcontrol.h is already in all three audit fixtures, and
# both call sites can see it (mm/memcontrol.c owns the header; mm/vmscan.c
# includes it at :43).
#
# v1 layout.  5.15 has no mm/memcontrol-v1.c: mem_cgroup_resize_max() and
# mem_cgroup_force_empty() live in mm/memcontrol.c (:3433 / :3575), so the
# two v1 hunks are re-anchored there rather than dropped.
# ---------------------------------------------------------------------------

# The anchor pair is the mem_cgroup_soft_limit_reclaim() declaration/stub, not
# the mem_cgroup_init() one upstream's patch context shows.  5.15.167 and
# 5.15.178 declare mem_cgroup_init() `static` in mm/memcontrol.c, so neither
# `extern int mem_cgroup_init(void);` nor its inline stub exists in their
# include/linux/memcontrol.h at all; the declaration split that introduces both
# arrived somewhere in 5.15.179..5.15.194 (absent on the 178 tree, present on the
# 194 tree), which is why only 194/216 have them.
#
# The soft-limit pair is what anchors that boundary on every baseline: it is the
# *last* declaration of the CONFIG_MEMCG block and the *last* stub of the
# !CONFIG_MEMCG block, so the helper still lands immediately before the
# #else/#endif /* CONFIG_MEMCG */ line -- the same placement upstream gives it,
# with the boundary line itself left out of the anchor on purpose.  Including
# `#else`/`#endif` would re-introduce the baseline dependency: on 194/216 the
# mem_cgroup_init() declaration/stub sits *between* the soft-limit pair and that
# line, so the anchor would only match on the two baselines whose header never
# mentions mem_cgroup_init() at all -- i.e. exactly the 167/178 shape, blocking
# the group on 194/216 instead of fixing it.
#
# One anchor for all four baselines rather than an either/or pair of steps:
# both constants are byte-identical on 167/178/194/216 (counts verified 1/1 each
# on all four fetched trees), so no step ever needs to degrade.  The alternative
# -- a second step pair anchored on the mem_cgroup_init() shape -- could not work
# anyway, because apply_steps treats a *required* miss as a group abort, so the
# unused variant would report missing_anchor and take the whole group to
# blocked_by_shape whichever shape it was not written for.
_DYING_HELPER_OLD = (
    "unsigned long mem_cgroup_soft_limit_reclaim(pg_data_t *pgdat, int order,\n"
    "\t\t\t\t\t\tgfp_t gfp_mask,\n"
    "\t\t\t\t\t\tunsigned long *total_scanned);\n"
    "\n"
)

_DYING_HELPER_NEW = (
    "unsigned long mem_cgroup_soft_limit_reclaim(pg_data_t *pgdat, int order,\n"
    "\t\t\t\t\t\tgfp_t gfp_mask,\n"
    "\t\t\t\t\t\tunsigned long *total_scanned);\n"
    "\n"
    "/*\n"
    " * ABK stable_515_backport: memcg_is_dying (v7.2 memcg dying-bailout\n"
    " * series).  True once cgroup_rmdir() has started tearing this css down.\n"
    " * A writer reclaiming under cgroup_mutex must not keep reclaiming into\n"
    " * a memcg that is already going away: rmdir waits for it in\n"
    " * kernfs_drain() with cgroup_mutex held, so the whole system backs up\n"
    " * behind it.  5.15 has no css_is_dying(), so the flag is tested directly\n"
    " * -- kill_css() sets CSS_DYING before css_clear_dir().\n"
    " */\n"
    "static inline bool memcg_is_dying(struct mem_cgroup *memcg)\n"
    "{\n"
    "\treturn memcg ? !!(memcg->css.flags & CSS_DYING) : false;\n"
    "}\n"
    "\n"
)

_DYING_STUB_OLD = (
    "static inline\n"
    "unsigned long mem_cgroup_soft_limit_reclaim(pg_data_t *pgdat, int order,\n"
    "\t\t\t\t\t    gfp_t gfp_mask,\n"
    "\t\t\t\t\t    unsigned long *total_scanned)\n"
    "{\n"
    "\treturn 0;\n"
    "}\n"
)

_DYING_STUB_NEW = (
    "static inline\n"
    "unsigned long mem_cgroup_soft_limit_reclaim(pg_data_t *pgdat, int order,\n"
    "\t\t\t\t\t    gfp_t gfp_mask,\n"
    "\t\t\t\t\t    unsigned long *total_scanned)\n"
    "{\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
    "static inline bool memcg_is_dying(struct mem_cgroup *memcg) { return false; }\n"
    "\n"
)

# The two v2 handlers' guards are byte-identical in 5.15, so each anchor
# carries its own `nr_pages <= high` / `nr_pages <= max` line to stay a
# one-site edit (a bare guard would let replace_once take the first
# occurrence twice).
_HIGH_BAIL_OLD = (
    "\t\tif (nr_pages <= high)\n"
    "\t\t\tbreak;\n"
    "\n"
    "\t\tif (signal_pending(current))\n"
    "\t\t\tbreak;\n"
)

_HIGH_BAIL_NEW = (
    "\t\tif (nr_pages <= high)\n"
    "\t\t\tbreak;\n"
    "\n"
    "\t\tif (signal_pending(current))\n"
    "\t\t\tbreak;\n"
    "\n"
    "\t\t/* cgroup_rmdir() waits for us with cgroup_mutex held. */\n"
    "\t\tif (memcg_is_dying(memcg))\n"
    "\t\t\tbreak;\n"
)

_MAX_BAIL_OLD = (
    "\t\tif (nr_pages <= max)\n"
    "\t\t\tbreak;\n"
    "\n"
    "\t\tif (signal_pending(current))\n"
    "\t\t\tbreak;\n"
)

_MAX_BAIL_NEW = (
    "\t\tif (nr_pages <= max)\n"
    "\t\t\tbreak;\n"
    "\n"
    "\t\tif (signal_pending(current))\n"
    "\t\t\tbreak;\n"
    "\n"
    "\t\t/* cgroup_rmdir() waits for us with cgroup_mutex held. */\n"
    "\t\tif (memcg_is_dying(memcg))\n"
    "\t\t\tbreak;\n"
)

_RESIZE_BAIL_OLD = (
    "\tdo {\n"
    "\t\tif (signal_pending(current)) {\n"
    "\t\t\tret = -EINTR;\n"
    "\t\t\tbreak;\n"
    "\t\t}\n"
)

_RESIZE_BAIL_NEW = (
    "\tdo {\n"
    "\t\tif (signal_pending(current)) {\n"
    "\t\t\tret = -EINTR;\n"
    "\t\t\tbreak;\n"
    "\t\t}\n"
    "\n"
    "\t\t/* cgroup_rmdir() waits for us with cgroup_mutex held. */\n"
    "\t\tif (memcg_is_dying(memcg))\n"
    "\t\t\tbreak;\n"
)

_FORCE_EMPTY_BAIL_OLD = (
    "\t\tif (signal_pending(current))\n"
    "\t\t\treturn -EINTR;\n"
)

_FORCE_EMPTY_BAIL_NEW = (
    "\t\tif (signal_pending(current))\n"
    "\t\t\treturn -EINTR;\n"
    "\n"
    "\t\t/* cgroup_rmdir() waits for us with cgroup_mutex held. */\n"
    "\t\tif (memcg_is_dying(memcg))\n"
    "\t\t\tbreak;\n"
)

# The proactive-reclaim handler is *generated* by Batch 37's
# memcg_memory_reclaim chain, and on 5.15 it lands in mm/memcontrol.c --
# not mm/vmscan.c.  Upstream 7.2 keeps that loop in user_proactive_reclaim()
# in mm/vmscan.c, which 5.15 does not have; Batch 37's own suspend-abort group
# documents the same re-anchoring.  This anchor therefore only exists after
# that chain has landed, which the registration order guarantees, and if that
# chain ever degrades this group degrades visibly instead of half-patching.
#
# Placement is deliberate and load-bearing.  Upstream puts the dying check
# immediately after the signal_pending() test, which is *inside* the block
# Batch 37's proactive_reclaim_suspend_abort rewrites -- inserting there
# splits that group's `new` block in half, so on a second pass its `old`
# (-EINTR) no longer matches and its `new` no longer matches either, and it
# degrades to blocked_by_shape on an already-patched tree (docs/group_recipe.md
# trap 5; caught by step_audit's second-pass assertion, not the first pass).
# The check therefore goes just after the "final attempt" comment instead:
# still inside the same while() loop, still before the
# try_to_free_mem_cgroup_pages() call, so the behaviour is identical, and the
# anchor is text Batch 37 never rewrites -- which also means this group needs
# only memcg_memory_reclaim to have landed, not the suspend-abort group.
_PROACTIVE_BAIL_OLD = (
    "\t\t * This is the final attempt, drain percpu lru caches in the\n"
    "\t\t * hope of introducing more evictable pages for\n"
    "\t\t * try_to_free_mem_cgroup_pages().\n"
    "\t\t */\n"
    "\t\tif (!nr_retries)\n"
    "\t\t\tlru_add_drain_all();\n"
)

_PROACTIVE_BAIL_NEW = (
    "\t\t * This is the final attempt, drain percpu lru caches in the\n"
    "\t\t * hope of introducing more evictable pages for\n"
    "\t\t * try_to_free_mem_cgroup_pages().\n"
    "\t\t */\n"
    "\n"
    "\t\t/* cgroup_rmdir() waits for us with cgroup_mutex held. */\n"
    "\t\tif (memcg && memcg_is_dying(memcg))\n"
    "\t\t\treturn -EAGAIN;\n"
    "\n"
    "\t\tif (!nr_retries)\n"
    "\t\t\tlru_add_drain_all();\n"
)

MEMCONTROL_H = "include/linux/memcontrol.h"
MEMCONTROL_C = "mm/memcontrol.c"


def _memcg_dying_bailout_apply(ctx):
    steps = [
        (MEMCONTROL_H, _DYING_HELPER_OLD, _DYING_HELPER_NEW, T),
        (MEMCONTROL_H, _DYING_STUB_OLD, _DYING_STUB_NEW, T),
        (MEMCONTROL_C, _HIGH_BAIL_OLD, _HIGH_BAIL_NEW, T),
        (MEMCONTROL_C, _MAX_BAIL_OLD, _MAX_BAIL_NEW, T),
        (MEMCONTROL_C, _RESIZE_BAIL_OLD, _RESIZE_BAIL_NEW, T),
        (MEMCONTROL_C, _FORCE_EMPTY_BAIL_OLD, _FORCE_EMPTY_BAIL_NEW, T),
        (MEMCONTROL_C, _PROACTIVE_BAIL_OLD, _PROACTIVE_BAIL_NEW, T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# Group 6: /proc/buddyinfo stops taking every zone's lock.
#
# mainline aaa98b100ea8 (v7.2, Yu Zhao).
#
# What it fixes.  frag_show() passes nolock=false to walk_zones_in_node(), so
# every read of /proc/buddyinfo takes each zone's spin_lock_irqsave and
# disables IRQs for the whole walk.  The print callback, however, only reads
# zone->free_area[order].nr_free -- a plain unsigned long per order.
#
# Why it is safe.  walk_zones_in_node()'s fourth parameter is `nolock`
# (mm/vmstat.c:1443-1460): with it true the lock pair is simply skipped.
# frag_show_print() reads nothing that needs the lock -- no free-list walk, no
# migratetype counters -- so the only consequence is a momentarily stale
# snapshot, which is what a diagnostic proc file is anyway.  On 5.15 the
# nolock mode is already exercised in-tree: pagetypeinfo_showmixedcount()
# (mm/vmstat.c:1610) passes nolock=true as well.  The sibling callbacks that
# DO need the lock (pagetypeinfo_showfree_print, which walks each free list)
# keep passing false and are untouched.
#
# Why it matters on a phone.  lmkd, dumpsys and vendor fragmentation monitors
# poll /proc/buddyinfo exactly when the allocator is contended -- i.e. when
# that zone lock is hottest -- so both the reader and the contending allocators
# pay for it.
#
# Fixture note: this is the module's first mm/vmstat.c group, so the file was
# added to tests/fetch_sublevel_tree.sh FETCH_FILES, tests/step_audit.py
# AUDIT_FILES and tests/smoke.sh SMOKE_FILES.
# ---------------------------------------------------------------------------

_BUDDYINFO_OLD = (
    "\twalk_zones_in_node(m, pgdat, true, false, frag_show_print);\n"
)

_BUDDYINFO_NEW = (
    "\twalk_zones_in_node(m, pgdat, true, true, frag_show_print);\n"
)


def _buddyinfo_nolock_apply(ctx):
    status, _results, detail = apply_steps(
        ctx, [(VMSTAT_C, _BUDDYINFO_OLD, _BUDDYINFO_NEW, T)])
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the Batch-38 PatchGroup records."""
    return [
        PatchGroup(
            "huge_memory_imap_split_uaf",
            "split_huge_page_to_list() releases the i_mmap lock before "
            "freeing the after-split subpages, so a concurrent evict()/iput() "
            "can no longer free the inode (and @mapping) out from under the "
            "final i_mmap_unlock_read(); stability fix, no perf claim",
            [
                "e923bd21058e (v7.2)",
                "f87c08060818 (5.15.y backport, 2026-08-19) -- the grafted form",
            ],
            [HUGE_MEMORY_C],
            _huge_memory_imap_split_uaf_apply,
        ),
        PatchGroup(
            "swap_readahead_lru_add_drain",
            "swap-in readahead stops forcing an lru_add_drain() on every "
            "cluster/VMA readahead: the pages reach the LRU through the "
            "caller's per-CPU pagevec batch anyway, so the flush only bought "
            "a lruvec_lock acquisition per swap-in fault",
            ["a4519e5b648a (v7.2)"],
            [SWAP_STATE_C],
            _swap_readahead_lru_add_drain_apply,
        ),
        PatchGroup(
            "vmscan_tasks_rcu_qs",
            "shrink_lruvec()'s scan loop reports Tasks-RCU quiescent states "
            "instead of a bare cond_resched(), so a task in long reclaim stops "
            "being an rcu_tasks holdout; no-op on this target's config "
            "(Tasks-RCU off), no device benefit claimed",
            [
                "25f52e812168 (v7.2)",
                "4cdc1bdf4094 (5.15.y backport, 2026-09-14) -- the grafted form",
            ],
            [VMSCAN_C],
            _vmscan_tasks_rcu_qs_apply,
        ),
        PatchGroup(
            "filemap_mmap_miss_tried",
            "filemap_map_pages() stops counting a FAULT_FLAG_TRIED retry as an "
            "mmap hit, so the mmap_miss counter no longer undershoots its "
            "increment side and read-around stays disabled for genuinely "
            "random mmap access under memory pressure",
            ["9b0fcac3cfe7 (v7.2, 1/2; 0b9c0aeba938 deliberately not grafted)"],
            [FILEMAP_C],
            _filemap_mmap_miss_tried_apply,
        ),
        PatchGroup(
            "memcg_dying_bailout",
            "memory.high / memory.max / memory.reclaim / memcg-v1 limit and "
            "force_empty writers stop reclaiming once the memcg is dying, so "
            "cgroup_rmdir() is no longer kept waiting in kernfs_drain() with "
            "cgroup_mutex held (upstream: 159 s cgdelete, 182 s hang for an "
            "unrelated cgroup reader)",
            [
                "0beeaf14e7b9 (v7.2, memory.high)",
                "e13f634f50d5 (v7.2, memory.max)",
                "757dd8193f6c (v7.2, proactive reclaim)",
                "10228e0a5123 (v7.2, memcg v1)",
            ],
            [MEMCONTROL_H, MEMCONTROL_C],
            _memcg_dying_bailout_apply,
        ),
        PatchGroup(
            "buddyinfo_nolock",
            "reading /proc/buddyinfo no longer takes every zone's "
            "spin_lock_irqsave: frag_show_print() only reads "
            "zone->free_area[order].nr_free, and lmkd/dumpsys plus vendor "
            "fragmentation monitors poll that file exactly while the allocator "
            "is contended",
            ["aaa98b100ea8 (v7.2)"],
            [VMSTAT_C],
            _buddyinfo_nolock_apply,
        ),
    ]