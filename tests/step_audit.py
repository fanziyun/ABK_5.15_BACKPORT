#!/usr/bin/env python3

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
    "Documentation/admin-guide/cgroup-v2.rst",
    "kernel/rcu/Kconfig",
    "kernel/rcu/tree_nocb.h",
    "mm/page_alloc.c",
    "mm/internal.h",
    "mm/oom_kill.c",
    "mm/vmscan.c",
    "mm/vmstat.c",
    "mm/memcontrol.c",
    "mm/workingset.c",
    "mm/swap.c",
    "include/linux/mm_inline.h",
    "include/linux/swap.h",
    "include/linux/cgroup-defs.h",
    "include/linux/cpuset.h",
    "include/linux/mmzone.h",
    "include/linux/memcontrol.h",
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
    "kernel/sched/cpufreq_schedutil.c",
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
    "mm/compaction.c",
    "arch/arm64/configs/gki_defconfig",
    "mm/Kconfig",
    "mm/khugepaged.c",
    "mm/madvise.c",
    "include/linux/huge_mm.h",
    "include/uapi/asm-generic/mman-common.h",
    "drivers/gpu/drm/drm_atomic_helper.c",
    "mm/readahead.c",
    "include/trace/hooks/mm.h",
    "drivers/android/vendor_hooks.c",
    "kernel/pid.c",
    "mm/slub.c",
    "mm/huge_memory.c",
    "mm/memory.c",
    "mm/swap_state.c",
    "include/linux/blkdev.h",
    "block/blk-core.c",
    "block/blk-mq-sched.c",
    "block/blk-sysfs.c",
    "block/blk.h",
    "block/elevator.c",
    "block/mq-deadline.c",
    "block/bfq-iosched.c",
    "block/kyber-iosched.c",
    "include/linux/sched/nohz.h",
    "include/linux/tick.h",
    "kernel/time/tick-sched.c",
    "kernel/sched/idle.c",
    "kernel/bpf/helpers.c",
    "mm/filemap.c",
    "arch/arm64/include/asm/pgtable.h",
    "mm/truncate.c",
    "include/linux/mm.h",
    "arch/arm64/include/asm/percpu.h",
    "fs/fuse/file.c",
    "fs/erofs/compress.h",
    "fs/erofs/decompressor.c",
    "fs/erofs/decompressor_lzma.c",
    "fs/erofs/zdata.c",
    "fs/erofs/zdata.h",
    "mm/page_io.c",
    "fs/proc/task_mmu.c",
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


C_SUFFIXES = (".c", ".h", ".S")


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
        group_statuses.setdefault(current_group[0], []).extend(
            (rel, step_status)
            for (rel, _old, _new, _required), (_rel, step_status)
            in zip(steps, results)
        )
        return status, results, detail

    rebound = [
        mod for mod in list(sys.modules.values())
        if mod is not module and getattr(mod, "apply_steps", None) is original
    ]
    module.apply_steps = wrapper
    for _rb in rebound:
        _rb.apply_steps = wrapper
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
        for _rb in rebound:
            _rb.apply_steps = original

    steps = [s for key in group_steps for s in group_steps[key]]

    pristine_texts = {rel: common.read_text(pristine_root / rel) for rel in AUDIT_FILES}
    for key, key_steps in group_steps.items():
        if key in pre_applied or key in debts:
            continue
        for rel, _old, new, _required in key_steps:
            if contains_block(pristine_texts[rel], new):
                fail(f"{name}/{key}: replacement block already exists in pristine "
                     f"{rel}; anchor must be unique enough that replace_once really "
                     f"applies:\n{new[:120]!r}")

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

    for rel in sorted({rel for rel, _o, _n, _r in steps
                       if rel.endswith(C_SUFFIXES)}):
        after_text = common.read_text(tree / rel)
        for label, delta, why in STRUCTURE_CHECKS:
            before = delta(pristine_texts[rel])
            after = delta(after_text)
            if before != after:
                fail(f"{name}: {label} balance changed in {rel} "
                     f"({before} -> {after}); {why}")

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
    raise SystemExit(
        f"could not read SUBLEVEL from {makefile}; "
        f"set ABK_TEST_SUB_LEVEL=<sublevel> to audit anyway"
    )


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
