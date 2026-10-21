from __future__ import annotations

from abk_backport_engine import apply_steps

PAGE_IO = "mm/page_io.c"

T = True

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

_WB_DROP_OLD = (
    "\t\tput_page(page);\n"
    "\t\treturn;\n"
    "\t}\n"
)

_WB_DROP_NEW = (
    "\t\t/*\n"
    "\t\t * sailboat_kcompressd_wb_pin: this page was pinned under\n"
    "\t\t * PG_writeback at enqueue, and this drop path returns before the\n"
    "\t\t * drain's end_page_writeback() below.  End it here too, or the page\n"
    "\t\t * is left under writeback forever -- unreclaimable, and freeing it\n"
    "\t\t * with the bit set trips bad_page().  No page lock is needed\n"
    "\t\t * (end_page_writeback() runs from bio completion), and the page is\n"
    "\t\t * still under writeback here because nothing has ended it yet.\n"
    "\t\t */\n"
    "\t\tend_page_writeback(page);\n"
    "\t\tput_page(page);\n"
    "\t\treturn;\n"
    "\t}\n"
)

WB_MARKER = "sailboat_kcompressd_wb_pin"
WB_SET = "set_page_writeback(page);"
WB_END = "end_page_writeback(page);"
WB_DROP_END = "end_page_writeback(page);\n\t\tput_page(page);"
DO_SWAPOUT_PROBE = "abk_kcompressd_do_swapout"


def build_steps():
    return [
        (PAGE_IO, _WB_SET_OLD, _WB_SET_NEW, T),
        (PAGE_IO, _WB_END_OLD, _WB_END_NEW, T),
        (PAGE_IO, _WB_DROP_OLD, _WB_DROP_NEW, T),
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
