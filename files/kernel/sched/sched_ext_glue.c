// SPDX-License-Identifier: GPL-2.0
/*
 * sailboat_sched_ext: the compilation unit for the sched_ext (SCX) payload in
 * this directory.
 *
 * The payload's kernel/sched/ext.c carries no include block of its own.  On the
 * 6.6 tree it is compiled by textual inclusion from kernel/sched/build_policy.c,
 * which has already pulled in every header it needs; 5.15 has no build_policy.c
 * (its policy files -- idle.c, rt.c, deadline.c, cputime.c, pelt.c,
 * cpudeadline.c -- are separate objects) and this module ships ext.c
 * byte-for-byte, so the include set lives here instead and kernel/sched/Makefile
 * builds this file rather than ext.c.
 *
 * The list mirrors the header block ext.c was compiled with on the 6.6 tree,
 * minus linux/seqlock_api.h (introduced after 5.15), minus the
 * \`#include "*.c"\` lines whose objects 5.15 builds separately -- pulling
 * those in here would emit the same symbols twice -- and minus autogroup.h
 * and stats.h.
 *
 * Those last two are not optional trimming and they are not a style choice:
 * 6.6 moved both into build_policy.c, so its header block lists them after
 * sched.h, but 5.15's kernel/sched/sched.h still includes them itself at its
 * tail, and neither header has an include guard.  Listing them here a second
 * time redefines every helper they define (measured on a configured
 * android13-5.15-lts tree: 20 errors, autogroup_task_group() and
 * sched_info_switch() among them).  They are reached through sched.h below.
 *
 * kernel/sched/sched.h pulls in kernel/sched/ext.h at its end (and
 * include/linux/sched.h pulls in include/linux/sched/ext.h), so neither is
 * listed again: kernel/sched/ext.h has no include guard and would redefine its
 * enums on a second inclusion.
 */
#include <linux/sched/clock.h>
#include <linux/sched/cputime.h>
#include <linux/sched/hotplug.h>
#include <linux/sched/rt.h>

#include <linux/cpuidle.h>
#include <linux/fs.h>
#include <linux/irq_work.h>
#include <linux/jiffies.h>
#include <linux/kthread.h>
#include <linux/livepatch.h>
#include <linux/percpu-rwsem.h>
#include <linux/psi.h>
#include <linux/seq_file.h>
#include <linux/slab.h>
#include <linux/suspend.h>
#include <linux/sysrq.h>
#include <linux/tsacct_kern.h>
#include <linux/vtime.h>

#include <uapi/linux/sched/types.h>

#include "sched.h"
#include "smp.h"

/* autogroup.h and stats.h are *not* listed here on purpose: sched.h already
 * includes both on this baseline, and neither has an include guard -- see the
 * file comment above.  pelt.h is not included by sched.h and is still needed. */
#include "pelt.h"

/* ===========================================================================
 * sailboat_sched_ext_payload_adapt: the 5.15 interface shims
 *
 * kernel/sched/ext.c is archived byte-for-byte from the 6.6 vendor tree and is
 * compiled from this unit unchanged.  Everything between this comment and
 * #include "ext.c" below is the interface layer that lets 6.6-shaped code see a
 * 5.15 baseline; the sites the C preprocessor cannot bridge (function-pointer
 * signatures, a vendor struct member, and the 6.2 SCHED_CHANGE_BLOCK whose
 * helpers are static inline in core.c) are either edited in ext.c by
 * batch57_perf_sched_ext_adapt or re-carried in core.c by
 * sched_ext_change_guard.  The measured list, the line numbers and the reason
 * each class is where it is: docs/survey_sched_ext_gap.md section 2c.
 * =========================================================================== */

/*
 * Upstream 6.6, kernel/sched/sched.h.  ext.c converts a task's static priority
 * to the cgroup weight scale before handing it to the BPF scheduler; the helper
 * only moved into sched.h after 5.15.  CGROUP_WEIGHT_{MIN,DFL,MAX} come in
 * through sched.h -> linux/cgroup.h.
 */
static inline unsigned long sched_weight_to_cgroup(unsigned long weight)
{
	return clamp_t(unsigned long,
		       DIV_ROUND_CLOSEST_ULL(weight * CGROUP_WEIGHT_DFL, 1024),
		       CGROUP_WEIGHT_MIN, CGROUP_WEIGHT_MAX);
}

/*
 * SCHED_CHANGE_BLOCK is 6.2's, and it is deliberately NOT carried here even
 * though the payload uses it: its expansion declares the guard in the for-loop
 * initialiser, and this baseline still builds with -std=gnu89, where clang
 * rejects that with -Wgcc-compat (an error under CONFIG_WERROR).  The guard's
 * *body* also calls dequeue_task()/enqueue_task(), which are static inline in
 * kernel/sched/core.c and carry the vendor trace hooks -- copying them into
 * this unit would fork the accounting they do.  So the two functions are
 * re-carried in core.c by the sched_ext_change_guard registry group and the
 * three call sites are open-coded by sched_ext_payload_adapt.
 */

/*
 * Upstream 6.2, include/linux/cpumask.h: "all CPUs in @mask1 that are not in
 * @mask2".  5.15 has neither the macro nor cpumask_next_andnot(), so the walk is
 * carried here instead of just the macro -- the open-coded form keeps the
 * payload's break/continue semantics exact and does not depend on a temporary
 * cpumask on the caller's stack.
 */
static inline unsigned int
sailboat_cpumask_next_andnot(int n, const struct cpumask *mask1,
			     const struct cpumask *mask2)
{
	while ((n = cpumask_next(n, mask1)) < nr_cpu_ids)
		if (!cpumask_test_cpu(n, mask2))
			break;
	return n;
}

#define for_each_cpu_andnot(cpu, mask1, mask2)				\
	for ((cpu) = -1;						\
	     (cpu) = sailboat_cpumask_next_andnot((cpu), (mask1), (mask2)), \
	     (cpu) < nr_cpu_ids;)

/*
 * include/linux/btf.h renamed the helper in 6.0; the 5.15 function of the old
 * name is byte-for-byte the same two lines.
 */
#define __btf_member_bit_offset	btf_member_bit_offset

/*
 * Upstream 6.2, include/linux/compiler_types.h.  5.15 only carries the
 * per-compiler __diag_ignore(compiler, version, option, comment) form, and it
 * defines __diag_clang_11 / __diag_clang_23 but not the 6.6 gate's _13 -- so the
 * suppression maps onto the lowest version this baseline knows, which is the
 * same _Pragma the 6.6 form ends up emitting.
 */
#define __diag_ignore_all(option, comment)				\
	__diag_ignore(clang, 11, option, comment)

#include "ext.c"
