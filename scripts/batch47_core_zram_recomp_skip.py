# -*- coding: utf-8 -*-

from __future__ import annotations

from abk_backport_engine import apply_steps

ZRAM_C = "drivers/block/zram/zram_drv.c"

T = True


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
