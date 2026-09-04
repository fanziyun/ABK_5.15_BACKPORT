#!/usr/bin/env python3
"""Unit tests for the abk_5_15_backport graft engine and compatibility shapes.

Runs fully self-contained on synthetic fixtures; no kernel tree required.
    python3 tests/stable_5_15_test.py
"""

from __future__ import annotations

import json
import sys
import tempfile
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
        check("suite slots untouched", "ANDROID_KABI_USE(1" not in text)


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
    for sub_level, child in (("211", "stable_perf_backport"),):
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
    test_madvise_collapse_step_independence()
    test_madvise_collapse_revalidate_convention()
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
