"""Batch 58 (sched_ext S2b-2b, first half): the task-lifecycle hooks.

Batch 57 made the 143 KB engine compile.  Compiling is not scheduling: on this
baseline `valid_policy()` still rejects `SCHED_EXT` and not one of the calls
that moves a task in or out of the BPF class exists, so the class sits in
`SCHED_DATA` and is never selected.  This batch lands the half of those calls
that lives on the task lifecycle -- fork, the `sched_setscheduler()` guard and
task teardown -- which is also the half whose call sites are either guarded by
`scx_enabled()` or change nothing while no BPF scheduler is loaded.

Four groups, plus the payload fix the hooks make reachable:

* `sched_ext_fork_hooks` -- `kernel/sched/core.c`'s `sched_fork()` gains
  `scx_pre_fork()` and the `task_on_scx()` class branch, plus the
  `out_cancel` unwind the 5.15 body does not have (the DL rejection below runs
  after the fork reader lock is taken); `sched_cgroup_fork()` returns
  `scx_fork()`'s verdict instead of `void`; `sched_post_fork()` ends with
  `scx_post_fork()`; and the new `sched_cancel_fork()` is the unwind for
  every failure after `sched_fork()` returned 0.  The two prototypes that
  change live in `include/linux/sched/task.h`.

* `sched_ext_fork_failure_path` -- `kernel/fork.c`'s `copy_process()`:
  `perf_event_init_task()`'s failure now unwinds through the new
  `bad_fork_sched_cancel_fork` label instead of straight to
  `bad_fork_cleanup_policy`, and `sched_cgroup_fork()`'s new return value
  routes to `bad_fork_cancel_cgroup`, which reaches the same label.  Both are
  6.4's shape; without them a rejected fork leaks the fork reader lock.

* `sched_ext_task_teardown` -- `kernel/fork.c`'s `__put_task_struct()`
  detaches the task from SCX's task list.  This is 6.6's call site; the 5.15
  failure paths go through `delayed_free_task()`/`free_task()` instead, so
  the list node is always linked by the time this runs.

* `sched_ext_setscheduler_hooks` -- `__setscheduler_prio()` gains the
  `task_on_scx()` branch and `__sched_setscheduler()` gains
  `scx_check_setscheduler()` right after the `rq->stop` rejection, which is
  what makes `ops.prep_enable()`'s `disallow` verdict stick.

* `sched_ext_payload_task_guard` -- the first callers of the four fork hooks
  expose two defects the archived payload carries: `scx_pre_fork()` allocates
  the per-task `struct sched_ext_entity` and, when that allocation fails,
  silently falls through to a task that everything else then dereferences, and
  nothing ever frees the entity.  The guard makes the failure path consistent
  (the task simply stays out of SCX) and adds the missing free.

What is deliberately NOT here: the pick path (`for_each_active_class`,
`put_prev_task_balance`), the tick watchdog, `scx_update_idle()`, the
`ext` debugfs file and the decision that makes `SCHED_EXT` selectable.  The
class is still unreachable after this batch.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

CORE_C = "kernel/sched/core.c"
SCHED_TASK_H = "include/linux/sched/task.h"
FORK_C = "kernel/fork.c"
EXT_C = "kernel/sched/ext.c"


# ---------------------------------------------------------------------------
# kernel/sched/core.c: sched_fork()/sched_cgroup_fork()/sched_post_fork() and
# the new sched_cancel_fork(); include/linux/sched/task.h: the two prototypes.
# ---------------------------------------------------------------------------

_FORK_RET_OLD = (
    "int sched_fork(unsigned long clone_flags, struct task_struct *p)\n"
    "{\n"
    "\ttrace_android_rvh_sched_fork(p);\n"
)

_FORK_RET_NEW = (
    "int sched_fork(unsigned long clone_flags, struct task_struct *p)\n"
    "{\n"
    "\t/*\n"
    "\t * sailboat_sched_ext_fork_hooks: SCX's sched_fork() hooks need one\n"
    "\t * unwind point.  The DL rejection below runs after scx_pre_fork() has\n"
    "\t * taken the fork reader lock, so it cannot simply return -EAGAIN.\n"
    "\t */\n"
    "\tint ret;\n"
    "\n"
    "\ttrace_android_rvh_sched_fork(p);\n"
)

_FORK_CLASS_OLD = (
    "\t\tp->sched_reset_on_fork = 0;\n"
    "\t}\n"
    "\n"
    "\tif (dl_prio(p->prio))\n"
    "\t\treturn -EAGAIN;\n"
    "\telse if (rt_prio(p->prio))\n"
    "\t\tp->sched_class = &rt_sched_class;\n"
    "\telse\n"
    "\t\tp->sched_class = &fair_sched_class;\n"
)

_FORK_CLASS_NEW = (
    "\t\tp->sched_reset_on_fork = 0;\n"
    "\t}\n"
    "\n"
    "\t/*\n"
    "\t * sailboat_sched_ext_fork_hooks: allocate @p's SCX state and take the\n"
    "\t * fork reader lock before @p becomes visible.  task_on_scx() is false\n"
    "\t * on a build without the class and false until a scheduler is loaded,\n"
    "\t * so the selection below is unchanged for every other task.\n"
    "\t */\n"
    "\tscx_pre_fork(p);\n"
    "\n"
    "\tif (dl_prio(p->prio)) {\n"
    "\t\tret = -EAGAIN;\n"
    "\t\tgoto out_cancel;\n"
    "#ifdef CONFIG_SCHED_CLASS_EXT\n"
    "\t} else if (task_on_scx(p)) {\n"
    "\t\tp->sched_class = &ext_sched_class;\n"
    "#endif\n"
    "\t} else if (rt_prio(p->prio)) {\n"
    "\t\tp->sched_class = &rt_sched_class;\n"
    "\t} else {\n"
    "\t\tp->sched_class = &fair_sched_class;\n"
    "\t}\n"
)

_FORK_TAIL_OLD = (
    "\tRB_CLEAR_NODE(&p->pushable_dl_tasks);\n"
    "#endif\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
    "void sched_cgroup_fork(struct task_struct *p, struct kernel_clone_args *kargs)\n"
)

_FORK_TAIL_NEW = (
    "\tRB_CLEAR_NODE(&p->pushable_dl_tasks);\n"
    "#endif\n"
    "\treturn 0;\n"
    "\n"
    "out_cancel:\n"
    "\t/*\n"
    "\t * sailboat_sched_ext_fork_hooks: scx_pre_fork() already ran, so the DL\n"
    "\t * rejection has to release what it took.\n"
    "\t */\n"
    "\tscx_cancel_fork(p);\n"
    "\treturn ret;\n"
    "}\n"
    "\n"
    "/*\n"
    " * sailboat_sched_ext_fork_hooks: int since SCX's scx_fork() can reject the\n"
    " * child.  copy_process() unwinds a rejection through sched_cancel_fork().\n"
    " */\n"
    "int sched_cgroup_fork(struct task_struct *p, struct kernel_clone_args *kargs)\n"
)

_CGROUP_TAIL_OLD = (
    "\tif (p->sched_class->task_fork)\n"
    "\t\tp->sched_class->task_fork(p);\n"
    "\traw_spin_unlock_irqrestore(&p->pi_lock, flags);\n"
    "}\n"
    "\n"
    "void sched_post_fork(struct task_struct *p)\n"
)

_CGROUP_TAIL_NEW = (
    "\tif (p->sched_class->task_fork)\n"
    "\t\tp->sched_class->task_fork(p);\n"
    "\traw_spin_unlock_irqrestore(&p->pi_lock, flags);\n"
    "\n"
    "\t/*\n"
    "\t * sailboat_sched_ext_fork_hooks: @p and its cgroup are pinned by now,\n"
    "\t * so the SCX prepare hook may sleep.\n"
    "\t */\n"
    "\treturn scx_fork(p);\n"
    "}\n"
    "\n"
    "void sched_post_fork(struct task_struct *p)\n"
)

_POST_FORK_OLD = (
    "void sched_post_fork(struct task_struct *p)\n"
    "{\n"
    "\tuclamp_post_fork(p);\n"
    "}\n"
)

_POST_FORK_NEW = (
    "void sched_post_fork(struct task_struct *p)\n"
    "{\n"
    "\tuclamp_post_fork(p);\n"
    "\n"
    "\t/*\n"
    "\t * sailboat_sched_ext_fork_hooks: @p is fully formed, so it may be\n"
    "\t * handed to the BPF scheduler.  This is the fork reader lock's release\n"
    "\t * for the success path.\n"
    "\t */\n"
    "\tscx_post_fork(p);\n"
    "}\n"
    "\n"
    "/*\n"
    " * sailboat_sched_ext_fork_hooks: the unwind for every failure after\n"
    " * sched_fork() returned 0 -- copy_process()'s bad_fork_sched_cancel_fork\n"
    " * label.  scx_post_fork() never ran, so the fork reader lock and any\n"
    " * prepared SCX state have to be released here.\n"
    " */\n"
    "\n"
    "void sched_cancel_fork(struct task_struct *p)\n"
    "{\n"
    "\tscx_cancel_fork(p);\n"
    "}\n"
)

_TASK_H_OLD = (
    "extern void sched_cgroup_fork(struct task_struct *p, struct kernel_clone_args *kargs);\n"
    "extern void sched_post_fork(struct task_struct *p);\n"
)

_TASK_H_NEW = (
    "/*\n"
    " * sailboat_sched_ext_fork_hooks: sched_cgroup_fork() returns SCX's fork\n"
    " * verdict (0 or -errno) so copy_process() can unwind it, and\n"
    " * sched_cancel_fork() is that unwind for every failure between sched_fork()\n"
    " * and sched_post_fork().\n"
    " */\n"
    "extern int sched_cgroup_fork(struct task_struct *p, struct kernel_clone_args *kargs);\n"
    "extern void sched_cancel_fork(struct task_struct *p);\n"
    "extern void sched_post_fork(struct task_struct *p);\n"
)


def _sched_ext_fork_hooks_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (CORE_C, _FORK_RET_OLD, _FORK_RET_NEW, True),
        (CORE_C, _FORK_CLASS_OLD, _FORK_CLASS_NEW, True),
        (CORE_C, _FORK_TAIL_OLD, _FORK_TAIL_NEW, True),
        (CORE_C, _CGROUP_TAIL_OLD, _CGROUP_TAIL_NEW, True),
        (CORE_C, _POST_FORK_OLD, _POST_FORK_NEW, True),
        (SCHED_TASK_H, _TASK_H_OLD, _TASK_H_NEW, True),
    ])
    return status, detail


# ---------------------------------------------------------------------------
# kernel/fork.c: copy_process()'s unwind labels.
# ---------------------------------------------------------------------------

_PERF_FAIL_OLD = (
    "\tretval = perf_event_init_task(p, clone_flags);\n"
    "\tif (retval)\n"
    "\t\tgoto bad_fork_cleanup_policy;\n"
)

_PERF_FAIL_NEW = (
    "\tretval = perf_event_init_task(p, clone_flags);\n"
    "\tif (retval)\n"
    "\t\tgoto bad_fork_sched_cancel_fork;\n"
)

_CGROUP_CALL_OLD = (
    "\tsched_cgroup_fork(p, args);\n"
)

_CGROUP_CALL_NEW = (
    "\t/*\n"
    "\t * sailboat_sched_ext_fork_failure_path: SCX's scx_fork() can reject the\n"
    "\t * child, so this call reports a verdict now.  The unwind runs through\n"
    "\t * bad_fork_cancel_cgroup down to the bad_fork_sched_cancel_fork label\n"
    "\t * below, which is also where the perf_event_init_task() failure above\n"
    "\t * lands.\n"
    "\t */\n"
    "\tretval = sched_cgroup_fork(p, args);\n"
    "\tif (retval)\n"
    "\t\tgoto bad_fork_cancel_cgroup;\n"
)

_CANCEL_LABEL_OLD = (
    "bad_fork_cleanup_perf:\n"
    "\tperf_event_free_task(p);\n"
    "bad_fork_cleanup_policy:\n"
)

_CANCEL_LABEL_NEW = (
    "bad_fork_cleanup_perf:\n"
    "\tperf_event_free_task(p);\n"
    "bad_fork_sched_cancel_fork:\n"
    "\t/*\n"
    "\t * sailboat_sched_ext_fork_failure_path: sched_fork() ran, so SCX's fork\n"
    "\t * reader lock and any prepared per-task state have to be released even\n"
    "\t * though sched_post_fork() never will.\n"
    "\t */\n"
    "\tsched_cancel_fork(p);\n"
    "bad_fork_cleanup_policy:\n"
)


def _sched_ext_fork_failure_path_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (FORK_C, _PERF_FAIL_OLD, _PERF_FAIL_NEW, True),
        (FORK_C, _CGROUP_CALL_OLD, _CGROUP_CALL_NEW, True),
        (FORK_C, _CANCEL_LABEL_OLD, _CANCEL_LABEL_NEW, True),
    ])
    return status, detail


# ---------------------------------------------------------------------------
# kernel/fork.c: __put_task_struct() takes the task off SCX's task list.
# ---------------------------------------------------------------------------

_TEARDOWN_OLD = (
    "\tWARN_ON(tsk == current);\n"
    "\n"
    "\tio_uring_free(tsk);\n"
)

_TEARDOWN_NEW = (
    "\tWARN_ON(tsk == current);\n"
    "\n"
    "\t/*\n"
    "\t * sailboat_sched_ext_task_teardown: detach @tsk from SCX's task list\n"
    "\t * before its per-task SCX state goes away with the task.  The 5.15\n"
    "\t * fork-failure paths free the task through\n"
    "\t * delayed_free_task()/free_task() instead of this function, so a task\n"
    "\t * that never reached sched_post_fork() never gets here and the list\n"
    "\t * node sched_ext_free() walks is always linked.\n"
    "\t */\n"
    "\tsched_ext_free(tsk);\n"
    "\n"
    "\tio_uring_free(tsk);\n"
)


def _sched_ext_task_teardown_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (FORK_C, _TEARDOWN_OLD, _TEARDOWN_NEW, True),
    ])
    return status, detail


# ---------------------------------------------------------------------------
# kernel/sched/core.c: the two class-selection sites.
#
# sched_ext_core_visibility (batch57_perf_sched_ext_adapt) generates the
# __setscheduler_prio() body this group edits, so that group now probes for its
# own marker before running its steps: a second pass would otherwise find
# neither its pristine old block nor its own new block (group_recipe trap 5).
# ---------------------------------------------------------------------------

_PRIO_OLD = (
    "void __setscheduler_prio(struct task_struct *p, int prio)\n"
    "{\n"
    "\tif (dl_prio(prio))\n"
    "\t\tp->sched_class = &dl_sched_class;\n"
    "\telse if (rt_prio(prio))\n"
    "\t\tp->sched_class = &rt_sched_class;\n"
    "\telse\n"
    "\t\tp->sched_class = &fair_sched_class;\n"
    "\n"
    "\tp->prio = prio;\n"
    "}\n"
)

_PRIO_NEW = (
    "void __setscheduler_prio(struct task_struct *p, int prio)\n"
    "{\n"
    "\tif (dl_prio(prio))\n"
    "\t\tp->sched_class = &dl_sched_class;\n"
    "#ifdef CONFIG_SCHED_CLASS_EXT\n"
    "\t/*\n"
    "\t * sailboat_sched_ext_setscheduler_hooks: a task whose ops.prep_enable()\n"
    "\t * accepted it, or any task while the scheduler owns the machine, keeps\n"
    "\t * the BPF class across a priority change.\n"
    "\t */\n"
    "\telse if (task_on_scx(p))\n"
    "\t\tp->sched_class = &ext_sched_class;\n"
    "#endif\n"
    "\telse if (rt_prio(prio))\n"
    "\t\tp->sched_class = &rt_sched_class;\n"
    "\telse\n"
    "\t\tp->sched_class = &fair_sched_class;\n"
    "\n"
    "\tp->prio = prio;\n"
    "}\n"
)

_STOP_GUARD_OLD = (
    "\t/*\n"
    "\t * Changing the policy of the stop threads its a very bad idea:\n"
    "\t */\n"
    "\tif (p == rq->stop) {\n"
    "\t\tretval = -EINVAL;\n"
    "\t\tgoto unlock;\n"
    "\t}\n"
)

_STOP_GUARD_NEW = (
    "\t/*\n"
    "\t * Changing the policy of the stop threads its a very bad idea:\n"
    "\t */\n"
    "\tif (p == rq->stop) {\n"
    "\t\tretval = -EINVAL;\n"
    "\t\tgoto unlock;\n"
    "\t}\n"
    "\n"
    "\t/*\n"
    "\t * sailboat_sched_ext_setscheduler_hooks: if the BPF scheduler rejected\n"
    "\t * @p from ops.prep_enable(), it can never move into SCHED_EXT.\n"
    "\t */\n"
    "\tretval = scx_check_setscheduler(p, policy);\n"
    "\tif (retval)\n"
    "\t\tgoto unlock;\n"
)


def _sched_ext_setscheduler_hooks_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (CORE_C, _PRIO_OLD, _PRIO_NEW, True),
        (CORE_C, _STOP_GUARD_OLD, _STOP_GUARD_NEW, True),
    ])
    return status, detail


# ---------------------------------------------------------------------------
# kernel/sched/ext.c: the per-task entity's failure path and its free.
#
# These are marked edits to the archived payload, like sched_ext_payload_adapt's:
# files/kernel/sched/ext.c stays byte-for-byte and the tree copy is adapted on
# the way in.
# ---------------------------------------------------------------------------

_PRE_FORK_INIT_OLD = (
    "\tINIT_LIST_HEAD(&p->scx->watchdog_node);\n"
    "\tp->scx->flags = 0;\n"
)

_PRE_FORK_INIT_NEW = (
    "\tINIT_LIST_HEAD(&p->scx->watchdog_node);\n"
    "\t/*\n"
    "\t * sailboat_sched_ext_payload_task_guard: the archived payload never\n"
    "\t * initialises this node, so a task that leaves before\n"
    "\t * scx_post_fork() linked it would hand list_del_init() in\n"
    "\t * sched_ext_free() whatever kmalloc() left there.\n"
    "\t */\n"
    "\tINIT_LIST_HEAD(&p->scx->tasks_node);\n"
    "\tp->scx->flags = 0;\n"
)

_TASK_ON_SCX_OLD = (
    "bool task_on_scx(struct task_struct *p)\n"
    "{\n"
    "\tif (!scx_enabled() || scx_ops_disabling())\n"
    "\t\treturn false;\n"
)

_TASK_ON_SCX_NEW = (
    "bool task_on_scx(struct task_struct *p)\n"
    "{\n"
    "\t/*\n"
    "\t * sailboat_sched_ext_payload_task_guard: without state there is\n"
    "\t * nothing to schedule, whatever the policy says.\n"
    "\t */\n"
    "\tif (!p->scx)\n"
    "\t\treturn false;\n"
    "\tif (!scx_enabled() || scx_ops_disabling())\n"
    "\t\treturn false;\n"
)

_SCX_FORK_OLD = (
    "\tpercpu_rwsem_assert_held(&scx_fork_rwsem);\n"
    "\n"
    "\tif (scx_enabled())\n"
    "\t\treturn scx_ops_prepare_task(p, task_group(p));\n"
    "\telse\n"
    "\t\treturn 0;\n"
)

_SCX_FORK_NEW = (
    "\tpercpu_rwsem_assert_held(&scx_fork_rwsem);\n"
    "\n"
    "\t/*\n"
    "\t * sailboat_sched_ext_payload_task_guard: scx_pre_fork() skips this task\n"
    "\t * when it could not allocate its state, and everything below inside\n"
    "\t * SCX dereferences it.\n"
    "\t */\n"
    "\tif (scx_enabled() && p->scx)\n"
    "\t\treturn scx_ops_prepare_task(p, task_group(p));\n"
    "\telse\n"
    "\t\treturn 0;\n"
)

_SCX_POST_OLD = (
    "void scx_post_fork(struct task_struct *p)\n"
    "{\n"
    "\tif (scx_enabled()) {\n"
    "\t\tstruct rq_flags rf;\n"
    "\t\tstruct rq *rq;\n"
    "\n"
    "\t\trq = task_rq_lock(p, &rf);\n"
    "\t\t/*\n"
    "\t\t * Set the weight manually before calling ops.enable() so that\n"
    "\t\t * the scheduler doesn't see a stale value if they inspect the\n"
    "\t\t * task struct. We'll invoke ops.set_weight() afterwards, as it\n"
    "\t\t * would be odd to receive a callback on the task before we\n"
    "\t\t * tell the scheduler that it's been fully enabled.\n"
    "\t\t */\n"
    "\t\tset_task_scx_weight(p);\n"
    "\t\tscx_ops_enable_task(p);\n"
    "\t\trefresh_scx_weight(p);\n"
    "\t\ttask_rq_unlock(rq, p, &rf);\n"
    "\t}\n"
    "\n"
    "\tspin_lock_irq(&scx_tasks_lock);\n"
    "\tlist_add_tail(&p->scx->tasks_node, &scx_tasks);\n"
    "\tspin_unlock_irq(&scx_tasks_lock);\n"
    "\n"
    "\tpercpu_up_read(&scx_fork_rwsem);\n"
    "}\n"
)

_SCX_POST_NEW = (
    "void scx_post_fork(struct task_struct *p)\n"
    "{\n"
    "\t/* sailboat_sched_ext_payload_task_guard: see scx_fork() */\n"
    "\tif (scx_enabled() && p->scx) {\n"
    "\t\tstruct rq_flags rf;\n"
    "\t\tstruct rq *rq;\n"
    "\n"
    "\t\trq = task_rq_lock(p, &rf);\n"
    "\t\t/*\n"
    "\t\t * Set the weight manually before calling ops.enable() so that\n"
    "\t\t * the scheduler doesn't see a stale value if they inspect the\n"
    "\t\t * task struct. We'll invoke ops.set_weight() afterwards, as it\n"
    "\t\t * would be odd to receive a callback on the task before we\n"
    "\t\t * tell the scheduler that it's been fully enabled.\n"
    "\t\t */\n"
    "\t\tset_task_scx_weight(p);\n"
    "\t\tscx_ops_enable_task(p);\n"
    "\t\trefresh_scx_weight(p);\n"
    "\t\ttask_rq_unlock(rq, p, &rf);\n"
    "\t}\n"
    "\n"
    "\tif (p->scx) {\n"
    "\t\tspin_lock_irq(&scx_tasks_lock);\n"
    "\t\tlist_add_tail(&p->scx->tasks_node, &scx_tasks);\n"
    "\t\tspin_unlock_irq(&scx_tasks_lock);\n"
    "\t}\n"
    "\n"
    "\tpercpu_up_read(&scx_fork_rwsem);\n"
    "}\n"
)

_SCX_CANCEL_OLD = (
    "void scx_cancel_fork(struct task_struct *p)\n"
    "{\n"
    "\tif (scx_enabled())\n"
    "\t\tscx_ops_disable_task(p);\n"
    "\tpercpu_up_read(&scx_fork_rwsem);\n"
    "}\n"
)

_SCX_CANCEL_NEW = (
    "void scx_cancel_fork(struct task_struct *p)\n"
    "{\n"
    "\t/* sailboat_sched_ext_payload_task_guard: see scx_fork() */\n"
    "\tif (scx_enabled() && p->scx)\n"
    "\t\tscx_ops_disable_task(p);\n"
    "\tpercpu_up_read(&scx_fork_rwsem);\n"
    "}\n"
)

_FREE_OLD = (
    "void sched_ext_free(struct task_struct *p)\n"
    "{\n"
    "\tunsigned long flags;\n"
    "\n"
    "\tspin_lock_irqsave(&scx_tasks_lock, flags);\n"
    "\tlist_del_init(&p->scx->tasks_node);\n"
    "\tspin_unlock_irqrestore(&scx_tasks_lock, flags);\n"
    "\n"
    "\t/*\n"
    "\t * @p is off scx_tasks and wholly ours. scx_ops_enable()'s PREPPED ->\n"
    "\t * ENABLED transitions can't race us. Disable ops for @p.\n"
    "\t */\n"
    "\tif (p->scx->flags & (SCX_TASK_OPS_PREPPED | SCX_TASK_OPS_ENABLED)) {\n"
    "\t\tstruct rq_flags rf;\n"
    "\t\tstruct rq *rq;\n"
    "\n"
    "\t\trq = task_rq_lock(p, &rf);\n"
    "\t\tscx_ops_disable_task(p);\n"
    "\t\ttask_rq_unlock(rq, p, &rf);\n"
    "\t}\n"
    "}\n"
)

_FREE_NEW = (
    "void sched_ext_free(struct task_struct *p)\n"
    "{\n"
    "\tunsigned long flags;\n"
    "\n"
    "\t/*\n"
    "\t * sailboat_sched_ext_payload_task_guard: the archived payload\n"
    "\t * kmalloc()s this state in scx_pre_fork() and never frees it, and it\n"
    "\t * never initialises the list node either; the fork hooks are what make\n"
    "\t * this function reachable at all, so both are fixed here.  @p is the\n"
    "\t * last reference -- scx_ops_enable()'s iterator holds its own -- so the\n"
    "\t * free cannot race a reader.\n"
    "\t */\n"
    "\tif (!p->scx)\n"
    "\t\treturn;\n"
    "\n"
    "\tspin_lock_irqsave(&scx_tasks_lock, flags);\n"
    "\tlist_del_init(&p->scx->tasks_node);\n"
    "\tspin_unlock_irqrestore(&scx_tasks_lock, flags);\n"
    "\n"
    "\t/*\n"
    "\t * @p is off scx_tasks and wholly ours. scx_ops_enable()'s PREPPED ->\n"
    "\t * ENABLED transitions can't race us. Disable ops for @p.\n"
    "\t */\n"
    "\tif (p->scx->flags & (SCX_TASK_OPS_PREPPED | SCX_TASK_OPS_ENABLED)) {\n"
    "\t\tstruct rq_flags rf;\n"
    "\t\tstruct rq *rq;\n"
    "\n"
    "\t\trq = task_rq_lock(p, &rf);\n"
    "\t\tscx_ops_disable_task(p);\n"
    "\t\ttask_rq_unlock(rq, p, &rf);\n"
    "\t}\n"
    "\n"
    "\tkfree(p->scx);\n"
    "\tp->scx = NULL;\n"
    "}\n"
)


def _sched_ext_payload_task_guard_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (EXT_C, _PRE_FORK_INIT_OLD, _PRE_FORK_INIT_NEW, True),
        (EXT_C, _TASK_ON_SCX_OLD, _TASK_ON_SCX_NEW, True),
        (EXT_C, _SCX_FORK_OLD, _SCX_FORK_NEW, True),
        (EXT_C, _SCX_POST_OLD, _SCX_POST_NEW, True),
        (EXT_C, _SCX_CANCEL_OLD, _SCX_CANCEL_NEW, True),
        (EXT_C, _FREE_OLD, _FREE_NEW, True),
    ])
    return status, detail


def build_groups(PatchGroup):
    """Return the five PatchGroup records, in registration order."""
    return [
        PatchGroup(
            "sched_ext_fork_hooks",
            ("SCX's fork path on kernel/sched/core.c: sched_fork() calls "
             "scx_pre_fork() and consults task_on_scx() for the class, with an "
             "out_cancel unwind because the 5.15 body returns -EAGAIN directly "
             "for a DL priority; sched_cgroup_fork() returns scx_fork()'s "
             "verdict; sched_post_fork() calls scx_post_fork(); and the new "
             "sched_cancel_fork() is the unwind for the window between the two. "
             "include/linux/sched/task.h declares the two changed prototypes. "
             "Every call is a no-op unless a BPF scheduler is loaded."),
            ["no upstream commit: the 6.6 call sites re-anchored on the 5.15 "
             "sched_fork()/sched_cgroup_fork() bodies, which split the same "
             "work differently"],
            [CORE_C, SCHED_TASK_H],
            _sched_ext_fork_hooks_apply,
        ),
        PatchGroup(
            "sched_ext_fork_failure_path",
            ("copy_process()'s unwind for the fork hooks: "
             "perf_event_init_task()'s failure routes through a new "
             "bad_fork_sched_cancel_fork label instead of straight to "
             "bad_fork_cleanup_policy, and sched_cgroup_fork()'s new verdict "
             "routes through bad_fork_cancel_cgroup, which reaches that same "
             "label.  Without it a rejected fork would hold SCX's fork reader "
             "lock forever."),
            ["no upstream commit: the label set is 6.4's, re-anchored on the "
             "5.15 copy_process() label chain"],
            [FORK_C],
            _sched_ext_fork_failure_path_apply,
        ),
        PatchGroup(
            "sched_ext_task_teardown",
            ("__put_task_struct() detaches the task from SCX's task list.  The "
             "5.15 fork-failure paths go through free_task() rather than "
             "put_task_struct(), so every task that reaches this function has "
             "already been linked by sched_post_fork()."),
            ["no upstream commit: 6.6's sched_ext_free() call site"],
            [FORK_C],
            _sched_ext_task_teardown_apply,
        ),
        PatchGroup(
            "sched_ext_setscheduler_hooks",
            ("the two class-selection sites that let a task move in and out of "
             "the BPF class: __setscheduler_prio() keeps ext_sched_class for a "
             "task task_on_scx() accepts, and __sched_setscheduler() consults "
             "scx_check_setscheduler() right after the rq->stop rejection so "
             "ops.prep_enable()'s disallow verdict is final.  "
             "__setscheduler_prio() is the function sched_ext_core_visibility "
             "de-static'ed, so that group probes for its own marker before "
             "running; see the note at the top of this module."),
            ["no upstream commit: 6.6's call sites in the 5.15 bodies, which "
             "carry the vendor rvh traces"],
            [CORE_C],
            _sched_ext_setscheduler_hooks_apply,
        ),
        PatchGroup(
            "sched_ext_payload_task_guard",
            ("the per-task struct sched_ext_entity's failure path and its free, "
             "in the tree copy of the archived kernel/sched/ext.c.  "
             "scx_pre_fork() kmalloc()s that state and, on failure, silently "
             "continues into a task the other four hooks dereference; the "
             "payload also never initialises the scx_tasks node and never frees "
             "the state.  The hooks this batch adds are the first callers, so "
             "the guard lands with them.  files/kernel/sched/ext.c stays "
             "byte-for-byte with its sha256 pin; these edits, like "
             "sched_ext_payload_adapt's, exist only in the materialised tree."),
            ["no upstream commit: a defect in the vendored payload, made "
             "reachable by this batch's call sites"],
            [EXT_C],
            _sched_ext_payload_task_guard_apply,
        ),
    ]
