# -*- coding: utf-8 -*-

__all__ = ["build_steps", "T"]


T = True


_LEGACY_HDR_OLD = "static struct cftype mem_cgroup_legacy_files[] = {\n"

_LEGACY_HDR_NEW = (
    "/* ABK stable_515_backport: Batch 10-4 cgroup-v1 proactive reclaim.\n"
    " * The handler is defined further down next to the v2 cftype table; v1\n"
    " * needs the same file, so declare it before the legacy table.\n"
    " */\n"
    "static ssize_t memory_reclaim(struct kernfs_open_file *of, char *buf,\n"
    "\t\t\t      size_t nbytes, loff_t off);\n"
    "\n" +
    _LEGACY_HDR_OLD
)

_LEGACY_ENT_OLD = (
    "\t{\n"
    "\t\t.name = \"force_empty\",\n"
    "\t\t.write = mem_cgroup_force_empty_write,\n"
    "\t},\n"
)

_LEGACY_ENT_NEW = (
    _LEGACY_ENT_OLD +
    "\t{\n"
    "\t\t.name = \"reclaim\",\n"
    "\t\t.write = memory_reclaim,\n"
    "\t},\n"
)


_V1_STAT_OLD = (
    "\tBUILD_BUG_ON(ARRAY_SIZE(memcg1_stat_names) != ARRAY_SIZE(memcg1_stats));\n"
    "\n"
    "\tmem_cgroup_flush_stats();\n"
)

_V1_STAT_NEW = (
    _V1_STAT_OLD +
    "\n"
    "\t/* ABK stable_515_backport: Batch 10-4 cached freeze reclaim accounting. */\n"
    "\t{\n"
    "\t\textern atomic_long_t abk_cfr_reclaim_attempts;\n"
    "\t\textern atomic_long_t abk_cfr_reclaim_requested;\n"
    "\t\textern atomic_long_t abk_cfr_reclaim_reclaimed;\n"
    "\n"
    "\t\tseq_printf(m, \"cfr_reclaim_attempts %ld\\n\",\n"
    "\t\t\t   atomic_long_read(&abk_cfr_reclaim_attempts));\n"
    "\t\tseq_printf(m, \"cfr_reclaim_requested %ld\\n\",\n"
    "\t\t\t   atomic_long_read(&abk_cfr_reclaim_requested));\n"
    "\t\tseq_printf(m, \"cfr_reclaim_reclaimed %ld\\n\",\n"
    "\t\t\t   atomic_long_read(&abk_cfr_reclaim_reclaimed));\n"
    "\t}\n"
)


def build_steps():
    return [
        ("mm/memcontrol.c", _LEGACY_HDR_OLD, _LEGACY_HDR_NEW, T),
        ("mm/memcontrol.c", _LEGACY_ENT_OLD, _LEGACY_ENT_NEW, T),
        ("mm/memcontrol.c", _V1_STAT_OLD, _V1_STAT_NEW, T),
    ]
