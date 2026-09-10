# -*- coding: utf-8 -*-
"""Batch 10-4c: schedutil smart-freq policy, made governor-independent.

The first form of this policy sampled its input in
``android_vh_map_util_freq_new`` -- a hook schedutil calls from
``get_next_freq()``.  On the target device the CPUfreq governor is the vendor
``walt``, so that hook is never reached: the ``abk_sf_*`` parameters were
visible and the module loaded, but no CPU ever became "boosting" and the floor
in ``android_vh_cpufreq_resolve_freq`` never fired.  Inert by construction.

Sampling now happens in ``android_vh_scheduler_tick``, which runs for every
governor.  Critically, ``scheduler_tick()`` calls that hook *after*
``rq_unlock()`` (kernel/sched/core.c: the unlock precedes the tracepoint), so
reading PELT state there is safe and lock-free.  Per-CPU state is sampled per
tick and the floor is aggregated over ``policy->cpus`` at resolve time, which
``drivers/cpufreq/cpufreq.c`` reaches on both the fast and the slow path --
again regardless of governor.

The heuristic itself is unchanged from the original design: a per-CPU
sustained-high-util reason with hysteresis, floor-ed at resolve time, inspired
by Qualcomm WALT smart_freq's SUSTAINED_HIGH_UTIL reason.  It remains a
from-scratch GPL policy layer, not a port: pipeline pinning, per-ms busy
locks, AMU IPC-FMAX and the WALT state machines are deliberately absent.
"""

import re

__all__ = ["build_steps", "T"]


def _tabs(text):
    text = re.sub(r"(?m)^    +",
                  lambda m: "\t" * (len(m.group(0)) // 4), text)
    return text.rstrip("\n") + "\n"


T = True

_INC_OLD = "#include <trace/hooks/sched.h>\n"
_INC_NEW = (
    "#include <trace/hooks/sched.h>\n"
    "#include <trace/hooks/cpufreq.h>\n"
    "#include <linux/math.h>\n"
    "#include <linux/moduleparam.h>\n"
)

_TAIL_OLD = "cpufreq_governor_init(schedutil_gov);\n"

_POLICY = _tabs(r"""
/*
 * ABK stable_515_backport: Batch 10-4 sched smart-freq policy (PELT).
 *
 * Sampling runs in the scheduler tick, not in a schedutil-only vendor hook:
 * the target device runs the vendor walt governor, so the schedutil path
 * (android_vh_map_util_freq_new) is never reached and the reason election
 * stayed inert.  android_vh_scheduler_tick() fires for every governor and
 * runs after rq_unlock() in scheduler_tick(), so reading PELT state there is
 * safe and lock-free.  The resulting floor is applied in
 * android_vh_cpufreq_resolve_freq(), which cpufreq.c calls on both the fast
 * and the slow path, again regardless of governor.
 *
 *   - while a CPU's PELT util stays >= abk_sf_sustained_pct of its capacity
 *     for >= abk_sf_sustained_ms, that CPU is "boosting";
 *   - once boosting, util must stay below abk_sf_exit_pct for
 *     abk_sf_exit_ms before the reason clears (hysteresis);
 *   - while any CPU of a policy is boosting, resolve_freq raises the target
 *     frequency to at least abk_sf_floor_pct of cpuinfo.max_freq, so short
 *     frame gaps do not collapse the frequency right after a sustained-high
 *     window.
 */
#define ABK_SF_SUSTAINED_PCT	90
#define ABK_SF_EXIT_PCT		70

static bool abk_sf_enable = true;
static int abk_sf_sustained_ms = 300;
static int abk_sf_exit_ms = 250;
static int abk_sf_floor_pct = 85;

module_param(abk_sf_enable, bool, 0644);
module_param(abk_sf_sustained_ms, int, 0644);
module_param(abk_sf_exit_ms, int, 0644);
module_param(abk_sf_floor_pct, int, 0644);
MODULE_PARM_DESC(abk_sf_enable,
    "ABK smart-freq policy: sustained-high-util frequency floor");
MODULE_PARM_DESC(abk_sf_sustained_ms,
    "ms of >=90% PELT util before a CPU starts boosting");
MODULE_PARM_DESC(abk_sf_exit_ms,
    "ms of <=70% PELT util before a boosting CPU clears");
MODULE_PARM_DESC(abk_sf_floor_pct,
    "floor frequency as a percentage of cpuinfo.max_freq while boosting");

struct abk_sf_cpu {
    unsigned long boost_start;  /* jiffies when sustained util began */
    unsigned long low_start;    /* jiffies when util dropped below exit */
    bool boosting;
};

static struct abk_sf_cpu *abk_sf_cpus;

static void abk_sf_sample(int cpu, unsigned long util, unsigned long cap)
{
    struct abk_sf_cpu *c;
    unsigned long now = jiffies;
    bool high;

    if (!cap)
        return;

    if (util > cap)
        util = cap;

    c = &abk_sf_cpus[cpu];
    high = util * 100 >= cap * ABK_SF_SUSTAINED_PCT;

    if (high) {
        c->low_start = 0;
        if (!c->boost_start)
            c->boost_start = now;
        else if (!c->boosting &&
                 time_after(now, c->boost_start +
                            msecs_to_jiffies(abk_sf_sustained_ms)))
            c->boosting = true;
    } else if (util * 100 < cap * ABK_SF_EXIT_PCT) {
        if (c->boosting) {
            if (!c->low_start)
                c->low_start = now;
            else if (time_after(now, c->low_start +
                                msecs_to_jiffies(abk_sf_exit_ms)))
                c->boosting = false;
        } else {
            c->boost_start = 0;
        }
    }
}

static void abk_sf_tick(void *data, struct rq *rq)
{
    unsigned long cap;

    if (!abk_sf_enable || !abk_sf_cpus)
        return;

    cap = arch_scale_cpu_capacity(rq->cpu);
    abk_sf_sample(rq->cpu, cpu_util_cfs(rq), cap);
}

static void abk_sf_resolve_freq(void *data, struct cpufreq_policy *policy,
                                unsigned int *target_freq,
                                unsigned int old_target_freq)
{
    unsigned int floor;
    bool boosting = false;
    int cpu;

    if (!abk_sf_enable || !abk_sf_cpus || !target_freq)
        return;

    for_each_cpu(cpu, policy->cpus) {
        if (abk_sf_cpus[cpu].boosting) {
            boosting = true;
            break;
        }
    }
    if (!boosting)
        return;

    floor = mult_frac(policy->cpuinfo.max_freq, abk_sf_floor_pct, 100);
    if (floor < policy->min)
        floor = policy->min;
    if (floor > policy->max)
        floor = policy->max;

    if (*target_freq < floor)
        *target_freq = floor;
}

static int __init abk_sf_init(void)
{
    int ret;

    abk_sf_cpus = kcalloc(num_possible_cpus(), sizeof(*abk_sf_cpus),
                          GFP_KERNEL);
    if (!abk_sf_cpus)
        return -ENOMEM;

    ret = register_trace_android_vh_scheduler_tick(abk_sf_tick, NULL);
    if (ret)
        pr_warn("ABK smart-freq: scheduler_tick hook unavailable (%d)\n",
                ret);

    ret = register_trace_android_vh_cpufreq_resolve_freq(abk_sf_resolve_freq,
                                                         NULL);
    if (ret)
        pr_warn("ABK smart-freq: cpufreq_resolve_freq hook unavailable (%d)\n",
                ret);

    pr_info("ABK stable_515_backport: sched smart-freq policy (PELT, "
        "governor-independent); enable=%d sustained_ms=%d exit_ms=%d "
        "floor_pct=%d\n", abk_sf_enable, abk_sf_sustained_ms,
        abk_sf_exit_ms, abk_sf_floor_pct);
    return 0;
}

late_initcall(abk_sf_init);
""")

_TAIL_NEW = _TAIL_OLD + _POLICY


def build_steps():
    return [
        ("kernel/sched/cpufreq_schedutil.c", _INC_OLD, _INC_NEW, T),
        ("kernel/sched/cpufreq_schedutil.c", _TAIL_OLD, _TAIL_NEW, T),
    ]
