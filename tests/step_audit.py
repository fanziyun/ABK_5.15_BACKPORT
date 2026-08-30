#!/usr/bin/env python3
"""Per-step audit of every PatchGroup against a pristine reference tree.

Group-level statuses hide two anchor bugs that only show up at compile time:

1. A step whose replacement block already exists somewhere in the pristine
   file.  ``replace_once`` checks the replacement first (idempotency), so the
   step is silently reported ``already_present`` and the real edit never
   happens, while the group still reports "applied".  On a pristine 5.15.167
   tree every single step must therefore be ``applied`` -- never
   ``already_present``.
2. A replacement that unbalances the ``/* ... */`` comment structure (e.g. a
   new block that starts with the comment terminator), which turns every
   following line into code and breaks the whole translation unit.

The audit copies the files the groups touch into a disposable tree, records
each step by wrapping ``apply_steps`` in both child modules, and asserts:

- every group and every individual step reports ``applied`` on the pristine
  tree;
- every step's replacement block is absent from the pristine file (both LF
  and CRLF forms);
- the open/close comment delta of each touched file is unchanged;
- a second pass over the patched tree is fully ``already_present`` and leaves
  every file byte-identical.

Usage:
  python tests/step_audit.py /path/to/android13-5.15-common-kernel-tree
(or set AUDIT_SOURCE_TREE; a tree at ../linux-common-android13-5.15 is picked
up automatically)
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_DIR / "scripts"))

import abk_common as common  # noqa: E402
from abk_backport_engine import GraftContext  # noqa: E402
import abk_stable_core  # noqa: E402
import abk_stable_perf  # noqa: E402

AUDIT_FILES = [
    "fs/file.c",
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
]

CHILD_MODULES = [
    ("stable_backport_core", abk_stable_core),
    ("stable_perf_backport", abk_stable_perf),
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


def new_ctx(root):
    return GraftContext(root, "167", "android13-5.15")


def record_steps(module, sink):
    """Wrap module.apply_steps so every step tuple is captured."""
    original = module.apply_steps

    def wrapper(ctx, steps):
        sink.extend(steps)
        return original(ctx, steps)

    module.apply_steps = wrapper
    return original


def comment_delta(text):
    return text.count("/*") - text.count("*/")


def contains_block(text, block):
    lf = block.replace("\r\n", "\n")
    return lf in text or lf.replace("\n", "\r\n") in text


def run_child_groups(module, ctx):
    statuses = {}
    for group in module.PATCH_GROUPS:
        result = group.run(ctx)
        statuses[group.key] = result["status"]
        if result["status"] not in ("applied", "already_present"):
            fail(f"{module.__name__}/{group.key} degraded on the pristine tree: "
                 f"{result['status']} ({result['detail']})")
    return statuses


def audit_child(name, module, pristine_root, work):
    steps = []
    original = record_steps(module, steps)
    try:
        tree = work / name
        shutil.copytree(pristine_root, tree)
        ctx = new_ctx(tree)
        statuses = run_child_groups(module, ctx)
    finally:
        module.apply_steps = original

    # Trap 1: a replacement block that pre-exists in the pristine file would
    # make replace_once short-circuit to already_present forever.
    pristine_texts = {rel: common.read_text(pristine_root / rel) for rel in AUDIT_FILES}
    for rel, old, new, _required in steps:
        if contains_block(pristine_texts[rel], new):
            fail(f"{name}: replacement block already exists in pristine {rel}; "
                 f"anchor must be unique enough that replace_once really applies:\n{new[:120]!r}")

    # Trap 2: comment structure must stay balanced per file.
    for rel in sorted({rel for rel, _o, _n, _r in steps}):
        before = comment_delta(pristine_texts[rel])
        after = comment_delta(common.read_text(tree / rel))
        if before != after:
            fail(f"{name}: comment balance changed in {rel} "
                 f"({before} -> {after}); a replacement broke a /* */ pair")

    # Idempotency: second pass over the patched tree, byte-identical result.
    patched_files = sorted({rel for rel, _o, _n, _r in steps})
    patched_bytes = {rel: (tree / rel).read_bytes() for rel in patched_files}
    ctx2 = new_ctx(tree)
    statuses2 = run_child_groups(module, ctx2)
    if any(s != "already_present" for s in statuses2.values()):
        fail(f"{name}: second pass was not idempotent: {statuses2}")
    for rel, blob in patched_bytes.items():
        if (tree / rel).read_bytes() != blob:
            fail(f"{name}: second pass rewrote {rel}")
    print(f"  {name}: {len(steps)} steps audited, second pass idempotent")


def audit_fdtable_on_suite_shape(module, source, work):
    """Audit the fdtable group's composed variant over the suite-first shape.

    Builds ABK_ABI_PATCH_SUITE's fallback alloc_fdtable() on top of the real
    reference fs/file.c (same deltas as the unit-test fixture), then runs the
    group: every step must be ``applied``, the replacement blocks must not
    pre-exist, comment balance must hold, and a second pass must be a
    byte-identical no-op.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from stable_5_15_test import SUITE_HELPER_TAIL, suite_fallback_deltas

    tree = work / "suite_first"
    (tree / "fs").mkdir(parents=True, exist_ok=True)
    pristine_text = common.read_text(source / "fs" / "file.c")
    suite_text = suite_fallback_deltas(pristine_text) + SUITE_HELPER_TAIL
    for marker in (
        "unsigned int slots_wanted = abk_fdtable_slots_wanted(nr);",
        "nr = ALIGN(slots_wanted, BITS_PER_LONG);",
        "if (unlikely(nr > INT_MAX / sizeof(struct file *)))\n\t\treturn NULL;",
    ):
        if marker not in suite_text:
            fail(f"suite-fallback delta did not land on the real fs/file.c: {marker!r}")
    (tree / "fs" / "file.c").write_bytes(suite_text.encode("utf-8"))
    suite_balance = comment_delta(suite_text)

    steps = []
    original = record_steps(module, steps)
    try:
        ctx = new_ctx(tree)
        group = next(g for g in module.PATCH_GROUPS if g.key == "fdtable_alloc_conventions")
        result = group.run(ctx)
        if result["status"] != "applied":
            fail(f"fdtable composed variant degraded on the suite shape: "
                 f"{result['status']} ({result['detail']})")
    finally:
        module.apply_steps = original

    for rel, old, new, _required in steps:
        if rel != "fs/file.c":
            continue
        if contains_block(suite_text, new):
            fail("fdtable composed variant: replacement block already exists in the "
                 f"suite-shaped file:\n{new[:120]!r}")
    after_text = common.read_text(tree / "fs" / "file.c")
    if comment_delta(after_text) != suite_balance:
        fail("fdtable composed variant: comment balance changed on fs/file.c")

    patched_bytes = (tree / "fs" / "file.c").read_bytes()
    ctx2 = new_ctx(tree)
    result2 = group.run(ctx2)
    if result2["status"] != "already_present":
        fail(f"fdtable composed variant not idempotent: {result2['status']}")
    if (tree / "fs" / "file.c").read_bytes() != patched_bytes:
        fail("fdtable composed variant: second pass rewrote fs/file.c")
    print(f"  fdtable-on-suite-shape: {len(steps)} steps audited, second pass idempotent")


def main():
    import os

    if len(sys.argv) > 1:
        source = Path(sys.argv[1]).resolve()
    else:
        env = os.environ.get("AUDIT_SOURCE_TREE", "")
        sibling = MODULE_DIR.parent / "linux-common-android13-5.15"
        source = Path(env).resolve() if env else sibling
    if not source.is_dir():
        raise SystemExit(f"usage: python tests/step_audit.py /path/to/kernel-tree "
                         f"(got {source})")

    with tempfile.TemporaryDirectory(prefix="abk515_audit_") as tmp:
        work = Path(tmp)
        pristine = make_tree(source, work)
        print(f"auditing {sum(len(m.PATCH_GROUPS) for _n, m in CHILD_MODULES)} groups "
              f"against {source}")
        for name, module in CHILD_MODULES:
            audit_child(name, module, pristine, work)
        audit_fdtable_on_suite_shape(abk_stable_core, source, work)
    print("STEP AUDIT OK")


if __name__ == "__main__":
    main()
