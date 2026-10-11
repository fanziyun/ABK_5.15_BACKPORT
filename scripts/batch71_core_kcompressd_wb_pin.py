"""Batch 71: kcompressd pins the swap slot with PG_writeback across the queue.

Field evidence: device A/B/A on SM8550 vermeer (5.15.220).  With kcompressd's
swap-out offload on, a sustained memory-thrash load corrupts swapped page data
-- a page-fingerprint harness (verify_swap) saw 10 corrupt pages of 614400
across three verifiers; with the offload off (``/proc/sys/vm/kcompressd=0``)
the identical load gave 0/614400.  The corruption surfaces up the stack as
ART heap/dex SIGSEGV in random apps (bilibili, system_server, camera) and, when
a reused page frame or slot carries file data to f2fs, as /data corruption.

Root cause (confirmed against mm/swapfile.c + mm/page_io.c on the device tree):
Batch 41 defers swap-out to a per-node FIFO.  Its offload branch in
swap_writepage() does ``unlock_page(page); return 0;`` -- the page leaves the
reclaimer UNLOCKED and NOT under writeback, pinned only by the enqueue
``get_page()``.  For the whole FIFO residency the swap slot is therefore
unprotected: ``try_to_free_swap()`` frees a slot only when the page is NOT under
writeback (``if (PageWriteback(page)) return 0;`` is its second guard), so a
swap-in fault during residency frees the slot, it is reused for another page,
and kcompressd's deferred write then lands in a slot that now belongs to a
different page -- silent cross-page / cross-file data corruption.  Batch 41's
only guard was ``PageSwapCache``, which the author documented as a believed-
unreachable risk; thrash makes it reachable.

What this group does (second-pass rewrite of Batch 41's generated text, trap 5):
restore the normal asynchronous-writeback contract that the deferral broke.

  1. Offload branch (enqueue, page still locked): ``set_page_writeback(page)``
     before ``unlock_page()``.  PG_writeback now pins the slot for the whole
     residency -- ``try_to_free_swap()`` bails, and shrink_page_list() keeps the
     page (its PAGE_SUCCESS path does ``if (PageWriteback) goto keep``) until the
     drain completes, exactly as a normal in-flight swap bio behaves.
  2. Drain head of abk_kcompressd_do_swapout() (page arrives unlocked + under
     writeback): ``lock_page(page)`` then ``end_page_writeback(page)``, returning
     the page to the locked / not-writeback state the write body expects.  The
     body (frontswap_store, or __swap_writepage -> bdev_write_page for zram)
     re-sets and ends writeback itself, self-balanced.  The lock covers the
     end_page_writeback -> body gap: try_to_free_swap() needs the page lock,
     which the drain holds.

Why lock_page (not trylock-skip): a page under writeback MUST be drained --
end_page_writeback has to run or the page is stuck under writeback forever and
can never be reclaimed again.  The page is unlocked for the whole residency, so
the drain's lock is brief (it waits only for a transient swap-in faulter that
maps the up-to-date page and releases), not the stall the earlier trylock draft
targeted.

Scope notes:

* Second-pass group over Batch 41's generated text: registered after
  ``vm_kcompressd_swapout`` and refuses (``blocked_by_shape``) when that group's
  engine is absent, so an unknown tree is never half-patched.  The ``sailboat_``
  marker rides in the new comments.
* Both edits sit above the ``@@ABK_KCOMPRESS_SWAPOUT_BODY@@`` join point / in
  swap_writepage()'s offload branch, byte-identical in the hook and plain
  engine variants, so one pair of anchors covers both.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

PAGE_IO = "mm/page_io.c"

T = True

# 1) Enqueue: set_page_writeback() in swap_writepage()'s offload branch, before
# the page is unlocked.  Anchored on the tail of Batch 41's offload comment,
# which is unique in the file.
_WB_SET_OLD = (
    "\t\t * same defect on 6.12, whose pageout() has the identical\n"
    "\t\t * PAGE_SUCCESS branch. */\n"
    "\t\tunlock_page(page);\n"
    "\t\treturn 0;\n"
    "\t}\n"
)

_WB_SET_NEW = (
    "\t\t * same defect on 6.12, whose pageout() has the identical\n"
    "\t\t * PAGE_SUCCESS branch. */\n"
    "\t\t/*\n"
    "\t\t * sailboat_kcompressd_wb_pin: mark the page under writeback\n"
    "\t\t * before releasing it, so its swap slot stays pinned for the\n"
    "\t\t * whole FIFO residency.  Without this the queued page is\n"
    "\t\t * unlocked and not-writeback, so try_to_free_swap() (which\n"
    "\t\t * bails only on PageWriteback) frees the slot, it is reused\n"
    "\t\t * for another page, and the deferred write lands in the wrong\n"
    "\t\t * slot -- silent swap data corruption (anon pages crash apps,\n"
    "\t\t * file pages = /data loss).  end_page_writeback() is paired in\n"
    "\t\t * abk_kcompressd_do_swapout() once the drain holds the lock.\n"
    "\t\t */\n"
    "\t\tset_page_writeback(page);\n"
    "\t\tunlock_page(page);\n"
    "\t\treturn 0;\n"
    "\t}\n"
)

# 2) Drain: end_page_writeback() right after the drain re-acquires the lock,
# balancing the enqueue set and returning the page to the locked /
# not-writeback state the write body expects.  The anchor spans the lock line
# through the head of the write body (frontswap_store, byte-identical in both
# engine variants) so the replacement is not a pure prefix-extension of its
# anchor (trap 1) and lands the end between the lock and the body.
_WB_END_OLD = (
    "\t * freed underneath us no matter who else locked it.\n"
    "\t */\n"
    "\tlock_page(page);\n"
    "\n"
    "\n"
    "\tif (frontswap_store(page) == 0) {\n"
)

_WB_END_NEW = (
    "\t * freed underneath us no matter who else locked it.\n"
    "\t */\n"
    "\tlock_page(page);\n"
    "\t/*\n"
    "\t * sailboat_kcompressd_wb_pin: the page arrived unlocked and under\n"
    "\t * writeback (set at enqueue to pin the swap slot across the queue).\n"
    "\t * Now that the drain holds the lock, end that writeback so the write\n"
    "\t * body below runs from the same locked / not-writeback state the\n"
    "\t * synchronous path expects; the body re-sets and ends writeback\n"
    "\t * itself (bdev_write_page for zram).  The lock covers this gap:\n"
    "\t * try_to_free_swap() needs the page lock, which we hold.\n"
    "\t */\n"
    "\tend_page_writeback(page);\n"
    "\n"
    "\n"
    "\tif (frontswap_store(page) == 0) {\n"
)

# Pins for the audits and the unit test.
WB_MARKER = "sailboat_kcompressd_wb_pin"
WB_SET = "set_page_writeback(page);"
WB_END = "end_page_writeback(page);"
DO_SWAPOUT_PROBE = "abk_kcompressd_do_swapout"


def build_steps():
    """Two required steps: set writeback at enqueue, end it at drain."""
    return [
        (PAGE_IO, _WB_SET_OLD, _WB_SET_NEW, T),
        (PAGE_IO, _WB_END_OLD, _WB_END_NEW, T),
    ]


def _kcompressd_wb_pin_apply(ctx):
    try:
        text = ctx.read(PAGE_IO)
    except FileNotFoundError:
        return "blocked_by_shape", PAGE_IO + ": file absent"
    if WB_MARKER in text:
        return "already_present", "the kcompressd writeback pin is already applied"
    if DO_SWAPOUT_PROBE not in text:
        return "blocked_by_shape", ("abk_kcompressd_do_swapout() not found: "
                                     "vm_kcompressd_swapout must apply first")
    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the single Batch-71 kcompressd writeback-pin PatchGroup record."""
    return [
        PatchGroup(
            "kcompressd_writeback_pin",
            "abk_kcompressd_do_swapout()'s deferred swap-out keeps the page "
            "under PG_writeback across the FIFO (set at enqueue, ended once the "
            "drain holds the lock), so try_to_free_swap() cannot free and reuse "
            "the swap slot mid-queue -- closing the silent swap data corruption "
            "that crashed apps (ART SIGSEGV) and corrupted /data under thrash",
            [
                "device A/B verify_swap: kcompressd on = 10/614400 corrupt "
                "pages, off = 0/614400 (SM8550 vermeer 5.15.220)",
            ],
            [PAGE_IO],
            _kcompressd_wb_pin_apply,
        ),
    ]
