#!/usr/bin/env python3

from __future__ import annotations

GROUP_COUNTS = {
    "stable_backport_core": 78,
    "stable_perf_backport": 24,
    "stable_display_fix": 1,
}

PRE_APPLIED = {
    "220": {
        "stable_backport_core": {
            "fdtable_alloc_conventions",
            "fdtable_replace_fd_errno",
            "pagealloc_thisnode_thp_noreclaim",
            "pagealloc_cpuset_bailout",
            "pagealloc_high_fraction_lockfree",
            "cgroup_destroy_wq_split",
            "arm64_pte_mkwrite_clean",
            "huge_memory_imap_split_uaf",
            "vmscan_tasks_rcu_qs",
        },
        "stable_perf_backport": {
            "sched_nohz_idle_balance_series",
            "sched_steal_time_excess_drop",
            "release_sock_cond_resched",
            "semaphore_wake_q",
            "sched_rt_optimizations",
            "sched_dst_group_allowed_stats",
            "blk_mq_suspend_wakeup_abort",
            "blk_mq_quiesced_elevator_switch",
        },
        "stable_display_fix": set(),
    },
}

KNOWN_DEBT = {}

SUPPORTED = tuple(PRE_APPLIED)

DEFAULT_SUB_LEVEL = "220"


def pre_applied(sub_level, child):
    try:
        return PRE_APPLIED[sub_level][child]
    except KeyError:
        raise SystemExit(
            f"no expectation recorded for sublevel {sub_level!r} child {child!r}; "
            f"supported sublevels: {', '.join(SUPPORTED)}.\n"
            f"Batch 44 dropped the 5.15.167/.178/.194 release baselines, so the "
            f"only row is the rolling android13-5.15-lts fixture, keyed to the "
            f"Makefile SUBLEVEL it was proven against.\n"
            f"If {sub_level!r} is a freshly fetched lts tree, the branch has "
            f"rolled: re-key this row to {sub_level!r} and re-prove every set "
            f"on that tree (docs/porting_policy.md 'Lts-only maintenance'). "
            f"ABK_TEST_SUB_LEVEL does not override that requirement."
        ) from None


def debt(sub_level, child):
    return KNOWN_DEBT.get(sub_level, {}).get(child, {})


def status_summary(sub_level, child):
    total = GROUP_COUNTS[child]
    present = len(pre_applied(sub_level, child))
    debts = debt(sub_level, child)
    degraded = {}
    for group_key, status in debts.items():
        if group_key not in pre_applied(sub_level, child):
            degraded[status] = degraded.get(status, 0) + 1
    applied = total - present - sum(degraded.values())
    summary = {}
    if applied:
        summary["applied"] = applied
    if present:
        summary["already_present"] = present
    for status, count in degraded.items():
        summary[status] = summary.get(status, 0) + count
    return summary


def idempotent_summary(sub_level, child):
    summary = {"already_present": GROUP_COUNTS[child]}
    for group_key, status in debt(sub_level, child).items():
        if status in ("blocked_by_shape", "blocked_by_missing_anchor"):
            summary["already_present"] -= 1
            summary[status] = summary.get(status, 0) + 1
    return summary


def applies(sub_level, child, group_key):
    return group_key not in pre_applied(sub_level, child)


def _main():
    import json
    import sys

    if len(sys.argv) < 2:
        raise SystemExit(
            "usage: sublevel_matrix.py <sublevel> [child]\n"
            "  no child: JSON of {child: {pass1, pass2}}\n"
            "  with child: JSON of {pass1, pass2}"
        )
    sub_level = sys.argv[1]
    if len(sys.argv) > 2:
        child = sys.argv[2]
        print(json.dumps({
            "pass1": status_summary(sub_level, child),
            "pass2": idempotent_summary(sub_level, child),
        }, sort_keys=True))
        return
    print(json.dumps({
        child: {
            "pass1": status_summary(sub_level, child),
            "pass2": idempotent_summary(sub_level, child),
        }
        for child in GROUP_COUNTS
    }, sort_keys=True))


if __name__ == "__main__":
    _main()
