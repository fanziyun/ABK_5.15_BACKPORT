# -*- coding: utf-8 -*-
"""Batch 10-3: cached freeze reclaim (generic name for memory-freeze).

AOSP chains two primitives: the cgroup v2 freezer parks a cached app's
threads, then CachedAppOptimizer writes the memcg's memory.current into its
memory.reclaim file, evicting file pages and swapping anon pages into zram
(source.android.com/docs/core/perf/cached-apps-freezer,
developer.android.com/topic/performance/memory/guide/reclaim).  The reclaim
execution primitive already landed in this module as memcg_memory_reclaim
(android14-6.1 memory.reclaim + MEMCG_RECLAIM_MAY_SWAP/PROACTIVE).  This
group adds the missing observation half, matching the agreed semantics:

  * kernel accounting: per-reclaim counters incremented inside
    try_to_free_mem_cgroup_pages() for proactive (memory.reclaim) requests
    only, and reported in the memory.stat text -- observable without new
    sysfs nodes or KABI struct fields;
  * freezer observability: NOT grafted.  cgroup_update_frozen() already emits
    the upstream TRACE_CGROUP_PATH(notify_frozen, cgrp, frozen) event on both
    CGRP_FROZEN transitions, which Perfetto's Freezer track and
    `am freeze/unfreeze` verification consume -- see the note above
    build_steps() for why a private duplicate was tried and reverted.

Every anchor here is pristine text, deliberately *outside* the blocks the
memcg_memory_reclaim group installs: recompressing inside another group's
replacement text would break that group's replace_once idempotency on the
second pass (the same trap the zram recompression group hit).  The counters
therefore live in mm/vmscan.c (the reclaim engine) and are read from
mm/memcontrol.c through a block-scope extern; the only cross-group reference
is the MEMCG_RECLAIM_PROACTIVE flag, which memcg_memory_reclaim defines and
this group is registered after.

Anchors verified verbatim on 5.15.167 (2024-11), 5.15.178 (2025-03) and
5.15.194 (2025-12):
  * mm/vmscan.c: the `#include <linux/memcontrol.h>` line (file heads are
    byte-identical across the three baselines) and the
    try_to_free_mem_cgroup_pages() tail, which no earlier group rewrites;
  * mm/memcontrol.c: the tail of memory_stat_format().

The userspace daemon half lives in tools/cached_freeze_reclaim.sh
(quota-limited reclaim sweeps over cached UIDs with AOSP-aligned unfreeze
triggers); it is a distribution artifact, not a tree graft.
"""

__all__ = ["build_steps", "T"]


T = True

# ---------------------------------------------------------------------------
# 1) mm/vmscan.c: counter storage next to the reclaim engine's includes.
# ---------------------------------------------------------------------------

_CFR_DECL_OLD = "#include <linux/memcontrol.h>\n"

_CFR_DECL_NEW = (
    "#include <linux/memcontrol.h>\n"
    "\n"
    "/* ABK stable_515_backport: cached freeze reclaim accounting (Batch 10-3). */\n"
    "atomic_long_t abk_cfr_reclaim_attempts;\n"
    "atomic_long_t abk_cfr_reclaim_requested;\n"
    "atomic_long_t abk_cfr_reclaim_reclaimed;\n"
)

# ---------------------------------------------------------------------------
# 2) mm/vmscan.c: feed the counters from the proactive-reclaim path only.
# ---------------------------------------------------------------------------

_CFR_ACC_OLD = (
    "\tmemalloc_noreclaim_restore(noreclaim_flag);\n"
    "\ttrace_mm_vmscan_memcg_reclaim_end(nr_reclaimed);\n"
    "\tset_task_reclaim_state(current, NULL);\n"
    "\n"
    "\treturn nr_reclaimed;\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(try_to_free_mem_cgroup_pages);\n"
)

_CFR_ACC_NEW = (
    "\tmemalloc_noreclaim_restore(noreclaim_flag);\n"
    "\ttrace_mm_vmscan_memcg_reclaim_end(nr_reclaimed);\n"
    "\tset_task_reclaim_state(current, NULL);\n"
    "\n"
    "\t/* ABK stable_515_backport: cached freeze reclaim accounting (Batch 10-3). */\n"
    "\tif (reclaim_options & MEMCG_RECLAIM_PROACTIVE) {\n"
    "\t\tatomic_long_inc(&abk_cfr_reclaim_attempts);\n"
    "\t\tatomic_long_add(nr_pages, &abk_cfr_reclaim_requested);\n"
    "\t\tatomic_long_add(nr_reclaimed, &abk_cfr_reclaim_reclaimed);\n"
    "\t}\n"
    "\n"
    "\treturn nr_reclaimed;\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(try_to_free_mem_cgroup_pages);\n"
)

# ---------------------------------------------------------------------------
# 3) mm/memcontrol.c: report the counters in the memory.stat text.
# ---------------------------------------------------------------------------

_CFR_STAT_OLD = (
    "\t/* The above should easily fit into one page */\n"
    "\tWARN_ON_ONCE(seq_buf_has_overflowed(&s));\n"
)

_CFR_STAT_NEW = (
    "\t/* ABK stable_515_backport: cached freeze reclaim accounting (Batch 10-3). */\n"
    "\t{\n"
    "\t\textern atomic_long_t abk_cfr_reclaim_attempts;\n"
    "\t\textern atomic_long_t abk_cfr_reclaim_requested;\n"
    "\t\textern atomic_long_t abk_cfr_reclaim_reclaimed;\n"
    "\n"
    "\t\tseq_buf_printf(&s, \"cfr_reclaim_attempts %ld\\n\",\n"
    "\t\t\t       atomic_long_read(&abk_cfr_reclaim_attempts));\n"
    "\t\tseq_buf_printf(&s, \"cfr_reclaim_requested %ld\\n\",\n"
    "\t\t\t       atomic_long_read(&abk_cfr_reclaim_requested));\n"
    "\t\tseq_buf_printf(&s, \"cfr_reclaim_reclaimed %ld\\n\",\n"
    "\t\t\t       atomic_long_read(&abk_cfr_reclaim_reclaimed));\n"
    "\t}\n"
    "\n" +
    _CFR_STAT_OLD
)

# ---------------------------------------------------------------------------
# Freezer observability is deliberately NOT grafted.
#
# The two CGRP_FROZEN transitions in cgroup_update_frozen() already emit
# TRACE_CGROUP_PATH(notify_frozen, cgrp, frozen) -- the platform-native cgroup
# freezer event that Perfetto's Freezer track and `am freeze/unfreeze`
# verification already consume, in both directions.  Adding abk_cfr_freeze /
# abk_cfr_thaw next to it was tried in review and reverted for two reasons:
#
#   * it is a duplicate of an existing event on the exact same transition;
#   * a TRACE_EVENT() written into kernel/cgroup/freezer.c links to nothing,
#     because that file includes <trace/events/cgroup.h> without
#     CREATE_TRACE_POINTS, so __tracepoint_/__traceiter_abk_cfr_* are only
#     declared, never defined -- the build dies at ld.lld with
#     "undefined symbol: __tracepoint_abk_cfr_freeze".  Instantiating them
#     would need a new trace header plus a CREATE_TRACE_POINTS owner, for an
#     event the baseline already provides.
# ---------------------------------------------------------------------------


def build_steps():
    return [
        ("mm/vmscan.c", _CFR_DECL_OLD, _CFR_DECL_NEW, T),
        ("mm/vmscan.c", _CFR_ACC_OLD, _CFR_ACC_NEW, T),
        ("mm/memcontrol.c", _CFR_STAT_OLD, _CFR_STAT_NEW, T),
    ]
