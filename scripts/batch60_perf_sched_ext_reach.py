"""Batch 60 (sched_ext S2b-2b, second half, part 2): reachability.

Batch 58 landed the task-lifecycle hooks and Batch 59 the scheduling-core ones,
but none of it can be reached: `SCHED_EXT` is still rejected by
`valid_policy()`, the BPF syscall has no `sched_ext_ops` value type to bind a
scheduler to, and the engine cannot report itself.  This batch closes the three
gaps, which together are what "the class is reachable" means.

Four groups:

* `sched_ext_policy_valid` -- `kernel/sched/sched.h`: 6.6's `normal_policy()` is
  reintroduced (5.15 folded it away) and `fair_policy()` builds on it, so a
  `SCHED_EXT` task is a "normal" policy where it matters: `__setscheduler_params()`
  then gives it a static priority and a load weight, and `valid_policy()`
  accepts the policy through `fair_policy()`.  Without this the syscall returns
  `-EINVAL` before any SCX hook runs.

* `sched_ext_priority_range` -- `kernel/sched/core.c`: `sched_get_priority_max()`
  and `sched_get_priority_min()` gain `case SCHED_EXT:`, because a loader has to
  ask for the priority range of the policy it is about to set.

* `sched_ext_struct_ops_type` -- `kernel/bpf/bpf_struct_ops_types.h`: the 5.15
  registry of struct_ops map value types is this header, included four times
  with the macro defined differently.  `BPF_STRUCT_OPS_TYPE(sched_ext_ops)`
  turns the engine's `bpf_sched_ext_ops` into a bindable value type; without it
  a `sched_ext_ops` struct_ops map has no type to load against.

* `sched_ext_debugfs` -- `kernel/sched/debug.c`: `sched_init_debug()` registers
  `/sys/kernel/debug/sched/ext`, the engine's own dump (enabled state, ops
  state, watchdog timeout, per-task queue state).

Two new files enter the three fixture lists (`FETCH_FILES`, `AUDIT_FILES`,
`SMOKE_FILES`), so both reference trees were re-fetched before the audits.

The defconfig decision is unchanged and recorded here: `CONFIG_SCHED_CLASS_EXT`
stays in the **module tier** (`_MODULE_CONFIGS`), so every graft carries the
class.  It is a static-key-gated class with no tasks on it until a BPF
scheduler is attached, so the cost of the graft is the build size, and the
alternative (a ROM tier) would mean shipping a tree where the whole S2 effort
is dead code.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

SCHED_H = "kernel/sched/sched.h"
CORE_C = "kernel/sched/core.c"
BPF_STRUCT_OPS_H = "kernel/bpf/bpf_struct_ops_types.h"
DEBUG_C = "kernel/sched/debug.c"


# ---------------------------------------------------------------------------
# kernel/sched/sched.h: normal_policy()/fair_policy().
# ---------------------------------------------------------------------------

_FAIR_POLICY_OLD = (
    "static inline int fair_policy(int policy)\n"
    "{\n"
    "\treturn policy == SCHED_NORMAL || policy == SCHED_BATCH;\n"
    "}\n"
)

_FAIR_POLICY_NEW = (
    "/*\n"
    " * sailboat_sched_ext_policy_valid: 6.6's normal_policy(), which 5.15 folded\n"
    " * into fair_policy().  It is the predicate that decides which policies are\n"
    " * \"normal\" in the sense that matters to __setscheduler_params(): they take a\n"
    " * nice value and a load weight.  SCHED_EXT is one of those -- the BPF\n"
    " * scheduler owns the class, but the task still needs p->static_prio and\n"
    " * p->se.load set before it is handed over -- so it is accepted here under\n"
    " * the class's own config, and valid_policy() then accepts it through\n"
    " * fair_policy() as well.  Everything else about the class stays gated on\n"
    " * scx_enabled().\n"
    " */\n"
    "static inline int normal_policy(int policy)\n"
    "{\n"
    "#ifdef CONFIG_SCHED_CLASS_EXT\n"
    "\tif (policy == SCHED_EXT)\n"
    "\t\treturn true;\n"
    "#endif\n"
    "\treturn policy == SCHED_NORMAL;\n"
    "}\n"
    "\n"
    "static inline int fair_policy(int policy)\n"
    "{\n"
    "\treturn normal_policy(policy) || policy == SCHED_BATCH;\n"
    "}\n"
)


def _sched_ext_policy_valid_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (SCHED_H, _FAIR_POLICY_OLD, _FAIR_POLICY_NEW, True),
    ])
    return status, detail


# ---------------------------------------------------------------------------
# kernel/sched/core.c: the priority-range syscalls.
# ---------------------------------------------------------------------------

_PRIO_MAX_OLD = (
    "\tcase SCHED_DEADLINE:\n"
    "\tcase SCHED_NORMAL:\n"
    "\tcase SCHED_BATCH:\n"
    "\tcase SCHED_IDLE:\n"
    "\t\tret = 0;\n"
    "\t\tbreak;\n"
    "\t}\n"
    "\treturn ret;\n"
    "}\n"
    "\n"
    "/**\n"
    " * sys_sched_get_priority_min - return minimum RT priority.\n"
)

_PRIO_MAX_NEW = (
    "\tcase SCHED_DEADLINE:\n"
    "\tcase SCHED_NORMAL:\n"
    "\tcase SCHED_BATCH:\n"
    "\tcase SCHED_IDLE:\n"
    "\t/* sailboat_sched_ext_priority_range: the range a loader asks for\n"
    "\t * before setting the policy on a task. */\n"
    "\tcase SCHED_EXT:\n"
    "\t\tret = 0;\n"
    "\t\tbreak;\n"
    "\t}\n"
    "\treturn ret;\n"
    "}\n"
    "\n"
    "/**\n"
    " * sys_sched_get_priority_min - return minimum RT priority.\n"
)

_PRIO_MIN_OLD = (
    "\tcase SCHED_DEADLINE:\n"
    "\tcase SCHED_NORMAL:\n"
    "\tcase SCHED_BATCH:\n"
    "\tcase SCHED_IDLE:\n"
    "\t\tret = 0;\n"
    "\t}\n"
    "\treturn ret;\n"
    "}\n"
)

_PRIO_MIN_NEW = (
    "\tcase SCHED_DEADLINE:\n"
    "\tcase SCHED_NORMAL:\n"
    "\tcase SCHED_BATCH:\n"
    "\tcase SCHED_IDLE:\n"
    "\t/* sailboat_sched_ext_priority_range */\n"
    "\tcase SCHED_EXT:\n"
    "\t\tret = 0;\n"
    "\t}\n"
    "\treturn ret;\n"
    "}\n"
)


def _sched_ext_priority_range_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (CORE_C, _PRIO_MAX_OLD, _PRIO_MAX_NEW, True),
        (CORE_C, _PRIO_MIN_OLD, _PRIO_MIN_NEW, True),
    ])
    return status, detail


# ---------------------------------------------------------------------------
# kernel/bpf/bpf_struct_ops_types.h: the struct_ops value type.
# ---------------------------------------------------------------------------

_STRUCT_OPS_TYPES_OLD = (
    "#ifdef CONFIG_BPF_JIT\n"
    "#ifdef CONFIG_INET\n"
    "#include <net/tcp.h>\n"
    "BPF_STRUCT_OPS_TYPE(tcp_congestion_ops)\n"
    "#endif\n"
    "#endif\n"
)

_STRUCT_OPS_TYPES_NEW = (
    "#ifdef CONFIG_BPF_JIT\n"
    "#ifdef CONFIG_INET\n"
    "#include <net/tcp.h>\n"
    "BPF_STRUCT_OPS_TYPE(tcp_congestion_ops)\n"
    "#endif\n"
    "#ifdef CONFIG_SCHED_CLASS_EXT\n"
    "/*\n"
    " * sailboat_sched_ext_struct_ops_type: the value type a BPF struct_ops map\n"
    " * of type sched_ext_ops binds to.  bpf_##_name is the payload's\n"
    " * bpf_sched_ext_ops in kernel/sched/ext.c; this header is included four\n"
    " * times with BPF_STRUCT_OPS_TYPE defined differently, so one line here\n"
    " * produces the extern, the map value struct, the enum and the registry\n"
    " * entry.  Without it the BPF syscall has no sched_ext_ops type id and no\n"
    " * scheduler can be attached.\n"
    " */\n"
    "BPF_STRUCT_OPS_TYPE(sched_ext_ops)\n"
    "#endif\n"
    "#endif\n"
)


def _sched_ext_struct_ops_type_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (BPF_STRUCT_OPS_H, _STRUCT_OPS_TYPES_OLD, _STRUCT_OPS_TYPES_NEW, True),
    ])
    return status, detail


# ---------------------------------------------------------------------------
# kernel/sched/debug.c: the ext dump.
# ---------------------------------------------------------------------------

_DEBUGFS_OLD = (
    "\tdebugfs_create_file(\"debug\", 0444, debugfs_sched, NULL, &sched_debug_fops);\n"
    "\n"
    "\treturn 0;\n"
    "}\n"
    "late_initcall(sched_init_debug);\n"
)

_DEBUGFS_NEW = (
    "\tdebugfs_create_file(\"debug\", 0444, debugfs_sched, NULL, &sched_debug_fops);\n"
    "\n"
    "#ifdef CONFIG_SCHED_CLASS_EXT\n"
    "\t/*\n"
    "\t * sailboat_sched_ext_debugfs: the engine's own dump -- enabled state,\n"
    "\t * ops state, watchdog timestamp and the per-CPU/queue counters.\n"
    "\t * sched_ext_fops is defined in kernel/sched/ext.c and declared in\n"
    "\t * kernel/sched/ext.h, which kernel/sched/sched.h includes at its tail.\n"
    "\t */\n"
    "\tdebugfs_create_file(\"ext\", 0444, debugfs_sched, NULL, &sched_ext_fops);\n"
    "#endif\n"
    "\n"
    "\treturn 0;\n"
    "}\n"
    "late_initcall(sched_init_debug);\n"
)


def _sched_ext_debugfs_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (DEBUG_C, _DEBUGFS_OLD, _DEBUGFS_NEW, True),
    ])
    return status, detail


def build_groups(PatchGroup):
    """Return the four PatchGroup records, in registration order."""
    return [
        PatchGroup(
            "sched_ext_policy_valid",
            ("kernel/sched/sched.h: 6.6's normal_policy() is restored and "
             "fair_policy() builds on it, so SCHED_EXT is a valid policy -- the "
             "syscall's valid_policy() check accepts it and "
             "__setscheduler_params() gives the task its static priority and "
             "load weight.  This is the gate that made the class unreachable "
             "through Batch 59."),
            ["no upstream commit: 6.6's normal_policy()/fair_policy() split "
             "re-expressed on the 5.15 helper set"],
            [SCHED_H],
            _sched_ext_policy_valid_apply,
        ),
        PatchGroup(
            "sched_ext_priority_range",
            ("kernel/sched/core.c: sched_get_priority_max() and "
             "sched_get_priority_min() gain case SCHED_EXT, so a loader can ask "
             "for the priority range of the policy it is about to set."),
            ["no upstream commit: 6.6's SCHED_EXT cases in the two syscalls"],
            [CORE_C],
            _sched_ext_priority_range_apply,
        ),
        PatchGroup(
            "sched_ext_struct_ops_type",
            ("kernel/bpf/bpf_struct_ops_types.h gains "
             "BPF_STRUCT_OPS_TYPE(sched_ext_ops): this header is the 5.15 "
             "registry of struct_ops map value types, and without the entry the "
             "BPF syscall has no sched_ext_ops value type to load a scheduler "
             "against.  The engine's bpf_sched_ext_ops is what the macro "
             "expands to reference."),
            ["no upstream commit: the type entry that has to exist for "
             "sched_ext_ops to be bindable on the 5.15 struct_ops registry"],
            [BPF_STRUCT_OPS_H],
            _sched_ext_struct_ops_type_apply,
        ),
        PatchGroup(
            "sched_ext_debugfs",
            ("kernel/sched/debug.c: sched_init_debug() registers "
             "/sys/kernel/debug/sched/ext, the engine's own dump, through the "
             "payload's sched_ext_fops."),
            ["no upstream commit: 6.6's debugfs_create_file(\"ext\", ...) call "
             "site"],
            [DEBUG_C],
            _sched_ext_debugfs_apply,
        ),
    ]
