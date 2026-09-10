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
  * freezer tracepoints: abk_cfr_freeze / abk_cfr_thaw emitted on the two
    CGRP_FROZEN transitions in cgroup_update_frozen(), which is what
    Perfetto's Freezer track and `am freeze/unfreeze` verification read.

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
  * mm/memcontrol.c: the tail of memory_stat_format();
  * kernel/cgroup/freezer.c: cgroup_update_frozen() and its two CGRP_FROZEN
    transitions.  freezer.c already pulls in trace/events/cgroup.h, so
    TRACE_EVENT is available.

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
# 4) kernel/cgroup/freezer.c: the two CGRP_FROZEN transitions become trace
#    points.  The TRACE_EVENT definitions go in front of the function so the
#    calls below resolve, and the comment block stays contiguous.
# ---------------------------------------------------------------------------

_CFR_EVENTS_OLD = (
    "/*\n"
    " * Revisit the cgroup frozen state.\n"
    " * Checks if the cgroup is really frozen and perform all state transitions.\n"
    " */\n"
    "void cgroup_update_frozen(struct cgroup *cgrp)\n"
)

_CFR_EVENTS_NEW = (
    "/* ABK stable_515_backport: cached freeze reclaim freezer tracepoints (Batch 10-3). */\n"
    "TRACE_EVENT(abk_cfr_freeze,\n"
    "\tTP_PROTO(struct cgroup *cgrp),\n"
    "\tTP_ARGS(cgrp),\n"
    "\tTP_STRUCT__entry(\n"
    "\t\t__field(struct cgroup *, cgrp)\n"
    "\t),\n"
    "\tTP_fast_assign(\n"
    "\t\t__entry->cgrp = cgrp;\n"
    "\t),\n"
    "\tTP_printk(\"cgrp=%p\", __entry->cgrp)\n"
    ");\n"
    "\n"
    "TRACE_EVENT(abk_cfr_thaw,\n"
    "\tTP_PROTO(struct cgroup *cgrp),\n"
    "\tTP_ARGS(cgrp),\n"
    "\tTP_STRUCT__entry(\n"
    "\t\t__field(struct cgroup *, cgrp)\n"
    "\t),\n"
    "\tTP_fast_assign(\n"
    "\t\t__entry->cgrp = cgrp;\n"
    "\t),\n"
    "\tTP_printk(\"cgrp=%p\", __entry->cgrp)\n"
    ");\n"
    "\n" +
    _CFR_EVENTS_OLD
)

_CFR_FREEZE_OLD = (
    "\t\tset_bit(CGRP_FROZEN, &cgrp->flags);\n"
    "\t} else {\n"
)

_CFR_FREEZE_NEW = (
    "\t\tset_bit(CGRP_FROZEN, &cgrp->flags);\n"
    "\t\t/* ABK stable_515_backport: Batch 10-3 freezer tracepoint. */\n"
    "\t\ttrace_abk_cfr_freeze(cgrp);\n"
    "\t} else {\n"
)

_CFR_THAW_OLD = (
    "\t\tclear_bit(CGRP_FROZEN, &cgrp->flags);\n"
    "\t}\n"
)

_CFR_THAW_NEW = (
    "\t\tclear_bit(CGRP_FROZEN, &cgrp->flags);\n"
    "\t\t/* ABK stable_515_backport: Batch 10-3 freezer tracepoint. */\n"
    "\t\ttrace_abk_cfr_thaw(cgrp);\n"
    "\t}\n"
)


def build_steps():
    return [
        ("mm/vmscan.c", _CFR_DECL_OLD, _CFR_DECL_NEW, T),
        ("mm/vmscan.c", _CFR_ACC_OLD, _CFR_ACC_NEW, T),
        ("mm/memcontrol.c", _CFR_STAT_OLD, _CFR_STAT_NEW, T),
        ("kernel/cgroup/freezer.c", _CFR_EVENTS_OLD, _CFR_EVENTS_NEW, T),
        ("kernel/cgroup/freezer.c", _CFR_FREEZE_OLD, _CFR_FREEZE_NEW, T),
        ("kernel/cgroup/freezer.c", _CFR_THAW_OLD, _CFR_THAW_NEW, T),
    ]
