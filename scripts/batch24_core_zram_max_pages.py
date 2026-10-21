from __future__ import annotations

from abk_backport_engine import apply_steps

ZRAM_C = "drivers/block/zram/zram_drv.c"

T = True


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

_SYNC_CALL_OLD = (
    '\t\terr = zram_recompress(zram, index, page, threshold, prio, prio_max);\n'
)

_SYNC_CALL_NEW = (
    '\t\tnum_recomp_pages--;\n'
    '\t\terr = zram_recompress(zram, index, page, threshold, prio, prio_max);\n'
)


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
    return [
        (ZRAM_C, _SYNC_HEAD_OLD, _SYNC_HEAD_NEW, T),
        (ZRAM_C, _SYNC_LOOP_OLD, _SYNC_LOOP_NEW, T),
        (ZRAM_C, _SYNC_CALL_OLD, _SYNC_CALL_NEW, T),
        (ZRAM_C, _ASYNC_HEAD_OLD, _ASYNC_HEAD_NEW, T),
        (ZRAM_C, _ASYNC_LOOP_OLD, _ASYNC_LOOP_NEW, T),
        (ZRAM_C, _ASYNC_ENQUEUE_OLD, _ASYNC_ENQUEUE_NEW, T),
    ]


RECOMPRESS_HELPER = "static int zram_recompress(struct zram *zram, u32 index,"
RECOMPRESS_ASYNC_STORE = "static ssize_t recompress_async_store(struct device *dev,"
MAX_PAGES_CAP_DECL = "u64 num_recomp_pages = ULLONG_MAX;"
MAX_PAGES_MARKER = "ABK stable_515_backport: 34efe1c3b688"


def _max_pages_apply(ctx):
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
    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
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
