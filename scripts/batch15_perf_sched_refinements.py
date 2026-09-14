"""Batch 15: scheduler refinements absorbed from ``ABK_ABI_PATCH_SUITE``.

Two groups, both lifted from ``ABK_ABI_PATCH_SUITE/scripts/abk_feature_porting.py``
and re-anchored to the android13-5.15 text shapes:

* ``nohz_field_refinement`` (suite lines 2099-2290) -- normalises the legacy
  ``struct tick_sched`` nohz state bits (``inidle`` / ``idle_active`` /
  ``tick_stopped``) behind one named enum plus accessors, and moves every read
  site in ``kernel/time/tick-sched.c`` onto that snapshot.
* ``avg_idle_preemption_mode`` (suite lines 2292-2412) -- retires the
  ``rq->wake_avg_idle`` wake-side idle prediction, moves the ``avg_idle``
  sample onto the switch-out-of-idle path, and re-sources the ``SIS_PROP``
  idle-scan budget from ``rq->avg_idle`` directly.

Both are *suite-shaped* refactors, not backports of a named upstream commit:
the suite's own ``PatchGroup`` declarations (lines 61-76 of that file) describe
them as intent, and neither helper name exists in the reference trees that were
read for this port (``update_rq_avg_idle`` occurs **0** times in both
``common-5.15-2024-11`` and ``common-6.1-src``).  The 6.1 reference tree still
carries the pristine ``wake_avg_idle`` / ``wake_stamp`` prediction and an empty
``put_prev_task_idle()``, so nothing here is "restoring an upstream shape" --
it is a deliberate policy change and is documented as such.

What was deliberately NOT ported (recorded so it is not relitigated):

* the suite's silent-anchor contract.  The suite guards every edit with
  ``if <sentinel> not in text`` and then ``text.replace(old, new, 1)``, so an
  anchor miss writes nothing and still reports success.  Every step here is
  ``required=True``: a miss writes nothing and reports
  ``blocked_by_missing_anchor``.  Consequence: two of the suite's hunks that
  only ever landed by accident on the 6.1 shape were re-derived rather than
  copied -- see the trap notes on ``_B_*`` / ``_E_*`` below.
* the suite's ``tick_nohz_tick_stopped_cpu()`` skip.  The suite's guard is
  ``if "return !!(abk_tick_nohz_state_flags(ts) & NOHZ_CPU_STATE_TICK_STOPPED);"
  not in tick_sched_text`` -- a sentinel that the *previous* step already
  installed -- so on the suite's own run the CPU-scoped variant is silently
  skipped and the file is left half-converted.  Here the two rewrites are
  textually distinct, so both land (that is also why ``_A_CPU_STOPPED_NEW``
  carries its own marker line).
* the suite's ``EXPORT_SYMBOL_GPL`` pair is kept verbatim (it is the suite's
  out-of-tree surface), but note that no in-tree caller outside
  ``tick-sched.c`` uses either symbol: the two exports are new KMI *additions*,
  not a reuse of anything.  ``kernel/sched/cpufreq_schedutil.c:314`` keeps
  calling ``tick_nohz_get_idle_calls_cpu()``, which now fronts
  ``nohz_cpu_idle_calls()``; ``kernel/irq_work.c:58``,
  ``kernel/sched/core.c:1106`` and ``kernel/sched/idle.c:234`` keep calling
  ``tick_nohz_tick_stopped()``.
* ``struct rq``'s ``wake_stamp`` / ``wake_avg_idle`` fields are **not**
  removed.  They become dead (nothing writes or reads them after
  ``avg_idle_preemption_mode``), but deleting them would move every field after
  them in ``struct rq``.  ``struct rq`` is not an exported/KMI struct, so this
  is a risk choice rather than a red line; keeping them costs 16 bytes per CPU
  and keeps the diff anchor-local.  A later batch may reclaim them.
* ``nohz_field_refinement``'s enum/accessors are wired into
  ``kernel/time/tick-sched.c`` only.  The suite declares
  ``nohz_cpu_inidle()`` / ``nohz_cpu_idle_active()`` / ``nohz_cpu_tick_stopped()``
  and never calls them anywhere; they are ported as declared-but-unused static
  inlines for surface parity, not as a scheduler-policy hook.

5.15 shape adaptations that the suite's 6.1 assumption gets wrong (each one was
checked against ``common-5.15-2024-11`` before being written):

1. ``update_rq_avg_idle()`` must keep the pristine ``if (rq->idle_stamp)``
   guard.  The suite's helper samples unconditionally:
   ``delta = rq_clock(rq) - rq->idle_stamp``.  On 5.15 ``idle_stamp`` is armed
   *by* ``newidle_balance()`` (``kernel/sched/fair.c:11169``), which the
   idle-entry pick path reaches with a non-NULL ``rf`` -- so on the first
   idle->busy switch of a CPU whose stamp was never armed (the boot CPU, or any
   CPU that entered the idle task without passing ``pick_next_task_fair()``'s
   idle label) ``idle_stamp`` is still 0 and the unconditional form samples
   ``rq_clock(rq) - 0``: nanoseconds since boot, clamped straight to
   ``2 * max_idle_balance_cost``.  The pristine wake path was guarded for
   exactly this reason, so the guard is restored here.  This is the pinned
   "compiles clean, behaves wrong on 5.15" class.
2. The sample moves from ``ttwu_do_wakeup()`` to ``put_prev_task_idle()`` and
   that *is* the right switch-out point on 5.15: for ``prev == idle``
   ``__pick_next_task()`` takes the ``restart:`` path
   (``kernel/sched/core.c:5782`` compares ``prev->sched_class <=
   &fair_sched_class``, and ``idle_sched_class`` sorts below ``fair_sched_class``),
   ``put_prev_task_balance()`` then runs an *empty* ``for_class_range``
   (``core.c:5758``, ``_from == _to == &idle_sched_class``) and calls
   ``put_prev_task()`` -> ``put_prev_task_idle()``.  ``newidle_balance()`` is
   not re-entered on that path (``pick_next_task_fair(rq, NULL, NULL)`` returns
   at its ``idle:`` label because ``rf == NULL``), so the stamp still describes
   the whole idle period when the helper runs -- the same interval
   ``ttwu_do_wakeup()`` used to measure, a few microseconds later.
3. ``sched_feat(SIS_UTIL)`` / ``sd_llc_shared->nr_idle_scan`` do not exist on
   5.15 (0 occurrences in ``kernel/sched/``), so the suite's 6.1 branch
   (delete the ``SIS_PROP`` arms, keep ``SIS_UTIL``) cannot be used: it would
   leave ``select_idle_cpu()`` with ``nr = INT_MAX`` and scan the entire LLC on
   every wakeup.  This port therefore keeps the ``SIS_PROP`` arm and only
   re-sources its budget (``avg_idle = this_rq->avg_idle / 2``) and drops the
   decay/aging writes.  Honest limitation: the pristine predictor *decayed*
   toward zero while the CPU stayed busy (``wake_stamp`` aging plus the
   per-scan ``wake_avg_idle -= min(wake_avg_idle, time)``); the replacement is
   a flat 50% of the last sampled idle period with no aging, so a CPU that has
   been busy for a long time can still budget a scan as if it had just been
   idle.  That is a policy tradeoff, not a correctness bug, and it is the price
   of retiring the field at all on this baseline.
4. ``put_prev_task_idle()`` is compiled unconditionally, but the ``struct rq``
   fields the helper touches live inside ``#ifdef CONFIG_SMP``
   (``kernel/sched/sched.h:1052-1059``, closed at 1064).  The helper definition
   and its ``sched.h`` declaration are therefore ``CONFIG_SMP``-guarded, with a
   static-inline no-op in the ``#else`` arm so ``idle.c`` needs no guard of its
   own.  The suite's unguarded form breaks a ``!CONFIG_SMP`` build.

Registration order: the two groups here are order-independent of each other
(disjoint file sets: nohz.h + tick-sched.c vs core.c/fair.c/idle.c/sched.h) and
of every group in ``scripts/abk_stable_perf.py``.  No anchor below occurs in any
other registered group: ``wake_avg_idle``, ``wake_stamp``,
``put_prev_task_idle``, ``cfs_bandwidth_usage_dec``, ``update_rq_avg_idle``,
``SIS_PROP`` and ``idle_stamp`` do not appear anywhere under ``scripts/``, and
no group lists ``kernel/sched/idle.c``.  ``sched_nohz_idle_balance_series``
touches ``kernel/sched/sched.h`` too, but its anchor starts at
``#define NOHZ_BALANCE_KICK_BIT`` (line 2786), three lines *below* this module's
``cfs_bandwidth_usage_dec`` anchor (line 2783), and the intervening
``#ifdef CONFIG_NO_HZ_COMMON`` belongs to neither anchor.  Register this batch
after ``stable_perf_backport`` only for report readability; correctness does not
depend on it.  Note that ``sched_nohz_idle_balance_series`` is already_present on
sublevels 178/194/216 (``tests/sublevel_matrix.py``), while these two groups are
synthetic and must never be listed in ``PRE_APPLIED``: every step's ``new``
block was checked to be absent from the pristine file in all four reference
trees.

See ``scripts/batch15_core_swap_table.py`` for the third suite feature
(``swap_table_phase2_large_folios``) and why it registers no group.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

NOHZ_H = "include/linux/sched/nohz.h"
TICK_SCHED_C = "kernel/time/tick-sched.c"
CORE_C = "kernel/sched/core.c"
FAIR_C = "kernel/sched/fair.c"
IDLE_C = "kernel/sched/idle.c"
SCHED_H = "kernel/sched/sched.h"

# Alias the required-flag for readability (the other batch modules use T/F).
T = True

_MARK_NOHZ = "/* ABK stable_515_backport: nohz tick_sched state field consistency helpers. */"
_MARK_AVG = "/* ABK stable_515_backport: avg_idle preemption mode simplification. */"
# Probe form of the avg_idle marker.  The fair.c replacement carries the marker
# as a multi-line block comment (``/*\n * ABK ... simplification.``), so a probe
# for the one-line spelling would fail on the second pass and the shape guard
# below would then refuse a tree this module had just grafted.  The subject
# substring matches both spellings.
_MARK_AVG_PROBE = "ABK stable_515_backport: avg_idle preemption mode simplification."

# --- nohz_field_refinement ------------------------------------------------
# Every block below was checked to occur exactly once in abk515_ref_{167,178,
# 194,211} (SUBLEVELs 167/178/194/216) and to be absent from the un-grafted
# text, which is what makes a required step safe.

_A_ENUM = (
    "/*\n"
    " * This is the interface between the scheduler and nohz/dynticks:\n"
    " */\n"
    "\n"
    "#if defined(CONFIG_SMP) && defined(CONFIG_NO_HZ_COMMON)\n"
)

_A_ENUM_NEW = (
    "/*\n"
    " * This is the interface between the scheduler and nohz/dynticks:\n"
    " */\n"
    "\n"
    "/*\n"
    " * ABK stable_515_backport: nohz tick_sched state field consistency\n"
    " * helpers.  The three legacy struct tick_sched bits (inidle, idle_active,\n"
    " * tick_stopped) are read as one named state mask so a reader that needs\n"
    " * two of them snapshots them consistently instead of re-probing the\n"
    " * bitfields one at a time.\n"
    " */\n"
    "enum nohz_cpu_state {\n"
    "\tNOHZ_CPU_STATE_NONE = 0,\n"
    "\tNOHZ_CPU_STATE_INIDLE = 1U << 0,\n"
    "\tNOHZ_CPU_STATE_IDLE_ACTIVE = 1U << 1,\n"
    "\tNOHZ_CPU_STATE_TICK_STOPPED = 1U << 2,\n"
    "};\n"
    "\n"
    "#if defined(CONFIG_SMP) && defined(CONFIG_NO_HZ_COMMON)\n"
)

# Trap 1 note: the accessor block keeps the header's own ``#endif`` in both the
# anchor and the replacement.  A pure "insert before the guard" step whose
# ``new`` is just the inserted text is fine here, but a *deletion-style* step
# would report already_present -- see the ``_B_*`` steps below for the
# surrounding-context discipline that avoids that.
_A_ACCESSORS = "\n#endif /* _LINUX_SCHED_NOHZ_H */\n"

_A_ACCESSORS_NEW = (
    "\n"
    "/*\n"
    " * ABK stable_515_backport: nohz tick_sched state field consistency\n"
    " * helpers.  nohz_cpu_state_flags() is the only reader of the legacy\n"
    " * bitfields outside kernel/time/tick-sched.c; the per-state predicates\n"
    " * exist so a caller states which bit it needs instead of open-coding a\n"
    " * mask test.\n"
    " */\n"
    "#ifdef CONFIG_NO_HZ_COMMON\n"
    "extern unsigned int nohz_cpu_state_flags(int cpu);\n"
    "extern unsigned long nohz_cpu_idle_calls(int cpu);\n"
    "\n"
    "static inline bool nohz_cpu_state_test(int cpu, unsigned int state)\n"
    "{\n"
    "\treturn nohz_cpu_state_flags(cpu) & state;\n"
    "}\n"
    "\n"
    "static inline bool nohz_cpu_inidle(int cpu)\n"
    "{\n"
    "\treturn nohz_cpu_state_test(cpu, NOHZ_CPU_STATE_INIDLE);\n"
    "}\n"
    "\n"
    "static inline bool nohz_cpu_idle_active(int cpu)\n"
    "{\n"
    "\treturn nohz_cpu_state_test(cpu, NOHZ_CPU_STATE_IDLE_ACTIVE);\n"
    "}\n"
    "\n"
    "static inline bool nohz_cpu_tick_stopped(int cpu)\n"
    "{\n"
    "\treturn nohz_cpu_state_test(cpu, NOHZ_CPU_STATE_TICK_STOPPED);\n"
    "}\n"
    "#else\n"
    "static inline unsigned int nohz_cpu_state_flags(int cpu)\n"
    "{\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
    "static inline unsigned long nohz_cpu_idle_calls(int cpu)\n"
    "{\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
    "static inline bool nohz_cpu_state_test(int cpu, unsigned int state)\n"
    "{\n"
    "\treturn false;\n"
    "}\n"
    "\n"
    "static inline bool nohz_cpu_inidle(int cpu)\n"
    "{\n"
    "\treturn false;\n"
    "}\n"
    "\n"
    "static inline bool nohz_cpu_idle_active(int cpu)\n"
    "{\n"
    "\treturn false;\n"
    "}\n"
    "\n"
    "static inline bool nohz_cpu_tick_stopped(int cpu)\n"
    "{\n"
    "\treturn false;\n"
    "}\n"
    "#endif\n"
    "\n"
    "#endif /* _LINUX_SCHED_NOHZ_H */\n"
)

# The helper insert and the ``tick_nohz_tick_stopped()`` rewrite are one step:
# the amendment to that function lives inside text the same anchor reproduces,
# and splitting them would make step N's ``new`` a prefix of step N+1's ``old``
# (step-authoring trap 1).  ``__setup("nohz=", setup_tick_nohz);`` pins the
# insertion to the ``CONFIG_NO_HZ_COMMON`` section of the file.
_A_TICK_STOPPED = (
    "__setup(\"nohz=\", setup_tick_nohz);\n"
    "\n"
    "bool tick_nohz_tick_stopped(void)\n"
    "{\n"
    "\tstruct tick_sched *ts = this_cpu_ptr(&tick_cpu_sched);\n"
    "\n"
    "\treturn ts->tick_stopped;\n"
    "}\n"
)

_A_TICK_STOPPED_NEW = (
    "__setup(\"nohz=\", setup_tick_nohz);\n"
    "\n"
    "/* ABK stable_515_backport: nohz tick_sched state field consistency helpers. */\n"
    "static unsigned int abk_tick_nohz_state_flags(const struct tick_sched *ts)\n"
    "{\n"
    "\tunsigned int state = NOHZ_CPU_STATE_NONE;\n"
    "\n"
    "\tif (ts->inidle)\n"
    "\t\tstate |= NOHZ_CPU_STATE_INIDLE;\n"
    "\tif (ts->idle_active)\n"
    "\t\tstate |= NOHZ_CPU_STATE_IDLE_ACTIVE;\n"
    "\tif (ts->tick_stopped)\n"
    "\t\tstate |= NOHZ_CPU_STATE_TICK_STOPPED;\n"
    "\n"
    "\treturn state;\n"
    "}\n"
    "\n"
    "unsigned int nohz_cpu_state_flags(int cpu)\n"
    "{\n"
    "\treturn abk_tick_nohz_state_flags(tick_get_tick_sched(cpu));\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(nohz_cpu_state_flags);\n"
    "\n"
    "unsigned long nohz_cpu_idle_calls(int cpu)\n"
    "{\n"
    "\treturn tick_get_tick_sched(cpu)->idle_calls;\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(nohz_cpu_idle_calls);\n"
    "\n"
    "bool tick_nohz_tick_stopped(void)\n"
    "{\n"
    "\tstruct tick_sched *ts = this_cpu_ptr(&tick_cpu_sched);\n"
    "\n"
    "\treturn !!(abk_tick_nohz_state_flags(ts) & NOHZ_CPU_STATE_TICK_STOPPED);\n"
    "}\n"
)

# Trap 2 note: this replacement deliberately carries its own marker line and
# therefore does NOT contain the whole replacement above.  A shared
# ``return !!(abk_tick_nohz_state_flags(ts) & ...)`` sentinel is what made the
# suite skip this function entirely.
_A_CPU_STOPPED = (
    "bool tick_nohz_tick_stopped_cpu(int cpu)\n"
    "{\n"
    "\tstruct tick_sched *ts = per_cpu_ptr(&tick_cpu_sched, cpu);\n"
    "\n"
    "\treturn ts->tick_stopped;\n"
    "}\n"
)

_A_CPU_STOPPED_NEW = (
    "bool tick_nohz_tick_stopped_cpu(int cpu)\n"
    "{\n"
    "\tstruct tick_sched *ts = per_cpu_ptr(&tick_cpu_sched, cpu);\n"
    "\n"
    "\t/* ABK stable_515_backport: CPU-scoped form of the nohz state probe. */\n"
    "\treturn !!(abk_tick_nohz_state_flags(ts) & NOHZ_CPU_STATE_TICK_STOPPED);\n"
    "}\n"
)

_A_IRQ_EXIT = (
    "void tick_nohz_irq_exit(void)\n"
    "{\n"
    "\tstruct tick_sched *ts = this_cpu_ptr(&tick_cpu_sched);\n"
    "\n"
    "\tif (ts->inidle)\n"
    "\t\ttick_nohz_start_idle(ts);\n"
    "\telse\n"
    "\t\ttick_nohz_full_update_tick(ts);\n"
    "}\n"
)

_A_IRQ_EXIT_NEW = (
    "void tick_nohz_irq_exit(void)\n"
    "{\n"
    "\tstruct tick_sched *ts = this_cpu_ptr(&tick_cpu_sched);\n"
    "\n"
    "\tif (abk_tick_nohz_state_flags(ts) & NOHZ_CPU_STATE_INIDLE)\n"
    "\t\ttick_nohz_start_idle(ts);\n"
    "\telse\n"
    "\t\ttick_nohz_full_update_tick(ts);\n"
    "}\n"
)

# Trap 1 note: ``\tWARN_ON_ONCE(!ts->inidle);\n`` occurs TWICE in the pristine
# file (tick_nohz_get_sleep_length() and tick_nohz_idle_exit()); the suite's
# one-line anchor only ever hit the first one by accident of ordering.  The
# ``*delta_next`` line following it makes this occurrence unique.
_A_SLEEP_WARN = (
    "\tWARN_ON_ONCE(!ts->inidle);\n"
    "\n"
    "\t*delta_next = ktime_sub(dev->next_event, now);\n"
)

_A_SLEEP_WARN_NEW = (
    "\tWARN_ON_ONCE(!(abk_tick_nohz_state_flags(ts) & NOHZ_CPU_STATE_INIDLE));\n"
    "\n"
    "\t*delta_next = ktime_sub(dev->next_event, now);\n"
)

_A_IDLE_CALLS_CPU = (
    "unsigned long tick_nohz_get_idle_calls_cpu(int cpu)\n"
    "{\n"
    "\tstruct tick_sched *ts = tick_get_tick_sched(cpu);\n"
    "\n"
    "\treturn ts->idle_calls;\n"
    "}\n"
)

_A_IDLE_CALLS_CPU_NEW = (
    "unsigned long tick_nohz_get_idle_calls_cpu(int cpu)\n"
    "{\n"
    "\treturn nohz_cpu_idle_calls(cpu);\n"
    "}\n"
)

_A_IDLE_CALLS = (
    "unsigned long tick_nohz_get_idle_calls(void)\n"
    "{\n"
    "\tstruct tick_sched *ts = this_cpu_ptr(&tick_cpu_sched);\n"
    "\n"
    "\treturn ts->idle_calls;\n"
    "}\n"
)

_A_IDLE_CALLS_NEW = (
    "unsigned long tick_nohz_get_idle_calls(void)\n"
    "{\n"
    "\treturn nohz_cpu_idle_calls(smp_processor_id());\n"
    "}\n"
)

# The snapshot is taken before ``ts->inidle = 0`` and only ``inidle`` is
# cleared by that store, so deriving idle_active/tick_stopped from the
# pre-clear mask is byte-equivalent to the pristine back-to-back field reads.
_A_IDLE_EXIT = (
    "void tick_nohz_idle_exit(void)\n"
    "{\n"
    "\tstruct tick_sched *ts = this_cpu_ptr(&tick_cpu_sched);\n"
    "\tbool idle_active, tick_stopped;\n"
    "\tktime_t now;\n"
    "\n"
    "\tlocal_irq_disable();\n"
    "\n"
    "\tWARN_ON_ONCE(!ts->inidle);\n"
    "\tWARN_ON_ONCE(ts->timer_expires_base);\n"
    "\n"
    "\tts->inidle = 0;\n"
    "\tidle_active = ts->idle_active;\n"
    "\ttick_stopped = ts->tick_stopped;\n"
    "\n"
    "\tif (idle_active || tick_stopped)\n"
    "\t\tnow = ktime_get();\n"
    "\n"
    "\tif (idle_active)\n"
    "\t\ttick_nohz_stop_idle(ts, now);\n"
    "\n"
    "\tif (tick_stopped)\n"
    "\t\ttick_nohz_idle_update_tick(ts, now);\n"
    "\n"
    "\tlocal_irq_enable();\n"
    "}\n"
)

_A_IDLE_EXIT_NEW = (
    "void tick_nohz_idle_exit(void)\n"
    "{\n"
    "\tstruct tick_sched *ts = this_cpu_ptr(&tick_cpu_sched);\n"
    "\tbool idle_active, tick_stopped;\n"
    "\tunsigned int nohz_state;\n"
    "\tktime_t now;\n"
    "\n"
    "\tlocal_irq_disable();\n"
    "\n"
    "\tnohz_state = abk_tick_nohz_state_flags(ts);\n"
    "\tWARN_ON_ONCE(!(nohz_state & NOHZ_CPU_STATE_INIDLE));\n"
    "\tWARN_ON_ONCE(ts->timer_expires_base);\n"
    "\n"
    "\tts->inidle = 0;\n"
    "\tidle_active = !!(nohz_state & NOHZ_CPU_STATE_IDLE_ACTIVE);\n"
    "\ttick_stopped = !!(nohz_state & NOHZ_CPU_STATE_TICK_STOPPED);\n"
    "\n"
    "\tif (idle_active || tick_stopped)\n"
    "\t\tnow = ktime_get();\n"
    "\n"
    "\tif (idle_active)\n"
    "\t\ttick_nohz_stop_idle(ts, now);\n"
    "\n"
    "\tif (tick_stopped)\n"
    "\t\ttick_nohz_idle_update_tick(ts, now);\n"
    "\n"
    "\tlocal_irq_enable();\n"
    "}\n"
)


# --- avg_idle_preemption_mode ---------------------------------------------
# rq->idle_stamp lifecycle on 5.15 (verified): armed by newidle_balance()
# (fair.c:11169) on the idle-entry path, consumed at the idle->busy switch.
# The helper keeps the pristine ``if (rq->idle_stamp)`` guard -- see item 1 of
# the module docstring for why dropping it is a real bug on this tree.

_B_HELPER = (
    "/*\n"
    " * Mark the task runnable and perform wakeup-preemption.\n"
    " */\n"
    "static void ttwu_do_wakeup(struct rq *rq, struct task_struct *p, int wake_flags,\n"
)

_B_HELPER_NEW = (
    "#ifdef CONFIG_SMP\n"
    "/*\n"
    " * ABK stable_515_backport: avg_idle preemption mode simplification.\n"
    " *\n"
    " * The wake-side idle prediction (rq->wake_avg_idle / rq->wake_stamp) is\n"
    " * retired, so the idle-duration sample moves to the single point where the\n"
    " * idle task stops being current.  put_prev_task_idle() is reached from\n"
    " * __pick_next_task()'s restart path with prev == idle, and\n"
    " * newidle_balance() does not run again on that path, so rq->idle_stamp\n"
    " * still describes the whole idle period when this executes.\n"
    " *\n"
    " * The guard is load-bearing: idle_stamp is only armed by\n"
    " * newidle_balance(), and a CPU that reached the idle task without passing\n"
    " * through it (the boot CPU, or an idle->idle repick) would otherwise\n"
    " * sample rq_clock(rq) - 0 here.\n"
    " */\n"
    "void update_rq_avg_idle(struct rq *rq)\n"
    "{\n"
    "\tu64 delta, max;\n"
    "\n"
    "\tif (!rq->idle_stamp)\n"
    "\t\treturn;\n"
    "\n"
    "\tdelta = rq_clock(rq) - rq->idle_stamp;\n"
    "\tmax = 2 * rq->max_idle_balance_cost;\n"
    "\n"
    "\tupdate_avg(&rq->avg_idle, delta);\n"
    "\n"
    "\tif (rq->avg_idle > max)\n"
    "\t\trq->avg_idle = max;\n"
    "\trq->idle_stamp = 0;\n"
    "}\n"
    "#endif /* CONFIG_SMP */\n"
    "\n"
    "/*\n"
    " * Mark the task runnable and perform wakeup-preemption.\n"
    " */\n"
    "static void ttwu_do_wakeup(struct rq *rq, struct task_struct *p, int wake_flags,\n"
)

# Trap 1 note: a "delete the idle_stamp block" step whose ``new`` were just
# ``\t}\n\n#endif\n`` would be found in the pristine file hundreds of times and
# report already_present without landing.  The replacement therefore keeps the
# unique surrounding statements (``rq_repin_lock`` before, ``ttwu_do_activate``
# after) and only drops the middle.
_B_TTWU = (
    "\t\trq_repin_lock(rq, rf);\n"
    "\t}\n"
    "\n"
    "\tif (rq->idle_stamp) {\n"
    "\t\tu64 delta = rq_clock(rq) - rq->idle_stamp;\n"
    "\t\tu64 max = 2*rq->max_idle_balance_cost;\n"
    "\n"
    "\t\tupdate_avg(&rq->avg_idle, delta);\n"
    "\n"
    "\t\tif (rq->avg_idle > max)\n"
    "\t\t\trq->avg_idle = max;\n"
    "\n"
    "\t\trq->wake_stamp = jiffies;\n"
    "\t\trq->wake_avg_idle = rq->avg_idle / 2;\n"
    "\n"
    "\t\trq->idle_stamp = 0;\n"
    "\t}\n"
    "#endif\n"
    "}\n"
    "\n"
    "static void\n"
    "ttwu_do_activate(struct rq *rq, struct task_struct *p, int wake_flags,\n"
)

_B_TTWU_NEW = (
    "\t\trq_repin_lock(rq, rf);\n"
    "\t}\n"
    "#endif\n"
    "}\n"
    "\n"
    "static void\n"
    "ttwu_do_activate(struct rq *rq, struct task_struct *p, int wake_flags,\n"
)

_B_INIT = (
    "\t\trq->idle_stamp = 0;\n"
    "\t\trq->avg_idle = 2*sysctl_sched_migration_cost;\n"
    "\t\trq->wake_stamp = jiffies;\n"
    "\t\trq->wake_avg_idle = rq->avg_idle;\n"
    "\t\trq->max_idle_balance_cost = sysctl_sched_migration_cost;\n"
)

_B_INIT_NEW = (
    "\t\trq->idle_stamp = 0;\n"
    "\t\trq->avg_idle = 2*sysctl_sched_migration_cost;\n"
    "\t\trq->max_idle_balance_cost = sysctl_sched_migration_cost;\n"
)

_B_SELECT_HEAD = (
    "static int select_idle_cpu(struct task_struct *p, struct sched_domain *sd, bool has_idle_core, int target)\n"
    "{\n"
    "\tstruct cpumask *cpus = this_cpu_cpumask_var_ptr(select_idle_mask);\n"
    "\tint i, cpu, idle_cpu = -1, nr = INT_MAX;\n"
    "\tstruct rq *this_rq = this_rq();\n"
    "\tint this = smp_processor_id();\n"
    "\tstruct sched_domain *this_sd;\n"
    "\tu64 time = 0;\n"
    "\n"
    "\tthis_sd = rcu_dereference(*this_cpu_ptr(&sd_llc));\n"
    "\tif (!this_sd)\n"
    "\t\treturn -1;\n"
    "\n"
    "\tcpumask_and(cpus, sched_domain_span(sd), p->cpus_ptr);\n"
    "\n"
    "\tif (sched_feat(SIS_PROP) && !has_idle_core) {\n"
    "\t\tu64 avg_cost, avg_idle, span_avg;\n"
    "\t\tunsigned long now = jiffies;\n"
    "\n"
    "\t\t/*\n"
    "\t\t * If we're busy, the assumption that the last idle period\n"
    "\t\t * predicts the future is flawed; age away the remaining\n"
    "\t\t * predicted idle time.\n"
    "\t\t */\n"
    "\t\tif (unlikely(this_rq->wake_stamp < now)) {\n"
    "\t\t\twhile (this_rq->wake_stamp < now && this_rq->wake_avg_idle) {\n"
    "\t\t\t\tthis_rq->wake_stamp++;\n"
    "\t\t\t\tthis_rq->wake_avg_idle >>= 1;\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\n"
    "\t\tavg_idle = this_rq->wake_avg_idle;\n"
    "\t\tavg_cost = this_sd->avg_scan_cost + 1;\n"
    "\n"
    "\t\tspan_avg = sd->span_weight * avg_idle;\n"
    "\t\tif (span_avg > 4*avg_cost)\n"
    "\t\t\tnr = div_u64(span_avg, avg_cost);\n"
    "\t\telse\n"
    "\t\t\tnr = 4;\n"
    "\n"
    "\t\ttime = cpu_clock(this);\n"
    "\t}\n"
)

_B_SELECT_HEAD_NEW = (
    "static int select_idle_cpu(struct task_struct *p, struct sched_domain *sd, bool has_idle_core, int target)\n"
    "{\n"
    "\tstruct cpumask *cpus = this_cpu_cpumask_var_ptr(select_idle_mask);\n"
    "\tint i, cpu, idle_cpu = -1, nr = INT_MAX;\n"
    "\tstruct rq *this_rq = this_rq();\n"
    "\tint this = smp_processor_id();\n"
    "\tstruct sched_domain *this_sd;\n"
    "\tu64 time = 0;\n"
    "\n"
    "\tthis_sd = rcu_dereference(*this_cpu_ptr(&sd_llc));\n"
    "\tif (!this_sd)\n"
    "\t\treturn -1;\n"
    "\n"
    "\tcpumask_and(cpus, sched_domain_span(sd), p->cpus_ptr);\n"
    "\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: avg_idle preemption mode simplification.\n"
    "\t * This tree has no SIS_UTIL and no sd_llc_shared->nr_idle_scan, so the\n"
    "\t * SIS_PROP scan budget is kept and re-sourced from the rq's own\n"
    "\t * avg_idle sample instead of the retired rq->wake_avg_idle prediction.\n"
    "\t * There is no aging term left: rq->avg_idle is only refreshed when the\n"
    "\t * CPU actually leaves idle, so a long-busy CPU budgets a scan from its\n"
    "\t * last idle period.\n"
    "\t */\n"
    "\tif (sched_feat(SIS_PROP) && !has_idle_core) {\n"
    "\t\tu64 avg_cost, avg_idle, span_avg;\n"
    "\n"
    "\t\tavg_idle = this_rq->avg_idle / 2;\n"
    "\t\tavg_cost = this_sd->avg_scan_cost + 1;\n"
    "\n"
    "\t\tspan_avg = sd->span_weight * avg_idle;\n"
    "\t\tif (span_avg > 4*avg_cost)\n"
    "\t\t\tnr = div_u64(span_avg, avg_cost);\n"
    "\t\telse\n"
    "\t\t\tnr = 4;\n"
    "\n"
    "\t\ttime = cpu_clock(this);\n"
    "\t}\n"
)

_B_SELECT_TAIL = (
    "\tif (sched_feat(SIS_PROP) && !has_idle_core) {\n"
    "\t\ttime = cpu_clock(this) - time;\n"
    "\n"
    "\t\t/*\n"
    "\t\t * Account for the scan cost of wakeups against the average\n"
    "\t\t * idle time.\n"
    "\t\t */\n"
    "\t\tthis_rq->wake_avg_idle -= min(this_rq->wake_avg_idle, time);\n"
    "\n"
    "\t\tupdate_avg(&this_sd->avg_scan_cost, time);\n"
    "\t}\n"
)

_B_SELECT_TAIL_NEW = (
    "\tif (sched_feat(SIS_PROP) && !has_idle_core) {\n"
    "\t\ttime = cpu_clock(this) - time;\n"
    "\n"
    "\t\t/*\n"
    "\t\t * ABK stable_515_backport: avg_idle preemption mode\n"
    "\t\t * simplification -- only sd->avg_scan_cost is accounted, since\n"
    "\t\t * rq->wake_avg_idle is no longer maintained on this tree.\n"
    "\t\t */\n"
    "\t\tupdate_avg(&this_sd->avg_scan_cost, time);\n"
    "\t}\n"
)

_B_IDLE_PUT_PREV = (
    "static void put_prev_task_idle(struct rq *rq, struct task_struct *prev)\n"
    "{\n"
    "}\n"
)

_B_IDLE_PUT_PREV_NEW = (
    "static void put_prev_task_idle(struct rq *rq, struct task_struct *prev)\n"
    "{\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: avg_idle preemption mode simplification.\n"
    "\t * This is where the retired ttwu_do_wakeup() idle-duration sample\n"
    "\t * now happens: rq->idle_stamp is still armed from the last\n"
    "\t * newidle_balance(), and it is cleared by the helper.\n"
    "\t */\n"
    "\tupdate_rq_avg_idle(rq);\n"
    "}\n"
)

# Declaring the helper on sched.h needs a CONFIG_SMP arm because the rq fields
# it touches are inside ``#ifdef CONFIG_SMP`` in this same header, while
# put_prev_task_idle() itself is compiled unconditionally.
_B_SCHED_H = (
    "extern void cfs_bandwidth_usage_inc(void);\n"
    "extern void cfs_bandwidth_usage_dec(void);\n"
)

_B_SCHED_H_NEW = (
    "extern void cfs_bandwidth_usage_inc(void);\n"
    "extern void cfs_bandwidth_usage_dec(void);\n"
    "\n"
    "#ifdef CONFIG_SMP\n"
    "/* ABK stable_515_backport: avg_idle preemption mode simplification. */\n"
    "extern void update_rq_avg_idle(struct rq *rq);\n"
    "#else\n"
    "static inline void update_rq_avg_idle(struct rq *rq) { }\n"
    "#endif /* CONFIG_SMP */\n"
)


def build_nohz_steps():
    """``nohz_field_refinement``: the legacy tick_sched state triplet, named.

    Order matters: the enum lands in the header (step 1), the accessors land
    after it (step 2), and only then does tick-sched.c start using them, so
    every intermediate state is a compilable tree.  Step 3 both inserts the
    implementation and rewrites ``tick_nohz_tick_stopped()`` -- they share one
    anchor and splitting them would make one ``new`` a prefix of the other
    step's ``old`` (step-authoring trap 1).
    """
    return [
        (NOHZ_H, _A_ENUM, _A_ENUM_NEW, T),
        (NOHZ_H, _A_ACCESSORS, _A_ACCESSORS_NEW, T),
        (TICK_SCHED_C, _A_TICK_STOPPED, _A_TICK_STOPPED_NEW, T),
        (TICK_SCHED_C, _A_CPU_STOPPED, _A_CPU_STOPPED_NEW, T),
        (TICK_SCHED_C, _A_IRQ_EXIT, _A_IRQ_EXIT_NEW, T),
        (TICK_SCHED_C, _A_SLEEP_WARN, _A_SLEEP_WARN_NEW, T),
        (TICK_SCHED_C, _A_IDLE_CALLS_CPU, _A_IDLE_CALLS_CPU_NEW, T),
        (TICK_SCHED_C, _A_IDLE_CALLS, _A_IDLE_CALLS_NEW, T),
        (TICK_SCHED_C, _A_IDLE_EXIT, _A_IDLE_EXIT_NEW, T),
    ]


def build_avg_idle_steps():
    """``avg_idle_preemption_mode``: retire ``rq->wake_avg_idle``.

    Seven steps across four files.  Every replacement is textually distinct
    from every other one in this module (step-authoring trap 2): the helper
    body in core.c does not repeat the fair.c scan-budget comment, and the two
    fair.c replacements spell their markers differently.
    """
    return [
        (CORE_C, _B_HELPER, _B_HELPER_NEW, T),
        (CORE_C, _B_TTWU, _B_TTWU_NEW, T),
        (CORE_C, _B_INIT, _B_INIT_NEW, T),
        (FAIR_C, _B_SELECT_HEAD, _B_SELECT_HEAD_NEW, T),
        (FAIR_C, _B_SELECT_TAIL, _B_SELECT_TAIL_NEW, T),
        (IDLE_C, _B_IDLE_PUT_PREV, _B_IDLE_PUT_PREV_NEW, T),
        (SCHED_H, _B_SCHED_H, _B_SCHED_H_NEW, T),
    ]


def _nohz_apply(ctx):
    try:
        tick_text = ctx.read(TICK_SCHED_C)
        nohz_text = ctx.read(NOHZ_H)
    except FileNotFoundError as exc:
        return "blocked_by_shape", f"{exc}: file absent"
    # Shape probe only: the enum is meaningless without the header's own guard,
    # and the tick-sched.c hunks are all inside the CONFIG_NO_HZ_COMMON section
    # that ``__setup("nohz=", ...)`` belongs to.  Nothing here probes for
    # whether the graft already ran -- ``replace_once`` answers that per step,
    # and the marker comments are the idempotency anchors.
    for probe, text, why in (
        ("#endif /* _LINUX_SCHED_NOHZ_H */", nohz_text, NOHZ_H),
        ("__setup(\"nohz=\", setup_tick_nohz);", tick_text, TICK_SCHED_C),
        ("bool tick_nohz_tick_stopped(void)", tick_text, TICK_SCHED_C),
    ):
        if probe not in text:
            return "blocked_by_shape", (
                f"{probe!r} not found in {why}; this does not look like the "
                "5.15 nohz header / tick-sched.c pair")
    # A tree may already carry some *other* nohz state-helper owner.  Once this
    # module's graft is in place the accessor exists AND its marker is in
    # tick-sched.c; the accessor without the marker is a shape this module
    # cannot name (re-defining it would collide), so refuse instead of
    # half-writing the enum.
    if "nohz_cpu_state_flags" in nohz_text and _MARK_NOHZ not in tick_text:
        return "blocked_by_shape", (
            "nohz.h already declares nohz_cpu_state_flags() without the ABK "
            "marker in tick-sched.c: the tree carries an unrecognised nohz "
            "state-helper owner; nothing was written")
    status, _results, detail = apply_steps(ctx, build_nohz_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _avg_idle_apply(ctx):
    try:
        core_text = ctx.read(CORE_C)
        fair_text = ctx.read(FAIR_C)
        idle_text = ctx.read(IDLE_C)
        sched_h_text = ctx.read(SCHED_H)
    except FileNotFoundError as exc:
        return "blocked_by_shape", f"{exc}: file absent"
    # Shape probe: the fair.c rewrites are keyed to the 5.15 SIS_PROP-only
    # select_idle_cpu().  sched_feat(SIS_UTIL) and sd_llc_shared->nr_idle_scan
    # are 6.1 additions (0 occurrences anywhere in 5.15 kernel/sched/), and the
    # suite's 6.1 branch deletes the SIS_PROP arms instead.  Applying this
    # module's 5.15 replacements to a SIS_UTIL tree would leave the scan
    # unbounded, so refuse the shape instead of guessing.
    if "sched_feat(SIS_UTIL)" in fair_text:
        return "blocked_by_shape", (
            "select_idle_cpu() carries SIS_UTIL (6.1+ shape), not the 5.15 "
            "SIS_PROP-only scan; this group's fair.c replacements are keyed to "
            "the 5.15 shape, so nothing was written")
    # A tree may already carry some *other* average-idle rewrite.  Once this
    # module's own graft is in place the prediction is gone AND the marker is
    # present; prediction gone with no marker is a shape this module cannot
    # name, and taking the plain path there would fail loudly on a later step
    # anyway -- name it explicitly instead.
    if "this_rq->wake_avg_idle" not in fair_text and _MARK_AVG_PROBE not in fair_text:
        return "blocked_by_shape", (
            "select_idle_cpu() has no rq->wake_avg_idle prediction and no ABK "
            "avg_idle marker: the tree carries an unrecognised avg_idle shape; "
            "nothing was written")
    for probe, text, why in (
        ("static void ttwu_do_wakeup(struct rq *rq", core_text, CORE_C),
        ("static int select_idle_cpu(struct task_struct *p", fair_text, FAIR_C),
        ("static void put_prev_task_idle(struct rq *rq", idle_text, IDLE_C),
        ("extern void cfs_bandwidth_usage_dec(void);", sched_h_text, SCHED_H),
    ):
        if probe not in text:
            return "blocked_by_shape", f"{probe} not found in {why}"
    status, _results, detail = apply_steps(ctx, build_avg_idle_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the two PatchGroup records, in registration order."""
    return [
        PatchGroup(
            "nohz_field_refinement",
            "normalise the legacy tick_sched nohz state bits (inidle / "
            "idle_active / tick_stopped) behind an enum plus accessors, and "
            "move every read site in kernel/time/tick-sched.c onto one state "
            "snapshot (ABK_ABI_PATCH_SUITE nohz_field_refinement, re-anchored "
            "to 5.15; the suite's CPU-scoped tick_stopped rewrite, which its "
            "own sentinel guard silently skipped, is included here)",
            [
                "ABK_ABI_PATCH_SUITE scripts/abk_feature_porting.py:"
                "patch_nohz_field_refinement() (group nohz_field_refinement)",
                "no upstream commit: the helper names occur 0 times in "
                "common-5.15-2024-11 and in common-6.1-src",
            ],
            [NOHZ_H, TICK_SCHED_C],
            _nohz_apply,
        ),
        PatchGroup(
            "avg_idle_preemption_mode",
            "retire the rq->wake_avg_idle wake-side idle prediction: sample "
            "avg_idle at the switch-out-of-idle point instead, keep the direct "
            "avg_idle newidle thresholds, and re-source the SIS_PROP idle-scan "
            "budget from rq->avg_idle (ABK_ABI_PATCH_SUITE "
            "avg_idle_preemption_mode, 5.15 re-anchored; the suite's SIS_UTIL "
            "branch is refused here because 5.15 has no SIS_UTIL, and its "
            "unguarded idle_stamp sample is replaced by the pristine guard)",
            [
                "ABK_ABI_PATCH_SUITE scripts/abk_feature_porting.py:"
                "patch_avg_idle_preemption_mode() (group "
                "avg_idle_preemption_mode)",
                "no upstream commit: update_rq_avg_idle() occurs 0 times in "
                "common-5.15-2024-11 and in common-6.1-src, whose "
                "put_prev_task_idle() is still empty",
            ],
            [CORE_C, FAIR_C, IDLE_C, SCHED_H],
            _avg_idle_apply,
        ),
    ]
