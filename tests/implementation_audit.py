#!/usr/bin/env python3
"""Implementation audit: no phantom grafts, no missing feature content.

step_audit.py proves every anchor lands with correct structure and
idempotency; this companion proves the *content* of a graft is real:

- a group that reports applied/partial must have changed at least one file
  (content diff, not path-set diff: several groups rewrite the same file);
- the 6.1/6.6-origin groups must leave their headline feature symbols in the
  patched tree;
- groups whose upstream shape is the whole point (zsmalloc chain sizing,
  MADV_COLLAPSE) must leave their behaviour-visible symbols behind.

Everything runs against a disposable copy of the source tree: the reference
trees are never written to.

Usage:
  python tests/implementation_audit.py /path/to/android13-5.15-tree
"""

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

MARKER = "ABK stable_515_backport:"

# A module-introduced line may only reference a symbol the tree hides behind a
# CONFIG gate if the line sits inside that same gate.  The zram writeback fields
# are the case that bit (Batch 23): struct zram declares bdev / backing_dev /
# wb_compressed -- and struct zram_stats its bd_* counters -- inside
# CONFIG_ZRAM_WRITEBACK, and ABK's dispatch does not have to enable that symbol
# (the module's own ROM tier is the only place that does, and it is off by
# default).  The Batch-17 compressed-writeback helpers were first added outside
# the guard, so every build that left the option off died with "no member named
# 'bdev' in 'struct zram'" while all four text audits stayed green -- they only
# ever ran against a tree, never through a preprocessor.
CONFIG_GATED_REFERENCES = {
    "drivers/block/zram/zram_drv.c": [
        ("CONFIG_ZRAM_WRITEBACK",
         ("zram->bdev", "zram->backing_dev", "zram->wb_limit_lock",
          "zram->wb_limit_enable", "zram->bd_wb_limit", "zram->bitmap",
          "zram->nr_pages", "zram->wb_compressed", "zram->stats.bd_",
          "abk_zram_bvec_read(")),
    ],
}


def _ifdef_regions(lines):
    """For each line, the conditional expressions enclosing it.

    A stack of #if/#ifdef/#ifndef lines, popped on #endif.  #else/#elif are
    ignored on purpose: what matters here is whether the gate is *anywhere*
    around the line, and both branches of a gate are inside it.
    """
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
    """Added lines that use a config-gated symbol outside that symbol's gate."""
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

# Feature content that must survive into the patched text wherever the group
# reports applied.  Keyed as "child:group".
REQUIRED_CONTENT = {
    "core:zram_recompression": [
        "zram_recompress", "recompress_store", "mark_idle",
        "num_active_comps", "zs_lookup_class_index",
        # Semantic checks to prevent empty implementation regression:
        "for (prio = 0; prio < ZRAM_MAX_COMPS; prio++)",  # disksize_store loop
        "zram_set_priority(zram, index, 0)",  # zram_free_page priority reset
    ],
    "core:memcg_memory_reclaim": ["memory.reclaim", "MEMCG_RECLAIM_MAY_SWAP"],
    "core:zsmalloc_chain_size": [
        "calculate_zspage_chain_size", "ZSMALLOC_CHAIN_SIZE", "is_power_of_2",
        # The chain-size ceiling must also widen the isolated-subpage counter,
        # or an 8-page zspage overflows it and double-adds on putback.
        "#define ISOLATED_BITS\t5",
    ],
    "core:madvise_collapse": [
        "madvise_collapse", "MADV_COLLAPSE",
        "khugepaged_scan_pmd(mm, vma, addr, &hpage, &result)",
        "khugepaged_scan_file(mm, file, pgoff, &hpage, &result)",
        # Both scan_file definitions must have moved to the out-parameter form;
        # the CONFIG_SHMEM-off stub is deliberately wrapped differently so it
        # cannot pre-create this text (see abk_stable_core.py).
        "struct file *file, pgoff_t start, struct page **hpage,\n\t\tint *res)",
        "pgoff_t start, struct page **hpage, int *res)",
        # 5.15 hugepage_vma_revalidate() returns 0 on success, so the graft must
        # test it as a plain scan code, not with 6.1's `!= SCAN_SUCCEED`
        # (SCAN_SUCCEED is 1 here, so that inverts the success test).
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
        # Both android vendor-hook callbacks must really be registered (a graft
        # that only adds the Kconfig entry but no policy would be a phantom).
        "register_trace_android_vh_ra_tuning_max_page(",
        "register_trace_android_vh_tune_mmap_readaround(",
        "core_initcall(abk_dra_init);",
        "abk_dra_is_background_task",
        "ABK stable_515_backport: dynamic readahead (Batch 9-1).",
    ],
    # Batch 15 (ABK_ABI_PATCH_SUITE absorption).  The suite guarded every edit
    # with `if old in text:` + `text.replace(old, new, 1)`, so a drifted anchor
    # was a silent no-op reported as success.  These pin the behaviour that
    # actually lands, so that failure mode cannot come back through this port.
    "core:pid_alloc_hotpath_phase2": [
        # Both halves of the retry: the label it jumps to, and the latch that
        # makes it single-shot per pid-namespace level.
        "retry_preload:",
        "retried_preload = true;",
        "retried_preload = false;",
    ],
    "core:fd_alloc_hotpath": [
        # Only the precheck half is ported; REQUIRED_ABSENT pins the half that is
        # deliberately not, and why.
        "abk_expand_files_needed",
    ],
    "core:close_range_hotpath": [
        "ABK stable_515_backport: close_range_hotpath.",
    ],
    "core:slab_alloc_free_hotpath": [
        # The shared helper plus both alloc paths and the single-resolution free.
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
        # The 5.15 convention trap this port had to respect: khugepaged_enter()
        # returns int here (non-zero = failure, include/linux/khugepaged.h:56),
        # where 6.1 renamed it khugepaged_enter_vma() and returns void.  The
        # int is propagated as VM_FAULT_OOM; see REQUIRED_ABSENT for the form
        # that must not appear.
        "khugepaged_enter(vma, vma->vm_flags)",
    ],
    "perf:sched_eevdf_core_fields": [
        # The KABI claim itself -- this is the group that takes ownership of the
        # sched_entity slots the retired red line used to forbid.  Four scalars,
        # so each ANDROID_KABI_USE's size/alignment static assert holds and
        # sizeof(struct sched_entity) is unchanged.  Batch 16 released slot 4
        # (see REQUIRED_ABSENT for the packing that must not come back); Batch 28
        # re-claimed it for real as ``u64 slice``, which update_deadline(), the
        # EEVDF yield and PREEMPT_SHORT all read.
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
        # The Batch 28 accumulators.  Without them avg_vruntime() falls back to
        # walking the tree, which is the quadratic cost the rebuild removed.
        "abk_entity_key",
        "abk_avg_vruntime_add",
        "abk_avg_vruntime_sub",
        "abk_avg_vruntime_update",
        # The policy rules Batch 28 added on top of the Batch 15 payload.
        "sched_feat(RUN_TO_PARITY)",
        "abk_eevdf_preempt_short",
        "abk_eevdf_lag_limit",
        # Definitions alone would pass even if nothing called them: these are the
        # call sites that make the runtime state live.  They are also what
        # sched_eevdf_core_fields' and sched_eevdf_modern_fields' anti-drift
        # gates probe before they will claim the KABI slots / add the cfs_rq
        # fields.
        "abk_avg_vruntime_add(cfs_rq, se);",
        "abk_eevdf_refresh_deadline(cfs_rq, curr);",
        "return abk_pick_eevdf(cfs_rq, curr);",
        "abk_eevdf_place_entity(cfs_rq, se, initial);",
        "cfs_rq->abk_pick_deadline = se->deadline;",
        "EEVDF runtime-state graft (Batch 28 shape)",
        # Batch 28 repair: upstream nulls an ineligible curr before its
        # run-to-parity test.  Without it run-to-parity also hands the CPU to
        # an ineligible current, which hides the skip buddy that
        # yield_task_fair() sets, so sched_yield() ends up weaker than stock CFS.
        "!abk_eevdf_eligible(curr, avruntime)",
        # The null-safety guarantee, and the reason it is pinned here: ACK
        # 1119609dce0875 ("ANDROID: if EEVDF scheduling fail, picking leftmost,
        # to avoid NULL pointer") is the downstream mitigation for a NULL deref
        # in pick_next_entity().  pick_next_task_fair() hands the result
        # straight to group_cfs_rq(), so a NULL there is a fatal fault, not a
        # fallback to idle.  The overflow that triggers it upstream
        # (vruntime_eligible()'s ``key * load``) cannot occur here --
        # docs/survey_eevdf_gap.md 4.3 shows abk_eevdf_eligible() is a
        # subtraction and a sign test -- but the selector must still never
        # return NULL when it finds no eligible entity.  This line is the whole
        # of that guarantee, the ACK patch's hunk 1 in effect, and it is one
        # edit away from being lost.
        "best = curr && curr->on_rq ? curr : __pick_first_entity(cfs_rq);",
        # ...and the guards that preserve a userspace-provided request size.
        "if (!se->slice)",
        # The !EEVDF halves.  See REQUIRED_PAIRING for why they are required.
        "if (sched_feat(EEVDF))",
        "if (!sched_feat(EEVDF) && wakeup_preempt_entity(se, pse) == 1)",
    ],
    "perf:sched_eevdf_modern_fields": [
        # The cfs_rq side of the rebuild: the two accumulators that make
        # avg_vruntime() O(1) and the run-to-parity stash, plus the two policy
        # switches.  struct cfs_rq is not an exported type, so these three
        # fields cost no KMI.
        "s64\t\t\tavg_vruntime;",
        "u64\t\t\tavg_load;",
        "u64\t\t\tabk_pick_deadline;",
        # Batch 28 repair: without a producer, se->slice can only ever hold the
        # global default, and abk_eevdf_preempt_short() is a tautology.
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
        # wake_avg_idle is retired and the SIS_PROP scan budget is re-sourced
        # from rq->avg_idle directly.  Both strings are absent from the pristine
        # tree, so they prove the mode really flipped rather than the group
        # no-op'ing.
        "ABK stable_515_backport: avg_idle preemption mode simplification.",
        "avg_idle = this_rq->avg_idle / 2;",
        # The suite sampled unconditionally: `delta = rq_clock(rq) -
        # rq->idle_stamp`.  On 5.15 idle_stamp is armed ONLY by
        # newidle_balance(), so a CPU sitting in the idle task without having
        # passed that path (boot CPU, idle->idle repick) has idle_stamp == 0 and
        # the unconditional form samples nanoseconds-since-boot, clamping
        # straight to 2*max_idle_balance_cost.  Pristine ttwu_do_wakeup() had
        # exactly this guard.
        "if (!rq->idle_stamp)",
    ],
    "perf:blk_mq_async_depth": [
        # The new limit_depth op and its routing through alloc_request.
        "static void blk_mq_limit_depth(unsigned int opf, struct blk_mq_alloc_data *data)",
        "e->type->ops.limit_depth(opf, data);",
        "limit_depth = blk_mq_limit_depth;",
        # The two CONVERTED consumers.  Do not "simplify" these back to the
        # suite's raw assignments: q->async_depth is a REQUEST count while
        # kqd->async_depth / bfqd->word_depths[][] are PER-WORD BIT caps
        # (sbitmap_queue_get_shallow() caps bits within one word), so the raw
        # assignment is >= one word on a 256-deep queue and never throttles --
        # dead code.  See REQUIRED_ABSENT.
        "kqd->async_depth = ((q->async_depth << shift) + q->nr_requests - 1) /",
        "depth = ((bfqd->queue->async_depth << bt->sb.shift) +",
    ],
    "perf:psi_trigger_kernfs_polling": ["psi_trigger_ext", "pending_event"],
    "perf:psi_irq_tracking": ["PSI_IRQ"],
    "perf:psi_cgroup_pressure_switch": [
        # The switch itself: a cgroup flag bit, the query the accounting paths
        # ask, the re-enable sync and the knob that drives them.
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
        # The trigger writer has to have yielded the name, or the base-file entry
        # above would route cgroup.pressure writes into the trigger path.
        "static ssize_t pressure_write(struct kernfs_open_file *of, char *buf,",
        "\treturn pressure_write(of, buf, nbytes, PSI_IO);",
        "\treturn pressure_write(of, buf, nbytes, PSI_MEM);",
        "\treturn pressure_write(of, buf, nbytes, PSI_CPU);",
        # User-visible, so documented.
        "  cgroup.pressure\n",
    ],
    "perf:psi_oncpu_state_mask": [
        # ONCPU is a flag in the state mask, and the four counters are all that
        # is left of psi_group_cpu::tasks[].
        "NR_PSI_TASK_COUNTS = 4,",
        "#define TSK_ONCPU\t(1 << NR_PSI_TASK_COUNTS)",
        "#define PSI_ONCPU\t(1 << NR_PSI_STATES)",
        "static bool test_state(unsigned int *tasks, enum psi_states state, bool oncpu)",
        "return unlikely(tasks[NR_RUNNING] > oncpu);",
        "return unlikely(tasks[NR_RUNNING] && !oncpu);",
        # The flag is set/cleared/carried before the counts, and asked for in
        # every place the counter used to be.
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
        "kthread_create_worker(0, \"zram_recompd\")",
        "recompress_async",
        "dev_attr_recompress_async.attr",
        # The reset drain call is folded into the recompression group's reset
        # text; make sure it really invokes the async teardown entry point.
        "abk_zram_recomp_drain(zram);",
    ],
    "perf:schedutil_smart_policy": [
        "Batch 10-4 sched smart-freq policy (PELT)",
        "abk_sf_enable",
        "register_trace_android_vh_scheduler_tick(abk_sf_tick, NULL)",
        "register_trace_android_vh_cpufreq_resolve_freq(abk_sf_resolve_freq,",
        "late_initcall(abk_sf_init)",
        # Sampling must be governor-independent: the util read happens in the
        # scheduler tick, not in a schedutil-only vendor hook (the device runs
        # the vendor walt governor, which never reaches the schedutil path).
        "cpu_util_cfs(rq)",
        "arch_scale_cpu_capacity(rq->cpu)",
        # Batch 10-5 ownership fix.  The floor is a *ratchet* on any policy
        # whose owner drives cpufreq by pinning min == max == its own target
        # (vendor FAS/WALT): the clamp to policy->max turns "raise to the
        # floor" into "keep the frequency already applied" and every downscale
        # is swallowed.  Measured on kalama: waltgov asked for 766 MHz while
        # the policy held 1785600 for a whole game session.  All of the
        # following must be present or the ratchet is back.
        "static bool abk_sf_enable = false;",
        "#include <linux/string.h>",
        "static bool abk_sf_dvfs_owned(struct cpufreq_policy *policy)",
        'strcmp(policy->governor->name, "schedutil") != 0',
        "if (abk_sf_dvfs_owned(policy) || policy->min == policy->max)",
        "if (floor <= policy->min || floor >= policy->max)",
        # The reason must expire on a wall-clock deadline as well as in the
        # tick, and the sustained window must actually slide; plus a read-only
        # view of the reason state so the next diagnosis is not blind.
        "unsigned long boost_release;",
        "static bool abk_sf_cpu_boosting(int cpu)",
        "module_param_cb(abk_sf_boosting, &abk_sf_boosting_ops, NULL, 0444);",
    ],
    "core:zram_secondary_comp": [
        "Batch 10-4 secondary zram compressor",
        "abk_zram_recomp_algo",
        "module_param_string(abk_recomp_algo",
        # The secondary slot must really be filled, or recompression stays the
        # silent no-op this group exists to fix.
        "comp_algorithm_set(zram, ZRAM_SECONDARY_COMP, abk_alg);",
        # The secondary is zstd (measured best ratio-per-cost) and the parameter
        # is read-only: the target ROM's own userspace daemon forced the
        # dominated lz4hc primary before disksize, so a writable parameter
        # would only invite the same choice back.
        'static char abk_zram_recomp_algo[CRYPTO_MAX_ALG_NAME] = "zstd";',
        "sizeof(abk_zram_recomp_algo), 0444);",
    ],
    "core:zram_algo_lock": [
        "Batch 11 - the zram algorithm lock",
        # Read-only policy parameters: the measured primary and the lock switch.
        'static char abk_zram_comp_algo[CRYPTO_MAX_ALG_NAME] = "lz4kd";',
        "module_param_string(abk_comp_algo, abk_zram_comp_algo",
        # Two names on purpose: the knob is zram.abk_lock_algo, the variable is
        # abk_zram_lock_algo, and plain module_param() refuses that split --
        # it fails the compile with "use of undeclared identifier".
        "module_param_named(abk_lock_algo, abk_zram_lock_algo, bool, 0444);",
        "static bool abk_zram_lock_algo = true;",
        # The primary must be selected at creation, before any disksize write.
        "comp_algorithm_set(zram, ZRAM_PRIMARY_COMP,",
        # Both nodes must really be repointed, and the locked store must accept
        # the write (a refused algorithm write aborts Android's mmd_setup,
        # writeback setup included -- the exclusivity this batch removes).
        "dev_attr_comp_algorithm.store = abk_zram_locked_algo_store;",
        "dev_attr_recomp_algorithm.store = abk_zram_locked_algo_store;",
        "late_initcall(abk_zram_algo_lock_init)",
        "return len;",
    ],
    "core:memcg_v1_reclaim": [
        "Batch 10-4 cgroup-v1 proactive reclaim",
        # Forward declaration must precede the legacy table, and that table
        # must carry the entry, or v1 devices have no memory.reclaim at all.
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
        # All three upstream hunk lines must really land (declare / call /
        # export); a hook that is declared but never called is inert, and one
        # that is called but not exported cannot be reached by a module.
        "DECLARE_HOOK(android_vh_customize_alloc_gfp,",
        "trace_android_vh_customize_alloc_gfp(&alloc_gfp, order);",
        "EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_customize_alloc_gfp);",
    ],
    "core:gfp_pressure_fastfail": [
        "Batch 13 high-order slowpath fast-fail",
        "register_trace_android_vh_customize_alloc_gfp(",
        "*gfp |= __GFP_NORETRY | __GFP_NOWARN;",
        "late_initcall(abk_gfp_fastfail_init);",
        # The THP-class default: order 9 on 4K-page arm64.
        "static unsigned int abk_gfp_fastfail_order = 9;",
    ],
    # --- Batch 14: zram writeback correctness ------------------------------
    "core:zram_wb_teardown": [
        # 74363ec674cb.  The NULL guard is the load-bearing half: zram_remove()
        # now always reaches reset_bdev(), and zram_meta_free() may be entered
        # for a device that never had a table/pool.
        "if (!zram->table)\n\t\treturn;",
        "zram->table = NULL;",
        # The leak close itself, in zram_remove() -- not by deleting
        # zram_reset_device()'s early return, which zram_recompression owns.
        "\tzram_reset_device(zram);\n\t/*\n\t * ABK stable_515_backport: 74363ec674cb.",
        "\treset_bdev(zram);\n\n\tpr_info(\"Removed device: %s\\n\"",
    ],
    "core:zram_writeback_bounds": [
        # 894913e2d35c: the bound and the range check must both be derived under
        # the read lock, not before it.
        "ABK stable_515_backport: 894913e2d35c",
        "\tunsigned long nr_pages;\n\tunsigned long index = 0;",
        # The marker is load-bearing for unambiguity: the bare
        # `nr_pages = ...; if (index >= nr_pages) {` sequence is the *upstream*
        # 2026 shape, so a baseline that ever backports 894913e2d35c itself
        # would satisfy a marker-less needle while this group reports
        # already_present.  Anchor on the comment the step actually writes.
        "\t * freed table and the loop would index past the new one.\n"
        "\t */\n"
        "\tunsigned long nr_pages;",
        # ...and the post-lock region still derives the bound and re-checks.
        "ABK stable_515_backport: 894913e2d35c -- bound and range check",
        "\tnr_pages = zram->disksize >> PAGE_SHIFT;\n"
        "\tif (index >= nr_pages) {\n"
        "\t\tret = -EINVAL;\n"
        "\t\tgoto release_init_lock;",
        # 424d0e5828ad: the reschedule point in the sweep.
        "\t\tcond_resched();",
    ],
    "core:zram_wb_limit_align": [
        # Mainline writeback_limit_store()'s alignment guard.
        "val = rounddown(val, PAGE_SIZE / 4096);",
    ],
    # --- Batch 17: zram writeback batching + compressed writeback ----------
    "core:zram_writeback_batching": [
        # f405066a1f0d.  The point of the change is the *absence* of the
        # synchronous single-page submit (asserted in REQUIRED_ABSENT); these
        # pin the in-flight machinery that replaces it.
        "struct zram_wb_ctl", "struct zram_wb_req",
        "zram_writeback_endio", "zram_submit_wb_request",
        "zram_complete_done_reqs", "zram_select_idle_req",
        "atomic_read(&wb_ctl->num_inflight)", "wait_event(wb_ctl->done_wait",
        # bf62f69574b1: the UAF fix must be in the shipped shape, not "fixed
        # later" -- kfree_rcu() plus an RCU read section in the callback.
        "kfree_rcu(wb_ctl, rcu)", "rcu_read_lock()",
        # 3e8d8eb8d7f5: the reserved-but-unused index is released before the
        # drain, and a recycled request cannot free the slot block twice.
        "free_block_bdev(zram, blk_idx)", "req->blk_idx = 0;",
        # 5.15 re-anchor: ZRAM_UNDER_WB/ZRAM_IDLE are the in-flight protocol
        # that keeps recompression and idle marking off the slot.
        "zram_set_flag(zram, index, ZRAM_UNDER_WB)",
        "zram_clear_flag(zram, index, ZRAM_UNDER_WB)",
        "zram_set_element(zram, index, req->blk_idx)",
        # d38fab605c66 write half + 3bf1c285dc40: raw object copy with the
        # 5.15 mapping API (no zs_obj_read_begin), trailing bytes zeroed.
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
        # d38fab605c66 read half: the dispatcher and its deferred
        # decompression, plus the attribute of 4c1d61389e8e/ba4c3698e696.
        "abk_zram_bvec_read(", "abk_zram_decompress_bdev_page",
        "abk_zram_deferred_decompress", "system_highpri_wq",
        "bio_inc_remaining(parent)", "compressed_writeback_store",
        "zram->wb_compressed = val;",
        "dev_attr_compressed_writeback.attr",
        # 5.15 conventions, each a compile-or-behave trap if the 6.x patch is
        # copied verbatim: the stream put takes the comp, decompress takes
        # four arguments, and the stale-page path zeroes instead of using
        # 6.19's memset_page().
        "zcomp_stream_put(zram->comps[prio])",
        "zcomp_decompress(zstrm, src, size, zstrm->buffer)",
        "zero_user(page, 0, PAGE_SIZE)",
    ],
    "perf:sched_steal_time_excess_drop": [
        # The drop is only real if the sampled value is remembered *before* the
        # delta clamp and stored back unbounded -- the pre-5.15.179 form adds
        # the clamped steal, which is what re-charges it to the next task.
        "steal = prev_steal = paravirt_steal_clock(cpu_of(rq));",
        "rq->prev_steal_time_rq = prev_steal;",
    ],
    "perf:randomize_kstack_pertask": [
        # "Per-task" is the whole point: both macros must read and write
        # current->kstack_offset instead of the per-CPU variable, and the
        # fork-time initialiser must exist.  The per-CPU definitions the commit
        # removes are pinned in REQUIRED_ABSENT below.
        "u32 offset = current->kstack_offset;",
        "current->kstack_offset = offset;",
        "random_kstack_task_init",
    ],
    "perf:blk_mq_suspend_wakeup_abort": [
        # The abort path is only real if the wait loop can see a pending wakeup,
        # can un-inactivate the hctx again, and can propagate the failure -- the
        # upstream hunk returns -EBUSY from blk_mq_hctx_notify_offline(), whose
        # caller is the CPUHP teardown state.
        "pm_wakeup_pending()",
        "clear_bit(BLK_MQ_S_INACTIVE, &hctx->state);",
        "ret = -EBUSY;",
        "#include <linux/suspend.h>",
    ],
    "perf:blk_mq_quiesced_elevator_switch": [
        # The rename has to land on all three sides at once: the definition,
        # the declaration and both call sites.  Half of it is a link error
        # (blk.h declaring a function nobody defines) or, worse, a non-static
        # elevator_switch_mq() that blk-mq no longer calls while the switch
        # happens through the unquiesced path the group exists to retire.
        "static int elevator_switch_mq(struct request_queue *q,",
        "int elevator_switch(struct request_queue *q, struct elevator_type *new_e);",
        "elevator_switch(q, NULL);",
        "elevator_switch(q, t);",
    ],
    "core:zram_recompress_max_pages": [
        # 34efe1c3b688.  The parameter has to be parsed *into* the counter, the
        # sweep has to test the counter, and both recompress nodes have to carry
        # it -- recompress_async advertises the same grammar, so a cap that only
        # bounds the synchronous pass leaves the companion's default path
        # uncapped.  2f529e73d720's type guard rides along: without it a
        # mistyped type= value means "no filter", the opposite of what the cap
        # is for.
        'if (!strcmp(param, "max_pages")) {',
        "ret = kstrtoull(val, 10, &num_recomp_pages);",
        "u64 num_recomp_pages = ULLONG_MAX;",
        "if (!num_recomp_pages)",
        "num_recomp_pages--;",
        "ABK stable_515_backport: 34efe1c3b688",
        "if (!mode)",
    ],
}

# Removal grafts: content that must NOT survive into the patched text wherever
# the group reports applied.  Keyed as "child:group".  An entry is either a
# bare string (checked against the blob of the group's files, the original
# removal-graft shape) or a [rel, needle] pair (checked against that one
# file only, for absence claims a *neighbouring* group's legitimate content
# in a shared file would otherwise defeat).
REQUIRED_ABSENT = {
    "perf:psi_oncpu_state_mask": [
        # The counter and the old "identical state makes the walk safe" trick
        # have to be gone, not merely bypassed: both were the failure mode.
        ["include/linux/psi_types.h", "NR_ONCPU"],
        ["kernel/sched/psi.c", "tasks[NR_ONCPU]"],
        ["kernel/sched/psi.c", "identical_state"],
    ],
    "perf:psi_cgroup_pressure_switch": [
        # The ACK 6.1 shape grows struct psi_group (bool enabled) and struct
        # cgroup (struct psi_group *psi, psi_files[]).  Both are KMI-visible
        # layouts on android13-5.15, so this port must not have grown them: the
        # state lives in the cgroup's flags word instead.
        ["include/linux/psi_types.h", "\tbool enabled;"],
        ["include/linux/cgroup-defs.h", "struct psi_group *psi;"],
        ["include/linux/cgroup-defs.h", "struct cgroup_file psi_files["],
        # ... and the old trigger-writer name must be gone, or cgroup.pressure
        # would be wired to the trigger path instead of to the switch.
        ["kernel/cgroup/cgroup.c",
         "static ssize_t cgroup_pressure_write(struct kernfs_open_file *of, "
         "char *buf,\n\t\t\t\t\t  size_t nbytes, enum psi_res res)"],
    ],
    "display:drm_valid_clones_revert": [
        "drm_atomic_check_valid_clones",
        "drm_atomic_check_valid_clones(state, crtc)",
    ],
    "perf:schedutil_smart_policy": [
        # The Batch 10-4c shapes: enabled by default, and a floor clamped *up
        # to* policy->max -- the ceiling lock itself.
        "static bool abk_sf_enable = true;",
        "if (floor > policy->max)",
    ],
    "core:customize_alloc_gfp_vh": [
        # Upstream-shape graft: the two files it exists to reproduce must
        # stay marker-free -- an ABK marker on a line upstream also has
        # breaks the already_present short-circuit on a future baseline that
        # carries 4466afd69452 itself.  Pinned per-file on purpose: the
        # group's third file, mm/page_alloc.c, legitimately carries markers
        # from other batches, so a whole-blob sweep can never see this.
        ["include/trace/hooks/mm.h", "ABK stable_515_backport:"],
        ["drivers/android/vendor_hooks.c", "ABK stable_515_backport:"],
    ],
    "core:fd_alloc_hotpath": [
        # The suite's capacity half must never be shipped from this module.
        # `abk_fdtable_slots_wanted` is one of this repo's OWN suite-detection
        # markers (abk_common.SUITE_FD_HELPER, consumed by
        # GraftContext.suite_touched / suite_fdtable_fallback /
        # fdtable_upstream_shape), so emitting it here would flip a second pass
        # to `skip_suite_processed` instead of `already_present`.  The other half
        # of the pair, the suite's ALIGN()-based capacity line, is what
        # fdtable_upstream_shape() tests for.  Pinned per file because fs/file.c
        # legitimately carries this module's other markers.
        ["fs/file.c", "abk_fdtable_slots_wanted"],
        ["fs/file.c", "ALIGN(slots_wanted"],
    ],
    "core:hugepage_fault_alloc_fastpath": [
        # The 6.1 spelling of the khugepaged entry.  5.15 has khugepaged_enter()
        # returning int; copying the 6.1 void-returning khugepaged_enter_vma()
        # would silently drop the failure path (and not compile).  The needle
        # carries the call's open paren on purpose: 5.15 legitimately contains
        # the *different* symbol khugepaged_enter_vma_merge(vma, ...), which a
        # bare "khugepaged_enter_vma" needle matches as a substring.
        ["mm/huge_memory.c", "khugepaged_enter_vma(vma,"],
    ],
    "perf:sched_eevdf_core_fields": [
        # The abandoned packing the suite used to upgrade FROM.  If it survives,
        # two fields share one 8-byte slot and the KABI size assert is wrong.
        ["include/linux/sched.h", "ANDROID_KABI_USE(3, struct {"],
        ["include/linux/sched.h", "ANDROID_KABI_USE(4, struct {"],
        # Batch 16 released slot 4 because the suite's ``u64 slice`` was written
        # once and read nowhere; Batch 28 re-claimed it because the rebuilt
        # payload really does read it.  The *released* form must not come back --
        # the needle carries the ``vlag`` line so it pins the sched_entity tail
        # and not the identical reserve runs in sched_rt_entity / task_struct.
        ["include/linux/sched.h",
         "ANDROID_KABI_USE(3, s64 vlag);\n\tANDROID_KABI_RESERVE(4);"],
    ],
    "perf:sched_eevdf_pick_logic": [
        # The Batch 15 consumer end of the old dead field: the helper wrote
        # se->slice and read its own local instead.  The rebuilt slice has real
        # readers (abk_eevdf_slice() feeds vslice, the deadline, the lag limit
        # and PREEMPT_SHORT), so the discarded-local form must not return.
        ["kernel/sched/fair.c", "se->slice = slice;"],
        # The Batch 15 selector refreshed deadlines while selecting, and walked
        # the tree to do it.  Both are what the rebuild removed.
        ["kernel/sched/fair.c", "abk_eevdf_refresh_deadline(cfs_rq, se);"],
        ["kernel/sched/fair.c", "abk_eevdf_max_slice"],
        ["kernel/sched/fair.c", "abk_eevdf_total_weight"],
    ],
    "perf:nohz_field_refinement": [
        # Batch 16 removed the suite's exported accessor pair and its four
        # per-state predicates.  A consumer sweep over the full GKI tree (52k
        # files) found zero callers for every predicate -- the only callers of
        # nohz_cpu_state_test() were the other three -- and no consumer outside
        # tick-sched.c for either export, so they were dead KMI surface rather
        # than a graft.  The enum and abk_tick_nohz_state_flags() are the live
        # part and stay.
        ["include/linux/sched/nohz.h", "nohz_cpu_state_test"],
        ["include/linux/sched/nohz.h", "nohz_cpu_inidle"],
        ["include/linux/sched/nohz.h", "nohz_cpu_idle_active"],
        ["include/linux/sched/nohz.h", "nohz_cpu_tick_stopped"],
        ["include/linux/sched/nohz.h", "extern unsigned int nohz_cpu_state_flags"],
        ["kernel/time/tick-sched.c", "EXPORT_SYMBOL_GPL(nohz_cpu_state_flags)"],
        ["kernel/time/tick-sched.c", "EXPORT_SYMBOL_GPL(nohz_cpu_idle_calls)"],
    ],
    "perf:blk_mq_async_depth": [
        # The suite's unconverted assignments: a request count written into a
        # per-word bit cap.  Both are the dead-code form this port replaces.
        ["block/kyber-iosched.c", "kqd->async_depth = q->async_depth;"],
        ["block/bfq-iosched.c", "depth = bfqd->queue->async_depth;"],
    ],
    "perf:avg_idle_preemption_mode": [
        # The retired wake-side prediction must be gone from the wake path.  The
        # struct rq fields themselves stay (removing them would move every field
        # after them and break KMI) -- they are simply dead now.
        ["kernel/sched/fair.c", "this_rq->wake_avg_idle"],
    ],
    "core:zram_writeback_batching": [
        # The synchronous single-page submit is exactly the shape this batch
        # replaces.  The needle is the *assignment* form, not the bare symbol:
        # the compressed read-back path (a later group) legitimately waits for
        # its own read bio, so a bare "submit_bio_wait" needle would be a false
        # positive.  The old loop comment is pinned for the same reason.
        ["drivers/block/zram/zram_drv.c", "err = submit_bio_wait(&bio);"],
        ["drivers/block/zram/zram_drv.c",
         "A single page IO would be inefficient for write"],
    ],
    "core:zram_compressed_writeback": [
        # The 6.x spellings of the pieces 5.15 does not have.  A verbatim copy
        # of the upstream patch would drag one of these in, and each is either
        # a build failure here (zs_obj_read_begin/end, local_copy) or a silent
        # behaviour difference (memset_page vs zero_user).  The needles carry
        # their call shape so this file can still *document* the difference in
        # a comment without tripping its own absence assertion.
        ["drivers/block/zram/zram_drv.c", "zs_obj_read_begin(zram->mem_pool"],
        ["drivers/block/zram/zram_drv.c", ", zstrm->local_copy)"],
        ["drivers/block/zram/zram_drv.c", "zcomp_stream_put(zstrm)"],
        ["drivers/block/zram/zram_drv.c", "memset_page("],
    ],
    "perf:sched_steal_time_excess_drop": [
        # The catch-up accumulator this commit removes.
        ["kernel/sched/core.c", "rq->prev_steal_time_rq += steal;"],
    ],
    "perf:randomize_kstack_pertask": [
        # The per-CPU offset the commit exists to remove.  Pinned per file on
        # purpose: init/main.c legitimately keeps its DEFINE_STATIC_KEY_MAYBE_RO()
        # next to it, so a whole-blob sweep of the group's files would be a
        # weaker claim than "the per-CPU variable is gone from *this* file".
        ["include/linux/randomize_kstack.h", "DECLARE_PER_CPU(u32, kstack_offset);"],
        ["init/main.c", "DEFINE_PER_CPU(u32, kstack_offset);"],
    ],
    "perf:blk_mq_suspend_wakeup_abort": [
        # The pre-5.15.198 wait loop (no escape on a pending wakeup).  The needle
        # is the whole tryget/put block, i.e. exactly the anchor the group
        # rewrites, so it cannot false-positive on a lookalike loop elsewhere in
        # the file.
        ["block/blk-mq.c",
         "\tif (percpu_ref_tryget(&hctx->queue->q_usage_counter)) {\n"
         "\t\twhile (blk_mq_hctx_has_requests(hctx))\n\t\t\tmsleep(5);\n"
         "\t\tpercpu_ref_put(&hctx->queue->q_usage_counter);\n\t}"],
    ],
    "perf:blk_mq_quiesced_elevator_switch": [
        # The pre-5.15.209 spelling must be gone from both ends that matter:
        # the call sites in blk-mq.c and the declaration in blk.h.  (elevator.c
        # keeps the symbol itself, demoted to static.)
        ["block/blk-mq.c", "elevator_switch_mq"],
        ["block/blk.h", "elevator_switch_mq"],
    ],
    "core:zram_recompress_max_pages": [
        # The cap counts *attempts*.  A decrement above the candidate filters --
        # which is what "charge it right after taking the slot" would be -- makes
        # the sweep stop after max_pages slots were *looked at*, so on a device
        # whose idle pages are sparse it recompresses far less than asked.
        ["drivers/block/zram/zram_drv.c",
         "\t\tnum_recomp_pages--;\n\t\tzram_slot_lock(zram, index);"],
    ],
}

# Function-scoped assertions.  REQUIRED_CONTENT above is whole-file substring
# matching, which cannot tell *which* function a graft landed in -- a swapped
# pair of anchors satisfies it perfectly.  These entries slice out one function
# body and assert on that slice, so a hunk landing in the neighbouring function
# fails the audit.  Keyed as "child:group" -> list of
# (rel, function_name, must_contain, must_not_contain).
# Cross-file invariants: a switch is only a switch if BOTH branches exist, and
# a comparison is only a comparison if the thing it compares can differ.
#
# This table exists because nothing else in this file could see the failure it
# catches.  Batch 28 first shipped `SCHED_FEAT(PREEMPT_SHORT, true)` wired to a
# tautology: se->slice had two writers (both the same global constant) and a
# reader whose zero-fallback was that same constant, so
# `abk_eevdf_slice(pse) >= abk_eevdf_slice(se)` was X >= X and the guard always
# rejected.  Every check passed -- the symbol existed in System.map, the feature
# was enabled, the field was read on a hot path.  Presence is not liveness, and
# a switch that cannot change anything is worse than an absent one because it
# reads as a control.
#
# Rows are (condition_rel, condition_needle, consequence_rel, consequence_needle,
# why).  The check fails when the condition is present and the consequence is
# not, in a group that reported applied/partial.
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
        # The flag must be handled where the mask is built, and it must never
        # reach the task counters.
        ("kernel/sched/psi.c", "psi_group_change",
         ["if (unlikely(clear & TSK_ONCPU)) {",
          "clear &= ~TSK_ONCPU;",
          "state_mask = PSI_ONCPU;",
          "state_mask = groupc->state_mask & PSI_ONCPU;",
          "test_state(groupc->tasks, s, state_mask & PSI_ONCPU)",
          "groupc->state_mask = state_mask;"],
         ["tasks[NR_ONCPU]"]),
        # Both halves of the switch: the early stop needs the flag, and every
        # other state difference has to keep propagating above that stop.
        ("kernel/sched/psi.c", "psi_task_switch",
         ["per_cpu_ptr(group->pcpu, cpu)->state_mask &",
          "if ((prev->psi_flags ^ next->psi_flags) & ~TSK_ONCPU) {",
          "group = iterate_groups(prev, &iter)"],
         ["tasks[NR_ONCPU]", "identical_state"]),
    ],
    "perf:psi_cgroup_pressure_switch": [
        # Accounting off has to be handled where the state mask is derived, not
        # in a helper the hot path never reaches, and it has to release the
        # sequence counter it still holds.
        ("kernel/sched/psi.c", "psi_group_change",
         ["if (unlikely(!psi_group_enabled(group)))",
          "groupc->state_mask = 0;",
          "write_seqcount_end(&groupc->seq);"],
         []),
        # Re-enabling rebuilds every CPU's mask under that CPU's rq lock, the
        # only place the counts and cpu_curr() are stable.
        ("kernel/sched/psi.c", "psi_cgroup_restart",
         ["if (!psi_group_enabled(group))",
          "rq_lock_irq(rq, &rf);",
          "psi_group_change(group, cpu, 0, 0, cpu_clock(cpu), true);",
          "rq_unlock_irq(rq, &rf);"],
         # Every possible CPU, not just the online ones: the masks of offline
         # CPUs have to be rebuilt too, or they stay stale across the switch.
         ["for_each_online_cpu"]),
        # The knob writes the flag and, only when turning accounting back on,
        # resyncs the group; the root cgroup's files are backed by psi_system.
        ("kernel/cgroup/cgroup.c", "cgroup_pressure_write",
         ["psi_cgroup_accounting_enabled(cgrp) != enable",
          "psi_cgroup_accounting_set(cgrp, enable);",
          "psi_cgroup_restart(cgroup_ino(cgrp) == 1 ?",
          "cgroup_kn_unlock(of->kn);"],
         ["psi_trigger_create"]),
    ],
    "perf:schedutil_smart_policy": [
        # The ownership gate has to sit in the resolve path itself: a gate that
        # lands in a helper nobody calls is exactly the "compiles but behaves
        # like the source kernel" failure this audit exists for.
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
        # Sliding window: any sample below the entry threshold restarts it.
        # The dead-band form (reset only below the *exit* threshold) let a CPU
        # bank boost time across 70-90% oscillation, which is what kept the
        # reason latched for a whole game.
        ("kernel/sched/cpufreq_schedutil.c", "abk_sf_sample",
         ["c->boost_start = 0;",
          "if (util * 100 < cap * ABK_SF_SUSTAINED_PCT)",
          "c->boost_release = now + msecs_to_jiffies(abk_sf_exit_ms)"],
         ["} else if (util * 100 < cap * ABK_SF_EXIT_PCT) {"]),
        # The deadline is re-checked outside the tick, because an idle CPU stops
        # ticking under NO_HZ_IDLE and would otherwise latch the reason.
        ("kernel/sched/cpufreq_schedutil.c", "abk_sf_cpu_boosting",
         ["time_after(jiffies, c->boost_release)", "abk_sf_clear(c)"],
         []),
    ],
    "core:pid_alloc_hotpath_phase2": [
        # The retry must stay inside alloc_pid() and re-enter the SAME
        # pid-namespace level.  The suite retried with `continue;` inside
        # `for (i = ns->level; i >= 0; i--)`, which runs the loop's increment
        # expression: it retried the PARENT level (re-running the set_tid
        # bookkeeping), and on ns->level == 0 -- every ordinary fork -- it left
        # the loop, hit `retval = -ENOMEM;` at the function tail and returned a
        # fully initialised pid whose numbers[0].nr had never been written.
        # `continue;` is absent from the pristine alloc_pid(), so this pins it.
        ("kernel/pid.c", "alloc_pid",
         ["retry_preload:", "retried_preload = false;", "retried_preload = true;"],
         ["continue;"]),
    ],
    "core:close_range_hotpath": [
        # The bitmap walk must land in __range_close() itself, not in a helper
        # nobody calls, and the suite's caller-locked 6.1 helpers must not appear
        # (on 5.15 pick_file() takes files->file_lock itself, so
        # abk_pick_file_for_close()'s lockdep assertion would be knowingly
        # false).  Asserted on in-body text, not on the marker comment: the
        # marker sits *above* the signature, where function_body() cannot see it.
        ("fs/file.c", "__range_close",
         ["n = last_fd(fdt);",
          "max_fd = min(max_fd, n);",
          "fd = find_next_bit(fdt->open_fds, max_fd + 1, fd);"],
         ["abk_pick_file_for_close", "abk_close_range_limit"]),
    ],
    "core:zram_recompression": [
        # The read path must delegate to the shared helper, not carry its own
        # copy of the decompress logic (two copies is how the get/put anchors
        # got swapped in the first place).
        ("drivers/block/zram/zram_drv.c", "__zram_bvec_read",
         ["zram_read_from_zspool(zram, page, index)"],
         ["zcomp_decompress", "zcomp_stream_get"]),
        # The helper selects the comp by the slot's stored priority.
        ("drivers/block/zram/zram_drv.c", "zram_read_from_zspool",
         ["prio = zram_get_priority(zram, index)",
          "zcomp_stream_get(zram->comps[prio])",
          "zcomp_stream_put(zram->comps[prio])"],
         ["zram->comp)"]),
        # The write path always compresses with the primary comp.
        ("drivers/block/zram/zram_drv.c", "__zram_bvec_write",
         ["zcomp_stream_get(zram->comps[ZRAM_PRIMARY_COMP])"],
         ["zram_get_priority", "zram->comp)"]),
        # Secondary comps must actually be created, or every recompress path
        # short-circuits on a NULL comps[prio] and the group is a no-op.
        ("drivers/block/zram/zram_drv.c", "disksize_store",
         ["for (prio = 0; prio < ZRAM_MAX_COMPS; prio++)",
          "zram->comps[prio] = comp", "zram->num_active_comps++",
          "out_free_comps"],
         []),
        # A reused slot must not keep a stale comp priority: the next write
        # compresses with the primary comp, so a stale priority would decompress
        # through the wrong algorithm.
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
        # The locked store must report success rather than refuse the write:
        # Android's mmd_setup aborts its whole zram bring-up -- writeback
        # backing device included -- on a failed algorithm write.
        ("drivers/block/zram/zram_drv.c", "abk_zram_locked_algo_store",
         ["abk_zram_algo_lock_report(attr->attr.name, buf);",
          "return len;"],
         ["-EPERM", "-EACCES"]),
        # The locked primary has to be chosen at device creation, or a build
        # whose def-comp is the dominated lz4hc keeps it for the whole boot.
        ("drivers/block/zram/zram_drv.c", "zram_add",
         ["comp_algorithm_set(zram, ZRAM_PRIMARY_COMP,",
          "zcomp_available_algorithm(abk_zram_comp_algo)"],
         []),
        # ... and the earlier groups' blocks must stay byte-identical, because
        # a later edit inside them breaks their idempotency.
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
        # The handler's three gates are the policy: a dropped enable check or
        # a dropped order comparison changes the blast radius of NORETRY from
        # "THP-class under pressure" to every allocation, silently.
        ("mm/page_alloc.c", "abk_gfp_fastfail_hook",
         ["if (!READ_ONCE(abk_gfp_fastfail))",
          "if (order < READ_ONCE(abk_gfp_fastfail_order))",
          "if (!abk_gfp_under_pressure())",
          "*gfp |= __GFP_NORETRY | __GFP_NOWARN;"],
         ["return true"]),
        # The gate is the once-per-second watermark probe, not a per-call
        # walk of every zone (that would put for_each_zone on the allocator's
        # hot path), and its two globals move through READ_ONCE/WRITE_ONCE:
        # the hook runs from the allocator, unsynchronised against itself.
        ("mm/page_alloc.c", "abk_gfp_under_pressure",
         ["time_is_after_jiffies(READ_ONCE(abk_gfp_wm_stamp) + HZ)",
          "WRITE_ONCE(abk_gfp_high_wm, sum);",
          "si_mem_available() < (long)limit"],
         ["abk_gfp_high_wm += "]),
    ],
    "core:zram_recompress_max_pages": [
        # Both nodes need all three parts, in this relative order.  Slicing by
        # function is the only way to see which node got which hunk: the two
        # grammars are otherwise textually identical, so a cap that landed only
        # on the synchronous side passes whole-file matching perfectly.
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
    "core:readahead_mmap_miss_race": [
        # The guard belongs in do_async_mmap_readahead(), the function whose
        # counter it protects.  Whole-file matching cannot say which function
        # got it: 5.15's FAULT_FLAG_SPECULATIVE branch in filemap_fault() holds
        # the same decrement, and this graft deliberately leaves that one alone
        # (upstream deleted the branch, so nothing upstream fixes it).  A hunk
        # that landed in the wrong function, or on the wrong side of the
        # RAND_READ return, is invisible to every other audit here.
        ("mm/filemap.c", "do_async_mmap_readahead",
         ["if (likely(!PageLocked(page))) {",
          "mmap_miss = READ_ONCE(ra->mmap_miss);",
          "if (PageReadahead(page)) {"],
         ["return fpin;\n\tmmap_miss = READ_ONCE(ra->mmap_miss);\n"]),
    ],
}


def function_body(text, name):
    """Return the text of `name`'s definition, or None.

    Matches a line whose last token before '(' is `name` at column 0 (kernel
    style puts the return type on the same line), then runs to the first
    line-initial '}'.  Returns every matching definition joined, so a symbol
    with an #ifdef/#else pair of definitions is checked as a whole.
    """
    bodies = []
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if line.startswith((" ", "\t", "#", "*", "/")) or f"{name}(" not in line:
            continue
        head = line.split(f"{name}(")[0]
        if head and not head.endswith(("*", " ")):
            continue
        # Look ahead to find the opening brace (may be several lines after signature)
        brace_start = None
        for scan in range(index, min(index + 20, len(lines))):
            if lines[scan].rstrip().endswith(";"):
                break  # forward declaration
            if lines[scan].lstrip().startswith("{"):
                brace_start = scan
                break
        if brace_start is None:
            continue
        # Now find the closing brace
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
            ctx = GraftContext(root, "167", "android13-5.15",
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
                    missing = [needle for needle in REQUIRED_CONTENT[key]
                               if needle not in blob]
                    if missing:
                        problems.append(f"{child}/{group.key}: missing "
                                        f"feature content {missing}")
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

                # Function-scoped assertions
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

                # Cross-file pairing invariants
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
