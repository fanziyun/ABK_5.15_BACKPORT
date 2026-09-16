"""Batch 32: keep a written-back slot's metadata -- and its stats -- intact.

Sourcing (read as a real patch, not from memory):

* `b0377ee8042985b0d91bf579afcc4ee9150db14d` ("zram: do not slot_free()
  written-back slots", Sergey Senozhatsky, mm-hotfixes-stable 2026-03,
  `Fixes: d38fab605c667`, Acked-by Minchan Kim) -- the mainline fix.
* `37b72d52550213151d56ddf04f65200d27d847fa` ("BACKPORT: FROMGIT: zram: do not
  slot_free() written-back slots", ACK android16-6.12, richardycc) -- the same
  change re-anchored onto the ACK slot APIs (`req->pps->index`,
  `zram->compressed_wb`, `zram_set_handle`).

Both halves of the fix matter here, and neither is a copy of the ACK form --
this module's writeback is the v6.19 series re-anchored onto 5.15's own
`ZRAM_UNDER_WB` / `ZRAM_IDLE` protocol, so the slot APIs and the surrounding
re-validation block are this module's own (`req->index`, `zram->wb_compressed`,
`zram_set_element`), and the free it must stop calling is 5.15's
`zram_free_page()` rather than a `slot_free()` helper.

Why this is a group of its own rather than an edit inside
`zram_writeback_batching` (which generated the text it rewrites): the fix is an
upstream commit with its own `Fixes:` line, and `docs/group_recipe.md` trap 5
says a later group may only rewrite an earlier group's replacement block once
that earlier group probes its own payload.  `zram_writeback_batching` now
short-circuits on `zram_account_writeback_submit()`, so a second pass reports
`already_present` instead of degrading to `blocked_by_shape` on the anchors
this group rewrote.

What it fixes, in the shape this module actually has:

* **->huge_pages underflow (the observable defect).** ``zram_writeback_complete()``
  used to call ``zram_free_page()`` and then restore the metadata the compressed
  read path needs -- including ``ZRAM_HUGE`` -- but the restore re-set the *flag*
  without giving back the ``atomic64_dec(&zram->stats.huge_pages)`` that
  ``zram_free_page()`` had just done.  The counter therefore fell one short of
  the flagged slots, and the slot's final release (``zram_free_page()`` on a
  ``ZRAM_WB`` slot, which reaches the huge block before the writeback early
  return) decremented it again: net one decrement too many per written-back huge
  page.  Reachable exactly on this module's ``huge`` writeback trigger with
  ``compressed_writeback`` on -- and the companion sets that attribute to 1
  (``ksu/abk_runtime_tunables/zram-policy.sh``), so on this device the pair is
  the shipped configuration, not an exotic one.
* **Lost slot metadata (upstream's other half).** ``zram_free_page()`` resets
  every flag and attribute, and the old restore covered only ``ZRAM_HUGE``,
  ``obj_size`` and ``priority``: ``ZRAM_INCOMPRESSIBLE`` and ``ac_time`` were
  simply dropped on each written-back slot.  This one is *latent* rather than
  live in this module's configuration, and the honest statement of that is the
  point: the two policies that read those inputs (``zram_recompression``'s
  candidate filter, ``mark_idle``'s ``ac_time`` cutoff) both skip ``ZRAM_WB``
  slots outright, and a read of a written-back slot refreshes ``ac_time`` through
  ``zram_accessed()`` -- so no current reader observes the loss.  It is removed
  because upstream removed it and because the same release is what deletes the
  counter bug, not because a device measurement shows it.

The fix open-codes the release instead of calling ``zram_free_page()``: the
zsmalloc object, ``compr_data_size``, ``huge_pages`` and ``ZRAM_IDLE`` go away,
everything else stays.  ``pages_stored`` is left untouched in *both*
directions -- ``zram_free_page()``'s ``out:`` label used to decrement it and the
increment below compensated, so removing both preserves the invariant the
v0.30.1 sweep table recorded (a page that moved to the backing device still
counts as stored in zram: 896 MB -> 896 MB across a full huge sweep).  That
table is a *pre-fix* measurement and cannot tell this fix from the bug -- it is
cited only for the number this change had to keep, not as evidence for the
change.

Second half: ``zram_free_page()``'s huge block only decrements ``->huge_pages``
for a slot that was never written back, because writeback completion already
accounted that huge page and deliberately kept the flag for the deferred
decompression path.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

ZRAM_C = "drivers/block/zram/zram_drv.c"

T = True

# ---------------------------------------------------------------------------
# Step 1: the locals the save/restore dance needed are gone with it.
# ---------------------------------------------------------------------------

# Includes the signature so the anchor is unique on its own, and so the step
# reads as "this function's prologue" rather than a bare declaration line.
_COMPLETE_DECL_OLD = (
    "static int zram_writeback_complete(struct zram *zram, struct zram_wb_req *req)\n"
    "{\n"
    "\tu32 index = req->index;\n"
    "\tu32 size = 0, prio = 0;\n"
    "\tbool huge = false;\n"
    "\tint err;\n"
)

_COMPLETE_DECL_NEW = (
    "static int zram_writeback_complete(struct zram *zram, struct zram_wb_req *req)\n"
    "{\n"
    "\tu32 index = req->index;\n"
    "\tint err;\n"
)

# ---------------------------------------------------------------------------
# Step 2: the release itself.  Anchored from the save block through the
# pages_stored increment, i.e. the whole success path after the failure test,
# so the save block cannot be left behind as dead code (removing it on its own
# is not expressible: an empty replacement is always "already_present").
# ---------------------------------------------------------------------------

_COMPLETE_RELEASE_OLD = (
    "\tif (zram->wb_compressed) {\n"
    "\t\t/*\n"
    "\t\t * ABK stable_515_backport: d38fab605c66.  The backing device holds\n"
    "\t\t * the raw object, so the slot has to keep the metadata the read\n"
    "\t\t * path needs to decompress it: zram_free_page() clears all of it.\n"
    "\t\t */\n"
    "\t\tsize = zram_get_obj_size(zram, index);\n"
    "\t\tprio = zram_get_priority(zram, index);\n"
    "\t\thuge = zram_test_flag(zram, index, ZRAM_HUGE);\n"
    "\t}\n"
    "\n"
    "\tatomic64_inc(&zram->stats.bd_writes);\n"
    "\tzram_slot_lock(zram, index);\n"
    "\t/*\n"
    "\t * The slot lock was released for the bio, so the slot can have changed\n"
    "\t * under us: slot_free(), or slot_free() plus zram_write_page().  Both\n"
    "\t * clear ZRAM_IDLE (zram_free_page() clears it), and idle_store() never\n"
    "\t * sets ZRAM_IDLE on a ZRAM_UNDER_WB slot, so ZRAM_IDLE is a sound \"this\n"
    "\t * is still the slot we read\" test.\n"
    "\t */\n"
    "\tif (!zram_allocated(zram, index) ||\n"
    "\t    !zram_test_flag(zram, index, ZRAM_IDLE)) {\n"
    "\t\tzram_clear_flag(zram, index, ZRAM_UNDER_WB);\n"
    "\t\tzram_clear_flag(zram, index, ZRAM_IDLE);\n"
    "\t\tfree_block_bdev(zram, req->blk_idx);\n"
    "\t\tgoto out;\n"
    "\t}\n"
    "\n"
    "\tzram_free_page(zram, index);\n"
    "\tzram_clear_flag(zram, index, ZRAM_UNDER_WB);\n"
    "\tzram_set_flag(zram, index, ZRAM_WB);\n"
    "\tzram_set_element(zram, index, req->blk_idx);\n"
    "\n"
    "\tif (zram->wb_compressed) {\n"
    "\t\tif (huge)\n"
    "\t\t\tzram_set_flag(zram, index, ZRAM_HUGE);\n"
    "\t\tzram_set_obj_size(zram, index, size);\n"
    "\t\tzram_set_priority(zram, index, prio);\n"
    "\t}\n"
    "\n"
    "\tatomic64_inc(&zram->stats.pages_stored);\n"
)

_COMPLETE_RELEASE_NEW = (
    "\tatomic64_inc(&zram->stats.bd_writes);\n"
    "\tzram_slot_lock(zram, index);\n"
    "\t/*\n"
    "\t * The slot lock was released for the bio, so the slot can have changed\n"
    "\t * under us: slot_free(), or slot_free() plus zram_write_page().  Both\n"
    "\t * clear ZRAM_IDLE (zram_free_page() clears it), and idle_store() never\n"
    "\t * sets ZRAM_IDLE on a ZRAM_UNDER_WB slot, so ZRAM_IDLE is a sound \"this\n"
    "\t * is still the slot we read\" test.\n"
    "\t */\n"
    "\tif (!zram_allocated(zram, index) ||\n"
    "\t    !zram_test_flag(zram, index, ZRAM_IDLE)) {\n"
    "\t\tzram_clear_flag(zram, index, ZRAM_UNDER_WB);\n"
    "\t\tzram_clear_flag(zram, index, ZRAM_IDLE);\n"
    "\t\tfree_block_bdev(zram, req->blk_idx);\n"
    "\t\tgoto out;\n"
    "\t}\n"
    "\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: b0377ee80429.  Release the slot without\n"
    "\t * zram_free_page(): that helper resets every flag and attribute, so the\n"
    "\t * slot would lose metadata the paths need -- obj_size and priority for\n"
    "\t * the compressed read, ZRAM_HUGE for the raw one, and (upstream's other\n"
    "\t * reason for this commit) ZRAM_INCOMPRESSIBLE plus ac_time.  It also\n"
    "\t * decrements ->huge_pages, and re-setting ZRAM_HUGE afterwards does not\n"
    "\t * give that decrement back: the slot's final release would decrement\n"
    "\t * the counter a second time (underflow).\n"
    "\t *\n"
    "\t * Release only what completion owes.  pages_stored is deliberately left\n"
    "\t * alone in both directions: zram_free_page()'s out: label used to\n"
    "\t * decrement it and the increment that closed this path compensated, so\n"
    "\t * dropping both keeps a written-back page counted as stored.\n"
    "\t */\n"
    "\tzram_clear_flag(zram, index, ZRAM_IDLE);\n"
    "\tif (zram_test_flag(zram, index, ZRAM_HUGE))\n"
    "\t\tatomic64_dec(&zram->stats.huge_pages);\n"
    "\tatomic64_sub(zram_get_obj_size(zram, index),\n"
    "\t\t     &zram->stats.compr_data_size);\n"
    "\tzs_free(zram->mem_pool, zram_get_handle(zram, index));\n"
    "\tzram_clear_flag(zram, index, ZRAM_UNDER_WB);\n"
    "\tzram_set_flag(zram, index, ZRAM_WB);\n"
    "\tzram_set_element(zram, index, req->blk_idx);\n"
)

# ---------------------------------------------------------------------------
# Step 3: the release side of the same accounting, in zram_free_page().
# ---------------------------------------------------------------------------

# The huge block is byte-identical in pristine 5.15 and in the shape
# zram_recompression rewrites it into (that group repeats the block verbatim
# before adding its INCOMPRESSIBLE/priority resets), and it is the only
# ->huge_pages decrement in the file -- so this anchor lands whether or not
# that group applied.
_FREE_PAGE_HUGE_OLD = (
    "\tif (zram_test_flag(zram, index, ZRAM_HUGE)) {\n"
    "\t\tzram_clear_flag(zram, index, ZRAM_HUGE);\n"
    "\t\tatomic64_dec(&zram->stats.huge_pages);\n"
    "\t}\n"
)

_FREE_PAGE_HUGE_NEW = (
    "\tif (zram_test_flag(zram, index, ZRAM_HUGE)) {\n"
    "\t\tzram_clear_flag(zram, index, ZRAM_HUGE);\n"
    "\t\t/*\n"
    "\t\t * ABK stable_515_backport: b0377ee80429.  A written-back slot keeps\n"
    "\t\t * ZRAM_HUGE for the deferred decompression path, and its huge page\n"
    "\t\t * was already accounted when the writeback completed -- decrement\n"
    "\t\t * only for a slot that was never written back.\n"
    "\t\t */\n"
    "\t\tif (!zram_test_flag(zram, index, ZRAM_WB))\n"
    "\t\t\tatomic64_dec(&zram->stats.huge_pages);\n"
    "\t}\n"
)


def build_steps():
    """Three required steps: prologue, release, and the free-side guard.

    All required: a tree that released the slot one way on one side and the
    other way on the other side would still double-count huge pages.
    """
    return [
        (ZRAM_C, _COMPLETE_DECL_OLD, _COMPLETE_DECL_NEW, T),
        (ZRAM_C, _COMPLETE_RELEASE_OLD, _COMPLETE_RELEASE_NEW, T),
        (ZRAM_C, _FREE_PAGE_HUGE_OLD, _FREE_PAGE_HUGE_NEW, T),
    ]


# The symbol zram_writeback_batching adds, this group rewrites around, and the
# earlier group now probes for (docs/group_recipe.md trap 5).  Kept here too so
# the unit test can pin the two spellings together.
BATCHING_PAYLOAD = "static void zram_account_writeback_submit(struct zram *zram)"
# This group's own added text, for the tests and the audits to probe for.
SLOT_PRESERVE_MARKER = "ABK stable_515_backport: b0377ee80429"
HUGE_GUARD = ("\t\tif (!zram_test_flag(zram, index, ZRAM_WB))\n"
              "\t\t\tatomic64_dec(&zram->stats.huge_pages);\n")


def _slot_preserve_apply(ctx):
    try:
        text = ctx.read(ZRAM_C)
    except FileNotFoundError:
        return "blocked_by_shape", ZRAM_C + ": file absent"
    # The completion helper is generated by zram_writeback_batching: without it
    # neither anchor of steps 1-2 exists.  Degrade instead of half-fixing the
    # accounting on one side only.
    if "static int zram_writeback_complete(struct zram *zram, struct zram_wb_req *req)" not in text:
        return "blocked_by_shape", ("zram_writeback_complete() not found: "
                                    "zram_writeback_batching must apply first")
    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the single Batch-32 PatchGroup record."""
    return [
        PatchGroup(
            "zram_wb_slot_preserve",
            "release a written-back slot without zram_free_page(): keep the "
            "metadata the read and recompression paths need (obj_size, "
            "priority, ZRAM_HUGE, ZRAM_INCOMPRESSIBLE, ac_time) and stop "
            "decrementing ->huge_pages twice, while leaving pages_stored "
            "unchanged",
            [
                "b0377ee80429 (mm-hotfixes-stable, Fixes: d38fab605c667) -- "
                "do not slot_free() written-back slots",
                "37b72d525502 (ACK android16-6.12) -- the same fix against the "
                "ACK slot APIs",
            ],
            [ZRAM_C],
            _slot_preserve_apply,
        ),
    ]
