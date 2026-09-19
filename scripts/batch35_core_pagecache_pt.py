"""Batch 35: page-cache shadow-entry sweeps.

Two upstream commits, registered as one ordered pair:

  truncate_shadow_batch           61c663e020d2 (v6.11) -- clear the shadow
                                  entries of a whole pagevec under one
                                  acquisition of the i_pages lock, instead of
                                  one lock/unlock/tree-walk per entry.
  truncate_shadow_batch_sweep     d3db2c042591 (v6.13) -- refactor that helper
                                  again: one xas_for_each() over the batch's
                                  index span replaces the per-entry
                                  __clear_shadow_entry() calls.

The pair is registered in dependency order and the later group rewrites text
the earlier one wrote, so the earlier group carries a shape probe on its own
payload (the trap-5 rule in AGENTS.md): on the second pass it reports
``already_present`` instead of re-deriving its anchors, which the later group
has already consumed.

The MADV_DONTNEED page-table pair that used to live here
(``madvise_pt_reclaim`` 6375e95f381e, and the ``madvise_batch_tlb_flush``
43c4cfde7e37 that was authored on top of it) is REMOVED: on 5.15
``pte_offset_map_lock()`` and the smaps/reclaim page-table walkers still read
``*pmd`` and derive the PTE page's ptl without the RCU-freed-page-table
protection upstream's 6.5 series (``pmdp_get_lockless()`` + RCU
``pte_offset_map*``) gives every walker.  Freeing an empty PTE page under
``mmap_read_lock`` in ``try_to_free_pte()`` therefore races a concurrent
walker holding a stale ptl pointer -- observed as a ``_raw_spin_lock`` fault
in ``smaps_pte_range`` (a /proc/pid/smaps read) with the PTE page already
gone.  Upstream also gates ``PT_RECLAIM`` behind ``ARCH_SUPPORTS_PT_RECLAIM``,
which arm64 did not select at v6.14; the footprint win is proportional to a
process's ``VmPTE`` under heavy MADV_DONTNEED churn over huge sparse mappings
(a server allocator pattern), negligible on this target.  See CHANGELOG.md
(Batch 35) and plan.md's exclusion record.

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
# Group adapters.
# ---------------------------------------------------------------------------

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


def build_groups(PatchGroup):
    """Return the two shadow-entry Batch-35 records, in dependency order.

    They are an ordered pair: the second group rewrites text the first one
    wrote, so the first carries a probe on its own payload and reports
    ``already_present`` on the second pass instead of re-deriving anchors the
    partner has already consumed.
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
    ]
