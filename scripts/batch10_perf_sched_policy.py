# -*- coding: utf-8 -*-
"""Batch 10-2: schedutil "smart freq" policy layer on PELT (plan).

The Qualcomm WALT suite (smart_freq/pipeline/voter, popsicle-w-oss) cannot
be transported onto AOSP android13-5.15: the frequency-decision inputs are
WALT window stats, and there is no PELT-based precedent anywhere public
(see research/popsicle_w_oss/walt_pelt_survey.md).  What *is* portable is
the control-layer subset -- a per-cluster reason election with hysteresis
that clamps/boosts the resulting frequency -- re-anchored on signals that
exist under PELT:

  * sustained-high-util detection reuses the PELT util that schedutil
    already feeds into get_next_freq() (android_vh_map_util_freq_new gives
    util/max before the freq mapping);
  * the actual cap/floor is applied where every frequency decision passes
    (android_vh_cpufreq_resolve_freq, the hook the driver calls on both the
    fast and the slow paths).

This is a from-scratch GKI policy layer *inspired by* smart_freq's
SUSTAINED_HIGH_UTIL reason, not a code move: pipeline pinning, per-ms busy
locks, AMU IPC-FMAX and the WALT state machines are deliberately absent.
It is inert by design unless a runtime knob is flipped: /sys/module/
cpufreq_schedutil/parameters/abk_sf_enable defaults to 1 but sustained
>=90%-util for >=300 ms is required before any floor applies, and the floor
only prevents the frequency from collapsing during the short dips that
follow a sustained-high window.
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
 * ABK stable_515_backport: Batch 10-2 sched smart-freq policy (PELT).
 *
 * Per-cluster reason election (inspired by Qualcomm WALT smart_freq's
 * SUSTAINED_HIGH_UTIL) built on the PELT util schedutil already computes:
 *
 *   - while a cluster's aggregate util stays >= abk_sf_sustained_pct of the
 *     capacity for >= abk_sf_sustained_ms, the cluster is "boosting";
 *   - once boosting, util must stay below abk_sf_exit_pct for
 *     abk_sf_exit_ms before the reason clears (hysteresis);
 *   - while boosting, cpufreq_resolve_freq clamps the target frequency to
 *     at least abk_sf_floor_pct of cpuinfo.max_freq, so short frame gaps do
 *     not collapse the frequency right after a sustained-high window.
 *
 * Inert unless /sys/module/cpufreq_schedutil/parameters/abk_sf_enable is 1
 * (default) and the util thresholds are actually crossed.
 */
#define ABK_SF_SUSTAINED_PCT        90
#define ABK_SF_EXIT_PCT             70

static bool abk_sf_enable = true;
static int abk_sf_sustained_ms = 300;
static int abk_sf_exit_ms = 250;
static int abk_sf_floor_pct = 85;

module_param(abk_sf_enable, bool, 0644);
module_param(abk_sf_sustained_ms, int, 0644);
module_param(abk_sf_exit_ms, int, 0644);
module_param(abk_sf_floor_pct, int, 0644);
MODULE_PARM_DESC(abk_sf_enable,
    "ABK smart-freq policy: sustained-high-util cluster floor");
MODULE_PARM_DESC(abk_sf_sustained_ms,
    "ms of >=90% PELT util before a cluster starts boosting");
MODULE_PARM_DESC(abk_sf_exit_ms,
    "ms of <=70% PELT util before a boosting cluster clears");
MODULE_PARM_DESC(abk_sf_floor_pct,
    "floor frequency as a percentage of cpuinfo.max_freq while boosting");

struct abk_sf_cluster {
    struct cpufreq_policy *policy;
    unsigned long boost_start;  /* jiffies when sustained util began */
    unsigned long low_start;    /* jiffies when util dropped below exit */
    bool boosting;
};

static struct abk_sf_cluster *abk_sf_cl;

static struct abk_sf_cluster *abk_sf_of(struct cpufreq_policy *policy)
{
    struct abk_sf_cluster *c;

    if (!abk_sf_cl)
        return NULL;

    c = &abk_sf_cl[policy->cpu];
    if (c->policy != policy)
        c->policy = policy;
    return c;
}

static void abk_sf_map_util(void *data, unsigned long util,
                            unsigned long freq, unsigned long cap,
                            unsigned long *next_freq,
                            struct cpufreq_policy *policy,
                            bool *need_freq_update)
{
    struct abk_sf_cluster *c;
    unsigned long now = jiffies;
    bool high;

    if (!abk_sf_enable || !cap)
        return;

    c = abk_sf_of(policy);
    if (!c)
        return;

    high = util * 100 >= cap * ABK_SF_SUSTAINED_PCT;

    if (high) {
        c->low_start = 0;
        if (!c->boost_start)
            c->boost_start = now;
        else if (!c->boosting &&
                 time_after(now, c->boost_start +
                            msecs_to_jiffies(abk_sf_sustained_ms)))
            c->boosting = true;
    } else {
        if (util * 100 < cap * ABK_SF_EXIT_PCT) {
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

    if (c->boosting && need_freq_update)
        *need_freq_update = true;
}

static void abk_sf_resolve_freq(void *data, struct cpufreq_policy *policy,
                                unsigned int *target_freq,
                                unsigned int old_target_freq)
{
    struct abk_sf_cluster *c;
    unsigned int floor;

    if (!abk_sf_enable || !target_freq)
        return;

    c = abk_sf_of(policy);
    if (!c || !c->boosting)
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
    int ret = 0;

    abk_sf_cl = kcalloc(num_possible_cpus(), sizeof(*abk_sf_cl), GFP_KERNEL);
    if (!abk_sf_cl)
        return -ENOMEM;

    ret = register_trace_android_vh_map_util_freq_new(abk_sf_map_util, NULL);
    if (ret)
        pr_warn("ABK smart-freq: map_util_freq_new hook unavailable (%d)\n",
                ret);

    ret = register_trace_android_vh_cpufreq_resolve_freq(abk_sf_resolve_freq,
                                                         NULL);
    if (ret)
        pr_warn("ABK smart-freq: cpufreq_resolve_freq hook unavailable (%d)\n",
                ret);

    pr_info("ABK stable_515_backport: sched smart-freq policy (PELT) loaded; "
            "enable=%d sustained_ms=%d exit_ms=%d floor_pct=%d\n",
            abk_sf_enable, abk_sf_sustained_ms, abk_sf_exit_ms,
            abk_sf_floor_pct);
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
