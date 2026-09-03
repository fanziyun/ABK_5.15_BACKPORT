#!/usr/bin/env python3
"""Implementation audit: no phantom grafts, no missing feature content.

step_audit.py proves every anchor lands with correct structure and
idempotency; this companion proves the *content* of a graft is real:

- a group that reports applied/partial must have changed at least one file
  (content diff, not path-set diff: several groups rewrite the same file);
- the 6.1/6.6-origin groups must leave their headline feature symbols in the
  patched tree;
- groups whose upstream shape is the whole point (zsmalloc chain sizing,
  MADV_COLLAPSE) must leave their behaviour-visible symbols behind.

Everything runs against a disposable copy of the source tree: the reference
trees are never written to.

Usage:
  python tests/implementation_audit.py /path/to/android13-5.15-tree
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_DIR / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import abk_stable_core  # noqa: E402
import abk_stable_perf  # noqa: E402
import abk_stable_display  # noqa: E402
from abk_backport_engine import GraftContext  # noqa: E402

MARKER = "ABK stable_515_backport:"

# Feature content that must survive into the patched text wherever the group
# reports applied.  Keyed as "child:group".
REQUIRED_CONTENT = {
    "core:zram_recompression": [
        "zram_recompress", "recompress_store", "mark_idle",
        "num_active_comps", "zs_lookup_class_index",
        # Semantic checks to prevent empty implementation regression:
        "for (prio = 0; prio < ZRAM_MAX_COMPS; prio++)",  # disksize_store loop
        "zram_set_priority(zram, index, 0)",  # zram_free_page priority reset
    ],
    "core:memcg_memory_reclaim": ["memory.reclaim", "MEMCG_RECLAIM_MAY_SWAP"],
    "core:zsmalloc_chain_size": [
        "calculate_zspage_chain_size", "ZSMALLOC_CHAIN_SIZE", "is_power_of_2",
    ],
    "core:madvise_collapse": [
        "madvise_collapse", "MADV_COLLAPSE",
        "khugepaged_scan_pmd(mm, vma, addr, &hpage, &result)",
        "khugepaged_scan_file(mm, file, pgoff, &hpage, &result)",
        # Both scan_file definitions must have moved to the out-parameter form;
        # the CONFIG_SHMEM-off stub is deliberately wrapped differently so it
        # cannot pre-create this text (see abk_stable_core.py).
        "struct file *file, pgoff_t start, struct page **hpage,\n\t\tint *res)",
        "pgoff_t start, struct page **hpage, int *res)",
        # 5.15 hugepage_vma_revalidate() returns 0 on success, so the graft must
        # test it as a plain scan code, not with 6.1's `!= SCAN_SUCCEED`
        # (SCAN_SUCCEED is 1 here, so that inverts the success test).
        "result = hugepage_vma_revalidate(mm, addr, &vma);\n\t\t\tif (result) {",
    ],
    "perf:psi_trigger_kernfs_polling": ["psi_trigger_ext", "pending_event"],
    "perf:psi_irq_tracking": ["PSI_IRQ"],
    "perf:sched_lazy_preemption_hooks": ["resched_curr_lazy"],
}

# Removal grafts: content that must NOT survive into the patched text wherever
# the group reports applied.  Keyed as "child:group".
REQUIRED_ABSENT = {
    "display:drm_valid_clones_revert": [
        "drm_atomic_check_valid_clones",
        "drm_atomic_check_valid_clones(state, crtc)",
    ],
}

# Function-scoped assertions.  REQUIRED_CONTENT above is whole-file substring
# matching, which cannot tell *which* function a graft landed in -- a swapped
# pair of anchors satisfies it perfectly.  These entries slice out one function
# body and assert on that slice, so a hunk landing in the neighbouring function
# fails the audit.  Keyed as "child:group" -> list of
# (rel, function_name, must_contain, must_not_contain).
REQUIRED_IN_FUNCTION = {
    "core:zram_recompression": [
        # The read path must delegate to the shared helper, not carry its own
        # copy of the decompress logic (two copies is how the get/put anchors
        # got swapped in the first place).
        ("drivers/block/zram/zram_drv.c", "__zram_bvec_read",
         ["zram_read_from_zspool(zram, page, index)"],
         ["zcomp_decompress", "zcomp_stream_get"]),
        # The helper selects the comp by the slot's stored priority.
        ("drivers/block/zram/zram_drv.c", "zram_read_from_zspool",
         ["prio = zram_get_priority(zram, index)",
          "zcomp_stream_get(zram->comps[prio])",
          "zcomp_stream_put(zram->comps[prio])"],
         ["zram->comp)"]),
        # The write path always compresses with the primary comp.
        ("drivers/block/zram/zram_drv.c", "__zram_bvec_write",
         ["zcomp_stream_get(zram->comps[ZRAM_PRIMARY_COMP])"],
         ["zram_get_priority", "zram->comp)"]),
        # Secondary comps must actually be created, or every recompress path
        # short-circuits on a NULL comps[prio] and the group is a no-op.
        ("drivers/block/zram/zram_drv.c", "disksize_store",
         ["for (prio = 0; prio < ZRAM_MAX_COMPS; prio++)",
          "zram->comps[prio] = comp", "zram->num_active_comps++",
          "out_free_comps"],
         []),
        # A reused slot must not keep a stale comp priority: the next write
        # compresses with the primary comp, so a stale priority would decompress
        # through the wrong algorithm.
        ("drivers/block/zram/zram_drv.c", "zram_free_page",
         ["zram_set_priority(zram, index, 0)",
          "zram_clear_flag(zram, index, ZRAM_INCOMPRESSIBLE)"],
         []),
    ],
}


def function_body(text, name):
    """Return the text of `name`'s definition, or None.

    Matches a line whose last token before '(' is `name` at column 0 (kernel
    style puts the return type on the same line), then runs to the first
    line-initial '}'.  Returns every matching definition joined, so a symbol
    with an #ifdef/#else pair of definitions is checked as a whole.
    """
    bodies = []
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if line.startswith((" ", "\t", "#", "*", "/")) or f"{name}(" not in line:
            continue
        head = line.split(f"{name}(")[0]
        if head and not head.endswith(("*", " ")):
            continue
        # Look ahead to find the opening brace (may be several lines after signature)
        brace_start = None
        for scan in range(index, min(index + 20, len(lines))):
            if lines[scan].rstrip().endswith(";"):
                break  # forward declaration
            if lines[scan].lstrip().startswith("{"):
                brace_start = scan
                break
        if brace_start is None:
            continue
        # Now find the closing brace
        for end in range(brace_start + 1, len(lines)):
            if lines[end].startswith(("}", "};")):
                bodies.append("\n".join(lines[index:end + 1]))
                break
    return "\n".join(bodies) if bodies else None


def fail(msg):
    print(f"AUDIT FAIL: {msg}")
    sys.exit(1)


def run_tree(source):
    src = Path(source)
    groups = list(abk_stable_core.PATCH_GROUPS) + list(
        abk_stable_perf.PATCH_GROUPS) + list(abk_stable_display.PATCH_GROUPS)
    all_files = sorted({f for g in groups for f in g.files})

    with tempfile.TemporaryDirectory(prefix="abk_impl_audit_") as tmp:
        root = Path(tmp) / "common"
        for rel in all_files:
            sp = src / rel
            if not sp.is_file():
                fail(f"source tree is missing {rel}")
            dp = root / rel
            dp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(sp, dp)

        problems = []
        for child, module in (("core", abk_stable_core),
                              ("perf", abk_stable_perf),
                              ("display", abk_stable_display)):
            ctx = GraftContext(root, "167", "android13-5.15",
                               defconfig=str(root /
                                             "arch/arm64/configs/gki_defconfig"))
            for group in module.PATCH_GROUPS:
                before = {rel: ctx.read(rel)
                          for rel in ctx.pending_writes()}
                try:
                    status, detail = group.apply_fn(ctx)
                except Exception as exc:      # noqa: BLE001 - audit report
                    status, detail = "error", repr(exc)[:200]
                changed = [rel for rel in ctx.pending_writes()
                           if ctx.read(rel) != before.get(rel)]

                if status in ("applied", "partial") and not changed:
                    problems.append(f"{child}/{group.key}: reported {status} "
                                    "without changing any file")
                key = f"{child}:{group.key}"
                if key in REQUIRED_CONTENT and status in ("applied", "partial"):
                    blob = "".join(ctx.read(f) for f in group.files
                                   if ctx.path(f).exists())
                    missing = [needle for needle in REQUIRED_CONTENT[key]
                               if needle not in blob]
                    if missing:
                        problems.append(f"{child}/{group.key}: missing "
                                        f"feature content {missing}")
                if key in REQUIRED_ABSENT and status in ("applied", "partial"):
                    blob = "".join(ctx.read(f) for f in group.files
                                   if ctx.path(f).exists())
                    present = [needle for needle in REQUIRED_ABSENT[key]
                               if needle in blob]
                    if present:
                        problems.append(f"{child}/{group.key}: removed "
                                        f"content survived {present}")

                # Function-scoped assertions
                if key in REQUIRED_IN_FUNCTION and status in ("applied", "partial"):
                    for rel, fn_name, must_have, must_not_have in REQUIRED_IN_FUNCTION[key]:
                        if not ctx.path(rel).exists():
                            continue
                        text = ctx.read(rel)
                        body = function_body(text, fn_name)
                        if not body:
                            problems.append(f"{child}/{group.key}: function "
                                            f"{fn_name} not found in {rel}")
                            continue
                        for needle in must_have:
                            if needle not in body:
                                problems.append(f"{child}/{group.key}: {fn_name} "
                                                f"missing required text: {needle!r}")
                        for needle in must_not_have:
                            if needle in body:
                                problems.append(f"{child}/{group.key}: {fn_name} "
                                                f"has forbidden text: {needle!r}")

                if status in ("applied", "partial") and \
                        not any(MARKER in ctx.read(f) for f in changed):
                    print(f"  info: {child}/{group.key}: no module marker "
                          "(upstream-shape idempotency)")
                print(f"  {child:5s} {group.key:36s} {status}")

        return problems


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: implementation_audit.py <kernel-tree>")
    problems = run_tree(sys.argv[1])
    if problems:
        fail("; ".join(problems))
    print("IMPLEMENTATION AUDIT OK")


if __name__ == "__main__":
    main()
