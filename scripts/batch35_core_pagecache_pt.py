"""Batch 35: page-cache shadow-entry sweeps, and the MADV_DONTNEED page-table
path.

Four upstream commits, registered as four groups in two ordered pairs:

  truncate_shadow_batch           61c663e020d2 (v6.11) -- clear the shadow
                                  entries of a whole pagevec under one
                                  acquisition of the i_pages lock, instead of
                                  one lock/unlock/tree-walk per entry.
  truncate_shadow_batch_sweep     d3db2c042591 (v6.13) -- refactor that helper
                                  again: one xas_for_each() over the batch's
                                  index span replaces the per-entry
                                  __clear_shadow_entry() calls.
  madvise_pt_reclaim              6375e95f381e (v6.14) -- hand an empty PTE
                                  page back to the buddy allocator in
                                  madvise(MADV_DONTNEED), via a new
                                  zap_details.reclaim_pt mark.
  madvise_batch_tlb_flush         43c4cfde7e37 (v6.16) -- gather the TLB
                                  flushes of one madvise() call in a single
                                  mmu_gather instead of one per VMA.

The pairs are registered in dependency order and the later group of each pair
rewrites text the earlier one wrote, so both earlier groups carry a shape probe
on their own payload (the trap-5 rule in AGENTS.md): on the second pass they
report ``already_present`` instead of re-deriving their anchors, which the
later group has already consumed.

Three commits of the same upstream area are deliberately *not* ported, because
android13-5.15 has no carrier for any of them (verified by grep against all
four reference trees -- the evidence is in plan.md's exclusion record):

  7a1eb89f7918  readahead: don't shorten readahead window in read_pages()
  d5ea5e5e50df  readahead: properly shorten readahead when falling back to
                do_page_cache_ra()
  0faa77afe72b  filemap: optimize folio refcount update in filemap_map_pages()

On 5.15 ``read_pages()`` never touches ``ra->size`` (the shortening the first
commit removes was introduced in the 5.18 readahead rework), there is no
``page_cache_ra_order()`` at all (the function the second one repairs), and
``filemap_map_pages()`` is still the single-page ``head``/``first_map_page()``
form with no ``filemap_map_folio_range()``/``filemap_map_order0_folio()`` --
where a page's reference is transferred to the PTE rather than taken and given
back, so there is no double update to optimise.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

TRUNCATE = "mm/truncate.c"
INTERNAL_H = "mm/internal.h"
MEMORY = "mm/memory.c"
MM_H = "include/linux/mm.h"
MADVISE = "mm/madvise.c"

T = True

# ---------------------------------------------------------------------------
# 61c663e020d2 -- batch-clear shadow entries.
# ---------------------------------------------------------------------------

_BATCH_FN_OLD = (
    "static void clear_shadow_entry(struct address_space *mapping, pgoff_t index,\n"
    "\t\t\t       void *entry)\n"
    "{\n"
    "\txa_lock_irq(&mapping->i_pages);\n"
    "\t__clear_shadow_entry(mapping, index, entry);\n"
    "\txa_unlock_irq(&mapping->i_pages);\n"
    "}\n"
)

_BATCH_FN_NEW = (
    "/*\n"
    " * ABK stable_515_backport: 61c663e020d2.  Clear the shadow entries of a\n"
    " * whole pagevec under one acquisition of the i_pages lock instead of one\n"
    " * per entry.  invalidate_mapping_pages() used to take the lock, walk the\n"
    " * tree and drop it again for every shadow entry, so a large enough range\n"
    " * -- invalidate_bdev() on a big file -- could keep one CPU in there long\n"
    " * enough to trip the soft-lockup detector (upstream's report: 11s inside\n"
    " * clear_shadow_entry()).  5.15 keeps __clear_shadow_entry() as the\n"
    " * per-entry worker; the two invalidate_exceptional_entry*() wrappers that\n"
    " * used to drive it one entry at a time are deleted below, because the\n"
    " * shmem/DAX guards they carried now sit here.\n"
    " */\n"
    "static void clear_shadow_entries(struct address_space *mapping,\n"
    "\t\t\t\t struct pagevec *pvec, pgoff_t *indices)\n"
    "{\n"
    "\tint i;\n"
    "\n"
    "\t/* Handled by shmem itself, or for DAX we do nothing. */\n"
    "\tif (shmem_mapping(mapping) || dax_mapping(mapping))\n"
    "\t\treturn;\n"
    "\n"
    "\txa_lock_irq(&mapping->i_pages);\n"
    "\n"
    "\tfor (i = 0; i < pagevec_count(pvec); i++) {\n"
    "\t\tstruct page *page = pvec->pages[i];\n"
    "\n"
    "\t\tif (xa_is_value(page))\n"
    "\t\t\t__clear_shadow_entry(mapping, indices[i], page);\n"
    "\t}\n"
    "\n"
    "\txa_unlock_irq(&mapping->i_pages);\n"
    "}\n"
)

# The two per-entry wrappers go away with it.  A plain deletion cannot be a
# step (an empty ``new`` block is in every file), so the region is replaced by
# this note.
_WRAPPERS_OLD = (
    "/*\n"
    " * Invalidate exceptional entry if easily possible. This handles exceptional\n"
    " * entries for invalidate_inode_pages().\n"
    " */\n"
    "static int invalidate_exceptional_entry(struct address_space *mapping,\n"
    "\t\t\t\t\tpgoff_t index, void *entry)\n"
    "{\n"
    "\t/* Handled by shmem itself, or for DAX we do nothing. */\n"
    "\tif (shmem_mapping(mapping) || dax_mapping(mapping))\n"
    "\t\treturn 1;\n"
    "\tclear_shadow_entry(mapping, index, entry);\n"
    "\treturn 1;\n"
    "}\n"
    "\n"
    "/*\n"
    " * Invalidate exceptional entry if clean. This handles exceptional entries for\n"
    " * invalidate_inode_pages2() so for DAX it evicts only clean entries.\n"
    " */\n"
    "static int invalidate_exceptional_entry2(struct address_space *mapping,\n"
    "\t\t\t\t\t pgoff_t index, void *entry)\n"
    "{\n"
    "\t/* Handled by shmem itself */\n"
    "\tif (shmem_mapping(mapping))\n"
    "\t\treturn 1;\n"
    "\tif (dax_mapping(mapping))\n"
    "\t\treturn dax_invalidate_mapping_entry_sync(mapping, index);\n"
    "\tclear_shadow_entry(mapping, index, entry);\n"
    "\treturn 1;\n"
    "}\n"
)

_WRAPPERS_NEW = (
    "/*\n"
    " * ABK stable_515_backport: 61c663e020d2.  invalidate_exceptional_entry()\n"
    " * and invalidate_exceptional_entry2() are gone with clear_shadow_entry():\n"
    " * the shmem/DAX guards and the shadow sweep they wrapped now happen once\n"
    " * per pagevec inside clear_shadow_entries(), so nothing calls them.\n"
    " */\n"
)

# __invalidate_mapping_pages(): declarations, the exceptional-entry branch and
# the batch clear that follows the loop.  The declaration step anchors on the
# three lines above the point 5.15.194/.216 insert their
# trace_android_vh_invalidate_mapping_pagevec() probe, so both shapes match.
_BIP_DECL_OLD = (
    "\tunsigned long count = 0;\n"
    "\tint i;\n"
)

_BIP_DECL_NEW = (
    "\tunsigned long count = 0;\n"
    "\tint i;\n"
    "\tbool xa_has_values = false;\n"
)

_BIP_ENTRY_OLD = (
    "\t\t\tif (xa_is_value(page)) {\n"
    "\t\t\t\tcount += invalidate_exceptional_entry(mapping,\n"
    "\t\t\t\t\t\t\t\t      index,\n"
    "\t\t\t\t\t\t\t\t      page);\n"
    "\t\t\t\tcontinue;\n"
    "\t\t\t}\n"
)

_BIP_ENTRY_NEW = (
    "\t\t\tif (xa_is_value(page)) {\n"
    "\t\t\t\txa_has_values = true;\n"
    "\t\t\t\tcount++;\n"
    "\t\t\t\tcontinue;\n"
    "\t\t\t}\n"
)

_BIP_CLEAR_OLD = (
    "\t\t\tcount += ret;\n"
    "\t\t}\n"
    "\t\tpagevec_remove_exceptionals(&pvec);\n"
)

_BIP_CLEAR_NEW = (
    "\t\t\tcount += ret;\n"
    "\t\t}\n"
    "\n"
    "\t\tif (xa_has_values)\n"
    "\t\t\tclear_shadow_entries(mapping, &pvec, indices);\n"
    "\n"
    "\t\tpagevec_remove_exceptionals(&pvec);\n"
)

# invalidate_inode_pages2_range(): same three edits.  The DAX check moves from
# invalidate_exceptional_entry2() to the call site, exactly as upstream did.
_IIP_DECL_OLD = (
    "\tint ret2 = 0;\n"
    "\tint did_range_unmap = 0;\n"
)

_IIP_DECL_NEW = (
    "\tint ret2 = 0;\n"
    "\tint did_range_unmap = 0;\n"
    "\tbool xa_has_values = false;\n"
)

_IIP_ENTRY_OLD = (
    "\t\t\tif (xa_is_value(page)) {\n"
    "\t\t\t\tif (!invalidate_exceptional_entry2(mapping,\n"
    "\t\t\t\t\t\t\t\t   index, page))\n"
    "\t\t\t\t\tret = -EBUSY;\n"
    "\t\t\t\tcontinue;\n"
    "\t\t\t}\n"
)

_IIP_ENTRY_NEW = (
    "\t\t\tif (xa_is_value(page)) {\n"
    "\t\t\t\txa_has_values = true;\n"
    "\t\t\t\tif (dax_mapping(mapping) &&\n"
    "\t\t\t\t    !dax_invalidate_mapping_entry_sync(mapping, index))\n"
    "\t\t\t\t\tret = -EBUSY;\n"
    "\t\t\t\tcontinue;\n"
    "\t\t\t}\n"
)

_IIP_CLEAR_OLD = (
    "\t\t\tunlock_page(page);\n"
    "\t\t}\n"
    "\t\tpagevec_remove_exceptionals(&pvec);\n"
)

_IIP_CLEAR_NEW = (
    "\t\t\tunlock_page(page);\n"
    "\t\t}\n"
    "\n"
    "\t\tif (xa_has_values)\n"
    "\t\t\tclear_shadow_entries(mapping, &pvec, indices);\n"
    "\n"
    "\t\tpagevec_remove_exceptionals(&pvec);\n"
)


def build_shadow_batch_steps():
    return [
        (TRUNCATE, _BATCH_FN_OLD, _BATCH_FN_NEW, T),
        (TRUNCATE, _WRAPPERS_OLD, _WRAPPERS_NEW, T),
        (TRUNCATE, _BIP_DECL_OLD, _BIP_DECL_NEW, T),
        (TRUNCATE, _BIP_ENTRY_OLD, _BIP_ENTRY_NEW, T),
        (TRUNCATE, _BIP_CLEAR_OLD, _BIP_CLEAR_NEW, T),
        (TRUNCATE, _IIP_DECL_OLD, _IIP_DECL_NEW, T),
        (TRUNCATE, _IIP_ENTRY_OLD, _IIP_ENTRY_NEW, T),
        (TRUNCATE, _IIP_CLEAR_OLD, _IIP_CLEAR_NEW, T),
    ]


def _shadow_batch_probe(ctx):
    """Trap 5: the sweep group rewrites the helper this one writes."""
    return "clear_shadow_entries" in ctx.read(TRUNCATE)


# ---------------------------------------------------------------------------
# d3db2c042591 -- one traversal for the whole pagevec instead of one per entry.
# ---------------------------------------------------------------------------

_SWEEP_FN_OLD = _BATCH_FN_NEW

_SWEEP_FN_NEW = (
    "/*\n"
    " * ABK stable_515_backport: d3db2c042591.  Hold the i_pages lock once (as\n"
    " * 61c663e020d2 does) *and* walk the tree once: xas_for_each() over the\n"
    " * pagevec's [start, max] index span replaces one __clear_shadow_entry()\n"
    " * per entry, which each had to load the slot, compare it against the entry\n"
    " * the caller saw and store NULL.  The slots in between are ordinary pages\n"
    " * the caller has already dealt with, and xas_set_update() keeps the\n"
    " * workingset's node accounting.  5.15 shape: a pagevec of struct page, and\n"
    " * the lock taken with xa_lock_irq() rather than xas_lock_irq().\n"
    " *\n"
    " * __clear_shadow_entry() stays: upstream deleted it in this commit because\n"
    " * nothing else called it, but 5.15's truncate path still sweeps a pagevec\n"
    " * of exceptional entries through it in truncate_exceptional_pvec_entries(),\n"
    " * and that path takes the page lock first, so it is not this one.\n"
    " *\n"
    " * This replacement overwrites the one 61c663e020d2 installs -- the two are\n"
    " * one change landed in two commits, and the pagevec form never survives a\n"
    " * full pass of this batch.  Its group probes for clear_shadow_entries()\n"
    " * instead of re-deriving that text.\n"
    " */\n"
    "static void clear_shadow_entries(struct address_space *mapping,\n"
    "\t\t\t\t unsigned long start, unsigned long max)\n"
    "{\n"
    "\tXA_STATE(xas, &mapping->i_pages, start);\n"
    "\tstruct page *page;\n"
    "\n"
    "\t/* Handled by shmem itself, or for DAX we do nothing. */\n"
    "\tif (shmem_mapping(mapping) || dax_mapping(mapping))\n"
    "\t\treturn;\n"
    "\n"
    "\txas_set_update(&xas, workingset_update_node);\n"
    "\n"
    "\txa_lock_irq(&mapping->i_pages);\n"
    "\n"
    "\t/* Clear all shadow entries from start to max */\n"
    "\txas_for_each(&xas, page, max) {\n"
    "\t\tif (xa_is_value(page))\n"
    "\t\t\txas_store(&xas, NULL);\n"
    "\t}\n"
    "\n"
    "\txa_unlock_irq(&mapping->i_pages);\n"
    "}\n"
)

# The two loops learn the batch size (which the call sites need for indices[]).
_SWEEP_BIP_LOOP_OLD = (
    "\tpagevec_init(&pvec);\n"
    "\twhile (find_lock_entries(mapping, index, end, &pvec, indices)) {\n"
    "\t\tfor (i = 0; i < pagevec_count(&pvec); i++) {\n"
)

_SWEEP_BIP_LOOP_NEW = (
    "\tpagevec_init(&pvec);\n"
    "\twhile (find_lock_entries(mapping, index, end, &pvec, indices)) {\n"
    "\t\tint nr = pagevec_count(&pvec);\n"
    "\n"
    "\t\tfor (i = 0; i < nr; i++) {\n"
)

_SWEEP_BIP_CALL_OLD = (
    "\t\t\tcount += ret;\n"
    "\t\t}\n"
    "\n"
    "\t\tif (xa_has_values)\n"
    "\t\t\tclear_shadow_entries(mapping, &pvec, indices);\n"
)

_SWEEP_BIP_CALL_NEW = (
    "\t\t\tcount += ret;\n"
    "\t\t}\n"
    "\n"
    "\t\tif (xa_has_values)\n"
    "\t\t\tclear_shadow_entries(mapping, indices[0], indices[nr-1]);\n"
)

_SWEEP_IIP_LOOP_OLD = (
    "\tpagevec_init(&pvec);\n"
    "\tindex = start;\n"
    "\twhile (find_get_entries(mapping, index, end, &pvec, indices)) {\n"
    "\t\tfor (i = 0; i < pagevec_count(&pvec); i++) {\n"
)

_SWEEP_IIP_LOOP_NEW = (
    "\tpagevec_init(&pvec);\n"
    "\tindex = start;\n"
    "\twhile (find_get_entries(mapping, index, end, &pvec, indices)) {\n"
    "\t\tint nr = pagevec_count(&pvec);\n"
    "\n"
    "\t\tfor (i = 0; i < nr; i++) {\n"
)

_SWEEP_IIP_CALL_OLD = (
    "\t\t\tunlock_page(page);\n"
    "\t\t}\n"
    "\n"
    "\t\tif (xa_has_values)\n"
    "\t\t\tclear_shadow_entries(mapping, &pvec, indices);\n"
)

_SWEEP_IIP_CALL_NEW = (
    "\t\t\tunlock_page(page);\n"
    "\t\t}\n"
    "\n"
    "\t\tif (xa_has_values)\n"
    "\t\t\tclear_shadow_entries(mapping, indices[0], indices[nr-1]);\n"
)


def build_shadow_sweep_steps():
    return [
        (TRUNCATE, _SWEEP_FN_OLD, _SWEEP_FN_NEW, T),
        (TRUNCATE, _SWEEP_BIP_LOOP_OLD, _SWEEP_BIP_LOOP_NEW, T),
        (TRUNCATE, _SWEEP_BIP_CALL_OLD, _SWEEP_BIP_CALL_NEW, T),
        (TRUNCATE, _SWEEP_IIP_LOOP_OLD, _SWEEP_IIP_LOOP_NEW, T),
        (TRUNCATE, _SWEEP_IIP_CALL_OLD, _SWEEP_IIP_CALL_NEW, T),
    ]


def _shadow_sweep_probe(ctx):
    """Companion probe for the same trap-5 pair, from the other side."""
    return "unsigned long start, unsigned long max" in ctx.read(TRUNCATE)


# ---------------------------------------------------------------------------
# 6375e95f381e -- reclaim an empty PTE page in madvise(MADV_DONTNEED).
# ---------------------------------------------------------------------------

_ZD_OLD = (
    "struct zap_details {\n"
    "\tstruct address_space *check_mapping;\t/* Check page->mapping if set */\n"
    "\tpgoff_t\tfirst_index;\t\t\t/* Lowest page->index to unmap */\n"
    "\tpgoff_t last_index;\t\t\t/* Highest page->index to unmap */\n"
    "\tstruct page *single_page;\t\t/* Locked page to be unmapped */\n"
    "};\n"
)

_ZD_NEW = (
    "struct zap_details {\n"
    "\tstruct address_space *check_mapping;\t/* Check page->mapping if set */\n"
    "\tpgoff_t\tfirst_index;\t\t\t/* Lowest page->index to unmap */\n"
    "\tpgoff_t last_index;\t\t\t/* Highest page->index to unmap */\n"
    "\tstruct page *single_page;\t\t/* Locked page to be unmapped */\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: 6375e95f381e -- MADV_DONTNEED may hand back\n"
    "\t * the PTE page of a range it has just emptied.\n"
    "\t */\n"
    "\tbool reclaim_pt;\t\t\t/* Need reclaim page tables? */\n"
    "};\n"
)

_ZPR_DECL_OLD = (
    "void unmap_page_range(struct mmu_gather *tlb,\n"
    "\t\t\t     struct vm_area_struct *vma,\n"
    "\t\t\t     unsigned long addr, unsigned long end,\n"
    "\t\t\t     struct zap_details *details);\n"
    "\n"
)

_ZPR_DECL_NEW = (
    "void unmap_page_range(struct mmu_gather *tlb,\n"
    "\t\t\t     struct vm_area_struct *vma,\n"
    "\t\t\t     unsigned long addr, unsigned long end,\n"
    "\t\t\t     struct zap_details *details);\n"
    "void zap_page_range_single(struct vm_area_struct *vma, unsigned long address,\n"
    "\t\tunsigned long size, struct zap_details *details);\n"
    "\n"
)

# The helpers go next to should_zap_cows(), which needs no change on 5.15: it
# already zaps everything when details->check_mapping is NULL, and that is
# what upstream spells even_cows = true.
_HELPERS_OLD = (
    "static inline bool should_zap_cows(struct zap_details *details)\n"
    "{\n"
    "\t/* By default, zap all pages */\n"
    "\tif (!details)\n"
    "\t\treturn true;\n"
    "\n"
    "\t/* Or, we zap COWed pages only if the caller wants to */\n"
    "\treturn !details->check_mapping;\n"
    "}\n"
    "\n"
    "static unsigned long zap_pte_range(struct mmu_gather *tlb,\n"
)

_HELPERS_NEW = (
    "static inline bool should_zap_cows(struct zap_details *details)\n"
    "{\n"
    "\t/* By default, zap all pages */\n"
    "\tif (!details)\n"
    "\t\treturn true;\n"
    "\n"
    "\t/* Or, we zap COWed pages only if the caller wants to */\n"
    "\treturn !details->check_mapping;\n"
    "}\n"
    "\n"
    "/*\n"
    " * ABK stable_515_backport: 6375e95f381e (v6.14); mainline\n"
    " *\n"
    " *   mm: pgtable: reclaim empty PTE page in madvise(MADV_DONTNEED)\n"
    " *\n"
    " * A range that MADV_DONTNEED has just emptied usually keeps its PTE page\n"
    " * mapped for the rest of the process's life, which is what a userspace\n"
    " * allocator that churns address space (jemalloc/tcmalloc; ART on Android)\n"
    " * turns into the upstream server snapshot of VmPTE 110g against RSS 590g.\n"
    " * The page is handed back once the range is gone, behind a zap_details mark\n"
    " * that only MADV_DONTNEED sets.\n"
    " *\n"
    " * 5.15 shape differences, all deliberate:\n"
    " *\n"
    " *  - only the lock-protected path is ported.  Upstream first tries to take\n"
    " *    the pmd lock from under the pte lock (try_get_and_clear_pmd(), reading\n"
    " *    the pmd with pmdp_get_lockless()); 5.15 has no pmdp_get_lockless()\n"
    " *    on any baseline, so every attempt goes through try_to_free_pte(),\n"
    " *    which re-decides under the pmd lock.  That is upstream's own fallback\n"
    " *    path, and it is also what makes the decision safe here: the pte loop\n"
    " *    above can skip an entry (details->check_mapping, a page it could not\n"
    " *    take), and the re-scan finds it rather than trusting the loop.\n"
    " *  - the lock order is pmd slot lock, then the page-table page's own ptl --\n"
    " *    pmd_lockptr() looks at the page holding the pmd entry, ptlock_ptr()\n"
    " *    at the page the pmd points to, so they differ wherever split ptlocks\n"
    " *    are on.  The (ptl != pml) test keeps the folded configurations (one\n"
    " *    lock for both levels) from taking it twice.\n"
    " *  - freeing the page table is only safe with MMU_GATHER_RCU_TABLE_FREE,\n"
    " *    which arm64 selects unconditionally and the page is released through\n"
    " *    pte_free_tlb().  free_pte_page() also carries the\n"
    " *    CONFIG_SPECULATIVE_PAGE_FAULT barrier of free_pte_range(), which\n"
    " *    upstream's helper has no counterpart for, and which this tree needs\n"
    " *    wherever a reader can hold the ptl of a page table it does not lock.\n"
    " *\n"
    " * No performance claim of our own: the number above is upstream's server\n"
    " * measurement, not a device measurement.\n"
    " */\n"
    "static bool reclaim_pt_is_enabled(unsigned long start, unsigned long end,\n"
    "\t\t\t\t  struct zap_details *details)\n"
    "{\n"
    "\treturn details && details->reclaim_pt && (end - start >= PMD_SIZE);\n"
    "}\n"
    "\n"
    "/* Mirror of free_pte_range() for a page table whose pmd we just cleared. */\n"
    "static void free_pte_page(struct mmu_gather *tlb, pmd_t *pmd, pmd_t pmdval,\n"
    "\t\t\t  unsigned long addr)\n"
    "{\n"
    "\tpgtable_t token = pmd_pgtable(pmdval);\n"
    "\n"
    "#ifdef CONFIG_SPECULATIVE_PAGE_FAULT\n"
    "\t/*\n"
    "\t * Same barrier as free_pte_range(): a speculative fault that managed\n"
    "\t * to take the pmd lock must be done before the page table goes, and\n"
    "\t * with split ptlocks a reader that never takes it has to be waited\n"
    "\t * for by hand.\n"
    "\t */\n"
    "\tspinlock_t *ptl = pmd_lock(tlb->mm, pmd);\n"
    "\n"
    "\tspin_unlock(ptl);\n"
    "#if ALLOC_SPLIT_PTLOCKS\n"
    "\tsmp_call_function(wait_for_smp_sync, NULL, 1);\n"
    "#endif\n"
    "#endif\n"
    "\tpte_free_tlb(tlb, token, addr);\n"
    "\tmm_dec_nr_ptes(tlb->mm);\n"
    "}\n"
    "\n"
    "static void try_to_free_pte(struct mm_struct *mm, pmd_t *pmd,\n"
    "\t\t\t    unsigned long addr, struct mmu_gather *tlb)\n"
    "{\n"
    "\tpmd_t pmdval;\n"
    "\tspinlock_t *pml, *ptl;\n"
    "\tpte_t *start_pte, *pte;\n"
    "\tint i;\n"
    "\n"
    "\tpml = pmd_lock(mm, pmd);\n"
    "\tpmdval = *pmd;\n"
    "\tif (pmd_none(pmdval) || pmd_bad(pmdval)) {\n"
    "\t\tspin_unlock(pml);\n"
    "\t\treturn;\n"
    "\t}\n"
    "\n"
    "\tptl = pte_lockptr(mm, pmd);\n"
    "\tstart_pte = pte_offset_map(pmd, addr);\n"
    "\tif (ptl != pml)\n"
    "\t\tspin_lock_nested(ptl, SINGLE_DEPTH_NESTING);\n"
    "\n"
    "\t/* Check if it is empty PTE page */\n"
    "\tfor (i = 0, pte = start_pte; i < PTRS_PER_PTE; i++, pte++) {\n"
    "\t\tif (!pte_none(*pte)) {\n"
    "\t\t\tpte_unmap(start_pte);\n"
    "\t\t\tif (ptl != pml)\n"
    "\t\t\t\tspin_unlock(ptl);\n"
    "\t\t\tspin_unlock(pml);\n"
    "\t\t\treturn;\n"
    "\t\t}\n"
    "\t}\n"
    "\tpte_unmap(start_pte);\n"
    "\n"
    "\tpmd_clear(pmd);\n"
    "\n"
    "\tif (ptl != pml)\n"
    "\t\tspin_unlock(ptl);\n"
    "\tspin_unlock(pml);\n"
    "\n"
    "\tfree_pte_page(tlb, pmd, pmdval, addr);\n"
    "}\n"
    "\n"
    "static unsigned long zap_pte_range(struct mmu_gather *tlb,\n"
)

_ZPR_LOCALS_OLD = (
    "\tstruct mm_struct *mm = tlb->mm;\n"
    "\tint force_flush = 0;\n"
    "\tint rss[NR_MM_COUNTERS];\n"
    "\tspinlock_t *ptl;\n"
    "\tpte_t *start_pte;\n"
    "\tpte_t *pte;\n"
    "\tswp_entry_t entry;\n"
    "\tbool bypass = false;\n"
)

_ZPR_LOCALS_NEW = (
    "\tstruct mm_struct *mm = tlb->mm;\n"
    "\tint force_flush = 0;\n"
    "\tint rss[NR_MM_COUNTERS];\n"
    "\tspinlock_t *ptl;\n"
    "\tpte_t *start_pte;\n"
    "\tpte_t *pte;\n"
    "\tswp_entry_t entry;\n"
    "\tbool bypass = false;\n"
    "\tunsigned long start = addr;\n"
)

_ZPR_TAIL_OLD = (
    "\tif (addr != end) {\n"
    "\t\tcond_resched();\n"
    "\t\tgoto again;\n"
    "\t}\n"
    "\n"
    "\treturn addr;\n"
    "}\n"
)

_ZPR_TAIL_NEW = (
    "\tif (addr != end) {\n"
    "\t\tcond_resched();\n"
    "\t\tgoto again;\n"
    "\t}\n"
    "\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: 6375e95f381e.  addr == end, so the pmd's\n"
    "\t * whole range is gone.  try_to_free_pte() re-checks it under the pmd\n"
    "\t * lock, so an entry the loop skipped simply keeps the page table.\n"
    "\t */\n"
    "\tif (reclaim_pt_is_enabled(start, end, details))\n"
    "\t\ttry_to_free_pte(mm, pmd, start, tlb);\n"
    "\n"
    "\treturn addr;\n"
    "}\n"
)

# The per-VMA entry point becomes reachable from madvise.c so that it can carry
# zap_details.  MADV_DONTNEED's callback has end <= vma->vm_end, and 5.15's
# zap_page_range() loop runs exactly one iteration in that case, so this is the
# same range through the same unmap path.  The doc comment stays in both blocks
# because the bare signature of the new form is a *substring* of the pristine
# ``static void ...`` line, which would make replace_once see its own
# replacement and skip the step.
_ZPS_OLD = (
    " * The range must fit into one VMA.\n"
    " */\n"
    "static void zap_page_range_single(struct vm_area_struct *vma, unsigned long address,\n"
    "\t\tunsigned long size, struct zap_details *details)\n"
)

_ZPS_NEW = (
    " * The range must fit into one VMA.\n"
    " */\n"
    "void zap_page_range_single(struct vm_area_struct *vma, unsigned long address,\n"
    "\t\tunsigned long size, struct zap_details *details)\n"
)

_MADV_OLD = (
    "static long madvise_dontneed_single_vma(struct vm_area_struct *vma,\n"
    "\t\t\t\t\tunsigned long start, unsigned long end)\n"
    "{\n"
    "\tmadvise_vma_pad_pages(vma, start, end);\n"
    "\n"
    "\tzap_page_range(vma, start, end - start);\n"
    "\treturn 0;\n"
    "}\n"
)

_MADV_NEW = (
    "static long madvise_dontneed_single_vma(struct vm_area_struct *vma,\n"
    "\t\t\t\t\tunsigned long start, unsigned long end)\n"
    "{\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: 6375e95f381e.  MADV_DONTNEED is the one\n"
    "\t * caller that may give a range's PTE page back: the range is gone and\n"
    "\t * nothing is expected to be found there again.  5.15 routed this\n"
    "\t * through the multi-VMA zap_page_range(); this callback is handed\n"
    "\t * end <= vma->vm_end, so the single-VMA entry that can carry\n"
    "\t * zap_details zaps the same range.\n"
    "\t */\n"
    "\tstruct zap_details details = {\n"
    "\t\t.reclaim_pt = true,\n"
    "\t};\n"
    "\n"
    "\tmadvise_vma_pad_pages(vma, start, end);\n"
    "\n"
    "\tzap_page_range_single(vma, start, end - start, &details);\n"
    "\treturn 0;\n"
    "}\n"
)


def build_pt_reclaim_steps():
    return [
        (MM_H, _ZD_OLD, _ZD_NEW, T),
        (INTERNAL_H, _ZPR_DECL_OLD, _ZPR_DECL_NEW, T),
        (MEMORY, _HELPERS_OLD, _HELPERS_NEW, T),
        (MEMORY, _ZPR_LOCALS_OLD, _ZPR_LOCALS_NEW, T),
        (MEMORY, _ZPR_TAIL_OLD, _ZPR_TAIL_NEW, T),
        (MEMORY, _ZPS_OLD, _ZPS_NEW, T),
        (MADVISE, _MADV_OLD, _MADV_NEW, T),
    ]


# ---------------------------------------------------------------------------
# 43c4cfde7e37 -- one mmu_gather for the whole madvise() call.
# ---------------------------------------------------------------------------

_BATCHED_DECL_OLD = (
    "void zap_page_range_single(struct vm_area_struct *vma, unsigned long address,\n"
    "\t\tunsigned long size, struct zap_details *details);\n"
    "\n"
)

_BATCHED_DECL_NEW = (
    "void zap_page_range_single(struct vm_area_struct *vma, unsigned long address,\n"
    "\t\tunsigned long size, struct zap_details *details);\n"
    "void zap_page_range_single_batched(struct mmu_gather *tlb,\n"
    "\t\tstruct vm_area_struct *vma, unsigned long address,\n"
    "\t\tunsigned long size, struct zap_details *details);\n"
    "\n"
)

# Split the single-VMA zap into the part that claims a mmu_gather and the part
# that feeds one: madvise() then owns one gather for its whole request.  The
# kernel-doc block stays attached to zap_page_range_single(), so the batched
# half is appended *after* it rather than spliced into the middle.
_BATCHED_CORE_OLD = (
    " * The range must fit into one VMA.\n"
    " */\n"
    "void zap_page_range_single(struct vm_area_struct *vma, unsigned long address,\n"
    "\t\tunsigned long size, struct zap_details *details)\n"
    "{\n"
    "\tstruct mmu_notifier_range range;\n"
    "\tstruct mmu_gather tlb;\n"
    "\n"
    "\tlru_add_drain();\n"
    "\tmmu_notifier_range_init(&range, MMU_NOTIFY_CLEAR, 0, vma, vma->vm_mm,\n"
    "\t\t\t\taddress, address + size);\n"
    "\ttlb_gather_mmu(&tlb, vma->vm_mm);\n"
    "\tupdate_hiwater_rss(vma->vm_mm);\n"
    "\tmmu_notifier_invalidate_range_start(&range);\n"
    "\tunmap_single_vma(&tlb, vma, address, range.end, details);\n"
    "\tmmu_notifier_invalidate_range_end(&range);\n"
    "\ttlb_finish_mmu(&tlb);\n"
    "}\n"
)

_BATCHED_CORE_NEW = (
    " * The range must fit into one VMA.\n"
    " */\n"
    "void zap_page_range_single(struct vm_area_struct *vma, unsigned long address,\n"
    "\t\tunsigned long size, struct zap_details *details)\n"
    "{\n"
    "\tstruct mmu_gather tlb;\n"
    "\n"
    "\tlru_add_drain();\n"
    "\ttlb_gather_mmu(&tlb, vma->vm_mm);\n"
    "\tzap_page_range_single_batched(&tlb, vma, address, size, details);\n"
    "\ttlb_finish_mmu(&tlb);\n"
    "}\n"
    "\n"
    "/*\n"
    " * ABK stable_515_backport: 43c4cfde7e37.  The TLB work of one\n"
    " * zap_page_range_single() call, with the mmu_gather owned by the caller:\n"
    " * madvise(MADV_DONTNEED) walks its whole request one VMA at a time, and a\n"
    " * gather per VMA flushes -- and IPIs -- per VMA.  5.15 has no\n"
    " * madvise_behavior block to carry the gather, so it is threaded through\n"
    " * madvise_walk_vmas() explicitly (NULL means \"not batched\").\n"
    " */\n"
    "void zap_page_range_single_batched(struct mmu_gather *tlb,\n"
    "\t\tstruct vm_area_struct *vma, unsigned long address,\n"
    "\t\tunsigned long size, struct zap_details *details)\n"
    "{\n"
    "\tstruct mmu_notifier_range range;\n"
    "\n"
    "\tVM_WARN_ON_ONCE(!tlb || tlb->mm != vma->vm_mm);\n"
    "\n"
    "\tmmu_notifier_range_init(&range, MMU_NOTIFY_CLEAR, 0, vma, vma->vm_mm,\n"
    "\t\t\t\taddress, address + size);\n"
    "\tupdate_hiwater_rss(vma->vm_mm);\n"
    "\tmmu_notifier_invalidate_range_start(&range);\n"
    "\tunmap_single_vma(tlb, vma, address, range.end, details);\n"
    "\tmmu_notifier_invalidate_range_end(&range);\n"
    "}\n"
)

_MBTF_OLD = (
    "static int madvise_vma_behavior(struct vm_area_struct *vma,\n"
    "\t\t\t\tstruct vm_area_struct **prev,\n"
    "\t\t\t\tunsigned long start, unsigned long end,\n"
    "\t\t\t\tunsigned long behavior)\n"
)

_MBTF_NEW = (
    "static int madvise_vma_behavior(struct vm_area_struct *vma,\n"
    "\t\t\t\tstruct vm_area_struct **prev,\n"
    "\t\t\t\tunsigned long start, unsigned long end,\n"
    "\t\t\t\tunsigned long behavior,\n"
    "\t\t\t\tstruct mmu_gather *tlb)\n"
)

# The helper goes after madvise_walk_vmas(), i.e. in the one spot in the file
# where it does not land underneath another function's kernel-doc block.
_MBTF_HELPER_OLD = (
    "\treturn unmapped_error;\n"
    "}\n"
)

_MBTF_HELPER_NEW = (
    "\treturn unmapped_error;\n"
    "}\n"
    "\n"
    "/*\n"
    " * ABK stable_515_backport: 43c4cfde7e37.  Behaviors whose whole request can\n"
    " * share one mmu_gather.  Upstream also lists MADV_DONTNEED_LOCKED (5.15 has\n"
    " * no such behavior) and MADV_FREE (5.15's madvise_free_single_vma() still\n"
    " * owns its own gather, so batching it is a separate change).\n"
    " */\n"
    "static bool madvise_batch_tlb_flush(int behavior)\n"
    "{\n"
    "\tswitch (behavior) {\n"
    "\tcase MADV_DONTNEED:\n"
    "\t\treturn true;\n"
    "\tdefault:\n"
    "\t\treturn false;\n"
    "\t}\n"
    "}\n"
)

_MBTF_DISPATCH_OLD = (
    "\tcase MADV_FREE:\n"
    "\tcase MADV_DONTNEED:\n"
    "\t\treturn madvise_dontneed_free(vma, prev, start, end, behavior);\n"
)

_MBTF_DISPATCH_NEW = (
    "\tcase MADV_FREE:\n"
    "\tcase MADV_DONTNEED:\n"
    "\t\treturn madvise_dontneed_free(vma, prev, start, end, behavior,\n"
    "\t\t\t\t\t      tlb);\n"
)

_MBTF_WALK_OLD = (
    "int madvise_walk_vmas(struct mm_struct *mm, unsigned long start,\n"
    "\t\t      unsigned long end, unsigned long arg,\n"
    "\t\t      int (*visit)(struct vm_area_struct *vma,\n"
    "\t\t\t\t   struct vm_area_struct **prev, unsigned long start,\n"
    "\t\t\t\t   unsigned long end, unsigned long arg))\n"
)

_MBTF_WALK_NEW = (
    "int madvise_walk_vmas(struct mm_struct *mm, unsigned long start,\n"
    "\t\t      unsigned long end, unsigned long arg,\n"
    "\t\t      struct mmu_gather *tlb,\n"
    "\t\t      int (*visit)(struct vm_area_struct *vma,\n"
    "\t\t\t\t   struct vm_area_struct **prev, unsigned long start,\n"
    "\t\t\t\t   unsigned long end, unsigned long arg,\n"
    "\t\t\t\t   struct mmu_gather *tlb))\n"
)

_MBTF_VISIT_OLD = (
    "\t\terror = visit(vma, &prev, start, tmp, arg);\n"
)

_MBTF_VISIT_NEW = (
    "\t\terror = visit(vma, &prev, start, tmp, arg, tlb);\n"
)

# CONFIG_ANON_VMA_NAME's callback takes the same walker.
_MBTF_ANON_OLD = (
    "static int madvise_vma_anon_name(struct vm_area_struct *vma,\n"
    "\t\t\t\t struct vm_area_struct **prev,\n"
    "\t\t\t\t unsigned long start, unsigned long end,\n"
    "\t\t\t\t unsigned long anon_name)\n"
)

_MBTF_ANON_NEW = (
    "static int madvise_vma_anon_name(struct vm_area_struct *vma,\n"
    "\t\t\t\t struct vm_area_struct **prev,\n"
    "\t\t\t\t unsigned long start, unsigned long end,\n"
    "\t\t\t\t unsigned long anon_name,\n"
    "\t\t\t\t struct mmu_gather *tlb)\n"
)

_MBTF_ANON_CALL_OLD = (
    "\treturn madvise_walk_vmas(mm, start, end, (unsigned long)anon_name,\n"
    "\t\t\t\t madvise_vma_anon_name);\n"
)

_MBTF_ANON_CALL_NEW = (
    "\treturn madvise_walk_vmas(mm, start, end, (unsigned long)anon_name,\n"
    "\t\t\t\t NULL, madvise_vma_anon_name);\n"
)

_MBTF_FREE_OLD = (
    "static long madvise_dontneed_free(struct vm_area_struct *vma,\n"
    "\t\t\t\t  struct vm_area_struct **prev,\n"
    "\t\t\t\t  unsigned long start, unsigned long end,\n"
    "\t\t\t\t  int behavior)\n"
)

_MBTF_FREE_NEW = (
    "static long madvise_dontneed_free(struct vm_area_struct *vma,\n"
    "\t\t\t\t  struct vm_area_struct **prev,\n"
    "\t\t\t\t  unsigned long start, unsigned long end,\n"
    "\t\t\t\t  int behavior, struct mmu_gather *tlb)\n"
)

_MBTF_FREE_CALL_OLD = (
    "\tif (behavior == MADV_DONTNEED)\n"
    "\t\treturn madvise_dontneed_single_vma(vma, start, end);\n"
)

_MBTF_FREE_CALL_NEW = (
    "\tif (behavior == MADV_DONTNEED)\n"
    "\t\treturn madvise_dontneed_single_vma(vma, start, end, tlb);\n"
)

_MBTF_SINGLE_OLD = (
    "static long madvise_dontneed_single_vma(struct vm_area_struct *vma,\n"
    "\t\t\t\t\tunsigned long start, unsigned long end)\n"
    "{\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: 6375e95f381e.  MADV_DONTNEED is the one\n"
    "\t * caller that may give a range's PTE page back: the range is gone and\n"
    "\t * nothing is expected to be found there again.  5.15 routed this\n"
    "\t * through the multi-VMA zap_page_range(); this callback is handed\n"
    "\t * end <= vma->vm_end, so the single-VMA entry that can carry\n"
    "\t * zap_details zaps the same range.\n"
    "\t */\n"
    "\tstruct zap_details details = {\n"
    "\t\t.reclaim_pt = true,\n"
    "\t};\n"
    "\n"
    "\tmadvise_vma_pad_pages(vma, start, end);\n"
    "\n"
    "\tzap_page_range_single(vma, start, end - start, &details);\n"
    "\treturn 0;\n"
    "}\n"
)

_MBTF_SINGLE_NEW = (
    "static long madvise_dontneed_single_vma(struct vm_area_struct *vma,\n"
    "\t\t\t\t\tunsigned long start, unsigned long end,\n"
    "\t\t\t\t\tstruct mmu_gather *tlb)\n"
    "{\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: 6375e95f381e.  MADV_DONTNEED is the one\n"
    "\t * caller that may give a range's PTE page back: the range is gone and\n"
    "\t * nothing is expected to be found there again.  5.15 routed this\n"
    "\t * through the multi-VMA zap_page_range(); this callback is handed\n"
    "\t * end <= vma->vm_end, so the single-VMA entry that can carry\n"
    "\t * zap_details zaps the same range.\n"
    "\t *\n"
    "\t * ABK stable_515_backport: 43c4cfde7e37.  The gather belongs to the\n"
    "\t * whole madvise() call, so this VMA only adds its range to it; tlb is\n"
    "\t * NULL for a caller that did not ask for batching.\n"
    "\t */\n"
    "\tstruct zap_details details = {\n"
    "\t\t.reclaim_pt = true,\n"
    "\t};\n"
    "\n"
    "\tmadvise_vma_pad_pages(vma, start, end);\n"
    "\n"
    "\tif (!tlb) {\n"
    "\t\tzap_page_range_single(vma, start, end - start, &details);\n"
    "\t\treturn 0;\n"
    "\t}\n"
    "\n"
    "\tzap_page_range_single_batched(tlb, vma, start, end - start, &details);\n"
    "\treturn 0;\n"
    "}\n"
)

_MBTF_DO_OLD = (
    "\tint write;\n"
    "\tsize_t len;\n"
    "\tstruct blk_plug plug;\n"
)

_MBTF_DO_NEW = (
    "\tint write;\n"
    "\tsize_t len;\n"
    "\tstruct blk_plug plug;\n"
    "\tstruct mmu_gather tlb;\n"
    "\tstruct mmu_gather *tlbp = NULL;\n"
)

_MBTF_DO_WALK_OLD = (
    "\tblk_start_plug(&plug);\n"
    "\terror = madvise_walk_vmas(mm, start, end, behavior,\n"
    "\t\t\tmadvise_vma_behavior);\n"
    "\tblk_finish_plug(&plug);\n"
)

_MBTF_DO_WALK_NEW = (
    "\tblk_start_plug(&plug);\n"
    "\tif (madvise_batch_tlb_flush(behavior)) {\n"
    "\t\tlru_add_drain();\n"
    "\t\ttlb_gather_mmu(&tlb, mm);\n"
    "\t\ttlbp = &tlb;\n"
    "\t}\n"
    "\terror = madvise_walk_vmas(mm, start, end, behavior, tlbp,\n"
    "\t\t\tmadvise_vma_behavior);\n"
    "\tif (tlbp)\n"
    "\t\ttlb_finish_mmu(&tlb);\n"
    "\tblk_finish_plug(&plug);\n"
)


def build_tlb_batch_steps():
    return [
        (INTERNAL_H, _BATCHED_DECL_OLD, _BATCHED_DECL_NEW, T),
        (MEMORY, _BATCHED_CORE_OLD, _BATCHED_CORE_NEW, T),
        (MADVISE, _MBTF_OLD, _MBTF_NEW, T),
        (MADVISE, _MBTF_DISPATCH_OLD, _MBTF_DISPATCH_NEW, T),
        (MADVISE, _MBTF_WALK_OLD, _MBTF_WALK_NEW, T),
        (MADVISE, _MBTF_VISIT_OLD, _MBTF_VISIT_NEW, T),
        (MADVISE, _MBTF_HELPER_OLD, _MBTF_HELPER_NEW, T),
        (MADVISE, _MBTF_ANON_OLD, _MBTF_ANON_NEW, T),
        (MADVISE, _MBTF_ANON_CALL_OLD, _MBTF_ANON_CALL_NEW, T),
        (MADVISE, _MBTF_FREE_OLD, _MBTF_FREE_NEW, T),
        (MADVISE, _MBTF_FREE_CALL_OLD, _MBTF_FREE_CALL_NEW, T),
        (MADVISE, _MBTF_SINGLE_OLD, _MBTF_SINGLE_NEW, T),
        (MADVISE, _MBTF_DO_OLD, _MBTF_DO_NEW, T),
        (MADVISE, _MBTF_DO_WALK_OLD, _MBTF_DO_WALK_NEW, T),
    ]


# ---------------------------------------------------------------------------
# Group adapters.
# ---------------------------------------------------------------------------

RECLAIM_PT_MARKER = "ABK stable_515_backport: 6375e95f381e"
# The probe for the batching group: the gather it hoists into do_madvise().
# Taken from the replacement block rather than spelled out, so the probe and
# the text it guards cannot drift apart.
MADVISE_TLB_GATHER = "\t\ttlb_gather_mmu(&tlb, mm);\n"


def _run(ctx, steps, probe=None):
    if probe is not None and probe(ctx):
        return "already_present", (
            "this group's own payload is already in the tree, so its paired "
            "group has consumed its anchors in this pass")
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _shadow_batch_apply(ctx):
    return _run(ctx, build_shadow_batch_steps(), _shadow_batch_probe)


def _shadow_sweep_apply(ctx):
    return _run(ctx, build_shadow_sweep_steps(), _shadow_sweep_probe)


def _pt_reclaim_probe(ctx):
    return RECLAIM_PT_MARKER in ctx.read(MM_H)


def _pt_reclaim_apply(ctx):
    return _run(ctx, build_pt_reclaim_steps(), _pt_reclaim_probe)


def _tlb_batch_probe(ctx):
    return MADVISE_TLB_GATHER in ctx.read(MADVISE)


def _tlb_batch_apply(ctx):
    return _run(ctx, build_tlb_batch_steps(), _tlb_batch_probe)


def build_groups(PatchGroup):
    """Return the four Batch-34 records, in dependency order.

    The pairs are registered in the order their commits landed upstream: the
    second group of each pair rewrites text the first one wrote, and both first
    groups carry a probe on their own payload so the second pass stops there
    instead of re-deriving anchors the partner has already consumed.
    """
    return [
        PatchGroup(
            "truncate_shadow_batch",
            "mm/truncate: clear the shadow entries of a whole pagevec under a "
            "single acquisition of the i_pages lock, instead of one lock/walk "
            "per entry (the 11s clear_shadow_entry() soft-lockup)",
            [
                "61c663e020d2 (v6.11, 'mm/truncate: batch-clear shadow "
                "entries'; Reported-by: Bharata B Rao)",
            ],
            [TRUNCATE],
            _shadow_batch_apply,
        ),
        PatchGroup(
            "truncate_shadow_batch_sweep",
            "mm: clear a pagevec's shadow entries with one xas_for_each() over "
            "the batch's index span instead of one __clear_shadow_entry() per "
            "entry (refactors the group above)",
            [
                "d3db2c042591 (v6.13, 'mm: optimize invalidation of shadow "
                "entries'; 18% off fadvise(DONTNEED) on a 200GiB fuse file)",
            ],
            [TRUNCATE],
            _shadow_sweep_apply,
        ),
        PatchGroup(
            "madvise_pt_reclaim",
            "mm: MADV_DONTNEED hands back the PTE page of a range it has just "
            "emptied, behind a zap_details.reclaim_pt mark (5.15 carries the "
            "lock-protected path only: it has no pmdp_get_lockless())",
            [
                "6375e95f381e (v6.14, 'mm: pgtable: reclaim empty PTE page in "
                "madvise(MADV_DONTNEED)')",
            ],
            [MM_H, INTERNAL_H, MEMORY, MADVISE],
            _pt_reclaim_apply,
        ),
        PatchGroup(
            "madvise_batch_tlb_flush",
            "mm/madvise: gather the TLB flushes of one MADV_DONTNEED request in "
            "a single mmu_gather instead of one per VMA",
            [
                "43c4cfde7e37 (v6.16, 'mm/madvise: batch tlb flushes for "
                "MADV_DONTNEED[_LOCKED]')",
            ],
            [INTERNAL_H, MEMORY, MADVISE],
            _tlb_batch_apply,
        ),
    ]
