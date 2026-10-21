from __future__ import annotations

from abk_backport_engine import apply_steps

NOHZ_H = "include/linux/sched/nohz.h"
TICK_SCHED_C = "kernel/time/tick-sched.c"
CORE_C = "kernel/sched/core.c"
FAIR_C = "kernel/sched/fair.c"
IDLE_C = "kernel/sched/idle.c"
SCHED_H = "kernel/sched/sched.h"

T = True

_MARK_NOHZ = "/* ABK stable_515_backport: nohz tick_sched state field consistency helpers. */"
_MARK_AVG = "/* ABK stable_515_backport: avg_idle preemption mode simplification. */"
_MARK_AVG_PROBE = "ABK stable_515_backport: avg_idle preemption mode simplification."


_A_ENUM = (
    "/*\n"
    " * This is the interface between the scheduler and nohz/dynticks:\n"
    " */\n"
    "\n"
    "#if defined(CONFIG_SMP) && defined(CONFIG_NO_HZ_COMMON)\n"
)

_A_ENUM_NEW = (
    "/*\n"
    " * This is the interface between the scheduler and nohz/dynticks:\n"
    " */\n"
    "\n"
    "/*\n"
    " * ABK stable_515_backport: nohz tick_sched state field consistency\n"
    " * helpers.  The three legacy struct tick_sched bits (inidle, idle_active,\n"
    " * tick_stopped) are read as one named state mask so a reader that needs\n"
    " * two of them snapshots them consistently instead of re-probing the\n"
    " * bitfields one at a time.\n"
    " */\n"
    "enum nohz_cpu_state {\n"
    "\tNOHZ_CPU_STATE_NONE = 0,\n"
    "\tNOHZ_CPU_STATE_INIDLE = 1U << 0,\n"
    "\tNOHZ_CPU_STATE_IDLE_ACTIVE = 1U << 1,\n"
    "\tNOHZ_CPU_STATE_TICK_STOPPED = 1U << 2,\n"
    "};\n"
    "\n"
    "#if defined(CONFIG_SMP) && defined(CONFIG_NO_HZ_COMMON)\n"
)


_A_TICK_STOPPED = (
    "__setup(\"nohz=\", setup_tick_nohz);\n"
    "\n"
    "bool tick_nohz_tick_stopped(void)\n"
    "{\n"
    "\tstruct tick_sched *ts = this_cpu_ptr(&tick_cpu_sched);\n"
    "\n"
    "\treturn ts->tick_stopped;\n"
    "}\n"
)

_A_TICK_STOPPED_NEW = (
    "__setup(\"nohz=\", setup_tick_nohz);\n"
    "\n"
    "/* ABK stable_515_backport: nohz tick_sched state field consistency helpers. */\n"
    "static unsigned int abk_tick_nohz_state_flags(const struct tick_sched *ts)\n"
    "{\n"
    "\tunsigned int state = NOHZ_CPU_STATE_NONE;\n"
    "\n"
    "\tif (ts->inidle)\n"
    "\t\tstate |= NOHZ_CPU_STATE_INIDLE;\n"
    "\tif (ts->idle_active)\n"
    "\t\tstate |= NOHZ_CPU_STATE_IDLE_ACTIVE;\n"
    "\tif (ts->tick_stopped)\n"
    "\t\tstate |= NOHZ_CPU_STATE_TICK_STOPPED;\n"
    "\n"
    "\treturn state;\n"
    "}\n"
    "\n"
    "static unsigned long nohz_cpu_idle_calls(int cpu)\n"
    "{\n"
    "\treturn tick_get_tick_sched(cpu)->idle_calls;\n"
    "}\n"
    "\n"
    "bool tick_nohz_tick_stopped(void)\n"
    "{\n"
    "\tstruct tick_sched *ts = this_cpu_ptr(&tick_cpu_sched);\n"
    "\n"
    "\treturn !!(abk_tick_nohz_state_flags(ts) & NOHZ_CPU_STATE_TICK_STOPPED);\n"
    "}\n"
)

_A_CPU_STOPPED = (
    "bool tick_nohz_tick_stopped_cpu(int cpu)\n"
    "{\n"
    "\tstruct tick_sched *ts = per_cpu_ptr(&tick_cpu_sched, cpu);\n"
    "\n"
    "\treturn ts->tick_stopped;\n"
    "}\n"
)

_A_CPU_STOPPED_NEW = (
    "bool tick_nohz_tick_stopped_cpu(int cpu)\n"
    "{\n"
    "\tstruct tick_sched *ts = per_cpu_ptr(&tick_cpu_sched, cpu);\n"
    "\n"
    "\t/* ABK stable_515_backport: CPU-scoped form of the nohz state probe. */\n"
    "\treturn !!(abk_tick_nohz_state_flags(ts) & NOHZ_CPU_STATE_TICK_STOPPED);\n"
    "}\n"
)

_A_IRQ_EXIT = (
    "void tick_nohz_irq_exit(void)\n"
    "{\n"
    "\tstruct tick_sched *ts = this_cpu_ptr(&tick_cpu_sched);\n"
    "\n"
    "\tif (ts->inidle)\n"
    "\t\ttick_nohz_start_idle(ts);\n"
    "\telse\n"
    "\t\ttick_nohz_full_update_tick(ts);\n"
    "}\n"
)

_A_IRQ_EXIT_NEW = (
    "void tick_nohz_irq_exit(void)\n"
    "{\n"
    "\tstruct tick_sched *ts = this_cpu_ptr(&tick_cpu_sched);\n"
    "\n"
    "\tif (abk_tick_nohz_state_flags(ts) & NOHZ_CPU_STATE_INIDLE)\n"
    "\t\ttick_nohz_start_idle(ts);\n"
    "\telse\n"
    "\t\ttick_nohz_full_update_tick(ts);\n"
    "}\n"
)

_A_SLEEP_WARN = (
    "\tWARN_ON_ONCE(!ts->inidle);\n"
    "\n"
    "\t*delta_next = ktime_sub(dev->next_event, now);\n"
)

_A_SLEEP_WARN_NEW = (
    "\tWARN_ON_ONCE(!(abk_tick_nohz_state_flags(ts) & NOHZ_CPU_STATE_INIDLE));\n"
    "\n"
    "\t*delta_next = ktime_sub(dev->next_event, now);\n"
)

_A_IDLE_CALLS_CPU = (
    "unsigned long tick_nohz_get_idle_calls_cpu(int cpu)\n"
    "{\n"
    "\tstruct tick_sched *ts = tick_get_tick_sched(cpu);\n"
    "\n"
    "\treturn ts->idle_calls;\n"
    "}\n"
)

_A_IDLE_CALLS_CPU_NEW = (
    "unsigned long tick_nohz_get_idle_calls_cpu(int cpu)\n"
    "{\n"
    "\treturn nohz_cpu_idle_calls(cpu);\n"
    "}\n"
)

_A_IDLE_CALLS = (
    "unsigned long tick_nohz_get_idle_calls(void)\n"
    "{\n"
    "\tstruct tick_sched *ts = this_cpu_ptr(&tick_cpu_sched);\n"
    "\n"
    "\treturn ts->idle_calls;\n"
    "}\n"
)

_A_IDLE_CALLS_NEW = (
    "unsigned long tick_nohz_get_idle_calls(void)\n"
    "{\n"
    "\treturn nohz_cpu_idle_calls(smp_processor_id());\n"
    "}\n"
)

_A_IDLE_EXIT = (
    "void tick_nohz_idle_exit(void)\n"
    "{\n"
    "\tstruct tick_sched *ts = this_cpu_ptr(&tick_cpu_sched);\n"
    "\tbool idle_active, tick_stopped;\n"
    "\tktime_t now;\n"
    "\n"
    "\tlocal_irq_disable();\n"
    "\n"
    "\tWARN_ON_ONCE(!ts->inidle);\n"
    "\tWARN_ON_ONCE(ts->timer_expires_base);\n"
    "\n"
    "\tts->inidle = 0;\n"
    "\tidle_active = ts->idle_active;\n"
    "\ttick_stopped = ts->tick_stopped;\n"
    "\n"
    "\tif (idle_active || tick_stopped)\n"
    "\t\tnow = ktime_get();\n"
    "\n"
    "\tif (idle_active)\n"
    "\t\ttick_nohz_stop_idle(ts, now);\n"
    "\n"
    "\tif (tick_stopped)\n"
    "\t\ttick_nohz_idle_update_tick(ts, now);\n"
    "\n"
    "\tlocal_irq_enable();\n"
    "}\n"
)

_A_IDLE_EXIT_NEW = (
    "void tick_nohz_idle_exit(void)\n"
    "{\n"
    "\tstruct tick_sched *ts = this_cpu_ptr(&tick_cpu_sched);\n"
    "\tbool idle_active, tick_stopped;\n"
    "\tunsigned int nohz_state;\n"
    "\tktime_t now;\n"
    "\n"
    "\tlocal_irq_disable();\n"
    "\n"
    "\tnohz_state = abk_tick_nohz_state_flags(ts);\n"
    "\tWARN_ON_ONCE(!(nohz_state & NOHZ_CPU_STATE_INIDLE));\n"
    "\tWARN_ON_ONCE(ts->timer_expires_base);\n"
    "\n"
    "\tts->inidle = 0;\n"
    "\tidle_active = !!(nohz_state & NOHZ_CPU_STATE_IDLE_ACTIVE);\n"
    "\ttick_stopped = !!(nohz_state & NOHZ_CPU_STATE_TICK_STOPPED);\n"
    "\n"
    "\tif (idle_active || tick_stopped)\n"
    "\t\tnow = ktime_get();\n"
    "\n"
    "\tif (idle_active)\n"
    "\t\ttick_nohz_stop_idle(ts, now);\n"
    "\n"
    "\tif (tick_stopped)\n"
    "\t\ttick_nohz_idle_update_tick(ts, now);\n"
    "\n"
    "\tlocal_irq_enable();\n"
    "}\n"
)


_B_HELPER = (
    "/*\n"
    " * Mark the task runnable and perform wakeup-preemption.\n"
    " */\n"
    "static void ttwu_do_wakeup(struct rq *rq, struct task_struct *p, int wake_flags,\n"
)

_B_HELPER_NEW = (
    "#ifdef CONFIG_SMP\n"
    "/*\n"
    " * ABK stable_515_backport: avg_idle preemption mode simplification.\n"
    " *\n"
    " * The wake-side idle prediction (rq->wake_avg_idle / rq->wake_stamp) is\n"
    " * retired, so the idle-duration sample moves to the single point where the\n"
    " * idle task stops being current.  put_prev_task_idle() is reached from\n"
    " * __pick_next_task()'s restart path with prev == idle, and\n"
    " * newidle_balance() does not run again on that path, so rq->idle_stamp\n"
    " * still describes the whole idle period when this executes.\n"
    " *\n"
    " * The guard is load-bearing: idle_stamp is only armed by\n"
    " * newidle_balance(), and a CPU that reached the idle task without passing\n"
    " * through it (the boot CPU, or an idle->idle repick) would otherwise\n"
    " * sample rq_clock(rq) - 0 here.\n"
    " */\n"
    "void update_rq_avg_idle(struct rq *rq)\n"
    "{\n"
    "\tu64 delta, max;\n"
    "\n"
    "\tif (!rq->idle_stamp)\n"
    "\t\treturn;\n"
    "\n"
    "\tdelta = rq_clock(rq) - rq->idle_stamp;\n"
    "\tmax = 2 * rq->max_idle_balance_cost;\n"
    "\n"
    "\tupdate_avg(&rq->avg_idle, delta);\n"
    "\n"
    "\tif (rq->avg_idle > max)\n"
    "\t\trq->avg_idle = max;\n"
    "\trq->idle_stamp = 0;\n"
    "}\n"
    "#endif /* CONFIG_SMP */\n"
    "\n"
    "/*\n"
    " * Mark the task runnable and perform wakeup-preemption.\n"
    " */\n"
    "static void ttwu_do_wakeup(struct rq *rq, struct task_struct *p, int wake_flags,\n"
)

_B_TTWU = (
    "\t\trq_repin_lock(rq, rf);\n"
    "\t}\n"
    "\n"
    "\tif (rq->idle_stamp) {\n"
    "\t\tu64 delta = rq_clock(rq) - rq->idle_stamp;\n"
    "\t\tu64 max = 2*rq->max_idle_balance_cost;\n"
    "\n"
    "\t\tupdate_avg(&rq->avg_idle, delta);\n"
    "\n"
    "\t\tif (rq->avg_idle > max)\n"
    "\t\t\trq->avg_idle = max;\n"
    "\n"
    "\t\trq->wake_stamp = jiffies;\n"
    "\t\trq->wake_avg_idle = rq->avg_idle / 2;\n"
    "\n"
    "\t\trq->idle_stamp = 0;\n"
    "\t}\n"
    "#endif\n"
    "}\n"
    "\n"
    "static void\n"
    "ttwu_do_activate(struct rq *rq, struct task_struct *p, int wake_flags,\n"
)

_B_TTWU_NEW = (
    "\t\trq_repin_lock(rq, rf);\n"
    "\t}\n"
    "#endif\n"
    "}\n"
    "\n"
    "static void\n"
    "ttwu_do_activate(struct rq *rq, struct task_struct *p, int wake_flags,\n"
)

_B_INIT = (
    "\t\trq->idle_stamp = 0;\n"
    "\t\trq->avg_idle = 2*sysctl_sched_migration_cost;\n"
    "\t\trq->wake_stamp = jiffies;\n"
    "\t\trq->wake_avg_idle = rq->avg_idle;\n"
    "\t\trq->max_idle_balance_cost = sysctl_sched_migration_cost;\n"
)

_B_INIT_NEW = (
    "\t\trq->idle_stamp = 0;\n"
    "\t\trq->avg_idle = 2*sysctl_sched_migration_cost;\n"
    "\t\trq->max_idle_balance_cost = sysctl_sched_migration_cost;\n"
)

_B_SELECT_HEAD = (
    "static int select_idle_cpu(struct task_struct *p, struct sched_domain *sd, bool has_idle_core, int target)\n"
    "{\n"
    "\tstruct cpumask *cpus = this_cpu_cpumask_var_ptr(select_idle_mask);\n"
    "\tint i, cpu, idle_cpu = -1, nr = INT_MAX;\n"
    "\tstruct rq *this_rq = this_rq();\n"
    "\tint this = smp_processor_id();\n"
    "\tstruct sched_domain *this_sd;\n"
    "\tu64 time = 0;\n"
    "\n"
    "\tthis_sd = rcu_dereference(*this_cpu_ptr(&sd_llc));\n"
    "\tif (!this_sd)\n"
    "\t\treturn -1;\n"
    "\n"
    "\tcpumask_and(cpus, sched_domain_span(sd), p->cpus_ptr);\n"
    "\n"
    "\tif (sched_feat(SIS_PROP) && !has_idle_core) {\n"
    "\t\tu64 avg_cost, avg_idle, span_avg;\n"
    "\t\tunsigned long now = jiffies;\n"
    "\n"
    "\t\t/*\n"
    "\t\t * If we're busy, the assumption that the last idle period\n"
    "\t\t * predicts the future is flawed; age away the remaining\n"
    "\t\t * predicted idle time.\n"
    "\t\t */\n"
    "\t\tif (unlikely(this_rq->wake_stamp < now)) {\n"
    "\t\t\twhile (this_rq->wake_stamp < now && this_rq->wake_avg_idle) {\n"
    "\t\t\t\tthis_rq->wake_stamp++;\n"
    "\t\t\t\tthis_rq->wake_avg_idle >>= 1;\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\n"
    "\t\tavg_idle = this_rq->wake_avg_idle;\n"
    "\t\tavg_cost = this_sd->avg_scan_cost + 1;\n"
    "\n"
    "\t\tspan_avg = sd->span_weight * avg_idle;\n"
    "\t\tif (span_avg > 4*avg_cost)\n"
    "\t\t\tnr = div_u64(span_avg, avg_cost);\n"
    "\t\telse\n"
    "\t\t\tnr = 4;\n"
    "\n"
    "\t\ttime = cpu_clock(this);\n"
    "\t}\n"
)

_B_SELECT_HEAD_NEW = (
    "static int select_idle_cpu(struct task_struct *p, struct sched_domain *sd, bool has_idle_core, int target)\n"
    "{\n"
    "\tstruct cpumask *cpus = this_cpu_cpumask_var_ptr(select_idle_mask);\n"
    "\tint i, cpu, idle_cpu = -1, nr = INT_MAX;\n"
    "\tstruct rq *this_rq = this_rq();\n"
    "\tint this = smp_processor_id();\n"
    "\tstruct sched_domain *this_sd;\n"
    "\tu64 time = 0;\n"
    "\n"
    "\tthis_sd = rcu_dereference(*this_cpu_ptr(&sd_llc));\n"
    "\tif (!this_sd)\n"
    "\t\treturn -1;\n"
    "\n"
    "\tcpumask_and(cpus, sched_domain_span(sd), p->cpus_ptr);\n"
    "\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: avg_idle preemption mode simplification.\n"
    "\t * This tree has no SIS_UTIL and no sd_llc_shared->nr_idle_scan, so the\n"
    "\t * SIS_PROP scan budget is kept and re-sourced from the rq's own\n"
    "\t * avg_idle sample instead of the retired rq->wake_avg_idle prediction.\n"
    "\t * There is no aging term left: rq->avg_idle is only refreshed when the\n"
    "\t * CPU actually leaves idle, so a long-busy CPU budgets a scan from its\n"
    "\t * last idle period.\n"
    "\t */\n"
    "\tif (sched_feat(SIS_PROP) && !has_idle_core) {\n"
    "\t\tu64 avg_cost, avg_idle, span_avg;\n"
    "\n"
    "\t\tavg_idle = this_rq->avg_idle / 2;\n"
    "\t\tavg_cost = this_sd->avg_scan_cost + 1;\n"
    "\n"
    "\t\tspan_avg = sd->span_weight * avg_idle;\n"
    "\t\tif (span_avg > 4*avg_cost)\n"
    "\t\t\tnr = div_u64(span_avg, avg_cost);\n"
    "\t\telse\n"
    "\t\t\tnr = 4;\n"
    "\n"
    "\t\ttime = cpu_clock(this);\n"
    "\t}\n"
)

_B_SELECT_TAIL = (
    "\tif (sched_feat(SIS_PROP) && !has_idle_core) {\n"
    "\t\ttime = cpu_clock(this) - time;\n"
    "\n"
    "\t\t/*\n"
    "\t\t * Account for the scan cost of wakeups against the average\n"
    "\t\t * idle time.\n"
    "\t\t */\n"
    "\t\tthis_rq->wake_avg_idle -= min(this_rq->wake_avg_idle, time);\n"
    "\n"
    "\t\tupdate_avg(&this_sd->avg_scan_cost, time);\n"
    "\t}\n"
)

_B_SELECT_TAIL_NEW = (
    "\tif (sched_feat(SIS_PROP) && !has_idle_core) {\n"
    "\t\ttime = cpu_clock(this) - time;\n"
    "\n"
    "\t\t/*\n"
    "\t\t * ABK stable_515_backport: avg_idle preemption mode\n"
    "\t\t * simplification -- only sd->avg_scan_cost is accounted, since\n"
    "\t\t * rq->wake_avg_idle is no longer maintained on this tree.\n"
    "\t\t */\n"
    "\t\tupdate_avg(&this_sd->avg_scan_cost, time);\n"
    "\t}\n"
)

_B_IDLE_PUT_PREV = (
    "static void put_prev_task_idle(struct rq *rq, struct task_struct *prev)\n"
    "{\n"
    "}\n"
)

_B_IDLE_PUT_PREV_NEW = (
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

_B_SCHED_H = (
    "extern void cfs_bandwidth_usage_inc(void);\n"
    "extern void cfs_bandwidth_usage_dec(void);\n"
)

_B_SCHED_H_NEW = (
    "extern void cfs_bandwidth_usage_inc(void);\n"
    "extern void cfs_bandwidth_usage_dec(void);\n"
    "\n"
    "#ifdef CONFIG_SMP\n"
    "/* ABK stable_515_backport: avg_idle preemption mode simplification. */\n"
    "extern void update_rq_avg_idle(struct rq *rq);\n"
    "#else\n"
    "static inline void update_rq_avg_idle(struct rq *rq) { }\n"
    "#endif /* CONFIG_SMP */\n"
)


def build_nohz_steps():
    return [
        (NOHZ_H, _A_ENUM, _A_ENUM_NEW, T),
        (TICK_SCHED_C, _A_TICK_STOPPED, _A_TICK_STOPPED_NEW, T),
        (TICK_SCHED_C, _A_CPU_STOPPED, _A_CPU_STOPPED_NEW, T),
        (TICK_SCHED_C, _A_IRQ_EXIT, _A_IRQ_EXIT_NEW, T),
        (TICK_SCHED_C, _A_SLEEP_WARN, _A_SLEEP_WARN_NEW, T),
        (TICK_SCHED_C, _A_IDLE_CALLS_CPU, _A_IDLE_CALLS_CPU_NEW, T),
        (TICK_SCHED_C, _A_IDLE_CALLS, _A_IDLE_CALLS_NEW, T),
        (TICK_SCHED_C, _A_IDLE_EXIT, _A_IDLE_EXIT_NEW, T),
    ]


def build_avg_idle_steps():
    return [
        (CORE_C, _B_HELPER, _B_HELPER_NEW, T),
        (CORE_C, _B_TTWU, _B_TTWU_NEW, T),
        (CORE_C, _B_INIT, _B_INIT_NEW, T),
        (FAIR_C, _B_SELECT_HEAD, _B_SELECT_HEAD_NEW, T),
        (FAIR_C, _B_SELECT_TAIL, _B_SELECT_TAIL_NEW, T),
        (IDLE_C, _B_IDLE_PUT_PREV, _B_IDLE_PUT_PREV_NEW, T),
        (SCHED_H, _B_SCHED_H, _B_SCHED_H_NEW, T),
    ]


def _nohz_apply(ctx):
    try:
        tick_text = ctx.read(TICK_SCHED_C)
        nohz_text = ctx.read(NOHZ_H)
    except FileNotFoundError as exc:
        return "blocked_by_shape", f"{exc}: file absent"
    for probe, text, why in (
        ("#endif /* _LINUX_SCHED_NOHZ_H */", nohz_text, NOHZ_H),
        ("__setup(\"nohz=\", setup_tick_nohz);", tick_text, TICK_SCHED_C),
        ("bool tick_nohz_tick_stopped(void)", tick_text, TICK_SCHED_C),
    ):
        if probe not in text:
            return "blocked_by_shape", (
                f"{probe!r} not found in {why}; this does not look like the "
                "5.15 nohz header / tick-sched.c pair")
    if "enum nohz_cpu_state" in nohz_text and _MARK_NOHZ not in tick_text:
        return "blocked_by_shape", (
            "nohz.h already declares enum nohz_cpu_state without the ABK "
            "marker in tick-sched.c: the tree carries an unrecognised nohz "
            "state-helper owner; nothing was written")
    status, _results, detail = apply_steps(ctx, build_nohz_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _avg_idle_apply(ctx):
    try:
        core_text = ctx.read(CORE_C)
        fair_text = ctx.read(FAIR_C)
        idle_text = ctx.read(IDLE_C)
        sched_h_text = ctx.read(SCHED_H)
    except FileNotFoundError as exc:
        return "blocked_by_shape", f"{exc}: file absent"
    if "sched_feat(SIS_UTIL)" in fair_text:
        return "blocked_by_shape", (
            "select_idle_cpu() carries SIS_UTIL (6.1+ shape), not the 5.15 "
            "SIS_PROP-only scan; this group's fair.c replacements are keyed to "
            "the 5.15 shape, so nothing was written")
    if "this_rq->wake_avg_idle" not in fair_text and _MARK_AVG_PROBE not in fair_text:
        return "blocked_by_shape", (
            "select_idle_cpu() has no rq->wake_avg_idle prediction and no ABK "
            "avg_idle marker: the tree carries an unrecognised avg_idle shape; "
            "nothing was written")
    for probe, text, why in (
        ("static void ttwu_do_wakeup(struct rq *rq", core_text, CORE_C),
        ("static int select_idle_cpu(struct task_struct *p", fair_text, FAIR_C),
        ("static void put_prev_task_idle(struct rq *rq", idle_text, IDLE_C),
        ("extern void cfs_bandwidth_usage_dec(void);", sched_h_text, SCHED_H),
    ):
        if probe not in text:
            return "blocked_by_shape", f"{probe} not found in {why}"
    status, _results, detail = apply_steps(ctx, build_avg_idle_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    return [
        PatchGroup(
            "nohz_field_refinement",
            "normalise the legacy tick_sched nohz state bits (inidle / "
            "idle_active / tick_stopped) behind an enum plus accessors, and "
            "move every read site in kernel/time/tick-sched.c onto one state "
            "snapshot (ABK_ABI_PATCH_SUITE nohz_field_refinement, re-anchored "
            "to 5.15; the suite's CPU-scoped tick_stopped rewrite, which its "
            "own sentinel guard silently skipped, is included here)",
            [
                "ABK_ABI_PATCH_SUITE scripts/abk_feature_porting.py:"
                "patch_nohz_field_refinement() (group nohz_field_refinement)",
                "no upstream commit: the helper names occur 0 times in "
                "common-5.15-2024-11 and in common-6.1-src",
            ],
            [NOHZ_H, TICK_SCHED_C],
            _nohz_apply,
        ),
        PatchGroup(
            "avg_idle_preemption_mode",
            "retire the rq->wake_avg_idle wake-side idle prediction: sample "
            "avg_idle at the switch-out-of-idle point instead, keep the direct "
            "avg_idle newidle thresholds, and re-source the SIS_PROP idle-scan "
            "budget from rq->avg_idle (ABK_ABI_PATCH_SUITE "
            "avg_idle_preemption_mode, 5.15 re-anchored; the suite's SIS_UTIL "
            "branch is refused here because 5.15 has no SIS_UTIL, and its "
            "unguarded idle_stamp sample is replaced by the pristine guard)",
            [
                "ABK_ABI_PATCH_SUITE scripts/abk_feature_porting.py:"
                "patch_avg_idle_preemption_mode() (group "
                "avg_idle_preemption_mode)",
                "no upstream commit: update_rq_avg_idle() occurs 0 times in "
                "common-5.15-2024-11 and in common-6.1-src, whose "
                "put_prev_task_idle() is still empty",
            ],
            [CORE_C, FAIR_C, IDLE_C, SCHED_H],
            _avg_idle_apply,
        ),
    ]
