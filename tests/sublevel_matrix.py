#!/usr/bin/env python3
"""Expected per-sublevel graft statuses for the android13-5.15 GKI baselines.

The engine gates purely on text anchors -- ``ctx.sub_level`` never takes part
in a comparison -- so a group whose upstream commit is already in the target
baseline correctly reports ``already_present`` instead of ``applied``.  That
makes "applied" the wrong universal assertion: on 5.15.194 the fd-table
conventions (5.15.191) are already in the tree.

This table records, per sublevel, which groups are expected to arrive
pre-applied.  Anything not listed must report ``applied`` on a pristine tree;
nothing may ever report ``blocked_by_shape``.

Verified against the real AOSP branches:
  167 -> deprecated/android13-5.15-2024-11
  178 -> deprecated/android13-5.15-2025-03
  194 -> android13-5.15-2025-12
"""

from __future__ import annotations

# child id -> total registered groups
GROUP_COUNTS = {
    "stable_backport_core": 11,
    "stable_perf_backport": 12,
}

# sublevel -> child id -> groups whose upstream commit the baseline already has
PRE_APPLIED = {
    "167": {
        "stable_backport_core": set(),
        "stable_perf_backport": set(),
    },
    "178": {
        "stable_backport_core": set(),
        # 5.15.174 NOHZ series landed in the 2025-03 baseline.
        "stable_perf_backport": {"sched_nohz_idle_balance_series"},
    },
    "194": {
        # 5.15.191 fd-table conventions, 5.15.191 cpuset bail-out and the
        # 5.15.194 cgroup destroy-wq split are all in the 2025-12 baseline.
        "stable_backport_core": {
            "fdtable_alloc_conventions",
            "pagealloc_cpuset_bailout",
            "cgroup_destroy_wq_split",
        },
        # 5.15.174 NOHZ series and 5.15.180 semaphore wake_q.
        "stable_perf_backport": {
            "sched_nohz_idle_balance_series",
            "semaphore_wake_q",
        },
    },
}

SUPPORTED = tuple(PRE_APPLIED)
DEFAULT_SUB_LEVEL = "167"


def pre_applied(sub_level, child):
    """Groups expected to be ``already_present`` on a pristine ``sub_level`` tree."""
    try:
        return PRE_APPLIED[sub_level][child]
    except KeyError:
        raise SystemExit(
            f"no expectation recorded for sublevel {sub_level!r} child {child!r}; "
            f"supported sublevels: {', '.join(SUPPORTED)}"
        ) from None


def status_summary(sub_level, child):
    """The ``status_summary`` dict a first pass must produce."""
    total = GROUP_COUNTS[child]
    present = len(pre_applied(sub_level, child))
    summary = {}
    if total - present:
        summary["applied"] = total - present
    if present:
        summary["already_present"] = present
    return summary


def idempotent_summary(child):
    """The ``status_summary`` dict a second pass must produce."""
    return {"already_present": GROUP_COUNTS[child]}


def applies(sub_level, child, group_key):
    """True when ``group_key`` really rewrites the ``sub_level`` baseline.

    A group the baseline already carries leaves no ``ABK stable_515_backport:``
    marker, so marker assertions must be gated on this.
    """
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
            "pass2": idempotent_summary(child),
        }, sort_keys=True))
        return
    print(json.dumps({
        child: {
            "pass1": status_summary(sub_level, child),
            "pass2": idempotent_summary(child),
        }
        for child in GROUP_COUNTS
    }, sort_keys=True))


if __name__ == "__main__":
    _main()
