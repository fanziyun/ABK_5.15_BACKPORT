"""Child ``stable_perf_backport``: upstream 5.15.y optimization grafts.

Carries the NOHZ idle-balance optimization series (5.15.174), the PSI
migration flags micro-optimization (5.15.179), the RT scan optimizations
(5.15.202/.212), per-task kstack randomization (5.15.210), the
__release_sock() cond_resched reduction (5.15.197), the semaphore wake_q
offload (5.15.180), and the blk-mq suspend wakeup abort (5.15.198).

KMI notes: the per-task kstack offset reuses task_struct's
ANDROID_KABI_RESERVE(8) slot instead of growing the struct, and the PSI group
only removes a bitfield member whose word is force-aligned by ``unsigned :0``
(upstream-verified no-op for struct layout).

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
         "\tint clear = TSK_RUNNING;\n\n\tif (static_branch_likely(&psi_disabled))\n\t\treturn;\n",
         "\tif (static_branch_likely(&psi_disabled))\n\t\treturn;\n",
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
         " */\n"
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
         "\t\t\t randomize_kstack_offset);\nDECLARE_PER_CPU(u32, kstack_offset);\n",
         "\t\t\t randomize_kstack_offset);\n",
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
         "DEFINE_PER_CPU(u32, kstack_offset);\n",
         "DEFINE_STATIC_KEY_MAYBE_RO(CONFIG_RANDOMIZE_KSTACK_OFFSET_DEFAULT,\n"
         "\t\t\t   randomize_kstack_offset);\n",
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
