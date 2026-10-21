from __future__ import annotations

from abk_backport_engine import apply_steps

VMSAN = "mm/vmscan.c"
PAGE_IO = "mm/page_io.c"

T = True

_DECL_OLD = (
    "\tLIST_HEAD(ret_pages);\n"
    "\tLIST_HEAD(free_pages);\n"
    "\tLIST_HEAD(demote_pages);\n"
)

_DECL_NEW = (
    """	LIST_HEAD(ret_pages);
	LIST_HEAD(free_pages);
	LIST_HEAD(demote_pages);
	/* sailboat_kcompressd_swapcache_drop */
	LIST_HEAD(wb_retry_pages);
	bool wb_retry_done = false;
"""
)

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
    """			case PAGE_SUCCESS:
				stat->nr_pageout += thp_nr_pages(page);

				if (PageWriteback(page)) {
					/* sailboat_kcompressd_swapcache_drop */
#ifdef CONFIG_SWAP
					if (!wb_retry_done && PageSwapCache(page) &&
					    (page_swap_info(page)->flags &
					     SWP_SYNCHRONOUS_IO)) {
						list_add(&page->lru, &wb_retry_pages);
						continue;
					}
#endif
					goto keep;
				}
				if (PageDirty(page))
					goto keep;
"""
)

_RETRY_OLD = (
    "\t/* 'page_list' is always empty here */\n"
    "\n"
    "\t/* Migrate pages selected for demotion */\n"
)

_RETRY_NEW = (
    """	/* 'page_list' is always empty here */

	/* sailboat_kcompressd_swapcache_drop */
	if (!wb_retry_done && !list_empty(&wb_retry_pages)) {
		struct page *page, *next;
		bool drain_ctx = (current->flags & PF_KSWAPD) &&
				 !(current->flags & PF_SWAPWRITE);

		wb_retry_done = true;
		list_for_each_entry_safe(page, next, &wb_retry_pages, lru) {
			if (!PageWriteback(page))
				continue;
			if (!drain_ctx) {
				wait_on_page_writeback(page);
				if (!PageWriteback(page))
					continue;
			}
			list_move(&page->lru, &ret_pages);
		}
		if (!list_empty(&wb_retry_pages)) {
			list_splice_tail(&wb_retry_pages, page_list);
			do_demote_pass = false;
			goto retry;
		}
	}

	/* Migrate pages selected for demotion */
"""
)

MARKER = "sailboat_kcompressd_swapcache_drop"
WB_PIN_MARKER = "sailboat_kcompressd_wb_pin"
DECL_LIST = "LIST_HEAD(wb_retry_pages);"
DECL_FLAG = "bool wb_retry_done = false;"
PARK = "list_add(&page->lru, &wb_retry_pages);"
WAIT_PIN = "wait_on_page_writeback(page);"
DRAIN_GUARD = "PF_KSWAPD"
SPLICE = "list_splice_tail(&wb_retry_pages, page_list);"
RETRY_LATCH = "\t\twb_retry_done = true;"
SWAP_GATE = "#ifdef CONFIG_SWAP"
PAGE_SUCCESS_BLOCK = (
    "\t\t\tcase PAGE_SUCCESS:\n"
    "\t\t\t\tstat->nr_pageout += thp_nr_pages(page);\n"
)


def build_steps():
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
