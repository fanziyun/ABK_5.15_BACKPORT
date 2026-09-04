#!/usr/bin/env python3
"""Per-step audit of every PatchGroup against a pristine reference tree.

Group-level statuses hide three anchor bugs that only show up at compile time:

1. A step whose replacement block already exists somewhere in the pristine
   file.  ``replace_once`` checks the replacement first (idempotency), so the
   step is silently reported ``already_present`` and the real edit never
   happens, while the group still reports "applied".  Every step of a group
   that is supposed to apply on the target baseline must therefore be
   ``applied`` -- never ``already_present``.  Groups whose upstream commit the
   baseline already carries are exempt (see ``tests/sublevel_matrix.py``): for
   those, ``already_present`` is the correct answer, not a trap.
2. The same short-circuit created *inside* the group: an earlier step whose
   replacement text contains a later step's replacement text.  Checking the
   pristine file cannot catch it, so the audit consumes the per-step statuses
   ``apply_steps`` returns and fails on any ``already_present`` step of a
   must-apply group.
3. A replacement that unbalances the ``/* ... */`` comment structure (e.g. a
   new block that starts with the comment terminator), which turns every
   following line into code and breaks the whole translation unit.

The audit copies the files the groups touch into a disposable tree, records
each step by wrapping ``apply_steps`` in both child modules, and asserts:

- every group and every individual step reports ``applied`` on the pristine
  tree, except for groups whose upstream commit the target baseline already
  carries (``tests/sublevel_matrix.py``), which must report
  ``already_present``;
- every step's replacement block is absent from the pristine file (both LF
  and CRLF forms) -- checked only for groups that must really apply;
- no step of a must-apply group reports ``already_present`` at run time;
- the open/close comment, brace and ``#if``/``#endif`` delta of each touched
  file is unchanged;
- a second pass over the patched tree is fully ``already_present`` and leaves
  every file byte-identical.

Usage:
  python tests/step_audit.py /path/to/android13-5.15-common-kernel-tree
(or set AUDIT_SOURCE_TREE; a tree at ../linux-common-android13-5.15 is picked
up automatically).  The sublevel is read from the tree's Makefile; override
with ABK_TEST_SUB_LEVEL.  Fetch a non-default baseline with
``bash tests/fetch_sublevel_tree.sh <branch> <outdir>``.
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_DIR / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import abk_common as common  # noqa: E402
import sublevel_matrix  # noqa: E402
from abk_backport_engine import GraftContext  # noqa: E402
import abk_stable_core  # noqa: E402
import abk_stable_perf  # noqa: E402
import abk_stable_display  # noqa: E402

SUB_LEVEL = sublevel_matrix.DEFAULT_SUB_LEVEL

AUDIT_FILES = [
    "fs/file.c",
    "Documentation/admin-guide/kernel-parameters.txt",
    "kernel/rcu/Kconfig",
    "kernel/rcu/tree_nocb.h",
    "mm/page_alloc.c",
    "mm/internal.h",
    "mm/oom_kill.c",
    "mm/vmscan.c",
    "mm/memcontrol.c",
    "include/linux/swap.h",
    "include/linux/cgroup-defs.h",
    "include/linux/cpuset.h",
    "include/linux/mmzone.h",
    "include/linux/randomize_kstack.h",
    "include/linux/sched.h",
    "include/linux/psi_types.h",
    "include/linux/psi.h",
    "include/trace/hooks/dtask.h",
    "include/trace/hooks/rwsem.h",
    "kernel/cgroup/cgroup-internal.h",
    "kernel/cgroup/cgroup.c",
    "kernel/cgroup/cpuset.c",
    "kernel/sched/sched.h",
    "kernel/sched/core.c",
    "kernel/sched/fair.c",
    "kernel/sched/rt.c",
    "kernel/sched/features.h",
    "kernel/sched/stats.h",
    "kernel/sched/psi.c",
    "kernel/fork.c",
    "kernel/locking/semaphore.c",
    "kernel/locking/mutex.c",
    "kernel/locking/rwsem.c",
    "init/main.c",
    "net/core/sock.c",
    "block/blk-mq.c",
    "drivers/block/zram/Kconfig",
    "drivers/block/zram/zram_drv.h",
    "drivers/block/zram/zram_drv.c",
    "mm/zsmalloc.c",
    "include/linux/zsmalloc.h",
    # Batch 8: fallback claimability cleanup updates compaction callers.
    "mm/compaction.c",
    # Batch 6: the defconfig lane plus the zsmalloc and MADV_COLLAPSE grafts.
    "arch/arm64/configs/gki_defconfig",
    "mm/Kconfig",
    "mm/khugepaged.c",
    "mm/madvise.c",
    "include/linux/huge_mm.h",
    "include/uapi/asm-generic/mman-common.h",
    # Batch 7: the 5.15.185 drm valid-clones revert.
    "drivers/gpu/drm/drm_atomic_helper.c",
]

CHILD_MODULES = [
    ("stable_backport_core", abk_stable_core),
    ("stable_perf_backport", abk_stable_perf),
    ("stable_display_fix", abk_stable_display),
]


def fail(msg):
    raise SystemExit(f"AUDIT FAIL: {msg}")


def make_tree(source, work):
    root = work / "common"
    for rel in AUDIT_FILES:
        src = source / rel
        if not src.is_file():
            fail(f"reference tree is missing {rel}")
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return root


def check_fixture_coverage():
    """Every file a group declares must be part of the audit fixture.

    Otherwise the group runs against a missing file, degrades to a shape error
    and the failure reads like an anchor problem instead of the fixture gap it
    actually is.
    """
    for name, module in CHILD_MODULES:
        for group in module.PATCH_GROUPS:
            for rel in group.files:
                if rel not in AUDIT_FILES:
                    fail(f"{name}/{group.key} touches {rel}, which is not in "
                         "AUDIT_FILES; add it here and to "
                         "tests/fetch_sublevel_tree.sh FETCH_FILES")


def new_ctx(root):
    return GraftContext(root, SUB_LEVEL, "android13-5.15",
                        defconfig=str(root / "arch/arm64/configs/gki_defconfig"))


def record_steps(module, sink, status_sink=None):
    """Wrap module.apply_steps so every step tuple is captured per group.

    ``status_sink`` additionally collects the per-step ``(rel, status)`` pairs
    ``apply_steps`` returned, which is what trap 1b is asserted against.
    """
    original = module.apply_steps

    def wrapper(ctx, steps):
        sink.extend(steps)
        status, results, detail = original(ctx, steps)
        if status_sink is not None:
            status_sink.extend(
                (rel, step_status)
                for (rel, _old, _new, _required), (_rel, step_status)
                in zip(steps, results)
            )
        return status, results, detail

    module.apply_steps = wrapper
    return original


def comment_delta(text):
    return text.count("/*") - text.count("*/")


def brace_delta(text):
    return text.count("{") - text.count("}")


def ifdef_delta(text):
    opens = len(re.findall(r"^\s*#\s*(?:if|ifdef|ifndef)\b", text, re.M))
    closes = len(re.findall(r"^\s*#\s*endif\b", text, re.M))
    return opens - closes


STRUCTURE_CHECKS = (
    ("comment", comment_delta, "a replacement broke a /* */ pair"),
    ("brace", brace_delta, "a replacement dropped or added a brace"),
    ("ifdef", ifdef_delta, "a replacement unbalanced an #if/#endif pair"),
)


def contains_block(text, block):
    lf = block.replace("\r\n", "\n")
    return lf in text or lf.replace("\n", "\r\n") in text


def run_child_groups(module, ctx, child_id, pass_name):
    """Run every group, asserting each landed the status the baseline implies."""
    pre_applied = sublevel_matrix.pre_applied(SUB_LEVEL, child_id)
    debts = sublevel_matrix.debt(SUB_LEVEL, child_id)
    statuses = {}
    for group in module.PATCH_GROUPS:
        result = group.run(ctx)
        statuses[group.key] = result["status"]
        if pass_name == "pristine":
            expected = debts.get(group.key) or (
                "already_present" if group.key in pre_applied else "applied")
        else:
            # An "applied"-valued debt (a partial-apply drift) becomes
            # already_present on the second pass.  True degradations stay.
            want = debts.get(group.key)
            expected = "already_present" if want in (None, "applied") else want
        if result["status"] != expected:
            fail(f"{child_id}/{group.key} reported {result['status']} on the "
                 f"{pass_name} 5.15.{SUB_LEVEL} tree, expected {expected}; either "
                 f"the anchors drifted or tests/sublevel_matrix.py is stale")
    return statuses


def audit_child(name, module, pristine_root, work):
    group_steps = {}
    group_statuses = {}
    current_group = [None]
    original = module.apply_steps

    def wrapper(ctx, steps, _o=original):
        status, results, detail = _o(ctx, steps)
        group_steps.setdefault(current_group[0], []).extend(steps)
        # apply_steps returns one (rel, status) per evaluated step, in order,
        # and stops early on a required miss -- zip truncates to what ran.
        group_statuses.setdefault(current_group[0], []).extend(
            (rel, step_status)
            for (rel, _old, _new, _required), (_rel, step_status)
            in zip(steps, results)
        )
        return status, results, detail

    module.apply_steps = wrapper
    pre_applied = sublevel_matrix.pre_applied(SUB_LEVEL, name)
    debts = sublevel_matrix.debt(SUB_LEVEL, name)
    try:
        tree = work / name
        shutil.copytree(pristine_root, tree)
        ctx = new_ctx(tree)
        for group in module.PATCH_GROUPS:
            current_group[0] = group.key
            result = group.run(ctx)
            expected = debts.get(group.key) or (
                "already_present" if group.key in pre_applied else "applied")
            if result["status"] != expected:
                fail(f"{name}/{group.key} reported {result['status']} on the pristine "
                     f"5.15.{SUB_LEVEL} tree, expected {expected}; either the anchors "
                     f"drifted or tests/sublevel_matrix.py is stale")
    finally:
        module.apply_steps = original

    steps = [s for key in group_steps for s in group_steps[key]]

    # Trap 1: a replacement block that pre-exists in the pristine file would
    # make replace_once short-circuit to already_present forever.  Groups the
    # baseline already carries are legitimately already_present, so only audit
    # the ones that are supposed to really apply.
    pristine_texts = {rel: common.read_text(pristine_root / rel) for rel in AUDIT_FILES}
    for key, key_steps in group_steps.items():
        if key in pre_applied or key in debts:
            continue
        for rel, _old, new, _required in key_steps:
            if contains_block(pristine_texts[rel], new):
                fail(f"{name}/{key}: replacement block already exists in pristine "
                     f"{rel}; anchor must be unique enough that replace_once really "
                     f"applies:\n{new[:120]!r}")

    # Trap 1b: the same short-circuit, but created *during* the group by an
    # earlier step whose replacement contains a later step's replacement text.
    # Trap 1 cannot see it -- it only inspects the pristine file -- so this
    # consumes the per-step statuses apply_steps actually returned.  A step of
    # a must-apply group that reports already_present means the real edit never
    # landed while the group still reported "applied" (the MADV_COLLAPSE
    # khugepaged_scan_file() signature reached CI that way).
    for key, key_statuses in group_statuses.items():
        if key in pre_applied or key in debts:
            continue
        skipped = [rel for rel, status in key_statuses
                   if status == "already_present"]
        if skipped:
            fail(f"{name}/{key}: {len(skipped)} step(s) reported already_present on "
                 f"the pristine 5.15.{SUB_LEVEL} tree ({sorted(set(skipped))}); the "
                 "group still reports applied, so the edit was silently skipped -- "
                 "check whether an earlier step's replacement text contains this "
                 "step's replacement text")

    # Trap 2: comment, brace and #ifdef structure must stay balanced per file.
    for rel in sorted({rel for rel, _o, _n, _r in steps}):
        after_text = common.read_text(tree / rel)
        for label, delta, why in STRUCTURE_CHECKS:
            before = delta(pristine_texts[rel])
            after = delta(after_text)
            if before != after:
                fail(f"{name}: {label} balance changed in {rel} "
                     f"({before} -> {after}); {why}")

    # Idempotency: second pass over the patched tree, byte-identical result.
    patched_files = sorted({rel for rel, _o, _n, _r in steps})
    patched_bytes = {rel: (tree / rel).read_bytes() for rel in patched_files}
    ctx2 = new_ctx(tree)
    statuses2 = run_child_groups(module, ctx2, name, "patched")
    debts = sublevel_matrix.debt(SUB_LEVEL, name)
    non_idempotent = [
        k for k, s in statuses2.items()
        if s != "already_present" and debts.get(k) != s
    ]
    if non_idempotent:
        fail(f"{name}: second pass was not idempotent: "
             f"{[k + '=' + statuses2[k] for k in non_idempotent]}")
    for rel, blob in patched_bytes.items():
        if (tree / rel).read_bytes() != blob:
            fail(f"{name}: second pass rewrote {rel}")
    skipped = f", {len(pre_applied)} group(s) already in baseline" if pre_applied else ""
    print(f"  {name}: {len(steps)} steps audited{skipped}, second pass idempotent")


def audit_fdtable_on_suite_shape(module, source, work):
    """Audit the fdtable group's composed variant over the suite-first shape.

    Builds ABK_ABI_PATCH_SUITE's fallback alloc_fdtable() on top of the real
    reference fs/file.c (same deltas as the unit-test fixture), then runs the
    group: every step must be ``applied``, the replacement blocks must not
    pre-exist, comment balance must hold, and a second pass must be a
    byte-identical no-op.

    The suite builds its fallback out of the pre-5.15.191 ``alloc_fdtable()``
    body, so this audit only applies to a baseline that still carries that
    shape (167, 178).  From 5.15.191 the tree is already in the upstream form
    and the suite takes its adapt branch instead, so there is nothing to
    compose and the audit is skipped.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from stable_5_15_test import SUITE_HELPER_TAIL, suite_fallback_deltas

    tree = work / "suite_first"
    (tree / "fs").mkdir(parents=True, exist_ok=True)
    pristine_text = common.read_text(source / "fs" / "file.c")
    if "alloc_fdtable(unsigned int slots_wanted)" in pristine_text:
        print("  fdtable-on-suite-shape: skipped, baseline is already in the "
              "upstream 5.15.191 shape (the suite adapts instead of rewriting)")
        return
    suite_text = suite_fallback_deltas(pristine_text) + SUITE_HELPER_TAIL
    for marker in (
        "unsigned int slots_wanted = abk_fdtable_slots_wanted(nr);",
        "nr = ALIGN(slots_wanted, BITS_PER_LONG);",
        "if (unlikely(nr > INT_MAX / sizeof(struct file *)))\n\t\treturn NULL;",
    ):
        if marker not in suite_text:
            fail(f"suite-fallback delta did not land on the real fs/file.c: {marker!r}")
    (tree / "fs" / "file.c").write_bytes(suite_text.encode("utf-8"))

    steps = []
    step_statuses = []
    original = record_steps(module, steps, step_statuses)
    try:
        ctx = new_ctx(tree)
        group = next(g for g in module.PATCH_GROUPS if g.key == "fdtable_alloc_conventions")
        result = group.run(ctx)
        if result["status"] != "applied":
            fail(f"fdtable composed variant degraded on the suite shape: "
                 f"{result['status']} ({result['detail']})")
        # The 5.15.195 replace_fd() fix is its own group and must still land on
        # top of the composed shape.
        errno_group = next(g for g in module.PATCH_GROUPS
                           if g.key == "fdtable_replace_fd_errno")
        errno_result = errno_group.run(ctx)
        if errno_result["status"] != "applied":
            fail(f"replace_fd errno group degraded on the composed suite shape: "
                 f"{errno_result['status']} ({errno_result['detail']})")
    finally:
        module.apply_steps = original

    for rel, old, new, _required in steps:
        if rel != "fs/file.c":
            continue
        if contains_block(suite_text, new):
            fail("fdtable composed variant: replacement block already exists in the "
                 f"suite-shaped file:\n{new[:120]!r}")
    skipped = [rel for rel, status in step_statuses if status == "already_present"]
    if skipped:
        fail(f"fdtable composed variant: {len(skipped)} step(s) reported "
             f"already_present on the suite-shaped tree ({sorted(set(skipped))}); "
             "the group still reports applied, so the edit was silently skipped")
    after_text = common.read_text(tree / "fs" / "file.c")
    for label, delta, why in STRUCTURE_CHECKS:
        if delta(suite_text) != delta(after_text):
            fail(f"fdtable composed variant: {label} balance changed on fs/file.c "
                 f"({delta(suite_text)} -> {delta(after_text)}); {why}")

    patched_bytes = (tree / "fs" / "file.c").read_bytes()
    ctx2 = new_ctx(tree)
    result2 = group.run(ctx2)
    if result2["status"] != "already_present":
        fail(f"fdtable composed variant not idempotent: {result2['status']}")
    errno_result2 = errno_group.run(ctx2)
    if errno_result2["status"] != "already_present":
        fail(f"replace_fd errno group not idempotent on the composed shape: "
             f"{errno_result2['status']}")
    if (tree / "fs" / "file.c").read_bytes() != patched_bytes:
        fail("fdtable composed variant: second pass rewrote fs/file.c")
    print(f"  fdtable-on-suite-shape: {len(steps)} steps audited, second pass idempotent")


def detect_sub_level(source):
    """Read SUBLEVEL from the reference tree's Makefile; ABK_TEST_SUB_LEVEL wins."""
    import os

    override = os.environ.get("ABK_TEST_SUB_LEVEL", "").strip()
    if override:
        return override
    makefile = source / "Makefile"
    if makefile.is_file():
        for line in common.read_text(makefile).splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "SUBLEVEL" and parts[1] == "=":
                return parts[2]
    return sublevel_matrix.DEFAULT_SUB_LEVEL


def main():
    import os

    global SUB_LEVEL

    if len(sys.argv) > 1:
        source = Path(sys.argv[1]).resolve()
    else:
        env = os.environ.get("AUDIT_SOURCE_TREE", "")
        sibling = MODULE_DIR.parent / "linux-common-android13-5.15"
        source = Path(env).resolve() if env else sibling
    if not source.is_dir():
        raise SystemExit(f"usage: python tests/step_audit.py /path/to/kernel-tree "
                         f"(got {source})")

    SUB_LEVEL = detect_sub_level(source)
    if SUB_LEVEL not in sublevel_matrix.SUPPORTED:
        raise SystemExit(
            f"no expectation matrix for 5.15.{SUB_LEVEL}; supported: "
            f"{', '.join(sublevel_matrix.SUPPORTED)}. Add an entry to "
            f"tests/sublevel_matrix.py before auditing this baseline."
        )

    with tempfile.TemporaryDirectory(prefix="abk515_audit_") as tmp:
        work = Path(tmp)
        check_fixture_coverage()
        pristine = make_tree(source, work)
        print(f"auditing {sum(len(m.PATCH_GROUPS) for _n, m in CHILD_MODULES)} groups "
              f"against {source} (5.15.{SUB_LEVEL})")
        for name, module in CHILD_MODULES:
            audit_child(name, module, pristine, work)
        audit_fdtable_on_suite_shape(abk_stable_core, source, work)
    print(f"STEP AUDIT OK (5.15.{SUB_LEVEL})")


if __name__ == "__main__":
    main()
