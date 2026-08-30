"""Child ``stable_perf_backport``: upstream 5.15.y optimization grafts.

Carries the NOHZ idle-balance optimization series (5.15.174), the PSI
migration flags micro-optimization (5.15.179), the RT scan optimizations
(5.15.202/.212), per-task kstack randomization (5.15.210), the
__release_sock() cond_resched reduction (5.15.197), the semaphore wake_q
offload (5.15.180), the blk-mq suspend wakeup abort (5.15.198), and the
android14-6.1 line (lazy preemption + mutex/rwsem wakeup vendor hooks,
PSI IRQ pressure tracking, PSI trigger kernfs polling).

KMI notes: the per-task kstack offset reuses task_struct's
ANDROID_KABI_RESERVE(8) slot instead of growing the struct, and the PSI group
only removes a bitfield member whose word is force-aligned by ``unsigned :0``
(upstream-verified no-op for struct layout).  The android14-6.1 groups only
add vendor tracepoints and heap-internal struct members, so the stable KMI is
preserved; the ACK 6.1 psi_group pointer/parent rework is deliberately NOT
ported (it changes struct cgroup layout).

Every group degrades to a reported status when its anchor shape is absent.
KMI slots claimed by other graft modules (sched_entity 1-4, request_queue 1)
are never touched by this child.

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
# Per-task kstack randomization offset (5.15.210) - KMI-safe via KABI slot 8
# ---------------------------------------------------------------------------

def _sched_h_kstack_step(text):
    """Pick the KABI slot for kstack_offset based on the tree shape.

    ABK's kernel-specific patch step rewrites task_struct's slots 6/7/8 into
    the CONFIG_SYSVIPC sysvsem/sysvshm restoration (an #ifdef/#else block).
    In that shape slots 7/8 only exist inside the dead #else branch, so a
    first-occurrence RESERVE(8) replacement lands in dead code and the struct
    silently loses the member.  Use the still-free slot 5 there, and the
    anchored slots 1..8 run otherwise.
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
    if "kstack_offset;" not in text:
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

def _blk_mq_suspend_apply(ctx):
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
]


def main():
    args = parse_args("stable_perf_backport: 5.15.y scheduler/net/locking/block optimization grafts")
    ctx = make_context(args)
    if ctx.family != "android13-5.15":
        print(f"[ABK stable_515_backport] unsupported family {ctx.family}; "
              "all groups stay report-only")
    run_child("stable_perf_backport", PATCH_GROUPS, ctx, args)


if __name__ == "__main__":
    main()
