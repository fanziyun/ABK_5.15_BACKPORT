// SPDX-License-Identifier: GPL-2.0
/*
 * abk_scx_min -- the smallest usable sched_ext scheduler for this kernel.
 *
 * This is the userspace half of S3: the first BPF scheduler that can actually
 * be attached to the class Batches 54-60 landed.  It is deliberately trivial --
 * one global FIFO DSQ, no vtime, no per-CPU DSQs, no cpumask work -- because
 * what it has to prove first is the attach path itself:
 *
 *   struct_ops map bind -> ops.init (create the DSQ) -> ops.enqueue
 *   (dispatch to it) -> ops.dispatch (consume into the local DSQ) ->
 *   the task runs on SCHED_EXT -> ops.exit (destroy the DSQ).
 *
 * Design decisions that belong in the file:
 *
 *  - **Partial by default.**  The scheduler never calls scx_bpf_switch_all(),
 *    so only tasks explicitly marked SCHED_EXT are taken; everything else
 *    (including this ROM's vendor WALT/FAS tenants) stays on the built-in
 *    classes.  That is the only mode whose blast radius is a task list the
 *    user chose.
 *
 *  - **No ops.select_cpu work.**  It returns prev_cpu untouched; wake-up CPU
 *    selection keeps the built-in behaviour.  A version that wants to steer
 *    placement has to pick idle CPUs itself, which needs a cpumask kfunc
 *    family this payload does not carry (survey section 2b/7).
 *
 *  - **The kfunc set is the payload's, not mainline's.**  Only four kfuncs are
 *    called and all four are in the landed registry; the unit test pins that.
 */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>

#include "abk_scx_compat.h"

char _license[] SEC("license") = "GPL";

/*
 * Wake-up placement: leave it to the kernel's own selection.  The op exists so
 * the attach path exercises an op that is called on every wake-up, not because
 * it does anything yet.
 */
SEC("struct_ops/abk_scx_min_select_cpu")
s32 abk_scx_min_select_cpu(struct task_struct *p, s32 prev_cpu, u64 wake_flags)
{
	return prev_cpu;
}

/*
 * Every enqueue goes straight to the single DSQ with the default slice.  FIFO
 * order and no accounting: this scheduler makes no latency claim, it is the
 * harness the claim will be measured on.
 */
SEC("struct_ops/abk_scx_min_enqueue")
void abk_scx_min_enqueue(struct task_struct *p, u64 enq_flags)
{
	scx_bpf_dispatch(p, ABK_SCX_DSQ_ID, ABK_SCX_SLICE_DFL, enq_flags);
}

/*
 * A DSQ has something to run: move one task to the local DSQ.  scx_bpf_consume()
 * is a no-op when the DSQ is empty or the local DSQ is already occupied.
 */
SEC("struct_ops/abk_scx_min_dispatch")
void abk_scx_min_dispatch(s32 cpu, struct task_struct *prev)
{
	scx_bpf_consume(ABK_SCX_DSQ_ID);
}

/*
 * ops.init runs in a sleepable context (SCX_KF_INIT), which is why the program
 * is in the ".s" section: scx_bpf_create_dsq() is registered in the sleepable
 * kfunc set and refuses to run anywhere else.
 */
SEC("struct_ops.s/abk_scx_min_init")
s32 abk_scx_min_init(void)
{
	return scx_bpf_create_dsq(ABK_SCX_DSQ_ID, -1);
}

SEC("struct_ops/abk_scx_min_exit")
void abk_scx_min_exit(struct scx_exit_info *info)
{
	scx_bpf_destroy_dsq(ABK_SCX_DSQ_ID);
}

/*
 * The struct_ops map.  ops.name is what /sys/kernel/debug/sched/ext reports and
 * what the loader matches on; it must not be empty (the engine rejects an empty
 * name at bind time).
 */
SEC(".struct_ops")
struct sched_ext_ops abk_scx_min_ops = {
	.select_cpu	= (void *)abk_scx_min_select_cpu,
	.enqueue	= (void *)abk_scx_min_enqueue,
	.dispatch	= (void *)abk_scx_min_dispatch,
	.init		= (void *)abk_scx_min_init,
	.exit		= (void *)abk_scx_min_exit,
	.name		= "abk_scx_min",
};
