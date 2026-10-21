from __future__ import annotations

from abk_backport_engine import apply_steps

HUGE_MEMORY_C = "mm/huge_memory.c"
SWAP_STATE_C = "mm/swap_state.c"
FILEMAP_C = "mm/filemap.c"
VMSCAN_C = "mm/vmscan.c"
VMSTAT_C = "mm/vmstat.c"

T = True


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
            "being an rcu_tasks holdout (the target runs CONFIG_TASKS_RCU=y, "
            "so the classic holdout clear is compiled in); no speedup claimed "
            "for the device because none was measured",
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
