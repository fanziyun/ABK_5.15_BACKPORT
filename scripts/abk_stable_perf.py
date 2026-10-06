"""Child ``stable_perf_backport``: upstream 5.15.y optimization grafts.

Carries the NOHZ idle-balance optimization series (5.15.174), the PSI
migration flags micro-optimization (5.15.179), the RT scan optimizations
(5.15.202/.212), per-task kstack randomization (5.15.210), the
__release_sock() cond_resched reduction (5.15.197), the semaphore wake_q
offload (5.15.180), the blk-mq suspend wakeup abort (5.15.198), and the
android14-6.1 line (lazy preemption + mutex/rwsem wakeup vendor hooks,
PSI IRQ pressure tracking, PSI trigger kernfs polling, the per-cgroup PSI
accounting switch `cgroup.pressure`, and the PSI ONCPU state-mask sync).

KMI notes: the per-task kstack offset reuses task_struct's
ANDROID_KABI_RESERVE(8) slot instead of growing the struct, and the PSI group
only removes a bitfield member whose word is force-aligned by ``unsigned :0``
(upstream-verified no-op for struct layout).  The android14-6.1 groups only
add vendor tracepoints and heap-internal struct members, so the stable KMI is
preserved; the ACK 6.1 psi_group pointer/parent rework is deliberately NOT
ported (it changes struct cgroup layout).  The Batch 21 `cgroup.pressure` switch
keeps its state in the cgroup's own `flags` word (CGRP_PSI_DISABLED) for the same
reason -- `struct psi_group` is embedded in `struct cgroup`, so the ACK
`psi_group::enabled` member cannot be added without moving `bpf`/`congestion_count`/
`freezer`/`ancestor_ids[]`.

Every group degrades to a reported status when its anchor shape is absent.
From Batch 15 this child is the owner of the sched_entity 1-4 slots (the
absorbed EEVDF family) and of request_queue slot 1 (blk_mq_async_depth), so
these are now claimed here rather than avoided -- see the Batch 15 modules and
"Suite absorption" in docs/porting_policy.md.

Coexistence: standalone by default; if storage-rollback or other feature-
graft modules are injected in the same build, keep this child between them
(injection order in docs/porting_policy.md).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import abk_common as common  # noqa: E402
from abk_backport_engine import (  # noqa: E402
    PatchGroup,
    apply_steps,
    make_context,
    parse_args,
    run_child,
)

T = True  # required step
F = False  # optional step


# ---------------------------------------------------------------------------
# NOHZ idle balance optimization series (5.15.174)
# ---------------------------------------------------------------------------

def _nohz_apply(ctx):
    steps = [
        # kernel/sched/sched.h: NOHZ_NEXT_KICK flag
        ("kernel/sched/sched.h",
         "#define NOHZ_BALANCE_KICK_BIT\t0\n"
         "#define NOHZ_STATS_KICK_BIT\t1\n"
         "#define NOHZ_NEWILB_KICK_BIT\t2\n"
         "\n"
         "#define NOHZ_BALANCE_KICK\tBIT(NOHZ_BALANCE_KICK_BIT)\n"
         "#define NOHZ_STATS_KICK\t\tBIT(NOHZ_STATS_KICK_BIT)\n"
         "#define NOHZ_NEWILB_KICK\tBIT(NOHZ_NEWILB_KICK_BIT)\n"
         "\n"
         "#define NOHZ_KICK_MASK\t(NOHZ_BALANCE_KICK | NOHZ_STATS_KICK)\n",
         "#define NOHZ_BALANCE_KICK_BIT\t0\n"
         "#define NOHZ_STATS_KICK_BIT\t1\n"
         "#define NOHZ_NEWILB_KICK_BIT\t2\n"
         "#define NOHZ_NEXT_KICK_BIT\t3\n"
         "\n"
         "/* Run rebalance_domains() */\n"
         "#define NOHZ_BALANCE_KICK\tBIT(NOHZ_BALANCE_KICK_BIT)\n"
         "/* Update blocked load */\n"
         "#define NOHZ_STATS_KICK\t\tBIT(NOHZ_STATS_KICK_BIT)\n"
         "/* Update blocked load when entering idle */\n"
         "#define NOHZ_NEWILB_KICK\tBIT(NOHZ_NEWILB_KICK_BIT)\n"
         "/* Update nohz.next_balance */\n"
         "#define NOHZ_NEXT_KICK\t\tBIT(NOHZ_NEXT_KICK_BIT)\n"
         "\n"
         "#define NOHZ_KICK_MASK\t(NOHZ_BALANCE_KICK | NOHZ_STATS_KICK | NOHZ_NEXT_KICK)\n",
         T),
        # fair.c: nohz_balancer_kick() five kick sites become scoped kicks
        ("kernel/sched/fair.c",
         "\tif (rq->nr_running >= 2) {\n\t\tflags = NOHZ_KICK_MASK;\n\t\tgoto out;\n\t}",
         "\tif (rq->nr_running >= 2) {\n\t\tflags = NOHZ_STATS_KICK | NOHZ_BALANCE_KICK;\n\t\tgoto out;\n\t}",
         T),
        ("kernel/sched/fair.c",
         "\t\tif (rq->cfs.h_nr_running >= 1 && check_cpu_capacity(rq, sd)) {\n\t\t\tflags = NOHZ_KICK_MASK;\n\t\t\tgoto unlock;\n\t\t}",
         "\t\tif (rq->cfs.h_nr_running >= 1 && check_cpu_capacity(rq, sd)) {\n\t\t\tflags = NOHZ_STATS_KICK | NOHZ_BALANCE_KICK;\n\t\t\tgoto unlock;\n\t\t}",
         T),
        ("kernel/sched/fair.c",
         "\t\t\tif (sched_asym_prefer(i, cpu)) {\n\t\t\t\tflags = NOHZ_KICK_MASK;\n\t\t\t\tgoto unlock;\n\t\t\t}",
         "\t\t\tif (sched_asym_prefer(i, cpu)) {\n\t\t\t\tflags = NOHZ_STATS_KICK | NOHZ_BALANCE_KICK;\n\t\t\t\tgoto unlock;\n\t\t\t}",
         T),
        ("kernel/sched/fair.c",
         "\t\tif (check_misfit_status(rq, sd)) {\n\t\t\tflags = NOHZ_KICK_MASK;\n\t\t\tgoto unlock;\n\t\t}",
         "\t\tif (check_misfit_status(rq, sd)) {\n\t\t\tflags = NOHZ_STATS_KICK | NOHZ_BALANCE_KICK;\n\t\t\tgoto unlock;\n\t\t}",
         T),
        ("kernel/sched/fair.c",
         "\t\tif (nr_busy > 1) {\n\t\t\tflags = NOHZ_KICK_MASK;\n\t\t\tgoto unlock;\n\t\t}",
         "\t\tif (nr_busy > 1) {\n\t\t\tflags = NOHZ_STATS_KICK | NOHZ_BALANCE_KICK;\n\t\t\tgoto unlock;\n\t\t}",
         T),
        # fair.c: _nohz_idle_balance() only maintain blocked stats for STATS kicks
        ("kernel/sched/fair.c",
         "\tWRITE_ONCE(nohz.has_blocked, 0);\n",
         "\tif (flags & NOHZ_STATS_KICK)\n\t\tWRITE_ONCE(nohz.has_blocked, 0);\n",
         T),
        ("kernel/sched/fair.c",
         "\t\tif (need_resched()) {\n\t\t\thas_blocked_load = true;\n\t\t\tgoto abort;\n\t\t}\n"
         "\n\t\trq = cpu_rq(balance_cpu);\n\n\t\thas_blocked_load |= update_nohz_stats(rq);",
         "\t\tif (!idle_cpu(this_cpu) && need_resched()) {\n\t\t\tif (flags & NOHZ_STATS_KICK)\n\t\t\t\thas_blocked_load = true;\n\t\t\tgoto abort;\n\t\t}\n"
         "\n\t\trq = cpu_rq(balance_cpu);\n\n\t\tif (flags & NOHZ_STATS_KICK)\n\t\t\thas_blocked_load |= update_nohz_stats(rq);",
         T),
        ("kernel/sched/fair.c",
         "\tWRITE_ONCE(nohz.next_blocked,\n\t\tnow + msecs_to_jiffies(LOAD_AVG_PERIOD));\n",
         "\tif (flags & NOHZ_STATS_KICK)\n\t\tWRITE_ONCE(nohz.next_blocked,\n\t\t\t   now + msecs_to_jiffies(LOAD_AVG_PERIOD));\n",
         T),
        # core.c: nohz_csd_func() stops waking ksoftirqd and uses the raw raise
        ("kernel/sched/core.c",
         "\trq->idle_balance = idle_cpu(cpu);\n"
         "\tif (rq->idle_balance && !need_resched()) {\n"
         "\t\trq->nohz_idle_balance = flags;\n"
         "\t\traise_softirq_irqoff(SCHED_SOFTIRQ);\n"
         "\t}",
         "\trq->idle_balance = idle_cpu(cpu);\n"
         "\tif (rq->idle_balance) {\n"
         "\t\trq->nohz_idle_balance = flags;\n"
         "\t\t__raise_softirq_irqoff(SCHED_SOFTIRQ);\n"
         "\t}",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# PSI: use task->psi_flags deltas on CPU migration (5.15.179)
# ---------------------------------------------------------------------------

def _psi_flags_apply(ctx):
    steps = [
        ("include/linux/sched.h",
         "\tunsigned\t\t\tsched_reset_on_fork:1;\n"
         "\tunsigned\t\t\tsched_contributes_to_load:1;\n"
         "\tunsigned\t\t\tsched_migrated:1;\n"
         "#ifdef CONFIG_PSI\n"
         "\tunsigned\t\t\tsched_psi_wake_requeue:1;\n"
         "#endif\n"
         "\n\t/* Force alignment to the next boundary: */",
         "\tunsigned\t\t\tsched_reset_on_fork:1;\n"
         "\tunsigned\t\t\tsched_contributes_to_load:1;\n"
         "\tunsigned\t\t\tsched_migrated:1;\n"
         "\n\t/* Force alignment to the next boundary: */",
         T),
        ("kernel/sched/core.c",
         "\t\tpsi_enqueue(p, flags & ENQUEUE_WAKEUP);",
         "\t\tpsi_enqueue(p, (flags & ENQUEUE_WAKEUP) && !(flags & ENQUEUE_MIGRATED));",
         T),
        ("kernel/sched/stats.h",
         "\tif (!wakeup || p->sched_psi_wake_requeue) {\n"
         "\t\tif (p->in_memstall)\n"
         "\t\t\tset |= TSK_MEMSTALL;\n"
         "\t\tif (p->sched_psi_wake_requeue)\n"
         "\t\t\tp->sched_psi_wake_requeue = 0;\n"
         "\t} else {",
         "\tif (!wakeup) {\n"
         "\t\tif (p->in_memstall)\n"
         "\t\t\tset |= TSK_MEMSTALL;\n"
         "\t} else {",
         T),
        ("kernel/sched/stats.h",
         "static inline void psi_dequeue(struct task_struct *p, bool sleep)\n"
         "{\n"
         "\tint clear = TSK_RUNNING;\n"
         "\n"
         "\tif (static_branch_likely(&psi_disabled))\n",
         "static inline void psi_dequeue(struct task_struct *p, bool sleep)\n"
         "{\n"
         "\tif (static_branch_likely(&psi_disabled))\n",
         T),
        ("kernel/sched/stats.h",
         "\tif (p->in_memstall)\n\t\tclear |= (TSK_MEMSTALL | TSK_MEMSTALL_RUNNING);\n\n\tpsi_task_change(p, clear, 0);\n}",
         "\tpsi_task_change(p, p->psi_flags, 0);\n}",
         T),
        ("kernel/sched/stats.h",
         "\tif (unlikely(p->in_iowait || p->in_memstall)) {\n"
         "\t\tstruct rq_flags rf;\n"
         "\t\tstruct rq *rq;\n"
         "\t\tint clear = 0;\n"
         "\n"
         "\t\tif (p->in_iowait)\n"
         "\t\t\tclear |= TSK_IOWAIT;\n"
         "\t\tif (p->in_memstall)\n"
         "\t\t\tclear |= TSK_MEMSTALL;\n"
         "\n"
         "\t\trq = __task_rq_lock(p, &rf);\n"
         "\t\tpsi_task_change(p, clear, 0);\n"
         "\t\tp->sched_psi_wake_requeue = 1;\n"
         "\t\t__task_rq_unlock(rq, &rf);\n"
         "\t}",
         "\tif (unlikely(p->psi_flags)) {\n"
         "\t\tstruct rq_flags rf;\n"
         "\t\tstruct rq *rq;\n"
         "\n"
         "\t\trq = __task_rq_lock(p, &rf);\n"
         "\t\tpsi_task_change(p, p->psi_flags, 0);\n"
         "\t\t__task_rq_unlock(rq, &rf);\n"
         "\t}",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# RT scan optimizations (5.15.202 rto skip-self + 5.15.212 RT_PUSH_IPI default)
# ---------------------------------------------------------------------------

def _rt_optimizations_apply(ctx):
    steps = [
        ("kernel/sched/rt.c",
         "static int rto_next_cpu(struct root_domain *rd)\n{\n\tint next;\n\tint cpu;\n",
         "static int rto_next_cpu(struct root_domain *rd)\n{\n\tint this_cpu = smp_processor_id();\n\tint next;\n\tint cpu;\n",
         T),
        ("kernel/sched/rt.c",
         "\t\trd->rto_cpu = cpu;\n\n\t\tif (cpu < nr_cpu_ids)\n\t\t\treturn cpu;",
         "\t\trd->rto_cpu = cpu;\n\n\t\t/* Do not send IPI to self */\n\t\tif (cpu == this_cpu)\n\t\t\tcontinue;\n\n\t\tif (cpu < nr_cpu_ids)\n\t\t\treturn cpu;",
         T),
        ("kernel/sched/features.h",
         " */\nSCHED_FEAT(RT_PUSH_IPI, true)\n#endif",
         " * This is best for PREEMPT_RT, but for non-RT it can cause issues\n"
         " * when preemption is disabled for long periods of time. Have\n"
         " * it only default enabled for PREEMPT_RT.\n"
         " */\n"
         "# ifdef CONFIG_PREEMPT_RT\nSCHED_FEAT(RT_PUSH_IPI, true)\n# else\nSCHED_FEAT(RT_PUSH_IPI, false)\n# endif\n#endif",
         F),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# update_sg_wakeup_stats(): only count CPUs the waking task may use (5.15.212)
# ---------------------------------------------------------------------------

def _dst_group_allowed_stats_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        ("kernel/sched/fair.c",
         "\tfor_each_cpu(i, sched_group_span(group)) {\n"
         "\t\tstruct rq *rq = cpu_rq(i);\n"
         "\t\tunsigned int local;\n",
         "\tfor_each_cpu_and(i, sched_group_span(group), p->cpus_ptr) {\n"
         "\t\tstruct rq *rq = cpu_rq(i);\n"
         "\t\tunsigned int local;\n",
         T),
    ])
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# sched: drop excess steal time instead of carrying it forward (5.15.179)
# ---------------------------------------------------------------------------

# Upstream-shape rewrite (no marker): the 194/216 baselines already carry it and
# a tree that does ends up byte-identical to this group's output.  Value
# boundary, stated up front: the whole block sits under
# CONFIG_PARAVIRT_TIME_ACCOUNTING (present in the GKI defconfig because the same
# image runs as a KVM/AVF guest) *and* inside
# static_key_false(paravirt_steal_rq_enabled), which only a hypervisor that
# advertises steal time ever turns on.  On bare metal the graft is therefore
# inert by construction; inside a guest it stops the catch-up from freezing
# clock_task for the whole time the host is suspended (and from charging that
# steal to whichever task happens to be running next).


def _sched_steal_time_drop_apply(ctx):
    try:
        text = ctx.read("kernel/sched/core.c")
    except FileNotFoundError:
        text = ""
    if "rq->prev_steal_time_rq = prev_steal;" in text:
        return "already_present", (
            "the 5.15.179 excess-steal-time drop is already in "
            "update_rq_clock_task()")
    status, _results, detail = apply_steps(ctx, [
        ("kernel/sched/core.c",
         "#ifdef CONFIG_PARAVIRT_TIME_ACCOUNTING\n"
         "\tif (static_key_false((&paravirt_steal_rq_enabled))) {\n"
         "\t\tsteal = paravirt_steal_clock(cpu_of(rq));\n"
         "\t\tsteal -= rq->prev_steal_time_rq;\n"
         "\n"
         "\t\tif (unlikely(steal > delta))\n"
         "\t\t\tsteal = delta;\n"
         "\n"
         "\t\trq->prev_steal_time_rq += steal;\n"
         "\t\tdelta -= steal;\n"
         "\t}\n"
         "#endif\n",
         "#ifdef CONFIG_PARAVIRT_TIME_ACCOUNTING\n"
         "\tif (static_key_false((&paravirt_steal_rq_enabled))) {\n"
         "\t\tu64 prev_steal;\n"
         "\n"
         "\t\tsteal = prev_steal = paravirt_steal_clock(cpu_of(rq));\n"
         "\t\tsteal -= rq->prev_steal_time_rq;\n"
         "\n"
         "\t\tif (unlikely(steal > delta))\n"
         "\t\t\tsteal = delta;\n"
         "\n"
         "\t\trq->prev_steal_time_rq = prev_steal;\n"
         "\t\tdelta -= steal;\n"
         "\t}\n"
         "#endif\n",
         T),
    ])
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# Per-task kstack randomization offset (5.15.210) - KMI-safe via KABI slot 8
# ---------------------------------------------------------------------------

def _sched_h_kstack_step(text):
    """Pick the KABI slot for kstack_offset based on the tree shape.

    Three shapes are known, and every one of them keeps the member inside
    struct task_struct (see _verify_kstack_member, which re-checks that):

    - the anchored 1..8 RESERVE run (every deprecated/ release baseline) --
      claim slot 8;
    - ABK's kernel-specific patch step rewrites task_struct's slots 6/7/8 into
      the CONFIG_SYSVIPC sysvsem/sysvshm restoration (an #ifdef/#else block).
      In that shape slots 7/8 only exist inside the dead #else branch, so a
      first-occurrence RESERVE(8) replacement lands in dead code and the struct
      silently loses the member -- use the still-free slot 5 there;
    - the android13-5.15-lts branch from 5.15.211 on: AOSP already claims slot 1
      for the user_dumpable:1 bitfield, so the free RESERVE run starts at 2 and
      the 1..8 anchor is gone.  Claim slot 8 there as well, so the smoke grep
      for ANDROID_KABI_USE(8 holds on every baseline.
    """
    marker = "/* ABK stable_515_backport: per-task kstack randomization offset (5.15.210) mapped onto the KABI reserve slot. */"
    if "ANDROID_KABI_USE(6, struct sysv_sem sysvsem)" in text:
        return (
            "include/linux/sched.h",
            "\tANDROID_KABI_RESERVE(5);",
            "\t" + marker + "\n"
            "\tANDROID_KABI_USE(5, u32\t\t\tkstack_offset);",
            T,
        )
    if "ANDROID_KABI_USE(1, struct {" in text and "user_dumpable:1;" in text:
        # 5.15.211+ lts shape: slot 1 is AOSP, so the anchored run is 2..8.
        # (The probe cannot fire on the 1..8 baselines: they carry neither the
        # KABI_USE(1) bitfield nor user_dumpable.)
        run_old = "".join("\tANDROID_KABI_RESERVE(%d);\n" % n for n in range(2, 9))
        run_new = (
            "".join("\tANDROID_KABI_RESERVE(%d);\n" % n for n in range(2, 8))
            + "\t" + marker + "\n"
            + "\tANDROID_KABI_USE(8, u32\t\t\tkstack_offset);\n"
        )
        return ("include/linux/sched.h", run_old, run_new, T)
    run_old = "".join("\tANDROID_KABI_RESERVE(%d);\n" % n for n in range(1, 9))
    run_new = (
        "".join("\tANDROID_KABI_RESERVE(%d);\n" % n for n in range(1, 8))
        + "\t" + marker + "\n"
        + "\tANDROID_KABI_USE(8, u32\t\t\tkstack_offset);\n"
    )
    return ("include/linux/sched.h", run_old, run_new, T)


def _verify_kstack_member(ctx):
    """Fail loudly when the kstack_offset member is not inside task_struct."""
    text = ctx.read("include/linux/sched.h")
    if "kstack_offset" not in text:
        raise ValueError("kstack_offset member missing from include/linux/sched.h")
    start = text.index("struct task_struct {")
    end = text.index("\n};", start)
    if "kstack_offset" not in text[start:end]:
        raise ValueError(
            "kstack_offset KABI slot landed outside struct task_struct; "
            "sched.h shape not covered"
        )


def _kstack_pertask_apply(ctx):
    text = ctx.read("include/linux/sched.h")
    steps = []
    # Shape probe on this group's own added member, not on the reserve run it
    # consumed: a later group (Batch 55's sched_ext_task_slot) claims the next
    # free slot and therefore rewrites that run, which would otherwise leave
    # this step anchoring on text no longer present on the second pass --
    # group_recipe trap 5.  The member reads "kstack_offset);" (the macro call
    # closes with a parenthesis), which is why the older probe spelling with a
    # bare semicolon never fired.
    if "kstack_offset);" not in text:
        steps.append(_sched_h_kstack_step(text))
    steps.extend([
        ("include/linux/randomize_kstack.h",
         "\t\t\t randomize_kstack_offset);\n"
         "DECLARE_PER_CPU(u32, kstack_offset);\n"
         "\n"
         "/*\n",
         "\t\t\t randomize_kstack_offset);\n"
         "\n"
         "/*\n",
         T),
        ("include/linux/randomize_kstack.h",
         "/*\n * These macros must be used during syscall entry when interrupts and\n * preempt are disabled, and after user registers have been stored to\n * the stack.\n */\n"
         "#define add_random_kstack_offset() do {\t\t\t\t\t\\\n"
         "\tif (static_branch_maybe(CONFIG_RANDOMIZE_KSTACK_OFFSET_DEFAULT,\t\\\n"
         "\t\t\t\t&randomize_kstack_offset)) {\t\t\\\n"
         "\t\tu32 offset = raw_cpu_read(kstack_offset);\t\t\\\n"
         "\t\tu8 *ptr = __kstack_alloca(KSTACK_OFFSET_MAX(offset));\t\\\n"
         "\t\t/* Keep allocation even after \"ptr\" loses scope. */\t\\\n"
         "\t\tasm volatile(\"\" :: \"r\"(ptr) : \"memory\");\t\t\\\n"
         "\t}\t\t\t\t\t\t\t\t\\\n"
         "} while (0)\n"
         "\n"
         "#define choose_random_kstack_offset(rand) do {\t\t\t\t\\\n"
         "\tif (static_branch_maybe(CONFIG_RANDOMIZE_KSTACK_OFFSET_DEFAULT,\t\\\n"
         "\t\t\t\t&randomize_kstack_offset)) {\t\t\\\n"
         "\t\tu32 offset = raw_cpu_read(kstack_offset);\t\t\\\n"
         "\t\toffset = ror32(offset, 5) ^ (rand);\t\t\t\\\n"
         "\t\traw_cpu_write(kstack_offset, offset);\t\t\t\\\n"
         "\t}\t\t\t\t\t\t\t\t\\\n"
         "} while (0)\n",
         "/**\n"
         " * add_random_kstack_offset - Increase stack utilization by previously\n"
         " *\t\t\t      chosen random offset\n"
         " *\n"
         " * This should be used in the syscall entry path after user registers have been\n"
         " * stored to the stack. Preemption may be enabled. For testing the resulting\n"
         " * entropy, please see: tools/testing/selftests/lkdtm/stack-entropy.sh\n"
         " */\n"
         "#define add_random_kstack_offset() do {\t\t\t\t\t\\\n"
         "\tif (static_branch_maybe(CONFIG_RANDOMIZE_KSTACK_OFFSET_DEFAULT,\t\\\n"
         "\t\t\t\t&randomize_kstack_offset)) {\t\t\\\n"
         "\t\tu32 offset = current->kstack_offset;\t\t\t\\\n"
         "\t\tu8 *ptr = __kstack_alloca(KSTACK_OFFSET_MAX(offset));\t\\\n"
         "\t\t/* Keep allocation even after \"ptr\" loses scope. */\t\\\n"
         "\t\tasm volatile(\"\" :: \"r\"(ptr) : \"memory\");\t\t\\\n"
         "\t}\t\t\t\t\t\t\t\t\\\n"
         "} while (0)\n"
         "\n"
         "/**\n"
         " * choose_random_kstack_offset - Choose the random offset for the next\n"
         " *\t\t\t\t add_random_kstack_offset()\n"
         " *\n"
         " * This should only be used during syscall exit. Preemption may be enabled. This\n"
         " * position in the syscall flow is done to frustrate attacks from userspace\n"
         " * attempting to learn the next offset:\n"
         " * - Maximize the timing uncertainty visible from userspace: if the\n"
         " *   offset is chosen at syscall entry, userspace has much more control\n"
         " *   over the timing between choosing offsets. \"How long will we be in\n"
         " *   kernel mode?\" tends to be more difficult to predict than \"how long\n"
         " *   will we be in user mode?\"\n"
         " * - Reduce the lifetime of the new offset sitting in memory during\n"
         " *   kernel mode execution. Exposure of \"thread-local\" memory content\n"
         " *   (e.g. current, percpu, etc) tends to be easier than arbitrary\n"
         " *   location memory exposure.\n"
         " */\n"
         "#define choose_random_kstack_offset(rand) do {\t\t\t\t\\\n"
         "\tif (static_branch_maybe(CONFIG_RANDOMIZE_KSTACK_OFFSET_DEFAULT,\t\\\n"
         "\t\t\t\t&randomize_kstack_offset)) {\t\t\\\n"
         "\t\tu32 offset = current->kstack_offset;\t\t\t\\\n"
         "\t\toffset = ror32(offset, 5) ^ (rand);\t\t\t\\\n"
         "\t\tcurrent->kstack_offset = offset;\t\t\t\\\n"
         "\t}\t\t\t\t\t\t\t\t\\\n"
         "} while (0)\n"
         "\n"
         "/* ABK stable_515_backport: task-local offset initializer for the per-task kstack randomization graft. */\n"
         "#ifdef CONFIG_HAVE_ARCH_RANDOMIZE_KSTACK_OFFSET\n"
         "static inline void random_kstack_task_init(struct task_struct *tsk)\n"
         "{\n"
         "\ttsk->kstack_offset = 0;\n"
         "}\n"
         "#else\n"
         "#define random_kstack_task_init(tsk)\t\tdo { } while (0)\n"
         "#endif\n",
         T),
        ("init/main.c",
         "DEFINE_STATIC_KEY_MAYBE_RO(CONFIG_RANDOMIZE_KSTACK_OFFSET_DEFAULT,\n"
         "\t\t\t   randomize_kstack_offset);\n"
         "DEFINE_PER_CPU(u32, kstack_offset);\n"
         "\n"
         "static int __init early_randomize_kstack_offset(char *buf)\n",
         "DEFINE_STATIC_KEY_MAYBE_RO(CONFIG_RANDOMIZE_KSTACK_OFFSET_DEFAULT,\n"
         "\t\t\t   randomize_kstack_offset);\n"
         "\n"
         "static int __init early_randomize_kstack_offset(char *buf)\n",
         T),
        ("kernel/fork.c",
         "#include <linux/kasan.h>\n#include <linux/scs.h>",
         "#include <linux/kasan.h>\n#include <linux/randomize_kstack.h>\n#include <linux/scs.h>",
         T),
        ("kernel/fork.c",
         "\tstackleak_task_init(p);",
         "\trandom_kstack_task_init(p);\n\tstackleak_task_init(p);",
         T),
    ])
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    if status in ("applied", "partial"):
        _verify_kstack_member(ctx)
    return status, detail


# ---------------------------------------------------------------------------
# net core: __release_sock() cond_resched reduction (5.15.197)
# ---------------------------------------------------------------------------

def _release_sock_apply(ctx):
    old = """	struct sk_buff *skb, *next;

	while ((skb = sk->sk_backlog.head) != NULL) {
		sk->sk_backlog.head = sk->sk_backlog.tail = NULL;

		spin_unlock_bh(&sk->sk_lock.slock);

		do {
			next = skb->next;
			prefetch(next);
			WARN_ON_ONCE(skb_dst_is_noref(skb));
			skb_mark_not_on_list(skb);
			sk_backlog_rcv(sk, skb);

			cond_resched();

			skb = next;
		} while (skb != NULL);

		spin_lock_bh(&sk->sk_lock.slock);
	}
"""
    new = """	struct sk_buff *skb, *next;
	int nb = 0;

	while ((skb = sk->sk_backlog.head) != NULL) {
		sk->sk_backlog.head = sk->sk_backlog.tail = NULL;

		spin_unlock_bh(&sk->sk_lock.slock);

		while (1) {
			next = skb->next;
			prefetch(next);
			WARN_ON_ONCE(skb_dst_is_noref(skb));
			skb_mark_not_on_list(skb);
			sk_backlog_rcv(sk, skb);

			skb = next;
			if (!skb)
				break;

			if (!(++nb & 15))
				cond_resched();
		}

		spin_lock_bh(&sk->sk_lock.slock);
	}
"""
    status, _results, detail = apply_steps(ctx, [("net/core/sock.c", old, new, T)])
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# locking: semaphore wake_q offload (5.15.180)
# ---------------------------------------------------------------------------

def _semaphore_wake_q_apply(ctx):
    steps = [
        ("kernel/locking/semaphore.c",
         "#include <linux/sched/debug.h>\n#include <linux/semaphore.h>",
         "#include <linux/sched/debug.h>\n#include <linux/sched/wake_q.h>\n#include <linux/semaphore.h>",
         T),
        ("kernel/locking/semaphore.c",
         "static noinline void __up(struct semaphore *sem);\n",
         "static noinline void __up(struct semaphore *sem, struct wake_q_head *wake_q);\n",
         T),
        ("kernel/locking/semaphore.c",
         "void up(struct semaphore *sem)\n{\n\tunsigned long flags;\n\n\traw_spin_lock_irqsave(&sem->lock, flags);\n"
         "\tif (likely(list_empty(&sem->wait_list)))\n\t\tsem->count++;\n\telse\n\t\t__up(sem);\n"
         "\traw_spin_unlock_irqrestore(&sem->lock, flags);\n}",
         "void up(struct semaphore *sem)\n{\n\tunsigned long flags;\n\tDEFINE_WAKE_Q(wake_q);\n\n\traw_spin_lock_irqsave(&sem->lock, flags);\n"
         "\tif (likely(list_empty(&sem->wait_list)))\n\t\tsem->count++;\n\telse\n\t\t__up(sem, &wake_q);\n"
         "\traw_spin_unlock_irqrestore(&sem->lock, flags);\n\tif (!wake_q_empty(&wake_q))\n\t\twake_up_q(&wake_q);\n}",
         T),
        ("kernel/locking/semaphore.c",
         "static noinline void __sched __up(struct semaphore *sem)\n{",
         "static noinline void __sched __up(struct semaphore *sem,\n\t\t\t\t  struct wake_q_head *wake_q)\n{",
         T),
        ("kernel/locking/semaphore.c",
         "\tlist_del(&waiter->list);\n\twaiter->up = true;\n\twake_up_process(waiter->task);\n}",
         "\tlist_del(&waiter->list);\n\twaiter->up = true;\n\twake_q_add(wake_q, waiter->task);\n}",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# block: abort suspend when wakeup events are pending (5.15.198)
# ---------------------------------------------------------------------------

# The lts branch (5.15.211+) carries 8fe7de5d1c7f upstream-first, but with the
# <linux/suspend.h> include wrapped in an AOSP `#ifndef __GENKSYMS__` guard
# (AOSP keeps the CRCs stable that way), so the plain include-pair anchor never
# matches there and the group reported blocked_by_shape on a tree that already
# has the payload.  Probe the payload itself instead -- it is the same four
# lines either way, and this module's own graft uses the identical text, so the
# second pass short-circuits here too.
_BLK_MQ_SUSPEND_PAYLOAD = (
    "\t\t\tif (pm_wakeup_pending()) {\n"
    "\t\t\t\tclear_bit(BLK_MQ_S_INACTIVE, &hctx->state);\n"
    "\t\t\t\tret = -EBUSY;\n"
    "\t\t\t\tbreak;\n"
    "\t\t\t}\n"
)


def _blk_mq_suspend_apply(ctx):
    try:
        text = ctx.read("block/blk-mq.c")
    except FileNotFoundError:
        text = ""
    if _BLK_MQ_SUSPEND_PAYLOAD in text:
        return "already_present", (
            "the 5.15.198 abort-on-pending-wakeup payload is already in "
            "blk_mq_hctx_notify_offline() (upstream-first on this baseline, or "
            "this module's own graft on the second pass)")
    steps = [
        ("block/blk-mq.c",
         "#include <linux/sched/signal.h>\n#include <linux/delay.h>",
         "#include <linux/sched/signal.h>\n#include <linux/suspend.h>\n#include <linux/delay.h>",
         T),
        ("block/blk-mq.c",
         "\tstruct blk_mq_hw_ctx *hctx = hlist_entry_safe(node,\n\t\t\tstruct blk_mq_hw_ctx, cpuhp_online);\n\n\tif (!cpumask_test_cpu(cpu, hctx->cpumask) ||",
         "\tstruct blk_mq_hw_ctx *hctx = hlist_entry_safe(node,\n\t\t\tstruct blk_mq_hw_ctx, cpuhp_online);\n\tint ret = 0;\n\n\tif (!cpumask_test_cpu(cpu, hctx->cpumask) ||",
         T),
        ("block/blk-mq.c",
         "\tif (percpu_ref_tryget(&hctx->queue->q_usage_counter)) {\n\t\twhile (blk_mq_hctx_has_requests(hctx))\n\t\t\tmsleep(5);\n\t\tpercpu_ref_put(&hctx->queue->q_usage_counter);\n\t}\n\n\treturn 0;\n}",
         "\tif (percpu_ref_tryget(&hctx->queue->q_usage_counter)) {\n\t\twhile (blk_mq_hctx_has_requests(hctx)) {\n"
         "\t\t\t/*\n"
         "\t\t\t * The wakeup capable IRQ handler of block device is\n"
         "\t\t\t * not called during suspend. Skip the loop by checking\n"
         "\t\t\t * pm_wakeup_pending to prevent the deadlock and improve\n"
         "\t\t\t * suspend latency.\n"
         "\t\t\t */\n"
         "\t\t\tif (pm_wakeup_pending()) {\n"
         "\t\t\t\tclear_bit(BLK_MQ_S_INACTIVE, &hctx->state);\n"
         "\t\t\t\tret = -EBUSY;\n"
         "\t\t\t\tbreak;\n"
         "\t\t\t}\n"
         "\t\t\tmsleep(5);\n"
         "\t\t}\n"
         "\t\tpercpu_ref_put(&hctx->queue->q_usage_counter);\n"
         "\t}\n\n\treturn ret;\n}",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# android14-6.1 line: lazy preemption via vendor hooks (ACK 969cb3d family)
# ---------------------------------------------------------------------------

def _blk_mq_quiesced_elevator_apply(ctx):
    """Reinit-time elevator switch goes through the quiesced entry point.

    Upstream-shape rewrite (no marker): the baseline that already carries
    8237c01f1696 looks byte-identical to this group's output, which is what
    the already_present probe below detects.
    """
    try:
        text = ctx.read("block/blk-mq.c")
    except FileNotFoundError:
        text = ""
    if "elevator_switch(q, NULL);" in text:
        return "already_present", (
            "the 5.15.209 quiesced elevator switch is already in "
            "blk_mq_elv_switch_none()/blk_mq_elv_switch_back()")
    steps = [
        # rename -> users, all required: a tree holding one half of the pair
        # (a static definition plus a non-static declaration) does not link.
        ("block/elevator.c",
         "int elevator_switch_mq(struct request_queue *q,\n"
         "\t\t\t      struct elevator_type *new_e)\n"
         "{\n",
         "static int elevator_switch_mq(struct request_queue *q,\n"
         "\t\t\t      struct elevator_type *new_e)\n"
         "{\n",
         T),
        # The one-line form of this replacement is a substring of the
        # pristine static definition, so replace_once would short-circuit to
        # already_present (trap 1) -- the comment tail anchors it instead.
        ("block/elevator.c",
         " */\n"
         "static int elevator_switch(struct request_queue *q, struct elevator_type *new_e)\n"
         "{\n"
         "\tint err;\n",
         " */\n"
         "int elevator_switch(struct request_queue *q, struct elevator_type *new_e)\n"
         "{\n"
         "\tint err;\n",
         T),
        ("block/blk.h",
         "int elevator_switch_mq(struct request_queue *q,\n"
         "\t\t\t      struct elevator_type *new_e);",
         "int elevator_switch(struct request_queue *q, struct elevator_type *new_e);",
         T),
        ("block/blk-mq.c",
         "\t * After elevator_switch_mq, the previous elevator_queue will be\n",
         "\t * After elevator_switch, the previous elevator_queue will be\n",
         T),
        ("block/blk-mq.c",
         "\televator_switch_mq(q, NULL);\n",
         "\televator_switch(q, NULL);\n",
         T),
        ("block/blk-mq.c",
         "\televator_switch_mq(q, t);\n",
         "\televator_switch(q, t);\n",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _lazy_preempt_hooks_apply(ctx):
    steps = []
    # dtask.h: the set_tsk_need_resched_lazy hook exists only on the newer
    # 5.15 ACK lineage; the 2024-11 baseline needs the whole block added.
    # The marker doubles as the idempotency probe: once grafted, no dtask
    # step runs and the remaining steps all report already_present.
    dtext = ctx.read("include/trace/hooks/dtask.h")
    if "ABK stable_515_backport: lazy preemption scheduling hooks" not in dtext:
        if "android_vh_set_tsk_need_resched_lazy" not in dtext:
            steps.append((
                "include/trace/hooks/dtask.h",
                "DECLARE_HOOK(android_vh_freeze_whether_wake,\n"
                "\tTP_PROTO(struct task_struct *t, bool *wake),\n"
                "\tTP_ARGS(t, wake));\n"
                "\n"
                "#endif /* _TRACE_HOOK_DTASK_H */",
                "DECLARE_HOOK(android_vh_freeze_whether_wake,\n"
                "\tTP_PROTO(struct task_struct *t, bool *wake),\n"
                "\tTP_ARGS(t, wake));\n"
                "\n"
                "/* ABK stable_515_backport: lazy preemption scheduling hooks (android14-6.1). */\n"
                "DECLARE_HOOK(android_vh_set_tsk_need_resched_lazy,\n"
                "\tTP_PROTO(struct task_struct *p, struct rq *rq, int *need_lazy),\n"
                "\tTP_ARGS(p, rq, need_lazy));\n"
                "\n"
                "DECLARE_HOOK(android_vh_resched_curr_lazy,\n"
                "\tTP_PROTO(struct rq *rq, bool *skip_preempt),\n"
                "\tTP_ARGS(rq, skip_preempt));\n"
                "\n"
                "DECLARE_HOOK(android_vh_clear_curr_lazy,\n"
                "\tTP_PROTO(struct task_struct *tsk),\n"
                "\tTP_ARGS(tsk));\n"
                "\n"
                "DECLARE_HOOK(android_vh_lock_delay_schedule,\n"
                "\tTP_PROTO(struct task_struct *prev, int sched_mode, bool *ext_slice),\n"
                "\tTP_ARGS(prev, sched_mode, ext_slice));\n"
                "#endif /* _TRACE_HOOK_DTASK_H */",
                T,
            ))
        else:
            steps.append((
                "include/trace/hooks/dtask.h",
                "DECLARE_HOOK(android_vh_set_tsk_need_resched_lazy,\n"
                "\tTP_PROTO(struct task_struct *p, struct rq *rq, int *need_lazy),\n"
                "\tTP_ARGS(p, rq, need_lazy));\n"
                "#endif /* _TRACE_HOOK_DTASK_H */",
                "DECLARE_HOOK(android_vh_set_tsk_need_resched_lazy,\n"
                "\tTP_PROTO(struct task_struct *p, struct rq *rq, int *need_lazy),\n"
                "\tTP_ARGS(p, rq, need_lazy));\n"
                "\n"
                "/* ABK stable_515_backport: lazy preemption scheduling hooks (android14-6.1). */\n"
                "DECLARE_HOOK(android_vh_resched_curr_lazy,\n"
                "\tTP_PROTO(struct rq *rq, bool *skip_preempt),\n"
                "\tTP_ARGS(rq, skip_preempt));\n"
                "\n"
                "DECLARE_HOOK(android_vh_clear_curr_lazy,\n"
                "\tTP_PROTO(struct task_struct *tsk),\n"
                "\tTP_ARGS(tsk));\n"
                "\n"
                "DECLARE_HOOK(android_vh_lock_delay_schedule,\n"
                "\tTP_PROTO(struct task_struct *prev, int sched_mode, bool *ext_slice),\n"
                "\tTP_ARGS(prev, sched_mode, ext_slice));\n"
                "#endif /* _TRACE_HOOK_DTASK_H */",
                T,
            ))
    # core.c: resched_curr() gains the lazy gate on the 2024-11 baseline
    ctext = ctx.read("kernel/sched/core.c")
    if "trace_android_vh_set_tsk_need_resched_lazy" not in ctext:
        steps.append((
            "kernel/sched/core.c",
            "void resched_curr(struct rq *rq)\n"
            "{\n"
            "\tstruct task_struct *curr = rq->curr;\n"
            "\tint cpu;\n"
            "\n"
            "\tlockdep_assert_rq_held(rq);\n"
            "\n"
            "\tif (test_tsk_need_resched(curr))\n"
            "\t\treturn;\n"
            "\n"
            "\tcpu = cpu_of(rq);",
            "void resched_curr(struct rq *rq)\n"
            "{\n"
            "\tstruct task_struct *curr = rq->curr;\n"
            "\tint cpu, need_lazy = 0;\n"
            "\n"
            "\tlockdep_assert_rq_held(rq);\n"
            "\n"
            "\tif (test_tsk_need_resched(curr))\n"
            "\t\treturn;\n"
            "\n"
            "\t/* ABK stable_515_backport: lazy preemption resched gate (android14-6.1). */\n"
            "\ttrace_android_vh_set_tsk_need_resched_lazy(curr, rq, &need_lazy);\n"
            "\tif (need_lazy)\n"
            "\t\treturn;\n"
            "\n"
            "\tcpu = cpu_of(rq);",
            T,
        ))
    steps.extend([
        # fair.c: the lazy hooks live in dtask.h; mirror the 6.1 include pair
        # so the tracepoint macros are visible in this translation unit.
        ("kernel/sched/fair.c",
         "#include <trace/hooks/sched.h>",
         "#include <trace/hooks/sched.h>\n"
         "#include <trace/hooks/dtask.h>",
         T),
        # core.c: __schedule() may skip this schedule() call entirely
        ("kernel/sched/core.c",
         "\tstruct task_struct *prev, *next;\n"
         "\tunsigned long *switch_count;\n"
         "\tunsigned long prev_state;\n"
         "\tstruct rq_flags rf;\n"
         "\tstruct rq *rq;\n"
         "\tint cpu;\n"
         "\n"
         "\tcpu = smp_processor_id();",
         "\tstruct task_struct *prev, *next;\n"
         "\tunsigned long *switch_count;\n"
         "\tunsigned long prev_state;\n"
         "\tstruct rq_flags rf;\n"
         "\tstruct rq *rq;\n"
         "\tint cpu;\n"
         "\t/* ABK stable_515_backport: bounded schedule deferral (android14-6.1). */\n"
         "\tbool skip_schedule = false;\n"
         "\n"
         "\tcpu = smp_processor_id();",
         T),
        ("kernel/sched/core.c",
         "\tschedule_debug(prev, !!sched_mode);\n"
         "\n"
         "\tif (sched_feat(HRTICK) || sched_feat(HRTICK_DL))",
         "\tschedule_debug(prev, !!sched_mode);\n"
         "\n"
         "\ttrace_android_vh_lock_delay_schedule(prev, sched_mode, &skip_schedule);\n"
         "\n"
         "\tif (skip_schedule)\n"
         "\t\treturn;\n"
         "\n"
         "\tif (sched_feat(HRTICK) || sched_feat(HRTICK_DL))",
         T),
        # core.c: lazy state is cleared once the next task is picked
        ("kernel/sched/core.c",
         "\tnext = pick_next_task(rq, prev, &rf);\n"
         "\tclear_tsk_need_resched(prev);\n"
         "\tclear_preempt_need_resched();",
         "\tnext = pick_next_task(rq, prev, &rf);\n"
         "\tclear_tsk_need_resched(prev);\n"
         "\tclear_preempt_need_resched();\n"
         "\ttrace_android_vh_clear_curr_lazy(prev);",
         T),
        # fair.c: check_preempt_tick() can defer the resched
        ("kernel/sched/fair.c",
         "\tif (delta_exec > ideal_runtime) {\n"
         "\t\tresched_curr(rq_of(cfs_rq));\n"
         "\t\t/*\n"
         "\t\t * The current task ran long enough, ensure it doesn't get",
         "\tif (delta_exec > ideal_runtime) {\n"
         "\t\ttrace_android_vh_resched_curr_lazy(rq_of(cfs_rq), &skip_preempt);\n"
         "\n"
         "\t\tif (skip_preempt)\n"
         "\t\t\treturn;\n"
         "\n"
         "\t\tresched_curr(rq_of(cfs_rq));\n"
         "\t\t/*\n"
         "\t\t * The current task ran long enough, ensure it doesn't get",
         T),
        # fair.c: entity_tick() HRTICK branch can defer the resched
        ("kernel/sched/fair.c",
         "\tif (queued) {\n"
         "\t\tresched_curr(rq_of(cfs_rq));\n"
         "\t\treturn;\n"
         "\t}",
         "\tif (queued) {\n"
         "\t\tbool skip_preempt = false;\n"
         "\n"
         "\t\ttrace_android_vh_resched_curr_lazy(rq_of(cfs_rq), &skip_preempt);\n"
         "\n"
         "\t\tif (skip_preempt)\n"
         "\t\t\treturn;\n"
         "\n"
         "\t\tresched_curr(rq_of(cfs_rq));\n"
         "\t\treturn;\n"
         "\t}",
         T),
        # fair.c: wakeup preemption can defer the resched
        ("kernel/sched/fair.c",
         "preempt:\n"
         "\tresched_curr(rq);\n"
         "\t/*\n"
         "\t * Only set the backward buddy when the current task is still",
         "preempt:\n"
         "\ttrace_android_vh_resched_curr_lazy(rq_of(cfs_rq), &ignore);\n"
         "\n"
         "\tif (ignore)\n"
         "\t\treturn;\n"
         "\n"
         "\tresched_curr(rq);\n"
         "\t/*\n"
         "\t * Only set the backward buddy when the current task is still",
         T),
    ])
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# android14-6.1 line: mutex/rwsem wakeup patch vendor hooks (ACK dfdcb1d)
# ---------------------------------------------------------------------------

def _locking_wakeup_patch_apply(ctx):
    steps = [
        ("include/trace/hooks/dtask.h",
         "DECLARE_HOOK(android_vh_mutex_unlock_slowpath,\n"
         "\tTP_PROTO(struct mutex *lock),\n"
         "\tTP_ARGS(lock));\n"
         "DECLARE_HOOK(android_vh_record_mutex_lock_starttime,",
         "DECLARE_HOOK(android_vh_mutex_unlock_slowpath,\n"
         "\tTP_PROTO(struct mutex *lock),\n"
         "\tTP_ARGS(lock));\n"
         "\n"
         "/* ABK stable_515_backport: post-wakeup fixup hook (android14-6.1). */\n"
         "DECLARE_HOOK(android_vh_mutex_wakeup_patch,\n"
         "\tTP_PROTO(struct mutex *lock),\n"
         "\tTP_ARGS(lock));\n"
         "DECLARE_HOOK(android_vh_record_mutex_lock_starttime,",
         T),
        ("include/trace/hooks/rwsem.h",
         "DECLARE_HOOK(android_vh_rwsem_wake_finish,\n"
         "\tTP_PROTO(struct rw_semaphore *sem),\n"
         "\tTP_ARGS(sem));\n"
         "DECLARE_HOOK(android_vh_rwsem_downgrade_wake_finish,",
         "DECLARE_HOOK(android_vh_rwsem_wake_finish,\n"
         "\tTP_PROTO(struct rw_semaphore *sem),\n"
         "\tTP_ARGS(sem));\n"
         "\n"
         "/* ABK stable_515_backport: post-wakeup fixup hook (android14-6.1). */\n"
         "DECLARE_HOOK(android_vh_rwsem_wakeup_patch,\n"
         "\tTP_PROTO(struct rw_semaphore *sem),\n"
         "\tTP_ARGS(sem));\n"
         "DECLARE_HOOK(android_vh_rwsem_downgrade_wake_finish,",
         T),
        ("kernel/locking/mutex.c",
         "\traw_spin_unlock(&lock->wait_lock);\n"
         "\n"
         "\twake_up_q(&wake_q);\n"
         "}",
         "\traw_spin_unlock(&lock->wait_lock);\n"
         "\n"
         "\twake_up_q(&wake_q);\n"
         "\n"
         "\t/* ABK stable_515_backport: post-wakeup fixup point (android14-6.1). */\n"
         "\ttrace_android_vh_mutex_wakeup_patch(lock);\n"
         "}",
         T),
        ("kernel/locking/rwsem.c",
         "\ttrace_android_vh_rwsem_wake_finish(sem);\n"
         "\n"
         "\traw_spin_unlock_irqrestore(&sem->wait_lock, flags);\n"
         "\twake_up_q(&wake_q);\n"
         "\n"
         "\treturn sem;\n"
         "}",
         "\ttrace_android_vh_rwsem_wake_finish(sem);\n"
         "\n"
         "\traw_spin_unlock_irqrestore(&sem->wait_lock, flags);\n"
         "\twake_up_q(&wake_q);\n"
         "\n"
         "\t/* ABK stable_515_backport: post-wakeup fixup point (android14-6.1). */\n"
         "\ttrace_android_vh_rwsem_wakeup_patch(sem);\n"
         "\n"
         "\treturn sem;\n"
         "}",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# android14-6.1 line: PSI IRQ pressure accounting (mainline 6.1 52b1364,
# adapted to the 5.15 iterate_groups walk and embedded psi_group)
# ---------------------------------------------------------------------------

def _psi_irq_tracking_apply(ctx):
    # Shape probe.  psi_cgroup_pressure_switch (registered below) adds the
    # per-cgroup accounting switch to the walk this group appends, so its
    # psi.c replacement block no longer appears verbatim on the second pass:
    # without this probe the group would re-append the whole function and the
    # tree would end up with two psi_account_irqtime() definitions (the module's
    # trap 2, one group removed).  The function's presence in psi.c is the
    # group's own all-or-nothing marker -- apply_steps is transactional.
    try:
        text = ctx.read("kernel/sched/psi.c")
    except FileNotFoundError:
        text = ""
    if "psi_account_irqtime" in text:
        return "already_present", (
            "PSI IRQ accounting is already in the tree")
    steps = [
        # psi_types.h: PSI_IRQ resource and state, gated like the ACK tree
        ("include/linux/psi_types.h",
         "enum psi_res {\n"
         "\tPSI_IO,\n"
         "\tPSI_MEM,\n"
         "\tPSI_CPU,\n"
         "\tNR_PSI_RESOURCES = 3,\n"
         "};",
         "enum psi_res {\n"
         "\tPSI_IO,\n"
         "\tPSI_MEM,\n"
         "\tPSI_CPU,\n"
         "/* ABK stable_515_backport: IRQ pressure resource (android14-6.1). */\n"
         "#ifdef CONFIG_IRQ_TIME_ACCOUNTING\n"
         "\tPSI_IRQ,\n"
         "#endif\n"
         "\tNR_PSI_RESOURCES,\n"
         "};",
         T),
        ("include/linux/psi_types.h",
         "\tPSI_CPU_SOME,\n"
         "\tPSI_CPU_FULL,\n"
         "\t/* Only per-CPU, to weigh the CPU in the global average: */\n"
         "\tPSI_NONIDLE,\n"
         "\tNR_PSI_STATES = 7,\n"
         "};",
         "\tPSI_CPU_SOME,\n"
         "\tPSI_CPU_FULL,\n"
         "#ifdef CONFIG_IRQ_TIME_ACCOUNTING\n"
         "\tPSI_IRQ_FULL,\n"
         "#endif\n"
         "\t/* Only per-CPU, to weigh the CPU in the global average: */\n"
         "\tPSI_NONIDLE,\n"
         "\tNR_PSI_STATES,\n"
         "};",
         T),
        # stats.h: declaration and compile-out stubs
        ("kernel/sched/stats.h",
         "#ifdef CONFIG_PSI\n"
         "/*\n"
         " * PSI tracks state that persists across sleeps, such as iowaits and",
         "#ifdef CONFIG_PSI\n"
         "\n"
         "/* ABK stable_515_backport: PSI IRQ pressure accounting (android14-6.1). */\n"
         "#ifdef CONFIG_IRQ_TIME_ACCOUNTING\n"
         "void psi_account_irqtime(struct rq *rq, struct task_struct *curr, struct task_struct *prev);\n"
         "#else\n"
         "static inline void psi_account_irqtime(struct rq *rq, struct task_struct *curr,\n"
         "\t\t\t\t       struct task_struct *prev) {}\n"
         "#endif /* CONFIG_IRQ_TIME_ACCOUNTING */\n"
         "\n"
         "/*\n"
         " * PSI tracks state that persists across sleeps, such as iowaits and",
         T),
        ("kernel/sched/stats.h",
         "static inline void psi_sched_switch(struct task_struct *prev,\n"
         "\t\t\t\t    struct task_struct *next,\n"
         "\t\t\t\t    bool sleep) {}\n"
         "#endif /* CONFIG_PSI */",
         "static inline void psi_sched_switch(struct task_struct *prev,\n"
         "\t\t\t\t    struct task_struct *next,\n"
         "\t\t\t\t    bool sleep) {}\n"
         "#ifdef CONFIG_IRQ_TIME_ACCOUNTING\n"
         "static inline void psi_account_irqtime(struct rq *rq, struct task_struct *curr,\n"
         "\t\t\t\t       struct task_struct *prev) {}\n"
         "#endif /* CONFIG_IRQ_TIME_ACCOUNTING */\n"
         "#endif /* CONFIG_PSI */",
         T),
        # psi.c: the accounting function, walking the 5.15 iterate_groups chain
        ("kernel/sched/psi.c",
         "/**\n"
         " * psi_memstall_enter - mark the beginning of a memory stall section\n"
         " * @flags: flags to handle nested sections",
         "/* ABK stable_515_backport: PSI IRQ pressure accounting, grafted from the\n"
         " * android14-6.1 ACK shape onto the 5.15 iterate_groups walk.\n"
         " */\n"
         "#ifdef CONFIG_IRQ_TIME_ACCOUNTING\n"
         "static DEFINE_PER_CPU(u64, psi_irq_time);\n"
         "void psi_account_irqtime(struct rq *rq, struct task_struct *curr, struct task_struct *prev)\n"
         "{\n"
         "\tint cpu = task_cpu(curr);\n"
         "\tstruct psi_group *group;\n"
         "\tstruct psi_group_cpu *groupc;\n"
         "\tvoid *iter = NULL;\n"
         "\tu64 *psi_time;\n"
         "\ts64 delta;\n"
         "\tu64 irq;\n"
         "\n"
         "\tif (!curr->pid)\n"
         "\t\treturn;\n"
         "\n"
         "\tlockdep_assert_rq_held(rq);\n"
         "\tif (prev) {\n"
         "\t\tvoid *prev_iter = NULL;\n"
         "\n"
         "\t\tif (iterate_groups(prev, &prev_iter) == iterate_groups(curr, &iter))\n"
         "\t\t\treturn;\n"
         "\t\titer = NULL;\n"
         "\t}\n"
         "\n"
         "\tirq = irq_time_read(cpu);\n"
         "\tpsi_time = &per_cpu(psi_irq_time, cpu);\n"
         "\tdelta = (s64)(irq - *psi_time);\n"
         "\tif (delta < 0)\n"
         "\t\treturn;\n"
         "\t*psi_time = irq;\n"
         "\n"
         "\twhile ((group = iterate_groups(curr, &iter))) {\n"
         "\t\tu64 now;\n"
         "\n"
         "\t\tgroupc = per_cpu_ptr(group->pcpu, cpu);\n"
         "\n"
         "\t\twrite_seqcount_begin(&groupc->seq);\n"
         "\t\tnow = cpu_clock(cpu);\n"
         "\n"
         "\t\trecord_times(groupc, now);\n"
         "\t\tgroupc->times[PSI_IRQ_FULL] += delta;\n"
         "\n"
         "\t\twrite_seqcount_end(&groupc->seq);\n"
         "\n"
         "\t\tif (group->poll_states & (1 << PSI_IRQ_FULL))\n"
         "\t\t\tpsi_schedule_poll_work(group, 1, false);\n"
         "\t}\n"
         "}\n"
         "#endif /* CONFIG_IRQ_TIME_ACCOUNTING */\n"
         "\n"
         "/**\n"
         " * psi_memstall_enter - mark the beginning of a memory stall section\n"
         " * @flags: flags to handle nested sections",
         T),
        # psi.c: psi_show() renders the irq resource as a single full line
        ("kernel/sched/psi.c",
         "int psi_show(struct seq_file *m, struct psi_group *group, enum psi_res res)\n"
         "{\n"
         "\tint full;\n"
         "\tu64 now;",
         "int psi_show(struct seq_file *m, struct psi_group *group, enum psi_res res)\n"
         "{\n"
         "\tbool only_full = false;\n"
         "\tint full;\n"
         "\tu64 now;",
         T),
        ("kernel/sched/psi.c",
         "\tmutex_unlock(&group->avgs_lock);\n"
         "\n"
         "\tfor (full = 0; full < 2; full++) {",
         "\tmutex_unlock(&group->avgs_lock);\n"
         "\n"
         "#ifdef CONFIG_IRQ_TIME_ACCOUNTING\n"
         "\tonly_full = res == PSI_IRQ;\n"
         "#endif\n"
         "\n"
         "\tfor (full = 0; full < 2 - only_full; full++) {",
         T),
        ("kernel/sched/psi.c",
         "\t\t\t   full ? \"full\" : \"some\",",
         "\t\t\t   full || only_full ? \"full\" : \"some\",",
         T),
        # psi.c: psi_trigger_create() must reject some/ only for the irq resource
        ("kernel/sched/psi.c",
         "\telse\n"
         "\t\treturn ERR_PTR(-EINVAL);\n"
         "\n"
         "\tif (state >= PSI_NONIDLE)\n"
         "\t\treturn ERR_PTR(-EINVAL);",
         "\telse\n"
         "\t\treturn ERR_PTR(-EINVAL);\n"
         "\n"
         "/* ABK stable_515_backport: irq pressure only supports full triggers (android14-6.1). */\n"
         "#ifdef CONFIG_IRQ_TIME_ACCOUNTING\n"
         "\tif (res == PSI_IRQ && --state != PSI_IRQ_FULL)\n"
         "\t\treturn ERR_PTR(-EINVAL);\n"
         "#endif\n"
         "\n"
         "\tif (state >= PSI_NONIDLE)\n"
         "\t\treturn ERR_PTR(-EINVAL);",
         T),
        # psi.c: /proc/pressure/irq surface
        ("kernel/sched/psi.c",
         "\nstatic int __init psi_proc_init(void)\n",
         "\n"
         "/* ABK stable_515_backport: /proc/pressure/irq (android14-6.1). */\n"
         "#ifdef CONFIG_IRQ_TIME_ACCOUNTING\n"
         "static int psi_irq_show(struct seq_file *m, void *v)\n"
         "{\n"
         "\treturn psi_show(m, &psi_system, PSI_IRQ);\n"
         "}\n"
         "\n"
         "static int psi_irq_open(struct inode *inode, struct file *file)\n"
         "{\n"
         "\treturn single_open(file, psi_irq_show, NULL);\n"
         "}\n"
         "\n"
         "static ssize_t psi_irq_write(struct file *file, const char __user *user_buf,\n"
         "\t\t\t     size_t nbytes, loff_t *ppos)\n"
         "{\n"
         "\treturn psi_write(file, user_buf, nbytes, PSI_IRQ);\n"
         "}\n"
         "\n"
         "static const struct proc_ops psi_irq_proc_ops = {\n"
         "\t.proc_open\t= psi_irq_open,\n"
         "\t.proc_read\t= seq_read,\n"
         "\t.proc_lseek\t= seq_lseek,\n"
         "\t.proc_write\t= psi_irq_write,\n"
         "\t.proc_poll\t= psi_fop_poll,\n"
         "\t.proc_release\t= psi_fop_release,\n"
         "};\n"
         "#endif\n"
         "\n"
         "static int __init psi_proc_init(void)\n",
         T),
        ("kernel/sched/psi.c",
         "\t\tproc_create(\"pressure/cpu\", 0, NULL, &psi_cpu_proc_ops);\n"
         "\t}",
         "\t\tproc_create(\"pressure/cpu\", 0, NULL, &psi_cpu_proc_ops);\n"
         "#ifdef CONFIG_IRQ_TIME_ACCOUNTING\n"
         "\t\tproc_create(\"pressure/irq\", 0, NULL, &psi_irq_proc_ops);\n"
         "#endif\n"
         "\t}",
         T),
        # core.c: account irq time at tick and before the context switch
        ("kernel/sched/core.c",
         "\trq_lock(rq, &rf);\n"
         "\n"
         "\tupdate_rq_clock(rq);\n"
         "\ttrace_android_rvh_tick_entry(rq);",
         "\trq_lock(rq, &rf);\n"
         "\n"
         "\t/* ABK stable_515_backport: PSI IRQ pressure accounting (android14-6.1). */\n"
         "\tpsi_account_irqtime(rq, curr, NULL);\n"
         "\n"
         "\tupdate_rq_clock(rq);\n"
         "\ttrace_android_rvh_tick_entry(rq);",
         T),
        ("kernel/sched/core.c",
         "\t\tmigrate_disable_switch(rq, prev);\n"
         "\t\tpsi_sched_switch(prev, next, !task_on_rq_queued(prev));",
         "\t\tmigrate_disable_switch(rq, prev);\n"
         "\t\tpsi_account_irqtime(rq, prev, next);\n"
         "\t\tpsi_sched_switch(prev, next, !task_on_rq_queued(prev));",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# android14-6.1 line: PSI trigger kernfs polling (ACK 6.1 backport of the
# kernfs polling rework; psi_trigger is heap-only so the KMI is untouched)
# ---------------------------------------------------------------------------

def _psi_kernfs_polling_apply(ctx):
    steps = [
        # psi_types.h: deferred-event flag and the kernfs wrapper struct
        ("include/linux/psi_types.h",
         "\t/*\n"
         "\t * Time last event was generated. Used for rate-limiting\n"
         "\t * events to one per window\n"
         "\t */\n"
         "\tu64 last_event_time;\n"
         "};",
         "\t/*\n"
         "\t * Time last event was generated. Used for rate-limiting\n"
         "\t * events to one per window\n"
         "\t */\n"
         "\tu64 last_event_time;\n"
         "\n"
         "\t/* ABK stable_515_backport: deferred event(s) from the previous ratelimit window (android14-6.1). */\n"
         "\tbool pending_event;\n"
         "};",
         T),
        ("include/linux/psi_types.h",
         "enum poll_wakeup_bits {\n"
         "\tPOLL_WAKEUP\t= 0,\n"
         "\tPOLL_SCHEDULED\t= 1,\n"
         "};",
         "enum poll_wakeup_bits {\n"
         "\tPOLL_WAKEUP\t= 0,\n"
         "\tPOLL_SCHEDULED\t= 1,\n"
         "};\n"
         "\n"
         "/* ABK stable_515_backport: kernfs polling wrapper for cgroup triggers (android14-6.1). */\n"
         "struct psi_trigger_ext {\n"
         "\tstruct psi_trigger trigger;\n"
         "\n"
         "\t/* Kernfs file for cgroup triggers */\n"
         "\tstruct kernfs_open_file *of;\n"
         "};",
         T),
        # psi.h: trigger creation carries the file/kernfs identity
        ("include/linux/psi.h",
         "struct psi_trigger *psi_trigger_create(struct psi_group *group,\n"
         "\t\t\tchar *buf, size_t nbytes, enum psi_res res);",
         "/* ABK stable_515_backport: kernfs-aware trigger creation (android14-6.1). */\n"
         "struct psi_trigger *psi_trigger_create(struct psi_group *group, char *buf,\n"
         "\t\t\t\t       enum psi_res res, struct file *file,\n"
         "\t\t\t\t       struct kernfs_open_file *of);",
         T),
        # psi.c: short windows are no longer floored at 500ms
        ("kernel/sched/psi.c",
         "/* PSI trigger definitions */\n"
         "#define WINDOW_MIN_US 500000\t/* Min window size is 500ms */\n"
         "#define WINDOW_MAX_US 10000000\t/* Max window size is 10s */",
         "/* PSI trigger definitions */\n"
         "/* ABK stable_515_backport: windows may start at 1us (android14-6.1). */\n"
         "#define WINDOW_MAX_US 10000000\t/* Max window size is 10s */",
         T),
        ("kernel/sched/psi.c",
         "\tif (window_us < WINDOW_MIN_US ||\n"
         "\t\twindow_us > WINDOW_MAX_US)\n"
         "\t\treturn ERR_PTR(-EINVAL);",
         "\tif (window_us == 0 || window_us > WINDOW_MAX_US)\n"
         "\t\treturn ERR_PTR(-EINVAL);",
         T),
        # psi.c: trigger_create() allocates the ext wrapper and seeds the window
        ("kernel/sched/psi.c",
         "struct psi_trigger *psi_trigger_create(struct psi_group *group,\n"
         "\t\t\tchar *buf, size_t nbytes, enum psi_res res)\n"
         "{\n"
         "\tstruct psi_trigger *t;",
         "struct psi_trigger *psi_trigger_create(struct psi_group *group, char *buf,\n"
         "\t\t\t\t       enum psi_res res, struct file *file,\n"
         "\t\t\t\t       struct kernfs_open_file *of)\n"
         "{\n"
         "\tstruct psi_trigger_ext *t_ext;\n"
         "\tstruct psi_trigger *t;",
         T),
        ("kernel/sched/psi.c",
         "\tt = kmalloc(sizeof(*t), GFP_KERNEL);\n"
         "\tif (!t)\n"
         "\t\treturn ERR_PTR(-ENOMEM);",
         "\tt_ext = kmalloc(sizeof(*t_ext), GFP_KERNEL);\n"
         "\tif (!t_ext)\n"
         "\t\treturn ERR_PTR(-ENOMEM);\n"
         "\tt = &t_ext->trigger;",
         T),
        ("kernel/sched/psi.c",
         "\twindow_reset(&t->win, 0, 0, 0);\n"
         "\n"
         "\tt->event = 0;\n"
         "\tt->last_event_time = 0;\n"
         "\tinit_waitqueue_head(&t->event_wait);",
         "\twindow_reset(&t->win, sched_clock(),\n"
         "\t\t\tgroup->total[PSI_POLL][t->state], 0);\n"
         "\n"
         "\tt->event = 0;\n"
         "\tt->last_event_time = 0;\n"
         "\tt_ext->of = of;\n"
         "\tif (!of)\n"
         "\t\tinit_waitqueue_head(&t->event_wait);\n"
         "\tt->pending_event = false;",
         T),
        ("kernel/sched/psi.c",
         "\t\ttask = kthread_create(psi_poll_worker, group, \"psimon\");\n"
         "\t\tif (IS_ERR(task)) {\n"
         "\t\t\tkfree(t);\n"
         "\t\t\tmutex_unlock(&group->trigger_lock);",
         "\t\ttask = kthread_create(psi_poll_worker, group, \"psimon\");\n"
         "\t\tif (IS_ERR(task)) {\n"
         "\t\t\tkfree(t_ext);\n"
         "\t\t\tmutex_unlock(&group->trigger_lock);",
         T),
        # psi.c: update_triggers() ratelimits without dropping events and
        # signals cgroup triggers through kernfs
        ("kernel/sched/psi.c",
         "static u64 update_triggers(struct psi_group *group, u64 now)\n"
         "{\n"
         "\tstruct psi_trigger *t;\n"
         "\tbool new_stall = false;\n"
         "\tu64 *total = group->total[PSI_POLL];",
         "static u64 update_triggers(struct psi_group *group, u64 now)\n"
         "{\n"
         "\tstruct psi_trigger *t;\n"
         "\tbool update_total = false;\n"
         "\tu64 *total = group->total[PSI_POLL];",
         T),
        ("kernel/sched/psi.c",
         "\tlist_for_each_entry(t, &group->triggers, node) {\n"
         "\t\tu64 growth;\n"
         "\n"
         "\t\t/* Check for stall activity */\n"
         "\t\tif (group->polling_total[t->state] == total[t->state])\n"
         "\t\t\tcontinue;\n"
         "\n"
         "\t\t/*\n"
         "\t\t * Multiple triggers might be looking at the same state,\n"
         "\t\t * remember to update group->polling_total[] once we've\n"
         "\t\t * been through all of them. Also remember to extend the\n"
         "\t\t * polling time if we see new stall activity.\n"
         "\t\t */\n"
         "\t\tnew_stall = true;\n"
         "\n"
         "\t\t/* Calculate growth since last update */\n"
         "\t\tgrowth = window_update(&t->win, now, total[t->state]);\n"
         "\t\tif (growth < t->threshold)\n"
         "\t\t\tcontinue;\n"
         "\n"
         "\t\t/* Limit event signaling to once per window */\n"
         "\t\tif (now < t->last_event_time + t->win.size)\n"
         "\t\t\tcontinue;",
         "\tlist_for_each_entry(t, &group->triggers, node) {\n"
         "\t\tu64 growth;\n"
         "\t\tbool new_stall;\n"
         "\n"
         "\t\tnew_stall = group->polling_total[t->state] != total[t->state];\n"
         "\n"
         "\t\t/* Check for stall activity or a previous threshold breach */\n"
         "\t\tif (!new_stall && !t->pending_event)\n"
         "\t\t\tcontinue;\n"
         "\t\t/*\n"
         "\t\t * Check for new stall activity, as well as deferred\n"
         "\t\t * events that occurred in the last window after the\n"
         "\t\t * trigger had already fired (we want to ratelimit\n"
         "\t\t * events without dropping any).\n"
         "\t\t */\n"
         "\t\tif (new_stall) {\n"
         "\t\t\t/*\n"
         "\t\t\t * Multiple triggers might be looking at the same state,\n"
         "\t\t\t * remember to update group->polling_total[] once we've\n"
         "\t\t\t * been through all of them. Also remember to extend the\n"
         "\t\t\t * polling time if we see new stall activity.\n"
         "\t\t\t */\n"
         "\t\t\tupdate_total = true;\n"
         "\n"
         "\t\t\t/* Calculate growth since last update */\n"
         "\t\t\tgrowth = window_update(&t->win, now, total[t->state]);\n"
         "\t\t\tif (!t->pending_event) {\n"
         "\t\t\t\tif (growth < t->threshold)\n"
         "\t\t\t\t\tcontinue;\n"
         "\n"
         "\t\t\t\tt->pending_event = true;\n"
         "\t\t\t}\n"
         "\t\t}\n"
         "\t\t/* Limit event signaling to once per window */\n"
         "\t\tif (now < t->last_event_time + t->win.size)\n"
         "\t\t\tcontinue;",
         T),
        ("kernel/sched/psi.c",
         "\t\t/* Generate an event */\n"
         "\t\tif (cmpxchg(&t->event, 0, 1) == 0)\n"
         "\t\t\twake_up_interruptible(&t->event_wait);\n"
         "\t\tt->last_event_time = now;\n"
         "\t}",
         "\t\t/* Generate an event */\n"
         "\t\tif (cmpxchg(&t->event, 0, 1) == 0) {\n"
         "\t\t\tstruct psi_trigger_ext *t_ext;\n"
         "\n"
         "\t\t\tt_ext = container_of(t, struct psi_trigger_ext, trigger);\n"
         "\t\t\tif (t_ext->of)\n"
         "\t\t\t\tkernfs_notify(t_ext->of->kn);\n"
         "\t\t\telse\n"
         "\t\t\t\twake_up_interruptible(&t->event_wait);\n"
         "\t\t}\n"
         "\t\tt->last_event_time = now;\n"
         "\t\t/* Reset threshold breach flag once event got generated */\n"
         "\t\tt->pending_event = false;\n"
         "\t}",
         T),
        ("kernel/sched/psi.c",
         "\tif (new_stall)\n"
         "\t\tmemcpy(group->polling_total, total,\n"
         "\t\t\t\tsizeof(group->polling_total));",
         "\tif (update_total)\n"
         "\t\tmemcpy(group->polling_total, total,\n"
         "\t\t\t\tsizeof(group->polling_total));",
         T),
        # psi.c: trigger destruction and poll wake cgroup waiters via kernfs
        ("kernel/sched/psi.c",
         "void psi_trigger_destroy(struct psi_trigger *t)\n"
         "{\n"
         "\tstruct psi_group *group;\n"
         "\tstruct task_struct *task_to_destroy = NULL;",
         "void psi_trigger_destroy(struct psi_trigger *t)\n"
         "{\n"
         "\tstruct psi_trigger_ext *t_ext;\n"
         "\tstruct psi_group *group;\n"
         "\tstruct task_struct *task_to_destroy = NULL;",
         T),
        ("kernel/sched/psi.c",
         "\twake_up_pollfree(&t->event_wait);",
         "\tt_ext = container_of(t, struct psi_trigger_ext, trigger);\n"
         "\tif (t_ext->of)\n"
         "\t\tkernfs_notify(t_ext->of->kn);\n"
         "\telse\n"
         "\t\twake_up_interruptible(&t->event_wait);",
         T),
        ("kernel/sched/psi.c",
         "\t\tkthread_stop(task_to_destroy);\n"
         "\t\tatomic_clear_bit(POLL_SCHEDULED, &group->poll_wakeup);\n"
         "\t}\n"
         "\tkfree(t);\n"
         "}",
         "\t\tkthread_stop(task_to_destroy);\n"
         "\t\tatomic_clear_bit(POLL_SCHEDULED, &group->poll_wakeup);\n"
         "\t}\n"
         "\tkfree(t_ext);\n"
         "}",
         T),
        ("kernel/sched/psi.c",
         "\t__poll_t ret = DEFAULT_POLLMASK;\n"
         "\tstruct psi_trigger *t;",
         "\t__poll_t ret = DEFAULT_POLLMASK;\n"
         "\tstruct psi_trigger_ext *t_ext;\n"
         "\tstruct psi_trigger *t;",
         T),
        ("kernel/sched/psi.c",
         "\tt = smp_load_acquire(trigger_ptr);\n"
         "\tif (!t)\n"
         "\t\treturn DEFAULT_POLLMASK | EPOLLERR | EPOLLPRI;\n"
         "\n"
         "\tpoll_wait(file, &t->event_wait, wait);",
         "\tt = smp_load_acquire(trigger_ptr);\n"
         "\tif (!t)\n"
         "\t\treturn DEFAULT_POLLMASK | EPOLLERR | EPOLLPRI;\n"
         "\n"
         "\tt_ext = container_of(t, struct psi_trigger_ext, trigger);\n"
         "\tif (t_ext->of)\n"
         "\t\tkernfs_generic_poll(t_ext->of, wait);\n"
         "\telse\n"
         "\t\tpoll_wait(file, &t->event_wait, wait);",
         T),
        # psi.c + cgroup.c: callers pass the open file identity through
        ("kernel/sched/psi.c",
         "\tnew = psi_trigger_create(&psi_system, buf, nbytes, res);",
         "\tnew = psi_trigger_create(&psi_system, buf, res, file, NULL);",
         T),
        ("kernel/cgroup/cgroup.c",
         "\tnew = psi_trigger_create(psi, buf, nbytes, res);",
         "\tnew = psi_trigger_create(psi, buf, res, of->file, of);",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# android14-6.1 line: the per-cgroup PSI accounting switch (cgroup.pressure)
# ---------------------------------------------------------------------------

# Upstream keeps the switch in struct psi_group::enabled and reaches the
# ancestor chain through a newly added struct psi_group::parent.  Neither is
# available on 5.15: struct psi_group is embedded in struct cgroup
# (cgroup-defs.h), so a new member -- at the front as much as at the back --
# moves bpf/congestion_count/freezer/ancestor_ids[] and breaks the cgroup KMI.
# The 5.15 walk (iterate_groups()) already reaches every ancestor level through
# the cgroup tree, so no parent pointer is needed either.  What the port keeps is
# the semantics:
#
#   * the switch is per cgroup and NOT hierarchical (cgroup-v2.rst: disabling
#     accounting in a cgroup does not affect its descendants),
#   * a disabled group keeps counting tasks -- the levels above and below consult
#     those counts -- but stops deriving state masks and timing them,
#   * re-enabling rebuilds each CPU's state mask from those counts
#     (psi_cgroup_restart()), and
#   * a disabled group has no pressure data to report.
#
# The state is therefore the CGRP_PSI_DISABLED bit of the cgroup's own
# (unsigned long) flags word, and the root cgroup's switch -- whose pressure
# files are backed by psi_system rather than by an embedded group -- drives a
# flag in psi.c.  One deviation is deliberate: 5.15 has no kernfs_show() and no
# KERNFS_HIDDEN (that mechanism arrived with this very series), so the pressure
# files are not hidden; reading one reports -EOPNOTSUPP instead of frozen
# numbers, which is the same signal to a consumer.

def _psi_cgroup_pressure_apply(ctx):
    try:
        text = ctx.read("kernel/cgroup/cgroup.c")
    except FileNotFoundError:
        text = ""
    if "cgroup.pressure" in text:
        return "already_present", (
            "cgroup.pressure is already in the cgroup base files")
    steps = [
        # cgroup-defs.h: a flag bit, so no struct offset moves
        ("include/linux/cgroup-defs.h",
         "\t/* Control group has to be killed. */\n"
         "\tCGRP_KILL,\n"
         "};",
         "\t/* Control group has to be killed. */\n"
         "\tCGRP_KILL,\n"
         "\n"
         "\t/*\n"
         "\t * ABK stable_515_backport: PSI accounting is switched off for this\n"
         "\t * cgroup (cgroup.pressure, android14-6.1).  struct psi_group is\n"
         "\t * embedded in struct cgroup, so the ACK \"enabled\" member cannot be\n"
         "\t * added to it; a flag bit moves no member.\n"
         "\t */\n"
         "\tCGRP_PSI_DISABLED,\n"
         "};",
         T),
        # psi.h: what the cgroup file needs from psi.c
        ("include/linux/psi.h",
         "#ifdef CONFIG_CGROUPS\n"
         "int psi_cgroup_alloc(struct cgroup *cgrp);\n"
         "void psi_cgroup_free(struct cgroup *cgrp);\n"
         "void cgroup_move_task(struct task_struct *p, struct css_set *to);\n"
         "#endif",
         "#ifdef CONFIG_CGROUPS\n"
         "int psi_cgroup_alloc(struct cgroup *cgrp);\n"
         "void psi_cgroup_free(struct cgroup *cgrp);\n"
         "void cgroup_move_task(struct task_struct *p, struct css_set *to);\n"
         "\n"
         "/* ABK stable_515_backport: per-cgroup PSI accounting switch\n"
         " * (cgroup.pressure, android14-6.1).\n"
         " */\n"
         "bool psi_cgroup_accounting_enabled(struct cgroup *cgrp);\n"
         "void psi_cgroup_accounting_set(struct cgroup *cgrp, bool enable);\n"
         "void psi_cgroup_restart(struct psi_group *group);\n"
         "#endif",
         T),
        # psi.c: the helper every accounting path asks, plus the system group's
        # own switch (psi_system has no cgroup to carry a flag)
        ("kernel/sched/psi.c",
         "static void psi_group_change(struct psi_group *group, int cpu,\n"
         "\t\t\t     unsigned int clear, unsigned int set, u64 now,\n"
         "\t\t\t     bool wake_clock)\n",
         "/* ABK stable_515_backport: per-cgroup PSI accounting switch\n"
         " * (cgroup.pressure, android14-6.1).  struct psi_group is embedded in\n"
         " * struct cgroup, so the flag cannot live in the group itself -- a new\n"
         " * member would move struct cgroup's members.  psi_system has no cgroup\n"
         " * of its own (the root cgroup's pressure files are backed by it), so the\n"
         " * root's switch is kept here.\n"
         " */\n"
         "static bool psi_system_accounting_disabled;\n"
         "\n"
         "static bool psi_group_enabled(struct psi_group *group)\n"
         "{\n"
         "#ifdef CONFIG_CGROUPS\n"
         "\tif (group != &psi_system)\n"
         "\t\treturn !test_bit(CGRP_PSI_DISABLED,\n"
         "\t\t\t\t &container_of(group, struct cgroup, psi)->flags);\n"
         "#endif\n"
         "\treturn !psi_system_accounting_disabled;\n"
         "}\n"
         "\n"
         "static void psi_group_change(struct psi_group *group, int cpu,\n"
         "\t\t\t     unsigned int clear, unsigned int set, u64 now,\n"
         "\t\t\t     bool wake_clock)\n",
         T),
        # psi.c: accounting off -- counts stay live, states stop
        ("kernel/sched/psi.c",
         "\tfor (t = 0; set; set &= ~(1 << t), t++)\n"
         "\t\tif (set & (1 << t))\n"
         "\t\t\tgroupc->tasks[t]++;\n"
         "\n"
         "\t/* Calculate state mask representing active states */\n",
         "\tfor (t = 0; set; set &= ~(1 << t), t++)\n"
         "\t\tif (set & (1 << t))\n"
         "\t\t\tgroupc->tasks[t]++;\n"
         "\n"
         "\t/*\n"
         "\t * ABK stable_515_backport: cgroup.pressure -- with the accounting\n"
         "\t * switched off, keep the task counts current (the levels above and\n"
         "\t * below consult them) but stop deriving and timing states.  The\n"
         "\t * record_times() above already concluded the state that was live when\n"
         "\t * the switch was turned off.\n"
         "\t */\n"
         "\tif (unlikely(!psi_group_enabled(group))) {\n"
         "\t\tgroupc->state_mask = 0;\n"
         "\n"
         "\t\twrite_seqcount_end(&groupc->seq);\n"
         "\n"
         "\t\treturn;\n"
         "\t}\n"
         "\n"
         "\t/* Calculate state mask representing active states */\n",
         T),
        # psi.c: the IRQ accounting walk (added by psi_irq_tracking) honours it
        ("kernel/sched/psi.c",
         "\twhile ((group = iterate_groups(curr, &iter))) {\n"
         "\t\tu64 now;\n"
         "\n"
         "\t\tgroupc = per_cpu_ptr(group->pcpu, cpu);\n",
         "\twhile ((group = iterate_groups(curr, &iter))) {\n"
         "\t\tu64 now;\n"
         "\n"
         "\t\tif (!psi_group_enabled(group))\n"
         "\t\t\tcontinue;\n"
         "\n"
         "\t\tgroupc = per_cpu_ptr(group->pcpu, cpu);\n",
         T),
        # psi.c: reports
        ("kernel/sched/psi.c",
         "\tif (static_branch_likely(&psi_disabled))\n"
         "\t\treturn -EOPNOTSUPP;\n"
         "\n"
         "\t/* Update averages before reporting them */\n",
         "\tif (static_branch_likely(&psi_disabled))\n"
         "\t\treturn -EOPNOTSUPP;\n"
         "\n"
         "\t/* ABK stable_515_backport: cgroup.pressure -- a cgroup with the\n"
         "\t * accounting switched off has no pressure data to report.  5.15 has\n"
         "\t * no kernfs_show(), so the file stays visible and says so instead of\n"
         "\t * being hidden with the ACK 6.1 shape.\n"
         "\t */\n"
         "\tif (!psi_group_enabled(group))\n"
         "\t\treturn -EOPNOTSUPP;\n"
         "\n"
         "\t/* Update averages before reporting them */\n",
         T),
        # psi.c: no new trigger on a group that does not account
        ("kernel/sched/psi.c",
         "\tif (static_branch_likely(&psi_disabled))\n"
         "\t\treturn ERR_PTR(-EOPNOTSUPP);\n"
         "\n"
         "\tif (sscanf(buf, \"some %u %u\", &threshold_us, &window_us) == 2)\n",
         "\tif (static_branch_likely(&psi_disabled))\n"
         "\t\treturn ERR_PTR(-EOPNOTSUPP);\n"
         "\n"
         "\t/* ABK stable_515_backport: cgroup.pressure -- a group with the\n"
         "\t * accounting switched off never grows a state to trigger on.\n"
         "\t */\n"
         "\tif (!psi_group_enabled(group))\n"
         "\t\treturn ERR_PTR(-EOPNOTSUPP);\n"
         "\n"
         "\tif (sscanf(buf, \"some %u %u\", &threshold_us, &window_us) == 2)\n",
         T),
        # psi.c: the accessors and the re-enable sync
        ("kernel/sched/psi.c",
         "\ttask_rq_unlock(rq, task, &rf);\n"
         "}\n"
         "#endif /* CONFIG_CGROUPS */",
         "\ttask_rq_unlock(rq, task, &rf);\n"
         "}\n"
         "\n"
         "/* ABK stable_515_backport: cgroup.pressure accessors (android14-6.1).\n"
         " * The root cgroup's pressure files are backed by psi_system, not by an\n"
         " * embedded group, so its switch lives in this file rather than in a\n"
         " * cgroup flag.\n"
         " */\n"
         "bool psi_cgroup_accounting_enabled(struct cgroup *cgrp)\n"
         "{\n"
         "\tif (cgroup_ino(cgrp) == 1)\n"
         "\t\treturn !psi_system_accounting_disabled;\n"
         "\n"
         "\treturn !test_bit(CGRP_PSI_DISABLED, &cgrp->flags);\n"
         "}\n"
         "\n"
         "void psi_cgroup_accounting_set(struct cgroup *cgrp, bool enable)\n"
         "{\n"
         "\tif (cgroup_ino(cgrp) == 1) {\n"
         "\t\tpsi_system_accounting_disabled = !enable;\n"
         "\t\treturn;\n"
         "\t}\n"
         "\n"
         "\tif (enable)\n"
         "\t\tclear_bit(CGRP_PSI_DISABLED, &cgrp->flags);\n"
         "\telse\n"
         "\t\tset_bit(CGRP_PSI_DISABLED, &cgrp->flags);\n"
         "}\n"
         "\n"
         "void psi_cgroup_restart(struct psi_group *group)\n"
         "{\n"
         "\tint cpu;\n"
         "\n"
         "\t/*\n"
         "\t * Switching the accounting off needs no sync: psi_group_change() sees\n"
         "\t * the switch and only keeps the task accounting.  Switching it back on\n"
         "\t * has to rebuild every CPU's state mask from the counts that were kept,\n"
         "\t * and restart the state clock, or the group stays silent.\n"
         "\t */\n"
         "\tif (!psi_group_enabled(group))\n"
         "\t\treturn;\n"
         "\n"
         "\tfor_each_possible_cpu(cpu) {\n"
         "\t\tstruct rq *rq = cpu_rq(cpu);\n"
         "\t\tstruct rq_flags rf;\n"
         "\n"
         "\t\trq_lock_irq(rq, &rf);\n"
         "\t\tpsi_group_change(group, cpu, 0, 0, cpu_clock(cpu), true);\n"
         "\t\trq_unlock_irq(rq, &rf);\n"
         "\t}\n"
         "}\n"
         "#endif /* CONFIG_CGROUPS */",
         T),
        # cgroup.c: the trigger writer yields its name to the switch, as it does
        # in the ACK tree (where it became pressure_write())
        ("kernel/cgroup/cgroup.c",
         "static ssize_t cgroup_pressure_write(struct kernfs_open_file *of, char *buf,\n"
         "\t\t\t\t\t  size_t nbytes, enum psi_res res)\n",
         "/* ABK stable_515_backport: renamed for the cgroup.pressure switch below,\n"
         " * which takes the cgroup_pressure_write() name as in android14-6.1.\n"
         " */\n"
         "static ssize_t pressure_write(struct kernfs_open_file *of, char *buf,\n"
         "\t\t\t      size_t nbytes, enum psi_res res)\n",
         T),
        ("kernel/cgroup/cgroup.c",
         "\treturn cgroup_pressure_write(of, buf, nbytes, PSI_IO);\n",
         "\treturn pressure_write(of, buf, nbytes, PSI_IO);\n",
         T),
        ("kernel/cgroup/cgroup.c",
         "\treturn cgroup_pressure_write(of, buf, nbytes, PSI_MEM);\n",
         "\treturn pressure_write(of, buf, nbytes, PSI_MEM);\n",
         T),
        ("kernel/cgroup/cgroup.c",
         "\treturn cgroup_pressure_write(of, buf, nbytes, PSI_CPU);\n",
         "\treturn pressure_write(of, buf, nbytes, PSI_CPU);\n",
         T),
        # cgroup.c: the knob itself
        ("kernel/cgroup/cgroup.c",
         "static __poll_t cgroup_pressure_poll(struct kernfs_open_file *of,\n"
         "\t\t\t\t\t  poll_table *pt)\n",
         "/* ABK stable_515_backport: cgroup.pressure -- the per-cgroup PSI\n"
         " * accounting switch of android14-6.1.  Upstream keeps the state in\n"
         " * struct psi_group; here it is the cgroup's CGRP_PSI_DISABLED flag bit\n"
         " * (see psi.c), which leaves both struct layouts alone.  Upstream also\n"
         " * hides the pressure files while accounting is off; 5.15 has no\n"
         " * kernfs_show(), so they report EOPNOTSUPP instead of being hidden.\n"
         " */\n"
         "static int cgroup_pressure_show(struct seq_file *seq, void *v)\n"
         "{\n"
         "\tstruct cgroup *cgrp = seq_css(seq)->cgroup;\n"
         "\n"
         "\tseq_printf(seq, \"%d\\n\", psi_cgroup_accounting_enabled(cgrp));\n"
         "\n"
         "\treturn 0;\n"
         "}\n"
         "\n"
         "static ssize_t cgroup_pressure_write(struct kernfs_open_file *of,\n"
         "\t\t\t\t     char *buf, size_t nbytes,\n"
         "\t\t\t\t     loff_t off)\n"
         "{\n"
         "\tssize_t ret;\n"
         "\tint enable;\n"
         "\tstruct cgroup *cgrp;\n"
         "\n"
         "\tret = kstrtoint(strstrip(buf), 0, &enable);\n"
         "\tif (ret)\n"
         "\t\treturn ret;\n"
         "\n"
         "\tif (enable < 0 || enable > 1)\n"
         "\t\treturn -ERANGE;\n"
         "\n"
         "\tcgrp = cgroup_kn_lock_live(of->kn, false);\n"
         "\tif (!cgrp)\n"
         "\t\treturn -ENOENT;\n"
         "\n"
         "\tif (psi_cgroup_accounting_enabled(cgrp) != enable) {\n"
         "\t\tpsi_cgroup_accounting_set(cgrp, enable);\n"
         "\t\tif (enable)\n"
         "\t\t\tpsi_cgroup_restart(cgroup_ino(cgrp) == 1 ?\n"
         "\t\t\t\t\t   &psi_system : &cgrp->psi);\n"
         "\t}\n"
         "\n"
         "\tcgroup_kn_unlock(of->kn);\n"
         "\n"
         "\treturn nbytes;\n"
         "}\n"
         "\n"
         "static __poll_t cgroup_pressure_poll(struct kernfs_open_file *of,\n"
         "\t\t\t\t\t  poll_table *pt)\n",
         T),
        # cgroup.c: the file, next to the pressure files it controls
        ("kernel/cgroup/cgroup.c",
         "\t{\n"
         "\t\t.name = \"cpu.pressure\",\n"
         "\t\t.flags = CFTYPE_PRESSURE,\n"
         "\t\t.seq_show = cgroup_cpu_pressure_show,\n"
         "\t\t.write = cgroup_cpu_pressure_write,\n"
         "\t\t.poll = cgroup_pressure_poll,\n"
         "\t\t.release = cgroup_pressure_release,\n"
         "\t},\n",
         "\t{\n"
         "\t\t.name = \"cpu.pressure\",\n"
         "\t\t.flags = CFTYPE_PRESSURE,\n"
         "\t\t.seq_show = cgroup_cpu_pressure_show,\n"
         "\t\t.write = cgroup_cpu_pressure_write,\n"
         "\t\t.poll = cgroup_pressure_poll,\n"
         "\t\t.release = cgroup_pressure_release,\n"
         "\t},\n"
         "\t{\n"
         "\t\t/* ABK stable_515_backport: per-cgroup PSI accounting switch\n"
         "\t\t * (android14-6.1).  CFTYPE_PRESSURE makes it appear and\n"
         "\t\t * disappear together with the pressure files it controls.\n"
         "\t\t */\n"
         "\t\t.name = \"cgroup.pressure\",\n"
         "\t\t.flags = CFTYPE_PRESSURE,\n"
         "\t\t.seq_show = cgroup_pressure_show,\n"
         "\t\t.write = cgroup_pressure_write,\n"
         "\t},\n",
         T),
        # Documentation: the knob is user-visible, so it is documented
        ("Documentation/admin-guide/cgroup-v2.rst",
         "\tIn a threaded cgroup, writing this file fails with EOPNOTSUPP as\n"
         "\tkilling cgroups is a process directed operation, i.e. it affects\n"
         "\tthe whole thread-group.\n"
         "\n"
         "Controllers\n"
         "===========\n",
         "\tIn a threaded cgroup, writing this file fails with EOPNOTSUPP as\n"
         "\tkilling cgroups is a process directed operation, i.e. it affects\n"
         "\tthe whole thread-group.\n"
         "\n"
         "  cgroup.pressure\n"
         "\tA read-write single value file that allowed values are \"0\" and \"1\".\n"
         "\tThe default is \"1\".\n"
         "\n"
         "\tWriting \"0\" to the file will disable the cgroup PSI accounting.\n"
         "\tWriting \"1\" to the file will re-enable the cgroup PSI accounting.\n"
         "\n"
         "\tThis control attribute is not hierarchical, so disable or enable PSI\n"
         "\taccounting in a cgroup does not affect PSI accounting in descendants\n"
         "\tand doesn't need pass enablement via ancestors from root.\n"
         "\n"
         "\tThe reason this control attribute exists is that PSI accounts stalls for\n"
         "\teach cgroup separately and aggregates it at each level of the hierarchy.\n"
         "\tThis may cause non-negligible overhead for some workloads when under\n"
         "\tdeep level of the hierarchy, in which case this control attribute can\n"
         "\tbe used to disable PSI accounting in the non-leaf cgroups.\n"
         "\n"
         "\tWritten on the root cgroup it controls the system-wide pressure\n"
         "\taccounting reported by the /proc/pressure files.\n"
         "\n"
         "\tWhile the accounting of a cgroup is disabled, its pressure files report\n"
         "\t\"operation not supported\" instead of a stale value.\n"
         "\n"
         "Controllers\n"
         "===========\n",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# android14-6.1 line: TSK_ONCPU becomes a bit of the state mask
# ---------------------------------------------------------------------------

# The last PSI item on the 6.1 backlog (plan.md: "PSI 内部全量同步").  Upstream
# counts ONCPU like any other task state, which is one counter more than the
# hardware can justify -- only one task is ever scheduled on a CPU -- and makes
# the scheduler/psi hand-off racy: psi_task_switch() has to guess when to stop
# setting ONCPU on @next's ancestors (the old code needed an "identical state"
# comparison to decide), and a mismatch shows up as the "psi: task underflow!"
# splat.  The ACK 6.1 shape drops the counter: TSK_ONCPU is a flag carried in
# the same state mask that holds the derived states, so setting it where it is
# already set is idempotent and the switch can stop at the first ancestor that
# already has it.
#
# The ancestor walk stays the 5.15 iterate_groups() cgroup-tree walk (the ACK
# tree's psi_group::parent pointer is still not needed and must not be added --
# struct psi_group is embedded in struct cgroup).  psi_group_cpu is
# percpu-internal, so dropping tasks[NR_ONCPU] changes no KMI-visible layout.

def _psi_oncpu_state_mask_apply(ctx):
    try:
        text = ctx.read("include/linux/psi_types.h")
    except FileNotFoundError:
        text = ""
    if "PSI_ONCPU" in text:
        return "already_present", (
            "TSK_ONCPU is already a state-mask bit")
    steps = [
        # psi_types.h: NR_ONCPU is not a task count any more
        ("include/linux/psi_types.h",
         "\tNR_RUNNING,\n"
         "\t/*\n"
         "\t * This can't have values other than 0 or 1 and could be\n"
         "\t * implemented as a bit flag. But for now we still have room\n"
         "\t * in the first cacheline of psi_group_cpu, and this way we\n"
         "\t * don't have to special case any state tracking for it.\n"
         "\t */\n"
         "\tNR_ONCPU,\n"
         "\t/*\n"
         "\t * For IO and CPU stalls the presence of running/oncpu tasks\n"
         "\t * in the domain means a partial rather than a full stall.\n",
         "\tNR_RUNNING,\n"
         "\t/*\n"
         "\t * For IO and CPU stalls the presence of running/oncpu tasks\n"
         "\t * in the domain means a partial rather than a full stall.\n",
         T),
        ("include/linux/psi_types.h",
         "\tNR_MEMSTALL_RUNNING,\n"
         "\tNR_PSI_TASK_COUNTS = 5,\n"
         "};",
         "\tNR_MEMSTALL_RUNNING,\n"
         "\tNR_PSI_TASK_COUNTS = 4,\n"
         "};",
         T),
        ("include/linux/psi_types.h",
         "#define TSK_ONCPU\t(1 << NR_ONCPU)\n",
         "/* ABK stable_515_backport: only one task can be scheduled on a CPU, so\n"
         " * TSK_ONCPU is a flag in the state mask rather than a task count\n"
         " * (android14-6.1).\n"
         " */\n"
         "#define TSK_ONCPU\t(1 << NR_PSI_TASK_COUNTS)\n",
         T),
        ("include/linux/psi_types.h",
         "\t/* Only per-CPU, to weigh the CPU in the global average: */\n"
         "\tPSI_NONIDLE,\n"
         "\tNR_PSI_STATES,\n"
         "};",
         "\t/* Only per-CPU, to weigh the CPU in the global average: */\n"
         "\tPSI_NONIDLE,\n"
         "\tNR_PSI_STATES,\n"
         "};\n"
         "\n"
         "/* ABK stable_515_backport: use one bit in the state mask to track\n"
         " * TSK_ONCPU (android14-6.1).\n"
         " */\n"
         "#define PSI_ONCPU\t(1 << NR_PSI_STATES)\n",
         T),
        # psi.c: test_state asks the mask instead of counting
        ("kernel/sched/psi.c",
         "static bool test_state(unsigned int *tasks, enum psi_states state)\n"
         "{\n"
         "\tswitch (state) {\n"
         "\tcase PSI_IO_SOME:\n",
         "static bool test_state(unsigned int *tasks, enum psi_states state, bool oncpu)\n"
         "{\n"
         "\tswitch (state) {\n"
         "\tcase PSI_IO_SOME:\n",
         T),
        ("kernel/sched/psi.c",
         "\tcase PSI_CPU_SOME:\n"
         "\t\treturn unlikely(tasks[NR_RUNNING] > tasks[NR_ONCPU]);\n"
         "\tcase PSI_CPU_FULL:\n"
         "\t\treturn unlikely(tasks[NR_RUNNING] && !tasks[NR_ONCPU]);\n",
         "\tcase PSI_CPU_SOME:\n"
         "\t\treturn unlikely(tasks[NR_RUNNING] > oncpu);\n"
         "\tcase PSI_CPU_FULL:\n"
         "\t\treturn unlikely(tasks[NR_RUNNING] && !oncpu);\n",
         T),
        # psi.c: the flag is set, cleared or carried before the counts are
        # touched, so it never reaches the count loops
        ("kernel/sched/psi.c",
         "\twrite_seqcount_begin(&groupc->seq);\n"
         "\n"
         "\trecord_times(groupc, now);\n"
         "\n"
         "\tfor (t = 0, m = clear; m; m &= ~(1 << t), t++) {\n",
         "\twrite_seqcount_begin(&groupc->seq);\n"
         "\n"
         "\trecord_times(groupc, now);\n"
         "\n"
         "\t/*\n"
         "\t * ABK stable_515_backport: TSK_ONCPU has no task count -- only one\n"
         "\t * task can be scheduled on a CPU, so it is a flag carried in the\n"
         "\t * state mask (android14-6.1).  Set, clear or carry it, then keep it\n"
         "\t * out of the count update below.\n"
         "\t */\n"
         "\tif (unlikely(clear & TSK_ONCPU)) {\n"
         "\t\tstate_mask = 0;\n"
         "\t\tclear &= ~TSK_ONCPU;\n"
         "\t} else if (unlikely(set & TSK_ONCPU)) {\n"
         "\t\tstate_mask = PSI_ONCPU;\n"
         "\t\tset &= ~TSK_ONCPU;\n"
         "\t} else {\n"
         "\t\tstate_mask = groupc->state_mask & PSI_ONCPU;\n"
         "\t}\n"
         "\n"
         "\tfor (t = 0, m = clear; m; m &= ~(1 << t), t++) {\n",
         T),
        # psi.c: four counters left in the underflow splat
        ("kernel/sched/psi.c",
         "\t\t\tprintk_deferred(KERN_ERR \"psi: task underflow! cpu=%d t=%d tasks=[%u %u %u %u %u] clear=%x set=%x\\n\",\n"
         "\t\t\t\t\tcpu, t, groupc->tasks[0],\n"
         "\t\t\t\t\tgroupc->tasks[1], groupc->tasks[2],\n"
         "\t\t\t\t\tgroupc->tasks[3], groupc->tasks[4],\n"
         "\t\t\t\t\tclear, set);\n",
         "\t\t\tprintk_deferred(KERN_ERR \"psi: task underflow! cpu=%d t=%d tasks=[%u %u %u %u] clear=%x set=%x\\n\",\n"
         "\t\t\t\t\tcpu, t, groupc->tasks[0],\n"
         "\t\t\t\t\tgroupc->tasks[1], groupc->tasks[2],\n"
         "\t\t\t\t\tgroupc->tasks[3], clear, set);\n",
         T),
        # psi.c: the cgroup.pressure switch keeps the flag, not just the counts
        ("kernel/sched/psi.c",
         "\t * ABK stable_515_backport: cgroup.pressure -- with the accounting\n"
         "\t * switched off, keep the task counts current (the levels above and\n"
         "\t * below consult them) but stop deriving and timing states.  The\n"
         "\t * record_times() above already concluded the state that was live when\n"
         "\t * the switch was turned off.\n"
         "\t */\n"
         "\tif (unlikely(!psi_group_enabled(group))) {\n"
         "\t\tgroupc->state_mask = 0;\n",
         "\t * ABK stable_515_backport: cgroup.pressure -- with the accounting\n"
         "\t * switched off, keep the task counts and the ONCPU flag current (the\n"
         "\t * levels above and below consult them) but stop deriving and timing\n"
         "\t * states.  The record_times() above already concluded the state that\n"
         "\t * was live when the switch was turned off.\n"
         "\t */\n"
         "\tif (unlikely(!psi_group_enabled(group))) {\n"
         "\t\tgroupc->state_mask = state_mask;\n",
         T),
        ("kernel/sched/psi.c",
         "\t/* Calculate state mask representing active states */\n"
         "\tfor (s = 0; s < NR_PSI_STATES; s++) {\n"
         "\t\tif (test_state(groupc->tasks, s))\n"
         "\t\t\tstate_mask |= (1 << s);\n"
         "\t}\n",
         "\t/* Calculate state mask representing active states */\n"
         "\tfor (s = 0; s < NR_PSI_STATES; s++) {\n"
         "\t\tif (test_state(groupc->tasks, s, state_mask & PSI_ONCPU))\n"
         "\t\t\tstate_mask |= (1 << s);\n"
         "\t}\n",
         T),
        ("kernel/sched/psi.c",
         "\tif (unlikely(groupc->tasks[NR_ONCPU] && cpu_curr(cpu)->in_memstall))\n",
         "\tif (unlikely((state_mask & PSI_ONCPU) && cpu_curr(cpu)->in_memstall))\n",
         T),
        # psi.c: the switch stops at the first ancestor that already carries the
        # flag, which is the common ancestor -- no state comparison needed
        ("kernel/sched/psi.c",
         "\tif (next->pid) {\n"
         "\t\tbool identical_state;\n"
         "\n"
         "\t\tpsi_flags_change(next, 0, TSK_ONCPU);\n"
         "\t\t/*\n"
         "\t\t * When switching between tasks that have an identical\n"
         "\t\t * runtime state, the cgroup that contains both tasks\n"
         "\t\t * runtime state, the cgroup that contains both tasks\n"
         "\t\t * we reach the first common ancestor. Iterate @next's\n"
         "\t\t * ancestors only until we encounter @prev's ONCPU.\n"
         "\t\t */\n"
         "\t\tidentical_state = prev->psi_flags == next->psi_flags;\n"
         "\t\titer = NULL;\n"
         "\t\twhile ((group = iterate_groups(next, &iter))) {\n"
         "\t\t\tif (identical_state &&\n"
         "\t\t\t    per_cpu_ptr(group->pcpu, cpu)->tasks[NR_ONCPU]) {\n"
         "\t\t\t\tcommon = group;\n"
         "\t\t\t\tbreak;\n"
         "\t\t\t}\n"
         "\n"
         "\t\t\tpsi_group_change(group, cpu, 0, TSK_ONCPU, now, true);\n"
         "\t\t}\n"
         "\t}\n",
         "\tif (next->pid) {\n"
         "\t\tpsi_flags_change(next, 0, TSK_ONCPU);\n"
         "\t\t/*\n"
         "\t\t * Set TSK_ONCPU on @next's cgroups. If @next shares any\n"
         "\t\t * ancestors with @prev, those will already have @prev's\n"
         "\t\t * TSK_ONCPU bit set, and we can stop the iteration there.\n"
         "\t\t */\n"
         "\t\titer = NULL;\n"
         "\t\twhile ((group = iterate_groups(next, &iter))) {\n"
         "\t\t\tif (per_cpu_ptr(group->pcpu, cpu)->state_mask &\n"
         "\t\t\t    PSI_ONCPU) {\n"
         "\t\t\t\tcommon = group;\n"
         "\t\t\t\tbreak;\n"
         "\t\t\t}\n"
         "\n"
         "\t\t\tpsi_group_change(group, cpu, 0, TSK_ONCPU, now, true);\n"
         "\t\t}\n"
         "\t}\n",
         T),
        # psi.c: ... so everything else has to keep propagating above it
        ("kernel/sched/psi.c",
         "\t\t/*\n"
         "\t\t * TSK_ONCPU is handled up to the common ancestor. If we're tasked\n"
         "\t\t * with dequeuing too, finish that for the rest of the hierarchy.\n"
         "\t\t */\n"
         "\t\tif (sleep) {\n"
         "\t\t\tclear &= ~TSK_ONCPU;\n"
         "\t\t\tfor (; group; group = iterate_groups(prev, &iter))\n"
         "\t\t\t\tpsi_group_change(group, cpu, clear, set, now, true);\n"
         "\t\t}\n",
         "\t\t/*\n"
         "\t\t * TSK_ONCPU is handled up to the common ancestor. If there are\n"
         "\t\t * any other differences between the two tasks (e.g. prev goes\n"
         "\t\t * to sleep, or only one task is memstall), finish propagating\n"
         "\t\t * those differences all the way up to the root.\n"
         "\t\t */\n"
         "\t\tif ((prev->psi_flags ^ next->psi_flags) & ~TSK_ONCPU) {\n"
         "\t\t\tclear &= ~TSK_ONCPU;\n"
         "\t\t\tfor (; group; group = iterate_groups(prev, &iter))\n"
         "\t\t\t\tpsi_group_change(group, cpu, clear, set, now, true);\n"
         "\t\t}\n",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


PATCH_GROUPS = [
    PatchGroup(
        "sched_nohz_idle_balance_series",
        "NOHZ idle balance: scoped kicks, no ksoftirqd wakeup, raw softirq raise (5.15.174)",
        ["d071dba5ddd2 (5.15.174)", "6aeeac48fc1b (5.15.174)", "38a4826f1bdf (5.15.174)", "25fc82f3a868 (5.15.174)"],
        ["kernel/sched/sched.h", "kernel/sched/fair.c", "kernel/sched/core.c"],
        _nohz_apply,
    ),
    PatchGroup(
        "sched_psi_flags_migration",
        "PSI CPU migration switches task states via psi_flags delta (5.15.179)",
        ["b3a5ff8c4b6e (5.15.179)"],
        ["include/linux/sched.h", "kernel/sched/core.c", "kernel/sched/stats.h"],
        _psi_flags_apply,
    ),
    PatchGroup(
        "sched_rt_optimizations",
        "rto_next_cpu skips the current CPU; RT_PUSH_IPI defaults off on non-RT (5.15.202/.212)",
        ["3b3c672a66db (5.15.202)", "d8312a56d9a1 (5.15.212)"],
        ["kernel/sched/rt.c", "kernel/sched/features.h"],
        _rt_optimizations_apply,
    ),
    PatchGroup(
        "sched_dst_group_allowed_stats",
        "update_sg_wakeup_stats counts only CPUs allowed for p, fixing wake imbalance for affinity-restricted forks (5.15.212)",
        ["d99f14f8b142 (5.15.212)"],
        ["kernel/sched/fair.c"],
        _dst_group_allowed_stats_apply,
    ),
    PatchGroup(
        "sched_steal_time_excess_drop",
        "excess steal time is dropped instead of catching up later (5.15.179)",
        ["56135262c1f9 (5.15.179)"],
        ["kernel/sched/core.c"],
        _sched_steal_time_drop_apply,
    ),
    PatchGroup(
        "randomize_kstack_pertask",
        "kstack randomization offset becomes per-task, extending entropy lifetime (5.15.210)",
        ["7e1b6b281aa8 (5.15.210)"],
        ["include/linux/sched.h", "include/linux/randomize_kstack.h", "init/main.c", "kernel/fork.c"],
        _kstack_pertask_apply,
    ),
    PatchGroup(
        "release_sock_cond_resched",
        "__release_sock() yields only every 16 processed skbs (5.15.197)",
        ["66bcd6c577d8 (5.15.197)"],
        ["net/core/sock.c"],
        _release_sock_apply,
    ),
    PatchGroup(
        "semaphore_wake_q",
        "semaphore up() wakes waiters outside the lock via wake_q (5.15.180)",
        ["46c66d975a58 (5.15.180)"],
        ["kernel/locking/semaphore.c"],
        _semaphore_wake_q_apply,
    ),
    PatchGroup(
        "blk_mq_suspend_wakeup_abort",
        "blk-mq hctx offline wait aborts when pm_wakeup_pending() (5.15.198)",
        ["8fe7de5d1c7f (5.15.198)"],
        ["block/blk-mq.c"],
        _blk_mq_suspend_apply,
    ),
    PatchGroup(
        "blk_mq_quiesced_elevator_switch",
        "blk-mq reinitialisation switches elevators through the quiesced entry point (5.15.209)",
        ["9646443f28f3 (5.15.209)"],
        ["block/blk-mq.c", "block/blk.h", "block/elevator.c"],
        _blk_mq_quiesced_elevator_apply,
    ),
    PatchGroup(
        "sched_lazy_preemption_hooks",
        "lazy preemption scheduling hooks: bounded resched deferral in tick/wakeup/schedule (android14-6.1)",
        ["ACK android14-6.1 lazy preemption via hooks (969cb3d family)"],
        ["include/trace/hooks/dtask.h", "kernel/sched/core.c", "kernel/sched/fair.c"],
        _lazy_preempt_hooks_apply,
    ),
    PatchGroup(
        "locking_wakeup_patch_hooks",
        "mutex/rwsem post-wakeup fixup vendor hooks (android14-6.1)",
        ["ACK android14-6.1 locking wakeup patch hooks (dfdcb1d)"],
        ["include/trace/hooks/dtask.h", "include/trace/hooks/rwsem.h", "kernel/locking/mutex.c", "kernel/locking/rwsem.c"],
        _locking_wakeup_patch_apply,
    ),
    PatchGroup(
        "psi_irq_tracking",
        "PSI_IRQ pressure tracking with /proc/pressure/irq, adapted to the 5.15 group walk (android14-6.1 / 6.1)",
        ["52b1364 (6.1) + ACK android14-6.1 adaptations"],
        ["include/linux/psi_types.h", "kernel/sched/psi.c", "kernel/sched/stats.h", "kernel/sched/core.c"],
        _psi_irq_tracking_apply,
    ),
    PatchGroup(
        "psi_trigger_kernfs_polling",
        "PSI trigger events delivered via kernfs polling with deferred-event ratelimiting and 1us windows (android14-6.1)",
        ["ACK android14-6.1 kernfs PSI polling backport (c1496f6 family)"],
        ["include/linux/psi_types.h", "include/linux/psi.h", "kernel/sched/psi.c", "kernel/cgroup/cgroup.c"],
        _psi_kernfs_polling_apply,
    ),
    PatchGroup(
        "psi_cgroup_pressure_switch",
        "cgroup.pressure: per-cgroup PSI accounting switch, kept off the struct layouts the ACK 6.1 psi_group pointer would move (android14-6.1)",
        ["ACK android14-6.1 cgroup.pressure switch (psi_group.enabled family)"],
        ["include/linux/cgroup-defs.h", "include/linux/psi.h", "kernel/sched/psi.c",
         "kernel/cgroup/cgroup.c", "Documentation/admin-guide/cgroup-v2.rst"],
        _psi_cgroup_pressure_apply,
    ),
    PatchGroup(
        "psi_oncpu_state_mask",
        "PSI: TSK_ONCPU becomes a flag in the state mask instead of a task count, so the switch walks ancestors until the flag is already set (android14-6.1)",
        ["ACK android14-6.1 PSI ONCPU state-mask rework (psi_group_cpu::tasks[] 5 -> 4)"],
        ["include/linux/psi_types.h", "kernel/sched/psi.c"],
        _psi_oncpu_state_mask_apply,
    ),
]

# ============================================================================
# Batch 10-2 / 10-4c / 10-5: schedutil smart-freq policy layer on PELT
# (inspired by the WALT smart_freq SUSTAINED_HIGH_UTIL reason; see
# walt_pelt_survey.md).  Steps live in scripts/batch10_perf_sched_policy.py:
# per-cluster sustained-high util election sampled in the scheduler tick,
# floored at cpufreq resolve time (android_vh_cpufreq_resolve_freq).
#
# 10-5 is the ownership fix.  A device whose only governor is the vendor walt
# one (FAS driven by a userspace scheduler profile) showed the floor turning
# into a ratchet: waltgov computed 766 MHz from cluster demand while the policy
# sat at 1785600 for a whole game session, because a FAS owner requests
# min == max == its own target and a floor clamped to policy->max simply
# re-states the current frequency.  The policy is now disabled by default and
# refuses to act on any policy that is not plain schedutil or has collapsed to
# a single operating point; a tree already carrying the 10-4c payload is
# upgraded in place (build_upgrade_steps) instead of getting a second copy.
# ============================================================================
import batch10_perf_sched_policy as _b10_sched  # noqa: E402


def _sched_smart_policy_apply(ctx):
    rel = "kernel/sched/cpufreq_schedutil.c"
    try:
        text = ctx.read(rel)
    except FileNotFoundError:
        # Let apply_steps report the missing file as a degraded shape, exactly
        # as it does for any other absent anchor.
        text = ""
    if _b10_sched.has_unknown_policy(text):
        # Batch 10-2 grafted this group once with a different payload, and that
        # shape matches neither migration anchor while still leaving the insert
        # anchor in place.  Taking the plain path there would define the policy
        # twice -- and still report `applied`.  A shape this module cannot name
        # is refused; nothing is written.
        return "blocked_by_shape", (
            "an unrecognised smart-freq payload is already in the tree "
            "(neither the current nor the Batch 10-4c shape); inserting would "
            "duplicate its definitions, so nothing was written")
    upgrade = _b10_sched.needs_legacy_upgrade(text)
    steps = (_b10_sched.build_upgrade_steps() if upgrade
             else _b10_sched.build_steps())
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    if upgrade:
        detail = "upgraded the Batch 10-4c payload in place; " + detail
    # The payload cannot carry its own digest (that would move the digest), so the
    # graft report is where "which generation did this build write" is recorded.
    return status, detail + "; payload " + _b10_sched.policy_sha256()


PATCH_GROUPS = PATCH_GROUPS + [
    PatchGroup(
        "schedutil_smart_policy",
        "schedutil smart-freq policy (PELT): per-cluster sustained-high-util reason election with a sliding window + frequency floor at cpufreq resolve time, via the android_vh hooks; the hooks are governor-independent, so the policy is disabled by default and defers to any policy whose governor is not schedutil or whose range is already pinned (inspired by WALT smart_freq; Batch 10-2, 10-4c, ownership fix in 10-5)",
        [
            "popsicle-w-oss walt smart_freq/pipeline semantics (control-layer subset)",
            "research/popsicle_w_oss/walt_pelt_survey.md",
        ],
        ["kernel/sched/cpufreq_schedutil.c"],
        _sched_smart_policy_apply,
    ),
]

# ============================================================================
# Batch 42: the un-landed half of Batch 10-2's WALT smart_freq scope -- the
# freq_cap[] -> min(freq, cap) clamp.  Steps live in
# scripts/batch42_perf_schedutil_smart_cap.py.
#
# Batch 10-2's scope was "reason election + deactivation hysteresis +
# frequency cap + per-cluster threshold table"; schedutil_smart_policy landed
# the first two (per-cluster sustained-high-util reason election and the
# frequency floor) and the cap never landed.  This group is the cap, from the
# same upstream code (smart_freq.c:433/505, cpufreq_walt.c:264) and on the same
# hook, off by default and gated exactly as hard.
#
# ORDERING -- the reason this group is registered immediately after
# schedutil_smart_policy and not anywhere else in this child:
#
# Both payloads register on android_vh_cpufreq_resolve_freq in the same
# translation unit, and android_vh probes run in registration order, so
# registration order IS execution order.  The clause has to be "cap first,
# floor second": a floor that runs first would raise the target above the cap
# and the cap would be applied to a target it can no longer see.  Both groups
# use late_initcall, so within one translation unit that is text order -- and
# the cap's payload is therefore grafted *in front of* the
# cpufreq_governor_init(schedutil_gov); anchor this group's predecessor is
# appended behind.  That placement is also the only one that is idempotent for
# both groups: inserting after that anchor would sandwich the cap between the
# anchor and the floor payload, so on the second pass the floor group's
# replacement would stop matching while its anchor still did and it would
# append a second floor payload -- the Batch-21 psi_account_irqtime() trap
# (group_recipe.md §2 trap 5).
#
# "Cap first" alone does not settle the conflict, because the floor runs after
# the clamp and can only raise.  The cap therefore keeps a second probe on the
# same hook, registered at late_initcall_sync (which orders after the floor's
# late_initcall), that re-asserts the clamp this pass took -- so while the cap
# owns a policy's range the floor yields, and the two never trade the range
# back and forth.  The floor payload itself is byte-frozen (Batch 10-5's
# migration anchor), so the yield is enforced on the cap's side rather than by
# rewriting code that already-grafted trees would stop recognising.  Both
# payloads expose a read-only node (abk_sc_capped, abk_sf_boosting) so which of
# them owns a range is observable instead of inferred from frequencies.
# ============================================================================
import batch42_perf_schedutil_smart_cap as _b42_cap  # noqa: E402


def _sched_smart_cap_apply(ctx):
    rel = "kernel/sched/cpufreq_schedutil.c"
    try:
        text = ctx.read(rel)
    except FileNotFoundError:
        # Let apply_steps report the missing file as a degraded shape, exactly
        # as it does for any other absent anchor.
        text = ""
    if _b42_cap.has_unknown_cap(text):
        # A cap payload that matches no known anchor still leaves _TAIL_OLD in
        # place, so the plain insert would define the payload twice and the
        # group would still report applied.  Refuse, and write nothing.
        return "blocked_by_shape", (
            "an unrecognised smart-freq cap payload is already in the tree; "
            "inserting would duplicate its definitions, so nothing was "
            "written")
    # The shared header block is added by schedutil_smart_policy's include step
    # when that group runs first (it does: this group is registered after it),
    # so probing for it is what keeps this group's own include step from
    # short-circuiting to already_present.
    steps = _b42_cap.build_steps(
        add_headers=not _b42_cap.has_header_block(text))
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail + "; payload " + _b42_cap.cap_sha256()


PATCH_GROUPS = PATCH_GROUPS + [
    PatchGroup(
        "schedutil_smart_cap",
        "schedutil smart_freq cap (Batch 10-2's un-landed half): clamp the resolved frequency to abk_sc_cap_pct of cpuinfo.max_freq inside android_vh_cpufreq_resolve_freq (fast and slow path), released on the direction of the frequency request after abk_sc_hold_ms and re-asserted only when every CPU of the policy has been below abk_sc_release_pct for abk_sc_release_ms; per-policy state under a raw spinlock (fast_switch runs under rq->lock), off by default and gated to schedutil DVFS ownership exactly like schedutil_smart_policy (inspired by WALT smart_freq)",
        [
            "popsicle-w-oss walt smart_freq freq_cap[] election",
            "research/popsicle_w_oss/walt_extract/smart_freq.c",
            "research/popsicle_w_oss/walt_extract/cpufreq_walt.c",
            "research/popsicle_w_oss/walt_pelt_survey.md",
        ],
        ["kernel/sched/cpufreq_schedutil.c"],
        _sched_smart_cap_apply,
    ),
]

# ============================================================================
# Batch 15: ABK_ABI_PATCH_SUITE absorption -- scheduler refinements + EEVDF.
# Steps live in scripts/batch15_perf_sched_refinements.py and
# scripts/batch15_perf_eevdf.py.
#
# Batch 15 retires the suite-preference rule (docs/porting_policy.md, "Suite
# absorption"), so the suite's scheduler inventory is re-registered here:
#
#   batch15_perf_sched_refinements
#       nohz_field_refinement    names the legacy tick_sched nohz state fields
#                                and adds accessors, so the ~8 read sites stop
#                                open-coding the bit tests.
#       avg_idle_preemption_mode drops the wake_avg_idle prediction while
#                                keeping the direct avg_idle newidle thresholds,
#                                and simplifies the SIS_PROP scan budgeting.
#
#   batch15_perf_eevdf
#       THIS is where this module takes ownership of the sched_entity KABI
#       slots 1-4 (deadline / min_vruntime / vlag / slice) -- the slots the
#       retired red line used to forbid.  Its three groups are registered in a
#       deliberate order, pick_logic BEFORE core_fields and modern_fields, so
#       the slot claim and the cfs_rq accumulators only happen once the fair.c
#       logic has really landed: on anchor drift the slots stay
#       ANDROID_KABI_RESERVE instead of being claimed for code that is not in
#       the tree.
#
#       Batch 28 rebuilt that payload onto the upstream data structure and
#       policy (docs/survey_eevdf_gap.md): the cfs_rq virtual-time accumulators
#       make avg_vruntime() O(1), update_curr() owns the deadline refresh so the
#       selector is O(n) and read-only, and RUN_TO_PARITY / PREEMPT_SHORT /
#       EEVDF wakeup preemption / EEVDF yield / new-task placement are in.
#       The modern_fields group adds the cfs_rq fields and the two switches.
#
# Ordering: both modules are appended after every pre-existing perf group, so
# their fair.c / core.c anchors see the earlier groups' output.  A build that
# injects ABK_ABI_PATCH_SUITE instead of this module keeps the suite's copies;
# the suite must never ride the same build (AGENTS.md -- it would double-claim
# sched_entity 1-4 and request_queue 1).
# ============================================================================
import batch15_perf_sched_refinements as _b15_sched  # noqa: E402
import batch15_perf_eevdf as _b15_eevdf  # noqa: E402
import batch15_perf_blk_mq_async_depth as _b15_blkdepth  # noqa: E402

PATCH_GROUPS = PATCH_GROUPS + _b15_sched.build_groups(PatchGroup)
PATCH_GROUPS = PATCH_GROUPS + _b15_eevdf.build_groups(PatchGroup)

# blk_mq_async_depth: the absorbed queue-depth policy.  This is the group that
# claims request_queue KABI slot 1 (ANDROID_KABI_USE(1, unsigned int async_depth))
# -- the other half of the retired red line.  It shares no anchor text with
# blk_mq_suspend_wakeup_abort (that one owns the blk-mq.c #include pair and
# blk_mq_hctx_notify_offline(); this one owns __blk_mq_alloc_request(),
# blk_mq_init_allocated_queue() and blk_mq_update_nr_requests()), so their
# relative order does not matter.
PATCH_GROUPS = PATCH_GROUPS + _b15_blkdepth.build_groups(PatchGroup)


# ---------------------------------------------------------------------------
# Batch 50 (Tier A1): sched_sis_util.  Source: 70fb5ccf2ebb "sched/fair:
# Introduce SIS_UTIL to search idle CPU based on sum of util_avg" (mainline
# v6.0; shipped on android15-6.6).  Registered after the Batch 15 appends on
# purpose: batch15_perf_sched_refinements rewrites select_idle_cpu() in place,
# so every anchor below is the post-Batch-15 text.
# ---------------------------------------------------------------------------

_SIS_UTIL_SCAN_FN = (
    "/* sailboat_sched_sis_util: derive the LLC idle-scan depth from sum_util. */\n"
    "static void update_idle_cpu_scan(struct lb_env *env,\n"
    "\t\t\t\t unsigned long sum_util)\n"
    "{\n"
    "\tstruct sched_domain_shared *sd_share;\n"
    "\tint llc_weight, pct;\n"
    "\tu64 x, y, tmp;\n"
    "\t/*\n"
    "\t * Update the number of CPUs to scan in LLC domain, which could\n"
    "\t * be used as a hint in select_idle_cpu(). The update of sd_share\n"
    "\t * could be expensive because it is within a shared cache line.\n"
    "\t * So the write of this hint only occurs during periodic load\n"
    "\t * balancing, rather than CPU_NEWLY_IDLE, because the latter\n"
    "\t * can fire way more frequently than the former.\n"
    "\t */\n"
    "\tif (!sched_feat(SIS_UTIL) || env->idle == CPU_NEWLY_IDLE)\n"
    "\t\treturn;\n"
    "\n"
    "\tllc_weight = per_cpu(sd_llc_size, env->dst_cpu);\n"
    "\tif (env->sd->span_weight != llc_weight)\n"
    "\t\treturn;\n"
    "\n"
    "\tsd_share = rcu_dereference(per_cpu(sd_llc_shared, env->dst_cpu));\n"
    "\tif (!sd_share)\n"
    "\t\treturn;\n"
    "\n"
    "\t/*\n"
    "\t * The number of CPUs to search drops as sum_util increases, when\n"
    "\t * sum_util hits 85% or above, the scan stops.\n"
    "\t * The reason to choose 85% as the threshold is because this is the\n"
    "\t * imbalance_pct(117) when a LLC sched group is overloaded.\n"
    "\t *\n"
    "\t * let y = SCHED_CAPACITY_SCALE - p * x^2                       [1]\n"
    "\t * and y'= y / SCHED_CAPACITY_SCALE\n"
    "\t *\n"
    "\t * x is the ratio of sum_util compared to the CPU capacity:\n"
    "\t * x = sum_util / (llc_weight * SCHED_CAPACITY_SCALE)\n"
    "\t * y' is the ratio of CPUs to be scanned in the LLC domain,\n"
    "\t * and the number of CPUs to scan is calculated by:\n"
    "\t *\n"
    "\t * nr_scan = llc_weight * y'                                    [2]\n"
    "\t *\n"
    "\t * When x hits the threshold of overloaded, AKA, when\n"
    "\t * x = 100 / pct, y drops to 0. According to [1],\n"
    "\t * p should be SCHED_CAPACITY_SCALE * pct^2 / 10000\n"
    "\t *\n"
    "\t * Scale x by SCHED_CAPACITY_SCALE:\n"
    "\t * x' = sum_util / llc_weight;                                  [3]\n"
    "\t *\n"
    "\t * and finally [1] becomes:\n"
    "\t * y = SCHED_CAPACITY_SCALE -\n"
    "\t *     x'^2 * pct^2 / (10000 * SCHED_CAPACITY_SCALE)            [4]\n"
    "\t *\n"
    "\t */\n"
    "\t/* equation [3] */\n"
    "\tx = sum_util;\n"
    "\tdo_div(x, llc_weight);\n"
    "\n"
    "\t/* equation [4] */\n"
    "\tpct = env->sd->imbalance_pct;\n"
    "\ttmp = x * x * pct * pct;\n"
    "\tdo_div(tmp, 10000 * SCHED_CAPACITY_SCALE);\n"
    "\ttmp = min_t(long, tmp, SCHED_CAPACITY_SCALE);\n"
    "\ty = SCHED_CAPACITY_SCALE - tmp;\n"
    "\n"
    "\t/* equation [2] */\n"
    "\ty *= llc_weight;\n"
    "\tdo_div(y, SCHED_CAPACITY_SCALE);\n"
    "\tif ((int)y != sd_share->nr_idle_scan)\n"
    "\t\tWRITE_ONCE(sd_share->nr_idle_scan, (int)y);\n"
    "}\n"
    "\n"
)

def _sis_util_apply(ctx):
    """SIS_UTIL: bound the LLC idle-CPU scan from the load balancer's hint.

    The upstream commit also disables the SIS_PROP arms; the android15-6.6 line
    ships exactly that shape (SIS_PROP=false, SIS_UTIL=true) and running both
    would compute nr twice per wakeup.  batch15_perf_sched_refinements'
    avg_idle_preemption_mode is therefore superseded on this knob -- its field
    retirement (rq->wake_avg_idle / rq->wake_stamp) remains in force.
    """
    status, _results, detail = apply_steps(ctx, [
        # struct sched_domain_shared::nr_idle_scan.  android14-6.1 places it
        # before ANDROID_VENDOR_DATA(1); mirrored so the vendor slot keeps its
        # offset.
        ("include/linux/sched/topology.h",
         "\tatomic_t\tnr_busy_cpus;\n"
         "\tint\t\thas_idle_cores;\n",
         "\tatomic_t\tnr_busy_cpus;\n"
         "\tint\t\thas_idle_cores;\n"
         "\tint\t\tnr_idle_scan;\t/* sailboat_sched_sis_util: SIS_UTIL scan-depth hint */\n",
         T),
        # SIS_PROP off, SIS_UTIL on -- the upstream pair.
        ("kernel/sched/features.h",
         "SCHED_FEAT(SIS_PROP, true)\n",
         "SCHED_FEAT(SIS_PROP, false)\n"
         "SCHED_FEAT(SIS_UTIL, true)\t/* sailboat_sched_sis_util */\n",
         T),
        # select_idle_cpu(): the shared-domain hint pointer.
        ("kernel/sched/fair.c",
         "\tint i, cpu, idle_cpu = -1, nr = INT_MAX;\n"
         "\tstruct rq *this_rq = this_rq();\n",
         "\tint i, cpu, idle_cpu = -1, nr = INT_MAX;\n"
         "\tstruct sched_domain_shared *sd_share;\t/* sailboat_sched_sis_util */\n"
         "\tstruct rq *this_rq = this_rq();\n",
         T),
        # select_idle_cpu(): SIS_UTIL bound, after the SIS_PROP arm.
        ("kernel/sched/fair.c",
         "\t\ttime = cpu_clock(this);\n"
         "\t}\n"
         "\n"
         "\tfor_each_cpu_wrap(cpu, cpus, target + 1) {\n",
         "\t\ttime = cpu_clock(this);\n"
         "\t}\n"
         "\n"
         "\t/*\n"
         "\t * sailboat_sched_sis_util: SIS_UTIL scan bound.  The hint is\n"
         "\t * written by update_idle_cpu_scan() during periodic load balance;\n"
         "\t * a zero hint means no idle search on this LLC.\n"
         "\t */\n"
         "\tif (sched_feat(SIS_UTIL)) {\n"
         "\t\tsd_share = rcu_dereference(per_cpu(sd_llc_shared, target));\n"
         "\t\tif (sd_share) {\n"
         "\t\t\t/* because !--nr is the condition to stop scan */\n"
         "\t\t\tnr = READ_ONCE(sd_share->nr_idle_scan) + 1;\n"
         "\t\t\t/* overloaded LLC is unlikely to have idle cpu/core */\n"
         "\t\t\tif (nr == 1)\n"
         "\t\t\t\treturn -1;\n"
         "\t\t}\n"
         "\t}\n"
         "\n"
         "\tfor_each_cpu_wrap(cpu, cpus, target + 1) {\n",
         T),
        # the hint writer, immediately before update_sd_lb_stats()
        ("kernel/sched/fair.c",
         "\n/**\n * update_sd_lb_stats - Update sched_domain's statistics for load balancing.\n",
         "\n" + _SIS_UTIL_SCAN_FN +
         "\n/**\n * update_sd_lb_stats - Update sched_domain's statistics for load balancing.\n",
         T),
        # update_sd_lb_stats(): sum_util accumulator
        ("kernel/sched/fair.c",
         "\tstruct sg_lb_stats tmp_sgs;\n"
         "\tint sg_status = 0;\n",
         "\tstruct sg_lb_stats tmp_sgs;\n"
         "\tunsigned long sum_util = 0;\t/* sailboat_sched_sis_util */\n"
         "\tint sg_status = 0;\n",
         T),
        # accumulate each group's utilisation
        ("kernel/sched/fair.c",
         "\t\tsds->total_load += sgs->group_load;\n"
         "\t\tsds->total_capacity += sgs->group_capacity;\n"
         "\n"
         "\t\tsg = sg->next;\n",
         "\t\tsds->total_load += sgs->group_load;\n"
         "\t\tsds->total_capacity += sgs->group_capacity;\n"
         "\n"
         "\t\tsum_util += sgs->group_util;\t/* sailboat_sched_sis_util */\n"
         "\t\tsg = sg->next;\n",
         T),
        # publish the hint
        ("kernel/sched/fair.c",
         "\t} else if (sg_status & SG_OVERUTILIZED) {\n"
         "\t\tset_rd_overutilized_status(env->dst_rq->rd, SG_OVERUTILIZED);\n"
         "\t}\n"
         "}\n",
         "\t} else if (sg_status & SG_OVERUTILIZED) {\n"
         "\t\tset_rd_overutilized_status(env->dst_rq->rd, SG_OVERUTILIZED);\n"
         "\t}\n"
         "\n"
         "\tupdate_idle_cpu_scan(env, sum_util);\t/* sailboat_sched_sis_util */\n"
         "}\n",
         T),
    ])
    return status, detail



# ---------------------------------------------------------------------------
# sched_ext (S1a): the aarch64 instruction generators and A64 macros the arm64
# BPF plt is built on.
#
# Source: the arm64 BPF-trampoline set that landed in mainline v6.1 --
# b2ad54e1533e "bpf, arm64: Implement bpf_arch_text_poke() for arm64" (which
# introduces struct bpf_plt and therefore A64_LDR*LIT), efc9909fdce0 "bpf,
# arm64: Add bpf trampoline for arm64" (the first user of A64_STR64I /
# A64_LDR64I) and the instruction-encoder commit both of them depend on.  This
# group carries only what those two need.
#
# 5.15 shapes, measured on the supported tree rather than assumed:
#   * the encoder lives in arch/arm64/lib/insn.c -- arch/arm64/kernel/insn.c is
#     a 404 on android13-5.15-lts (checked against the branch's own directory
#     listing), so an upstream patch path of kernel/insn.c has to be
#     re-pointed, not copied;
#   * there is no aarch64_insn_ldst_size[] array (v6.1's refactor), so the size
#     shift stays the switch the generator below uses;
#   * the offset range check is still branch_imm_common(); the upstream
#     label_imm_common() rename is not carried, because nothing in this tree
#     else calls the renamed helper.
# ---------------------------------------------------------------------------

_ARM64_INSN_LITERAL_IMPL = (
    "/* sailboat_arm64_insn_load_literal: imm-offset and literal load/store. */\n"
    "u32 aarch64_insn_gen_load_store_imm(enum aarch64_insn_register reg,\n"
    "\t\t\t\t    enum aarch64_insn_register base,\n"
    "\t\t\t\t    unsigned int imm,\n"
    "\t\t\t\t    enum aarch64_insn_size_type size,\n"
    "\t\t\t\t    enum aarch64_insn_ldst_type type)\n"
    "{\n"
    "\tu32 insn;\n"
    "\tu32 shift;\n"
    "\n"
    "\tif (size < AARCH64_INSN_SIZE_8 || size > AARCH64_INSN_SIZE_64) {\n"
    "\t\tpr_err(\"%s: unknown size encoding %d\\n\", __func__, type);\n"
    "\t\treturn AARCH64_BREAK_FAULT;\n"
    "\t}\n"
    "\n"
    "\tswitch (size) {\n"
    "\tcase AARCH64_INSN_SIZE_8:\n"
    "\t\tshift = 0;\n"
    "\t\tbreak;\n"
    "\tcase AARCH64_INSN_SIZE_16:\n"
    "\t\tshift = 1;\n"
    "\t\tbreak;\n"
    "\tcase AARCH64_INSN_SIZE_32:\n"
    "\t\tshift = 2;\n"
    "\t\tbreak;\n"
    "\tdefault:\n"
    "\t\tshift = 3;\n"
    "\t\tbreak;\n"
    "\t}\n"
    "\tif (imm & ~(BIT(12 + shift) - BIT(shift))) {\n"
    "\t\tpr_err(\"%s: invalid imm: %d\\n\", __func__, imm);\n"
    "\t\treturn AARCH64_BREAK_FAULT;\n"
    "\t}\n"
    "\n"
    "\timm >>= shift;\n"
    "\n"
    "\tswitch (type) {\n"
    "\tcase AARCH64_INSN_LDST_LOAD_IMM_OFFSET:\n"
    "\t\tinsn = aarch64_insn_get_load_imm_value();\n"
    "\t\tbreak;\n"
    "\tcase AARCH64_INSN_LDST_STORE_IMM_OFFSET:\n"
    "\t\tinsn = aarch64_insn_get_store_imm_value();\n"
    "\t\tbreak;\n"
    "\tdefault:\n"
    "\t\tpr_err(\"%s: unknown load/store encoding %d\\n\", __func__, type);\n"
    "\t\treturn AARCH64_BREAK_FAULT;\n"
    "\t}\n"
    "\n"
    "\tinsn = aarch64_insn_encode_ldst_size(size, insn);\n"
    "\n"
    "\tinsn = aarch64_insn_encode_register(AARCH64_INSN_REGTYPE_RT, insn, reg);\n"
    "\n"
    "\tinsn = aarch64_insn_encode_register(AARCH64_INSN_REGTYPE_RN, insn,\n"
    "\t\t\t\t\t    base);\n"
    "\n"
    "\treturn aarch64_insn_encode_immediate(AARCH64_INSN_IMM_12, insn, imm);\n"
    "}\n"
    "\n"
    "u32 aarch64_insn_gen_load_literal(unsigned long pc, unsigned long addr,\n"
    "\t\t\t\t  enum aarch64_insn_register reg,\n"
    "\t\t\t\t  bool is64bit)\n"
    "{\n"
    "\tu32 insn;\n"
    "\tlong offset;\n"
    "\n"
    "\toffset = branch_imm_common(pc, addr, SZ_1M);\n"
    "\tif (offset >= SZ_1M)\n"
    "\t\treturn AARCH64_BREAK_FAULT;\n"
    "\n"
    "\tinsn = aarch64_insn_get_ldr_lit_value();\n"
    "\n"
    "\tif (is64bit)\n"
    "\t\tinsn |= BIT(30);\n"
    "\n"
    "\tinsn = aarch64_insn_encode_register(AARCH64_INSN_REGTYPE_RT, insn, reg);\n"
    "\n"
    "\treturn aarch64_insn_encode_immediate(AARCH64_INSN_IMM_19, insn,\n"
    "\t\t\t\t\t     offset >> 2);\n"
    "}\n"
    "\n"
)

def _arm64_insn_load_literal_apply(ctx):
    """aarch64_insn_gen_load_literal() + the A64 macros built on it.

    Dead code on its own: the only callers in this module are the arm64 BPF
    text_poke and trampoline groups, and upstream ships the same three pieces
    as one dependency of the same series.
    """
    status, _results, detail = apply_steps(ctx, [
        # enum aarch64_insn_ldst_type: the two unsigned-immediate forms.
        ("arch/arm64/include/asm/insn.h",
         "\tAARCH64_INSN_LDST_LOAD_REG_OFFSET,\n"
         "\tAARCH64_INSN_LDST_STORE_REG_OFFSET,\n"
         "\tAARCH64_INSN_LDST_LOAD_PAIR_PRE_INDEX,\n",
         "\tAARCH64_INSN_LDST_LOAD_REG_OFFSET,\n"
         "\tAARCH64_INSN_LDST_STORE_REG_OFFSET,\n"
         "\t/* sailboat_arm64_insn_load_literal: unsigned-immediate forms */\n"
         "\tAARCH64_INSN_LDST_LOAD_IMM_OFFSET,\n"
         "\tAARCH64_INSN_LDST_STORE_IMM_OFFSET,\n"
         "\tAARCH64_INSN_LDST_LOAD_PAIR_PRE_INDEX,\n",
         T),
        # NB: no opcode matcher is added here.  v6.1's
        # aarch64_insn_gen_load_store_imm() looks up
        # aarch64_insn_get_{ldr,str}_imm_value(), but v6.1 insn.h *also* still
        # carries aarch64_insn_get_{load,store}_imm_value() with the identical
        # mask/value pair, and 5.15 has only that older pair -- so the generator
        # below is written against the pair this tree already has instead of
        # adding two byte-identical duplicates.
        # Declarations, immediately before the pair generator they sit next to
        # upstream.
        ("arch/arm64/include/asm/insn.h",
         "u32 aarch64_insn_gen_load_store_pair(enum aarch64_insn_register reg1,\n",
         "/* sailboat_arm64_insn_load_literal */\n"
         "u32 aarch64_insn_gen_load_store_imm(enum aarch64_insn_register reg,\n"
         "\t\t\t\t    enum aarch64_insn_register base,\n"
         "\t\t\t\t    unsigned int imm,\n"
         "\t\t\t\t    enum aarch64_insn_size_type size,\n"
         "\t\t\t\t    enum aarch64_insn_ldst_type type);\n"
         "u32 aarch64_insn_gen_load_literal(unsigned long pc, unsigned long addr,\n"
         "\t\t\t\t  enum aarch64_insn_register reg,\n"
         "\t\t\t\t  bool is64bit);\n"
         "u32 aarch64_insn_gen_load_store_pair(enum aarch64_insn_register reg1,\n",
         T),
        # Implementations, between aarch64_insn_gen_load_store_reg() and the
        # pair generator.
        ("arch/arm64/lib/insn.c",
         "u32 aarch64_insn_gen_load_store_pair(enum aarch64_insn_register reg1,\n",
         _ARM64_INSN_LITERAL_IMPL +
         "u32 aarch64_insn_gen_load_store_pair(enum aarch64_insn_register reg1,\n",
         T),
        # bpf_jit.h: the A64 load/store-immediate family.  bpf_jit.h has no
        # immediate-offset form on 5.15 at all, so A64_STR64I()/A64_LDR64I()
        # arrive here rather than in the trampoline group that uses them.
        ("arch/arm64/net/bpf_jit.h",
         "#define A64_LDR64(Xt, Xn, Xm) A64_LS_REG(Xt, Xn, Xm, 64, LOAD)\n"
         "\n"
         "/* Load/store register pair */\n",
         "#define A64_LDR64(Xt, Xn, Xm) A64_LS_REG(Xt, Xn, Xm, 64, LOAD)\n"
         "\n"
         "/* sailboat_arm64_insn_load_literal: register (immediate offset) */\n"
         "#define A64_LS_IMM(Rt, Rn, imm, size, type) \\\n"
         "\taarch64_insn_gen_load_store_imm(Rt, Rn, imm, \\\n"
         "\t\tAARCH64_INSN_SIZE_##size, \\\n"
         "\t\tAARCH64_INSN_LDST_##type##_IMM_OFFSET)\n"
         "#define A64_STRBI(Wt, Xn, imm)  A64_LS_IMM(Wt, Xn, imm, 8, STORE)\n"
         "#define A64_LDRBI(Wt, Xn, imm)  A64_LS_IMM(Wt, Xn, imm, 8, LOAD)\n"
         "#define A64_STRHI(Wt, Xn, imm)  A64_LS_IMM(Wt, Xn, imm, 16, STORE)\n"
         "#define A64_LDRHI(Wt, Xn, imm)  A64_LS_IMM(Wt, Xn, imm, 16, LOAD)\n"
         "#define A64_STR32I(Wt, Xn, imm) A64_LS_IMM(Wt, Xn, imm, 32, STORE)\n"
         "#define A64_LDR32I(Wt, Xn, imm) A64_LS_IMM(Wt, Xn, imm, 32, LOAD)\n"
         "#define A64_STR64I(Xt, Xn, imm) A64_LS_IMM(Xt, Xn, imm, 64, STORE)\n"
         "#define A64_LDR64I(Xt, Xn, imm) A64_LS_IMM(Xt, Xn, imm, 64, LOAD)\n"
         "\n"
         "/* sailboat_arm64_insn_load_literal: LDR (literal) */\n"
         "#define A64_LDR32LIT(Wt, offset) \\\n"
         "\taarch64_insn_gen_load_literal(0, offset, Wt, false)\n"
         "#define A64_LDR64LIT(Xt, offset) \\\n"
         "\taarch64_insn_gen_load_literal(0, offset, Xt, true)\n"
         "\n"
         "/* Load/store register pair */\n",
         T),
        # bpf_jit.h: the nop the patchsite and the plt alignment are made of.
        ("arch/arm64/net/bpf_jit.h",
         "#define A64_BTI_JC A64_HINT(AARCH64_INSN_HINT_BTIJC)\n"
         "\n"
         "#endif /* _BPF_JIT_H */\n",
         "#define A64_BTI_JC A64_HINT(AARCH64_INSN_HINT_BTIJC)\n"
         "#define A64_NOP    A64_HINT(AARCH64_INSN_HINT_NOP)"
         "\t/* sailboat_arm64_insn_load_literal */\n"
         "\n"
         "#endif /* _BPF_JIT_H */\n",
         T),
    ])
    return status, detail


# ---------------------------------------------------------------------------
# sched_ext (S1b): bpf_arch_text_poke() for arm64.
#
# Source: b2ad54e1533e "bpf, arm64: Implement bpf_arch_text_poke() for arm64"
# (mainline v6.1), with 33f32e5072b6 (.global dummy_tramp) and 339ed900b307
# (x30 instead of lr in the same asm) folded in -- both fix the very patch this
# group carries, and shipping the broken intermediate would be a build failure
# on clang+CFI and on assemblers that do not accept "lr".
#
# Written for 5.15's interfaces rather than copied:
#   * 5.15's arm64 BPF prologue has no paciasp, so BTI_INSNS + 2 + 7 is the new
#     PROLOGUE_OFFSET (8/7 on the pristine file) and POKE_OFFSET is
#     BTI_INSNS + 1;
#   * linux/sizes.h is reachable through filter.h -> skbuff.h ->
#     dma-mapping.h, so SZ_128M needs no include of its own;
#   * 19f68ed6dc90 (kvcalloc for ctx.offset) is deliberately NOT carried:
#     kvcalloc()/kvfree() are declared in linux/mm.h, which this translation
#     unit does not include on 5.15, and pulling mm.h in for one allocation
#     helper is a divergence with no bearing on the trampoline.
#
# Depends on arm64_insn_load_literal for A64_LDR64LIT / A64_NOP / A64_STR64I.
# ---------------------------------------------------------------------------

_ARM64_BPF_TRAMPOLINE_FN = (
    "\n"
    "/* sailboat_arm64_bpf_trampoline: the arm64 BPF trampoline.\n"
    " *\n"
    " * Ported from the mainline v6.1 arm64 series (efc9909fdce0, with the\n"
    " * endianness fix aada47665546 folded in) onto this tree's trampoline\n"
    " * interface: kernel/bpf/trampoline.c here hands the arch builder a\n"
    " * struct bpf_tramp_progs, and __bpf_prog_enter()/__bpf_prog_exit() take\n"
    " * (prog) / (prog, start) with no run context, so a program carries no\n"
    " * cookie slot and the trampoline stack has no run-context frame.  This is\n"
    " * what makes struct_ops -- and therefore sched_ext -- attachable on\n"
    " * arm64.\n"
    " *\n"
    " * The struct-argument guard from the v6.1 follow-up is deliberately NOT\n"
    " * carried: this tree's struct btf_func_model has no per-argument flags\n"
    " * and no struct-argument marker exists, because trampoline struct\n"
    " * arguments were only introduced upstream after 5.15.  There is nothing\n"
    " * to reject, and adding the guard would not compile.\n"
    " */\n"
    "\n"
    "/* invoke one bpf prog from the trampoline: enter, call, exit. */\n"
    "static void invoke_bpf_prog(struct jit_ctx *ctx, struct bpf_prog *p,\n"
    "\t\t\t    int args_off, int retval_off, bool save_ret)\n"
    "{\n"
    "\t__le32 *branch;\n"
    "\tu64 enter_prog;\n"
    "\tu64 exit_prog;\n"
    "\n"
    "\tif (p->aux->sleepable) {\n"
    "\t\tenter_prog = (u64)__bpf_prog_enter_sleepable;\n"
    "\t\texit_prog = (u64)__bpf_prog_exit_sleepable;\n"
    "\t} else {\n"
    "\t\tenter_prog = (u64)__bpf_prog_enter;\n"
    "\t\texit_prog = (u64)__bpf_prog_exit;\n"
    "\t}\n"
    "\n"
    "\t/* save p to callee saved register x19 to avoid loading p with mov_i64\n"
    "\t * each time.\n"
    "\t */\n"
    "\temit_addr_mov_i64(A64_R(19), (const u64)p, ctx);\n"
    "\n"
    "\t/* arg1: prog */\n"
    "\temit(A64_MOV(1, A64_R(0), A64_R(19)), ctx);\n"
    "\n"
    "\temit_call(enter_prog, ctx);\n"
    "\n"
    "\t/* save return value to callee saved register x20.  The save has to\n"
    "\t * precede the branch: skip_exec_of_prog lands on the exit call, so a\n"
    "\t * later store would leave x20 holding whatever the caller had, and\n"
    "\t * __bpf_prog_exit() would charge that value as the program's start\n"
    "\t * time whenever bpf stats are on (this tree's update_prog_stats()\n"
    "\t * only rejects a start <= NO_START_TIME).  The 5.15 x86 trampoline,\n"
    "\t * which is written against this same interface, saves first for the\n"
    "\t * same reason; the v6.1 arm64 order saves after the placeholder.\n"
    "\t */\n"
    "\temit(A64_MOV(1, A64_R(20), A64_R(0)), ctx);\n"
    "\n"
    "\t/* if (__bpf_prog_enter(prog) == 0)\n"
    "\t *         goto skip_exec_of_prog;\n"
    "\t */\n"
    "\tbranch = ctx->image + ctx->idx;\n"
    "\temit(A64_NOP, ctx);\n"
    "\n"
    "\temit(A64_ADD_I(1, A64_R(0), A64_SP, args_off), ctx);\n"
    "\tif (!p->jited)\n"
    "\t\temit_addr_mov_i64(A64_R(1), (const u64)p->insnsi, ctx);\n"
    "\n"
    "\temit_call((const u64)p->bpf_func, ctx);\n"
    "\n"
    "\tif (save_ret)\n"
    "\t\temit(A64_STR64I(A64_R(0), A64_SP, retval_off), ctx);\n"
    "\n"
    "\tif (ctx->image) {\n"
    "\t\tint offset = &ctx->image[ctx->idx] - branch;\n"
    "\t\t*branch = cpu_to_le32(A64_CBZ(1, A64_R(0), offset));\n"
    "\t}\n"
    "\n"
    "\t/* arg1: prog */\n"
    "\temit(A64_MOV(1, A64_R(0), A64_R(19)), ctx);\n"
    "\t/* arg2: start time */\n"
    "\temit(A64_MOV(1, A64_R(1), A64_R(20)), ctx);\n"
    "\n"
    "\temit_call(exit_prog, ctx);\n"
    "}\n"
    "\n"
    "static void invoke_bpf_mod_ret(struct jit_ctx *ctx, struct bpf_tramp_progs *tp,\n"
    "\t\t\t       int args_off, int retval_off, __le32 **branches)\n"
    "{\n"
    "\tint i;\n"
    "\n"
    "\t/* The first fmod_ret program will receive a garbage return value.\n"
    "\t * Set this to 0 to avoid confusing the program.\n"
    "\t */\n"
    "\temit(A64_STR64I(A64_ZR, A64_SP, retval_off), ctx);\n"
    "\tfor (i = 0; i < tp->nr_progs; i++) {\n"
    "\t\tinvoke_bpf_prog(ctx, tp->progs[i], args_off, retval_off, true);\n"
    "\t\t/* if (*(u64 *)(sp + retval_off) !=  0)\n"
    "\t\t *\tgoto do_fexit;\n"
    "\t\t */\n"
    "\t\temit(A64_LDR64I(A64_R(10), A64_SP, retval_off), ctx);\n"
    "\t\t/* Save the location of branch, and generate a nop.\n"
    "\t\t * This nop will be replaced with a cbnz later.\n"
    "\t\t */\n"
    "\t\tbranches[i] = ctx->image + ctx->idx;\n"
    "\t\temit(A64_NOP, ctx);\n"
    "\t}\n"
    "}\n"
    "\n"
    "static void save_args(struct jit_ctx *ctx, int args_off, int nargs)\n"
    "{\n"
    "\tint i;\n"
    "\n"
    "\tfor (i = 0; i < nargs; i++) {\n"
    "\t\temit(A64_STR64I(i, A64_SP, args_off), ctx);\n"
    "\t\targs_off += 8;\n"
    "\t}\n"
    "}\n"
    "\n"
    "static void restore_args(struct jit_ctx *ctx, int args_off, int nargs)\n"
    "{\n"
    "\tint i;\n"
    "\n"
    "\tfor (i = 0; i < nargs; i++) {\n"
    "\t\temit(A64_LDR64I(i, A64_SP, args_off), ctx);\n"
    "\t\targs_off += 8;\n"
    "\t}\n"
    "}\n"
    "\n"
    "/* Based on the x86's implementation of arch_prepare_bpf_trampoline().\n"
    " *\n"
    " * bpf prog and function entry before bpf trampoline hooked:\n"
    " *   mov x9, lr\n"
    " *   nop\n"
    " *\n"
    " * bpf prog and function entry after bpf trampoline hooked:\n"
    " *   mov x9, lr\n"
    " *   bl  <bpf_trampoline or plt>\n"
    " *\n"
    " */\n"
    "static int prepare_trampoline(struct jit_ctx *ctx, struct bpf_tramp_image *im,\n"
    "\t\t\t      struct bpf_tramp_progs *tprogs, void *orig_call,\n"
    "\t\t\t      int nargs, u32 flags)\n"
    "{\n"
    "\tint i;\n"
    "\tint stack_size;\n"
    "\tint retaddr_off;\n"
    "\tint regs_off;\n"
    "\tint retval_off;\n"
    "\tint args_off;\n"
    "\tint nargs_off;\n"
    "\tint ip_off;\n"
    "\tstruct bpf_tramp_progs *fentry = &tprogs[BPF_TRAMP_FENTRY];\n"
    "\tstruct bpf_tramp_progs *fexit = &tprogs[BPF_TRAMP_FEXIT];\n"
    "\tstruct bpf_tramp_progs *fmod_ret = &tprogs[BPF_TRAMP_MODIFY_RETURN];\n"
    "\tbool save_ret;\n"
    "\t__le32 **branches = NULL;\n"
    "\n"
    "\t/* trampoline stack layout.  This tree passes no run context, so\n"
    "\t * the stack starts at the IP argument:\n"
    "\t *\n"
    "\t *                  [ parent ip         ]\n"
    "\t *                  [ FP                ]\n"
    "\t * SP + retaddr_off [ self ip           ]\n"
    "\t *                  [ FP                ]\n"
    "\t *\n"
    "\t *                  [ padding           ] align SP to multiples of 16\n"
    "\t *\n"
    "\t *                  [ x20               ] callee saved reg x20\n"
    "\t * SP + regs_off    [ x19               ] callee saved reg x19\n"
    "\t *\n"
    "\t * SP + retval_off  [ return value      ] BPF_TRAMP_F_CALL_ORIG or\n"
    "\t *                                        BPF_TRAMP_F_RET_FENTRY_RET\n"
    "\t *\n"
    "\t *                  [ argN              ]\n"
    "\t *                  [ ...               ]\n"
    "\t * SP + args_off    [ arg1              ]\n"
    "\t *\n"
    "\t * SP + nargs_off   [ args count        ]\n"
    "\t *\n"
    "\t * SP + ip_off      [ traced function   ] BPF_TRAMP_F_IP_ARG flag\n"
    "\t */\n"
    "\n"
    "\tstack_size = 0;\n"
    "\n"
    "\tip_off = stack_size;\n"
    "\t/* room for IP address argument */\n"
    "\tif (flags & BPF_TRAMP_F_IP_ARG)\n"
    "\t\tstack_size += 8;\n"
    "\n"
    "\tnargs_off = stack_size;\n"
    "\t/* room for args count */\n"
    "\tstack_size += 8;\n"
    "\n"
    "\targs_off = stack_size;\n"
    "\t/* room for args */\n"
    "\tstack_size += nargs * 8;\n"
    "\n"
    "\t/* room for return value */\n"
    "\tretval_off = stack_size;\n"
    "\tsave_ret = flags & (BPF_TRAMP_F_CALL_ORIG | BPF_TRAMP_F_RET_FENTRY_RET);\n"
    "\tif (save_ret)\n"
    "\t\tstack_size += 8;\n"
    "\n"
    "\t/* room for callee saved registers, currently x19 and x20 are used */\n"
    "\tregs_off = stack_size;\n"
    "\tstack_size += 16;\n"
    "\n"
    "\t/* round up to multiples of 16 to avoid SPAlignmentFault */\n"
    "\tstack_size = round_up(stack_size, 16);\n"
    "\n"
    "\t/* return address locates above FP */\n"
    "\tretaddr_off = stack_size + 8;\n"
    "\n"
    "\t/* bpf trampoline may be invoked by 3 instruction types:\n"
    "\t * 1. bl, attached to bpf prog or kernel function via short jump\n"
    "\t * 2. br, attached to bpf prog or kernel function via long jump\n"
    "\t * 3. blr, working as a function pointer, used by struct_ops.\n"
    "\t * So BTI_JC should used here to support both br and blr.\n"
    "\t */\n"
    "\temit_bti(A64_BTI_JC, ctx);\n"
    "\n"
    "\t/* frame for parent function */\n"
    "\temit(A64_PUSH(A64_FP, A64_R(9), A64_SP), ctx);\n"
    "\temit(A64_MOV(1, A64_FP, A64_SP), ctx);\n"
    "\n"
    "\t/* frame for patched function */\n"
    "\temit(A64_PUSH(A64_FP, A64_LR, A64_SP), ctx);\n"
    "\temit(A64_MOV(1, A64_FP, A64_SP), ctx);\n"
    "\n"
    "\t/* allocate stack space */\n"
    "\temit(A64_SUB_I(1, A64_SP, A64_SP, stack_size), ctx);\n"
    "\n"
    "\tif (flags & BPF_TRAMP_F_IP_ARG) {\n"
    "\t\t/* save ip address of the traced function */\n"
    "\t\temit_addr_mov_i64(A64_R(10), (const u64)orig_call, ctx);\n"
    "\t\temit(A64_STR64I(A64_R(10), A64_SP, ip_off), ctx);\n"
    "\t}\n"
    "\n"
    "\t/* save args count*/\n"
    "\temit(A64_MOVZ(1, A64_R(10), nargs, 0), ctx);\n"
    "\temit(A64_STR64I(A64_R(10), A64_SP, nargs_off), ctx);\n"
    "\n"
    "\t/* save args */\n"
    "\tsave_args(ctx, args_off, nargs);\n"
    "\n"
    "\t/* save callee saved registers */\n"
    "\temit(A64_STR64I(A64_R(19), A64_SP, regs_off), ctx);\n"
    "\temit(A64_STR64I(A64_R(20), A64_SP, regs_off + 8), ctx);\n"
    "\n"
    "\tif (flags & BPF_TRAMP_F_CALL_ORIG) {\n"
    "\t\temit_addr_mov_i64(A64_R(0), (const u64)im, ctx);\n"
    "\t\temit_call((const u64)__bpf_tramp_enter, ctx);\n"
    "\t}\n"
    "\n"
    "\tfor (i = 0; i < fentry->nr_progs; i++)\n"
    "\t\tinvoke_bpf_prog(ctx, fentry->progs[i], args_off,\n"
    "\t\t\t\tretval_off,\n"
    "\t\t\t\tflags & BPF_TRAMP_F_RET_FENTRY_RET);\n"
    "\n"
    "\tif (fmod_ret->nr_progs) {\n"
    "\t\tbranches = kcalloc(fmod_ret->nr_progs, sizeof(__le32 *),\n"
    "\t\t\t\t   GFP_KERNEL);\n"
    "\t\tif (!branches)\n"
    "\t\t\treturn -ENOMEM;\n"
    "\n"
    "\t\tinvoke_bpf_mod_ret(ctx, fmod_ret, args_off, retval_off,\n"
    "\t\t\t\t   branches);\n"
    "\t}\n"
    "\n"
    "\tif (flags & BPF_TRAMP_F_CALL_ORIG) {\n"
    "\t\trestore_args(ctx, args_off, nargs);\n"
    "\t\t/* call original func */\n"
    "\t\temit(A64_LDR64I(A64_R(10), A64_SP, retaddr_off), ctx);\n"
    "\t\temit(A64_BLR(A64_R(10)), ctx);\n"
    "\t\t/* store return value */\n"
    "\t\temit(A64_STR64I(A64_R(0), A64_SP, retval_off), ctx);\n"
    "\t\t/* reserve a nop for bpf_tramp_image_put */\n"
    "\t\tim->ip_after_call = ctx->image + ctx->idx;\n"
    "\t\temit(A64_NOP, ctx);\n"
    "\t}\n"
    "\n"
    "\t/* update the branches saved in invoke_bpf_mod_ret with cbnz */\n"
    "\tfor (i = 0; i < fmod_ret->nr_progs && ctx->image != NULL; i++) {\n"
    "\t\tint offset = &ctx->image[ctx->idx] - branches[i];\n"
    "\t\t*branches[i] = cpu_to_le32(A64_CBNZ(1, A64_R(10), offset));\n"
    "\t}\n"
    "\n"
    "\tfor (i = 0; i < fexit->nr_progs; i++)\n"
    "\t\tinvoke_bpf_prog(ctx, fexit->progs[i], args_off, retval_off,\n"
    "\t\t\t\tfalse);\n"
    "\n"
    "\tif (flags & BPF_TRAMP_F_CALL_ORIG) {\n"
    "\t\tim->ip_epilogue = ctx->image + ctx->idx;\n"
    "\t\temit_addr_mov_i64(A64_R(0), (const u64)im, ctx);\n"
    "\t\temit_call((const u64)__bpf_tramp_exit, ctx);\n"
    "\t}\n"
    "\n"
    "\tif (flags & BPF_TRAMP_F_RESTORE_REGS)\n"
    "\t\trestore_args(ctx, args_off, nargs);\n"
    "\n"
    "\t/* restore callee saved register x19 and x20 */\n"
    "\temit(A64_LDR64I(A64_R(19), A64_SP, regs_off), ctx);\n"
    "\temit(A64_LDR64I(A64_R(20), A64_SP, regs_off + 8), ctx);\n"
    "\n"
    "\tif (save_ret)\n"
    "\t\temit(A64_LDR64I(A64_R(0), A64_SP, retval_off), ctx);\n"
    "\n"
    "\t/* reset SP  */\n"
    "\temit(A64_MOV(1, A64_SP, A64_FP), ctx);\n"
    "\n"
    "\t/* pop frames  */\n"
    "\temit(A64_POP(A64_FP, A64_LR, A64_SP), ctx);\n"
    "\temit(A64_POP(A64_FP, A64_R(9), A64_SP), ctx);\n"
    "\n"
    "\tif (flags & BPF_TRAMP_F_SKIP_FRAME) {\n"
    "\t\t/* skip patched function, return to parent */\n"
    "\t\temit(A64_MOV(1, A64_LR, A64_R(9)), ctx);\n"
    "\t\temit(A64_RET(A64_R(9)), ctx);\n"
    "\t} else {\n"
    "\t\t/* return to patched function */\n"
    "\t\temit(A64_MOV(1, A64_R(10), A64_LR), ctx);\n"
    "\t\temit(A64_MOV(1, A64_LR, A64_R(9)), ctx);\n"
    "\t\temit(A64_RET(A64_R(10)), ctx);\n"
    "\t}\n"
    "\n"
    "\tif (ctx->image)\n"
    "\t\tbpf_flush_icache(ctx->image, ctx->image + ctx->idx);\n"
    "\n"
    "\tkfree(branches);\n"
    "\n"
    "\treturn ctx->idx;\n"
    "}\n"
    "\n"
    "int arch_prepare_bpf_trampoline(struct bpf_tramp_image *im, void *image,\n"
    "\t\t\t\tvoid *image_end, const struct btf_func_model *m,\n"
    "\t\t\t\tu32 flags, struct bpf_tramp_progs *tprogs,\n"
    "\t\t\t\tvoid *orig_call)\n"
    "{\n"
    "\tint ret;\n"
    "\tint nargs = m->nr_args;\n"
    "\tint max_insns = ((long)image_end - (long)image) / AARCH64_INSN_SIZE;\n"
    "\tstruct jit_ctx ctx = {\n"
    "\t\t.image = NULL,\n"
    "\t\t.idx = 0,\n"
    "\t};\n"
    "\n"
    "\t/* the first 8 arguments are passed by registers */\n"
    "\tif (nargs > 8)\n"
    "\t\treturn -ENOTSUPP;\n"
    "\n"
    "\tret = prepare_trampoline(&ctx, im, tprogs, orig_call, nargs, flags);\n"
    "\tif (ret < 0)\n"
    "\t\treturn ret;\n"
    "\n"
    "\tif (ret > max_insns)\n"
    "\t\treturn -EFBIG;\n"
    "\n"
    "\tctx.image = image;\n"
    "\tctx.idx = 0;\n"
    "\n"
    "\tjit_fill_hole(image, (unsigned int)(image_end - image));\n"
    "\tret = prepare_trampoline(&ctx, im, tprogs, orig_call, nargs, flags);\n"
    "\n"
    "\tif (ret > 0 && validate_code(&ctx) < 0)\n"
    "\t\tret = -EINVAL;\n"
    "\n"
    "\tif (ret > 0)\n"
    "\t\tret *= AARCH64_INSN_SIZE;\n"
    "\n"
    "\treturn ret;\n"
    "}\n"
)

_ARM64_BPF_DUMMY_TRAMP = (
    "/* sailboat_arm64_bpf_text_poke: a legal, harmless long-jump destination. */\n"
    "void dummy_tramp(void);\n"
    "\n"
    "asm (\n"
    "\"\t.pushsection .text, \\\"ax\\\", @progbits\\n\"\n"
    "\"\t.global dummy_tramp\\n\"\n"
    "\"\t.type dummy_tramp, %function\\n\"\n"
    "\"dummy_tramp:\"\n"
    "#if IS_ENABLED(CONFIG_ARM64_BTI_KERNEL)\n"
    "\"\tbti j\\n\" /* dummy_tramp is called via \"br x10\" */\n"
    "#endif\n"
    "\"\tmov x10, x30\\n\"\n"
    "\"\tmov x30, x9\\n\"\n"
    "\"\tret x10\\n\"\n"
    "\"\t.size dummy_tramp, .-dummy_tramp\\n\"\n"
    "\"\t.popsection\\n\"\n"
    ");\n"
    "\n"
    "/* build a plt initialized like this:\n"
    " *\n"
    " * plt:\n"
    " *\tldr tmp, target\n"
    " *\tbr tmp\n"
    " * target:\n"
    " *\t.quad dummy_tramp\n"
    " *\n"
    " * when a long jump trampoline is attached, target is filled with the\n"
    " * trampoline address, and when the trampoline is removed, target is\n"
    " * restored to dummy_tramp address.\n"
    " */\n"
    "static void build_plt(struct jit_ctx *ctx)\n"
    "{\n"
    "\tconst u8 tmp = bpf2a64[TMP_REG_1];\n"
    "\tstruct bpf_plt *plt = NULL;\n"
    "\n"
    "\t/* make sure target is 64-bit aligned */\n"
    "\tif ((ctx->idx + PLT_TARGET_OFFSET / AARCH64_INSN_SIZE) % 2)\n"
    "\t\temit(A64_NOP, ctx);\n"
    "\n"
    "\tplt = (struct bpf_plt *)(ctx->image + ctx->idx);\n"
    "\t/* plt is called via bl, no BTI needed here */\n"
    "\temit(A64_LDR64LIT(tmp, 2 * AARCH64_INSN_SIZE), ctx);\n"
    "\temit(A64_BR(tmp), ctx);\n"
    "\n"
    "\tif (ctx->image)\n"
    "\t\tplt->target = (u64)&dummy_tramp;\n"
    "}\n"
    "\n"
)

_ARM64_BPF_TEXT_POKE_FN = (
    "/* sailboat_arm64_bpf_text_poke: how a bpf prog's patchsite is patched. */\n"
    "static bool is_long_jump(void *ip, void *target)\n"
    "{\n"
    "\tlong offset;\n"
    "\n"
    "\t/* NULL target means this is a NOP */\n"
    "\tif (!target)\n"
    "\t\treturn false;\n"
    "\n"
    "\toffset = (long)target - (long)ip;\n"
    "\treturn offset < -SZ_128M || offset >= SZ_128M;\n"
    "}\n"
    "\n"
    "static int gen_branch_or_nop(enum aarch64_insn_branch_type type, void *ip,\n"
    "\t\t\t     void *addr, void *plt, u32 *insn)\n"
    "{\n"
    "\tvoid *target;\n"
    "\n"
    "\tif (!addr) {\n"
    "\t\t*insn = aarch64_insn_gen_nop();\n"
    "\t\treturn 0;\n"
    "\t}\n"
    "\n"
    "\tif (is_long_jump(ip, addr))\n"
    "\t\ttarget = plt;\n"
    "\telse\n"
    "\t\ttarget = addr;\n"
    "\n"
    "\t*insn = aarch64_insn_gen_branch_imm((unsigned long)ip,\n"
    "\t\t\t\t\t    (unsigned long)target,\n"
    "\t\t\t\t\t    type);\n"
    "\n"
    "\treturn *insn != AARCH64_BREAK_FAULT ? 0 : -EFAULT;\n"
    "}\n"
    "\n"
    "/* Replace the branch instruction from @ip to @old_addr in a bpf prog or a bpf\n"
    " * trampoline with the branch instruction from @ip to @new_addr. If @old_addr\n"
    " * or @new_addr is NULL, the old or new instruction is NOP.\n"
    " *\n"
    " * When @ip is the bpf prog entry, a bpf trampoline is being attached or\n"
    " * detached. Since bpf trampoline and bpf prog are allocated separately with\n"
    " * vmalloc, the address distance may exceed 128MB, the maximum branch range.\n"
    " * So long jump should be handled.\n"
    " *\n"
    " * When a bpf prog is constructed, a plt pointing to empty trampoline\n"
    " * dummy_tramp is placed at the end:\n"
    " *\n"
    " *\tbpf_prog:\n"
    " *\t\tmov x9, lr\n"
    " *\t\tnop // patchsite\n"
    " *\t\t...\n"
    " *\t\tret\n"
    " *\n"
    " *\tplt:\n"
    " *\t\tldr x10, target\n"
    " *\t\tbr x10\n"
    " *\ttarget:\n"
    " *\t\t.quad dummy_tramp // plt target\n"
    " *\n"
    " * This is also the state when no trampoline is attached.\n"
    " *\n"
    " * When a short-jump bpf trampoline is attached, the patchsite is patched to\n"
    " * a bl instruction to the trampoline directly:\n"
    " *\n"
    " *\tbpf_prog:\n"
    " *\t\tmov x9, lr\n"
    " *\t\tbl <short-jump bpf trampoline address> // patchsite\n"
    " *\t\t...\n"
    " *\t\tret\n"
    " *\n"
    " *\tplt:\n"
    " *\t\tldr x10, target\n"
    " *\t\tbr x10\n"
    " *\ttarget:\n"
    " *\t\t.quad dummy_tramp // plt target\n"
    " *\n"
    " * When a long-jump bpf trampoline is attached, the plt target is filled with\n"
    " * the trampoline address and the patchsite is patched to a bl instruction to\n"
    " * the plt:\n"
    " *\n"
    " *\tbpf_prog:\n"
    " *\t\tmov x9, lr\n"
    " *\t\tbl plt // patchsite\n"
    " *\t\t...\n"
    " *\t\tret\n"
    " *\n"
    " *\tplt:\n"
    " *\t\tldr x10, target\n"
    " *\t\tbr x10\n"
    " *\ttarget:\n"
    " *\t\t.quad <long-jump bpf trampoline address> // plt target\n"
    " *\n"
    " * The dummy_tramp is used to prevent another CPU from jumping to unknown\n"
    " * locations during the patching process, making the patching process easier.\n"
    " */\n"
    "int bpf_arch_text_poke(void *ip, enum bpf_text_poke_type poke_type,\n"
    "\t\t       void *old_addr, void *new_addr)\n"
    "{\n"
    "\tint ret;\n"
    "\tu32 old_insn;\n"
    "\tu32 new_insn;\n"
    "\tu32 replaced;\n"
    "\tstruct bpf_plt *plt = NULL;\n"
    "\tunsigned long size = 0UL;\n"
    "\tunsigned long offset = ~0UL;\n"
    "\tenum aarch64_insn_branch_type branch_type;\n"
    "\tchar namebuf[KSYM_NAME_LEN];\n"
    "\tvoid *image = NULL;\n"
    "\tu64 plt_target = 0ULL;\n"
    "\tbool poking_bpf_entry;\n"
    "\n"
    "\tif (!__bpf_address_lookup((unsigned long)ip, &size, &offset, namebuf))\n"
    "\t\t/* Only poking bpf text is supported. Since kernel function\n"
    "\t\t * entry is set up by ftrace, we reply on ftrace to poke kernel\n"
    "\t\t * functions.\n"
    "\t\t */\n"
    "\t\treturn -ENOTSUPP;\n"
    "\n"
    "\timage = ip - offset;\n"
    "\t/* zero offset means we're poking bpf prog entry */\n"
    "\tpoking_bpf_entry = (offset == 0UL);\n"
    "\n"
    "\t/* bpf prog entry, find plt and the real patchsite */\n"
    "\tif (poking_bpf_entry) {\n"
    "\t\t/* plt locates at the end of bpf prog */\n"
    "\t\tplt = image + size - PLT_TARGET_OFFSET;\n"
    "\n"
    "\t\t/* skip to the nop instruction in bpf prog entry:\n"
    "\t\t * bti c // if BTI enabled\n"
    "\t\t * mov x9, x30\n"
    "\t\t * nop\n"
    "\t\t */\n"
    "\t\tip = image + POKE_OFFSET * AARCH64_INSN_SIZE;\n"
    "\t}\n"
    "\n"
    "\t/* long jump is only possible at bpf prog entry */\n"
    "\tif (WARN_ON((is_long_jump(ip, new_addr) || is_long_jump(ip, old_addr)) &&\n"
    "\t\t    !poking_bpf_entry))\n"
    "\t\treturn -EINVAL;\n"
    "\n"
    "\tif (poke_type == BPF_MOD_CALL)\n"
    "\t\tbranch_type = AARCH64_INSN_BRANCH_LINK;\n"
    "\telse\n"
    "\t\tbranch_type = AARCH64_INSN_BRANCH_NOLINK;\n"
    "\n"
    "\tif (gen_branch_or_nop(branch_type, ip, old_addr, plt, &old_insn) < 0)\n"
    "\t\treturn -EFAULT;\n"
    "\n"
    "\tif (gen_branch_or_nop(branch_type, ip, new_addr, plt, &new_insn) < 0)\n"
    "\t\treturn -EFAULT;\n"
    "\n"
    "\tif (is_long_jump(ip, new_addr))\n"
    "\t\tplt_target = (u64)new_addr;\n"
    "\telse if (is_long_jump(ip, old_addr))\n"
    "\t\t/* if the old target is a long jump and the new target is not,\n"
    "\t\t * restore the plt target to dummy_tramp, so there is always a\n"
    "\t\t * legal and harmless address stored in plt target, and we'll\n"
    "\t\t * never jump from plt to an unknown place.\n"
    "\t\t */\n"
    "\t\tplt_target = (u64)&dummy_tramp;\n"
    "\n"
    "\tif (plt_target) {\n"
    "\t\t/* non-zero plt_target indicates we're patching a bpf prog,\n"
    "\t\t * which is read only.\n"
    "\t\t */\n"
    "\t\tif (set_memory_rw(PAGE_MASK & ((uintptr_t)&plt->target), 1))\n"
    "\t\t\treturn -EFAULT;\n"
    "\t\tWRITE_ONCE(plt->target, plt_target);\n"
    "\t\tset_memory_ro(PAGE_MASK & ((uintptr_t)&plt->target), 1);\n"
    "\t\t/* since plt target points to either the new trampoline\n"
    "\t\t * or dummy_tramp, even if another CPU reads the old plt\n"
    "\t\t * target value before fetching the bl instruction to plt,\n"
    "\t\t * it will be brought back by dummy_tramp, so no barrier is\n"
    "\t\t * required here.\n"
    "\t\t */\n"
    "\t}\n"
    "\n"
    "\t/* if the old target and the new target are both long jumps, no\n"
    "\t * patching is required\n"
    "\t */\n"
    "\tif (old_insn == new_insn)\n"
    "\t\treturn 0;\n"
    "\n"
    "\tmutex_lock(&text_mutex);\n"
    "\tif (aarch64_insn_read(ip, &replaced)) {\n"
    "\t\tret = -EFAULT;\n"
    "\t\tgoto out;\n"
    "\t}\n"
    "\n"
    "\tif (replaced != old_insn) {\n"
    "\t\tret = -EFAULT;\n"
    "\t\tgoto out;\n"
    "\t}\n"
    "\n"
    "\t/* We call aarch64_insn_patch_text_nosync() to replace instruction\n"
    "\t * atomically, so no other CPUs will fetch a half-new and half-old\n"
    "\t * instruction. But there is chance that another CPU executes the\n"
    "\t * old instruction after the patching operation finishes (e.g.,\n"
    "\t * pipeline not flushed, or icache not synchronized yet).\n"
    "\t *\n"
    "\t * 1. when a new trampoline is attached, it is not a problem for\n"
    "\t *    different CPUs to jump to different trampolines temporarily.\n"
    "\t *\n"
    "\t * 2. when an old trampoline is freed, we should wait for all other\n"
    "\t *    CPUs to exit the trampoline and make sure the trampoline is no\n"
    "\t *    longer reachable, since bpf_tramp_image_put() function already\n"
    "\t *    uses percpu_ref and task-based rcu to do the sync, no need to call\n"
    "\t *    the sync version here, see bpf_tramp_image_put() for details.\n"
    "\t */\n"
    "\tret = aarch64_insn_patch_text_nosync(ip, new_insn);\n"
    "out:\n"
    "\tmutex_unlock(&text_mutex);\n"
    "\n"
    "\treturn ret;\n"
    "}\n"
)

def _arm64_bpf_text_poke_apply(ctx):
    """bpf_arch_text_poke() for arm64 (the patchsite + the long-jump plt)."""
    status, _results, detail = apply_steps(ctx, [
        # linux/memory.h carries text_mutex; asm/patching.h the two insn
        # accessors.  Both are what upstream added for this commit.
        ("arch/arm64/net/bpf_jit_comp.c",
         "#include <linux/filter.h>\n"
         "#include <linux/printk.h>\n"
         "#include <linux/slab.h>\n"
         "\n"
         "#include <asm/byteorder.h>\n"
         "#include <asm/cacheflush.h>\n"
         "#include <asm/cpufeature.h>\n"
         "#include <asm/debug-monitors.h>\n"
         "#include <asm/insn.h>\n"
         "#include <asm/set_memory.h>\n",
         "#include <linux/filter.h>\n"
         "#include <linux/memory.h>\t"
         "/* sailboat_arm64_bpf_text_poke: text_mutex */\n"
         "#include <linux/printk.h>\n"
         "#include <linux/slab.h>\n"
         "\n"
         "#include <asm/byteorder.h>\n"
         "#include <asm/cacheflush.h>\n"
         "#include <asm/cpufeature.h>\n"
         "#include <asm/debug-monitors.h>\n"
         "#include <asm/insn.h>\n"
         "#include <asm/patching.h>\t"
         "/* sailboat_arm64_bpf_text_poke: aarch64_insn_patch_text_nosync() */\n"
         "#include <asm/set_memory.h>\n",
         T),
        # struct bpf_plt + the two offsets the plt is addressed by.
        ("arch/arm64/net/bpf_jit_comp.c",
         "\t__le32 *image;\n"
         "\tu32 stack_size;\n"
         "};\n"
         "\n"
         "static inline void emit(const u32 insn, struct jit_ctx *ctx)\n",
         "\t__le32 *image;\n"
         "\tu32 stack_size;\n"
         "};\n"
         "\n"
         "/* sailboat_arm64_bpf_text_poke: the plt a bpf prog carries at its end. */\n"
         "struct bpf_plt {\n"
         "\tu32 insn_ldr; /* load target */\n"
         "\tu32 insn_br;  /* branch to target */\n"
         "\tu64 target;   /* target value */\n"
         "};\n"
         "\n"
         "#define PLT_TARGET_SIZE   sizeof_field(struct bpf_plt, target)\n"
         "#define PLT_TARGET_OFFSET offsetof(struct bpf_plt, target)\n"
         "\n"
         "static inline void emit(const u32 insn, struct jit_ctx *ctx)\n",
         T),
        # emit_bti(): the trampoline group emits it too, and it has to be
        # unconditional here because the bpf prog's own landing pad moves.
        ("arch/arm64/net/bpf_jit_comp.c",
         "\t\tshift -= 16;\n"
         "\t}\n"
         "}\n"
         "\n"
         "/*\n"
         " * Kernel addresses in the vmalloc space use at most 48 bits, and the\n",
         "\t\tshift -= 16;\n"
         "\t}\n"
         "}\n"
         "\n"
         "static inline void emit_bti(u32 insn, struct jit_ctx *ctx)\n"
         "{\n"
         "\tif (IS_ENABLED(CONFIG_ARM64_BTI_KERNEL))\n"
         "\t\temit(insn, ctx);\n"
         "}\n"
         "\n"
         "/*\n"
         " * Kernel addresses in the vmalloc space use at most 48 bits, and the\n",
         T),
        # The prologue grows by the poked mov/nop pair.  5.15's arm64 prologue
        # has no paciasp, so the fixed tail is 7 instructions, not v6.1's 8.
        ("arch/arm64/net/bpf_jit_comp.c",
         "/* Tail call offset to jump into */\n"
         "#if IS_ENABLED(CONFIG_ARM64_BTI_KERNEL)\n"
         "#define PROLOGUE_OFFSET 8\n"
         "#else\n"
         "#define PROLOGUE_OFFSET 7\n"
         "#endif\n",
         "/* Tail call offset to jump into */\n"
         "#define BTI_INSNS (IS_ENABLED(CONFIG_ARM64_BTI_KERNEL) ? 1 : 0)\n"
         "\n"
         "/* Offset of nop instruction in bpf prog entry to be poked */\n"
         "#define POKE_OFFSET (BTI_INSNS + 1)\n"
         "\n"
         "/* bti landing pad + the poked mov/nop pair + the 7 fixed instructions\n"
         " * of this tree's prologue (no paciasp here, unlike v6.1).\n"
         " */\n"
         "#define PROLOGUE_OFFSET (BTI_INSNS + 2 + 7)\n",
         T),
        # build_prologue(): the landing pad, the patchsite.
        ("arch/arm64/net/bpf_jit_comp.c",
         "\t/* BTI landing pad */\n"
         "\tif (IS_ENABLED(CONFIG_ARM64_BTI_KERNEL))\n"
         "\t\temit(A64_BTI_C, ctx);\n"
         "\n"
         "\t/* Save FP and LR registers to stay align with ARM64 AAPCS */\n",
         "\temit_bti(A64_BTI_C, ctx);\n"
         "\n"
         "\t/* sailboat_arm64_bpf_text_poke: the patchsite.  mov x9, lr keeps the\n"
         "\t * return address reachable when a long-jump trampoline is attached\n"
         "\t * (dummy_tramp restores it); the nop becomes the bl that\n"
         "\t * bpf_arch_text_poke() writes.\n"
         "\t */\n"
         "\temit(A64_MOV(1, A64_R(9), A64_LR), ctx);\n"
         "\temit(A64_NOP, ctx);\n"
         "\n"
         "\t/* Save FP and LR registers to stay align with ARM64 AAPCS */\n",
         T),
        # build_prologue(): the tail-call landing pad uses the same wrapper.
        ("arch/arm64/net/bpf_jit_comp.c",
         "\t\t/* BTI landing pad for the tail call, done with a BR */\n"
         "\t\tif (IS_ENABLED(CONFIG_ARM64_BTI_KERNEL))\n"
         "\t\t\temit(A64_BTI_J, ctx);\n",
         "\t\t/* BTI landing pad for the tail call, done with a BR */\n"
         "\t\temit_bti(A64_BTI_J, ctx);\n",
         T),
        # dummy_tramp + build_plt go in front of the epilogue builder.
        ("arch/arm64/net/bpf_jit_comp.c",
         "static void build_epilogue(struct jit_ctx *ctx, bool was_classic)\n",
         _ARM64_BPF_DUMMY_TRAMP +
         "static void build_epilogue(struct jit_ctx *ctx, bool was_classic)\n",
         T),
        # validate_code() loses the prog-specific tail so a trampoline image
        # (which has no ctx->prog) can be validated with it.
        ("arch/arm64/net/bpf_jit_comp.c",
         "\t\tif (a64_insn == AARCH64_BREAK_FAULT)\n"
         "\t\t\treturn -1;\n"
         "\t}\n"
         "\n"
         "\tif (WARN_ON_ONCE(ctx->exentry_idx != ctx->prog->aux->num_exentries))\n"
         "\t\treturn -1;\n"
         "\n"
         "\treturn 0;\n"
         "}\n",
         "\t\tif (a64_insn == AARCH64_BREAK_FAULT)\n"
         "\t\t\treturn -1;\n"
         "\t}\n"
         "\n"
         "\treturn 0;\n"
         "}\n"
         "\n"
         "/* sailboat_arm64_bpf_text_poke: keep the prog-specific checks out of\n"
         " * validate_code(), which the trampoline generator also calls.\n"
         " */\n"
         "static int validate_ctx(struct jit_ctx *ctx)\n"
         "{\n"
         "\tif (validate_code(ctx))\n"
         "\t\treturn -1;\n"
         "\n"
         "\tif (WARN_ON_ONCE(ctx->exentry_idx != ctx->prog->aux->num_exentries))\n"
         "\t\treturn -1;\n"
         "\n"
         "\treturn 0;\n"
         "}\n",
         T),
        # bpf_int_jit_compile(): the plt needs its own room behind the image,
        # and the extable has to move past it.
        ("arch/arm64/net/bpf_jit_comp.c",
         "\tint image_size, prog_size, extable_size;\n",
         "\tint image_size, prog_size, extable_size, extable_align, extable_offset;\n",
         T),
        ("arch/arm64/net/bpf_jit_comp.c",
         "\tctx.epilogue_offset = ctx.idx;\n"
         "\tbuild_epilogue(&ctx, was_classic);\n"
         "\n"
         "\textable_size = prog->aux->num_exentries *\n"
         "\t\tsizeof(struct exception_table_entry);\n"
         "\n"
         "\t/* Now we know the actual image size. */\n"
         "\tprog_size = sizeof(u32) * ctx.idx;\n"
         "\timage_size = prog_size + extable_size;\n",
         "\tctx.epilogue_offset = ctx.idx;\n"
         "\tbuild_epilogue(&ctx, was_classic);\n"
         "\tbuild_plt(&ctx);\n"
         "\n"
         "\textable_align = __alignof__(struct exception_table_entry);\n"
         "\textable_size = prog->aux->num_exentries *\n"
         "\t\tsizeof(struct exception_table_entry);\n"
         "\n"
         "\t/* Now we know the actual image size. */\n"
         "\tprog_size = sizeof(u32) * ctx.idx;\n"
         "\t/* also allocate space for plt target */\n"
         "\textable_offset = round_up(prog_size + PLT_TARGET_SIZE, extable_align);\n"
         "\timage_size = extable_offset + extable_size;\n",
         T),
        ("arch/arm64/net/bpf_jit_comp.c",
         "\tif (extable_size)\n"
         "\t\tprog->aux->extable = (void *)image_ptr + prog_size;\n",
         "\tif (extable_size)\n"
         "\t\tprog->aux->extable = (void *)image_ptr + extable_offset;\n",
         T),
        ("arch/arm64/net/bpf_jit_comp.c",
         "\tbuild_epilogue(&ctx, was_classic);\n"
         "\n"
         "\t/* 3. Extra pass to validate JITed code. */\n"
         "\tif (validate_code(&ctx)) {\n",
         "\tbuild_epilogue(&ctx, was_classic);\n"
         "\tbuild_plt(&ctx);\n"
         "\n"
         "\t/* 3. Extra pass to validate JITed code. */\n"
         "\tif (validate_ctx(&ctx)) {\n",
         T),
        # The poking side itself, appended to the translation unit.
        ("arch/arm64/net/bpf_jit_comp.c",
         "void bpf_jit_free_exec(void *addr)\n"
         "{\n"
         "\treturn vfree(addr);\n"
         "}\n",
         "void bpf_jit_free_exec(void *addr)\n"
         "{\n"
         "\treturn vfree(addr);\n"
         "}\n"
         "\n" +
         _ARM64_BPF_TEXT_POKE_FN,
         T),
    ])
    return status, detail


def _arm64_bpf_trampoline_apply(ctx):
    """The arm64 BPF trampoline itself (fentry/fexit/fmod_ret/struct_ops)."""
    status, _results, detail = apply_steps(ctx, [
        # emit_call(): load an address and blr it.  The trampoline calls
        # __bpf_prog_enter/exit, the bpf program and the original function
        # through it, so it has to sit above build_insn().
        ("arch/arm64/net/bpf_jit_comp.c",
         "static inline void emit_addr_mov_i64(const int reg, const u64 val,\n"
         "\t\t\t\t     struct jit_ctx *ctx)\n"
         "{\n"
         "\tu64 tmp = val;\n"
         "\tint shift = 0;\n"
         "\n"
         "\temit(A64_MOVN(1, reg, ~tmp & 0xffff, shift), ctx);\n"
         "\twhile (shift < 32) {\n"
         "\t\ttmp >>= 16;\n"
         "\t\tshift += 16;\n"
         "\t\temit(A64_MOVK(1, reg, tmp & 0xffff, shift), ctx);\n"
         "\t}\n"
         "}\n"
         "\n"
         "static inline int bpf2a64_offset(int bpf_insn, int off,\n",
         "static inline void emit_addr_mov_i64(const int reg, const u64 val,\n"
         "\t\t\t\t     struct jit_ctx *ctx)\n"
         "{\n"
         "\tu64 tmp = val;\n"
         "\tint shift = 0;\n"
         "\n"
         "\temit(A64_MOVN(1, reg, ~tmp & 0xffff, shift), ctx);\n"
         "\twhile (shift < 32) {\n"
         "\t\ttmp >>= 16;\n"
         "\t\tshift += 16;\n"
         "\t\temit(A64_MOVK(1, reg, tmp & 0xffff, shift), ctx);\n"
         "\t}\n"
         "}\n"
         "\n"
         "/* sailboat_arm64_bpf_trampoline: load an address and call it. */\n"
         "static inline void emit_call(u64 target, struct jit_ctx *ctx)\n"
         "{\n"
         "\tu8 tmp = bpf2a64[TMP_REG_1];\n"
         "\n"
         "\temit_addr_mov_i64(tmp, target, ctx);\n"
         "\temit(A64_BLR(tmp), ctx);\n"
         "}\n"
         "\n"
         "static inline int bpf2a64_offset(int bpf_insn, int off,\n",
         T),
        # The one existing helper-call site goes through the new emitter.
        ("arch/arm64/net/bpf_jit_comp.c",
         "\t\temit_addr_mov_i64(tmp, func_addr, ctx);\n"
         "\t\temit(A64_BLR(tmp), ctx);\n"
         "\t\temit(A64_MOV(1, r0, A64_R(0)), ctx);\n",
         "\t\temit_call(func_addr, ctx);\n"
         "\t\temit(A64_MOV(1, r0, A64_R(0)), ctx);\n",
         T),
        # The trampoline generator itself, appended after bpf_arch_text_poke()
        # rather than before is_long_jump(): arm64_bpf_text_poke's payload is
        # one contiguous block, so inserting into its middle would stop that
        # group's `new` block from matching on the second pass while its `old`
        # anchor still did -- appending a second copy of the whole poking side
        # (group_recipe trap 5).  No C ordering depends on the placement:
        # every helper the trampoline uses is defined above, and
        # arch_prepare_bpf_trampoline() is called from another TU.
        ("arch/arm64/net/bpf_jit_comp.c",
         "out:\n"
         "\tmutex_unlock(&text_mutex);\n"
         "\n"
         "\treturn ret;\n"
         "}\n",
         "out:\n"
         "\tmutex_unlock(&text_mutex);\n"
         "\n"
         "\treturn ret;\n"
         "}\n" +
         _ARM64_BPF_TRAMPOLINE_FN,
         T),
    ])
    return status, detail


PATCH_GROUPS = PATCH_GROUPS + [
    PatchGroup(
        "sched_sis_util",
        "SIS_UTIL: bound the LLC idle-CPU scan with the hint the periodic load"
        " balancer derives from sum_util, and disable the avg_idle-prediction"
        " budget it supersedes (mainline v6.0 70fb5ccf2ebb; the android15-6.6"
        " line ships SIS_PROP=false + SIS_UTIL=true).  Supersedes"
        " batch15_perf_sched_refinements' avg_idle_preemption_mode on the scan"
        " budget only; that group's rq->wake_avg_idle/wake_stamp retirement"
        " remains in force.  struct sched_domain_shared grows nr_idle_scan"
        " before ANDROID_VENDOR_DATA(1), mirroring android14-6.1.",
        ["70fb5ccf2ebb (mainline v6.0; present on android15-6.6)"],
        ["include/linux/sched/topology.h", "kernel/sched/features.h", "kernel/sched/fair.c"],
        _sis_util_apply,
    ),
    PatchGroup(
        "arm64_insn_load_literal",
        "aarch64_insn_gen_load_literal() (plus the unsigned-immediate load/store"
        " generator it shares an encoding with) and the A64_LS_IMM / A64_LDR*LIT"
        " / A64_NOP macros built on them.  Both arm64 bpf groups below need"
        " them: struct bpf_plt emits A64_LDR64LIT and the trampoline saves"
        " arguments with A64_STR64I/A64_LDR64I, neither of which exists on"
        " 5.15.  Dead code until arm64_bpf_text_poke runs.  Carried from the"
        " mainline v6.1 arm64 BPF-trampoline series, adapted to this tree:"
        " the file is arch/arm64/lib/insn.c here (kernel/insn.c is a 404 on"
        " android13-5.15-lts), there is no aarch64_insn_ldst_size[] array,"
        " and the offset check is still branch_imm_common().",
        ["b2ad54e1533e / efc9909fdce0 dependency (mainline v6.1)",
         "A64_LS_IMM + A64_LDR*LIT + A64_NOP"],
        ["arch/arm64/include/asm/insn.h", "arch/arm64/lib/insn.c",
         "arch/arm64/net/bpf_jit.h"],
        _arm64_insn_load_literal_apply,
    ),
    PatchGroup(
        "arm64_bpf_text_poke",
        "bpf_arch_text_poke() for arm64: the bpf prog entry gains an"
        " ftrace-style patchsite (mov x9, lr; nop), the prog tail grows a plt"
        " that points at dummy_tramp until a long-jump trampoline replaces it,"
        " and kernel functions are left to ftrace (-ENOTSUPP).  This is what"
        " lets a bpf trampoline attach on arm64 at all.  Written for 5.15, not"
        " copied: this tree's prologue has no paciasp, so PROLOGUE_OFFSET is"
        " BTI_INSNS + 2 + 7 and POKE_OFFSET is BTI_INSNS + 1; linux/sizes.h"
        " already arrives through filter.h -> skbuff.h -> dma-mapping.h, so"
        " SZ_128M needs no include.  19f68ed6dc90 (kvcalloc for ctx.offset) is"
        " deliberately NOT carried -- kvcalloc()/kvfree() live in"
        " linux/mm.h, which this TU does not include on 5.15, and pulling mm.h"
        " in for one allocation helper has nothing to do with the trampoline.",
        ["b2ad54e1533e (mainline v6.1)",
         "33f32e5072b6 + 339ed900b307 (dummy_tramp .global / x30, folded in)"],
        ["arch/arm64/net/bpf_jit_comp.c"],
        _arm64_bpf_text_poke_apply,
    ),
    PatchGroup(
        "arm64_bpf_trampoline",
        "The arm64 BPF trampoline: native ABI -> bpf ABI conversion for"
        " fentry/fexit/fmod_ret and struct_ops.  This group is what makes"
        " struct_ops -- and therefore sched_ext -- attachable on arm64; the"
        " two groups above are its prerequisites (the instruction macros and"
        " the poking side), and it is registered last because it is their"
        " only consumer.  Re-authored onto this tree's interface, not copied:"
        " kernel/bpf/trampoline.c here passes struct bpf_tramp_progs and"
        " __bpf_prog_enter(prog)/__bpf_prog_exit(prog, start) take no"
        " bpf_tramp_run_ctx, so a program carries no cookie slot and the"
        " trampoline stack has no run-ctx frame; a verbatim v6.1 copy"
        " references bpf_tramp_links/bpf_tramp_run_ctx and does not compile."
        " aada47665546 (__le32 branch targets + cpu_to_le32) is folded in --"
        " this tree's ctx->image is already __le32 *, so the sparse warning"
        " that commit fixes is a compile error here.  eb707dde264a (reject"
        " struct arguments) is deliberately NOT carried: struct"
        " btf_func_model has no arg_flags[] on 5.15 and"
        " BTF_FMODEL_STRUCT_ARG does not exist, because trampoline struct"
        " arguments only landed upstream after 5.15 -- the guard would not"
        " compile and there is nothing for it to reject."
        " Sized but not attach-verified on device: arm64 selects no"
        " HAVE_DYNAMIC_FTRACE_WITH_DIRECT_CALLS on 5.15 either, so fentry on"
        " an ftrace-managed target stays -ENOTSUPP; struct_ops, which takes"
        " the bpf_arch_text_poke() path, is what this group unblocks.",
        ["efc9909fdce0 (mainline v6.1, the trampoline)",
         "aada47665546 (folded in: __le32 branch targets)",
         "eb707dde264a (measured inapplicable on 5.15, see above)"],
        ["arch/arm64/net/bpf_jit_comp.c"],
        _arm64_bpf_trampoline_apply,
    ),
]


# ============================================================================
# Batch 53 (sched_ext S1b): the kfunc allow-list API, the verifier gate that
# consults it, and the arm64 override that lets a kfunc be called at all.
# Registered after every arm64 group on purpose: arm64_bpf_text_poke appends a
# whole poking payload after bpf_jit_free_exec() and arm64_bpf_trampoline
# appends the trampoline inside that payload, so a new block inserted into
# either of them would stop their own `new` block from matching on the second
# pass while their `old` anchor still did -- group_recipe trap 5, the earlier
# group appends its payload again.  arm64_jit_kfunc_call therefore anchors well
# above them, on bpf_jit_alloc_exec_limit(), which neither group touches.
# ============================================================================
import batch53_perf_btf_kfunc as _b53_kfunc  # noqa: E402

PATCH_GROUPS = PATCH_GROUPS + _b53_kfunc.build_groups(PatchGroup)


# ============================================================================
# Batch 55 (sched_ext S2b-1): the build wiring that makes the Batch-54 payload
# a compiled subsystem.  Registered last on purpose: sched_ext_task_slot claims
# task_struct KABI slot 7 and anchors on the post-randomize_kstack_pertask run
# (which appended to slot 8), so the two must run in that order.
# ============================================================================
import batch55_perf_sched_ext_wiring as _b55_scx  # noqa: E402

PATCH_GROUPS = PATCH_GROUPS + _b55_scx.build_groups(PatchGroup)


# ============================================================================
# Batch 57 (sched_ext S2b-2a): the 5.15 adaptation layer for the Batch-54
# payload.  Registered after Batch 55 on purpose: sched_ext_task_slot anchors on
# the post-randomize_kstack_pertask run and sched_ext_rq_state appends to
# kernel/sched/sched.h, while these two groups edit kernel/sched/core.c,
# kernel/sched/sched.h and the overlaid payload -- and the overlay runs before
# this child (scripts/stable_backport.sh), because sched_ext_payload_adapt edits
# kernel/sched/ext.c, which the overlay is what creates.
# ============================================================================
import batch57_perf_sched_ext_adapt as _b57_adapt  # noqa: E402  (three groups)

PATCH_GROUPS = PATCH_GROUPS + _b57_adapt.build_groups(PatchGroup)


# ============================================================================
# Batch 58 (sched_ext S2b-2b, first half): the task-lifecycle hooks.  Registered
# last, and after Batch 57 in particular: sched_ext_setscheduler_hooks edits the
# __setscheduler_prio() body sched_ext_core_visibility generates (that group
# therefore probes for its own marker first), and these groups edit
# kernel/sched/ext.c, which the overlay creates before this child runs.
# ============================================================================
import batch58_perf_sched_ext_hooks as _b58_hooks  # noqa: E402  (five groups)

PATCH_GROUPS = PATCH_GROUPS + _b58_hooks.build_groups(PatchGroup)


# ============================================================================
# Batch 59 (sched_ext S2b-2b, second half, part 1): the scheduling-core hooks.
# Registered after Batch 58 for the same reason: the consumers of
# for_each_active_class() must see the materialised payload, and
# sched_ext_active_class rewrites that macro in the tree copy of
# kernel/sched/ext.h, which the overlay creates before this child runs.
# ============================================================================
import batch59_perf_sched_ext_pick as _b59_pick  # noqa: E402  (four groups)

PATCH_GROUPS = PATCH_GROUPS + _b59_pick.build_groups(PatchGroup)


# ============================================================================
# Batch 60 (sched_ext S2b-2b, second half, part 2): reachability.  Registered
# last: sched_ext_policy_valid opens the syscall gate the earlier batches' hooks
# sit behind, and the struct_ops type entry is what lets a BPF scheduler bind at
# all.  Two files enter the fixture lists here (kernel/bpf/
# bpf_struct_ops_types.h and kernel/sched/debug.c), so the reference trees were
# re-fetched before the audits were run.
# ============================================================================
import batch60_perf_sched_ext_reach as _b60_reach  # noqa: E402  (four groups)

PATCH_GROUPS = PATCH_GROUPS + _b60_reach.build_groups(PatchGroup)


def main():
    args = parse_args("stable_perf_backport: 5.15.y scheduler/net/locking/block optimization grafts")
    ctx = make_context(args)
    enabled = ctx.family == "android13-5.15" or args.allow_unsupported
    if not enabled:
        print(f"[ABK stable_515_backport] unsupported family {ctx.family}; "
              "every group reports report_only and nothing is written "
              "(pass --allow-unsupported to override)")
    run_child("stable_perf_backport", PATCH_GROUPS, ctx, args,
              enabled=enabled)


if __name__ == "__main__":
    main()
