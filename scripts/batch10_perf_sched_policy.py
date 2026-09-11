# -*- coding: utf-8 -*-
"""Batch 10-4c/10-5: schedutil smart-freq policy, gated to the schedutil owner.

The first form of this policy sampled its input in
``android_vh_map_util_freq_new`` -- a hook schedutil calls from
``get_next_freq()``.  On a device whose only governor is the vendor ``walt``
one, that hook is never reached, so the policy was inert.  Batch 10-4c moved
sampling to ``android_vh_scheduler_tick`` (every governor) and applied the
floor in ``android_vh_cpufreq_resolve_freq`` (both cpufreq paths), which made
the policy *live* -- and that turned out to be the wrong fix.

Measured on a kalama (SM8550) device running that vendor governor with FAS
driven by a userspace scheduler profile: with the policy enabled, every cpufreq
policy froze at its current ceiling.  ``waltgov_next_freq`` showed the governor
computing 766 MHz from a cluster at 22% demand while the policy sat at
1785600 for a whole game session.  The reason is structural: a FAS-style owner
requests ``min == max == its own target``, so ``floor`` -- computed from
``cpuinfo.max_freq`` and then clamped to ``policy->max`` -- degenerates to "the
frequency applied a moment ago".  The hook then cancels every downscale, and
the cluster ratchets up to the ceiling and stays there.  Two further defects
made the latch nearly permanent: ``boost_start`` only reset below the *exit*
threshold (so the 70-90% dead band banked boost time, turning "sustained for
300 ms" into "high once, more than 300 ms ago"), and release was evaluated only
in the tick (an idle CPU stops ticking under NO_HZ_IDLE, freezing the flag).

The current form therefore (a) defaults to disabled so a vendor FAS/WALT device
keeps exclusive ownership of DVFS, (b) refuses to act on any policy whose
governor is not ``schedutil`` or whose range has collapsed to a single
operating point, (c) only applies a floor that has real headroom under
``policy->max``, (d) slides the sustained window properly, and (e) expires the
reason at resolve time as well as in the tick.  ``abk_sf_boosting`` exposes the
reason state read-only so the next diagnosis does not have to infer it from
frequencies.

``_POLICY_V1`` is kept verbatim: a tree already grafted with it is upgraded in
place (see ``build_upgrade_steps``) rather than having a second copy appended,
which would fail the compile with duplicate definitions.  The *other* legacy
shape -- Batch 10-2, whose payload hung off ``android_vh_map_util_freq_new`` --
matches no anchor at all while still leaving ``_TAIL_OLD`` intact, so inserting
there would duplicate the definitions the same way.  Nothing in this module can
upgrade a shape it cannot name, so that shape is detected and refused
(:func:`has_unknown_policy`) instead of being reported as a clean apply.
"""

import hashlib
import re

__all__ = [
    "build_steps", "build_upgrade_steps", "has_current_policy",
    "has_legacy_policy", "has_unknown_policy", "needs_legacy_upgrade",
    "policy_sha256", "T",
]


def _tabs(text):
    text = re.sub(r"(?m)^    +",
                  lambda m: "\t" * (len(m.group(0)) // 4), text)
    return text.rstrip("\n") + "\n"


T = True

_INC_OLD = "#include <trace/hooks/sched.h>\n"
# The include block Batch 10-4c left behind.  It is its own anchor so a v1 tree
# gains only the missing header: re-inserting the whole block right after the
# first include line would duplicate the three lines that are already there.
_INC_V1 = (
    "#include <trace/hooks/sched.h>\n"
    "#include <trace/hooks/cpufreq.h>\n"
    "#include <linux/math.h>\n"
    "#include <linux/moduleparam.h>\n"
)
_INC_NEW = _INC_V1 + "#include <linux/string.h>\n"

_TAIL_OLD = "cpufreq_governor_init(schedutil_gov);\n"
_REL = "kernel/sched/cpufreq_schedutil.c"

# --- payloads ---------------------------------------------------------------
# v1: the shape shipped by Batch 10-4c.  Byte-frozen, because it is the
# migration anchor for any tree already carrying it.  Do not edit: changing it
# silently strands those trees (the upgrade anchor stops matching and the
# plain insert would then define the policy twice).
_POLICY_V1 = _tabs(r"""
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

_POLICY_V2 = _tabs(r"""
/*
 * ABK stable_515_backport: Batch 10-4 sched smart-freq policy (PELT).
 *
 * Sampling runs in the scheduler tick, not in a schedutil-only vendor hook:
 * android_vh_scheduler_tick() fires for every governor and runs after
 * rq_unlock() in scheduler_tick(), so reading PELT state there is safe and
 * lock-free.  The resulting floor is applied in
 * android_vh_cpufreq_resolve_freq(), which cpufreq.c calls on both the fast
 * and the slow path.
 *
 * Off by default, and gated at apply time by abk_sf_dvfs_owned().  Measured on
 * a kalama device whose only governor is the vendor walt one running FAS, the
 * first shipped form of this policy degenerated into a ratchet: waltgov
 * computed 766 MHz from the cluster's demand while the policy sat at 1785600
 * for a whole game session.  A FAS-style owner drives cpufreq by requesting
 * min == max == its own target, so a floor clamped up to policy->max merely
 * re-states the frequency already applied, and every downscale gets swallowed.
 * A WALT/FAS device already has this feature (that is what smart_freq is); the
 * layer below is for plain GKI schedutil.
 *
 *   - while a CPU's PELT util stays >= abk_sf_sustained_pct of its capacity
 *     for >= abk_sf_sustained_ms, that CPU is "boosting";
 *   - the sustained window slides: one sample below the entry threshold
 *     restarts it, so a spiky workload cannot bank boost time across lulls;
 *   - once boosting, the reason is held for abk_sf_exit_ms after the last
 *     sample busy enough to re-arm it, and that hold-off is also honoured at
 *     resolve time: an idle CPU stops ticking under NO_HZ_IDLE and would
 *     otherwise latch its flag for exactly as long as it stays idle;
 *   - while any CPU of a policy is boosting, resolve_freq raises the target to
 *     at least abk_sf_floor_pct of cpuinfo.max_freq, but only while that floor
 *     falls strictly inside the range the current owner left open.
 */
#define ABK_SF_SUSTAINED_PCT	90
#define ABK_SF_EXIT_PCT		70

static bool abk_sf_enable = false;
static int abk_sf_sustained_ms = 300;
static int abk_sf_exit_ms = 250;
static int abk_sf_floor_pct = 85;

module_param(abk_sf_enable, bool, 0644);
module_param(abk_sf_sustained_ms, int, 0644);
module_param(abk_sf_exit_ms, int, 0644);
module_param(abk_sf_floor_pct, int, 0644);
MODULE_PARM_DESC(abk_sf_enable,
    "ABK smart-freq policy: sustained-high-util frequency floor (off by "
    "default; a vendor FAS/WALT governor owns DVFS, see abk_sf_dvfs_owned)");
MODULE_PARM_DESC(abk_sf_sustained_ms,
    "ms of >=90% PELT util before a CPU starts boosting");
MODULE_PARM_DESC(abk_sf_exit_ms,
    "ms of <=70% PELT util before a boosting CPU clears");
MODULE_PARM_DESC(abk_sf_floor_pct,
    "floor frequency as a percentage of cpuinfo.max_freq while boosting");

struct abk_sf_cpu {
    unsigned long boost_start;    /* first sample of the current window */
    unsigned long boost_release;  /* hold-off deadline once boosting */
    bool boosting;
};

static struct abk_sf_cpu *abk_sf_cpus;

static void abk_sf_clear(struct abk_sf_cpu *c)
{
    c->boosting = false;
    c->boost_start = 0;
    c->boost_release = 0;
}

static void abk_sf_sample(int cpu, unsigned long util, unsigned long cap)
{
    struct abk_sf_cpu *c;
    unsigned long now = jiffies;

    if (!cap)
        return;

    if (util > cap)
        util = cap;

    c = &abk_sf_cpus[cpu];

    if (c->boosting) {
        if (util * 100 >= cap * ABK_SF_EXIT_PCT)
            c->boost_release = now + msecs_to_jiffies(abk_sf_exit_ms);
        else if (time_after(now, c->boost_release))
            abk_sf_clear(c);
        return;
    }

    /* A true sliding window: any sample below entry resets the timer, rather
     * than the old dead band (70-90%) that left boost_start accumulating. */
    if (util * 100 < cap * ABK_SF_SUSTAINED_PCT) {
        c->boost_start = 0;
        return;
    }
    if (!c->boost_start) {
        c->boost_start = now;
        return;
    }
    if (time_after(now,
                   c->boost_start + msecs_to_jiffies(abk_sf_sustained_ms))) {
        c->boosting = true;
        c->boost_release = now + msecs_to_jiffies(abk_sf_exit_ms);
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

/*
 * The tick is not the only place the hold-off can expire, because a CPU that
 * goes idle stops ticking under NO_HZ_IDLE: re-check the deadline here too.
 * Clearing from this context races harmlessly with the owning CPU's tick, as
 * both write the same "not boosting" state.
 */
static bool abk_sf_cpu_boosting(int cpu)
{
    struct abk_sf_cpu *c = &abk_sf_cpus[cpu];

    if (!c->boosting)
        return false;
    if (time_after(jiffies, c->boost_release)) {
        abk_sf_clear(c);
        return false;
    }
    return true;
}

/*
 * Is this policy's frequency owned by a DVFS engine this layer must not speak
 * to?  A FAS-style owner (vendor WALT/FAS -- Scene's sceneFAS among them) drives
 * cpufreq by requesting min == max == its own target.  There, lifting the target
 * to a floor is not a lift at all: it re-states the frequency already applied, so
 * the owner can go up but never come down.  Anything that is not plain schedutil
 * counts as owned; the caller adds the other half of the test, a range that has
 * already collapsed to a single operating point, which covers a thermal or perf
 * cap on a tree that really does run schedutil.
 */
static bool abk_sf_dvfs_owned(struct cpufreq_policy *policy)
{
    if (!policy->governor)
        return false;
    if (strcmp(policy->governor->name, "schedutil") != 0)
        return true;
    return false;
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

    if (abk_sf_dvfs_owned(policy) || policy->min == policy->max)
        return;

    for_each_cpu(cpu, policy->cpus) {
        if (abk_sf_cpu_boosting(cpu)) {
            boosting = true;
            break;
        }
    }
    if (!boosting)
        return;

    floor = mult_frac(policy->cpuinfo.max_freq, abk_sf_floor_pct, 100);

    /*
     * cpufreq clamps the resolved target into [policy->min, policy->max], so a
     * floor at or below policy->min is already satisfied by that clamp, and a
     * floor at or above policy->max has no headroom left to lift into.
     * Applying either is exactly what turns a floor into a lock.
     */
    if (floor <= policy->min || floor >= policy->max)
        return;

    if (*target_freq < floor)
        *target_freq = floor;
}

static unsigned long abk_sf_boost_mask(void)
{
    unsigned long mask = 0;
    int cpu;

    if (!abk_sf_cpus)
        return 0;

    for_each_possible_cpu(cpu) {
        if (cpu < BITS_PER_LONG && abk_sf_cpus[cpu].boosting)
            mask |= 1UL << cpu;
    }
    return mask;
}

static int abk_sf_boosting_get(char *buf, const struct kernel_param *kp)
{
    return sprintf(buf, "0x%lx\n", abk_sf_boost_mask());
}

/*
 * Read-only view of the reason state.  Without it this policy can only be
 * diagnosed from the frequencies it produces, which is how a ratchet stayed
 * invisible on a shipping device for a whole batch.
 */
static const struct kernel_param_ops abk_sf_boosting_ops = {
    .get = abk_sf_boosting_get,
};

module_param_cb(abk_sf_boosting, &abk_sf_boosting_ops, NULL, 0444);
MODULE_PARM_DESC(abk_sf_boosting,
    "read-only mask of CPUs currently holding the sustained-high reason");

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
        "hooks are governor-independent, so this build stands down unless "
        "schedutil owns the policy); "
        "enable=%d sustained_ms=%d exit_ms=%d floor_pct=%d\n",
        abk_sf_enable, abk_sf_sustained_ms, abk_sf_exit_ms,
        abk_sf_floor_pct);
    return 0;
}

late_initcall(abk_sf_init);
""")

_TAIL_NEW = _TAIL_OLD + _POLICY_V2


def _norm(text):
    return text.replace("\r\n", "\n")


def has_current_policy(text):
    """True when the tree already carries the current payload."""
    return _norm(_POLICY_V2) in _norm(text)


def has_legacy_policy(text):
    """True when the tree carries the Batch 10-4c payload."""
    return _norm(_POLICY_V1) in _norm(text)


def has_unknown_policy(text):
    """True when a smart-freq payload is present that matches no known anchor.

    Batch 10-2 shipped this group once before the hook move, and its payload
    declares the same variables against the same `_TAIL_OLD` anchor.  Neither
    migration anchor matches it, so the plain insert would put a second copy of
    the policy in front of it -- duplicate definitions, and a group still
    reporting `applied`.  Refusing is the only transactional option.
    """
    text = _norm(text)
    return ("static bool abk_sf_enable" in text
            and not has_current_policy(text) and not has_legacy_policy(text))


def needs_legacy_upgrade(text):
    """A previously grafted tree whose payload predates the DVFS-ownership fix."""
    return has_legacy_policy(text) and not has_current_policy(text)


def policy_sha256():
    """Short fingerprint of the live payload.

    Reported in the graft detail so a build log says *which* payload generation it
    wrote.  It cannot live in the payload itself -- embedding the digest would move
    the digest -- and before it existed, telling a pre-10-5 kernel from a current
    one meant reading frequencies and guessing, which is how the Batch 10-6
    mis-attribution happened in the first place.
    """
    return hashlib.sha256(_POLICY_V2.encode()).hexdigest()[:16]


def _inc_step(anchor):
    return (_REL, anchor, _INC_NEW, T)


def build_steps():
    """Steps for a tree without this policy (or one already at v2)."""
    return [
        _inc_step(_INC_OLD),
        (_REL, _TAIL_OLD, _TAIL_NEW, T),
    ]


def build_upgrade_steps():
    """Steps for a tree carrying the v1 payload: rewrite it in place.

    ``_TAIL_OLD`` alone still matches such a file (it is the line the payload
    was appended to), so taking :func:`build_steps` there would insert a second
    copy of the policy ahead of the first -- duplicate definitions, compile
    failure.  Anchoring on ``_TAIL_OLD + _POLICY_V1`` instead makes the upgrade
    a replacement, and every shape stays idempotent.
    """
    return [
        _inc_step(_INC_V1),
        (_REL, _TAIL_OLD + _POLICY_V1, _TAIL_OLD + _POLICY_V2, T),
    ]
