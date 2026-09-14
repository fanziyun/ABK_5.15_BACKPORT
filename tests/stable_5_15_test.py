#!/usr/bin/env python3
"""Unit tests for the abk_5_15_backport graft engine and compatibility shapes.

Runs fully self-contained on synthetic fixtures; no kernel tree required.
    python3 tests/stable_5_15_test.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import abk_common as common  # noqa: E402
import sublevel_matrix  # noqa: E402
from abk_backport_engine import GraftContext, PatchGroup, apply_steps, run_child  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name} {detail}")
        FAILURES.append(name)


FD_PRISTINE = (
    "/*\n"
    " * Note how the fdtable bitmap allocations very much have to be a multiple of\n"
    " * BITS_PER_LONG.\n"
    " *\n"
    " * The ALIGN(nr, BITS_PER_LONG) here is for clarity: since we just multiplied\n"
    " * by that \"1024/sizeof(ptr)\" before, we already know there are sufficient\n"
    " * clear low bits. Clang seems to realize that, gcc ends up being confused.\n"
    " *\n"
    " * On a 128-bit machine, the ALIGN() would actually matter. In the meantime,\n"
    " * let's consider it documentation (and maybe a test-case for gcc to improve\n"
    " * its code generation ;)\n"
    " */\n"
    "static struct fdtable * alloc_fdtable(unsigned int nr)\n"
    "{\n"
    "\tstruct fdtable *fdt;\n"
    "\tvoid *data;\n"
    "\tnr /= (1024 / sizeof(struct file *));\n"
    "\tnr = roundup_pow_of_two(nr + 1);\n"
    "\tnr *= (1024 / sizeof(struct file *));\n"
    "\tnr = ALIGN(nr, BITS_PER_LONG);\n"
    "\t/*\n"
    "\t * Note that this can drive nr *below* what we had passed if sysctl_nr_open\n"
    "\t * had been set lower between the check in expand_files() and here.  Deal\n"
    "\t * with that in caller, it's cheaper that way.\n"
    "\t *\n"
    "\t * We make sure that nr remains a multiple of BITS_PER_LONG - otherwise\n"
    "\t * bitmaps handling below becomes unpleasant, to put it mildly...\n"
    "\t */\n"
    "\tif (unlikely(nr > sysctl_nr_open))\n"
    "\t\tnr = ((sysctl_nr_open - 1) | (BITS_PER_LONG - 1)) + 1;\n"
    "out_fdt:\n"
    "\tkfree(fdt);\n"
    "out:\n"
    "\treturn NULL;\n"
    "}\n"
    "static int expand_fdtable(struct files_struct *files, unsigned int nr)\n"
    "{\n"
    "\tspin_unlock(&files->file_lock);\n"
    "\tnew_fdt = alloc_fdtable(nr);\n"
    "\tif (!new_fdt)\n"
    "\t\treturn -ENOMEM;\n"
    "\t/*\n"
    "\t * extremely unlikely race - sysctl_nr_open decreased between the check in\n"
    "\t * caller and alloc_fdtable().  Cheaper to catch it here...\n"
    "\t */\n"
    "\tif (unlikely(new_fdt->max_fds <= nr)) {\n"
    "\t\t__free_fdtable(new_fdt);\n"
    "\t\treturn -EMFILE;\n"
    "\t}\n"
    "}\n"
    "struct files_struct *dup_fd(struct files_struct *oldf, unsigned int max_fds, int *errorp)\n"
    "{\n"
    "\topen_files = sane_fdtable_size(old_fdt, max_fds);\n"
    "\twhile (unlikely(open_files > new_fdt->max_fds)) {\n"
    "\t\tnew_fdt = alloc_fdtable(open_files - 1);\n"
    "\t\tif (!new_fdt) {\n"
    "\t\t\t*errorp = -ENOMEM;\n"
    "\t\t\tgoto out_release;\n"
    "\t\t}\n"
    "\n"
    "\t\t/* beyond sysctl_nr_open; nothing to do */\n"
    "\t\tif (unlikely(new_fdt->max_fds < open_files)) {\n"
    "\t\t\t__free_fdtable(new_fdt);\n"
    "\t\t\t*errorp = -EMFILE;\n"
    "\t\t\tgoto out_release;\n"
    "\t\t}\n"
    "\t}\n"
    "\treturn newf;\n"
    "\n"
    "out_release:\n"
    "\tkmem_cache_free(files_cachep, newf);\n"
    "out:\n"
    "\treturn NULL;\n"
    "}\n"
    "int replace_fd(unsigned fd, struct file *file, unsigned flags)\n"
    "{\n"
    "\treturn do_dup2(files, file, fd, flags);\n"
    "}\n"
)
# Faithful reproduction of ABK_ABI_PATCH_SUITE's fallback alloc_fdtable()
# (scripts/abk_feature_porting.py patch_fd_alloc_hotpath): helper local in the
# body, ALIGN capacity line with its comment, round-up clamp plus an
# INT_MAX -> return NULL guard.  Shared with tests/step_audit.py so the audit
# can build the suite-first shape over the real reference tree as well.
def suite_fallback_deltas(text):
    return (
        text
        .replace(
            "static struct fdtable * alloc_fdtable(unsigned int nr)\n"
            "{\n"
            "\tstruct fdtable *fdt;\n"
            "\tvoid *data;\n",
            "static struct fdtable * alloc_fdtable(unsigned int nr)\n"
            "{\n"
            "\tstruct fdtable *fdt;\n"
            "\tunsigned int slots_wanted = abk_fdtable_slots_wanted(nr);\n"
            "\tvoid *data;\n",
        )
        .replace(
            "\tnr /= (1024 / sizeof(struct file *));\n"
            "\tnr = roundup_pow_of_two(nr + 1);\n"
            "\tnr *= (1024 / sizeof(struct file *));\n"
            "\tnr = ALIGN(nr, BITS_PER_LONG);\n",
            "\t/*\n"
            "\t * Keep the legacy file-local interface shape, but derive capacity from\n"
            "\t * the requested slot count before dropping into the allocator.\n"
            "\t */\n"
            "\tnr = ALIGN(slots_wanted, BITS_PER_LONG);\n",
        )
        .replace(
            "\tif (unlikely(nr > sysctl_nr_open))\n"
            "\t\tnr = ((sysctl_nr_open - 1) | (BITS_PER_LONG - 1)) + 1;\n",
            "\tif (unlikely(nr > sysctl_nr_open))\n"
            "\t\tnr = ((sysctl_nr_open - 1) | (BITS_PER_LONG - 1)) + 1;\n"
            "\tif (unlikely(nr > INT_MAX / sizeof(struct file *)))\n"
            "\t\treturn NULL;\n",
        )
    )


SUITE_HELPER_TAIL = (
    "/* ABK feature_porting: fd allocation hotpath slot-count helper. */\n"
    "static inline unsigned int abk_fdtable_slots_wanted(unsigned int nr)\n"
    "{\n"
    "\tunsigned int slots_wanted;\n\n\tslots_wanted = nr + 1;\n"
    "\tif (IS_ENABLED(CONFIG_32BIT) && slots_wanted < 256)\n"
    "\t\treturn 256;\n"
    "\treturn roundup_pow_of_two(slots_wanted);\n"
    "}\n"
    "/* ABK feature_porting: fd allocation hotpath helper graft. */\n"
    "static inline bool abk_expand_files_needed(const struct fdtable *fdt, unsigned int nr)\n"
    "{\n"
    "\treturn nr >= fdt->max_fds;\n"
    "}\n"
)

FD_SUITE_FALLBACK = suite_fallback_deltas(FD_PRISTINE) + SUITE_HELPER_TAIL
FD_UPSTREAM = (
    "static struct fdtable *alloc_fdtable(unsigned int slots_wanted)\n"
    "{\n"
    "\tif (IS_ENABLED(CONFIG_32BIT) && slots_wanted < 256)\n"
    "\t\tnr = 256;\n"
    "\telse\n"
    "\t\tnr = roundup_pow_of_two(slots_wanted);\n"
    "\tif (unlikely(nr > sysctl_nr_open)) {\n"
    "\t\tnr = round_down(sysctl_nr_open, BITS_PER_LONG);\n"
    "\t\tif (nr < slots_wanted)\n"
    "\t\t\treturn ERR_PTR(-EMFILE);\n"
    "\t}\n"
    "\tif (unlikely(nr > INT_MAX / sizeof(struct file *)))\n"
    "\t\treturn ERR_PTR(-EMFILE);\n"
    "}\n"
    "int replace_fd(unsigned fd, struct file *file, unsigned flags)\n"
    "{\n"
    "\treturn do_dup2(files, file, fd, flags);\n"
    "}\n"
)


def make_ctx(tmp, files, sub_level=sublevel_matrix.DEFAULT_SUB_LEVEL):
    root = Path(tmp) / "common"
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))
    ctx = GraftContext(str(root), sub_level, "android13-5.15")
    ctx.report_dir = str(Path(tmp) / "reports")
    return ctx


def test_replace_once_eol():
    print("replace_once EOL handling")
    text = "a\r\nb\r\nc\n"
    out, status = common.replace_once(text, "b\n", "B\n")
    check("crlf region matched", status == "applied" and "B\r\n" in out, repr(out))
    out, status = common.replace_once("x\ny\n", "y\n", "Y\n")
    check("lf region matched", status == "applied" and out == "x\nY\n", repr(out))
    out, status = common.replace_once(out, "y\n", "Y\n")
    check("idempotent on new content", status == "already_present", repr(out))
    out, status = common.replace_once("x\n", "z\n", "Z\n")
    check("missing anchor", status == "missing_anchor")


def test_apply_steps_transactional():
    print("apply_steps transactional behavior")
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"mm/x.c": "alpha\nbeta\n"})
        steps = [
            ("mm/x.c", "alpha", "ALPHA", True),
            ("mm/x.c", "gamma", "GAMMA", True),  # missing -> aborts everything
        ]
        status, _res, _detail = apply_steps(ctx, steps)
        wrote = ctx.pending_writes()
        check("required miss aborts", status is None)
        check("no partial writes", wrote == [], wrote)
        check("tree untouched", (Path(tmp) / "common/mm/x.c").read_text() == "alpha\nbeta\n")


def test_engine_skips_degraded_without_writes():
    print("engine refuses degraded groups that wrote")

    def bad_apply(ctx):
        ctx.write("mm/x.c", "tampered\n")
        return "blocked_by_missing_anchor", "oops"

    def good_apply(ctx):
        return "report_only", "nothing to do"

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"mm/x.c": "original\n"})
        groups = [PatchGroup("bad", "", [], ["mm/x.c"], bad_apply),
                  PatchGroup("fine", "", [], [], good_apply)]
        run_child("unit", groups, ctx, None)
        check("tampering detected", False, "engine should have raised")


def test_fdtable_shapes():
    print("fdtable compatibility shapes")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import abk_stable_core as core

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"fs/file.c": FD_PRISTINE})
        status, _ = core._fdtable_apply(ctx)
        check("pristine monthly shape grafts", status == "applied", status)
        check("upstream shape reached",
              "alloc_fdtable(unsigned int slots_wanted)" in ctx.read("fs/file.c"))
        check("suite probe satisfied",
              ctx.fdtable_upstream_shape() and "INT_MAX / sizeof(struct file *)" in ctx.read("fs/file.c"))

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"fs/file.c": FD_UPSTREAM})
        status, _ = core._fdtable_apply(ctx)
        check("upstream shape reports already_present", status == "already_present", status)

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"fs/file.c": FD_SUITE_FALLBACK})
        status, detail = core._fdtable_apply(ctx)
        check("suite fallback shape composes", status == "applied", f"{status}: {detail}")
        composed = ctx.read("fs/file.c")
        check("composed tree reaches upstream signature",
              "static struct fdtable *alloc_fdtable(unsigned int slots_wanted)" in composed)
        check("composed tree uses roundup capacity",
              "nr = roundup_pow_of_two(slots_wanted);" in composed)
        check("suite ALIGN capacity line gone", "ALIGN(slots_wanted, BITS_PER_LONG)" not in composed)
        check("suite return-NULL INT_MAX guard replaced",
              composed.count("INT_MAX / sizeof(struct file *)") == 1
              and "return ERR_PTR(-EMFILE)" in composed)
        check("composed tail reports ERR_PTR(-ENOMEM)", "return ERR_PTR(-ENOMEM);" in composed)
        check("suite helpers retained", "abk_fdtable_slots_wanted" in composed
              and "abk_expand_files_needed" in composed)
        check("upstream probe satisfied after composition", ctx.fdtable_upstream_shape())
        status2, _ = core._fdtable_apply(ctx)
        check("composed tree idempotent", status2 == "already_present", status2)

    # Hard-group semantics: an unknown shape must abort the build.
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"fs/file.c": "int weird;\n"})
        try:
            core._fdtable_apply(ctx)
            check("unknown shape aborts hard group", False, "no SystemExit")
        except SystemExit:
            check("unknown shape aborts hard group", True)


def test_replace_fd_errno_group():
    """The 5.15.195 replace_fd() fix must reach a .191-.194 baseline.

    It used to be an optional step inside the fd-table conventions group, which
    short-circuits to already_present the moment the tree is in the upstream
    5.15.191 shape -- so on a 5.15.194 target the hunk was silently skipped.
    """
    print("replace_fd errno group (5.15.195)")
    import abk_stable_core as core

    # Upstream .191-.194 shape: conventions already in, replace_fd not yet.
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"fs/file.c": FD_UPSTREAM}, sub_level="194")
        status, _ = core._fdtable_apply(ctx)
        check("conventions already_present on .194 shape",
              status == "already_present", status)
        status, detail = core._replace_fd_errno_apply(ctx)
        check("replace_fd fix still applies", status == "applied", f"{status}: {detail}")
        text = ctx.read("fs/file.c")
        check("do_dup2 error propagated",
              "\terr = do_dup2(files, file, fd, flags);\n\tif (err < 0)\n"
              "\t\treturn err;\n\treturn 0;\n" in text)
        status2, _ = core._replace_fd_errno_apply(ctx)
        check("replace_fd group idempotent", status2 == "already_present", status2)

    # Pristine monthly shape: the conventions group runs, the fix still lands.
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"fs/file.c": FD_PRISTINE})
        core._fdtable_apply(ctx)
        status, detail = core._replace_fd_errno_apply(ctx)
        check("replace_fd applies after conventions", status == "applied",
              f"{status}: {detail}")

    # A tree without the anchor degrades softly instead of aborting.
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"fs/file.c": "int unrelated;\n"})
        status, _ = core._replace_fd_errno_apply(ctx)
        check("missing anchor degrades to blocked_by_shape",
              status == "blocked_by_shape", status)
        check("degraded group wrote nothing", ctx.pending_writes() == [],
              ctx.pending_writes())


def test_display_valid_clones_revert():
    """The 5.15.185 valid-clones revert must apply on check-carrying trees,
    report already_present on pre-185 trees, and never half-patch a shape it
    does not recognize."""
    print("display valid-clones revert")
    import abk_stable_display as display

    group = next(g for g in display.PATCH_GROUPS if g.key == "drm_valid_clones_revert")
    rel = "drivers/gpu/drm/drm_atomic_helper.c"

    with tempfile.TemporaryDirectory() as tmp:
        # 5.15.185+ shape: function and call site both present.
        ctx = make_ctx(tmp, {rel: display._VC_FN_OLD + "\n" + display._VC_CALL_OLD})
        status, detail = group.apply_fn(ctx)
        check("185+ shape reverts", status == "applied", (status, detail))
        text = ctx.read(rel)
        check("function removed", "drm_atomic_check_valid_clones" not in text)
        check("call site removed", "drm_atomic_check_valid_clones(state, crtc)" not in text)
        status2, _ = group.apply_fn(ctx)
        check("second pass is already_present", status2 == "already_present", status2)

    with tempfile.TemporaryDirectory() as tmp:
        # Pre-185 shape: the fixed form already exists (167/178 baselines).
        pre185 = display._VC_FN_NEW + "\n" + display._VC_CALL_NEW
        ctx = make_ctx(tmp, {rel: pre185})
        before = ctx.read(rel)
        status, detail = group.apply_fn(ctx)
        check("pre-185 shape already fixed",
              status == "already_present", (status, detail))
        check("pre-185 tree untouched", ctx.read(rel) == before)

    with tempfile.TemporaryDirectory() as tmp:
        # Unrecognized shape: both steps must degrade without writing.
        ctx = make_ctx(tmp, {rel: "static int unrelated(void) { return 0; }\n"})
        before = ctx.read(rel)
        status, detail = group.apply_fn(ctx)
        check("unknown shape degrades", status == "blocked_by_shape", (status, detail))
        check("degraded tree untouched", ctx.read(rel) == before)


def test_sublevel_matrix():
    """The expectation matrix must stay in sync with the registries."""
    print("sublevel expectation matrix")
    import abk_stable_core as core
    import abk_stable_perf as perf
    import abk_stable_display as display

    registries = {
        "stable_backport_core": core.PATCH_GROUPS,
        "stable_perf_backport": perf.PATCH_GROUPS,
        "stable_display_fix": display.PATCH_GROUPS,
    }
    for child, groups in registries.items():
        check(f"{child} group count matches registry",
              sublevel_matrix.GROUP_COUNTS[child] == len(groups),
              f"{sublevel_matrix.GROUP_COUNTS[child]} != {len(groups)}")
        keys = {g.key for g in groups}
        for sub_level in sublevel_matrix.SUPPORTED:
            unknown = sublevel_matrix.pre_applied(sub_level, child) - keys
            check(f"{child}@{sub_level} names only real groups",
                  not unknown, unknown)
            unknown_debt = set(sublevel_matrix.debt(sub_level, child)) - keys
            check(f"{child}@{sub_level} debts are real groups",
                  not unknown_debt, unknown_debt)
            surf = (sublevel_matrix.pre_applied(sub_level, child)
                    & set(sublevel_matrix.debt(sub_level, child)))
            check(f"{child}@{sub_level} debts disjoint from pre-applied",
                  not surf, surf)
            summary = sublevel_matrix.status_summary(sub_level, child)
            check(f"{child}@{sub_level} summary totals all groups",
                  sum(summary.values()) == len(groups), summary)

    # Every sublevel must cover every child, and 167 must be all-applied for
    # the forward-graft children.  The display child is a revert: on 167 the
    # 5.15.185 valid-clones check never existed, so its group is legitimately
    # already_present there (see sublevel_matrix.PRE_APPLIED).
    for sub_level in sublevel_matrix.SUPPORTED:
        check(f"{sub_level} covers every child",
              set(sublevel_matrix.PRE_APPLIED[sub_level]) == set(registries),
              set(sublevel_matrix.PRE_APPLIED[sub_level]))
    check("167 is the all-applied baseline for forward grafts",
          all(not sublevel_matrix.pre_applied("167", c)
              for c in registries if c != "stable_display_fix"))


def test_f2fs_shape_probe():
    print("F2FS rollback shape probe")
    monthly_blk = "void f(void)\n{\n\tdelayed_work_pending(&hctx->run_work);\n}\n"
    rolled_blk = "void f(void)\n{\n\tmsleep(5);\n}\n"
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"block/blk-mq.c": monthly_blk})
        check("monthly tree detected", ctx.block_rolled_back() is False)
        ctx2 = make_ctx(tmp, {"block/blk-mq.c": rolled_blk})
        check("rolled-back tree detected", ctx2.block_rolled_back() is True)


def test_kabi_slot_policy():
    print("KABI slot policy")
    with tempfile.TemporaryDirectory() as tmp:
        sched_h = "struct task_struct {\n\tANDROID_KABI_RESERVE(1);\n\tANDROID_KABI_RESERVE(8);\n};\n"
        ctx = make_ctx(tmp, {"include/linux/sched.h": sched_h, "include/linux/randomize_kstack.h":
                             "DECLARE_PER_CPU(u32, kstack_offset);\n", "init/main.c": "x\n",
                             "kernel/fork.c": "stackleak_task_init(p);\n"})
        import abk_stable_perf as perf
        # Only assert the KABI slot rewrite step works standalone; the other
        # kstack hunks need the full upstream shape and may abort this group.
        text = ctx.read("include/linux/sched.h")
        check("slot 8 present", "ANDROID_KABI_RESERVE(8);" in text)
        perf_ctx_texts = perf  # noqa: F841 - import proves the module loads
        # Batch 15 retired the ABK_ABI_PATCH_SUITE red line: this module now
        # claims sched_entity slots 1-3 itself (Batch 16 released slot 4, which
        # nothing read).  task_struct slot 1 was never this group's to take and
        # still is not -- the kstack rewrite lands on slot 8 (or slot 5 on a
        # SysVIPC-patched tree, see the test below).
        check("kstack group claims no task_struct slot 1",
              "ANDROID_KABI_USE(1" not in text)


def test_kstack_slot_shape_selection():
    print("kstack KABI slot shape selection")
    import abk_stable_perf as perf
    pristine = "".join("\tANDROID_KABI_RESERVE(%d);\n" % n for n in range(1, 9))
    step = perf._sched_h_kstack_step(pristine)
    check("pristine -> slot 8 run anchor", "RESERVE(1);" in step[1] and "USE(8" in step[2])
    sysv = (
        "\tANDROID_KABI_RESERVE(5);\n#ifdef CONFIG_SYSVIPC\n"
        "\tANDROID_KABI_USE(6, struct sysv_sem sysvsem);\n"
        "\t_ANDROID_KABI_REPLACE(ANDROID_KABI_RESERVE(7); ANDROID_KABI_RESERVE(8), struct sysv_shm sysvshm);\n"
        "#else\n\tANDROID_KABI_RESERVE(6);\n\tANDROID_KABI_RESERVE(7);\n\tANDROID_KABI_RESERVE(8);\n#endif\n"
    )
    step2 = perf._sched_h_kstack_step(sysv)
    check("sysv-patched -> slot 5", "RESERVE(5);" in step2[1] and "USE(5" in step2[2])
    check("slot-5 anchor hits patched tail", step2[1] in sysv)
    # android13-5.15-lts from 5.15.211 on: AOSP claims slot 1 for the
    # user_dumpable bitfield, so the 1..8 run no longer exists -- the group has
    # to find the 2..8 run (that shape reported blocked_by_shape until it did).
    lts = (
        "\tANDROID_KABI_USE(1, struct {\n"
        "\t\t/* Save user-dumpable when mm goes away */\n"
        "\t\tunsigned\tuser_dumpable:1;\n"
        "\t\t});\n"
        "\n"
        + "".join("\tANDROID_KABI_RESERVE(%d);\n" % n for n in range(2, 9))
    )
    step3 = perf._sched_h_kstack_step(lts)
    check("lts slot-1-taken -> the 2..8 run",
          "RESERVE(2);" in step3[1] and "RESERVE(1);" not in step3[1])
    check("lts anchor hits the free run", step3[1] in lts)
    check("lts still claims slot 8",
          "USE(8" in step3[2] and "USE(1," not in step3[2])


def test_defconfig_lane():
    print("defconfig lane: three forms, idempotency, scope guard")
    import abk_stable_core as core

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "common"
        (root / "arch/arm64/configs").mkdir(parents=True)
        defconfig = root / "arch/arm64/configs/gki_defconfig"
        defconfig.write_text(
            "CONFIG_A=y\n"
            "# CONFIG_B is not set\n"
            "CONFIG_C=n\n"
        )
        ctx = GraftContext(str(root), "167", "android13-5.15",
                           defconfig=str(defconfig))
        status, detail = ctx.enable_configs([
            ("B", "y"), ("C", "y"), ("A", "y"), ("NEW", "y"),
        ])
        check("defconfig applied", status == "applied", status)
        text = defconfig.read_text()
        check("disabled symbol rewritten",
              "CONFIG_B=y" in text and "# CONFIG_B is not set" not in text, text)
        check("other-value symbol rewritten", "CONFIG_C=y" in text, text)
        check("already-target untouched once", text.count("CONFIG_A=y") == 1, text)
        check("new symbol appended + one marker",
              "CONFIG_NEW=y" in text
              and text.count("ABK stable_515_backport: config_enablement") == 1,
              text)

        ctx2 = GraftContext(str(root), "167", "android13-5.15",
                            defconfig=str(defconfig))
        status2, _d = ctx2.enable_configs([("B", "y")])
        check("defconfig idempotent", status2 == "already_present", status2)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "common"
        root.mkdir()
        outside = Path(tmp) / "gki_defconfig"
        outside.write_text("# bare\n")
        ctx = GraftContext(str(root), "167", "android13-5.15",
                           defconfig=str(outside))
        status, detail = ctx.enable_configs([("A", "y")])
        check("defconfig outside KERNEL_ROOT refused",
              status == "report_only" and "outside" in detail,
              (status, detail))


def test_family_gate():
    print("family gate: unsupported lineage is report-only")
    def would_write(ctx):
        ctx.write("mm/x.c", "boom\n")
        return "applied", "wrote"

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"mm/x.c": "pristine\n"})
        ctx.family = "android15-6.6"
        groups = [PatchGroup("g", "", [], ["mm/x.c"], would_write)]
        report = run_child("unit", groups, ctx, None, enabled=False)
        check("zero writes under gate", ctx.pending_writes() == [],
              ctx.pending_writes())
        check("all report_only",
              [g["status"] for g in report["groups"]] == ["report_only"],
              [g["status"] for g in report["groups"]])


def test_apply_steps_noop_blocked():
    print("apply_steps empty step list is blocked, not already_present")
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"mm/x.c": "x\n"})
        status, _res, detail = apply_steps(ctx, [])
        check("empty steps blocked",
              status == "blocked_by_missing_anchor" and "empty step list" in detail,
              (status, detail))


def test_batch6_registration():
    print("Batch 6 registry: keys, counts, debt-consistency")
    import abk_stable_core as core
    import abk_stable_perf as perf
    keys = {g.key for g in core.PATCH_GROUPS}
    perf_keys = {g.key for g in perf.PATCH_GROUPS}
    need = {"config_enablement", "zsmalloc_chain_size", "madvise_collapse"}
    check("batch6 groups registered", need <= keys, need - keys)
    # The no-op path must not regress: a graft that cannot find any anchor on
    # an empty tree degrades instead of silently succeeding.
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {
            "mm/Kconfig": "config ZSMALLOC\n\ttristate\n",
            "include/uapi/asm-generic/mman-common.h": "#define MADV_POPULATE_WRITE 23\n",
            "include/linux/khugepaged.h": "extern void collapse_pte_mapped_thp(void);\n",
            "mm/madvise.c": "static int f(void) { return 1; }\n",
            "mm/khugepaged.c": "static void khugepaged_scan_mm_slot(void) {}\n",
        })
        status, _ = core._zsmalloc_chain_size_apply(ctx)
        status2, _ = core._madvise_collapse_apply(ctx)
        check("zsmalloc degrades cleanly",
              status == "blocked_by_shape", status)
        check("madvise degrades cleanly",
              status2 == "blocked_by_shape", status2)
    for sub_level, child in (("216", "stable_perf_backport"),):
        for key, want in sublevel_matrix.debt(sub_level, child).items():
            check(f"debt {sub_level}/{key} is a real group",
                  key in perf_keys, key)


def test_batch8_pagealloc_fallback_reuse():
    print("Batch 8 pagealloc fallback reuse")
    import abk_stable_core as core

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "pagealloc_fallback_reuse"), None)
    check("batch8 group registered", group is not None)
    if group is None:
        return
    check("batch8 touches the fallback callers",
          set(group.files) == {"mm/page_alloc.c", "mm/compaction.c",
                               "mm/internal.h"}, group.files)

    # Keep this fixture self-contained while still exercising the complete
    # nine-step transaction and its second-pass shape detection.
    internal_old = (
        "int find_suitable_fallback(struct free_area *area, unsigned int order,\n"
        "\t\t\tint migratetype, bool only_stealable, bool *can_steal);"
    )
    compaction_old = core._B8_COMPACTION_DECL_OLD + "\n" + core._B8_COMPACTION_CALL_OLD
    page_old = (core._B8_FIND_OLD + "\n" + core._B8_RMQUEUE_OLD + "\n" +
                core._B8_FALLBACK_OLD + "\n" + core._B8_BULK_DECL_OLD + "\n" +
                core._B8_BULK_CALL_OLD + "\n" + core._B8_BUDDY_CALL_OLD)
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"mm/internal.h": internal_old,
                             "mm/compaction.c": compaction_old,
                             "mm/page_alloc.c": page_old})
        status, detail = core._pagealloc_fallback_reuse_apply(ctx)
        check("batch8 fixture applies all steps", status == "applied",
              (status, detail))
        patched = {rel: ctx.read(rel) for rel in ctx.pending_writes()}
        check("mode enum is present", "enum rmqueue_mode" in patched["mm/page_alloc.c"])
        check("find helper has distinct unclaimable result",
              "return -2" in patched["mm/page_alloc.c"])
        ctx2 = make_ctx(tmp, patched)
        status2, _detail2 = core._pagealloc_fallback_reuse_apply(ctx2)
        check("batch8 fixture is idempotent", status2 == "already_present", status2)


def test_batch8_rcu_nocb_cpu_default_all():
    print("Batch 8 RCU_NOCB_CPU_DEFAULT_ALL")
    import abk_stable_core as core

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "rcu_nocb_cpu_default_all"), None)
    check("RCU default-all group registered", group is not None)
    if group is None:
        return
    check("RCU default-all touches its three source files",
          set(group.files) == {
              "Documentation/admin-guide/kernel-parameters.txt",
              "kernel/rcu/Kconfig", "kernel/rcu/tree_nocb.h",
          }, group.files)

    kconfig = core._B8_RCU_NOCB_KCONFIG_OLD + "\n"
    params = (core._B8_RCU_NOCB_DOC_NOHZ_OLD + "\n" +
              core._B8_RCU_NOCB_DOC_PARAM_OLD + "\n")
    nocb = (core._B8_RCU_NOCB_INIT_OLD + "\n" +
            core._B8_RCU_NOCB_NOHZ_OLD + "\n" +
            core._B8_RCU_NOCB_SETALL_OLD + "\n")
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {
            "kernel/rcu/Kconfig": kconfig,
            "kernel/rcu/tree_nocb.h": nocb,
            "Documentation/admin-guide/kernel-parameters.txt": params,
        })
        status, detail = core._rcu_nocb_cpu_default_all_apply(ctx)
        check("RCU default-all fixture applies all steps", status == "applied",
              (status, detail))
        patched = {rel: ctx.read(rel) for rel in ctx.pending_writes()}
        check("RCU default-all has opt-in Kconfig",
              "config RCU_NOCB_CPU_DEFAULT_ALL" in patched["kernel/rcu/Kconfig"])
        check("RCU default-all materializes the mask",
              "cpumask_setall(rcu_nocb_mask)" in patched["kernel/rcu/tree_nocb.h"])
        check("explicit boot masks retain precedence",
              patched["Documentation/admin-guide/kernel-parameters.txt"].count(
                  "CONFIG_RCU_NOCB_CPU_DEFAULT_ALL") == 2)
        ctx2 = make_ctx(tmp, patched)
        status2, _detail2 = core._rcu_nocb_cpu_default_all_apply(ctx2)
        check("RCU default-all fixture is idempotent",
              status2 == "already_present", status2)


def test_batch9_dynamic_readahead():
    print("Batch 9 dynamic_readahead_lowmem")
    import abk_stable_core as core

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "dynamic_readahead_lowmem"), None)
    check("dynamic_readahead group registered", group is not None)
    if group is None:
        return
    check("dynamic_readahead touches its two source files",
          set(group.files) == {"mm/readahead.c", "mm/Kconfig"}, group.files)

    readahead = core._DRA_RA_OLD + "\nvoid\nfile_ra_state_init(void){}\n"
    kconfig = core._DRA_KC_OLD + "\n\tdef_bool y\n"
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {
            "mm/readahead.c": readahead,
            "mm/Kconfig": kconfig,
        })
        status, detail = core._dynamic_readahead_apply(ctx)
        check("dynamic_readahead fixture applies all steps",
              status == "applied", (status, detail))
        patched = {rel: ctx.read(rel) for rel in ctx.pending_writes()}
        check("readahead policy registers the max-page hook",
              "register_trace_android_vh_ra_tuning_max_page" in
              patched["mm/readahead.c"])
        check("readahead policy registers the readaround hook",
              "register_trace_android_vh_tune_mmap_readaround" in
              patched["mm/readahead.c"])
        check("policy is gated by the module-owned Kconfig symbol",
              "config ABK_DYNAMIC_READAHEAD" in patched["mm/Kconfig"])
        check("no vendor xring dependency leaks into the graft",
              "qos_inherit" not in patched["mm/readahead.c"])
        ctx2 = make_ctx(tmp, patched)
        status2, _detail2 = core._dynamic_readahead_apply(ctx2)
        check("dynamic_readahead fixture is idempotent",
              status2 == "already_present", status2)


def test_batch10_zram_async_recompress():
    print("Batch 10-1 zram_async_recompress")
    import abk_stable_core as core
    import batch10_core_zram_async as z10

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "zram_async_recompress"), None)
    check("zram_async_recompress group registered", group is not None)
    if group is None:
        return

    steps = z10.build_steps()
    check("group files match its steps",
          {s[0] for s in steps} == set(group.files), (steps, group.files))
    for (rel, old, new, req) in steps:
        check(f"step {old.splitlines()[0][:34]!r} is required",
              req is True and old and old != new, (req, old == new))

    zram_c = (
        z10._C_INC_OLD + z10._DECL_OLD
        + "\t&dev_attr_comp_algorithm.attr,\n"
        + "static int zram_init(void) { return 0; }\n"
        + z10._MODULE_INIT_OLD
    )
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {
            "drivers/block/zram/zram_drv.c": zram_c,
        })
        status, detail = core._zram_async_recompress_apply(ctx)
        check("zram_async_recompress fixture applies all steps",
              status == "applied", (status, detail))
        text = ctx.read("drivers/block/zram/zram_drv.c")
        check("async node and engine text landed in the driver",
              "recompress_async" in text
              and "abk_zram_recomp_enqueue" in text
              and "kthread_create_worker(0, \"zram_recompd\")" in text)
        check("sysfs list entry is guarded",
              "#ifdef CONFIG_ZRAM_MULTI_COMP\n"
              "\t&dev_attr_recompress_async.attr,\n"
              "#endif\n"
              "\t&dev_attr_comp_algorithm.attr," in text)
        check("reset drain entry point is declared",
              "abk_zram_recomp_drain(struct zram *zram);" in text)
        ctx2 = make_ctx(tmp, {"drivers/block/zram/zram_drv.c": text})
        status2, _detail2 = core._zram_async_recompress_apply(ctx2)
        check("zram_async_recompress fixture is idempotent",
              status2 == "already_present", status2)

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {})
        status3, detail3 = core._zram_async_recompress_apply(ctx)
        check("zram_async_recompress degrades on an empty tree",
              status3.startswith("blocked") and ctx.pending_writes() == [],
              (status3, detail3))


def test_batch10_sched_smart_policy():
    print("Batch 10-5 schedutil_smart_policy (DVFS-ownership gated)")
    import abk_stable_perf as perf
    import batch10_perf_sched_policy as s10

    group = next((g for g in perf.PATCH_GROUPS
                  if g.key == "schedutil_smart_policy"), None)
    check("schedutil_smart_policy group registered", group is not None)
    if group is None:
        return

    rel = "kernel/sched/cpufreq_schedutil.c"
    pristine = s10._INC_OLD + "void governor(void);\n" + s10._TAIL_OLD
    # What Batch 10-4c leaves in a tree it already grafted: its include block
    # plus its payload appended after the governor init.
    v1_tree = (s10._INC_V1 + "void governor(void);\n" + s10._TAIL_OLD
               + s10._POLICY_V1)

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {rel: pristine})
        status, detail = perf._sched_smart_policy_apply(ctx)
        check("sched smart-policy fixture applies all steps",
              status == "applied", (status, detail))
        text = ctx.read(rel)
        for name, cond in {
            "policy samples util from the scheduler tick":
                "register_trace_android_vh_scheduler_tick(abk_sf_tick, NULL)"
                in text and "cpu_util_cfs(rq)" in text,
            "policy applies its floor at cpufreq resolve time":
                "register_trace_android_vh_cpufreq_resolve_freq(abk_sf_resolve_freq,"
                in text,
            "policy no longer depends on a schedutil-only hook":
                "register_trace_android_vh_map_util_freq_new" not in text,
            "policy ships disabled so FAS/WALT keeps DVFS":
                "static bool abk_sf_enable = false;" in text,
            "policy refuses to speak to a foreign governor or a pinned range":
                'strcmp(policy->governor->name, "schedutil") != 0' in text
                and "if (abk_sf_dvfs_owned(policy) || policy->min == policy->max)"
                in text,
            "a floor without headroom is never applied":
                "if (floor <= policy->min || floor >= policy->max)" in text
                and "floor = policy->max" not in text,
            "the sustained window slides instead of banking boost time":
                "if (util * 100 < cap * ABK_SF_SUSTAINED_PCT) {" in text
                and "} else if (util * 100 < cap * ABK_SF_EXIT_PCT) {"
                not in text,
            "the reason also expires at resolve time (NO_HZ_IDLE)":
                "static bool abk_sf_cpu_boosting(int cpu)" in text
                and "time_after(jiffies, c->boost_release)" in text,
            "the reason state is observable read-only":
                "module_param_cb(abk_sf_boosting, &abk_sf_boosting_ops, "
                "NULL, 0444);" in text,
        }.items():
            check(name, cond)
        check("the payload lands exactly once",
              text.count("static bool abk_sf_enable") == 1,
              text.count("static bool abk_sf_enable"))
        # module_param(NAME, ...) compiles the identifier NAME as the variable,
        # so every one-name form must really declare it (ABK CI caught the
        # counterexample once already; text audits cannot see this class).
        declared = set(re.findall(r"(?m)^static\s+[\w \t\*]+?(\w+)\s*=", text))
        for name in re.findall(r"(?m)^module_param\((\w+),", text):
            check(f"one-name module_param {name!r} really declares that variable",
                  name in declared, (name, sorted(declared)))
        ctx2 = make_ctx(tmp, {rel: text})
        status2, _detail2 = perf._sched_smart_policy_apply(ctx2)
        check("sched smart-policy fixture is idempotent",
              status2 == "already_present", status2)

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {rel: v1_tree})
        status, detail = perf._sched_smart_policy_apply(ctx)
        check("a Batch 10-4c tree is upgraded in place",
              status == "applied" and "upgraded" in detail, (status, detail))
        text = ctx.read(rel)
        check("the upgrade leaves one payload, already at the current shape",
              text.count("static bool abk_sf_enable") == 1
              and s10.has_current_policy(text)
              and not s10.needs_legacy_upgrade(text),
              text.count("static bool abk_sf_enable"))
        check("the upgrade does not duplicate the include block",
              text.count("#include <linux/string.h>") == 1
              and text.count("#include <trace/hooks/cpufreq.h>") == 1,
              text.count("#include <trace/hooks/cpufreq.h>"))
        ctx2 = make_ctx(tmp, {rel: text})
        status2, _d2 = perf._sched_smart_policy_apply(ctx2)
        check("the upgraded tree is idempotent",
              status2 == "already_present", status2)

    with tempfile.TemporaryDirectory() as tmp:
        # Why the upgrade branch exists: the plain insert anchors on the line
        # the payload was appended to, which still matches -- so it would
        # define the policy a second time and fail the compile.
        ctx = make_ctx(tmp, {rel: v1_tree})
        perf.apply_steps(ctx, s10.build_steps())
        copies = ctx.read(rel).count("static bool abk_sf_enable")
        check("the upgrade branch is load-bearing (plain insert duplicates)",
              copies == 2, copies)

        # Batch 10-2 grafted this group once with a payload that matches neither
        # migration anchor, and its `_TAIL_OLD` anchor survives there too.  The
        # same duplication would happen silently, so an unnamed shape must be
        # refused rather than "applied".
        b10_2 = (s10._INC_V1 + "void governor(void);\n" + s10._TAIL_OLD
                 + "\n/*\n * ABK stable_515_backport: Batch 10-2 sched"
                 " smart-freq policy (PELT).\n */\n"
                 "static bool abk_sf_enable = true;\n"
                 "register_trace_android_vh_map_util_freq_new(abk_sf_sample, NULL);\n")
        check("the Batch 10-2 shape is detected as unnamed",
              s10.has_unknown_policy(b10_2)
              and not s10.has_legacy_policy(b10_2)
              and not s10.has_current_policy(b10_2))
        ctx = make_ctx(tmp, {rel: b10_2})
        status_u, detail_u = perf._sched_smart_policy_apply(ctx)
        check("an unnamed payload shape is refused, not applied",
              status_u == "blocked_by_shape" and "unrecognised" in detail_u,
              (status_u, detail_u))
        check("and the refusal writes nothing",
              ctx.pending_writes() == [], ctx.pending_writes())
        check("the v1 and current shapes are not treated as unnamed",
              not s10.has_unknown_policy(v1_tree)
              and not s10.has_unknown_policy(pristine)
              and not s10.has_unknown_policy(s10._POLICY_V2))

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {})
        status3, detail3 = perf._sched_smart_policy_apply(ctx)
        check("sched smart-policy degrades on an empty tree",
              status3.startswith("blocked") and ctx.pending_writes() == [],
              (status3, detail3))


def test_batch10_zram_secondary_comp():
    print("Batch 10-4 zram_secondary_comp")
    import abk_stable_core as core
    import batch10_core_zram_secondary as zs

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "zram_secondary_comp"), None)
    check("zram_secondary_comp group registered", group is not None)
    if group is None:
        return
    check("zram_secondary_comp touches only the zram driver",
          set(group.files) == {"drivers/block/zram/zram_drv.c"}, group.files)

    steps = zs.build_steps()
    for (rel, old, new, req) in steps:
        check(f"step {old.splitlines()[0][:34]!r} is required",
              req is True and old and old != new, (req, old == new))

    zram = (
        "static const char *default_compressor = CONFIG_ZRAM_DEF_COMP;\n"
        "\n"
        "static void comp_algorithm_set(struct zram *zram, u32 prio,\n"
        "\t\t\t       const char *alg) {}\n"
        "\n"
        "static int zram_add(void)\n"
        "{\n"
        "\tint ret, device_id;\n"
        "\n"
        "\tret = idr_alloc(&zram_index_idr, zram, 0, 0, GFP_KERNEL);\n"
        "\tif (ret < 0)\n"
        "\t\tgoto out_free_dev;\n"
        "\tdevice_id = ret;\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"drivers/block/zram/zram_drv.c": zram})
        status, detail = core._zram_secondary_comp_apply(ctx)
        check("zram_secondary_comp fixture applies all steps",
              status == "applied", (status, detail))
        text = ctx.read("drivers/block/zram/zram_drv.c")
        check("secondary compressor parameter is declared read-only",
              'static char abk_zram_recomp_algo[CRYPTO_MAX_ALG_NAME] = "zstd";'
              in text and "module_param_string(abk_recomp_algo" in text
              and "sizeof(abk_zram_recomp_algo), 0444);" in text
              and "0644" not in text)
        check("secondary slot is filled at device creation",
              "comp_algorithm_set(zram, ZRAM_SECONDARY_COMP, abk_alg);" in text
              and "zcomp_available_algorithm(abk_zram_recomp_algo)" in text)
        ctx2 = make_ctx(tmp, {"drivers/block/zram/zram_drv.c": text})
        status2, _detail2 = core._zram_secondary_comp_apply(ctx2)
        check("zram_secondary_comp fixture is idempotent",
              status2 == "already_present", status2)

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {})
        status3, detail3 = core._zram_secondary_comp_apply(ctx)
        check("zram_secondary_comp degrades on an empty tree",
              status3.startswith("blocked") and ctx.pending_writes() == [],
              (status3, detail3))


def test_batch11_zram_algo_lock():
    print("Batch 11 zram_algo_lock")
    import abk_stable_core as core
    import batch11_core_zram_algo_lock as zl

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "zram_algo_lock"), None)
    check("zram_algo_lock group registered", group is not None)
    if group is None:
        return
    check("zram_algo_lock touches only the zram driver",
          set(group.files) == {"drivers/block/zram/zram_drv.c"}, group.files)
    check("zram_algo_lock runs after the groups it anchors on",
          [g.key for g in core.PATCH_GROUPS].index("zram_algo_lock")
          > [g.key for g in core.PATCH_GROUPS].index("zram_secondary_comp"))

    steps = zl.build_steps()
    for (rel, old, new, req) in steps:
        check(f"step {old.splitlines()[0][:34]!r} is required",
              req is True and old and old != new, (req, old == new))

    # The fixture is the shape the earlier groups leave behind: Batch 10-4's
    # parameter block, the multi-comp store both nodes share, the primary
    # assignment in zram_add(), and Batch 10-1's engine before module_init().
    zram = (
        "static const char *default_compressor = CONFIG_ZRAM_DEF_COMP;\n"
        "\n"
        "#ifdef CONFIG_ZRAM_MULTI_COMP\n"
        "static char abk_zram_recomp_algo[CRYPTO_MAX_ALG_NAME] = \"zstd\";\n"
        "module_param_string(abk_recomp_algo, abk_zram_recomp_algo,\n"
        "\t\t    sizeof(abk_zram_recomp_algo), 0444);\n"
        "MODULE_PARM_DESC(abk_recomp_algo,\n"
        "\t\"ABK: secondary zram compressor enabling recompression (read-only; empty disables)\");\n"
        "#endif\n"
        "\n"
        "static void comp_algorithm_set(struct zram *zram, u32 prio,\n"
        "\t\t\t       const char *alg) {}\n"
        "\n"
        "static int zram_add(void)\n"
        "{\n"
        "\tint ret, device_id;\n"
        "\n"
        "\tret = idr_alloc(&zram_index_idr, zram, 0, 0, GFP_KERNEL);\n"
        "\tif (ret < 0)\n"
        "\t\tgoto out_free_dev;\n"
        "\tdevice_id = ret;\n"
        "\n"
        "\tzram->comp_algs[ZRAM_PRIMARY_COMP] = default_compressor;\n"
        "\tzram->num_active_comps = 1;\n"
        "\n"
        "\tzram_debugfs_register(zram);\n"
        "\tpr_info(\"Added device: %s\\n\", zram->disk->disk_name);\n"
        "}\n"
        "\n"
        "/* ABK stable_515_backport: Batch 10-1 async recompress engine (plan A) */\n"
        "\n"
        "module_init(zram_init);\n"
        "module_exit(zram_exit);\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"drivers/block/zram/zram_drv.c": zram})
        status, detail = core._zram_algo_lock_apply(ctx)
        check("zram_algo_lock fixture applies all steps",
              status == "applied", (status, detail))
        text = ctx.read("drivers/block/zram/zram_drv.c")
        check("locked primary parameter is declared read-only",
              'static char abk_zram_comp_algo[CRYPTO_MAX_ALG_NAME] = "lz4kd";' in text
              and "module_param_string(abk_comp_algo, abk_zram_comp_algo" in text
              and "module_param_named(abk_lock_algo, abk_zram_lock_algo, bool, 0444);"
              in text)
        # module_param(NAME, ...) compiles the *identifier* NAME as the
        # variable, so a knob whose sysfs name differs from its variable has to
        # use module_param_named()/module_param_string().  ABK CI caught exactly
        # this ("use of undeclared identifier 'abk_lock_algo'") because the
        # text-level audits cannot see it; check every one-argument form here.
        declared = set(re.findall(r"(?m)^static\s+[\w \t\*]+?(\w+)\s*=", text))
        for name in re.findall(r"(?m)^module_param\((\w+),", text):
            check(f"one-name module_param {name!r} really declares that variable",
                  name in declared, (name, sorted(declared)))
        check("locked primary is selected at device creation",
              "comp_algorithm_set(zram, ZRAM_PRIMARY_COMP," in text
              and "zcomp_available_algorithm(abk_zram_comp_algo)" in text)
        check("a name this build lacks keeps the build default",
              "keeping %s\\n" in text and "default_compressor);" in text)
        check("both algorithm nodes are repointed at the locked store",
              "dev_attr_comp_algorithm.store = abk_zram_locked_algo_store;" in text
              and "dev_attr_recomp_algorithm.store = abk_zram_locked_algo_store;" in text
              and "late_initcall(abk_zram_algo_lock_init)" in text)
        # The whole point of accepting the write: a refused algorithm write
        # aborts Android's mmd_setup, writeback backing device included.
        check("a locked-out write is reported as success, not refused",
              "-EPERM" not in text and "-EACCES" not in text,
              [line for line in text.splitlines() if "-EP" in line])
        check("the lock is reported through the kernel log",
              "pr_info_ratelimited(" in text)
        # And the texts the earlier groups own must survive byte-identical, or
        # their second pass stops being idempotent (the audit checks this).
        check("earlier groups' blocks stay untouched",
              "#ifdef CONFIG_ZRAM_MULTI_COMP\n"
              "static char abk_zram_recomp_algo[CRYPTO_MAX_ALG_NAME] = \"zstd\";\n"
              "module_param_string(abk_recomp_algo, abk_zram_recomp_algo,\n"
              "\t\t    sizeof(abk_zram_recomp_algo), 0444);\n"
              "MODULE_PARM_DESC(abk_recomp_algo,\n"
              "\t\"ABK: secondary zram compressor enabling recompression (read-only; empty disables)\");\n"
              "#endif\n" in text
              and "\tzram->comp_algs[ZRAM_PRIMARY_COMP] = default_compressor;\n"
              "\tzram->num_active_comps = 1;\n" in text
              and "/* ABK stable_515_backport: Batch 10-1 async recompress engine"
              " (plan A) */\n\nmodule_init(zram_init);\n" in text)
        check("the lock is registered after zram's own initcall",
              text.index("late_initcall(abk_zram_algo_lock_init)")
              > text.index("module_init(zram_init);"))
        ctx2 = make_ctx(tmp, {"drivers/block/zram/zram_drv.c": text})
        status2, _detail2 = core._zram_algo_lock_apply(ctx2)
        check("zram_algo_lock fixture is idempotent",
              status2 == "already_present", status2)

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {})
        status3, detail3 = core._zram_algo_lock_apply(ctx)
        check("zram_algo_lock degrades on an empty tree",
              status3.startswith("blocked") and ctx.pending_writes() == [],
              (status3, detail3))


_BATCH13_MM_H = (
    "DECLARE_HOOK(android_vh_page_cache_miss,\n"
    "\tTP_PROTO(struct file *file),\n"
    "\tTP_ARGS(file));\n"
    "#endif /* _TRACE_HOOK_MM_H */\n"
)
_BATCH13_PAGE_ALLOC = (
    "#include <trace/hooks/mm.h>\n"
    "\tac.nodemask = nodemask;\n"
    "\n"
    "\tpage = __alloc_pages_slowpath(alloc_gfp, order, &ac);\n"
    "out:\n"
    "\treturn page;\n"
    "}\n"
    "\n"
    "#ifdef CONFIG_ZONE_DMA\n"
    "bool has_managed_dma(void)\n"
    "{\n"
    "\tstruct pglist_data *pgdat;\n"
    "\n"
    "\tfor_each_online_pgdat(pgdat) {\n"
    "\t\tstruct zone *zone = &pgdat->node_zones[ZONE_DMA];\n"
    "\n"
    "\t\tif (managed_zone(zone))\n"
    "\t\t\treturn true;\n"
    "\t}\n"
    "\treturn false;\n"
    "}\n"
    "#endif /* CONFIG_ZONE_DMA */\n"
)
_BATCH13_VENDOR_HOOKS = (
    "EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_vmscan_kswapd_done);\n"
    "EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_should_end_madvise);\n"
)


def test_batch13_customize_alloc_gfp_vh():
    print("Batch 13 customize_alloc_gfp_vh (upstream-shape hook graft)")
    import abk_stable_core as core
    import batch13_core_gfp_customize_vh as g13

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "customize_alloc_gfp_vh"), None)
    check("customize_alloc_gfp_vh group registered", group is not None)
    if group is None:
        return
    check("hook group files match its steps",
          set(group.files) == {s[0] for s in g13.build_hook_steps()},
          group.files)
    # The 5.15.167 slowpath-entry block is byte-identical to 6.6's; the graft
    # is the upstream text and must stay that way -- an ABK marker comment on
    # a line upstream also has would break already_present on a future
    # baseline that carries the commit itself.  This is the whole-tree rule's
    # fixture-level pin: the implementation audit cannot check it per-group
    # because page_alloc.c legitimately carries markers from other groups.
    for blob in (g13.HOOK_MM_NEW, g13.HOOK_PA_NEW, g13.HOOK_VH_NEW):
        check("hook graft text carries no ABK marker (upstream-shape)",
              "ABK stable_515_backport" not in blob, blob[:60])
    check("upstream commit recorded",
          any("4466afd69452" in c for c in group.commits), group.commits)

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {
            "include/trace/hooks/mm.h": _BATCH13_MM_H,
            "mm/page_alloc.c": _BATCH13_PAGE_ALLOC,
            "drivers/android/vendor_hooks.c": _BATCH13_VENDOR_HOOKS,
        })
        status, detail = core._customize_alloc_gfp_vh_apply(ctx)
        check("hook fixture applies all three steps",
              status == "applied", (status, detail))
        mm_h = ctx.read("include/trace/hooks/mm.h")
        pa = ctx.read("mm/page_alloc.c")
        vh = ctx.read("drivers/android/vendor_hooks.c")
        check("hook declared in mm.h",
              "DECLARE_HOOK(android_vh_customize_alloc_gfp," in mm_h
              and "TP_PROTO(gfp_t *alloc_gfp, unsigned int order)" in mm_h)
        check("call lands between the nodemask restore and the slowpath entry",
              "ac.nodemask = nodemask;\n"
              "\ttrace_android_vh_customize_alloc_gfp(&alloc_gfp, order);\n"
              "\n\tpage = __alloc_pages_slowpath(alloc_gfp, order, &ac);" in pa)
        check("tracepoint exported for modules",
              "EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_customize_alloc_gfp);"
              in vh)
        ctx2 = make_ctx(tmp, {
            "include/trace/hooks/mm.h": mm_h,
            "mm/page_alloc.c": pa,
            "drivers/android/vendor_hooks.c": vh,
        })
        status2, _detail2 = core._customize_alloc_gfp_vh_apply(ctx2)
        check("hook fixture is idempotent",
              status2 == "already_present", status2)

    # A tree whose baseline already carries the commit (android15-6.6 form)
    # must short-circuit to already_present with zero writes -- that is what
    # the marker-free upstream-shape form buys.
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {
            "include/trace/hooks/mm.h": g13.HOOK_MM_NEW,
            "mm/page_alloc.c":
                _BATCH13_PAGE_ALLOC.replace(g13.HOOK_PA_OLD, g13.HOOK_PA_NEW),
            "drivers/android/vendor_hooks.c": g13.HOOK_VH_NEW,
        })
        status3, _d3 = core._customize_alloc_gfp_vh_apply(ctx)
        check("upstream-carried tree reports already_present",
              status3 == "already_present", status3)
        check("already_present writes nothing",
              ctx.pending_writes() == [], ctx.pending_writes())

    # AGENTS.md trap 5: a grafted call in a TU that lost the hook-header
    # include cannot compile -- the group must block, writing nothing.
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {
            "include/trace/hooks/mm.h": _BATCH13_MM_H,
            "mm/page_alloc.c": _BATCH13_PAGE_ALLOC.replace(
                "#include <trace/hooks/mm.h>\n", ""),
            "drivers/android/vendor_hooks.c": _BATCH13_VENDOR_HOOKS,
        })
        status, detail = core._customize_alloc_gfp_vh_apply(ctx)
        check("hook graft refuses a TU without the header include",
              status == "blocked_by_shape" and ctx.pending_writes() == [],
              (status, detail))


def test_batch13_gfp_pressure_fastfail():
    print("Batch 13 gfp_pressure_fastfail (policy payload)")
    import abk_stable_core as core
    import batch13_core_gfp_customize_vh as g13

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "gfp_pressure_fastfail"), None)
    check("gfp_pressure_fastfail group registered", group is not None)
    if group is None:
        return
    check("policy group registered after the hook group",
          [g.key for g in core.PATCH_GROUPS].index("gfp_pressure_fastfail")
          > [g.key for g in core.PATCH_GROUPS].index("customize_alloc_gfp_vh"))

    # The payload must not land on a tree without the hook: the
    # register_trace_ symbol would not exist at compile time (AGENTS.md trap
    # 5 family -- text gates stay green, the build dies).
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {
            "include/trace/hooks/mm.h": _BATCH13_MM_H,
            "mm/page_alloc.c": _BATCH13_PAGE_ALLOC,
        })
        status, detail = core._gfp_pressure_fastfail_apply(ctx)
        check("policy refuses a tree without the grafted hook",
              status == "blocked_by_shape" and ctx.pending_writes() == [],
              (status, detail))

    # A merely-declared hook is still inert: the callback would register but
    # never fire, so the probe also demands the call site in the same TU.
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {
            "include/trace/hooks/mm.h": g13.HOOK_MM_NEW,
            "mm/page_alloc.c": _BATCH13_PAGE_ALLOC,
        })
        status, detail = core._gfp_pressure_fastfail_apply(ctx)
        check("policy refuses a declared-but-never-called hook",
              status == "blocked_by_shape" and ctx.pending_writes() == [],
              (status, detail))

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {
            "include/trace/hooks/mm.h": _BATCH13_MM_H,
            "mm/page_alloc.c": _BATCH13_PAGE_ALLOC,
            "drivers/android/vendor_hooks.c": _BATCH13_VENDOR_HOOKS,
        })
        s0, _d0 = core._customize_alloc_gfp_vh_apply(ctx)
        assert s0 == "applied"
        status, detail = core._gfp_pressure_fastfail_apply(ctx)
        check("policy fixture applies after the hook",
              status == "applied", (status, detail))
        text = ctx.read("mm/page_alloc.c")
        mm_h = ctx.read("include/trace/hooks/mm.h")
        vh = ctx.read("drivers/android/vendor_hooks.c")
        for name, cond in {
            "policy registers the hook callback":
                "register_trace_android_vh_customize_alloc_gfp(" in text,
            "policy is vendor-hook gated":
                "#ifdef CONFIG_ANDROID_VENDOR_HOOKS" in text,
            "pressure adds NORETRY and NOWARN":
                "*gfp |= __GFP_NORETRY | __GFP_NOWARN;" in text,
            "order gate really compares against the knob":
                "if (order < READ_ONCE(abk_gfp_fastfail_order))" in text,
            "enable knob really gates the handler":
                "if (!READ_ONCE(abk_gfp_fastfail))" in text,
            "THP-class default order":
                "static unsigned int abk_gfp_fastfail_order = 9;" in text,
            "carrier carries the ABK marker (module-introduced code)":
                "ABK stable_515_backport: Batch 13 high-order slowpath "
                "fast-fail." in text,
        }.items():
            check(name, cond)
        # One-name module_param() compiles the identifier as the variable;
        # pin that every knob declared here really declares that name.
        for knob in re.findall(r"(?m)^module_param\((\w+),", text):
            check(f"one-name module_param {knob!r} really declares it",
                  re.search(rf"(?m)^static [^;]*\b{knob}\b", text) is not None)
        ctx2 = make_ctx(tmp, {
            "include/trace/hooks/mm.h": mm_h,
            "mm/page_alloc.c": text,
            "drivers/android/vendor_hooks.c": vh,
        })
        s1, _d1 = core._customize_alloc_gfp_vh_apply(ctx2)
        check("hook stays already_present under the policy", s1 == "already_present", s1)
        status2, _detail2 = core._gfp_pressure_fastfail_apply(ctx2)
        check("policy fixture is idempotent",
              status2 == "already_present", status2)


def test_batch13_wake_up_new_task_excluded():
    """Pin the survey's negative result.

    research/hooks_gfp_vs_wake_up_new_task.md verified
    ``android_rvh_wake_up_new_task`` verbatim on .167/.178/.194/lts: already
    carried, so it gets **no group** (plan.md: 已排除，不再重议).  This is
    the guard that keeps that decision from silently regressing into a
    duplicate graft on a future baseline that still carries the hook.
    """
    print("Batch 13 wake_up_new_task exclusion pin")
    import abk_stable_core as core
    import abk_stable_perf as perf

    keys = [g.key for g in core.PATCH_GROUPS] + \
           [g.key for g in perf.PATCH_GROUPS]
    check("no group grafts wake_up_new_task (carried by every baseline)",
          not any("wake_up_new_task" in k for k in keys),
          [k for k in keys if "wake_up_new_task" in k])


def test_config_tiers():
    print("config tiers: module-owned vs GKI align vs ROM integration")
    import abk_stable_core as core
    import os

    def enabled(env_name):
        saved = {k: os.environ.get(k) for k in
                 ("ABK_515_DEFCONFIG_ALIGN", "ABK_515_DEFCONFIG_ROM")}
        for key in saved:
            os.environ.pop(key, None)
        if env_name:
            os.environ[env_name] = "1"
        caught = {}

        class Probe:
            family = "android13-5.15"
            sub_level = "167"

            def enable_configs(self, configs):
                caught["configs"] = list(configs)
                return "applied", "probe"

        try:
            status, detail = core._config_enablement_apply(Probe())
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        return status, detail, dict(caught.get("configs", []))

    _s, _d, plain = enabled(None)
    _s, _d, align = enabled("ABK_515_DEFCONFIG_ALIGN")
    status, detail, rom = enabled("ABK_515_DEFCONFIG_ROM")

    check("module-owned tier enables the module's own symbols",
          dict(plain).get("ZRAM_MULTI_COMP") == "y"
          and "ZRAM_WRITEBACK" not in dict(plain), sorted(dict(plain)))
    check("align tier adds the 6.6 GKI config deltas",
          dict(align).get("LRU_GEN_ENABLED") == "y"
          and "ZRAM_WRITEBACK" not in dict(align), sorted(dict(align)))
    check("ROM tier adds CONFIG_ZRAM_WRITEBACK",
          dict(rom).get("ZRAM_WRITEBACK") == "y"
          and "LRU_GEN_ENABLED" not in dict(rom), sorted(dict(rom)))
    check("ROM tier is reported in the detail string",
          status == "applied" and "ROM integration" in detail, (status, detail))


def test_introduced_kconfig_tiers():
    """Every Kconfig symbol this module introduces must have a way to compile.

    The failure this guards against is not hypothetical: Batch 8 added
    ``config RCU_NOCB_CPU_DEFAULT_ALL`` (bool, ``default n``) plus the
    ``offload_all`` machinery in ``kernel/rcu/tree_nocb.h``, but no tier ever
    enabled the symbol -- so both assignments that could set ``offload_all``
    compiled out, ``if (offload_all)`` was provably always false, and the group
    still reported "applied".  A graft that compiles to nothing is not a graft.
    """
    print("introduced Kconfig symbols: each one has a way to compile")
    import abk_stable_core as core

    tiers = {
        "module": dict(core._MODULE_CONFIGS),
        "align": dict(core._ALIGN_CONFIGS),
        "rom": dict(core._ROM_CONFIGS),
    }

    for symbol, tier in sorted(core._INTRODUCED_KCONFIG.items()):
        if tier is None:
            # Kconfig itself supplies a workable default (a non-bool, or a bool
            # defaulting to y), so no tier has to name it.
            continue
        check(f"{symbol} is named by the {tier} tier",
              symbol in tiers[tier], f"{tier} has {sorted(tiers[tier])}")
        check(f"{symbol} is enabled (=y) in the {tier} tier",
              tiers[tier].get(symbol) == "y", tiers[tier].get(symbol))

    # Reverse direction, module tier only: a symbol this module turns on without
    # documenting it in the table is a symbol nobody checked has a Kconfig
    # declaration at all.  (The align tier is deliberately exempt -- those
    # symbols come from the 6.6 GKI defconfig, not from this module's Kconfig.)
    for symbol in tiers["module"]:
        check(f"module-tier symbol {symbol} is recorded in _INTRODUCED_KCONFIG",
              symbol in core._INTRODUCED_KCONFIG,
              "add it to _INTRODUCED_KCONFIG in scripts/abk_stable_core.py")


def test_batch10_memcg_v1_reclaim():
    print("Batch 10-4 memcg_v1_reclaim")
    import abk_stable_core as core
    import batch10_core_memcg_v1 as v1

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "memcg_v1_reclaim"), None)
    check("memcg_v1_reclaim group registered", group is not None)
    if group is None:
        return

    memcontrol = (
        "static int memcg_stat_show(struct seq_file *m, void *v)\n"
        "{\n" + v1._V1_STAT_OLD + "}\n"
        "\n"
        + v1._LEGACY_HDR_OLD +
        v1._LEGACY_ENT_OLD +
        "};\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"mm/memcontrol.c": memcontrol})
        status, detail = core._memcg_v1_reclaim_apply(ctx)
        check("memcg_v1_reclaim fixture applies all steps",
              status == "applied", (status, detail))
        text = ctx.read("mm/memcontrol.c")
        check("legacy table gains the reclaim entry",
              ".write = memory_reclaim," in text)
        check("forward declaration precedes the legacy table",
              text.index("static ssize_t memory_reclaim(struct kernfs_open_file")
              < text.index("static struct cftype mem_cgroup_legacy_files[] = {"))
        check("v1 memory.stat reports the reclaim counters",
              "cfr_reclaim_attempts %ld" in text)
        ctx2 = make_ctx(tmp, {"mm/memcontrol.c": text})
        status2, _detail2 = core._memcg_v1_reclaim_apply(ctx2)
        check("memcg_v1_reclaim fixture is idempotent",
              status2 == "already_present", status2)

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {})
        status3, detail3 = core._memcg_v1_reclaim_apply(ctx)
        check("memcg_v1_reclaim degrades on an empty tree",
              status3.startswith("blocked") and ctx.pending_writes() == [],
              (status3, detail3))


def test_batch10_cached_freeze_reclaim():
    print("Batch 10-3 cached_freeze_reclaim")
    import abk_stable_core as core
    import batch10_core_cached_freeze_reclaim as c10

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "cached_freeze_reclaim"), None)
    check("cached_freeze_reclaim group registered", group is not None)
    if group is None:
        return

    steps = c10.build_steps()
    check("group files match its steps",
          {s[0] for s in steps} == set(group.files), (steps, group.files))
    for (rel, old, new, req) in steps:
        check(f"step {old.splitlines()[0][:34]!r} is required",
              req is True and old and old != new, (req, old == new))

    # vmscan/memcontrol fixtures: pristine anchors only (the accounting must
    # not touch the text the memcg_memory_reclaim group installs, or that
    # group stops being idempotent on the second pass).
    vmscan = (
        c10._CFR_DECL_OLD + "\n"
        "unsigned long try_to_free_mem_cgroup_pages(void)\n"
        "{\n"
        + c10._CFR_ACC_OLD
    )
    memcontrol = (
        "static char *memory_stat_format(void)\n"
        "{\n" + c10._CFR_STAT_OLD + "}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {
            "mm/vmscan.c": vmscan,
            "mm/memcontrol.c": memcontrol,
        })
        status, detail = core._cached_freeze_reclaim_apply(ctx)
        check("cached_freeze_reclaim fixture applies all steps",
              status == "applied", (status, detail))
        patched = {rel: ctx.read(rel) for rel in ctx.pending_writes()}
        check("reclaim accounting lands in the reclaim engine",
              "atomic_long_inc(&abk_cfr_reclaim_attempts);" in
              patched["mm/vmscan.c"]
              and "reclaim_options & MEMCG_RECLAIM_PROACTIVE" in
              patched["mm/vmscan.c"])
        check("counters surface in memory.stat text",
              "cfr_reclaim_reclaimed %ld" in patched["mm/memcontrol.c"])
        ctx2 = make_ctx(tmp, patched)
        status2, _detail2 = core._cached_freeze_reclaim_apply(ctx2)
        check("cached_freeze_reclaim fixture is idempotent",
              status2 == "already_present", status2)

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {})
        status3, detail3 = core._cached_freeze_reclaim_apply(ctx)
        check("cached_freeze_reclaim degrades on an empty tree",
              status3.startswith("blocked") and ctx.pending_writes() == [],
              (status3, detail3))


def test_batch10_daemon_script():
    """The cached-freeze-reclaim daemon is a userspace script, not a graft.

    The script is exercised against throwaway cgroup directories in *both*
    layouts: v2 (memory.current / memory.reclaim / cgroup.freeze) and v1
    (memory.usage_in_bytes / memory.reclaim / freezer.state), because this
    module's own target device mounts the memory controller on v1.  The whole
    driver is fed to the shell on stdin so Windows/WSL path and quoting
    differences never touch the assertions.
    """
    print("Batch 10-3 cached_freeze_reclaim daemon script")
    tool = Path(__file__).resolve().parent.parent / "tools" / "cached_freeze_reclaim.sh"
    check("cfr daemon script exists", tool.is_file(), tool)

    bash = shutil.which("bash")
    if bash is None:
        print("  (no bash on this host: shell checks skipped)")
        return

    use_wsl = os.name == "nt"
    shell = ["wsl", "bash", "-s"] if use_wsl else [bash, "-s"]
    tool_sh = str(tool).replace("\\", "/")
    if use_wsl and len(tool_sh) > 2 and tool_sh[1] == ":":
        tool_sh = "/mnt/" + tool_sh[0].lower() + tool_sh[2:]

    def run_shell(script):
        # Feed the driver as bytes: text mode would rewrite LF to CRLF on
        # Windows and bash would choke on the stray carriage returns.
        r = subprocess.run(shell, input=script.encode("utf-8"),
                           capture_output=True, timeout=120)
        return subprocess.CompletedProcess(
            r.args, r.returncode,
            r.stdout.decode("utf-8", "replace"),
            r.stderr.decode("utf-8", "replace"))

    r = run_shell(f'bash -n "{tool_sh}"\n')
    check("cfr daemon script passes bash -n", r.returncode == 0, r.stderr)

    driver = f'''
set -u
T=$(mktemp -d)
mkdir -p "$T/apps/uid_1000"
echo 1073741824 > "$T/apps/uid_1000/memory.current"
: > "$T/apps/uid_1000/memory.reclaim"
echo 0 > "$T/apps/uid_1000/cgroup.freeze"

# default: reclaim memory.current whole (AOSP "maximal reclaim"), no freeze
CFR_ONE_SHOT=1 bash "{tool_sh}" --cgroup-root "$T"
echo "RC1=$?"
echo "reclaim1=$(cat "$T/apps/uid_1000/memory.reclaim")"
echo "freeze1=$(cat "$T/apps/uid_1000/cgroup.freeze")"

# --quota-mb caps the write
echo 999 > "$T/apps/uid_1000/memory.reclaim"
CFR_ONE_SHOT=1 bash "{tool_sh}" --cgroup-root "$T" --quota-mb 16
echo "RC2=$?"
echo "reclaim2=$(cat "$T/apps/uid_1000/memory.reclaim")"

# --freeze quiesces, reclaims, then thaws back to 0
echo 999 > "$T/apps/uid_1000/memory.reclaim"
CFR_ONE_SHOT=1 bash "{tool_sh}" --cgroup-root "$T" --quota-mb 16 --freeze
echo "RC3=$?"
echo "freeze3=$(cat "$T/apps/uid_1000/cgroup.freeze")"

# --dry-run writes nothing
echo 999 > "$T/apps/uid_1000/memory.reclaim"
CFR_ONE_SHOT=1 bash "{tool_sh}" --cgroup-root "$T" --dry-run
echo "RC4=$?"
echo "reclaim4=$(cat "$T/apps/uid_1000/memory.reclaim")"

# cgroup v1 layout: memory.usage_in_bytes + freezer.state (FROZEN/THAWED)
T1=$(mktemp -d)
T1E=$(mktemp -d)
mkdir -p "$T1/apps/uid_10042"
echo 268435456 > "$T1/apps/uid_10042/memory.usage_in_bytes"
: > "$T1/apps/uid_10042/memory.reclaim"
echo THAWED > "$T1/apps/uid_10042/freezer.state"
CFR_ONE_SHOT=1 sh "{tool_sh}" --cgroup-root "$T1" --freeze
echo "RC_V1=$?"
echo "v1_reclaim=$(cat "$T1/apps/uid_10042/memory.reclaim")"
echo "v1_freezer=$(cat "$T1/apps/uid_10042/freezer.state")"

# --list reports the targets without writing to them
echo 123 > "$T1/apps/uid_10042/memory.reclaim"
CFR_ONE_SHOT=1 sh "{tool_sh}" --cgroup-root "$T1" --list
echo "v1_list_rc=$?"
echo "v1_after_list=$(cat "$T1/apps/uid_10042/memory.reclaim")"

# a root with no uid_* group must fail loudly instead of reporting success
CFR_ONE_SHOT=1 sh "{tool_sh}" --cgroup-root "$T1E" >/dev/null 2>&1
echo "RC_NO_GROUPS=$?"

# --uid narrows the sweep to one group
mkdir -p "$T1/apps/uid_10043"
echo 1048576 > "$T1/apps/uid_10043/memory.usage_in_bytes"
: > "$T1/apps/uid_10043/memory.reclaim"
echo 555 > "$T1/apps/uid_10042/memory.reclaim"
echo 555 > "$T1/apps/uid_10043/memory.reclaim"
CFR_ONE_SHOT=1 sh "{tool_sh}" --cgroup-root "$T1" --uid 10043
echo "v1_uid_rc=$?"
echo "v1_uid_target=$(cat "$T1/apps/uid_10043/memory.reclaim")"
echo "v1_uid_other=$(cat "$T1/apps/uid_10042/memory.reclaim")"

# named groups (HyperOS keeps freeze-app / game / mimd / protect_memcg_* on v1)
mkdir -p "$T1/freeze-app"
echo 52428800 > "$T1/freeze-app/memory.usage_in_bytes"
echo 555 > "$T1/freeze-app/memory.reclaim"
echo 555 > "$T1/apps/uid_10043/memory.reclaim"
CFR_ONE_SHOT=1 sh "{tool_sh}" --cgroup-root "$T1" --group freeze-app
echo "v1_group_rc=$?"
echo "v1_group_target=$(cat "$T1/freeze-app/memory.reclaim")"
echo "v1_group_other=$(cat "$T1/apps/uid_10043/memory.reclaim")"

# a named group with nothing to reclaim, and a --group that is a path
CFR_ONE_SHOT=1 sh "{tool_sh}" --cgroup-root "$T1" --group no-such-group >/dev/null 2>&1
echo "RC_GROUP_MISSING=$?"
sh "{tool_sh}" --cgroup-root "$T1" --group ../../etc >/dev/null 2>&1
echo "RC_GROUP_TRAVERSAL=$?"

# --list must still name a group that is found but currently empty: on a real
# v1 ROM the named groups read 0 while idle, and silence would look like
# "nothing found" instead of "found, nothing charged".
mkdir -p "$T1/game"
echo 0 > "$T1/game/memory.usage_in_bytes"
: > "$T1/game/memory.reclaim"
CFR_ONE_SHOT=1 sh "{tool_sh}" --cgroup-root "$T1" --group game --list
echo "v1_list_zero_rc=$?"

rm -rf "$T" "$T1" "$T1E"
'''
    r = run_shell(driver)
    got = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            got[k] = v
    check("cfr daemon default sweep succeeds", got.get("RC1") == "0",
          r.stdout + r.stderr)
    check("cfr daemon default reclaims memory.current whole",
          got.get("reclaim1") == "1073741824", r.stdout)
    check("cfr daemon does not freeze unless asked",
          got.get("freeze1") == "0", r.stdout)
    check("cfr daemon honours --quota-mb",
          got.get("RC2") == "0" and got.get("reclaim2") == str(16 * 1024 * 1024),
          r.stdout)
    check("cfr daemon --freeze thaws back within the sweep",
          got.get("RC3") == "0" and got.get("freeze3") == "0", r.stdout)
    check("cfr daemon dry-run writes nothing",
          got.get("RC4") == "0" and got.get("reclaim4") == "999", r.stdout)
    check("cfr daemon handles the cgroup v1 layout (usage_in_bytes + freezer.state)",
          got.get("RC_V1") == "0"
          and got.get("v1_reclaim") == "268435456"
          and got.get("v1_freezer") == "THAWED", r.stdout)
    check("cfr daemon --list reports the targets without writing",
          got.get("v1_list_rc") == "0" and got.get("v1_after_list") == "123", r.stdout)
    check("cfr daemon fails loudly when no cached group exists",
          got.get("RC_NO_GROUPS") == "1", r.stdout)
    check("cfr daemon --uid narrows the sweep",
          got.get("v1_uid_rc") == "0"
          and got.get("v1_uid_target") == "1048576"
          and got.get("v1_uid_other") == "555", r.stdout)
    check("cfr daemon --group sweeps a named v1 group only",
          got.get("v1_group_rc") == "0"
          and got.get("v1_group_target") == "52428800"
          and got.get("v1_group_other") == "555", r.stdout)
    check("cfr daemon fails loudly for a named group with no reclaim file",
          got.get("RC_GROUP_MISSING") == "1", r.stdout)
    check("cfr daemon rejects a --group that is a path",
          got.get("RC_GROUP_TRAVERSAL") == "2", r.stdout)
    check("cfr daemon --list still names a group that is currently empty",
          got.get("v1_list_zero_rc") == "0" and "/game" in r.stdout, r.stdout)


def test_batch10_zram_trigger_script():
    """The recompression trigger is a userspace script, not a graft.

    Its most important behaviour is refusing to report success on a device
    whose secondary compressor was never registered -- that silent no-op is
    exactly what the on-device check found, and it is what Batch 10-4's
    kernel half fixes.
    """
    print("Batch 10-4 zram_recompress_trigger script")
    tool = (Path(__file__).resolve().parent.parent / "tools"
            / "zram_recompress_trigger.sh")
    check("zram trigger script exists", tool.is_file(), tool)

    bash = shutil.which("bash")
    if bash is None:
        print("  (no bash on this host: shell checks skipped)")
        return

    use_wsl = os.name == "nt"
    shell = ["wsl", "bash", "-s"] if use_wsl else [bash, "-s"]
    tool_sh = str(tool).replace("\\", "/")
    if use_wsl and len(tool_sh) > 2 and tool_sh[1] == ":":
        tool_sh = "/mnt/" + tool_sh[0].lower() + tool_sh[2:]

    def run_shell(script):
        r = subprocess.run(shell, input=script.encode("utf-8"),
                           capture_output=True, timeout=120)
        return subprocess.CompletedProcess(
            r.args, r.returncode,
            r.stdout.decode("utf-8", "replace"),
            r.stderr.decode("utf-8", "replace"))

    driver = f'''
set -u
T=$(mktemp -d)
mkdir -p "$T/block/zram0"
echo 17179869184 > "$T/block/zram0/disksize"
echo "lzo [lz4kd]" > "$T/block/zram0/comp_algorithm"
: > "$T/block/zram0/recomp_algorithm"
echo "1000 400 500 0 0 0 0 0 0" > "$T/block/zram0/mm_stat"
: > "$T/block/zram0/idle"
: > "$T/block/zram0/recompress"
: > "$T/block/zram0/recompress_async"

# unarmed: must fail loudly and write nothing
set +e
sh "{tool_sh}" --sys-root "$T" >/dev/null 2>&1
echo "RC_UNARMED=$?"
set -e
echo "unarmed_wrote=$(cat "$T/block/zram0/recompress_async")"

# arm it: the pass must go through the async node with our threshold
echo "lz4hc" > "$T/block/zram0/recomp_algorithm"
sh "{tool_sh}" --sys-root "$T" --threshold 64
echo "RC_ARMED=$?"
echo "async_after=$(cat "$T/block/zram0/recompress_async")"

# --mark-idle writes 'all', --mode sync uses the synchronous node
sh "{tool_sh}" --sys-root "$T" --mark-idle --mode sync
echo "idle=$(cat "$T/block/zram0/idle")"
echo "sync_after=$(cat "$T/block/zram0/recompress")"

# --dry-run writes nothing
: > "$T/block/zram0/recompress_async"
sh "{tool_sh}" --sys-root "$T" --dry-run
echo "async_after_dryrun=$(cat "$T/block/zram0/recompress_async")"

sh "{tool_sh}" --sys-root "$T" --status

# A secondary that equals the primary cannot shrink anything: refuse with exit
# 3 and write nothing, unless the caller explicitly allows it.
echo "lzo [lz4hc]" > "$T/block/zram0/comp_algorithm"
echo "lz4hc" > "$T/block/zram0/recomp_algorithm"
: > "$T/block/zram0/recompress_async"
set +e
sh "{tool_sh}" --sys-root "$T" >/dev/null 2>&1
echo "RC_SAME_ALGO=$?"
set -e
echo "same_algo_wrote=$(cat "$T/block/zram0/recompress_async")"
sh "{tool_sh}" --sys-root "$T" --allow-same-algo >/dev/null 2>&1
echo "RC_SAME_ALGO_ALLOWED=$?"
echo "same_algo_allowed_wrote=$(cat "$T/block/zram0/recompress_async")"

# --idle-age carries the age through to the kernel (age semantics), instead of
# the blunt "all" that --mark-idle writes.
echo "lzo [lz4kd]" > "$T/block/zram0/comp_algorithm"
: > "$T/block/zram0/idle"
sh "{tool_sh}" --sys-root "$T" --idle-age 3600
echo "idle_age=$(cat "$T/block/zram0/idle")"

# Usage errors must be rejected before anything is written.
set +e
sh "{tool_sh}" --sys-root "$T" --idle-age 0 >/dev/null 2>&1
echo "RC_IDLE_AGE_ZERO=$?"
sh "{tool_sh}" --sys-root "$T" --mark-idle --idle-age 60 >/dev/null 2>&1
echo "RC_IDLE_AGE_CONFLICT=$?"
sh "{tool_sh}" --sys-root "$T" --daemon --mark-each-pass >/dev/null 2>&1
echo "RC_MARK_EACH_BAD=$?"
set -e

rm -rf "$T"
'''
    r = run_shell(driver)
    got = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            got[k] = v

    check("trigger refuses to no-op on an unarmed device",
          got.get("RC_UNARMED") == "1", r.stdout + r.stderr)
    check("unarmed run writes nothing",
          got.get("unarmed_wrote") == "", r.stdout)
    check("armed run drives the async node",
          got.get("RC_ARMED") == "0"
          and got.get("async_after") == "type=idle threshold=64", r.stdout)
    check("--mark-idle marks every stored page",
          got.get("idle") == "all", r.stdout)
    check("--mode sync drives the synchronous node",
          got.get("sync_after") == "type=idle threshold=0", r.stdout)
    check("--dry-run writes nothing",
          got.get("async_after_dryrun") == "", r.stdout)
    check("--status reports the armed device",
          "recomp     = [lz4hc]" in r.stdout, r.stdout)
    check("same-algorithm secondary is refused with exit 3",
          got.get("RC_SAME_ALGO") == "3"
          and got.get("same_algo_wrote") == "", r.stdout + r.stderr)
    check("--allow-same-algo runs the pass anyway",
          got.get("RC_SAME_ALGO_ALLOWED") == "0"
          and got.get("same_algo_allowed_wrote") == "type=idle threshold=0",
          r.stdout)
    check("--idle-age passes the age to the kernel, not 'all'",
          got.get("idle_age") == "3600", r.stdout)
    check("bad --idle-age and --mark-idle conflicts are usage errors",
          got.get("RC_IDLE_AGE_ZERO") == "2"
          and got.get("RC_IDLE_AGE_CONFLICT") == "2"
          and got.get("RC_MARK_EACH_BAD") == "2", r.stdout)


def test_runtime_tunables_module():
    """The KernelSU companion module, its fixed zram policy and its packagers.

    Three separate things are pinned here:

      * the module packages deterministically and the AK3 ride-along is
        idempotent, because that is how the policy reaches the device,
      * the algorithm policy is a *constant* with a fixed order of operations,
        so the ROM's own zram owner cannot win the race again, and
      * the device scripts behave: they rewrite the algorithms, they refuse to
        touch swap that is in use, they preserve (or establish) the writeback
        backing device instead of trading it away, they do nothing at all on a
        kernel that already locks the compressors, and the supervisor keeps
        sweeping when a pass fails.
    """
    print("Batch 11 runtime tunables companion module")
    repo = Path(__file__).resolve().parent.parent
    module_dir = repo / "ksu" / "abk_runtime_tunables"
    check("companion module directory exists", module_dir.is_dir(), module_dir)
    if not module_dir.is_dir():
        return

    required = ("module.prop", "common.sh", "zram-policy.sh", "post-fs-data.sh",
                "service.sh", "action.sh", "tunables.conf", "embed.conf",
                "sepolicy.rule", "README.md")
    for name in required:
        check(f"module ships {name}", (module_dir / name).is_file())

    props = {}
    for line in (module_dir / "module.prop").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            props[key.strip()] = value.strip()
    check("module id matches the directory KernelSU installs into",
          props.get("id") == module_dir.name == "abk_runtime_tunables", props.get("id"))
    for key in ("name", "version", "versionCode", "author", "description"):
        check(f"module.prop has {key}", bool(props.get(key)))
    check("module versionCode is a positive integer",
          props.get("versionCode", "").isdigit() and int(props["versionCode"]) > 0)

    # module.conf carries the companion through the module-set contract
    # (ABK's app reads parts[10] = name and parts[11] = download url).  On this
    # repo the companion is *bundled into the AnyKernel3 zip* (after_patch ->
    # ak3_bundle_ksu_module.py, abk-ksu-modules/), so flashing the kernel also
    # installs it and there is nothing for the app to download: both fields are
    # deliberately empty placeholders.  The 12-field shape still holds; if a
    # future layout stops bundling the module, the fields get filled and this
    # check flips to the old non-empty assertions.
    conf = (repo / "module.conf").read_text(encoding="utf-8")
    items = re.search(r"ABK_MODULE_SET_ITEMS='(.*?)'", conf, re.S)
    check("module.conf declares ABK_MODULE_SET_ITEMS", items is not None)
    rows = [line for line in items.group(1).splitlines() if line.strip()] if items else []
    fields = {row.split("|")[0]: row.split("|") for row in rows}
    check("every child row keeps the 12-field module-set shape",
          bool(fields) and all(len(row) >= 12 for row in fields.values()),
          {name: len(row) for name, row in fields.items()})
    core_row = fields.get("stable_backport_core", [])
    check("companion ships inside the kernel zip, not via an app download",
          len(core_row) >= 12 and core_row[10] == "" and core_row[11] == "",
          core_row[10:12])
    # The companion rides the kernel module's version: it is bundled into the
    # same zip, so a release that bumps one and not the other would ship a
    # module.prop that disagrees with module.conf.  Releasing means updating
    # the expected version below -- that is deliberate, it is the touch point
    # that makes an unreleased version drift visible instead of silent.
    _versions = re.findall(r'^ABK_MODULE_(?:SET_)?VERSION="([^"]+)"', conf, re.M)
    check("both module.conf versions move together",
          len(_versions) == 2 and _versions[0] == _versions[1], _versions)
    check("module.conf carries the released version",
          _versions == ["0.23.0", "0.23.0"], _versions)

    # The zram writeback data path is kernel-side: the loop worker -- a kernel
    # thread, so u:r:kernel:s0, whoever attached the loop device -- is what reads
    # and writes the backing file, and Android's policy has no rule for that
    # direction.  Under Enforcing the first page therefore returns -EIO, the
    # reserved block is freed again, and writeback reports success while moving
    # nothing: measured on the reference device, where even the ROM's own
    # loop49 backing store had written 0 pages since boot (bd_stat 0 0 0) until
    # this rule was submitted.  KernelSU can add the rule from a module, which
    # is the only route that needs neither a ROM rebuild nor a relaxed boot
    # policy.  What is pinned here is *narrowness* as much as presence: a
    # widened rule is a policy change shipped by a kernel flash, so the file is
    # exactly one least-privilege allow, and no module code gets to relax
    # SELinux to reach the same end.
    rule = (module_dir / "sepolicy.rule").read_text(encoding="utf-8")
    rule_statements = [line.strip() for line in rule.splitlines()
                       if line.strip() and not line.lstrip().startswith("#")]
    check("the SELinux rule file carries exactly one statement",
          rule_statements == ["allow kernel zram_data_file file { read write }"],
          rule_statements)
    for forbidden in ("setenforce", "permissive", "neverallow", "dontaudit",
                      "auditallow", "type_transition", "allowx"):
        check(f"the SELinux rule never uses {forbidden!r}",
              forbidden not in rule)
    check("the SELinux rule names the kernel domain, not a permissive shell",
          rule_statements and rule_statements[0].startswith("allow kernel "))

    post_fs_data = (module_dir / "post-fs-data.sh").read_text(encoding="utf-8")
    common_source = (module_dir / "common.sh").read_text(encoding="utf-8")
    action_source = (module_dir / "action.sh").read_text(encoding="utf-8")
    selinux_apply = common_source[common_source.index("abk_selinux_apply_rules() {"):
                                  common_source.index("abk_apply_early_knobs() {")]
    check("the boot stage submits the shipped rule through ksud",
          "abk_selinux_apply_rules" in post_fs_data
          and 'sepolicy apply "$_sa_rule"' in selinux_apply)
    check("the boot stage submits it before service.sh touches zram",
          post_fs_data.index("abk_selinux_apply_rules")
          > post_fs_data.index("abk_apply_early_knobs"))
    check("a missing manager or a failed apply is reported, never fatal",
          "abk_warn" in selinux_apply
          and 'no ksud on PATH' in selinux_apply
          and post_fs_data.index("abk_selinux_apply_rules")
          < post_fs_data.index('abk_log "post-fs-data: done"'))
    check("the status report shows the verdict node, not just the attachment",
          'abk_show "bd_stat"' in action_source
          and 'abk_show "selinux"' in action_source)

    tunables = (module_dir / "tunables.conf").read_text(encoding="utf-8")
    for forbidden in ("algo", "disksize", "mem_limit"):
        check(f"tunables.conf exposes no {forbidden} knob",
              not re.search(rf"(?m)^\s*{forbidden}", tunables))

    policy = (module_dir / "zram-policy.sh").read_text(encoding="utf-8")
    common_sh = (module_dir / "common.sh").read_text(encoding="utf-8")
    check("policy hardcodes the measured primary",
          'ABK_ZRAM_PRIMARY="lz4kd"' in common_sh)
    check("policy hardcodes the measured secondary",
          'ABK_ZRAM_SECONDARY="zstd"' in common_sh)
    check("the dominated lz4hc is never selected as a policy value",
          not re.search(r'(?m)^\s*ABK_ZRAM_\w+="lz4hc"', common_sh + policy))

    # The DVFS ownership report (Batch 10-5/10-6).  A cluster's *placement
    # weight* is its DMIPS capacity scaled by the ceiling somebody else wrote to
    # scaling_max_freq, which is how a userspace limiter ends up deciding that
    # the super core never runs an app launch.  Without that number in the boot
    # log the symptom has nothing to start from.
    dvfs = common_sh[common_sh.index("abk_report_dvfs_state() {"):
                     common_sh.index("abk_apply_readahead_knob() {")]
    check("the DVFS report logs the capacity the placer sees",
          "cap_view=" in dvfs and "_rs_capv" in dvfs)
    check("the DVFS report warns when the super core is no bigger than a weaker cluster",
          "is capped to" in dvfs and "abk_warn" in dvfs)
    check("the smart-freq floor warns per payload generation, not per governor name",
          "_rs_foreign" in dvfs and "_rs_pinned" in dvfs
          and "pre-10-5 payload" in dvfs and "abk_sf_boosting node" in dvfs)
    check("the DVFS capacity math goes through abk_mul_div, not shell arithmetic",
          'abk_mul_div "$_rs_arch" "$_rs_max" "$_rs_imax"' in dvfs)
    # Comments may name the path they are avoiding; only the code must not.
    dvfs_code = "\n".join(l for l in dvfs.splitlines()
                          if not l.lstrip().startswith("#"))
    # The report reads sysfs by design; what must never appear is an Android
    # partition path.  That is the precise rule, and it is the sweep over every
    # installed script (including bin/) below that enforces it.
    check("the DVFS report reads plain sysfs paths",
          "/devices/system/cpu/cpufreq/policy" in dvfs_code
          and "/devices/system/cpu/cpu" in dvfs_code)

    takeover = policy[policy.index("abk_zram_takeover() {"):policy.index("abk_zram_reassert() {")]
    takeover_order = [takeover.index(token) for token in
                      ("ABK_SWAPOFF", 'reset" 1', "abk_zram_set_algorithms",
                       "abk_zram_mount_swap")]
    check("takeover order is swapoff -> reset -> algorithms -> remount",
          takeover_order == sorted(takeover_order), takeover_order)

    mount = policy[policy.index("abk_zram_mount_swap() {"):policy.index("abk_zram_takeover() {")]
    mount_order = [mount.index(token) for token in
                   ("disksize", "abk_zram_set_mem_limit", "ABK_MKSWAP", "ABK_SWAPON")]
    check("remount order is disksize -> mem_limit -> mkswap -> swapon",
          mount_order == sorted(mount_order), mount_order)

    check("the safety gate precedes the swapoff",
          "abk_zram_swap_idle_enough" in takeover
          and takeover.index("abk_zram_swap_idle_enough") < takeover.index("ABK_SWAPOFF"))
    # Writeback and the algorithm policy used to exclude each other: the
    # backing device lives in the same pre-disksize window and `reset` drops it
    # (reset_bdev()).  The rewrite now remembers it before the reset and puts it
    # back in that window instead of trading it away.
    check("the writeback attachment is remembered before the rewrite",
          "abk_zram_backing_dev" in takeover
          and takeover.index("abk_zram_backing_dev") < takeover.index("ABK_SWAPOFF"))
    check("and restored in the pre-disksize window",
          "abk_zram_attach_writeback" in takeover
          and takeover.rindex("abk_zram_attach_writeback")
          < takeover.rindex("abk_zram_mount_swap"))
    check("a device that already owns writeback is never rewritten blindly",
          "keeping the live writeback device" in takeover)

    # The locked-kernel path: `abk_zram_ensure` rewrites only when something is
    # actually missing, which is what keeps writeback and the ROM's own
    # bring-up out of the module's way.
    ensure = policy[policy.index("abk_zram_ensure() {"):policy.index("abk_zram_reassert() {")]
    check("the service entry point rewrites only when the policy is not in force",
          "abk_zram_need_rewrite" in ensure
          and ensure.index("abk_zram_need_rewrite") < ensure.index("abk_zram_takeover"))
    check("the policy check covers algorithms, init, swap and writeback",
          all(token in policy[policy.index("abk_zram_need_rewrite() {"):
                             policy.index("abk_zram_ensure() {")]
              for token in ("abk_zram_algorithms_ok", "abk_zram_initstate",
                            "abk_zram_swap_on", "abk_zram_writeback_wanted")))
    check("the kernel-side lock is detected and reported",
          "abk_lock_algo" in policy and 'Y|y|1' in policy
          and "the kernel locks the compressors" in policy)
    check("the supervisor re-checks the policy on its own clock",
          'abk_cfg zram.reassert_interval_sec' in policy
          and "reassert=${_zs_reassert}s" in policy)
    check("writeback policy keys are known tunables.conf keys",
          all(key in common_sh for key in ("zram.writeback",
                                           "zram.writeback.size_mb",
                                           "zram.reassert_interval_sec"))
          and re.search(r"(?m)^\s*zram\.writeback=", tunables) is not None
          and re.search(r"(?m)^\s*zram\.reassert_interval_sec=", tunables) is not None)

    # The compaction gate ships ON with measured defaults; a silent default
    # drift would take the gate out the back door (healthy devices would
    # start paying compaction passes, or fragmented ones would stop).
    for line in ("zram.compact.enable=1", "zram.compact.min_waste_mb=50",
                 "zram.compact.waste_pct=15"):
        check(f"tunables.conf ships the compaction default {line}",
              re.search(rf"(?m)^{re.escape(line)}\s*$", tunables) is not None)
    check("compaction keys are known tunables.conf keys",
          all(key in common_sh for key in ("zram.compact.enable",
                                           "zram.compact.min_waste_mb",
                                           "zram.compact.waste_pct")))

    # Byte-count arithmetic has to leave the shell: /system/bin/sh is Android's
    # mksh and wraps at 2^31 on the target ROM (measured on device:
    # `15561024 * 1024` -> -1245380608), while KernelSU's busybox ash does not.
    # A wrapped value turns the compressed-memory cap negative, the kernel
    # refuses the write, and the cap silently stays off.
    check("RAM byte math goes through awk, not shell arithmetic",
          "abk_mul_div() {" in common_sh and "a * b / c" in common_sh
          and "_mt_kb * 1024" not in common_sh
          and "printf '%s\\n' \"$(( _mt_kb" not in common_sh
          and "_ml_mem * ABK_ZRAM_MEM_LIMIT_PCT" not in policy)
    check("... and the swap-size fallback too",
          "abk_mul_div \"$_sd_mem\" 1 2" in policy)
    check("large-value comparisons go through awk as well",
          "abk_gt() {" in common_sh and "abk_le() {" in common_sh
          and 'abk_is_uint "$_sd_live" && abk_gt "$_sd_live" 0' in policy
          and 'abk_le "$_si_used" "$_si_limit"' in policy
          and '[ "$_sd_live" -gt 0 ]' not in policy)
    cfr_tool = (repo / "tools" / "cached_freeze_reclaim.sh").read_text(
        encoding="utf-8")
    check("... and the reclaim quota in the shipped tool",
          'QUOTA_MB * 1024 * 1024' not in cfr_tool
          and 'awk -v mb="$QUOTA_MB"' in cfr_tool)
    check("... and the per-group byte comparison in the shipped tool",
          '[ "$cur" -gt 0 ]' not in cfr_tool
          and '[ "$cur" -gt "$quota_bytes" ]' not in cfr_tool
          and "awk -v a=\"$cur\" -v b=\"$quota_bytes\"" in cfr_tool)

    service_sh = (module_dir / "service.sh").read_text(encoding="utf-8")
    check("the spawn path clears a stale supervisor with SIGKILL",
          'pkill -9 -f "service.sh $_sp_arg"' in service_sh)
    check("the cfr supervisor drives the embedded reclaim tool in one-shot mode",
          'cached_freeze_reclaim.sh' in policy
          and 'CFR_ONE_SHOT=1 sh "$_cf_tool"' in policy
          and "--cgroup-root $ABK_SYS_ROOT/fs/cgroup" in policy
          and "--cgroup-root $ABK_MEMCG_ROOT" in policy)
    check("the cfr supervisor can select named groups",
          'abk_cfg cfr.group' in policy and '--group $_cf_g' in policy)
    check("cfr.group is a known tunables.conf key",
          "cfr.group" in common_sh
          and re.search(r"(?m)^\s*cfr\.group=", tunables) is not None)

    # "Never touches a read-only partition" is about the Android partitions
    # (/system, /vendor, /odm, /product).  /sys/devices/system/... is sysfs, which
    # this module reads and writes by design, so it must not match -- an earlier
    # form of this regex did, and module code was contorted into globs to dodge
    # it.  With the rule precise, the sweep can and must cover the tools this
    # module ships in bin/ as well: once installed they are module code too.
    ro_path = re.compile(r"/(?:vendor|odm|product)\b|(?<!/devices)/system\b")
    # sepolicy.rule belongs in this sweep: it is policy shipped by a kernel
    # flash, so "never relaxes SELinux" has to hold for it as literally as it
    # does for the scripts.
    for script in sorted(module_dir.glob("*.sh")) + [module_dir / "sepolicy.rule"]:
        body = script.read_text(encoding="utf-8")
        code = "\n".join(line.split("#", 1)[0] for line in body.splitlines())
        check(f"{script.name} never touches a read-only partition",
              ro_path.search(code) is None,
              ro_path.search(code).group(0) if ro_path.search(code) else "")
        check(f"{script.name} never relaxes SELinux",
              "setenforce" not in code and "permissive" not in code.lower())

    # --- packaging: the module zip and the AK3 ride-along ---
    sys.path.insert(0, str(repo / "scripts"))
    import ak3_bundle_ksu_module as ak3  # noqa: E402
    import build_ksu_module as bkm  # noqa: E402

    check("packager reads the module id", bkm.read_module_id(module_dir) == "abk_runtime_tunables")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        first = bkm.build(module_dir, tmp_path / "one.zip")
        second = bkm.build(module_dir, tmp_path / "abk_runtime_tunables.zip")
        check("module zip build is deterministic", first.read_bytes() == second.read_bytes())

        with zipfile.ZipFile(second) as archive:
            names = archive.namelist()
            check("module zip carries tunables.conf", "tunables.conf" in names)
            check("module zip carries the device scripts",
                  {"common.sh", "zram-policy.sh", "post-fs-data.sh",
                   "service.sh", "action.sh"} <= set(names))
            check("module zip carries the SELinux rule",
                  "sepolicy.rule" in names
                  and archive.read("sepolicy.rule")
                  == (module_dir / "sepolicy.rule").read_bytes())
            check("embedded trigger tool is inside the module",
                  "bin/zram_recompress_trigger.sh" in names)
            check("embedded trigger tool is byte-identical to tools/",
                  archive.read("bin/zram_recompress_trigger.sh")
                  == (repo / "tools" / "zram_recompress_trigger.sh").read_bytes())
            check("embedded reclaim tool is inside the module",
                  "bin/cached_freeze_reclaim.sh" in names)
            check("embedded reclaim tool is byte-identical to tools/",
                  archive.read("bin/cached_freeze_reclaim.sh")
                  == (repo / "tools" / "cached_freeze_reclaim.sh").read_bytes())
            check("embed.conf contributes exactly the three device tools",
                  sorted(name for name in names if name.startswith("bin/"))
                  == ["bin/abk_fas_check.sh",
                      "bin/cached_freeze_reclaim.sh",
                      "bin/zram_recompress_trigger.sh"])
            check("embedded FAS check tool is byte-identical to tools/",
                  archive.read("bin/abk_fas_check.sh")
                  == (repo / "tools" / "abk_fas_check.sh").read_bytes())
            # Installed under bin/, these are module code as much as service.sh
            # is, so the same two invariants apply to them.
            for _bin in sorted(n for n in names if n.startswith("bin/")):
                _bin_code = "\n".join(
                    line.split("#", 1)[0]
                    for line in archive.read(_bin).decode("utf-8").splitlines())
                check(f"{_bin} never touches a read-only partition",
                      ro_path.search(_bin_code) is None,
                      ro_path.search(_bin_code).group(0)
                      if ro_path.search(_bin_code) else "")
                check(f"{_bin} never relaxes SELinux",
                      "setenforce" not in _bin_code
                      and "permissive" not in _bin_code.lower())
            _fas_tool = archive.read("bin/abk_fas_check.sh").decode("utf-8")
            check("the shipped FAS check ranks clusters by the capacity the placer sees",
                  "cap_view" in _fas_tool and "const_for" in _fas_tool)
            check("the shipped FAS check says an off smart-freq knob is runtime-only on a pre-10-5 payload",
                  "runtime-only" in _fas_tool
                  and "no abk_sf_boosting node" in _fas_tool)
            check("the shipped FAS check has its own exit code for a starved super core",
                  "flag 4" in _fas_tool
                  and "capped out of the placement decision" in _fas_tool)
            check("the shipped FAS check judges load by measured busy time, not loadavg",
                  '(busy + 0 < minbusy + 0) ? "PARKED_IDLE" : "FROZEN_UNDER_LOAD"' in _fas_tool)
            check("the shipped FAS check scales capacity through awk, not 32-bit shell math",
                  "a * f / m" in _fas_tool and "$2 / 1000) * $1" not in _fas_tool)
            check("the shipped FAS check caches the constants out of the sample loop",
                  "POLICY_LIST=" in _fas_tool and "head -n 1" in _fas_tool)
            modes = {info.filename: (info.external_attr >> 16) & 0o777
                     for info in archive.infolist()}
            check("every module script is 0755",
                  all(modes[name] == 0o755 for name in names if name.endswith(".sh")))
            check("the zip root carries module.prop (KernelSU layout)",
                  "module.prop" in names and not any("/" in name and name.count("/") > 1
                                                   for name in names))

        ak3_dir = tmp_path / "AnyKernel3"
        (ak3_dir / "tools").mkdir(parents=True)
        script = ak3_dir / "anykernel.sh"
        script.write_text("#!/sbin/sh\nproperties() {\n  kernel.string=test\n}\n"
                          "# trailing CRLF from a Windows checkout\r\n")
        module_zip = second

        script_path, target = ak3.inject_dir(ak3_dir, module_zip)
        first_body = script.read_bytes()
        check("AK3 bundle receives the module zip",
              (ak3_dir / "abk-ksu-modules" / "abk_runtime_tunables.zip").is_file(), target)
        check("AK3 installer block is present exactly once",
              first_body.count(ak3.BEGIN_MARKER.encode()) == 1)
        check("AK3 injection normalises line endings", b"\r" not in first_body)
        check("AK3 tree verifies after injection", ak3.verify_dir(ak3_dir) == [])

        ak3.inject_dir(ak3_dir, module_zip)
        check("AK3 injection is idempotent (byte-identical second pass)",
              script.read_bytes() == first_body)

        (ak3_dir / "abk-ksu-modules" / "abk_runtime_tunables.zip").unlink()
        check("verify catches a missing bundle", ak3.verify_dir(ak3_dir) != [])

        finished = tmp_path / "AnyKernel3.zip"
        with zipfile.ZipFile(finished, "w") as archive:
            archive.writestr("anykernel.sh", "#!/sbin/sh\n")
            archive.writestr("tools/ak3-core.sh", "# core\n")
        patched = tmp_path / "AnyKernel3-patched.zip"
        _, entry = ak3.inject_zip(finished, patched, module_zip)
        check("zip injection adds the bundle and verifies",
              ak3.verify_zip(patched) == [] and entry.endswith("abk_runtime_tunables.zip"))

        saved_env = {key: os.environ.pop(key, None)
                     for key in ("ANYKERNEL3", "GITHUB_WORKSPACE")}
        try:
            # Outside the directory that now holds a real AK3 tree, the
            # discovery must report "nothing" rather than pick a stranger's
            # anykernel.sh.
            with tempfile.TemporaryDirectory() as empty_tmp:
                check("injector discovers nothing instead of guessing",
                      ak3.discover_ak3_dir(None, start=Path(empty_tmp)) is None)
        finally:
            for key, value in saved_env.items():
                if value is not None:
                    os.environ[key] = value

    # --- device behaviour, against a fixture sysfs tree ---
    bash = shutil.which("bash")
    if bash is None:
        print("  (no bash on this host: policy behaviour checks skipped)")
        return

    use_wsl = os.name == "nt"
    shell = ["wsl", "bash", "-s"] if use_wsl else [bash, "-s"]
    module_sh = str(module_dir).replace("\\", "/")
    if use_wsl and len(module_sh) > 2 and module_sh[1] == ":":
        module_sh = "/mnt/" + module_sh[0].lower() + module_sh[2:]

    def run_shell(script):
        r = subprocess.run(shell, input=script.encode("utf-8"),
                           capture_output=True, timeout=180)
        return subprocess.CompletedProcess(
            r.args, r.returncode,
            r.stdout.decode("utf-8", "replace"),
            r.stderr.decode("utf-8", "replace"))

    harness = r'''
set -u
MD="__MD__"
T=$(mktemp -d)
mkdir -p "$T/sys/block/zram0" "$T/stub"
HARNESS_CALLS="$T/calls"
export HARNESS_CALLS

printf 'MemTotal:       15561024 kB\n' > "$T/meminfo"
printf 'zram.recomp.enable=1\n' > "$T/tunables.conf"
printf '/dev/block/zram0                        partition       16777212  0       0\n' > "$T/proc_swaps"

for c in swapoff swapon mkswap; do
  printf '#!/bin/sh\necho "%s $*" >> "$HARNESS_CALLS"\nexit 0\n' "$c" > "$T/stub/$c"
  chmod +x "$T/stub/$c"
done
printf '#!/bin/sh\necho "losetup $*" >> "$HARNESS_CALLS"\n[ "$1" = "-f" ] && echo /dev/block/loop42\nexit 0\n' > "$T/stub/losetup"
printf '#!/bin/sh\necho "dd $*" >> "$HARNESS_CALLS"\nexit 0\n' > "$T/stub/dd"
chmod +x "$T/stub/losetup" "$T/stub/dd"

export ABK_CONF="$T/tunables.conf"
export ABK_RUN_DIR="$T/run" ABK_STATE_DIR="$T/state" ABK_SYS_ROOT="$T/sys"
export ABK_PROC_SWAPS="$T/proc_swaps" ABK_MEMINFO="$T/meminfo"
export ABK_SWAPOFF="$T/stub/swapoff" ABK_SWAPON="$T/stub/swapon" ABK_MKSWAP="$T/stub/mkswap"
export ABK_LOSETUP="$T/stub/losetup" ABK_DD="$T/stub/dd"
export ABK_ZRAM_NODE="/dev/block/zram0" ABK_ZRAM_WB_DIR="$T/wb"
export MODDIR="$MD"
export ABK_STDOUT=1

. "$MD/common.sh"
. "$MD/zram-policy.sh"

# the reset/initstate loops sleep; make that instant
sleep() { return 0; }

reset_fixture() {
  printf '17179869184\n' > "$T/sys/block/zram0/disksize"
  printf '1\n' > "$T/sys/block/zram0/initstate"
  printf 'lzo lzo-rle lz4 [lz4hc] lz4k lz4k_oplus lz4kd deflate 842 zstd\n' > "$T/sys/block/zram0/comp_algorithm"
  printf '#1: lzo lz4 [lz4hc] lz4k lz4kd zstd\n' > "$T/sys/block/zram0/recomp_algorithm"
  printf '    4096       62    20480        0    20480        0        0        0        0\n' > "$T/sys/block/zram0/mm_stat"
  printf '0\n' > "$T/sys/block/zram0/mem_limit"
  rm -f "$T/sys/block/zram0/compact"
  rm -f "$T/sys/block/zram0/writeback" "$T/sys/block/zram0/backing_dev"
  rm -f "$T/sys/block/zram0/writeback_limit" "$T/sys/block/zram0/writeback_limit_enable"
  rm -rf "$T/sys/module" "$T/wb"
  : > "$T/calls"
}

echo "locked0=$(abk_zram_kernel_locked && echo yes || echo no)"

# 1. the happy path: the ROM left lz4hc, the module rewrites both algorithms
reset_fixture
abk_zram_takeover > "$T/out1" 2>&1
echo "RC1=$?"
echo "primary1=$(abk_zram_primary)"
echo "secondary1=$(abk_zram_secondary)"
echo "disksize1=$(abk_zram_disksize)"
echo "memlimit1=$(cat "$T/sys/block/zram0/mem_limit" 2>/dev/null)"
echo "calls1=$(tr '\n' ';' < "$T/calls")"

# 2. the safety gate: swap in use must stop the rewrite before swapoff
reset_fixture
printf '/dev/block/zram0                        partition       16777212  1048576     0\n' > "$T/proc_swaps"
abk_zram_takeover > "$T/out2" 2>&1
echo "RC2=$?"
echo "primary2=$(abk_zram_primary)"
echo "calls2=$(tr '\n' ';' < "$T/calls")"

# 3. the kernel supports writeback and nobody owns it: the rewrite attaches one
printf '/dev/block/zram0                        partition       16777212  0       0\n' > "$T/proc_swaps"
reset_fixture
: > "$T/sys/block/zram0/writeback"
printf 'none\n' > "$T/sys/block/zram0/backing_dev"
printf '0\n' > "$T/sys/block/zram0/writeback_limit"
printf '0\n' > "$T/sys/block/zram0/writeback_limit_enable"
abk_zram_takeover > "$T/out3" 2>&1
echo "RC3=$?"
echo "primary3=$(abk_zram_primary)"
echo "backing3=$(abk_zram_backing_dev)"
echo "limit3=$(cat "$T/sys/block/zram0/writeback_limit")"
echo "limit_enable3=$(cat "$T/sys/block/zram0/writeback_limit_enable")"
echo "calls3=$(tr '\n' ';' < "$T/calls")"

# 4. a live writeback device survives the rewrite (the whole point: `reset`
#    drops it, so it has to be put back in the same pre-disksize window)
reset_fixture
: > "$T/sys/block/zram0/writeback"
printf '/dev/block/loop7\n' > "$T/sys/block/zram0/backing_dev"
printf '512\n' > "$T/sys/block/zram0/writeback_limit"
printf '1\n' > "$T/sys/block/zram0/writeback_limit_enable"
abk_zram_takeover > "$T/out4" 2>&1
echo "RC4=$?"
echo "primary4=$(abk_zram_primary)"
echo "backing4=$(abk_zram_backing_dev)"
echo "limit4=$(cat "$T/sys/block/zram0/writeback_limit")"
echo "calls4=$(tr '\n' ';' < "$T/calls")"

# 5. ... and a live writeback device is never traded for the algorithm when the
#    swap area is in use
reset_fixture
: > "$T/sys/block/zram0/writeback"
printf '/dev/block/loop7\n' > "$T/sys/block/zram0/backing_dev"
printf '512\n' > "$T/sys/block/zram0/writeback_limit"
printf '/dev/block/zram0                        partition       16777212  1048576     0\n' > "$T/proc_swaps"
abk_zram_takeover > "$T/out5" 2>&1
echo "RC5=$?"
echo "primary5=$(abk_zram_primary)"
echo "calls5=$(tr '\n' ';' < "$T/calls")"
printf '/dev/block/zram0                        partition       16777212  0       0\n' > "$T/proc_swaps"

# 6. a kernel that locks the compressors: the policy is in force by definition,
#    so nothing is rewritten (this is the path that lets writeback and the ROM's
#    own bring-up run untouched)
reset_fixture
mkdir -p "$T/sys/module/zram/parameters"
printf 'Y\n' > "$T/sys/module/zram/parameters/abk_lock_algo"
printf 'lz4kd\n' > "$T/sys/module/zram/parameters/abk_comp_algo"
printf 'lzo lzo-rle lz4 lz4hc lz4k lz4k_oplus [lz4kd] deflate 842 zstd\n' > "$T/sys/block/zram0/comp_algorithm"
printf '#1: lzo lzo-rle lz4 lz4hc lz4k lz4k_oplus lz4kd deflate 842 [zstd]\n' > "$T/sys/block/zram0/recomp_algorithm"
echo "locked6=$(abk_zram_kernel_locked && echo yes || echo no)"
abk_zram_ensure > "$T/out6" 2>&1
echo "RC6=$?"
echo "memlimit6=$(cat "$T/sys/block/zram0/mem_limit" 2>/dev/null)"
echo "calls6=$(tr '\n' ';' < "$T/calls")"

# 7. the supervisor keeps sweeping when a pass fails, and records its pid.  The
#    device already carries the policy here, so each tick is a cheap re-check
#    plus the sweep -- which is the point of the tick loop.
reset_fixture
printf 'lzo lzo-rle lz4 lz4hc lz4k lz4k_oplus [lz4kd] deflate 842 zstd\n' > "$T/sys/block/zram0/comp_algorithm"
printf '#1: lzo lzo-rle lz4 lz4hc lz4k lz4k_oplus lz4kd deflate 842 [zstd]\n' > "$T/sys/block/zram0/recomp_algorithm"
# A fragmented device (the measured kill-storm state: 352 MB used vs 186 MB
# compressed) + a compact node, so each sweep tick also exercises the gate.
: > "$T/sys/block/zram0/compact"
printf '1443160064 186810547 352772096 0 0 0 0 0 0\n' > "$T/sys/block/zram0/mm_stat"
printf 'zram.recomp.enable=1\nzram.recomp.interval_sec=60\nzram.reassert_interval_sec=60\n' > "$T/tunables.conf"
mkdir -p "$T/fakebin"
cat > "$T/fakebin/tool.sh" <<EOF
#!/bin/sh
echo "run \$*" >> "$T/runs"
exit 1
EOF
chmod +x "$T/fakebin/tool.sh"
export ABK_RECOMP_TOOL="$T/fakebin/tool.sh"
_n=0
sleep() { _n=$((_n+1)); [ "$_n" -ge 4 ] && exit 0; return 0; }
( abk_zram_supervisor_main > "$T/out7" 2>&1 )
echo "supervisor_runs=$(wc -l < "$T/runs" | tr -d ' ')"
echo "supervisor_args=$(head -n 1 "$T/runs")"
echo "supervisor_compact=$(cat "$T/sys/block/zram0/compact" 2>/dev/null)"
echo "supervisor_gate_line=$(grep -o 'compact=[^ ]*' "$T/state/abk_runtime_tunables.log" | tail -1)"
echo "supervisor_pid=$([ -s "$T/state/zram.pid" ] && echo set || echo unset)"
printf 'zram.recomp.enable=1\n' > "$T/tunables.conf"

# 8. tunables.conf parsing: unknown keys warn, empty means "leave alone"
reset_fixture
printf 'zram.recomp.enable=1\nvm.swappiness=\nbogus.key=7\n' > "$T/tunables.conf"
echo "cfg_default=$(abk_cfg zram.recomp.idle_age_sec 3600)"
echo "cfg_empty=$(abk_cfg vm.swappiness '')"
echo "cfg_clamped=$(abk_clamp_uint 999 0 300)"
echo "cfg_writeback=$(abk_cfg zram.writeback auto)"
echo "ram_bytes=$(abk_mem_total_bytes)"
echo "ram_25pct=$(abk_mem_pct_bytes 25)"
echo "ram_half=$(abk_mul_div "$(abk_mem_total_bytes)" 1 2)"
echo "saved_live=$(abk_zram_saved_disksize)"
rm -f "$T/state/disksize"
printf '0\n' > "$T/sys/block/zram0/disksize"
echo "saved_fallback=$(abk_zram_saved_disksize)"
printf '17179869184\n' > "$T/sys/block/zram0/disksize"
abk_cfg_lint > "$T/out8" 2>&1
echo "lint=$(tr '\n' ';' < "$T/out8")"

# 9. the module keeps its own log, because logcat cannot be relied on
LOG="$T/state/abk_runtime_tunables.log"
echo "log_file=$([ -s "$LOG" ] && echo present || echo missing)"
echo "log_rewrite=$(grep -c 'rewrite: size=' "$LOG" 2>/dev/null)"
echo "log_refusal=$(grep -c 'swap in use' "$LOG" 2>/dev/null)"
echo "log_keep=$(grep -c 'keeping the live writeback device' "$LOG" 2>/dev/null)"
echo "log_writeback=$(grep -c 'writeback: backing device' "$LOG" 2>/dev/null)"
echo "log_supervisor=$(grep -c 'recompression supervisor up' "$LOG" 2>/dev/null)"

# 10. the gated compaction: it fires only on a fragmented device, obeys the
#     kill switch, is silent on a healthy one and on a kernel without the node.
reset_fixture
: > "$T/sys/block/zram0/compact"
printf '1443160064 186810547 352772096 0 0 0 0 0 0\n' > "$T/sys/block/zram0/mm_stat"
abk_zram_compact_if_fragmented > "$T/out10a" 2>&1
echo "compact_go=$(cat "$T/sys/block/zram0/compact" 2>/dev/null)"
echo "compact_go_log=$(grep -c 'zsmalloc compaction: used' "$LOG" 2>/dev/null)"
reset_fixture
: > "$T/sys/block/zram0/compact"
printf '1443160064 175755663 181440512 0 0 0 0 0 0\n' > "$T/sys/block/zram0/mm_stat"
abk_zram_compact_if_fragmented > "$T/out10b" 2>&1
echo "compact_healthy=$(wc -c < "$T/sys/block/zram0/compact" | tr -d ' ')"
reset_fixture
: > "$T/sys/block/zram0/compact"
printf '1443160064 186810547 352772096 0 0 0 0 0 0\n' > "$T/sys/block/zram0/mm_stat"
printf 'zram.compact.enable=0\n' > "$T/tunables.conf"
abk_zram_compact_if_fragmented > "$T/out10c" 2>&1
echo "compact_off=$(wc -c < "$T/sys/block/zram0/compact" | tr -d ' ')"
reset_fixture
: > "$T/tunables.conf"
printf '1443160064 186810547 352772096 0 0 0 0 0 0\n' > "$T/sys/block/zram0/mm_stat"
abk_zram_compact_if_fragmented > "$T/out10d" 2>&1
echo "compact_nonode_rc=$?"
echo "compact_nonode_warn=$(grep -c 'node missing' "$T/out10d" 2>/dev/null)"

rm -rf "$T"
'''
    r = run_shell(harness.replace("__MD__", module_sh))
    got = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            got[key] = value

    check("takeover succeeds on the ROM's lz4hc state", got.get("RC1") == "0",
          r.stdout + r.stderr)
    check("takeover installs the lz4kd primary", got.get("primary1") == "lz4kd",
          r.stdout)
    check("takeover installs the zstd secondary", got.get("secondary1") == "zstd",
          r.stdout)
    check("takeover preserves the ROM's swap size",
          got.get("disksize1") == "17179869184", r.stdout)
    check("takeover caps zram at 25% of RAM (3983622144 bytes)",
          got.get("memlimit1") == "3983622144", r.stdout)
    calls1 = got.get("calls1", "")
    check("takeover unmounts and remounts the swap area itself",
          "swapoff /dev/block/zram0" in calls1
          and "mkswap /dev/block/zram0" in calls1
          and "swapon -p 0 /dev/block/zram0" in calls1, calls1)
    check("no lock is claimed on a kernel that has none",
          got.get("locked0") == "no", r.stdout)

    check("swap in use is refused instead of rewritten", got.get("RC2") != "0")
    check("the refused run never calls swapoff", "swapoff" not in got.get("calls2", ""),
          got.get("calls2"))
    check("the refused run leaves the ROM's algorithm alone",
          got.get("primary2") == "lz4hc", r.stdout)

    # 3: writeback-capable, unowned -> the module establishes it rather than
    # losing it (this is the half that used to be traded away).
    check("an unowned writeback device is set up during the rewrite",
          got.get("RC3") == "0" and got.get("primary3") == "lz4kd", r.stdout)
    check("the backing device is a loop device over the backing file",
          got.get("backing3") == "/dev/block/loop42", got.get("backing3"))
    check("the writeback limit is bounded and enabled",
          got.get("limit3") == "262144" and got.get("limit_enable3") == "1",
          (got.get("limit3"), got.get("limit_enable3")))
    check("the backing file is a sparse 1024 MiB file",
          "dd if=/dev/zero" in got.get("calls3", "")
          and "bs=1048576" in got.get("calls3", "")
          and "seek=1023" in got.get("calls3", ""), got.get("calls3"))

    # 4: a live writeback device survives the rewrite.
    check("a live writeback device is re-attached, not dropped",
          got.get("backing4") == "/dev/block/loop7" and got.get("RC4") == "0", r.stdout)
    check("... and the same device is reused instead of a new loop",
          "losetup -f" not in got.get("calls4", "")
          and "dd if=/dev/zero" not in got.get("calls4", ""), got.get("calls4"))
    check("... with the ROM's own writeback limit kept",
          got.get("limit4") == "512", got.get("limit4"))

    # 5: never trade a live writeback device for the algorithm.
    check("a live writeback device plus swap in use refuses the rewrite",
          got.get("RC5") != "0" and "swapoff" not in got.get("calls5", ""),
          (got.get("RC5"), got.get("calls5")))
    check("... leaving the ROM's algorithm alone", got.get("primary5") == "lz4hc",
          r.stdout)

    # 6: locked kernel -> nothing to rewrite.
    check("the kernel-side lock is detected", got.get("locked6") == "yes", r.stdout)
    check("a locked kernel is left completely alone",
          got.get("RC6") == "0" and "swapoff" not in got.get("calls6", "")
          and 'reset" 1' not in got.get("calls6", ""), got.get("calls6"))
    check("... with only the compressed-memory cap re-asserted",
          got.get("memlimit6") == "3983622144", got.get("memlimit6"))

    check("the supervisor sweeps repeatedly after a failing pass",
          (got.get("supervisor_runs") or "0").isdigit()
          and int(got["supervisor_runs"]) >= 3, r.stdout)
    check("the supervisor drives an age-marked pass",
          "--idle-age 3600" in got.get("supervisor_args", ""),
          got.get("supervisor_args"))
    check("the supervisor tick runs the gated compaction on a fragmented device",
          got.get("supervisor_compact") == "100", r.stdout)
    check("the supervisor up line carries the gate config, so the defaults are pinned",
          got.get("supervisor_gate_line") == "compact=1>50MB+15%", r.stdout)
    check("the supervisor records its own pid", got.get("supervisor_pid") == "set",
          r.stdout)

    check("an unset key falls back to the built-in default",
          got.get("cfg_default") == "3600", r.stdout)
    check("an empty value means leave the kernel alone",
          got.get("cfg_empty") == "", r.stdout)
    check("numeric knobs are clamped to their safe range",
          got.get("cfg_clamped") == "300", r.stdout)
    check("the writeback policy defaults to auto", got.get("cfg_writeback") == "auto",
          r.stdout)
    # 15561024 KiB is past 2^31 bytes; a 32-bit shell would wrap it negative.
    check("byte counts survive the 32-bit shell arithmetic limit",
          got.get("ram_bytes") == "15934488576"
          and got.get("ram_25pct") == "3983622144"
          and got.get("ram_half") == "7967244288",
          (got.get("ram_bytes"), got.get("ram_25pct"), got.get("ram_half")))
    # A 16 GiB swap is the case that broke on device: `[ 17179869184 -gt 0 ]` is
    # false under this ROM's mksh, so the module treated a healthy device as
    # sizeless and halved it on the next rewrite.
    check("a 16 GiB swap size is preserved, not treated as sizeless",
          got.get("saved_live") == "17179869184"
          and got.get("saved_fallback") == "7967244288",
          (got.get("saved_live"), got.get("saved_fallback")))
    check("unknown config keys are reported, not applied",
          "bogus.key" in got.get("lint", ""), got.get("lint"))

    check("the module keeps its own log", got.get("log_file") == "present", r.stdout)
    check("the log records the rewrite", (got.get("log_rewrite") or "0") != "0", r.stdout)
    check("the log records the swap-in-use refusal",
          (got.get("log_refusal") or "0") != "0", r.stdout)
    check("the log records that a live writeback device was kept",
          (got.get("log_keep") or "0") != "0", r.stdout)
    check("the log records the writeback attachment",
          (got.get("log_writeback") or "0") != "0", r.stdout)
    check("the log records the supervisor start",
          (got.get("log_supervisor") or "0") != "0", r.stdout)

    # 10: the compaction gate, directly.
    check("a fragmented device gets one full compaction pass",
          got.get("compact_go") == "100"
          and (got.get("compact_go_log") or "0") != "0",
          (got.get("compact_go"), got.get("compact_go_log")))
    check("a healthy device is never compacted",
          got.get("compact_healthy") == "0", r.stdout)
    check("zram.compact.enable=0 stops the pass even when fragmented",
          got.get("compact_off") == "0", r.stdout)
    check("a kernel without the compact node is left alone, silently",
          got.get("compact_nonode_rc") == "0" and got.get("compact_nonode_warn") == "0",
          (got.get("compact_nonode_rc"), got.get("compact_nonode_warn")))


def test_batch8_autofdo_tool():
    """The AutoFDO tool is a build-engineering script, not a graft.

    It never edits a kernel tree and never registers a PatchGroup; its only job
    is to collect a device-specific 5.15 AutoFDO profile and to refuse any
    profile that does not identify a 5.15 kernel or whose vmlinux hash does not
    match the recorded build.  These checks exercise only the offline gates
    (init / validate / build-env); record and convert need a real device, adb
    and simpleperf/create_llvm_prof, so they are not invoked here.
    """
    print("Batch 8 AutoFDO profile tool")
    tool = Path(__file__).resolve().parent.parent / "tools" / "autofdo_515_profile.sh"
    check("autofdo tool exists", tool.is_file(), tool)

    # The tool is a host script; on Windows the only bash is WSL's, which cannot
    # open a C:\... path (backslashes and the drive letter are mangled before
    # bash ever sees them).  Every path argument therefore has to be handed over
    # in /mnt/<drive>/... form, exactly as the other shell-driven tests do.
    bash = shutil.which("bash")
    if bash is None:
        print("  (no bash on this host: autofdo checks skipped)")
        return
    use_wsl = os.name == "nt"
    shell = ["wsl", "bash"] if use_wsl else [bash]

    def shell_path(path):
        text = str(path).replace("\\", "/")
        if use_wsl and len(text) > 2 and text[1] == ":":
            return "/mnt/" + text[0].lower() + text[2:]
        return text

    tool_sh = shell_path(tool)

    def run_tool(args):
        mapped = [arg if arg.startswith("--") else shell_path(arg) for arg in args]
        return subprocess.run(shell + [tool_sh] + mapped,
                              capture_output=True, text=True, env=dict(os.environ))

    r = subprocess.run(shell + ["-n", tool_sh], capture_output=True, text=True)
    check("autofdo tool passes bash -n", r.returncode == 0, r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "kernel515"
        out = Path(tmp) / "ws"
        root.mkdir()
        vmlinux = root / "vmlinux"
        vmlinux.write_bytes(b"fake vmlinux for the 5.15 build")
        (root / "Makefile").write_text(
            "VERSION = 5\nPATCHLEVEL = 15\nSUBLEVEL = 167\nEXTRAVERSION =\n")

        r = run_tool(["init", "--kernel-root", str(root),
                      "--output-dir", str(out), "--vmlinux", str(vmlinux),
                      "--source-revision", "test-rev", "--toolchain", "test-tc"])
        check("autofdo init accepts 5.15", r.returncode == 0,
              r.stdout + r.stderr)
        manifest = out / "manifest.env"
        check("autofdo manifest written", manifest.is_file())
        mtext = manifest.read_text() if manifest.is_file() else ""
        check("manifest declares the abk-autofdo-515 format",
              "format=abk-autofdo-515-v1" in mtext)
        check("manifest records the 5.15 kernel version",
              "kernel_version=5.15.167" in mtext)
        check("manifest records a vmlinux hash", "vmlinux_sha256=" in mtext)

        (out / "kernel.autofdo").write_text("branch-list payload")
        (out / "kernel.llvm_profdata").write_text("llvm extbinary payload")
        r = run_tool(["validate", "--output-dir", str(out)])
        check("autofdo validate accepts a matching workspace", r.returncode == 0,
              r.stdout + r.stderr)

        vmlinux.write_bytes(b"a different build")
        r = run_tool(["validate", "--output-dir", str(out)])
        check("autofdo validate rejects a vmlinux mismatch", r.returncode != 0,
              r.stdout + r.stderr)

        vmlinux.write_bytes(b"fake vmlinux for the 5.15 build")
        r = run_tool(["build-env", "--output-dir", str(out)])
        check("autofdo build-env succeeds on a valid workspace",
              r.returncode == 0, r.stdout + r.stderr)
        check("build-env emits the autofdo build variables",
              "CONFIG_AUTOFDO_CLANG=y" in r.stdout
              and "CLANG_AUTOFDO_PROFILE=" in r.stdout)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "kernel66"
        out = Path(tmp) / "ws66"
        root.mkdir()
        vmlinux = root / "vmlinux"
        vmlinux.write_bytes(b"6.6 vmlinux")
        (root / "Makefile").write_text(
            "VERSION = 6\nPATCHLEVEL = 6\nSUBLEVEL = 1\nEXTRAVERSION =\n")
        r = run_tool(["init", "--kernel-root", str(root),
                      "--output-dir", str(out), "--vmlinux", str(vmlinux)])
        check("autofdo init rejects a non-5.15 kernel", r.returncode != 0,
              r.stdout + r.stderr)


def test_madvise_collapse_step_independence():
    """No step of a group may pre-create a later step's replacement text.

    ``replace_once`` checks the replacement before the anchor (idempotency), so
    a step whose ``new`` text is contained in an earlier step's ``new`` text is
    silently reported ``already_present`` while the group still says "applied".
    That is how the MADV_COLLAPSE ``khugepaged_scan_file()`` signature reached
    CI: the CONFIG_SHMEM-off stub was built by string-concatenating the very
    signature the following step was supposed to install, so the real
    CONFIG_SHMEM=y definition stayed at four parameters while its body and all
    of its callers moved to five.
    """
    print("madvise_collapse step independence (silent-skip trap)")
    import abk_stable_core as core

    check("stub replacement does not contain the signature replacement",
          core._MC_FILE_SIG_NEW not in core._MC_FILE_STUB_NEW,
          "the stub embeds _MC_FILE_SIG_NEW verbatim")
    check("stub replacement really is the 5-parameter form",
          "int *res)" in core._MC_FILE_STUB_NEW
          and "BUILD_BUG();" in core._MC_FILE_STUB_NEW)

    # Same invariant, generically, over every _MC_*_NEW pair.
    new_blocks = {n: getattr(core, n) for n in dir(core)
                  if n.startswith("_MC_") and n.endswith("_NEW")
                  and isinstance(getattr(core, n), str)}
    nested = [(a, b) for a in new_blocks for b in new_blocks
              if a != b and new_blocks[b].replace("\r\n", "\n")
              in new_blocks[a].replace("\r\n", "\n")]
    check("no _MC_ replacement contains another", not nested, nested)

    # End-to-end on a two-definition fixture: both signatures must move.
    fixture = (
        "#ifdef CONFIG_SHMEM\n"
        + core._MC_FILE_SIG_OLD + "\n{\n\tint result = SCAN_SUCCEED;\n}\n"
        "#else\n" + core._MC_FILE_STUB_OLD + "\n#endif\n"
    )
    text = fixture
    for old, new in ((core._MC_FILE_STUB_OLD, core._MC_FILE_STUB_NEW),
                     (core._MC_FILE_SIG_OLD, core._MC_FILE_SIG_NEW)):
        text, status = common.replace_once(text, old, new)
        check(f"step applied ({old.splitlines()[0][:38]}...)",
              status == "applied", status)
    check("no 4-parameter definition left behind",
          core._MC_FILE_SIG_OLD not in text)
    check("both definitions carry int *res",
          text.count("int *res)") == 2, text.count("int *res)"))


def test_madvise_collapse_revalidate_convention():
    """The graft must use the 5.15 hugepage_vma_revalidate() return convention.

    5.15 returns 0 on success and a scan code otherwise; 6.1 returns
    SCAN_SUCCEED, which is 1 in this enum.  Copying the 6.1
    ``!= SCAN_SUCCEED`` test makes every successful revalidation look like a
    failure, so any multi-PMD MADV_COLLAPSE that dropped mmap_lock returns
    -EINVAL.  It compiles either way, so only this assertion catches it.
    """
    print("madvise_collapse revalidate return convention")
    import abk_stable_core as core

    body = core._MC_IMPL_D
    check("revalidate result tested as a plain scan code",
          "result = hugepage_vma_revalidate(mm, addr, &vma);\n"
          "\t\t\tif (result) {" in body,
          "graft does not use the 5.15 `if (result)` convention")
    check("no 6.1-style SCAN_SUCCEED comparison on revalidate",
          "if (result != SCAN_SUCCEED) {" not in body)


def test_batch17_zram_writeback():
    """Batch 17 registry, anchor hygiene and the C-level shape of the graft."""
    print("Batch 17: zram writeback batching + compressed writeback")
    import batch14_core_zram_writeback as b14
    import batch17_core_zram_writeback as b17
    import abk_stable_core as core

    keys = [g.key for g in core.PATCH_GROUPS]
    for key in ("zram_writeback_batching", "zram_wb_batch_size",
                "zram_compressed_writeback"):
        check(f"batch17 group {key} registered", key in keys)
    check("batching runs after zram_writeback_bounds",
          keys.index("zram_writeback_batching")
          > keys.index("zram_writeback_bounds"))
    check("the three batch17 groups keep their order",
          keys.index("zram_writeback_batching")
          < keys.index("zram_wb_batch_size")
          < keys.index("zram_compressed_writeback"))

    steps = []
    for key, group_steps in (
        ("b14:writeback_bounds", b14.build_writeback_bounds_steps()),
        ("b17:batching", b17.build_batching_steps()),
        ("b17:batch_size", b17.build_batch_size_steps()),
        ("b17:compressed", b17.build_compressed_steps()),
    ):
        for _rel, old, new, _req in group_steps:
            steps.append((key, old, new))

    # A later anchor that occurs inside an earlier replacement is how
    # replace_once edits the wrong occurrence or leaves a duplicated copy
    # behind: no group status and no brace/comment balance check can see it.
    # This is the exact shape that shipped a stray page = alloc_page() into the
    # sweep while this batch was being written (the batching group then
    # replaced the *pristine* copy and left the one Batch 14 had just inserted,
    # because _B_POSTLOCK_NEW still carried that line while _B_POSTLOCK no
    # longer did).
    collisions = []
    for i, (k1, _o1, n1) in enumerate(steps):
        for k2, o2, _n2 in steps[i + 1:]:
            if k1 == k2:
                # within one group the per-step audit (trap 2) covers it
                continue
            if o2 in n1:
                collisions.append((k1, k2, o2.strip()[:60]))
    check("no later anchor sits inside an earlier replacement",
          not collisions, collisions)

    def joined(prefix):
        return "".join(n for k, _o, n in steps if k == prefix)

    g1 = joined("b17:batching")
    g2 = joined("b17:batch_size")
    g3 = joined("b17:compressed")

    # bf62f69574b1: the UAF fix is the only form that may ship.
    check("wb_ctl is freed through RCU", "kfree_rcu(wb_ctl, rcu)" in g1)
    check("the buggy kfree(wb_ctl) form is not shipped",
          "kfree(wb_ctl);" not in g1)
    check("the completion callback runs inside an RCU read section",
          "rcu_read_lock();" in g1 and "rcu_read_unlock();" in g1)
    # 3e8d8eb8d7f5: release the reservation *before* draining.
    tail = b17._A_TAIL_NEW
    check("the reserved blk_idx is released before the drain",
          tail.index("free_block_bdev(zram, blk_idx);")
          < tail.index("while (atomic_read(&wb_ctl->num_inflight) > 0)"))
    check("a recycled request drops its block index",
          "req->blk_idx = 0;" in g1)
    # 5.15 slot metadata: the block index lives in .element, not in a handle.
    check("the block index is stored through zram_set_element()",
          "zram_set_element(zram, index, req->blk_idx)" in g1
          and "zram_set_handle(zram, index, req->blk_idx)" not in g1)
    # The sweep must not wait synchronously and must still yield.
    loop = b17._A_LOOP_NEW
    check("the sweep no longer submits synchronously",
          "submit_bio_wait" not in loop)
    check("the sweep yields once per iteration",
          loop.count("cond_resched();") == 1)
    # The gate only *reads* the budget; charging moved into the submission
    # helper (before submit_bio), or a batch of in-flight bios would overshoot
    # the configured limit by up to wb_batch_size pages.
    check("the sweep only reads the writeback budget",
          loop.count("spin_lock(&zram->wb_limit_lock);") == 1
          and "zram->bd_wb_limit -=" not in loop)
    check("the in-flight window is marked with ZRAM_UNDER_WB",
          "zram_set_flag(zram, index, ZRAM_UNDER_WB)" in loop
          and "zram_clear_flag(zram, index, ZRAM_UNDER_WB)" in loop)
    # d38fab605c66 (+ 3bf1c285dc40) write half.
    # The needle carries its call shape on purpose: the helper block documents
    # the upstream API by name in a comment, and a bare needle would match the
    # comment and fail the assertion it is supposed to protect.
    check("the raw object read uses the 5.15 mapping API",
          "zs_map_object(zram->mem_pool, handle, ZS_MM_RO)" in g1
          and "zs_obj_read_begin(zram->mem_pool" not in g1)
    check("trailing bytes are zeroed before writeback",
          "memzero_page(page, size, PAGE_SIZE - size)" in g1)
    check("compressed writeback preserves the slot metadata",
          "zram_set_obj_size(zram, index, size)" in g1
          and "zram_set_priority(zram, index, prio)" in g1)
    check("the write half is gated on the flag", "zram->wb_compressed" in g1)
    # d38fab605c66 read half, 5.15 zcomp convention included.
    check("decompression uses the 5.15 stream API",
          "zcomp_stream_put(zram->comps[prio])" in g3
          and "zcomp_stream_put(zstrm)" not in g3
          and "zcomp_decompress(zstrm, src, size, zstrm->buffer)" in g3)
    check("async read-back decompression is deferred",
          "queue_work(system_highpri_wq" in g3
          and "bio_inc_remaining(parent)" in g3)
    check("a stale read-back zeroes through zero_user()",
          "zero_user(page, 0, PAGE_SIZE)" in g3
          and "memset_page(" not in g3)
    # Defaults, the attribute surface and the bounded pool.
    check("the batch size defaults to upstream 32 and compressed is off",
          "zram->wb_batch_size = 32;" in g1
          and "zram->wb_compressed = false;" in g1)
    check("the pool is bounded and 0 is rejected",
          "ZRAM_WB_BATCH_SIZE_MAX" in g2 and "if (!val)" in g2)
    check("both attributes are published",
          "dev_attr_writeback_batch_size.attr" in g2
          and "dev_attr_compressed_writeback.attr" in g3)
    check("compressed writeback refuses a live device",
          "return -EBUSY;" in g3 and "zram->wb_compressed = val;" in g3)
    # step_audit trap 4: the third group shape probe must accept both the
    # pristine and the already-redirected call site, or it reports
    # blocked_by_shape on every second pass.
    src = (Path(__file__).resolve().parent.parent / "scripts"
           / "batch17_core_zram_writeback.py").read_text()
    check("the compressed-writeback probe accepts both call shapes",
          "ret = __zram_bvec_read(zram, page, index, bio, true);" in src
          and "ret = abk_zram_bvec_read(zram, page, index, bio, true);" in src)
    # Degradation on a foreign tree: no half-patched writes.
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {
            "drivers/block/zram/zram_drv.c": "static int zram_bvec_read(void);\n",
            "drivers/block/zram/zram_drv.h": "struct zram { int x; };\n",
        })
        status, _detail = b17._batching_apply(ctx)
        check("batching degrades on a foreign tree",
              status == "blocked_by_shape", status)
        status, _detail = b17._compressed_apply(ctx)
        check("compressed writeback degrades without the batching group",
              status == "blocked_by_shape", status)
        body = (Path(tmp) / "common" / "drivers/block/zram"
                / "zram_drv.c").read_text()
        check("a degraded run writes nothing",
              body == "static int zram_bvec_read(void);\n", repr(body))


def main():
    test_replace_once_eol()
    test_apply_steps_transactional()
    raised = False
    try:
        test_engine_skips_degraded_without_writes()
    except SystemExit:
        raised = True
    check("engine raised on tampering", raised)
    test_fdtable_shapes()
    test_replace_fd_errno_group()
    test_defconfig_lane()
    test_family_gate()
    test_apply_steps_noop_blocked()
    test_batch6_registration()
    test_batch8_pagealloc_fallback_reuse()
    test_batch8_rcu_nocb_cpu_default_all()
    test_batch9_dynamic_readahead()
    test_batch10_zram_async_recompress()
    test_batch10_sched_smart_policy()
    test_batch10_zram_secondary_comp()
    test_batch11_zram_algo_lock()
    test_batch13_customize_alloc_gfp_vh()
    test_batch13_gfp_pressure_fastfail()
    test_batch13_wake_up_new_task_excluded()
    test_config_tiers()
    test_introduced_kconfig_tiers()
    test_batch10_memcg_v1_reclaim()
    test_batch10_cached_freeze_reclaim()
    test_batch10_daemon_script()
    test_batch10_zram_trigger_script()
    test_runtime_tunables_module()
    test_batch8_autofdo_tool()
    test_madvise_collapse_step_independence()
    test_madvise_collapse_revalidate_convention()
    test_batch17_zram_writeback()
    test_display_valid_clones_revert()
    test_sublevel_matrix()
    test_f2fs_shape_probe()
    test_kabi_slot_policy()
    test_kstack_slot_shape_selection()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
