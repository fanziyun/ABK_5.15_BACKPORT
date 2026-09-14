"""Batch 15 feasibility record: ``swap_table_phase2_large_folios`` is NOT ported.

**Assessment only.  This file registers no ``PatchGroup`` and must not be wired
as a child.**  ``build_groups()`` returns ``[]`` on purpose: the ported feature
cannot be expressed on android13-5.15, so there is nothing to register, and
wiring this module would add a child that does nothing.  The evidence behind
that verdict is recorded here (and machine-readably in ``SUITE_ANCHOR_AUDIT``)
so it is not re-litigated.

What the suite feature is (``ABK_ABI_PATCH_SUITE/scripts/abk_feature_porting.py``
``patch_swap_table_phase2_large_folios()``, lines 2414-2517; scope recorded by
``collect_swap_table_phase2_large_folios_status()``, lines 3048-3110): move
``mm/swap_state.c`` onto a *folio-first* swapcache and readahead surface --
``swap_update_readahead_info()``, ``__swap_cache_prepare_and_add()``,
``swap_cache_alloc_folio()``, ``swapin_folio()``,
``read_swap_cache_async_folio()``, ``swap_read_folio_compat()`` -- while
keeping page-returning public wrappers (``read_swap_cache_async()``,
``swap_cluster_readahead()``, ``swapin_readahead()``).  Its own status collector
already gates on ``if not swap_h.is_file(): return blocked_by_layout``; on
5.15 that is the *first* thing that happens, but it is not the real blocker.

Verdict: **not absorbable, and not salvageable as a page-first re-spelling.**

1. The target file does not exist.  ``mm/swap.h`` is absent from all four
   reference trees and from the full 5.15.167 tree
   (``ls mm/swap.h`` -> "No such file or directory").  It is a later split: the
   6.1 tree has it (4139 bytes).  On 5.15 the declarations the suite wants to
   extend live in two places instead:
     * ``include/linux/swap.h`` -- ``lookup_swap_cache()`` (line 463),
       ``find_get_incore_page()`` (466), ``read_swap_cache_async()`` (467),
       ``__read_swap_cache_async()`` (470), ``swap_cluster_readahead()`` (473),
       ``swapin_readahead()`` (475), plus the ``!CONFIG_SWAP`` stubs at 604/610;
     * ``mm/internal.h`` and ``mm/swap_state.c`` for the file-local helpers.
   Relocating the two ``mm/swap.h`` steps into ``include/linux/swap.h`` is
   mechanically possible -- the suite's ``swap_cache_get_folio``-shaped
   anchors are *text* that also exists in the page spelling elsewhere -- but it
   buys nothing, because of point 2.

2. The folio abstraction the feature is built on does not exist on 5.15.
   ``struct folio`` was introduced in v5.16.  Measured on the 5.15.167 tree:
   ``grep -rn "struct folio" include/linux mm`` -> **0** hits;
   ``grep -c folio mm/swap_state.c`` -> **0**.  So the suite's core anchor
   ``struct folio *swap_cache_get_folio(swp_entry_t entry, ...)`` has no
   5.15 counterpart at all -- not a different name, an absent type.  The 5.15
   function is ``struct page *lookup_swap_cache(swp_entry_t entry,
   struct vm_area_struct *vma, unsigned long addr)`` (``mm/swap_state.c:334``),
   and what the suite extracts into ``swap_update_readahead_info(folio, vma,
   addr)`` is inline in its body at lines 347-378, spelled with
   ``PageTransCompound()`` / ``TestClearPageReadahead()``.

3. Ten of the twelve helpers the grafted bodies call do not exist on 5.15.
   ``grep -rn <sym> include mm kernel`` on 5.15.167:

     symbol                                          hits
     ----------------------------------------------  ----
     folio_test_large                                0
     folio_test_clear_readahead                      0
     folio_set_readahead                             0
     filemap_get_folio                               0
     folio_file_page                                 0
     __folio_set_locked                              0
     __folio_set_swapbacked                          0
     mem_cgroup_swapin_charge_folio                  0
     swap_read_folio                                 0
     swapin_folio                                    0
     struct swap_iocb                                0
     swap_read_unplug                                0

   (``swapcache_prepare`` (``include/linux/swap.h:503``) and
   ``add_to_swap_cache`` do exist, but page-shaped: ``int
   add_to_swap_cache(struct page *page, swp_entry_t entry, gfp_t gfp,
   void **shadowp)`` at ``mm/swap_state.c:100``, and the 5.15 caller spells
   ``__SetPageLocked()`` / ``__SetPageSwapBacked()`` /
   ``mem_cgroup_swapin_charge_page()`` -- all four names differ from the 6.1
   body the suite inserts.)

4. The read path's calling convention differs, so even the "page surface
   retained" half does not transcribe.  5.15: ``extern int
   swap_readpage(struct page *page, bool do_poll);``
   (``include/linux/swap.h:425``), ``read_swap_cache_async(swp_entry_t, gfp_t,
   struct vm_area_struct *, unsigned long, bool do_poll)``
   (``mm/swap_state.c:521``), and ``swap_cluster_readahead()`` carries its own
   ``bool do_poll`` plus a ``blk_plug`` and ``swap_read_unplug()``-free
   ``swap_readpage(page, false)`` (lines 613-662).  The suite's replacement
   calls ``swap_readpage(&folio->page, false, plug)`` (three arguments) and
   ``swap_read_unplug(splug)``, and its public wrapper passes
   ``struct swap_iocb **plug`` where 5.15 passes ``bool do_poll``.  Copying the
   6.1 call shape does not compile; adapting it back to ``do_poll`` deletes the
   plugging half of the feature.

5. A page-first re-spelling would be pure code motion with no payload.  The
   *only* thing that could be transplanted is the extraction of the readahead
   accounting out of ``lookup_swap_cache()`` and of the
   prepare/``add_to_swap_cache()`` tail out of ``__read_swap_cache_async()``
   into two static helpers, keeping ``struct page *`` throughout.  That is
   behaviour-identical by construction (the same statements, moved), so it is
   not this feature: the feature *is* the folio surface, which is the enabling
   step for large folios in the swap cache.  Shipping the re-spelling would add
   diff surface on the swap-in hot path for zero behaviour change, which this
   module's "features/optimizations/refactors with a measurable payload" rule
   rules out.

Consequence: no group, no per-tree anchor table, no marker.  ``mm/swap_state.c``
and ``include/linux/swap.h`` are byte-identical across all four reference trees
(md5 ``53f4fbd8766c`` and ``e7a6993d9f03`` respectively), so there is not even a
sublevel split to exploit -- the whole 5.15.x line is folio-free here.  Revisit
only if a future baseline adopts the v5.16+ folio conversion, at which point the
suite's own anchors become the starting point again.

What *was* left in place: the page-flag/``do_poll`` readahead surface is
untouched, so the ``swap_readahead``-visible behaviour of this tree is exactly
what android13-5.15 ships.  Nothing in this file edits ``mm/``.
"""

from __future__ import annotations

# The module-set contract expects ``build_groups``; this file deliberately
# contributes nothing to the registry.  ``REGISTER_AS_CHILD = False`` is a
# machine-readable reminder for whoever wires Batch 15.
REGISTER_AS_CHILD = False

VERDICT = "not_absorbable"

SUITE_GROUP = "swap_table_phase2_large_folios"

#: The suite's two target files, and what 5.15 has instead.
SUITE_TARGET_FILES = {
    "mm/swap.h": "absent on 5.15 (introduced with the v5.16 folio split; "
                 "6.1 tree has it)",
    "mm/swap_state.c": "present, but 0 occurrences of 'folio' -- the file is "
                       "entirely struct page",
}

#: suite anchor -> how the 5.15 tree spells it (or why it has no counterpart).
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

#: helpers the grafted bodies call that are absent from the 5.15 tree
#: (grep -rn <sym> include mm kernel == 0).
MISSING_5_15_CALLEES = (
    "struct folio", "folio_test_large", "folio_test_clear_readahead",
    "folio_set_readahead", "filemap_get_folio", "folio_file_page",
    "__folio_set_locked", "__folio_set_swapbacked",
    "mem_cgroup_swapin_charge_folio", "swap_read_folio", "swapin_folio",
    "struct swap_iocb", "swap_read_unplug",
)

#: page-shaped counterparts the graft would have to be rewritten onto.
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
    """Return no group: ``swap_table_phase2_large_folios`` does not anchor.

    Returning ``[]`` is the honest answer rather than a degraded group.  A
    group here would have to be spelled page-first, which is behaviour-neutral
    code motion (see point 5 of the module docstring), or folio-first, which
    cannot compile on a tree with no ``struct folio``.
    """
    return []
