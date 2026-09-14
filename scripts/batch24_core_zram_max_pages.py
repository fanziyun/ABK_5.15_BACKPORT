"""Batch 24: bound a recompression pass -- the ``max_pages`` parameter.

Sourcing (read as a real patch in ``research/upstream-zram/``, not from memory):

* ``34efe1c3b688`` ("zram: add max_pages param to recompression", Soonhyun
  Park, 2024-03-29) adds ``max_pages`` to ``recompress_store()``:
  ``u64 num_recomp_pages = ULLONG_MAX``, ``kstrtoull()`` in the parameter loop,
  ``if (!num_recomp_pages) break;`` at the top of the sweep, and one decrement
  per *attempt* inside ``recompress_slot()`` -- upstream decrements even when
  the attempt failed, "because we still spent resources on it".
* ``2f529e73d720`` ("zram: reject unrecognized type= values in
  recompress_store()", 2026-04-07) adds ``if (!mode) return -EINVAL;`` inside
  the ``type`` branch.  Without it a typo (``type=huger``) leaves ``mode`` at
  zero, and zero means "no filter", so the pass recompresses every allocated
  page instead of failing.

Why this is worth a group on its own: the companion module drives a sweep every
``zram.recomp.interval_sec`` (``tools/zram_recompress_trigger.sh``), and without
a cap one sweep walks the whole device -- an unbounded amount of CPU on the
exactly the devices this module targets.  ``max_pages`` is the upstream answer
to that, and ``android13-5.15`` has neither it nor the type guard: both are
newer than the recompression series the branch froze before.

5.15 shape differences (verified against the patched tree):

* the module's ``zram_recompress()`` loops over *priorities* internally and
  upstream's ``recompress_slot()`` takes a single prio, so the decrement cannot
  live "after the compress attempt" inside the recompression helper without
  changing its signature -- and that helper is also called by the async worker.
  The decrement therefore sits at the call site in the sweep loop, which is the
  same place in the control flow: reached once per slot, only after every
  candidate filter passed, whether or not the recompression succeeds.
* ``recompress_async`` (``zram_async_recompress``, Batch 10-1) advertises "the
  same type/threshold/algo grammar", so it gets the same parameter and the same
  guard; there its cap bounds the number of jobs queued, which is the same
  quantity (one job == one attempt).

Graft-boundary contract: this is the one batch that *does* edit text two earlier
groups produce, because ``recompress_store()`` is not pristine 5.15 -- the
``zram_recompression`` group generates it.  ``replace_once`` tests the new block
first, so those two groups had to learn a shape probe on one of their own added
symbols (``docs/group_recipe.md`` trap 5, the Batch-21 remedy) before this group
could be registered after them.  Both probes are short-circuits on content that
only those groups write, so a first pass still rewrites and only a second pass
stops.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

ZRAM_C = "drivers/block/zram/zram_drv.c"

T = True

# ---------------------------------------------------------------------------
# Steps 1-3: the synchronous recompress node (recompress_store()).
# ---------------------------------------------------------------------------

# The whole parameter prologue of the function, exactly as the
# zram_recompression group generates it: declarations through the end of the
# "type" branch.  Unique in the file -- the async copy has no page argument.
_SYNC_HEAD_OLD = (
    '\tchar *args, *param, *val, *algo = NULL;\n'
    '\tu32 mode = 0, threshold = 0;\n'
    '\tunsigned long index;\n'
    '\tstruct page *page;\n'
    '\tssize_t ret;\n'
    '\n'
    '\targs = skip_spaces(buf);\n'
    '\twhile (*args) {\n'
    '\t\targs = next_arg(args, &param, &val);\n'
    '\n'
    '\t\tif (!val || !*val)\n'
    '\t\t\treturn -EINVAL;\n'
    '\n'
    '\t\tif (!strcmp(param, "type")) {\n'
    '\t\t\tif (!strcmp(val, "idle"))\n'
    '\t\t\t\tmode = RECOMPRESS_IDLE;\n'
    '\t\t\tif (!strcmp(val, "huge"))\n'
    '\t\t\t\tmode = RECOMPRESS_HUGE;\n'
    '\t\t\tif (!strcmp(val, "huge_idle"))\n'
    '\t\t\t\tmode = RECOMPRESS_IDLE | RECOMPRESS_HUGE;\n'
    '\t\t\tcontinue;\n'
    '\t\t}\n'
)

_SYNC_HEAD_NEW = (
    '\tchar *args, *param, *val, *algo = NULL;\n'
    '\tu64 num_recomp_pages = ULLONG_MAX;\n'
    '\tu32 mode = 0, threshold = 0;\n'
    '\tunsigned long index;\n'
    '\tstruct page *page;\n'
    '\tssize_t ret;\n'
    '\n'
    '\targs = skip_spaces(buf);\n'
    '\twhile (*args) {\n'
    '\t\targs = next_arg(args, &param, &val);\n'
    '\n'
    '\t\tif (!val || !*val)\n'
    '\t\t\treturn -EINVAL;\n'
    '\n'
    '\t\tif (!strcmp(param, "type")) {\n'
    '\t\t\tif (!strcmp(val, "idle"))\n'
    '\t\t\t\tmode = RECOMPRESS_IDLE;\n'
    '\t\t\tif (!strcmp(val, "huge"))\n'
    '\t\t\t\tmode = RECOMPRESS_HUGE;\n'
    '\t\t\tif (!strcmp(val, "huge_idle"))\n'
    '\t\t\t\tmode = RECOMPRESS_IDLE | RECOMPRESS_HUGE;\n'
    '\t\t\tif (!mode)\n'
    '\t\t\t\treturn -EINVAL;\n'
    '\t\t\tcontinue;\n'
    '\t\t}\n'
    '\n'
    '\t\t/*\n'
    '\t\t * ABK stable_515_backport: 34efe1c3b688.  Bound the pass: at most\n'
    '\t\t * max_pages entries are attempted, so one sweep cannot recompress\n'
    '\t\t * the whole device.\n'
    '\t\t */\n'
    '\t\tif (!strcmp(param, "max_pages")) {\n'
    '\t\t\tret = kstrtoull(val, 10, &num_recomp_pages);\n'
    '\t\t\tif (ret)\n'
    '\t\t\t\treturn ret;\n'
    '\t\t\tcontinue;\n'
    '\t\t}\n'
)

# Sweep head: give up once the cap is spent, before touching a slot.
_SYNC_LOOP_OLD = (
    '\tret = len;\n'
    '\tfor (index = 0; index < nr_pages; index++) {\n'
    '\t\tint err = 0;\n'
    '\n'
    '\t\tzram_slot_lock(zram, index);\n'
)

_SYNC_LOOP_NEW = (
    '\tret = len;\n'
    '\tfor (index = 0; index < nr_pages; index++) {\n'
    '\t\tint err = 0;\n'
    '\n'
    '\t\tif (!num_recomp_pages)\n'
    '\t\t\tbreak;\n'
    '\n'
    '\t\tzram_slot_lock(zram, index);\n'
)

# The attempt itself.  Every candidate filter above ends in "goto next", so
# reaching this line *is* an attempt -- which is exactly what upstream counts
# (it decrements even when the recompression then fails).
_SYNC_CALL_OLD = (
    '\t\terr = zram_recompress(zram, index, page, threshold, prio, prio_max);\n'
)

_SYNC_CALL_NEW = (
    '\t\tnum_recomp_pages--;\n'
    '\t\terr = zram_recompress(zram, index, page, threshold, prio, prio_max);\n'
)

# ---------------------------------------------------------------------------
# Steps 4-6: the asynchronous node (recompress_async_store(), Batch 10-1).
# Same grammar, same cap; here it bounds the jobs queued, one per attempt.
# ---------------------------------------------------------------------------

_ASYNC_HEAD_OLD = (
    '\tchar *args, *param, *val, *algo = NULL;\n'
    '\tu32 mode = 0, threshold = 0;\n'
    '\tunsigned long index;\n'
    '\tssize_t ret;\n'
    '\n'
    '\targs = skip_spaces(buf);\n'
    '\twhile (*args) {\n'
    '\t\targs = next_arg(args, &param, &val);\n'
    '\n'
    '\t\tif (!val || !*val)\n'
    '\t\t\treturn -EINVAL;\n'
    '\n'
    '\t\tif (!strcmp(param, "type")) {\n'
    '\t\t\tif (!strcmp(val, "idle"))\n'
    '\t\t\t\tmode = RECOMPRESS_IDLE;\n'
    '\t\t\tif (!strcmp(val, "huge"))\n'
    '\t\t\t\tmode = RECOMPRESS_HUGE;\n'
    '\t\t\tif (!strcmp(val, "huge_idle"))\n'
    '\t\t\t\tmode = RECOMPRESS_IDLE | RECOMPRESS_HUGE;\n'
    '\t\t\tcontinue;\n'
    '\t\t}\n'
)

_ASYNC_HEAD_NEW = (
    '\tchar *args, *param, *val, *algo = NULL;\n'
    '\tu64 num_recomp_pages = ULLONG_MAX;\n'
    '\tu32 mode = 0, threshold = 0;\n'
    '\tunsigned long index;\n'
    '\tssize_t ret;\n'
    '\n'
    '\targs = skip_spaces(buf);\n'
    '\twhile (*args) {\n'
    '\t\targs = next_arg(args, &param, &val);\n'
    '\n'
    '\t\tif (!val || !*val)\n'
    '\t\t\treturn -EINVAL;\n'
    '\n'
    '\t\tif (!strcmp(param, "type")) {\n'
    '\t\t\tif (!strcmp(val, "idle"))\n'
    '\t\t\t\tmode = RECOMPRESS_IDLE;\n'
    '\t\t\tif (!strcmp(val, "huge"))\n'
    '\t\t\t\tmode = RECOMPRESS_HUGE;\n'
    '\t\t\tif (!strcmp(val, "huge_idle"))\n'
    '\t\t\t\tmode = RECOMPRESS_IDLE | RECOMPRESS_HUGE;\n'
    '\t\t\tif (!mode)\n'
    '\t\t\t\treturn -EINVAL;\n'
    '\t\t\tcontinue;\n'
    '\t\t}\n'
    '\n'
    '\t\t/*\n'
    '\t\t * ABK stable_515_backport: 34efe1c3b688.  Same cap as the\n'
    '\t\t * synchronous node; here it bounds the number of queued jobs, which\n'
    '\t\t * is one job per attempted page.\n'
    '\t\t */\n'
    '\t\tif (!strcmp(param, "max_pages")) {\n'
    '\t\t\tret = kstrtoull(val, 10, &num_recomp_pages);\n'
    '\t\t\tif (ret)\n'
    '\t\t\t\treturn ret;\n'
    '\t\t\tcontinue;\n'
    '\t\t}\n'
)

_ASYNC_LOOP_OLD = (
    '\tret = len;\n'
    '\tfor (index = 0; index < nr_pages; index++) {\n'
    '\t\tbool candidate = false;\n'
    '\t\tint err = 0;\n'
    '\n'
    '\t\tzram_slot_lock(zram, index);\n'
)

_ASYNC_LOOP_NEW = (
    '\tret = len;\n'
    '\tfor (index = 0; index < nr_pages; index++) {\n'
    '\t\tbool candidate = false;\n'
    '\t\tint err = 0;\n'
    '\n'
    '\t\tif (!num_recomp_pages)\n'
    '\t\t\tbreak;\n'
    '\n'
    '\t\tzram_slot_lock(zram, index);\n'
)

_ASYNC_ENQUEUE_OLD = (
    '\t\terr = abk_zram_recomp_enqueue(zram, index, threshold, prio,\n'
    '\t\t\t\t\t\t\t\t\tprio_max, mode & RECOMPRESS_IDLE);\n'
)

_ASYNC_ENQUEUE_NEW = (
    '\t\tnum_recomp_pages--;\n'
    '\t\terr = abk_zram_recomp_enqueue(zram, index, threshold, prio,\n'
    '\t\t\t\t\t\t\t\t\tprio_max, mode & RECOMPRESS_IDLE);\n'
)


def build_steps():
    """Six steps: three per node, in (prologue, sweep head, attempt) order.

    All required, so a node that learned the parameter but not the cap -- which
    would silently ignore max_pages -- cannot be produced by a partial apply.
    """
    return [
        (ZRAM_C, _SYNC_HEAD_OLD, _SYNC_HEAD_NEW, T),
        (ZRAM_C, _SYNC_LOOP_OLD, _SYNC_LOOP_NEW, T),
        (ZRAM_C, _SYNC_CALL_OLD, _SYNC_CALL_NEW, T),
        (ZRAM_C, _ASYNC_HEAD_OLD, _ASYNC_HEAD_NEW, T),
        (ZRAM_C, _ASYNC_LOOP_OLD, _ASYNC_LOOP_NEW, T),
        (ZRAM_C, _ASYNC_ENQUEUE_OLD, _ASYNC_ENQUEUE_NEW, T),
    ]


# The two symbols the earlier groups write and this group edits around.
RECOMPRESS_HELPER = "static int zram_recompress(struct zram *zram, u32 index,"
RECOMPRESS_ASYNC_STORE = "static ssize_t recompress_async_store(struct device *dev,"
# This group's own added symbol, so the tests and the audits can probe for it.
MAX_PAGES_CAP_DECL = "u64 num_recomp_pages = ULLONG_MAX;"
MAX_PAGES_MARKER = "ABK stable_515_backport: 34efe1c3b688"


def _max_pages_apply(ctx):
    try:
        text = ctx.read(ZRAM_C)
    except FileNotFoundError:
        return "blocked_by_shape", ZRAM_C + ": file absent"
    # recompress_store() is generated by zram_recompression and
    # recompress_async_store() by zram_async_recompress: without both, this
    # group's anchors cannot exist.  Degrade instead of patching one node only.
    if RECOMPRESS_HELPER not in text:
        return "blocked_by_shape", ("zram_recompress() not found: "
                                    "zram_recompression must apply first")
    if RECOMPRESS_ASYNC_STORE not in text:
        return "blocked_by_shape", ("recompress_async_store() not found: "
                                    "zram_async_recompress must apply first")
    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the single Batch-24 PatchGroup record."""
    return [
        PatchGroup(
            "zram_recompress_max_pages",
            "bound a recompression pass: recompress and recompress_async gain a "
            "max_pages parameter (counting attempts, not successes) and reject "
            "an unrecognised type= value instead of silently recompressing the "
            "whole device",
            [
                "34efe1c3b688 (v6.10) -- max_pages param",
                "2f529e73d720 (v7.1) -- reject unrecognized type= values",
            ],
            [ZRAM_C],
            _max_pages_apply,
        ),
    ]
