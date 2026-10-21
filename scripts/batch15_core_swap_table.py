from __future__ import annotations

REGISTER_AS_CHILD = False

VERDICT = "not_absorbable"

SUITE_GROUP = "swap_table_phase2_large_folios"

SUITE_TARGET_FILES = {
    "mm/swap.h": "absent on 5.15 (introduced with the v5.16 folio split; "
                 "6.1 tree has it)",
    "mm/swap_state.c": "present, but 0 occurrences of 'folio' -- the file is "
                       "entirely struct page",
}

SUITE_ANCHOR_AUDIT = {
    "mm/swap.h: struct page *find_get_incore_page(struct address_space *, pgoff_t);":
        "include/linux/swap.h:466 (same text, different file; declaration only)",
    "mm/swap.h: static inline struct page *swap_cluster_readahead(...) { return NULL; }":
        "include/linux/swap.h:604 (!CONFIG_SWAP stub, same text)",
    "mm/swap_state.c: struct folio *swap_cache_get_folio(swp_entry_t, ...)":
        "no counterpart: 5.15 spells it struct page *lookup_swap_cache(...) "
        "at mm/swap_state.c:334",
    "mm/swap_state.c: struct page *__read_swap_cache_async(swp_entry_t, gfp_t, ...)":
        "exists at mm/swap_state.c:417, but page-shaped and with a different "
        "body (alloc_page_vma/__SetPageLocked/mem_cgroup_swapin_charge_page)",
    "mm/swap_state.c: struct page *read_swap_cache_async(..., bool do_poll)":
        "exists at mm/swap_state.c:521; 5.15 passes bool do_poll, the suite "
        "passes struct swap_iocb **plug",
    "mm/swap_state.c: struct page *swap_cluster_readahead(...)":
        "exists at mm/swap_state.c:613; 5.15 uses SetPageReadahead() + "
        "swap_readpage(page, false), no swap_read_unplug()",
    "mm/swap_state.c: static struct page *swap_vma_readahead(...)":
        "exists at mm/swap_state.c:788",
    "mm/swap_state.c: static inline bool swap_use_vma_readahead(void)":
        "exists at mm/swap_state.c:323 (unchanged)",
}

MISSING_5_15_CALLEES = (
    "struct folio", "folio_test_large", "folio_test_clear_readahead",
    "folio_set_readahead", "filemap_get_folio", "folio_file_page",
    "__folio_set_locked", "__folio_set_swapbacked",
    "mem_cgroup_swapin_charge_folio", "swap_read_folio", "swapin_folio",
    "struct swap_iocb", "swap_read_unplug",
)

PAGE_SHAPED_5_15_COUNTERPARTS = {
    "swap_readpage": "int swap_readpage(struct page *page, bool do_poll) "
                     "(include/linux/swap.h:425)",
    "read_swap_cache_async": "..., bool do_poll (mm/swap_state.c:521)",
    "add_to_swap_cache": "int add_to_swap_cache(struct page *page, swp_entry_t, "
                         "gfp_t, void **shadowp) (mm/swap_state.c:100)",
    "mem_cgroup_swapin_charge_page": "include/linux/memcontrol.h",
    "PageTransCompound": "the 5.15 spelling of folio_test_large() for the "
                         "readahead bail-out (mm/swap_state.c:356)",
}


def build_groups(PatchGroup):
    return []
