from __future__ import annotations

from abk_backport_engine import apply_steps

ZRAM_C = "drivers/block/zram/zram_drv.c"

T = True


_A_META_FREE = (
    "\tsize_t num_pages = disksize >> PAGE_SHIFT;\n"
    "\tsize_t index;\n"
    "\n"
    "\t/* Free all pages that are still in this zram device */"
)

_A_META_FREE_NEW = (
    "\tsize_t num_pages = disksize >> PAGE_SHIFT;\n"
    "\tsize_t index;\n"
    "\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: 74363ec674cb.  zram_remove() now calls\n"
    "\t * reset_bdev() directly (see _A_REMOVE below), so this function can be\n"
    "\t * entered for a device that never reached `disksize`: both the table and\n"
    "\t * the pool can be NULL here.  zs_destroy_pool() dereferences\n"
    "\t * pool->size_class[], hence the guard is load-bearing.\n"
    "\t */\n"
    "\tif (!zram->table)\n"
    "\t\treturn;\n"
    "\n"
    "\t/* Free all pages that are still in this zram device */"
)

_A_VFREE = (
    "\tzs_destroy_pool(zram->mem_pool);\n"
    "\tvfree(zram->table);\n"
    "}"
)

_A_VFREE_NEW = (
    "\tzs_destroy_pool(zram->mem_pool);\n"
    "\tvfree(zram->table);\n"
    "\t/* ABK stable_515_backport: 74363ec674cb -- keep the NULL check above\n"
    "\t * meaningful on a second reset of the same struct. */\n"
    "\tzram->table = NULL;\n"
    "}"
)

_A_REMOVE = (
    "\t/* Make sure all the pending I/O are finished */\n"
    "\tfsync_bdev(bdev);\n"
    "\tzram_reset_device(zram);\n"
    "\n"
    "\tpr_info(\"Removed device: %s\\n\", zram->disk->disk_name);"
)

_A_REMOVE_NEW = (
    "\t/* Make sure all the pending I/O are finished */\n"
    "\tfsync_bdev(bdev);\n"
    "\tzram_reset_device(zram);\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: 74363ec674cb.  zram_reset_device() returns\n"
    "\t * early on a device that never reached `disksize`, and that early return\n"
    "\t * skips reset_bdev() -- so a backing device attached in the pre-disksize\n"
    "\t * window (the ROM and the runtime companion both do that) kept the\n"
    "\t * exclusive blkdev holder and the struct file until the module was\n"
    "\t * unloaded, pinning the backing block device.  reset_bdev() is a no-op\n"
    "\t * once the device is already detached (and an inline no-op entirely when\n"
    "\t * CONFIG_ZRAM_WRITEBACK is off, via the stub below), so calling it here\n"
    "\t * unconditionally is safe.  backing_dev_store() calls it again before it\n"
    "\t * installs a new backing device, so a later re-attach still starts from a\n"
    "\t * clean state.\n"
    "\t */\n"
    "\treset_bdev(zram);\n"
    "\n"
    "\tpr_info(\"Removed device: %s\\n\", zram->disk->disk_name);"
)


_B_DECL = (
    "\tunsigned long nr_pages = zram->disksize >> PAGE_SHIFT;\n"
    "\tunsigned long index = 0;"
)

_B_DECL_NEW = (
    "\t/*\n"
    "\t * ABK stable_515_backport: 894913e2d35c.  disksize is written under\n"
    "\t * init_lock (zram_reset_device), so nr_pages must be derived under the\n"
    "\t * read lock as well: a reset that re-initialises a smaller disksize\n"
    "\t * between this point and the scan would leave the bound describing the\n"
    "\t * freed table and the loop would index past the new one.\n"
    "\t */\n"
    "\tunsigned long nr_pages;\n"
    "\tunsigned long index = 0;"
)

_B_PARSE = (
    "\t\tif (kstrtol(buf + sizeof(PAGE_WB_SIG) - 1, 10, &index) ||\n"
    "\t\t\t\tindex >= nr_pages)\n"
    "\t\t\treturn -EINVAL;"
)

_B_PARSE_NEW = (
    "\t\tif (kstrtol(buf + sizeof(PAGE_WB_SIG) - 1, 10, &index))\n"
    "\t\t\treturn -EINVAL;"
)

_B_POSTLOCK = (
    "\tif (!zram->backing_dev) {\n"
    "\t\tret = -ENODEV;\n"
    "\t\tgoto release_init_lock;\n"
    "\t}"
)

_B_POSTLOCK_NEW = (
    "\tif (!zram->backing_dev) {\n"
    "\t\tret = -ENODEV;\n"
    "\t\tgoto release_init_lock;\n"
    "\t}\n"
    "\n"
    "\t/* ABK stable_515_backport: 894913e2d35c -- bound and range check\n"
    "\t * under the read lock, so they stay consistent with zram->table. */\n"
    "\tnr_pages = zram->disksize >> PAGE_SHIFT;\n"
    "\tif (index >= nr_pages) {\n"
    "\t\tret = -EINVAL;\n"
    "\t\tgoto release_init_lock;\n"
    "\t}\n"
    "\t/*\n"
    "\t * ABK fix.  The derivation above is unconditional, but the sweep\n"
    "\t * loop below counts *iterations* from `index` -- it is\n"
    "\t * ``for (; nr_pages != 0; index++, nr_pages--)``, so the bound is a\n"
    "\t * count, not an upper limit on `index`.  PAGE_WRITEBACK therefore\n"
    "\t * needs its single-iteration bound to survive this point: the\n"
    "\t * parse sets nr_pages = 1 for it, and overwriting that with the\n"
    "\t * device size makes ``page_index=N`` walk index up to\n"
    "\t * N + nr_pages - 1, past zram->table[nr_pages - 1].\n"
    "\t *\n"
    "\t * Measured on vermeer / 5.15.216 (2026-09-15): on a 16 GiB device\n"
    "\t * (nr_pages = 4194304, 24-byte zram_table_entry) `page_index=1`\n"
    "\t * reached index 4194304 and faulted in zram_slot_lock() --\n"
    "\t * ``&zram->table[4194304].flags`` is 8 bytes past the kvcalloc'd\n"
    "\t * 100663296-byte table, a level 3 translation fault that panics\n"
    "\t * the kernel (x1 == 0x400000 in the register dump).  `page_index=0`\n"
    "\t * did not fault -- it stopped exactly on the last valid slot -- but\n"
    "\t * silently swept the whole device instead of one page.\n"
    "\t *\n"
    "\t * The range check above cannot catch this: it validates only the\n"
    "\t * starting index, not start + count.\n"
    "\t */\n"
    "\tif (mode == PAGE_WRITEBACK)\n"
    "\t\tnr_pages = 1;"
)

_B_LOOP_TAIL = (
    "next:\n"
    "\t\tzram_slot_unlock(zram, index);\n"
    "\t}\n"
    "\n"
    "\tif (blk_idx)"
)

_B_LOOP_TAIL_NEW = (
    "next:\n"
    "\t\tzram_slot_unlock(zram, index);\n"
    "\t\t/*\n"
    "\t\t * ABK stable_515_backport: 424d0e5828ad.  A sweep can walk every\n"
    "\t\t * slot of a multi-GiB disk, each one with an alloc_page(), a\n"
    "\t\t * decompression and a blocking wait for in-flight writeback bios\n"
    "\t\t * while holding init_lock.  Yield so a reset/disksize store\n"
    "\t\t * waiting on the write lock (and the RCU/watchdog machinery) is\n"
    "\t\t * not starved.\n"
    "\t\t */\n"
    "\t\tcond_resched();\n"
    "\t}\n"
    "\n"
    "\tif (blk_idx)"
)

_C_LIMIT_STORE = (
    "\tif (kstrtoull(buf, 10, &val))\n"
    "\t\treturn ret;\n"
    "\n"
    "\tdown_read(&zram->init_lock);\n"
    "\tspin_lock(&zram->wb_limit_lock);\n"
    "\tzram->bd_wb_limit = val;"
)

_C_LIMIT_STORE_NEW = (
    "\tif (kstrtoull(buf, 10, &val))\n"
    "\t\treturn ret;\n"
    "\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: mainline writeback_limit_store() alignment\n"
    "\t * guard.  A page costs '1 << (PAGE_SHIFT - 12)' units, so on a\n"
    "\t * PAGE_SIZE > 4KiB build a budget smaller than one page underflows the\n"
    "\t * u64 on the first successful writeback (3 -> -1) and the wear cap\n"
    "\t * silently stops capping.  Round the budget down to a whole page.\n"
    "\t */\n"
    "\tval = rounddown(val, PAGE_SIZE / 4096);\n"
    "\n"
    "\tdown_read(&zram->init_lock);\n"
    "\tspin_lock(&zram->wb_limit_lock);\n"
    "\tzram->bd_wb_limit = val;"
)


def build_teardown_steps():
    return [
        (ZRAM_C, _A_META_FREE, _A_META_FREE_NEW, T),
        (ZRAM_C, _A_VFREE, _A_VFREE_NEW, T),
        (ZRAM_C, _A_REMOVE, _A_REMOVE_NEW, T),
    ]


def build_writeback_bounds_steps():
    return [
        (ZRAM_C, _B_DECL, _B_DECL_NEW, T),
        (ZRAM_C, _B_PARSE, _B_PARSE_NEW, T),
        (ZRAM_C, _B_POSTLOCK, _B_POSTLOCK_NEW, T),
        (ZRAM_C, _B_LOOP_TAIL, _B_LOOP_TAIL_NEW, T),
    ]


def build_limit_align_steps():
    return [
        (ZRAM_C, _C_LIMIT_STORE, _C_LIMIT_STORE_NEW, T),
    ]


def _teardown_apply(ctx):
    try:
        text = ctx.read(ZRAM_C)
    except FileNotFoundError:
        return "blocked_by_shape", f"{ZRAM_C}: file absent"
    for probe, why in (
        ("static void zram_meta_free(struct zram *zram, u64 disksize)",
         "zram_meta_free()"),
        ("static void zram_reset_device(struct zram *zram)", "zram_reset_device()"),
    ):
        if probe not in text:
            return "blocked_by_shape", f"{why} not found in zram_drv.c"
    status, _results, detail = apply_steps(ctx, build_teardown_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _writeback_bounds_apply(ctx):
    try:
        ctx.read(ZRAM_C)
    except FileNotFoundError:
        return "blocked_by_shape", f"{ZRAM_C}: file absent"
    status, _results, detail = apply_steps(ctx, build_writeback_bounds_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _limit_align_apply(ctx):
    try:
        text = ctx.read(ZRAM_C)
    except FileNotFoundError:
        return "blocked_by_shape", f"{ZRAM_C}: file absent"
    if "static ssize_t writeback_limit_store" not in text:
        return "blocked_by_shape", "writeback_limit_store() not found in zram_drv.c"
    status, _results, detail = apply_steps(ctx, build_limit_align_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    return [
        PatchGroup(
            "zram_wb_teardown",
            "release a writeback backing device attached before disksize: full "
            "teardown of an uninitialised device (74363ec674cb, with the "
            "zs_destroy_pool(NULL) guard that makes it safe)",
            [
                "74363ec674cb (v6.16, Cc: stable; Fixes: 013bf95a83ec)",
                "series partner be48c412f6eb is a precondition (5.15.168+), not "
                "a step -- see the module docstring for why 5.15.167 is accepted "
                "without it",
            ],
            [ZRAM_C],
            _teardown_apply,
        ),
        PatchGroup(
            "zram_writeback_bounds",
            "derive the writeback scan bound (and the page_index range check) "
            "under init_lock so a racing reset cannot index past the table, "
            "plus cond_resched() in the sweep loop (894913e2d35c + 424d0e5828ad, "
            "re-anchored to the 5.15 init_lock shape)",
            [
                "894913e2d35c (v7.3, Cc: stable; Fixes: a939888ec38b)",
                "424d0e5828ad (v6.14)",
            ],
            [ZRAM_C],
            _writeback_bounds_apply,
        ),
        PatchGroup(
            "zram_wb_limit_align",
            "round the writeback budget down to a whole page so a "
            "PAGE_SIZE > 4KiB build cannot underflow bd_wb_limit and silently "
            "disable the flash-wear cap (mainline writeback_limit_store() guard)",
            [
                "mainline writeback_limit_store(); lineage bb416d18b850 -> 1d69a3f8ae77",
            ],
            [ZRAM_C],
            _limit_align_apply,
        ),
    ]
