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
    "perf:psi_trigger_kernfs_polling": ["psi_trigger_ext", "pending_event"],
    "perf:psi_irq_tracking": ["PSI_IRQ"],
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
}

# Removal grafts: content that must NOT survive into the patched text wherever
# the group reports applied.  Keyed as "child:group".  An entry is either a
# bare string (checked against the blob of the group's files, the original
# removal-graft shape) or a [rel, needle] pair (checked against that one
# file only, for absence claims a *neighbouring* group's legitimate content
# in a shared file would otherwise defeat).
REQUIRED_ABSENT = {
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
}

# Function-scoped assertions.  REQUIRED_CONTENT above is whole-file substring
# matching, which cannot tell *which* function a graft landed in -- a swapped
# pair of anchors satisfies it perfectly.  These entries slice out one function
# body and assert on that slice, so a hunk landing in the neighbouring function
# fails the audit.  Keyed as "child:group" -> list of
# (rel, function_name, must_contain, must_not_contain).
REQUIRED_IN_FUNCTION = {
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

                if status in ("applied", "partial") and \
                        not any(MARKER in ctx.read(f) for f in changed):
                    print(f"  info: {child}/{group.key}: no module marker "
                          "(upstream-shape idempotency)")
                print(f"  {child:5s} {group.key:36s} {status}")

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
