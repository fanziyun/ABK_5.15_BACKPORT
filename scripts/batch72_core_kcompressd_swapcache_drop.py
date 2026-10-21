"""Batch 72: drop a kcompressd swap-cache page in the same reclaim pass.

Follow-up to Batch 71 (``kcompressd_writeback_pin``).  Batch 71 restores the
asynchronous-writeback contract the deferred swap-out broke: the queued page
carries PG_writeback for its whole FIFO residency, so ``try_to_free_swap()``
bails and the swap slot cannot be freed and reused underneath the drain.  That
closes the silent swap data corruption, and it also changes what reclaim sees:
a page whose slot is queued for the drain now arrives at
``shrink_page_list()``'s ``case PAGE_SUCCESS:`` still under writeback.

``pageout()`` has already run ``clear_page_dirty_for_io()`` and
``try_to_unmap()`` by then, so the page is clean, unmapped and owned only by
the swap cache -- its writeback is the *queue residency*, not a bio in flight.
The pristine ``if (PageWriteback(page)) goto keep;`` therefore puts it back on
the LRU with the swap cache entry still allocated, and the entry is only
dropped on some later scan, after the drain has ended the writeback.  Under
sustained thrash that keeps ``SwapCached`` (and the per-slot memory behind it)
inflated even though every page involved is unreferenced and one drain away
from reclaimable.

What this group does: give such a page one bounded re-scan inside the same
``shrink_page_list()`` call, so ``__remove_mapping()`` frees its swap cache
entry now instead of on a later scan.  Three steps, all in ``mm/vmscan.c``:

  1. Declarations next to the existing ``ret_pages`` / ``free_pages`` /
     ``demote_pages`` lists: ``wb_retry_pages`` (the parked pages, one page
     each) and ``wb_retry_done`` (the one-shot latch).
  2. ``case PAGE_SUCCESS:`` -- when the page is under writeback *and* is in
     the swap cache *and* that swap device is synchronous
     (``SWP_SYNCHRONOUS_IO``: zram, the deferred path) *and* the re-scan has
     not run yet, park the page on ``wb_retry_pages`` and ``continue``;
     otherwise keep the pristine ``goto keep;``.  The ``PageDirty`` guard
     stays where it was, after this block.
  3. After the loop's ``keep:`` block and ``/* 'page_list' is always empty
     here */``: wait for the deferred write to finish (the drain is one
     per-node thread and the FIFO is small, so the wait is bounded), then
     ``list_splice_tail()`` the parked pages onto ``page_list``, clear
     ``do_demote_pass`` (a demoted page must not be re-routed), and
     ``goto retry;``.  ``wb_retry_done`` makes the wait one-shot, so the loop
     always terminates.

Why ``SWP_SYNCHRONOUS_IO`` only: the deferred-drain shape this targets is a
synchronous swap device.  On an asynchronous swap device the page's writeback
is a real bio whose completion is not bounded by the reclaim path, so the
pristine "keep and revisit later" is the right answer and is left untouched.

Scope notes:

* Removal still goes through the kernel's own ``__remove_mapping()``; this
  group waits for the deferred write to finish and then lets the normal
  reclaim tail remove the page.  The wait applies only to pages this module
  queued, so its bound is the drain's FIFO rather than an arbitrary bio.
* ``page_swap_info()`` exists only under ``CONFIG_SWAP``, so its one reference
  sits in a ``CONFIG_SWAP`` gate with the pristine ``goto keep;`` as the
  ``#else`` behaviour (AGENTS.md "Step-authoring traps" #7).  Everything else
  in the group is swap-flag-free and stays compilable with the gate off.
* ``list_splice_tail()`` does not reinitialize its source, so after the retry
  splice ``wb_retry_pages`` still points into the entries it just handed to
  ``page_list``.  That list is dead from then on and the one-shot
  ``wb_retry_done`` latch is what guarantees nothing walks it again.  Keep the
  invariant if the latch is ever reworked: reset the list, or switch to
  ``list_splice_tail_init()``.  Stated here rather than in the generated C, so
  the grafted text (and the audits that pin its marker count) stay untouched.
* Ordering: registered after ``vm_kcompressd_swapout`` (Batch 41) and
  ``kcompressd_writeback_pin`` (Batch 71).  The step-2 shape the group edits
  is pristine text, but the state it acts on (a swap-cache page under
  writeback at ``PAGE_SUCCESS``) is what Batch 71 creates, so the group
  refuses with ``blocked_by_shape`` when Batch 71's graft (its
  ``sailboat_kcompressd_wb_pin`` marker in ``mm/page_io.c``) is absent.
  Without that pin the page is never in the state this wait targets.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

VMSAN = "mm/vmscan.c"
PAGE_IO = "mm/page_io.c"

T = True

# 1) Declarations: the parked-page list and the one-shot latch, next to the
# lists shrink_page_list() already keeps.  The anchor is the three-LIST_HEAD
# prologue, which is unique in the file.
_DECL_OLD = (
    "\tLIST_HEAD(ret_pages);\n"
    "\tLIST_HEAD(free_pages);\n"
    "\tLIST_HEAD(demote_pages);\n"
)

_DECL_NEW = (
    "\tLIST_HEAD(ret_pages);\n"
    "\tLIST_HEAD(free_pages);\n"
    "\tLIST_HEAD(demote_pages);\n"
    "\t/*\n"
    "\t * sailboat_kcompressd_swapcache_drop: pages left in the swap cache\n"
    "\t * by a synchronous swap-out that was still under writeback when\n"
    "\t * PAGE_SUCCESS was read.  They are re-scanned once below, after the\n"
    "\t * writeback ends, so the swap cache entry is dropped in this pass\n"
    "\t * instead of parking the page on the LRU with its slot still held.\n"
    "\t */\n"
    "\tLIST_HEAD(wb_retry_pages);\n"
    "\tbool wb_retry_done = false;\n"
)

# 2) PAGE_SUCCESS: park the page instead of keeping it, when it is a
# synchronous swap-cache page that is still under writeback.  The anchor spans
# the case label through the PageDirty guard, so the replacement cannot be a
# prefix-extension of its own anchor (trap 1) and the PageDirty guard provably
# stays after this block.
_PARK_OLD = (
    "\t\t\tcase PAGE_SUCCESS:\n"
    "\t\t\t\tstat->nr_pageout += thp_nr_pages(page);\n"
    "\n"
    "\t\t\t\tif (PageWriteback(page))\n"
    "\t\t\t\t\tgoto keep;\n"
    "\t\t\t\tif (PageDirty(page))\n"
    "\t\t\t\t\tgoto keep;\n"
)

_PARK_NEW = (
    "\t\t\tcase PAGE_SUCCESS:\n"
    "\t\t\t\tstat->nr_pageout += thp_nr_pages(page);\n"
    "\n"
    "\t\t\t\tif (PageWriteback(page)) {\n"
    "\t\t\t\t\t/*\n"
    "\t\t\t\t\t * sailboat_kcompressd_swapcache_drop: a deferred\n"
    "\t\t\t\t\t * swap-out (or any synchronous swap device) returns\n"
    "\t\t\t\t\t * PAGE_SUCCESS with PG_writeback still set, while\n"
    "\t\t\t\t\t * pageout() has already cleared the dirty bit and\n"
    "\t\t\t\t\t * unmapped the page.  The writeback is the queue\n"
    "\t\t\t\t\t * residency, not a bio in flight, so the page is\n"
    "\t\t\t\t\t * reclaimable as soon as the drain ends it -- park\n"
    "\t\t\t\t\t * it and re-scan once below rather than keeping the\n"
    "\t\t\t\t\t * swap cache entry alive until a later scan.\n"
    "\t\t\t\t\t */\n"
    "#ifdef CONFIG_SWAP\n"
    "\t\t\t\t\tif (!wb_retry_done && PageSwapCache(page) &&\n"
    "\t\t\t\t\t    (page_swap_info(page)->flags &\n"
    "\t\t\t\t\t     SWP_SYNCHRONOUS_IO)) {\n"
    "\t\t\t\t\t\tlist_add(&page->lru, &wb_retry_pages);\n"
    "\t\t\t\t\t\tcontinue;\n"
    "\t\t\t\t\t}\n"
    "#endif\n"
    "\t\t\t\t\tgoto keep;\n"
    "\t\t\t\t}\n"
    "\t\t\t\tif (PageDirty(page))\n"
    "\t\t\t\t\tgoto keep;\n"
)

# 3) One re-scan, after the loop drained page_list.  The anchor is the
# "page_list is always empty here" comment plus the demotion pass that
# follows; inserting between them keeps the working-set/demotion order intact
# (the demotion retry runs after this one, on a page_list this pass leaves
# empty again).
_RETRY_OLD = (
    "\t/* 'page_list' is always empty here */\n"
    "\n"
    "\t/* Migrate pages selected for demotion */\n"
)

_RETRY_NEW = (
    "\t/* 'page_list' is always empty here */\n"
    "\n"
    "\t/*\n"
    "\t * sailboat_kcompressd_swapcache_drop: wait for the deferred write\n"
    "\t * to finish, then re-scan the pages parked above.  The wait applies\n"
    "\t * only to pages this module queued, so its bound is the drain's FIFO\n"
    "\t * rather than an arbitrary device.  It is skipped when this reclaim\n"
    "\t * runs inside the drain itself (PF_KSWAPD without PF_SWAPWRITE):\n"
    "\t * waiting there would wait on our own FIFO, and those pages fall\n"
    "\t * back to @ret_pages exactly as before.  After the writeback ends\n"
    "\t * the page is clean, unmapped and owned only by the swap cache, so\n"
    "\t * the normal reclaim tail frees both the page and its entry in this\n"
    "\t * pass.  Bounded: taken at most once per shrink_page_list() call\n"
    "\t * (wb_retry_done).\n"
    "\t */\n"
    "\tif (!wb_retry_done && !list_empty(&wb_retry_pages)) {\n"
    "\t\tstruct page *page, *next;\n"
    "\t\tbool drain_ctx = (current->flags & PF_KSWAPD) &&\n"
    "\t\t\t\t !(current->flags & PF_SWAPWRITE);\n"
    "\n"
    "\t\twb_retry_done = true;\n"
    "\t\tlist_for_each_entry_safe(page, next, &wb_retry_pages, lru) {\n"
    "\t\t\tif (!PageWriteback(page))\n"
    "\t\t\t\tcontinue;\n"
    "\t\t\tif (!drain_ctx) {\n"
    "\t\t\t\twait_on_page_writeback(page);\n"
    "\t\t\t\tif (!PageWriteback(page))\n"
    "\t\t\t\t\tcontinue;\n"
    "\t\t\t}\n"
    "\t\t\tlist_move(&page->lru, &ret_pages);\n"
    "\t\t}\n"
    "\t\tif (!list_empty(&wb_retry_pages)) {\n"
    "\t\t\tlist_splice_tail(&wb_retry_pages, page_list);\n"
    "\t\t\tdo_demote_pass = false;\n"
    "\t\t\tgoto retry;\n"
    "\t\t}\n"
    "\t}\n"
    "\n"
    "\t/* Migrate pages selected for demotion */\n"
)

# Pins for the audits and the unit test.  The marker is the group's
# idempotency anchor: it must be part of the match, otherwise a tree whose
# comment was re-wrapped is skipped as already_present while the graft is not
# findable (AGENTS.md idempotency rule).
MARKER = "sailboat_kcompressd_swapcache_drop"
# Batch 71's marker, which lives in mm/page_io.c (its payload is the deferred
# swap_writepage() path).  This group's precondition, hence a second-file
# probe rather than a marker in mm/vmscan.c.
WB_PIN_MARKER = "sailboat_kcompressd_wb_pin"
DECL_LIST = "LIST_HEAD(wb_retry_pages);"
DECL_FLAG = "bool wb_retry_done = false;"
PARK = "list_add(&page->lru, &wb_retry_pages);"
WAIT_PIN = "wait_on_page_writeback(page);"
DRAIN_GUARD = "PF_KSWAPD"
# list_splice_tail() leaves its source list pointing into the entries it just
# spliced; nothing reads wb_retry_pages after this call and the wb_retry_done
# latch is what keeps it that way (docstring "Scope notes").
SPLICE = "list_splice_tail(&wb_retry_pages, page_list);"
RETRY_LATCH = "\t\twb_retry_done = true;"
SWAP_GATE = "#ifdef CONFIG_SWAP"
# The pristine PAGE_SUCCESS shape this group rewrites; also its shape probe.
PAGE_SUCCESS_BLOCK = (
    "\t\t\tcase PAGE_SUCCESS:\n"
    "\t\t\t\tstat->nr_pageout += thp_nr_pages(page);\n"
)


def build_steps():
    """Three required steps: the declarations, the PAGE_SUCCESS parking, and
    the one-shot re-scan."""
    return [
        (VMSAN, _DECL_OLD, _DECL_NEW, T),
        (VMSAN, _PARK_OLD, _PARK_NEW, T),
        (VMSAN, _RETRY_OLD, _RETRY_NEW, T),
    ]


def _kcompressd_swapcache_drop_apply(ctx):
    try:
        text = ctx.read(VMSAN)
    except FileNotFoundError:
        return "blocked_by_shape", VMSAN + ": file absent"
    if MARKER in text:
        return "already_present", (
            "the kcompressd swap-cache drop is already applied")
    if PAGE_SUCCESS_BLOCK not in text:
        return "blocked_by_shape", (
            VMSAN + ": the PAGE_SUCCESS block is not in the expected 5.15 "
            "shape; refusing rather than patching an unknown reclaim loop")
    # The state this group acts on (a swap-cache page under writeback at
    # PAGE_SUCCESS) is created by Batch 71's PG_writeback pin.  Probe for that
    # graft in the file that owns it, so a tree without the pin degrades
    # instead of growing dead bookkeeping.
    try:
        io_text = ctx.read(PAGE_IO)
    except FileNotFoundError:
        return "blocked_by_shape", PAGE_IO + ": file absent"
    if WB_PIN_MARKER not in io_text:
        return "blocked_by_shape", (
            PAGE_IO + ": " + WB_PIN_MARKER + " not found; "
            "kcompressd_writeback_pin must apply first")
    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the single Batch-72 swap-cache drop PatchGroup record."""
    return [
        PatchGroup(
            "kcompressd_swapcache_drop",
            "a swap-cache page whose synchronous swap-out is still under "
            "writeback at PAGE_SUCCESS is re-scanned once inside the same "
            "shrink_page_list() call, so __remove_mapping() frees its swap "
            "cache entry now instead of on a later scan -- bound to one "
            "one bounded wait for the deferred write, then one re-scan; no lock, no allocation",
            [
                "follow-up to Batch 71 kcompressd_writeback_pin: the pin "
                "keeps the page (and its slot) alive for the FIFO residency, "
                "which makes reclaim keep the swap cache entry allocated for "
                "the whole queue; this group drops it one drain later",
            ],
            [VMSAN],
            _kcompressd_swapcache_drop_apply,
        ),
    ]
