"""Batch 15: the ABK_ABI_PATCH_SUITE EEVDF scheduler family, absorbed.

Two groups, both rewritten as ``required`` anchor steps:

* ``sched_eevdf_pick_logic`` -- ``kernel/sched/fair.c``: the ``abk_eevdf_*``
  helper set plus the rewiring of ``pick_next_entity()``,
  ``reweight_entity()``, ``enqueue_entity()`` / ``dequeue_entity()`` /
  ``place_entity()``, ``check_preempt_tick()``, ``set_next_entity()``,
  ``put_prev_entity()`` and ``entity_tick()`` onto a scan-based EEVDF
  runtime state machine.
* ``sched_eevdf_core_fields`` -- ``include/linux/sched.h``: the three
  ``ANDROID_KABI_USE`` claims in ``struct sched_entity``
  (slot 1 ``u64 deadline``, slot 2 ``u64 min_vruntime``, slot 3 ``s64 vlag``)
  that the fair.c half reads.  Batch 16 released slot 4 (``u64 slice``) back to
  ``ANDROID_KABI_RESERVE(4)``: nothing ever read it.

Provenance.  Every anchor and every replacement below is the payload of
``ABK_ABI_PATCH_SUITE/scripts/abk_feature_porting.py``,
``patch_sched_entity_fields()`` (:454-518, group ``sched_eevdf_core_fields``) and
``patch_sched_pick_logic()`` (:592-1018, group ``sched_eevdf_pick_logic``), the
design of which is documented in that repo's
``docs/feature_porting_eevdf_pid.md``.  The absorption decision is
``docs/survey_suite_absorption.md``; the KABI ownership change is recorded in
``AGENTS.md`` ("Red lines") and ``docs/porting_policy.md``.

What changed in the absorption (why this is not a copy):

1. **``required`` anchors instead of ``if old in text:``.**  The suite guards
   every edit with ``if old in text:`` and then calls ``text.replace(old, new,
   1)``, so an anchor miss is a silent no-op that still reports success.  Here
   every step is ``required=True``: a miss aborts the whole group
   transactionally and the group reports ``blocked_by_missing_anchor``.
2. **An explicit uniqueness probe.**  Before emitting the fair.c steps this
   module counts every ``old`` block in the file and refuses the group
   (``blocked_by_shape``) if any of them occurs more than once.  ``replace_once``
   itself replaces the first occurrence only, so a context that grew a second
   copy in a real tree would otherwise be edited in the wrong place.  Every
   anchor was measured to occur exactly once in the pristine
   ``abk515_ref_{167,178,194,211}`` trees (see the per-anchor table below).
3. **A registration-order inversion.**  The slot claim is gated on the fair.c
   logic really having landed (see item 4), so ``sched_eevdf_pick_logic`` must be
   registered *before* ``sched_eevdf_core_fields`` -- the opposite of the
   suite's own order (``sched_eevdf_core_fields`` was :30, pick logic :33).  The
   suite's gate tested ``"abk_eevdf_" in fair.c`` on a tree where the fields had
   already been claimed by an earlier run; inverting the order makes the gate
   meaningful on a *first* pass.
4. **The core-fields group is an anti-drift gate, not a standalone edit.**  The
   four fields exist only to carry state that ``kernel/sched/fair.c`` maintains.
   ``_eevdf_core_fields_apply()`` therefore reads fair.c and requires the
   module's own helper marker, ``abk_eevdf_refresh_deadline(cfs_rq, se);``,
   ``abk_eevdf_place_entity(cfs_rq, se, initial);`` and
   ``return abk_pick_eevdf(cfs_rq, curr);`` to be present.  If fair.c did not
   carry the logic -- a tree where the pick-logic group was blocked, or where a
   later group reverted it -- the group reports ``blocked_by_shape`` and slots
   1-4 stay ``ANDROID_KABI_RESERVE``.  A half-claimed pair (fields without the
   logic, or logic without the fields) can therefore never be produced.

Feasibility finding (measured, not assumed).  The suite is described as ported
"onto the 6.1 rb-tree layout", so the fair.c anchors were probed against the four
5.15 reference trees before a line was written.  They all exist, in the *same*
shape, because 5.15 already carries the 6.1-era CFS body for every function the
suite touches: ``entity_before()``, ``sched_vslice()``, ``reweight_entity()``,
``place_entity()``, ``enqueue_entity()``, ``dequeue_entity()``,
``check_preempt_tick()``, ``set_next_entity()``, ``pick_next_entity()``,
``put_prev_entity()`` and ``entity_tick()``.  Four shape differences had to be
absorbed, and each is a deliberate *literal* re-anchor rather than the suite's
runtime ``fair_shape_for_tree()`` adaptation:

* the stats helpers are spelled without the ``_fair`` suffix on 5.15
  (``update_stats_dequeue`` / ``update_stats_wait_end`` /
  ``update_stats_wait_start``), where 6.1 uses ``*_fair``;
* 5.15 ``dequeue_entity()`` has no ``int action = UPDATE_TG;`` head, which is
  what the suite's ``_FAIR_6_1_DEQUEUE_ACTION`` substitution removes;
* 5.15 ``place_entity()`` ends with the AOSP
  ``trace_android_rvh_place_entity()`` hook *after* the
  ``max_vruntime()`` assignment, which is what the suite's
  ``_FAIR_5_15_PLACE_TAIL`` substitution appends to;
* ``reweight_entity()`` grew the AOSP ``trace_android_vh_reweight_entity(se);``
  hook between ``update_load_set()`` and the ``CONFIG_SMP`` PELT block in
  5.15.194 (and 5.15.216); 5.15.167/.178 do not have it.  The suite's
  whole-body ``reweight_entity()`` replacement therefore matches neither
  sublevel's body in full -- it would have silently dropped the hook on
  .194/.216.  This module rewrites the function in two steps split at that seam,
  which is correct on all four trees and leaves anything a later sublevel adds
  in the gap untouched.

``fair_shape_for_tree()`` is deliberately **not** reimplemented.  Two reasons.
First, its whole job is to guess which spelling a tree uses; here all four
audited trees use one spelling, and a guess is exactly the silent adaptation this
repo's anchor policy rejects -- an anchor written for the wrong spelling must
fail loudly, not be rewritten into a spelling that happens to match.  Second, it
would make the *emitted* step text depend on the tree, which ``tests/step_audit.py``
cannot audit and which would re-introduce 6.1 spellings the 5.15 compiler
rejects if the two spellings ever coexisted.

Deliberately NOT ported (recorded so it is not relitigated):

* ``sched_eevdf_runtime_state_phase3``.  ``patch_sched_runtime_state_phase3()``
  (:1020-1068) only inserts one comment in front of ``if (queued) {`` in
  ``reweight_entity()`` and then probes for ``DEQUEUE_DELAYED``/``DELAY_DEQUEUE``.
  The real phase-3 semantics already land inside ``patch_sched_pick_logic()``.
  Creating a group for it would be a phantom group whose entire content is a
  comment; ``docs/survey_suite_absorption.md`` §4 reaches the same verdict.
* ``_patch_fair_reweight_compat()`` (:519-590).  It is a regex rewrite of a
  *different* ``reweight_entity()`` body (a fallback for a tree that does not
  match the suite's own ``reweight_old``).  All four 5.15 trees match
  ``reweight_old`` verbatim, so the fallback is unreachable here, and a regex
  substitution is not expressible as this repo's ``(rel, old, new, required)``
  anchor step.  A tree whose ``reweight_entity()`` matches neither form now
  reports ``blocked_by_missing_anchor``/``blocked_by_shape`` instead -- loudly,
  which is the point.
* the ``struct sched_rt_entity`` slot *restore*.  The suite repairs
  ``sched_rt_entity`` slots 1-4 if an earlier run wrongly claimed them there.
  Nothing in this module or any other group in this repo ever writes those
  slots, so the restore step's anchor is absent from a pristine tree; as a
  ``required`` step it could only ever fail the group.  It is moot by
  construction: the only writer that could create that state is
  ABK_ABI_PATCH_SUITE itself, and ``AGENTS.md`` forbids co-injecting it with a
  Batch 15 build.
* ``u64 avg_vruntime()`` is kept exactly as the suite wrote it (non-``static``,
  no prototype).  ``avg_vruntime`` occurs nowhere else in the 5.15 tree
  (0 hits in ``kernel/`` and ``include/``), and ``-Wmissing-prototypes`` is a
  ``W=1``-only flag in 5.15 (``scripts/Makefile.extrawarn:28``), so the suite's
  form is warning-clean at the default ``CONFIG_WERROR=y`` level.  Making it
  ``static`` would be a gratuitous deviation from the absorbed payload.

KABI / ``sizeof(struct sched_entity)``.  ``ANDROID_KABI_USE(n, type)`` expands to
``_ANDROID_KABI_REPLACE(_ANDROID_KABI_RESERVE(n), type)``, i.e. an anonymous
union of the new declaration and ``struct { u64 android_kabi_reserved##n; }``,
with ``__ANDROID_KABI_CHECK_SIZE_ALIGN()`` asserting
``sizeof(struct{type;}) <= sizeof(struct{u64 ...;})`` (8) and equal alignment.
All four replacements are one 8-byte scalar of alignment 8, so the struct is
bit-for-bit unchanged *and the header's own ``_Static_assert`` is the
compile-time proof of it*.  ``CONFIG_ANDROID_KABI_RESERVE`` is ``default y`` with
no dependencies (``drivers/android/Kconfig:77``, sourced unconditionally from
``drivers/Kconfig:206``), so the ``RESERVE`` side is a real ``u64`` on every
baseline.  ``__GENKSYMS__`` keeps the ``RESERVE`` form, so the symbol CRCs are
unchanged too.  The same pattern already compiles in this tree
(``include/linux/hid.h:635``).

Verification performed while writing this module (all of it re-runnable):

* every ``old`` block above was counted in ``abk515_ref_{167,178,194,211}``:
  15/15 anchors occur exactly once per tree and 15/15 ``new`` blocks occur zero
  times, including the two ``reweight_entity()`` halves;
* the same 15 anchors were replayed in registration order against each tree:
  both groups report ``applied``, a second pass reports ``already_present`` and
  is byte-identical, and the ``/* */``, brace and ``#if/#endif`` deltas of both
  files are unchanged;
* the payload was applied to a pristine 5.15.167 tree and
  ``make ARCH=x86_64 kernel/sched/fair.o`` was run with ``CONFIG_SMP=y``,
  ``CONFIG_FAIR_GROUP_SCHED=y``, ``CONFIG_SCHED_DEBUG=y``, ``CONFIG_SCHEDSTATS=y``,
  ``CONFIG_ANDROID_KABI_RESERVE=y`` and ``CONFIG_WERROR=y``: the object builds
  with no diagnostic at all;
* ``sizeof(struct sched_entity)`` was measured with the compiler on both the
  pristine and the patched tree (a file-scope ``char probe[sizeof(struct
  sched_entity)];`` read back with ``nm -S``) under that same config:
  **512 bytes on both**, which is the concrete evidence for the KMI claim above;
* ``update_stats_dequeue_fair`` / ``update_stats_wait_end_fair`` /
  ``update_stats_wait_start_fair`` occur **zero** times in the 5.15
  ``kernel/sched/fair.c`` under any config, so the plain spellings these anchors
  use are not a ``CONFIG_*``-dependent rename;
* the fair.c anchors were compared against every step of the three existing
  ``stable_perf_backport`` fair.c groups and against
  ``batch15_perf_sched_refinements``' fair.c steps: no ``old`` or ``new`` block
  of either side contains the other's.

Runtime caveat inherited from the suite (not a porting defect, recorded so it is
not mistaken for one): the graft does not touch ``__sched_fork()``, so
``se->deadline`` / ``se->vlag`` / ``se->min_vruntime`` begin as a copy of the
parent's ``struct sched_entity`` (zero for ``init_task``).  The
lifecycle closes at ``task_fork_fair()`` -> ``place_entity(cfs_rq, se, 1)``,
which is called unconditionally and overwrites ``deadline``, ``vlag`` and
``slice`` on both of ``abk_eevdf_place_entity()``'s paths, so the inherited
values do not survive a fork.  What *is* inherited is the relative-deadline tag:
a child whose parent had ``ABK_EEVDF_REL_DEADLINE_BIT`` set in
``se->min_vruntime`` takes the relative-deadline branch at fork.  That is the
suite's designed behaviour, not an accident of the re-anchor.

Registration order required by this module (see item 3 and
"Anchor overlap" below): ``sched_eevdf_pick_logic`` first, then
``sched_eevdf_core_fields``, and both **after** every existing
``stable_perf_backport`` group that touches ``kernel/sched/fair.c``.

Anchor overlap with the existing ``stable_perf_backport`` children: none.
``sched_nohz_idle_balance_series`` (``nohz_balancer_kick()``,
``_nohz_idle_balance()``), ``sched_dst_group_allowed_stats``
(``update_sg_wakeup_stats()``), ``sched_lazy_preemption_hooks``
(``#include <trace/hooks/sched.h>``, the ``delta_exec > ideal_runtime`` arm of
``check_preempt_tick()``, the ``CONFIG_SCHED_HRTICK`` arm of ``entity_tick()``,
``wakeup_preempt_entity()``'s ``preempt:`` label) and
``schedutil_smart_policy`` (``cpufreq_schedutil.c``) each rewrite regions
disjoint from every ``old`` block here.  The nearest call is
``sched_lazy_preemption_hooks``: it edits the *head* of ``check_preempt_tick()``
while this module edits the tail, and it edits ``entity_tick()``'s HRTICK branch
above the ``cfs_rq->nr_running > 1`` tail this module anchors on -- no step's
text is produced or consumed by the other.  One *semantic* interaction is worth
recording: the EEVDF preemption point this module adds calls ``resched_curr()``
directly and therefore does not consult
``trace_android_vh_resched_curr_lazy()``; on a tree where the lazy-preemption
group landed, that one preemption path is eager rather than deferred.  It is a
policy overlap, not an anchor overlap, and it is left as-is rather than gated on
another group's output.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

SCHED_H = "include/linux/sched.h"
FAIR_C = "kernel/sched/fair.c"

# Alias the required-flag for readability (the other batch modules use T/F).
T = True

# --- provenance markers and shape probes ------------------------------------
#
# The fair.c marker doubles as the idempotency anchor for the pick-logic group
# and as the "did the logic really land" tell for the slot claim.
EEVDF_FAIR_MARKER = (
    "/* ABK stable_515_backport: scan-based EEVDF runtime-state graft. */"
)

# Present only when the *whole* fair.c half of the family landed.  Each string
# is emitted by a different step of the pick-logic group, so requiring all four
# proves the helpers, the placement hook and the ``pick_next_entity()`` routing
# all landed -- not merely that some earlier step wrote its comment.
_FAIR_LANDED_PROBES = (
    EEVDF_FAIR_MARKER,
    "abk_eevdf_refresh_deadline(cfs_rq, se);",
    "abk_eevdf_place_entity(cfs_rq, se, initial);",
    "return abk_pick_eevdf(cfs_rq, curr);",
)

# The 5.15 fair.c idiom this module is anchored to.  A tree that does not carry
# it is a tree whose anchors were never measured, so the group refuses instead
# of guessing (this is the concrete replacement for the suite's
# fair_shape_for_tree() adaptation -- see the module docstring).
_FAIR_SHAPE_PROBES = (
    ("update_stats_dequeue(cfs_rq, se, flags);",
     "the 5.15 update_stats_dequeue() spelling (6.1 uses *_fair)"),
    ("update_stats_wait_end(cfs_rq, se);",
     "the 5.15 update_stats_wait_end() spelling (6.1 uses *_fair)"),
    ("update_stats_wait_start(cfs_rq, prev);",
     "the 5.15 update_stats_wait_start() spelling (6.1 uses *_fair)"),
    ("trace_android_rvh_place_entity(cfs_rq, se, initial, &vruntime);",
     "the AOSP place_entity() hook that 6.1 does not have"),
    ("static void\nplace_entity(struct cfs_rq *cfs_rq, struct sched_entity *se, int initial)\n{\n",
     "the 5.15 place_entity() definition (7.0 moved its body to place_entity_deadline())"),
)


# ---------------------------------------------------------------------------
# include/linux/sched.h -- struct sched_entity KABI reserve slots 1-4
# ---------------------------------------------------------------------------
#
# The anchor is the whole sched_entity tail, from the CONFIG_SMP sched_avg block
# through the four reserves and the struct's closing brace.  ``struct
# sched_rt_entity`` carries an identical four-line RESERVE run (sched.h:591-594),
# so the sched_avg context is what pins this to sched_entity; a bare
# ``ANDROID_KABI_RESERVE(1..4)`` run would match twice in the file.

_SCHED_H_SE_TAIL = (
    "#ifdef CONFIG_SMP\n"
    "\t/*\n"
    "\t * Per entity load average tracking.\n"
    "\t *\n"
    "\t * Put into separate cache line so it does not\n"
    "\t * collide with read-mostly values above.\n"
    "\t */\n"
    "\tstruct sched_avg\t\tavg;\n"
    "#endif\n"
    "\n"
    "\tANDROID_KABI_RESERVE(1);\n"
    "\tANDROID_KABI_RESERVE(2);\n"
    "\tANDROID_KABI_RESERVE(3);\n"
    "\tANDROID_KABI_RESERVE(4);\n"
    "};"
)

# The pre-release draft the suite upgrades: slots 3 and 4 packed two u64 each.
# It cannot compile (ANDROID_KABI_USE's own static assert rejects a 16-byte
# payload in an 8-byte slot), so it can only exist in a tree a *draft* of this
# payload touched.  It is kept as a named shape rather than as a second step
# because the two forms are mutually exclusive and a required step whose anchor
# cannot exist would fail the group every time.
_SCHED_H_SE_TAIL_BROKEN = (
    "#ifdef CONFIG_SMP\n"
    "\t/*\n"
    "\t * Per entity load average tracking.\n"
    "\t *\n"
    "\t * Put into separate cache line so it does not\n"
    "\t * collide with read-mostly values above.\n"
    "\t */\n"
    "\tstruct sched_avg\t\tavg;\n"
    "#endif\n"
    "\n"
    "\tANDROID_KABI_USE(1, u64 deadline);\n"
    "\tANDROID_KABI_USE(2, u64 min_vruntime);\n"
    "\tANDROID_KABI_USE(3, struct {\n"
    "\t\tu64 min_slice;\n"
    "\t\tu64 max_slice;\n"
    "\t});\n"
    "\tANDROID_KABI_USE(4, struct {\n"
    "\t\ts64 vlag;\n"
    "\t\tu64 slice;\n"
    "\t});\n"
    "};"
)

_SCHED_H_SE_TAIL_NEW = (
    "#ifdef CONFIG_SMP\n"
    "\t/*\n"
    "\t * Per entity load average tracking.\n"
    "\t *\n"
    "\t * Put into separate cache line so it does not\n"
    "\t * collide with read-mostly values above.\n"
    "\t */\n"
    "\tstruct sched_avg\t\tavg;\n"
    "#endif\n"
    "\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: EEVDF scheduling-entity state.  This is the\n"
    "\t * ABK_ABI_PATCH_SUITE feature_porting/sched_eevdf_core_fields payload,\n"
    "\t * absorbed by Batch 15; kernel/sched/fair.c (group\n"
    "\t * sched_eevdf_pick_logic of this same module) is the only consumer.\n"
    "\t *\n"
    "\t * Android KABI reserve slots 1-3 become the three EEVDF fields the\n"
    "\t * fair.c half really reads.  Each replacement is one 8-byte,\n"
    "\t * 8-byte-aligned scalar, so ANDROID_KABI_USE's own size and alignment\n"
    "\t * static asserts both hold and sizeof(struct sched_entity) is\n"
    "\t * unchanged on every baseline.  Batch 16 released slot 4: the suite\n"
    "\t * also claimed it as ``u64 slice``, but a consumer sweep over the full\n"
    "\t * tree found abk_eevdf_slice()'s single write and no reader anywhere,\n"
    "\t * so the slot went back to ANDROID_KABI_RESERVE(4) instead of staying\n"
    "\t * dead frozen-ABI space.\n"
    "\t */\n"
    "\tANDROID_KABI_USE(1, u64 deadline);\n"
    "\tANDROID_KABI_USE(2, u64 min_vruntime);\n"
    "\tANDROID_KABI_USE(3, s64 vlag);\n"
    "\tANDROID_KABI_RESERVE(4);\n"
    "};"
)


# ---------------------------------------------------------------------------
# kernel/sched/fair.c -- anchors as they exist in the pristine 5.15 trees
# ---------------------------------------------------------------------------
#
# Every block below was measured to occur exactly once in abk515_ref_{167,178,
# 194,211} and in the full 5.15.167 tree.  The tabs are the tree's own; a
# mistyped indent shows up as a 0-count anchor in tests/step_audit.py rather
# than as a silent no-op, because every step is required.

# Step 1: the helper set, inserted between sched_vslice() and ``#include
# "pelt.h"``.  sched_slice() (fair.c:681), calc_delta_fair() (fair.c:651) and
# __node_2_se() (fair.c:541) are all defined above this point on 5.15.
_FAIR_VSLICE = (
    "static u64 sched_vslice(struct cfs_rq *cfs_rq, struct sched_entity *se)\n"
    "{\n"
    "\treturn calc_delta_fair(sched_slice(cfs_rq, se), se);\n"
    "}\n"
)

_FAIR_HELPERS = "\n" + """/* ABK stable_515_backport: scan-based EEVDF runtime-state graft. */
/*
 * Absorbed from ABK_ABI_PATCH_SUITE feature_porting/sched_eevdf_pick_logic
 * (phase "scan_based_eevdf_phase2").  The selector keeps the legacy rb-tree
 * ordering and re-derives the EEVDF quantities by scanning the tree, because
 * 5.15 has no augmented cfs_rq (no sum_weight / sum_w_vruntime /
 * zero_vruntime) and the sibling suite defers that augmentation explicitly.
 * The three sched_entity fields it reads (deadline, min_vruntime, vlag) are
 * claimed from the Android KABI reserve slots by the core-fields group of this
 * same module.  Batch 16 dropped the suite's fourth field (``slice``): it was
 * written here and read nowhere.
 */
#define ABK_EEVDF_REL_DEADLINE_BIT (1ULL << 63)
#define ABK_EEVDF_REL_DEADLINE_MASK (ABK_EEVDF_REL_DEADLINE_BIT - 1)

static inline u64 abk_eevdf_slice(struct cfs_rq *cfs_rq, struct sched_entity *se)
{
\tu64 slice = sched_slice(cfs_rq, se);

\treturn slice;
}

static inline u64 abk_eevdf_vslice(struct cfs_rq *cfs_rq, struct sched_entity *se)
{
\tu64 slice = abk_eevdf_slice(cfs_rq, se);

\treturn max_t(u64, calc_delta_fair(slice, se), 1ULL);
}

u64 avg_vruntime(struct cfs_rq *cfs_rq)
{
\tstruct sched_entity *curr = cfs_rq->curr;
\tstruct rb_node *node;
\ts64 weighted = 0;
\tlong total = 0;

\tfor (node = rb_first_cached(&cfs_rq->tasks_timeline); node; node = rb_next(node)) {
\t\tstruct sched_entity *se = __node_2_se(node);
\t\tunsigned long weight = max_t(unsigned long, scale_load_down(se->load.weight), 1UL);

\t\tweighted += (s64)(se->vruntime - cfs_rq->min_vruntime) * weight;
\t\ttotal += weight;
\t}

\tif (curr && curr->on_rq) {
\t\tunsigned long weight = max_t(unsigned long, scale_load_down(curr->load.weight), 1UL);

\t\tweighted += (s64)(curr->vruntime - cfs_rq->min_vruntime) * weight;
\t\ttotal += weight;
\t}

\tif (!total)
\t\treturn cfs_rq->min_vruntime;

\tif (weighted < 0)
\t\tweighted -= total - 1;

\treturn cfs_rq->min_vruntime + div_s64(weighted, total);
}

static inline bool abk_eevdf_has_rel_deadline(const struct sched_entity *se)
{
\treturn se->min_vruntime & ABK_EEVDF_REL_DEADLINE_BIT;
}

static inline u64 abk_eevdf_get_rel_deadline(const struct sched_entity *se)
{
\treturn se->min_vruntime & ABK_EEVDF_REL_DEADLINE_MASK;
}

static inline void abk_eevdf_set_rel_deadline(struct sched_entity *se, u64 deadline)
{
\tse->min_vruntime = ABK_EEVDF_REL_DEADLINE_BIT |
\t\t\t   (deadline & ABK_EEVDF_REL_DEADLINE_MASK);
}

static inline u64 abk_eevdf_take_rel_deadline(struct sched_entity *se)
{
\tu64 deadline = abk_eevdf_get_rel_deadline(se);

\tse->min_vruntime = 0;
\treturn deadline;
}

static inline void abk_eevdf_store_rel_deadline(struct sched_entity *se)
{
\tu64 deadline = 0;

\tif (se->deadline && (s64)(se->deadline - se->vruntime) > 0)
\t\tdeadline = se->deadline - se->vruntime;

\tabk_eevdf_set_rel_deadline(se, deadline);
}

static inline void abk_eevdf_scale_rel_deadline(struct sched_entity *se,
\t\t\t\t\t\tunsigned long old_weight,
\t\t\t\t\t\tunsigned long new_weight)
{
\tu64 deadline;

\tif (!abk_eevdf_has_rel_deadline(se))
\t\treturn;

\tdeadline = abk_eevdf_get_rel_deadline(se);
\tif (deadline)
\t\tdeadline = div_u64(deadline * old_weight, new_weight);

\tabk_eevdf_set_rel_deadline(se, deadline);
}

static inline bool abk_eevdf_entity_before(const struct sched_entity *a,
\t\t\t\t\t      const struct sched_entity *b)
{
\tif (!a)
\t\treturn false;
\tif (!b)
\t\treturn true;

\tif (a->deadline == b->deadline)
\t\treturn (s64)(a->vruntime - b->vruntime) < 0;

\treturn (s64)(a->deadline - b->deadline) < 0;
}

static inline bool abk_eevdf_eligible(struct sched_entity *se, u64 avruntime)
{
\treturn (s64)(avruntime - se->vruntime) >= 0;
}

static unsigned long abk_eevdf_total_weight(struct cfs_rq *cfs_rq)
{
\tstruct sched_entity *curr = cfs_rq->curr;
\tstruct rb_node *node;
\tunsigned long total = 0;

\tfor (node = rb_first_cached(&cfs_rq->tasks_timeline); node; node = rb_next(node)) {
\t\tstruct sched_entity *se = __node_2_se(node);

\t\ttotal += max_t(unsigned long, scale_load_down(se->load.weight), 1UL);
\t}

\tif (curr && curr->on_rq)
\t\ttotal += max_t(unsigned long, scale_load_down(curr->load.weight), 1UL);

\treturn total;
}

static u64 abk_eevdf_max_slice(struct cfs_rq *cfs_rq, struct sched_entity *hint)
{
\tstruct sched_entity *curr = cfs_rq->curr;
\tstruct rb_node *node;
\tu64 max_slice = 0;

\tif (hint)
\t\tmax_slice = max(max_slice, abk_eevdf_slice(cfs_rq, hint));

\tfor (node = rb_first_cached(&cfs_rq->tasks_timeline); node; node = rb_next(node)) {
\t\tstruct sched_entity *se = __node_2_se(node);

\t\tmax_slice = max(max_slice, abk_eevdf_slice(cfs_rq, se));
\t}

\tif (curr && curr->on_rq)
\t\tmax_slice = max(max_slice, abk_eevdf_slice(cfs_rq, curr));

\treturn max_t(u64, max_slice, 1ULL);
}

static s64 abk_eevdf_lag_limit(struct cfs_rq *cfs_rq, struct sched_entity *se)
{
\tu64 max_slice = abk_eevdf_max_slice(cfs_rq, se);

\tmax_slice += sysctl_sched_min_granularity;
\treturn max_t(s64, (s64)calc_delta_fair(max_slice, se), 1LL);
}

static void abk_eevdf_update_lag(struct cfs_rq *cfs_rq, struct sched_entity *se)
{
\ts64 vlag;
\ts64 limit;

\tvlag = (s64)(avg_vruntime(cfs_rq) - se->vruntime);
\tlimit = abk_eevdf_lag_limit(cfs_rq, se);
\tse->vlag = clamp_t(s64, vlag, -limit, limit);
}

static s64 abk_eevdf_preserved_lag(struct cfs_rq *cfs_rq, struct sched_entity *se)
{
\tunsigned long load = abk_eevdf_total_weight(cfs_rq);
\tunsigned long weight = max_t(unsigned long, scale_load_down(se->load.weight), 1UL);
\ts64 lag = se->vlag;
\ts64 limit = abk_eevdf_lag_limit(cfs_rq, se);

\tlag = clamp_t(s64, lag, -limit, limit);
\tif (load)
\t\tlag = div_s64(lag * (load + weight), load);

\treturn lag;
}

static void abk_eevdf_apply_lag_placement(struct cfs_rq *cfs_rq,
\t\t\t\t\t  struct sched_entity *se, s64 lag)
{
\tu64 avruntime = avg_vruntime(cfs_rq);

\tif (lag > 0) {
\t\tu64 delta = min_t(u64, avruntime, (u64)lag);

\t\tse->vruntime = avruntime - delta;
\t} else if (lag < 0) {
\t\tse->vruntime = avruntime + (u64)(-lag);
\t} else {
\t\tse->vruntime = avruntime;
\t}
}

static bool abk_eevdf_refresh_deadline(struct cfs_rq *cfs_rq,
\t\t\t\t       struct sched_entity *se)
{
\tu64 vslice = abk_eevdf_vslice(cfs_rq, se);

\tif (abk_eevdf_has_rel_deadline(se)) {
\t\tu64 rel_deadline = abk_eevdf_take_rel_deadline(se);

\t\tse->deadline = se->vruntime + rel_deadline;
\t}

\tif (se->deadline && (s64)(se->vruntime - se->deadline) < 0)
\t\treturn false;

\tse->deadline = se->vruntime + vslice;
\tavg_vruntime(cfs_rq);
\tabk_eevdf_update_lag(cfs_rq, se);
\treturn true;
}

static void abk_eevdf_place_entity(struct cfs_rq *cfs_rq,
\t\t\t\t struct sched_entity *se, int initial)
{
\tbool rel_deadline = abk_eevdf_has_rel_deadline(se);
\ts64 lag = 0;
\tu64 vslice = abk_eevdf_vslice(cfs_rq, se);

\tif (!initial && (se->vlag || rel_deadline)) {
\t\tlag = abk_eevdf_preserved_lag(cfs_rq, se);
\t\tabk_eevdf_apply_lag_placement(cfs_rq, se, lag);
\t}

\tif (rel_deadline) {
\t\tu64 rel = abk_eevdf_take_rel_deadline(se);

\t\tif (!rel)
\t\t\trel = vslice;
\t\tse->deadline = se->vruntime + rel;
\t\tse->vlag = 0;
\t\treturn;
\t}

\tif (initial)
\t\tvslice = max_t(u64, vslice >> 1, 1ULL);

\tse->deadline = se->vruntime + vslice;
\tse->vlag = 0;
}

static struct sched_entity *abk_pick_eevdf(struct cfs_rq *cfs_rq,
\t\t\t\t\t  struct sched_entity *curr)
{
\tstruct sched_entity *best = NULL;
\tstruct sched_entity *next = cfs_rq->next;
\tstruct rb_node *node;
\tu64 avruntime = avg_vruntime(cfs_rq);

\tif (curr && curr->on_rq) {
\t\tabk_eevdf_refresh_deadline(cfs_rq, curr);
\t\tif (abk_eevdf_eligible(curr, avruntime))
\t\t\tbest = curr;
\t}

\tif (next && next->on_rq) {
\t\tabk_eevdf_refresh_deadline(cfs_rq, next);
\t\tif (abk_eevdf_eligible(next, avruntime) &&
\t\t    abk_eevdf_entity_before(next, best))
\t\t\tbest = next;
\t}

\tfor (node = rb_first_cached(&cfs_rq->tasks_timeline); node; node = rb_next(node)) {
\t\tstruct sched_entity *se = __node_2_se(node);

\t\tabk_eevdf_refresh_deadline(cfs_rq, se);
\t\tif (!abk_eevdf_eligible(se, avruntime))
\t\t\tcontinue;
\t\tif (cfs_rq->skip == se)
\t\t\tcontinue;
\t\tif (abk_eevdf_entity_before(se, best))
\t\t\tbest = se;
\t}

\tif (!best)
\t\tbest = curr && curr->on_rq ? curr : __pick_first_entity(cfs_rq);

\treturn best;
}
"""

_FAIR_VSLICE_NEW = _FAIR_VSLICE + _FAIR_HELPERS

# Step 2: __pick_next_entity() loses its only caller when step 11 routes
# pick_next_entity() through abk_pick_eevdf(), so it needs __maybe_unused or
# -Wall -Wunused-function turns into -Werror (CONFIG_WERROR=y by default).  It
# has no other user anywhere in the tree.
_FAIR_NEXT_PICK = (
    "static struct sched_entity *__pick_next_entity(struct sched_entity *se)\n"
)

_FAIR_NEXT_PICK_NEW = (
    "/* ABK stable_515_backport: __pick_next_entity() lost its only caller when\n"
    " * pick_next_entity() was routed through abk_pick_eevdf(); keep it for the\n"
    " * CONFIG_SCHED_DEBUG surface without tripping -Wunused-function.\n"
    " */\n"
    "static __maybe_unused struct sched_entity *__pick_next_entity(struct sched_entity *se)\n"
)

# Step 3: reweight_entity() (fair.c:3082) calls place_entity() (fair.c:4346), so
# the reweight rewrite needs a forward declaration.  The anchor is the !SMP
# dequeue_load_avg() stub, which on 5.15 sits immediately above reweight_entity.
_FAIR_LOAD_AVG_STUB = (
    "static inline void\n"
    "dequeue_load_avg(struct cfs_rq *cfs_rq, struct sched_entity *se) { }\n"
    "#endif\n"
    "\n"
)

_FAIR_LOAD_AVG_STUB_NEW = (
    "static inline void\n"
    "dequeue_load_avg(struct cfs_rq *cfs_rq, struct sched_entity *se) { }\n"
    "#endif\n"
    "\n"
    "/* ABK stable_515_backport: reweight_entity() now calls place_entity() and\n"
    " * the tree insert/remove helpers, and 5.15 defines reweight_entity() ~1200\n"
    " * lines before place_entity().  __dequeue_entity()/__enqueue_entity() are\n"
    " * already defined above this point.\n"
    " */\n"
    "static void\n"
    "place_entity(struct cfs_rq *cfs_rq, struct sched_entity *se, int initial);\n"
    "\n"
)

# Step 4: reweight_entity().  The suite replaces the whole body in one hunk; on
# 5.15 that hunk cannot be one anchor, because 5.15.194/.216 insert the AOSP
# ``trace_android_vh_reweight_entity(se);`` hook between ``update_load_set()``
# and the CONFIG_SMP PELT block (5.15.167/.178 do not have it).  The rewrite is
# therefore split at that seam into a head and a tail step, so the hook -- and
# anything else a later sublevel adds in the same gap -- is left exactly where
# the tree put it.  This also removes the need for the suite's
# ``_patch_fair_reweight_compat()`` regex fallback (see the module docstring).
_FAIR_REWEIGHT_HEAD = (
    "static void reweight_entity(struct cfs_rq *cfs_rq, struct sched_entity *se,\n"
    "\t\t\t    unsigned long weight)\n"
    "{\n"
    "\tif (se->on_rq) {\n"
    "\t\t/* commit outstanding execution time */\n"
    "\t\tif (cfs_rq->curr == se)\n"
    "\t\t\tupdate_curr(cfs_rq);\n"
    "\t\tupdate_load_sub(&cfs_rq->load, se->load.weight);\n"
    "\t}\n"
    "\tdequeue_load_avg(cfs_rq, se);\n"
    "\n"
    "\tupdate_load_set(&se->load, weight);\n"
)

_FAIR_REWEIGHT_HEAD_NEW = (
    "static void reweight_entity(struct cfs_rq *cfs_rq, struct sched_entity *se,\n"
    "\t\t\t    unsigned long weight)\n"
    "{\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: a weight change preserves the entity's lag\n"
    "\t * and its remaining relative deadline instead of resetting them, on\n"
    "\t * both the current and the queued path.  Lag scales with the weight\n"
    "\t * ratio and the entity is re-placed at the end, which is the phase-2\n"
    "\t * runtime-state behaviour the sibling suite's phase-3 marker only\n"
    "\t * annotates.\n"
    "\t */\n"
    "\tbool curr = cfs_rq->curr == se;\n"
    "\tbool queued = se->on_rq;\n"
    "\tunsigned long old_weight = max_t(unsigned long, se->load.weight, 1UL);\n"
    "\tunsigned long new_weight = max_t(unsigned long, weight, 1UL);\n"
    "\n"
    "\tif (queued) {\n"
    "\t\t/* commit outstanding execution time before preserving lag/deadline */\n"
    "\t\tif (curr)\n"
    "\t\t\tupdate_curr(cfs_rq);\n"
    "\t\tabk_eevdf_update_lag(cfs_rq, se);\n"
    "\t\tabk_eevdf_store_rel_deadline(se);\n"
    "\t\tif (!curr)\n"
    "\t\t\t__dequeue_entity(cfs_rq, se);\n"
    "\t\tupdate_load_sub(&cfs_rq->load, se->load.weight);\n"
    "\t}\n"
    "\tdequeue_load_avg(cfs_rq, se);\n"
    "\n"
    "\tse->vlag = div_s64(se->vlag * (s64)old_weight, new_weight);\n"
    "\tabk_eevdf_scale_rel_deadline(se, old_weight, new_weight);\n"
    "\tupdate_load_set(&se->load, weight);\n"
)

_FAIR_REWEIGHT_TAIL = (
    "\tenqueue_load_avg(cfs_rq, se);\n"
    "\tif (se->on_rq)\n"
    "\t\tupdate_load_add(&cfs_rq->load, se->load.weight);\n"
    "\n"
    "}\n"
)

_FAIR_REWEIGHT_TAIL_NEW = (
    "\tenqueue_load_avg(cfs_rq, se);\n"
    "\tif (queued) {\n"
    "\t\tplace_entity(cfs_rq, se, 0);\n"
    "\t\tupdate_load_add(&cfs_rq->load, se->load.weight);\n"
    "\t\tif (!curr)\n"
    "\t\t\t__enqueue_entity(cfs_rq, se);\n"
    "\t}\n"
    "\n"
    "}\n"
)

# Step 5: enqueue_entity() must re-place an entity that carries preserved state
# even when it is not a wakeup (e.g. a requeued one).
_FAIR_ENQUEUE_PLACE = (
    "\tif (flags & ENQUEUE_WAKEUP)\n"
    "\t\tplace_entity(cfs_rq, se, 0);\n"
)

_FAIR_ENQUEUE_PLACE_NEW = (
    "\t/* ABK stable_515_backport: an entity that still carries lag or a\n"
    "\t * relative deadline is re-placed even on a non-wakeup enqueue. */\n"
    "\tif ((flags & ENQUEUE_WAKEUP) || abk_eevdf_has_rel_deadline(se) || se->vlag)\n"
    "\t\tplace_entity(cfs_rq, se, 0);\n"
)

# Step 6: dequeue_entity() head.  5.15 has no ``int action = UPDATE_TG;`` here
# (the suite's _FAIR_6_1_DEQUEUE_ACTION substitution removes it from the 6.1
# form), so the anchor is the definition line plus the opening brace.
_FAIR_DEQUEUE_HEAD = (
    "static void\n"
    "dequeue_entity(struct cfs_rq *cfs_rq, struct sched_entity *se, int flags)\n"
    "{\n"
)

_FAIR_DEQUEUE_HEAD_NEW = (
    "static void\n"
    "dequeue_entity(struct cfs_rq *cfs_rq, struct sched_entity *se, int flags)\n"
    "{\n"
    "\t/* ABK stable_515_backport: DEQUEUE_SLEEP decides whether the entity\n"
    "\t * keeps its relative deadline (sleep) or is normalized into the\n"
    "\t * migrated/exit form. */\n"
    "\tbool sleep = flags & DEQUEUE_SLEEP;\n"
)

# Step 7: capture lag and the relative deadline while the entity is still in the
# tree.  The anchor is the 5.15 ``update_stats_dequeue`` spelling.
_FAIR_DEQUEUE_BODY = (
    "\tupdate_stats_dequeue(cfs_rq, se, flags);\n"
    "\n"
    "\tclear_buddies(cfs_rq, se);\n"
    "\n"
    "\tif (se != cfs_rq->curr)\n"
    "\t\t__dequeue_entity(cfs_rq, se);\n"
)

_FAIR_DEQUEUE_BODY_NEW = (
    "\tupdate_stats_dequeue(cfs_rq, se, flags);\n"
    "\n"
    "\tclear_buddies(cfs_rq, se);\n"
    "\t/* ABK stable_515_backport: snapshot lag before the entity leaves the\n"
    "\t * tree; a sleeping entity additionally keeps its remaining relative\n"
    "\t * deadline for the next placement. */\n"
    "\tabk_eevdf_update_lag(cfs_rq, se);\n"
    "\tif (!sleep)\n"
    "\t\tabk_eevdf_store_rel_deadline(se);\n"
    "\n"
    "\tif (se != cfs_rq->curr)\n"
    "\t\t__dequeue_entity(cfs_rq, se);\n"
)

# Step 8: place_entity() tail.  The anchor carries the AOSP place hook, which is
# what distinguishes the 5.15 shape from the 6.1 one (the suite's
# _FAIR_5_15_PLACE_TAIL substitution appends it at runtime; here it is literal).
_FAIR_PLACE_TAIL = (
    "\tif (entity_is_long_sleeper(se))\n"
    "\t\tse->vruntime = vruntime;\n"
    "\telse\n"
    "\t\tse->vruntime = max_vruntime(se->vruntime, vruntime);\n"
    "\ttrace_android_rvh_place_entity(cfs_rq, se, initial, &vruntime);\n"
    "}\n"
)

_FAIR_PLACE_TAIL_NEW = (
    "\tif (entity_is_long_sleeper(se))\n"
    "\t\tse->vruntime = vruntime;\n"
    "\telse\n"
    "\t\tse->vruntime = max_vruntime(se->vruntime, vruntime);\n"
    "\ttrace_android_rvh_place_entity(cfs_rq, se, initial, &vruntime);\n"
    "\t/* ABK stable_515_backport: the scan-based EEVDF placement supersedes\n"
    "\t * the CFS min_vruntime pull above -- it places the entity at the\n"
    "\t * weighted average vruntime offset by its preserved lag, then hands it\n"
    "\t * a slice worth of deadline. */\n"
    "\tabk_eevdf_place_entity(cfs_rq, se, initial);\n"
    "}\n"
)

# Step 9: check_preempt_tick() tail.  The lazy-preemption group edits the head of
# this function, not this block.
_FAIR_PREEMPT = (
    "\tse = __pick_first_entity(cfs_rq);\n"
    "\tdelta = curr->vruntime - se->vruntime;\n"
    "\n"
    "\tif (delta < 0)\n"
    "\t\treturn;\n"
    "\n"
    "\tif (delta > ideal_runtime)\n"
    "\t\tresched_curr(rq_of(cfs_rq));\n"
    "}\n"
)

_FAIR_PREEMPT_NEW = (
    "\t/* ABK stable_515_backport: the EEVDF selector decides preemption\n"
    "\t * first; the slot-based check below stays as the fallback. */\n"
    "\tse = abk_pick_eevdf(cfs_rq, curr);\n"
    "\tif (se && se != curr && abk_eevdf_entity_before(se, curr)) {\n"
    "\t\tresched_curr(rq_of(cfs_rq));\n"
    "\t\treturn;\n"
    "\t}\n"
    "\n"
    "\tse = __pick_first_entity(cfs_rq);\n"
    "\tdelta = curr->vruntime - se->vruntime;\n"
    "\n"
    "\tif (delta < 0)\n"
    "\t\treturn;\n"
    "\n"
    "\tif (delta > ideal_runtime)\n"
    "\t\tresched_curr(rq_of(cfs_rq));\n"
    "}\n"
)

# Step 10: set_next_entity().  The 5.15 spelling is update_stats_wait_end (no
# _fair suffix).
_FAIR_SET_NEXT = (
    "\t\tupdate_stats_wait_end(cfs_rq, se);\n"
    "\t\t__dequeue_entity(cfs_rq, se);\n"
    "\t\tupdate_load_avg(cfs_rq, se, UPDATE_TG);\n"
)

_FAIR_SET_NEXT_NEW = (
    "\t\tupdate_stats_wait_end(cfs_rq, se);\n"
    "\t\t/* ABK stable_515_backport: closing boundary of the slice\n"
    "\t\t * lifecycle -- refresh the deadline and the lag before the entity\n"
    "\t\t * becomes current. */\n"
    "\t\tabk_eevdf_refresh_deadline(cfs_rq, se);\n"
    "\t\tabk_eevdf_update_lag(cfs_rq, se);\n"
    "\t\t__dequeue_entity(cfs_rq, se);\n"
    "\t\tupdate_load_avg(cfs_rq, se, UPDATE_TG);\n"
)

# Step 11: pick_next_entity().  Matches the pristine 5.15 body verbatim,
# including the AOSP vendor hook.
_FAIR_PICK = (
    "static struct sched_entity *\n"
    "pick_next_entity(struct cfs_rq *cfs_rq, struct sched_entity *curr)\n"
    "{\n"
    "\tstruct sched_entity *left = __pick_first_entity(cfs_rq);\n"
    "\tstruct sched_entity *se = NULL;\n"
    "\n"
    "\ttrace_android_rvh_pick_next_entity(cfs_rq, curr, &se);\n"
    "\tif (se)\n"
    "\t\tgoto done;\n"
    "\n"
    "\t/*\n"
    "\t * If curr is set we have to see if its left of the leftmost entity\n"
    "\t * still in the tree, provided there was anything in the tree at all.\n"
    "\t */\n"
    "\tif (!left || (curr && entity_before(curr, left)))\n"
    "\t\tleft = curr;\n"
    "\n"
    "\tse = left; /* ideally we run the leftmost entity */\n"
    "\n"
    "\t/*\n"
    "\t * Avoid running the skip buddy, if running something else can\n"
    "\t * be done without getting too unfair.\n"
    "\t */\n"
    "\tif (cfs_rq->skip && cfs_rq->skip == se) {\n"
    "\t\tstruct sched_entity *second;\n"
    "\n"
    "\t\tif (se == curr) {\n"
    "\t\t\tsecond = __pick_first_entity(cfs_rq);\n"
    "\t\t} else {\n"
    "\t\t\tsecond = __pick_next_entity(se);\n"
    "\t\t\tif (!second || (curr && entity_before(curr, second)))\n"
    "\t\t\t\tsecond = curr;\n"
    "\t\t}\n"
    "\n"
    "\t\tif (second && wakeup_preempt_entity(second, left) < 1)\n"
    "\t\t\tse = second;\n"
    "\t}\n"
    "\n"
    "\tif (cfs_rq->next && wakeup_preempt_entity(cfs_rq->next, left) < 1) {\n"
    "\t\t/*\n"
    "\t\t * Someone really wants this to run. If it's not unfair, run it.\n"
    "\t\t */\n"
    "\t\tse = cfs_rq->next;\n"
    "\t} else if (cfs_rq->last && wakeup_preempt_entity(cfs_rq->last, left) < 1) {\n"
    "\t\t/*\n"
    "\t\t * Prefer last buddy, try to return the CPU to a preempted task.\n"
    "\t\t */\n"
    "\t\tse = cfs_rq->last;\n"
    "\t}\n"
    "\n"
    "done:\n"
    "\treturn se;\n"
    "}\n"
)

_FAIR_PICK_NEW = (
    "static struct sched_entity *\n"
    "pick_next_entity(struct cfs_rq *cfs_rq, struct sched_entity *curr)\n"
    "{\n"
    "\tstruct sched_entity *se = NULL;\n"
    "\n"
    "\t/* ABK stable_515_backport: the AOSP vendor hook keeps the first\n"
    "\t * word; when it does not select, the scan-based EEVDF selector owns\n"
    "\t * the choice (buddy/skip handling now lives inside abk_pick_eevdf, so\n"
    "\t * the legacy left/next/last/skip ladder is gone). */\n"
    "\ttrace_android_rvh_pick_next_entity(cfs_rq, curr, &se);\n"
    "\tif (se)\n"
    "\t\treturn se;\n"
    "\n"
    "\treturn abk_pick_eevdf(cfs_rq, curr);\n"
    "}\n"
)

# Step 12: put_prev_entity().
_FAIR_PUT_PREV = (
    "\tif (prev->on_rq) {\n"
    "\t\tupdate_stats_wait_start(cfs_rq, prev);\n"
    "\t\t/* Put 'current' back into the tree. */\n"
    "\t\t__enqueue_entity(cfs_rq, prev);\n"
    "\t\t/* in !on_rq case, update occurred at dequeue */\n"
    "\t\tupdate_load_avg(cfs_rq, prev, 0);\n"
    "\t}\n"
)

_FAIR_PUT_PREV_NEW = (
    "\tif (prev->on_rq) {\n"
    "\t\tupdate_stats_wait_start(cfs_rq, prev);\n"
    "\t\t/* ABK stable_515_backport: the preempted entity re-enters the\n"
    "\t\t * tree only after its deadline and lag are refreshed. */\n"
    "\t\tabk_eevdf_refresh_deadline(cfs_rq, prev);\n"
    "\t\tabk_eevdf_update_lag(cfs_rq, prev);\n"
    "\t\t/* Put 'current' back into the tree. */\n"
    "\t\t__enqueue_entity(cfs_rq, prev);\n"
    "\t\t/* in !on_rq case, update occurred at dequeue */\n"
    "\t\tupdate_load_avg(cfs_rq, prev, 0);\n"
    "\t}\n"
)

# Step 13: entity_tick() tail.  The lazy-preemption group edits the
# CONFIG_SCHED_HRTICK branch above this block, not this one.
_FAIR_TICK = (
    "\tif (cfs_rq->nr_running > 1)\n"
    "\t\tcheck_preempt_tick(cfs_rq, curr);\n"
    "\ttrace_android_rvh_entity_tick(cfs_rq, curr);\n"
    "}\n"
)

_FAIR_TICK_NEW = (
    "\t/* ABK stable_515_backport: an expired slice at tick time clears the\n"
    "\t * buddies and reschedules through the unified slice lifecycle. */\n"
    "\tif (abk_eevdf_refresh_deadline(cfs_rq, curr)) {\n"
    "\t\tclear_buddies(cfs_rq, curr);\n"
    "\t\tresched_curr(rq_of(cfs_rq));\n"
    "\t}\n"
    "\tabk_eevdf_update_lag(cfs_rq, curr);\n"
    "\n"
    "\tif (cfs_rq->nr_running > 1)\n"
    "\t\tcheck_preempt_tick(cfs_rq, curr);\n"
    "\ttrace_android_rvh_entity_tick(cfs_rq, curr);\n"
    "}\n"
)


# (label, pristine ``old`` block) for the uniqueness probe.  The labels are the
# report text when a real tree turns out to carry a context twice.
_FAIR_ANCHORS = (
    ("sched_vslice() helper insert", _FAIR_VSLICE),
    ("__pick_next_entity() declaration", _FAIR_NEXT_PICK),
    ("dequeue_load_avg() stub", _FAIR_LOAD_AVG_STUB),
    ("reweight_entity() head", _FAIR_REWEIGHT_HEAD),
    ("reweight_entity() tail", _FAIR_REWEIGHT_TAIL),
    ("enqueue_entity() placement gate", _FAIR_ENQUEUE_PLACE),
    ("dequeue_entity() head", _FAIR_DEQUEUE_HEAD),
    ("dequeue_entity() body", _FAIR_DEQUEUE_BODY),
    ("place_entity() tail", _FAIR_PLACE_TAIL),
    ("check_preempt_tick() tail", _FAIR_PREEMPT),
    ("set_next_entity() body", _FAIR_SET_NEXT),
    ("pick_next_entity() body", _FAIR_PICK),
    ("put_prev_entity() body", _FAIR_PUT_PREV),
    ("entity_tick() tail", _FAIR_TICK),
)


def build_pick_logic_steps():
    """``sched_eevdf_pick_logic``: 14 ordered, all-required steps.

    Order matters in three places, and the steps are not split across groups:

    * step 1 must precede every other step -- steps 4-14 call the helpers it
      inserts;
    * step 3 (the ``place_entity()`` forward declaration) must precede step 4,
      which calls ``place_entity()`` from ``reweight_entity()``;
    * step 6 introduces ``sleep``, which step 7 consumes; step 4 introduces
      ``queued`` and ``curr``, which step 5 consumes.

    Every step's ``new`` block is textually distinct from every other step's, so
    no step can satisfy another's idempotency check (``replace_once`` tests the
    replacement first) -- the failure mode ``docs/group_recipe.md`` calls trap 2.
    """
    return [
        (FAIR_C, _FAIR_VSLICE, _FAIR_VSLICE_NEW, T),
        (FAIR_C, _FAIR_NEXT_PICK, _FAIR_NEXT_PICK_NEW, T),
        (FAIR_C, _FAIR_LOAD_AVG_STUB, _FAIR_LOAD_AVG_STUB_NEW, T),
        (FAIR_C, _FAIR_REWEIGHT_HEAD, _FAIR_REWEIGHT_HEAD_NEW, T),
        (FAIR_C, _FAIR_REWEIGHT_TAIL, _FAIR_REWEIGHT_TAIL_NEW, T),
        (FAIR_C, _FAIR_ENQUEUE_PLACE, _FAIR_ENQUEUE_PLACE_NEW, T),
        (FAIR_C, _FAIR_DEQUEUE_HEAD, _FAIR_DEQUEUE_HEAD_NEW, T),
        (FAIR_C, _FAIR_DEQUEUE_BODY, _FAIR_DEQUEUE_BODY_NEW, T),
        (FAIR_C, _FAIR_PLACE_TAIL, _FAIR_PLACE_TAIL_NEW, T),
        (FAIR_C, _FAIR_PREEMPT, _FAIR_PREEMPT_NEW, T),
        (FAIR_C, _FAIR_SET_NEXT, _FAIR_SET_NEXT_NEW, T),
        (FAIR_C, _FAIR_PICK, _FAIR_PICK_NEW, T),
        (FAIR_C, _FAIR_PUT_PREV, _FAIR_PUT_PREV_NEW, T),
        (FAIR_C, _FAIR_TICK, _FAIR_TICK_NEW, T),
    ]


def build_core_fields_steps(text):
    """``sched_eevdf_core_fields``: exactly one step, for the shape ``text`` has.

    Three mutually exclusive shapes, so the selection happens here and the
    emitted step is ``required`` either way:

    * the claim is already in place -> ``old == new`` (reported already_present);
    * the pre-release draft -> upgraded;
    * the pristine four-reserve run -> claimed.

    Returns ``None`` for a shape none of the three describe; the caller reports
    ``blocked_by_shape`` rather than guessing which reserve run to rewrite.
    """
    if _SCHED_H_SE_TAIL_NEW in text:
        return [(SCHED_H, _SCHED_H_SE_TAIL_NEW, _SCHED_H_SE_TAIL_NEW, T)]
    if _SCHED_H_SE_TAIL in text:
        return [(SCHED_H, _SCHED_H_SE_TAIL, _SCHED_H_SE_TAIL_NEW, T)]
    if _SCHED_H_SE_TAIL_BROKEN in text:
        return [(SCHED_H, _SCHED_H_SE_TAIL_BROKEN, _SCHED_H_SE_TAIL_NEW, T)]
    return None


def _occurrences(text, block):
    """Occurrence count of ``block`` in ``text``, EOL-insensitively."""
    return text.replace("\r\n", "\n").count(block.replace("\r\n", "\n"))


def _eevdf_core_fields_apply(ctx):
    try:
        text = ctx.read(SCHED_H)
    except FileNotFoundError:
        return "blocked_by_shape", f"{SCHED_H}: file absent"

    # Anti-drift gate.  The three slots encode state that only the fair.c half of
    # this family reads; claiming them without the logic would leave dead members
    # in a frozen-struct ABI.  Require the *payload* -- the marker, the
    # deadline refresh, the placement hook and the selector routing -- not just
    # the marker, because the marker alone is written by the first step of the
    # pick-logic group.
    try:
        fair = ctx.read(FAIR_C)
    except FileNotFoundError:
        return "blocked_by_shape", (
            f"{FAIR_C}: file absent, so the EEVDF fair.c half cannot have "
            "landed; sched_entity reserve slots stay ANDROID_KABI_RESERVE"
        )
    absent = [probe for probe in _FAIR_LANDED_PROBES if probe not in fair]
    if absent:
        return "blocked_by_shape", (
            f"{FAIR_C} does not carry the EEVDF payload "
            f"({len(absent)}/{len(_FAIR_LANDED_PROBES)} probe(s) missing, "
            f"first: {absent[0]!r}); sched_entity reserve slots stay "
            "ANDROID_KABI_RESERVE so the KABI slots are never claimed without "
            "the scheduler logic that consumes them"
        )

    steps = build_core_fields_steps(text)
    if steps is None:
        return "blocked_by_shape", (
            f"{SCHED_H}: struct sched_entity reserve slots 1-4 are in none of "
            "the three recognised shapes (pristine ANDROID_KABI_RESERVE(1..4) "
            "run, the pre-release packed draft, the claimed 1-3 form)"
        )
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _sched_eevdf_pick_logic_apply(ctx):
    try:
        text = ctx.read(FAIR_C)
    except FileNotFoundError:
        return "blocked_by_shape", f"{FAIR_C}: file absent"

    # Shape probe: the three 5.15-vs-6.1 differences this module re-anchors.
    missing = [why for probe, why in _FAIR_SHAPE_PROBES if probe not in text]
    if missing:
        return "blocked_by_shape", (
            f"{FAIR_C} is not the 5.15 shape this port was measured against; "
            f"missing: {'; '.join(missing)}"
        )

    # Uniqueness probe (the suite had none).  replace_once edits the first
    # occurrence only, so an anchor that a real tree carries twice would be
    # edited in the wrong place while still reporting applied.  Anchors that are
    # legitimately gone (an already-applied tree) are left to apply_steps, which
    # reports them per step.
    ambiguous = [
        (label, count) for label, block in _FAIR_ANCHORS
        for count in (_occurrences(text, block),) if count > 1
    ]
    if ambiguous:
        return "blocked_by_shape", (
            f"{FAIR_C}: ambiguous anchor(s), each must occur exactly once: "
            + ", ".join(f"{label} x{count}" for label, count in ambiguous)
        )

    status, _results, detail = apply_steps(ctx, build_pick_logic_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the two PatchGroup records, in registration order.

    ``sched_eevdf_pick_logic`` **must** be registered before
    ``sched_eevdf_core_fields``: the slot claim is gated on fair.c carrying the
    logic, which only holds once the pick-logic group has run in the same
    invocation (see the module docstring, item 3).  Both must come after every
    existing ``stable_perf_backport`` group that touches ``kernel/sched/fair.c``
    (``sched_nohz_idle_balance_series``, ``sched_dst_group_allowed_stats``,
    ``sched_lazy_preemption_hooks``); no anchor is shared, so the order is a
    safety margin rather than a correctness requirement.
    """
    return [
        PatchGroup(
            "sched_eevdf_pick_logic",
            "scan-based EEVDF selection and runtime-state maintenance on the "
            "5.15 rb-tree layout: abk_eevdf_* helper set plus rewiring of "
            "pick_next_entity/reweight_entity/enqueue_entity/dequeue_entity/"
            "place_entity/check_preempt_tick/set_next_entity/put_prev_entity/"
            "entity_tick (ABK_ABI_PATCH_SUITE feature_porting/"
            "sched_eevdf_pick_logic, absorbed by Batch 15)",
            [
                "ABK_ABI_PATCH_SUITE feature_porting/sched_eevdf_pick_logic "
                "(scan_based_eevdf_phase2)",
                "design: ABK_ABI_PATCH_SUITE/docs/feature_porting_eevdf_pid.md",
                "absorption: docs/survey_suite_absorption.md",
            ],
            [FAIR_C],
            _sched_eevdf_pick_logic_apply,
        ),
        PatchGroup(
            "sched_eevdf_core_fields",
            "claim Android KABI reserve slots 1-3 of struct sched_entity for the "
            "EEVDF fields the fair.c half reads (u64 deadline, u64 min_vruntime, "
            "s64 vlag); sizeof(struct sched_entity) is unchanged and the slots "
            "stay ANDROID_KABI_RESERVE unless the fair.c logic landed",
            [
                "ABK_ABI_PATCH_SUITE feature_porting/sched_eevdf_core_fields",
                "KABI ownership: AGENTS.md red lines (Batch 15), "
                "docs/porting_policy.md \"Suite absorption\"",
            ],
            [SCHED_H],
            _eevdf_core_fields_apply,
        ),
    ]
