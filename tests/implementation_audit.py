#!/usr/bin/env python3

from __future__ import annotations

import difflib
import re
import shutil
import sys
import tempfile
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_DIR / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import abk_stable_core  # noqa: E402
import abk_stable_perf  # noqa: E402
import abk_stable_display  # noqa: E402
from abk_backport_engine import GraftContext  # noqa: E402
import sublevel_matrix  # noqa: E402

MARKER = "ABK stable_515_backport:"

CONFIG_GATED_REFERENCES = {
    "drivers/block/zram/zram_drv.c": [
        ("CONFIG_ZRAM_WRITEBACK",
         ("zram->bdev", "zram->backing_dev", "zram->wb_limit_lock",
          "zram->wb_limit_enable", "zram->bd_wb_limit", "zram->bitmap",
          "zram->nr_pages", "zram->wb_compressed", "zram->stats.bd_",
          "abk_zram_bvec_read(")),
    ],
    "mm/filemap.c": [
        ("CONFIG_CPUSETS",
         ("cpuset_cgrp_id",)),
    ],
    "mm/vmscan.c": [
        ("CONFIG_SWAP",
         ("page_swap_info(",)),
    ],
}


def _ifdef_regions(lines):
    stack = []
    regions = []
    for line in lines:
        stripped = line.strip()
        if re.match(r"#\s*(if|ifdef|ifndef)\b", stripped):
            stack.append(stripped)
        regions.append(list(stack))
        if re.match(r"#\s*endif\b", stripped) and stack:
            stack.pop()
    return regions


def config_gate_scope_problems(source, patched_root):
    problems = []
    for rel, gates in CONFIG_GATED_REFERENCES.items():
        pristine = Path(source) / rel
        patched = Path(patched_root) / rel
        if not pristine.is_file() or not patched.is_file():
            continue
        before = pristine.read_text().split("\n")
        after = patched.read_text().split("\n")
        regions = _ifdef_regions(after)
        matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
        for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
            if tag not in ("insert", "replace"):
                continue
            for j in range(j1, j2):
                line = after[j]
                if not line.strip():
                    continue
                for gate, needles in gates:
                    if not any(needle in line for needle in needles):
                        continue
                    if not any(gate in cond for cond in regions[j]):
                        problems.append(
                            f"{rel}:{j + 1}: uses a {gate}-gated symbol "
                            f"outside the gate: {line.strip()[:90]}")
    return problems

REQUIRED_CONTENT = {
    "core:zram_recompression": [
        "zram_recompress", "recompress_store", "mark_idle",
        "num_active_comps", "zs_lookup_class_index",
        "for (prio = 0; prio < ZRAM_MAX_COMPS; prio++)",
        "zram_set_priority(zram, index, 0)",
    ],
    "core:memcg_memory_reclaim": ["memory.reclaim", "MEMCG_RECLAIM_MAY_SWAP"],
    "core:zsmalloc_chain_size": [
        "calculate_zspage_chain_size", "ZSMALLOC_CHAIN_SIZE", "is_power_of_2",
        "#define ISOLATED_BITS\t5",
    ],
    "core:madvise_collapse": [
        "madvise_collapse", "MADV_COLLAPSE",
        "khugepaged_scan_pmd(mm, vma, addr, &hpage, &result)",
        "khugepaged_scan_file(mm, file, pgoff, &hpage, &result)",
        "struct file *file, pgoff_t start, struct page **hpage,\n\t\tint *res)",
        "pgoff_t start, struct page **hpage, int *res)",
        "result = hugepage_vma_revalidate(mm, addr, &vma);\n\t\t\tif (result) {",
    ],
    "core:pagealloc_fallback_reuse": [
        "enum rmqueue_mode",
        "RMQUEUE_CLAIM",
        "RMQUEUE_STEAL",
        "return -2",
        "find_suitable_fallback(area, order, migratetype,\n"
        "\t\t\t\t\t\ttrue) >= 0",
        "__rmqueue(zone, order, migratetype, alloc_flags, &rmqm)",
    ],
    "core:rcu_nocb_cpu_default_all": [
        "config RCU_NOCB_CPU_DEFAULT_ALL",
        "bool \"Offload RCU callback processing from all CPUs by default\"",
        "offload_all",
        "cpumask_setall(rcu_nocb_mask)",
    ],
    "core:dynamic_readahead_lowmem": [
        "config ABK_DYNAMIC_READAHEAD",
        "register_trace_android_vh_ra_tuning_max_page(",
        "register_trace_android_vh_tune_mmap_readaround(",
        "core_initcall(abk_dra_init);",
        "abk_dra_is_background_task",
        "ABK stable_515_backport: dynamic readahead (Batch 9-1).",
    ],
    "core:pid_alloc_hotpath_phase2": [
        "retry_preload:",
        "retried_preload = true;",
        "retried_preload = false;",
    ],
    "core:fd_alloc_hotpath": [
        "abk_expand_files_needed",
    ],
    "core:close_range_hotpath": [
        "ABK stable_515_backport: close_range_hotpath.",
    ],
    "core:slab_alloc_free_hotpath": [
        "abk_slab_next_object",
        "c->freelist = next_object;",
        "struct page *page = virt_to_head_page(x);",
        "slab_free(s, page, x, NULL, 1, _RET_IP_);",
    ],
    "core:hugepage_fault_alloc_fastpath": [
        "abk_thp_fault_prepare",
        "abk_thp_fault_alloc_page",
        "abk_thp_fault_charge_page",
        "abk_map_anon_page_pmd",
        "abk_do_huge_pmd_anonymous_zero_page",
        "abk_create_anonymous_huge_pmd",
        "khugepaged_enter(vma, vma->vm_flags)",
    ],
    "perf:sched_eevdf_core_fields": [
        "ANDROID_KABI_USE(1, u64 deadline);",
        "ANDROID_KABI_USE(2, u64 min_vruntime);",
        "ANDROID_KABI_USE(3, s64 vlag);",
        "ANDROID_KABI_USE(4, u64 slice);",
    ],
    "perf:sched_eevdf_pick_logic": [
        "abk_pick_eevdf",
        "abk_eevdf_eligible",
        "abk_eevdf_vslice",
        "abk_eevdf_update_lag",
        "abk_eevdf_take_rel_deadline",
        "abk_eevdf_store_rel_deadline",
        "abk_eevdf_scale_rel_deadline",
        "ABK_EEVDF_REL_DEADLINE_BIT",
        "abk_entity_key",
        "abk_avg_vruntime_add",
        "abk_avg_vruntime_sub",
        "abk_avg_vruntime_update",
        "sched_feat(RUN_TO_PARITY)",
        "abk_eevdf_preempt_short",
        "abk_eevdf_lag_limit",
        "abk_avg_vruntime_add(cfs_rq, se);",
        "abk_eevdf_refresh_deadline(cfs_rq, curr);",
        "return abk_pick_eevdf(cfs_rq, curr);",
        "abk_eevdf_place_entity(cfs_rq, se, initial);",
        "cfs_rq->abk_pick_deadline = se->deadline;",
        "EEVDF runtime-state graft (Batch 28 shape)",
        "!abk_eevdf_eligible(curr, avruntime)",
        "best = curr && curr->on_rq ? curr : __pick_first_entity(cfs_rq);",
        "if (!se->slice)",
        "if (sched_feat(EEVDF))",
        "if (!sched_feat(EEVDF) && wakeup_preempt_entity(se, pse) == 1)",
    ],
    "perf:sched_eevdf_modern_fields": [
        "s64\t\t\tavg_vruntime;",
        "u64\t\t\tavg_load;",
        "u64\t\t\tabk_pick_deadline;",
        "p->se.slice = min_t(u64, max_t(u64, attr->sched_runtime,",
        "p->se.slice = 0;",
        "SCHED_FEAT(EEVDF, true)",
        "SCHED_FEAT(RUN_TO_PARITY, true)",
        "SCHED_FEAT(PREEMPT_SHORT, true)",
    ],
    "perf:nohz_field_refinement": [
        "enum nohz_cpu_state",
        "abk_tick_nohz_state_flags",
        "NOHZ_CPU_STATE_TICK_STOPPED",
    ],
    "perf:avg_idle_preemption_mode": [
        "ABK stable_515_backport: avg_idle preemption mode simplification.",
        "avg_idle = this_rq->avg_idle / 2;",
        "if (!rq->idle_stamp)",
    ],
    "perf:blk_mq_async_depth": [
        "static void blk_mq_limit_depth(unsigned int opf, struct blk_mq_alloc_data *data)",
        "e->type->ops.limit_depth(opf, data);",
        "limit_depth = blk_mq_limit_depth;",
        "kqd->async_depth = ((q->async_depth << shift) + q->nr_requests - 1) /",
        "depth = ((bfqd->queue->async_depth << bt->sb.shift) +",
    ],
    "perf:psi_trigger_kernfs_polling": ["psi_trigger_ext", "pending_event"],
    "perf:psi_irq_tracking": ["PSI_IRQ"],
    "perf:psi_cgroup_pressure_switch": [
        "CGRP_PSI_DISABLED",
        "static bool psi_group_enabled(struct psi_group *group)",
        "&container_of(group, struct cgroup, psi)->flags",
        "bool psi_cgroup_accounting_enabled(struct cgroup *cgrp)",
        "void psi_cgroup_accounting_set(struct cgroup *cgrp, bool enable)",
        "void psi_cgroup_restart(struct psi_group *group)",
        "static int cgroup_pressure_show(struct seq_file *seq, void *v)",
        "static ssize_t cgroup_pressure_write(struct kernfs_open_file *of,",
        '\t\t.name = "cgroup.pressure",',
        "\t\t.seq_show = cgroup_pressure_show,",
        "static ssize_t pressure_write(struct kernfs_open_file *of, char *buf,",
        "\treturn pressure_write(of, buf, nbytes, PSI_IO);",
        "\treturn pressure_write(of, buf, nbytes, PSI_MEM);",
        "\treturn pressure_write(of, buf, nbytes, PSI_CPU);",
        "  cgroup.pressure\n",
    ],
    "perf:psi_oncpu_state_mask": [
        "NR_PSI_TASK_COUNTS = 4,",
        "#define TSK_ONCPU\t(1 << NR_PSI_TASK_COUNTS)",
        "#define PSI_ONCPU\t(1 << NR_PSI_STATES)",
        "static bool test_state(unsigned int *tasks, enum psi_states state, bool oncpu)",
        "return unlikely(tasks[NR_RUNNING] > oncpu);",
        "return unlikely(tasks[NR_RUNNING] && !oncpu);",
        "if (unlikely(clear & TSK_ONCPU)) {",
        "state_mask = PSI_ONCPU;",
        "state_mask = groupc->state_mask & PSI_ONCPU;",
        "if (test_state(groupc->tasks, s, state_mask & PSI_ONCPU))",
        "if (unlikely((state_mask & PSI_ONCPU) && cpu_curr(cpu)->in_memstall))",
        "if (per_cpu_ptr(group->pcpu, cpu)->state_mask &",
        "if ((prev->psi_flags ^ next->psi_flags) & ~TSK_ONCPU) {",
        "tasks=[%u %u %u %u] clear=%x",
    ],
    "perf:sched_lazy_preemption_hooks": ["resched_curr_lazy"],
    "core:zram_async_recompress": [
        "Batch 10-1 async recompress engine (plan A)",
        "abk_zram_recomp_enqueue",
        "kthread_create_worker(0, \"Sail_recompd\")",
        "recompress_async",
        "dev_attr_recompress_async.attr",
        "abk_zram_recomp_drain(zram);",
    ],
    "perf:schedutil_smart_policy": [
        "Batch 10-4 sched smart-freq policy (PELT)",
        "abk_sf_enable",
        "register_trace_android_vh_scheduler_tick(abk_sf_tick, NULL)",
        "register_trace_android_vh_cpufreq_resolve_freq(abk_sf_resolve_freq,",
        "late_initcall(abk_sf_init)",
        "cpu_util_cfs(rq)",
        "arch_scale_cpu_capacity(rq->cpu)",
        "static bool abk_sf_enable = false;",
        "#include <linux/string.h>",
        "static bool abk_sf_dvfs_owned(struct cpufreq_policy *policy)",
        'strcmp(policy->governor->name, "schedutil") != 0',
        "if (abk_sf_dvfs_owned(policy) || policy->min == policy->max)",
        "if (floor <= policy->min || floor >= policy->max)",
        "unsigned long boost_release;",
        "static bool abk_sf_cpu_boosting(int cpu)",
        "module_param_cb(abk_sf_boosting, &abk_sf_boosting_ops, NULL, 0444);",
    ],
    "perf:schedutil_smart_cap": [
        "Batch 42 schedutil smart_freq cap",
        "static bool abk_sc_enable = false;",
        "static uint abk_sc_cap_pct = 100;",
        "static uint abk_sc_hold_ms = 300;",
        "static uint abk_sc_entry_pct = 90;",
        "static uint abk_sc_release_pct = 70;",
        "static uint abk_sc_release_ms = 250;",
        "return mult_frac(policy->cpuinfo.max_freq, pct, 100);",
        "*target_freq = cap;",
        "static bool abk_sc_dvfs_owned(struct cpufreq_policy *policy)",
        'strcmp(policy->governor->name, "schedutil") != 0',
        "if (abk_sc_dvfs_owned(policy) || policy->min == policy->max)",
        "if (c <= policy->min || c >= policy->max)",
        "ABK_SC_RELEASE IS NOT A UTIL WINDOW",
        "unsigned long high_start;",
        "p->high_start = jiffies;",
        "p->high_start = 0;",
        "p->released = false;",
        "struct abk_sc_policy {",
        "raw_spinlock_t lock;",
        "raw_spin_lock_irqsave(&p->lock, flags)",
        "raw_spin_lock_init(&abk_sc_policies[cpu].lock);",
        "late_initcall_sync(abk_sc_reassert_init);",
        "if (p->capped && *target_freq > cap)",
        "module_param_cb(abk_sc_boosting, &abk_sc_boosting_ops, NULL, 0444);",
        "module_param_cb(abk_sc_capped, &abk_sc_capped_ops, NULL, 0444);",
    ],
    "core:zram_secondary_comp": [
        "Batch 10-4 secondary zram compressor",
        "abk_zram_recomp_algo",
        "module_param_string(abk_recomp_algo",
        "comp_algorithm_set(zram, ZRAM_SECONDARY_COMP, abk_alg);",
        'static char abk_zram_recomp_algo[CRYPTO_MAX_ALG_NAME] = "zstd";',
        "sizeof(abk_zram_recomp_algo), 0444);",
    ],
    "core:zram_algo_lock": [
        "Batch 11 - the zram algorithm lock",
        'static char abk_zram_comp_algo[CRYPTO_MAX_ALG_NAME] = "lz4kd";',
        "module_param_string(abk_comp_algo, abk_zram_comp_algo",
        "module_param_named(abk_lock_algo, abk_zram_lock_algo, bool, 0444);",
        "static bool abk_zram_lock_algo = true;",
        "comp_algorithm_set(zram, ZRAM_PRIMARY_COMP,",
        "dev_attr_comp_algorithm.store = abk_zram_locked_algo_store;",
        "dev_attr_recomp_algorithm.store = abk_zram_locked_algo_store;",
        "late_initcall(abk_zram_algo_lock_init)",
        "return len;",
    ],
    "core:memcg_v1_reclaim": [
        "Batch 10-4 cgroup-v1 proactive reclaim",
        "static ssize_t memory_reclaim(struct kernfs_open_file *of, char *buf,",
        'static struct cftype mem_cgroup_legacy_files[] = {',
        '.name = "reclaim",',
        ".write = memory_reclaim,",
        "cfr_reclaim_attempts %ld",
    ],
    "core:cached_freeze_reclaim": [
        "Batch 10-3",
        "abk_cfr_reclaim_attempts",
        "abk_cfr_reclaim_requested",
        "cfr_reclaim_reclaimed",
        "reclaim_options & MEMCG_RECLAIM_PROACTIVE",
    ],
    "core:customize_alloc_gfp_vh": [
        "DECLARE_HOOK(android_vh_customize_alloc_gfp,",
        "trace_android_vh_customize_alloc_gfp(&alloc_gfp, order);",
        "EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_customize_alloc_gfp);",
    ],
    "core:gfp_pressure_fastfail": [
        "Batch 13 high-order slowpath fast-fail",
        "register_trace_android_vh_customize_alloc_gfp(",
        "*gfp |= __GFP_NORETRY | __GFP_NOWARN;",
        "late_initcall(abk_gfp_fastfail_init);",
        "static unsigned int abk_gfp_fastfail_order = 9;",
    ],
    "core:zram_wb_teardown": [
        "if (!zram->table)\n\t\treturn;",
        "zram->table = NULL;",
        "\tzram_reset_device(zram);\n\t/*\n\t * ABK stable_515_backport: 74363ec674cb.",
        "\treset_bdev(zram);\n\n\tpr_info(\"Removed device: %s\\n\"",
    ],
    "core:zram_writeback_bounds": [
        "ABK stable_515_backport: 894913e2d35c",
        "\tunsigned long nr_pages;\n\tunsigned long index = 0;",
        "\t * freed table and the loop would index past the new one.\n"
        "\t */\n"
        "\tunsigned long nr_pages;",
        "ABK stable_515_backport: 894913e2d35c -- bound and range check",
        "\tnr_pages = zram->disksize >> PAGE_SHIFT;\n"
        "\tif (index >= nr_pages) {\n"
        "\t\tret = -EINVAL;\n"
        "\t\tgoto release_init_lock;",
        "\t\tcond_resched();",
    ],
    "core:zram_wb_limit_align": [
        "val = rounddown(val, PAGE_SIZE / 4096);",
    ],
    "core:zram_writeback_batching": [
        "struct zram_wb_ctl", "struct zram_wb_req",
        "zram_writeback_endio", "zram_submit_wb_request",
        "zram_complete_done_reqs", "zram_select_idle_req",
        "atomic_read(&wb_ctl->num_inflight)", "wait_event(wb_ctl->done_wait",
        "kfree_rcu(wb_ctl, rcu)", "rcu_read_lock()",
        "free_block_bdev(zram, blk_idx)", "req->blk_idx = 0;",
        "zram_set_flag(zram, index, ZRAM_UNDER_WB)",
        "zram_clear_flag(zram, index, ZRAM_UNDER_WB)",
        "zram_set_element(zram, index, req->blk_idx)",
        "zs_map_object(zram->mem_pool, handle, ZS_MM_RO)",
        "zs_unmap_object(zram->mem_pool, handle)",
        "memzero_page(page, size, PAGE_SIZE - size)",
    ],
    "core:zram_wb_batch_size": [
        "wb_batch_size", "zram->wb_batch_size = 32;",
        "writeback_batch_size_store", "writeback_batch_size_show",
        "ZRAM_WB_BATCH_SIZE_MAX",
        "dev_attr_writeback_batch_size.attr",
    ],
    "core:zram_compressed_writeback": [
        "abk_zram_bvec_read(", "abk_zram_decompress_bdev_page",
        "abk_zram_deferred_decompress", "system_highpri_wq",
        "bio_inc_remaining(parent)", "compressed_writeback_store",
        "zram->wb_compressed = val;",
        "dev_attr_compressed_writeback.attr",
        "zcomp_stream_put(zram->comps[prio])",
        "zcomp_decompress(zstrm, src, size, zstrm->buffer)",
        "zero_user(page, 0, PAGE_SIZE)",
    ],
    "perf:sched_steal_time_excess_drop": [
        "steal = prev_steal = paravirt_steal_clock(cpu_of(rq));",
        "rq->prev_steal_time_rq = prev_steal;",
    ],
    "perf:randomize_kstack_pertask": [
        "u32 offset = current->kstack_offset;",
        "current->kstack_offset = offset;",
        "random_kstack_task_init",
    ],
    "perf:blk_mq_suspend_wakeup_abort": [
        "pm_wakeup_pending()",
        "clear_bit(BLK_MQ_S_INACTIVE, &hctx->state);",
        "ret = -EBUSY;",
        "#include <linux/suspend.h>",
    ],
    "perf:blk_mq_quiesced_elevator_switch": [
        "static int elevator_switch_mq(struct request_queue *q,",
        "int elevator_switch(struct request_queue *q, struct elevator_type *new_e);",
        "elevator_switch(q, NULL);",
        "elevator_switch(q, t);",
    ],
    "core:zram_recompress_max_pages": [
        'if (!strcmp(param, "max_pages")) {',
        "ret = kstrtoull(val, 10, &num_recomp_pages);",
        "u64 num_recomp_pages = ULLONG_MAX;",
        "if (!num_recomp_pages)",
        "num_recomp_pages--;",
        "ABK stable_515_backport: 34efe1c3b688",
        "if (!mode)",
    ],
    "core:zram_recomp_best_prio_skip": [
        "static bool abk_zram_recompress_pointless(struct zram *zram,",
        "u32 abk_cur = zram_get_priority(zram, index);",
        "if (prio > abk_cur && zram->comps[prio])",
        "ABK stable_515_backport: Batch 47",
        "#ifdef CONFIG_ZRAM_MULTI_COMP\n/*\n * sailboat_zram_recomp_skip:",
    ],
    "core:arm64_pte_mkwrite_clean": [
        "if (pte_sw_dirty(pte))\n"
        "\t\tpte = clear_pte_bit(pte, __pgprot(PTE_RDONLY));",
    ],    "core:zram_wb_slot_preserve": [
        "ABK stable_515_backport: b0377ee80429",
        "atomic64_sub(zram_get_obj_size(zram, index),",
        "zs_free(zram->mem_pool, zram_get_handle(zram, index));",
        "if (!zram_test_flag(zram, index, ZRAM_WB))",
    ],
    "core:zsmalloc_free_zspage_out_of_lock": [
        "static inline void __free_zspage_lockless(struct zs_pool *pool,",
        "zspage_to_free",
        "ABK stable_515_backport: 7ef28e8b8142",
        "free_zspage(pool, class, src_zspage);",
    ],
    "core:proactive_reclaim_batch_fidelity": [
        "min(nr_to_reclaim - nr_reclaimed, SWAP_CLUSTER_MAX),",
    ],
    "core:proactive_reclaim_decaying_batches": [
        "/* Will converge on zero, but reclaim enforces a minimum */",
        "unsigned long batch_size = (nr_to_reclaim - nr_reclaimed) / 4;",
        "batch_size, GFP_KERNEL, reclaim_options);",
    ],
    "core:reclaim_swappiness_defines": [
        "#define MIN_SWAPPINESS 0",
        "#define MAX_SWAPPINESS 200",
        "\tfp = (MAX_SWAPPINESS - swappiness) * (total_cost + 1);",
        "\tif (val > MAX_SWAPPINESS)",
    ],
    "core:proactive_reclaim_swappiness_arg": [
        "int *swappiness",
        "int *proactive_swappiness;",
        "static int sc_swappiness(struct scan_control *sc, struct mem_cgroup *memcg)",
        "int swappiness = sc_swappiness(sc, memcg);",
        "sc_swappiness(sc, memcg);",
        ".proactive_swappiness = swappiness,",
        "#include <linux/parser.h>",
        "static const match_table_t tokens = {",
        '{ MEMORY_RECLAIM_SWAPPINESS, "swappiness=%d"},',
        "nr_to_reclaim = memparse(buf, &buf) / PAGE_SIZE;",
        "swappiness == -1 ? NULL : &swappiness",
        "MEMCG_RECLAIM_MAY_SWAP,\n\t\t\t\t\t\t\t     NULL);",
        "gfp_mask, reclaim_options,\n\t\t\t\t\t\t    NULL);",
        "memsw ? 0 : MEMCG_RECLAIM_MAY_SWAP,\n\t\t\t\t\tNULL)) {",
        "MEMCG_RECLAIM_MAY_SWAP,\n\t\t\t\t\t\t\tNULL);",
        "GFP_KERNEL, MEMCG_RECLAIM_MAY_SWAP,\n\t\t\t\t\tNULL);",
        "GFP_KERNEL, MEMCG_RECLAIM_MAY_SWAP, NULL))",
        "  swappiness            Swappiness value to reclaim with",
    ],
    "core:proactive_reclaim_suspend_abort": [
        "if (unlikely(sc->proactive && signal_pending(current)))",
        "return -ERESTARTSYS;",
    ],
    "core:lru_add_drain_dead_folios": [
        "if (page_ref_freeze(page, 1)) {",
        "__ClearPageActive(page);",
        "__ClearPageUnevictable(page);",
        "list_add(&page->lru, &pages_to_free);",
        "mem_cgroup_uncharge_list(&pages_to_free);",
        "free_unref_page_list(&pages_to_free);",
        "/* A drained add batch may have freed this slot already. */",
    ],
    "core:truncate_shadow_batch": [
        "clear_shadow_entries",
        "xa_has_values",
        "ABK stable_515_backport: 61c663e020d2",
    ],
    "core:truncate_shadow_batch_sweep": [
        "static void clear_shadow_entries(struct address_space *mapping,\n"
        "\t\t\t\t unsigned long start, unsigned long max)",
        "xas_for_each(&xas, page, max)",
        "clear_shadow_entries(mapping, indices[0], indices[nr-1]);",
        "int nr = pagevec_count(&pvec);",
        "ABK stable_515_backport: d3db2c042591",
    ],
    "core:arm64_lse_percpu_load_atomics": [
        '#op_lse "\\t%" #w "[val], %" #w "[tmp], %[ptr]\\n"',
        "PERCPU_OP(add, add, ldadd)",
        "PERCPU_OP(andnot, bic, ldclr)",
        "PERCPU_OP(or, orr, ldset)",
        "PERCPU_RET_OP(add, add, ldadd)\n",
        "e7d539ed-ced0-4b96-8ecd-048a5b803b85@paulmck-laptop",
    ],
"core:fuse_prefault_out_of_write_path": [
        "while not holding the page lock:",
        "\t\ttmp = copy_page_from_iter_atomic(page, offset, bytes, ii);",
    ],
    "core:memcg_stats_percpu_slim": [
        "ABK stable_515_backport: memcg_stats_percpu_slim",
        "struct abk_vmstats_percpu",
        "struct abk_lruvec_stats_percpu",
        "__alloc_percpu_gfp(sizeof(struct abk_vmstats_percpu)",
        "__alloc_percpu_gfp(sizeof(struct abk_lruvec_stats_percpu)",
        "static void init_memcg_stats(void)",
        "static void init_memcg_events(void)",
        "init_memcg_stats();\n\t\tinit_memcg_events();",
        "static const unsigned int memcg_node_stat_items[] = {",
        "static const unsigned int memcg_stat_items[] = {",
        "static const unsigned int memcg_vm_event_items[] = {",

        "NR_INACTIVE_ANON,\n\tNR_ACTIVE_ANON,\n\tNR_INACTIVE_FILE,\n\tNR_ACTIVE_FILE,\n\tNR_UNEVICTABLE,",
        "NR_SLAB_RECLAIMABLE_B,\n\tNR_SLAB_UNRECLAIMABLE_B,",
        "WORKINGSET_REFAULT_ANON,\n\tWORKINGSET_REFAULT_FILE,\n\tWORKINGSET_ACTIVATE_ANON,\n\tWORKINGSET_ACTIVATE_FILE,\n\tWORKINGSET_RESTORE_ANON,\n\tWORKINGSET_RESTORE_FILE,\n\tWORKINGSET_NODERECLAIM,",
        "NR_ANON_MAPPED,\n\tNR_FILE_MAPPED,\n\tNR_FILE_PAGES,\n\tNR_FILE_DIRTY,\n\tNR_WRITEBACK,\n\tNR_SHMEM,\n\tNR_SHMEM_THPS,\n\tNR_FILE_THPS,\n\tNR_ANON_THPS,\n\tNR_KERNEL_STACK_KB,\n\tNR_PAGETABLE,",
        "#ifdef CONFIG_SWAP\n\tNR_SWAPCACHE,\n#endif",
        "MEMCG_SWAP,\n\tMEMCG_SOCK,\n\tMEMCG_PERCPU_B,",
        "PGPGIN,\n\tPGPGOUT,\n\tPGFAULT,\n\tPGMAJFAULT,\n\tPGREFILL,\n\tPGSCAN_KSWAPD,\n\tPGSCAN_DIRECT,\n\tPGSTEAL_KSWAPD,\n\tPGSTEAL_DIRECT,\n\tPGACTIVATE,\n\tPGDEACTIVATE,\n\tPGLAZYFREE,\n\tPGLAZYFREED,",
        "#ifdef CONFIG_TRANSPARENT_HUGEPAGE\n\tTHP_FAULT_ALLOC,\n\tTHP_COLLAPSE_ALLOC,\n#endif",

        "struct lruvec_stats {\n\t/* Aggregated (CPU and subtree) state */\n\tlong state[NR_VM_NODE_STAT_ITEMS];",
        "struct memcg_vmstats {",
        "unsigned long lruvec_page_state_local(struct lruvec *lruvec,\n\t\t\t\t      enum node_stat_item idx);",
        "unsigned long lruvec_page_state_local(struct lruvec *lruvec,\n\t\t\t\t      enum node_stat_item idx)\n{",
    ],
    "core:mglru_clean_workingset": [
        "VM_BUG_ON_PAGE(!PageLocked(page), page);"
    ],
    "core:mglru_optimize_deactivation": [
        "static bool lru_gen_clear_refs(struct page *page)",
        "return gen == lru_gen_from_seq(READ_ONCE(lruvec->lrugen.min_seq[type]));",
        "if (lru_gen_enabled() && lru_gen_clear_refs(page))\n\t\treturn;",
        "if (lru_gen_enabled() ? lru_gen_clear_refs(page)\n"
        "\t\t\t\t\t      : !PageActive(page))",
        "bool active = PageActive(page) || lru_gen_enabled();",
        "if (lru_gen_enabled())\n\t\t\tlru_gen_clear_refs(page);\n"
        "\t\telse\n\t\t\tClearPageReferenced(page);",
    ],
    "core:mglru_rework_aging_feedback": [
        "unsigned long protected[NR_HIST_GENS][ANON_AND_FILE][MAX_NR_TIERS];",
        "\tint swappiness;\n\tbool full_scan;",
        "#define evictable_min_seq(min_seq, swappiness)",
        "#define for_each_evictable_type(type, swappiness)",
        "if (type ? swappiness > MAX_SWAPPINESS : !swappiness)",
        "if (walk->swappiness > MAX_SWAPPINESS)\n\t\treturn true;",
        "if (min_seq[LRU_GEN_ANON] > seq && min_seq[LRU_GEN_FILE] < seq)",
        "WRITE_ONCE(lrugen->protected[hist][type][tier],\n"
        "\t\t\t\t   lrugen->protected[hist][type][tier] + delta);",
        "return evictable_min_seq(min_seq, swappiness) + MIN_NR_GENS == max_seq;",
        "if (evictable_min_seq(lrugen->min_seq, swappiness) + MIN_NR_GENS > lrugen->max_seq)",
        "n[2] = READ_ONCE(lrugen->protected[hist][type][tier]);",
        "int swappiness = get_swappiness(lruvec, sc);",
    ],
    "core:mglru_rework_type_selection": [
        "pos->refaulted = pos->total = 0;",
        "for (i = tier % MAX_NR_TIERS; i <= min_t(typeof(tier), tier, MAX_NR_TIERS - 1); i++) {",
        "gain factor (2:3)",
        "read_ctrl_pos(lruvec, type, 0, 2, &sp);",
        "read_ctrl_pos(lruvec, LRU_GEN_ANON, MAX_NR_TIERS, swappiness, &sp);",
        "read_ctrl_pos(lruvec, LRU_GEN_FILE, MAX_NR_TIERS, MAX_SWAPPINESS - swappiness, &pv);",
        "if (swappiness <= MIN_SWAPPINESS + 1)\n\t\treturn LRU_GEN_FILE;",
        "int type = get_type_to_scan(lruvec, swappiness);",
        "\tint scanned;\n\t\tint tier = get_tier_idx(lruvec, type);",
    ],
    "core:mglru_rework_refault_detection": [
        "seq = READ_ONCE(lrugen->max_seq) & (EVICTION_MASK >> LRU_REFS_WIDTH);",
        "if (diff >= MAX_NR_GENS)\n\t\tgoto unlock;",
        "hist = lru_hist_from_seq(READ_ONCE(lrugen->min_seq[type]));",
    ],
    "core:mglru_wake_flushers": [
        "\tbool dirty, writeback;",
        "sc->nr.file_taken += delta;",
        "\tif (type == LRU_GEN_FILE)\n\t\tsc->nr.file_taken += isolated;",
        "sc->nr.unqueued_dirty += stat.nr_unqueued_dirty;",
        "if (PageLocked(page) || writeback ||\n"
        "\t    (type == LRU_GEN_FILE && dirty)) {",
        "if (sc->nr.unqueued_dirty && sc->nr.unqueued_dirty == sc->nr.file_taken)\n"
        "\t\twakeup_flusher_threads(WB_REASON_VMSCAN);",
    ],
    "core:memcg_dying_bailout": [
        "static inline bool memcg_is_dying(struct mem_cgroup *memcg)\n{\n"
        "\treturn memcg ? !!(memcg->css.flags & CSS_DYING) : false;\n}",
        "static inline bool memcg_is_dying(struct mem_cgroup *memcg) "
        "{ return false; }",
    ],
    "core:erofs_readahead_relaxed_gfp": [
        ["fs/erofs/compress.h", "\tgfp_t gfp;"],
        ["fs/erofs/decompressor.c", "erofs_allocpage(pagepool, rq->gfp);"],
        ["fs/erofs/decompressor_lzma.c", "erofs_allocpage(pagepool, rq->gfp);"],
        ["fs/erofs/zdata.h", "\tbool besteffort;"],
        ["fs/erofs/zdata.c", "\tclt->pcl->besteffort |= !fe->readahead;"],
        ["fs/erofs/zdata.c", "\t\t\t\t\t.gfp = pcl->besteffort ?"],
        ["fs/erofs/zdata.c", "\t\t\t\t\t\tGFP_NOWAIT | __GFP_NORETRY"],
        ["fs/erofs/zdata.c", "\tpcl->besteffort = false;"],
    ],
    "core:erofs_readmore_past_eof": [
        ["fs/erofs/zdata.c",
         "\twhile ((cur >= end) && (cur < i_size_read(inode))) {\n"],
        ["fs/erofs/zdata.c", "cur < i_size_read(inode)"],
    ],
    "core:smaps_migration_guard": [
        ["fs/proc/task_mmu.c", "sailboat_smaps_migration_guard"],
        ["fs/proc/task_mmu.c", "static struct page *abk_smaps_migration_page("],
        ["fs/proc/task_mmu.c", "page = abk_smaps_migration_page(swpent);"],
        ["fs/proc/task_mmu.c", "page = abk_smaps_migration_page(entry);"],
    ],
    "core:kcompressd_writeback_pin": [
        ["mm/page_io.c", "sailboat_kcompressd_wb_pin"],
    ],
    "core:kcompressd_swapcache_drop": [
        ["mm/vmscan.c", "sailboat_kcompressd_swapcache_drop"],
        ["mm/vmscan.c", "LIST_HEAD(wb_retry_pages);"],
        ["mm/vmscan.c", "bool wb_retry_done = false;"],
        ["mm/vmscan.c", "list_add(&page->lru, &wb_retry_pages);"],
        ["mm/vmscan.c", "wait_on_page_writeback(page);"],
        ["mm/vmscan.c", "PF_KSWAPD"],
        ["mm/vmscan.c", "list_splice_tail(&wb_retry_pages, page_list);"],
        ["mm/vmscan.c", "SWP_SYNCHRONOUS_IO"],
    ],
    "core:swapin_readahead_window": [
        ["mm/readahead.c", "sailboat_swapin_readahead_window"],
        ["mm/readahead.c", "bool abk_swapin_shrink_gate(void)"],
        ["mm/readahead.c", "unsigned int abk_swapin_window_clamp(unsigned int pages)"],
        ["mm/readahead.c", "module_param_named(swapin_vma_ra, enable_vma_readahead, bool, 0644);"],
        ["mm/swap_state.c", "pages = abk_swapin_window_clamp(pages);"],
        ["mm/swap_state.c", "sailboat_swapin_readahead_window"],
        ["mm/internal.h", "extern unsigned int abk_swapin_window_clamp(unsigned int pages);"],
        ["mm/internal.h", "extern bool abk_swapin_shrink_gate(void);"],
    ],
    "core:fault_around_app_policy": [
        "sailboat_fault_around_app_policy",
        "\tabk_fa_adjust(&should_around);",
        "static void abk_fa_adjust(bool *should_around)",
        "static DEFINE_MUTEX(abk_fa_app_lock);",
        "return from_kuid(&init_user_ns, current_uid());",
        "module_param(fault_around_enable, bool, 0644);",
        "module_param(fault_around_sys_uid, bool, 0644);",
        "module_param(fault_around_app_only, bool, 0644);",
        "module_param_cb(fault_around_app, &abk_fa_app_param_ops, NULL, 0644);",
        "bitmap_zero(abk_fa_app_bitmap, 90000);",
    ],
    "core:vm_kcompressd_swapout": [
        "Sail_kcompd%d",
        "abk_kcompressd_store",
        "abk_kcompressd_do_swapout",
        "static int abk_kcompressd_threshold = 24;",
        "static atomic_long_t abk_kcompressd_enqueued;",
        "static atomic_long_t abk_kcompressd_swapped;",
        "static atomic_long_t abk_kcompressd_sync;",
        ".procname	= \"kcompressd\"",
        "proc_dointvec_minmax",
        "SYSCTL_ZERO",
        "if (!current_is_kswapd())",
        "if (!PageAnon(page))",
        "if (memcg_is_dying(page_memcg(page)))",
        "if (!frontswap_enabled() &&",
        "SWP_SYNCHRONOUS_IO",
        "if (!abk_kcompressd_threshold || unlikely(!kcd->task))",
        "get_page(page);",
        "put_page(page);",
        "unlikely(!kfifo_out(&kcd->fifo, &head, sizeof(page)))",
        "if (head)",
        "pr_warn_once(\"kcompressd: dropping a queued page",
        "if (unlikely(kcd->task == current))",
        "cannot register /proc/sys/vm/kcompressd, offload disabled",
        "static void abk_kcompressd_add_node(int nid)",
        "static void abk_kcompressd_del_node(int nid)",
        "register_memory_notifier(&abk_kcompressd_memory_nb);",
        "case MEM_OFFLINE:",
        "if (arg->status_change_nid < 0)",
        "!kfifo_is_empty(&kcd->fifo) ||",
        "kthread_should_stop());",
        "static bool abk_kcompressd_store(struct page *page);",
        "wake_up_interruptible(&kcd->wait);",
        "kthread_stop(task);",
        "if (unlikely(!kcd->task)) {",
        "__swap_writepage(page, &wbc, end_swap_bio_write);",
        "\t\tunlock_page(page);\n\t\treturn 0;\n\t}\n\n"
        "\tret = __swap_writepage(page, wbc, end_swap_bio_write);\n",
        "current->flags |= PF_MEMALLOC | PF_KSWAPD;",
        "kfifo_out_locked(&kcd->fifo, &page, sizeof(page),",
    ],
    "core:mglru_reclaim_loop_rework": [
        "static unsigned long lruvec_evictable_size(struct lruvec *lruvec, int swappiness)",
        "static bool should_run_aging(struct lruvec *lruvec, unsigned long max_seq,\n"
        "\t\t\t     struct scan_control *sc, int swappiness)",
        "/* try to avoid aging, do gentle reclaim at the default priority */",
        "nr_to_scan = get_nr_to_scan(lruvec, sc, memcg, swappiness);",
        "while (nr_to_scan > 0) {",
        "nr_batch = min_t(unsigned long, nr_to_scan, MIN_LRU_BATCH);",
        "nr_to_scan -= delta;",
        "if (root_reclaim(sc) && should_age)",
        "static bool root_reclaim(struct scan_control *sc)",
        "return !sc->target_mem_cgroup ||\n\t       mem_cgroup_is_root(sc->target_mem_cgroup);",
        "bool should_age = false;",
        "unsigned long remaining = nr_to_scan;",
        "VM_WARN_ON_ONCE(nr_to_scan > MAX_LRU_BATCH);",
        "\t*isolatedp = isolated;\n\treturn scanned;\n}",
        "int total_scanned = 0;",
        "\t\tif (!scanned)\n\t\t\ttype = !type;",
        "/* In case page deletion left empty old gens, flush them */",
        "/* Scanning may have emptied the oldest gen, flush it */",
        "static void try_to_inc_min_seq(struct lruvec *lruvec, int swappiness)",
        "if (current_is_kswapd())\n\t\t\t\tbreak;",
        "nr_batch = min_t(unsigned long, nr_to_reclaim - sc->nr_reclaimed,",
    ],
    "core:mglru_dirty_reclaim_rework": [
        "if (stat.nr_unqueued_dirty == isolated) {\n"
        "\t\twakeup_flusher_threads(WB_REASON_VMSCAN);\n\t}",
        "sailboat_mglru_dirty_reclaim_rework",
        "static int page_inc_gen(struct lruvec *lruvec, struct page *page)",
        "/* for shrink_page_list(); PG_reclaim is left to the common routine */",
    ],
    "core:mglru_prefault_accessed_placement": [
        "sailboat_mglru_prefault_accessed_placement",
        ["include/linux/mm_inline.h",
         "else if (PageReferenced(page))\n\t\tseq = lrugen->min_seq[type] + 1;"],
        ["mm/swap.c",
         "} else if (!PageReferenced(page)) {\n\t\t\tmark_page_accessed(page);\n\t\t}"],
        ["mm/swap.c", "if (PageWorkingset(page)) {"],
        ["mm/workingset.c",
         "if (lru_gen_in_fault())\n"
         "\t\t\tmod_lruvec_state(lruvec, WORKINGSET_ACTIVATE_BASE + type, delta);"],
    ],
    "core:bg_readahead_tune": [
        "sailboat_bg_readahead_tune",
        ["mm/filemap.c", "register_trace_android_vh_should_fault_around("],
        ["mm/filemap.c", "register_trace_android_vh_do_async_mmap_readahead("],
        ["mm/filemap.c", "register_trace_android_vh_ra_tuning_max_page("],
        ["mm/filemap.c", "register_trace_android_vh_tune_mmap_readaround("],
        ["mm/filemap.c", 'task_css(current, cpuset_cgrp_id)'],
        ["mm/filemap.c", '"background"'],
        ["mm/filemap.c", "current->nvcsw <= (unsigned long)READ_ONCE(abk_bg_ra_nvcsw_min)"],
        ["mm/filemap.c", "module_param(abk_bg_ra_suppress, int, 0644);"],
        ["mm/filemap.c", "module_param(abk_bg_ra_nvcsw_min, int, 0644);"],
        ["mm/filemap.c", "*should_around = false;"],
        ["mm/filemap.c", "*skip = true;"],
        ["mm/filemap.c", "ractl->ra->ra_pages >> 1"],
        ["mm/filemap.c", "*start = max_t(long, 0, pgoff - ra_pages / 4);"],
    ],
    "core:bg_launch_prefetch": [
        "sailboat_bg_launch_prefetch",
        ["mm/filemap.c", "register_trace_android_vh_filemap_read("],
        ["mm/filemap.c", "register_trace_android_vh_filemap_map_pages("],
        ["mm/filemap.c", "register_trace_android_vh_should_fault_around("],
        ["mm/filemap.c", "r->mtime_sec == host->i_mtime.tv_sec"],
        ["mm/filemap.c", "f->f_mapping->host->i_ino != ino"],
        ["mm/filemap.c",
         "current->nvcsw >= (unsigned long)READ_ONCE(abk_lp_young_max)"],
        ["mm/filemap.c", "page_cache_sync_readahead("],
        ["mm/filemap.c", "filp_open(path, O_RDONLY | O_LARGEFILE, 0)"],
        ["mm/filemap.c", "queue_work(system_long_wq, &abk_lp_kick)"],
        ["mm/filemap.c", "d_path(&file->f_path, rec->path, ABK_LP_PATH)"],
        ["mm/filemap.c", "module_param(abk_lp_young_max, int, 0644);"],
        ["mm/filemap.c", "module_param(abk_lp_replay_kb, int, 0644);"],
        ["mm/filemap.c", "module_param(abk_lp_fault_replay, int, 0644);"],
        ["mm/filemap.c", "if (!READ_ONCE(abk_lp_fault_replay))"],
        ["mm/filemap.c",
         "module_param_call(abk_lp_record_uid, abk_lp_record_uid_set, "
         "&param_get_int,"],
        ["mm/filemap.c",
         "module_param_call(abk_lp_prefetch_uid, abk_lp_prefetch_uid_set, "
         "&param_get_int,"],
    ],
}


REQUIRED_ABSENT = {
    "perf:psi_oncpu_state_mask": [
        ["include/linux/psi_types.h", "NR_ONCPU"],
        ["kernel/sched/psi.c", "tasks[NR_ONCPU]"],
        ["kernel/sched/psi.c", "identical_state"],
    ],
    "perf:psi_cgroup_pressure_switch": [
        ["include/linux/psi_types.h", "\tbool enabled;"],
        ["include/linux/cgroup-defs.h", "struct psi_group *psi;"],
        ["include/linux/cgroup-defs.h", "struct cgroup_file psi_files["],
        ["kernel/cgroup/cgroup.c",
         "static ssize_t cgroup_pressure_write(struct kernfs_open_file *of, "
         "char *buf,\n\t\t\t\t\t  size_t nbytes, enum psi_res res)"],
    ],
    "display:drm_valid_clones_revert": [
        "drm_atomic_check_valid_clones",
        "drm_atomic_check_valid_clones(state, crtc)",
    ],
    "core:vm_kcompressd_swapout": [
        "WARN_ON_ONCE",
        "WARN_ON(",
        "WARN(",
    ],
    "core:kcompressd_writeback_pin": [
        "WARN_ON_ONCE",
        "WARN_ON(",
        "WARN(",
    ],
    "perf:schedutil_smart_policy": [
        "static bool abk_sf_enable = true;",
        "if (floor > policy->max)",
    ],
    "core:customize_alloc_gfp_vh": [
        ["include/trace/hooks/mm.h", "ABK stable_515_backport:"],
        ["drivers/android/vendor_hooks.c", "ABK stable_515_backport:"],
    ],
    "core:fd_alloc_hotpath": [
        ["fs/file.c", "abk_fdtable_slots_wanted"],
        ["fs/file.c", "ALIGN(slots_wanted"],
    ],
    "core:hugepage_fault_alloc_fastpath": [
        ["mm/huge_memory.c", "khugepaged_enter_vma(vma,"],
    ],
    "perf:sched_eevdf_core_fields": [
        ["include/linux/sched.h", "ANDROID_KABI_USE(3, struct {"],
        ["include/linux/sched.h", "ANDROID_KABI_USE(4, struct {"],
        ["include/linux/sched.h",
         "ANDROID_KABI_USE(3, s64 vlag);\n\tANDROID_KABI_RESERVE(4);"],
    ],
    "perf:sched_eevdf_pick_logic": [
        ["kernel/sched/fair.c", "se->slice = slice;"],
        ["kernel/sched/fair.c", "abk_eevdf_refresh_deadline(cfs_rq, se);"],
        ["kernel/sched/fair.c", "abk_eevdf_max_slice"],
        ["kernel/sched/fair.c", "abk_eevdf_total_weight"],
    ],
    "perf:nohz_field_refinement": [
        ["include/linux/sched/nohz.h", "nohz_cpu_state_test"],
        ["include/linux/sched/nohz.h", "nohz_cpu_inidle"],
        ["include/linux/sched/nohz.h", "nohz_cpu_idle_active"],
        ["include/linux/sched/nohz.h", "nohz_cpu_tick_stopped"],
        ["include/linux/sched/nohz.h", "extern unsigned int nohz_cpu_state_flags"],
        ["kernel/time/tick-sched.c", "EXPORT_SYMBOL_GPL(nohz_cpu_state_flags)"],
        ["kernel/time/tick-sched.c", "EXPORT_SYMBOL_GPL(nohz_cpu_idle_calls)"],
    ],
    "perf:blk_mq_async_depth": [
        ["block/kyber-iosched.c", "kqd->async_depth = q->async_depth;"],
        ["block/bfq-iosched.c", "depth = bfqd->queue->async_depth;"],
    ],
    "perf:avg_idle_preemption_mode": [
        ["kernel/sched/fair.c", "this_rq->wake_avg_idle"],
    ],
    "core:zram_writeback_batching": [
        ["drivers/block/zram/zram_drv.c", "err = submit_bio_wait(&bio);"],
        ["drivers/block/zram/zram_drv.c",
         "A single page IO would be inefficient for write"],
    ],
    "core:zram_compressed_writeback": [
        ["drivers/block/zram/zram_drv.c", "zs_obj_read_begin(zram->mem_pool"],
        ["drivers/block/zram/zram_drv.c", ", zstrm->local_copy)"],
        ["drivers/block/zram/zram_drv.c", "zcomp_stream_put(zstrm)"],
        ["drivers/block/zram/zram_drv.c", "memset_page("],
    ],
    "perf:sched_steal_time_excess_drop": [
        ["kernel/sched/core.c", "rq->prev_steal_time_rq += steal;"],
    ],
    "perf:randomize_kstack_pertask": [
        ["include/linux/randomize_kstack.h", "DECLARE_PER_CPU(u32, kstack_offset);"],
        ["init/main.c", "DEFINE_PER_CPU(u32, kstack_offset);"],
    ],
    "perf:blk_mq_suspend_wakeup_abort": [
        ["block/blk-mq.c",
         "\tif (percpu_ref_tryget(&hctx->queue->q_usage_counter)) {\n"
         "\t\twhile (blk_mq_hctx_has_requests(hctx))\n\t\t\tmsleep(5);\n"
         "\t\tpercpu_ref_put(&hctx->queue->q_usage_counter);\n\t}"],
    ],
    "perf:blk_mq_quiesced_elevator_switch": [
        ["block/blk-mq.c", "elevator_switch_mq"],
        ["block/blk.h", "elevator_switch_mq"],
    ],
    "core:zram_recompress_max_pages": [
        ["drivers/block/zram/zram_drv.c",
         "\t\tnum_recomp_pages--;\n\t\tzram_slot_lock(zram, index);"],
    ],
    "core:arm64_pte_mkwrite_clean": [
        "\tpte = set_pte_bit(pte, __pgprot(PTE_WRITE));\n"
        "\tpte = clear_pte_bit(pte, __pgprot(PTE_RDONLY));",
    ],
    "core:arm64_lse_percpu_load_atomics": [
        '#op_lse "\\t%" #w "[val], %[ptr]\\n"',
        "PERCPU_OP(add, add, stadd)",
        "PERCPU_OP(andnot, bic, stclr)",
        "PERCPU_OP(or, orr, stset)",
    ],    "core:zram_wb_slot_preserve": [
        ["drivers/block/zram/zram_drv.c", "\tu32 size = 0, prio = 0;"],
        ["drivers/block/zram/zram_drv.c",
         "\t\tsize = zram_get_obj_size(zram, index);"],
        ["drivers/block/zram/zram_drv.c", "\t\tif (huge)"],
    ],
    "core:truncate_shadow_batch": [
        ["mm/truncate.c",
         "static int invalidate_exceptional_entry(struct address_space *mapping,"],
        ["mm/truncate.c",
         "static int invalidate_exceptional_entry2(struct address_space *mapping,"],
        ["mm/truncate.c", "\tclear_shadow_entry(mapping, index, entry);"],
        ["mm/truncate.c", "count += invalidate_exceptional_entry(mapping,"],
    ],
    "core:truncate_shadow_batch_sweep": [
        ["mm/truncate.c", "clear_shadow_entries(mapping, &pvec, indices);"],
        ["mm/truncate.c", "\t\t\t__clear_shadow_entry(mapping, indices[i], page);"],
    ],
"core:fuse_prefault_out_of_write_path": [
        ["fs/fuse/file.c", "ABK stable_515_backport:"],
    ],
    "core:erofs_readahead_relaxed_gfp": [
        ["fs/erofs/decompressor.c", "GFP_KERNEL | __GFP_NOFAIL"],
        ["fs/erofs/decompressor_lzma.c", "GFP_KERNEL | __GFP_NOFAIL"],
        ["fs/erofs/compress.h", "ABK stable_515_backport:"],
        ["fs/erofs/decompressor.c", "ABK stable_515_backport:"],
        ["fs/erofs/decompressor_lzma.c", "ABK stable_515_backport:"],
        ["fs/erofs/zdata.c", "ABK stable_515_backport:"],
        ["fs/erofs/zdata.h", "ABK stable_515_backport:"],
        ["fs/erofs/zdata.c", "bool ra)"],
    ],
    "core:erofs_readmore_past_eof": [
        ["fs/erofs/zdata.c", "ABK stable_515_backport:"],
    ],
    "core:memcg_stats_percpu_slim": [
        "vmstats_percpu->state[idx]",
        "vmstats_percpu->events[idx]",
        "vmstats_percpu->events[event]",
        "vmstats_percpu->nr_page_events",
        "vmstats_percpu->targets[target]",
        "lruvec_stats_percpu->state[idx]",
        "alloc_percpu_gfp(struct memcg_vmstats_percpu",
        "alloc_percpu_gfp(struct lruvec_stats_percpu",
    ],
    "core:mglru_rework_aging_feedback": [
        ["include/linux/mmzone.h", "bool can_swap;"],
        ["include/linux/mmzone.h", "MAX_NR_TIERS - 1];"],
        ["mm/vmscan.c", "VM_WARN_ON_ONCE(!full_scan && (type == LRU_GEN_FILE || can_swap));"],
        ["mm/vmscan.c", "min_seq[LRU_GEN_ANON] = min(min_seq[LRU_GEN_ANON], min_seq[LRU_GEN_FILE]);"],
        ["mm/vmscan.c", "get_nr_gens(lruvec, !swappiness) == MIN_NR_GENS"],
        ["mm/vmscan.c", "swappiness > 200)"],
        ["mm/vmscan.c", "min_seq[!can_swap]"],
        ["mm/vmscan.c", "walk->can_swap"],
    ],
    "core:mglru_rework_type_selection": [
        ["mm/vmscan.c", "int gain[ANON_AND_FILE] = { swappiness, 200 - swappiness };"],
        ["mm/vmscan.c", "get_type_to_scan(lruvec, swappiness, &tier);"],
        ["mm/vmscan.c", "*tier_idx = tier - 1;"],
        ["mm/vmscan.c", "else if (swappiness == 200)"],
    ],
    "core:mglru_rework_refault_detection": [
        ["mm/workingset.c",
         "if ((token >> LRU_REFS_WIDTH) != (min_seq & (EVICTION_MASK >> LRU_REFS_WIDTH)))"],
    ],
    "core:mglru_reclaim_loop_rework": [
        ["mm/vmscan.c", "return isolated || !remaining ? scanned : 0;"],
        ["mm/vmscan.c", "*need_aging = should_run_aging(lruvec, max_seq, min_seq,"],
        ["mm/vmscan.c", "scanned += try_to_inc_min_seq(lruvec, swappiness);"],
        ["mm/vmscan.c",
         "if (evictable_min_seq(lrugen->min_seq, swappiness) + MIN_NR_GENS > lrugen->max_seq)\n"
         "\t\tscanned = 0;"],
        ["mm/vmscan.c",
         "\tint remaining = MAX_LRU_BATCH;\n"
         "\tstruct lru_gen_struct *lrugen = &lruvec->lrugen;\n"
         "\tstruct mem_cgroup *memcg = lruvec_memcg(lruvec);"],
    ],
    "core:mglru_dirty_reclaim_rework": [
        ["mm/vmscan.c", "\tbool dirty, writeback;"],
        ["mm/vmscan.c", "sc->nr.file_taken += delta;"],
        ["mm/vmscan.c", "sc->nr.file_taken += isolated;"],
        ["mm/vmscan.c",
         "\tsc->nr.unqueued_dirty += stat.nr_unqueued_dirty;\n"
         "\tsc->nr_reclaimed += reclaimed;"],
        ["mm/vmscan.c",
         "if (sc->nr.unqueued_dirty && sc->nr.unqueued_dirty == sc->nr.file_taken)"],
        ["mm/vmscan.c", "page_inc_gen(lruvec, page, true)"],
        ["mm/vmscan.c", "page_inc_gen(lruvec, page, false)"],
        ["mm/vmscan.c", "\t/* for shrink_page_list() */\n\tClearPageReclaim(page);"],
        ["mm/vmscan.c", "/* for end_page_writeback() */"],
    ],
    "core:mglru_prefault_accessed_placement": [
        ["mm/workingset.c",
         "atomic_long_add(delta, &lrugen->refaulted[hist][type][tier]);\n"
         "\tmod_lruvec_state(lruvec, WORKINGSET_ACTIVATE_BASE + type, delta);"],
    ],
}

REQUIRED_PAIRING = {
    "perf:sched_eevdf_modern_fields": [
        ("kernel/sched/features.h", "SCHED_FEAT(PREEMPT_SHORT, true)",
         "kernel/sched/core.c", "attr->sched_runtime",
         "PREEMPT_SHORT compares two request sizes; with no producer of a "
         "non-default se->slice the comparison is a tautology"),
        ("kernel/sched/features.h", "SCHED_FEAT(EEVDF, true)",
         "kernel/sched/fair.c", "if (!sched_feat(EEVDF) && wakeup_preempt_entity",
         "an EEVDF switch with no !EEVDF wakeup path is a switch with one "
         "branch, so turning it off changes nothing"),
        ("kernel/sched/features.h", "SCHED_FEAT(EEVDF, true)",
         "kernel/sched/fair.c",
         "if (sched_feat(EEVDF))\n\t\treturn abk_pick_eevdf(cfs_rq, curr);",
         "same: the selector needs its legacy half too"),
    ],
}


REQUIRED_IN_FUNCTION = {
    "perf:psi_oncpu_state_mask": [
        ("kernel/sched/psi.c", "psi_group_change",
         ["if (unlikely(clear & TSK_ONCPU)) {",
          "clear &= ~TSK_ONCPU;",
          "state_mask = PSI_ONCPU;",
          "state_mask = groupc->state_mask & PSI_ONCPU;",
          "test_state(groupc->tasks, s, state_mask & PSI_ONCPU)",
          "groupc->state_mask = state_mask;"],
         ["tasks[NR_ONCPU]"]),
        ("kernel/sched/psi.c", "psi_task_switch",
         ["per_cpu_ptr(group->pcpu, cpu)->state_mask &",
          "if ((prev->psi_flags ^ next->psi_flags) & ~TSK_ONCPU) {",
          "group = iterate_groups(prev, &iter)"],
         ["tasks[NR_ONCPU]", "identical_state"]),
    ],
    "perf:psi_cgroup_pressure_switch": [
        ("kernel/sched/psi.c", "psi_group_change",
         ["if (unlikely(!psi_group_enabled(group)))",
          "groupc->state_mask = 0;",
          "write_seqcount_end(&groupc->seq);"],
         []),
        ("kernel/sched/psi.c", "psi_cgroup_restart",
         ["if (!psi_group_enabled(group))",
          "rq_lock_irq(rq, &rf);",
          "psi_group_change(group, cpu, 0, 0, cpu_clock(cpu), true);",
          "rq_unlock_irq(rq, &rf);"],
         ["for_each_online_cpu"]),
        ("kernel/cgroup/cgroup.c", "cgroup_pressure_write",
         ["psi_cgroup_accounting_enabled(cgrp) != enable",
          "psi_cgroup_accounting_set(cgrp, enable);",
          "psi_cgroup_restart(cgroup_ino(cgrp) == 1 ?",
          "cgroup_kn_unlock(of->kn);"],
         ["psi_trigger_create"]),
    ],
    "perf:schedutil_smart_cap": [
        ("kernel/sched/cpufreq_schedutil.c", "abk_sc_owns",
         ["if (abk_sc_dvfs_owned(policy) || policy->min == policy->max)",
          "c = abk_sc_cap(policy);",
          "if (c <= policy->min || c >= policy->max)"],
         ["*cap = policy->max"]),
        ("kernel/sched/cpufreq_schedutil.c", "abk_sc_resolve_freq",
         ["raw_spin_lock_irqsave(&p->lock, flags)",
          "if (abk_sc_cooled(policy))",
          "p->high_start = jiffies;",
          "p->high_start = 0;",
          "*target_freq = cap;",
          "raw_spin_unlock_irqrestore(&p->lock, flags)"],
         ["util * 100 >= cap * abk_sc_entry_pct",
          "abk_sc_entry_pct",
          "pr_info(", "pr_err(", "pr_warn(", "pr_debug(", "printk(",
          "uclamp", "trace_printk"]),
        ("kernel/sched/cpufreq_schedutil.c", "abk_sc_reassert_freq",
         ["if (!target_freq || !abk_sc_owns(policy, &cap))",
          "if (p->capped && *target_freq > cap)",
          "*target_freq = cap;"],
         ["pr_info(", "pr_err(", "pr_warn(", "printk(", "uclamp"]),
        ("kernel/sched/cpufreq_schedutil.c", "abk_sc_cooled",
         ["for_each_cpu(cpu, policy->cpus)",
          "time_after(jiffies, oldest + msecs_to_jiffies(abk_sc_release_ms))"],
         ["abk_sc_entry_pct"]),
        ("kernel/sched/cpufreq_schedutil.c", "abk_sc_init",
         ["register_trace_android_vh_scheduler_tick(abk_sc_tick, NULL)",
          "abk_sc_resolve_freq, NULL);",
          "raw_spin_lock_init(&abk_sc_policies[cpu].lock);",
          "kcalloc(num_possible_cpus(), sizeof(*abk_sc_policies),"],
         ["pr_err(", "pr_debug("]),
    ],
    "perf:schedutil_smart_policy": [
        ("kernel/sched/cpufreq_schedutil.c", "abk_sf_resolve_freq",
         ["if (abk_sf_dvfs_owned(policy) || policy->min == policy->max)",
          "floor = mult_frac(policy->cpuinfo.max_freq, abk_sf_floor_pct, 100)",
          "if (floor <= policy->min || floor >= policy->max)",
          "abk_sf_cpu_boosting(cpu)"],
         ["floor = policy->max"]),
        ("kernel/sched/cpufreq_schedutil.c", "abk_sf_dvfs_owned",
         ['strcmp(policy->governor->name, "schedutil") != 0',
          "if (!policy->governor)"],
         []),
        ("kernel/sched/cpufreq_schedutil.c", "abk_sf_sample",
         ["c->boost_start = 0;",
          "if (util * 100 < cap * ABK_SF_SUSTAINED_PCT)",
          "c->boost_release = now + msecs_to_jiffies(abk_sf_exit_ms)"],
         ["} else if (util * 100 < cap * ABK_SF_EXIT_PCT) {"]),
        ("kernel/sched/cpufreq_schedutil.c", "abk_sf_cpu_boosting",
         ["time_after(jiffies, c->boost_release)", "abk_sf_clear(c)"],
         []),
    ],
    "core:pid_alloc_hotpath_phase2": [
        ("kernel/pid.c", "alloc_pid",
         ["retry_preload:", "retried_preload = false;", "retried_preload = true;"],
         ["continue;"]),
    ],
    "core:close_range_hotpath": [
        ("fs/file.c", "__range_close",
         ["n = last_fd(fdt);",
          "max_fd = min(max_fd, n);",
          "fd = find_next_bit(fdt->open_fds, max_fd + 1, fd);"],
         ["abk_pick_file_for_close", "abk_close_range_limit"]),
    ],
    "core:zram_recompression": [
        ("drivers/block/zram/zram_drv.c", "__zram_bvec_read",
         ["zram_read_from_zspool(zram, page, index)"],
         ["zcomp_decompress", "zcomp_stream_get"]),
        ("drivers/block/zram/zram_drv.c", "zram_read_from_zspool",
         ["prio = zram_get_priority(zram, index)",
          "zcomp_stream_get(zram->comps[prio])",
          "zcomp_stream_put(zram->comps[prio])"],
         ["zram->comp)"]),
        ("drivers/block/zram/zram_drv.c", "__zram_bvec_write",
         ["zcomp_stream_get(zram->comps[ZRAM_PRIMARY_COMP])"],
         ["zram_get_priority", "zram->comp)"]),
        ("drivers/block/zram/zram_drv.c", "disksize_store",
         ["for (prio = 0; prio < ZRAM_MAX_COMPS; prio++)",
          "zram->comps[prio] = comp", "zram->num_active_comps++",
          "out_free_comps"],
         []),
        ("drivers/block/zram/zram_drv.c", "zram_free_page",
         ["zram_set_priority(zram, index, 0)",
          "zram_clear_flag(zram, index, ZRAM_INCOMPRESSIBLE)"],
         []),
    ],
    "core:pagealloc_fallback_reuse": [
        ("mm/page_alloc.c", "find_suitable_fallback",
         ["claimable && !can_steal_fallback(order, migratetype)",
          "for (i = 0; fallbacks[migratetype][i] != MIGRATE_TYPES; i++)"],
         ["bool *can_steal", "only_stealable"]),
        ("mm/page_alloc.c", "__rmqueue",
         ["switch (*mode)", "case RMQUEUE_CLAIM:",
          "*mode = RMQUEUE_STEAL"],
         ["__rmqueue_fallback"]),
        ("mm/page_alloc.c", "rmqueue_bulk",
         ["enum rmqueue_mode rmqm = RMQUEUE_NORMAL",
          "alloc_flags, &rmqm"],
         []),
        ("mm/compaction.c", "__compact_finished",
         ["find_suitable_fallback(area, order, migratetype,\n"
          "\t\t\t\t\t\ttrue) >= 0"],
         ["bool can_steal"]),
    ],
    "core:zram_algo_lock": [
        ("drivers/block/zram/zram_drv.c", "abk_zram_locked_algo_store",
         ["abk_zram_algo_lock_report(attr->attr.name, buf);",
          "return len;"],
         ["-EPERM", "-EACCES"]),
        ("drivers/block/zram/zram_drv.c", "zram_add",
         ["comp_algorithm_set(zram, ZRAM_PRIMARY_COMP,",
          "zcomp_available_algorithm(abk_zram_comp_algo)"],
         []),
        ("drivers/block/zram/zram_drv.c", "__comp_algorithm_store",
         ["comp_algorithm_set(zram, prio, compressor);"],
         ["abk_zram_lock_algo", "abk_zram_locked_algo_store"]),
    ],
    "core:rcu_nocb_cpu_default_all": [
        ("kernel/rcu/tree_nocb.h", "rcu_init_nohz",
         ["if (!cpumask_available(rcu_nocb_mask))",
          "offload_all = true",
          "offload_all = false",
          "if (offload_all)",
          "cpumask_setall(rcu_nocb_mask)"],
         ["rcu_state.nocb_is_setup"]),
    ],
    "core:gfp_pressure_fastfail": [
        ("mm/page_alloc.c", "abk_gfp_fastfail_hook",
         ["if (!READ_ONCE(abk_gfp_fastfail))",
          "if (order < READ_ONCE(abk_gfp_fastfail_order))",
          "if (!abk_gfp_under_pressure())",
          "*gfp |= __GFP_NORETRY | __GFP_NOWARN;"],
         ["return true"]),
        ("mm/page_alloc.c", "abk_gfp_under_pressure",
         ["time_is_after_jiffies(READ_ONCE(abk_gfp_wm_stamp) + HZ)",
          "WRITE_ONCE(abk_gfp_high_wm, sum);",
          "si_mem_available() < (long)limit"],
         ["abk_gfp_high_wm += "]),
    ],
    "core:zram_recompress_max_pages": [
        ("drivers/block/zram/zram_drv.c", "recompress_store",
         ["u64 num_recomp_pages = ULLONG_MAX;",
          'if (!strcmp(param, "max_pages")) {',
          "if (!num_recomp_pages)\n\t\t\tbreak;\n\n\t\tzram_slot_lock(zram, index);",
          "num_recomp_pages--;\n\t\terr = zram_recompress(zram, index, page,"],
         ["abk_zram_recomp_enqueue"]),
        ("drivers/block/zram/zram_drv.c", "recompress_async_store",
         ["u64 num_recomp_pages = ULLONG_MAX;",
          'if (!strcmp(param, "max_pages")) {',
          "if (!num_recomp_pages)\n\t\t\tbreak;\n\n\t\tzram_slot_lock(zram, index);",
          "num_recomp_pages--;\n\t\terr = abk_zram_recomp_enqueue("],
         ["zram_recompress(zram, index, page"]),
    ],
    "core:zram_recomp_best_prio_skip": [
        ("drivers/block/zram/zram_drv.c", "recompress_store",
         ["zram_test_flag(zram, index, ZRAM_INCOMPRESSIBLE))",
          "if (abk_zram_recompress_pointless(zram, index, prio, prio_max))",
          "num_recomp_pages--;\n\t\terr = zram_recompress(zram, index, page,"],
         ["num_recomp_pages--;\n\n\t\t/* ABK stable_515_backport: Batch 47."]),
        ("drivers/block/zram/zram_drv.c", "recompress_async_store",
         ["zram_test_flag(zram, index, ZRAM_INCOMPRESSIBLE))",
          "if (abk_zram_recompress_pointless(zram, index, prio, prio_max))",
          "candidate = true;"],
         ["candidate = true;\n\n\t\t/* ABK stable_515_backport: Batch 47."]),
    ],
    "core:readahead_mmap_miss_race": [
        ("mm/filemap.c", "do_async_mmap_readahead",
         ["if (likely(!PageLocked(page))) {",
          "mmap_miss = READ_ONCE(ra->mmap_miss);",
          "if (PageReadahead(page)) {"],
         ["return fpin;\n\tmmap_miss = READ_ONCE(ra->mmap_miss);\n"]),
    ],    "core:zram_wb_slot_preserve": [
        ("drivers/block/zram/zram_drv.c", "zram_writeback_complete",
         ["if (zram_test_flag(zram, index, ZRAM_HUGE))\n"
          "\t\tatomic64_dec(&zram->stats.huge_pages);",
          "atomic64_sub(zram_get_obj_size(zram, index),\n"
          "\t\t     &zram->stats.compr_data_size);",
          "zs_free(zram->mem_pool, zram_get_handle(zram, index));",
          "zram_set_element(zram, index, req->blk_idx);"],
         ["zram_free_page(zram, index);",
          "atomic64_inc(&zram->stats.pages_stored);",
          "\t\tif (huge)"]),
    ],
    "core:zsmalloc_free_zspage_out_of_lock": [
        ("mm/zsmalloc.c", "zs_free",
         ["\tstruct zspage *zspage_to_free = NULL;",
          "if (trylock_zspage(zspage)) {",
          "remove_zspage(class, zspage, ZS_EMPTY);",
          "zs_stat_dec(class, OBJ_ALLOCATED,",
          "spin_unlock(&class->lock);\n\n"
          "\tif (zspage_to_free) {\n"
          "\t\t__free_zspage_lockless(pool, zspage_to_free);",
          "atomic_long_sub(class->pages_per_zspage,"],
         ["\t\tfree_zspage(pool, class, zspage);"]),
        ("mm/zsmalloc.c", "__free_zspage",
         ["assert_spin_locked(&class->lock);",
          "__free_zspage_lockless(pool, zspage);",
          "zs_stat_dec(class, OBJ_ALLOCATED, class->objs_per_zspage);"],
         ["put_page(page);"]),
    ],
    "core:proactive_reclaim_batch_fidelity": [
        ("mm/memcontrol.c", "memory_reclaim",
         ["min(nr_to_reclaim - nr_reclaimed, SWAP_CLUSTER_MAX),"],
         ["\t\t\t\t\t\tnr_to_reclaim - nr_reclaimed,\n"]),
    ],
    "core:proactive_reclaim_decaying_batches": [
        ("mm/memcontrol.c", "memory_reclaim",
         ["unsigned long batch_size = (nr_to_reclaim - nr_reclaimed) / 4;",
          "batch_size, GFP_KERNEL, reclaim_options);"],
         ["min(nr_to_reclaim - nr_reclaimed, SWAP_CLUSTER_MAX)"]),
    ],
    "core:proactive_reclaim_swappiness_arg": [
        ("mm/memcontrol.c", "memory_reclaim",
         ["int swappiness = -1;",
          "substring_t args[MAX_OPT_ARGS];",
          "nr_to_reclaim = memparse(buf, &buf) / PAGE_SIZE;",
          "while ((start = strsep(&buf, \" \")) != NULL) {",
          "case MEMORY_RECLAIM_SWAPPINESS:",
          "if (swappiness < MIN_SWAPPINESS || swappiness > MAX_SWAPPINESS)",
          "swappiness == -1 ? NULL : &swappiness);"],
         ["page_counter_memparse(buf, \"\", &nr_to_reclaim);",
          "return -ERESTARTSYS;"]),
        ("mm/vmscan.c", "get_scan_count",
         ["int swappiness = sc_swappiness(sc, memcg);"],
         ["int swappiness = mem_cgroup_swappiness(memcg);"]),
        ("mm/vmscan.c", "get_swappiness",
         ["sc_swappiness(sc, memcg);"],
         ["mem_cgroup_swappiness(memcg)"]),
        ("mm/vmscan.c", "try_to_free_mem_cgroup_pages",
         ["int *swappiness)",
          ".proactive_swappiness = swappiness,"],
         []),
    ],
    "core:proactive_reclaim_suspend_abort": [
        ("mm/vmscan.c", "should_abort_scan",
         ["if (unlikely(sc->proactive && signal_pending(current)))",
          "return true;"],
         ["if (unlikely(sc->proactive && signal_pending(current)))\n"
          "\t\treturn false;"]),
        ("mm/memcontrol.c", "memory_reclaim",
         ["return -ERESTARTSYS;"],
         ["return -EINTR;"]),
    ],
    "core:lru_add_drain_dead_folios": [
        ("mm/swap.c", "__pagevec_lru_add",
         ["if (page_ref_freeze(page, 1)) {",
          "__ClearPageActive(page);",
          "__ClearPageUnevictable(page);",
          "pvec->pages[i] = NULL;",
          "free_unref_page_list(&pages_to_free);",
          "release_pages(pvec->pages, pvec->nr);"],
         []),
        ("mm/swap.c", "release_pages",
         ["if (!page)\n\t\t\tcontinue;"],
         []),
    ],
    "core:truncate_shadow_batch_sweep": [
        ("mm/truncate.c", "__invalidate_mapping_pages",
         ["int nr = pagevec_count(&pvec);",
          "clear_shadow_entries(mapping, indices[0], indices[nr-1]);"],
         []),
        ("mm/truncate.c", "invalidate_inode_pages2_range",
         ["int nr = pagevec_count(&pvec);",
          "xa_has_values = true;",
          "clear_shadow_entries(mapping, indices[0], indices[nr-1]);"],
         []),
    ],
"core:fuse_prefault_out_of_write_path": [
        ("fs/fuse/file.c", "fuse_fill_write_pages",
         ["\t\tif (!tmp) {\n"
          "\t\t\tunlock_page(page);\n"
          "\t\t\tput_page(page);\n"
          "\n"
          "\t\t\t/*\n"
          "\t\t\t * Ensure forward progress by faulting in\n"
          "\t\t\t * while not holding the page lock:\n"
          "\t\t\t */\n"
          "\t\t\tif (fault_in_iov_iter_readable(ii, bytes)) {\n"
          "\t\t\t\terr = -EFAULT;\n"
          "\t\t\t\tbreak;\n"
          "\t\t\t}\n"
          "\n"
          "\t\t\tgoto again;",
          "\t\terr = -ENOMEM;\n"
          "\t\tpage = grab_cache_page_write_begin(mapping, index, 0);"],
         [" again:\n"
          "\t\terr = -EFAULT;\n"]),
    ],
    "core:erofs_readahead_relaxed_gfp": [
        ("fs/erofs/decompressor.c", "z_erofs_lz4_prepare_dstpages",
         ["erofs_allocpage(pagepool, rq->gfp);",
          "if (!victim)",
          "return -ENOMEM;"],
         ["GFP_KERNEL | __GFP_NOFAIL"]),
        ("fs/erofs/decompressor_lzma.c", "z_erofs_lzma_decompress",
         ["erofs_allocpage(pagepool, rq->gfp);",
          "goto failed;",
          "\nfailed:\n"],
         ["GFP_KERNEL | __GFP_NOFAIL"]),
        ("fs/erofs/zdata.c", "z_erofs_do_read_page",
         ["clt->pcl->besteffort |= !fe->readahead;"],
         ["bool ra"]),
        ("fs/erofs/zdata.c", "z_erofs_decompress_pcluster",
         [".gfp = pcl->besteffort ?",
          "GFP_KERNEL | __GFP_NOFAIL :",
          "GFP_NOWAIT | __GFP_NORETRY",
          "pcl->besteffort = false;"],
         []),
    ],
    "core:erofs_readmore_past_eof": [
        ("fs/erofs/zdata.c", "z_erofs_pcluster_readmore",
         ["\twhile ((cur >= end) && (cur < i_size_read(inode))) {\n",
          "cur = map->m_la + map->m_llen - 1;"],
         ["\twhile (cur >= end) {\n",
          "\twhile ((cur >= end) || (cur < i_size_read(inode))) {\n"]),
    ],
    "core:mglru_rework_refault_detection": [
        ("mm/workingset.c", "lru_gen_refault",
         ["unsigned long seq;",
          "unsigned long diff;",
          "if (diff >= MAX_NR_GENS)",
          "hist = lru_hist_from_seq(READ_ONCE(lrugen->min_seq[type]));"],
         ["unsigned long min_seq;"]),
    ],
    "core:memcg_stats_percpu_slim": [
        ("mm/memcontrol.c", "__mod_memcg_state",
         ["i = memcg_stats_index(idx);",
          "if (i < 0)",
          "abk_vmstats_percpu_of(memcg)->state[i], val"],
         ["vmstats_percpu->state[idx]"]),
        ("mm/memcontrol.c", "memcg_page_state_local",
         ["i = memcg_stats_index(idx);",
          "abk_vmstats_percpu_of(memcg)->state[i], cpu"],
         ["vmstats_percpu->state[idx]"]),
        ("mm/memcontrol.c", "__mod_memcg_lruvec_state",
         ["i = memcg_stats_index(idx);",
          "abk_vmstats_percpu_of(memcg)->state[i], val",
          "abk_lruvec_stats_percpu_of(pn)->state[i], val"],
         ["vmstats_percpu->state[idx]",
          "lruvec_stats_percpu->state[idx]"]),
        ("mm/memcontrol.c", "__count_memcg_events",
         ["i = memcg_events_index(idx);",
          "abk_vmstats_percpu_of(memcg)->events[i], count"],
         ["vmstats_percpu->events[idx]"]),
        ("mm/memcontrol.c", "memcg_events_local",
         ["i = memcg_events_index(event);",
          "abk_vmstats_percpu_of(memcg)->events[i], cpu"],
         ["vmstats_percpu->events[event]"]),
        ("mm/memcontrol.c", "lruvec_page_state_local",
         ["i = memcg_stats_index(idx);",
          "abk_lruvec_stats_percpu_of(pn)->state[i], cpu"],
         ["lruvec_stats_percpu->state[idx]"]),
        ("mm/memcontrol.c", "mem_cgroup_css_rstat_flush",
         ["struct abk_vmstats_percpu *statc",
          "per_cpu_ptr(abk_vmstats_percpu_of(memcg), cpu)",
          "for (i = 0; i < NR_MEMCG_VMSTAT_SIZE; i++)",
          "int item = i < NR_MEMCG_NODE_STAT_ITEMS ?",
          "memcg->vmstats.state[item] += delta",
          "for (i = 0; i < NR_MEMCG_VM_EVENTS; i++)",
          "int event = memcg_vm_event_items[i];",
          "memcg->vmstats.events[event] += delta",
          "struct abk_lruvec_stats_percpu *lstatc",
          "per_cpu_ptr(abk_lruvec_stats_percpu_of(pn), cpu)",
          "for (i = 0; i < NR_MEMCG_NODE_STAT_ITEMS; i++)",
          "int item = memcg_node_stat_items[i];",
          "pn->lruvec_stats.state[item] += delta"],
         ["for (i = 0; i < MEMCG_NR_STAT; i++)",
          "for (i = 0; i < NR_VM_EVENT_ITEMS; i++)",
          "for (i = 0; i < NR_VM_NODE_STAT_ITEMS; i++)"]),
        ("mm/memcontrol.c", "mem_cgroup_charge_statistics",
         ["abk_vmstats_percpu_of(memcg)->nr_page_events"],
         ["vmstats_percpu->nr_page_events"]),
        ("mm/memcontrol.c", "mem_cgroup_event_ratelimit",
         ["abk_vmstats_percpu_of(memcg)->nr_page_events",
          "abk_vmstats_percpu_of(memcg)->targets[target]"],
         ["vmstats_percpu->nr_page_events",
          "vmstats_percpu->targets[target]"]),
        ("mm/memcontrol.c", "uncharge_batch",
         ["abk_vmstats_percpu_of(ug->memcg)->nr_page_events"],
         ["vmstats_percpu->nr_page_events"]),
        ("mm/memcontrol.c", "alloc_mem_cgroup_per_node_info",
         ["__alloc_percpu_gfp(sizeof(struct abk_lruvec_stats_percpu)",
          "__alignof__(struct abk_lruvec_stats_percpu)"],
         ["alloc_percpu_gfp(struct lruvec_stats_percpu"]),
        ("mm/memcontrol.c", "mem_cgroup_alloc",
         ["__alloc_percpu_gfp(sizeof(struct abk_vmstats_percpu)",
          "__alignof__(struct abk_vmstats_percpu)"],
         ["alloc_percpu_gfp(struct memcg_vmstats_percpu"]),
    ],
    "core:huge_memory_imap_split_uaf": [
        ("mm/huge_memory.c", "__split_huge_page",
         ["pgoff_t end, struct address_space *mapping)",
          "\tif (mapping)\n\t\ti_mmap_unlock_read(mapping);"],
         []),
        ("mm/huge_memory.c", "split_huge_page_to_list",
         ["__split_huge_page(page, list, end, mapping);",
          "\t\tmapping = NULL;"],
         ["__split_huge_page(page, list, end);"]),
    ],
    "core:swap_readahead_lru_add_drain": [
        ("mm/swap_state.c", "swap_cluster_readahead", [], ["lru_add_drain"]),
        ("mm/swap_state.c", "swap_vma_readahead", [], ["lru_add_drain"]),
    ],
    "core:vmscan_tasks_rcu_qs": [
        ("mm/vmscan.c", "shrink_lruvec",
         ["cond_resched_tasks_rcu_qs();"],
         ["\t\tcond_resched();"]),
    ],
    "core:filemap_mmap_miss_tried": [
        ("mm/filemap.c", "filemap_map_pages",
         ["if (mmap_miss > 0 && !(vmf->flags & FAULT_FLAG_TRIED))"],
         ["if (mmap_miss > 0)\n\t\t\tmmap_miss--;"]),
    ],
    "core:memcg_dying_bailout": [
        ("mm/memcontrol.c", "memory_high_write",
         ["if (memcg_is_dying(memcg))"], []),
        ("mm/memcontrol.c", "memory_max_write",
         ["if (memcg_is_dying(memcg))"], []),
        ("mm/memcontrol.c", "mem_cgroup_resize_max",
         ["if (memcg_is_dying(memcg))"], []),
        ("mm/memcontrol.c", "mem_cgroup_force_empty",
         ["if (memcg_is_dying(memcg))"], []),
        ("mm/memcontrol.c", "memory_reclaim",
         ["if (memcg && memcg_is_dying(memcg))"], []),
    ],
    "core:buddyinfo_nolock": [
        ("mm/vmstat.c", "frag_show",
         ["walk_zones_in_node(m, pgdat, true, true, frag_show_print);"],
         ["walk_zones_in_node(m, pgdat, true, false, frag_show_print);"]),
    ],
    "core:pagealloc_batch_clear": [
        ("mm/page_alloc.c", "kernel_init_free_pages",
         ["kasan_disable_current();",
          "if (!IS_ENABLED(CONFIG_HIGHMEM)) {",
          "memset(kasan_reset_tag(page_address(page)), 0,",
          "(unsigned long)numpages * PAGE_SIZE);",
          "clear_highpage(page + i);",
          "kasan_enable_current();"],
         []),
    ],
    "core:mglru_reclaim_loop_rework": [
        ("mm/vmscan.c", "lru_gen_shrink_lruvec",
         ["bool should_age = false;",
          "nr_to_scan = get_nr_to_scan(lruvec, sc, memcg, swappiness);",
          "while (nr_to_scan > 0) {",
          "if (should_run_aging(lruvec, max_seq, sc, swappiness)) {",
          "nr_batch = min_t(unsigned long, nr_to_scan, MIN_LRU_BATCH);",
          "if (root_reclaim(sc) && should_age)",
          "nr_to_scan -= delta;"],
         ["goto done;", "while (true)", "*need_aging"]),
        ("mm/vmscan.c", "get_nr_to_scan",
         ["struct mem_cgroup *memcg, int swappiness)",
          "evictable = lruvec_evictable_size(lruvec, swappiness);",
          "if (!mem_cgroup_online(memcg))\n\t\treturn evictable;",
          "return evictable >> sc->priority;"],
         ["should_run_aging", "need_aging"]),
        ("mm/vmscan.c", "should_run_aging",
         ["struct scan_control *sc, int swappiness)",
          "if (sc->priority == DEF_PRIORITY)\n\t\treturn false;",
          "return evictable_min_seq(min_seq, swappiness) + MIN_NR_GENS == max_seq;"],
         ["nr_to_scan", "for_each_evictable_type", "lruvec_memcg"]),
        ("mm/vmscan.c", "scan_pages",
         ["unsigned long remaining = nr_to_scan;",
          "VM_WARN_ON_ONCE(nr_to_scan > MAX_LRU_BATCH);",
          "\t*isolatedp = isolated;\n\treturn scanned;\n}"],
         ["int remaining = MAX_LRU_BATCH;"]),
        ("mm/vmscan.c", "isolate_pages",
         ["int total_scanned = 0;",
          "scanned = scan_pages(lruvec, sc, type, tier, nr_to_scan, list, isolated);",
          "\t\tif (!scanned)\n\t\t\ttype = !type;"],
         ["*type_scanned"]),
        ("mm/vmscan.c", "evict_pages",
         ["try_to_inc_min_seq(lruvec, swappiness);",
          "\t/* Scanning may have emptied the oldest gen, flush it */\n"
          "\tif (scanned)\n\t\ttry_to_inc_min_seq(lruvec, swappiness);",
          "unsigned long nr_to_scan, bool *need_swapping)"],
         ["scanned = 0;", "scanned += try_to_inc_min_seq"]),
        ("mm/vmscan.c", "try_to_inc_min_seq",
         [],
         ["bool success", "return success;"]),
        ("mm/vmscan.c", "age_lruvec",
         ["need_aging = should_run_aging(lruvec, max_seq, sc, swappiness);",
          "evictable = lruvec_evictable_size(lruvec, swappiness);"],
         ["&nr_to_scan"]),
    ],
    "core:mglru_dirty_reclaim_rework": [
        ("mm/vmscan.c", "evict_pages",
         ["if (stat.nr_unqueued_dirty == isolated) {",
          "\t\twakeup_flusher_threads(WB_REASON_VMSCAN);\n\t}"],
         ["sc->nr.unqueued_dirty", "sc->nr.file_taken"]),
        ("mm/vmscan.c", "sort_page",
         ["sailboat_mglru_dirty_reclaim_rework"],
         ["waiting for writeback", "file_taken", "page_inc_gen(lruvec, page, true)"]),
        ("mm/vmscan.c", "page_inc_gen",
         ["new_flags |= (new_gen + 1UL) << LRU_GEN_PGOFF;"],
         ["reclaiming", "BIT(PG_reclaim)"]),
        ("mm/vmscan.c", "isolate_page",
         ["\tClearPageReferenced(page);"],
         ["ClearPageReclaim"]),
    ],
    "core:mglru_prefault_accessed_placement": [
        ("mm/swap.c", "lru_cache_add",
         ["if (PageWorkingset(page)) {",
          "trace_android_vh_lru_cache_add_page_activate(page, &bypass);",
          "mark_page_accessed(page);"],
         []),
        ("mm/workingset.c", "lru_gen_refault",
         ["if (lru_gen_in_fault())\n"
          "\t\t\tmod_lruvec_state(lruvec, WORKINGSET_ACTIVATE_BASE + type, delta);",
          "SetPageWorkingset(page);"],
         ["\tmod_lruvec_state(lruvec, WORKINGSET_ACTIVATE_BASE + type, delta);\n\n"]),
    ],
    "core:kcompressd_writeback_pin": [
        ("mm/page_io.c", "swap_writepage",
         ["sailboat_kcompressd_wb_pin",
          "\t\tset_page_writeback(page);\n"
          "\t\tunlock_page(page);\n"
          "\t\treturn 0;\n"],
         ["if (!trylock_page(page)) {"]),
        ("mm/page_io.c", "abk_kcompressd_do_swapout",
         ["sailboat_kcompressd_wb_pin",
          "\tlock_page(page);\n",
          "end_page_writeback(page);",
          "end_page_writeback(page);\n\t\tput_page(page);"],
         ["if (!trylock_page(page)) {",
          "kcompressd: skipping a locked page"]),
    ],
    "core:smaps_migration_guard": [
        ("fs/proc/task_mmu.c", "abk_smaps_migration_page",
         ["if (!PageLocked(p))",
          "if (!is_migration_entry(entry))",
          "p = pfn_to_page(swp_offset(entry));"],
         ["pfn_swap_entry_to_page"]),
        ("fs/proc/task_mmu.c", "smaps_pte_entry",
         ["page = abk_smaps_migration_page(swpent);"],
         []),
        ("fs/proc/task_mmu.c", "smaps_pmd_entry",
         ["page = abk_smaps_migration_page(entry);"],
         []),
        ("fs/proc/task_mmu.c", "smaps_hugetlb_range",
         ["page = abk_smaps_migration_page(swpent);"],
         []),
        ("fs/proc/task_mmu.c", "pte_to_pagemap_entry",
         ["page = abk_smaps_migration_page(entry);"],
         []),
        ("fs/proc/task_mmu.c", "pagemap_pmd_range",
         ["page = abk_smaps_migration_page(entry);"],
         []),
    ],
}

REQUIRED_CONTENT_IF_PRESENT = {
    "core:vm_kcompressd_swapout": (
        "mm/page_io.c",
        "trace_android_vh_shrink_page_lock_owner_clear(page);",
        [
            "trace_android_vh_shrink_page_lock_owner_clear(page);",
        ],
    ),
}


def function_body(text, name):
    bodies = []
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if line.startswith((" ", "\t", "#", "*", "/")) or f"{name}(" not in line:
            continue
        head = line.split(f"{name}(")[0]
        if head and not head.endswith(("*", " ")):
            continue
        brace_start = None
        for scan in range(index, min(index + 20, len(lines))):
            if lines[scan].rstrip().endswith(";"):
                break
            if lines[scan].lstrip().startswith("{"):
                brace_start = scan
                break
        if brace_start is None:
            continue
        for end in range(brace_start + 1, len(lines)):
            if lines[end].startswith(("}", "};")):
                bodies.append("\n".join(lines[index:end + 1]))
                break
    return "\n".join(bodies) if bodies else None


def fail(msg):
    print(f"AUDIT FAIL: {msg}")
    sys.exit(1)


def run_tree(source):
    src = Path(source)
    groups = list(abk_stable_core.PATCH_GROUPS) + list(
        abk_stable_perf.PATCH_GROUPS) + list(abk_stable_display.PATCH_GROUPS)
    all_files = sorted({f for g in groups for f in g.files})

    with tempfile.TemporaryDirectory(prefix="abk_impl_audit_") as tmp:
        root = Path(tmp) / "common"
        for rel in all_files:
            sp = src / rel
            if not sp.is_file():
                fail(f"source tree is missing {rel}")
            dp = root / rel
            dp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(sp, dp)

        problems = []
        for child, module in (("core", abk_stable_core),
                              ("perf", abk_stable_perf),
                              ("display", abk_stable_display)):
            ctx = GraftContext(root, sublevel_matrix.DEFAULT_SUB_LEVEL,
                               "android13-5.15",
                               defconfig=str(root /
                                             "arch/arm64/configs/gki_defconfig"))
            for group in module.PATCH_GROUPS:
                before = {rel: ctx.read(rel)
                          for rel in ctx.pending_writes()}
                try:
                    status, detail = group.apply_fn(ctx)
                except Exception as exc:      # noqa: BLE001 - audit report
                    status, detail = "error", repr(exc)[:200]
                changed = [rel for rel in ctx.pending_writes()
                           if ctx.read(rel) != before.get(rel)]

                if status in ("applied", "partial") and not changed:
                    problems.append(f"{child}/{group.key}: reported {status} "
                                    "without changing any file")
                key = f"{child}:{group.key}"
                if key in REQUIRED_CONTENT and status in ("applied", "partial"):
                    blob = "".join(ctx.read(f) for f in group.files
                                   if ctx.path(f).exists())
                    missing = []
                    for needle in REQUIRED_CONTENT[key]:
                        if isinstance(needle, (list, tuple)):
                            rel, sub = needle
                            if not (ctx.path(rel).exists()
                                    and sub in ctx.read(rel)):
                                missing.append(f"{sub!r} not in {rel}")
                        elif needle not in blob:
                            missing.append(needle)
                    if missing:
                        problems.append(f"{child}/{group.key}: missing "
                                        f"feature content {missing}")
                key = f"{child}:{group.key}"
                if key in REQUIRED_CONTENT_IF_PRESENT and \
                        status in ("applied", "partial"):
                    rel, prerequisite, needles = REQUIRED_CONTENT_IF_PRESENT[key]
                    pristine = src / rel
                    if pristine.is_file() and prerequisite in \
                            pristine.read_text(encoding="utf-8", errors="replace"):
                        blob = ctx.read(rel) if ctx.path(rel).exists() else ""
                        absent = [n for n in needles if n not in blob]
                        if absent:
                            problems.append(
                                f"{child}/{group.key}: the {rel} shape carries "
                                f"the prerequisite but the graft dropped {absent}")
                if key in REQUIRED_ABSENT and status in ("applied", "partial"):
                    blob = "".join(ctx.read(f) for f in group.files
                                   if ctx.path(f).exists())
                    present = []
                    for needle in REQUIRED_ABSENT[key]:
                        if isinstance(needle, (list, tuple)):
                            rel, sub = needle
                            if (ctx.path(rel).exists()
                                    and sub in ctx.read(rel)):
                                present.append(f"{sub!r} in {rel}")
                        elif needle in blob:
                            present.append(needle)
                    if present:
                        problems.append(f"{child}/{group.key}: removed "
                                        f"content survived {present}")

                if key in REQUIRED_IN_FUNCTION and status in ("applied", "partial"):
                    for rel, fn_name, must_have, must_not_have in REQUIRED_IN_FUNCTION[key]:
                        if not ctx.path(rel).exists():
                            continue
                        text = ctx.read(rel)
                        body = function_body(text, fn_name)
                        if not body:
                            problems.append(f"{child}/{group.key}: function "
                                            f"{fn_name} not found in {rel}")
                            continue
                        for needle in must_have:
                            if needle not in body:
                                problems.append(f"{child}/{group.key}: {fn_name} "
                                                f"missing required text: {needle!r}")
                        for needle in must_not_have:
                            if needle in body:
                                problems.append(f"{child}/{group.key}: {fn_name} "
                                                f"has forbidden text: {needle!r}")

                if key in REQUIRED_PAIRING and status in ("applied", "partial"):
                    for crel, cneedle, drel, dneedle, why in REQUIRED_PAIRING[key]:
                        if not (ctx.path(crel).exists() and ctx.path(drel).exists()):
                            continue
                        if cneedle in ctx.read(crel) and dneedle not in ctx.read(drel):
                            problems.append(
                                f"{child}/{group.key}: {cneedle!r} in {crel} has no "
                                f"counterpart {dneedle!r} in {drel} -- {why}")

                if status in ("applied", "partial") and \
                        not any(MARKER in ctx.read(f) for f in changed):
                    print(f"  info: {child}/{group.key}: no module marker "
                          "(upstream-shape idempotency)")
                print(f"  {child:5s} {group.key:36s} {status}")

        problems.extend(config_gate_scope_problems(src, root))

        return problems


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: implementation_audit.py <kernel-tree>")
    problems = run_tree(sys.argv[1])
    if problems:
        fail("; ".join(problems))
    print("IMPLEMENTATION AUDIT OK")


if __name__ == "__main__":
    main()
