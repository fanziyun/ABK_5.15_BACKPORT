# -*- coding: utf-8 -*-
"""Batch 10-4b: cgroup-v1 proactive reclaim + reclaim accounting visibility.

The module's per-memcg proactive reclaim (Batch 5) and the cached-freeze
reclaim counters (Batch 10-3) both hang off the **cgroup v2** side of
memcontrol.c: `memory_reclaim()` is wired into `memory_files[]` and the
counters are printed by `memory_stat_format()`.  On the target device the
memory controller is mounted **v1** (`/dev/memcg`, with
`/sys/fs/cgroup/cgroup.controllers` empty), so neither exists there:

  * `memory.reclaim` is absent -- only the vendor's own `memory.reclaim_once`
    in `/dev/memcg/`, so tools/cached_freeze_reclaim.sh cannot run at all;
  * `cfr_reclaim_*` never appears -- v1 renders `memory.stat` from
    `memcg_stat_show()`, not from `memory_stat_format()`.

This group closes both gaps on the v1 side:

  * a forward declaration of the shared `memory_reclaim()` handler, placed
    before `mem_cgroup_legacy_files[]` (the handler itself is defined much
    later in the file), plus the `reclaim` cftype entry in that legacy array.
    The same handler is reused rather than duplicated, and because it sets
    `MEMCG_RECLAIM_PROACTIVE` the v1 path feeds the Batch 10-3 counters too;
  * the `cfr_reclaim_*` lines in `memcg_stat_show()`, matching the v2 output.

Every anchor is pristine text.  The two v2-side blocks are deliberately left
alone (they belong to Batch 5 / Batch 10-3, and editing inside them would
break those groups' second-pass idempotency).

Anchors verified verbatim on 5.15.167 (2024-11), 5.15.178 (2025-03) and
5.15.194 (2025-12).
"""

__all__ = ["build_steps", "T"]


T = True

# ---------------------------------------------------------------------------
# 1) forward declaration + the v1 cftype entry in mem_cgroup_legacy_files[].
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# 2) the counters in the v1 stat renderer.
# ---------------------------------------------------------------------------

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
