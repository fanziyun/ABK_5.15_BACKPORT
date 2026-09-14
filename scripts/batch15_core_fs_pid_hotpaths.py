"""Batch 15: core fs/pid allocation hot paths (ABK_ABI_PATCH_SUITE absorption).

Three groups, absorbed from ``ABK_ABI_PATCH_SUITE``'s
``scripts/abk_feature_porting.py`` -- ``patch_pid_alloc()`` (group
``pid_alloc_hotpath_phase2``), ``patch_fd_alloc_hotpath()`` (group
``fd_alloc_hotpath``) and ``patch_close_range_hotpath()`` (group
``close_range_hotpath``) -- and re-registered here so Batch 15 owns the
optimization inventory instead of co-injecting that suite (AGENTS.md: the suite
must never ride the same build; it claims the ``sched_entity`` 1-4 and
``request_queue`` 1 KABI slots this module owns).

Provenance of the three group summaries (suite lines 41-52, verbatim):

* ``pid_alloc_hotpath_phase2`` -- "Port alloc_pid() preload/retry and ENOSPC
  handling onto the 6.1 pid namespace shape without changing pid_namespace
  layout."
* ``fd_alloc_hotpath`` -- "Port lock-avoiding fd allocation and fdtable growth
  helpers onto fs/file.c without changing files_struct or fdtable layout."
* ``close_range_hotpath`` -- "Port bitmap-driven close_range() batching helpers
  onto fs/file.c without changing syscall-visible behavior."

Targets are ``kernel/pid.c`` and ``fs/file.c``.  Every anchor below was checked
to occur **exactly once** in each of the four pristine reference trees
``abk515_ref_167`` (.167), ``_178`` (.178), ``_194`` (.194) and ``_211``
(.216/5.15.y-lts), i.e. the two legacy ``alloc_fdtable(unsigned int nr)``
baselines and the two baselines that already carry the upstream
``alloc_fdtable(unsigned int slots_wanted)`` refactor.

The one thing this port deliberately does differently from the suite: the suite
guards every edit with ``if old in text:`` and then calls
``text.replace(old, new, 1)``, so a drifted anchor is a *silent no-op reported
as success*.  Here every step is ``required=True``, so a miss aborts the whole
group transactionally (``apply_steps``) and surfaces as
``blocked_by_shape``/``blocked_by_missing_anchor``.

Composition with this repo's existing ``fdtable_alloc_conventions`` (Step 4 of
the absorption brief) -- **the two overlap, and the suite's version loses**:

``scripts/abk_stable_core.py``'s ``fdtable_alloc_conventions`` already lands the
upstream 5.15.191 fd-table shape on ``fs/file.c`` (``alloc_fdtable(unsigned int
slots_wanted)``, ``roundup_pow_of_two(slots_wanted)`` capacity,
``ERR_PTR()``/``IS_ERR()`` failure reporting, the ``nr + 1`` call in
``expand_fdtable()`` and the ``alloc_fdtable(open_files)`` call in ``dup_fd()``).
It reports ``applied`` on .167/.178 and ``already_present`` on .194/.216, and it
is registered *earlier* in ``abk_stable_core.PATCH_GROUPS`` than any batch child
(the batch modules are appended at the tail of that list, and batch15 groups are
appended after them).  So when these groups run, ``alloc_fdtable()`` has the
upstream ``slots_wanted`` body on **every** baseline.

The suite's ``fd_alloc_hotpath`` rewrites exactly that function: it inserts
``abk_fdtable_slots_wanted()`` and replaces the legacy capacity block with
``slots_wanted = abk_fdtable_slots_wanted(nr); nr = ALIGN(slots_wanted,
BITS_PER_LONG);`` plus an ``INT_MAX`` -> ``return NULL`` guard.  That is
incompatible with the repo's group in both directions:

* run after ``fdtable_alloc_conventions`` (the order that actually happens) and
  the suite's anchor (``nr /= (1024 / sizeof(struct file *)); ...``) no longer
  exists -- as a required step it would block the group, and as the suite has it
  it silently did nothing;
* run before it and the repo's ``_suite_fallback_shape()`` composition path
  would have to rewrite the same function *again*, leaving the suite's
  ``abk_fdtable_slots_wanted()`` helper defined-but-unused and its ``ALIGN``
  capacity line disagreeing with upstream's ``roundup_pow_of_two``.

There is also an outright text conflict beyond the function body: the suite's
helper name ``abk_fdtable_slots_wanted`` is one of this repo's *suite
detection* markers (``abk_common.SUITE_FD_HELPER``; ``GraftContext.
suite_touched()``/``suite_fdtable_fallback()``/``fdtable_upstream_shape()``) --
shipping it from this module would make a second pass report
``skip_suite_processed`` instead of ``already_present``.  So the capacity half of
``fd_alloc_hotpath`` is **NOT ported**: ``fdtable_alloc_conventions`` owns that
text and already delivers the lock-avoiding shape (upstream's capacity
computation *is* the slot-count precheck the suite was reaching for).  What is
ported is the rest of the group -- the ``abk_expand_files_needed()`` precheck
helper and its two call sites in ``expand_files()`` and ``alloc_fd()``, none of
which ``fdtable_alloc_conventions`` touches -- plus the ``get_unused_fd_flags()``
wrapper comment.

Required registration order: **these three groups must run after
``fdtable_alloc_conventions``** (and after ``fdtable_replace_fd_errno``).
``_fd_alloc_apply()`` enforces it with an explicit shape probe: if
``alloc_fdtable()`` does not already carry the upstream ``slots_wanted`` body,
the group reports ``blocked_by_shape`` instead of half-grafting a precheck onto
a table allocator it does not understand.

Suite co-injection: ``_fd_alloc_apply()`` and ``_close_range_apply()`` both
return ``skip_suite_processed`` when ``ctx.suite_touched("fs/file.c")`` holds.
That is not a hypothetical -- the suite's ``fd_alloc_hotpath`` inserts the same
``abk_expand_files_needed()`` helper and its ``close_range_hotpath`` inserts the
same ``fd_is_open()`` precheck, so re-grafting over a suite-processed tree would
be a duplicate definition.  A tree that already carries the suite keeps the
suite's copy of these two groups; this module's markers are ``ABK
stable_515_backport`` and never trip that probe, so its own second pass is
unaffected.

Deviations from the suite, recorded so they are not relitigated:

1. ``patch_pid_alloc()``'s retry used ``continue;`` inside ``alloc_pid()``'s
   ``for (i = ns->level; i >= 0; i--)``.  ``continue`` runs the loop's increment
   expression, so the retry did **not** re-attempt the same pid namespace level:
   it advanced ``i`` past a level whose ``pid->numbers[i]`` entry had never been
   written, and on a single-level namespace (``ns->level == 0``, i.e. every
   ordinary fork) it fell out of the loop, reached ``retval = -ENOMEM;`` at the
   end of the function and returned a *successfully initialised* pid whose
   ``pid->numbers[0].nr`` had never been set.  This port retries the same level
   through a ``retry_preload:`` label placed after the ``set_tid`` block and
   immediately before ``idr_preload(GFP_KERNEL)`` (placing it before that block
   would re-run the ``set_tid_size--`` bookkeeping).  The ``-ENOMEM`` retry is
   also **not** an upstream construct: ``alloc_pid()`` is byte-identical in the
   5.15, 6.1 and 6.6 reference trees and has no retry at all, so the retry is
   this inventory's own hardening and is justified on its own terms (an
   ``idr_preload()`` pool that came up short surfaces as ``-ENOMEM`` from inside
   ``pidmap_lock`` although the caller may sleep).
2. ``patch_pid_alloc()``'s other half -- translating ``idr_alloc_cyclic()``'s
   ``-ENOSPC`` into ``-EAGAIN`` -- is **already present** in every 5.15 baseline
   (``kernel/pid.c``: ``retval = (nr == -ENOSPC) ? -EAGAIN : nr;``), so no step
   is emitted for it.  Adding the suite's second, branch-local
   ``if (nr == -ENOSPC) nr = -EAGAIN;`` would have been a no-op restatement.
3. ``patch_close_range_hotpath()``'s ``abk_close_range_limit()`` and
   ``abk_pick_file_for_close()`` helpers are **NOT ported**.  The suite itself
   lands them only on the caller-locked 6.1 ``__range_close()`` shape (its
   ``range_new`` branch); on 5.15 ``pick_file()`` takes ``files->file_lock``
   itself and returns ``ERR_PTR()``, so ``abk_pick_file_for_close()``'s
   ``lockdep_assert_held(&files->file_lock)`` would be a knowingly false
   assertion and ``abk_close_range_limit()`` would be dead code.  The 5.15 form
   of the group (the suite's ``range_new_5_15``) uses neither, and reads
   ``open_fds`` under ``rcu_read_lock()`` exactly as ``__range_cloexec()`` and
   ``__close_range()`` already do in this file.
4. ``patch_fd_alloc_hotpath()``'s ``_probe_refactored_alloc_fdtable()`` shape
   negotiation is dropped along with the capacity rewrite it guards: with the
   repo's ``fdtable_alloc_conventions`` running first, the ``slots_wanted`` shape
   is the *only* shape these steps can see, so negotiating between the two
   spellings would be dead code.  ``_fd_alloc_apply()`` probes for that shape
   directly and blocks if it is absent.

Callee conventions checked against the 5.15 tree (not against the suite's mixed
baselines): ``fd_is_open()`` is the ``static inline`` in
``include/linux/fdtable.h`` (included by ``fs/file.c``) and reads
``fdt->open_fds``; ``find_next_bit()`` is the ``asm-generic/bitops/find.h``
declaration pulled in by ``<linux/bitops.h>``, also included by ``fs/file.c``;
``last_fd()`` returns ``fdt->max_fds - 1`` and is defined immediately above
``__range_close()``; ``min()``/``max()`` are the ``<linux/minmax.h>`` forms the
same file already calls.  ``idr_preload()``/``idr_preload_end()``,
``idr_alloc_cyclic()`` and ``idr_alloc()`` all keep their 5.15 signatures in
``kernel/pid.c``.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

PID_C = "kernel/pid.c"
FILE_C = "fs/file.c"

# Alias the required-flag for readability (the other batch modules use T/F).
T = True

# ---------------------------------------------------------------------------
# pid_alloc_hotpath_phase2 -- kernel/pid.c
# ---------------------------------------------------------------------------
# Three steps, one logical change.  The whole block below was checked to occur
# exactly once in abk515_ref_{167,178,194,211}: `idr_preload_end();` and
# `idr_preload(GFP_KERNEL);` each occur exactly once in kernel/pid.c, which is
# what makes the middle and last anchor unique by construction.
#
# Anchor note (registration order): nothing else in this module or in
# abk_stable_core.py's registry touches kernel/pid.c, so this group is
# order-independent.  It is first only to keep the three groups in the suite's
# own order.

_PID_DECL_OLD = (
    "\tstruct upid *upid;\n"
    "\tint retval = -ENOMEM;\n"
)

_PID_DECL_NEW = (
    "\tstruct upid *upid;\n"
    "\tint retval = -ENOMEM;\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: pid_alloc_hotpath_phase2.  idr_preload()\n"
    "\t * can come up short for the GFP_ATOMIC idr_alloc_cyclic() below\n"
    "\t * (the idr has to grow a node the preloaded pool did not cover), and\n"
    "\t * the allocation then reports -ENOMEM from inside pidmap_lock even\n"
    "\t * though this caller is allowed to sleep.  One retry with a freshly\n"
    "\t * preloaded pool turns that spurious -ENOMEM into a successful fork;\n"
    "\t * a second failure is fatal.\n"
    "\t */\n"
    "\tbool retried_preload;\n"
)

# The `retry_preload` label sits *after* the set_tid bookkeeping and
# immediately before the preload, so a retry re-attempts the same namespace
# level (a `continue` would run the for-loop's `i--` and skip it).
_PID_PRELOAD_OLD = (
    "\t\t\tset_tid_size--;\n"
    "\t\t}\n"
    "\n"
    "\t\tidr_preload(GFP_KERNEL);\n"
    "\t\tspin_lock_irq(&pidmap_lock);\n"
    "\n"
    "\t\tif (tid) {\n"
)

_PID_PRELOAD_NEW = (
    "\t\t\tset_tid_size--;\n"
    "\t\t}\n"
    "\n"
    "\t\tretried_preload = false;\n"
    "\t\t/* ABK stable_515_backport: the retry re-enters here -- same\n"
    "\t\t * namespace level, set_tid bookkeeping not repeated. */\n"
    "retry_preload:\n"
    "\t\tidr_preload(GFP_KERNEL);\n"
    "\t\tspin_lock_irq(&pidmap_lock);\n"
    "\n"
    "\t\tif (tid) {\n"
)

_PID_RETRY_OLD = (
    "\t\tspin_unlock_irq(&pidmap_lock);\n"
    "\t\tidr_preload_end();\n"
    "\n"
    "\t\tif (nr < 0) {\n"
    "\t\t\tretval = (nr == -ENOSPC) ? -EAGAIN : nr;\n"
    "\t\t\tgoto out_free;\n"
    "\t\t}\n"
    "\n"
    "\t\tpid->numbers[i].nr = nr;\n"
    "\t\tpid->numbers[i].ns = tmp;\n"
    "\t\ttmp = tmp->parent;\n"
    "\t}\n"
)

_PID_RETRY_NEW = (
    "\t\tspin_unlock_irq(&pidmap_lock);\n"
    "\t\tidr_preload_end();\n"
    "\n"
    "\t\tif (nr < 0) {\n"
    "\t\t\t/*\n"
    "\t\t\t * ABK stable_515_backport: retry this namespace level once\n"
    "\t\t\t * under a fresh GFP_KERNEL preload.  Deliberately a `goto`\n"
    "\t\t\t * and not `continue`: `continue` would run the for-loop's\n"
    "\t\t\t * `i--`, skipping a level whose pid->numbers[i] entry has\n"
    "\t\t\t * not been written yet.\n"
    "\t\t\t */\n"
    "\t\t\tif (nr == -ENOMEM && !retried_preload) {\n"
    "\t\t\t\tretried_preload = true;\n"
    "\t\t\t\tgoto retry_preload;\n"
    "\t\t\t}\n"
    "\t\t\tretval = (nr == -ENOSPC) ? -EAGAIN : nr;\n"
    "\t\t\tgoto out_free;\n"
    "\t\t}\n"
    "\n"
    "\t\tpid->numbers[i].nr = nr;\n"
    "\t\tpid->numbers[i].ns = tmp;\n"
    "\t\ttmp = tmp->parent;\n"
    "\t}\n"
)

# ---------------------------------------------------------------------------
# fd_alloc_hotpath -- fs/file.c
# ---------------------------------------------------------------------------
# The suite's slot-count helper + alloc_fdtable() capacity rewrite is NOT here;
# see the module docstring (fdtable_alloc_conventions owns that function and
# runs first).  Anchor note: `#define fdt_words(fdt) ...` occurs exactly once in
# fs/file.c on all four baselines, and the function-body anchors below survive
# both the pristine and the fdtable_alloc_conventions-rewritten spellings --
# they do not touch alloc_fdtable()/expand_fdtable()/dup_fd() text at all.

_FD_HELPER_OLD = (
    "#define fdt_words(fdt) ((fdt)->max_fds / BITS_PER_LONG) // words in ->open_fds\n"
)

_FD_HELPER_NEW = (
    "#define fdt_words(fdt) ((fdt)->max_fds / BITS_PER_LONG) // words in ->open_fds\n"
    "\n"
    "/*\n"
    " * ABK stable_515_backport: fd_alloc_hotpath.  expand_files() already\n"
    " * reports \"nothing done\" (0) for a descriptor the current table covers,\n"
    " * so the two hot-path callers below can skip the call -- and its second\n"
    " * files_fdtable() lookup -- with one comparison.\n"
    " */\n"
    "static inline bool abk_expand_files_needed(const struct fdtable *fdt,\n"
    "\t\t\t\t\t   unsigned int nr)\n"
    "{\n"
    "\treturn nr >= fdt->max_fds;\n"
    "}\n"
    "\n"
)

_FD_EXPAND_OLD = (
    "repeat:\n"
    "\tfdt = files_fdtable(files);\n"
    "\n"
    "\t/* Do we need to expand? */\n"
    "\tif (nr < fdt->max_fds)\n"
    "\t\treturn expanded;\n"
    "\n"
    "\t/* Can we expand? */\n"
    "\tif (nr >= sysctl_nr_open)\n"
    "\t\treturn -EMFILE;\n"
)

_FD_EXPAND_NEW = (
    "repeat:\n"
    "\tfdt = files_fdtable(files);\n"
    "\n"
    "\t/* Do we need to expand? */\n"
    "\tif (!abk_expand_files_needed(fdt, nr))\n"
    "\t\treturn expanded;\n"
    "\n"
    "\t/* Can we expand? */\n"
    "\tif (nr >= sysctl_nr_open)\n"
    "\t\treturn -EMFILE;\n"
)

_FD_ALLOC_OLD = (
    "\terror = -EMFILE;\n"
    "\tif (fd >= end)\n"
    "\t\tgoto out;\n"
    "\n"
    "\terror = expand_files(files, fd);\n"
    "\tif (error < 0)\n"
    "\t\tgoto out;\n"
    "\n"
    "\t/*\n"
    "\t * If we needed to expand the fs array we\n"
    "\t * might have blocked - try again.\n"
    "\t */\n"
    "\tif (error)\n"
    "\t\tgoto repeat;\n"
)

_FD_ALLOC_NEW = (
    "\terror = -EMFILE;\n"
    "\tif (fd >= end)\n"
    "\t\tgoto out;\n"
    "\n"
    "\t/* ABK stable_515_backport: fd_alloc_hotpath -- fdt was read at the top\n"
    "\t * of this iteration, so the growth check needs no call. */\n"
    "\tif (abk_expand_files_needed(fdt, fd)) {\n"
    "\t\terror = expand_files(files, fd);\n"
    "\t\tif (error < 0)\n"
    "\t\t\tgoto out;\n"
    "\n"
    "\t\t/*\n"
    "\t\t * If we needed to expand the fs array we\n"
    "\t\t * might have blocked - try again.\n"
    "\t\t */\n"
    "\t\tif (error)\n"
    "\t\t\tgoto repeat;\n"
    "\t}\n"
)

_FD_UNUSED_OLD = (
    "int get_unused_fd_flags(unsigned flags)\n"
    "{\n"
    "\treturn __get_unused_fd_flags(flags, rlimit(RLIMIT_NOFILE));\n"
    "}\n"
)

_FD_UNUSED_NEW = (
    "int get_unused_fd_flags(unsigned flags)\n"
    "{\n"
    "\t/* ABK stable_515_backport: fd_alloc_hotpath -- deliberately a bare\n"
    "\t * tail call so the hot open()/dup() entry points stay on alloc_fd().\n"
    "\t */\n"
    "\treturn __get_unused_fd_flags(flags, rlimit(RLIMIT_NOFILE));\n"
    "}\n"
)

# ---------------------------------------------------------------------------
# close_range_hotpath -- fs/file.c
# ---------------------------------------------------------------------------
# Two steps.  Anchor note: both blocks are byte-identical on all four
# baselines (167: 634-657 / 699-717, 178: 619-642 / 684-702, 194+211:
# 617-640 / 682-700); the `fd >= fdt->max_fds` guard with `ERR_PTR(-EINVAL)`
# is the 5.15/6.1 pick_file() shape (a caller-locked pick_file() returning
# NULL would have no such guard and is rejected by the shape probe).

_CR_PICK_OLD = (
    "\tspin_lock(&files->file_lock);\n"
    "\tfdt = files_fdtable(files);\n"
    "\tif (fd >= fdt->max_fds) {\n"
    "\t\tfile = ERR_PTR(-EINVAL);\n"
    "\t\tgoto out_unlock;\n"
    "\t}\n"
    "\tfd = array_index_nospec(fd, fdt->max_fds);\n"
)

_CR_PICK_NEW = (
    "\tspin_lock(&files->file_lock);\n"
    "\tfdt = files_fdtable(files);\n"
    "\tif (fd >= fdt->max_fds) {\n"
    "\t\tfile = ERR_PTR(-EINVAL);\n"
    "\t\tgoto out_unlock;\n"
    "\t}\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: close_range_hotpath.  __clear_open_fd()\n"
    "\t * clears the open bit under this same lock, and the fd-array slot is\n"
    "\t * NULLed before it, so a clear bit means the load below cannot return\n"
    "\t * a file.  Answering from the bitmap skips array_index_nospec() and\n"
    "\t * the fd-array cache line for every descriptor the range walk skips.\n"
    "\t */\n"
    "\tif (!fd_is_open(fd, fdt)) {\n"
    "\t\tfile = ERR_PTR(-EBADF);\n"
    "\t\tgoto out_unlock;\n"
    "\t}\n"
    "\tfd = array_index_nospec(fd, fdt->max_fds);\n"
)

_CR_RANGE_OLD = (
    "static inline void __range_close(struct files_struct *cur_fds, unsigned int fd,\n"
    "\t\t\t\t unsigned int max_fd)\n"
    "{\n"
    "\twhile (fd <= max_fd) {\n"
    "\t\tstruct file *file;\n"
    "\n"
    "\t\tfile = pick_file(cur_fds, fd++);\n"
    "\t\tif (!IS_ERR(file)) {\n"
    "\t\t\t/* found a valid file to close */\n"
    "\t\t\tfilp_close(file, cur_fds);\n"
    "\t\t\tcond_resched();\n"
    "\t\t\tcontinue;\n"
    "\t\t}\n"
    "\n"
    "\t\t/* beyond the last fd in that table */\n"
    "\t\tif (PTR_ERR(file) == -EINVAL)\n"
    "\t\t\treturn;\n"
    "\t}\n"
    "}\n"
)

# The 5.15 form (the suite's `range_new_5_15`): pick_file() takes the lock
# itself and returns ERR_PTR, so the walk holds no files->file_lock; it reads
# the bitmap under rcu_read_lock() and recomputes both the bound and the next
# set bit after every close, because filp_close() drops the lock.
_CR_RANGE_NEW = (
    "/*\n"
    " * ABK stable_515_backport: close_range_hotpath.  Walk open_fds instead of\n"
    " * probing every descriptor in [fd, max_fd]: a range close of a large span\n"
    " * used to pay one locked pick_file() per descriptor, almost all of them\n"
    " * -EBADF misses.  The table is re-read under rcu_read_lock() after each\n"
    " * filp_close() -- the same context __range_cloexec() and __close_range()\n"
    " * use for last_fd()/open_fds -- and max_fd is re-clamped to it, so a\n"
    " * concurrent expansion cannot make the walk read past the live table.\n"
    " */\n"
    "static inline void __range_close(struct files_struct *cur_fds, unsigned int fd,\n"
    "\t\t\t\t unsigned int max_fd)\n"
    "{\n"
    "\tstruct fdtable *fdt;\n"
    "\tunsigned int n;\n"
    "\n"
    "\trcu_read_lock();\n"
    "\tfdt = files_fdtable(cur_fds);\n"
    "\tn = last_fd(fdt);\n"
    "\tmax_fd = min(max_fd, n);\n"
    "\tfd = find_next_bit(fdt->open_fds, max_fd + 1, fd);\n"
    "\trcu_read_unlock();\n"
    "\n"
    "\twhile (fd <= max_fd) {\n"
    "\t\tstruct file *file;\n"
    "\n"
    "\t\tfile = pick_file(cur_fds, fd);\n"
    "\t\tif (!IS_ERR(file)) {\n"
    "\t\t\t/* found a valid file to close */\n"
    "\t\t\tfilp_close(file, cur_fds);\n"
    "\t\t\tcond_resched();\n"
    "\t\t} else if (PTR_ERR(file) == -EINVAL) {\n"
    "\t\t\t/* beyond the last fd in that table */\n"
    "\t\t\treturn;\n"
    "\t\t}\n"
    "\n"
    "\t\trcu_read_lock();\n"
    "\t\tfdt = files_fdtable(cur_fds);\n"
    "\t\tmax_fd = min(max_fd, last_fd(fdt));\n"
    "\t\tfd = find_next_bit(fdt->open_fds, max_fd + 1, fd + 1);\n"
    "\t\trcu_read_unlock();\n"
    "\t}\n"
    "}\n"
)


def build_pid_alloc_steps():
    """``pid_alloc_hotpath_phase2``: three steps, one transaction.

    Step 1 declares the retry latch, step 2 resets it (per namespace level) and
    opens the ``retry_preload`` label the third step jumps to, step 3 is the
    error path itself.  They cannot be split across groups: on its own, step 3's
    ``goto retry_preload`` would not compile.

    Each replacement is textually distinct from every other replacement in the
    module (step audit trap 2), and no replacement is a truncation of its own
    anchor (trap 1: ``replace_once`` tests ``new`` first, so a pure deletion
    reports ``already_present`` while never landing).
    """
    return [
        (PID_C, _PID_DECL_OLD, _PID_DECL_NEW, T),
        (PID_C, _PID_PRELOAD_OLD, _PID_PRELOAD_NEW, T),
        (PID_C, _PID_RETRY_OLD, _PID_RETRY_NEW, T),
    ]


def build_fd_alloc_steps():
    """``fd_alloc_hotpath``: helper + the two prechecks + the wrapper comment.

    The suite's ``alloc_fdtable()`` capacity rewrite and its
    ``abk_fdtable_slots_wanted()`` helper are deliberately absent -- see the
    module docstring; ``fdtable_alloc_conventions`` owns that function and must
    have run before this group.
    """
    return [
        (FILE_C, _FD_HELPER_OLD, _FD_HELPER_NEW, T),
        (FILE_C, _FD_EXPAND_OLD, _FD_EXPAND_NEW, T),
        (FILE_C, _FD_ALLOC_OLD, _FD_ALLOC_NEW, T),
        (FILE_C, _FD_UNUSED_OLD, _FD_UNUSED_NEW, T),
    ]


def build_close_range_steps():
    """``close_range_hotpath``: the pick_file() early-out and the bitmap walk.

    Both steps are required and belong to one group: step 2's walk skips fds the
    old loop visited, and step 1 is what makes skipping them cheap.  Neither
    changes syscall-visible behaviour -- ``pick_file()`` returned ``-EBADF`` for
    a clear open bit before this patch too, just from the fd-array load.
    """
    return [
        (FILE_C, _CR_PICK_OLD, _CR_PICK_NEW, T),
        (FILE_C, _CR_RANGE_OLD, _CR_RANGE_NEW, T),
    ]


def _pid_alloc_apply(ctx):
    try:
        text = ctx.read(PID_C)
    except FileNotFoundError:
        return "blocked_by_shape", f"{PID_C}: file absent"
    # Shape probe only: the retry latch is meaningless on a pid allocator that
    # does not use the idr preload protocol.  `idr_preload(GFP_KERNEL);` occurs
    # exactly once in kernel/pid.c on every baseline.
    for probe, why in (
        ("struct pid *alloc_pid(struct pid_namespace *ns, pid_t *set_tid,",
         "alloc_pid()"),
        ("\t\tidr_preload(GFP_KERNEL);\n", "idr_preload(GFP_KERNEL) protocol"),
        ("\tidr_preload_end();\n", "idr_preload_end() protocol"),
    ):
        if probe not in text:
            return "blocked_by_shape", f"{why} not found in pid.c"
    status, _results, detail = apply_steps(ctx, build_pid_alloc_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _fd_alloc_apply(ctx):
    try:
        text = ctx.read(FILE_C)
    except FileNotFoundError:
        return "blocked_by_shape", f"{FILE_C}: file absent"
    # Co-injection guard: the suite's fd_alloc_hotpath grafts the same helper and
    # the same two prechecks with its own markers, so composing over it would
    # duplicate abk_expand_files_needed().  The probe keys on the suite's
    # markers, never on ours.
    if ctx.suite_touched(FILE_C):
        return (
            "skip_suite_processed",
            "fs/file.c already carries the ABK_ABI_PATCH_SUITE fd allocation "
            "hotpath graft (abk_fdtable_slots_wanted/ABK feature_porting "
            "marker); Batch 15 absorbs that group, so its copy is left alone",
        )
    # Registration-order assertion: this group's prechecks are only meaningful
    # once the repo's fdtable_alloc_conventions has landed the upstream
    # slots_wanted/roundup_pow_of_two table allocator.  Without it, bail loudly
    # instead of half-grafting.
    if (
        "alloc_fdtable(unsigned int slots_wanted)" not in text
        or "roundup_pow_of_two(slots_wanted)" not in text
    ):
        return (
            "blocked_by_shape",
            "fs/file.c does not carry the upstream 5.15.191 fdtable shape yet; "
            "fdtable_alloc_conventions must run before fd_alloc_hotpath",
        )
    for probe, why in (
        ("#define fdt_words(fdt)", "fdt_words() helper site"),
        ("static int expand_files(struct files_struct *files, unsigned int nr)",
         "expand_files()"),
        ("static int alloc_fd(unsigned start, unsigned end, unsigned flags)",
         "alloc_fd()"),
        ("int get_unused_fd_flags(unsigned flags)\n", "get_unused_fd_flags()"),
    ):
        if probe not in text:
            return "blocked_by_shape", f"{why} not found in fs/file.c"
    status, _results, detail = apply_steps(ctx, build_fd_alloc_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _close_range_apply(ctx):
    try:
        text = ctx.read(FILE_C)
    except FileNotFoundError:
        return "blocked_by_shape", f"{FILE_C}: file absent"
    if ctx.suite_touched(FILE_C):
        return (
            "skip_suite_processed",
            "fs/file.c already carries the ABK_ABI_PATCH_SUITE close_range() "
            "bitmap graft (ABK feature_porting marker); Batch 15 absorbs that "
            "group, so its copy is left alone",
        )
    # The steps are written for the 5.15/6.1 pick_file() shape -- it takes
    # files->file_lock itself and reports ERR_PTR(-EINVAL) past max_fds.  A
    # caller-locked pick_file() returning NULL (the shape upstream moved to
    # later) would miss every anchor, which apply_steps would correctly report
    # as a blocked group; probing it here just makes the reason precise.
    for probe, why in (
        ("static struct file *pick_file(struct files_struct *files, unsigned fd)",
         "pick_file()"),
        ("static inline void __range_close(struct files_struct *cur_fds, unsigned int fd,",
         "__range_close()"),
        ("static inline unsigned last_fd(struct fdtable *fdt)", "last_fd()"),
        ("\t\tfile = ERR_PTR(-EINVAL);\n", "pick_file() 5.15 error convention"),
    ):
        if probe not in text:
            return "blocked_by_shape", f"{why} not found in fs/file.c"
    status, _results, detail = apply_steps(ctx, build_close_range_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the three PatchGroup records, in registration order.

    All three must be registered after ``fdtable_alloc_conventions`` (and after
    ``fdtable_replace_fd_errno``): the fd group composes on the upstream
    5.15.191 fd-table shape that group lands, and asserts it.  ``close_range``
    and ``pid`` share no text with any other registered group.
    """
    return [
        PatchGroup(
            "pid_alloc_hotpath_phase2",
            "alloc_pid() preload/retry on the 5.15 pid-namespace shape: "
            "idr_preload(GFP_KERNEL) is retried once (same namespace level) "
            "after a GFP_ATOMIC -ENOMEM under pidmap_lock; "
            "idr_alloc_cyclic()'s -ENOSPC stays translated to -EAGAIN; no "
            "pid_namespace layout change",
            [
                "ABK_ABI_PATCH_SUITE pid_alloc_hotpath_phase2 "
                "(abk_feature_porting.patch_pid_alloc, absorbed by Batch 15)",
                "the suite's `continue` retry is corrected to a same-level "
                "`goto retry_preload` -- see the module docstring",
                "no upstream commit: alloc_pid() is byte-identical and "
                "retry-free in the 5.15/6.1/6.6 reference trees; the -ENOSPC "
                "-> -EAGAIN half is already present on every 5.15 baseline",
            ],
            [PID_C],
            _pid_alloc_apply,
        ),
        PatchGroup(
            "fd_alloc_hotpath",
            "fd allocation hot path on top of the 5.15.191 fd-table "
            "conventions: abk_expand_files_needed() lets expand_files() and "
            "alloc_fd() answer the growth question with one comparison "
            "instead of an expand_files() call on the common path; the "
            "suite's alloc_fdtable() capacity rewrite is NOT ported because "
            "fdtable_alloc_conventions owns that text and runs first",
            [
                "ABK_ABI_PATCH_SUITE fd_alloc_hotpath "
                "(abk_feature_porting.patch_fd_alloc_hotpath, absorbed by "
                "Batch 15), minus its abk_fdtable_slots_wanted() helper and "
                "alloc_fdtable() capacity rewrite",
                "composition: requires fdtable_alloc_conventions (5.15.191, "
                "04a2c4b4511d + 1d3b4bec3ce5) to have landed the upstream "
                "slots_wanted/ERR_PTR alloc_fdtable()",
            ],
            [FILE_C],
            _fd_alloc_apply,
        ),
        PatchGroup(
            "close_range_hotpath",
            "close_range() walks the open_fds bitmap instead of probing every "
            "descriptor in the range: pick_file() gains a bitmap early-out "
            "(-EBADF) and __range_close() advances fd-to-fd through "
            "find_next_bit(), re-reading the table under rcu_read_lock() "
            "after each filp_close(); syscall-visible behaviour unchanged",
            [
                "ABK_ABI_PATCH_SUITE close_range_hotpath "
                "(abk_feature_porting.patch_close_range_hotpath, absorbed by "
                "Batch 15), in its 5.15 (range_new_5_15) form -- the "
                "abk_close_range_limit()/abk_pick_file_for_close() helpers "
                "belong to the caller-locked 6.1 shape and are not ported",
            ],
            [FILE_C],
            _close_range_apply,
        ),
    ]
