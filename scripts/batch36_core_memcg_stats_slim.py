from __future__ import annotations

from abk_backport_engine import apply_steps

MEMCONTROL_C = "mm/memcontrol.c"
MEMCONTROL_H = "include/linux/memcontrol.h"

T = True


_SLIM_BLOCK_OLD = (
    "/*\n"
    " * memcg and lruvec stats flushing\n"
    " *\n"
    " * Many codepaths leading to stats update or read are performance sensitive and\n"
)

_SLIM_BLOCK_NEW = (
    "/*\n"
    " * ABK stable_515_backport: memcg_stats_percpu_slim\n"
    " *\n"
    " * Memory reduction for the memcg stats, from mainline 70a64b7919cb +\n"
    " * ff48c71c26aa (v6.10, Shakeel Butt): memcg only accounts a subset of\n"
    " * node_stat_item / vm_event_item, so the per-cpu stats arrays are indexed\n"
    " * through an item -> slot table instead of being sized with the full\n"
    " * enums.\n"
    " *\n"
    " * Reshaped for this tree: upstream also shrinks the embedded\n"
    " * memcg_vmstats/lruvec_stats aggregates, which moves members of the\n"
    " * KMI-visible structs mem_cgroup and mem_cgroup_per_node (both carry\n"
    " * layouts in android/abi_gki_aarch64.xml and have no ANDROID_KABI_RESERVE\n"
    " * slots; shrinking cannot be masked).  Here only the two per-cpu objects\n"
    " * are compacted: they are heap objects behind __percpu pointer fields, so\n"
    " * the header definitions stay byte-identical and the real objects are the\n"
    " * private structs below, allocated with __alloc_percpu_gfp().  The\n"
    " * aggregates keep their full, raw-indexed arrays; the rstat flush maps\n"
    " * each compact per-cpu slot back to its item when it propagates.\n"
    " * memcg_stats_index()/memcg_events_index() answer -1 for items with no\n"
    " * memcg accounting; their per-cpu updates are dropped, exactly like 6.10\n"
    " * drops them after its own index tables.\n"
    " */\n"
    "\n"
    "/* Subset of node_stat_item with memcg accounting (the memory_stats[]\n"
    " * readers and the v1 memcg1_stats[] table). */\n"
    "static const unsigned int memcg_node_stat_items[] = {\n"
    "\tNR_INACTIVE_ANON,\n"
    "\tNR_ACTIVE_ANON,\n"
    "\tNR_INACTIVE_FILE,\n"
    "\tNR_ACTIVE_FILE,\n"
    "\tNR_UNEVICTABLE,\n"
    "\tNR_SLAB_RECLAIMABLE_B,\n"
    "\tNR_SLAB_UNRECLAIMABLE_B,\n"
    "\tWORKINGSET_REFAULT_ANON,\n"
    "\tWORKINGSET_REFAULT_FILE,\n"
    "\tWORKINGSET_ACTIVATE_ANON,\n"
    "\tWORKINGSET_ACTIVATE_FILE,\n"
    "\tWORKINGSET_RESTORE_ANON,\n"
    "\tWORKINGSET_RESTORE_FILE,\n"
    "\tWORKINGSET_NODERECLAIM,\n"
    "\tNR_ANON_MAPPED,\n"
    "\tNR_FILE_MAPPED,\n"
    "\tNR_FILE_PAGES,\n"
    "\tNR_FILE_DIRTY,\n"
    "\tNR_WRITEBACK,\n"
    "\tNR_SHMEM,\n"
    "\tNR_SHMEM_THPS,\n"
    "\tNR_FILE_THPS,\n"
    "\tNR_ANON_THPS,\n"
    "\tNR_KERNEL_STACK_KB,\n"
    "\tNR_PAGETABLE,\n"
    "#ifdef CONFIG_SWAP\n"
    "\tNR_SWAPCACHE,\n"
    "#endif\n"
    "};\n"
    "\n"
    "/* memcg-only items appended after the lruvec subset. */\n"
    "static const unsigned int memcg_stat_items[] = {\n"
    "\tMEMCG_SWAP,\n"
    "\tMEMCG_SOCK,\n"
    "\tMEMCG_PERCPU_B,\n"
    "};\n"
    "\n"
    "#define NR_MEMCG_NODE_STAT_ITEMS ARRAY_SIZE(memcg_node_stat_items)\n"
    "#define NR_MEMCG_VMSTAT_SIZE (NR_MEMCG_NODE_STAT_ITEMS + \\\n"
    "\t\t\t      ARRAY_SIZE(memcg_stat_items))\n"
    "\n"
    "/* Subset of vm_event_item counted into the memcg stats. */\n"
    "static const unsigned int memcg_vm_event_items[] = {\n"
    "\tPGPGIN,\n"
    "\tPGPGOUT,\n"
    "\tPGFAULT,\n"
    "\tPGMAJFAULT,\n"
    "\tPGREFILL,\n"
    "\tPGSCAN_KSWAPD,\n"
    "\tPGSCAN_DIRECT,\n"
    "\tPGSTEAL_KSWAPD,\n"
    "\tPGSTEAL_DIRECT,\n"
    "\tPGACTIVATE,\n"
    "\tPGDEACTIVATE,\n"
    "\tPGLAZYFREE,\n"
    "\tPGLAZYFREED,\n"
    "#ifdef CONFIG_TRANSPARENT_HUGEPAGE\n"
    "\tTHP_FAULT_ALLOC,\n"
    "\tTHP_COLLAPSE_ALLOC,\n"
    "#endif\n"
    "};\n"
    "\n"
    "#define NR_MEMCG_VM_EVENTS ARRAY_SIZE(memcg_vm_event_items)\n"
    "\n"
    "static int8_t mem_cgroup_stats_index[MEMCG_NR_STAT] __read_mostly;\n"
    "static int8_t mem_cgroup_events_index[NR_VM_EVENT_ITEMS] __read_mostly;\n"
    "\n"
    "static void init_memcg_stats(void)\n"
    "{\n"
    "\tint8_t i, j = 0;\n"
    "\n"
    "\tBUILD_BUG_ON(MEMCG_NR_STAT >= S8_MAX);\n"
    "\n"
    "\tfor (i = 0; i < NR_MEMCG_NODE_STAT_ITEMS; ++i)\n"
    "\t\tmem_cgroup_stats_index[memcg_node_stat_items[i]] = ++j;\n"
    "\n"
    "\tfor (i = 0; i < ARRAY_SIZE(memcg_stat_items); ++i)\n"
    "\t\tmem_cgroup_stats_index[memcg_stat_items[i]] = ++j;\n"
    "}\n"
    "\n"
    "static void init_memcg_events(void)\n"
    "{\n"
    "\tint8_t i, j = 0;\n"
    "\n"
    "\tBUILD_BUG_ON(NR_VM_EVENT_ITEMS >= S8_MAX);\n"
    "\n"
    "\tfor (i = 0; i < NR_MEMCG_VM_EVENTS; ++i)\n"
    "\t\tmem_cgroup_events_index[memcg_vm_event_items[i]] = ++j;\n"
    "}\n"
    "\n"
    "static inline int memcg_stats_index(int idx)\n"
    "{\n"
    "\treturn mem_cgroup_stats_index[idx] - 1;\n"
    "}\n"
    "\n"
    "static inline int memcg_events_index(int idx)\n"
    "{\n"
    "\treturn mem_cgroup_events_index[idx] - 1;\n"
    "}\n"
    "\n"
    "/*\n"
    " * The compact per-cpu stats objects.  Field layout mirrors\n"
    " * struct memcg_vmstats_percpu / struct lruvec_stats_percpu; only the\n"
    " * array widths differ.  The declared pointer fields in struct mem_cgroup\n"
    " * and struct mem_cgroup_per_node keep pointing at these.\n"
    " */\n"
    "struct abk_vmstats_percpu {\n"
    "\t/* Local (CPU and cgroup) page state & events */\n"
    "\tlong\t\t\tstate[NR_MEMCG_VMSTAT_SIZE];\n"
    "\tunsigned long\t\tevents[NR_MEMCG_VM_EVENTS];\n"
    "\n"
    "\t/* Delta calculation for lockless upward propagation */\n"
    "\tlong\t\t\tstate_prev[NR_MEMCG_VMSTAT_SIZE];\n"
    "\tunsigned long\t\tevents_prev[NR_MEMCG_VM_EVENTS];\n"
    "\n"
    "\t/* Cgroup1: threshold notifications & softlimit tree updates */\n"
    "\tunsigned long\t\tnr_page_events;\n"
    "\tunsigned long\t\ttargets[MEM_CGROUP_NTARGETS];\n"
    "};\n"
    "\n"
    "struct abk_lruvec_stats_percpu {\n"
    "\t/* Local (CPU and cgroup) state */\n"
    "\tlong state[NR_MEMCG_NODE_STAT_ITEMS];\n"
    "\n"
    "\t/* Delta calculation for lockless upward propagation */\n"
    "\tlong state_prev[NR_MEMCG_NODE_STAT_ITEMS];\n"
    "};\n"
    "\n"
    "static inline struct abk_vmstats_percpu __percpu *\n"
    "abk_vmstats_percpu_of(struct mem_cgroup *memcg)\n"
    "{\n"
    "\treturn (struct abk_vmstats_percpu __percpu *)memcg->vmstats_percpu;\n"
    "}\n"
    "\n"
    "static inline struct abk_lruvec_stats_percpu __percpu *\n"
    "abk_lruvec_stats_percpu_of(struct mem_cgroup_per_node *pn)\n"
    "{\n"
    "\treturn (struct abk_lruvec_stats_percpu __percpu *)\n"
    "\t\tpn->lruvec_stats_percpu;\n"
    "}\n"
    "\n"
    "/*\n"
    " * Out of line (moved from include/linux/memcontrol.h): the per-cpu\n"
    " * lruvec stats object is compact and slot-indexed, so the reader needs\n"
    " * the index table above.  No struct member moves; the KMI types keep\n"
    " * their layouts.\n"
    " */\n"
    "unsigned long lruvec_page_state_local(struct lruvec *lruvec,\n"
    "\t\t\t\t      enum node_stat_item idx)\n"
    "{\n"
    "\tstruct mem_cgroup_per_node *pn;\n"
    "\tlong x = 0;\n"
    "\tint cpu;\n"
    "\tint i;\n"
    "\n"
    "\tif (mem_cgroup_disabled())\n"
    "\t\treturn node_page_state(lruvec_pgdat(lruvec), idx);\n"
    "\n"
    "\ti = memcg_stats_index(idx);\n"
    "\tif (i < 0)\n"
    "\t\treturn 0;\n"
    "\n"
    "\tpn = container_of(lruvec, struct mem_cgroup_per_node, lruvec);\n"
    "\tfor_each_possible_cpu(cpu)\n"
    "\t\tx += per_cpu(abk_lruvec_stats_percpu_of(pn)->state[i], cpu);\n"
    "#ifdef CONFIG_SMP\n"
    "\tif (x < 0)\n"
    "\t\tx = 0;\n"
    "#endif\n"
    "\treturn x;\n"
    "}\n"
    "\n"
    "/*\n"
    " * memcg and lruvec stats flushing\n"
    " *\n"
    " * Many codepaths leading to stats update or read are performance sensitive and\n"
)


_MOD_STATE_OLD = (
    "void __mod_memcg_state(struct mem_cgroup *memcg, int idx, int val)\n"
    "{\n"
    "\tif (mem_cgroup_disabled())\n"
    "\t\treturn;\n"
    "\n"
    "\t__this_cpu_add(memcg->vmstats_percpu->state[idx], val);\n"
    "\tmemcg_rstat_updated(memcg, val);\n"
    "}\n"
)

_MOD_STATE_NEW = (
    "void __mod_memcg_state(struct mem_cgroup *memcg, int idx, int val)\n"
    "{\n"
    "\tint i;\n"
    "\n"
    "\tif (mem_cgroup_disabled())\n"
    "\t\treturn;\n"
    "\n"
    "\ti = memcg_stats_index(idx);\n"
    "\tif (i < 0)\n"
    "\t\treturn;\n"
    "\n"
    "\t__this_cpu_add(abk_vmstats_percpu_of(memcg)->state[i], val);\n"
    "\tmemcg_rstat_updated(memcg, val);\n"
    "}\n"
)


_PAGE_STATE_LOCAL_OLD = (
    "/* idx can be of type enum memcg_stat_item or node_stat_item. */\n"
    "static unsigned long memcg_page_state_local(struct mem_cgroup *memcg, int idx)\n"
    "{\n"
    "\tlong x = 0;\n"
    "\tint cpu;\n"
    "\n"
    "\tfor_each_possible_cpu(cpu)\n"
    "\t\tx += per_cpu(memcg->vmstats_percpu->state[idx], cpu);\n"
    "#ifdef CONFIG_SMP\n"
    "\tif (x < 0)\n"
    "\t\tx = 0;\n"
    "#endif\n"
    "\treturn x;\n"
    "}\n"
)

_PAGE_STATE_LOCAL_NEW = (
    "/* idx can be of type enum memcg_stat_item or node_stat_item. */\n"
    "static unsigned long memcg_page_state_local(struct mem_cgroup *memcg, int idx)\n"
    "{\n"
    "\tlong x = 0;\n"
    "\tint cpu;\n"
    "\tint i = memcg_stats_index(idx);\n"
    "\n"
    "\tif (i < 0)\n"
    "\t\treturn 0;\n"
    "\n"
    "\tfor_each_possible_cpu(cpu)\n"
    "\t\tx += per_cpu(abk_vmstats_percpu_of(memcg)->state[i], cpu);\n"
    "#ifdef CONFIG_SMP\n"
    "\tif (x < 0)\n"
    "\t\tx = 0;\n"
    "#endif\n"
    "\treturn x;\n"
    "}\n"
)


_MOD_LRUVEC_OLD = (
    "void __mod_memcg_lruvec_state(struct lruvec *lruvec, enum node_stat_item idx,\n"
    "\t\t\t      int val)\n"
    "{\n"
    "\tstruct mem_cgroup_per_node *pn;\n"
    "\tstruct mem_cgroup *memcg;\n"
    "\n"
    "\tpn = container_of(lruvec, struct mem_cgroup_per_node, lruvec);\n"
    "\tmemcg = pn->memcg;\n"
    "\n"
    "\t/* Update memcg */\n"
    "\t__this_cpu_add(memcg->vmstats_percpu->state[idx], val);\n"
    "\n"
    "\t/* Update lruvec */\n"
    "\t__this_cpu_add(pn->lruvec_stats_percpu->state[idx], val);\n"
    "\n"
    "\tmemcg_rstat_updated(memcg, val);\n"
    "}\n"
)

_MOD_LRUVEC_NEW = (
    "void __mod_memcg_lruvec_state(struct lruvec *lruvec, enum node_stat_item idx,\n"
    "\t\t\t      int val)\n"
    "{\n"
    "\tstruct mem_cgroup_per_node *pn;\n"
    "\tstruct mem_cgroup *memcg;\n"
    "\tint i = memcg_stats_index(idx);\n"
    "\n"
    "\tif (i < 0)\n"
    "\t\treturn;\n"
    "\n"
    "\tpn = container_of(lruvec, struct mem_cgroup_per_node, lruvec);\n"
    "\tmemcg = pn->memcg;\n"
    "\n"
    "\t/* Update memcg */\n"
    "\t__this_cpu_add(abk_vmstats_percpu_of(memcg)->state[i], val);\n"
    "\n"
    "\t/* Update lruvec */\n"
    "\t__this_cpu_add(abk_lruvec_stats_percpu_of(pn)->state[i], val);\n"
    "\n"
    "\tmemcg_rstat_updated(memcg, val);\n"
    "}\n"
)


_COUNT_EVENTS_OLD = (
    "void __count_memcg_events(struct mem_cgroup *memcg, enum vm_event_item idx,\n"
    "\t\t\t  unsigned long count)\n"
    "{\n"
    "\tif (mem_cgroup_disabled())\n"
    "\t\treturn;\n"
    "\n"
    "\t__this_cpu_add(memcg->vmstats_percpu->events[idx], count);\n"
    "\tmemcg_rstat_updated(memcg, count);\n"
    "}\n"
)

_COUNT_EVENTS_NEW = (
    "void __count_memcg_events(struct mem_cgroup *memcg, enum vm_event_item idx,\n"
    "\t\t\t  unsigned long count)\n"
    "{\n"
    "\tint i;\n"
    "\n"
    "\tif (mem_cgroup_disabled())\n"
    "\t\treturn;\n"
    "\n"
    "\ti = memcg_events_index(idx);\n"
    "\tif (i < 0)\n"
    "\t\treturn;\n"
    "\n"
    "\t__this_cpu_add(abk_vmstats_percpu_of(memcg)->events[i], count);\n"
    "\tmemcg_rstat_updated(memcg, count);\n"
    "}\n"
)


_EVENTS_LOCAL_OLD = (
    "static unsigned long memcg_events_local(struct mem_cgroup *memcg, int event)\n"
    "{\n"
    "\tlong x = 0;\n"
    "\tint cpu;\n"
    "\n"
    "\tfor_each_possible_cpu(cpu)\n"
    "\t\tx += per_cpu(memcg->vmstats_percpu->events[event], cpu);\n"
    "\treturn x;\n"
    "}\n"
)

_EVENTS_LOCAL_NEW = (
    "static unsigned long memcg_events_local(struct mem_cgroup *memcg, int event)\n"
    "{\n"
    "\tlong x = 0;\n"
    "\tint cpu;\n"
    "\tint i = memcg_events_index(event);\n"
    "\n"
    "\tif (i < 0)\n"
    "\t\treturn 0;\n"
    "\n"
    "\tfor_each_possible_cpu(cpu)\n"
    "\t\tx += per_cpu(abk_vmstats_percpu_of(memcg)->events[i], cpu);\n"
    "\treturn x;\n"
    "}\n"
)


_NR_PAGE_EVENTS_OLD = (
    "\t__this_cpu_add(memcg->vmstats_percpu->nr_page_events, nr_pages);\n"
)

_NR_PAGE_EVENTS_NEW = (
    "\t__this_cpu_add(abk_vmstats_percpu_of(memcg)->nr_page_events,\n"
    "\t\t\t nr_pages);\n"
)

_RATELIMIT_READ_OLD = (
    "\tval = __this_cpu_read(memcg->vmstats_percpu->nr_page_events);\n"
    "\tnext = __this_cpu_read(memcg->vmstats_percpu->targets[target]);\n"
)

_RATELIMIT_READ_NEW = (
    "\tval = __this_cpu_read(abk_vmstats_percpu_of(memcg)->nr_page_events);\n"
    "\tnext = __this_cpu_read(abk_vmstats_percpu_of(memcg)->targets[target]);\n"
)

_RATELIMIT_WRITE_OLD = (
    "\t\t__this_cpu_write(memcg->vmstats_percpu->targets[target], next);\n"
)

_RATELIMIT_WRITE_NEW = (
    "\t\t__this_cpu_write(abk_vmstats_percpu_of(memcg)->targets[target],\n"
    "\t\t\t\t next);\n"
)


_UNCHARGE_OLD = (
    "\t__this_cpu_add(ug->memcg->vmstats_percpu->nr_page_events, ug->nr_memory);\n"
)

_UNCHARGE_NEW = (
    "\t__this_cpu_add(abk_vmstats_percpu_of(ug->memcg)->nr_page_events,\n"
    "\t\t\t ug->nr_memory);\n"
)


_PN_ALLOC_OLD = (
    "\tpn->lruvec_stats_percpu = alloc_percpu_gfp(struct lruvec_stats_percpu,\n"
    "\t\t\t\t\t\t   GFP_KERNEL_ACCOUNT);\n"
)

_PN_ALLOC_NEW = (
    "\tpn->lruvec_stats_percpu = (struct lruvec_stats_percpu __percpu *)\n"
    "\t\t__alloc_percpu_gfp(sizeof(struct abk_lruvec_stats_percpu),\n"
    "\t\t\t\t   __alignof__(struct abk_lruvec_stats_percpu),\n"
    "\t\t\t\t   GFP_KERNEL_ACCOUNT);\n"
)

_MEMCG_ALLOC_OLD = (
    "\tmemcg->vmstats_percpu = alloc_percpu_gfp(struct memcg_vmstats_percpu,\n"
    "\t\t\t\t\t\t GFP_KERNEL_ACCOUNT);\n"
)

_MEMCG_ALLOC_NEW = (
    "\tmemcg->vmstats_percpu = (struct memcg_vmstats_percpu __percpu *)\n"
    "\t\t__alloc_percpu_gfp(sizeof(struct abk_vmstats_percpu),\n"
    "\t\t\t\t   __alignof__(struct abk_vmstats_percpu),\n"
    "\t\t\t\t   GFP_KERNEL_ACCOUNT);\n"
)


_CSS_ALLOC_OLD = (
    "\t} else {\n"
    "\t\tpage_counter_init(&memcg->memory, NULL);\n"
)

_CSS_ALLOC_NEW = (
    "\t} else {\n"
    "\t\tinit_memcg_stats();\n"
    "\t\tinit_memcg_events();\n"
    "\n"
    "\t\tpage_counter_init(&memcg->memory, NULL);\n"
)


_FLUSH_DECL_OLD = (
    "\tstruct mem_cgroup *parent = parent_mem_cgroup(memcg);\n"
    "\tstruct memcg_vmstats_percpu *statc;\n"
)

_FLUSH_DECL_NEW = (
    "\tstruct mem_cgroup *parent = parent_mem_cgroup(memcg);\n"
    "\tstruct abk_vmstats_percpu *statc;\n"
)

_FLUSH_STATC_OLD = (
    "\tstatc = per_cpu_ptr(memcg->vmstats_percpu, cpu);\n"
)

_FLUSH_STATC_NEW = (
    "\tstatc = per_cpu_ptr(abk_vmstats_percpu_of(memcg), cpu);\n"
)

_FLUSH_STATE_OLD = (
    "\tfor (i = 0; i < MEMCG_NR_STAT; i++) {\n"
    "\t\t/*\n"
    "\t\t * Collect the aggregated propagation counts of groups\n"
    "\t\t * below us. We're in a per-cpu loop here and this is\n"
    "\t\t * a global counter, so the first cycle will get them.\n"
    "\t\t */\n"
    "\t\tdelta = memcg->vmstats.state_pending[i];\n"
    "\t\tif (delta)\n"
    "\t\t\tmemcg->vmstats.state_pending[i] = 0;\n"
    "\n"
    "\t\t/* Add CPU changes on this level since the last flush */\n"
    "\t\tv = READ_ONCE(statc->state[i]);\n"
    "\t\tif (v != statc->state_prev[i]) {\n"
    "\t\t\tdelta += v - statc->state_prev[i];\n"
    "\t\t\tstatc->state_prev[i] = v;\n"
    "\t\t}\n"
    "\n"
    "\t\tif (!delta)\n"
    "\t\t\tcontinue;\n"
    "\n"
    "\t\t/* Aggregate counts on this level and propagate upwards */\n"
    "\t\tmemcg->vmstats.state[i] += delta;\n"
    "\t\tif (parent)\n"
    "\t\t\tparent->vmstats.state_pending[i] += delta;\n"
    "\t}\n"
)

_FLUSH_STATE_NEW = (
    "\tfor (i = 0; i < NR_MEMCG_VMSTAT_SIZE; i++) {\n"
    "\t\t/*\n"
    "\t\t * ABK stable_515_backport: memcg_stats_percpu_slim.  The percpu\n"
    "\t\t * object is compact and slot-indexed, the embedded aggregate stays\n"
    "\t\t * raw-indexed (its layout is KMI-frozen), so every aggregate access\n"
    "\t\t * goes through the slot's item.\n"
    "\t\t */\n"
    "\t\tint item = i < NR_MEMCG_NODE_STAT_ITEMS ?\n"
    "\t\t\t   memcg_node_stat_items[i] :\n"
    "\t\t\t   memcg_stat_items[i - NR_MEMCG_NODE_STAT_ITEMS];\n"
    "\n"
    "\t\t/*\n"
    "\t\t * Collect the aggregated propagation counts of groups\n"
    "\t\t * below us. We're in a per-cpu loop here and this is\n"
    "\t\t * a global counter, so the first cycle will get them.\n"
    "\t\t */\n"
    "\t\tdelta = memcg->vmstats.state_pending[item];\n"
    "\t\tif (delta)\n"
    "\t\t\tmemcg->vmstats.state_pending[item] = 0;\n"
    "\n"
    "\t\t/* Add CPU changes on this level since the last flush */\n"
    "\t\tv = READ_ONCE(statc->state[i]);\n"
    "\t\tif (v != statc->state_prev[i]) {\n"
    "\t\t\tdelta += v - statc->state_prev[i];\n"
    "\t\t\tstatc->state_prev[i] = v;\n"
    "\t\t}\n"
    "\n"
    "\t\tif (!delta)\n"
    "\t\t\tcontinue;\n"
    "\n"
    "\t\t/* Aggregate counts on this level and propagate upwards */\n"
    "\t\tmemcg->vmstats.state[item] += delta;\n"
    "\t\tif (parent)\n"
    "\t\t\tparent->vmstats.state_pending[item] += delta;\n"
    "\t}\n"
)

_FLUSH_EVENTS_OLD = (
    "\tfor (i = 0; i < NR_VM_EVENT_ITEMS; i++) {\n"
    "\t\tdelta = memcg->vmstats.events_pending[i];\n"
    "\t\tif (delta)\n"
    "\t\t\tmemcg->vmstats.events_pending[i] = 0;\n"
    "\n"
    "\t\tv = READ_ONCE(statc->events[i]);\n"
    "\t\tif (v != statc->events_prev[i]) {\n"
    "\t\t\tdelta += v - statc->events_prev[i];\n"
    "\t\t\tstatc->events_prev[i] = v;\n"
    "\t\t}\n"
    "\n"
    "\t\tif (!delta)\n"
    "\t\t\tcontinue;\n"
    "\n"
    "\t\tmemcg->vmstats.events[i] += delta;\n"
    "\t\tif (parent)\n"
    "\t\t\tparent->vmstats.events_pending[i] += delta;\n"
    "\t}\n"
)

_FLUSH_EVENTS_NEW = (
    "\tfor (i = 0; i < NR_MEMCG_VM_EVENTS; i++) {\n"
    "\t\tint event = memcg_vm_event_items[i];\n"
    "\n"
    "\t\tdelta = memcg->vmstats.events_pending[event];\n"
    "\t\tif (delta)\n"
    "\t\t\tmemcg->vmstats.events_pending[event] = 0;\n"
    "\n"
    "\t\tv = READ_ONCE(statc->events[i]);\n"
    "\t\tif (v != statc->events_prev[i]) {\n"
    "\t\t\tdelta += v - statc->events_prev[i];\n"
    "\t\t\tstatc->events_prev[i] = v;\n"
    "\t\t}\n"
    "\n"
    "\t\tif (!delta)\n"
    "\t\t\tcontinue;\n"
    "\n"
    "\t\tmemcg->vmstats.events[event] += delta;\n"
    "\t\tif (parent)\n"
    "\t\t\tparent->vmstats.events_pending[event] += delta;\n"
    "\t}\n"
)

_FLUSH_LRUVEC_OLD = (
    "\tfor_each_node_state(nid, N_MEMORY) {\n"
    "\t\tstruct mem_cgroup_per_node *pn = memcg->nodeinfo[nid];\n"
    "\t\tstruct mem_cgroup_per_node *ppn = NULL;\n"
    "\t\tstruct lruvec_stats_percpu *lstatc;\n"
    "\n"
    "\t\tif (parent)\n"
    "\t\t\tppn = parent->nodeinfo[nid];\n"
    "\n"
    "\t\tlstatc = per_cpu_ptr(pn->lruvec_stats_percpu, cpu);\n"
    "\n"
    "\t\tfor (i = 0; i < NR_VM_NODE_STAT_ITEMS; i++) {\n"
    "\t\t\tdelta = pn->lruvec_stats.state_pending[i];\n"
    "\t\t\tif (delta)\n"
    "\t\t\t\tpn->lruvec_stats.state_pending[i] = 0;\n"
    "\n"
    "\t\t\tv = READ_ONCE(lstatc->state[i]);\n"
    "\t\t\tif (v != lstatc->state_prev[i]) {\n"
    "\t\t\t\tdelta += v - lstatc->state_prev[i];\n"
    "\t\t\t\tlstatc->state_prev[i] = v;\n"
    "\t\t\t}\n"
    "\n"
    "\t\t\tif (!delta)\n"
    "\t\t\t\tcontinue;\n"
    "\n"
    "\t\t\tpn->lruvec_stats.state[i] += delta;\n"
    "\t\t\tif (ppn)\n"
    "\t\t\t\tppn->lruvec_stats.state_pending[i] += delta;\n"
    "\t\t}\n"
    "\t}\n"
)

_FLUSH_LRUVEC_NEW = (
    "\tfor_each_node_state(nid, N_MEMORY) {\n"
    "\t\tstruct mem_cgroup_per_node *pn = memcg->nodeinfo[nid];\n"
    "\t\tstruct mem_cgroup_per_node *ppn = NULL;\n"
    "\t\tstruct abk_lruvec_stats_percpu *lstatc;\n"
    "\n"
    "\t\tif (parent)\n"
    "\t\t\tppn = parent->nodeinfo[nid];\n"
    "\n"
    "\t\tlstatc = per_cpu_ptr(abk_lruvec_stats_percpu_of(pn), cpu);\n"
    "\n"
    "\t\tfor (i = 0; i < NR_MEMCG_NODE_STAT_ITEMS; i++) {\n"
    "\t\t\tint item = memcg_node_stat_items[i];\n"
    "\n"
    "\t\t\tdelta = pn->lruvec_stats.state_pending[item];\n"
    "\t\t\tif (delta)\n"
    "\t\t\t\tpn->lruvec_stats.state_pending[item] = 0;\n"
    "\n"
    "\t\t\tv = READ_ONCE(lstatc->state[i]);\n"
    "\t\t\tif (v != lstatc->state_prev[i]) {\n"
    "\t\t\t\tdelta += v - lstatc->state_prev[i];\n"
    "\t\t\t\tlstatc->state_prev[i] = v;\n"
    "\t\t\t}\n"
    "\n"
    "\t\t\tif (!delta)\n"
    "\t\t\t\tcontinue;\n"
    "\n"
    "\t\t\tpn->lruvec_stats.state[item] += delta;\n"
    "\t\t\tif (ppn)\n"
    "\t\t\t\tppn->lruvec_stats.state_pending[item] += delta;\n"
    "\t\t}\n"
    "\t}\n"
)


_HEADER_LOCAL_OLD = (
    "static inline unsigned long lruvec_page_state_local(struct lruvec *lruvec,\n"
    "\t\t\t\t\t\t    enum node_stat_item idx)\n"
    "{\n"
    "\tstruct mem_cgroup_per_node *pn;\n"
    "\tlong x = 0;\n"
    "\tint cpu;\n"
    "\n"
    "\tif (mem_cgroup_disabled())\n"
    "\t\treturn node_page_state(lruvec_pgdat(lruvec), idx);\n"
    "\n"
    "\tpn = container_of(lruvec, struct mem_cgroup_per_node, lruvec);\n"
    "\tfor_each_possible_cpu(cpu)\n"
    "\t\tx += per_cpu(pn->lruvec_stats_percpu->state[idx], cpu);\n"
    "#ifdef CONFIG_SMP\n"
    "\tif (x < 0)\n"
    "\t\tx = 0;\n"
    "#endif\n"
    "\treturn x;\n"
    "}\n"
)

_HEADER_LOCAL_NEW = (
    "/*\n"
    " * ABK stable_515_backport: memcg_stats_percpu_slim.  Out of line in\n"
    " * mm/memcontrol.c: the per-cpu lruvec stats object is compact and\n"
    " * slot-indexed there.  No struct member moves; the KMI types keep their\n"
    " * layouts.\n"
    " */\n"
    "unsigned long lruvec_page_state_local(struct lruvec *lruvec,\n"
    "\t\t\t\t      enum node_stat_item idx);\n"
)

MARKER = "ABK stable_515_backport: memcg_stats_percpu_slim"
ABK_VMSTATS_STRUCT = "struct abk_vmstats_percpu"
ABK_LRUVEC_STRUCT = "struct abk_lruvec_stats_percpu"
VMSTATS_ALLOC = "__alloc_percpu_gfp(sizeof(struct abk_vmstats_percpu)"
LRUVEC_ALLOC = "__alloc_percpu_gfp(sizeof(struct abk_lruvec_stats_percpu)"
STATE_TABLE = "memcg_node_stat_items[]"
EVENTS_TABLE = "memcg_vm_event_items[]"
INIT_PAIR = "init_memcg_stats();\n\t\tinit_memcg_events();"


def build_steps():
    return [
        (MEMCONTROL_C, _SLIM_BLOCK_OLD, _SLIM_BLOCK_NEW, T),
        (MEMCONTROL_C, _MOD_STATE_OLD, _MOD_STATE_NEW, T),
        (MEMCONTROL_C, _PAGE_STATE_LOCAL_OLD, _PAGE_STATE_LOCAL_NEW, T),
        (MEMCONTROL_C, _MOD_LRUVEC_OLD, _MOD_LRUVEC_NEW, T),
        (MEMCONTROL_C, _COUNT_EVENTS_OLD, _COUNT_EVENTS_NEW, T),
        (MEMCONTROL_C, _EVENTS_LOCAL_OLD, _EVENTS_LOCAL_NEW, T),
        (MEMCONTROL_C, _NR_PAGE_EVENTS_OLD, _NR_PAGE_EVENTS_NEW, T),
        (MEMCONTROL_C, _UNCHARGE_OLD, _UNCHARGE_NEW, T),
        (MEMCONTROL_C, _RATELIMIT_READ_OLD, _RATELIMIT_READ_NEW, T),
        (MEMCONTROL_C, _RATELIMIT_WRITE_OLD, _RATELIMIT_WRITE_NEW, T),
        (MEMCONTROL_C, _PN_ALLOC_OLD, _PN_ALLOC_NEW, T),
        (MEMCONTROL_C, _MEMCG_ALLOC_OLD, _MEMCG_ALLOC_NEW, T),
        (MEMCONTROL_C, _CSS_ALLOC_OLD, _CSS_ALLOC_NEW, T),
        (MEMCONTROL_C, _FLUSH_DECL_OLD, _FLUSH_DECL_NEW, T),
        (MEMCONTROL_C, _FLUSH_STATC_OLD, _FLUSH_STATC_NEW, T),
        (MEMCONTROL_C, _FLUSH_STATE_OLD, _FLUSH_STATE_NEW, T),
        (MEMCONTROL_C, _FLUSH_EVENTS_OLD, _FLUSH_EVENTS_NEW, T),
        (MEMCONTROL_C, _FLUSH_LRUVEC_OLD, _FLUSH_LRUVEC_NEW, T),
        (MEMCONTROL_H, _HEADER_LOCAL_OLD, _HEADER_LOCAL_NEW, T),
    ]


def _memcg_stats_percpu_slim_apply(ctx):
    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    return [
        PatchGroup(
            "memcg_stats_percpu_slim",
            "shrink the memcg per-cpu stats objects: index state/event arrays "
            "through item -> slot tables (mainline 70a64b7919cb + ff48c71c26aa, "
            "v6.10) and allocate them at the compacted size, keeping the "
            "KMI-visible header layouts byte-identical (the embedded aggregates "
            "stay full-width; only the per-cpu objects shrink)",
            [
                "70a64b7919cb (mainline v6.10, 'memcg: dynamically allocate "
                "lruvec_stats')",
                "ff48c71c26aa (mainline v6.10, 'memcg: reduce memory for the "
                "lruvec and memcg stats')",
                "+ KMI reshape: header byte-identical, private compact percpu "
                "structs behind the unchanged pointer fields (android13-5.15 "
                "abi_gki_aarch64.xml tracks mem_cgroup / mem_cgroup_per_node / "
                "memcg_vmstats / lruvec_stats; the item tables are re-derived "
                "for 5.15 from memory_stats[] / memcg1_stats[] and the full "
                "count_memcg_events* writer set)",
            ],
            [MEMCONTROL_C, MEMCONTROL_H],
            _memcg_stats_percpu_slim_apply,
        ),
    ]
