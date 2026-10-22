#!/usr/bin/env python3

from __future__ import annotations

import base64
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
CHECKS_RUN = []


def check(name, cond, detail=""):
    CHECKS_RUN.append(name)
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
            ("mm/x.c", "gamma", "GAMMA", True),
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

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"fs/file.c": "int weird;\n"})
        try:
            core._fdtable_apply(ctx)
            check("unknown shape aborts hard group", False, "no SystemExit")
        except SystemExit:
            check("unknown shape aborts hard group", True)


def test_replace_fd_errno_group():
    print("replace_fd errno group (5.15.195)")
    import abk_stable_core as core

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"fs/file.c": FD_UPSTREAM}, sub_level="216")
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

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"fs/file.c": FD_PRISTINE})
        core._fdtable_apply(ctx)
        status, detail = core._replace_fd_errno_apply(ctx)
        check("replace_fd applies after conventions", status == "applied",
              f"{status}: {detail}")

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"fs/file.c": "int unrelated;\n"})
        status, _ = core._replace_fd_errno_apply(ctx)
        check("missing anchor degrades to blocked_by_shape",
              status == "blocked_by_shape", status)
        check("degraded group wrote nothing", ctx.pending_writes() == [],
              ctx.pending_writes())


def test_display_valid_clones_revert():
    print("display valid-clones revert")
    import abk_stable_display as display

    group = next(g for g in display.PATCH_GROUPS if g.key == "drm_valid_clones_revert")
    rel = "drivers/gpu/drm/drm_atomic_helper.c"

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {rel: display._VC_FN_OLD + "\n" + display._VC_CALL_OLD})
        status, detail = group.apply_fn(ctx)
        check("185+ shape reverts", status == "applied", (status, detail))
        text = ctx.read(rel)
        check("function removed", "drm_atomic_check_valid_clones" not in text)
        check("call site removed", "drm_atomic_check_valid_clones(state, crtc)" not in text)
        status2, _ = group.apply_fn(ctx)
        check("second pass is already_present", status2 == "already_present", status2)

    with tempfile.TemporaryDirectory() as tmp:
        pre185 = display._VC_FN_NEW + "\n" + display._VC_CALL_NEW
        ctx = make_ctx(tmp, {rel: pre185})
        before = ctx.read(rel)
        status, detail = group.apply_fn(ctx)
        check("pre-185 shape already fixed",
              status == "already_present", (status, detail))
        check("pre-185 tree untouched", ctx.read(rel) == before)

    with tempfile.TemporaryDirectory() as tmp:
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

    check("registered after psi_irq_tracking",
          [g.key for g in perf.PATCH_GROUPS].index(
              "psi_cgroup_pressure_switch")
          > [g.key for g in perf.PATCH_GROUPS].index("psi_irq_tracking"))


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

    import implementation_audit as ia
    check("the config-gated reference audit covers zram_drv.c",
          "drivers/block/zram/zram_drv.c" in ia.CONFIG_GATED_REFERENCES
          and any(gate == "CONFIG_ZRAM_WRITEBACK"
                  for gate, _n in ia.CONFIG_GATED_REFERENCES[
                      "drivers/block/zram/zram_drv.c"]))


def test_batch24_zram_max_pages():
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
        check("the attempt is counted after the filters, not at the scan top",
              text.index("goto next;\n\n\t\tnum_recomp_pages--;") >
              text.index("\t\tif (!num_recomp_pages)"))

        snapshot = ctx.read(b24.ZRAM_C)
        status2, detail2 = group.apply_fn(ctx)
        check("second pass is a no-op", status2 == "already_present",
              (status2, detail2))
        check("second pass is byte-identical", ctx.read(b24.ZRAM_C) == snapshot)

        ctx_bare = make_ctx(tmp + "/bare", {b24.ZRAM_C: "static int x;\n"})
        status3, detail3 = group.apply_fn(ctx_bare)
        check("degrades without zram_recompression",
              status3 == "blocked_by_shape" and "zram_recompression" in detail3,
              (status3, detail3))

        ctx_half = make_ctx(tmp + "/half", {b24.ZRAM_C: sync_body})
        status4, detail4 = group.apply_fn(ctx_half)
        check("degrades without the async node",
              status4 == "blocked_by_shape"
              and "recompress_async_store" in detail4, (status4, detail4))

def test_sublevel_matrix():
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

    for sub_level in sublevel_matrix.SUPPORTED:
        check(f"{sub_level} covers every child",
              set(sublevel_matrix.PRE_APPLIED[sub_level]) == set(registries),
              set(sublevel_matrix.PRE_APPLIED[sub_level]))
    check("the default baseline is a supported one",
          sublevel_matrix.DEFAULT_SUB_LEVEL in sublevel_matrix.SUPPORTED,
          sublevel_matrix.DEFAULT_SUB_LEVEL)

    only = sublevel_matrix.SUPPORTED[0]
    for child, groups in registries.items():
        if child == "stable_display_fix":
            continue
        keys = {g.key for g in groups}
        leftover = keys - sublevel_matrix.pre_applied(only, child)
        check(f"{child} still really grafts on {only}",
              leftover, f"all {len(keys)} groups recorded pre-applied")
    check("display revert really applies on the rolling baseline",
          not sublevel_matrix.pre_applied(only, "stable_display_fix"),
          sublevel_matrix.pre_applied(only, "stable_display_fix"))


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
        text = ctx.read("include/linux/sched.h")
        check("slot 8 present", "ANDROID_KABI_RESERVE(8);" in text)
        perf_ctx_texts = perf  # noqa: F841 - import proves the module loads
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
        ctx = GraftContext(str(root), "216", "android13-5.15",
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

        ctx2 = GraftContext(str(root), "216", "android13-5.15",
                            defconfig=str(defconfig))
        status2, _d = ctx2.enable_configs([("B", "y")])
        check("defconfig idempotent", status2 == "already_present", status2)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "common"
        root.mkdir()
        outside = Path(tmp) / "gki_defconfig"
        outside.write_text("# bare\n")
        ctx = GraftContext(str(root), "216", "android13-5.15",
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
              and "kthread_create_worker(0, \"Sail_recompd\")" in text)
        _worker = re.search(r'kthread_create_worker\(0, "([^"]+)"\)', text)
        check("the recompress worker carries the product's short name",
              _worker is not None and _worker.group(1) == "Sail_recompd"
              and len(_worker.group(1)) <= 15,
              _worker.group(1) if _worker else "(no worker name)")
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
        ctx = make_ctx(tmp, {rel: v1_tree})
        perf.apply_steps(ctx, s10.build_steps())
        copies = ctx.read(rel).count("static bool abk_sf_enable")
        check("the upgrade branch is load-bearing (plain insert duplicates)",
              copies == 2, copies)

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
        check("a locked-out write is reported as success, not refused",
              "-EPERM" not in text and "-EACCES" not in text,
              [line for line in text.splitlines() if "-EP" in line])
        check("the lock is reported through the kernel log",
              "pr_info_ratelimited(" in text)
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

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {
            "include/trace/hooks/mm.h": _BATCH13_MM_H,
            "mm/page_alloc.c": _BATCH13_PAGE_ALLOC,
        })
        status, detail = core._gfp_pressure_fastfail_apply(ctx)
        check("policy refuses a tree without the grafted hook",
              status == "blocked_by_shape" and ctx.pending_writes() == [],
              (status, detail))

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
            sub_level = "216"

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
          and "LRU_GEN_ENABLED" not in dict(plain)
          and "ZRAM_WRITEBACK" not in dict(plain), sorted(dict(plain)))
    check("align tier adds the 6.6 GKI config deltas (incl. opt-in MGLRU)",
          dict(align).get("TCP_CONG_BBR") == "y"
          and dict(align).get("LRU_GEN_ENABLED") == "y"
          and "ZRAM_WRITEBACK" not in dict(align), sorted(dict(align)))
    check("ROM tier adds CONFIG_ZRAM_WRITEBACK",
          dict(rom).get("ZRAM_WRITEBACK") == "y"
          and "TCP_CONG_BBR" not in dict(rom), sorted(dict(rom)))
    check("ROM tier is reported in the detail string",
          status == "applied" and "ROM integration" in detail, (status, detail))
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
          psi.get("ZRAM_MULTI_COMP") == "y"
          and "LRU_GEN_ENABLED" not in psi
          and "ZRAM_WRITEBACK" not in psi
          and "TCP_CONG_BBR" not in psi, sorted(psi))
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
    print("introduced Kconfig symbols: each one has a way to compile")
    import abk_stable_core as core

    tiers = {
        "module": dict(core._MODULE_CONFIGS),
        "align": dict(core._ALIGN_CONFIGS),
        "rom": dict(core._ROM_CONFIGS),
    }

    for symbol, tier in sorted(core._INTRODUCED_KCONFIG.items()):
        if tier is None:
            continue
        check(f"{symbol} is named by the {tier} tier",
              symbol in tiers[tier], f"{tier} has {sorted(tiers[tier])}")
        check(f"{symbol} is enabled (=y) in the {tier} tier",
              tiers[tier].get(symbol) == "y", tiers[tier].get(symbol))

    for symbol in tiers["module"]:
        check(f"module-tier symbol {symbol} is recorded in _INTRODUCED_KCONFIG",
              symbol in core._INTRODUCED_KCONFIG,
              "add it to _INTRODUCED_KCONFIG in scripts/abk_stable_core.py")


def test_mglru_is_opt_in_only():
    print("MGLRU is OFF by default (device-wedge evidence); opt-in only")
    import abk_stable_core as core

    module = dict(core._MODULE_CONFIGS)
    align = dict(core._ALIGN_CONFIGS)
    check("LRU_GEN_ENABLED is NOT in the default (module) tier",
          "LRU_GEN_ENABLED" not in module, sorted(module))
    check("LRU_GEN_ENABLED is in the opt-in align tier",
          align.get("LRU_GEN_ENABLED") == "y", sorted(align))
    check("LRU_GEN_ENABLED is recorded in _INTRODUCED_KCONFIG as align",
          core._INTRODUCED_KCONFIG.get("LRU_GEN_ENABLED") == "align",
          core._INTRODUCED_KCONFIG.get("LRU_GEN_ENABLED"))

    mglru = sorted(g.key for g in core.PATCH_GROUPS if g.key.startswith("mglru_"))
    check("the MGLRU groups stay registered (compiled-in, runtime-off)",
          len(mglru) >= 6, mglru)

    ksu = (Path(__file__).resolve().parent.parent / "ksu"
           / "abk_runtime_tunables")
    tunables = (ksu / "tunables.conf").read_text(encoding="utf-8")
    check("the companion does not re-assert MGLRU on (lru_gen.enable=0)",
          re.search(r"(?m)^lru_gen\.enable=0$", tunables) is not None,
          [l for l in tunables.splitlines() if l.startswith("lru_gen.enable")])
    common_sh = (ksu / "common.sh").read_text(encoding="utf-8")
    lines = common_sh.split("\n")
    start = next((i for i, l in enumerate(lines)
                  if l.startswith("abk_apply_lru_gen()")), None)
    check("abk_apply_lru_gen() is present in common.sh", start is not None)
    if start is not None:
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i] == "}"), None)
        check("abk_apply_lru_gen() is closed by a line-initial }", end is not None)
        if end is not None:
            body = "\n".join(lines[start:end + 1])
            check("abk_apply_lru_gen() has no branch that turns MGLRU off",
                  '"n"' not in body and "write \"n\"" not in body,
                  [l for l in body.split("\n") if '"n"' in l])
            check("abk_apply_lru_gen() writes y, never a bare 1",
                  'abk_write "$_lg_node" y' in body, "the write target changed")

    check("common.sh ships the lru_gen reassert",
          "abk_reassert_lru_gen()" in common_sh
          and 'abk_write "$_rl_node" y' in common_sh,
          "no reassert write in common.sh")
    check("the reassert stays write-only (no n branch either)",
          'abk_write "$_rl_node" n' not in common_sh, "reassert writes n")
    rstart = next((i for i, l in enumerate(lines)
                   if l.startswith("abk_reassert_lru_gen()")), None)
    if rstart is not None:
        rend = next((i for i in range(rstart + 1, len(lines))
                     if lines[i] == "}"), None)
        rbody = "\n".join(lines[rstart:rend + 1]) if rend is not None else ""
        check("the reassert is gated on lru_gen.enable=1",
              'abk_cfg lru_gen.enable 0' in rbody, rbody)
        check("the reassert only fires on an even mask (bit 0 clear)",
              "*0|*2|*4|*6|*8" in rbody, rbody)
    service_sh = (ksu / "service.sh").read_text(encoding="utf-8")
    check("service.sh spawns the lru_gen supervisor on enable=1",
          "--supervise-lru-gen" in service_sh
          and 'abk_cfg lru_gen.enable 0' in service_sh, service_sh[-800:])
    check("tunables.conf ships the reassert interval key",
          re.search(r"(?m)^vm\.reassert_interval_sec=60$", tunables) is not None,
          [l for l in tunables.splitlines()
           if l.startswith("vm.reassert_interval_sec")])
    check("vm.reassert_interval_sec is a known config key",
          re.search(r"(?m)^vm\.reassert_interval_sec$", common_sh) is not None,
          "abk_cfg_lint would flag the key as unknown")


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
# the signal that exists on a platform that does not freeze.  Every process, not
# any -- the write reclaims the group as a whole, so one visible process in it
# would be reclaimed with the cached ones.  The rank is read through
# CFR_PROC_ROOT so the fixture is a fixture and not the host's own /proc.  The
# list walked is cgroup.procs, one pid per line: oom_score_adj belongs to the
# thread group, so the tool walks processes rather than re-reading one value per
# thread, and cgroup.procs is the only one of the two that v2 has.
T4=$(mktemp -d)
T4P=$(mktemp -d)
T4F=$(mktemp -d)
mkdir -p "$T4/uid_4001" "$T4/uid_4002" "$T4/uid_4003" "$T4/uid_4004"
mkdir -p "$T4P/11" "$T4P/21" "$T4P/22" "$T4P/41"
for g in 4001 4002 4003 4004; do
  echo 268435456 > "$T4/uid_$g/memory.usage_in_bytes"
  echo 555 > "$T4/uid_$g/memory.reclaim"
done
echo 11 > "$T4/uid_4001/cgroup.procs"; echo 900 > "$T4P/11/oom_score_adj"
# Two lines, not "21 22": the tool reads the list line by line.
echo 21 > "$T4/uid_4002/cgroup.procs"
echo 22 >> "$T4/uid_4002/cgroup.procs"
echo 905 > "$T4P/21/oom_score_adj"; echo 100 > "$T4P/22/oom_score_adj"
: > "$T4/uid_4003/cgroup.procs"
echo 41 > "$T4/uid_4004/cgroup.procs"; echo 899 > "$T4P/41/oom_score_adj"
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

# --no-mark is the other half of that pair: the pass still runs, the mark does
# not.  A capped sweep only advances because of this -- marking re-sets ZRAM_IDLE
# on every page older than the cutoff, and a recompressed page keeps its old age
# (zram_recompress() reads through zram_read_from_zspool(), never
# zram_accessed()), so the prefix the last pass cleared would come straight back
# and the sweep would re-drain that same prefix forever.
echo "sentinel-not-marked" > "$T/block/zram0/idle"
: > "$T/block/zram0/recompress_async"
sh "{tool_sh}" --sys-root "$T" --idle-age 3600 --no-mark
echo "nomark_idle=$(cat "$T/block/zram0/idle")"
echo "nomark_async=$(cat "$T/block/zram0/recompress_async")"

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
sh "{tool_sh}" --sys-root "$T" --no-mark --mark-idle >/dev/null 2>&1
echo "RC_NOMARK_MARKIDLE=$?"
sh "{tool_sh}" --sys-root "$T" --no-mark --daemon --mark-each-pass --idle-age 60 >/dev/null 2>&1
echo "RC_NOMARK_MARKEACH=$?"
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
    check("--no-mark skips the mark and still runs the pass",
          got.get("nomark_idle") == "sentinel-not-marked"
          and got.get("nomark_async") == "type=idle threshold=0", r.stdout)
    check("--no-mark is refused against --mark-idle and --mark-each-pass",
          got.get("RC_NOMARK_MARKIDLE") == "2"
          and got.get("RC_NOMARK_MARKEACH") == "2", r.stdout)
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


def test_batch47_zram_recomp_best_prio_skip():
    print("Batch 47: zram_recomp_best_prio_skip (skip pointless recompressions)")

    import abk_stable_core as core
    import batch24_core_zram_max_pages as b24
    import batch47_core_zram_recomp_skip as b47

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "zram_recomp_best_prio_skip"), None)
    check("zram_recomp_best_prio_skip group registered", group is not None)
    if group is None:
        return
    keys = [g.key for g in core.PATCH_GROUPS]
    for dep in ("zram_recompression", "zram_async_recompress",
                "zram_recompress_max_pages"):
        check(f"registered after {dep} (it generates an anchor)",
              keys.index("zram_recomp_best_prio_skip") > keys.index(dep))

    steps = b47.build_steps()
    check("three required steps: the predicate and one filter per node",
          len(steps) == 3 and all(req for _r, _o, _n, req in steps),
          [(rel, req) for rel, _o, _n, req in steps])
    for i, (_rel, _old, new_i, _req) in enumerate(steps):
        for j in range(i + 1, len(steps)):
            check("step %d new does not contain step %d new" % (i, j),
                  steps[j][2] not in new_i)

    check("the predicate is wrapped in CONFIG_ZRAM_MULTI_COMP",
          b47._HELP_NEW.count("#ifdef CONFIG_ZRAM_MULTI_COMP") == 1
          and b47._HELP_NEW.count("#endif") == 1)
    check("the predicate is anchored on pristine text, not on Batch 4's payload",
          "static void zram_bio_discard" not in b47._HELP_OLD
          and b47._HELP_OLD in b47._HELP_NEW)
    check("the predicate reads the priority and walks comps[] the way "
          "zram_recompress() does",
          "zram_get_priority(zram, index)" in b47._HELP_NEW
          and "prio > abk_cur && zram->comps[prio]" in b47._HELP_NEW)

    for name, old, new in (("sync", b47._SYNC_OLD, b47._SYNC_NEW),
                           ("async", b47._ASYNC_OLD, b47._ASYNC_NEW)):
        check(f"the {name} step's old is not a prefix of its new",
              not new.startswith(old))

    sync_body = (
        "static void zram_bio_discard(struct zram *zram, u32 index,\n"
        "\t\t\t\t    struct bio *bio)\n"
        "{\n}\n\n"
        + b47._HELP_OLD
        + "#ifdef CONFIG_ZRAM_MULTI_COMP\n"
        + b47.RECOMPRESS_HELPER + " struct page *page,\n"
        "\t\t\t   u32 threshold, u32 prio, u32 prio_max)\n"
        "{\n\treturn 0;\n}\n\n"
        "static ssize_t recompress_store(struct device *dev,\n"
        "\t\t\t\tstruct device_attribute *attr,\n"
        "\t\t\t\tconst char *buf, size_t len)\n"
        "{\n"
        + b24._SYNC_HEAD_NEW +
        "\n\tif (threshold >= huge_class_size)\n\t\treturn -EINVAL;\n\n"
        + b24._SYNC_LOOP_NEW +
        "\n\tif (!zram_allocated(zram, index))\n\t\tgoto next;\n\n"
        + b47._SYNC_OLD +
        "next:\n\t\tzram_slot_unlock(zram, index);\n\t}\n}\n\n"
        "#endif\n\n"
    )
    async_body = (
        "static ssize_t recompress_async_store(struct device *dev,\n"
        "\t\t\t\tstruct device_attribute *attr,\n"
        "\t\t\t\tconst char *buf, size_t len)\n"
        "{\n"
        + b24._ASYNC_HEAD_NEW +
        "\n\tif (threshold >= huge_class_size)\n\t\treturn -EINVAL;\n\n"
        + b24._ASYNC_LOOP_NEW +
        "\n\tif (!zram_allocated(zram, index))\n\t\tgoto abk_async_next;\n\n"
        + b47._ASYNC_OLD +
        "abk_async_next:\n\t\tzram_slot_unlock(zram, index);\n"
        "\t\tif (!candidate)\n\t\t\tcontinue;\n\n"
        + b24._ASYNC_ENQUEUE_NEW +
        "\t\tif (err) {\n\t\t\tret = err;\n\t\t\tbreak;\n\t\t}\n\t}\n}\n"
    )
    files = {b47.ZRAM_C: sync_body + async_body}

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, files)
        status, detail = group.apply_fn(ctx)
        check("all three steps land on the composed shape",
              status == "applied", (status, detail))
        text = ctx.read(b47.ZRAM_C)

        check("the predicate is defined exactly once",
              text.count(b47.SKIP_PREDICATE) == 1)
        check("both nodes carry the filter",
              text.count("if (abk_zram_recompress_pointless(zram, index, "
                         "prio, prio_max))") == 2)
        async_part = text[text.index("recompress_async_store"):]
        check("the async filter precedes candidate = true",
              async_part.index("if (abk_zram_recompress_pointless") <
              async_part.index("candidate = true;"),
              [ln for ln in text.split(chr(10))
               if "candidate = true" in ln or "pointless" in ln])
        check("the sync filter precedes the attempt and its cap decrement",
              text.index("if (abk_zram_recompress_pointless") <
              text.index("num_recomp_pages--;"))
        check("the sync filter follows the candidate filters",
              text.index("if (abk_zram_recompress_pointless") >
              text.index("zram_test_flag(zram, index, ZRAM_INCOMPRESSIBLE))"))

        snapshot = ctx.read(b47.ZRAM_C)
        status2, detail2 = group.apply_fn(ctx)
        check("a second pass is idempotent",
              status2 == "already_present" and ctx.read(b47.ZRAM_C) == snapshot,
              (status2, detail2))

    with tempfile.TemporaryDirectory() as tmp:
        bare = files[b47.ZRAM_C].replace(
            "\t\tnum_recomp_pages--;\n"
            "\t\terr = zram_recompress(zram, index, page, threshold, prio, prio_max);\n",
            "\t\terr = zram_recompress(zram, index, page, threshold, prio, prio_max);\n")
        ctx2 = make_ctx(tmp, {b47.ZRAM_C: bare})
        st_b, d_b = group.apply_fn(ctx2)
        check("a tree without the max_pages graft is refused, not half-patched",
              st_b == "blocked_by_shape" and ctx2.pending_writes() == [],
              (st_b, d_b))

    with tempfile.TemporaryDirectory() as tmp:
        ctx3 = make_ctx(tmp, {})
        st_e, d_e = group.apply_fn(ctx3)
        check("degrades on an empty tree",
              st_e.startswith("blocked") and ctx3.pending_writes() == [],
              (st_e, d_e))


def test_runtime_tunables_module():
    print("Batch 11 runtime tunables companion module")
    repo = Path(__file__).resolve().parent.parent
    module_dir = repo / "ksu" / "abk_runtime_tunables"
    check("companion module directory exists", module_dir.is_dir(), module_dir)
    if not module_dir.is_dir():
        return

    required = ("module.prop", "common.sh", "zram-policy.sh", "post-fs-data.sh",
                "service.sh", "action.sh", "iorap-watch.sh", "tunables.conf",
                "embed.conf", "sepolicy.rule", "README.md")
    for name in required:
        check(f"module ships {name}", (module_dir / name).is_file())

    for name in required:
        blob = (module_dir / name).read_bytes()
        check(f"{name} is LF, not CRLF", b"\r\n" not in blob,
              blob.count(b"\r\n"))

    shipped = sorted(path for path in module_dir.rglob("*")
                     if path.is_file() and path.suffix != ".log"
                     and "state" not in path.parts and "run" not in path.parts)
    shipped_names = {path.name for path in shipped}
    check("the companion ships exactly the files this list knows about",
          shipped_names == set(required),
          sorted(shipped_names ^ set(required)))
    for path in shipped:
        blob = path.read_bytes()
        check(f"{path.relative_to(module_dir)} is LF, not CRLF",
              b"\r\n" not in blob, blob.count(b"\r\n"))

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
    _versions = re.findall(r'^ABK_MODULE_(?:SET_)?VERSION="([^"]+)"', conf, re.M)
    check("both module.conf versions move together",
          len(_versions) == 2 and _versions[0] == _versions[1], _versions)
    check("module.conf carries the released version",
          _versions == ["0.76.0", "0.76.0"], _versions)

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
    check("the status report distinguishes a missing swap area from a rewrite",
          "abk_zram_need_swap_area" in action_source
          and "the swap area is missing" in action_source
          and action_source.index("abk_zram_need_swap_area")
          < action_source.index('abk_show "policy" "in force"'))

    tunables = (module_dir / "tunables.conf").read_text(encoding="utf-8")
    for forbidden in ("algo", "disksize", "mem_limit"):
        check(f"tunables.conf exposes no {forbidden} knob",
              not re.search(rf"(?m)^\s*{forbidden}", tunables))

    policy = (module_dir / "zram-policy.sh").read_text(encoding="utf-8")
    common_sh = (module_dir / "common.sh").read_text(encoding="utf-8")
    check("common.sh ABK_VERSION is computed from module.prop",
          "ABK_VERSION=\"$(sed -n 's/^version=//p'" in common_sh
          and "module.prop" in common_sh,
          re.findall(r"(?m)^ABK_VERSION=.*", common_sh))
    check("policy hardcodes the measured primary",
          'ABK_ZRAM_PRIMARY="lz4kd"' in common_sh)
    check("policy hardcodes the measured secondary",
          'ABK_ZRAM_SECONDARY="zstd"' in common_sh)
    check("the dominated lz4hc is never selected as a policy value",
          not re.search(r'(?m)^\s*ABK_ZRAM_\w+="lz4hc"', common_sh + policy))
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
    mount_code = "\n".join(l for l in mount.splitlines()
                           if not l.lstrip().startswith("#"))
    check("the dry-run branch names the real swap commands, not $_ABK_ typos",
          not re.search(r'\$_ABK_', mount_code),
          re.findall(r'\$_\w+', mount_code))

    check("the safety gate precedes the swapoff",
          "abk_zram_swap_idle_enough" in takeover
          and takeover.index("abk_zram_swap_idle_enough") < takeover.index("ABK_SWAPOFF"))
    check("the writeback attachment is remembered before the rewrite",
          "abk_zram_backing_dev" in takeover
          and takeover.index("abk_zram_backing_dev") < takeover.index("ABK_SWAPOFF"))
    check("and restored in the pre-disksize window",
          "abk_zram_attach_writeback" in takeover
          and takeover.rindex("abk_zram_attach_writeback")
          < takeover.rindex("abk_zram_mount_swap"))
    check("a device that already owns writeback is never rewritten blindly",
          "keeping the live writeback device" in takeover)

    ensure = policy[policy.index("abk_zram_ensure() {"):policy.index("abk_zram_reassert() {")]
    check("the service entry point rewrites only when the policy is not in force",
          "abk_zram_need_rewrite" in ensure
          and ensure.index("abk_zram_need_rewrite") < ensure.index("abk_zram_takeover"))
    need_rewrite = policy[policy.index("abk_zram_need_rewrite() {"):
                          policy.index("abk_zram_need_swap_area() {")]
    need_swap = policy[policy.index("abk_zram_need_swap_area() {"):
                       policy.index("abk_zram_ensure() {")]
    check("the rewrite triggers cover algorithms, init and writeback, not swap",
          all(token in need_rewrite
              for token in ("abk_zram_algorithms_ok", "abk_zram_initstate",
                            "abk_zram_writeback_wanted"))
          and "abk_zram_swap_on" not in need_rewrite)
    check("a swap-only gap is detected separately, on an initialised device",
          all(token in need_swap
              for token in ("abk_zram_initstate", "abk_zram_algorithms_ok",
                            "abk_zram_writeback_wanted", "abk_zram_swap_on")))
    check("the service entry point repairs a swap-only gap in place",
          "abk_zram_need_swap_area" in ensure
          and ensure.index("abk_zram_need_swap_area") < ensure.index("abk_zram_takeover")
          and "abk_zram_mount_swap_area" in ensure)
    repair = policy[policy.index("abk_zram_mount_swap_area() {"):
                    policy.index("abk_zram_takeover() {")]
    check("the swap-area repair never resets or rewrites the device",
          "reset\" 1" not in repair
          and 'abk_write "$ABK_ZRAM_DIR/disksize"' not in repair
          and "ABK_MKSWAP" in repair and "ABK_SWAPON" in repair)
    check("the swap-area repair treats a swap area that appeared as success",
          repair.count("if abk_zram_swap_on; then") == 1
          and repair.rstrip().endswith("abk_zram_swap_on\n}"))
    check("the swap-area repair restores the compressed-memory cap",
          repair.count("abk_zram_set_mem_limit") == 1
          and repair.index("abk_zram_set_mem_limit")
          < repair.index('while [ "$_ma_n" -lt "$ABK_ZRAM_SWAP_RETRIES" ]'))
    takeover_fn = policy[policy.index("abk_zram_takeover() {"):
                         policy.index("abk_zram_need_rewrite() {")]
    retry = takeover_fn[takeover_fn.index("if ! abk_zram_mount_swap"):
                        takeover_fn.index('_to_state="$(abk_zram_initstate)"')]
    check("the takeover retry never re-writes disksize after init_done",
          "abk_zram_mount_swap_area \"$_to_prio\"" in retry
          and "swap area recovered on the retry" in retry
          and '[ "$(abk_zram_initstate)" = "1" ]' in retry
          and retry.index('[ "$(abk_zram_initstate)" = "1" ]')
          < retry.index("abk_zram_mount_swap_area \"$_to_prio\""))
    algo_recovery = takeover_fn[takeover_fn.index("if ! abk_zram_set_algorithms"):
                                takeover_fn.index("_to_wb_ok=0")]
    check("the algorithm-failure recovery also splits on initstate",
          "abk_zram_mount_swap_area \"$_to_prio\"" in algo_recovery
          and '[ "$(abk_zram_initstate)" = "1" ]' in algo_recovery)
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

    for line in ("zram.compact.enable=1", "zram.compact.min_waste_mb=50",
                 "zram.compact.waste_pct=15"):
        check(f"tunables.conf ships the compaction default {line}",
              re.search(rf"(?m)^{re.escape(line)}\s*$", tunables) is not None)
    check("compaction keys are known tunables.conf keys",
          all(key in common_sh for key in ("zram.compact.enable",
                                           "zram.compact.min_waste_mb",
                                           "zram.compact.waste_pct")))

    _zs_clamp = {
        "zram.recomp.idle_age_sec": "_zs_age",
        "zram.recomp.interval_sec": "_zs_interval",
        "zram.recomp.mark_interval_sec": "_zs_mark_interval",
        "zram.recomp.max_pages": "_zs_max_pages",
    }
    _zs_readme = (module_dir / "README.md").read_text(encoding="utf-8")
    for _key, _value in (("zram.recomp.idle_age_sec", "900"),
                         ("zram.recomp.interval_sec", "1800"),
                         ("zram.recomp.mark_interval_sec", "3600"),
                         ("zram.recomp.max_pages", "131072")):
        check(f"tunables.conf ships {_key}={_value}",
              re.search(rf"(?m)^{re.escape(_key)}={_value}\s*$", tunables)
              is not None)
        check(f"{_key} has the same abk_cfg fallback in the supervisor",
              re.search(rf"abk_cfg {re.escape(_key)} {re.escape(_value)}\b",
                        policy) is not None)
        check(f"{_key} has the same abk_is_uint clamp in the supervisor",
              re.search(rf"abk_is_uint \"\${_zs_clamp[_key]}\" \|\| "
                        rf"{_zs_clamp[_key]}={re.escape(_value)}\b",
                        policy) is not None)
        check(f"the README documents {_key}={_value}",
              f"{_key}` | `{_value}`" in _zs_readme)


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
    setter = policy[policy.index("abk_zram_set_mem_limit() {"):
                    policy.index("abk_zram_mount_swap() {")]
    check("the mem_limit target is floored to the alignment constant",
          "abk_align_down() {" in common_sh
          and 'ABK_ZRAM_MEM_LIMIT_ALIGN=65536' in common_sh
          and 'abk_align_down "$_ml_val" "$ABK_ZRAM_MEM_LIMIT_ALIGN"' in setter)
    check("the floor can never turn the cap off (0 means uncapped)",
          'abk_gt "$_ml_val" 0 || return 0' in setter)
    check("an unchanged cap is neither rewritten nor logged",
          'if [ "$_ml_seen" = "$_ml_val" ]; then' in setter
          and setter.index('"$_ml_seen" = "$_ml_val"')
          < setter.index('abk_write "$ABK_ZRAM_DIR/mem_limit"'))
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
    check("a process that exits mid-walk cannot take the sweep down with it",
          '2>/dev/null || continue' in cfr_tool
          and 'done < "$1/cgroup.procs" || true' in cfr_tool)
    for key in ("cfr.cgroup_root", "cfr.frozen_only", "cfr.freezer_root",
                "cfr.cached_only"):
        check(f"{key} is a known tunables.conf key",
              key in common_sh
              and re.search(r"(?m)^\s*" + re.escape(key) + r"=", tunables) is not None)

    ro_path = re.compile(r"/(?:vendor|odm|product)\b|(?<!/devices)/system\b")
    # The one legal mention is the ABK_SHELL default (an exec target, never a write);
    # it is removed before the scan and then pinned exactly, below.
    ro_refs = []
    for script in sorted(module_dir.glob("*.sh")) + [module_dir / "sepolicy.rule"]:
        body = script.read_text(encoding="utf-8")
        code = "\n".join(line.split("#", 1)[0] for line in body.splitlines())
        ro_refs += [ln.strip() for ln in body.splitlines()
                    if ro_path.search(ln) and not ln.lstrip().startswith("#")]
        code = "\n".join(ln for ln in code.splitlines()
                          if 'ABK_SHELL="' not in ln)
        hit = ro_path.search(code)
        check(f"{script.name} never touches a read-only partition",
              hit is None, hit.group(0) if hit else "")
        check(f"{script.name} never relaxes SELinux",
              "setenforce" not in code and "permissive" not in code.lower())
    check("the only read-only path in the shipped scripts is the shell default",
          ro_refs == ['ABK_SHELL="${ABK_SHELL:-/system/bin/sh}"'], ro_refs)

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
    check("the companion README records the two-boot rule and every supervisor",
          "two boots" in readme_source
          and "five supervisors" in readme_source)
    _pid_writes = []
    for _sh in sorted(module_dir.glob("*.sh")):
        _pid_writes += re.findall(r'abk_pid_write\s+(\S+)\s+"([^"]*)"',
                                  _sh.read_text(encoding="utf-8"))
    check("every supervisor writes its real pid into its pid file",
          bool(_pid_writes) and all(arg == "$$" for _n, arg in _pid_writes),
          _pid_writes)
    check("the psi supervisor is one of them",
          any(name == "psi" for name, _arg in _pid_writes), _pid_writes)

    _spawns = re.findall(r'abk_spawn\s+--supervise-\S+\s+(\S+)',
                         (module_dir / "service.sh").read_text(encoding="utf-8"))
    _write_names = {name for name, _arg in _pid_writes}
    check("every spawned supervisor has a pid write, so none goes unreported",
          bool(_spawns) and set(_spawns) <= _write_names,
          (sorted(_spawns), sorted(_write_names)))
    check("status reports every supervisor it spawns",
          all(f'abk_supervisor_state {_n} ' in action_source for _n in _spawns),
          [_n for _n in _spawns
           if f'abk_supervisor_state {_n} ' not in action_source])

    # Process naming: ps NAME is argv[0], so a supervisor runs through a long-name
    # link and comm (TASK_COMM_LEN-1 bytes) carries the short alias.
    _expected_long = {
        "zram": "Sailboat_zram_supervisor",
        "cfr": "Sailboat_cfr_supervisor",
        "psi": "Sailboat_psi_supervisor",
        "lru_gen": "Sailboat_lrugen_supervisor",
        "iorap-watch": "Sailboat_iorap_supervisor",
    }
    _expected_comm = {
        "zram": "Sail_zram",
        "cfr": "Sail_cfr",
        "psi": "Sail_psi",
        "lru_gen": "Sail_lrugen",
        "iorap-watch": "Sail_iorap",
    }
    _names_fn = common_sh[common_sh.index("abk_supervisor_name() {"):
                          common_sh.index("abk_set_comm() {")]
    _table = {_m.group(1): (_m.group(2), _m.group(3))
              for _m in re.finditer(
                  r"(?m)^\s*(\S+)\) _sn_long=(\S+); _sn_comm=(\S+) ;;", _names_fn)}
    for _key, _name in _expected_long.items():
        check(f"the {_key} supervisor shows {_name} in ps",
              _table.get(_key, ("", ""))[0] == _name, _table)
    for _key, _name in _expected_comm.items():
        check(f"the {_key} supervisor carries comm {_name}",
              _table.get(_key, ("", ""))[1] == _name, _table)
    check("one mapping function is the only place either name is written",
          len(_table) == 5
          and all(common_sh.count(_n) == 1 for _n in
                  list(_expected_long.values()) + list(_expected_comm.values())),
          _table)
    check("every comm fits the 15-byte comm field",
          all(len(_n) <= 15 for _n in _expected_comm.values()), _expected_comm)
    check("every long name is longer than comm allows, which is why it is a link",
          all(len(_n) > 15 for _n in _expected_long.values()), _expected_long)
    check("every spawned supervisor has both names",
          set(_table) == set(_expected_long) == set(_spawns),
          (sorted(_table), sorted(_spawns)))
    check("comm is written without the trailing newline echo would add",
          'echo -n "$1" > "/proc/$$/comm" 2>/dev/null' in common_sh)
    check("a refused comm write is silent, never a WARN",
          '|| true' in common_sh[common_sh.index("abk_set_comm() {"):
                                 common_sh.index("abk_getprop() {")])
    for _arg, _key, _main in (
            ("--supervise-zram", "zram", "abk_zram_supervisor_main"),
            ("--supervise-cfr", "cfr", "abk_cfr_supervisor_main"),
            ("--supervise-psi", "psi", "abk_psi_supervisor_main"),
            ("--supervise-lru-gen", "lru_gen", "abk_lru_gen_supervisor_main"),
            ("--supervise-iorap", "iorap-watch", "abk_iorap_main")):
        _line = next((ln for ln in service_sh.splitlines()
                      if ln.strip().startswith(_arg + ")")), "")
        check(f"{_arg} sets comm before the supervisor body runs",
              f'abk_set_comm "$(abk_supervisor_name {_key} comm)"' in _line
              and _line.index("abk_set_comm") < _line.index(_main), _line)
    check("the spawn execs the long-name link instead of a bare sh",
          'setsid "$_sp_link" "$MODDIR/service.sh" "$_sp_arg"' in service_sh)
    check("the link is reused when it already points at the shell",
          'readlink "$_sp_link"' in service_sh
          and service_sh.index('readlink "$_sp_link"')
          < service_sh.index('ln -s "$ABK_SHELL" "$_sp_link"'))
    _spawn_fn = service_sh[service_sh.index("abk_spawn() {"):
                          service_sh.index('abk_log "service: kernel')]
    _bare_launch = _spawn_fn[_spawn_fn.index("  else\n    setsid sh"):
                             _spawn_fn.index("  fi\n  _sp_n=0")]
    check("a link that cannot be created falls back to the old launch with a WARN",
          "falling back to sh" in _spawn_fn
          and _spawn_fn.index('_sp_long="') < _spawn_fn.index("no long-name link")
          < _spawn_fn.index('if [ -n "$_sp_link" ]'))
    check("an unknown supervisor name falls back to sh without a WARN",
          "abk_warn" not in _bare_launch, _bare_launch)
    check("the link target is a read-only reference, not a write",
          'ABK_SHELL="${ABK_SHELL:-/system/bin/sh}"' in common_sh)
    check("the README documents both names and the two kernel threads",
          all(_n in readme_source for _n in _expected_long.values())
          and all(_n in readme_source for _n in _expected_comm.values())
          and "[Sail_kcompd0]" in readme_source
          and "[Sail_recompd]" in readme_source)

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
            check("embed.conf contributes exactly the five device tools",
                  sorted(name for name in names if name.startswith("bin/"))
                  == ["bin/abk_launch_bench.sh",
                      "bin/abk_psi_bench.sh",
                      "bin/abk_psi_policy.sh",
                      "bin/cached_freeze_reclaim.sh",
                      "bin/zram_recompress_trigger.sh"],
                  sorted(name for name in names if name.startswith("bin/")))
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
            check("the PSI walk books a group that went away mid-walk as vanished",
                  "ABK_n_vanished" in _psi_tool
                  and "vanished=$ABK_n_vanished" in _psi_tool
                  and _psi_walk.count("ABK_n_vanished=$((") >= 2
                  and "gone/cgroup.pressure" in _psi_tool)
            check("the PSI selftest asserts the vanished counter",
                  "vanished=1" in _psi_tool)
            _psi_bench = archive.read("bin/abk_psi_bench.sh").decode("utf-8")
            _psi_bench_code = "\n".join(l for l in _psi_bench.splitlines()
                                         if not l.lstrip().startswith("#"))
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
            check("the wake storm is a blocking wake, not a fork storm in disguise",
              "mkfifo" in _psi_bench_code and "sleep 0" not in _psi_bench_code
                  and "printf" not in _psi_bench_code
                  and "echo x >&3" in _psi_bench_code
                  and "echo y; done <" in _psi_bench_code)
            check("the bench reports the groups it left behind",
              "groups left =" in _psi_bench)
            check("the bench re-checks both arms every round and aborts if one moved",
                  "arm state changed mid-run" in _psi_bench
                  and "want=$_want_v" in _psi_bench)
            check("the bench has a help path", "-h|--help) abk_usage" in _psi_bench)
            check("the bench cannot overflow its 32-bit arithmetic",
                  "_diff / (_on_tot / 1000)" in _psi_bench_code
                  and "* 10000 / _on_tot" not in _psi_bench_code)
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
            with tempfile.TemporaryDirectory() as empty_tmp:
                check("injector discovers nothing instead of guessing",
                      ak3.discover_ak3_dir(None, start=Path(empty_tmp)) is None)
        finally:
            for key, value in saved_env.items():
                if value is not None:
                    os.environ[key] = value

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
# sys.boot_completed is the one property the zram writeback policy reads: init
# wipes /data/per_boot at exactly that point, so the attach is gated on it.  The
# stub defaults to "completed" (1); a scenario that needs the pre-boot state
# exports ABK_TEST_BOOT_COMPLETED=0 around the call.
cat > "$T/stub/getprop" <<'EOF'
#!/bin/sh
case "$1" in
  sys.boot_completed) echo "${ABK_TEST_BOOT_COMPLETED:-1}" ;;
esac
EOF
chmod +x "$T/stub/getprop"

export ABK_CONF="$T/tunables.conf"
export ABK_RUN_DIR="$T/run" ABK_STATE_DIR="$T/state" ABK_SYS_ROOT="$T/sys"
export ABK_PROC_SWAPS="$T/proc_swaps" ABK_MEMINFO="$T/meminfo"
export ABK_SWAPOFF="$T/stub/swapoff" ABK_SWAPON="$T/stub/swapon" ABK_MKSWAP="$T/stub/mkswap"
export ABK_LOSETUP="$T/stub/losetup" ABK_DD="$T/stub/dd"
export ABK_GETPROP="$T/stub/getprop"
export ABK_ZRAM_NODE="/dev/block/zram0" ABK_ZRAM_WB_DIR="$T/wb"
export MODDIR="$MD"
export ABK_STDOUT=1

. "$MD/common.sh"
. "$MD/zram-policy.sh"

# the reset/initstate loops sleep; make that instant
sleep() { return 0; }

# Model the kernel: mem_limit is write-only and mm_stat field 4 is the only readback, so a plain echo fixture could never reproduce the mismatch.
eval "$(declare -f abk_write | sed '1s/^abk_write/abk_write_node/')"
abk_write() {
  _kw_path="$1"
  _kw_val="$2"
  abk_write_node "$_kw_path" "$_kw_val" || return 1
  case "$_kw_path" in
    */disksize)
      printf 'x\n' >> "$T/disksize_writes"
      ;;
    */mem_limit)
      if [ -e "$T/mem_limit_ignored" ]; then
        # A kernel that ignores the write: the one state the readback WARN is for.
        _kw_al="$(awk -v v="$_kw_val" 'BEGIN { printf "%.0f\n", v + 4096 }')"
      else
        _kw_al="$(awk -v v="$_kw_val" \
          'BEGIN { printf "%.0f\n", int((v + 4095) / 4096) * 4096 }')"
      fi
      # Replace field 4 only, like the kernel does.
      awk -v f4="$_kw_al" 'NF >= 4 { $4 = f4 } { print }' \
        "$T/sys/block/zram0/mm_stat" > "$T/mm_stat.tmp" 2>/dev/null \
        && mv "$T/mm_stat.tmp" "$T/sys/block/zram0/mm_stat"
      ;;
  esac
  return 0
}

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
  rm -f "$T/mem_limit_ignored"
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

# 3. the kernel supports writeback and nobody owns it: the rewrite attaches one.
#    auto is opt-in now, so the fixture has to ask for it.
printf '/dev/block/zram0                        partition       16777212  0       0\n' > "$T/proc_swaps"
reset_fixture
printf 'zram.recomp.enable=1\nzram.writeback=auto\n' > "$T/tunables.conf"
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

# 12. the boot gate.  writeback=auto, but sys.boot_completed is not set yet:
#     init wipes /data/per_boot at exactly that property, so an attach before it
#     loses the file to the wipe and leaves the loop over an unlinked inode.
reset_fixture
: > "$T/sys/block/zram0/writeback"
printf 'none\n' > "$T/sys/block/zram0/backing_dev"
printf 'zram.recomp.enable=1\nzram.writeback=auto\n' > "$T/tunables.conf"
export ABK_TEST_BOOT_COMPLETED=0
echo "want12=$(abk_zram_writeback_wanted && echo yes || echo no)"
abk_zram_takeover > "$T/out12" 2>&1
echo "RC12=$?"
echo "backing12=$(abk_zram_backing_dev)"
echo "calls12=$(tr '\n' ';' < "$T/calls")"
unset ABK_TEST_BOOT_COMPLETED

# 13. after the boot the same policy attaches one; and a stranded attachment of
#     the module's own is released (loop detached, file removed) once the policy
#     says off -- including when the file was already unlinked by that wipe, which
#     is the only form the kernel still reports for it ("<path> (deleted)").
reset_fixture
: > "$T/sys/block/zram0/writeback"
printf 'none\n' > "$T/sys/block/zram0/backing_dev"
printf 'zram.recomp.enable=1\nzram.writeback=auto\n' > "$T/tunables.conf"
abk_zram_takeover > "$T/out13" 2>&1
echo "RC13=$?"
echo "backing13=$(abk_zram_backing_dev)"
echo "calls13=$(tr '\n' ';' < "$T/calls")"

reset_fixture
: > "$T/sys/block/zram0/writeback"
printf '/dev/block/loop42\n' > "$T/sys/block/zram0/backing_dev"
printf '512\n' > "$T/sys/block/zram0/writeback_limit"
printf '1\n' > "$T/sys/block/zram0/writeback_limit_enable"
mkdir -p "$T/sys/block/loop42/loop" "$T/wb"
: > "$T/wb/zram_swap"
printf '%s\n' "$T/wb/zram_swap (deleted)" > "$T/sys/block/loop42/loop/backing_file"
printf 'zram.recomp.enable=1\nzram.writeback=off\n' > "$T/tunables.conf"
abk_zram_takeover > "$T/out13b" 2>&1
echo "RC13b=$?"
echo "ours13b=$(abk_zram_backing_dev_ours /dev/block/loop42 && echo yes || echo no)"
echo "file13b=$([ -e "$T/wb/zram_swap" ] && echo present || echo removed)"
echo "calls13b=$(tr '\n' ';' < "$T/calls")"

# 14. a device the ROM owns is never released, even with writeback=off.
reset_fixture
: > "$T/sys/block/zram0/writeback"
printf '/dev/block/loop7\n' > "$T/sys/block/zram0/backing_dev"
printf '512\n' > "$T/sys/block/zram0/writeback_limit"
printf 'zram.recomp.enable=1\nzram.writeback=off\n' > "$T/tunables.conf"
abk_zram_takeover > "$T/out14" 2>&1
echo "RC14=$?"
echo "backing14=$(abk_zram_backing_dev)"
echo "calls14=$(tr '\n' ';' < "$T/calls")"

# 15. the file an older build left behind goes at the first pass, not at the next
#     rewrite: writeback=off and nothing attached, so the file is nobody's and
#     would otherwise sit in /data/per_boot until init's wipe at boot_completed.
reset_fixture
: > "$T/sys/block/zram0/writeback"
printf 'none\n' > "$T/sys/block/zram0/backing_dev"
mkdir -p "$T/wb"
: > "$T/wb/zram_swap"
printf 'zram.recomp.enable=1\nzram.writeback=off\n' > "$T/tunables.conf"
abk_zram_ensure > "$T/out15" 2>&1
echo "RC15=$?"
echo "file15=$([ -e "$T/wb/zram_swap" ] && echo present || echo removed)"

# 16. ... but not while a device is attached: then the file belongs to the
#     attachment and is the rewrite's release path's business, not a bare rm -f.
reset_fixture
: > "$T/sys/block/zram0/writeback"
printf '/dev/block/loop7\n' > "$T/sys/block/zram0/backing_dev"
mkdir -p "$T/wb"
: > "$T/wb/zram_swap"
printf 'zram.recomp.enable=1\nzram.writeback=off\n' > "$T/tunables.conf"
abk_zram_ensure > "$T/out16" 2>&1
echo "file16=$([ -e "$T/wb/zram_swap" ] && echo present || echo removed)"

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

# 6b. mem_limit: an unaligned target reads one page high forever (kernel stores PAGE_ALIGN(limit), mm_stat f4 is the only readback); fixture uses that device's MemTotal because the generic fixture's 25 % lands aligned.
reset_fixture
printf 'MemTotal:       11460012 kB\n' > "$T/meminfo"
printf 'lzo lzo-rle lz4 lz4hc lz4k lz4k_oplus [lz4kd] deflate 842 zstd\n' > "$T/sys/block/zram0/comp_algorithm"
printf '#1: lzo lzo-rle lz4 lz4hc lz4k lz4k_oplus lz4kd deflate 842 [zstd]\n' > "$T/sys/block/zram0/recomp_algorithm"
echo "ml0_raw=$(abk_mem_pct_bytes 25)"
echo "ml0_mod=$(awk -v v="$(abk_mem_pct_bytes 25)" 'BEGIN { print v % 4096 }')"
abk_zram_ensure > "$T/out6b1" 2>&1
echo "ml1_state=$(abk_zram_mem_limit)"
echo "ml1_warn=$(grep -c 'readback is' "$T/out6b1" 2>/dev/null)"
echo "ml1_log=$(grep -c 'zram mem_limit=' "$T/out6b1" 2>/dev/null)"
# steady state: the same reassert the supervisor runs every tick, twice
abk_zram_ensure > "$T/out6b2" 2>&1
abk_zram_ensure > "$T/out6b3" 2>&1
echo "ml2_lines=$(cat "$T/out6b2" "$T/out6b3" 2>/dev/null | grep -c 'mem_limit')"
# the cap is dropped behind the module's back: re-arming it must log again
printf '4096 62 20480 0 20480 0 0 0 0\n' > "$T/sys/block/zram0/mm_stat"
abk_zram_ensure > "$T/out6b4" 2>&1
echo "ml3_log=$(grep -c 'zram mem_limit=' "$T/out6b4" 2>/dev/null)"
# a kernel that ignores the write is the one case the WARN is for
reset_fixture
printf 'lzo lzo-rle lz4 lz4hc lz4k lz4k_oplus [lz4kd] deflate 842 zstd\n' > "$T/sys/block/zram0/comp_algorithm"
printf '#1: lzo lzo-rle lz4 lz4hc lz4k lz4k_oplus lz4kd deflate 842 [zstd]\n' > "$T/sys/block/zram0/recomp_algorithm"
: > "$T/mem_limit_ignored"
abk_zram_ensure > "$T/out6b5" 2>&1
echo "ml4_warn=$(grep -c 'readback is' "$T/out6b5" 2>/dev/null)"
rm -f "$T/mem_limit_ignored"
printf 'MemTotal:       15561024 kB\n' > "$T/meminfo"

reset_fixture
printf 'lzo lzo-rle lz4 lz4hc lz4k lz4k_oplus [lz4kd] deflate 842 zstd\n' > "$T/sys/block/zram0/comp_algorithm"
printf '#1: lzo lzo-rle lz4 lz4hc lz4k lz4k_oplus lz4kd deflate 842 [zstd]\n' > "$T/sys/block/zram0/recomp_algorithm"
mkdir -p "$T/sys/module/zram/parameters"
printf 'Y\n' > "$T/sys/module/zram/parameters/abk_lock_algo"
printf 'lz4kd\n' > "$T/sys/module/zram/parameters/abk_comp_algo"
: > "$T/proc_swaps"
: > "$T/calls"
: > "$T/sys/block/zram0/reset"
abk_zram_ensure > "$T/out6c" 2>&1
echo "RC6c=$?"
echo "reset6c=$(cat "$T/sys/block/zram0/reset" 2>/dev/null)"
echo "disksize6c=$(abk_zram_disksize)"
echo "ml6c=$(abk_zram_mem_limit)"
echo "calls6c=$(tr '\n' ';' < "$T/calls")"
echo "log6c=$(tr '\n' ';' < "$T/out6c")"
rm -f "$T/sys/block/zram0/reset"

reset_fixture
: > "$T/proc_swaps"
: > "$T/calls"
: > "$T/disksize_writes"
cat > "$T/stub/swapon" <<'EOF'
#!/bin/sh
echo "swapon $*" >> "$HARNESS_CALLS"
_n=$(cat "$(dirname "$HARNESS_CALLS")/swapon_calls" 2>/dev/null || echo 0)
_n=$((_n + 1))
echo "$_n" > "$(dirname "$HARNESS_CALLS")/swapon_calls"
if [ "$_n" -ge 2 ]; then
  printf '/dev/block/zram0                        partition       16777212  0       0\n' > "$(dirname "$HARNESS_CALLS")/proc_swaps"
  exit 0
fi
exit 1
EOF
chmod +x "$T/stub/swapon"
abk_zram_takeover > "$T/out6d" 2>&1
echo "RC6d=$?"
echo "swapon6d=$(grep -c 'swapon ' "$T/calls")"
echo "disksize6d=$(wc -l < "$T/disksize_writes" | tr -d ' ')"
echo "ready6d=$(grep -c 'zram ready' "$T/out6d")"
echo "recovered6d=$(grep -c 'swap area recovered on the retry' "$T/out6d")"
echo "failed6d=$(grep -c 'RECOVERY FAILED' "$T/out6d")"
printf '#!/bin/sh\necho "swapon $*" >> "$HARNESS_CALLS"\nexit 0\n' > "$T/stub/swapon"
chmod +x "$T/stub/swapon"
rm -f "$T/swapon_calls" "$T/disksize_writes"

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
echo "supervisor_args2=$(sed -n '2p' "$T/runs")"
echo "supervisor_mark=$(grep 'recompression supervisor up' "$T/state/abk_runtime_tunables.log" | grep -o 'mark=[^ ]*' | tail -1)"
echo "supervisor_compact=$(cat "$T/sys/block/zram0/compact" 2>/dev/null)"
echo "supervisor_gate_line=$(grep -o 'compact=[^ ]*' "$T/state/abk_runtime_tunables.log" | tail -1)"
echo "supervisor_pid=$([ -s "$T/state/zram.pid" ] && echo set || echo unset)"
printf 'zram.recomp.enable=1\n' > "$T/tunables.conf"

# 7b. the mark clock is counted in SWEEPS, not ticks: _zs_per_mark must divide
#     by the sweep interval, and only a fixture whose interval differs from the
#     reassert (with a second mark reached) can show it.
reset_fixture
printf 'lzo lzo-rle lz4 lz4hc lz4k lz4k_oplus [lz4kd] deflate 842 zstd\n' > "$T/sys/block/zram0/comp_algorithm"
printf '#1: lzo lzo-rle lz4 lz4hc lz4k lz4k_oplus lz4kd deflate 842 [zstd]\n' > "$T/sys/block/zram0/recomp_algorithm"
printf '1443160064 186810547 352772096 0 0 0 0 0 0\n' > "$T/sys/block/zram0/mm_stat"
# one sweep every 3 ticks, a mark every second sweep; the wrong divisor would
# still pass --no-mark on the third run.
printf 'zram.recomp.enable=1\nzram.recomp.interval_sec=60\nzram.reassert_interval_sec=20\nzram.recomp.mark_interval_sec=120\n' > "$T/tunables.conf"
: > "$T/runs"
mkdir -p "$T/fakebin"
cat > "$T/fakebin/tool.sh" <<EOF
#!/bin/sh
echo "run \$*" >> "$T/runs"
exit 1
EOF
chmod +x "$T/fakebin/tool.sh"
export ABK_RECOMP_TOOL="$T/fakebin/tool.sh"
_n=0
sleep() { _n=$((_n+1)); [ "$_n" -ge 20 ] && exit 0; return 0; }
( abk_zram_supervisor_main > "$T/out7b" 2>&1 )
sleep() { return 0; }
echo "mark_run1=$(sed -n '1p' "$T/runs")"
echo "mark_run2=$(sed -n '2p' "$T/runs")"
echo "mark_run3=$(sed -n '3p' "$T/runs")"
echo "mark_run4=$(sed -n '4p' "$T/runs")"
echo "mark_runs=$(wc -l < "$T/runs" | tr -d ' ')"
printf 'zram.recomp.enable=1\n' > "$T/tunables.conf"

# 8. tunables.conf parsing: unknown keys warn, empty means "leave alone"
reset_fixture
printf 'zram.recomp.enable=1\nvm.swappiness=\nbogus.key=7\n' > "$T/tunables.conf"
echo "cfg_default=$(abk_cfg zram.recomp.idle_age_sec 3600)"
echo "cfg_empty=$(abk_cfg vm.swappiness '')"
echo "cfg_clamped=$(abk_clamp_uint 999 0 300)"
echo "wb_mode_unset=$(abk_zram_writeback_mode)"
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
    check("takeover caps zram at 25% of RAM, floored to the mem_limit alignment",
          got.get("memlimit1") == "3983605760", r.stdout)
    check("the regression fixture reproduces the device's unaligned target",
          got.get("ml0_raw") == "2933763072" and got.get("ml0_mod") != "0",
          (got.get("ml0_raw"), got.get("ml0_mod")))
    check("the cap is written already page-aligned, so the readback is exact",
          got.get("ml1_state") == "2933719040"
          and got.get("ml1_warn") == "0" and got.get("ml1_log") == "1",
          (got.get("ml1_state"), got.get("ml1_warn"), got.get("ml1_log")))
    check("a steady-state reassert says nothing about an unchanged cap",
          got.get("ml2_lines") == "0", got.get("ml2_lines"))
    check("a cap dropped behind the module is re-armed and reported",
          got.get("ml3_log") == "1", got.get("ml3_log"))
    check("a kernel that ignores the write still trips the readback WARN",
          got.get("ml4_warn") == "1", got.get("ml4_warn"))
    check("a swap-only gap is repaired without a reset",
          got.get("RC6c") == "0" and got.get("reset6c") == ""
          and got.get("disksize6c") == "17179869184", r.stdout)
    check("... by redoing mkswap+swapon only",
          "mkswap /dev/block/zram0" in got.get("calls6c", "")
          and "swapon -p 0 /dev/block/zram0" in got.get("calls6c", "")
          and "swapoff" not in got.get("calls6c", ""), got.get("calls6c"))
    check("... and says so instead of logging a rewrite",
          "repairing without a reset" in got.get("log6c", "")
          and "swap area repaired" in got.get("log6c", "")
          and "rewrite: size=" not in got.get("log6c", "")
          and "zram ready" not in got.get("log6c", ""), got.get("log6c"))
    check("... and restores the compressed-memory cap on the repair path",
          got.get("ml6c") == "3983605760", got.get("ml6c"))
    check("the takeover retry recovers through the swap area",
          got.get("RC6d") == "0" and got.get("ready6d") == "1"
          and got.get("recovered6d") == "1" and got.get("failed6d") == "0",
          r.stdout)
    check("... and calls swapon twice without a second disksize rewrite",
          got.get("swapon6d") == "2" and got.get("disksize6d") == "1",
          (got.get("swapon6d"), got.get("disksize6d")))
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

    check("a live writeback device is re-attached, not dropped",
          got.get("backing4") == "/dev/block/loop7" and got.get("RC4") == "0", r.stdout)
    check("... and the same device is reused instead of a new loop",
          "losetup -f" not in got.get("calls4", "")
          and "dd if=/dev/zero" not in got.get("calls4", ""), got.get("calls4"))
    check("... with the ROM's own writeback limit kept",
          got.get("limit4") == "512", got.get("limit4"))

    check("a live writeback device plus swap in use refuses the rewrite",
          got.get("RC5") != "0" and "swapoff" not in got.get("calls5", ""),
          (got.get("RC5"), got.get("calls5")))
    check("... leaving the ROM's algorithm alone", got.get("primary5") == "lz4hc",
          r.stdout)

    check("the kernel-side lock is detected", got.get("locked6") == "yes", r.stdout)
    check("a locked kernel is left completely alone",
          got.get("RC6") == "0" and "swapoff" not in got.get("calls6", "")
          and 'reset" 1' not in got.get("calls6", ""), got.get("calls6"))
    check("... with only the compressed-memory cap re-asserted",
          got.get("memlimit6") == "3983605760", got.get("memlimit6"))

    check("the supervisor sweeps repeatedly after a failing pass",
          (got.get("supervisor_runs") or "0").isdigit()
          and int(got["supervisor_runs"]) >= 3, r.stdout)
    check("the supervisor drives an age-marked pass",
          "--idle-age 900" in got.get("supervisor_args", ""),
          got.get("supervisor_args"))
    check("the supervisor tick runs the gated compaction on a fragmented device",
          got.get("supervisor_compact") == "100", r.stdout)
    check("the supervisor up line carries the gate config, so the defaults are pinned",
          got.get("supervisor_gate_line") == "compact=1>50MB+15%", r.stdout)
    check("the first sweep marks and the ones after it pass --no-mark",
          "--no-mark" not in got.get("supervisor_args", "")
          and "--no-mark" in got.get("supervisor_args2", ""),
          (got.get("supervisor_args"), got.get("supervisor_args2")))
    check("the supervisor up line carries the mark cadence",
          got.get("supervisor_mark") == "mark=3600s", r.stdout)
    check("the supervisor records its own pid", got.get("supervisor_pid") == "set",
          r.stdout)

    check("the mark cadence survives long enough to reach a second mark",
          (got.get("mark_runs") or "0").isdigit()
          and int(got["mark_runs"]) >= 4, r.stdout)
    check("the first sweep marks", "--no-mark" not in got.get("mark_run1", ""),
          got.get("mark_run1"))
    check("the sweep after it passes --no-mark",
          "--no-mark" in got.get("mark_run2", ""), got.get("mark_run2"))
    check("the SECOND sweep after a mark marks again, so the cold set is "
          "refreshed once per mark_interval_sec / interval_sec sweeps",
          "--no-mark" not in got.get("mark_run3", ""), got.get("mark_run3"))
    check("the sweep after the second mark passes --no-mark",
          "--no-mark" in got.get("mark_run4", ""), got.get("mark_run4"))

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
    check("the writeback policy defaults to off and auto is opt-in",
          got.get("wb_mode_unset") == "off", r.stdout)
    check("the shipped tunables.conf carries that off default",
          any(line.strip() == "zram.writeback=off" for line in tunables.splitlines()),
          tunables)
    check("writeback is not wanted before the boot completes",
          got.get("want12") == "no", r.stdout)
    check("... and no backing file is created either",
          got.get("RC12") == "0" and not got.get("backing12")
          and "dd if=/dev/zero" not in got.get("calls12", "")
          and "losetup" not in got.get("calls12", ""),
          (got.get("RC12"), got.get("backing12"), got.get("calls12")))
    check("after boot_completed the same policy attaches a backing file",
          got.get("RC13") == "0" and got.get("backing13") == "/dev/block/loop42"
          and "dd if=/dev/zero" in got.get("calls13", ""), got.get("calls13"))
    check("a stranded attachment of the module's own is recognised after unlink",
          got.get("ours13b") == "yes", r.stdout)
    check("... and released when the policy says off",
          got.get("RC13b") == "0" and got.get("file13b") == "removed"
          and "losetup -d /dev/block/loop42" in got.get("calls13b", ""),
          (got.get("calls13b"), got.get("file13b")))
    check("a backing device the ROM owns is never released",
          got.get("RC14") == "0" and got.get("backing14") == "/dev/block/loop7"
          and "losetup -d" not in got.get("calls14", ""), got.get("calls14"))
    check("a stale backing file is removed at the first pass",
          got.get("RC15") == "0" and got.get("file15") == "removed", r.stdout)
    check("... but not while a backing device is attached",
          got.get("file16") == "present", r.stdout)
    check("byte counts survive the 32-bit shell arithmetic limit",
          got.get("ram_bytes") == "15934488576"
          and got.get("ram_25pct") == "3983622144"
          and got.get("ram_half") == "7967244288",
          (got.get("ram_bytes"), got.get("ram_25pct"), got.get("ram_half")))
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
    print("Batch 8 AutoFDO profile tool")
    tool = Path(__file__).resolve().parent.parent / "tools" / "autofdo_515_profile.sh"
    check("autofdo tool exists", tool.is_file(), tool)

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
            "VERSION = 5\nPATCHLEVEL = 15\nSUBLEVEL = 216\nEXTRAVERSION =\n")

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
              "kernel_version=5.15.216" in mtext)
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
    print("madvise_collapse step independence (silent-skip trap)")
    import abk_stable_core as core

    check("stub replacement does not contain the signature replacement",
          core._MC_FILE_SIG_NEW not in core._MC_FILE_STUB_NEW,
          "the stub embeds _MC_FILE_SIG_NEW verbatim")
    check("stub replacement really is the 5-parameter form",
          "int *res)" in core._MC_FILE_STUB_NEW
          and "BUILD_BUG();" in core._MC_FILE_STUB_NEW)

    new_blocks = {n: getattr(core, n) for n in dir(core)
                  if n.startswith("_MC_") and n.endswith("_NEW")
                  and isinstance(getattr(core, n), str)}
    nested = [(a, b) for a in new_blocks for b in new_blocks
              if a != b and new_blocks[b].replace("\r\n", "\n")
              in new_blocks[a].replace("\r\n", "\n")]
    check("no _MC_ replacement contains another", not nested, nested)

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

    collisions = []
    for i, (k1, _o1, n1) in enumerate(steps):
        for k2, o2, _n2 in steps[i + 1:]:
            if k1 == k2:
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

    check("wb_ctl is freed through RCU", "kfree_rcu(wb_ctl, rcu)" in g1)
    check("the buggy kfree(wb_ctl) form is not shipped",
          "kfree(wb_ctl);" not in g1)
    check("the completion callback runs inside an RCU read section",
          "rcu_read_lock();" in g1 and "rcu_read_unlock();" in g1)
    tail = b17._A_TAIL_NEW
    check("the reserved blk_idx is released before the drain",
          tail.index("free_block_bdev(zram, blk_idx);")
          < tail.index("while (atomic_read(&wb_ctl->num_inflight) > 0)"))
    check("a recycled request drops its block index",
          "req->blk_idx = 0;" in g1)
    check("the block index is stored through zram_set_element()",
          "zram_set_element(zram, index, req->blk_idx)" in g1
          and "zram_set_handle(zram, index, req->blk_idx)" not in g1)
    loop = b17._A_LOOP_NEW
    check("the sweep no longer submits synchronously",
          "submit_bio_wait" not in loop)
    check("the sweep yields once per iteration",
          loop.count("cond_resched();") == 1)
    check("the sweep only reads the writeback budget",
          loop.count("spin_lock(&zram->wb_limit_lock);") == 1
          and "zram->bd_wb_limit -=" not in loop)
    check("the in-flight window is marked with ZRAM_UNDER_WB",
          "zram_set_flag(zram, index, ZRAM_UNDER_WB)" in loop
          and "zram_clear_flag(zram, index, ZRAM_UNDER_WB)" in loop)
    check("the raw object read uses the 5.15 mapping API",
          "zs_map_object(zram->mem_pool, handle, ZS_MM_RO)" in g1
          and "zs_obj_read_begin(zram->mem_pool" not in g1)
    check("trailing bytes are zeroed before writeback",
          "memzero_page(page, size, PAGE_SIZE - size)" in g1)
    check("compressed writeback preserves the slot metadata",
          "zram_set_obj_size(zram, index, size)" in g1
          and "zram_set_priority(zram, index, prio)" in g1)
    check("the write half is gated on the flag", "zram->wb_compressed" in g1)
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
    src = (Path(__file__).resolve().parent.parent / "scripts"
           / "batch17_core_zram_writeback.py").read_text()
    check("the compressed-writeback probe accepts both call shapes",
          "ret = __zram_bvec_read(zram, page, index, bio, true);" in src
          and "ret = abk_zram_bvec_read(zram, page, index, bio, true);" in src)
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

    src = inspect.getsource(b17._batching_apply)
    check("_batching_apply short-circuits on its own payload",
          b32.BATCHING_PAYLOAD in src and "already_present" in src,
          [ln for ln in src.split(chr(10)) if "payload" in ln or "probe" in ln])

    steps = b32.build_steps()
    check("three required steps",
          len(steps) == 3 and all(req for _r, _o, _n, req in steps),
          [(rel, req) for rel, _o, _n, req in steps])
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
        mentions = [ln for ln in body.split(chr(10)) if "pages_stored" in ln]
        check("pages_stored is left untouched by completion",
              all(ln.lstrip().startswith("*") for ln in mentions), mentions)
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

        ctx_bare = make_ctx(tmp + "/bare", {b32.ZRAM_C: free_page})
        status3, detail3 = group.apply_fn(ctx_bare)
        check("degrades without zram_writeback_batching",
              status3 == "blocked_by_shape" and "zram_writeback_batching" in detail3,
              (status3, detail3))

        half = b17._A_HELPERS_NEW + "\nstatic void zram_free_page(void) {}\n"
        ctx_half = make_ctx(tmp + "/half", {b32.ZRAM_C: half})
        status4, detail4 = group.apply_fn(ctx_half)
        check("degrades when the free path anchor is missing",
              status4 == "blocked_by_shape", (status4, detail4))
        check("a degraded run writes nothing",
              ctx_half.read(b32.ZRAM_C) == half)


def test_batch27_launch_bench():
    print("Batch 27 cold-launch instrument")
    repo = Path(__file__).resolve().parent.parent
    tool = repo / "tools" / "abk_launch_bench.sh"
    check("launch bench exists", tool.is_file(), tool)
    text = tool.read_text(encoding="utf-8")
    check("the tool documents the screen guard",
          "dozing" in text and "top-app (cpus 0-7)" in text)
    check("the tool names its batch of origin",
          "Batch 27" in text)
    check("the tool does not claim launch_boost is a kernel feature",
          "CONFIG_LAUNCH_BOOST" not in text
          and ("xiaomi.launch_boost" not in text
               or ("userspace" in text and "is not running" in text)))
    check("cap_view goes through awk, not shell arithmetic",
          "a * f / m" in text and "int(carch[p] * clr[k] / cinfo[p])" in text)
    check("the refusal to report a zero it did not measure is present",
          "0 ms would be a lie" in text)
    check("the two placement verdicts are distinct",
          "VERDICT: ceiling bound." in text
          and "VERDICT: placement bound, cause not measured." in text)
    check("the storage verdict needs the other arm",
          "abk_storage_share" in text and "--compare" in text
          and "one arm cannot say" in text)
    check("the device guard is re-checked per launch, not only at startup",
          "abk_device_block" in text and "after $_done_launches launch(es)" in text)
    check("the lock screen is part of the guard, not just the screen",
          "mDreamingLockscreen" in text and "lock screen is up" in text
          and "mShowingLockscreen" in text and "stayed false" in text)
    check("a run that stops producing LaunchStates aborts",
          "produced no LaunchState" in text)
    check("the cpuset range form is expanded, not compared as one token",
          "abk_expand_cpus" in text)
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
    for case in ("a capped super core counts as an inversion",
                 "a ceiling that moves is reported as ceilings=3",
                 "a pid that never appeared yields 0 polls",
                 "cpulist 0-7 contains cpu7 and 0-6 does not",
                 "the processor field survives a paren-wrapped and a spaced comm",
                 "the two-arm storage share is 33% here"):
        check(f"selftest covers: {case}", case in r.stdout)

    r = run_tool(["--apps", "not-a-component", "--selftest"])
    check("a malformed --apps entry is refused with usage", r.returncode == 2,
          r.stdout + r.stderr)
    check("the refusal names the offending entry",
          "not-a-component" in (r.stdout + r.stderr))
    r = run_tool(["--apps", "com.example.app/com.example.app/.MainActivity",
                  "--selftest"])
    check("a pkg/pkg.Activity entry is accepted", r.returncode == 0,
          r.stdout + r.stderr)

    r = run_tool(["--help"])
    check("--help prints the usage block", "Usage:" in r.stdout)
    for opt in ("--mode", "--apps", "--iters", "--invert-pct", "--allow-screen-off",
                "--selftest", "--sys-root", "--save", "--compare"):
        check(f"--help documents {opt}", opt in r.stdout)
    check("--help stops at its own end marker",
          "end of help" in r.stdout and "abk_launch_selftest" not in r.stdout)


def test_batch30_readahead_mmap_miss_race():
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
        ctx3 = make_ctx(tmp, {"mm/filemap.c": "static int other(void) { return 0; }\n"})
        status3, _d3 = b30._mmap_miss_race_apply(ctx3)
        check("a missing anchor degrades to blocked_by_shape",
              status3 == "blocked_by_shape", status3)
        check("a degraded group writes nothing",
              ctx3.pending_writes() == [], ctx3.pending_writes())
_BATCH31_PGTABLE_PRECOMMIT = (
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
            "arch/arm64/include/asm/pgtable.h": _BATCH31_PGTABLE_PRECOMMIT,
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
    for i, (_rel, _old, new_i, _req) in enumerate(steps):
        for j in range(i + 1, len(steps)):
            check("step %d new does not contain step %d new" % (i, j),
                  steps[j][2] not in new_i)

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
        check("the locked wrapper no longer touches the pages",
              text.index("put_page(page);")
              < text.index("assert_spin_locked(&class->lock);"))
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

        ctx_bare = make_ctx(tmp + "/bare", {b33.ZSMALLOC_C: "static int x;\n"})
        status3, detail3 = group.apply_fn(ctx_bare)
        check("degrades on an unknown shape", status3 == "blocked_by_shape",
              (status3, detail3))
        check("the degraded tree is not written",
              ctx_bare.read(b33.ZSMALLOC_C) == "static int x;\n")


def _batch37_memory_reclaim_fixture(b37):
    return (
        b37._TOKENS_OLD
        + "\t\t\t      size_t nbytes, loff_t off)\n"
        "{\n"
        + b37._PARSER_OLD
        + "\n"
        + b37._BATCH_DECL_OLD
        + b37._SIGNAL_OLD
        + "\t\t * hope of introducing more evictable pages for\n"
        "\t\t * try_to_free_mem_cgroup_pages().\n"
        "\t\t */\n"
        "\t\tif (!nr_retries)\n"
        "\t\t\tlru_add_drain_all();\n"
        "\n"
        + b37._CALL_OLD
        + "\n"
        "\t\tif (!reclaimed && !nr_retries--)\n"
        "\t\t\treturn -EAGAIN;\n"
        "\n"
        "\t\tnr_reclaimed += reclaimed;\n"
        "\t}\n"
        "\n"
        "\treturn nbytes;\n"
        "}\n"
    )


def test_batch37_reclaim_paths():
    print("Batch 37: proactive reclaim's batch, swappiness= and suspend abort")
    import abk_stable_core as core
    import batch37_core_reclaim_paths as b37

    keys = ["proactive_reclaim_batch_fidelity",
            "proactive_reclaim_decaying_batches",
            "reclaim_swappiness_defines",
            "proactive_reclaim_swappiness_arg",
            "proactive_reclaim_suspend_abort",
            "lru_add_drain_dead_folios"]
    groups = {g.key: g for g in core.PATCH_GROUPS if g.key in keys}
    check("all six Batch 37 groups are registered", set(groups) == set(keys),
          sorted(groups))
    if set(groups) != set(keys):
        return
    order = [g.key for g in core.PATCH_GROUPS if g.key in keys]
    check("the chain is registered in dependency order", order == keys, order)
    for group in groups.values():
        check(f"{group.key} declares its own files",
              group.files and all(isinstance(f, str) for f in group.files),
              group.files)

    chain_steps = [dict(zip(["rel", "old", "new", "req"], s))
                   for s in b37.build_steps_batch_fidelity()]
    chain_steps += [dict(zip(["rel", "old", "new", "req"], s))
                    for s in b37.build_steps_decaying_batches()]
    chain_steps += [dict(zip(["rel", "old", "new", "req"], s))
                    for s in b37.build_steps_swappiness_arg()]
    for i, a in enumerate(chain_steps):
        for j, b in enumerate(chain_steps):
            if i >= j or a["rel"] != b["rel"]:
                continue
            check(f"chain step {i} does not contain step {j}",
                  not (a["new"] in b["new"] and i < j)
                  and not (b["new"] in a["new"] and j < i))

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"mm/memcontrol.c":
                             b37.MEMORY_RECLAIM_MARKER + "\n"
                             "static ssize_t memory_reclaim(struct kernfs_open_file *of,"
                             " char *buf,\n"})
        status, _detail = core._memcg_reclaim_apply(ctx)
        check("memcg_memory_reclaim stops on its own marker",
              status == "already_present" and ctx.pending_writes() == [],
              (status, ctx.pending_writes()))

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"mm/memcontrol.c": b37.DECAYING_BATCH_DECL})
        status, _d = groups["proactive_reclaim_batch_fidelity"].apply_fn(ctx)
        check("the fixed cap does not reappear once the batch decays",
              status == "already_present" and ctx.pending_writes() == [], status)
        status2, _d2 = groups["proactive_reclaim_decaying_batches"].apply_fn(ctx)
        check("the decaying batch is recognised on a second pass",
              status2 == "already_present" and ctx.pending_writes() == [], status2)

    fixture = _batch37_memory_reclaim_fixture(b37)
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {
            "mm/memcontrol.c": (
                fixture
                + b37._INCLUDE_OLD
                + b37._SWAPPINESS_WRITE_OLD
                + b37._CALL_RECLAIM_HIGH_OLD + b37._CALL_TRY_CHARGE_OLD
                + b37._CALL_RESIZE_MAX_OLD + b37._CALL_FORCE_EMPTY_OLD
                + b37._CALL_HIGH_WRITE_OLD + b37._CALL_MAX_WRITE_OLD),
            "mm/vmscan.c": (
                b37._SC_FIELD_OLD + b37._SC_HELPER_OLD + b37._SC_HELPER_NO_MEMCG_OLD
                + b37._GET_SCAN_COUNT_OLD + b37._SWAPPINESS_LITERAL_FP_OLD
                + b37._GET_SWAPPINESS_OLD
                + b37._TTFMCP_OLD + "};\n}\n"
                + b37._SHOULD_ABORT_OLD),
            "include/linux/swap.h": b37._DEFINES_OLD + b37._PROTO_OLD,
            "Documentation/admin-guide/cgroup-v2.rst": b37._DOC_OLD,
        })
        for key in keys[:5]:
            status, detail = groups[key].apply_fn(ctx)
            check(f"{key} applies over the synthetic handler",
                  status == "applied", (status, detail))
        mc = ctx.read("mm/memcontrol.c")
        vm = ctx.read("mm/vmscan.c")
        check("the batch decays rather than capping",
              b37.DECAYING_BATCH_DECL in mc and b37.SWAP_CLUSTER_BATCH not in mc)
        check("the swappiness key is parsed and reaches the reclaim call",
              b37.SWAPPINESS_ARG in mc and '"swappiness=%d"' in mc)
        check("the freezer's signal is not converted to -EINTR",
              b37.SUSPEND_ABORT_ERRNO in mc
              and "return -EINTR;" not in mc.split("memory_reclaim")[-1])
        check("the scan balance and the MGLRU abort both learned about it",
              "sc_swappiness(sc, memcg)" in vm
              and b37.SUSPEND_ABORT_GUARD in vm)
        check("the manual documents the nested key",
              "Swappiness value to reclaim with"
              in ctx.read("Documentation/admin-guide/cgroup-v2.rst"))

        snap = {rel: ctx.read(rel) for rel in ctx.pending_writes()}
        for key in keys[:5]:
            status, detail = groups[key].apply_fn(ctx)
            check(f"{key} is idempotent", status == "already_present", (status, detail))
        for rel, text in snap.items():
            check(f"second pass is byte-identical for {rel}", ctx.read(rel) == text)

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"mm/swap.c": b37._LRU_ADD_OLD + b37._RELEASE_OLD})
        status, detail = groups["lru_add_drain_dead_folios"].apply_fn(ctx)
        check("the dead-page filter applies", status == "applied", (status, detail))
        swap = ctx.read("mm/swap.c")
        check("a page on its last reference is taken out of the add batch",
              b37.DEAD_PAGE_FILTER in swap
              and "free_unref_page_list(&pages_to_free);" in swap)
        check("the flags the LRU add would have cleared are cleared here",
              "__ClearPageActive(page);" in swap
              and "__ClearPageUnevictable(page);" in swap)
        check("the deferred-split unqueue is not carried onto the page API",
              "folio_unqueue_deferred_split(folio);" not in swap
              and "list_del(page_deferred_list(page));" not in swap)
        check("release_pages() tolerates the vacated slot",
              "if (!page)\n\t\t\tcontinue;" in swap)
        snapshot = ctx.read("mm/swap.c")
        status2, _d2 = groups["lru_add_drain_dead_folios"].apply_fn(ctx)
        check("the lru_add group is idempotent", status2 == "already_present", status2)
        check("the lru_add group's second pass is byte-identical",
              ctx.read("mm/swap.c") == snapshot)

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {"mm/swap.c": "static int x;\n"})
        status, _d = groups["lru_add_drain_dead_folios"].apply_fn(ctx)
        check("the lru_add group degrades on an unknown shape",
              status == "blocked_by_shape", status)
        check("the degraded tree is not written",
              ctx.read("mm/swap.c") == "static int x;\n")


def test_batch35_pagecache_pt():
    print("Batch 35: shadow-entry sweeps")
    import abk_stable_core as core
    import batch35_core_pagecache_pt as b34

    keys = [g.key for g in core.PATCH_GROUPS]
    groups = {g.key: g for g in core.PATCH_GROUPS}
    for key in ("truncate_shadow_batch", "truncate_shadow_batch_sweep"):
        check(f"{key} group registered", key in groups)

    for key in ("madvise_pt_reclaim", "madvise_batch_tlb_flush"):
        check(f"{key} group removed", key not in groups)

    check("the sweep group follows the batch group",
          keys.index("truncate_shadow_batch")
          < keys.index("truncate_shadow_batch_sweep"))

    for key, builder in (
            ("truncate_shadow_batch", b34.build_shadow_batch_steps),
            ("truncate_shadow_batch_sweep", b34.build_shadow_sweep_steps)):
        steps = builder()
        check(f"{key}: every step is required",
              all(req for _rel, _old, _new, req in steps),
              [(rel, req) for rel, _o, _n, req in steps])
        for i, (_rel, _old, new_i, _req) in enumerate(steps):
            for j in range(i + 1, len(steps)):
                check("%s: step %d new does not contain step %d new"
                      % (key, i, j), steps[j][2] not in new_i)

    check("the two sweep call-site steps are textually distinct",
          b34._SWEEP_BIP_CALL_NEW != b34._SWEEP_IIP_CALL_NEW)
    check("the two sweep loop steps are textually distinct",
          b34._SWEEP_BIP_LOOP_NEW != b34._SWEEP_IIP_LOOP_NEW)
    check("the two batch clear steps are textually distinct",
          b34._BIP_CLEAR_NEW != b34._IIP_CLEAR_NEW)
    class _ProbeCtx:
        def __init__(self, texts):
            self._texts = texts

        def read(self, rel):
            return self._texts[rel]

    probe_cases = (
        (b34._shadow_batch_probe, b34.TRUNCATE,
         b34._BATCH_FN_OLD, b34._SWEEP_FN_NEW),
        (b34._shadow_sweep_probe, b34.TRUNCATE,
         b34._SWEEP_FN_OLD, b34._SWEEP_FN_NEW),
    )
    for probe, rel, before, after in probe_cases:
        check(f"{probe.__name__}: false before its payload lands",
              probe(_ProbeCtx({rel: before})) is False)
        check(f"{probe.__name__}: true once its payload is in the tree",
              probe(_ProbeCtx({rel: after})) is True)
    check("the truncate pair uses two different probes",
          b34._shadow_batch_probe.__name__ != b34._shadow_sweep_probe.__name__)
    check("the batch probe stops on the pagevec form, not the sweep form",
          b34._shadow_batch_probe(_ProbeCtx({b34.TRUNCATE: b34._SWEEP_FN_NEW}))
          is True)

    truncate_fixture = (
        "static inline void __clear_shadow_entry(struct address_space *mapping,\n"
        "\t\t\t\tpgoff_t index, void *entry)\n{\n"
        "\tXA_STATE(xas, &mapping->i_pages, index);\n"
        "\n"
        "\txas_set_update(&xas, workingset_update_node);\n"
        "\tif (xas_load(&xas) != entry)\n"
        "\t\treturn;\n"
        "\txas_store(&xas, NULL);\n}\n"
        "\n"
        + b34._BATCH_FN_OLD + "\n"
        + b34._WRAPPERS_OLD + "\n"
        "static unsigned long __invalidate_mapping_pages(struct address_space *mapping,\n"
        "\t\tpgoff_t start, pgoff_t end, unsigned long *nr_pagevec)\n{\n"
        "\tpgoff_t indices[PAGEVEC_SIZE];\n"
        "\tstruct pagevec pvec;\n"
        "\tpgoff_t index = start;\n"
        "\tunsigned long ret;\n"
        + b34._BIP_DECL_OLD
        + "\n\tpagevec_init(&pvec);\n"
        "\twhile (find_lock_entries(mapping, index, end, &pvec, indices)) {\n"
        "\t\tfor (i = 0; i < pagevec_count(&pvec); i++) {\n"
        "\t\t\tstruct page *page = pvec.pages[i];\n"
        "\t\t\tindex = indices[i];\n"
        + b34._BIP_ENTRY_OLD
        + "\t\t\tcount += ret;\n"
        "\t\t}\n"
        "\t\tpagevec_remove_exceptionals(&pvec);\n"
        "\t\tpagevec_release(&pvec);\n"
        "\t}\n"
        "\treturn count;\n}\n"
        "\n"
        "int invalidate_inode_pages2_range(struct address_space *mapping,\n"
        "\t\t\t\t  pgoff_t start, pgoff_t end)\n{\n"
        "\tpgoff_t indices[PAGEVEC_SIZE];\n"
        "\tstruct pagevec pvec;\n"
        "\tpgoff_t index;\n"
        "\tint i;\n"
        "\tint ret = 0;\n"
        + b34._IIP_DECL_OLD
        + "\n\tpagevec_init(&pvec);\n"
        "\tindex = start;\n"
        "\twhile (find_get_entries(mapping, index, end, &pvec, indices)) {\n"
        "\t\tfor (i = 0; i < pagevec_count(&pvec); i++) {\n"
        "\t\t\tstruct page *page = pvec.pages[i];\n"
        "\t\t\tindex = indices[i];\n"
        + b34._IIP_ENTRY_OLD
        + "\t\t\tunlock_page(page);\n"
        "\t\t}\n"
        "\t\tpagevec_remove_exceptionals(&pvec);\n"
        "\t\tpagevec_release(&pvec);\n"
        "\t}\n"
        "\treturn ret;\n}\n"
        "static void truncate_exceptional_pvec_entries(struct address_space *mapping,\n"
        "\t\t\t\tstruct pagevec *pvec, pgoff_t *indices)\n{\n"
        "\t__clear_shadow_entry(mapping, index, page);\n}\n"
    )

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {b34.TRUNCATE: truncate_fixture})
        group = groups["truncate_shadow_batch"]
        status, detail = group.apply_fn(ctx)
        check("the batch group applies", status == "applied", (status, detail))
        text = ctx.read(b34.TRUNCATE)
        check("the pagevec helper really lands first",
              "clear_shadow_entries(struct address_space *mapping,\n"
              "\t\t\t\t struct pagevec *pvec, pgoff_t *indices)" in text)
        check("the per-entry wrapper definitions are gone",
              "static int invalidate_exceptional_entry" not in text)
        check("the truncate path's own caller survives",
              "truncate_exceptional_pvec_entries" in text
              and text.count("\t__clear_shadow_entry(mapping, index, page);") == 1)

        sweep = groups["truncate_shadow_batch_sweep"]
        status2, detail2 = sweep.apply_fn(ctx)
        check("the sweep group applies on top of it",
              status2 == "applied", (status2, detail2))
        text = ctx.read(b34.TRUNCATE)
        check("the pagevec form is gone from the end state",
              "&pvec, indices);" not in text)
        check("the sweep walks the index span once",
              "xas_for_each(&xas, page, max)" in text)
        check("both call sites carry the batch span",
              text.count("clear_shadow_entries(mapping, indices[0], indices[nr-1]);")
              == 2)
        check("both loops index by the batch size",
              text.count("int nr = pagevec_count(&pvec);") == 2)
        check("__clear_shadow_entry() keeps its truncate caller",
              text.count("\t__clear_shadow_entry(mapping, index, page);") == 1)

        for name, fn in (("batch", group), ("sweep", sweep)):
            status3, detail3 = fn.apply_fn(ctx)
            check(f"second pass: the {name} group is already_present",
                  status3 == "already_present", (status3, detail3))

        bare = make_ctx(tmp + "/bare35", {b34.TRUNCATE: "static int x;\n"})
        status4, _detail4 = group.apply_fn(bare)
        check("degrades on an unknown shape", status4 == "blocked_by_shape")
        check("the degraded tree is not written",
              bare.read(b34.TRUNCATE) == "static int x;\n")


def test_batch41_kcompressd_offload():
    print("Batch 41: vm_kcompressd_swapout (per-node kcompressd offload)")
    import abk_stable_core as core
    import batch41_core_vm_kcompressd as b41

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "vm_kcompressd_swapout"), None)
    check("vm_kcompressd_swapout group registered", group is not None)
    if group is None:
        return
    check("the group owns exactly mm/page_io.c",
          group.files == [b41.PAGE_IO], group.files)

    steps = b41.build_steps()
    check("four required steps, all in mm/page_io.c",
          [rel for rel, _o, _n, req in steps] == [b41.PAGE_IO] * 4
          and all(req for _r, _o, _n, req in steps),
          [(rel, req) for rel, _o, _n, req in steps])
    decl_step = next(s for s in steps if s[0] == b41.PAGE_IO
                     and "static bool abk_kcompressd_store(struct page *page);"
                     in s[2])
    check("the prototype step keeps the pristine doc comment with its function",
          b41._C_DECL_NEW.index(" * We may have stale swap cache pages")
          > b41._C_DECL_NEW.index("static bool abk_kcompressd_store"),
          b41._C_DECL_NEW)
    check("the prototype's anchor is the doc comment plus the signature",
          b41._C_DECL_OLD == b41.DECL_ANCHOR, b41._C_DECL_OLD)
    def pristine_text(frontswap):
        return (
            "#include <linux/sched/task.h>\n"
            "\n"
            "/*\n"
            " * We may have stale swap cache pages in memory: notice\n"
            " * them here and get rid of the unnecessary final write.\n"
            " */\n"
            "int swap_writepage(struct page *page, struct writeback_control *wbc)\n"
            "{\n"
            "\tint ret = 0;\n"
            "\n"
            + frontswap +
            "\tret = __swap_writepage(page, wbc, end_swap_bio_write);\n"
            "out:\n"
            "\treturn ret;\n"
            "}\n"
            "\n"
            + b41._ENGINE_ANCHOR_OLD
        )

    for label, frontswap in (("plain", b41.PLAIN_PROBE),
                             ("hook", b41.HOOK_PROBE)):
        base = pristine_text(frontswap)
        for index, (_rel, _old, new, _req) in enumerate(steps):
            check(f"{label} shape: step {index}'s replacement is not already "
                  "in the pristine file", new not in base)
    for i, (_rel, _old, new_i, _req) in enumerate(steps):
        for j, (_rel2, old_j, new_j, _req2) in enumerate(steps):
            if i == j or new_i in old_j:
                continue
            check(f"step {i} does not pre-create step {j}'s replacement",
                  new_j not in new_i)
    check("the offload step keeps the pristine tail verbatim",
          steps[1][2].endswith(b41._C_OFFLOAD_OLD), steps[1][2])
    check("the engine step keeps the file's last function verbatim",
          steps[3][2].startswith(b41._ENGINE_ANCHOR_OLD))

    prefixes = {
        "plain": b41.PLAIN_PROBE,
        "hook": b41.HOOK_PROBE,
    }
    for label, frontswap in prefixes.items():
        pristine = pristine_text(frontswap)
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx(tmp, {b41.PAGE_IO: pristine})
            body = b41.swapout_body(ctx)
            check(f"{label} shape: the probe resolves", body is not None)
            if body is None:
                continue
            status, detail = core._vm_kcompressd_swapout_apply(ctx)
            check(f"{label} shape: all three steps apply",
                  status == "applied", (status, detail))
            text = ctx.read(b41.PAGE_IO)

            check(f"{label} shape: the store path is hooked in",
                  "\tif (abk_kcompressd_store(page)) {\n" in text
                  and "\t\treturn 0;\n" in text)
            offload_block = re.search(r"\tif \(abk_kcompressd_store\(page\)\)"
                                      r" \{\n(.*?)\n\t\}\n", text, re.S)
            check(f"{label} shape: the offload releases the page lock",
                  offload_block is not None
                  and "unlock_page(page);" in offload_block.group(1),
                  offload_block.group(1) if offload_block else "(no block)")
            check(f"{label} shape: the thread is named Sail_kcompd%d",
                  '"Sail_kcompd%d", nid);' in text
                  and '"kcompressd%d", nid);' not in text)
            # nid is bounded by arm64's NODES_SHIFT 4 (MAX_NUMNODES 16), so the
            # rendered comm is at most 13 bytes and survives TASK_COMM_LEN-1.
            _kcd_name = re.search(r'"(Sail_kcompd%d)", nid\);', text)
            check(f"{label} shape: the rendered thread name fits comm",
                  _kcd_name is not None
                  and len(_kcd_name.group(1) % 9) <= 15,
                  _kcd_name.group(1) if _kcd_name else "(no name)")
            check(f"{label} shape: the sysctl name and log prefix keep their identifiers",
                  '.procname\t= "kcompressd",' in text
                  and 'pr_err("kcompressd: node %d failed to start' in text)
            check(f"{label} shape: per-node state is private",
                  "static struct abk_kcompressd_node "
                  "abk_kcompressd_nodes[MAX_NUMNODES];" in text,
                  [ln.strip() for ln in text.splitlines()
                   if "abk_kcompressd_nodes[" in ln])
            check(f"{label} shape: the knob and its counters are registered",
                  'register_sysctl("vm", abk_kcompressd_sysctl_table);' in text
                  and ".procname	= \"kcompressd\"," in text
                  and ".procname	= \"kcompressd_enqueued\"," in text)
            check(f"{label} shape: the three-argument __swap_writepage form",
                  "__swap_writepage(page, &wbc, end_swap_bio_write);" in text)
            check(f"{label} shape: the reference the refcount argument needs",
                  "get_page(page);" in text and "put_page(page);" in text)
            check(f"{label} shape: the head-drain ordering",
                  "unlikely(!kfifo_out(&kcd->fifo, &head, sizeof(page)))" in text
                  and "if (head)" in text)
            check(f"{label} shape: the gates",
                  "if (!current_is_kswapd())" in text
                  and "if (!PageAnon(page))" in text
                  and "if (memcg_is_dying(page_memcg(page)))" in text
                  and "if (!frontswap_enabled() &&" in text
                  and "SWP_SYNCHRONOUS_IO" in text)

            hook_call = "\t\ttrace_android_vh_shrink_page_lock_owner_clear(page);\n"
            hooked = ("\t\tset_page_writeback(page);\n" + hook_call) in text
            check(f"{label} shape: the frontswap hook follows the tree's shape",
                  hooked == (label == "hook"), (hooked, label))
            check(f"{label} shape: the engine replicates the hook exactly once",
                  text.count(hook_call) == (2 if label == "hook" else 0),
                  text.count(hook_call))

            engine = text.split(
                "ABK stable_515_backport: Batch 41 -- kcompressd swap-out"
                " offload.", 1)[1]
            do_swapout = engine[:engine.index("/*\n * abk_kcompressd_enqueue")]
            locks = [ln.strip() for ln in do_swapout.split("\n")
                     if ln.strip() == "lock_page(page);"]
            check("do_swapout takes the page lock exactly once",
                  locks == ["lock_page(page);"], locks)
            check("do_swapout drops the reference on both exits",
                  do_swapout.count("put_page(page);") == 2,
                  do_swapout.count("put_page(page);"))
            check("do_swapout counts both branches",
                  do_swapout.count("atomic_long_inc(&abk_kcompressd_swapped);")
                  == 1)
            check("do_swapout has no leftover goto out",
                  "goto out;" not in do_swapout)
            check("the engine opens no preprocessor gate",
                  "#if" not in engine and "#endif" not in engine)

            guard = ('\tif (unlikely(!PageSwapCache(page))) {\n'
                     '\t\tpr_warn_once("kcompressd: dropping a queued page')
            check("the swap-slot guard skips the write instead of warning",
                  guard in text, text.count("pr_warn_once"))
            code_lines = [ln for ln in engine.split("\n")
                          if not ln.lstrip().startswith(("*", "/*", "//"))]
            warns = [ln.strip() for ln in code_lines
                     if "WARN_ON" in ln or "WARN(" in ln]
            check("no WARN in the payload's code (a WARN is an oops, and "
                  "panic_on_oops turns it into a panic)",
                  not warns, warns)
            check("the drain thread cannot enqueue into its own FIFO",
                  "if (unlikely(kcd->task == current))" in text)
            check("the kswapd-only gate is still the one that rejects",
                  "if (!current_is_kswapd())" in text)
            check("the knob registration fails closed",
                  "struct ctl_table_header *header;" in text
                  and 'header = register_sysctl("vm", '
                      'abk_kcompressd_sysctl_table);' in text
                  and "if (!header) {" in text
                  and 'pr_err("kcompressd: cannot register '
                      '/proc/sys/vm/kcompressd, offload disabled' in text)

            check("the node bring-up and teardown both exist",
                  "static void abk_kcompressd_add_node(int nid)" in text
                  and "static void abk_kcompressd_del_node(int nid)" in text)
            check("the teardown NULLs the task pointer under the ring lock",
                  "task = kcd->task;" + "\n" + "\t" + "kcd->task = NULL;" in text)
            check("the teardown wakes before kthread_stop",
                  text.index("wake_up_interruptible(&kcd->wait);")
                  < text.index("kthread_stop(task);"))
            check("the teardown drains and frees the ring",
                  "while (kfifo_out(&kcd->fifo, &page, sizeof(page)))" in text
                  and "kfifo_free(&kcd->fifo);" in text)
            check("the stop flag is part of the wait predicate",
                  "!kfifo_is_empty(&kcd->fifo) ||" in text
                  and "kthread_should_stop());" in text)
            check("the enqueue refuses a node whose thread is gone",
                  "if (unlikely(!kcd->task)) {" in text)
            check("the notifier is registered and returns NOTIFY_OK",
                  "register_memory_notifier(&abk_kcompressd_memory_nb);" in text
                  and "return NOTIFY_OK;" in text)
            check("MEM_OFFLINE is gated on the node becoming memoryless",
                  "if (arg->status_change_nid < 0)" in text
                  and "case MEM_OFFLINE:" in text)
            order = [text.index("if (!current_is_kswapd())"),
                     text.index("if (!PageAnon(page))"),
                     text.index("if (!frontswap_enabled() &&"),
                     text.index("if (memcg_is_dying(page_memcg(page)))")]
            check("the memcg gate is evaluated last", order == sorted(order),
                  order)

            ctx2 = make_ctx(tmp, {b41.PAGE_IO: text})
            status2, detail2 = core._vm_kcompressd_swapout_apply(ctx2)
            check(f"{label} shape: second pass is already_present",
                  status2 == "already_present", (status2, detail2))
            check(f"{label} shape: second pass is byte-identical",
                  ctx2.read(b41.PAGE_IO) == text)

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {b41.PAGE_IO:
                             "#include <linux/sched/task.h>\n"
                             "int other(void);\n"})
        status, detail = core._vm_kcompressd_swapout_apply(ctx)
        check("an unknown shape reports blocked_by_shape",
              status == "blocked_by_shape", (status, detail))
        check("the unknown-shape tree is not written",
              ctx.pending_writes() == [])

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {})
        status, detail = core._vm_kcompressd_swapout_apply(ctx)
        check("a missing file reports blocked_by_shape",
              status == "blocked_by_shape", (status, detail))
        check("the empty tree is not written",
              ctx.pending_writes() == [])


def test_batch40_erofs_readahead():
    print("Batch 40: erofs_readahead_relaxed_gfp (readahead allocs may fail)")
    import abk_stable_core as core
    import batch40_core_erofs_readahead as b40

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "erofs_readahead_relaxed_gfp"), None)
    check("erofs_readahead_relaxed_gfp group registered", group is not None)
    if group is None:
        return
    check("the group owns exactly the five erofs files",
          group.files == [b40.COMPRESS_H, b40.DECOMPRESSOR_C,
                          b40.DECOMPRESSOR_LZMA_C, b40.ZDATA_C, b40.ZDATA_H],
          group.files)
    check("the pcluster header is fs/erofs/zdata.h",
          b40.ZDATA_H == "fs/erofs/zdata.h", b40.ZDATA_H)

    steps = b40.build_steps()
    check("the besteffort declaration lands in zdata.h, not zdata.c",
          [rel for rel, _o, new, _r in steps
           if b40.PCLUSTER_BESTEFFORT in new] == [b40.ZDATA_H],
          [rel for rel, _o, new, _r in steps if b40.PCLUSTER_BESTEFFORT in new])
    check("eight required steps",
          len(steps) == 8 and all(req for _r, _o, _n, req in steps),
          [(rel, req) for rel, _o, _n, req in steps])
    for i, (_rel, _old, new_i, _req) in enumerate(steps):
        for j in range(i + 1, len(steps)):
            check("step %d new does not contain step %d new" % (i, j),
                  steps[j][2] not in new_i)
    for rel, old, new, _req in steps:
        check(f"the {rel} new block is not already in its old block",
              new not in old)

    for i, (_rel, _old, new_i, _req) in enumerate(steps):
        check("step %d adds no ABK marker" % i,
              "ABK stable_515_backport" not in new_i)

    init = steps[6][2]
    check("the mode is read as pcl->besteffort ? NOFAIL : NOWAIT",
          b40.REQ_GFP_INIT in init
          and "GFP_KERNEL | __GFP_NOFAIL :" in init
          and "GFP_NOWAIT | __GFP_NORETRY" in init
          and init.index("GFP_KERNEL | __GFP_NOFAIL")
          < init.index("GFP_NOWAIT | __GFP_NORETRY"),
          init)
    check("the set side is `besteffort |= !fe->readahead`",
          b40.SET_MODE_LINE == "\tclt->pcl->besteffort |= !fe->readahead;",
          b40.SET_MODE_LINE)
    check("both decompressors take the flags from rq->gfp",
          "erofs_allocpage(pagepool, rq->gfp);" in steps[1][2]
          and "erofs_allocpage(pagepool, rq->gfp);" in steps[2][2])
    for i, (_rel, _old, new_i, _req) in enumerate(steps):
        check("step %d does not copy upstream's `bool ra` parameter" % i,
              "bool ra" not in new_i and "!ra" not in new_i)

    fixture = {
        b40.COMPRESS_H: (
            "struct z_erofs_decompress_req {\n"
            "\tstruct super_block *sb;\n"
            + b40._REQ_STRUCT_OLD +
            "\nstruct z_erofs_decompressor {\n\tint x;\n};\n"),
        b40.DECOMPRESSOR_C: (
            "static int z_erofs_lz4_prepare_dstpages(void)\n{\n"
            "\t\tif (top) {\n"
            "\t\t\tvictim = availables[--top];\n"
            "\t\t\tget_page(victim);\n"
            "\t\t} else {\n"
            + b40._LZ4_ALLOC_OLD +
            "\t\t}\n"
            "\t\trq->out[i] = victim;\n"
            "\t}\n\treturn 0;\n}\n"),
        b40.DECOMPRESSOR_LZMA_C: (
            "int z_erofs_lzma_decompress(struct z_erofs_decompress_req *rq,\n"
            "\t\t\t    struct page **pagepool)\n{\n"
            "\tfor (ni = 0, no = -1;;) {\n"
            "\t\tfor (j = ni + 1; j < nrpages_in; ++j) {\n"
            "\t\t\tstruct page *tmppage;\n"
            "\n\t\t\tif (rq->out[no] != rq->in[j])\n"
            "\t\t\t\tcontinue;\n"
            + b40._LZMA_ALLOC_OLD +
            "\t\t}\n\t}\n"
            + b40._LZMA_LABEL_OLD +
            "\t\tkunmap(rq->in[ni]);\n"
            "\treturn err;\n}\n"),
        b40.ZDATA_H: (
            "struct z_erofs_pcluster {\n"
            "\tunsigned int length;\n"
            "\tunsigned short pclusterpages;\n"
            + b40._PCLUSTER_OLD +
            "\tstruct page *compressed_pages[];\n};\n"),
        b40.ZDATA_C: (
            "static int z_erofs_do_read_page(void)\n{\nrestart_now:\n"
            + b40._SET_MODE_OLD +
            "\tif (should_alloc_managed_pages())\n"
            "\t\tcache_strategy = TRYALLOC;\n"
            "\treturn 0;\n}\n\n"
            "static int z_erofs_decompress_pcluster(void)\n{\n"
            "\terr = z_erofs_decompress(&(struct z_erofs_decompress_req) {\n"
            "\t\t\t\t\t.sb = sb,\n"
            "\t\t\t\t\t.inplace_io = overlapped,\n"
            + b40._REQ_INIT_OLD +
            "\tcl->nr_pages = 0;\n"
            "\tcl->vcnt = 0;\n"
            "\tWRITE_ONCE(pcl->next, Z_EROFS_PCLUSTER_NIL);\n"
            "\treturn err;\n}\n"),
    }

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, fixture)
        status, detail = group.apply_fn(ctx)
        check("all eight steps land", status == "applied", (status, detail))
        cc = ctx.read(b40.COMPRESS_H)
        check("the request struct gained gfp_t gfp",
              b40.REQ_GFP_FIELD in cc and cc.count("gfp_t gfp;") == 1)
        lz4 = ctx.read(b40.DECOMPRESSOR_C)
        lzma = ctx.read(b40.DECOMPRESSOR_LZMA_C)
        check("LZ4 allocates with rq->gfp and may fail",
              "erofs_allocpage(pagepool, rq->gfp);" in lz4
              and "return -ENOMEM;" in lz4)
        check("LZMA allocates with rq->gfp and may fail",
              "erofs_allocpage(pagepool, rq->gfp);" in lzma
              and "goto failed;" in lzma and "\nfailed:\n" in lzma)
        check("neither decompressor still demands a NOFAIL page",
              "GFP_KERNEL | __GFP_NOFAIL" not in lz4
              and "GFP_KERNEL | __GFP_NOFAIL" not in lzma)
        check("the pcluster carries the mode",
              b40.PCLUSTER_BESTEFFORT in ctx.read(b40.ZDATA_H))
        zd = ctx.read(b40.ZDATA_C)
        check("the collector records the mode", b40.SET_MODE_LINE in zd)
        check("the decompressor selects the flags per mode",
              b40.REQ_GFP_INIT in zd
              and "GFP_NOWAIT | __GFP_NORETRY" in zd
              and zd.count("GFP_NOWAIT | __GFP_NORETRY") == 1)
        check("the pcluster is put back to pristine",
              b40.RESET_LINE in zd)

        status2, detail2 = group.apply_fn(ctx)
        check("second pass: already_present",
              status2 == "already_present", (status2, detail2))
        check("second pass: the tree is byte-identical",
              ctx.read(b40.ZDATA_C) == zd)

        post = make_ctx(tmp + "/post", {
            b40.COMPRESS_H: ("\tunsigned int alg;\n"
                             "\tbool inplace_io, partial_decoding, fillgaps;\n"
                             "};\n")})
        status3, _d3 = group.apply_fn(post)
        check("degrades on a post-v6.9 request struct",
              status3 == "blocked_by_shape", status3)
        check("the degraded tree is not written",
              "gfp_t gfp;" not in post.read(b40.COMPRESS_H))

        bare = make_ctx(tmp + "/bare40", {b40.COMPRESS_H: "static int x;\n"})
        status4, _d4 = group.apply_fn(bare)
        check("degrades on an unknown shape", status4 == "blocked_by_shape")
        check("the unknown-shape tree is not written",
              bare.read(b40.COMPRESS_H) == "static int x;\n")


def test_batch46_erofs_readmore_eof():
    print("Batch 46: erofs_readmore_past_eof (readmore bounded by i_size)")
    import abk_stable_core as core
    import batch46_core_erofs_readmore_eof as b46

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "erofs_readmore_past_eof"), None)
    check("erofs_readmore_past_eof group registered", group is not None)
    if group is None:
        return
    check("the group owns exactly fs/erofs/zdata.c",
          group.files == [b46.ZDATA_C], group.files)

    steps = b46.build_steps()
    check("one required step",
          len(steps) == 1 and all(req for _r, _o, _n, req in steps),
          [(rel, req) for rel, _o, _n, req in steps])
    check("the step lands in fs/erofs/zdata.c", steps[0][0] == b46.ZDATA_C)

    for rel, old, new, _req in steps:
        check(f"the {rel} new block is not already in its old block",
              new not in old)
    for i, (_rel, _old, new_i, _req) in enumerate(steps):
        check("step %d adds no ABK marker" % i,
              "ABK stable_515_backport" not in new_i)
    check("the anchor does not reach the backmost branch",
          "backmost" not in steps[0][1] and "backmost" not in steps[0][2])
    check("the anchor starts at the mapped-extent line",
          steps[0][1].startswith("\tcur = map->m_la + map->m_llen - 1;\n"))
    old_lines = steps[0][1].split("\n")
    new_lines = steps[0][2].split("\n")
    check("the anchor is three lines", len(old_lines) == 4, old_lines)
    check("the replacement is the same three lines",
          len(new_lines) == 4, new_lines)
    differing = [i for i, (a, b) in enumerate(zip(old_lines, new_lines))
                 if a != b]
    check("exactly one line changed", differing == [1], differing)
    check("the changed line is the while condition",
          old_lines[1] == "\twhile (cur >= end) {"
          and new_lines[1] == "\twhile ((cur >= end) && (cur < i_size_read(inode))) {",
          (old_lines[1], new_lines[1]))

    check("the EOF test is the second conjunct",
          b46.LOOP_CONDITION_NEW
          == "\twhile ((cur >= end) && (cur < i_size_read(inode))) {\n",
          b46.LOOP_CONDITION_NEW)
    check("the extent test is still there",
          "cur >= end" in b46.LOOP_CONDITION_NEW)
    check("the EOF test names i_size_read(inode)",
          b46.EOF_GUARD == "cur < i_size_read(inode)", b46.EOF_GUARD)

    fixture = {
        b46.ZDATA_C: (
            "static void z_erofs_pcluster_readmore(void)\n{\n"
            "\tstruct inode *inode = f->inode;\n"
            "\n"
            "\tif (backmost) {\n"
            "\t\tmap->m_la = end;\n"
            "\t\tend = round_up(end, PAGE_SIZE);\n"
            "\t} else {\n"
            "\t\tend = round_up(map->m_la, PAGE_SIZE);\n"
            "\n"
            "\t\tif (!map->m_llen)\n"
            "\t\t\treturn;\n"
            "\t}\n"
            + b46._READMORE_OLD +
            "\t\tstruct page *page;\n"
            "\t\terr = z_erofs_do_read_page(f, page, pagepool);\n"
            "skip:\n"
            "\t\tcur = (index << PAGE_SHIFT) - 1;\n"
            "\t}\n"
            "}\n"),
    }

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, fixture)
        status, detail = group.apply_fn(ctx)
        check("the step lands", status == "applied", (status, detail))
        zd = ctx.read(b46.ZDATA_C)
        check("the loop is bounded by the inode size",
              b46.EOF_GUARD in zd and zd.count(b46.EOF_GUARD) == 1)
        check("the loop still honours its extent bound", "cur >= end" in zd)
        check("the anchor line itself is unchanged",
              "\tcur = map->m_la + map->m_llen - 1;\n" in zd)

        status2, detail2 = group.apply_fn(ctx)
        check("second pass: already_present",
              status2 == "already_present", (status2, detail2))
        check("second pass: the tree is byte-identical",
              ctx.read(b46.ZDATA_C) == zd)

        upstream = make_ctx(tmp + "/upstream", {
            b46.ZDATA_C: (
                "static void z_erofs_pcluster_readmore("
                "struct z_erofs_decompress_frontend *f,\n"
                "\t\tstruct readahead_control *rac, bool backmost)\n{\n"
                "\tstruct inode *inode = f->inode;\n"
                "\tcur = map->m_la + map->m_llen - 1;\n"
                + b46._READMORE_NEW +
                "\t\tstruct page *page;\n"
                "\t}\n"
                "}\n")})
        status3, _d3 = group.apply_fn(upstream)
        check("a tree already carrying the commit reports already_present",
              status3 == "already_present", status3)
        check("that tree is not rewritten",
              upstream.read(b46.ZDATA_C).count(b46.EOF_GUARD) == 1)

        bare = make_ctx(tmp + "/bare46", {b46.ZDATA_C: "static int x;\n"})
        status4, _d4 = group.apply_fn(bare)
        check("degrades on a missing anchor", status4 == "blocked_by_shape",
              status4)
        check("the missing-anchor tree is not written",
              bare.read(b46.ZDATA_C) == "static int x;\n")


def test_sched_tunables_module():
    print("sailboat addon 2 (schedutil governor + smart-freq band)")
    repo = Path(__file__).resolve().parent.parent
    module_dir = repo / "ksu" / "sailboat_addon_2"
    check("sched module directory exists", module_dir.is_dir(), module_dir)
    if not module_dir.is_dir():
        return

    required = ("module.prop", "common.sh", "service.sh", "post-fs-data.sh",
                "action.sh", "tunables.conf", "embed.conf", "README.md")
    for name in required:
        check(f"module ships {name}", (module_dir / name).is_file())

    for name in required:
        blob = (module_dir / name).read_bytes()
        check(f"{name} is LF, not CRLF", b"\r\n" not in blob,
              blob.count(b"\r\n"))
    shipped = sorted(path for path in module_dir.rglob("*")
                     if path.is_file() and path.suffix != ".log"
                     and "state" not in path.parts and "run" not in path.parts)
    check("the module ships exactly the files this list knows about",
          {path.name for path in shipped} == set(required),
          sorted({path.name for path in shipped} ^ set(required)))

    props = {}
    for line in (module_dir / "module.prop").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            props[key.strip()] = value.strip()
    check("module id matches the directory KernelSU installs into",
          props.get("id") == module_dir.name == "sailboat_addon_2",
          props.get("id"))
    check("the display name is sailboat addon 2",
          props.get("name") == "sailboat 附加模块（二）", props.get("name"))
    check("the id is not the display name",
          props.get("id") != props.get("name") and "sailboat_addon_2" in (props.get("id") or ""))
    check("module versionCode is a positive integer",
          props.get("versionCode", "").isdigit() and int(props["versionCode"]) > 0)

    common_sh = (module_dir / "common.sh").read_text(encoding="utf-8")
    tunables_conf = (module_dir / "tunables.conf").read_text(encoding="utf-8")
    action_source = (module_dir / "action.sh").read_text(encoding="utf-8")
    service_source = (module_dir / "service.sh").read_text(encoding="utf-8")
    post_fs_data = (module_dir / "post-fs-data.sh").read_text(encoding="utf-8")
    readme_source = (module_dir / "README.md").read_text(encoding="utf-8")

    one = repo / "ksu" / "abk_runtime_tunables"
    one_common = (one / "common.sh").read_text(encoding="utf-8")
    one_tunables = (one / "tunables.conf").read_text(encoding="utf-8")
    one_action = (one / "action.sh").read_text(encoding="utf-8")
    one_props = {}
    for line in (one / "module.prop").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            one_props[key.strip()] = value.strip()
    check("addon 1 was renamed to sailboat addon 1",
          one_props.get("name") == "sailboat 附加模块（一）", one_props.get("name"))
    check("addon 1 kept its id, so an installed copy upgrades instead of being orphaned",
          one_props.get("id") == "abk_runtime_tunables", one_props.get("id"))
    for token in ("abk_gov_wanted", "abk_gov_set_one", "abk_gov_enforce",
                  "abk_gov_supervisor_main", "abk_apply_sched_knobs",
                  "abk_report_dvfs_state", "ABK_FAS_NODE"):
        check(f"addon 1 no longer defines {token}", token not in one_common,
              [l for l in one_common.splitlines() if token in l][:2])
    _keys_list = common_sh.split("cat <<'EOF'", 1)[1].split("EOF", 1)[0]
    sched_keys = [k for k in _keys_list.split() if k.startswith("sched.")]
    check("this module owns exactly the eleven scheduling keys",
          len(sched_keys) == 11, sched_keys)
    check("addon 1's tunables.conf carries none of them",
          all(f"{k}=" not in one_tunables for k in sched_keys),
          [k for k in sched_keys if f"{k}=" in one_tunables])
    check("addon 1's linter does not know them either, so it cannot drive them",
          all(k not in one_common for k in sched_keys))
    check("addon 1's action.sh points here instead of reading those nodes",
          "ksu/sailboat_addon_2" in one_action
          and "abk_sc_cap_pct" not in one_action)
    bundler = (repo / "scripts" / "ak3_bundle_ksu_module.py").read_text(encoding="utf-8")
    check("the AK3 installer installs every bundled module, not just one",
          '*' in bundler and '*.zip' in bundler,
          [l.strip() for l in bundler.splitlines() if ".zip" in l][:3])

    dvfs = common_sh[common_sh.index("abk_report_dvfs_state() {"):]
    sched_knobs = common_sh[common_sh.index("abk_apply_sched_knobs() {"):
                           common_sh.index("abk_gov_wanted() {")]
    sched_knobs_code = "\n".join(l for l in sched_knobs.splitlines()
                                if not l.lstrip().startswith("#"))
    check("the DVFS report logs the capacity the placer sees",
          "cap_view=" in dvfs and "_rs_capv" in dvfs)
    check("the DVFS report warns when the super core is no bigger than a weaker cluster",
          "is capped to" in dvfs and "abk_warn" in dvfs)
    check("the smart-freq floor warns per payload generation, not per governor name",
          "_rs_foreign" in dvfs and "_rs_pinned" in dvfs
          and "pre-10-5 payload" in dvfs and "abk_sf_boosting node" in dvfs)
    check("the DVFS capacity math goes through abk_mul_div, not shell arithmetic",
          'abk_mul_div "$_rs_arch" "$_rs_max" "$_rs_imax"' in dvfs)

    _loop = re.search(r"(?ms)^  for node in (.*?); do", action_source)
    check("action.sh reports the payload nodes from one loop",
          _loop is not None)
    _node_names = []
    if _loop:
        _node_names = re.sub(r"\\\s+", " ", _loop.group(1)).split()
    for node in ("abk_sf_enable", "abk_sf_floor_pct", "abk_sf_sustained_ms",
                 "abk_sf_exit_ms", "abk_sc_enable", "abk_sc_cap_pct",
                 "abk_sc_hold_ms", "abk_sc_release_pct", "abk_sc_release_ms"):
        check(f"action.sh status reports the node {node}",
              node in _node_names, _node_names)
    for node in ("abk_sf_boosting", "abk_sc_capped", "abk_sc_boosting"):
        check(f"action.sh status reports the read-only node {node}",
              f'abk_show_or_absent "{node}"' in action_source)

    check("the cap's boot line carries both of its nodes",
          "_rs_sc_enable=" in dvfs and "_rs_sc_capped=" in dvfs)
    check("abk_capped is read from the cap payload, not the floor's",
          "_rs_sc_capped=\"$(abk_read_flat \"$_rs_sf/abk_sc_capped\")\"" in dvfs)
    check("every sched.abk_sc knob is a known key",
          all(f"sched.abk_sc_{k}" in common_sh for k in
              ("enable", "cap_pct", "hold_ms", "release_pct", "release_ms")))
    check("abk_apply_sched_knobs drives the cap's five knobs",
          all(f'abk_write "$_sk_dir/abk_sc_{k}"' in sched_knobs_code for k in
              ("enable", "cap_pct", "hold_ms", "release_pct", "release_ms")),
          [l for l in sched_knobs_code.splitlines() if "abk_sc_" in l])
    tunables_code = "\n".join(l for l in tunables_conf.splitlines()
                              if not l.lstrip().startswith("#"))
    check("no companion knob for the diagnostic-only entry_pct",
          "sched.abk_sc_entry_pct" not in tunables_code
          and "abk_sc_entry_pct" not in sched_knobs_code)
    sched_block = tunables_code
    sf_floor = int(re.search(r"(?m)^sched\.abk_sf_floor_pct=(\d+)$",
                             sched_block).group(1))
    sc_cap = int(re.search(r"(?m)^sched\.abk_sc_cap_pct=(\d+)$",
                           sched_block).group(1))
    check("the module arms both the floor and the cap",
          re.search(r"(?m)^sched\.abk_sf_enable=1$", sched_block) is not None
          and re.search(r"(?m)^sched\.abk_sc_enable=1$", sched_block) is not None,
          [l for l in sched_block.splitlines() if l.startswith("sched.")])
    check("the cap stays above the floor (a crossed band is a frequency lock)",
          sc_cap - sf_floor >= 5 and sc_cap <= 100,
          (sf_floor, sc_cap))
    check("... and the band's inequality is stated in the README",
          "floor_pct=85" in tunables_conf and "cap_pct=90" in tunables_conf
          and "Batch 10-4c ratchet" in readme_source,
          [l for l in tunables_conf.splitlines()
           if "floor_pct" in l or "cap_pct" in l])
    dvfs_code = "\n".join(l for l in dvfs.splitlines()
                          if not l.lstrip().startswith("#"))
    check("the DVFS report reads plain sysfs paths",
          "/devices/system/cpu/cpufreq/policy" in dvfs_code
          and "/devices/system/cpu/cpu" in dvfs_code)

    gov_block = common_sh[common_sh.index("abk_gov_wanted() {"):
                          common_sh.index("abk_read_flat() {")]
    gov_code = "\n".join(l for l in gov_block.splitlines()
                         if not l.lstrip().startswith("#"))
    check("the governor is read through the tunables, with the CR stripped",
          "abk_cfg sched.abk_governor ''" in gov_code
          and "tr -d '\\r'" in gov_code,
          [l for l in gov_code.splitlines() if "abk_gov_wanted" in l])
    check("the re-assert interval is clamped to a sane range",
          'abk_clamp_uint "$_gi" 5 3600' in gov_code)
    check("the policy list is a glob, so post-fs-data finds nothing safely",
          'policy*' in gov_code and "[ -d \"$_gp_d\" ]" in gov_code)
    check("the chmod is reached only after a refused write",
          'if ! echo "$_gs_want" > "$_gs_node" 2>/dev/null; then' in gov_code
          and 'chmod 0644 "$_gs_node"' in gov_code,
          [l for l in gov_code.splitlines() if "chmod" in l or "_gs_node\" 2>" in l])
    check("the write is verified by reading the node back",
          gov_code.count('"$_gs_node"') >= 2
          and "verify failed" in gov_code)
    set_one = gov_code[gov_code.index("abk_gov_set_one() {"):
                       gov_code.index("abk_gov_enforce() {")]
    check("the per-policy setter logs nothing (its stdout is captured)",
          "abk_log" not in set_one and "abk_warn" not in set_one,
          [l for l in set_one.splitlines() if "abk_" in l])
    check("the enforcer reports failure through its exit status",
          '[ "$_ge_bad" -eq 0 ] || return 1' in gov_code)
    check("a governor the policy does not offer is refused before any write",
          "not offered by" in gov_code
          and "scaling_available_governors" in gov_code)
    check("the supervisor stops itself when every write is refused",
          "three rounds running" in gov_code
          and "_gm_streak" in gov_code)
    check("the supervisor logs only on change, so a stable device is silent",
          'if [ "$_gm_line" != "$_gm_prev" ]; then' in gov_code)
    check("an empty key spawns no governor supervisor at all",
          "[ -n \"$(abk_gov_wanted)\" ]" in service_source
          and "leaving the ROM's own governor in charge" in service_source)
    for key in ("sched.abk_governor", "sched.abk_governor_interval_sec"):
        check(f"{key} is a known key", key in common_sh)
        check(f"{key} is assigned in the shipped tunables.conf",
              re.search(r"(?m)^" + re.escape(key) + r"=", tunables_conf) is not None)
    check("the shipped governor is schedutil, not the ROM's walt default",
          re.search(r"(?m)^sched\.abk_governor=schedutil$", tunables_conf)
          is not None,
          [l for l in tunables_code.splitlines() if l.startswith("sched.")])
    check("the DVFS report reads the governor but never writes it",
          "abk_gov_state_line" in dvfs and "abk_gov_enforce" not in dvfs)
    _pfd_code = "\n".join(l for l in post_fs_data.splitlines()
                          if not l.lstrip().startswith("#"))
    early_order = [_pfd_code.index(t) for t in
                   ("abk_gov_enforce", "abk_apply_sched_knobs")]
    check("the governor is established before the band is written",
          early_order == sorted(early_order), early_order)
    check("the boot pass logs what the enforce actually did",
          'abk_log "gov: $_ae_gov"' in post_fs_data)
    check("service.sh runs the governor supervisor and gates it on the key",
          "--supervise-gov" in service_source
          and "abk_gov_supervisor_main" in service_source)
    # Process naming: ps NAME is argv[0], so the governor supervisor is exec'd
    # through a long-name link; comm is a 15-byte field and takes the short alias.
    _gov_fn = common_sh[common_sh.index("abk_supervisor_name() {"):
                        common_sh.index("abk_set_comm() {")]
    _gov_table = {_m.group(1): (_m.group(2), _m.group(3))
                  for _m in re.finditer(
                      r"(?m)^\s*(\S+)\) _sn_long=(\S+); _sn_comm=(\S+) ;;", _gov_fn)}
    check("the governor maps to exactly one long name and one comm",
          _gov_table == {"gov": ("Sailboat_gov_supervisor", "Sail_gov")}, _gov_table)
    check("the comm fits the 15-byte comm field",
          all(len(_c) <= 15 for _l, _c in _gov_table.values()), _gov_table)
    check("the README records the governor's long name and comm",
          "Sailboat_gov_supervisor" in readme_source and "Sail_gov" in readme_source)
    check("comm is written without the trailing newline echo would add",
          'echo -n "$1" > "/proc/$$/comm" 2>/dev/null' in common_sh)
    _gov_line = next((ln for ln in service_source.splitlines()
                      if ln.strip().startswith("--supervise-gov)")), "")
    check("--supervise-gov sets comm before the supervisor body runs",
          'abk_set_comm "$(abk_supervisor_name gov comm)"' in _gov_line
          and _gov_line.index("abk_set_comm") < _gov_line.index("abk_gov_supervisor_main"),
          _gov_line)
    check("the spawn execs the long-name link, not a bare sh",
          'setsid "$_sp_link" "$MODDIR/service.sh" "$_sp_arg"' in service_source)
    check("the governor link is created once and then reused",
          'readlink "$_sp_link"' in service_source
          and service_source.index('readlink "$_sp_link"')
          < service_source.index('ln -s "$ABK_SHELL" "$_sp_link"'))
    _gov_spawn = service_source[service_source.index("abk_spawn() {"):
                                service_source.index('abk_log "service: kernel')]
    _gov_bare = _gov_spawn[_gov_spawn.index("  else\n    setsid sh"):
                           _gov_spawn.index("  fi\n  _sp_n=0")]
    check("a link that cannot be created falls back to the old launch with a WARN",
          "falling back to sh" in _gov_spawn
          and _gov_spawn.index('_sp_long="') < _gov_spawn.index("no long-name link")
          < _gov_spawn.index('if [ -n "$_sp_link" ]'))
    check("an unknown supervisor name falls back to sh without a WARN",
          "abk_warn" not in _gov_bare, _gov_bare)
    _gov_ro = re.compile(r"/(?:vendor|odm|product)\b|(?<!/devices)/system\b")
    _gov_refs = []
    for _script in sorted(module_dir.glob("*.sh")):
        _body = _script.read_text(encoding="utf-8")
        _code = "\n".join(ln.split("#", 1)[0] for ln in _body.splitlines())
        _gov_refs += [ln.strip() for ln in _body.splitlines()
                      if _gov_ro.search(ln)
                      and not ln.lstrip().startswith("#")]
        _code = "\n".join(ln for ln in _code.splitlines() if 'ABK_SHELL="' not in ln)
        _hit = _gov_ro.search(_code)
        check(f"addon 2's {_script.name} never touches a read-only partition",
              _hit is None, _hit.group(0) if _hit else "")
    check("addon 2's only read-only path is the shell default",
          _gov_refs == ['ABK_SHELL="${ABK_SHELL:-/system/bin/sh}"'], _gov_refs)
    check("common.sh's ABK_VERSION tracks module.prop",
          f'ABK_VERSION="{props.get("version")}"' in common_sh,
          (props.get("version"),
           [ln for ln in common_sh.splitlines() if ln.startswith("ABK_VERSION=")]))
    check("action.sh can apply the governor now and print the verdict",
          "governor)" in action_source
          and '"$(abk_gov_enforce)"' in action_source)
    check("status shows the wanted governor, the real one, and the supervisor",
          "abk_gov_state_line" in action_source
          and "abk_supervisor_state gov" in action_source)
    check("the README states the mechanism, the revert, and the unknown trigger",
          "chmod 0644" in readme_source
          and "sched.abk_governor=" in readme_source
          and "event-driven" in readme_source,
          "README must carry the chmod route, how to revert, and that the"
          " vendor's revert has no identified trigger")
    check("the README records why cap_pct is 90 and not 95",
          "92-94%" in readme_source and "never once run" in readme_source)
    check("the tunables ship the band and the README explains the measurement",
          re.search(r"(?m)^sched\.abk_sf_floor_pct=\d+$", tunables_conf) is not None
          and re.search(r"(?m)^sched\.abk_sc_cap_pct=\d+$", tunables_conf) is not None
          and "92-94%" in readme_source)


def test_batch72_kcompressd_swapcache_drop():
    print("Batch 72: kcompressd_swapcache_drop (same-pass swap cache drop)")
    import abk_stable_core as core
    import batch72_core_kcompressd_swapcache_drop as b72

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "kcompressd_swapcache_drop"), None)
    check("kcompressd_swapcache_drop group registered", group is not None)
    if group is None:
        return
    check("the group owns exactly mm/vmscan.c",
          group.files == [b72.VMSAN], group.files)

    keys = [g.key for g in core.PATCH_GROUPS]
    check("registered after kcompressd_writeback_pin",
          keys.index("kcompressd_swapcache_drop")
          > keys.index("kcompressd_writeback_pin"),
          keys[keys.index("kcompressd_swapcache_drop") - 1:
               keys.index("kcompressd_swapcache_drop") + 1])

    steps = b72.build_steps()
    check("three required steps, all in mm/vmscan.c",
          [(rel, req) for rel, _o, _n, req in steps]
          == [(b72.VMSAN, True)] * 3,
          [(rel, req) for rel, _o, _n, req in steps])
    for i, (_rel, old, new, _req) in enumerate(steps):
        check(f"step {i}'s replacement is not a prefix of its own anchor "
              "(trap 1)", not old.startswith(new))
        if i != 2:
            check(f"step {i} adds no wait_on_page_writeback",
                  "wait_on_page_writeback" not in new)

    def synthetic_vmscan():
        return (
            "static unsigned int shrink_page_list(struct list_head *page_list)\n"
            "{\n"
            + b72._DECL_OLD +
            "\tunsigned int nr_reclaimed = 0;\n"
            "\tbool do_demote_pass;\n"
            "\n"
            "retry:\n"
            "\twhile (!list_empty(page_list)) {\n"
            "\t\tstruct page *page;\n"
            "\n"
            "\t\tpage = lru_to_page(page_list);\n"
            "\t\tlist_del(&page->lru);\n"
            "\t\tswitch (pageout(page, mapping)) {\n"
            "\t\t\tcase PAGE_KEEP:\n"
            "\t\t\t\tgoto keep_locked;\n"
            + b72._PARK_OLD +
            "\t\t\t\tif (!trylock_page(page))\n"
            "\t\t\t\t\tgoto keep;\n"
            "\t\t\tcase PAGE_CLEAN:\n"
            "\t\t\t\t;\n"
            "\t\t}\n"
            "keep_locked:\n"
            "\t\tunlock_page(page);\n"
            "keep:\n"
            "\t\tlist_add(&page->lru, &ret_pages);\n"
            "\t}\n"
            + b72._RETRY_OLD +
            "\tnr_reclaimed += demote_page_list(&demote_pages, pgdat);\n"
            "\tif (!list_empty(&demote_pages)) {\n"
            "\t\tlist_splice_init(&demote_pages, page_list);\n"
            "\t\tdo_demote_pass = false;\n"
            "\t\tgoto retry;\n"
            "\t}\n"
            "\treturn nr_reclaimed;\n"
            "}\n"
        )

    io_pinned = ("int swap_writepage(struct page *page)\n{\n"
                 "\t/* sailboat_kcompressd_wb_pin: marker */\n"
                 "\tset_page_writeback(page);\n"
                 "\treturn 0;\n}\n")

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {b72.VMSAN: synthetic_vmscan(),
                             b72.PAGE_IO: "int swap_writepage(void)\n"})
        status, detail = b72._kcompressd_swapcache_drop_apply(ctx)
        check("a tree without Batch 71's pin reports blocked_by_shape",
              status == "blocked_by_shape", (status, detail))
        check("the refusal names the missing pin",
              b72.WB_PIN_MARKER in detail, detail)
        check("the refused tree is not written", ctx.pending_writes() == [])

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {b72.VMSAN: "int shrink_page_list(void) {}\n",
                             b72.PAGE_IO: io_pinned})
        status, detail = b72._kcompressd_swapcache_drop_apply(ctx)
        check("an unknown reclaim loop reports blocked_by_shape",
              status == "blocked_by_shape", (status, detail))
        check("the unknown shape is not written", ctx.pending_writes() == [])

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {})
        status, detail = b72._kcompressd_swapcache_drop_apply(ctx)
        check("a missing file reports blocked_by_shape",
              status == "blocked_by_shape", (status, detail))
        check("the empty tree is not written", ctx.pending_writes() == [])

    with tempfile.TemporaryDirectory() as tmp:
        pristine = synthetic_vmscan()
        ctx = make_ctx(tmp, {b72.VMSAN: pristine, b72.PAGE_IO: io_pinned})
        status, detail = b72._kcompressd_swapcache_drop_apply(ctx)
        check("the group applies on the pristine 5.15 shape",
              status == "applied", (status, detail))
        text = ctx.read(b72.VMSAN)
        check("only mm/vmscan.c is written",
              ctx.pending_writes() == [b72.VMSAN], ctx.pending_writes())

        check("wb_retry_pages is declared after demote_pages",
              "\tLIST_HEAD(demote_pages);\n" in text
              and text.index("\tLIST_HEAD(demote_pages);\n")
              < text.index("\tLIST_HEAD(wb_retry_pages);\n"))
        check("the one-shot latch is declared with the lists",
              b72.DECL_FLAG in text)
        check("the declarations still precede the function's first statement",
              text.index(b72.DECL_LIST) < text.index("\tbool do_demote_pass;"))

        park = text[text.index("\t\t\tcase PAGE_SUCCESS:"):
                    text.index("\t\t\tcase PAGE_CLEAN:")]
        check("the parking block is inside the CONFIG_SWAP gate",
              park.index("#ifdef CONFIG_SWAP") < park.index("page_swap_info(page)")
              < park.index("#endif"), "page_swap_info outside its gate")
        check("the parking block keeps the pristine goto keep as the "
              "#else behaviour", "#endif\n\t\t\t\t\tgoto keep;\n" in park)
        check("the parked page continues the loop on the retry list",
              b72.PARK in park and "\t\t\t\t\t\tcontinue;\n" in park)
        check("PageSwapCache is tested before page_swap_info()",
              park.index("PageSwapCache(page)")
              < park.index("page_swap_info(page)"))
        check("the SWP_SYNCHRONOUS_IO filter is present",
              "SWP_SYNCHRONOUS_IO" in park)
        check("the latch guards the parking",
              park.index("!wb_retry_done") < park.index(b72.PARK))
        check("the PageDirty guard stays after the parking block",
              park.index("goto keep;\n\t\t\t\t}") < park.index("PageDirty(page)"))
        check("the parking adds no wait",
              "wait_on_page_writeback" not in park)

        tail = text[text.index("\t/* 'page_list' is always empty here */"):]
        check("the retry block is anchored after the empty-page_list comment",
              tail.index("sailboat_kcompressd_swapcache_drop")
              < tail.index("wb_retry_done = true;"))
        check("the latch is set before the re-scan",
              tail.index(b72.RETRY_LATCH) < tail.index(b72.SPLICE))
        check("still-writeback pages wait for the deferred write",
              b72.WAIT_PIN in tail
              and tail.index("list_for_each_entry_safe(page, next, "
                             "&wb_retry_pages, lru)")
              < tail.index(b72.WAIT_PIN))
        check("the wait is guarded by PageWriteback",
              "if (!PageWriteback(page))\n\t\t\t\tcontinue;" in tail)
        check("the drain context falls back to the ret_pages pre-filter",
              b72.DRAIN_GUARD in tail and "PF_SWAPWRITE" in tail
              and "list_move(&page->lru, &ret_pages);" in tail)
        check("the remaining pages are spliced onto page_list",
              b72.SPLICE in tail)
        check("do_demote_pass is cleared before the retry",
              tail.index(b72.SPLICE)
              < tail.index("\t\t\tdo_demote_pass = false;")
              < tail.index("\t\t\tgoto retry;"))
        check("the retry precedes the demotion retry",
              tail.index("\t\t\tgoto retry;")
              < tail.index("list_splice_init(&demote_pages, page_list);"))
        check("both halves are bounded by the one-shot latch",
              tail.count("!wb_retry_done && !list_empty(&wb_retry_pages)") == 1
              and tail.count("wb_retry_done = true;") == 1)
        check("the group marker rides on all three edits",
              text.count(b72.MARKER) == 3, text.count(b72.MARKER))
        check("the whole file adds exactly one wait_on_page_writeback",
              text.count("wait_on_page_writeback")
              == pristine.count("wait_on_page_writeback") + 1)

        ctx2 = make_ctx(tmp + "-b", {b72.VMSAN: text, b72.PAGE_IO: io_pinned})
        status2, detail2 = b72._kcompressd_swapcache_drop_apply(ctx2)
        check("second pass is already_present", status2 == "already_present",
              (status2, detail2))
        check("second pass is byte-identical", ctx2.read(b72.VMSAN) == text)
        check("second pass writes nothing", ctx2.pending_writes() == [])
        st = next(g for g in core.PATCH_GROUPS
                  if g.key == "kcompressd_swapcache_drop").run(ctx2)["status"]
        check("the registered group is idempotent", st == "already_present", st)


B73_READAHEAD_FIXTURE = (
    "#include <linux/kernel.h>\n"
    "#include <linux/fadvise.h>\n"
    "#include <linux/sched/mm.h>\n"
    "#include <trace/hooks/mm.h>\n"
    "\n"
    '#include "internal.h"\n'
    "\n"
    "void abk_fixture(void) { }\n"
    "EXPORT_SYMBOL(readahead_expand);\n"
)

B73_SWAP_STATE_FIXTURE = (
    "static bool enable_vma_readahead __read_mostly = true;\n"
    "\n"
    "static unsigned int __swapin_nr_pages(unsigned long prev_offset,\n"
    "\t\t\t\t      int prev_win)\n"
    "{\n"
    "\tunsigned int pages, last_ra;\n"
    "\n"
    "\tpages = 2;\n"
    "\t/* Don't shrink readahead too fast */\n"
    "\tlast_ra = prev_win / 2;\n"
    "\tif (pages < last_ra)\n"
    "\t\tpages = last_ra;\n"
    "\n"
    "\treturn pages;\n"
    "}\n"
    "\n"
    "static unsigned long swapin_nr_pages(unsigned long offset)\n"
    "{\n"
    "\tunsigned int pages;\n"
    "\tstatic atomic_t last_readahead_pages;\n"
    "\n"
    "\tpages = 2;\n"
    "\tatomic_set(&last_readahead_pages, pages);\n"
    "\n"
    "\treturn pages;\n"
    "}\n"
)

B73_INTERNAL_FIXTURE = (
    "extern int isolate_lru_page(struct page *page);\n"
    "extern void putback_lru_page(struct page *page);\n"
)


def test_batch73_swapin_readahead_window():
    print("Batch 73: swapin_readahead_window (boost/shrink/vma_ra)")
    import abk_stable_core as core
    import batch73_core_swapin_window as b73

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "swapin_readahead_window"), None)
    check("swapin_readahead_window group registered", group is not None)
    if group is None:
        return
    check("the group owns exactly its three mm files",
          group.files == [b73.READAHEAD, b73.SWAP_STATE, b73.INTERNAL_H],
          group.files)

    steps = b73.build_steps()
    fixture_all = (B73_READAHEAD_FIXTURE + B73_SWAP_STATE_FIXTURE
                   + B73_INTERNAL_FIXTURE)
    check("six required steps", len(steps) == 6 and all(s[3] for s in steps))
    for i, (rel, old, new, _req) in enumerate(steps):
        check(f"step {i} ({rel}) anchor is unique in the fixtures",
              fixture_all.count(old) == 1, fixture_all.count(old))
        check(f"step {i} ({rel}) replacement is not already present (trap 1)",
              new not in fixture_all)
    for i, (_r, _o, new_i, _q) in enumerate(steps):
        for j, (_r2, old_j, _q2, _q3) in enumerate(steps):
            if i != j:
                check(f"step {i} does not pre-create step {j}'s anchor (trap 2)",
                      old_j not in new_i)

    ra = b73._RA_TAIL_NEW
    ss = b73._SS_SWAPIN_NEW
    check("defaults: boost 8 pages / shrink 1 page / background-only",
          "static unsigned int swapin_boost_pages __read_mostly = 8;" in ra
          and "static unsigned int swapin_shrink_pages __read_mostly = 1;" in ra
          and "static bool swapin_window_background_only __read_mostly = true;"
          in ra)
    check("swapin_vma_ra is a handle, not a new variable (trap 6)",
          "module_param_named(swapin_vma_ra, enable_vma_readahead, bool, 0644);"
          in ra and "swapin_vma_ra __read_mostly" not in ra)
    check("all seven knobs use the two-name form",
          ra.count("module_param_named(") == 7
          and "module_param(" not in ra.replace("module_param_named(", ""))
    check("the vma switch is de-staticized for the second handle",
          "static bool enable_vma_readahead" not in b73._SS_VMA_RA_NEW
          and "bool enable_vma_readahead __read_mostly = true;"
          in b73._SS_VMA_RA_NEW)
    check("clamp comes after the don't-shrink-too-fast floor",
          ss.index("last_ra = prev_win / 2;") < ss.index(b73.CLAMP_CALL))
    check("the policy is applied exactly once per clamp site",
          ss.count(b73.CLAMP_CALL) == 1
          and b73._SS_CLUSTER_NEW.count(b73.CLAMP_CALL) == 1)
    check("the cluster path's early one-page return is untouched",
          "if (max_pages <= 1)" not in b73._SS_CLUSTER_NEW)
    check("both helpers are declared in the swap block of mm/internal.h",
          "extern unsigned int abk_swapin_window_clamp(unsigned int pages);"
          in b73._INTERNAL_NEW
          and "extern bool abk_swapin_shrink_gate(void);" in b73._INTERNAL_NEW)
    check("the fault-path helper carries no lock/sleep/allocation",
          not any(x in ra for x in ("mutex_lock", "GFP_KERNEL", "schedule(",
                                    "wait_on_page")))
    check("the classifier reuse carries its own config gate (trap 7)",
          "#ifdef CONFIG_ABK_DYNAMIC_READAHEAD" in ra
          and "abk_dra_is_background_task()" in ra and "#else" in ra)
    check("the whole block is CONFIG_SWAP gated",
          "#ifdef CONFIG_SWAP" in ra and "#endif" in ra)

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {
            b73.READAHEAD: B73_READAHEAD_FIXTURE,
            b73.SWAP_STATE: B73_SWAP_STATE_FIXTURE,
            b73.INTERNAL_H: B73_INTERNAL_FIXTURE,
        })
        status, detail = b73._swapin_window_apply(ctx)
        check("the group applies on the 5.15 shape", status == "applied",
              (status, detail))
        text = ctx.read(b73.SWAP_STATE)
        check("both call sites got the clamp", text.count(b73.CLAMP_CALL) == 2,
              text.count(b73.CLAMP_CALL))
        check("the de-static landed",
              "bool enable_vma_readahead __read_mostly" in text)
        status2, _d2 = b73._swapin_window_apply(ctx)
        check("second pass is already_present", status2 == "already_present",
              status2)

        broken = B73_SWAP_STATE_FIXTURE.replace("last_ra = prev_win / 2;",
                                               "last_ra = 0;")
        ctx2 = make_ctx(tmp + "/2", {
            b73.READAHEAD: B73_READAHEAD_FIXTURE,
            b73.SWAP_STATE: broken,
            b73.INTERNAL_H: B73_INTERNAL_FIXTURE,
        })
        status3, detail3 = b73._swapin_window_apply(ctx2)
        check("a moved window computation blocks the group",
              status3 == "blocked_by_shape", (status3, detail3))


MEMORY_FA_FIXTURE = (
    "static vm_fault_t do_read_fault(struct vm_fault *vmf)\n"
    "{\n"
    "\tstruct vm_area_struct *vma = vmf->vma;\n"
    "\tvm_fault_t ret = 0;\n"
    "\tbool should_around = true;\n"
    "\n"
    "\ttrace_android_vh_tune_fault_around_bytes(&fault_around_bytes);\n"
    "\n"
    "\t/*\n"
    "\t * Let's call ->map_pages() first and use ->fault() as fallback\n"
    "\t * if page by the offset is not ready to be mapped (cold cache or\n"
    "\t * something).\n"
    "\t */\n"
    "\ttrace_android_vh_should_fault_around(vmf, &should_around);\n"
    "\tif (vma->vm_ops->map_pages && fault_around_bytes >> PAGE_SHIFT > 1 && should_around) {\n"
    "\t\tif (likely(!userfaultfd_minor(vmf->vma)))\n"
    "\t\t\tret = do_fault_around(vmf);\n"
    "\t}\n"
    "\treturn ret;\n"
    "}\n"
)


def test_batch74_fault_around_app_policy():
    import tempfile
    from pathlib import Path as _Path

    tmpdir = _Path(tempfile.mkdtemp(prefix="abk-b74-"))
    """Batch 74: per-uid fault-around policy after the vendor hook."""
    print("Batch 74: fault_around_app_policy (per-uid fault-around)")
    import abk_stable_core as core
    import batch74_core_fault_around_app_policy as b74

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "fault_around_app_policy"), None)
    check("fault_around_app_policy group registered", group is not None)
    if group is None:
        return
    check("the group owns exactly mm/memory.c",
          group.files == [b74.MEMORY_C], group.files)

    steps = b74.build_steps()
    check("two required steps, both in mm/memory.c",
          [rel for rel, _o, _n, req in steps] == [b74.MEMORY_C] * 2
          and all(req for _r, _o, _n, req in steps),
          [(rel, req) for rel, _o, _n, req in steps])

    for index, (_rel, old, new, _req) in enumerate(steps):
        check(f"step {index}'s anchor is unique in the pristine fixture",
              MEMORY_FA_FIXTURE.count(old) == 1, MEMORY_FA_FIXTURE.count(old))
        check(f"step {index}'s replacement is not already in the pristine "
              "fixture", new not in MEMORY_FA_FIXTURE)

    for i, (_rel, _old, new_i, _req) in enumerate(steps):
        for j, (_rel2, old_j, new_j, _req2) in enumerate(steps):
            if i == j or new_i in old_j:
                continue
            check(f"step {i} does not pre-create step {j}'s replacement",
                  new_j not in new_i)

    def_step, call_step = steps
    check("the policy block is inserted above do_read_fault(), keeping its "
          "head verbatim",
          def_step[2].endswith(def_step[1])
          and def_step[2].startswith(b74._POLICY_BLOCK), def_step[2][:80])
    hook_line, if_line = call_step[1].split("\n")[:2]
    check("the call is inserted between the hook and the if it feeds",
          call_step[2].startswith(hook_line + "\n")
          and call_step[2].endswith(if_line + "\n"))
    check("the call site adds exactly one abk_fa_adjust() call",
          call_step[2].count(b74.CALL_SITE) == 1, call_step[2])

    policy = b74._POLICY_BLOCK
    check("defaults are performance-first (enable/sys_uid on, app_only off)",
          "static bool fault_around_enable __read_mostly = true;" in policy
          and "static bool fault_around_sys_uid __read_mostly = true;" in policy
          and re.search(r"static bool fault_around_app_only __read_mostly;",
                        policy) is not None, policy[:200])
    check("the uid is read through current_uid()/from_kuid(), not a cred "
          "offset", b74.UID_READ in policy
          and "current_cred()" not in policy
          and "->cred" not in policy
          and "cred->uid" not in policy)
    check("the bitmap is 90000 bits indexed by uid - 10000",
          b74.BITMAP_DEF in policy
          and "uid - ABK_FA_APP_UID_BASE" in policy)
    check("the bitmap access is range-checked before indexing",
          "if (uid > ABK_FA_APP_UID_LAST)" in policy)
    check("writers take the mutex, the fault path is lockless",
          b74.LOCK_DEF in policy
          and "mutex_lock(&abk_fa_app_lock);" in policy
          and "test_bit(uid - ABK_FA_APP_UID_BASE" in policy)
    check("the three policy gates are present in order",
          b74.GATE_ENABLE in policy and b74.GATE_SYS_UID in policy
          and b74.GATE_APP_ONLY in policy)
    check("write 0 clears the list", b74.CLEAR_CALL in policy)
    check("the getter caps the printed list at 64 uids and guards the buffer",
          b74.READ_BOUND in policy and "if (len > PAGE_SIZE - 16)" in policy)
    check("the parameters are the four documented knobs",
          all(pin in policy for pin in (b74.PARAM_ENABLE, b74.PARAM_SYS_UID,
                                        b74.PARAM_APP_ONLY, b74.PARAM_APP)))
    check("the marker rides in the payload", b74.MARKER in policy)

    root = tmpdir / "b74"
    (root / "mm").mkdir(parents=True, exist_ok=True)
    (root / b74.MEMORY_C).write_text(MEMORY_FA_FIXTURE, encoding="utf-8")
    ctx = GraftContext(root, 220, "android13-5.15")
    status, _results, detail = apply_steps(ctx, steps)
    check("both steps land on the pristine fixture", status == "applied",
          (status, detail))
    patched = (root / b74.MEMORY_C).read_text(encoding="utf-8")
    check("the call follows the hook call",
          patched.index(b74.CALL_SITE) > patched.index(b74.HOOK_CALL))
    check("the policy block precedes its only caller",
          patched.index(b74.ADJUST_DEF) < patched.index(b74.DO_READ_FAULT_PROBE))

    result = group.run(GraftContext(root, 220, "android13-5.15"))
    check("second pass is already_present",
          result["status"] == "already_present", result["status"])
    check("second pass is byte-identical",
          (root / b74.MEMORY_C).read_text(encoding="utf-8") == patched)

    root3 = tmpdir / "b74-markerless"
    (root3 / "mm").mkdir(parents=True, exist_ok=True)
    markerless = patched.replace(b74.MARKER, "sailboat_marker_reflowed")
    (root3 / b74.MEMORY_C).write_text(markerless, encoding="utf-8")
    result = group.run(GraftContext(root3, 220, "android13-5.15"))
    check("a tree whose marker was lost but whose payload survives is "
          "blocked_by_shape", result["status"] == "blocked_by_shape",
          result["status"])
    check("the marker-loss refusal writes nothing",
          (root3 / b74.MEMORY_C).read_text(encoding="utf-8") == markerless)

    root2 = tmpdir / "b74-unknown"
    (root2 / "mm").mkdir(parents=True, exist_ok=True)
    unknown = MEMORY_FA_FIXTURE.replace(b74.HOOK_CALL + "\n", "")
    (root2 / b74.MEMORY_C).write_text(unknown, encoding="utf-8")
    result = group.run(GraftContext(root2, 220, "android13-5.15"))
    check("a tree without the hook call site is blocked_by_shape",
          result["status"] == "blocked_by_shape", result["status"])
    check("a blocked group writes nothing",
          (root2 / b74.MEMORY_C).read_text(encoding="utf-8") == unknown)


def test_port_review_regressions():
    print("port review regressions (I-G1 boost clamp order, I-P4 depth formula)")
    import batch73_core_swapin_window as b73

    blob = "\n".join(new for _rel, _old, new, _req in b73.build_steps())
    clamped = blob.find("if (boost > ABK_SWAPIN_WINDOW_MAX)")
    rounded = blob.find("roundup_pow_of_two(boost)")
    check("I-G1: the boost floor is clamped before round-up",
          clamped != -1 and rounded != -1 and clamped < rounded,
          (clamped, rounded))

    common = (Path(__file__).resolve().parent.parent
              / "ksu" / "abk_runtime_tunables" / "common.sh")
    text = common.read_text(encoding="utf-8")
    check("I-P4: depth is enqueued - swapped", "d = e - s" in text)
    check("I-P4: depth no longer subtracts the sync count",
          "e - s - y" not in text)


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
    test_mglru_is_opt_in_only()
    test_batch10_memcg_v1_reclaim()
    test_batch10_cached_freeze_reclaim()
    test_batch10_daemon_script()
    test_batch10_zram_trigger_script()
    test_runtime_tunables_module()
    test_sched_tunables_module()
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
    test_batch47_zram_recomp_best_prio_skip()
    test_batch32_zram_wb_slot_preserve()
    test_f2fs_shape_probe()
    test_kabi_slot_policy()
    test_kstack_slot_shape_selection()
    test_batch27_launch_bench()
    test_batch30_readahead_mmap_miss_race()
    test_batch31_arm64_pte_mkwrite_clean()
    test_batch33_zsmalloc_free_out_of_lock()
    test_batch37_reclaim_paths()
    test_batch35_pagecache_pt()
    test_batch40_erofs_readahead()
    test_batch46_erofs_readmore_eof()
    test_batch41_kcompressd_offload()
    test_batch42_schedutil_smart_cap()
    test_batch70_smaps_migration_guard()
    test_batch71_kcompressd_wb_pin()
    test_batch72_kcompressd_swapcache_drop()
    test_batch73_swapin_readahead_window()
    test_batch74_fault_around_app_policy()
    test_port_review_regressions()
    test_fetch_decode_roundtrip()
    test_workflow_expressions_are_ascii()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} of {len(CHECKS_RUN)} check(s): {FAILURES}")
        sys.exit(1)
    print(f"all checks passed ({len(CHECKS_RUN)} checks)")


def test_batch42_schedutil_smart_cap():
    print("Batch 42 schedutil_smart_cap (freq_cap[] -> min(freq, cap))")
    import abk_stable_perf as perf
    import batch10_perf_sched_policy as b10
    import batch42_perf_schedutil_smart_cap as b42

    for key in ("schedutil_smart_policy", "schedutil_smart_cap"):
        check(f"{key} group registered",
              any(g.key == key for g in perf.PATCH_GROUPS))
    keys = [g.key for g in perf.PATCH_GROUPS]
    check("the cap is registered after the floor (registration order is "
          "execution order)",
          keys.index("schedutil_smart_cap") > keys.index("schedutil_smart_policy"),
          keys[keys.index("schedutil_smart_policy") - 1:
               keys.index("schedutil_smart_cap") + 1])

    rel = "kernel/sched/cpufreq_schedutil.c"
    pristine = b42._INC_OLD + "void governor(void);\n" + b42._TAIL_OLD
    floor_only = (b10._INC_NEW + "void governor(void);\n" + b10._TAIL_OLD
                  + b10._POLICY_V2)

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {rel: floor_only})
        status, detail = perf._sched_smart_cap_apply(ctx)
        check("the cap adds no duplicate include on a floor-shaped tree",
              status == "applied", (status, detail))
        text = ctx.read(rel)
        for found, want, name in [
            ("#include <trace/hooks/cpufreq.h>\n"
             "#include <linux/math.h>\n"
             "#include <linux/moduleparam.h>\n"
             "#include <linux/string.h>\n", 1,
             "the shared header block appears once"),
            ("#include <trace/hooks/sched.h>", 1,
             "the sched hook header appears once"),
        ]:
            check(name, text.count(found) == want, text.count(found))

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {rel: pristine})
        st_p, d_p = perf._sched_smart_policy_apply(ctx)
        st_c, d_c = perf._sched_smart_cap_apply(ctx)
        check("both payloads apply on the pristine fixture",
              (st_p, st_c) == ("applied", "applied"), (st_p, st_c, d_p, d_c))
        text = ctx.read(rel)
        cap_init = text.index("late_initcall(abk_sc_init);")
        floor_init = text.index("late_initcall(abk_sf_init);")
        check("cap's late_initcall is textually before the floor's, so the "
              "clamp is the first thing the floor sees",
              cap_init < floor_init, (cap_init, floor_init))
        check("the re-assert probe sits one initcall level later (7s), which "
              "orders after every level-7 entry including the floor's",
              "late_initcall_sync(abk_sc_reassert_init);" in text
              and "late_initcall_sync(abk_sf_init);" not in text)
        check("the cap payload lands ahead of the anchor",
              text.index("ABK stable_515_backport: Batch 42 schedutil "
                         "smart_freq cap") < text.index(b42._TAIL_OLD),
              None)
        check("neither payload is defined twice",
              text.count("static bool abk_sf_enable") == 1
              and text.count("static bool abk_sc_enable") == 1
              and text.count("static void abk_sc_resolve_freq(") == 1
              and text.count("static void abk_sc_reassert_freq(") == 1
              and text.count("static void abk_sf_resolve_freq(") == 1,
              None)
        for g in perf.PATCH_GROUPS:
            if g.key in ("schedutil_smart_policy", "schedutil_smart_cap"):
                st2 = g.run(ctx)["status"]
                check(f"{g.key} is idempotent", st2 == "already_present",
                      (g.key, st2))

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {rel: pristine})
        perf._sched_smart_cap_apply(ctx)
        perf._sched_smart_policy_apply(ctx)
        reversed_text = ctx.read(rel)
        ctx2 = make_ctx(tmp + "-b", {rel: pristine})
        perf._sched_smart_policy_apply(ctx2)
        perf._sched_smart_cap_apply(ctx2)
        check("either application order produces the same file",
              reversed_text == ctx2.read(rel))
        check("cap-before-floor holds in the reversed order too",
              reversed_text.index("late_initcall(abk_sc_init);")
              < reversed_text.index("late_initcall(abk_sf_init);"))

    payload = b42._CAP
    declared = set(re.findall(r"(?m)^static\s+[\w \t\*]+?(\w+)\s*=", payload))
    for name in re.findall(r"(?m)^module_param\((\w+),", payload):
        check(f"one-name module_param {name!r} really declares that variable",
              name in declared, (name, sorted(declared)))

    for name, cond in {
        "cap ships disabled": "static bool abk_sc_enable = false;" in payload,
        "cap_pct defaults to no clamp": "static uint abk_sc_cap_pct = 100;" in payload,
        "hold_ms is upstream UNCAP_THRES":
            "static uint abk_sc_hold_ms = 300;" in payload,
        "entry_pct is upstream UTIL_THRESHOLD":
            "static uint abk_sc_entry_pct = 90;" in payload,
        "release_pct/release_ms defaults":
            "static uint abk_sc_release_pct = 70;" in payload
            and "static uint abk_sc_release_ms = 250;" in payload,
        "the cap is a fraction of cpuinfo.max_freq":
            "return mult_frac(policy->cpuinfo.max_freq, pct, 100);" in payload,
        "cap_pct > 100 is treated as 100":
            "if (pct > 100)\n\t\tpct = 100;" in payload,
        "all six knobs are 0644":
            len(re.findall(r"(?m)^module_param\(abk_sc_\w+, \w+, 0644\);",
                           payload)) == 6,
        "ownership gate: a non-schedutil governor is refused":
            'strcmp(policy->governor->name, "schedutil") != 0' in payload,
        "ownership gate: the helper is this payload's own, not the floor's":
            "static bool abk_sc_dvfs_owned(struct cpufreq_policy *policy)"
            in payload and "abk_sf_dvfs_owned(" not in payload,
        "a collapsed range is refused":
            "policy->min == policy->max" in payload,
        "a cap without headroom is never applied":
            "if (c <= policy->min || c >= policy->max)" in payload,
        "the clamp itself is upstream's form":
            "*target_freq = cap;" in payload,
        "the dead-band lesson is cited in the payload":
            "ABK_SC_RELEASE IS NOT A UTIL WINDOW" in payload
            and "70-90% dead band" in payload,
        "release is driven by the request direction":
            "p->high_start = jiffies;" in payload
            and "time_after(jiffies, p->high_start +" in payload
            and "msecs_to_jiffies(abk_sc_hold_ms)))" in payload,
        "a request at or below the cap restarts the window":
            "p->high_start = 0;" in payload,
        "the util window only re-asserts an already-released cap":
            "p->released = false;" in payload
            and "abk_sc_cooled(policy)" in payload,
        "the release expires on jiffies, not on a tick (NO_HZ_IDLE)":
            "time_after(jiffies, oldest + msecs_to_jiffies(abk_sc_release_ms))"
            in payload,
        "none of smart_policy's util-window idiom is reused":
            not re.search(r"\b(ABK_SF_\w+|boost_release)\b", payload)
            and len(re.findall(r"\babk_sf_\w+", payload)) == 1,
        "state is per-policy, not per-CPU":
            "struct abk_sc_policy {" in payload
            and "abk_sc_policies[policy->cpu]" in payload,
        "the ladder is guarded by a raw spinlock (fast_switch, rq->lock)":
            "raw_spinlock_t lock;" in payload
            and "raw_spin_lock_irqsave(&p->lock, flags)" in payload
            and "raw_spin_unlock_irqrestore(&p->lock, flags)" in payload,
        "the policy locks are initialised":
            "raw_spin_lock_init(&abk_sc_policies[cpu].lock);" in payload,
        "the arrays are zeroed allocations, with a matching free":
            "kcalloc(num_possible_cpus(), sizeof(*abk_sc_cpus)," in payload
            and "kcalloc(num_possible_cpus(), sizeof(*abk_sc_policies)," in payload
            and "kfree(abk_sc_cpus);" in payload,
        "the re-assert probe re-applies this pass's clamp only":
            "if (p->capped && *target_freq > cap)" in payload
            and "late_initcall_sync(abk_sc_reassert_init);" in payload,
        "the re-assert cannot touch a target the cap did not clamp":
            "if (!target_freq || !abk_sc_owns(policy, &cap))" in payload,
        "both read-only nodes are 0444":
            "module_param_cb(abk_sc_boosting, &abk_sc_boosting_ops, "
            "NULL, 0444);" in payload
            and "module_param_cb(abk_sc_capped, &abk_sc_capped_ops, "
            "NULL, 0444);" in payload,
        "the reason election is the upstream thres_based_uncap shape":
            "time_after(now, c->reason_start +" in payload
            and "c->reason_start = 0;" in payload,
        "the reason mask is derived, never latched":
            "c->reason = c->reason_start &&" in payload,
        "no knob name collides with the floor's abk_sf_ namespace":
            re.search(r"(?m)^(?:static\s+)?[\w \t\*]+?\babk_sf_\w+", payload) is None,
        "the only abk_sf_ mention is the comment naming the duplicate":
            payload.count("abk_sf_") == 1
            and "abk_sf_dvfs_owned" in payload,
        "no debug residue": "pr_err(" not in payload and "pr_debug(" not in payload
            and payload.count("pr_info(") == 1
            and payload.count("pr_warn(") == 3,
        "no uclamp misuse": "uclamp" not in payload,
        "no Kconfig, no structure change, no iowait boost":
            not any(s in payload for s in
                    ("Kconfig", "#if", "iowait_boost", "arch_scale_freq_invariant")),
        "every new line carries the marker":
            "/*\n * ABK stable_515_backport: Batch 42 schedutil smart_freq cap"
            in payload
            and payload.count("ABK stable_515_backport:") == 5,
    }.items():
        check(name, cond)

    check("the unknown-shape probe trips on the payload's declaring line",
          b42.has_unknown_cap("\nstatic bool abk_sc_enable = true;\n"))
    check("the unknown-shape probe is quiet on the current payload and on a "
          "tree that has none",
          not b42.has_unknown_cap(payload)
          and not b42.has_unknown_cap(pristine))
    check("the header-block probe agrees with what the include step produces",
          b42.has_header_block(b42._INC_NEW)
          and not b42.has_header_block(b42._INC_OLD)
          and b42._INC_HINT in b42._INC_NEW)

    with tempfile.TemporaryDirectory() as tmp:
        stray = (b42._INC_OLD + "void governor(void);\n" + b42._TAIL_OLD
                 + "\n/*\n * ABK stable_515_backport: Batch 9-9 cap.\n */\n"
                 "static bool abk_sc_enable = true;\n")
        check("an unnamed cap shape is not mistaken for the current one",
              b42.has_unknown_cap(stray) and not b42.has_current_cap(stray))
        ctx = make_ctx(tmp, {rel: stray})
        st_u, d_u = perf._sched_smart_cap_apply(ctx)
        check("an unnamed cap payload is refused, not applied",
              st_u == "blocked_by_shape" and "unrecognised" in d_u,
              (st_u, d_u))
        check("and the refusal writes nothing",
              ctx.pending_writes() == [], ctx.pending_writes())

    with tempfile.TemporaryDirectory() as tmp:
        ctx2 = make_ctx(tmp, {})
        st_m, d_m = perf._sched_smart_cap_apply(ctx2)
        check("the cap degrades on an empty tree",
              st_m.startswith("blocked") and ctx2.pending_writes() == [],
              (st_m, d_m))
        odd = ("#include <linux/sched/cpufreq.h>\n" + "void governor(void);\n"
               + b42._TAIL_OLD)
        ctx3 = make_ctx(tmp, {rel: odd})
        st_o, d_o = perf._sched_smart_cap_apply(ctx3)
        check("an unrecognised include shape reports blocked_by_shape",
              st_o == "blocked_by_shape" and ctx3.pending_writes() == [],
              (st_o, d_o, ctx3.pending_writes()))


TASK_MMU_FIXTURE = (
    "static void smaps_pte_entry(pte_t *pte, unsigned long addr,\n"
    "\t\tstruct mm_walk *walk)\n"
    "{\n"
    "\tstruct page *page = NULL;\n"
    "\tbool migration = false, young = false, dirty = false;\n"
    "\n"
    "\tif (pte_present(*pte)) {\n"
    "\t\tpage = vm_normal_page(vma, addr, *pte);\n"
    "\t\tyoung = pte_young(*pte);\n"
    "\t\tdirty = pte_dirty(*pte);\n"
    "\t} else if (is_swap_pte(*pte)) {\n"
    "\t\tswp_entry_t swpent = pte_to_swp_entry(*pte);\n"
    "\n"
    "\t\tif (!non_swap_entry(swpent)) {\n"
    "\t\t\tmss->swap += PAGE_SIZE;\n"
    "\t\t} else if (is_pfn_swap_entry(swpent)) {\n"
    "\t\t\tif (is_migration_entry(swpent))\n"
    "\t\t\t\tmigration = true;\n"
    "\t\t\tpage = pfn_swap_entry_to_page(swpent);\n"
    "\t\t}\n"
    "\t}\n"
    "\n"
    "\tif (!page)\n"
    "\t\treturn;\n"
    "\n"
    "\tsmaps_account(mss, page, false, young, dirty, locked, migration);\n"
    "}\n"
    "\n"
    "static void smaps_pmd_entry(pmd_t *pmd, unsigned long addr,\n"
    "\t\tstruct mm_walk *walk)\n"
    "{\n"
    "\tstruct page *page = NULL;\n"
    "\tbool migration = false;\n"
    "\n"
    "\tif (pmd_present(*pmd)) {\n"
    "\t\tpage = follow_trans_huge_pmd(vma, addr, pmd, FOLL_DUMP);\n"
    "\t} else if (unlikely(thp_migration_supported() && is_swap_pmd(*pmd))) {\n"
    "\t\tswp_entry_t entry = pmd_to_swp_entry(*pmd);\n"
    "\n"
    "\t\tif (is_migration_entry(entry)) {\n"
    "\t\t\tmigration = true;\n"
    "\t\t\tpage = pfn_swap_entry_to_page(entry);\n"
    "\t\t}\n"
    "\t}\n"
    "\tif (IS_ERR_OR_NULL(page))\n"
    "\t\treturn;\n"
    "\n"
    "\tsmaps_account(mss, page, true, pmd_young(*pmd), pmd_dirty(*pmd),\n"
    "\t\t      locked, migration);\n"
    "}\n"
    "\n"
    "static int smaps_hugetlb_range(pte_t *pte, unsigned long hmask,\n"
    "\t\t\t\t unsigned long addr, unsigned long end,\n"
    "\t\t\t\t struct mm_walk *walk)\n"
    "{\n"
    "\tstruct page *page = NULL;\n"
    "\n"
    "\tif (pte_present(*pte)) {\n"
    "\t\tpage = vm_normal_page(vma, addr, *pte);\n"
    "\t} else if (is_swap_pte(*pte)) {\n"
    "\t\tswp_entry_t swpent = pte_to_swp_entry(*pte);\n"
    "\n"
    "\t\tif (is_pfn_swap_entry(swpent))\n"
    "\t\t\tpage = pfn_swap_entry_to_page(swpent);\n"
    "\t}\n"
    "\tif (page) {\n"
    "\t\tif (page_mapcount(page) >= 2 || hugetlb_pmd_shared(pte))\n"
    "\t\t\tseq_putc(m, '\\n');\n"
    "\t}\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
    "static pagemap_entry_t pte_to_pagemap_entry(struct pagemapread *pm,\n"
    "\t\tstruct vm_area_struct *vma, unsigned long addr, pte_t pte)\n"
    "{\n"
    "\tu64 frame = 0, flags = 0;\n"
    "\tstruct page *page = NULL;\n"
    "\tbool migration = false;\n"
    "\n"
    "\tif (pte_present(pte)) {\n"
    "\t\tpage = pte_page(pte);\n"
    "\n"
    "\t\tflags |= PM_PRESENT;\n"
    "\t} else if (is_swap_pte(pte)) {\n"
    "\t\tswp_entry_t entry;\n"
    "\n"
    "\t\tif (pte_swp_soft_dirty(pte))\n"
    "\t\t\tflags |= PM_SOFT_DIRTY;\n"
    "\t\tentry = pte_to_swp_entry(pte);\n"
    "\t\tif (pm->show_pfn)\n"
    "\t\t\tframe = swp_type(entry) |\n"
    "\t\t\t\t(swp_offset(entry) << MAX_SWAPFILES_SHIFT);\n"
    "\t\tflags |= PM_SWAP;\n"
    "\t\tmigration = is_migration_entry(entry);\n"
    "\t\tif (is_pfn_swap_entry(entry))\n"
    "\t\t\tpage = pfn_swap_entry_to_page(entry);\n"
    "\t}\n"
    "\n"
    "\tif (page && !PageAnon(page))\n"
    "\t\tflags |= PM_FILE;\n"
    "\tif (page && !migration && page_mapcount(page) == 1)\n"
    "\t\tflags |= PM_MMAP_EXCLUSIVE;\n"
    "\n"
    "\treturn make_pme(frame, flags);\n"
    "}\n"
    "\n"
    "static int pagemap_pmd_range(pmd_t *pmdp, unsigned long addr,\n"
    "\t\t\t     unsigned long end, struct mm_walk *walk)\n"
    "{\n"
    "\tspinlock_t *ptl;\n"
    "\tint err = 0;\n"
    "#ifdef CONFIG_TRANSPARENT_HUGEPAGE\n"
    "\tbool migration = false;\n"
    "#endif\n"
    "\n"
    "\tptl = pmd_trans_huge_lock(pmdp, walk->vma);\n"
    "\tif (ptl) {\n"
    "\t\tu64 flags = 0, frame = 0;\n"
    "\t\tpmd_t pmd = *pmdp;\n"
    "\t\tstruct page *page = NULL;\n"
    "\n"
    "\t\tif (pmd_present(pmd)) {\n"
    "\t\t\tpage = pmd_page(pmd);\n"
    "\n"
    "\t\t\tflags |= PM_PRESENT;\n"
    "\t\t}\n"
    "#ifdef CONFIG_ARCH_ENABLE_THP_MIGRATION\n"
    "\t\telse if (is_swap_pmd(pmd)) {\n"
    "\t\t\tswp_entry_t entry = pmd_to_swp_entry(pmd);\n"
    "\t\t\tunsigned long offset;\n"
    "\n"
    "\t\t\tif (pm->show_pfn) {\n"
    "\t\t\t\toffset = swp_offset(entry) +\n"
    "\t\t\t\t\t((addr & ~PMD_MASK) >> PAGE_SHIFT);\n"
    "\t\t\t\tframe = swp_type(entry) |\n"
    "\t\t\t\t\t(offset << MAX_SWAPFILES_SHIFT);\n"
    "\t\t\t}\n"
    "\t\t\tflags |= PM_SWAP;\n"
    "\t\t\tVM_BUG_ON(!is_pmd_migration_entry(pmd));\n"
    "\t\t\tmigration = is_migration_entry(entry);\n"
    "\t\t\tpage = pfn_swap_entry_to_page(entry);\n"
    "\t\t}\n"
    "#endif\n"
    "\n"
    "\t\tif (page && !PageAnon(page))\n"
    "\t\t\tflags |= PM_FILE;\n"
    "\t\tif (page && !migration && page_mapcount(page) == 1)\n"
    "\t\t\tflags |= PM_MMAP_EXCLUSIVE;\n"
    "\n"
    "\t\tspin_unlock(ptl);\n"
    "\t\treturn err;\n"
    "\t}\n"
    "\treturn 0;\n"
    "}\n"
)


def test_batch70_smaps_migration_guard():
    print("Batch 70: smaps_migration_guard (migration-entry PageLocked race)")
    import abk_stable_core as core
    import batch70_core_smaps_migration_guard as b70

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "smaps_migration_guard"), None)
    check("smaps_migration_guard group registered", group is not None)
    if group is None:
        return
    check("the group owns exactly fs/proc/task_mmu.c",
          group.files == [b70.TASK_MMU], group.files)
    check("fs/proc/task_mmu.c is the module's first fs/proc target",
          b70.TASK_MMU == "fs/proc/task_mmu.c", b70.TASK_MMU)

    owners = [g.key for g in core.PATCH_GROUPS if b70.TASK_MMU in g.files]
    check("no other group writes fs/proc/task_mmu.c",
          owners == ["smaps_migration_guard"], owners)

    steps = b70.build_steps()
    check("six required steps (one helper + five caller guards), all in "
          "fs/proc/task_mmu.c",
          [rel for rel, _o, _n, req in steps] == [b70.TASK_MMU] * 6
          and all(req for _r, _o, _n, req in steps),
          [(rel, req) for rel, _o, _n, req in steps])

    fixture = TASK_MMU_FIXTURE
    for index, (_rel, _old, new, _req) in enumerate(steps):
        check(f"step {index}'s replacement is not already in the pristine file",
              new not in fixture)
        check(f"step {index}'s replacement does not start with its own anchor",
              not new.startswith(_old))
    for i, (_rel, _o, new_i, _req) in enumerate(steps):
        for j, (_rel2, old_j, new_j, _req2) in enumerate(steps):
            if i == j or new_i in old_j:
                continue
            check(f"step {i} does not pre-create step {j}'s replacement",
                  new_j not in new_i)

    for index, (_rel, old, _n, _req) in enumerate(steps):
        check(f"step {index}'s anchor occurs exactly once in the fixture",
              fixture.count(old) == 1, fixture.count(old))

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {b70.TASK_MMU: fixture})
        status, detail = b70._smaps_migration_guard_apply(ctx)
        check("the group applies on the tree's shape",
              status == "applied", (status, detail))
        text = ctx.read(b70.TASK_MMU)

        helper = text[text.index("/*\n * sailboat_smaps_migration_guard:"):
                      text.index(b70._HELPER_OLD)]
        check("the helper resolves the page itself",
              "p = pfn_to_page(swp_offset(entry));" in helper, helper)
        helper_code = [ln for ln in helper.split("\n")
                       if not ln.lstrip().startswith(("*", "/*", "//"))]
        check("the helper never calls the BUG_ON form",
              not [ln for ln in helper_code
                   if "pfn_swap_entry_to_page" in ln],
              helper_code)
        check("the helper keeps the pfn-before-flags ordering",
              "smp_rmb();" in helper)
        check("the helper gates on PageLocked (the BUG_ON's own predicate)",
              "if (!PageLocked(p))" in helper
              and helper.count("if (!is_migration_entry(entry))") == 1,
              helper)
        check("the helper returns NULL rather than panicking",
              helper.count("return NULL;") == 2, helper)

        check("the smaps PTE and hugetlb callers are guarded",
              text.count(b70.PTE_GUARD) == 2, text.count(b70.PTE_GUARD))
        check("the smaps PMD and both pagemap callers are guarded",
              text.count(b70.PMD_GUARD) == 3, text.count(b70.PMD_GUARD))

        check("the smaps PTE non-migration path keeps the direct call",
              "\t\t\tif (is_migration_entry(swpent)) {\n"
              "\t\t\t\tmigration = true;\n"
              "\t\t\t\tpage = abk_smaps_migration_page(swpent);\n"
              "\t\t\t} else {\n"
              "\t\t\t\tpage = pfn_swap_entry_to_page(swpent);\n"
              "\t\t\t}\n" in text)
        check("the hugetlb non-migration path keeps the direct call",
              "\t\tif (is_migration_entry(swpent))\n"
              "\t\t\tpage = abk_smaps_migration_page(swpent);\n"
              "\t\telse if (is_pfn_swap_entry(swpent))\n"
              "\t\t\tpage = pfn_swap_entry_to_page(swpent);\n" in text)
        check("the pagemap PTE non-migration path keeps the direct call",
              "\t\tif (is_migration_entry(entry))\n"
              "\t\t\tpage = abk_smaps_migration_page(entry);\n"
              "\t\telse if (is_pfn_swap_entry(entry))\n"
              "\t\t\tpage = pfn_swap_entry_to_page(entry);\n" in text)
        check("the pagemap PMD guard keeps the tree's own VM_BUG_ON",
              "\t\t\tVM_BUG_ON(!is_pmd_migration_entry(pmd));\n"
              "\t\t\tmigration = is_migration_entry(entry);\n"
              "\t\t\tpage = abk_smaps_migration_page(entry);\n" in text)
        gate = text.index("#ifdef CONFIG_ARCH_ENABLE_THP_MIGRATION")
        check("the pagemap PMD guard stays inside its CONFIG gate",
              "\t\telse if (is_swap_pmd(pmd)) {" in text
              and gate < text.index("\t\t\tpage = abk_smaps_migration_page(entry);",
                                   gate)
              < text.index("#endif", gate))

        first_guard = min(text.index(b70.PTE_GUARD), text.index(b70.PMD_GUARD))
        check("the helper is defined before its first caller",
              text.index(b70.HELPER_DEF) < first_guard)

        check("the smaps PMD caller still raises the migration flag",
              "\t\tif (is_migration_entry(entry)) {\n"
              "\t\t\tmigration = true;\n"
              "\t\t\tpage = abk_smaps_migration_page(entry);\n" in text)
        check("the smaps PTE caller still raises the migration flag",
              "\t\t\tif (is_migration_entry(swpent)) {\n"
              "\t\t\t\tmigration = true;\n"
              "\t\t\t\tpage = abk_smaps_migration_page(swpent);\n" in text)
        check("the NULL contract is satisfied by the callers' own code",
              "\tif (!page)\n\t\treturn;\n" in text
              and "\tif (IS_ERR_OR_NULL(page))\n\t\treturn;\n" in text
              and "\tif (page && !PageAnon(page))\n" in text)

        check("the guard adds no include",
              text.count("#include") == fixture.count("#include"),
              text.count("#include"))
        check("the guard opens no preprocessor gate",
              text.count("#if") == fixture.count("#if"))

        markerless = text.replace(b70.HELPER_MARKER, "no marker here")
        check("the probe requires the marker, not just the helper",
              b70.HELPER_MARKER in text and b70.HELPER_MARKER not in markerless,
              b70.HELPER_MARKER)
        with tempfile.TemporaryDirectory() as tmp_m:
            ctx_m = make_ctx(tmp_m, {b70.TASK_MMU: markerless})
            status_m, detail_m = b70._smaps_migration_guard_apply(ctx_m)
            check("a tree whose marker is gone is refused, not skipped",
                  status_m == "blocked_by_shape", (status_m, detail_m))
            check("the refusal names the missing marker",
                  b70.HELPER_MARKER in detail_m, detail_m)
            check("the marker-less tree is not written",
                  ctx_m.pending_writes() == [],
                  ctx_m.pending_writes())
        check("a second pass cannot duplicate the helper",
              text.count(b70.HELPER_DEF) == 1, text.count(b70.HELPER_DEF))
        code_lines = [ln for ln in text.split("\n")
                      if not ln.lstrip().startswith(("*", "/*", "//"))]
        warns = [ln.strip() for ln in code_lines
                 if "WARN_ON" in ln or "WARN(" in ln]
        check("no WARN in the guarded code", warns == [], warns)

        with tempfile.TemporaryDirectory() as tmp2:
            ctx2 = make_ctx(tmp2, {b70.TASK_MMU: text})
            status2, detail2 = b70._smaps_migration_guard_apply(ctx2)
            check("second pass is already_present",
                  status2 == "already_present", (status2, detail2))
            check("second pass is byte-identical",
                  ctx2.read(b70.TASK_MMU) == text)

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {b70.TASK_MMU: "static void other(void)\n"})
        status, detail = b70._smaps_migration_guard_apply(ctx)
        check("an unknown shape reports blocked_by_shape",
              status == "blocked_by_shape", (status, detail))
        check("the unknown-shape tree is not written",
              ctx.pending_writes() == [])

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {})
        status, detail = b70._smaps_migration_guard_apply(ctx)
        check("a missing file reports blocked_by_shape",
              status == "blocked_by_shape", (status, detail))
        check("the empty tree is not written",
              ctx.pending_writes() == [])


def test_fetch_decode_roundtrip():
    print("fetch_sublevel_tree.sh: decode() refuses a non-canonical body")
    tool = Path(__file__).resolve().parent / "fetch_sublevel_tree.sh"
    check("the fetcher is present", tool.is_file(), tool)

    bash = shutil.which("bash")
    if bash is None:
        check("bash is available for the fetcher self-test", False, "no bash")
        return
    quoted = str(tool).replace("\\", "/")

    def run_decode(body_b64, backend=None):
        env = dict(os.environ)
        if backend:
            env["ABK_FETCH_DECODER"] = backend
        else:
            env.pop("ABK_FETCH_DECODER", None)
        with tempfile.NamedTemporaryFile("w", suffix=".b64", delete=False) as fh:
            fh.write(body_b64 if body_b64.endswith("\n") else body_b64 + "\n")
            body_path = fh.name
        try:
            return subprocess.run(
                [bash, quoted, "--decode-only"],
                stdin=open(body_path, "rb"), capture_output=True, timeout=180,
                env=env)
        finally:
            os.unlink(body_path)

    raw = b"/* SPDX-License-Identifier: GPL-2.0 */\nint x;\n"
    clean_b64 = base64.b64encode(raw).decode()
    corrupt_b64 = clean_b64[:8] + "!" + clean_b64[8:]

    for backend in ("base64", "python3"):
        if shutil.which(backend) is None:
            check(f"the {backend} backend is available", False, "missing")
            continue

        good = run_decode(clean_b64, backend)
        check(f"{backend}: a clean body decodes to its exact source",
              good.returncode == 0 and good.stdout == raw,
              (good.returncode, good.stdout[:60]))

        bad = run_decode(corrupt_b64, backend)
        check(f"{backend}: a corrupted body is refused (non-zero exit)",
              bad.returncode != 0,
              (bad.returncode, bad.stdout[:60]))
        if backend == "python3":
            check("python3: a corrupted body yields no bytes at all",
                  bad.stdout == b"", bad.stdout[:60])

        wrapped = base64.b64encode(raw * 40).decode()
        wrapped = "\n".join(wrapped[i:i + 76] for i in range(0, len(wrapped), 76))
        wr = run_decode(wrapped, backend)
        check(f"{backend}: a 76-column wrapped body still decodes",
              wr.returncode == 0 and wr.stdout == raw * 40,
              (wr.returncode, wr.stdout[:40]))

    st = subprocess.run([bash, quoted, "--self-test-decode"],
                        capture_output=True, text=True, timeout=180)
    check("the fetcher self-test passes", st.returncode == 0,
          (st.returncode, st.stdout[-300:], st.stderr[-300:]))
    for backend in ("base64", "python3"):
        check(f"the self-test drove the {backend} backend",
              f"{backend} refused the corrupted body" in st.stdout,
              st.stdout[-300:])


def test_batch71_kcompressd_wb_pin():
    print("Batch 71: kcompressd_writeback_pin (swap slot pinned under writeback)")
    import abk_stable_core as core
    import batch41_core_vm_kcompressd as b41
    import batch71_core_kcompressd_wb_pin as b71

    group = next((g for g in core.PATCH_GROUPS
                  if g.key == "kcompressd_writeback_pin"), None)
    check("kcompressd_writeback_pin group registered", group is not None)
    if group is None:
        return
    check("the group owns exactly mm/page_io.c",
          group.files == [b71.PAGE_IO], group.files)

    keys = [g.key for g in core.PATCH_GROUPS]
    check("the writeback pin is registered after vm_kcompressd_swapout",
          keys.index("kcompressd_writeback_pin")
          > keys.index("vm_kcompressd_swapout"),
          keys[keys.index("kcompressd_writeback_pin") - 1:
               keys.index("kcompressd_writeback_pin") + 1])

    steps = b71.build_steps()
    check("three required steps, all in mm/page_io.c",
          [(rel, req) for rel, _o, _n, req in steps]
          == [(b71.PAGE_IO, True), (b71.PAGE_IO, True), (b71.PAGE_IO, True)],
          [(rel, req) for rel, _o, _n, req in steps])
    for i, (_rel, old, new, _req) in enumerate(steps):
        check(f"step {i}'s replacement does not start with its own anchor (trap 1)",
              not new.startswith(old))

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {b71.PAGE_IO: "int swap_writepage(void)\n"})
        status, detail = b71._kcompressd_wb_pin_apply(ctx)
        check("a tree without the Batch 41 engine reports blocked_by_shape",
              status == "blocked_by_shape", (status, detail))
        check("the refusal names the missing engine",
              "vm_kcompressd_swapout" in detail, detail)
        check("the refused tree is not written",
              ctx.pending_writes() == [])

    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_ctx(tmp, {})
        status, detail = b71._kcompressd_wb_pin_apply(ctx)
        check("a missing file reports blocked_by_shape",
              status == "blocked_by_shape", (status, detail))
        check("the empty tree is not written",
              ctx.pending_writes() == [])

    def b41_pristine(frontswap):
        return (
            "#include <linux/sched/task.h>\n"
            "\n"
            "/*\n"
            " * We may have stale swap cache pages in memory: notice\n"
            " * them here and get rid of the unnecessary final write.\n"
            " */\n"
            "int swap_writepage(struct page *page, struct writeback_control *wbc)\n"
            "{\n"
            "\tint ret = 0;\n"
            "\n"
            + frontswap +
            "\tret = __swap_writepage(page, wbc, end_swap_bio_write);\n"
            "out:\n"
            "\treturn ret;\n"
            "}\n"
            "\n"
            + b41._ENGINE_ANCHOR_OLD
        )

    for label, frontswap in (("plain", b41.PLAIN_PROBE),
                             ("hook", b41.HOOK_PROBE)):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx(tmp, {b41.PAGE_IO: b41_pristine(frontswap)})
            status, detail = core._vm_kcompressd_swapout_apply(ctx)
            check(f"{label} shape: Batch 41 applies first",
                  status == "applied", (status, detail))
            text = ctx.read(b71.PAGE_IO)
            for _rel, anchor, _new, _req in steps:
                check(f"{label} shape: an anchor occurs exactly once in "
                      "Batch 41's output", text.count(anchor) == 1,
                      text.count(anchor))

            status, detail = b71._kcompressd_wb_pin_apply(ctx)
            check(f"{label} shape: the writeback pin applies on top",
                  status == "applied", (status, detail))
            text = ctx.read(b71.PAGE_IO)

            check(f"{label} shape: enqueue sets writeback before unlocking",
                  "\t\tset_page_writeback(page);\n"
                  "\t\tunlock_page(page);\n"
                  "\t\treturn 0;\n" in text, "offload branch")

            do_swapout = text[text.index("static void abk_kcompressd_do_swapout("):
                              text.index("static bool abk_kcompressd_enqueue(")]
            check(f"{label} shape: the drain takes the page lock",
                  "\tlock_page(page);\n" in do_swapout)
            check(f"{label} shape: the drain ends the writeback it inherited",
                  "end_page_writeback(page);\n\n" in do_swapout)
            drain_end = do_swapout.index("end_page_writeback(page);\n\n")
            check(f"{label} shape: lock precedes the drain end precedes body",
                  do_swapout.index("\tlock_page(page);\n") < drain_end
                  < do_swapout.index("frontswap_store(page)"))
            check(f"{label} shape: the drop path ends writeback before put_page",
                  "end_page_writeback(page);\n\t\tput_page(page);" in do_swapout)
            check(f"{label} shape: the drop-path end precedes the drain lock",
                  do_swapout.index("end_page_writeback(page);\n\t\tput_page(page);")
                  < do_swapout.index("\tlock_page(page);\n"))
            check(f"{label} shape: no trylock-skip remains",
                  "if (!trylock_page(page)) {" not in text
                  and "skipping a locked page" not in text, "trylock remnants")
            check(f"{label} shape: the marker is on all three edits",
                  text.count("sailboat_kcompressd_wb_pin") == 3,
                  text.count("sailboat_kcompressd_wb_pin"))
            code_lines = [ln for ln in do_swapout.split("\n")
                          if not ln.lstrip().startswith(("*", "/*", "//"))]
            warns = [ln.strip() for ln in code_lines
                     if "WARN_ON" in ln or "WARN(" in ln]
            check(f"{label} shape: no WARN in the drain's code", not warns, warns)

            ctx2 = make_ctx(tmp + "-b", {b71.PAGE_IO: text})
            status2, detail2 = core._vm_kcompressd_swapout_apply(ctx2)
            check(f"{label} shape: Batch 41's second pass is already_present",
                  status2 == "already_present", (status2, detail2))
            check(f"{label} shape: Batch 41's second pass is byte-identical",
                  ctx2.read(b71.PAGE_IO) == text)
            status3, detail3 = b71._kcompressd_wb_pin_apply(ctx2)
            check(f"{label} shape: the writeback pin's second pass is "
                  "already_present", status3 == "already_present",
                  (status3, detail3))
            check(f"{label} shape: the second pass is byte-identical",
                  ctx2.read(b71.PAGE_IO) == text)
            for key in ("vm_kcompressd_swapout", "kcompressd_writeback_pin"):
                st = next(g for g in core.PATCH_GROUPS
                          if g.key == key).run(ctx2)["status"]
                check(f"{label} shape: {key} is idempotent",
                      st == "already_present", (key, st))


def test_workflow_expressions_are_ascii():
    print("workflow expression hygiene (a ${{ }} in a run block is live)")
    wf_dir = Path(__file__).resolve().parent.parent / ".github" / "workflows"
    files = sorted(wf_dir.glob("*.y*ml"))
    check("workflows: the CI workflow files are present", bool(files), str(wf_dir))
    offenders = []
    total = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"\$\{\{(.*?)\}\}", text, re.S):
            total += 1
            if any(ord(c) > 127 for c in m.group(1)):
                offenders.append(f"{path.name}:{text.count(chr(10), 0, m.start()) + 1}")
    check("workflows: every interpolation is ASCII", not offenders, offenders)
    check("workflows: the scan actually saw expressions", total > 0, total)
    gate = (wf_dir / "abk-kernel-compile.yml").read_text(encoding="utf-8")
    check("gate: the ;ref= probe matches the run-name prefix, not a literal",
          "^run-name:[^A-Za-z0-9]*ABK-CI" in gate)
    check("gate: no literal run-name probe is left behind",
          "grep -q 'run-name: ABK-CI'" not in gate)


if __name__ == "__main__":
    main()
