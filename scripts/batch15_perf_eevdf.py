"""Batch 15: the ABK_ABI_PATCH_SUITE EEVDF scheduler family, absorbed.
Batch 28: the same family rebuilt onto the upstream data structure and policy.

Three groups, all rewritten as ``required`` anchor steps:

* ``sched_eevdf_pick_logic`` -- ``kernel/sched/fair.c``: the ``abk_eevdf_*``
  helper set, the ``cfs_rq`` virtual-time accumulators that feed it, and the
  rewiring of ``update_curr()``, ``update_min_vruntime()``,
  ``__enqueue_entity()`` / ``__dequeue_entity()``, ``pick_next_entity()``,
  ``reweight_entity()``, ``enqueue_entity()`` / ``dequeue_entity()`` /
  ``place_entity()``, ``check_preempt_tick()``, ``check_preempt_wakeup()``,
  ``set_next_entity()`` and ``yield_task_fair()``.
* ``sched_eevdf_core_fields`` -- ``include/linux/sched.h``: the four
  ``ANDROID_KABI_USE`` claims in ``struct sched_entity`` (slot 1 ``u64
  deadline``, slot 2 ``u64 min_vruntime``, slot 3 ``s64 vlag``, slot 4 ``u64
  slice``) that the fair.c half reads.
* ``sched_eevdf_modern_fields`` -- ``kernel/sched/sched.h`` (the two
  ``struct cfs_rq`` accumulators) and ``kernel/sched/features.h`` (the
  ``RUN_TO_PARITY`` / ``PREEMPT_SHORT`` switches).

Provenance.  Every anchor and every replacement of the Batch 15 payload is the
payload of ``ABK_ABI_PATCH_SUITE/scripts/abk_feature_porting.py``,
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
   copy in a real tree would otherwise be edited in the wrong place.
3. **A registration-order inversion.**  The slot claim is gated on the fair.c
   logic really having landed (see item 4), so ``sched_eevdf_pick_logic`` must be
   registered *before* ``sched_eevdf_core_fields`` -- the opposite of the
   suite's own order.  The suite's gate tested ``"abk_eevdf_" in fair.c`` on a
   tree where the fields had already been claimed by an earlier run; inverting
   the order makes the gate meaningful on a *first* pass.
4. **The core-fields group is an anti-drift gate, not a standalone edit.**  The
   four fields exist only to carry state that ``kernel/sched/fair.c`` maintains.
   ``_eevdf_core_fields_apply()`` therefore reads fair.c and requires the
   module's own helper marker plus five call sites (the accumulator, the
   deadline refresh in ``update_curr()``, the placement hook, the selector
   routing and the run-to-parity stash).  If fair.c did not carry the logic --
   a tree where the pick-logic group was blocked, or where a later group
   reverted it -- the group reports ``blocked_by_shape`` and slots 1-4 stay
   ``ANDROID_KABI_RESERVE``.  A half-claimed pair (fields without the logic, or
   logic without the fields) can therefore never be produced.

Feasibility finding (measured, not assumed).  The suite is described as ported
"onto the 6.1 rb-tree layout", so the fair.c anchors were probed against the four
5.15 reference trees before a line was written.  They all exist, in the *same*
shape, because 5.15 already carries the 6.1-era CFS body for every function the
suite touches: ``entity_before()``, ``sched_vslice()``, ``reweight_entity()``,
``place_entity()``, ``enqueue_entity()``, ``dequeue_entity()``,
``check_preempt_tick()``, ``set_next_entity()``, ``pick_next_entity()`` and
``entity_tick()``.  Four shape differences had to be absorbed, and each is a
deliberate *literal* re-anchor rather than the suite's runtime
``fair_shape_for_tree()`` adaptation:

* the stats helpers are spelled without the ``_fair`` suffix on 5.15
  (``update_stats_dequeue`` / ``update_stats_wait_end`` /
  ``update_stats_wait_start``), where 6.1 uses ``*_fair``;
* 5.15 ``dequeue_entity()`` has no ``int action = UPDATE_TG;`` head, which is
  what the suite's ``_FAIR_6_1_DEQUEUE_ACTION`` substitution removes;
* 5.15 ``place_entity()`` ends with the AOSP
  ``trace_android_rvh_place_entity()`` hook *after* the
  ``max_vruntime()`` assignment;
* ``reweight_entity()`` grew the AOSP ``trace_android_vh_reweight_entity(se);``
  hook between ``update_load_set()`` and the ``CONFIG_SMP`` PELT block in
  5.15.194 (and 5.15.216); 5.15.167/.178 do not have it.  The suite's
  whole-body ``reweight_entity()`` replacement therefore matches neither
  sublevel's body in full -- it would have silently dropped the hook on
  .194/.216.  This module rewrites the function in two steps split at that seam,
  which is correct on all four trees and leaves anything a later sublevel adds
  in the gap untouched.

--------------------------------------------------------------------------
Batch 28: what the 6.x EEVDF work changed, and why this payload was rebuilt
--------------------------------------------------------------------------

The Batch 15 payload was a *reconstruction* of EEVDF, not EEVDF.  It kept the
legacy rb-tree ordering and re-derived every EEVDF quantity by scanning the
tree, because 5.15 has no ``cfs_rq`` accumulator.  That made the selector
O(n^2)-O(n^3) -- ``abk_eevdf_refresh_deadline()`` was called per tree node and
each call itself walked the whole tree twice -- on every context switch and
every tick, and it *mutated* ``deadline`` / ``vlag`` / ``vruntime`` as a side
effect of merely choosing who runs.

``docs/survey_eevdf_gap.md`` is the difference audit against Linux 6.6 -> 7.3.
Batch 28 lands its S and A tiers, in upstream's own order:

* ``af4cf40470c2`` (v6.6) ``sched/fair: Add cfs_rq::avg_vruntime`` -- the
  ``avg_vruntime`` / ``avg_load`` accumulators, maintained by
  ``__enqueue_entity()`` / ``__dequeue_entity()`` and re-based by
  ``update_min_vruntime()``, so ``avg_vruntime()`` is O(1).
* ``147f3efaa241`` (v6.6) -- ``update_deadline()`` semantics: the deadline is
  refreshed from ``update_curr()`` and the entity's own ``se->slice``, not by
  scanning the tree from the selector.
* ``63304558ba5d`` (v6.6) ``sched/eevdf: Curb wakeup-preemption`` -- adds
  ``RUN_TO_PARITY``.  The selector now returns ``curr`` for as long as it is
  eligible and its deadline has not been refreshed since it was picked;
  upstream's measurement for that switch is -31% context switches on
  ``perf bench sched messaging``.
* ``ee4373dc902c`` (v6.8) -- the O(1) front door: the leftmost eligible entity
  is taken before any further search.
* the ``check_preempt_wakeup()`` half of ``147f3efaa241`` -- wakeup preemption
  now asks the selector instead of the CFS granularity ladder, so placement and
  preemption cannot disagree.  That ladder is not deleted: it becomes the
  ``!sched_feat(EEVDF)`` half of the same switch, which is what keeps
  ``wakeup_preempt_entity()`` and ``wakeup_gran()`` live (and
  ``__pick_next_entity()`` with them, through ``pick_next_entity()``'s own
  ``!EEVDF`` half).
* ``85e511df3cec`` (v6.12) ``sched/eevdf: Allow shorter slices to
  wakeup-preempt`` -- ``PREEMPT_SHORT``, gated exactly as upstream's
  ``do_preempt_short()`` (shorter request, wakee eligible, and not already
  ahead of an eligible current).  Its *producer* -- the per-task request size
  that ``sched_setattr(sched_runtime)`` writes -- is ported with it, in
  ``__setscheduler_params()``; see "The per-task request size" below.
* ``79104becf42b`` (v6.17) plus the ``yield_task_fair()`` half of
  ``147f3efaa241`` -- EEVDF yield: forfeit the remaining vruntime and push the
  deadline one slice ahead, but only while the entity is eligible, because
  core scheduling prefers running an ineligible task over force-idling and an
  unguarded forfeit makes vruntime run away in a yield loop.
* ``c40dd90ac045`` (v6.12) -- a new task starts on the rq it will actually run
  on, so ``place_entity(cfs_rq, se, 1)`` places it at ``avg_vruntime()`` rather
  than at the vruntime it inherited from its parent.

The per-task request size, and why it is not optional.  ``se->slice`` is what
``PREEMPT_SHORT`` compares, and a comparison is only a comparison if the two
sides can differ.  Upstream gets that from ``sched_setattr(sched_runtime)``,
which ``__setparam_fair()`` turns into ``se->custom_slice = 1; se->slice = r``.
The first draft of this batch ported the *consumer* and not the producer, so
``se->slice`` could only ever hold ``sysctl_sched_min_granularity`` and
``abk_eevdf_slice(pse) >= abk_eevdf_slice(se)`` was ``X >= X``: the guard always
rejected, the switch was static-branch-true and did nothing, and KABI slot 4
was spent on a constant.  Two things follow, and both are load-bearing:

* ``__setscheduler_params()`` writes ``p->se.slice`` from ``attr->sched_runtime``
  (clamped 100us..100ms, upstream's bounds).  It lives in ``core.c`` rather than
  in a ``__setparam_fair()`` in ``fair.c`` because 5.15's ``fair.c`` does not
  see ``struct sched_attr`` at all; the behaviour is upstream's.
* the two writers of ``se->slice`` -- ``abk_eevdf_refresh_deadline()`` and
  ``abk_eevdf_place_entity()`` -- only fill in the default when the field is
  zero, so a request survives them.  Zero is the flag; there is no
  ``custom_slice`` bit because the reserve run is exhausted.  See
  ``abk_eevdf_slice()``.

``tests/implementation_audit.py`` pins this pairing so the consumer cannot come
back without the producer.

Deliberately NOT ported, and why (they need a ``sched_entity`` field that does
not fit -- see below):

* ``152e11f6df29`` and the delayed-dequeue family (v6.12): needs
  ``se->sched_delayed``, ``cfs_rq->nr_delayed`` and a ``dequeue_task()`` core
  rework, and carries ~20 follow-up fixes.
* slice protection (``se->vprot``, v6.15+) and ``min_slice`` / ``max_slice``
  propagation (v6.12/v6.15): three more ``sched_entity`` fields.
* ``2227a957e1d5`` (v6.8) ``sched/eevdf: Sort the rbtree by virtual deadline``
  and the augmented-tree heap search it enables: this re-keys the rb-tree on
  ``deadline``, which changes ``__pick_first_entity()`` semantics for every
  other caller (``update_min_vruntime()``, ``check_preempt_tick()``) and
  requires ``rb_add_augmented_cached()``, which does not exist before 6.6.
  Batch 28 already removes the quadratic cost; the remaining O(n) -> O(log n)
  is a separate, higher-risk batch.
* upstream's ``SCHED_FEAT(EEVDF)`` gate itself.  The EEVDF series carried it
  from ``147f3efaa241`` until ``5e963f2bd465`` "Commit to EEVDF" removed it.
  This port restores it around the two places where a wrong decision is worst
  -- selection and wakeup preemption -- so the tree can be returned to CFS
  behaviour without a reflash.  It is not a full revert: ``place_entity()``
  keeps its EEVDF placement, which is why the switch is documented as a
  selection fallback rather than as "EEVDF off".
* ``e8f331bcc270`` (v6.6) lag-based cross-runqueue placement: 5.15 still
  renormalises ``vruntime`` against ``min_vruntime`` in ``enqueue_entity()``,
  ``dequeue_entity()``, ``detach_task_cfs_rq()``, ``attach_task_cfs_rq()`` and
  ``task_fork_fair()``; replacing that handshake is a migration-path rewrite,
  not a bounded graft.
* ``sched_eevdf_runtime_state_phase3``.  ``patch_sched_runtime_state_phase3()``
  (:1020-1068) only inserts one comment in front of ``if (queued) {`` in
  ``reweight_entity()`` and then probes for ``DEQUEUE_DELAYED``/``DELAY_DEQUEUE``.
  Creating a group for it would be a phantom group whose entire content is a
  comment; ``docs/survey_suite_absorption.md`` §4 reaches the same verdict.
* ``_patch_fair_reweight_compat()`` (:519-590).  A regex rewrite of a
  *different* ``reweight_entity()`` body; all four 5.15 trees match
  ``reweight_old`` verbatim, so the fallback is unreachable here and a regex
  substitution is not expressible as an anchor step.
* the ``struct sched_rt_entity`` slot *restore*.  Nothing in this repo writes
  those slots, so the restore's anchor is absent from a pristine tree; as a
  ``required`` step it could only ever fail the group.
* ``u64 avg_vruntime()`` is kept exactly as the suite wrote it (non-``static``,
  no prototype).  ``avg_vruntime`` occurs nowhere else in the 5.15 tree
  (0 hits in ``kernel/`` and ``include/``), and ``-Wmissing-prototypes`` is a
  ``W=1``-only flag in 5.15 (``scripts/Makefile.extrawarn:28``), so the suite's
  form is warning-clean at the default ``CONFIG_WERROR=y`` level.

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

Batch 28 consumes the **last** free reserve slot.  Upstream's 6.12+ EEVDF carries
seven ``sched_entity`` fields plus three flag bits (``deadline``,
``min_vruntime``, ``min_slice``, ``max_slice``, ``vlag``, ``vprot``, ``slice``,
``rel_deadline``, ``custom_slice``, ``sched_delayed``); four is all the Android
reserve run has, which is the concrete reason the 6.12+ features above are out
of scope rather than merely unattempted.

The ``struct cfs_rq`` accumulators (group ``sched_eevdf_modern_fields``) are not
a KMI concern: ``cfs_rq`` lives in ``kernel/sched/sched.h``, is not an exported
type and appears in no KMI-checked symbol.

Runtime caveat inherited from the suite (not a porting defect, recorded so it is
not mistaken for one): the graft does not touch ``__sched_fork()``, so
``se->deadline`` / ``se->vlag`` / ``se->min_vruntime`` begin as a copy of the
parent's ``struct sched_entity`` (zero for ``init_task``).  The lifecycle closes
at ``task_fork_fair()`` -> ``place_entity(cfs_rq, se, 1)``, which is called
unconditionally and overwrites ``deadline``, ``vlag`` and ``slice`` on both of
``abk_eevdf_place_entity()``'s paths, so the inherited values do not survive a
fork.  ``se->slice`` is additionally made self-healing in
``abk_eevdf_slice()`` (a zero slice reads as ``sysctl_sched_min_granularity``),
so a path that reaches the helpers before any placement cannot divide by or
compare against zero.

Registration order required by this module (see item 3 and "Anchor overlap"
below): ``sched_eevdf_pick_logic`` first, then ``sched_eevdf_core_fields``, then
``sched_eevdf_modern_fields``, and all three **after** every existing
``stable_perf_backport`` group that touches ``kernel/sched/fair.c``.

Anchor overlap with the existing ``stable_perf_backport`` children:
``sched_nohz_idle_balance_series`` (``nohz_balancer_kick()``,
``_nohz_idle_balance()``), ``sched_dst_group_allowed_stats``
(``update_sg_wakeup_stats()``), ``sched_lazy_preemption_hooks``
(``#include <trace/hooks/sched.h>``, the ``delta_exec > ideal_runtime`` arm of
``check_preempt_tick()``, the ``CONFIG_SCHED_HRTICK`` arm of ``entity_tick()``,
and ``check_preempt_wakeup()``'s ``preempt:`` label) and
``schedutil_smart_policy`` (``cpufreq_schedutil.c``) each rewrite regions
disjoint from every ``old`` block here.  The nearest call is
``sched_lazy_preemption_hooks``: it edits the *head* of ``check_preempt_tick()``
while this module edits the tail, it edits ``entity_tick()``'s HRTICK branch
while this module no longer edits ``entity_tick()`` at all, and its
``preempt:`` label edit starts *below* the ``return;`` this module's
``check_preempt_wakeup()`` step anchors on.  One *semantic* interaction is worth
recording: the EEVDF preemption points this module adds flag
``cfs_rq``/``se`` through the ``preempt:`` label, which the lazy group has
already wrapped in ``trace_android_vh_resched_curr_lazy()``, so they inherit
that policy for free; only the direct ``resched_curr()`` inside
``abk_eevdf_refresh_deadline()`` is eager rather than deferred.  It is a policy
overlap, not an anchor overlap.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

SCHED_H = "include/linux/sched.h"
FAIR_C = "kernel/sched/fair.c"
SCHED_H_INTERNAL = "kernel/sched/sched.h"
FEATURES_H = "kernel/sched/features.h"
CORE_C = "kernel/sched/core.c"

# Alias the required-flag for readability (the other batch modules use T/F).
T = True

# --- provenance markers and shape probes ------------------------------------
#
# The fair.c marker doubles as the idempotency anchor for the pick-logic group
# and as the "did the logic really land" tell for the slot claim.
EEVDF_FAIR_MARKER = (
    "/* ABK stable_515_backport: EEVDF runtime-state graft (Batch 28 shape). */"
)

# Present only when the *whole* fair.c half of the family landed.  Each string
# is emitted by a different step of the pick-logic group, so requiring all of
# them proves the accumulators, the helpers, the placement hook, the deadline
# refresh in update_curr(), the selector routing and the run-to-parity stash all
# landed -- not merely that some earlier step wrote its comment.
_FAIR_LANDED_PROBES = (
    EEVDF_FAIR_MARKER,
    "abk_avg_vruntime_add(cfs_rq, se);",
    "abk_eevdf_refresh_deadline(cfs_rq, curr);",
    "abk_eevdf_place_entity(cfs_rq, se, initial);",
    "if (sched_feat(EEVDF))",
    "return abk_pick_eevdf(cfs_rq, curr);",
    "cfs_rq->abk_pick_deadline = se->deadline;",
    "if (!se->slice)",
    "!abk_eevdf_eligible(curr, avruntime)",
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
# sched_rt_entity`` carries an identical four-line RESERVE run, so the sched_avg
# context is what pins this to sched_entity; a bare
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

# The Batch 15 form: slots 1-3 claimed, slot 4 released back to RESERVE.  A tree
# already carrying it is upgraded to the Batch 28 form, which claims slot 4 as
# ``u64 slice`` -- the per-entity request size update_deadline(), the EEVDF yield
# and PREEMPT_SHORT all read (see docs/survey_eevdf_gap.md).
_SCHED_H_SE_TAIL_V15 = (
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
    "\t * ABK stable_515_backport: EEVDF scheduling-entity state.  Absorbed\n"
    "\t * from ABK_ABI_PATCH_SUITE feature_porting/sched_eevdf_core_fields by\n"
    "\t * Batch 15; Batch 28 extended it to the fourth slot.\n"
    "\t * kernel/sched/fair.c (group sched_eevdf_pick_logic of this same\n"
    "\t * module) is the only consumer.\n"
    "\t *\n"
    "\t * All four Android KABI reserve slots become the four EEVDF fields\n"
    "\t * upstream carries from 6.6 on (deadline, min_vruntime, vlag, slice).\n"
    "\t * Each replacement is one 8-byte, 8-byte-aligned scalar, so\n"
    "\t * ANDROID_KABI_USE's own size and alignment static asserts both hold\n"
    "\t * and sizeof(struct sched_entity) is unchanged on every baseline.\n"
    "\t *\n"
    "\t * Slot 4 was released by Batch 16 as dead space (the suite's write-only\n"
    "\t * ``slice``); Batch 28 claims it for real: update_deadline(), the EEVDF\n"
    "\t * yield and PREEMPT_SHORT all read se->slice.  This exhausts the\n"
    "\t * reserve run -- the 6.12+ fields (min_slice, max_slice, vprot,\n"
    "\t * sched_delayed) have no slot left and are deliberately not ported.\n"
    "\t */\n"
    "\tANDROID_KABI_USE(1, u64 deadline);\n"
    "\tANDROID_KABI_USE(2, u64 min_vruntime);\n"
    "\tANDROID_KABI_USE(3, s64 vlag);\n"
    "\tANDROID_KABI_USE(4, u64 slice);\n"
    "};"
)


# ---------------------------------------------------------------------------
# kernel/sched/sched.h -- struct cfs_rq virtual-time accumulators
# ---------------------------------------------------------------------------
#
# Upstream af4cf40470c2 (v6.6).  The anchor is the two u64 timing fields at the
# top of struct cfs_rq plus the CONFIG_SCHED_CORE guard that follows them; the
# guard is what makes it unique (``u64 min_vruntime`` alone also appears in the
# CONFIG_SCHED_CORE block as ``u64 min_vruntime_fi``, and in the
# !CONFIG_64BIT block as ``u64 min_vruntime_copy``).
#
# struct cfs_rq is not an exported type and appears in no KMI-checked symbol, so
# these three fields are free to add.

_SCHED_H_CFS_RQ = (
    "\tu64\t\t\texec_clock;\n"
    "\tu64\t\t\tmin_vruntime;\n"
    "#ifdef CONFIG_SCHED_CORE\n"
)

_SCHED_H_CFS_RQ_NEW = (
    "\tu64\t\t\texec_clock;\n"
    "\tu64\t\t\tmin_vruntime;\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: EEVDF virtual-time accumulators\n"
    "\t * (upstream af4cf40470c2, v6.6).\n"
    "\t *\n"
    "\t *   avg_vruntime := \\Sum (v_i - v0) * w_i\n"
    "\t *   avg_load    := \\Sum w_i\n"
    "\t *\n"
    "\t * with v0 := min_vruntime, so avg_vruntime() is the weighted\n"
    "\t * average virtual time in O(1) instead of a tree walk.\n"
    "\t * __enqueue_entity()/__dequeue_entity() maintain it and\n"
    "\t * update_min_vruntime() re-bases it whenever v0 moves.\n"
    "\t *\n"
    "\t * abk_pick_deadline is the RUN_TO_PARITY stash (upstream\n"
    "\t * 63304558ba5d, v6.6): the deadline curr was picked with, which\n"
    "\t * update_curr() invalidates by refreshing se->deadline once the\n"
    "\t * request is consumed.  Upstream borrows se->vlag for this; on this\n"
    "\t * tree vlag carries the lag, so the stash lives here instead.\n"
    "\t */\n"
    "\ts64\t\t\tavg_vruntime;\n"
    "\tu64\t\t\tavg_load;\n"
    "\tu64\t\t\tabk_pick_deadline;\n"
    "#ifdef CONFIG_SCHED_CORE\n"
)


# ---------------------------------------------------------------------------
# kernel/sched/core.c -- the per-task request size producer
# ---------------------------------------------------------------------------
#
# Upstream puts __setparam_fair() in fair.c and calls it from
# __setscheduler_params(); 5.15's fair.c does not see `struct sched_attr` at
# all (it includes only "sched.h" and the AOSP hook header), so the body is
# inlined at the one call site rather than dragging a uapi include into
# fair.c.  The behaviour is upstream's: sched_runtime != 0 sets the request,
# 0 clears it, and the clamp is upstream's 100us..100ms.
#
# Without this step `se->slice` could only ever hold the global default,
# which made abk_eevdf_preempt_short() a tautology -- see the file docstring.

_CORE_SETSCHED_PARAMS = (
    "\tif (dl_policy(policy))\n"
    "\t\t__setparam_dl(p, attr);\n"
    "\telse if (fair_policy(policy))\n"
    "\t\tp->static_prio = NICE_TO_PRIO(attr->sched_nice);\n"
)

_CORE_SETSCHED_PARAMS_NEW = (
    "\tif (dl_policy(policy))\n"
    "\t\t__setparam_dl(p, attr);\n"
    "\telse if (fair_policy(policy)) {\n"
    "\t\tp->static_prio = NICE_TO_PRIO(attr->sched_nice);\n"
    "\t\t/*\n"
    "\t\t * ABK stable_515_backport: the EEVDF per-task request size --\n"
    "\t\t * upstream __setparam_fair() (the 85e511df3cec era), inlined\n"
    "\t\t * here because 5.15 fair.c does not see struct sched_attr.\n"
    "\t\t *\n"
    "\t\t * A non-zero sched_runtime becomes the entity's request; zero\n"
    "\t\t * means \"no custom request\" and abk_eevdf_slice() supplies the\n"
    "\t\t * default.  The clamp is upstream's, and it is what bounds a\n"
    "\t\t * hostile or buggy caller -- without it sched_setattr would be\n"
    "\t\t * an unbounded slice request from unprivileged userspace.\n"
    "\t\t */\n"
    "\t\tif (attr->sched_runtime)\n"
    "\t\t\tp->se.slice = min_t(u64, max_t(u64, attr->sched_runtime,\n"
    "\t\t\t\t\t       NSEC_PER_MSEC / 10),\n"
    "\t\t\t\t\t    NSEC_PER_MSEC * 100);\n"
    "\t\telse\n"
    "\t\t\tp->se.slice = 0;\n"
    "\t}\n"
)

# ---------------------------------------------------------------------------
# kernel/sched/features.h -- the EEVDF policy switches
# ---------------------------------------------------------------------------
#
# The anchor is the Android tail of the file.  ``sched_feat()`` is
# available with and without CONFIG_SCHED_DEBUG: without it, features.h is
# expanded into a compile-time constant bitmask seeded from the ``enabled``
# column (kernel/sched/sched.h:2045-2052), so ``true`` here is the default on
# every config.

_FEATURES_TAIL = (
    "SCHED_FEAT(ALT_PERIOD, true)\n"
    "SCHED_FEAT(BASE_SLICE, true)\n"
)

_FEATURES_TAIL_NEW = (
    "SCHED_FEAT(ALT_PERIOD, true)\n"
    "SCHED_FEAT(BASE_SLICE, true)\n"
    "\n"
    "/*\n"
    " * ABK stable_515_backport: EEVDF policy switches.\n"
    " *\n"
    " * RUN_TO_PARITY (upstream 63304558ba5d, v6.6): once picked, a task keeps\n"
    " * the CPU until it either becomes non-eligible or consumes its request.\n"
    " * Without it, lag-based placement puts a wakee left of current AND with\n"
    " * an earlier deadline, which causes immediate preemption on every wakeup\n"
    " * -- upstream measured -31% context switches on `perf bench sched\n"
    " * messaging` for switching it on.\n"
    " *\n"
    " * PREEMPT_SHORT (upstream 85e511df3cec, v6.12): a wakee with a shorter\n"
    " * request than current may preempt it, which is what makes short-slice\n"
    " * latency-sensitive tasks responsive.\n"
    " */\n"
    "SCHED_FEAT(EEVDF, true)\n"
    "SCHED_FEAT(RUN_TO_PARITY, true)\n"
    "SCHED_FEAT(PREEMPT_SHORT, true)\n"
)


# ---------------------------------------------------------------------------
# kernel/sched/fair.c -- anchors as they exist in the pristine 5.15 trees
# ---------------------------------------------------------------------------
#
# Every block below was measured to occur exactly once in the pristine
# abk515_ref_{167,178,194,216} trees.  The tabs are the tree's own; a mistyped
# indent shows up as a 0-count anchor in tests/step_audit.py rather than as a
# silent no-op, because every step is required.

# Step 1: the cfs_rq accumulators, inserted between entity_before() and
# __node_2_se().  They are placed this high in the file because
# update_min_vruntime() (fair.c:544) has to call abk_avg_vruntime_update().
_FAIR_ENTITY_BEFORE = (
    "static inline bool entity_before(struct sched_entity *a,\n"
    "\t\t\t\tstruct sched_entity *b)\n"
    "{\n"
    "\treturn (s64)(a->vruntime - b->vruntime) < 0;\n"
    "}\n"
    "\n"
    "#define __node_2_se(node) \\\n"
    "\trb_entry((node), struct sched_entity, run_node)\n"
)

_FAIR_ENTITY_BEFORE_NEW = (
    "static inline bool entity_before(struct sched_entity *a,\n"
    "\t\t\t\tstruct sched_entity *b)\n"
    "{\n"
    "\treturn (s64)(a->vruntime - b->vruntime) < 0;\n"
    "}\n"
    "\n"
    "#define __node_2_se(node) \\\n"
    "\trb_entry((node), struct sched_entity, run_node)\n"
    "\n"
    "/* ABK stable_515_backport: cfs_rq virtual-time accumulators. */\n"
    "/*\n"
    " * Upstream sched/fair: Add cfs_rq::avg_vruntime (af4cf40470c2, v6.6) keeps\n"
    " *\n"
    " *     \\Sum (v_i - v0) * w_i   in cfs_rq::avg_vruntime\n"
    " *     \\Sum w_i                in cfs_rq::avg_load\n"
    " *\n"
    " * so avg_vruntime() is O(1) instead of a tree walk.  v0 is min_vruntime, so\n"
    " * update_min_vruntime() re-bases the sum whenever v0 moves and every\n"
    " * add/remove has to happen while v0 is unchanged.\n"
    " */\n"
    "static inline s64 abk_entity_key(struct cfs_rq *cfs_rq, struct sched_entity *se)\n"
    "{\n"
    "\treturn (s64)(se->vruntime - cfs_rq->min_vruntime);\n"
    "}\n"
    "\n"
    "static void abk_avg_vruntime_update(struct cfs_rq *cfs_rq, s64 delta)\n"
    "{\n"
    "\t/*\n"
    "\t * v' = v + d ==> avg_vruntime' = avg_vruntime - d * avg_load\n"
    "\t */\n"
    "\tcfs_rq->avg_vruntime -= cfs_rq->avg_load * delta;\n"
    "}\n"
    "\n"
    "static void abk_avg_vruntime_add(struct cfs_rq *cfs_rq, struct sched_entity *se)\n"
    "{\n"
    "\tunsigned long weight = scale_load_down(se->load.weight);\n"
    "\n"
    "\tcfs_rq->avg_vruntime += abk_entity_key(cfs_rq, se) * weight;\n"
    "\tcfs_rq->avg_load += weight;\n"
    "}\n"
    "\n"
    "static void abk_avg_vruntime_sub(struct cfs_rq *cfs_rq, struct sched_entity *se)\n"
    "{\n"
    "\tunsigned long weight = scale_load_down(se->load.weight);\n"
    "\n"
    "\tcfs_rq->avg_vruntime -= abk_entity_key(cfs_rq, se) * weight;\n"
    "\tcfs_rq->avg_load -= weight;\n"
    "}\n"
)

# Step 2: update_min_vruntime() re-bases the accumulators when v0 moves.
_FAIR_MIN_VRUNTIME = (
    "static void update_min_vruntime(struct cfs_rq *cfs_rq)\n"
    "{\n"
    "\tstruct sched_entity *curr = cfs_rq->curr;\n"
    "\tstruct rb_node *leftmost = rb_first_cached(&cfs_rq->tasks_timeline);\n"
    "\n"
    "\tu64 vruntime = cfs_rq->min_vruntime;\n"
    "\n"
    "\tif (curr) {\n"
    "\t\tif (curr->on_rq)\n"
    "\t\t\tvruntime = curr->vruntime;\n"
    "\t\telse\n"
    "\t\t\tcurr = NULL;\n"
    "\t}\n"
    "\n"
    "\tif (leftmost) { /* non-empty tree */\n"
    "\t\tstruct sched_entity *se = __node_2_se(leftmost);\n"
    "\n"
    "\t\tif (!curr)\n"
    "\t\t\tvruntime = se->vruntime;\n"
    "\t\telse\n"
    "\t\t\tvruntime = min_vruntime(vruntime, se->vruntime);\n"
    "\t}\n"
    "\n"
    "\t/* ensure we never gain time by being placed backwards. */\n"
    "\tcfs_rq->min_vruntime = max_vruntime(cfs_rq->min_vruntime, vruntime);\n"
    "#ifndef CONFIG_64BIT\n"
    "\tsmp_wmb();\n"
    "\tcfs_rq->min_vruntime_copy = cfs_rq->min_vruntime;\n"
    "#endif\n"
    "}\n"
)

_FAIR_MIN_VRUNTIME_NEW = (
    "static void update_min_vruntime(struct cfs_rq *cfs_rq)\n"
    "{\n"
    "\tstruct sched_entity *curr = cfs_rq->curr;\n"
    "\tstruct rb_node *leftmost = rb_first_cached(&cfs_rq->tasks_timeline);\n"
    "\n"
    "\tu64 vruntime = cfs_rq->min_vruntime;\n"
    "\tu64 old_min_vruntime = cfs_rq->min_vruntime;\n"
    "\n"
    "\tif (curr) {\n"
    "\t\tif (curr->on_rq)\n"
    "\t\t\tvruntime = curr->vruntime;\n"
    "\t\telse\n"
    "\t\t\tcurr = NULL;\n"
    "\t}\n"
    "\n"
    "\tif (leftmost) { /* non-empty tree */\n"
    "\t\tstruct sched_entity *se = __node_2_se(leftmost);\n"
    "\n"
    "\t\tif (!curr)\n"
    "\t\t\tvruntime = se->vruntime;\n"
    "\t\telse\n"
    "\t\t\tvruntime = min_vruntime(vruntime, se->vruntime);\n"
    "\t}\n"
    "\n"
    "\t/* ensure we never gain time by being placed backwards. */\n"
    "\tcfs_rq->min_vruntime = max_vruntime(cfs_rq->min_vruntime, vruntime);\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: the accumulators are relative to\n"
    "\t * min_vruntime, so the sum has to be re-based when it moves.\n"
    "\t */\n"
    "\tif (cfs_rq->min_vruntime != old_min_vruntime)\n"
    "\t\tabk_avg_vruntime_update(cfs_rq, cfs_rq->min_vruntime - old_min_vruntime);\n"
    "#ifndef CONFIG_64BIT\n"
    "\tsmp_wmb();\n"
    "\tcfs_rq->min_vruntime_copy = cfs_rq->min_vruntime;\n"
    "#endif\n"
    "}\n"
)

# Step 3: the tree insert/remove maintain the accumulators.  Upstream adds
# before the insert and subtracts after the erase, so that the entity is
# accounted for exactly while it is in the tree.
_FAIR_ENQ_DEQ = (
    "static void __enqueue_entity(struct cfs_rq *cfs_rq, struct sched_entity *se)\n"
    "{\n"
    "\ttrace_android_rvh_enqueue_entity(cfs_rq, se);\n"
    "\trb_add_cached(&se->run_node, &cfs_rq->tasks_timeline, __entity_less);\n"
    "}\n"
    "\n"
    "static void __dequeue_entity(struct cfs_rq *cfs_rq, struct sched_entity *se)\n"
    "{\n"
    "\ttrace_android_rvh_dequeue_entity(cfs_rq, se);\n"
    "\trb_erase_cached(&se->run_node, &cfs_rq->tasks_timeline);\n"
    "}\n"
)

_FAIR_ENQ_DEQ_NEW = (
    "static void __enqueue_entity(struct cfs_rq *cfs_rq, struct sched_entity *se)\n"
    "{\n"
    "\ttrace_android_rvh_enqueue_entity(cfs_rq, se);\n"
    "\tabk_avg_vruntime_add(cfs_rq, se);\n"
    "\trb_add_cached(&se->run_node, &cfs_rq->tasks_timeline, __entity_less);\n"
    "}\n"
    "\n"
    "static void __dequeue_entity(struct cfs_rq *cfs_rq, struct sched_entity *se)\n"
    "{\n"
    "\ttrace_android_rvh_dequeue_entity(cfs_rq, se);\n"
    "\trb_erase_cached(&se->run_node, &cfs_rq->tasks_timeline);\n"
    "\tabk_avg_vruntime_sub(cfs_rq, se);\n"
    "}\n"
)

# Step 4: the helper set, inserted between sched_vslice() and ``#include
# "pelt.h"``.  sched_slice() (fair.c:681), calc_delta_fair() (fair.c:651),
# scale_load_down() and __node_2_se() are all defined above this point on 5.15,
# and so are the accumulators step 1 added.
_FAIR_VSLICE = (
    "static u64 sched_vslice(struct cfs_rq *cfs_rq, struct sched_entity *se)\n"
    "{\n"
    "\treturn calc_delta_fair(sched_slice(cfs_rq, se), se);\n"
    "}\n"
)

_FAIR_HELPERS = "\n" + """/* ABK stable_515_backport: EEVDF runtime-state graft (Batch 28 shape). */
/*
 * Absorbed from ABK_ABI_PATCH_SUITE feature_porting/sched_eevdf_pick_logic
 * (phase "scan_based_eevdf_phase2") by Batch 15, and rebuilt onto the upstream
 * data structure and policy by Batch 28.  docs/survey_eevdf_gap.md is the
 * difference audit against Linux 6.6 -> 7.3; the per-commit provenance of every
 * rule below is in this module's docstring.
 *
 * The four sched_entity fields it reads (deadline, min_vruntime, vlag, slice)
 * are claimed from the Android KABI reserve slots by the core-fields group of
 * this same module; the cfs_rq accumulators it reads are added by the
 * modern-fields group.
 */
#define ABK_EEVDF_REL_DEADLINE_BIT (1ULL << 63)
#define ABK_EEVDF_REL_DEADLINE_MASK (ABK_EEVDF_REL_DEADLINE_BIT - 1)

/* clear_buddies() is defined ~3800 lines below; abk_eevdf_refresh_deadline()
 * calls it when the request is consumed. */
static void clear_buddies(struct cfs_rq *cfs_rq, struct sched_entity *se);

static inline u64 abk_eevdf_slice(struct sched_entity *se)
{
\t/*
\t * se->slice carries the entity's request size, and zero means "no custom
\t * request": the value is then the runqueue-wide default.  A non-zero value
\t * is a request the task made for itself through sched_setattr(sched_runtime)
\t * -- __setscheduler_params() writes it, and the two writers below leave it
\t * alone.  That is what lets PREEMPT_SHORT compare two different request
\t * sizes; with every entity on the default it would be a tautology.
\t *
\t * Upstream spells this as a separate `unsigned char custom_slice` bit beside
\t * the field.  5.15's sched_entity reserve run is exhausted -- four slots, all
\t * four already spent -- so zero carries the flag instead.  The observable
\t * behaviour is identical, because upstream's `!custom_slice` paths also
\t * write sysctl_sched_base_slice.
\t *
\t * (750us is 5.15's sysctl_sched_min_granularity; 6.6 renames it
\t * sysctl_sched_base_slice.  Its normalised value is also reachable from
\t * __setscheduler_params(), which is the only writer of a non-default slice.)
\t */
\treturn se->slice ? se->slice : (u64)sysctl_sched_min_granularity;
}

static inline u64 abk_eevdf_vslice(struct cfs_rq *cfs_rq, struct sched_entity *se)
{
\tu64 slice = abk_eevdf_slice(se);

\treturn max_t(u64, calc_delta_fair(slice, se), 1ULL);
}

/*
 * O(1) weighted average virtual time.  Specifically: avg_vruntime() + 0 must
 * make abk_eevdf_eligible() true, which is why the division below carries a
 * left bias -- integer division floors unless the value is negative, where it
 * ceils, and the sign flip is what keeps the result eligible either way
 * (upstream 650cad561cce, v6.6).
 */
u64 avg_vruntime(struct cfs_rq *cfs_rq)
{
\tstruct sched_entity *curr = cfs_rq->curr;
\ts64 avg = cfs_rq->avg_vruntime;
\tlong load = cfs_rq->avg_load;

\tif (curr && curr->on_rq) {
\t\tunsigned long weight = scale_load_down(curr->load.weight);

\t\tavg += abk_entity_key(cfs_rq, curr) * weight;
\t\tload += weight;
\t}

\tif (load) {
\t\t/* sign flips effective floor / ceil */
\t\tif (avg < 0)
\t\t\tavg -= (load - 1);
\t\tavg = div_s64(avg, load);
\t}

\treturn cfs_rq->min_vruntime + avg;
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

static inline s64 abk_eevdf_lag_limit(struct sched_entity *se)
{
\t/*
\t * lag_i = S - s_i = w_i * (V - v_i); V is the weighted average of all
\t * entities, so add/remove/reweight can move V and leave a lag larger than
\t * the system started with.  Upstream bounds it at twice the entity's own
\t * request, with TICK_NSEC as the floor because that is the timing
\t * granularity (update_entity_lag(), v6.6).
\t */
\treturn max_t(s64, (s64)calc_delta_fair(max_t(u64, 2 * abk_eevdf_slice(se),
\t\t\t\t\t\t     TICK_NSEC), se), 1LL);
}

static void abk_eevdf_update_lag(struct cfs_rq *cfs_rq, struct sched_entity *se)
{
\ts64 vlag, limit;

\tvlag = (s64)(avg_vruntime(cfs_rq) - se->vruntime);
\tlimit = abk_eevdf_lag_limit(se);
\tse->vlag = clamp_t(s64, vlag, -limit, limit);
}

static s64 abk_eevdf_preserved_lag(struct cfs_rq *cfs_rq, struct sched_entity *se)
{
\tunsigned long load = cfs_rq->avg_load;
\tunsigned long weight = max_t(unsigned long, scale_load_down(se->load.weight), 1UL);
\ts64 lag = se->vlag;
\ts64 limit = abk_eevdf_lag_limit(se);

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

/*
 * EEVDF: vd_i = ve_i + r_i / w_i.  This is upstream update_deadline() (v6.6)
 * plus this module's relative-deadline tag.  It returns true when the request
 * was refreshed, which means the entity has consumed its slice.
 *
 * It is O(1): the Batch 15 form walked the whole tree here (avg_vruntime() for
 * the lag and a max-slice scan for the limit), and the selector called it once
 * per tree node, which is what made pick_next_entity() quadratic.
 */
static bool abk_eevdf_refresh_deadline(struct cfs_rq *cfs_rq,
\t\t\t\t       struct sched_entity *se)
{
\tif (abk_eevdf_has_rel_deadline(se)) {
\t\tu64 rel_deadline = abk_eevdf_take_rel_deadline(se);

\t\tse->deadline = se->vruntime + rel_deadline;
\t}

\tif ((s64)(se->vruntime - se->deadline) < 0)
\t\treturn false;

\t/*
\t * For EEVDF the virtual time slope is determined by w_i (iow. nice)
\t * while the request time r_i is per entity.
\t */
\tif (!se->slice)
\t\tse->slice = sysctl_sched_min_granularity;
\tse->deadline = se->vruntime + abk_eevdf_vslice(cfs_rq, se);

\t/*
\t * The task has consumed its request.  Its deadline moved, so the
\t * RUN_TO_PARITY stash in cfs_rq::abk_pick_deadline no longer matches
\t * and the selector is free to pick someone else.
\t */
\tif (cfs_rq->nr_running > 1) {
\t\tresched_curr(rq_of(cfs_rq));
\t\tclear_buddies(cfs_rq, se);
\t}

\treturn true;
}

static void abk_eevdf_place_entity(struct cfs_rq *cfs_rq,
\t\t\t\t struct sched_entity *se, int initial)
{
\tbool rel_deadline = abk_eevdf_has_rel_deadline(se);
\tu64 vslice;
\ts64 lag = 0;

\tif (!se->slice)
\t\tse->slice = sysctl_sched_min_granularity;
\tvslice = abk_eevdf_vslice(cfs_rq, se);

\tif (!initial && (se->vlag || rel_deadline)) {
\t\tlag = abk_eevdf_preserved_lag(cfs_rq, se);
\t\tabk_eevdf_apply_lag_placement(cfs_rq, se, lag);
\t} else if (initial) {
\t\t/*
\t\t * A new task runs on the rq that wake_up_new_task() assigned, not
\t\t * on the one it was forked on, so start it at the current ideal
\t\t * virtual time rather than at the vruntime it inherited from its
\t\t * parent (upstream c40dd90ac045, v6.12).
\t\t */
\t\tse->vruntime = avg_vruntime(cfs_rq);
\t}

\tif (rel_deadline) {
\t\tu64 rel = abk_eevdf_take_rel_deadline(se);

\t\tif (!rel)
\t\t\trel = vslice;
\t\tse->deadline = se->vruntime + rel;
\t\tse->vlag = 0;
\t\treturn;
\t}

\t/*
\t * When joining the competition the existing tasks will be, on average,
\t * halfway through their slice, so start a new task off with half a slice
\t * to ease into the competition (PLACE_DEADLINE_INITIAL, v6.6).
\t */
\tif (initial)
\t\tvslice = max_t(u64, vslice >> 1, 1ULL);

\tse->deadline = se->vruntime + vslice;
\tse->vlag = 0;
}

/*
 * EEVDF: from the eligible entities, take the one with the earliest virtual
 * deadline.
 *
 * This is a single O(n) pass with O(1) per node, and -- unlike the Batch 15
 * form -- it does not mutate scheduler state.  Every quantity it reads
 * (deadline, vruntime) is established by place_entity() and maintained by
 * update_curr() -> abk_eevdf_refresh_deadline(); the Batch 15 form refreshed
 * them here, per node, which is why asking who should run used to perturb the
 * scheduler.
 *
 * The remaining O(n) -> O(log n) step is upstream 2227a957e1d5 (v6.8): re-key
 * the rb-tree on deadline and augment it with se->min_vruntime so eligibility
 * can prune subtrees.  That changes __pick_first_entity() semantics for
 * update_min_vruntime() and check_preempt_tick() as well, so it is a separate
 * batch.
 */
static struct sched_entity *abk_pick_eevdf(struct cfs_rq *cfs_rq,
\t\t\t\t\t  struct sched_entity *curr)
{
\tstruct sched_entity *best = NULL;
\tstruct sched_entity *next = cfs_rq->next;
\tstruct rb_node *node;
\tu64 avruntime = avg_vruntime(cfs_rq);

\t/*
\t * An entity that is not eligible is owed no service, so it cannot be the
\t * answer below -- drop it before any rule looks at it.  Upstream does this
\t * immediately ahead of its run-to-parity test (pick_eevdf(), v6.6);
\t * leaving it out lets run-to-parity hand the CPU to an ineligible current,
\t * which is both more than upstream promises and enough to hide the skip
\t * buddy that yield_task_fair() sets.
\t */
\tif (curr && (!curr->on_rq || !abk_eevdf_eligible(curr, avruntime)))
\t\tcurr = NULL;

\t/*
\t * Once selected, run a task until it either becomes non-eligible or until
\t * it gets a new slice (upstream 63304558ba5d, v6.6).  cfs_rq->
\t * abk_pick_deadline is the deadline curr was picked with, set in
\t * set_next_entity(); abk_eevdf_refresh_deadline() invalidates the match
\t * by moving se->deadline once the request is consumed.
\t */
\tif (sched_feat(RUN_TO_PARITY) && curr && curr->on_rq && curr->deadline &&
\t    cfs_rq->abk_pick_deadline == curr->deadline)
\t\treturn curr;

\tif (curr && curr->on_rq && abk_eevdf_eligible(curr, avruntime))
\t\tbest = curr;

\tif (next && next->on_rq && abk_eevdf_eligible(next, avruntime) &&
\t    abk_eevdf_entity_before(next, best))
\t\tbest = next;

\tfor (node = rb_first_cached(&cfs_rq->tasks_timeline); node; node = rb_next(node)) {
\t\tstruct sched_entity *se = __node_2_se(node);

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

/*
 * PREEMPT_SHORT (upstream 85e511df3cec, v6.12): a wakee that asked for less
 * time than current may preempt it, so a latency-sensitive short request is not
 * stuck behind a long one.  The guards are upstream do_preempt_short()'s: the
 * request really is shorter, the wakee is eligible, and the wakee is not
 * already ahead of an eligible current (in which case the normal rule below
 * would take it anyway).
 */
static inline bool abk_eevdf_preempt_short(struct cfs_rq *cfs_rq,
\t\t\t\t\t   struct sched_entity *se,
\t\t\t\t\t   struct sched_entity *pse)
{
\tif (!sched_feat(PREEMPT_SHORT))
\t\treturn false;

\tif (abk_eevdf_slice(pse) >= abk_eevdf_slice(se))
\t\treturn false;

\tif (!abk_eevdf_eligible(pse, avg_vruntime(cfs_rq)))
\t\treturn false;

\tif (abk_eevdf_eligible(se, avg_vruntime(cfs_rq)) &&
\t    pse->vruntime >= se->vruntime)
\t\treturn false;

\treturn true;
}
"""

_FAIR_VSLICE_NEW = _FAIR_VSLICE + _FAIR_HELPERS

# Step 5: update_curr() is where upstream refreshes the deadline
# (update_deadline(), v6.6).  This is the other half of the quadratic-cost fix:
# the selector no longer has to refresh anything, because the entity that is
# running has its deadline maintained as its vruntime advances.
_FAIR_UPDATE_CURR = (
    "\tcurr->vruntime += calc_delta_fair(delta_exec, curr);\n"
    "\tupdate_min_vruntime(cfs_rq);\n"
)

_FAIR_UPDATE_CURR_NEW = (
    "\tcurr->vruntime += calc_delta_fair(delta_exec, curr);\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: EEVDF request accounting (upstream\n"
    "\t * update_deadline(), v6.6).  Refreshing here rather than inside the\n"
    "\t * selector is what keeps pick_next_entity() free of scheduler-state\n"
    "\t * mutation.\n"
    "\t */\n"
    "\tabk_eevdf_refresh_deadline(cfs_rq, curr);\n"
    "\tupdate_min_vruntime(cfs_rq);\n"
)

# __pick_next_entity(): probe-only, deliberately NOT a step.
#
# Batch 15 added __maybe_unused to this definition because routing
# pick_next_entity() through abk_pick_eevdf() took away its only caller, and
# -Wall -Wunused-function is -Werror here.  Batch 28 keeps it live instead: the
# !EEVDF half of pick_next_entity() calls it.  The pristine definition is
# therefore already the target form, and a step whose old == new is a no-op
# that replace_once reports as already_present forever -- docs/group_recipe.md
# trap 1.  The anchor stays here so the uniqueness probe still covers the
# context; there is no entry for it in build_pick_logic_steps().
_FAIR_NEXT_PICK = (
    "static struct sched_entity *__pick_next_entity(struct sched_entity *se)\n"
)


# Step 7: reweight_entity() (fair.c:3082) calls place_entity() (fair.c:4346), so
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
)

# Step 8: reweight_entity() HEAD.
_FAIR_REWEIGHT_HEAD = (
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

# Step 9: reweight_entity() TAIL.  Split from the head at the AOSP hook so the
# hook -- and anything a later sublevel adds in the same gap -- stays put.
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
    "}\n"
)

# Step 10: enqueue_entity() must re-place an entity that carries preserved state
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

# Step 11: dequeue_entity() head.  5.15 has no ``int action = UPDATE_TG;`` here
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

# Step 12: capture lag and the relative deadline while the entity is still in the
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
    "\t * tree; a non-sleeping entity additionally stores its remaining\n"
    "\t * relative deadline for the next placement. */\n"
    "\tabk_eevdf_update_lag(cfs_rq, se);\n"
    "\tif (!sleep)\n"
    "\t\tabk_eevdf_store_rel_deadline(se);\n"
    "\n"
    "\tif (se != cfs_rq->curr)\n"
    "\t\t__dequeue_entity(cfs_rq, se);\n"
)

# Step 13: place_entity() tail.  The anchor carries the AOSP place hook, which is
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
    "\t/* ABK stable_515_backport: the EEVDF placement supersedes the CFS\n"
    "\t * min_vruntime pull above -- it places the entity at the weighted\n"
    "\t * average vruntime offset by its preserved lag, then hands it a slice\n"
    "\t * worth of deadline. */\n"
    "\tabk_eevdf_place_entity(cfs_rq, se, initial);\n"
    "}\n"
)

# Step 14: check_preempt_tick() tail.  The lazy-preemption group edits the head of
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
    "\tif (sched_feat(EEVDF)) {\n"
    "\t\tse = abk_pick_eevdf(cfs_rq, curr);\n"
    "\t\tif (se && se != curr && abk_eevdf_entity_before(se, curr)) {\n"
    "\t\t\tresched_curr(rq_of(cfs_rq));\n"
    "\t\t\treturn;\n"
    "\t\t}\n"
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

# Step 15: set_next_entity().  The 5.15 spelling is update_stats_wait_end (no
# _fair suffix).  This is also where the RUN_TO_PARITY stash is taken: the
# deadline the entity had at the moment it was picked.
_FAIR_SET_NEXT = (
    "\t\tupdate_stats_wait_end(cfs_rq, se);\n"
    "\t\t__dequeue_entity(cfs_rq, se);\n"
    "\t\tupdate_load_avg(cfs_rq, se, UPDATE_TG);\n"
)

_FAIR_SET_NEXT_NEW = (
    "\t\tupdate_stats_wait_end(cfs_rq, se);\n"
    "\t\t/* ABK stable_515_backport: RUN_TO_PARITY stash -- remember the\n"
    "\t\t * deadline this entity was picked with; update_curr() invalidates\n"
    "\t\t * it by refreshing se->deadline once the request is consumed.  It\n"
    "\t\t * lives in cfs_rq because se->vlag carries the lag on this tree. */\n"
    "\t\tcfs_rq->abk_pick_deadline = se->deadline;\n"
    "\t\t__dequeue_entity(cfs_rq, se);\n"
    "\t\tupdate_load_avg(cfs_rq, se, UPDATE_TG);\n"
)

# Step 16: pick_next_entity().  Matches the pristine 5.15 body verbatim,
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
    "\tstruct sched_entity *left;\n"
    "\tstruct sched_entity *se = NULL;\n"
    "\n"
    "\t/* ABK stable_515_backport: the AOSP vendor hook keeps the first\n"
    "\t * word; when it does not select, the EEVDF selector owns the choice.\n"
    "\t * The legacy CFS ladder below is kept, and is what runs when EEVDF is\n"
    "\t * switched off -- sched_feat(EEVDF) is the switch the EEVDF series\n"
    "\t * itself carried until 5e963f2bd465 \"Commit to EEVDF\" removed it, and\n"
    "\t * it is the way back to the old selection without a reflash. */\n"
    "\ttrace_android_rvh_pick_next_entity(cfs_rq, curr, &se);\n"
    "\tif (se)\n"
    "\t\treturn se;\n"
    "\n"
    "\tif (sched_feat(EEVDF))\n"
    "\t\treturn abk_pick_eevdf(cfs_rq, curr);\n"
    "\n"
    "\tleft = __pick_first_entity(cfs_rq);\n"
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
    "\treturn se;\n"
    "}\n"
)

# wakeup_preempt_entity() and wakeup_gran(): probe-only, deliberately NOT steps.
#
# Batch 28 originally marked all three __maybe_unused because the EEVDF wakeup
# rule took away their last caller; Batch 28's repair keeps them live instead,
# as the !EEVDF half of check_preempt_wakeup().  Their pristine definitions are
# therefore already the target form -- same trap 1 as __pick_next_entity().
_FAIR_WPE_DECL = (
    "static int\n"
    "wakeup_preempt_entity(struct sched_entity *curr, struct sched_entity *se);\n"
)


_FAIR_WAKEUP_GRAN = (
    "static unsigned long wakeup_gran(struct sched_entity *se)\n"
    "{\n"
)


_FAIR_WPE_DEF = (
    "static int\n"
    "wakeup_preempt_entity(struct sched_entity *curr, struct sched_entity *se)\n"
    "{\n"
)


# Step 20: wakeup preemption asks the selector (upstream 147f3efaa241, v6.6).
# The anchor stops at the ``return;`` above the ``preempt:`` label, which
# sched_lazy_preemption_hooks wraps separately.
_FAIR_WAKEUP_PREEMPT = (
    "\tupdate_curr(cfs_rq_of(se));\n"
    "\ttrace_android_rvh_check_preempt_wakeup(rq, p, &preempt, &ignore,\n"
    "\t\t\twake_flags, se, pse, next_buddy_marked, sysctl_sched_wakeup_granularity);\n"
    "\tif (preempt)\n"
    "\t\tgoto preempt;\n"
    "\tif (ignore)\n"
    "\t\treturn;\n"
    "\n"
    "\tif (wakeup_preempt_entity(se, pse) == 1) {\n"
    "\t\t/*\n"
    "\t\t * Bias pick_next to pick the sched entity that is\n"
    "\t\t * triggering this preemption.\n"
    "\t\t */\n"
    "\t\tif (!next_buddy_marked)\n"
    "\t\t\tset_next_buddy(pse);\n"
    "\t\tgoto preempt;\n"
    "\t}\n"
    "\n"
    "\treturn;\n"
)

_FAIR_WAKEUP_PREEMPT_NEW = (
    "\tcfs_rq = cfs_rq_of(se);\n"
    "\tupdate_curr(cfs_rq);\n"
    "\ttrace_android_rvh_check_preempt_wakeup(rq, p, &preempt, &ignore,\n"
    "\t\t\twake_flags, se, pse, next_buddy_marked, sysctl_sched_wakeup_granularity);\n"
    "\tif (preempt)\n"
    "\t\tgoto preempt;\n"
    "\tif (ignore)\n"
    "\t\treturn;\n"
    "\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: EEVDF wakeup preemption (upstream\n"
    "\t * 147f3efaa241, v6.6).  The decision is the selector's, not the CFS\n"
    "\t * wakeup-granularity ladder's, so placement and preemption cannot\n"
    "\t * disagree -- an entity that place_entity() put ahead of current must\n"
    "\t * also be able to take the CPU from it.  PREEMPT_SHORT\n"
    "\t * (85e511df3cec, v6.12) reroutes into the same decision, and only\n"
    "\t * fires for an entity that really did ask for a shorter request.\n"
    "\t */\n"
    "\tif (sched_feat(EEVDF) &&\n"
    "\t    (abk_eevdf_preempt_short(cfs_rq, se, pse) ||\n"
    "\t     abk_pick_eevdf(cfs_rq, se) == pse)) {\n"
    "\t\t/*\n"
    "\t\t * Bias pick_next to pick the sched entity that is\n"
    "\t\t * triggering this preemption.\n"
    "\t\t */\n"
    "\t\tif (!next_buddy_marked)\n"
    "\t\t\tset_next_buddy(pse);\n"
    "\t\tgoto preempt;\n"
    "\t}\n"
    "\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: the CFS wakeup-granularity ladder,\n"
    "\t * reachable only with EEVDF off.  It is the companion half of the\n"
    "\t * sched_feat(EEVDF) switch pick_next_entity() carries, and it is\n"
    "\t * what keeps wakeup_preempt_entity()/wakeup_gran() live.\n"
    "\t */\n"
    "\tif (!sched_feat(EEVDF) && wakeup_preempt_entity(se, pse) == 1) {\n"
    "\t\tif (!next_buddy_marked)\n"
    "\t\t\tset_next_buddy(pse);\n"
    "\t\tgoto preempt;\n"
    "\t}\n"
    "\n"
    "\treturn;\n"
)

# Step 21: EEVDF yield (upstream 147f3efaa241's yield_task_fair() hunk, v6.6,
# plus the forfeit of 79104becf42b, v6.17).
_FAIR_YIELD = (
    "\tclear_buddies(cfs_rq, se);\n"
    "\n"
    "\tif (curr->policy != SCHED_BATCH) {\n"
    "\t\tupdate_rq_clock(rq);\n"
    "\t\t/*\n"
    "\t\t * Update run-time statistics of the 'current'.\n"
    "\t\t */\n"
    "\t\tupdate_curr(cfs_rq);\n"
    "\t\t/*\n"
    "\t\t * Tell update_rq_clock() that we've just updated,\n"
    "\t\t * so we don't do microscopic update in schedule()\n"
    "\t\t * and double the fastpath cost.\n"
    "\t\t */\n"
    "\t\trq_clock_skip_update(rq);\n"
    "\t}\n"
    "\n"
    "\tset_skip_buddy(se);\n"
    "}\n"
)

_FAIR_YIELD_NEW = (
    "\tclear_buddies(cfs_rq, se);\n"
    "\n"
    "\tupdate_rq_clock(rq);\n"
    "\t/*\n"
    "\t * Update run-time statistics of the 'current'.\n"
    "\t */\n"
    "\tupdate_curr(cfs_rq);\n"
    "\t/*\n"
    "\t * Tell update_rq_clock() that we've just updated,\n"
    "\t * so we don't do microscopic update in schedule()\n"
    "\t * and double the fastpath cost.\n"
    "\t */\n"
    "\trq_clock_skip_update(rq);\n"
    "\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: EEVDF yield (upstream 147f3efaa241, v6.6,\n"
    "\t * with the forfeit fix of 79104becf42b, v6.17).\n"
    "\t *\n"
    "\t * Forfeit the remaining request and push the deadline one slice\n"
    "\t * ahead, so yield actually hands the CPU over.  The forfeit is\n"
    "\t * guarded on eligibility because core scheduling prefers running an\n"
    "\t * ineligible task over force-idling: without the guard, a task that\n"
    "\t * keeps being re-picked while yielding would push its vruntime ahead\n"
    "\t * on every pass and run away.\n"
    "\t */\n"
    "\tif (abk_eevdf_eligible(se, avg_vruntime(cfs_rq))) {\n"
    "\t\tse->vruntime = se->deadline;\n"
    "\t\tse->deadline += abk_eevdf_vslice(cfs_rq, se);\n"
    "\t\tupdate_min_vruntime(cfs_rq);\n"
    "\t}\n"
    "\n"
    "\tset_skip_buddy(se);\n"
    "}\n"
)


# (label, pristine ``old`` block) for the uniqueness probe.  The labels are the
# report text when a real tree turns out to carry a context twice.
_FAIR_ANCHORS = (
    ("entity_before() / __node_2_se()", _FAIR_ENTITY_BEFORE),
    ("update_min_vruntime() body", _FAIR_MIN_VRUNTIME),
    ("__enqueue_entity()/__dequeue_entity()", _FAIR_ENQ_DEQ),
    ("sched_vslice() helper insert", _FAIR_VSLICE),
    ("update_curr() vruntime advance", _FAIR_UPDATE_CURR),
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
    ("wakeup_preempt_entity() declaration", _FAIR_WPE_DECL),
    ("wakeup_gran() definition", _FAIR_WAKEUP_GRAN),
    ("wakeup_preempt_entity() definition", _FAIR_WPE_DEF),
    ("check_preempt_wakeup() tail", _FAIR_WAKEUP_PREEMPT),
    ("yield_task_fair() body", _FAIR_YIELD),
)


def build_pick_logic_steps():
    """``sched_eevdf_pick_logic``: 21 ordered, all-required steps.

    Order matters in four places, and the steps are not split across groups:

    * step 1 must precede steps 2-4, which call the accumulators it inserts;
    * step 4 must precede steps 5 and 14-21, which call the helpers it inserts;
    * step 3 (the ``place_entity()`` forward declaration) must precede step 8,
      which calls ``place_entity()`` from ``reweight_entity()``;
    * step 11 introduces ``sleep``, which step 12 consumes; step 8 introduces
      ``queued`` and ``curr``, which step 9 consumes.

    Every step's ``new`` block is textually distinct from every other step's, so
    no step can satisfy another's idempotency check (``replace_once`` tests the
    replacement first) -- the failure mode ``docs/group_recipe.md`` calls trap 2.
    """
    return [
        (FAIR_C, _FAIR_ENTITY_BEFORE, _FAIR_ENTITY_BEFORE_NEW, T),
        (FAIR_C, _FAIR_MIN_VRUNTIME, _FAIR_MIN_VRUNTIME_NEW, T),
        (FAIR_C, _FAIR_ENQ_DEQ, _FAIR_ENQ_DEQ_NEW, T),
        (FAIR_C, _FAIR_VSLICE, _FAIR_VSLICE_NEW, T),
        (FAIR_C, _FAIR_UPDATE_CURR, _FAIR_UPDATE_CURR_NEW, T),
        # __pick_next_entity() has no step: the pristine text is already the
        # target form (see the note at its anchor).  Probe-only.
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
        # wakeup_preempt_entity()'s declaration: pristine form, no step needed.
        # wakeup_gran(): pristine form, no step needed (live under !EEVDF).
        # wakeup_preempt_entity()'s definition: pristine form, no step needed.
        (FAIR_C, _FAIR_WAKEUP_PREEMPT, _FAIR_WAKEUP_PREEMPT_NEW, T),
        (FAIR_C, _FAIR_YIELD, _FAIR_YIELD_NEW, T),
    ]


def build_core_fields_steps(text):
    """``sched_eevdf_core_fields``: exactly one step, for the shape ``text`` has.

    Four mutually exclusive shapes, so the selection happens here and the
    emitted step is ``required`` either way:

    * the Batch 28 claim is already in place -> ``old == new`` (already_present);
    * the Batch 15 form (slots 1-3, slot 4 released) -> upgraded;
    * the pre-release draft -> upgraded;
    * the pristine four-reserve run -> claimed.

    Returns ``None`` for a shape none of the four describe; the caller reports
    ``blocked_by_shape`` rather than guessing which reserve run to rewrite.
    """
    if _SCHED_H_SE_TAIL_NEW in text:
        return [(SCHED_H, _SCHED_H_SE_TAIL_NEW, _SCHED_H_SE_TAIL_NEW, T)]
    if _SCHED_H_SE_TAIL_V15 in text:
        return [(SCHED_H, _SCHED_H_SE_TAIL_V15, _SCHED_H_SE_TAIL_NEW, T)]
    if _SCHED_H_SE_TAIL_BROKEN in text:
        return [(SCHED_H, _SCHED_H_SE_TAIL_BROKEN, _SCHED_H_SE_TAIL_NEW, T)]
    if _SCHED_H_SE_TAIL in text:
        return [(SCHED_H, _SCHED_H_SE_TAIL, _SCHED_H_SE_TAIL_NEW, T)]
    return None


def build_modern_fields_steps():
    """``sched_eevdf_modern_fields``: accumulators, producer, switches.

    Three plain idempotent steps -- each ``old`` occurs exactly once in a
    pristine tree and each ``new`` occurs zero times, so a second pass reports
    ``already_present`` for all three.
    """
    return [
        (SCHED_H_INTERNAL, _SCHED_H_CFS_RQ, _SCHED_H_CFS_RQ_NEW, T),
        (CORE_C, _CORE_SETSCHED_PARAMS, _CORE_SETSCHED_PARAMS_NEW, T),
        (FEATURES_H, _FEATURES_TAIL, _FEATURES_TAIL_NEW, T),
    ]


def _occurrences(text, block):
    """Occurrence count of ``block`` in ``text``, EOL-insensitively."""
    return text.replace("\r\n", "\n").count(block.replace("\r\n", "\n"))


def _eevdf_core_fields_apply(ctx):
    try:
        text = ctx.read(SCHED_H)
    except FileNotFoundError:
        return "blocked_by_shape", f"{SCHED_H}: file absent"

    # Anti-drift gate.  The four slots encode state that only the fair.c half of
    # this family reads; claiming them without the logic would leave dead members
    # in a frozen-struct ABI.  Require the *payload* -- the marker, the
    # accumulators, the deadline refresh, the placement hook, the selector
    # routing and the run-to-parity stash -- not just the marker, because the
    # marker alone is written by the first step of the pick-logic group.
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
            "the four recognised shapes (pristine ANDROID_KABI_RESERVE(1..4) "
            "run, the pre-release packed draft, the Batch 15 form, the Batch 28 "
            "claim)"
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

    # Shape probe: the 5.15-vs-6.1 differences this module re-anchors.
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


def _eevdf_modern_fields_apply(ctx):
    """The cfs_rq accumulators + the two EEVDF switches.

    Gated on the same fair.c payload as the slot claim: the accumulators are
    read by abk_avg_vruntime_add()/sub() from __enqueue_entity()/
    __dequeue_entity() and by avg_vruntime(), and the switches are read from
    abk_pick_eevdf()/abk_eevdf_preempt_short().  Adding them to a tree without
    the fair.c half would be dead state (and the switches would be unused
    enum members, which is harmless but meaningless).
    """
    try:
        fair = ctx.read(FAIR_C)
    except FileNotFoundError:
        return "blocked_by_shape", (
            f"{FAIR_C}: file absent, so the EEVDF fair.c half cannot have "
            "landed; the cfs_rq accumulators and the EEVDF switches are not "
            "written"
        )
    absent = [probe for probe in _FAIR_LANDED_PROBES if probe not in fair]
    if absent:
        return "blocked_by_shape", (
            f"{FAIR_C} does not carry the EEVDF payload "
            f"({len(absent)}/{len(_FAIR_LANDED_PROBES)} probe(s) missing, "
            f"first: {absent[0]!r}); the cfs_rq accumulators and the EEVDF "
            "switches are not written"
        )

    for rel, blocks in (
        (SCHED_H_INTERNAL, (("cfs_rq accumulators", _SCHED_H_CFS_RQ),)),
        (FEATURES_H, (("EEVDF switches", _FEATURES_TAIL),)),
    ):
        try:
            text = ctx.read(rel)
        except FileNotFoundError:
            return "blocked_by_shape", f"{rel}: file absent"
        ambiguous = [
            (label, count) for label, block in blocks
            for count in (_occurrences(text, block),) if count > 1
        ]
        if ambiguous:
            return "blocked_by_shape", (
                f"{rel}: ambiguous anchor(s): "
                + ", ".join(f"{label} x{count}" for label, count in ambiguous)
            )

    status, _results, detail = apply_steps(ctx, build_modern_fields_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the three PatchGroup records, in registration order.

    ``sched_eevdf_pick_logic`` **must** be registered before
    ``sched_eevdf_core_fields`` and ``sched_eevdf_modern_fields``: both are gated
    on fair.c carrying the logic, which only holds once the pick-logic group has
    run in the same invocation (see the module docstring, item 4).  All three
    must come after every existing ``stable_perf_backport`` group that touches
    ``kernel/sched/fair.c`` (``sched_nohz_idle_balance_series``,
    ``sched_dst_group_allowed_stats``, ``sched_lazy_preemption_hooks``); no
    anchor is shared, so the order is a safety margin rather than a correctness
    requirement.
    """
    return [
        PatchGroup(
            "sched_eevdf_pick_logic",
            "EEVDF selection and runtime-state maintenance on the 5.15 rb-tree "
            "layout: the abk_eevdf_* helper set, the cfs_rq virtual-time "
            "accumulators that make avg_vruntime() O(1), and the rewiring of "
            "update_curr/update_min_vruntime/__enqueue_entity/__dequeue_entity/"
            "pick_next_entity/reweight_entity/enqueue_entity/dequeue_entity/"
            "place_entity/check_preempt_tick/set_next_entity/"
            "check_preempt_wakeup/yield_task_fair (ABK_ABI_PATCH_SUITE "
            "feature_porting/sched_eevdf_pick_logic, absorbed by Batch 15, "
            "rebuilt onto the upstream form by Batch 28)",
            [
                "ABK_ABI_PATCH_SUITE feature_porting/sched_eevdf_pick_logic "
                "(scan_based_eevdf_phase2)",
                "design: ABK_ABI_PATCH_SUITE/docs/feature_porting_eevdf_pid.md",
                "absorption: docs/survey_suite_absorption.md",
                "Batch 28 provenance: docs/survey_eevdf_gap.md "
                "(af4cf40470c2, 147f3efaa241, 63304558ba5d, ee4373dc902c, "
                "85e511df3cec, 79104becf42b, c40dd90ac045)",
            ],
            [FAIR_C],
            _sched_eevdf_pick_logic_apply,
        ),
        PatchGroup(
            "sched_eevdf_core_fields",
            "claim Android KABI reserve slots 1-4 of struct sched_entity for the "
            "EEVDF fields the fair.c half reads (u64 deadline, u64 min_vruntime, "
            "s64 vlag, u64 slice); sizeof(struct sched_entity) is unchanged and "
            "the slots stay ANDROID_KABI_RESERVE unless the fair.c logic landed",
            [
                "ABK_ABI_PATCH_SUITE feature_porting/sched_eevdf_core_fields",
                "KABI ownership: AGENTS.md red lines (Batch 15), "
                "docs/porting_policy.md \"Suite absorption\"",
                "slot 4 (u64 slice): Batch 28, docs/survey_eevdf_gap.md",
            ],
            [SCHED_H],
            _eevdf_core_fields_apply,
        ),
        PatchGroup(
            "sched_eevdf_modern_fields",
            "the struct cfs_rq virtual-time accumulators (s64 avg_vruntime, u64 "
            "avg_load, u64 abk_pick_deadline) that make avg_vruntime() O(1) and "
            "carry the run-to-parity stash, plus the RUN_TO_PARITY and "
            "PREEMPT_SHORT sched_feat switches; both files are gated on the "
            "fair.c half having landed",
            [
                "upstream af4cf40470c2 (v6.6) cfs_rq::avg_vruntime",
                "upstream 63304558ba5d (v6.6) RUN_TO_PARITY",
                "upstream 85e511df3cec (v6.12) PREEMPT_SHORT",
                "audit: docs/survey_eevdf_gap.md",
            ],
            [SCHED_H_INTERNAL, CORE_C, FEATURES_H],
            _eevdf_modern_fields_apply,
        ),
    ]
