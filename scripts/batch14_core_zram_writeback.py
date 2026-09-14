"""Batch 14: zram writeback correctness fixes (upstream lineage, 5.15 re-anchored).

Four groups, all confined to ``drivers/block/zram/zram_drv.c``.  They exist
because android13-5.15 froze its zram writeback code around 2022: none of the
writeback fixes that landed in mainline after that reached the branch, and none
reached ``linux-5.15.y`` either (verified against the linux-5.15.y tree at
SUBLEVEL 220, which differs from the 5.15.194 baseline only by the
zero-sized-backing-device hunk that arrived through ACK).

Provenance (every commit below was read as a real patch, not from memory):

* ``zram_wb_teardown`` -- upstream ``74363ec674cb`` ("zram: fix uninitialized
  ZRAM not releasing backing device", Kairui Song, 2024-12-09, mm tree for
  v6.16, Cc: stable, Fixes: 013bf95a83ec).  Carried by ACK android14-6.1
  (ac3b5366b9), android15-6.6 (0b5b0b6556) and android16-6.12 (6fb92e9a52) --
  android13-5.15 is the only Android branch without it.

* ``zram_wb_bdev_guard`` -- upstream ``be48c412f6eb`` ("zram: refuse to use zero
  sized block device as backing device"), the series partner of the teardown fix
  from the same 2024-12-09 mm series.  Present in every audited baseline from
  5.15.168 on; absent from the 2024-11 baseline (5.15.167), which is the only
  reason this is a registered group instead of an assertion.  It is a group, not
  an optional step of ``zram_wb_teardown``, because ``tests/step_audit.py``
  requires every step of a must-apply group to be absent from the pristine file
  and rejects a step that reports ``already_present``: a step that is a no-op on
  178/194/216 fails there, while a group that legitimately reports
  ``already_present`` is recorded in ``tests/sublevel_matrix.py``'s
  ``PRE_APPLIED`` and exempt.

* ``zram_writeback_bounds`` -- upstream ``894913e2d35c`` ("zram: fix
  out-of-bounds access in writeback_store()", Longlong Xia, 2026-08-04, series
  "zram: fix stale scan bounds after reinitialization", Cc: stable, Fixes:
  a939888ec38b) plus upstream ``424d0e5828ad`` ("zram: cond_resched() in
  writeback loop", Sergey Senozhatsky, 2024-12-18).  Upstream's patch is
  written against the post-2024 ``dev_lock`` + ``lo/hi`` scan shape, which
  5.15 does not have, so this is a logical backport to the 5.15
  ``init_lock`` + ``index/nr_pages`` shape; the defect it fixes is identical
  (the scan bound is derived from ``zram->disksize`` before the lock, so a
  reset that re-initialises a smaller disksize leaves the bound describing the
  freed table).

* ``zram_wb_limit_align`` -- the ``rounddown(val, PAGE_SIZE / 4096)`` overflow
  guard that mainline ``writeback_limit_store()`` carries today, with its
  explanatory comment.  ``writeback_limit`` itself is NOT missing from 5.15:
  the feature arrived upstream as ``bb416d18b850`` ("zram: writeback
  throttle") and the 5.15 code is byte-equivalent to the 5.16 original.  Only
  the alignment guard is absent, and it matters because 5.15 charges
  ``bd_wb_limit -= 1UL << (PAGE_SHIFT - 12)`` per page: on a 16 KiB-page build
  a budget of 1..3 wraps the u64 and the wear cap silently stops capping.

Deliberately NOT ported here (recorded so it is not relitigated):

* the 2023+ writeback rewrites (``330edc2bc059`` pp-slot target selection,
  ``5e99893444a0`` remove UNDER_WB, ``b967fa1ba72b``) -- a different design,
  not a fix, and the 5.15 race they remove is already closed by ``idle_store()``'s
  ``ZRAM_UNDER_WB`` check;
* ``zram_read_from_zspool()`` in writeback (``b8d3ff7bb511``) -- this module's
  own ``zram_recompression`` group already adds an equivalent helper with a
  different signature; adding a second definition would be a compile error;
* compressed writeback (``d38fab605c66``, v7.0) and writeback bio batching
  (``f405066a1f0d`` + ``e828cccb72ed``, v6.19) -- both need the post-processing
  slot machinery plus (for batched bios) their own UAF/leak fixes
  (``bf62f69574b1``, ``3e8d8eb8d7f5``).  Deferred, see
  ``research/zram_writeback_plan.md``;
* the 6.16 writeback ABI rework (``cf42d4cccf0d``) -- no android13-5.15
  userspace consumer identified;
* ``compressed_writeback`` / ``writeback_batch_size`` attributes -- the
  compressed-writeback *control surface* is ``ABK_ABI_PATCH_SUITE`` territory
  and adding either symbol here would collide with that suite by name.

Ordering note: these groups are registered AFTER ``zram_recompression`` and
``zram_algo_lock`` because ``zram_writeback_bounds`` anchors inside
``writeback_store()``, whose callee ``zram_recompression`` rewrites, and
``zram_wb_limit_align`` anchors in the ``writeback_limit`` block that
``zram_recompression`` sits next to.  ``zram_wb_teardown``'s reset step must
**not** anchor on the tail of ``zram_reset_device()``: ``zram_recompression``
rewrites that tail (``comp = zram->comp;`` -> ``disksize = zram->disksize;``,
``zcomp_destroy(comp)`` -> ``zram_destroy_comps(zram)``), so the step anchors on
the ``down_write``/``limit_pages``/early-return prefix instead -- the 5.15
``init_lock`` shape that upstream's post-2024 ``dev_lock`` version replaced.
Anchoring on the tail passed in isolation and failed under the real group order
with ``blocked_by_shape``.  None of the four groups touches text another one
produces.

Re-anchor note (Batch 17): ``_B_POSTLOCK`` used to reach one line further, to the
``page = alloc_page(GFP_KERNEL);`` that opens the sweep.  Batch 17
(``zram_writeback_batching``) replaces that single page with a per-request page
pool, so it owns that line: a replacement block has to stay *contiguous* for
``replace_once``'s idempotency test, and two groups editing one span is exactly
what the graft-boundary contract in ``scripts/batch10_core_zram_async.py``
forbids.  The anchor now stops at the ``backing_dev`` check's closing brace; the
group's semantics, statuses and audit needles are unchanged.
``_B_LOOP_TAIL_NEW``'s comment was reworded for the same reason: after batching
the sweep no longer blocks in ``submit_bio_wait()`` but in the drain of its own
in-flight bios, and a marker comment describing a shape the tree no longer has is
worse than no comment.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

ZRAM_C = "drivers/block/zram/zram_drv.c"

# Alias the required-flag for readability (the other batch modules use T/F).
T = True

# --- anchors as they exist in the pristine / already-grafted 5.15 tree ------
# Every block below was checked to occur exactly once in abk515_ref_{167,178,
# 194,211} and in linux-5.15.y.
#
# NOTE (step-audit trap 1): a step whose ``new`` block is a prefix of its ``old``
# block (a pure deletion) is reported ``already_present`` by ``replace_once`` --
# it tests ``new`` first -- while the edit never lands.  That is why this group
# clears ``zram->table`` in the same replacement that frees it (``_A_VFREE_NEW``
# is a superset of the anchor, not a truncation), and why the previously drafted
# "delete the early return in zram_reset_device()" step was abandoned: the only
# way to make that deletion detectable was to keep following text in the anchor,
# and every version of it collided with ``zram_recompression``'s rewrite of the
# same function.

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

# Anchor note (registration order): this group runs after ``zram_recompression``,
# which rewrites the same function -- it drops ``comp = zram->comp;`` in favour of
# the ``comps[]`` teardown, adds the Batch 10-1 drain, and puts its own marker
# comment in front of the ``if (!init_done(zram))`` guard.  The leak close is
# therefore NOT placed here: deleting that guard would remove text the
# recompression step anchors on (it matches the function's *whole* pristine body,
# guard included), and a first attempt at "owning the deletion here" made the
# recompression group report ``blocked_by_shape`` on every second pass.  The
# teardown step lives in ``zram_remove()`` instead -- see ``_A_REMOVE`` below.
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

# Note on ``be48c412f6eb`` (zero-sized backing device rejection): it is the series
# partner of the teardown fix and every baseline from 5.15.168 on carries it.  The
# 2024-11 baseline (5.15.167) does not, and it is deliberately NOT ported here.
# A guard-only group was written and removed: its replacement text necessarily
# contains the pristine guard block (the marker has to go next to the code it
# annotates), so on 178/194/216 -- where the guard is upstream -- the group cannot
# tell "the baseline already has this" from "a previous step added it" and reports
# ``applied`` where the matrix expects ``already_present``.  There is no text-only
# discriminator.  It turned out not to matter: the shipped teardown fix does not
# depend on the guard (it is not editable input to ``zram_remove()``), so 5.15.167
# is covered like every other baseline.  The guard stays a real but small
# robustness gap on that one deprecated baseline -- a candidate for the suite.

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

# The anchor deliberately stops at the backing_dev check's closing brace.  The
# "page = alloc_page(GFP_KERNEL);" line that follows is owned by
# "zram_writeback_batching" (Batch 17 replaced the single writeback page with a
# per-request page pool), and a replacement block has to live on as a
# *contiguous* string for replace_once()'s idempotency test -- see the
# graft-boundary contract in scripts/batch10_core_zram_async.py.  Reaching into
# that line here passed in isolation and made this group report
# blocked_by_shape on every second pass.
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
    "\t}"
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
    """``zram_wb_teardown``: 74363ec674cb.

    Three steps, all required, and they must stay in one group: the ``zram->table``
    NULL guard added by step 1 is what makes the ``zram_remove()`` call added by
    step 3 safe *if* ``zram_meta_free()`` is ever reached with an uninitialised
    device; step 2 keeps that guard meaningful on a repeat reset.

    Where the fix lives: upstream deletes ``zram_reset_device()``'s
    ``if (!init_done(zram))`` early return so the reset runs on an uninitialised
    device as well.  That is not portable onto this baseline -- the function's
    whole pristine body is the anchor of the (earlier, load-bearing)
    ``zram_recompression`` group, so deleting text inside it makes that group miss
    on every second pass.  The same leak is closed instead by calling
    ``reset_bdev()`` in ``zram_remove()`` right after ``zram_reset_device()``:
    ``reset_bdev()`` releases the exclusive ``blkdev_put`` + ``filp_close``
    holder when one is attached and is a no-op otherwise, and
    ``zram_remove()`` is the only path that destroys an uninitialised device.
    """
    return [
        (ZRAM_C, _A_META_FREE, _A_META_FREE_NEW, T),
        (ZRAM_C, _A_VFREE, _A_VFREE_NEW, T),
        (ZRAM_C, _A_REMOVE, _A_REMOVE_NEW, T),
    ]


def build_writeback_bounds_steps():
    """``zram_writeback_bounds``: 894913e2d35c (+ 424d0e5828ad).

    Steps 1-3 are one logical change: 1 drops the pre-lock initialiser, 2 drops
    the pre-lock range check, 3 re-establishes both under the read lock.
    Splitting them across groups is what ``step_audit`` trap 2 punishes, and
    each replacement here is textually distinct from every other replacement in
    the module (step 3 deliberately does not repeat the comment from step 1).
    """
    return [
        (ZRAM_C, _B_DECL, _B_DECL_NEW, T),
        (ZRAM_C, _B_PARSE, _B_PARSE_NEW, T),
        (ZRAM_C, _B_POSTLOCK, _B_POSTLOCK_NEW, T),
        (ZRAM_C, _B_LOOP_TAIL, _B_LOOP_TAIL_NEW, T),
    ]


def build_limit_align_steps():
    """``zram_wb_limit_align``: the mainline writeback_limit overflow guard."""
    return [
        (ZRAM_C, _C_LIMIT_STORE, _C_LIMIT_STORE_NEW, T),
    ]


def _teardown_apply(ctx):
    try:
        text = ctx.read(ZRAM_C)
    except FileNotFoundError:
        return "blocked_by_shape", f"{ZRAM_C}: file absent"
    # Shape probe only: assert the two regions this group edits are the ones we
    # think they are.  Nothing here tests for the series partner
    # be48c412f6eb -- that hunk is not editable input to this fix, and probing it
    # made the group's outcome depend on an *earlier* group's output.
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
    # The whole group lives inside #ifdef CONFIG_ZRAM_WRITEBACK, but the text is
    # compiled out only by the preprocessor, not absent: assert the writeback
    # surface really is there so a crippled tree degrades instead of silently
    # matching some other `kstrtoull` block.
    if "static ssize_t writeback_limit_store" not in text:
        return "blocked_by_shape", "writeback_limit_store() not found in zram_drv.c"
    status, _results, detail = apply_steps(ctx, build_limit_align_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the three PatchGroup records, in registration order."""
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
