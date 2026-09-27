# -*- coding: utf-8 -*-
"""Batch 47: a recompression sweep skips entries that cannot be improved.

Why this exists.  `zram.recomp.mark_interval_sec` (companion v0.17.0) re-marks the
cold set several times a day instead of once, and a re-mark re-hands every page
whose age was never refreshed -- which is every page a sweep just recompressed,
because `zram_recompress()` reads through `zram_read_from_zspool()` rather than
`zram_accessed()` and so never updates `ac_time`.  A page that is already stored
under the *best available* compressor is therefore handed back on every mark,
forever, and the kernel's sweep -- which always restarts at index 0 and spends
its `max_pages` budget on attempts "whether or not they can improve anything" --
burns that budget before it ever reaches a genuinely new cold page.

`zram_recompress()` already refuses to *do* anything for such an entry: its
priority loop ends with `!zstrm` and the function returns 0.  The waste is that
it gets there only *after* the caller has paid for a slot lock, a full
decompress and a `ZRAM_IDLE` clear, and -- on the async node -- after the scan
has already queued a job against its cap.  So the cheap bail-out has to live in
front of it, at the call site.

What the two grafted scans learn here.  Both filter a candidate entry before the
attempt; this adds one more filter in the same place, expressed with the exact
condition the priority loop inside `zram_recompress()` uses, so a skipped entry
is one that loop would have rejected anyway.  Entries that *could* still be moved
to a higher priority are still attempted, so a kernel with no secondary
compressor simply skips everything (there is nothing to gain) instead of paying
for a no-op pass -- which is also what `recomp_algorithm` being empty means.

Not ported from upstream.  There is no upstream commit to name: this is the
remedy for a behaviour the recompression series never had to solve, because
upstream has no knob that re-marks at a different rate than it drains.  The
condition is local to this module's `zram_recompress()` and its callers.

Graft-boundary contract.  Three pristine / generated-text boundaries, none of
them the inside of a replacement another group owns (see `docs/group_recipe.md`
trap 5, whose remedy the earlier groups already carry in the form of their own
payload probes -- `zram_recompression` probes `RECOMPRESS_HELPER` and
`zram_async_recompress` probes `RECOMPRESS_ASYNC_STORE`):

  * the helper goes on the block boundary in front of the whole
    `zram_recompression` machinery, i.e. immediately after the pristine comment
    that documents `zram_bio_discard()` -- the same anchor that group opens with,
    used here only as a *preceding* boundary so its block stays byte-identical;
  * the synchronous filter in `recompress_store()`, after the `ZRAM_WB` /
    `ZRAM_UNDER_WB` / `ZRAM_SAME` / `ZRAM_INCOMPRESSIBLE` block and in front of
    the attempt, so the `max_pages` decrement (Batch 24) is not spent on it;
  * the same filter in `recompress_async_store()`, in front of `candidate =
    true`, so no job is queued and the cap counts only real work.

Registered after `zram_recompress_max_pages` (Batch 24), which generates the
`num_recomp_pages--` line the synchronous step anchors behind.

Two symbols only: `abk_zram_recompress_pointless` and the
`sailboat_zram_recomp_skip` marker.  No exported struct, no KABI slot, no
Kconfig, no sysfs interface.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

ZRAM_C = "drivers/block/zram/zram_drv.c"

T = True

# ---------------------------------------------------------------------------
# Step 1: the predicate, on the block boundary in front of the recompression
# machinery.  Pristine text, unique in the file.
# ---------------------------------------------------------------------------

_HELP_OLD = (
    " * @offset: byte offset within physical block\n"
    " */\n"
)

_HELP_NEW = (
    " * @offset: byte offset within physical block\n"
    " */\n"
    "\n"
    "#ifdef CONFIG_ZRAM_MULTI_COMP\n"
    "/*\n"
    " * sailboat_zram_recomp_skip: would a recompression pass gain anything on\n"
    " * this slot?  True when no compressor above the slot's current priority can\n"
    " * take it -- the same condition the priority loop inside\n"
    " * zram_recompress() rejects on.  Asked in front of the attempt so the\n"
    " * caller does not pay for a slot lock, a decompress and a ZRAM_IDLE clear to\n"
    " * reach that rejection, and so the max_pages budget (Batch 24) is not spent\n"
    " * on entries that cannot improve.\n"
    " *\n"
    " * This matters because a re-mark re-hands every page whose age was never\n"
    " * refreshed, and zram_recompress() never refreshes it: it reads through\n"
    " * zram_read_from_zspool() rather than zram_accessed().  A page already at the\n"
    " * top priority is thus eligible on every mark, forever, and without this the\n"
    " * capped sweep spends its whole budget on those before reaching new cold\n"
    " * pages.\n"
    " */\n"
    "static bool abk_zram_recompress_pointless(struct zram *zram,\n"
    "\t\t\t\t\t unsigned long index, u32 prio, u32 prio_max)\n"
    "{\n"
    "\tu32 abk_cur = zram_get_priority(zram, index);\n"
    "\n"
    "\tfor (; prio < prio_max; prio++)\n"
    "\t\tif (prio > abk_cur && zram->comps[prio])\n"
    "\t\t\treturn false;\n"
    "\treturn true;\n"
    "}\n"
    "#endif\n"
)

# ---------------------------------------------------------------------------
# Step 2: the synchronous node (recompress_store(), generated by
# zram_recompression and extended by zram_recompress_max_pages).  Unique by the
# four-space continuation indent and the bare `next:` label.
# ---------------------------------------------------------------------------

_SYNC_OLD = (
    "\t\tif (zram_test_flag(zram, index, ZRAM_WB) ||\n"
    "\t\t    zram_test_flag(zram, index, ZRAM_UNDER_WB) ||\n"
    "\t\t    zram_test_flag(zram, index, ZRAM_SAME) ||\n"
    "\t\t    zram_test_flag(zram, index, ZRAM_INCOMPRESSIBLE))\n"
    "\t\t\tgoto next;\n"
    "\n"
    "\t\tnum_recomp_pages--;\n"
    "\t\terr = zram_recompress(zram, index, page, threshold, prio, prio_max);\n"
)

_SYNC_NEW = (
    "\t\tif (zram_test_flag(zram, index, ZRAM_WB) ||\n"
    "\t\t    zram_test_flag(zram, index, ZRAM_UNDER_WB) ||\n"
    "\t\t    zram_test_flag(zram, index, ZRAM_SAME) ||\n"
    "\t\t    zram_test_flag(zram, index, ZRAM_INCOMPRESSIBLE))\n"
    "\t\t\tgoto next;\n"
    "\n"
    "\t\t/* ABK stable_515_backport: Batch 47.  Bail out before the attempt\n"
    "\t\t * -- and before the cap decrement -- when nothing above this slot's\n"
    "\t\t * priority could compress it further.  A re-mark hands these back on\n"
    "\t\t * every mark, so this is what keeps the sweep reaching new pages.\n"
    "\t\t */\n"
    "\t\tif (abk_zram_recompress_pointless(zram, index, prio, prio_max))\n"
    "\t\t\tgoto next;\n"
    "\n"
    "\t\tnum_recomp_pages--;\n"
    "\t\terr = zram_recompress(zram, index, page, threshold, prio, prio_max);\n"
)

# ---------------------------------------------------------------------------
# Step 3: the asynchronous node (recompress_async_store(), generated by
# zram_async_recompress and extended by zram_recompress_max_pages).  Unique by
# the tab continuation indent and the `abk_async_next:` label.
# ---------------------------------------------------------------------------

_ASYNC_OLD = (
    "\t\tif (zram_test_flag(zram, index, ZRAM_WB) ||\n"
    "\t\t\tzram_test_flag(zram, index, ZRAM_UNDER_WB) ||\n"
    "\t\t\tzram_test_flag(zram, index, ZRAM_SAME) ||\n"
    "\t\t\tzram_test_flag(zram, index, ZRAM_INCOMPRESSIBLE))\n"
    "\t\t\tgoto abk_async_next;\n"
    "\n"
    "\t\tcandidate = true;\n"
)

_ASYNC_NEW = (
    "\t\tif (zram_test_flag(zram, index, ZRAM_WB) ||\n"
    "\t\t\tzram_test_flag(zram, index, ZRAM_UNDER_WB) ||\n"
    "\t\t\tzram_test_flag(zram, index, ZRAM_SAME) ||\n"
    "\t\t\tzram_test_flag(zram, index, ZRAM_INCOMPRESSIBLE))\n"
    "\t\t\tgoto abk_async_next;\n"
    "\n"
    "\t\t/* ABK stable_515_backport: Batch 47.  Same bail-out as the synchronous\n"
    "\t\t * node, ahead of `candidate = true` so no job is queued and the\n"
    "\t\t * max_pages budget counts only entries that can still improve.\n"
    "\t\t */\n"
    "\t\tif (abk_zram_recompress_pointless(zram, index, prio, prio_max))\n"
    "\t\t\tgoto abk_async_next;\n"
    "\n"
    "\t\tcandidate = true;\n"
)

RECOMPRESS_HELPER = "static int zram_recompress(struct zram *zram, u32 index,"
RECOMPRESS_ASYNC_STORE = "static ssize_t recompress_async_store(struct device *dev,"
SKIP_PREDICATE = "static bool abk_zram_recompress_pointless("
SKIP_MARKER = "ABK stable_515_backport: Batch 47"


def build_steps():
    """Three steps: the predicate, then one filter per recompression node.

    All required.  A node that learned the predicate but not one of its two
    filters would keep spending its budget on the entries the other node now
    skips, which is exactly the half-grafted state the companion's cadence
    change is supposed to remove.
    """
    return [
        (ZRAM_C, _HELP_OLD, _HELP_NEW, T),
        (ZRAM_C, _SYNC_OLD, _SYNC_NEW, T),
        (ZRAM_C, _ASYNC_OLD, _ASYNC_NEW, T),
    ]


def _skip_apply(ctx):
    try:
        text = ctx.read(ZRAM_C)
    except FileNotFoundError:
        return "blocked_by_shape", ZRAM_C + ": file absent"
    # Both scans are generated text: recompress_store() by zram_recompression
    # and recompress_async_store() by zram_async_recompress, and the second one
    # additionally carries the max_pages decrement this group anchors behind.
    # Without all three the anchors cannot exist, and patching one node only
    # would leave the other spending its budget on the same waste.
    if RECOMPRESS_HELPER not in text:
        return "blocked_by_shape", ("zram_recompress() not found: "
                                    "zram_recompression must apply first")
    if RECOMPRESS_ASYNC_STORE not in text:
        return "blocked_by_shape", ("recompress_async_store() not found: "
                                    "zram_async_recompress must apply first")
    if "num_recomp_pages--;" not in text:
        return "blocked_by_shape", ("the max_pages decrement is not in either "
                                    "scan: zram_recompress_max_pages must "
                                    "apply first")
    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the single Batch-47 PatchGroup record."""
    return [
        PatchGroup(
            "zram_recomp_best_prio_skip",
            "a recompression sweep skips entries already at the best available "
            "compressor priority, so a re-mark cannot spend the max_pages "
            "budget on attempts that cannot improve anything",
            [
                "Batch 47 design (plan.md): the companion's shorter mark clock "
                "re-marks already-recompressed pages, and the capped sweep "
                "never got past them",
            ],
            [ZRAM_C],
            _skip_apply,
        ),
    ]
