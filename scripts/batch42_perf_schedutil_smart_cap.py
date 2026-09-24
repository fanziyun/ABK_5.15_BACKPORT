# -*- coding: utf-8 -*-
"""Batch 42: schedutil smart_freq cap -- the ``freq_cap[]`` half of the quartet.

Batch 10-2 scoped the WALT ``smart_freq`` control-downport as "reason election
+ deactivation hysteresis + **frequency cap** + per-cluster threshold table".
``schedutil_smart_policy`` (scripts/batch10_perf_sched_policy.py) landed the
reason election and the frequency *floor*; the cap itself -- upstream's
``if (freq > smart_freq) freq = smart_freq;`` in ``get_smart_freq_limit()`` --
was scoped in that draft and never landed.  This module is that half and
nothing else.  Group ``schedutil_smart_cap``, file
``kernel/sched/cpufreq_schedutil.c``, hook ``android_vh_cpufreq_resolve_freq``.

Coupling with ``schedutil_smart_policy``, which shares the hook, the file and
the anchor.  Registration order is execution order for ``android_vh`` probes,
so this group is registered *after* that one and grafted *in front of* the
``cpufreq_governor_init(schedutil_gov);`` line that group's payload is appended
behind.  That is the only placement that is all of:

- textually ahead of the floor payload, so this payload's ``late_initcall``
  runs first and the clamp is what the floor sees;
- independent of which group the engine happened to run first -- ``_TAIL_OLD``
  stays a complete anchor for either group after either has fired, so both
  orders converge on the same file;
- idempotent for *both* groups.  Inserting after that anchor instead would put
  this payload between the anchor and the floor payload, and on the second pass
  the floor group's replacement would stop matching while its anchor still did,
  so it would append a second copy of the floor payload -- the Batch-21
  ``psi_account_irqtime()`` duplicate-definition trap (group_recipe.md §2 trap
  5), which only ever shows up on the patched-tree pass.

The payload's state is per-policy under a raw spinlock, not a lock-free per-CPU
step counter: ``__resolve_freq()`` runs on the ``fast_switch`` path under
``rq->lock`` and every CPU of a shared policy can be inside it at once, so an
unlocked index is a data race.  The community ``schedhorizon`` fork of this idea
carries exactly that shape (an unlocked ``current_step`` with a type mismatch, a
threshold table whose length disagrees with itself and leftover debug prints),
and none of it is copied.
"""

import hashlib
import re

__all__ = [
    "build_steps", "has_current_cap", "has_unknown_cap", "has_header_block",
    "cap_sha256", "T",
]


def _tabs(text):
    text = re.sub(r"(?m)^    +",
                  lambda m: "\t" * (len(m.group(0)) // 4), text)
    return text.rstrip("\n") + "\n"


T = True

_INC_OLD = "#include <trace/hooks/sched.h>\n"
# The include block schedutil_smart_policy's step leaves behind.  Its last line
# is this group's shape probe (see has_header_block): the whole block is added
# in one step, so finding that line means the header block is already in the
# tree.
_INC_NEW = (
    "#include <trace/hooks/sched.h>\n"
    "#include <trace/hooks/cpufreq.h>\n"
    "#include <linux/math.h>\n"
    "#include <linux/moduleparam.h>\n"
    "#include <linux/string.h>\n"
)
_INC_HINT = "#include <linux/string.h>\n"

_TAIL_OLD = "cpufreq_governor_init(schedutil_gov);\n"
_REL = "kernel/sched/cpufreq_schedutil.c"

# --- payload ---------------------------------------------------------------
_CAP = _tabs(r"""/*
 * ABK stable_515_backport: Batch 42 schedutil smart_freq cap -- the
 * freq_cap[] -> freq = min(freq, cap) half of the WALT smart_freq quartet.
 *
 * Batch 10-2 scoped this line as reason election + deactivation hysteresis +
 * frequency cap + per-cluster thresholds; schedutil_smart_policy landed the
 * reason election and the frequency *floor*, and this half never landed.  It
 * comes from the same upstream code:
 *
 *   research/popsicle_w_oss/walt_extract/smart_freq.c:433
 *       smart_freq_update_one_cluster(): the cap election.  max_cap starts at
 *       NO_REASON_SMART_FREQ's freq_allowed and only grows towards
 *       max_possible_freq while a reason survives; the winner is published as
 *       freq_cap[SMART_FREQ][cluster->id].
 *   research/popsicle_w_oss/walt_extract/smart_freq.c:505
 *       thres_based_uncap(): UNCAP_THRES 300000000 and UTIL_THRESHOLD 90 --
 *       the sustained window that lifts a cap back to 100%.  Those two
 *       constants are abk_sc_hold_ms and abk_sc_entry_pct.
 *   research/popsicle_w_oss/walt_extract/cpufreq_walt.c:264
 *       get_smart_freq_limit(): `if (freq > smart_freq) freq = smart_freq`,
 *       which is the clamp below, line for line.
 *
 * The clamp runs in android_vh_cpufreq_resolve_freq(), which cpufreq.c calls
 * from __resolve_freq() after the policy min/max clamp and before the
 * frequency-table lookup, so it covers the fast_switch and the slow path alike
 * (research/popsicle_w_oss/walt_pelt_survey.md 3B names it the best place for
 * exactly this cap; 4.1(b) is this feature).
 *
 * What differs from upstream, on purpose:
 *
 *   - state is per-policy under a raw spinlock, not per-CPU and not lock-free.
 *     fast_switch calls __resolve_freq() under rq->lock, and several CPUs of
 *     one policy can be inside it at once, so the two CPUs of a shared policy
 *     have to see one ladder, not two.
 *   - the release decision is driven by the direction of the frequency
 *     request, not by a util time window.  See ABK_SC_RELEASE below.
 *   - off by default, and gated exactly like schedutil_smart_policy: on a
 *     vendor FAS/WALT device this cap is already someone else's feature, and a
 *     cap laid on a range that has collapsed to one operating point is a
 *     frequency lock rather than a cap -- Batch 10-4c's ratchet, from this
 *     side.
 */

static bool abk_sc_enable = false;
static uint abk_sc_cap_pct = 100;
static uint abk_sc_hold_ms = 300;
static uint abk_sc_entry_pct = 90;
static uint abk_sc_release_pct = 70;
static uint abk_sc_release_ms = 250;

module_param(abk_sc_enable, bool, 0644);
module_param(abk_sc_cap_pct, uint, 0644);
module_param(abk_sc_hold_ms, uint, 0644);
module_param(abk_sc_entry_pct, uint, 0644);
module_param(abk_sc_release_pct, uint, 0644);
module_param(abk_sc_release_ms, uint, 0644);
MODULE_PARM_DESC(abk_sc_enable,
    "ABK smart_freq cap: clamp the resolved frequency to a fraction of "
    "cpuinfo.max_freq (off by default; see abk_sc_dvfs_owned)");
MODULE_PARM_DESC(abk_sc_cap_pct,
    "cap as a percentage of cpuinfo.max_freq; 100 is no clamp at all");
MODULE_PARM_DESC(abk_sc_hold_ms,
    "ms the frequency request must stay above the cap before the cap is "
    "released (upstream UNCAP_THRES)");
MODULE_PARM_DESC(abk_sc_entry_pct,
    "util percentage that starts the sustained window abk_sc_boosting "
    "reports (upstream UTIL_THRESHOLD)");
MODULE_PARM_DESC(abk_sc_release_pct,
    "util percentage every CPU of the policy must stay below before the cap "
    "is re-asserted");
MODULE_PARM_DESC(abk_sc_release_ms,
    "ms the whole policy must stay below release_pct before the cap is "
    "re-asserted");

struct abk_sc_cpu {
    unsigned long reason_start;	/* first tick at/above entry_pct */
    unsigned long busy_last;		/* last tick at/above release_pct */
    bool reason;			/* entry_pct held for hold_ms */
};

struct abk_sc_policy {
    raw_spinlock_t lock;
    bool capped;		/* this resolve pass clamped the target */
    bool released;		/* sustained request demand lifted the cap */
    unsigned long high_start;	/* first request above cap in this run */
};

static struct abk_sc_cpu *abk_sc_cpus;
static struct abk_sc_policy *abk_sc_policies;

/*
 * This payload's own copy of the ownership gate.  schedutil_smart_policy has
 * an identically-named helper (abk_sf_dvfs_owned) in this same translation
 * unit, and two functions of one name is a compile error rather than a second
 * opinion, so the two payloads deliberately share no identifier.
 */
static bool abk_sc_dvfs_owned(struct cpufreq_policy *policy)
{
    if (!policy->governor)
        return false;
    if (strcmp(policy->governor->name, "schedutil") != 0)
        return true;
    return false;
}

/*
 * ABK_SC_RELEASE IS NOT A UTIL WINDOW, and that is the whole point.
 *
 * schedutil_smart_policy's first form released on a util time window -- enter
 * at >= 90%, renew at >= 70%, exit only after 250 ms below 70% -- and on a
 * real device that produced a 70-90% dead band: scrolling and background jobs
 * parked util at 75-85%, the exit window never ran, the reason never cleared,
 * and the frequency was held up until the session ended.  The cap therefore
 * releases on the direction of the frequency *request*: requests have to stay
 * above the cap continuously for abk_sc_hold_ms.
 *
 * The only util window left in this payload runs the other way -- re-asserting
 * a cap that has already been released -- and that asymmetry is deliberate.  A
 * window that can only re-arm a feature cannot hold a frequency down, so there
 * is no dead band here for the old failure mode to recur in: in the 75-85%
 * case above the request window opens, the cap is released and the frequency
 * is free.  abk_sc_entry_pct feeds nothing but the upstream-shaped reason
 * election reported through abk_sc_boosting (upstream thres_based_uncap()); it
 * gates no decision of its own.
 */

static unsigned int abk_sc_cap(struct cpufreq_policy *policy)
{
    unsigned int pct = abk_sc_cap_pct;

    if (pct > 100)
        pct = 100;
    return mult_frac(policy->cpuinfo.max_freq, pct, 100);
}

/*
 * The shared front of both probes: false when this policy is not ours at all.
 *
 * Anything that is not plain schedutil counts as DVFS-owned -- a vendor
 * FAS/WALT owner drives cpufreq by requesting min == max == its own target, so
 * a cap on top of that is the frequency lock Batch 10-4c shipped.
 * policy->min == policy->max is the same dead end reached by a thermal or a
 * perf cap on a tree that really does run schedutil.  And a cap that
 * __resolve_freq()'s own [policy->min, policy->max] clamp already satisfies
 * has nothing left to clamp into, so it is skipped rather than applied as a
 * lock.
 */
static bool abk_sc_owns(struct cpufreq_policy *policy, unsigned int *cap)
{
    unsigned int c;

    if (!abk_sc_enable || !abk_sc_policies)
        return false;
    if (abk_sc_dvfs_owned(policy) || policy->min == policy->max)
        return false;

    c = abk_sc_cap(policy);
    if (c <= policy->min || c >= policy->max)
        return false;

    *cap = c;
    return true;
}

static void abk_sc_tick(void *data, struct rq *rq)
{
    struct abk_sc_cpu *c;
    unsigned long cap, util, now;

    if (!abk_sc_enable || !abk_sc_cpus)
        return;

    cap = arch_scale_cpu_capacity(rq->cpu);
    if (!cap)
        return;

    util = cpu_util_cfs(rq);
    if (util > cap)
        util = cap;

    now = jiffies;
    c = &abk_sc_cpus[rq->cpu];

    if (util * 100 >= cap * abk_sc_entry_pct) {
        if (!c->reason_start)
            c->reason_start = now;
    } else {
        c->reason_start = 0;
    }

    if (util * 100 >= cap * abk_sc_release_pct)
        c->busy_last = now;

    /*
     * Derived state, not a hysteresis flag: upstream thres_based_uncap()
     * recomputes sustained_load from found_ts on every window rather than
     * latching it, and so does this.  The reason is true exactly while the CPU
     * has been at or above entry_pct for hold_ms, and stops being true the
     * moment it drops.
     */
    c->reason = c->reason_start &&
        time_after(now, c->reason_start +
                   msecs_to_jiffies(abk_sc_hold_ms));
}

/*
 * Has every CPU of this policy stayed below abk_sc_release_pct for
 * abk_sc_release_ms?  Read from jiffies against the last tick each CPU spent at
 * or above release_pct rather than from a window that only advances on ticks:
 * an idle CPU stops ticking under NO_HZ_IDLE, and a window that needs a tick
 * never expires on the small clusters -- the same defect that let
 * smart_policy's flag latch for exactly as long as a CPU stayed idle.
 *
 * Read without p->lock, on purpose: the owning CPU's tick writes its own entry
 * and both values only move towards "cooled", so a stale read costs one tick of
 * delay and never asserts a cap that has not been backed off.
 */
static bool abk_sc_cooled(struct cpufreq_policy *policy)
{
    unsigned long oldest = 0;
    int cpu;

    if (!abk_sc_cpus)
        return false;

    for_each_cpu(cpu, policy->cpus) {
        unsigned long busy = abk_sc_cpus[cpu].busy_last;

        if (!busy)
            continue;
        if (!oldest || time_before(busy, oldest))
            oldest = busy;
    }

    /* No CPU has been above release_pct since boot, so nothing holds it. */
    if (!oldest)
        return true;

    return time_after(jiffies, oldest + msecs_to_jiffies(abk_sc_release_ms));
}

static void abk_sc_resolve_freq(void *data, struct cpufreq_policy *policy,
                                unsigned int *target_freq,
                                unsigned int old_target_freq)
{
    struct abk_sc_policy *p;
    unsigned long flags;
    unsigned int cap;

    if (!target_freq || !abk_sc_owns(policy, &cap))
        return;

    p = &abk_sc_policies[policy->cpu];
    raw_spin_lock_irqsave(&p->lock, flags);

    /* The policy has backed off: re-assert the cap, restart the window. */
    if (abk_sc_cooled(policy)) {
        p->released = false;
        p->high_start = 0;
    }

    if (*target_freq > cap) {
        if (p->released) {
            p->capped = false;
        } else {
            if (!p->high_start) {
                p->high_start = jiffies;
            } else if (time_after(jiffies, p->high_start +
                                  msecs_to_jiffies(abk_sc_hold_ms))) {
                /*
                 * Sustained demand, from the request direction: the cap gets
                 * out of the way, until abk_sc_cooled() re-asserts it.
                 */
                p->released = true;
            }
            if (p->released) {
                p->capped = false;
            } else {
                *target_freq = cap;
                p->capped = true;
            }
        }
    } else {
        /*
         * Nothing above the cap is being asked for.  The window means
         * *continuously* above the cap, so it restarts here instead of being
         * stretched across the gap -- but the release itself is left alone: a
         * one-frame dip must not re-arm the cap a sustained session has
         * already lifted.
         */
        p->high_start = 0;
        p->capped = false;
    }

    raw_spin_unlock_irqrestore(&p->lock, flags);
}

/*
 * The other half of "cap first, and the cap wins".
 *
 * schedutil_smart_policy registers on the same vendor hook and -- this payload
 * being grafted ahead of it -- runs after the clamp above.  Its floor can only
 * raise a target, so left alone it would lift a clamped target straight back
 * above the cap and the two would trade one range back and forth.  This probe
 * is registered at late_initcall_sync, which orders after that group's
 * late_initcall, so it runs last on the hook and re-asserts the clamp this very
 * pass already took.  abk_sc_capped was set by the clamp probe inside the same
 * __resolve_freq() call, so it still describes this pass when it is read here;
 * a target the cap did not touch is never touched again.
 */
static void abk_sc_reassert_freq(void *data, struct cpufreq_policy *policy,
                                 unsigned int *target_freq,
                                 unsigned int old_target_freq)
{
    struct abk_sc_policy *p;
    unsigned long flags;
    unsigned int cap;

    if (!target_freq || !abk_sc_owns(policy, &cap))
        return;

    p = &abk_sc_policies[policy->cpu];
    raw_spin_lock_irqsave(&p->lock, flags);
    if (p->capped && *target_freq > cap)
        *target_freq = cap;
    raw_spin_unlock_irqrestore(&p->lock, flags);
}

/*
 * Both nodes are read-only on purpose: they are the diagnosis, not a control
 * surface, and a writable knob here is how a cap becomes a frequency lock by
 * keystroke.  abk_sc_boosting carries the upstream-shaped sustained-high
 * reason, kept so the two implementations can be compared on one device;
 * abk_sc_capped says which policy's target the cap is suppressing right now,
 * so "who owns this range, the cap or schedutil_smart_policy's floor" is
 * answerable without reading frequencies and guessing again -- which is how the
 * Batch 10-4c ratchet stayed invisible for a whole batch.
 */
static unsigned long abk_sc_boost_mask(void)
{
    unsigned long mask = 0;
    int cpu;

    if (!abk_sc_cpus)
        return 0;

    for_each_possible_cpu(cpu) {
        if (cpu < BITS_PER_LONG && abk_sc_cpus[cpu].reason)
            mask |= 1UL << cpu;
    }
    return mask;
}

/*
 * Keyed by policy->cpu -- the index the per-policy state itself uses -- so a
 * three-CPU cluster sets one bit here, not three.
 */
static unsigned long abk_sc_capped_mask(void)
{
    unsigned long mask = 0;
    int cpu;

    if (!abk_sc_policies)
        return 0;

    for_each_possible_cpu(cpu) {
        if (cpu < BITS_PER_LONG && abk_sc_policies[cpu].capped)
            mask |= 1UL << cpu;
    }
    return mask;
}

static int abk_sc_boosting_get(char *buf, const struct kernel_param *kp)
{
    return sprintf(buf, "0x%lx\n", abk_sc_boost_mask());
}

static const struct kernel_param_ops abk_sc_boosting_ops = {
    .get = abk_sc_boosting_get,
};

module_param_cb(abk_sc_boosting, &abk_sc_boosting_ops, NULL, 0444);
MODULE_PARM_DESC(abk_sc_boosting,
    "read-only mask of CPUs holding the sustained-high-util reason "
    "(upstream thres_based_uncap)");

static int abk_sc_capped_get(char *buf, const struct kernel_param *kp)
{
    return sprintf(buf, "0x%lx\n", abk_sc_capped_mask());
}

static const struct kernel_param_ops abk_sc_capped_ops = {
    .get = abk_sc_capped_get,
};

module_param_cb(abk_sc_capped, &abk_sc_capped_ops, NULL, 0444);
MODULE_PARM_DESC(abk_sc_capped,
    "read-only mask of policy->cpu whose target the cap is suppressing");

static int __init abk_sc_init(void)
{
    int ret, cpu;

    abk_sc_cpus = kcalloc(num_possible_cpus(), sizeof(*abk_sc_cpus),
                          GFP_KERNEL);
    abk_sc_policies = kcalloc(num_possible_cpus(), sizeof(*abk_sc_policies),
                              GFP_KERNEL);
    if (!abk_sc_cpus || !abk_sc_policies) {
        kfree(abk_sc_cpus);
        kfree(abk_sc_policies);
        abk_sc_cpus = NULL;
        abk_sc_policies = NULL;
        return -ENOMEM;
    }

    for_each_possible_cpu(cpu)
        raw_spin_lock_init(&abk_sc_policies[cpu].lock);

    ret = register_trace_android_vh_scheduler_tick(abk_sc_tick, NULL);
    if (ret)
        pr_warn("ABK stable_515_backport: smart_freq cap: scheduler_tick "
            "hook unavailable (%d)\n", ret);

    /*
     * Ordering, and it is load-bearing: android_vh probes run in the order
     * they were registered, so this late_initcall has to run before
     * schedutil_smart_policy's.  Both are late_initcall in this same file, so
     * within one translation unit that means textually first -- which is why
     * this payload is grafted in front of the
     * `cpufreq_governor_init(schedutil_gov);` line rather than behind it.  The
     * clamp then runs before the floor's raise; the re-assert below (7s, after
     * that group's late_initcall) runs after it.
     */
    ret = register_trace_android_vh_cpufreq_resolve_freq(
        abk_sc_resolve_freq, NULL);
    if (ret)
        pr_warn("ABK stable_515_backport: smart_freq cap: "
            "cpufreq_resolve_freq hook unavailable (%d)\n", ret);

    pr_info("ABK stable_515_backport: schedutil smart_freq cap "
        "(freq_cap[] -> min(freq, cap)); off by default and schedutil only, "
        "cap_pct=%u hold_ms=%u entry_pct=%u release_pct=%u release_ms=%u\n",
        abk_sc_cap_pct, abk_sc_hold_ms, abk_sc_entry_pct,
        abk_sc_release_pct, abk_sc_release_ms);
    return 0;
}

late_initcall(abk_sc_init);

static int __init abk_sc_reassert_init(void)
{
    int ret;

    ret = register_trace_android_vh_cpufreq_resolve_freq(
        abk_sc_reassert_freq, NULL);
    if (ret)
        pr_warn("ABK stable_515_backport: smart_freq cap: re-assert hook "
            "unavailable (%d)\n", ret);
    return 0;
}

late_initcall_sync(abk_sc_reassert_init);
""")
# One blank line between the payload and the anchor it is grafted in front of,
# so the grafted file does not read as one run-on block.
_CAP = _CAP + "\n"


def _norm(text):
    return text.replace("\r\n", "\n")


def has_header_block(text):
    """True when the shared header block is already in the tree.

    ``schedutil_smart_policy``'s include step adds this block, and it is the
    same block this group needs.  Probing for it keeps this group from emitting
    an include step that ``replace_once`` would short-circuit to
    ``already_present`` on a tree that group has already helped -- which
    ``step_audit.py`` rejects in a must-apply group (trap 1b) -- while a tree
    that has neither still gets the step and a genuinely unrecognised include
    shape degrades to a missing anchor.
    """
    return _INC_HINT in _norm(text)


def has_current_cap(text):
    """True when the tree already carries this payload."""
    return _norm(_CAP) in _norm(text)


def has_unknown_cap(text):
    """True when a smart-freq cap payload is present that matches no anchor.

    Same transactionality argument as ``batch10_perf_sched_policy``'s
    ``has_unknown_policy``: a shape this module cannot name still leaves
    ``_TAIL_OLD`` in place, so the plain insert would put a second copy of the
    payload in front of it -- duplicate definitions, and a group still
    reporting ``applied``.
    """
    text = _norm(text)
    return ("static bool abk_sc_enable" in text
            and not has_current_cap(text))


def cap_sha256():
    """Short fingerprint of the live payload, reported in the graft detail.

    The payload cannot carry its own digest (embedding it would move it), so
    "which generation did this build write" belongs in the report, exactly as
    ``batch10_perf_sched_policy.policy_sha256`` does for the floor.
    """
    return hashlib.sha256(_CAP.encode()).hexdigest()[:16]


def build_steps(add_headers):
    """Steps for a tree without this cap (or one already carrying it).

    ``add_headers`` is :func:`has_header_block` negated: on a tree
    ``schedutil_smart_policy`` has already helped, adding the shared include
    block again would be a duplicate include and the step would never run.
    """
    steps = []
    if add_headers:
        steps.append((_REL, _INC_OLD, _INC_NEW, T))
    steps.append((_REL, _TAIL_OLD, _CAP + _TAIL_OLD, T))
    return steps
