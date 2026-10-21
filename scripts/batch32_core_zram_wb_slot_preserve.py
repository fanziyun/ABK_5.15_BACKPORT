from __future__ import annotations

from abk_backport_engine import apply_steps

ZRAM_C = "drivers/block/zram/zram_drv.c"

T = True


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
    return [
        (ZRAM_C, _COMPLETE_DECL_OLD, _COMPLETE_DECL_NEW, T),
        (ZRAM_C, _COMPLETE_RELEASE_OLD, _COMPLETE_RELEASE_NEW, T),
        (ZRAM_C, _FREE_PAGE_HUGE_OLD, _FREE_PAGE_HUGE_NEW, T),
    ]


BATCHING_PAYLOAD = "static void zram_account_writeback_submit(struct zram *zram)"
SLOT_PRESERVE_MARKER = "ABK stable_515_backport: b0377ee80429"
HUGE_GUARD = ("\t\tif (!zram_test_flag(zram, index, ZRAM_WB))\n"
              "\t\t\tatomic64_dec(&zram->stats.huge_pages);\n")


def _slot_preserve_apply(ctx):
    try:
        text = ctx.read(ZRAM_C)
    except FileNotFoundError:
        return "blocked_by_shape", ZRAM_C + ": file absent"
    if "static int zram_writeback_complete(struct zram *zram, struct zram_wb_req *req)" not in text:
        return "blocked_by_shape", ("zram_writeback_complete() not found: "
                                    "zram_writeback_batching must apply first")
    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
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
