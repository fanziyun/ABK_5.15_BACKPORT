"""Batch 59 (sched_ext S2b-2b, second half, part 1): the scheduling-core hooks.

Batch 58 landed the task lifecycle: a task can now be handed to the BPF class and
taken out of it again.  What is still missing is everything that happens once a
task *is* on the class -- the pick path, the stall watchdog and the idle
transition -- so an enabled scheduler would enqueue tasks and never see them
selected.  This batch lands those three, plus the payload edit they make
necessary.

Four groups:

* `sched_ext_active_class` -- the archived `kernel/sched/ext.h` walks the class
  table with `class++` between 6.12's `__sched_class_highest` /
  `__sched_class_lowest`.  5.15 hands the table to the linker as the
  `SCHED_DATA` array, whose addresses *ascend* with lower priority (idle, ext,
  fair, rt, dl, stop) and which `kernel/sched/sched.h` walks *down* from
  `sched_class_highest`.  Only the step direction and the two bound names
  change; the skip logic is direction-independent because ext sits between idle
  and fair in both layouts.  This is a marked edit to the materialised copy of
  an archived payload, exactly like `sched_ext_payload_adapt`.

* `sched_ext_pick_path` -- `kernel/sched/core.c`: `__pick_next_task()` bypasses
  its fair-class fast path while a BPF scheduler is loaded, walks
  `for_each_active_class()` and reports the pick through
  `scx_notify_pick_next_task()` (which is what the ops.cpu_release() handshake
  counts on); `put_prev_task_balance()` walks `for_balance_class_range()` so
  `balance_scx()` runs even when the previous task is on a higher class.

* `sched_ext_tick_watchdog` -- `scheduler_tick()` calls `scx_notify_sched_tick()`,
  the only place that notices a BPF scheduler whose ops.tick() stopped checking
  in.

* `sched_ext_idle_hook` -- `kernel/sched/idle.c`: the idle class reports the CPU
  as entering and leaving idle through `scx_update_idle()`.

What is deliberately NOT here: the decision that makes `SCHED_EXT` selectable
(`valid_policy()` / `normal_policy()` in `kernel/sched/sched.h`, plus the
defconfig tier that turns the symbol on) and the `ext` debugfs file.  The class
is still unreachable after this batch, and every hook above is written so that
it is a no-op while no BPF scheduler is loaded: `scx_enabled()` is a static key
and `next_active_class()` skips ext when it is off.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

CORE_C = "kernel/sched/core.c"
IDLE_C = "kernel/sched/idle.c"
EXT_H = "kernel/sched/ext.h"


# ---------------------------------------------------------------------------
# kernel/sched/ext.h: the active-class walk, re-pointed at 5.15's SCHED_DATA
# array (direction and bounds only; see the module docstring).
# ---------------------------------------------------------------------------

_NEXT_ACTIVE_OLD = (
    "static inline const struct sched_class *next_active_class(const struct sched_class *class)\n"
    "{\n"
    "\tclass++;\n"
    "\tif (scx_switched_all() && class == &fair_sched_class)\n"
    "\t\tclass++;\n"
    "\tif (!scx_enabled() && class == &ext_sched_class)\n"
    "\t\tclass++;\n"
    "\treturn class;\n"
    "}\n"
)

_NEXT_ACTIVE_NEW = (
    "/*\n"
    " * sailboat_sched_ext_active_class: 5.15 hands the class table to the linker\n"
    " * as the SCHED_DATA array, addressed in ascending order of *lower* priority\n"
    " * (idle, ext, fair, rt, dl, stop), and kernel/sched/sched.h walks it\n"
    " * downwards from sched_class_highest with class--.  The archived payload is\n"
    " * written for the 6.12 layout, which counts up from __sched_class_highest.\n"
    " * The skip logic is direction-independent -- ext sits between idle and fair\n"
    " * in both -- so only the step direction and the two bound names change.\n"
    " */\n"
    "static inline const struct sched_class *next_active_class(const struct sched_class *class)\n"
    "{\n"
    "\tclass--;\n"
    "\tif (scx_switched_all() && class == &fair_sched_class)\n"
    "\t\tclass--;\n"
    "\tif (!scx_enabled() && class == &ext_sched_class)\n"
    "\t\tclass--;\n"
    "\treturn class;\n"
    "}\n"
)

_ACTIVE_CLASS_RANGE_OLD = (
    "#define for_each_active_class(class)\t\t\t\t\t\t\\\n"
    "\tfor_active_class_range(class, __sched_class_highest, __sched_class_lowest)\n"
)

_ACTIVE_CLASS_RANGE_NEW = (
    "#define for_each_active_class(class)\t\t\t\t\t\t\\\n"
    "\tfor_active_class_range(class, sched_class_highest, sched_class_lowest)\n"
)


def _sched_ext_active_class_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (EXT_H, _NEXT_ACTIVE_OLD, _NEXT_ACTIVE_NEW, True),
        (EXT_H, _ACTIVE_CLASS_RANGE_OLD, _ACTIVE_CLASS_RANGE_NEW, True),
    ])
    return status, detail


# ---------------------------------------------------------------------------
# kernel/sched/core.c: the pick path.
# ---------------------------------------------------------------------------

_BALANCE_WALK_OLD = (
    "\tfor_class_range(class, prev->sched_class, &idle_sched_class) {\n"
    "\t\tif (class->balance(rq, prev, rf))\n"
    "\t\t\tbreak;\n"
    "\t}\n"
)

_BALANCE_WALK_NEW = (
    "\t/* sailboat_sched_ext_pick_path: start one rung below @prev when it is\n"
    "\t * above ext, so that balance_scx() still runs; for_balance_class_range()\n"
    "\t * is for_class_range() with that one floor. */\n"
    "\tfor_balance_class_range(class, prev->sched_class, &idle_sched_class) {\n"
    "\t\tif (class->balance(rq, prev, rf))\n"
    "\t\t\tbreak;\n"
    "\t}\n"
)

_PICK_HEAD_OLD = (
    "__pick_next_task(struct rq *rq, struct task_struct *prev, struct rq_flags *rf)\n"
    "{\n"
    "\tconst struct sched_class *class;\n"
    "\tstruct task_struct *p;\n"
    "\n"
    "\t/*\n"
    "\t * Optimization: we know that if all tasks are in the fair class we can\n"
)

_PICK_HEAD_NEW = (
    "__pick_next_task(struct rq *rq, struct task_struct *prev, struct rq_flags *rf)\n"
    "{\n"
    "\tconst struct sched_class *class;\n"
    "\tstruct task_struct *p;\n"
    "\n"
    "\t/* sailboat_sched_ext_pick_path: the fair-class shortcut below assumes\n"
    "\t * fair is the lowest runnable class, which stops being true once a BPF\n"
    "\t * scheduler owns tasks (and is wrong outright in switched-all mode, where\n"
    "\t * fair must be skipped entirely). */\n"
    "\tif (scx_enabled())\n"
    "\t\tgoto restart;\n"
    "\n"
    "\t/*\n"
    "\t * Optimization: we know that if all tasks are in the fair class we can\n"
)

_PICK_LOOP_OLD = (
    "\tfor_each_class(class) {\n"
    "\t\tp = class->pick_next_task(rq);\n"
    "\t\tif (p)\n"
    "\t\t\treturn p;\n"
    "\t}\n"
    "\n"
    "\t/* The idle class should always have a runnable task: */\n"
    "\tBUG();\n"
)

_PICK_LOOP_NEW = (
    "\tfor_each_active_class(class) {\n"
    "\t\tp = class->pick_next_task(rq);\n"
    "\t\tif (p) {\n"
    "\t\t\t/* sailboat_sched_ext_pick_path: the ops.cpu_release() handshake\n"
    "\t\t\t * counts picks through the rq's pnt_seq here. */\n"
    "\t\t\tscx_notify_pick_next_task(rq, p, class);\n"
    "\t\t\treturn p;\n"
    "\t\t}\n"
    "\t}\n"
    "\n"
    "\t/* The idle class should always have a runnable task: */\n"
    "\tBUG();\n"
)


def _sched_ext_pick_path_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (CORE_C, _BALANCE_WALK_OLD, _BALANCE_WALK_NEW, True),
        (CORE_C, _PICK_HEAD_OLD, _PICK_HEAD_NEW, True),
        (CORE_C, _PICK_LOOP_OLD, _PICK_LOOP_NEW, True),
    ])
    return status, detail


# ---------------------------------------------------------------------------
# kernel/sched/core.c: the stall watchdog.
# ---------------------------------------------------------------------------

_TICK_OLD = (
    "\tif (sched_feat(LATENCY_WARN) && resched_latency)\n"
    "\t\tresched_latency_warn(cpu, resched_latency);\n"
    "\n"
    "\tperf_event_task_tick();\n"
)

_TICK_NEW = (
    "\tif (sched_feat(LATENCY_WARN) && resched_latency)\n"
    "\t\tresched_latency_warn(cpu, resched_latency);\n"
    "\n"
    "\t/* sailboat_sched_ext_tick_watchdog: a BPF scheduler checks in from its\n"
    "\t * ops.tick(); this is the only site that notices one that stopped. */\n"
    "\tscx_notify_sched_tick();\n"
    "\tperf_event_task_tick();\n"
)


def _sched_ext_tick_watchdog_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (CORE_C, _TICK_OLD, _TICK_NEW, True),
    ])
    return status, detail


# ---------------------------------------------------------------------------
# kernel/sched/idle.c: the idle transition.
# ---------------------------------------------------------------------------

# Batch 15's avg_idle_preemption_mode owns this body already: it moved the
# retired ttwu_do_wakeup() idle-duration sample here and left the marker comment
# below.  The SCX call is appended after that call rather than anchored on the
# pristine empty body, which no longer exists by the time this group runs.
_IDLE_PUT_OLD = (
    "static void put_prev_task_idle(struct rq *rq, struct task_struct *prev)\n"
    "{\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: avg_idle preemption mode simplification.\n"
    "\t * This is where the retired ttwu_do_wakeup() idle-duration sample\n"
    "\t * now happens: rq->idle_stamp is still armed from the last\n"
    "\t * newidle_balance(), and it is cleared by the helper.\n"
    "\t */\n"
    "\tupdate_rq_avg_idle(rq);\n"
    "}\n"
)

_IDLE_PUT_NEW = (
    "static void put_prev_task_idle(struct rq *rq, struct task_struct *prev)\n"
    "{\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: avg_idle preemption mode simplification.\n"
    "\t * This is where the retired ttwu_do_wakeup() idle-duration sample\n"
    "\t * now happens: rq->idle_stamp is still armed from the last\n"
    "\t * newidle_balance(), and it is cleared by the helper.\n"
    "\t */\n"
    "\tupdate_rq_avg_idle(rq);\n"
    "\t/* sailboat_sched_ext_idle_hook: ops.update_idle(), rq leaving idle. */\n"
    "\tscx_update_idle(rq, false);\n"
    "}\n"
)

_IDLE_SET_OLD = (
    "static void set_next_task_idle(struct rq *rq, struct task_struct *next, bool first)\n"
    "{\n"
    "\tupdate_idle_core(rq);\n"
    "\tschedstat_inc(rq->sched_goidle);\n"
)

_IDLE_SET_NEW = (
    "static void set_next_task_idle(struct rq *rq, struct task_struct *next, bool first)\n"
    "{\n"
    "\tupdate_idle_core(rq);\n"
    "\t/* sailboat_sched_ext_idle_hook: ops.update_idle(), rq entering idle. */\n"
    "\tscx_update_idle(rq, true);\n"
    "\tschedstat_inc(rq->sched_goidle);\n"
)


def _sched_ext_idle_hook_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (IDLE_C, _IDLE_PUT_OLD, _IDLE_PUT_NEW, True),
        (IDLE_C, _IDLE_SET_OLD, _IDLE_SET_NEW, True),
    ])
    return status, detail


def build_groups(PatchGroup):
    """Return the four PatchGroup records, in registration order."""
    return [
        PatchGroup(
            "sched_ext_active_class",
            ("the active-class walk in the materialised kernel/sched/ext.h: "
             "next_active_class() steps down the SCHED_DATA array instead of up, "
             "and for_each_active_class() is bounded by 5.15's "
             "sched_class_highest/sched_class_lowest.  Without it every consumer "
             "of the macro walks out of the array.  Marked edit to an archived "
             "payload, like sched_ext_payload_adapt's edits to ext.c."),
            ["no upstream commit: the 6.12 class-table layout re-expressed on "
             "the 5.15 SCHED_DATA array"],
            [EXT_H],
            _sched_ext_active_class_apply,
        ),
        PatchGroup(
            "sched_ext_pick_path",
            ("the pick path in kernel/sched/core.c: __pick_next_task() leaves "
             "the fair-class fast path while a BPF scheduler is loaded, walks "
             "for_each_active_class() and reports the pick with "
             "scx_notify_pick_next_task(); put_prev_task_balance() walks "
             "for_balance_class_range() so balance_scx() runs below a "
             "higher-class previous task as well."),
            ["no upstream commit: 6.6's call sites in the 5.15 "
             "__pick_next_task()/put_prev_task_balance() bodies"],
            [CORE_C],
            _sched_ext_pick_path_apply,
        ),
        PatchGroup(
            "sched_ext_tick_watchdog",
            ("scheduler_tick() calls scx_notify_sched_tick(), the watchdog that "
             "fails the BPF scheduler over when its ops.tick() stops checking "
             "in.  It runs after the rq is unlocked, like the upstream site."),
            ["no upstream commit: 6.6's scx_notify_sched_tick() call site"],
            [CORE_C],
            _sched_ext_tick_watchdog_apply,
        ),
        PatchGroup(
            "sched_ext_idle_hook",
            ("kernel/sched/idle.c reports the CPU entering and leaving idle to "
             "the BPF scheduler through scx_update_idle(), from the idle "
             "class's set_next_task() and put_prev_task()."),
            ["no upstream commit: 6.6's scx_update_idle() call sites"],
            [IDLE_C],
            _sched_ext_idle_hook_apply,
        ),
    ]
