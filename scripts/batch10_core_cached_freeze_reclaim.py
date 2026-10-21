# -*- coding: utf-8 -*-

__all__ = ["build_steps", "T"]


T = True


_CFR_DECL_OLD = "#include <linux/memcontrol.h>\n"

_CFR_DECL_NEW = (
    "#include <linux/memcontrol.h>\n"
    "\n"
    "/* ABK stable_515_backport: cached freeze reclaim accounting (Batch 10-3). */\n"
    "atomic_long_t abk_cfr_reclaim_attempts;\n"
    "atomic_long_t abk_cfr_reclaim_requested;\n"
    "atomic_long_t abk_cfr_reclaim_reclaimed;\n"
)


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


def build_steps():
    return [
        ("mm/vmscan.c", _CFR_DECL_OLD, _CFR_DECL_NEW, T),
        ("mm/vmscan.c", _CFR_ACC_OLD, _CFR_ACC_NEW, T),
        ("mm/memcontrol.c", _CFR_STAT_OLD, _CFR_STAT_NEW, T),
    ]
