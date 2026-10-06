"""Batch 57 (sched_ext S2b-2, first half): the 5.15 adaptation layer.

Batch 56 ran the first real compiler over the Batch-54 payload and measured what
"the payload participates in the build" actually means: the SCX translation unit
fails with 12 classes of 5.15/6.6 interface drift, and three of them are
function-pointer signatures no preprocessor shim can bridge.  The file therefore
stays archived byte-for-byte with its sha256 pin -- that is the provenance
property -- and gets a marked adaptation at the point it is materialised.

Three groups, split by where each class can live:

* `sched_ext_core_visibility` -- kernel/sched/core.c already owns
  `__setscheduler_prio()` and `check_class_changed()`; for the payload, a
  separate translation unit, to re-run the class selection when a task enters or
  leaves the BPF scheduler, both have to stop being translation-unit-local.  The
  bodies are unchanged; the group drops `static`/`static inline` and adds the
  two prototypes to kernel/sched/sched.h (classes 5 and 6 of the survey table).

* `sched_ext_change_guard` -- the 6.2 `SCHED_CHANGE_BLOCK` machinery
  (class 4).  It cannot be carried in the glue unit: its expansion declares the
  guard in the for-loop initialiser, which this baseline's `-std=gnu89` rejects
  under clang as `-Wgcc-compat` (an error with CONFIG_WERROR), and its body
  calls `dequeue_task()`/`enqueue_task()`, which are `static inline` in
  core.c and carry the vendor trace hooks.  So the guard is re-carried where
  those two helpers live: `struct sched_change_guard` plus prototypes in
  sched.h, the two functions in core.c directly after `dequeue_task()`.

* `sched_ext_payload_adapt` -- the sites a preprocessor cannot reach:
  `set_cpus_allowed_scx()`'s parameter list (this baseline's
  `sched_class::set_cpus_allowed()` is `(p, newmask, flags)`; 6.2's
  `struct affinity_context` does not exist here), OPPO's
  `CONFIG_SLIM_SCHED` `sched_prop` reset (a vendor KABI member with no
  carrier on this tree -- the policy is to drop the write, not to add a slot),
  the 7-argument `btf_struct_access()` this baseline carries from the ACK
  backport, the 2-argument `bpf_struct_ops::check_member()`, the
  `int`-taking `sysrq_key_op::handler` (classes 1, 3, 7, 9, 10), and the
  three `SCHED_CHANGE_BLOCK` sites open-coded onto the guard the second group
  provides (class 4).

The classes that do go through the preprocessor -- the scheduler weight helper,
`for_each_cpu_andnot()`, the BTF bit-offset alias and the diag suppression --
live in the module-authored glue translation unit
(files/kernel/sched/sched_ext_glue.c), not here: they are new code with no anchor
to attach to, and the glue unit is already the file that exists to supply what
ext.c does not carry.

This batch makes the payload compile.  It does not make the class reachable:
`valid_policy()` still rejects SCHED_EXT and none of the functional hooks
(fork/tick/pick/setscheduler/idle/debugfs) are wired, so the second half of S2b-2
is unchanged.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

CORE_C = "kernel/sched/core.c"
KSCHED_H = "kernel/sched/sched.h"
EXT_C = "kernel/sched/ext.c"


# ---------------------------------------------------------------------------
# kernel/sched/core.c + kernel/sched/sched.h: make the two class helpers visible.
# ---------------------------------------------------------------------------

_CORE_PRIO_OLD = (
    "static void __setscheduler_prio(struct task_struct *p, int prio)\n"
    "{\n"
    "\tif (dl_prio(prio))\n"
    "\t\tp->sched_class = &dl_sched_class;\n"
    "\telse if (rt_prio(prio))\n"
    "\t\tp->sched_class = &rt_sched_class;\n"
    "\telse\n"
    "\t\tp->sched_class = &fair_sched_class;\n"
    "\n"
    "\tp->prio = prio;\n"
    "}\n"
)

_CORE_PRIO_NEW = (
    "/*\n"
    " * sailboat_sched_ext_core_visibility: not static any more.  kernel/sched/ext.c\n"
    " * is a separate translation unit and re-runs the class selection when a task\n"
    " * enters or leaves the BPF scheduler, so it needs this helper.  The body is\n"
    " * unchanged and this stays the only implementation\n"
    " * (docs/survey_sched_ext_gap.md section 2c, class 5).\n"
    " */\n"
    "void __setscheduler_prio(struct task_struct *p, int prio)\n"
    "{\n"
    "\tif (dl_prio(prio))\n"
    "\t\tp->sched_class = &dl_sched_class;\n"
    "\telse if (rt_prio(prio))\n"
    "\t\tp->sched_class = &rt_sched_class;\n"
    "\telse\n"
    "\t\tp->sched_class = &fair_sched_class;\n"
    "\n"
    "\tp->prio = prio;\n"
    "}\n"
)

_CORE_CHANGED_OLD = (
    "static inline void check_class_changed(struct rq *rq, struct task_struct *p,\n"
    "\t\t\t\t       const struct sched_class *prev_class,\n"
    "\t\t\t\t       int oldprio)\n"
    "{\n"
    "\tif (prev_class != p->sched_class) {\n"
    "\t\tif (prev_class->switched_from)\n"
    "\t\t\tprev_class->switched_from(rq, p);\n"
    "\n"
    "\t\tp->sched_class->switched_to(rq, p);\n"
    "\t} else if (oldprio != p->prio || dl_task(p))\n"
    "\t\tp->sched_class->prio_changed(rq, p, oldprio);\n"
    "}\n"
)

_CORE_CHANGED_NEW = (
    "/*\n"
    " * sailboat_sched_ext_core_visibility: de-inlined and no longer static, for\n"
    " * kernel/sched/ext.c.  The body is unchanged\n"
    " * (docs/survey_sched_ext_gap.md section 2c, class 6).\n"
    " */\n"
    "void check_class_changed(struct rq *rq, struct task_struct *p,\n"
    "\t\t\t const struct sched_class *prev_class,\n"
    "\t\t\t int oldprio)\n"
    "{\n"
    "\tif (prev_class != p->sched_class) {\n"
    "\t\tif (prev_class->switched_from)\n"
    "\t\t\tprev_class->switched_from(rq, p);\n"
    "\n"
    "\t\tp->sched_class->switched_to(rq, p);\n"
    "\t} else if (oldprio != p->prio || dl_task(p))\n"
    "\t\tp->sched_class->prio_changed(rq, p, oldprio);\n"
    "}\n"
)

_KSCHED_PROTO_OLD = (
    "extern void set_cpus_allowed_common(struct task_struct *p, const struct cpumask *new_mask, u32 flags);\n"
)

_KSCHED_PROTO_NEW = _KSCHED_PROTO_OLD + (
    "\n"
    "/*\n"
    " * sailboat_sched_ext_core_visibility: the class-change pair lives in\n"
    " * kernel/sched/core.c and is no longer translation-unit local, so that\n"
    " * kernel/sched/ext.c can re-run the class selection on the SCX enable and\n"
    " * disable paths (batch57_perf_sched_ext_adapt; docs/survey_sched_ext_gap.md\n"
    " * section 2c, classes 5-6).  Without these two declarations the de-static'ed\n"
    " * definitions trip -Wmissing-prototypes under W=1.\n"
    " */\n"
    "extern void __setscheduler_prio(struct task_struct *p, int prio);\n"
    "extern void check_class_changed(struct rq *rq, struct task_struct *p,\n"
    "\t\t\t\tconst struct sched_class *prev_class,\n"
    "\t\t\t\tint oldprio);\n"
)


# Batch 58 (sched_ext_setscheduler_hooks) inserts the task_on_scx() branch into
# the __setscheduler_prio() body this group generates, so a second pass would
# otherwise find neither the pristine old block nor this group's own new block
# and report blocked_by_missing_anchor.  The probe is on this group's own marker
# text (group_recipe trap 5; the Batch 49/50 remedy).
_SCHED_EXT_CORE_VISIBILITY_PROBE = "sailboat_sched_ext_core_visibility:"


def _sched_ext_core_visibility_apply(ctx):
    try:
        probe = ctx.read(CORE_C)
    except FileNotFoundError:
        probe = ""
    if _SCHED_EXT_CORE_VISIBILITY_PROBE in probe:
        return "already_present", (
            "the class-change pair is already visible to the payload")
    status, _results, detail = apply_steps(ctx, [
        (CORE_C, _CORE_PRIO_OLD, _CORE_PRIO_NEW, True),
        (CORE_C, _CORE_CHANGED_OLD, _CORE_CHANGED_NEW, True),
        (KSCHED_H, _KSCHED_PROTO_OLD, _KSCHED_PROTO_NEW, True),
    ])
    return status, detail


# ---------------------------------------------------------------------------
# kernel/sched/core.c + kernel/sched/sched.h: the 6.2 SCHED_CHANGE_BLOCK guard.
# ---------------------------------------------------------------------------

_GUARD_PROTO_OLD = (
    "extern void activate_task(struct rq *rq, struct task_struct *p, int flags);\n"
    "extern void deactivate_task(struct rq *rq, struct task_struct *p, int flags);\n"
)

_GUARD_PROTO_NEW = _GUARD_PROTO_OLD + (
    "\n"
    "/*\n"
    " * sailboat_sched_ext_change_guard: upstream 6.2's sched_change_guard, which\n"
    " * takes a task off the runqueue and off the CPU for an attribute update and\n"
    " * puts it back afterwards.  kernel/sched/ext.c expresses its three\n"
    " * enable/disable switches with it; the functions live in core.c because that\n"
    " * is where dequeue_task()/enqueue_task() are defined\n"
    " * (docs/survey_sched_ext_gap.md section 2c, class 4).\n"
    " */\n"
    "struct sched_change_guard {\n"
    "\tstruct task_struct\t*p;\n"
    "\tstruct rq\t\t*rq;\n"
    "\tbool\t\t\tqueued;\n"
    "\tbool\t\t\trunning;\n"
    "\tbool\t\t\tdone;\n"
    "};\n"
    "\n"
    "extern struct sched_change_guard\n"
    "sched_change_guard_init(struct rq *rq, struct task_struct *p, int flags);\n"
    "\n"
    "extern void sched_change_guard_fini(struct sched_change_guard *cg, int flags);\n"
)

_GUARD_IMPL_OLD = (
    "\tp->sched_class->dequeue_task(rq, p, flags);\n"
    "\ttrace_android_rvh_after_dequeue_task(rq, p, flags);\n"
    "}\n"
    "\n"
    "void activate_task(struct rq *rq, struct task_struct *p, int flags)\n"
)

_GUARD_IMPL_NEW = (
    "\tp->sched_class->dequeue_task(rq, p, flags);\n"
    "\ttrace_android_rvh_after_dequeue_task(rq, p, flags);\n"
    "}\n"
    "\n"
    "/*\n"
    " * sailboat_sched_ext_change_guard: upstream 6.2's guard, re-carried here\n"
    " * rather than in the payload's glue unit because dequeue_task() and\n"
    " * enqueue_task() above are static inline in this file and carry the vendor\n"
    " * trace hooks -- a copy would fork the accounting they do.  The body is the\n"
    " * upstream one; set_next_task()/put_prev_task() are already the two-argument\n"
    " * shape on 5.15 (docs/survey_sched_ext_gap.md section 2c, class 4).\n"
    " */\n"
    "struct sched_change_guard\n"
    "sched_change_guard_init(struct rq *rq, struct task_struct *p, int flags)\n"
    "{\n"
    "\tstruct sched_change_guard cg = {\n"
    "\t\t.rq = rq,\n"
    "\t\t.p = p,\n"
    "\t\t.queued = task_on_rq_queued(p),\n"
    "\t\t.running = task_current(rq, p),\n"
    "\t};\n"
    "\n"
    "\tif (cg.queued) {\n"
    "\t\tlockdep_assert_rq_held(rq);\n"
    "\t\tdequeue_task(rq, p, flags);\n"
    "\t}\n"
    "\tif (cg.running)\n"
    "\t\tput_prev_task(rq, p);\n"
    "\n"
    "\treturn cg;\n"
    "}\n"
    "\n"
    "void sched_change_guard_fini(struct sched_change_guard *cg, int flags)\n"
    "{\n"
    "\tif (cg->queued)\n"
    "\t\tenqueue_task(cg->rq, cg->p, flags | ENQUEUE_NOCLOCK);\n"
    "\tif (cg->running)\n"
    "\t\tset_next_task(cg->rq, cg->p);\n"
    "\tcg->done = true;\n"
    "}\n"
    "\n"
    "void activate_task(struct rq *rq, struct task_struct *p, int flags)\n"
)


def _sched_ext_change_guard_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (KSCHED_H, _GUARD_PROTO_OLD, _GUARD_PROTO_NEW, True),
        (CORE_C, _GUARD_IMPL_OLD, _GUARD_IMPL_NEW, True),
    ])
    return status, detail


# ---------------------------------------------------------------------------
# kernel/sched/ext.c: the sites the preprocessor cannot bridge.
# ---------------------------------------------------------------------------

_EXT_CPUS_OLD = (
    "static void set_cpus_allowed_scx(struct task_struct *p, struct affinity_context *ctx)\n"
    "{\n"
    "\tset_cpus_allowed_common(p, ctx);\n"
)

_EXT_CPUS_NEW = (
    "/*\n"
    " * sailboat_sched_ext_payload_adapt: this baseline's sched_class::\n"
    " * set_cpus_allowed() and set_cpus_allowed_common() take (p, newmask, flags).\n"
    " * struct affinity_context arrived in 6.2 and has no other user in the\n"
    " * payload.  The archived bytes keep the 6.6 shape; this is one of the sites a\n"
    " * preprocessor cannot reach (docs/survey_sched_ext_gap.md section 2c,\n"
    " * class 1).\n"
    " */\n"
    "static void set_cpus_allowed_scx(struct task_struct *p,\n"
    "\t\t\t\t const struct cpumask *newmask, u32 flags)\n"
    "{\n"
    "\tset_cpus_allowed_common(p, newmask, flags);\n"
)

_EXT_PROP_OLD = (
    "\tp->scx->task = p;\n"
    "\tp->sched_prop = 0;\n"
)

_EXT_PROP_NEW = (
    "\tp->scx->task = p;\n"
    "\t/*\n"
    "\t * sailboat_sched_ext_payload_adapt: OPPO's CONFIG_SLIM_SCHED sched_prop\n"
    "\t * reset is dropped.  It is a vendor KABI member of the 6.6 tree with no\n"
    "\t * carrier here, and the payload only ever writes it -- nothing in SCX\n"
    "\t * reads it.  The decision is to delete the write rather than claim a\n"
    "\t * task_struct slot this module does not need\n"
    "\t * (docs/survey_sched_ext_gap.md section 2c, class 3).\n"
    "\t */\n"
)

_EXT_BTF_OLD = (
    "static int bpf_scx_btf_struct_access(struct bpf_verifier_log *log,\n"
    "\t\t\t\t const struct bpf_reg_state *reg,\n"
    "\t\t\t\t int off, int size)\n"
    "{\n"
    "\treturn 0;\n"
    "}\n"
)

_EXT_BTF_NEW = (
    "/*\n"
    " * sailboat_sched_ext_payload_adapt: the payload was written against the\n"
    " * 4-argument 6.6 form; this baseline carries the ACK backport of the\n"
    " * 7-argument one (log, btf, t, off, size, atype, next_btf_id).  Allowing every\n"
    " * access is what both forms express -- the 6.6 tree compiles the same\n"
    " * return 0 (docs/survey_sched_ext_gap.md section 2c, class 7).\n"
    " */\n"
    "static int bpf_scx_btf_struct_access(struct bpf_verifier_log *log,\n"
    "\t\t\t\t const struct btf *btf,\n"
    "\t\t\t\t const struct btf_type *t, int off, int size,\n"
    "\t\t\t\t enum bpf_access_type atype,\n"
    "\t\t\t\t u32 *next_btf_id)\n"
    "{\n"
    "\treturn 0;\n"
    "}\n"
)

_EXT_MEMBER_OLD = (
    "static int bpf_scx_check_member(const struct btf_type *t,\n"
    "\t\t\t\tconst struct btf_member *member,\n"
    "\t\t\t\tconst struct bpf_prog *prog)\n"
    "{\n"
)

_EXT_MEMBER_NEW = (
    "/*\n"
    " * sailboat_sched_ext_payload_adapt: this baseline's struct\n"
    " * bpf_struct_ops::check_member() takes (t, member); the program argument\n"
    " * arrived later and the payload does not use it -- the sleepable test that\n"
    " * would have is still commented out in the body\n"
    " * (docs/survey_sched_ext_gap.md section 2c, class 9).\n"
    " */\n"
    "static int bpf_scx_check_member(const struct btf_type *t,\n"
    "\t\t\t\tconst struct btf_member *member)\n"
    "{\n"
)

_EXT_SYSRQ_OLD = (
    "static void sysrq_handle_sched_ext_reset(u8 key)\n"
    "{\n"
)

_EXT_SYSRQ_NEW = (
    "/*\n"
    " * sailboat_sched_ext_payload_adapt: 5.15's sysrq_key_op::handler takes an\n"
    " * int.  The payload was written against the later u8 signature and does not\n"
    " * read the key (docs/survey_sched_ext_gap.md section 2c, class 10).\n"
    " */\n"
    "static void sysrq_handle_sched_ext_reset(int key)\n"
    "{\n"
)


_EXT_GUARD1_OLD = (
    "\t\t\tSCHED_CHANGE_BLOCK(task_rq(p), p,\n"
    "\t\t\t\t\t   DEQUEUE_SAVE | DEQUEUE_MOVE) {\n"
    "\t\t\t\t/* cycling deq/enq is enough, see above */\n"
    "\t\t\t}\n"
)

_EXT_GUARD1_NEW = (
    "\t\t\t/* sailboat_sched_ext_payload_adapt: SCHED_CHANGE_BLOCK, open-coded */\n"
    "\t\t\t{\n"
    "\t\t\t\tstruct sched_change_guard __cg =\n"
    "\t\t\t\t\tsched_change_guard_init(task_rq(p), p,\n"
    "\t\t\t\t\t\t\t\tDEQUEUE_SAVE | DEQUEUE_MOVE);\n"
    "\n"
    "\t\t\t\t/* cycling deq/enq is enough, see above */\n"
    "\t\t\t\tsched_change_guard_fini(&__cg,\n"
    "\t\t\t\t\t\t\tDEQUEUE_SAVE | DEQUEUE_MOVE);\n"
    "\t\t\t}\n"
)

_EXT_GUARD2_OLD = (
    "\t\tSCHED_CHANGE_BLOCK(rq, p, DEQUEUE_SAVE | DEQUEUE_MOVE |\n"
    "\t\t\t\t   DEQUEUE_NOCLOCK) {\n"
    "\t\t\tp->scx->slice = min_t(u64, p->scx->slice, SCX_SLICE_DFL);\n"
    "\n"
    "\t\t\t__setscheduler_prio(p, p->prio);\n"
    "\t\t}\n"
)

_EXT_GUARD2_NEW = (
    "\t\t/* sailboat_sched_ext_payload_adapt: SCHED_CHANGE_BLOCK, open-coded */\n"
    "\t\t{\n"
    "\t\t\tstruct sched_change_guard __cg =\n"
    "\t\t\t\tsched_change_guard_init(rq, p,\n"
    "\t\t\t\t\t\t\tDEQUEUE_SAVE | DEQUEUE_MOVE |\n"
    "\t\t\t\t\t\t\tDEQUEUE_NOCLOCK);\n"
    "\n"
    "\t\t\tp->scx->slice = min_t(u64, p->scx->slice, SCX_SLICE_DFL);\n"
    "\n"
    "\t\t\t__setscheduler_prio(p, p->prio);\n"
    "\n"
    "\t\t\tsched_change_guard_fini(&__cg,\n"
    "\t\t\t\t\t\tDEQUEUE_SAVE | DEQUEUE_MOVE |\n"
    "\t\t\t\t\t\tDEQUEUE_NOCLOCK);\n"
    "\t\t}\n"
)

_EXT_GUARD3_OLD = (
    "\t\t\tSCHED_CHANGE_BLOCK(rq, p, DEQUEUE_SAVE | DEQUEUE_MOVE |\n"
    "\t\t\t\t\t   DEQUEUE_NOCLOCK) {\n"
    "\t\t\t\tscx_ops_enable_task(p);\n"
    "\t\t\t\t__setscheduler_prio(p, p->prio);\n"
    "\t\t\t}\n"
)

_EXT_GUARD3_NEW = (
    "\t\t\t/* sailboat_sched_ext_payload_adapt: SCHED_CHANGE_BLOCK, open-coded */\n"
    "\t\t\t{\n"
    "\t\t\t\tstruct sched_change_guard __cg =\n"
    "\t\t\t\t\tsched_change_guard_init(rq, p,\n"
    "\t\t\t\t\t\t\t\tDEQUEUE_SAVE | DEQUEUE_MOVE |\n"
    "\t\t\t\t\t\t\t\tDEQUEUE_NOCLOCK);\n"
    "\n"
    "\t\t\t\tscx_ops_enable_task(p);\n"
    "\t\t\t\t__setscheduler_prio(p, p->prio);\n"
    "\n"
    "\t\t\t\tsched_change_guard_fini(&__cg,\n"
    "\t\t\t\t\t\t\tDEQUEUE_SAVE | DEQUEUE_MOVE |\n"
    "\t\t\t\t\t\t\tDEQUEUE_NOCLOCK);\n"
    "\t\t\t}\n"
)


def _sched_ext_payload_adapt_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (EXT_C, _EXT_CPUS_OLD, _EXT_CPUS_NEW, True),
        (EXT_C, _EXT_PROP_OLD, _EXT_PROP_NEW, True),
        (EXT_C, _EXT_BTF_OLD, _EXT_BTF_NEW, True),
        (EXT_C, _EXT_MEMBER_OLD, _EXT_MEMBER_NEW, True),
        (EXT_C, _EXT_SYSRQ_OLD, _EXT_SYSRQ_NEW, True),
        (EXT_C, _EXT_GUARD1_OLD, _EXT_GUARD1_NEW, True),
        (EXT_C, _EXT_GUARD2_OLD, _EXT_GUARD2_NEW, True),
        (EXT_C, _EXT_GUARD3_OLD, _EXT_GUARD3_NEW, True),
    ])
    return status, detail


def build_groups(PatchGroup):
    """Return the three PatchGroup records, in registration order."""
    return [
        PatchGroup(
            "sched_ext_core_visibility",
            ("kernel/sched/core.c's class-change pair made visible to the payload "
             "(a separate translation unit): __setscheduler_prio() loses static, "
             "check_class_changed() loses static inline, and both get prototypes "
             "in kernel/sched/sched.h.  The bodies are untouched, so core.c stays "
             "the only implementation; before this the payload's calls resolved "
             "to nothing and the compiler reported an implicit declaration at "
             "each of the four call sites (docs/survey_sched_ext_gap.md section "
             "2c, classes 5-6)."),
            ["no upstream commit: the helpers are 5.15's, only their linkage "
             "changes"],
            [CORE_C, KSCHED_H],
            _sched_ext_core_visibility_apply,
        ),
        PatchGroup(
            "sched_ext_change_guard",
            ("upstream 6.2's SCHED_CHANGE_BLOCK machinery, re-carried where its "
             "two helpers live: struct sched_change_guard and its prototypes in "
             "kernel/sched/sched.h, the init/fini pair in kernel/sched/core.c "
             "directly after dequeue_task().  It cannot live in the payload's "
             "glue unit: the 6.2 block macro declares its guard in the for-loop "
             "initialiser, which this baseline's -std=gnu89 rejects under clang "
             "as -Wgcc-compat (an error with CONFIG_WERROR), and the guard body "
             "calls dequeue_task()/enqueue_task(), which are static inline in "
             "core.c and carry the vendor trace hooks the payload's switches "
             "must keep calling (docs/survey_sched_ext_gap.md section 2c, "
             "class 4)."),
            ["no upstream commit: the guard is 6.2's, the placement is measured "
             "on this tree (gnu89 plus core.c-local helpers)"],
            [CORE_C, KSCHED_H],
            _sched_ext_change_guard_apply,
        ),
        PatchGroup(
            "sched_ext_payload_adapt",
            ("the marked 5.15 adaptation of the archived kernel/sched/ext.c: the "
             "set_cpus_allowed() callback shape (6.2's struct affinity_context "
             "does not exist here), OPPO's CONFIG_SLIM_SCHED sched_prop reset "
             "(dropped rather than given a task_struct slot), the 7-argument "
             "btf_struct_access() and 2-argument check_member() this baseline "
             "carries from the ACK backport, the int-taking sysrq handler, and "
             "the three SCHED_CHANGE_BLOCK sites open-coded onto the guard "
             "sched_ext_change_guard provides.  The file stays byte-for-byte "
             "archived under files/ with its sha256 pin; these edits are what "
             "the overlay materialises (docs/survey_sched_ext_gap.md section 2c, "
             "classes 1/3/4/7/9/10)."),
            ["no upstream commit: this is the measured 6.6 -> 5.15 interface "
             "delta, not a port"],
            [EXT_C],
            _sched_ext_payload_adapt_apply,
        ),
    ]
