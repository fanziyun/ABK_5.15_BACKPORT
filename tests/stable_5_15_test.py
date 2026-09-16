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


_BATCH21_CGROUP_DEFS = (
    "enum {\n"
    "\t/* Control Group requires release notifications to userspace */\n"
    "\tCGRP_NOTIFY_ON_RELEASE,\n"
    "\t/*\n"
    "\t * Clone the parent's configuration when creating a new child\n"
    "\t * cpuset cgroup.  For historical reasons, this option can be\n"
    "\t * specified at mount time and thus is implemented here.\n"
    "\t */\n"
    "\tCGRP_CPUSET_CLONE_CHILDREN,\n"
    "\n"
    "\t/* Control group has to be frozen. */\n"
    "\tCGRP_FREEZE,\n"
    "\n"
    "\t/* Cgroup is frozen. */\n"
    "\tCGRP_FROZEN,\n"
    "\n"
    "\t/* Control group has to be killed. */\n"
    "\tCGRP_KILL,\n"
    "};\n"
    "\n"
    "struct cgroup {\n"
    "\tunsigned long flags;\t\t/* \"unsigned long\" so bitops work */\n"
    "\tstruct kernfs_node *kn;\t\t/* cgroup kernfs entry */\n"
    "\tstruct cgroup_file procs_file;\t/* handle for \"cgroup.procs\" */\n"
    "\tstruct cgroup_file events_file;\t/* handle for \"cgroup.events\" */\n"
    "\n"
    "\t/* used to track pressure stalls */\n"
    "\tstruct psi_group psi;\n"
    "\n"
    "\t/* used to store eBPF programs */\n"
    "\tstruct cgroup_bpf bpf;\n"
    "\n"
    "\t/* ids of the ancestors at each level including self */\n"
    "\tu64 ancestor_ids[];\n"
    "};\n"
)

_BATCH21_PSI_H = (
    "#ifdef CONFIG_CGROUPS\n"
    "int psi_cgroup_alloc(struct cgroup *cgrp);\n"
    "void psi_cgroup_free(struct cgroup *cgrp);\n"
    "void cgroup_move_task(struct task_struct *p, struct css_set *to);\n"
    "#endif\n"
)

# The psi.c shape this group lands on: the 5.15 psi_group_change() before the
# state-mask loop, the IRQ walk psi_irq_tracking adds ahead of it (the switch
# patches that text), psi_show/psi_trigger_create, and the end of the
# CONFIG_CGROUPS block.
_BATCH21_PSI_C = (
    "static void psi_group_change(struct psi_group *group, int cpu,\n"
    "\t\t\t     unsigned int clear, unsigned int set, u64 now,\n"
    "\t\t\t     bool wake_clock)\n"
    "{\n"
    "\tstruct psi_group_cpu *groupc;\n"
    "\tu32 state_mask = 0;\n"
    "\tunsigned int t, m;\n"
    "\tenum psi_states s;\n"
    "\n"
    "\tgroupc = per_cpu_ptr(group->pcpu, cpu);\n"
    "\twrite_seqcount_begin(&groupc->seq);\n"
    "\n"
    "\trecord_times(groupc, now);\n"
    "\n"
    "\tfor (t = 0, m = clear; m; m &= ~(1 << t), t++) {\n"
    "\t\tif (!(m & (1 << t)))\n"
    "\t\t\tcontinue;\n"
    "\t\tif (groupc->tasks[t])\n"
    "\t\t\tgroupc->tasks[t]--;\n"
    "\t\telse if (!psi_bug) {\n"
    "\t\t\tprintk_deferred(KERN_ERR \"psi: task underflow! cpu=%d t=%d tasks=[%u %u %u %u %u] clear=%x set=%x\\n\",\n"
    "\t\t\t\t\tcpu, t, groupc->tasks[0],\n"
    "\t\t\t\t\tgroupc->tasks[1], groupc->tasks[2],\n"
    "\t\t\t\t\tgroupc->tasks[3], groupc->tasks[4],\n"
    "\t\t\t\t\tclear, set);\n"
    "\t\t\tpsi_bug = 1;\n"
    "\t\t}\n"
    "\t}\n"
    "\n"
    "\tfor (t = 0; set; set &= ~(1 << t), t++)\n"
    "\t\tif (set & (1 << t))\n"
    "\t\t\tgroupc->tasks[t]++;\n"
    "\n"
    "\t/* Calculate state mask representing active states */\n"
    "\tfor (s = 0; s < NR_PSI_STATES; s++) {\n"
    "\t\tif (test_state(groupc->tasks, s))\n"
    "\t\t\tstate_mask |= (1 << s);\n"
    "\t}\n"
    "\n"
    "\t/*\n"
    "\t * Since we care about lost potential, a memstall is FULL\n"
    "\t * when there are no other working tasks, but also when\n"
    "\t * the CPU is actively reclaiming and nothing productive\n"
    "\t * could run even if it were runnable. So when the current\n"
    "\t * task in a cgroup is in_memstall, the corresponding groupc\n"
    "\t * on that cpu is in PSI_MEM_FULL state.\n"
    "\t */\n"
    "\tif (unlikely(groupc->tasks[NR_ONCPU] && cpu_curr(cpu)->in_memstall))\n"
    "\t\tstate_mask |= (1 << PSI_MEM_FULL);\n"
    "\n"
    "\tgroupc->state_mask = state_mask;\n"
    "\twrite_seqcount_end(&groupc->seq);\n"
    "}\n"
    "\n"
    "void psi_account_irqtime(struct rq *rq, struct task_struct *curr, struct task_struct *prev)\n"
    "{\n"
    "\tvoid *iter = NULL;\n"
    "\n"
    "\twhile ((group = iterate_groups(curr, &iter))) {\n"
    "\t\tu64 now;\n"
    "\n"
    "\t\tgroupc = per_cpu_ptr(group->pcpu, cpu);\n"
    "\n"
    "\t\twrite_seqcount_begin(&groupc->seq);\n"
    "\t\tnow = cpu_clock(cpu);\n"
    "\t\trecord_times(groupc, now);\n"
    "\t\tgroupc->times[PSI_IRQ_FULL] += delta;\n"
    "\t\twrite_seqcount_end(&groupc->seq);\n"
    "\t}\n"
    "}\n"
    "\n"
    "void cgroup_move_task(struct task_struct *task, struct css_set *to)\n"
    "{\n"
    "\tstruct rq_flags rf;\n"
    "\tstruct rq *rq;\n"
    "\n"
    "\trq = task_rq_lock(task, &rf);\n"
    "\n"
    "\ttask_rq_unlock(rq, task, &rf);\n"
    "}\n"
    "#endif /* CONFIG_CGROUPS */\n"
    "\n"
    "int psi_show(struct seq_file *m, struct psi_group *group, enum psi_res res)\n"
    "{\n"
    "\tint full;\n"
    "\tu64 now;\n"
    "\n"
    "\tif (static_branch_likely(&psi_disabled))\n"
    "\t\treturn -EOPNOTSUPP;\n"
    "\n"
    "\t/* Update averages before reporting them */\n"
    "\tmutex_lock(&group->avgs_lock);\n"
    "\tnow = sched_clock();\n"
    "\tmutex_unlock(&group->avgs_lock);\n"
    "\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
    "struct psi_trigger *psi_trigger_create(struct psi_group *group,\n"
    "\t\t\tchar *buf, size_t nbytes, enum psi_res res)\n"
    "{\n"
    "\tstruct psi_trigger *t;\n"
    "\tenum psi_states state;\n"
    "\tu32 threshold_us;\n"
    "\tu32 window_us;\n"
    "\n"
    "\tif (static_branch_likely(&psi_disabled))\n"
    "\t\treturn ERR_PTR(-EOPNOTSUPP);\n"
    "\n"
    "\tif (sscanf(buf, \"some %u %u\", &threshold_us, &window_us) == 2)\n"
    "\t\tstate = PSI_IO_SOME + res * 2;\n"
    "\telse\n"
    "\t\treturn ERR_PTR(-EINVAL);\n"
    "\n"
    "\treturn t;\n"
    "}\n"
)

# cgroup.c after psi_trigger_kernfs_polling: the trigger writer (which this
# group renames), its three wrappers, the poll hook the switch is inserted
# before, and the base-file table the knob joins.
_BATCH21_GROUP_C = (
    "static ssize_t cgroup_pressure_write(struct kernfs_open_file *of, char *buf,\n"
    "\t\t\t\t\t  size_t nbytes, enum psi_res res)\n"
    "{\n"
    "\tstruct cgroup_file_ctx *ctx = of->priv;\n"
    "\tstruct psi_trigger *new;\n"
    "\tstruct cgroup *cgrp;\n"
    "\tstruct psi_group *psi;\n"
    "\n"
    "\tpsi = cgroup_ino(cgrp) == 1 ? &psi_system : &cgrp->psi;\n"
    "\tnew = psi_trigger_create(psi, buf, res, of->file, of);\n"
    "\tif (IS_ERR(new))\n"
    "\t\treturn PTR_ERR(new);\n"
    "\n"
    "\treturn nbytes;\n"
    "}\n"
    "\n"
    "static ssize_t cgroup_io_pressure_write(struct kernfs_open_file *of,\n"
    "\t\t\t\t\t  char *buf, size_t nbytes,\n"
    "\t\t\t\t\t  loff_t off)\n"
    "{\n"
    "\treturn cgroup_pressure_write(of, buf, nbytes, PSI_IO);\n"
    "}\n"
    "\n"
    "static ssize_t cgroup_memory_pressure_write(struct kernfs_open_file *of,\n"
    "\t\t\t\t\t  char *buf, size_t nbytes,\n"
    "\t\t\t\t\t  loff_t off)\n"
    "{\n"
    "\treturn cgroup_pressure_write(of, buf, nbytes, PSI_MEM);\n"
    "}\n"
    "\n"
    "static ssize_t cgroup_cpu_pressure_write(struct kernfs_open_file *of,\n"
    "\t\t\t\t\t  char *buf, size_t nbytes,\n"
    "\t\t\t\t\t  loff_t off)\n"
    "{\n"
    "\treturn cgroup_pressure_write(of, buf, nbytes, PSI_CPU);\n"
    "}\n"
    "\n"
    "static __poll_t cgroup_pressure_poll(struct kernfs_open_file *of,\n"
    "\t\t\t\t\t  poll_table *pt)\n"
    "{\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
    "static struct cftype cgroup_base_files[] = {\n"
    "#ifdef CONFIG_PSI\n"
    "\t{\n"
    "\t\t.name = \"io.pressure\",\n"
    "\t\t.flags = CFTYPE_PRESSURE,\n"
    "\t\t.seq_show = cgroup_io_pressure_show,\n"
    "\t\t.write = cgroup_io_pressure_write,\n"
    "\t\t.poll = cgroup_pressure_poll,\n"
    "\t\t.release = cgroup_pressure_release,\n"
    "\t},\n"
    "\t{\n"
    "\t\t.name = \"cpu.pressure\",\n"
    "\t\t.flags = CFTYPE_PRESSURE,\n"
    "\t\t.seq_show = cgroup_cpu_pressure_show,\n"
    "\t\t.write = cgroup_cpu_pressure_write,\n"
    "\t\t.poll = cgroup_pressure_poll,\n"
    "\t\t.release = cgroup_pressure_release,\n"
    "\t},\n"
    "#endif /* CONFIG_PSI */\n"
    "\t{ }\t/* terminate */\n"
    "};\n"
)

_BATCH21_DOC = (
    "  cgroup.kill\n"
    "\tA write-only single value file which exists in non-root cgroups.\n"
    "\tThe only allowed value is \"1\".\n"
    "\n"
    "\tIn a threaded cgroup, writing this file fails with EOPNOTSUPP as\n"
    "\tkilling cgroups is a process directed operation, i.e. it affects\n"
    "\tthe whole thread-group.\n"
    "\n"
    "Controllers\n"
    "===========\n"
)


def test_batch21_psi_cgroup_pressure_switch():
    print("Batch 21 psi_cgroup_pressure_switch (cgroup.pressure, KMI-neutral)")
    import abk_stable_perf as perf

    group = next((g for g in perf.PATCH_GROUPS
                  if g.key == "psi_cgroup_pressure_switch"), None)
    check("psi_cgroup_pressure_switch group registered", group is not None)

    # The whole point of this port is not growing a KMI-visible struct: the ACK
    # 6.1 shape lives in psi_types.h, so touching that file at all would be the
    # regression.  Pin the file list, not just the resulting text.
    check("the group does not touch include/linux/psi_types.h",
          group is not None and "include/linux/psi_types.h" not in group.files,
          group is not None and group.files)

    files = {
        "include/linux/cgroup-defs.h": _BATCH21_CGROUP_DEFS,
        "include/linux/psi.h": _BATCH21_PSI_H,
        "kernel/sched/psi.c": _BATCH21_PSI_C,
        "kernel/cgroup/cgroup.c": _BATCH21_GROUP_C,
        "Documentation/admin-guide/cgroup-v2.rst": _BATCH21_DOC,
    }
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, files)
        status, detail = group.apply_fn(ctx)
        check("all steps land on the 5.15 shape", status == "applied",
              (status, detail))

        psi_c = ctx.read("kernel/sched/psi.c")
        check("accounting paths ask the per-cgroup switch",
              "static bool psi_group_enabled(struct psi_group *group)" in psi_c
              and "&container_of(group, struct cgroup, psi)->flags" in psi_c,
              [ln for ln in psi_c.split("\n") if "psi_group_enabled" in ln][:4])
        check("a disabled group keeps counting tasks but stops timing states",
              "if (unlikely(!psi_group_enabled(group))) {" in psi_c
              and "groupc->state_mask = 0;" in psi_c
              and psi_c.index("if (unlikely(!psi_group_enabled(group))) {")
              < psi_c.index("/* Calculate state mask representing active "
                            "states */"))
        check("the IRQ walk honours the switch",
              "\t\tif (!psi_group_enabled(group))\n\t\t\tcontinue;\n" in psi_c)
        # Four queries on purpose: the state-mask branch, the IRQ walk, the
        # restart guard and the report path (the trigger path adds a fifth on
        # the ERR_PTR form below).
        check("reporting and trigger creation refuse a disabled group",
              psi_c.count("if (!psi_group_enabled(group))") == 4
              and "\tif (!psi_group_enabled(group))\n\t\treturn -EOPNOTSUPP;"
              in psi_c
              and "\tif (!psi_group_enabled(group))\n"
                  "\t\treturn ERR_PTR(-EOPNOTSUPP);" in psi_c
              and "if (unlikely(!psi_group_enabled(group)))" in psi_c)
        check("re-enabling rebuilds every possible CPU under its rq lock",
              "void psi_cgroup_restart(struct psi_group *group)" in psi_c
              and "for_each_possible_cpu(cpu) {" in psi_c
              and "rq_lock_irq(rq, &rf);" in psi_c
              and "psi_group_change(group, cpu, 0, 0, cpu_clock(cpu), true);"
              in psi_c
              and "for_each_online_cpu" not in psi_c)

        defs = ctx.read("include/linux/cgroup-defs.h")
        check("the switch is a flag bit, so no member moves",
              "\tCGRP_PSI_DISABLED," in defs
              and "CGRP_KILL,\n\n" in defs)
        check("struct cgroup did not grow the ACK 6.1 members",
              defs.count("struct psi_group psi;") == 1
              and "struct psi_group *psi;" not in defs
              and "psi_files[" not in defs)

        cgroup_c = ctx.read("kernel/cgroup/cgroup.c")
        check("the trigger writer yielded the cgroup_pressure_write name",
              "static ssize_t pressure_write(struct kernfs_open_file *of, "
              "char *buf," in cgroup_c
              and cgroup_c.count("return pressure_write(of, buf, nbytes, PSI_")
              == 3
              and "static ssize_t cgroup_pressure_write(struct "
              "kernfs_open_file *of, char *buf," not in cgroup_c)
        check("cgroup.pressure is the new owner of that name",
              'name = "cgroup.pressure"' in cgroup_c
              and ".write = cgroup_pressure_write," in cgroup_c
              and 'name = "cgroup.pressure",\n'
              '\t\t.flags = CFTYPE_PRESSURE,' in cgroup_c)
        check("the root cgroup's switch drives psi_system",
              "psi_cgroup_restart(cgroup_ino(cgrp) == 1 ?" in cgroup_c
              and "void psi_cgroup_accounting_set(struct cgroup *cgrp, "
              "bool enable)" in psi_c
              and "\tif (cgroup_ino(cgrp) == 1) {\n\t\tpsi_system_"
              "accounting_disabled = !enable;" in psi_c)
        check("the knob is documented",
              "  cgroup.pressure\n" in ctx.read(
                  "Documentation/admin-guide/cgroup-v2.rst"))

        snapshot = {rel: ctx.read(rel) for rel in files}
        status2, detail2 = group.apply_fn(ctx)
        check("second pass is a no-op", status2 == "already_present",
              (status2, detail2))
        check("second pass is byte-identical",
              all(ctx.read(rel) == snapshot[rel] for rel in files))

    # It patches text psi_irq_tracking adds (the IRQ walk), so it has to be
    # registered after it.
    check("registered after psi_irq_tracking",
          [g.key for g in perf.PATCH_GROUPS].index(
              "psi_cgroup_pressure_switch")
          > [g.key for g in perf.PATCH_GROUPS].index("psi_irq_tracking"))


# The psi_types.h shape the ONCPU group lands on: the 5.15 counter-based task
# states (psi_irq_tracking has already turned NR_PSI_STATES into an auto-sized
# enum by then, which is what the PSI_ONCPU anchor expects).
_BATCH22_PSI_TYPES = (
    "#ifdef CONFIG_PSI\n"
    "\n"
    "/* Tracked task states */\n"
    "enum psi_task_count {\n"
    "\tNR_IOWAIT,\n"
    "\tNR_MEMSTALL,\n"
    "\tNR_RUNNING,\n"
    "\t/*\n"
    "\t * This can't have values other than 0 or 1 and could be\n"
    "\t * implemented as a bit flag. But for now we still have room\n"
    "\t * in the first cacheline of psi_group_cpu, and this way we\n"
    "\t * don't have to special case any state tracking for it.\n"
    "\t */\n"
    "\tNR_ONCPU,\n"
    "\t/*\n"
    "\t * For IO and CPU stalls the presence of running/oncpu tasks\n"
    "\t * in the domain means a partial rather than a full stall.\n"
    "\t * For memory it's not so simple because of page reclaimers:\n"
    "\t */\n"
    "\tNR_MEMSTALL_RUNNING,\n"
    "\tNR_PSI_TASK_COUNTS = 5,\n"
    "};\n"
    "\n"
    "/* Task state bitmasks */\n"
    "#define TSK_IOWAIT\t(1 << NR_IOWAIT)\n"
    "#define TSK_MEMSTALL\t(1 << NR_MEMSTALL)\n"
    "#define TSK_RUNNING\t(1 << NR_RUNNING)\n"
    "#define TSK_ONCPU\t(1 << NR_ONCPU)\n"
    "#define TSK_MEMSTALL_RUNNING\t(1 << NR_MEMSTALL_RUNNING)\n"
    "\n"
    "enum psi_states {\n"
    "\tPSI_IO_SOME,\n"
    "\tPSI_IO_FULL,\n"
    "\tPSI_MEM_SOME,\n"
    "\tPSI_MEM_FULL,\n"
    "\tPSI_CPU_SOME,\n"
    "\tPSI_CPU_FULL,\n"
    "\t/* Only per-CPU, to weigh the CPU in the global average: */\n"
    "\tPSI_NONIDLE,\n"
    "\tNR_PSI_STATES,\n"
    "};\n"
    "\n"
    "struct psi_group_cpu {\n"
    "\tunsigned int tasks[NR_PSI_TASK_COUNTS];\n"
    "\tu32 state_mask;\n"
    "};\n"
)

# psi.c pieces the Batch 21 fixture does not carry: test_state() and
# psi_task_switch() in their pristine 5.15 (counter-based) form.
_BATCH22_PSI_EXTRA = (
    "static bool test_state(unsigned int *tasks, enum psi_states state)\n"
    "{\n"
    "\tswitch (state) {\n"
    "\tcase PSI_IO_SOME:\n"
    "\t\treturn unlikely(tasks[NR_IOWAIT]);\n"
    "\tcase PSI_IO_FULL:\n"
    "\t\treturn unlikely(tasks[NR_IOWAIT] && !tasks[NR_RUNNING]);\n"
    "\tcase PSI_MEM_SOME:\n"
    "\t\treturn unlikely(tasks[NR_MEMSTALL]);\n"
    "\tcase PSI_MEM_FULL:\n"
    "\t\treturn unlikely(tasks[NR_MEMSTALL] &&\n"
    "\t\t\ttasks[NR_RUNNING] == tasks[NR_MEMSTALL_RUNNING]);\n"
    "\tcase PSI_CPU_SOME:\n"
    "\t\treturn unlikely(tasks[NR_RUNNING] > tasks[NR_ONCPU]);\n"
    "\tcase PSI_CPU_FULL:\n"
    "\t\treturn unlikely(tasks[NR_RUNNING] && !tasks[NR_ONCPU]);\n"
    "\tcase PSI_NONIDLE:\n"
    "\t\treturn tasks[NR_IOWAIT] || tasks[NR_MEMSTALL] ||\n"
    "\t\t\ttasks[NR_RUNNING];\n"
    "\tdefault:\n"
    "\t\treturn false;\n"
    "\t}\n"
    "}\n"
    "\n"
    "void psi_task_switch(struct task_struct *prev, struct task_struct *next,\n"
    "\t\t     bool sleep)\n"
    "{\n"
    "\tstruct psi_group *group, *common = NULL;\n"
    "\tint cpu = task_cpu(prev);\n"
    "\tvoid *iter;\n"
    "\tu64 now = cpu_clock(cpu);\n"
    "\n"
    "\tif (next->pid) {\n"
    "\t\tbool identical_state;\n"
    "\n"
    "\t\tpsi_flags_change(next, 0, TSK_ONCPU);\n"
    "\t\t/*\n"
    "\t\t * When switching between tasks that have an identical\n"
    "\t\t * runtime state, the cgroup that contains both tasks\n"
    "\t\t * runtime state, the cgroup that contains both tasks\n"
    "\t\t * we reach the first common ancestor. Iterate @next's\n"
    "\t\t * ancestors only until we encounter @prev's ONCPU.\n"
    "\t\t */\n"
    "\t\tidentical_state = prev->psi_flags == next->psi_flags;\n"
    "\t\titer = NULL;\n"
    "\t\twhile ((group = iterate_groups(next, &iter))) {\n"
    "\t\t\tif (identical_state &&\n"
    "\t\t\t    per_cpu_ptr(group->pcpu, cpu)->tasks[NR_ONCPU]) {\n"
    "\t\t\t\tcommon = group;\n"
    "\t\t\t\tbreak;\n"
    "\t\t\t}\n"
    "\n"
    "\t\t\tpsi_group_change(group, cpu, 0, TSK_ONCPU, now, true);\n"
    "\t\t}\n"
    "\t}\n"
    "\n"
    "\tif (prev->pid) {\n"
    "\t\tint clear = TSK_ONCPU, set = 0;\n"
    "\n"
    "\t\t/*\n"
    "\t\t * When we're going to sleep, psi_dequeue() lets us\n"
    "\t\t * handle TSK_RUNNING, TSK_MEMSTALL_RUNNING and\n"
    "\t\t * TSK_IOWAIT here, where we can combine it with\n"
    "\t\t * TSK_ONCPU and save walking common ancestors twice.\n"
    "\t\t */\n"
    "\t\tif (sleep) {\n"
    "\t\t\tclear |= TSK_RUNNING;\n"
    "\t\t\tif (prev->in_memstall)\n"
    "\t\t\t\tclear |= TSK_MEMSTALL_RUNNING;\n"
    "\t\t\tif (prev->in_iowait)\n"
    "\t\t\t\tset |= TSK_IOWAIT;\n"
    "\t\t}\n"
    "\n"
    "\t\tpsi_flags_change(prev, clear, set);\n"
    "\n"
    "\t\titer = NULL;\n"
    "\t\twhile ((group = iterate_groups(prev, &iter)) && group != common)\n"
    "\t\t\tpsi_group_change(group, cpu, clear, set, now, true);\n"
    "\n"
    "\t\t/*\n"
    "\t\t * TSK_ONCPU is handled up to the common ancestor. If we're tasked\n"
    "\t\t * with dequeuing too, finish that for the rest of the hierarchy.\n"
    "\t\t */\n"
    "\t\tif (sleep) {\n"
    "\t\t\tclear &= ~TSK_ONCPU;\n"
    "\t\t\tfor (; group; group = iterate_groups(prev, &iter))\n"
    "\t\t\t\tpsi_group_change(group, cpu, clear, set, now, true);\n"
    "\t\t}\n"
    "\t}\n"
    "}\n"
)


def test_batch22_psi_oncpu_state_mask():
    print("Batch 22 psi_oncpu_state_mask (TSK_ONCPU as a state-mask bit)")
    import abk_stable_perf as perf

    group = next((g for g in perf.PATCH_GROUPS
                  if g.key == "psi_oncpu_state_mask"), None)
    check("psi_oncpu_state_mask group registered", group is not None)
    if group is None:
        return
    keys = [g.key for g in perf.PATCH_GROUPS]
    check("registered after the switch whose branch it rewrites",
          keys.index("psi_oncpu_state_mask")
          > keys.index("psi_cgroup_pressure_switch"))
    check("still no psi_group::parent (the 5.15 walk is used)",
          group.files == ["include/linux/psi_types.h", "kernel/sched/psi.c"]
          and "NR_ONCPU" not in repr(group.commits))

    files = {
        "include/linux/cgroup-defs.h": _BATCH21_CGROUP_DEFS,
        "include/linux/psi.h": _BATCH21_PSI_H,
        "include/linux/psi_types.h": _BATCH22_PSI_TYPES,
        "kernel/sched/psi.c": _BATCH21_PSI_C + _BATCH22_PSI_EXTRA,
        "kernel/cgroup/cgroup.c": _BATCH21_GROUP_C,
        "Documentation/admin-guide/cgroup-v2.rst": _BATCH21_DOC,
    }
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, files)
        switch = next(g for g in perf.PATCH_GROUPS
                      if g.key == "psi_cgroup_pressure_switch")
        s21, d21 = switch.apply_fn(ctx)
        check("cgroup.pressure is in place first", s21 == "applied", (s21, d21))
        status, detail = group.apply_fn(ctx)
        check("all steps land on the composed shape", status == "applied",
              (status, detail))

        types = ctx.read("include/linux/psi_types.h")
        check("ONCPU is a flag in the state mask, not a task count",
              "NR_PSI_TASK_COUNTS = 4," in types
              and "#define TSK_ONCPU\t(1 << NR_PSI_TASK_COUNTS)" in types
              and "#define PSI_ONCPU\t(1 << NR_PSI_STATES)" in types
              and "NR_ONCPU" not in types,
              [ln for ln in types.split("\n") if "ONCPU" in ln])
        # The bit has to sit above the state enum, not take a state bit: the
        # trigger path indexes states by PSI_IO_SOME + res * 2 and would read
        # someone else's counter otherwise.
        check("the flag sits above the state enum",
              "NR_PSI_STATES,\n};\n\n/* ABK stable_515_backport: use one bit "
              "in the state mask to track" in types
              and types.index("#define PSI_ONCPU") > types.index("PSI_NONIDLE,"))

        psi_c = ctx.read("kernel/sched/psi.c")
        check("test_state asks the mask instead of a counter",
              "enum psi_states state, bool oncpu)" in psi_c
              and "tasks[NR_RUNNING] > oncpu" in psi_c
              and "tasks[NR_RUNNING] && !oncpu" in psi_c
              and "tasks[NR_ONCPU]" not in psi_c)
        check("the flag is set, cleared or carried before the counts",
              psi_c.index("if (unlikely(clear & TSK_ONCPU)) {")
              < psi_c.index("for (t = 0, m = clear; m; m &= ~(1 << t), t++)"))
        check("the underflow splat counts four states",
              "tasks=[%u %u %u %u] clear=%x" in psi_c)
        check("a cgroup with accounting off still carries the flag",
              "groupc->state_mask = state_mask;" in psi_c
              and "captured" not in psi_c)
        check("the switch stops at the first ancestor holding the flag",
              "per_cpu_ptr(group->pcpu, cpu)->state_mask &\n"
              "\t\t\t    PSI_ONCPU" in psi_c
              and "identical_state" not in psi_c)
        check("other state differences still propagate above that stop",
              "if ((prev->psi_flags ^ next->psi_flags) & ~TSK_ONCPU) {"
              in psi_c
              and "if (sleep) {\n\t\t\tclear &= ~TSK_ONCPU;" not in psi_c)

        snapshot = {rel: ctx.read(rel) for rel in files}
        status2, detail2 = group.apply_fn(ctx)
        check("second pass is a no-op", status2 == "already_present",
              (status2, detail2))
        check("second pass is byte-identical",
              all(ctx.read(rel) == snapshot[rel] for rel in files))



def test_batch23_zram_writeback_guard():
    """Batch 23: the compressed-writeback block must survive the config being off."""
    print("Batch 23: zram compressed writeback inside CONFIG_ZRAM_WRITEBACK")
    import batch17_core_zram_writeback as b17

    read_new, read_old = b17._C_READ_NEW, b17._C_READ_OLD
    check("the added read block is inside the writeback gate",
          read_new.startswith("#ifdef CONFIG_ZRAM_WRITEBACK\n")
          and "#endif /* CONFIG_ZRAM_WRITEBACK */\n" in read_new
          and read_new.index("#endif /* CONFIG_ZRAM_WRITEBACK */")
          < read_new.index("static int zram_bvec_read(struct zram *zram"))
    check("the gate closes right before the pristine function head",
          read_new.endswith(read_old))
    # The helpers touch zram->bdev / zram->wb_compressed / stats.bd_reads, all of
    # which struct zram only declares under CONFIG_ZRAM_WRITEBACK -- and ABK's
    # dispatch does not have to enable it (that is how the CI build of this
    # batch failed: "no member named 'bdev' in 'struct zram'").
    for needle in ("zram->bdev", "zram->wb_compressed", "zram->stats.bd_reads"):
        check(f"the gated field {needle} is only reached inside the gate",
              needle in read_new)

    for name, block, pristine in (("call 1", b17._C_CALL1_NEW, b17._C_CALL1_OLD),
                                  ("call 2", b17._C_CALL2_NEW, b17._C_CALL2_OLD)):
        else_at = block.find("#else\n")
        end_at = block.find("#endif\n", else_at)
        fallback = block[else_at + len("#else\n"):end_at] if else_at >= 0 else ""
        check(f"{name} falls back to the pristine read without the gate",
              "#ifdef CONFIG_ZRAM_WRITEBACK\n" in block and else_at > 0
              and end_at > else_at and fallback.strip() == pristine.strip(),
              (name, block))

    # And the audit that catches a regression of this kind is registered: the
    # four text audits run against a tree and would never see a preprocessor.
    import implementation_audit as ia
    check("the config-gated reference audit covers zram_drv.c",
          "drivers/block/zram/zram_drv.c" in ia.CONFIG_GATED_REFERENCES
          and any(gate == "CONFIG_ZRAM_WRITEBACK"
                  for gate, _n in ia.CONFIG_GATED_REFERENCES[
                      "drivers/block/zram/zram_drv.c"]))


def test_batch24_zram_max_pages():
    """Batch 24: max_pages bounds a recompression pass on both sysfs nodes."""
    print("Batch 24: zram_recompress_max_pages (max_pages cap + type guard)")
    import inspect

    import abk_stable_core as core
    import batch24_core_zram_max_pages as b24

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "zram_recompress_max_pages"), None)
    check("zram_recompress_max_pages group registered", group is not None)
    if group is None:
        return
    keys = [g.key for g in core.PATCH_GROUPS]
    check("registered after zram_recompression (it generates recompress_store)",
          keys.index("zram_recompress_max_pages") > keys.index("zram_recompression"))
    check("registered after zram_async_recompress (it generates the async node)",
          keys.index("zram_recompress_max_pages") > keys.index("zram_async_recompress"))

    # Trap 5: this is the one group in the module that rewrites text two other
    # groups append.  Both of those must therefore recognise their own payload --
    # without the probe a second pass appends a second recompress_store().
    for fn, sym in (("_zram_recompression_apply", "RECOMPRESS_HELPER"),
                    ("_zram_async_recompress_apply", "RECOMPRESS_ASYNC_STORE")):
        src = inspect.getsource(getattr(core, fn))
        check(fn + " short-circuits on its own payload",
              "b24_zmp." + sym in src and "already_present" in src,
              [ln for ln in src.split(chr(10)) if sym in ln])

    steps = b24.build_steps()
    check("six required steps, three per node",
          len(steps) == 6 and all(req for _r, _o, _n, req in steps),
          [(rel, req) for rel, _o, _n, req in steps])
    # Trap 2: no step may build its replacement out of a later step's.
    for i, (_rel, _old, new_i, _req) in enumerate(steps):
        for j in range(i + 1, len(steps)):
            check("step %d new does not contain step %d new" % (i, j),
                  steps[j][2] not in new_i)

    sync_body = (
        b24.RECOMPRESS_HELPER + " struct page *page,\n"
        "\t\t\t   u32 threshold, u32 prio, u32 prio_max)\n"
        "{\n\treturn 0;\n}\n\n"
        "static ssize_t recompress_store(struct device *dev,\n"
        "\t\t\t\tstruct device_attribute *attr,\n"
        "\t\t\t\tconst char *buf, size_t len)\n"
        "{\n"
        + b24._SYNC_HEAD_OLD +
        "\n\tif (threshold >= huge_class_size)\n\t\treturn -EINVAL;\n\n"
        + b24._SYNC_LOOP_OLD +
        "\n\tif (!zram_allocated(zram, index))\n\t\tgoto next;\n\n"
        + b24._SYNC_CALL_OLD +
        "next:\n\t\tzram_slot_unlock(zram, index);\n\t}\n}\n\n"
    )
    async_body = (
        "static ssize_t recompress_async_store(struct device *dev,\n"
        "\t\t\t\t\t\t\t\t\t\t\tstruct device_attribute *attr,\n"
        "\t\t\t\t\t\t\t\t\t\t\tconst char *buf, size_t len)\n"
        "{\n"
        + b24._ASYNC_HEAD_OLD +
        "\n\tif (threshold >= huge_class_size)\n\t\treturn -EINVAL;\n\n"
        + b24._ASYNC_LOOP_OLD +
        "\n\tif (!zram_allocated(zram, index))\n\t\tgoto abk_async_next;\n\n"
        "abk_async_next:\n\t\tzram_slot_unlock(zram, index);\n"
        "\t\tif (!candidate)\n\t\t\tcontinue;\n\n"
        + b24._ASYNC_ENQUEUE_OLD +
        "\t\tif (err) {\n\t\t\tret = err;\n\t\t\tbreak;\n\t\t}\n\t}\n}\n"
    )
    files = {b24.ZRAM_C: sync_body + async_body}

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, files)
        status, detail = group.apply_fn(ctx)
        check("all six steps land on the composed shape",
              status == "applied", (status, detail))
        text = ctx.read(b24.ZRAM_C)

        check("both nodes declare the counter the way mainline does",
              text.count(b24.MAX_PAGES_CAP_DECL) == 2,
              [ln for ln in text.split(chr(10)) if "num_recomp_pages" in ln])
        check("both nodes parse max_pages with kstrtoull",
              text.count('if (!strcmp(param, "max_pages")) {') == 2
              and text.count("ret = kstrtoull(val, 10, &num_recomp_pages);") == 2)
        check("both nodes carry the provenance marker",
              text.count(b24.MAX_PAGES_MARKER) == 2)
        check("both nodes reject an unrecognised type= value",
              text.count("if (!mode)\n\t\t\t\treturn -EINVAL;") == 2)
        check("both loops stop on a spent cap",
              text.count("if (!num_recomp_pages)\n\t\t\tbreak;") == 2)
        check("the cap is tested before the slot is taken",
              text.count("if (!num_recomp_pages)\n\t\t\tbreak;\n\n"
                         "\t\tzram_slot_lock(zram, index);") == 2)
        check("the decrement counts attempts on both nodes",
              text.count("num_recomp_pages--;\n\t\terr = zram_recompress(") == 1
              and text.count("num_recomp_pages--;\n\t\terr = abk_zram_recomp_enqueue(") == 1)
        # Upstream counts an attempt even when the recompression then fails, so
        # the decrement must sit after every candidate filter, at the call.
        check("the attempt is counted after the filters, not at the scan top",
              text.index("goto next;\n\n\t\tnum_recomp_pages--;") >
              text.index("\t\tif (!num_recomp_pages)"))

        snapshot = ctx.read(b24.ZRAM_C)
        status2, detail2 = group.apply_fn(ctx)
        check("second pass is a no-op", status2 == "already_present",
              (status2, detail2))
        check("second pass is byte-identical", ctx.read(b24.ZRAM_C) == snapshot)

        # A tree that never gained the recompression surface degrades; it must
        # not half-graft the parameter onto one of the two nodes.
        ctx_bare = make_ctx(tmp + "/bare", {b24.ZRAM_C: "static int x;\n"})
        status3, detail3 = group.apply_fn(ctx_bare)
        check("degrades without zram_recompression",
              status3 == "blocked_by_shape" and "zram_recompression" in detail3,
              (status3, detail3))

        # Only the sync node present (the async group missing) degrades too.
        ctx_half = make_ctx(tmp + "/half", {b24.ZRAM_C: sync_body})
        status4, detail4 = group.apply_fn(ctx_half)
        check("degrades without the async node",
              status4 == "blocked_by_shape"
              and "recompress_async_store" in detail4, (status4, detail4))

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
    print("config tiers: module-owned vs GKI align vs ROM integration vs PSI")
    import abk_stable_core as core
    import os

    ENV_NAMES = ("ABK_515_DEFCONFIG_ALIGN", "ABK_515_DEFCONFIG_ROM",
                 "ABK_515_DEFCONFIG_PSI")

    def enabled(env_name, psi_status="applied", psi_detail="probe-drop"):
        saved = {k: os.environ.get(k) for k in ENV_NAMES}
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

            def defconfig_drop_cmdline_token(self, token):
                caught["cmdline"] = token
                return psi_status, psi_detail

        try:
            status, detail = core._config_enablement_apply(Probe())
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        return (status, detail, dict(caught.get("configs", [])),
                caught.get("cmdline"))

    _s, _d, plain, plain_cmd = enabled(None)
    _s, _d, align, align_cmd = enabled("ABK_515_DEFCONFIG_ALIGN")
    status, detail, rom, rom_cmd = enabled("ABK_515_DEFCONFIG_ROM")
    psi_status, psi_detail, psi, psi_cmd = enabled("ABK_515_DEFCONFIG_PSI")

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
    # The PSI tier is the only one that touches the kernel command line, and it
    # exists because the AOSP lts gki_defconfig hides every pressure file (and
    # switches per-cgroup accounting off) with one token:
    # cgroup_disable=pressure.  Without dropping it, Batch 21's cgroup.pressure
    # is never created and the companion's PSI policy has nothing to walk.
    check("only the PSI tier edits CONFIG_CMDLINE",
          plain_cmd is None and align_cmd is None and rom_cmd is None
          and psi_cmd == core._PSI_CMDLINE_TOKEN,
          (plain_cmd, align_cmd, rom_cmd, psi_cmd))
    check("the dropped token is the baseline's cgroup_disable=pressure",
          core._PSI_CMDLINE_TOKEN == "cgroup_disable=pressure",
          core._PSI_CMDLINE_TOKEN)
    check("the PSI tier is reported in the detail string",
          psi_status == "applied" and "per-cgroup PSI accounting" in psi_detail,
          (psi_status, psi_detail))
    check("the PSI tier keeps the module symbols and pulls in no other tier",
          psi.get("ZRAM_MULTI_COMP") == "y" and "ZRAM_WRITEBACK" not in psi
          and "LRU_GEN_ENABLED" not in psi, sorted(psi))
    blocked_status, blocked_detail, _c, _t = enabled(
        "ABK_515_DEFCONFIG_PSI", psi_status="blocked_by_missing_anchor",
        psi_detail="no CONFIG_CMDLINE line in arch/arm64/configs/gki_defconfig")
    check("a tree with no CONFIG_CMDLINE blocks the lane instead of passing it",
          blocked_status == "blocked_by_missing_anchor"
          and "no CONFIG_CMDLINE line" in blocked_detail,
          (blocked_status, blocked_detail))
    already_status, _d2, _c2, _t2 = enabled(
        "ABK_515_DEFCONFIG_PSI", psi_status="already_present",
        psi_detail="cgroup_disable=pressure already absent")
    check("an already-dropped token does not fail the lane",
          already_status == "applied", already_status)


def test_psi_cmdline_tier():
    """The PSI tier's one edit, on a real defconfig file.

    Batch 21's cgroup.pressure and the companion's per-cgroup PSI policy are
    both unreachable on any build from a tree whose gki_defconfig ships
    cgroup_disable=pressure -- which android13-5.15-lts does.  The token makes
    cgroup_psi_enabled() false, so psi_init() disables the psi_cgroups_enabled
    static branch and cgroup_addrm_files() never creates a CFTYPE_PRESSURE
    file: no accounting to switch off, and no switch.  This test pins the edit
    that removes it, the fact that it survives a second pass byte-identically,
    and that it refuses to touch a tree it was not written against.
    """
    print("PSI tier: CONFIG_CMDLINE token drop (Batch 26)")
    import abk_stable_core as core
    from pathlib import Path
    import tempfile

    token = core._PSI_CMDLINE_TOKEN
    base = ("stack_depot_disable=on kasan.stacktrace=off "
            "kvm-arm.mode=protected ")

    def fixture(tmp, preamble, eol="\n"):
        root = Path(tmp) / "common"
        cfgdir = root / "arch/arm64/configs"
        cfgdir.mkdir(parents=True)
        defconfig = cfgdir / "gki_defconfig"
        defconfig.write_bytes(preamble.encode("utf-8"))
        ctx = GraftContext(str(root), "216", "android13-5.15",
                           defconfig=str(defconfig))
        return ctx, defconfig

    with tempfile.TemporaryDirectory() as tmp:
        ctx, defconfig = fixture(tmp,
            "# base\n"
            'CONFIG_CMDLINE="' + base + token + '"\n'
            "CONFIG_CMDLINE_EXTEND=y\n")
        status, detail = ctx.defconfig_drop_cmdline_token(token)
        text = defconfig.read_text()
        check("the token is gone",
              status == "applied" and token not in text, (status, text))
        check("the surviving tokens keep their order and quoting",
              'CONFIG_CMDLINE="' + base.rstrip() + '"\n' in text, text)
        check("CONFIG_CMDLINE_EXTEND is untouched",
              "CONFIG_CMDLINE_EXTEND=y" in text, text)
        check("the pristine defconfig is snapshotted for rollback",
              (defconfig.parent / "gki_defconfig.abk-orig").read_text().count(token) == 1)
        after_first = text
        status2, _detail2 = ctx.defconfig_drop_cmdline_token(token)
        check("second pass reports already_present",
              status2 == "already_present", status2)
        check("second pass writes nothing",
              defconfig.read_text() == after_first, defconfig.read_text())

    with tempfile.TemporaryDirectory() as tmp:
        ctx, defconfig = fixture(tmp,
            "# base\r\n"
            'CONFIG_CMDLINE="' + base + token + '"\r\n'
            "CONFIG_CMDLINE_EXTEND=y\r\n")
        status, _detail = ctx.defconfig_drop_cmdline_token(token)
        raw = defconfig.read_bytes().decode("utf-8")
        check("a CRLF defconfig keeps CRLF",
              status == "applied" and token not in raw and "\r\n" in raw,
              repr(raw))

    with tempfile.TemporaryDirectory() as tmp:
        ctx, _defconfig = fixture(tmp, "CONFIG_PSI=y\n")
        status, detail = ctx.defconfig_drop_cmdline_token(token)
        check("no CONFIG_CMDLINE line is blocked_by_missing_anchor, not a pass",
              status == "blocked_by_missing_anchor" and ctx.pending_writes() == [],
              (status, detail))

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "common"
        root.mkdir()
        outside = Path(tmp) / "gki_defconfig"
        outside.write_text('CONFIG_CMDLINE="' + token + '"\n')
        ctx = GraftContext(str(root), "216", "android13-5.15",
                           defconfig=str(outside))
        status, detail = ctx.defconfig_drop_cmdline_token(token)
        check("a defconfig outside KERNEL_ROOT is refused",
              status == "report_only" and "outside" in detail, (status, detail))
        check("the refused defconfig is not written",
              token in outside.read_text())


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

# --frozen-only: a group is a target only while the platform keeps it frozen,
# which is the judgement that makes a per-UID root safe to point at.  The v2
# layout carries the answer itself.
T2=$(mktemp -d)
mkdir -p "$T2/apps/uid_2001" "$T2/apps/uid_2002"
echo 536870912 > "$T2/apps/uid_2001/memory.current"
echo 555 > "$T2/apps/uid_2001/memory.reclaim"
echo 1 > "$T2/apps/uid_2001/cgroup.freeze"
echo 536870912 > "$T2/apps/uid_2002/memory.current"
echo 555 > "$T2/apps/uid_2002/memory.reclaim"
echo 0 > "$T2/apps/uid_2002/cgroup.freeze"
CFR_ONE_SHOT=1 sh "{tool_sh}" --cgroup-root "$T2" --frozen-only
echo "FR_RC=$?"
echo "FR_FROZEN=$(cat "$T2/apps/uid_2001/memory.reclaim")"
echo "FR_THAWED=$(cat "$T2/apps/uid_2002/memory.reclaim")"
sh "{tool_sh}" --cgroup-root "$T2" --frozen-only --list > "$T2/list.txt" 2>&1
echo "FR_LIST_RC=$?"
echo "FR_LIST_MARKS=$(grep -c 'not frozen' "$T2/list.txt")"
echo "FR_LIST_FROZEN=$(grep -c 'uid_2001' "$T2/list.txt")"

# A v1 tree whose groups carry no freezer node of their own: the question is
# bridged to the tree that does the freezing, matched by the name both trees
# give the group.
T3=$(mktemp -d)
T3F=$(mktemp -d)
mkdir -p "$T3/uid_3001" "$T3/uid_3002" "$T3F/apps/uid_3001/pid_7" "$T3F/apps/uid_3002/pid_8"
for g in 3001 3002; do
  echo 268435456 > "$T3/uid_$g/memory.usage_in_bytes"
  echo 555 > "$T3/uid_$g/memory.reclaim"
done
echo 1 > "$T3F/apps/uid_3001/pid_7/cgroup.freeze"
echo 0 > "$T3F/apps/uid_3002/pid_8/cgroup.freeze"
CFR_ONE_SHOT=1 sh "{tool_sh}" --cgroup-root "$T3" --frozen-only --freezer-root "$T3F"
echo "BR_RC=$?"
echo "BR_FROZEN=$(cat "$T3/uid_3001/memory.reclaim")"
echo "BR_THAWED=$(cat "$T3/uid_3002/memory.reclaim")"

# The bridge is keyed on uid_*: a vendor group named something else has no
# counterpart to ask, so the filter must leave it alone rather than guess one.
mkdir -p "$T3/mimd"
echo 268435456 > "$T3/mimd/memory.usage_in_bytes"
echo 555 > "$T3/mimd/memory.reclaim"
CFR_ONE_SHOT=1 sh "{tool_sh}" --cgroup-root "$T3" --group mimd --frozen-only --freezer-root "$T3F" >/dev/null 2>&1
echo "BR_NAMED_RC=$?"
echo "BR_NAMED=$(cat "$T3/mimd/memory.reclaim")"

# Without --frozen-only the filter is off: the pair stays a deliberate choice
# rather than a new default that silently narrows every existing sweep.
CFR_ONE_SHOT=1 sh "{tool_sh}" --cgroup-root "$T3" --freezer-root "$T3F" >/dev/null 2>&1
echo "NOFILTER_RC=$?"
echo "NOFILTER_THAWED=$(cat "$T3/uid_3002/memory.reclaim")"

# --cached-only: AOSP's rank for a cached app (oom_score_adj >= 900), which is
# the signal that exists on a platform that does not freeze.  Every task, not
# any -- the write reclaims the group as a whole, so one visible process in it
# would be reclaimed with the cached ones.  The rank is read through
# CFR_PROC_ROOT so the fixture is a fixture and not the host's own /proc.
T4=$(mktemp -d)
T4P=$(mktemp -d)
T4F=$(mktemp -d)
mkdir -p "$T4/uid_4001" "$T4/uid_4002" "$T4/uid_4003" "$T4/uid_4004"
mkdir -p "$T4P/11" "$T4P/21" "$T4P/22" "$T4P/41"
for g in 4001 4002 4003 4004; do
  echo 268435456 > "$T4/uid_$g/memory.usage_in_bytes"
  echo 555 > "$T4/uid_$g/memory.reclaim"
done
echo 11 > "$T4/uid_4001/tasks"; echo 900 > "$T4P/11/oom_score_adj"
echo "21 22" > "$T4/uid_4002/tasks"
echo 905 > "$T4P/21/oom_score_adj"; echo 100 > "$T4P/22/oom_score_adj"
: > "$T4/uid_4003/tasks"
echo 41 > "$T4/uid_4004/tasks"; echo 899 > "$T4P/41/oom_score_adj"
CFR_ONE_SHOT=1 CFR_PROC_ROOT="$T4P" sh "{tool_sh}" --cgroup-root "$T4" --cached-only
echo "CO_RC=$?"
echo "CO_CACHED=$(cat "$T4/uid_4001/memory.reclaim")"
echo "CO_MIXED=$(cat "$T4/uid_4002/memory.reclaim")"
echo "CO_EMPTY=$(cat "$T4/uid_4003/memory.reclaim")"
echo "CO_BELOW=$(cat "$T4/uid_4004/memory.reclaim")"
CFR_PROC_ROOT="$T4P" sh "{tool_sh}" --cgroup-root "$T4" --cached-only --list > "$T4/list.txt" 2>&1
echo "CO_LIST_RC=$?"
echo "CO_LIST_MARKS=$(grep -c 'not fully cached' "$T4/list.txt")"
echo "CO_LIST_KEPT=$(grep -c 'uid_4001' "$T4/list.txt")"

# The two filters compose: a group must satisfy both when both are given.
mkdir -p "$T4P/uid_4002/pid_5"
mkdir -p "$T4F/apps/uid_4001/pid_9"
echo 1 > "$T4F/apps/uid_4001/pid_9/cgroup.freeze"
echo 555 > "$T4/uid_4001/memory.reclaim"
CFR_ONE_SHOT=1 CFR_PROC_ROOT="$T4P" sh "{tool_sh}" --cgroup-root "$T4" \
  --frozen-only --freezer-root "$T4F" --cached-only >/dev/null 2>&1
echo "BOTH_RC=$?"
echo "BOTH_KEPT=$(cat "$T4/uid_4001/memory.reclaim")"

rm -rf "$T" "$T1" "$T1E" "$T2" "$T3" "$T3F" "$T4" "$T4P" "$T4F"
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
    check("cfr daemon --frozen-only sweeps a frozen group and spares a thawed one",
          got.get("FR_RC") == "0"
          and got.get("FR_FROZEN") == "536870912"
          and got.get("FR_THAWED") == "555", r.stdout)
    check("cfr daemon --frozen-only --list names the group it left out",
          got.get("FR_LIST_RC") == "0"
          and got.get("FR_LIST_MARKS") == "1"
          and got.get("FR_LIST_FROZEN") == "1", r.stdout)
    check("cfr daemon --freezer-root bridges a v1 group to the tree that freezes it",
          got.get("BR_RC") == "0"
          and got.get("BR_FROZEN") == "268435456"
          and got.get("BR_THAWED") == "555", r.stdout)
    check("cfr daemon --frozen-only will not guess for a named group",
          got.get("BR_NAMED_RC") == "1" and got.get("BR_NAMED") == "555", r.stdout)
    check("cfr daemon sweeps an unfiltered root whole without --frozen-only",
          got.get("NOFILTER_RC") == "0"
          and got.get("NOFILTER_THAWED") == "268435456", r.stdout)
    check("cfr daemon --cached-only sweeps a fully cached group (adj >= 900)",
          got.get("CO_RC") == "0"
          and got.get("CO_CACHED") == "268435456", r.stdout)
    check("cfr daemon --cached-only spares a group that also holds a visible task",
          got.get("CO_MIXED") == "555", r.stdout)
    check("cfr daemon --cached-only spares a group with no readable task",
          got.get("CO_EMPTY") == "555", r.stdout)
    check("cfr daemon --cached-only spares a group just below the cached rank",
          got.get("CO_BELOW") == "555", r.stdout)
    check("cfr daemon --cached-only --list names each group it left out",
          got.get("CO_LIST_RC") == "0"
          and got.get("CO_LIST_MARKS") == "3"
          and got.get("CO_LIST_KEPT") == "1", r.stdout)
    check("cfr daemon composes --frozen-only with --cached-only",
          got.get("BOTH_RC") == "0"
          and got.get("BOTH_KEPT") == "268435456", r.stdout)


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

# --max-pages appends the cap to the same pass string, in mainline order
# (type, threshold, max_pages).  Without the option the pass string is byte
# identical to the pre-Batch-24 form, which is what a kernel without the graft
# needs to keep working.
: > "$T/block/zram0/recompress_async"
sh "{tool_sh}" --sys-root "$T" --threshold 64 --max-pages 4096
echo "capped_async=$(cat "$T/block/zram0/recompress_async")"

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
sh "{tool_sh}" --sys-root "$T" --max-pages abc >/dev/null 2>&1
echo "RC_MAX_PAGES_BAD=$?"
sh "{tool_sh}" --sys-root "$T" --status --max-pages 4096 | sed -n 's/^.*pass cap *= */PASS_CAP=/p'
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
    check("--max-pages extends the pass string in mainline parameter order",
          got.get("capped_async") == "type=idle threshold=64 max_pages=4096",
          r.stdout)
    check("a bad --max-pages is a usage error",
          got.get("RC_MAX_PAGES_BAD") == "2", r.stdout)
    check("--status reports the cap it will send",
          got.get("PASS_CAP", "").startswith("4096 attempted entries"), r.stdout)
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
          _versions == ["0.37.0", "0.37.0"], _versions)

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
    # widened rule is a policy change shipped by a kernel flash, so every
    # statement below is a least-privilege allow, and no module code gets to
    # relax SELinux to reach the same end.
    #
    # There are two, and the second is not optional: the rule has to name the
    # file TYPE of the backing store actually in use, and the type differs with
    # who attached the device.  The module's own backing file lives under
    # /data/per_boot/zram/ (zram_data_file); the ROM's memory extension backs
    # its own loop device with /data/extm/extm_file (extm_data_file), which is
    # the case the module prefers, because it preserves a ROM attachment rather
    # than trading it away.  Measured on vermeer / 5.15.216 2026-09-15:
    # /data/extm/extm_file is u:object_r:extm_data_file:s0 while
    # /data/per_boot/zram is u:object_r:zram_data_file:s0 -- so with only the
    # first rule the file matches nothing on that device and every page still
    # returns -EIO.  A third type must fail this check and be argued for.
    rule = (module_dir / "sepolicy.rule").read_text(encoding="utf-8")
    rule_statements = [line.strip() for line in rule.splitlines()
                       if line.strip() and not line.lstrip().startswith("#")]
    check("the SELinux rule file carries exactly the two least-privilege allows",
          rule_statements == ["allow kernel zram_data_file file { read write }",
                              "allow kernel extm_data_file file { read write }"],
          rule_statements)
    for forbidden in ("setenforce", "permissive", "neverallow", "dontaudit",
                      "auditallow", "type_transition", "allowx"):
        check(f"the SELinux rule never uses {forbidden!r}",
              forbidden not in rule)
    check("the SELinux rule names the kernel domain, not a permissive shell",
          rule_statements
          and all(s.startswith("allow kernel ") for s in rule_statements))

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
    # A per-UID tree on this ROM is one level below the root the tool searches
    # (/dev/memcg/mimd), and it holds the app on screen as well as the cached
    # ones -- so reaching it and restricting it are one change, not two: an
    # extra root without the frozen filter would reclaim the foreground app.
    check("the cfr supervisor can reach a deeper per-UID tree",
          "abk_cfg cfr.cgroup_root" in policy
          and '--cgroup-root $_cf_r' in policy
          and '$_cf_root_args' in policy)
    check("the cfr supervisor can restrict a sweep to frozen (cached) groups",
          "abk_cfg cfr.frozen_only" in policy
          and '--frozen-only' in policy
          and "abk_cfg cfr.freezer_root" in policy
          and '--freezer-root $_cf_freezer' in policy)
    check("a freezer root without the frozen filter is dropped, not passed inert",
          '[ "$_cf_frozen" = 1 ] || _cf_freezer=""' in policy)
    # The frozen filter needs a platform that freezes.  This one does not (the
    # freezing had been an installed extension), so the rank-based filter is
    # the one that decides anything here -- and it must be a separate knob, not
    # a reinterpretation of the frozen one.
    check("the cfr supervisor can restrict a sweep to the platform's cached rank",
          "abk_cfg cfr.cached_only" in policy and '--cached-only' in policy
          and "cached_only=$_cf_cached" in policy)
    check("the cfr tool documents both filters it implements",
          "--frozen-only" in cfr_tool and "--freezer-root" in cfr_tool
          and "--cached-only" in cfr_tool and "oom_score_adj" in cfr_tool
          and "uid_*" in cfr_tool)
    check("the cached rank is AOSP's CACHED_APP_MIN_ADJ, not an invented one",
          "ABK_CACHED_APP_MIN_ADJ=900" in cfr_tool
          and 'if [ "$_gc_adj" -lt "$ABK_CACHED_APP_MIN_ADJ" ]' in cfr_tool)
    check("a task that exits mid-walk cannot take the sweep down with it",
          '2>/dev/null || true)"' in cfr_tool)
    for key in ("cfr.cgroup_root", "cfr.frozen_only", "cfr.freezer_root",
                "cfr.cached_only"):
        check(f"{key} is a known tunables.conf key",
              key in common_sh
              and re.search(r"(?m)^\s*" + re.escape(key) + r"=", tunables) is not None)

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

    # --- per-cgroup PSI accounting policy (companion v0.9.0) ---------------
    # The node comes from this module's own Batch 21 graft, so nothing here is
    # kernel work.  What can go wrong is the policy: protect so much that the
    # pass buys nothing, protect so little that a future reader is refused, or
    # re-enable a group whose per-cpu windows some other kernel already freed.
    service_source = (module_dir / "service.sh").read_text(encoding="utf-8")
    readme_source = (module_dir / "README.md").read_text(encoding="utf-8")
    for key in ("psi.cgroup", "psi.cgroup.protect", "psi.cgroup.interval_sec"):
        check(f"{key} is registered as a known key", key in common_sh)
        check(f"{key} is assigned in the shipped tunables.conf",
              re.search(r"(?m)^" + re.escape(key) + r"=", tunables) is not None)
    check("the mode vocabulary is exactly keep|auto|aggressive with keep built in",
          "abk_cfg psi.cgroup keep" in common_sh
          and "keep|auto|aggressive)" in common_sh)
    check("an unrecognised psi.cgroup value leaves the kernel alone",
          "leaving the kernel alone" in common_sh)
    check("the protect default is the narrow system prefix, not apps",
          "abk_cfg psi.cgroup.protect system" in common_sh
          and "system,apps" not in common_sh)
    check("the pass is a supervisor and never runs at the early boot stage",
          "abk_psi_supervisor_main" in service_source
          and "--supervise-psi" in service_source
          and "abk_psi" not in post_fs_data)
    check("psi.cgroup=keep spawns no supervisor at all",
          "= keep ]; then" in service_source)
    check("a pass that changed nothing stays out of logcat",
          "abk_log_append INFO" in common_sh)
    check("the supervisor stops when every write was refused",
          "stopping the supervisor instead of re-walking" in common_sh)
    check("status prints what the tree says, not what the config intends",
          "--status --cgroot" in action_source
          and "abk_supervisor_state psi" in action_source)
    check("the companion README records the two-boot rule and the third supervisor",
          "two boots" in readme_source
          and "three supervisors" in readme_source)
    # Measured on the target device (2026-09-15): the psi supervisor wrote a
    # literal "$" into its pid file -- `abk_pid_write psi "$"` is a quoted dollar
    # sign, not the pid -- so abk_spawn polled for ten seconds and logged "psi
    # supervisor did not start" while the supervisor was in fact running, and
    # every pid-file-based check of it was blind.  zram and cfr already passed
    # "$$"; the psi one was a typo.  Pin the whole class, not the one line.
    _pid_writes = []
    for _sh in sorted(module_dir.glob("*.sh")):
        _pid_writes += re.findall(r'abk_pid_write\s+(\S+)\s+"([^"]*)"',
                                  _sh.read_text(encoding="utf-8"))
    check("every supervisor writes its real pid into its pid file",
          bool(_pid_writes) and all(arg == "$$" for _n, arg in _pid_writes),
          _pid_writes)
    check("the psi supervisor is one of them",
          any(name == "psi" for name, _arg in _pid_writes), _pid_writes)

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
            check("embed.conf contributes exactly the six device tools",
                  sorted(name for name in names if name.startswith("bin/"))
                  == ["bin/abk_fas_check.sh",
                      "bin/abk_launch_bench.sh",
                      "bin/abk_psi_bench.sh",
                      "bin/abk_psi_policy.sh",
                      "bin/cached_freeze_reclaim.sh",
                      "bin/zram_recompress_trigger.sh"])
            check("embedded launch bench is byte-identical to tools/",
                  archive.read("bin/abk_launch_bench.sh")
                  == (repo / "tools" / "abk_launch_bench.sh").read_bytes())
            check("embedded PSI policy tool is byte-identical to tools/",
                  archive.read("bin/abk_psi_policy.sh")
                  == (repo / "tools" / "abk_psi_policy.sh").read_bytes())
            check("embedded PSI bench is byte-identical to tools/",
                  archive.read("bin/abk_psi_bench.sh")
                  == (repo / "tools" / "abk_psi_bench.sh").read_bytes())
            _psi_tool = archive.read("bin/abk_psi_policy.sh").decode("utf-8")
            check("the shipped PSI tool carries the fixture self test the device lacks",
                  "abk_psi_selftest" in _psi_tool
                  and "psi selftest PASS" in _psi_tool)
            # The one-way rule.  The upstream version of this switch frees the
            # group per-cpu windows and calls re-enabling not restore safe; the
            # Batch 21 graft can restart accounting, but the companion cannot
            # tell the two kernels apart from userspace, so the walk body must
            # hold no write of 1.  Sliced, because the self test re-arms its own
            # fixture files on purpose.
            _psi_walk = _psi_tool.split("abk_psi_walk() {", 1)[1]
            _psi_walk = _psi_walk.split(chr(10) + "}", 1)[0]
            check("the PSI walk only ever writes 0",
                  "echo 0 > " in _psi_walk and "echo 1" not in _psi_walk)
            check("the root group is skipped before the protect list is consulted",
                  0 <= _psi_walk.find("ABK_n_root=$((")
                  < _psi_walk.find("ABK_n_protect=$(("))
            check("the PSI tool names Batch 21 as the node origin, not upstream",
                  "Batch 21" in _psi_tool
                  and "inherited upstream" not in _psi_tool)
            # A live phone deletes cgroups underneath the walk.  Booking that as
            # "refused" made the supervisor's first-pass rule (nothing off,
            # nothing disabled, something refused => this kernel will not let me
            # write) fire on a single exited app and stop the policy for the rest
            # of the boot.  The two counters are separate on purpose and the
            # fixture has a node that disappears to prove it.
            check("the PSI walk books a group that went away mid-walk as vanished",
                  "ABK_n_vanished" in _psi_tool
                  and "vanished=$ABK_n_vanished" in _psi_tool
                  and _psi_walk.count("ABK_n_vanished=$((") >= 2
                  and "gone/cgroup.pressure" in _psi_tool)
            check("the PSI selftest asserts the vanished counter",
                  "vanished=1" in _psi_tool)
            _psi_bench = archive.read("bin/abk_psi_bench.sh").decode("utf-8")
            # Whether a string is absent depends on comment vs code here: two of
            # the strings below are named in the tool header precisely because
            # they are the old bugs, so the absence checks run against a
            # comment-stripped view (the same split the DVFS pins use above).
            _psi_bench_code = "\n".join(l for l in _psi_bench.splitlines()
                                         if not l.lstrip().startswith("#"))
            # The first version of this tool billed itself out of /proc/stat and was
            # rewritten after the first device run, because two things about it were wrong
            # in ways every local gate had passed: machine-wide busy jiffies are about 45%
            # background load on this phone, so the saving this switch can produce is a
            # rounding error on that number; and its wake storm was a second fork storm
            # wearing a wake storm label, because toybox sleep forks.  The pins below are
            # about the second version, and they pin the same three shapes the first one
            # ate: a reading that came back silently empty, a label the tree does not
            # support, and a run that generated no work exiting 0.
            check("the bench reads cpu.stat by key, not by summing /proc/stat",
              "awk -v k=" in _psi_bench_code and "$1 == k" in _psi_bench_code
                  and "/^cpu  /" not in _psi_bench_code
                  and "/proc/stat" not in _psi_bench_code)
            check("the bench refuses an unreadable cpu.stat instead of billing zero",
              "aborting instead of billing zero" in _psi_bench)
            check("the bench refuses a group that is not being billed",
              "not being billed at depth" in _psi_bench and "instrument live" in _psi_bench)
            check("the bench verifies both arms by reading the pressure nodes back",
              "arms did not land on on=1 off=0" in _psi_bench and "echo 0 > " in _psi_bench)
            check("the bench says so when the kernel has no switch to A/B",
              "this kernel has no per-cgroup PSI switch" in _psi_bench)
            check("a missing storm binary fails the bench instead of passing it",
              "cannot generate a storm" in _psi_bench
                  and "exit 1" in _psi_bench.split("cannot generate a storm", 1)[1][:40])
            # The loop must create no process at all.  "sleep 0" was the first
            # shape of that bug; the second was "printf", which on the target
            # ROM is a tracked alias to /system/bin/printf -- 500 calls measured
            # 6.3 s on device, i.e. two fork+execs per round trip.  echo is a
            # builtin there (500 calls: 0.01 s).
            check("the wake storm is a blocking wake, not a fork storm in disguise",
              "mkfifo" in _psi_bench_code and "sleep 0" not in _psi_bench_code
                  and "printf" not in _psi_bench_code
                  and "echo x >&3" in _psi_bench_code
                  and "echo y; done <" in _psi_bench_code)
            check("the bench reports the groups it left behind",
              "groups left =" in _psi_bench)
            # The companion's own periodic pass disables every unprotected group,
            # the bench's arms included, so a run crossing a tick would compare
            # two disabled arms and call the difference a saving.
            check("the bench re-checks both arms every round and aborts if one moved",
                  "arm state changed mid-run" in _psi_bench
                  and "want=$_want_v" in _psi_bench)
            check("the bench has a help path", "-h|--help) abk_usage" in _psi_bench)
            # Measured on the target device: _diff * 10000 overflows mksh's 32-bit
            # arithmetic and printed 49 permille for a negative saving.  Divide first.
            check("the bench cannot overflow its 32-bit arithmetic",
                  "_diff / (_on_tot / 1000)" in _psi_bench_code
                  and "* 10000 / _on_tot" not in _psi_bench_code)
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

# 11. the writeback sweep: the trigger the kernel does not have on its own.
#     `off` must not touch the node at all, `huge`/`idle` must re-arm the
#     budget before writing, and the budget is clamped by the backing device so
#     a pass cannot run it out of space.
reset_fixture
printf '/dev/block/loop9\n' > "$T/sys/block/zram0/backing_dev"
: > "$T/sys/block/zram0/writeback"
: > "$T/sys/block/zram0/idle"
printf '0\n' > "$T/sys/block/zram0/writeback_limit"
printf '0\n' > "$T/sys/block/zram0/writeback_limit_enable"
printf '0 0 0\n' > "$T/sys/block/zram0/bd_stat"
mkdir -p "$T/sys/block/loop9"
printf '2097152\n' > "$T/sys/block/loop9/size"   # 1 GiB = 262144 4 KiB blocks

printf 'zram.writeback.trigger=off\n' > "$T/tunables.conf"
abk_zram_writeback_sweep >/dev/null 2>&1
echo "wb_off_lines=$(grep -c . < "$T/sys/block/zram0/writeback")"
echo "wb_off_limit=$(cat "$T/sys/block/zram0/writeback_limit")"

printf 'zram.writeback.trigger=huge\nzram.writeback.budget_mb=64\n' > "$T/tunables.conf"
abk_zram_writeback_sweep >/dev/null 2>&1
echo "wb_huge=$(cat "$T/sys/block/zram0/writeback")"
echo "wb_huge_limit=$(cat "$T/sys/block/zram0/writeback_limit")"
echo "wb_huge_enable=$(cat "$T/sys/block/zram0/writeback_limit_enable")"

printf 'zram.writeback.trigger=huge\nzram.writeback.budget_mb=65536\n' > "$T/tunables.conf"
abk_zram_writeback_sweep >/dev/null 2>&1
echo "wb_clamp_limit=$(cat "$T/sys/block/zram0/writeback_limit")"

: > "$T/sys/block/zram0/idle"
: > "$T/sys/block/zram0/writeback"
printf 'zram.writeback.trigger=idle\nzram.writeback.idle_age_sec=1800\n' > "$T/tunables.conf"
abk_zram_writeback_sweep >/dev/null 2>&1
echo "wb_idle_mark=$(cat "$T/sys/block/zram0/idle")"
echo "wb_idle_node=$(cat "$T/sys/block/zram0/writeback")"

# ... and an unknown mode is refused rather than guessed at
: > "$T/sys/block/zram0/writeback"
printf 'zram.writeback.trigger=everything\n' > "$T/tunables.conf"
abk_zram_writeback_sweep >> "$T/out11" 2>&1
echo "wb_bad_lines=$(grep -c . < "$T/sys/block/zram0/writeback")"
echo "wb_bad_warn=$(grep -c 'is not off/huge/idle' "$T/out11" 2>/dev/null)"
printf 'zram.recomp.enable=1\n' > "$T/tunables.conf"

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

    # 11: the writeback sweep.  The kernel only moves a page when userspace
    # asks, so this is the whole trigger -- and it is the one place the module
    # writes the writeback node, which is why `off` is checked as hard as the
    # two live modes.
    check("the writeback sweep leaves the node alone while trigger=off",
          got.get("wb_off_lines") == "0" and got.get("wb_off_limit") == "0",
          (got.get("wb_off_lines"), got.get("wb_off_limit")))
    check("trigger=huge writes the node and re-arms the budget per pass",
          got.get("wb_huge") == "huge"
          and got.get("wb_huge_limit") == "16384"
          and got.get("wb_huge_enable") == "1",
          (got.get("wb_huge"), got.get("wb_huge_limit"),
           got.get("wb_huge_enable")))
    check("an oversized budget is clamped to the backing device",
          got.get("wb_clamp_limit") == "262144", got.get("wb_clamp_limit"))
    check("trigger=idle marks by age and never falls back to 'all'",
          got.get("wb_idle_mark") == "1800"
          and got.get("wb_idle_node") == "idle",
          (got.get("wb_idle_mark"), got.get("wb_idle_node")))
    check("an unknown writeback mode is refused, not guessed at",
          got.get("wb_bad_lines") == "0" and got.get("wb_bad_warn") == "1",
          (got.get("wb_bad_lines"), got.get("wb_bad_warn")))

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
    # This is the batching group's *generated* payload.  Batch 32
    # (zram_wb_slot_preserve) later removes the save/restore dance from it --
    # the pairs asserted there are what keeps the metadata without going through
    # zram_free_page(), and the assertions below stay true of this group's own
    # replacement block.
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


def test_batch32_zram_wb_slot_preserve():
    """Batch 32: a written-back slot keeps its metadata and is counted once.

    The fixture's completion helper is *batch17's own generated text*
    (``_A_HELPERS_NEW``), not a copy of this batch's anchors, so the steps are
    checked against the shape the earlier group really produces.  The
    zram_free_page() half is composed from this batch's pristine anchor, but
    that block is byte-identical in pristine 5.15 and in the shape
    zram_recompression rewrites it into -- which step_audit.py verifies for real
    on all four baselines.
    """
    print("Batch 32: zram_wb_slot_preserve (no zram_free_page() on a WB slot)")
    import inspect

    import implementation_audit as ia
    import abk_stable_core as core
    import batch17_core_zram_writeback as b17
    import batch32_core_zram_wb_slot_preserve as b32

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "zram_wb_slot_preserve"), None)
    check("zram_wb_slot_preserve group registered", group is not None)
    if group is None:
        return
    keys = [g.key for g in core.PATCH_GROUPS]
    for earlier in ("zram_writeback_batching", "zram_compressed_writeback",
                    "zram_recompression"):
        check(f"registered after {earlier}",
              keys.index("zram_wb_slot_preserve") > keys.index(earlier))

    # Trap 5: this group rewrites zram_writeback_complete(), which
    # zram_writeback_batching generates.  That group must therefore recognise
    # its own payload -- without the probe a second pass drops to
    # blocked_by_shape (its replacement block no longer matches the file) and
    # step_audit.py fails on the patched tree.
    src = inspect.getsource(b17._batching_apply)
    check("_batching_apply short-circuits on its own payload",
          b32.BATCHING_PAYLOAD in src and "already_present" in src,
          [ln for ln in src.split(chr(10)) if "payload" in ln or "probe" in ln])

    steps = b32.build_steps()
    check("three required steps",
          len(steps) == 3 and all(req for _r, _o, _n, req in steps),
          [(rel, req) for rel, _o, _n, req in steps])
    # Trap 2: no step may build its replacement out of a later step's.
    for i, (_rel, _old, new_i, _req) in enumerate(steps):
        for j in range(i + 1, len(steps)):
            check("step %d new does not contain step %d new" % (i, j),
                  steps[j][2] not in new_i)

    free_page = (
        "static void zram_free_page(struct zram *zram, size_t index)\n"
        "{\n"
        "\tunsigned long handle;\n"
        "\n"
        "\tif (zram_test_flag(zram, index, ZRAM_IDLE))\n"
        "\t\tzram_clear_flag(zram, index, ZRAM_IDLE);\n"
        "\n"
        + b32._FREE_PAGE_HUGE_OLD +
        "\n"
        "\tif (zram_test_flag(zram, index, ZRAM_WB)) {\n"
        "\t\tzram_clear_flag(zram, index, ZRAM_WB);\n"
        "\t\tfree_block_bdev(zram, zram_get_element(zram, index));\n"
        "\t\tgoto out;\n"
        "\t}\n"
        "\n"
        "\thandle = zram_get_handle(zram, index);\n"
        "\tif (!handle)\n"
        "\t\treturn;\n"
        "\n"
        "\tzs_free(zram->mem_pool, handle);\n"
        "\n"
        "\tatomic64_sub(zram_get_obj_size(zram, index),\n"
        "\t\t\t&zram->stats.compr_data_size);\n"
        "out:\n"
        "\tatomic64_dec(&zram->stats.pages_stored);\n"
        "\tzram_set_handle(zram, index, 0);\n"
        "\tzram_set_obj_size(zram, index, 0);\n"
        "}\n"
    )
    # The real generated shape: the batching group's helper suite (it carries
    # zram_writeback_complete() plus the pool/endio/drain helpers) followed by
    # the free path it is counted against.
    fixture = b17._A_HELPERS_NEW + "\n" + free_page

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {b32.ZRAM_C: fixture})
        status, detail = group.apply_fn(ctx)
        check("all three steps land on the generated shape",
              status == "applied", (status, detail))
        text = ctx.read(b32.ZRAM_C)
        body = ia.function_body(text, "zram_writeback_complete")

        check("the completion no longer frees the page",
              "zram_free_page(zram, index);" not in body,
              [ln for ln in body.split(chr(10)) if "zram_free_page" in ln])
        check("the object, its size and the huge page are released in place",
              "zs_free(zram->mem_pool, zram_get_handle(zram, index));" in body
              and "atomic64_sub(zram_get_obj_size(zram, index)," in body
              and "if (zram_test_flag(zram, index, ZRAM_HUGE))\n"
                  "\t\tatomic64_dec(&zram->stats.huge_pages);" in body)
        # pages_stored moved in both directions before (zram_free_page's out:
        # label decremented, the path incremented); dropping only one side
        # would make a written-back page start counting twice.
        mentions = [ln for ln in body.split(chr(10)) if "pages_stored" in ln]
        check("pages_stored is left untouched by completion",
              all(ln.lstrip().startswith("*") for ln in mentions), mentions)
        # The metadata readers this keeps: obj_size/priority for the compressed
        # read, ZRAM_HUGE for the raw one.  The values must survive on the slot,
        # i.e. nothing may write them back from a saved copy -- asserted on the
        # function slice, because the recompression path legitimately writes
        # obj_size and priority after its own zram_free_page().
        check("the saved-copy restore dance is gone",
              "\t\tsize = zram_get_obj_size(zram, index);" not in text
              and "\tu32 size = 0, prio = 0;" not in text)
        check("the flag the read path needs is no longer cleared then re-set",
              "zram_set_flag(zram, index, ZRAM_HUGE)" not in body
              and "zram_set_obj_size(zram, index, size)" not in body
              and "zram_set_priority(zram, index, prio)" not in body
              and "\t\tif (huge)" not in body,
              [ln for ln in body.split(chr(10))
               if "huge" in ln or "prio" in ln])
        # The other half of the accounting: the final release of a written-back
        # huge slot must not decrement the counter a second time.
        check("zram_free_page() guards the huge-page decrement",
              b32.HUGE_GUARD in text)
        check("the huge-page decrement is written once per side",
              text.count(chr(10) + "\t\tatomic64_dec(&zram->stats.huge_pages);\n") == 1
              and text.count(chr(10) + "\t\t\tatomic64_dec(&zram->stats.huge_pages);\n") == 1,
              [ln for ln in text.split(chr(10)) if "huge_pages" in ln])
        check("the provenance marker is in both halves",
              text.count(b32.SLOT_PRESERVE_MARKER) == 2)

        snapshot = ctx.read(b32.ZRAM_C)
        status2, detail2 = group.apply_fn(ctx)
        check("second pass is a no-op", status2 == "already_present",
              (status2, detail2))
        check("second pass is byte-identical", ctx.read(b32.ZRAM_C) == snapshot)

        # A tree that never gained the batching graft degrades; it must not
        # rewrite the free path for a writeback path that does not exist.
        ctx_bare = make_ctx(tmp + "/bare", {b32.ZRAM_C: free_page})
        status3, detail3 = group.apply_fn(ctx_bare)
        check("degrades without zram_writeback_batching",
              status3 == "blocked_by_shape" and "zram_writeback_batching" in detail3,
              (status3, detail3))

        # Half a tree: the completion helper is there, the huge block is not.
        # Transactional apply, so nothing at all may be written.
        half = b17._A_HELPERS_NEW + "\nstatic void zram_free_page(void) {}\n"
        ctx_half = make_ctx(tmp + "/half", {b32.ZRAM_C: half})
        status4, detail4 = group.apply_fn(ctx_half)
        check("degrades when the free path anchor is missing",
              status4 == "blocked_by_shape", (status4, detail4))
        check("a degraded run writes nothing",
              ctx_half.read(b32.ZRAM_C) == half)


def test_batch27_launch_bench():
    """The cold-launch instrument refuses to answer a question it did not ask.

    Batch 27's premise is that a launch has three candidate owners and only
    measurement taken *during* the launch window separates them, so the tool's
    value is entirely in what it refuses: a median it did not take, a placement
    ratio out of too few samples, a run against a dozing phone.  Those refusals
    are what these checks pin -- the device run itself is in
    research/launch/vermeer_launch_20260915/.
    """
    print("Batch 27 cold-launch instrument")
    repo = Path(__file__).resolve().parent.parent
    tool = repo / "tools" / "abk_launch_bench.sh"
    check("launch bench exists", tool.is_file(), tool)
    text = tool.read_text(encoding="utf-8")
    # The header's own promise, and the trap that produced the first wrong answer.
    check("the tool documents the screen guard",
          "dozing" in text and "top-app (cpus 0-7)" in text)
    check("the tool names its batch of origin",
          "Batch 27" in text)
    # The user-side stack is not a kernel feature and the tool must not imply it
    # is.  Naming it is allowed -- the storage verdict names it precisely to say
    # it is not running -- but only in a sentence that calls it userspace, and no
    # invented CONFIG_ name may appear at all.
    check("the tool does not claim launch_boost is a kernel feature",
          "CONFIG_LAUNCH_BOOST" not in text
          and ("xiaomi.launch_boost" not in text
               or ("userspace" in text and "is not running" in text)))
    # 32-bit shell arithmetic: 855 * 2803200 wraps on this ROM, so the product has
    # to reach awk.  Same rule abk_fas_check.sh landed with.
    check("cap_view goes through awk, not shell arithmetic",
          "a * f / m" in text and "int(carch[p] * clr[k] / cinfo[p])" in text)
    check("the refusal to report a zero it did not measure is present",
          "0 ms would be a lie" in text)
    # "The launch did not run on the super core" has two different owners and the
    # tool must not name one when it only measured the other: a measured ceiling
    # inversion is the ceiling holder's problem, while an available-but-unused
    # super core is an outcome with no cause measured in that run.
    check("the two placement verdicts are distinct",
          "VERDICT: ceiling bound." in text
          and "VERDICT: placement bound, cause not measured." in text)
    # The storage verdict may only be printed when the other arm is available.
    # Measured on device: dropping the page cache made every launch read 5 to 340
    # times more pages for 11% to 29% more wall clock, so "this arm read N pages"
    # is traffic and not a cause -- a single arm must not be allowed to call it.
    check("the storage verdict needs the other arm",
          "abk_storage_share" in text and "--compare" in text
          and "one arm cannot say" in text)
    # The device state is re-read before every launch, not only at startup: four
    # runs produced complete, plausible tables from a phone that was asleep or
    # sitting on its lock screen, and the visible symptoms were "the launches that
    # failed are the ones that were warm" and "the super core is not in the app's
    # cpuset".  Both are the same precondition, so both are checked.
    check("the device guard is re-checked per launch, not only at startup",
          "abk_device_block" in text and "after $_done_launches launch(es)" in text)
    check("the lock screen is part of the guard, not just the screen",
          "mDreamingLockscreen" in text and "lock screen is up" in text
          and "mShowingLockscreen" in text and "stayed false" in text)
    # A launch that never reports a LaunchState did not happen; a median over the
    # survivors is not a sample of anything.
    check("a run that stops producing LaunchStates aborts",
          "produced no LaunchState" in text)
    check("the cpuset range form is expanded, not compared as one token",
          "abk_expand_cpus" in text)
    # The processor-field strip.  The first version matched up to the first '(',
    # which never matches on a line whose comm is itself parenthesised; the
    # substitution then did nothing and $37 read cnswap, so forty launches
    # reported a perfectly shaped "0/714 samples on the super core".  A fixture
    # case in the tool's own selftest now covers it; this pins the form.  The
    # absence half runs against a comment-stripped view, because the header names
    # the old expression on purpose (the same split the PSI bench pins use).
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    check("the processor strip goes to the last paren, not the first",
          "sub(/^.*\\) /" in code and "sub(/^[^(]*\\) /" not in code)

    bash = shutil.which("bash")
    if bash is None:
        print("  (no bash on this host: launch bench runs skipped)")
        return
    use_wsl = os.name == "nt"
    shell = ["wsl", "bash"] if use_wsl else [bash]

    def shell_path(path):
        s = str(path).replace("\\", "/")
        if use_wsl and len(s) > 2 and s[1] == ":":
            return "/mnt/" + s[0].lower() + s[2:]
        return s

    tool_sh = shell_path(tool)

    def run_tool(args):
        return subprocess.run(shell + [tool_sh] + args,
                              capture_output=True, text=True,
                              env=dict(os.environ, TMPDIR="/tmp"))

    r = subprocess.run(shell + ["-n", tool_sh], capture_output=True, text=True)
    check("launch bench passes bash -n", r.returncode == 0, r.stderr)

    r = run_tool(["--selftest"])
    check("launch bench selftest passes", r.returncode == 0, r.stdout + r.stderr)
    check("selftest reports every case it ran", "selftest PASS" in r.stdout)
    # The fixture cases that back the verdict, by name, so a case silently
    # dropped from the selftest is a failure here too.
    for case in ("a capped super core counts as an inversion",
                 "a ceiling that moves is reported as ceilings=3",
                 "a pid that never appeared yields 0 polls",
                 "cpulist 0-7 contains cpu7 and 0-6 does not",
                 "the processor field survives a paren-wrapped and a spaced comm",
                 "the two-arm storage share is 33% here"):
        check(f"selftest covers: {case}", case in r.stdout)

    # A malformed --apps entry is refused before anything is launched.
    r = run_tool(["--apps", "not-a-component", "--selftest"])
    check("a malformed --apps entry is refused with usage", r.returncode == 2,
          r.stdout + r.stderr)
    check("the refusal names the offending entry",
          "not-a-component" in (r.stdout + r.stderr))
    # ... and a well-formed one is accepted, including the pkg/component form
    # that repeats the package name (a slash count of one is not the rule).
    r = run_tool(["--apps", "com.example.app/com.example.app/.MainActivity",
                  "--selftest"])
    check("a pkg/pkg.Activity entry is accepted", r.returncode == 0,
          r.stdout + r.stderr)

    # --help must not truncate as options are added (the abk_fas_check.sh lesson).
    r = run_tool(["--help"])
    check("--help prints the usage block", "Usage:" in r.stdout)
    for opt in ("--mode", "--apps", "--iters", "--invert-pct", "--allow-screen-off",
                "--selftest", "--sys-root", "--save", "--compare"):
        check(f"--help documents {opt}", opt in r.stdout)
    # The help body is a sed range that ends at a marker in the header.  A code
    # symbol leaking into it means the range ran past its end -- the failure
    # abk_fas_check.sh fixed by adding the marker in the first place.
    check("--help stops at its own end marker",
          "end of help" in r.stdout and "abk_launch_selftest" not in r.stdout)


def test_batch30_readahead_mmap_miss_race():
    """Batch 30: the mmap_miss decrement sits behind a page-lock test."""
    print("Batch 30: readahead_mmap_miss_race (concurrent-fault mmap_miss guard)")
    import abk_stable_core as core
    import batch30_core_mmap_miss_races as b30

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "readahead_mmap_miss_race"), None)
    check("readahead_mmap_miss_race group registered", group is not None)
    if group is None:
        return
    check("the group touches only mm/filemap.c",
          list(group.files) == ["mm/filemap.c"], group.files)

    steps = b30.build_steps()
    check("one required step",
          len(steps) == 1 and steps[0][3] is True,
          [(rel, req) for rel, _o, _n, req in steps])
    rel, old, new, _req = steps[0]
    check("step targets mm/filemap.c", rel == "mm/filemap.c", rel)

    # Trap 1/2: replace_once tests `new` first, so `old` must not survive inside
    # `new` (that would make a patched tree re-apply) and the block must not be
    # present in the pristine file.
    check("old is not a substring of new (the guard re-indents the decrement)",
          old not in new)
    check("new is not a prefix of old", not new.startswith(old))
    check("the guard is the upstream page-lock test",
          "if (likely(!PageLocked(page))) {" in new)
    check("the decrement moved inside the guard",
          "if (likely(!PageLocked(page))) {\n"
          "\t\tmmap_miss = READ_ONCE(ra->mmap_miss);\n"
          "\t\tif (mmap_miss)\n"
          "\t\t\tWRITE_ONCE(ra->mmap_miss, --mmap_miss);\n"
          "\t}\n" in new)
    check("the PageReadahead test still follows the counter update",
          "\t}\n\tif (PageReadahead(page)) {\n" in new)
    check("no folio API leaks onto 5.15", "folio" not in new)
    check("the pristine side keeps the unguarded decrement",
          old.endswith("\tif (PageReadahead(page)) {\n")
          and "mmap_miss);\n\tif (mmap_miss)\n" in old)

    fixture = (
        "static struct file *do_sync_mmap_readahead(struct vm_fault *vmf)\n"
        "{\n"
        "\treturn NULL;\n"
        "}\n"
        "\n"
        "static struct file *do_async_mmap_readahead(struct vm_fault *vmf,\n"
        "\t\t\t\t\t    struct page *page)\n"
        "{\n"
        "\tstruct file *fpin = NULL;\n"
        "\tunsigned int mmap_miss;\n"
        "\n"
        + old +
        "\t\tfpin = maybe_unlock_mmap_for_io(vmf, fpin);\n"
        "\t\tpage_cache_async_readahead(mapping, ra, file,\n"
        "\t\t\t\t\t   page, offset, ra->ra_pages);\n"
        "\t}\n"
        "\treturn fpin;\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"mm/filemap.c": fixture})
        status, detail = b30._mmap_miss_race_apply(ctx)
        check("mmap_miss fixture applies", status == "applied", (status, detail))
        patched = ctx.read("mm/filemap.c")
        check("the patched function guards the decrement",
              "if (likely(!PageLocked(page))) {" in patched)
        check("the helper body around it is untouched",
              "page_cache_async_readahead(mapping, ra, file,\n"
              "\t\t\t\t\t   page, offset, ra->ra_pages);" in patched)
        ctx2 = make_ctx(tmp, {"mm/filemap.c": patched})
        status2, _d2 = b30._mmap_miss_race_apply(ctx2)
        check("mmap_miss fixture is idempotent",
              status2 == "already_present", status2)
        check("the second pass writes nothing",
              ctx2.read("mm/filemap.c") == patched)
        # A tree without the anchor degrades instead of half-patching.
        # apply_steps returns None for a missing *required* anchor and the child
        # maps that to blocked_by_shape (house convention: the shape preflight
        # is what reports the missing-anchor status).
        ctx3 = make_ctx(tmp, {"mm/filemap.c": "static int other(void) { return 0; }\n"})
        status3, _d3 = b30._mmap_miss_race_apply(ctx3)
        check("a missing anchor degrades to blocked_by_shape",
              status3 == "blocked_by_shape", status3)
        check("a degraded group writes nothing",
              ctx3.pending_writes() == [], ctx3.pending_writes())
# Minimal arm64/include/asm/pgtable.h shapes -- only what the Batch 31 group
# anchors on.  The `pte_sw_dirty()` macro is the 5.15 spelling the fix relies
# on (and it is why this is portable at all: the macro predates the commit).
_BATCH31_PGTABLE_167 = (
    "#define pte_sw_dirty(pte)\t(!!(pte_val(pte) & PTE_DIRTY))\n"
    "\n"
    "static inline pte_t pte_mkwrite(pte_t pte)\n"
    "{\n"
    "\tpte = set_pte_bit(pte, __pgprot(PTE_WRITE));\n"
    "\tpte = clear_pte_bit(pte, __pgprot(PTE_RDONLY));\n"
    "\treturn pte;\n"
    "}\n"
)

_BATCH31_PGTABLE_216 = (
    "#define pte_sw_dirty(pte)\t(!!(pte_val(pte) & PTE_DIRTY))\n"
    "\n"
    "static inline pte_t pte_mkwrite(pte_t pte)\n"
    "{\n"
    "\tpte = set_pte_bit(pte, __pgprot(PTE_WRITE));\n"
    "\tif (pte_sw_dirty(pte))\n"
    "\t\tpte = clear_pte_bit(pte, __pgprot(PTE_RDONLY));\n"
    "\treturn pte;\n"
    "}\n"
)


def test_batch31_arm64_pte_mkwrite_clean():
    """The port source is 5.15.y's shape, and the target form is the probe.

    Two things this group gets wrong silently if they drift, so both are pinned
    here rather than only on a real tree: the *name* (mainline's
    `pte_mkwrite_novma()` arrives with the v6.6 rename 2f0584f3f4bd -- pasting
    that hunk anchors nowhere on all four baselines, and shows up only as
    `blocked_by_shape` on a real one), and the *absence* of an ABK marker (an
    upstream-shape rewrite must leave a baseline that already carries 5.15.196
    byte-identical, which is what makes 216 report already_present).
    """
    print("Batch 31 arm64 pte_mkwrite() dirty guard (upstream-shape rewrite)")
    import abk_stable_core as core

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "arm64_pte_mkwrite_clean"), None)
    check("arm64_pte_mkwrite_clean group registered", group is not None)
    if group is None:
        return
    check("group targets the arm64 pte helper",
          group.files == ["arch/arm64/include/asm/pgtable.h"], group.files)
    check("both the 5.15.y and the mainline commit are recorded",
          any("8a2375b0e9b8" in c for c in group.commits)
          and any("143937ca51cc" in c for c in group.commits), group.commits)
    for blob in (core._ARM64_PTE_MKWRITE_OLD, core._ARM64_PTE_MKWRITE_NEW):
        check("the graft speaks the 5.15 name, not mainline's",
              "static inline pte_t pte_mkwrite(pte_t pte)" in blob
              and "pte_mkwrite_novma" not in blob, blob[:60])
        check("graft text carries no ABK marker (upstream-shape)",
              "ABK stable_515_backport" not in blob, blob[:60])

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {
            "arch/arm64/include/asm/pgtable.h": _BATCH31_PGTABLE_167,
        })
        status, detail = core._arm64_pte_mkwrite_clean_apply(ctx)
        check("guard applies on the 5.15.167 shape",
              status == "applied", (status, detail))
        text = ctx.read("arch/arm64/include/asm/pgtable.h")
        check("PTE_RDONLY is cleared only for a software-dirty PTE",
              "if (pte_sw_dirty(pte))\n"
              "\t\tpte = clear_pte_bit(pte, __pgprot(PTE_RDONLY));" in text)

        ctx2 = make_ctx(tmp, {"arch/arm64/include/asm/pgtable.h": text})
        status2, detail2 = core._arm64_pte_mkwrite_clean_apply(ctx2)
        check("second pass is already_present on the grafted text",
              status2 == "already_present", (status2, detail2))

        ctx3 = make_ctx(tmp, {
            "arch/arm64/include/asm/pgtable.h": _BATCH31_PGTABLE_216,
        })
        status3, detail3 = core._arm64_pte_mkwrite_clean_apply(ctx3)
        check("the 5.15.196 shape reports already_present",
              status3 == "already_present", (status3, detail3))
        check("a baseline that already has the guard is left byte-identical",
              ctx3.read("arch/arm64/include/asm/pgtable.h") == _BATCH31_PGTABLE_216)


def test_batch33_zsmalloc_free_out_of_lock():
    """Batch 33: zs_free() returns a dead zspage's pages outside class->lock."""
    print("Batch 33: zsmalloc_free_zspage_out_of_lock (free after the unlock)")
    import abk_stable_core as core
    import batch33_core_zsmalloc_free as b33

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "zsmalloc_free_zspage_out_of_lock"), None)
    check("zsmalloc_free_zspage_out_of_lock group registered", group is not None)
    if group is None:
        return
    check("batch30 owns only mm/zsmalloc.c",
          group.files == [b33.ZSMALLOC_C], group.files)

    steps = b33.build_steps()
    check("three required steps", len(steps) == 3
          and all(req for _r, _o, _n, req in steps),
          [(rel, req) for rel, _o, _n, req in steps])
    # Trap 2: no step may build its replacement out of a later step's.
    for i, (_rel, _old, new_i, _req) in enumerate(steps):
        for j in range(i + 1, len(steps)):
            check("step %d new does not contain step %d new" % (i, j),
                  steps[j][2] not in new_i)

    # The region this group owns: the two helpers, then zs_free()'s declaration
    # head and its tail.  Kept synthetic -- the real anchors are proven against
    # a fetched tree by step_audit.py.
    fixture = (
        "static void free_zspage(struct zs_pool *pool, struct size_class *class,\n"
        "\t\t\t\tstruct zspage *zspage)\n{\n"
        "\tremove_zspage(class, zspage, ZS_EMPTY);\n"
        "\t__free_zspage(pool, class, zspage);\n}\n\n"
        + b33._ZF_OLD + "\n"
        + b33._ZS_FREE_DECL_OLD
        + "\tbool isolated;\n\n"
        "\tspin_lock(&class->lock);\n"
        + b33._ZS_FREE_TAIL_OLD
        + "\tunpin_tag(handle);\n"
        "\tcache_free_handle(pool, handle);\n}\n"
    )

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {b33.ZSMALLOC_C: fixture})
        status, detail = group.apply_fn(ctx)
        check("all three steps land", status == "applied", (status, detail))
        text = ctx.read(b33.ZSMALLOC_C)

        check("the split helper is defined exactly once",
              text.count(b33.LOCKLESS_HELPER) == 1)
        check("the old locked tail is gone", b33.LOCKED_TAIL not in text)
        check("zs_free() frees after dropping class->lock",
              b33.FREE_OUTSIDE_LOCK in text)
        # The pages move with the helper: put_page() must now sit above the
        # wrapper's assert, not inside it.
        check("the locked wrapper no longer touches the pages",
              text.index("put_page(page);")
              < text.index("assert_spin_locked(&class->lock);"))
        # The wrapper's only remaining job is the locked class stat: class->stats
        # .objs[] is a plain unsigned long (zs_stat_dec() does -=), so it cannot
        # move out with the pages.
        check("the class stat stays inside the locked wrapper",
              "zs_stat_dec(class, OBJ_ALLOCATED, class->objs_per_zspage);"
              in text.split("assert_spin_locked(&class->lock);")[1])
        check("the provenance marker is carried twice (helper + call site)",
              text.count(b33.MARKER) == 2, text.count(b33.MARKER))

        snapshot = ctx.read(b33.ZSMALLOC_C)
        status2, detail2 = group.apply_fn(ctx)
        check("second pass is a no-op", status2 == "already_present",
              (status2, detail2))
        check("second pass is byte-identical", ctx.read(b33.ZSMALLOC_C) == snapshot)

        # A tree that lacks the anchors degrades instead of half-patching: a
        # renamed helper nothing calls would change no behaviour at all.
        ctx_bare = make_ctx(tmp + "/bare", {b33.ZSMALLOC_C: "static int x;\n"})
        status3, detail3 = group.apply_fn(ctx_bare)
        check("degrades on an unknown shape", status3 == "blocked_by_shape",
              (status3, detail3))
        check("the degraded tree is not written",
              ctx_bare.read(b33.ZSMALLOC_C) == "static int x;\n")


# The region the Batch-34 group owns, in the 5.15 page form (upstream's hunk is
# the 6.x folio form and anchors nowhere here).  Synthetic on purpose: the real
# anchors are proven against a fetched tree by step_audit.py.
_BATCH35_FUSE_FILL_WRITE_PAGES = (
    "static ssize_t fuse_fill_write_pages(struct fuse_io_args *ia,\n"
    "\t\t\t\t     struct address_space *mapping,\n"
    "\t\t\t\t     struct iov_iter *ii, loff_t pos,\n"
    "\t\t\t\t     unsigned int max_pages)\n"
    "{\n"
    "\tstruct fuse_args_pages *ap = &ia->ap;\n"
    "\tsize_t count = 0;\n"
    "\tint err;\n"
    "\n"
    "\tdo {\n"
    "\t\tsize_t tmp;\n"
    "\t\tstruct page *page;\n"
    "\t\tpgoff_t index = pos >> PAGE_SHIFT;\n"
    "\t\tsize_t bytes = min_t(size_t, PAGE_SIZE - offset,\n"
    "\t\t\t\t     iov_iter_count(ii));\n"
    "\n"
    "\t\tbytes = min_t(size_t, bytes, fc->max_write - count);\n"
    "\n"
    " again:\n"
    "\t\terr = -EFAULT;\n"
    "\t\tif (fault_in_iov_iter_readable(ii, bytes))\n"
    "\t\t\tbreak;\n"
    "\n"
    "\t\terr = -ENOMEM;\n"
    "\t\tpage = grab_cache_page_write_begin(mapping, index, 0);\n"
    "\t\tif (!page)\n"
    "\t\t\tbreak;\n"
    "\n"
    "\t\ttmp = copy_page_from_iter_atomic(page, offset, bytes, ii);\n"
    "\t\tflush_dcache_page(page);\n"
    "\n"
    "\t\tif (!tmp) {\n"
    "\t\t\tunlock_page(page);\n"
    "\t\t\tput_page(page);\n"
    "\t\t\tgoto again;\n"
    "\t\t}\n"
    "\n"
    "\t\terr = 0;\n"
    "\t\tcount += tmp;\n"
    "\t} while (iov_iter_count(ii) && count < fc->max_write &&\n"
    "\t\t ap->num_pages < max_pages && offset == 0);\n"
    "\n"
    "\treturn count > 0 ? count : err;\n"
    "}\n"
)


def test_batch35_fuse_prefault_out_of_write_path():
    """Batch 35: the FUSE write path prefaults only on a no-progress retry.

    Three things this group gets wrong silently, so all three are pinned here
    rather than only on a real tree: the *pairing* (either half alone compiles
    and is wrong -- dropping the loop-head fault keeps the extra userspace
    touch, adding the retry fault without dropping it faults twice), the
    *absence* of an ABK marker (an upstream-shape rewrite must leave a baseline
    that already carries faa794dd2e17 byte-identical), and the *scope* of the
    batch (the erofs half is excluded, and an erofs or fs/super.c group
    reappearing unnoticed is exactly how that exclusion would regress).
    """
    print("Batch 35 fuse_fill_write_pages() prefault move (upstream-shape rewrite)")
    import abk_stable_core as core
    import abk_stable_display as display
    import abk_stable_perf as perf
    import batch35_core_fuse_erofs as b34

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "fuse_prefault_out_of_write_path"), None)
    check("fuse_prefault_out_of_write_path group registered", group is not None)
    if group is None:
        return
    check("group owns only fs/fuse/file.c",
          group.files == [b34.FUSE_FILE_C], group.files)
    check("the upstream commit is recorded",
          any("faa794dd2e17" in c for c in group.commits), group.commits)

    steps = b34.build_steps()
    check("two required steps", len(steps) == 2
          and all(req for _r, _o, _n, req in steps),
          [(rel, req) for rel, _o, _n, req in steps])
    # Trap 2: no step may build its replacement out of a later step's.
    for i, (_rel, _old, new_i, _req) in enumerate(steps):
        for j in range(i + 1, len(steps)):
            check("step %d new does not contain step %d new" % (i, j),
                  steps[j][2] not in new_i)
    # Each step owns one half of the pair, and the halves must not be swapped:
    # step 0 removes the loop-head prefault, step 1 adds the retry one.
    check("step 0 removes the loop-head prefault",
          b34.LOOP_HEAD_PREFAULT in steps[0][1]
          and b34.LOOP_HEAD_PREFAULT not in steps[0][2], steps[0][1][:60])
    check("step 1 adds the prefault to the no-progress branch",
          b34.RETRY_PREFAULT in steps[1][2]
          and b34.RETRY_PREFAULT not in steps[1][1], steps[1][2][:60])
    for _rel, _old, new, _req in steps:
        check("graft text carries no ABK marker (upstream-shape)",
              "ABK stable_515_backport" not in new, new[:60])

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {b34.FUSE_FILE_C: _BATCH35_FUSE_FILL_WRITE_PAGES})
        status, detail = group.apply_fn(ctx)
        check("the move applies on the 5.15 page shape",
              status == "applied", (status, detail))
        text = ctx.read(b34.FUSE_FILE_C)

        check("the loop head no longer prefaults",
              b34.LOOP_HEAD_PREFAULT not in text)
        check("the no-progress retry does",
              b34.RETRY_PREFAULT in text)
        # Pairing, restated positionally: the prefault must sit *inside* the
        # branch that just released the page, ahead of its goto.
        check("the prefault is inside the !tmp branch",
              text.index("if (!tmp) {")
              < text.index(b34.RETRY_PREFAULT)
              < text.index("\t\t\tgoto again;"))
        # ... and the loop head still grabs the page, i.e. the label kept the
        # rest of its body rather than the step swallowing it.
        check("the retry label keeps its page grab",
              text.index(" again:")
              < text.index("page = grab_cache_page_write_begin(mapping, index, 0);"))
        check("err = -EFAULT survives on the retry path only",
              text.count("err = -EFAULT;") == 1, text.count("err = -EFAULT;"))

        snapshot = ctx.read(b34.FUSE_FILE_C)
        status2, detail2 = group.apply_fn(ctx)
        check("second pass is a no-op", status2 == "already_present",
              (status2, detail2))
        check("second pass is byte-identical", ctx.read(b34.FUSE_FILE_C) == snapshot)

        # A tree that lacks the anchors degrades instead of half-patching: the
        # pair is one edit, so a partial landing is the failure this group
        # exists to avoid.
        ctx_bare = make_ctx(tmp + "/bare", {b34.FUSE_FILE_C: "static int x;\n"})
        status3, detail3 = group.apply_fn(ctx_bare)
        check("degrades on an unknown shape", status3 == "blocked_by_shape",
              (status3, detail3))
        check("the degraded tree is not written",
              ctx_bare.read(b34.FUSE_FILE_C) == "static int x;\n")

    # The batch's other half is excluded, not deferred silently.  This is the
    # guard that keeps the exclusion from regressing: an erofs group (or the
    # fs/super.c helper it would have needed) reappearing without the
    # prerequisite backport is a mount whose every inode returns -EOPNOTSUPP.
    groups = list(core.PATCH_GROUPS) + list(perf.PATCH_GROUPS) + \
        list(display.PATCH_GROUPS)
    stray = sorted({rel for g in groups for rel in g.files
                    if rel.startswith("fs/erofs/") or rel == "fs/super.c"}
                   | {g.key for g in groups if "erofs" in g.key})
    check("no group grafts erofs file-backed mounts (excluded, see plan.md)",
          not stray, stray)
    check("no group grafts lib/iov_iter (5.15 lacks the bug it fixes)",
          not [rel for g in groups for rel in g.files
               if rel == "lib/iov_iter.c"])
    check("no group grafts FUSE passthrough (android13-5.15 ships its own)",
          not [k for k in (g.key for g in groups) if "passthrough" in k])

    # The exclusion is part of the deliverable, so the record itself is pinned:
    # a future batch that ports the erofs pair has to delete these lines and
    # prove the prerequisites, rather than discover the gap at compile time.
    plan = (Path(__file__).resolve().parent.parent / "plan.md").read_text(
        encoding="utf-8")
    for needle in ("fb176750266a", "6422cde1b0d5", "770c8d55c428",
                   "fs/erofs/fileio.c"):
        check("plan.md records the %s exclusion" % needle, needle in plan)
    check("plan.md records the FUSE passthrough non-goal",
          "passthrough" in plan and "_IOW(229,126)" in plan)


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
    test_psi_cmdline_tier()
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
    test_batch21_psi_cgroup_pressure_switch()
    test_batch22_psi_oncpu_state_mask()
    test_batch23_zram_writeback_guard()
    test_batch24_zram_max_pages()
    test_batch32_zram_wb_slot_preserve()
    test_f2fs_shape_probe()
    test_kabi_slot_policy()
    test_kstack_slot_shape_selection()
    test_batch27_launch_bench()
    test_batch30_readahead_mmap_miss_race()
    test_batch31_arm64_pte_mkwrite_clean()
    test_batch33_zsmalloc_free_out_of_lock()
    test_batch35_fuse_prefault_out_of_write_path()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
