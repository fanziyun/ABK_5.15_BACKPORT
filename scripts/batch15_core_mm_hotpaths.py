"""Batch 15: SLUB alloc/free hotpath + anonymous THP fault-alloc fastpath.

Two groups, absorbed from ``ABK_ABI_PATCH_SUITE`` (feature ids
``slab_alloc_free_hotpath`` and ``hugepage_fault_alloc_fastpath``).  They are
the suite's two *optimization* inventory items that live in mm; Batch 15 owns
that suite's optimization inventory (see "Suite absorption" in
``docs/porting_policy.md``), so they are ported here as registry groups instead
of relying on the sibling module.

Provenance (read as real Python source, not from memory):

* ``ABK_ABI_PATCH_SUITE/scripts/abk_feature_porting.py``
  - ``patch_slab_alloc_free_hotpath()`` -- lines 2519-2653, group
    ``slab_alloc_free_hotpath``, targets ``mm/slub.c``.
  - ``patch_hugepage_fault_alloc_fastpath()`` -- lines 2655-2772, group
    ``hugepage_fault_alloc_fastpath``, targets ``mm/huge_memory.c`` and
    ``mm/memory.c``.
  - the suite's own ``PatchGroup(...)`` declarations for these two ids are at
    lines 77-84 and state the intended summaries verbatim:
    "Tighten the 6.1 SLUB alloc/free hotpath with a shared bulk-free backend,
    free-side validation split, and bulk alloc prefetch." and "Tighten
    anonymous THP fault-time allocation into a file-local helper split that
    keeps THP fault fallback tracking and PMD fault routing intact."
  - ``collect_slab_alloc_free_hotpath_status()`` (3112-3150) and
    ``collect_hugepage_fault_alloc_fastpath_status()`` (3152-3216) are the
    scope limits, not just reporters.  Their ``next_action`` strings are the
    fenced-off territory and are quoted in "NOT ported" below.

The one thing fixed on the way in: the suite guards every hunk with
``if old in text: text = text.replace(old, new, 1)``, which is a silent no-op
on an anchor miss that still reports ``mode: patched``/``already_patched``.
That is the exact failure mode this repo rejects, so **every step below is
emitted with ``required=True``**: a miss aborts the group transactionally
(``apply_steps`` writes nothing) and reports
``blocked_by_missing_anchor``/``blocked_by_shape`` instead of a green no-op.

Callee audit against the 5.15 tree (this is the repo's pinned bug class --
"compiles clean, behaves like the source kernel").  Every function the ported
C calls was grepped for its *real* 5.15 definition; the ones whose 5.15
semantics differ from what the suite's 6.1-shaped code assumes:

* ``khugepaged_enter(vma, vm_flags)`` -- 5.15 returns ``int`` and reports
  failure as non-zero (``include/linux/khugepaged.h``:56 / :77, both
  ``static inline int``), so ``abk_thp_fault_prepare()`` propagates it as
  ``VM_FAULT_OOM`` exactly the way the pristine entry point does.  From 6.1 it
  is ``khugepaged_enter_vma(vma, flags)`` returning ``void`` -- copying the
  6.1 helper would have dropped the error path.  The suite's own
  ``helper_block_5_15`` already carries the 5.15 form; verified, not assumed.
* ``mem_cgroup_charge(page, mm, gfp)`` -- 5.15 returns ``int``, 0 on success
  (``include/linux/memcontrol.h``:710 and the CONFIG_MEMCG=n stub at :1241).
  Same convention as the suite assumes; unchanged.
* ``page_add_new_anon_rmap(page, vma, haddr, /*compound=*/true)`` -- the 5.15
  prototype is the four-argument ``bool`` form
  (``include/linux/rmap.h``:195), which is what the pristine
  ``__do_huge_pmd_anonymous_page()`` already calls.  Unchanged.
* ``alloc_hugepage_vma()`` / ``prep_transhuge_page()`` instead of the suite's
  ``vma_alloc_folio()`` -- 5.15 is pre-folio (5.16).  ``alloc_hugepage_vma``
  is a macro (``include/linux/gfp.h``:613) expanding to
  ``alloc_pages_vma(mask, order, vma, addr, numa_node_id(), true)``, and
  ``vma_thp_gfp_mask()`` is declared ``extern`` at ``include/linux/gfp.h``:693
  while being *defined* at ``mm/huge_memory.c``:682 -- i.e. after the point the
  suite inserts its helper block.  The insert point is therefore safe, and
  ``set_huge_zero_page()`` (defined at :709, static, no header prototype) needs
  the forward declaration the suite ships.  Kept.
* ``slab_free()`` -- 5.15 has **no** ``struct slab`` (5.17) and takes six
  arguments: ``slab_free(s, struct page *page, void *head, void *tail, int cnt,
  unsigned long addr)`` (``mm/slub.c``:3513), with no object tail pointer.  The
  suite's "free-side validation split" is written in ``struct slab`` terms with
  the 7-argument 6.1 call; it ports as hoisting ``virt_to_head_page(x)`` above
  ``cache_from_obj()`` and passing that ``struct page *`` on.  Same single-
  resolution split, the 5.15 type.
* ``get_freepointer()`` vs ``get_freepointer_safe()`` -- the pristine
  ``kmem_cache_alloc_bulk()`` reads the next freepointer with the unchecked
  ``get_freepointer()``; the shared helper uses ``get_freepointer_safe()``.
  The two are identical unless ``debug_pagealloc`` is active, where the safe
  form is a strict refinement (it uses ``copy_from_kernel_nofault``).  This is
  the suite's own choice and is kept.
* ``hugepage_vma_revalidate()`` -- the repo's pinned convention trap
  (0 == success on 5.15, ``SCAN_SUCCEED`` == 1 from 6.1).  **Not called** by
  either ported feature: the suite's fault fastpath never revalidates the vma,
  so nothing here depends on its convention.

Behavioural notes on the ported code (differences that are deliberate and
already upstream in the suite, recorded so they are not "fixed" later):

* ``THP_FAULT_ALLOC`` / ``count_memcg_event_mm(THP_FAULT_ALLOC)`` move from
  after ``spin_unlock(vmf->ptl)`` in the pristine function into
  ``abk_map_anon_page_pmd()``, i.e. they are now emitted while the PMD lock is
  still held.  Both are per-CPU counter bumps, so this is safe, and the event
  still fires exactly once per successfully mapped anonymous PMD.
* ``VM_BUG_ON_PAGE(!PageCompound(page), page)`` ends up in *two* places:
  ``abk_thp_fault_alloc_page()`` (replacing the pristine entry point's
  post-``prep_transhuge_page()`` call) and ``__do_huge_pmd_anonymous_page()``
  (unchanged, as in the pristine function).  It is a
  ``CONFIG_DEBUG_VM``-only assertion and holds at both sites; the suite's
  arrangement is kept rather than deduplicated so the helper split stays
  line-for-line comparable with the suite.
* the suite inserts the helper set immediately after
  ``EXPORT_SYMBOL_GPL(thp_get_unmapped_area);``, which is *above*
  ``vma_thp_gfp_mask()``'s definition in this file.  That is only safe because
  ``include/linux/gfp.h``:693 declares it ``extern`` -- verified in the 5.15
  tree.  The one symbol without a header prototype,
  ``set_huge_zero_page()``, is forward declared inside the graft.

Deliberately NOT ported (recorded so it is not relitigated):

* ``build_detached_freelist()``'s "resolve the slab handle once" half of the
  free-side split.  The 6.1 code the suite collapses does a two-step lookup
  (``virt_to_folio()`` then ``folio_slab()``) and stores ``df->slab``; on 5.15
  ``struct detached_freelist`` has no ``slab`` member at all and the function
  already derives ``page = virt_to_head_page(object)`` exactly once
  (``mm/slub.c``:3594).  There is nothing to collapse, which is precisely the
  branch the suite itself takes ("this tree predates folios and already derives
  the slab handle in one step, nothing to collapse", lines 2617-2625).  No step
  is emitted, and ``mm/slub.c``'s pristine occurrence count is unchanged.
* the suite's 6.1-only arms: ``kmem_cache_free_old``/``_new`` (the
  ``&x`` tail pointer) and ``build_detached_old``/``_new`` (``df->slab``,
  ``free_large_kmalloc()``, ``struct folio``).  Unreachable on 5.15.
* a "shared bulk-free backend" edit to ``kmem_cache_free_bulk()`` /
  ``__kmem_cache_free_bulk()``.  The suite makes none either -- 5.15's
  ``kmem_cache_free_bulk()`` already routes through ``build_detached_freelist()``
  -- so the summary's phrase is satisfied by the two helpers that *are* shared
  (``abk_slab_next_object()`` across the single and bulk alloc paths,
  ``get_freepointer_safe()`` semantics on both).
* ``mm/vmstat.c``.  The suite's status collector *reads* it for the
  ``"thp_fault_alloc"`` / ``"thp_fault_fallback"`` strings; it is never edited,
  and ``count_vm_event(THP_FAULT_*)`` / ``count_memcg_event_mm()`` accounting
  is entirely inside ``mm/huge_memory.c``.  Not a step target.
* everything the suite's two ``next_action`` strings fence off.  For
  ``slab_alloc_free_hotpath``: "Keep slab_alloc_free_hotpath inside mm/slub.c
  and stop at helper grafts.  Do not widen this batch into sheaf/barn
  structural ports unless a later phase justifies broader allocator layout
  changes."  For ``hugepage_fault_alloc_fastpath``: "Keep
  hugepage_fault_alloc_fastpath on the anonymous THP fault-time path only.  Do
  not widen this batch into khugepaged collapse, compaction, split/recovery, or
  memcg policy rewrites unless the direct PMD fault helper split stops fitting
  the 6.1 tree."  Both are honoured: no allocator layout/sheaf model, no
  khugepaged collapse, no compaction, no split/recovery, no memcg policy, no
  full THP policy rewrite.
* the suite's ``mode: already_patched`` short-circuits.  They are replaced by
  required steps plus a ``ctx.suite_touched()`` -> ``skip_suite_processed``
  compatibility probe, which is this repo's sanctioned way to yield to the
  sibling module (``abk_stable_core.py``'s fdtable group does the same).
  ABK_ABI_PATCH_SUITE must not be co-injected with a Batch 15 build anyway.

Marker policy: every module-introduced line carries
``/* ABK stable_515_backport: <subject> */``; the suite's
``/* ABK feature_porting: ... */`` markers are NOT reproduced (they are the
sibling module's namespace and would make ``ctx.suite_touched()`` lie).  The
two additions that are pure upstream-shape rewrites of an existing function
body (``__do_huge_pmd_anonymous_page()`` and the fault entry) keep an
explanatory marker comment inside the function header instead of on each
rewritten line, so the group is still identifiable after a rollback-less graft.

Anchor overlap with the existing registry: **none**.  ``mm/slub.c``,
``mm/huge_memory.c`` and ``mm/memory.c`` are not referenced by any ``PatchGroup``
in ``abk_stable_core.py``, ``abk_stable_perf.py``, ``abk_stable_display.py`` or
``scripts/batch10_*``..``batch14_*``.  The THP / collapse groups ``AGENTS.md``
mentions by subsystem live in ``mm/khugepaged.c`` and ``mm/madvise.c``
(``madvise_collapse``) and in ``mm/internal.h`` + ``mm/page_alloc.c``
(``free_area_*``, ``__rmqueue_pcplist``); none of them touches the three files
this batch edits, and none of the anchor strings below appears in any other
batch module.  Registration order is therefore free: nothing here consumes text
another group produces, and no other group consumes text this one produces.

Anchor verification: every ``old`` block below was asserted to occur **exactly
once** in its target file in all four pristine reference trees
(``abk515_ref_167`` / ``_178`` / ``_194`` / ``_211``, SUBLEVELs 167/178/194/216),
and every ``new`` block was asserted absent from the pristine file and not a
verbatim substring of any earlier step's ``new`` in the same group (the
step-audit traps 1 and 2).
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

SLUB_C = "mm/slub.c"
HUGE_C = "mm/huge_memory.c"
MEMORY_C = "mm/memory.c"

# Alias the required-flag for readability (the other batch modules use T/F).
T = True

# ---------------------------------------------------------------------------
# Group 1: slab_alloc_free_hotpath -- mm/slub.c
# ---------------------------------------------------------------------------
# All five anchors were checked to occur exactly once per reference tree.  The
# suite's ``build_detached_freelist()`` hunk is absent on purpose (see the
# module docstring): the 5.15 function already resolves its page handle once.
#
# Ordering note: the bulk steps come *after* the helper insert and the
# single-object step.  No step's ``new`` is a substring of another's (checked),
# so the order is not load-bearing, but keeping the helper first is what makes
# the later call sites readable in a two-pass diff.

# mm/slub.c: the doc comment that precedes slab_alloc_node().  The helper is
# inserted in front of it and the anchor is re-emitted at the tail of ``new``
# (a superset, never a truncation -- see group_recipe trap 1).
_A_HELPER = (
    "/*\n"
    " * Inlined fastpath so that allocation functions (kmalloc, kmem_cache_alloc)\n"
)

_A_HELPER_NEW = (
    "/*\n"
    " * ABK stable_515_backport: slab_alloc_free_hotpath helper graft.\n"
    " *\n"
    " * One next-object helper for the single-object fastpath and for\n"
    " * kmem_cache_alloc_bulk(), so both issue the freepointer prefetch.  The\n"
    " * single-object path keeps its own post-cmpxchg prefetch_freepointer()\n"
    " * call below: that one is a no-op once the line is resident, and the\n"
    " * point of the graft is the earlier prefetch -- the next object is warmed\n"
    " * while the cmpxchg_double above still validates tid and freelist.\n"
    " */\n"
    "static __always_inline void *abk_slab_next_object(struct kmem_cache *s,\n"
    "\t\t\t\t\t      void *object)\n"
    "{\n"
    "\tvoid *next_object = get_freepointer_safe(s, object);\n"
    "\n"
    "\tprefetch_freepointer(s, next_object);\n"
    "\treturn next_object;\n"
    "}\n"
    "\n"
    "/*\n"
    " * Inlined fastpath so that allocation functions (kmalloc, kmem_cache_alloc)\n"
)

# mm/slub.c slab_alloc_node(): the single-object fastpath's next-object read.
_A_ALLOC_FAST = (
    "\t} else {\n"
    "\t\tvoid *next_object = get_freepointer_safe(s, object);\n"
    "\n"
    "\t\t/*\n"
    "\t\t * The cmpxchg will only match if there was no additional\n"
    "\t\t * operation and if we are on the right processor.\n"
)

_A_ALLOC_FAST_NEW = (
    "\t} else {\n"
    "\t\t/*\n"
    "\t\t * ABK stable_515_backport: slab_alloc_free_hotpath -- the same helper\n"
    "\t\t * kmem_cache_alloc_bulk() uses, which prefetches the next freelist\n"
    "\t\t * entry (the post-cmpxchg prefetch below is kept for the unmodified\n"
    "\t\t * upstream shape).\n"
    "\t\t */\n"
    "\t\tvoid *next_object = abk_slab_next_object(s, object);\n"
    "\n"
    "\t\t/*\n"
    "\t\t * The cmpxchg will only match if there was no additional\n"
    "\t\t * operation and if we are on the right processor.\n"
)

# mm/slub.c kmem_cache_alloc_bulk(): hoist the loop-carried temporary to the
# function head.  5.15's 6-argument slab_free()/slab_alloc() split means the
# bulk path has no ``struct slab *'' to thread, only this one local.
_A_BULK_DECL = (
    "int kmem_cache_alloc_bulk(struct kmem_cache *s, gfp_t flags, size_t size,\n"
    "\t\t\t  void **p)\n"
    "{\n"
    "\tstruct kmem_cache_cpu *c;\n"
)

_A_BULK_DECL_NEW = (
    "int kmem_cache_alloc_bulk(struct kmem_cache *s, gfp_t flags, size_t size,\n"
    "\t\t\t  void **p)\n"
    "{\n"
    "\tvoid *next_object; /* ABK stable_515_backport: slab_alloc_free_hotpath */\n"
    "\tstruct kmem_cache_cpu *c;\n"
)

# mm/slub.c kmem_cache_alloc_bulk(): advance the per-cpu freelist through the
# shared helper so the next entry is prefetched.
_A_BULK_USE = (
    "\t\tc->freelist = get_freepointer(s, object);\n"
    "\t\tp[i] = object;\n"
    "\t\tmaybe_wipe_obj_freeptr(s, p[i]);\n"
)

_A_BULK_USE_NEW = (
    "\t\t/*\n"
    "\t\t * ABK stable_515_backport: slab_alloc_free_hotpath -- prefetch the next\n"
    "\t\t * freelist entry before advancing the per-cpu freelist.\n"
    "\t\t */\n"
    "\t\tnext_object = abk_slab_next_object(s, object);\n"
    "\t\tc->freelist = next_object;\n"
    "\t\tp[i] = object;\n"
    "\t\tmaybe_wipe_obj_freeptr(s, p[i]);\n"
)

# mm/slub.c kmem_cache_free(): the free-side validation split.  The suite does
# this in struct slab terms (6.1 form, ``slab_free(s, slab, x, NULL, &x, 1,
# _RET_IP_)``); 5.15 has no struct slab and a 6-argument slab_free(), so the
# handle is the same single ``virt_to_head_page()`` the pristine line already
# performed -- just derived before ``s`` is reassigned.
_A_KFREE = (
    "void kmem_cache_free(struct kmem_cache *s, void *x)\n"
    "{\n"
    "\ts = cache_from_obj(s, x);\n"
    "\tif (!s)\n"
    "\t\treturn;\n"
    "\tslab_free(s, virt_to_head_page(x), x, NULL, 1, _RET_IP_);\n"
    "\ttrace_kmem_cache_free(_RET_IP_, x, s->name);\n"
    "}\n"
)

_A_KFREE_NEW = (
    "void kmem_cache_free(struct kmem_cache *s, void *x)\n"
    "{\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: slab_alloc_free_hotpath.  Resolve the slab\n"
    "\t * handle from `x` above cache_from_obj() so it is available before `s`\n"
    "\t * is reassigned to the memcg cache, and reuse it for slab_free()\n"
    "\t * instead of resolving it twice on the way through.  On 5.15 the\n"
    "\t * handle is a struct page -- struct slab is 5.17, so there is exactly\n"
    "\t * one resolution here, not the suite's two-step folio/slab lookup.\n"
    "\t */\n"
    "\tstruct page *page = virt_to_head_page(x);\n"
    "\n"
    "\ts = cache_from_obj(s, x);\n"
    "\tif (!s)\n"
    "\t\treturn;\n"
    "\tslab_free(s, page, x, NULL, 1, _RET_IP_);\n"
    "\ttrace_kmem_cache_free(_RET_IP_, x, s->name);\n"
    "}\n"
)

# ---------------------------------------------------------------------------
# Group 2: hugepage_fault_alloc_fastpath -- mm/huge_memory.c + mm/memory.c
# ---------------------------------------------------------------------------
# The whole group is the suite's 5.15 branch (``helper_block_5_15`` and
# ``do_huge_*_5_15`` / ``fault_entry_new_5_15`` / ``memory_*_5_15``), which the
# suite selects on ``"struct folio" in text``.  Only that branch is shipped
# here, so the page-typed signatures below double as the shape probe: a
# folio-shaped tree fails it and degrades to ``blocked_by_shape`` before any
# step runs (group_recipe trap 4 -- nothing is written, nothing is half-grafted).

# mm/huge_memory.c: thp_get_unmapped_area() is the last function before the
# anonymous-PMD fault block, so the helper set lands immediately above
# __do_huge_pmd_anonymous_page().  set_huge_zero_page() is defined *after* this
# point in the file, hence the forward declaration inside the graft.
_B_HELPERS = "EXPORT_SYMBOL_GPL(thp_get_unmapped_area);\n"

_B_HELPERS_NEW = (
    "EXPORT_SYMBOL_GPL(thp_get_unmapped_area);\n"
    "\n"
    "/*\n"
    " * ABK stable_515_backport: hugepage fault alloc fastpath helper graft.\n"
    " *\n"
    " * 5.15 shape, not the suite's 6.1 shape: there are no folios here (5.16),\n"
    " * so every helper works in struct page, mem_cgroup_charge() takes the\n"
    " * page, and the allocation is alloc_hugepage_vma() + prep_transhuge_page()\n"
    " * rather than vma_alloc_folio().  The decomposition is the same, call for\n"
    " * call: prepare, fallback accounting, allocate, charge, prep and map.\n"
    " *\n"
    " * set_huge_zero_page() is static and defined further down this file (above\n"
    " * do_huge_pmd_anonymous_page()), so it is forward declared here rather than\n"
    " * moved -- moving it would rewrite a pristine upstream block for no gain.\n"
    " */\n"
    "static void set_huge_zero_page(pgtable_t pgtable, struct mm_struct *mm,\n"
    "\t\t\t\t struct vm_area_struct *vma, unsigned long haddr,\n"
    "\t\t\t\t pmd_t *pmd, struct page *zero_page);\n"
    "\n"
    "static vm_fault_t abk_thp_fault_fallback(bool charge)\n"
    "{\n"
    "\tcount_vm_event(THP_FAULT_FALLBACK);\n"
    "\tif (charge)\n"
    "\t\tcount_vm_event(THP_FAULT_FALLBACK_CHARGE);\n"
    "\treturn VM_FAULT_FALLBACK;\n"
    "}\n"
    "\n"
    "/*\n"
    " * 5.15's khugepaged_enter() returns an int error (6.1 folds this into a\n"
    " * void khugepaged_enter_vma()), so it must be propagated, not dropped.\n"
    " */\n"
    "static vm_fault_t abk_thp_fault_prepare(struct vm_fault *vmf,\n"
    "\t\t\t\t       unsigned long haddr)\n"
    "{\n"
    "\tstruct vm_area_struct *vma = vmf->vma;\n"
    "\n"
    "\tif (!transhuge_vma_suitable(vma, haddr))\n"
    "\t\treturn VM_FAULT_FALLBACK;\n"
    "\tif (unlikely(anon_vma_prepare(vma)))\n"
    "\t\treturn VM_FAULT_OOM;\n"
    "\tif (unlikely(khugepaged_enter(vma, vma->vm_flags)))\n"
    "\t\treturn VM_FAULT_OOM;\n"
    "\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
    "static struct page *abk_thp_fault_alloc_page(struct vm_area_struct *vma,\n"
    "\t\t\t\t\t     unsigned long haddr,\n"
    "\t\t\t\t\t     gfp_t *gfp)\n"
    "{\n"
    "\tstruct page *page;\n"
    "\n"
    "\t*gfp = vma_thp_gfp_mask(vma);\n"
    "\tpage = alloc_hugepage_vma(*gfp, vma, haddr, HPAGE_PMD_ORDER);\n"
    "\tif (!page)\n"
    "\t\treturn NULL;\n"
    "\n"
    "\tprep_transhuge_page(page);\n"
    "\tVM_BUG_ON_PAGE(!PageCompound(page), page);\n"
    "\treturn page;\n"
    "}\n"
    "\n"
    "static vm_fault_t abk_thp_fault_charge_page(struct page *page,\n"
    "\t\t\t\t\t    struct vm_area_struct *vma,\n"
    "\t\t\t\t\t    gfp_t gfp)\n"
    "{\n"
    "\tif (mem_cgroup_charge(page, vma->vm_mm, gfp)) {\n"
    "\t\tput_page(page);\n"
    "\t\treturn abk_thp_fault_fallback(true);\n"
    "\t}\n"
    "\n"
    "\tcgroup_throttle_swaprate(page, gfp);\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
    "static void abk_prep_anon_thp_page(struct page *page,\n"
    "\t\t\t\t   unsigned long address)\n"
    "{\n"
    "\tclear_huge_page(page, address, HPAGE_PMD_NR);\n"
    "\t/*\n"
    "\t * The memory barrier inside __SetPageUptodate makes sure that\n"
    "\t * clear_huge_page writes become visible before the set_pmd_at()\n"
    "\t * write.\n"
    "\t */\n"
    "\t__SetPageUptodate(page);\n"
    "}\n"
    "\n"
    "static void abk_map_anon_page_pmd(struct page *page, pgtable_t pgtable,\n"
    "\t\t\t\t  pmd_t *pmd,\n"
    "\t\t\t\t  struct vm_area_struct *vma,\n"
    "\t\t\t\t  unsigned long haddr,\n"
    "\t\t\t\t  unsigned long address)\n"
    "{\n"
    "\tpmd_t entry;\n"
    "\n"
    "\tentry = mk_huge_pmd(page, vma->vm_page_prot);\n"
    "\tentry = maybe_pmd_mkwrite(pmd_mkdirty(entry), vma);\n"
    "\tpage_add_new_anon_rmap(page, vma, haddr, true);\n"
    "\tlru_cache_add_inactive_or_unevictable(page, vma);\n"
    "\tpgtable_trans_huge_deposit(vma->vm_mm, pmd, pgtable);\n"
    "\tset_pmd_at(vma->vm_mm, haddr, pmd, entry);\n"
    "\tupdate_mmu_cache_pmd(vma, address, pmd);\n"
    "\tadd_mm_counter(vma->vm_mm, MM_ANONPAGES, HPAGE_PMD_NR);\n"
    "\tmm_inc_nr_ptes(vma->vm_mm);\n"
    "\tcount_vm_event(THP_FAULT_ALLOC);\n"
    "\tcount_memcg_event_mm(vma->vm_mm, THP_FAULT_ALLOC);\n"
    "}\n"
    "\n"
    "static vm_fault_t abk_do_huge_pmd_anonymous_zero_page(struct vm_fault *vmf,\n"
    "\t\t\t\t\t      unsigned long haddr)\n"
    "{\n"
    "\tstruct vm_area_struct *vma = vmf->vma;\n"
    "\tpgtable_t pgtable;\n"
    "\tstruct page *zero_page;\n"
    "\tvm_fault_t ret;\n"
    "\n"
    "\tpgtable = pte_alloc_one(vma->vm_mm);\n"
    "\tif (unlikely(!pgtable))\n"
    "\t\treturn VM_FAULT_OOM;\n"
    "\tzero_page = mm_get_huge_zero_page(vma->vm_mm);\n"
    "\tif (unlikely(!zero_page)) {\n"
    "\t\tpte_free(vma->vm_mm, pgtable);\n"
    "\t\treturn abk_thp_fault_fallback(false);\n"
    "\t}\n"
    "\tvmf->ptl = pmd_lock(vma->vm_mm, vmf->pmd);\n"
    "\tret = 0;\n"
    "\tif (pmd_none(*vmf->pmd)) {\n"
    "\t\tret = check_stable_address_space(vma->vm_mm);\n"
    "\t\tif (ret) {\n"
    "\t\t\tspin_unlock(vmf->ptl);\n"
    "\t\t\tpte_free(vma->vm_mm, pgtable);\n"
    "\t\t} else if (userfaultfd_missing(vma)) {\n"
    "\t\t\tspin_unlock(vmf->ptl);\n"
    "\t\t\tpte_free(vma->vm_mm, pgtable);\n"
    "\t\t\tret = handle_userfault(vmf, VM_UFFD_MISSING);\n"
    "\t\t\tVM_BUG_ON(ret & VM_FAULT_FALLBACK);\n"
    "\t\t} else {\n"
    "\t\t\tset_huge_zero_page(pgtable, vma->vm_mm, vma,\n"
    "\t\t\t\t\t   haddr, vmf->pmd, zero_page);\n"
    "\t\t\tupdate_mmu_cache_pmd(vma, vmf->address, vmf->pmd);\n"
    "\t\t\tspin_unlock(vmf->ptl);\n"
    "\t\t}\n"
    "\t} else {\n"
    "\t\tspin_unlock(vmf->ptl);\n"
    "\t\tpte_free(vma->vm_mm, pgtable);\n"
    "\t}\n"
    "\treturn ret;\n"
    "}\n"
)

# mm/huge_memory.c __do_huge_pmd_anonymous_page(): charge / prep / map move into
# the helpers.  The THP_FAULT_FALLBACK and THP_FAULT_FALLBACK_CHARGE accounting
# moves *into* abk_thp_fault_charge_page()'s failure arm, so the event pair is
# still emitted exactly once per failed charge.
_B_DO_HUGE = (
    "static vm_fault_t __do_huge_pmd_anonymous_page(struct vm_fault *vmf,\n"
    "\t\t\tstruct page *page, gfp_t gfp)\n"
    "{\n"
    "\tstruct vm_area_struct *vma = vmf->vma;\n"
    "\tpgtable_t pgtable;\n"
    "\tunsigned long haddr = vmf->address & HPAGE_PMD_MASK;\n"
    "\tvm_fault_t ret = 0;\n"
    "\n"
    "\tVM_BUG_ON_PAGE(!PageCompound(page), page);\n"
    "\n"
    "\tif (mem_cgroup_charge(page, vma->vm_mm, gfp)) {\n"
    "\t\tput_page(page);\n"
    "\t\tcount_vm_event(THP_FAULT_FALLBACK);\n"
    "\t\tcount_vm_event(THP_FAULT_FALLBACK_CHARGE);\n"
    "\t\treturn VM_FAULT_FALLBACK;\n"
    "\t}\n"
    "\tcgroup_throttle_swaprate(page, gfp);\n"
    "\n"
    "\tpgtable = pte_alloc_one(vma->vm_mm);\n"
    "\tif (unlikely(!pgtable)) {\n"
    "\t\tret = VM_FAULT_OOM;\n"
    "\t\tgoto release;\n"
    "\t}\n"
    "\n"
    "\tclear_huge_page(page, vmf->address, HPAGE_PMD_NR);\n"
    "\t/*\n"
    "\t * The memory barrier inside __SetPageUptodate makes sure that\n"
    "\t * clear_huge_page writes become visible before the set_pmd_at()\n"
    "\t * write.\n"
    "\t */\n"
    "\t__SetPageUptodate(page);\n"
    "\n"
    "\tvmf->ptl = pmd_lock(vma->vm_mm, vmf->pmd);\n"
    "\tif (unlikely(!pmd_none(*vmf->pmd))) {\n"
    "\t\tgoto unlock_release;\n"
    "\t} else {\n"
    "\t\tpmd_t entry;\n"
    "\n"
    "\t\tret = check_stable_address_space(vma->vm_mm);\n"
    "\t\tif (ret)\n"
    "\t\t\tgoto unlock_release;\n"
    "\n"
    "\t\t/* Deliver the page fault to userland */\n"
    "\t\tif (userfaultfd_missing(vma)) {\n"
    "\t\t\tspin_unlock(vmf->ptl);\n"
    "\t\t\tput_page(page);\n"
    "\t\t\tpte_free(vma->vm_mm, pgtable);\n"
    "\t\t\tret = handle_userfault(vmf, VM_UFFD_MISSING);\n"
    "\t\t\tVM_BUG_ON(ret & VM_FAULT_FALLBACK);\n"
    "\t\t\treturn ret;\n"
    "\t\t}\n"
    "\n"
    "\t\tentry = mk_huge_pmd(page, vma->vm_page_prot);\n"
    "\t\tentry = maybe_pmd_mkwrite(pmd_mkdirty(entry), vma);\n"
    "\t\tpage_add_new_anon_rmap(page, vma, haddr, true);\n"
    "\t\tlru_cache_add_inactive_or_unevictable(page, vma);\n"
    "\t\tpgtable_trans_huge_deposit(vma->vm_mm, vmf->pmd, pgtable);\n"
    "\t\tset_pmd_at(vma->vm_mm, haddr, vmf->pmd, entry);\n"
    "\t\tupdate_mmu_cache_pmd(vma, vmf->address, vmf->pmd);\n"
    "\t\tadd_mm_counter(vma->vm_mm, MM_ANONPAGES, HPAGE_PMD_NR);\n"
    "\t\tmm_inc_nr_ptes(vma->vm_mm);\n"
    "\t\tspin_unlock(vmf->ptl);\n"
    "\t\tcount_vm_event(THP_FAULT_ALLOC);\n"
    "\t\tcount_memcg_event_mm(vma->vm_mm, THP_FAULT_ALLOC);\n"
    "\t}\n"
    "\n"
    "\treturn 0;\n"
    "unlock_release:\n"
    "\tspin_unlock(vmf->ptl);\n"
    "release:\n"
    "\tif (pgtable)\n"
    "\t\tpte_free(vma->vm_mm, pgtable);\n"
    "\tput_page(page);\n"
    "\treturn ret;\n"
    "\n"
    "}\n"
)

_B_DO_HUGE_NEW = (
    "static vm_fault_t __do_huge_pmd_anonymous_page(struct vm_fault *vmf,\n"
    "\t\t\tstruct page *page, gfp_t gfp)\n"
    "{\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: hugepage_fault_alloc_fastpath -- the charge,\n"
    "\t * clear/uptodate prep and PMD map steps are factored into the\n"
    "\t * page-typed helpers above; the OOM, userfaultfd and release paths are\n"
    "\t * unchanged.\n"
    "\t */\n"
    "\tstruct vm_area_struct *vma = vmf->vma;\n"
    "\tpgtable_t pgtable;\n"
    "\tunsigned long haddr = vmf->address & HPAGE_PMD_MASK;\n"
    "\tvm_fault_t ret;\n"
    "\n"
    "\tVM_BUG_ON_PAGE(!PageCompound(page), page);\n"
    "\n"
    "\tret = abk_thp_fault_charge_page(page, vma, gfp);\n"
    "\tif (ret)\n"
    "\t\treturn ret;\n"
    "\n"
    "\tpgtable = pte_alloc_one(vma->vm_mm);\n"
    "\tif (unlikely(!pgtable)) {\n"
    "\t\tret = VM_FAULT_OOM;\n"
    "\t\tgoto release;\n"
    "\t}\n"
    "\n"
    "\tabk_prep_anon_thp_page(page, vmf->address);\n"
    "\n"
    "\tvmf->ptl = pmd_lock(vma->vm_mm, vmf->pmd);\n"
    "\tif (unlikely(!pmd_none(*vmf->pmd)))\n"
    "\t\tgoto unlock_release;\n"
    "\n"
    "\tret = check_stable_address_space(vma->vm_mm);\n"
    "\tif (ret)\n"
    "\t\tgoto unlock_release;\n"
    "\n"
    "\t/* Deliver the page fault to userland */\n"
    "\tif (userfaultfd_missing(vma)) {\n"
    "\t\tspin_unlock(vmf->ptl);\n"
    "\t\tput_page(page);\n"
    "\t\tpte_free(vma->vm_mm, pgtable);\n"
    "\t\tret = handle_userfault(vmf, VM_UFFD_MISSING);\n"
    "\t\tVM_BUG_ON(ret & VM_FAULT_FALLBACK);\n"
    "\t\treturn ret;\n"
    "\t}\n"
    "\n"
    "\tabk_map_anon_page_pmd(page, pgtable, vmf->pmd, vma,\n"
    "\t\t\t      haddr, vmf->address);\n"
    "\tspin_unlock(vmf->ptl);\n"
    "\treturn 0;\n"
    "\n"
    "unlock_release:\n"
    "\tspin_unlock(vmf->ptl);\n"
    "release:\n"
    "\tif (pgtable)\n"
    "\t\tpte_free(vma->vm_mm, pgtable);\n"
    "\tput_page(page);\n"
    "\treturn ret;\n"
    "\n"
    "}\n"
)

# mm/huge_memory.c do_huge_pmd_anonymous_page(): the zero-page branch becomes a
# helper call, the vma checks/alloc/fallback become helper calls.  The
# zero-page arm's THP_FAULT_FALLBACK accounting moves into
# abk_thp_fault_fallback(false) inside the helper -- still once per event.
_B_ENTRY = (
    "vm_fault_t do_huge_pmd_anonymous_page(struct vm_fault *vmf)\n"
    "{\n"
    "\tstruct vm_area_struct *vma = vmf->vma;\n"
    "\tgfp_t gfp;\n"
    "\tstruct page *page;\n"
    "\tunsigned long haddr = vmf->address & HPAGE_PMD_MASK;\n"
    "\n"
    "\tif (!transhuge_vma_suitable(vma, haddr))\n"
    "\t\treturn VM_FAULT_FALLBACK;\n"
    "\tif (unlikely(anon_vma_prepare(vma)))\n"
    "\t\treturn VM_FAULT_OOM;\n"
    "\tif (unlikely(khugepaged_enter(vma, vma->vm_flags)))\n"
    "\t\treturn VM_FAULT_OOM;\n"
    "\tif (!(vmf->flags & FAULT_FLAG_WRITE) &&\n"
    "\t\t\t!mm_forbids_zeropage(vma->vm_mm) &&\n"
    "\t\t\ttransparent_hugepage_use_zero_page()) {\n"
    "\t\tpgtable_t pgtable;\n"
    "\t\tstruct page *zero_page;\n"
    "\t\tvm_fault_t ret;\n"
    "\t\tpgtable = pte_alloc_one(vma->vm_mm);\n"
    "\t\tif (unlikely(!pgtable))\n"
    "\t\t\treturn VM_FAULT_OOM;\n"
    "\t\tzero_page = mm_get_huge_zero_page(vma->vm_mm);\n"
    "\t\tif (unlikely(!zero_page)) {\n"
    "\t\t\tpte_free(vma->vm_mm, pgtable);\n"
    "\t\t\tcount_vm_event(THP_FAULT_FALLBACK);\n"
    "\t\t\treturn VM_FAULT_FALLBACK;\n"
    "\t\t}\n"
    "\t\tvmf->ptl = pmd_lock(vma->vm_mm, vmf->pmd);\n"
    "\t\tret = 0;\n"
    "\t\tif (pmd_none(*vmf->pmd)) {\n"
    "\t\t\tret = check_stable_address_space(vma->vm_mm);\n"
    "\t\t\tif (ret) {\n"
    "\t\t\t\tspin_unlock(vmf->ptl);\n"
    "\t\t\t\tpte_free(vma->vm_mm, pgtable);\n"
    "\t\t\t} else if (userfaultfd_missing(vma)) {\n"
    "\t\t\t\tspin_unlock(vmf->ptl);\n"
    "\t\t\t\tpte_free(vma->vm_mm, pgtable);\n"
    "\t\t\t\tret = handle_userfault(vmf, VM_UFFD_MISSING);\n"
    "\t\t\t\tVM_BUG_ON(ret & VM_FAULT_FALLBACK);\n"
    "\t\t\t} else {\n"
    "\t\t\t\tset_huge_zero_page(pgtable, vma->vm_mm, vma,\n"
    "\t\t\t\t\t\t   haddr, vmf->pmd, zero_page);\n"
    "\t\t\t\tupdate_mmu_cache_pmd(vma, vmf->address, vmf->pmd);\n"
    "\t\t\t\tspin_unlock(vmf->ptl);\n"
    "\t\t\t}\n"
    "\t\t} else {\n"
    "\t\t\tspin_unlock(vmf->ptl);\n"
    "\t\t\tpte_free(vma->vm_mm, pgtable);\n"
    "\t\t}\n"
    "\t\treturn ret;\n"
    "\t}\n"
    "\tgfp = vma_thp_gfp_mask(vma);\n"
    "\tpage = alloc_hugepage_vma(gfp, vma, haddr, HPAGE_PMD_ORDER);\n"
    "\tif (unlikely(!page)) {\n"
    "\t\tcount_vm_event(THP_FAULT_FALLBACK);\n"
    "\t\treturn VM_FAULT_FALLBACK;\n"
    "\t}\n"
    "\tprep_transhuge_page(page);\n"
    "\treturn __do_huge_pmd_anonymous_page(vmf, page, gfp);\n"
    "}\n"
)

_B_ENTRY_NEW = (
    "vm_fault_t do_huge_pmd_anonymous_page(struct vm_fault *vmf)\n"
    "{\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: hugepage_fault_alloc_fastpath -- the vma\n"
    "\t * checks, the huge zero page branch, the allocation and the fallback\n"
    "\t * accounting all live in the file-local helpers above.  The THP\n"
    "\t * fault-fallback events and the PMD fault routing are unchanged.\n"
    "\t */\n"
    "\tstruct vm_area_struct *vma = vmf->vma;\n"
    "\tgfp_t gfp;\n"
    "\tstruct page *page;\n"
    "\tunsigned long haddr = vmf->address & HPAGE_PMD_MASK;\n"
    "\tvm_fault_t ret;\n"
    "\n"
    "\tret = abk_thp_fault_prepare(vmf, haddr);\n"
    "\tif (ret)\n"
    "\t\treturn ret;\n"
    "\n"
    "\tif (!(vmf->flags & FAULT_FLAG_WRITE) &&\n"
    "\t\t\t!mm_forbids_zeropage(vma->vm_mm) &&\n"
    "\t\t\ttransparent_hugepage_use_zero_page())\n"
    "\t\treturn abk_do_huge_pmd_anonymous_zero_page(vmf, haddr);\n"
    "\n"
    "\tpage = abk_thp_fault_alloc_page(vma, haddr, &gfp);\n"
    "\tif (unlikely(!page))\n"
    "\t\treturn abk_thp_fault_fallback(false);\n"
    "\treturn __do_huge_pmd_anonymous_page(vmf, page, gfp);\n"
    "}\n"
)

# mm/memory.c create_huge_pmd(): the anonymous arm routes through a file-local
# helper.  5.15 has no per-VMA lock, so there is no FAULT_FLAG_VMA_LOCK /
# vma_end_read() arm to preserve (that shape is 6.1's).
_B_MEMORY = (
    "static inline vm_fault_t create_huge_pmd(struct vm_fault *vmf)\n"
    "{\n"
    "\tif (vma_is_anonymous(vmf->vma))\n"
    "\t\treturn do_huge_pmd_anonymous_page(vmf);\n"
    "\tif (vmf->vma->vm_ops->huge_fault)\n"
    "\t\treturn vmf->vma->vm_ops->huge_fault(vmf, PE_SIZE_PMD);\n"
    "\treturn VM_FAULT_FALLBACK;\n"
    "}\n"
)

_B_MEMORY_NEW = (
    "/* ABK stable_515_backport: hugepage fault alloc fastpath routing helper. */\n"
    "static inline vm_fault_t abk_create_anonymous_huge_pmd(struct vm_fault *vmf)\n"
    "{\n"
    "\treturn do_huge_pmd_anonymous_page(vmf);\n"
    "}\n"
    "\n"
    "static inline vm_fault_t create_huge_pmd(struct vm_fault *vmf)\n"
    "{\n"
    "\tif (vma_is_anonymous(vmf->vma))\n"
    "\t\treturn abk_create_anonymous_huge_pmd(vmf);\n"
    "\tif (vmf->vma->vm_ops->huge_fault)\n"
    "\t\treturn vmf->vma->vm_ops->huge_fault(vmf, PE_SIZE_PMD);\n"
    "\treturn VM_FAULT_FALLBACK;\n"
    "}\n"
)


def build_slab_alloc_free_steps():
    """``slab_alloc_free_hotpath``: five required steps, all in mm/slub.c.

    1. the shared next-object helper (with the freepointer prefetch),
    2. the single-object fastpath call site,
    3. the bulk path's loop-carried temporary,
    4. the bulk fastpath call site,
    5. the free-side single resolution of the slab handle.

    They are one group because 2 and 4 are the users of 1 -- a tree that has the
    helper but not its users (or the reverse) would compile only by accident.
    The suite's ``build_detached_freelist()`` hunk is deliberately absent: on
    5.15 that function already resolves its handle once, so there is nothing to
    rewrite (see the module docstring).
    """
    return [
        (SLUB_C, _A_HELPER, _A_HELPER_NEW, T),
        (SLUB_C, _A_ALLOC_FAST, _A_ALLOC_FAST_NEW, T),
        (SLUB_C, _A_BULK_DECL, _A_BULK_DECL_NEW, T),
        (SLUB_C, _A_BULK_USE, _A_BULK_USE_NEW, T),
        (SLUB_C, _A_KFREE, _A_KFREE_NEW, T),
    ]


def build_hugepage_fault_alloc_steps():
    """``hugepage_fault_alloc_fastpath``: four required steps, two files.

    1. the helper set inserted above ``__do_huge_pmd_anonymous_page()``,
    2. that function's charge / prep / map decomposition,
    3. the fault entry's prepare / zero-page / alloc / fallback decomposition,
    4. ``mm/memory.c``'s anonymous huge-PMD routing helper.

    Step 3 must land with 1 and 2 (it calls four of the helpers) and step 4
    must land with 1 (``mm/memory.c`` only wraps the public entry, but the
    group is one feature -- a half-applied split is not a valid tree).  None of
    the four ``new`` blocks contains another's, so the order is not
    load-bearing; the suite's order is kept.
    """
    return [
        (HUGE_C, _B_HELPERS, _B_HELPERS_NEW, T),
        (HUGE_C, _B_DO_HUGE, _B_DO_HUGE_NEW, T),
        (HUGE_C, _B_ENTRY, _B_ENTRY_NEW, T),
        (MEMORY_C, _B_MEMORY, _B_MEMORY_NEW, T),
    ]


def _slab_alloc_free_apply(ctx):
    try:
        text = ctx.read(SLUB_C)
    except FileNotFoundError:
        return "blocked_by_shape", f"{SLUB_C}: file absent"
    # Compatibility: the sibling suite grafts the same two functions under its
    # own ``/* ABK feature_porting: `` namespace.  Yield to it rather than
    # stacking a second helper on top (the two modules must not be co-injected;
    # this is the graceful path, not the expected one).
    if ctx.suite_touched(SLUB_C):
        return ("skip_suite_processed",
                f"{SLUB_C} already carries ABK_ABI_PATCH_SUITE markers")
    # Shape probe: the 5.15-GKI SLUB surface this group edits.  The
    # slab_alloc_node() probe pins the five-argument 5.15 form (6.1 threads a
    # ``struct list_lru *lru`` through as a sixth), and the other three are the
    # suite's own ensure_contains() set, so a crippled tree degrades here
    # instead of matching some other block.  All four are signatures this
    # group's replacements preserve verbatim, so they still hold on a second
    # pass -- if one ever stopped holding after grafting, the group would
    # report blocked_by_shape on every re-run (group_recipe trap 4), which the
    # two-pass engine simulation guards against.
    for probe, why in (
        ("static __always_inline void *slab_alloc_node(struct kmem_cache *s,",
         "slab_alloc_node()"),
        ("int kmem_cache_alloc_bulk(struct kmem_cache *s, gfp_t flags, size_t size,",
         "kmem_cache_alloc_bulk()"),
        ("void kmem_cache_free(struct kmem_cache *s, void *x)", "kmem_cache_free()"),
        ("static __always_inline void maybe_wipe_obj_freeptr(struct kmem_cache *s,",
         "maybe_wipe_obj_freeptr()"),
    ):
        if probe not in text:
            return "blocked_by_shape", f"{why} not found in {SLUB_C}"
    status, _results, detail = apply_steps(ctx, build_slab_alloc_free_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _hugepage_fault_alloc_apply(ctx):
    try:
        huge_text = ctx.read(HUGE_C)
        memory_text = ctx.read(MEMORY_C)
    except FileNotFoundError as exc:
        return "blocked_by_shape", f"target file absent: {exc}"
    if ctx.suite_touched(HUGE_C) or ctx.suite_touched(MEMORY_C):
        return ("skip_suite_processed",
                f"{HUGE_C}/{MEMORY_C} already carry ABK_ABI_PATCH_SUITE markers")
    # Shape probe: assert both files are the pre-folio 5.15 shape this port is
    # written for *before* any step runs, so a folio-shaped (6.1+) tree cannot
    # get half a graft.  The suite selects between its two variants on
    # "struct folio"; only the 5.15 variant is shipped here, so the
    # discriminators are the page-typed helpers and the pre-per-VMA-lock
    # create_huge_pmd() body.  Each probe is a *signature* the graft preserves
    # verbatim (the suite's new blocks keep every prototype and the
    # EXPORT_SYMBOL line), so all of them hold on a second pass too -- a
    # pristine-only probe here would strand the second pass on
    # blocked_by_shape instead of reaching apply_steps' already_present
    # (group_recipe trap 4).
    for probe, why in (
        ("EXPORT_SYMBOL_GPL(thp_get_unmapped_area);\n",
         "thp_get_unmapped_area() (helper insert point)"),
        ("static vm_fault_t __do_huge_pmd_anonymous_page(struct vm_fault *vmf,\n"
         "\t\t\tstruct page *page, gfp_t gfp)\n{",
         "the page-typed __do_huge_pmd_anonymous_page() (5.15 shape)"),
        ("vm_fault_t do_huge_pmd_anonymous_page(struct vm_fault *vmf)\n{",
         "do_huge_pmd_anonymous_page()"),
    ):
        if probe not in huge_text:
            return "blocked_by_shape", f"{why} not found in {HUGE_C}"
    # mm/memory.c: the pristine anonymous arm *or* this group's grafted arm.
    # The body line is the only thing the group rewrites, so a probe that
    # matched the pristine form alone would strand the second pass on
    # blocked_by_shape instead of letting apply_steps report already_present
    # (group_recipe trap 4 -- caught by the two-pass engine simulation, not by
    # the per-step anchor audit).  Neither form present means a shape this port
    # does not know, e.g. a 6.1 tree whose body carries the
    # FAULT_FLAG_VMA_LOCK / vma_end_read() arm.
    if not (
        "\tif (vma_is_anonymous(vmf->vma))\n"
        "\t\treturn do_huge_pmd_anonymous_page(vmf);" in memory_text
        or "\t\treturn abk_create_anonymous_huge_pmd(vmf);" in memory_text
    ):
        return ("blocked_by_shape",
                "the 5.15 create_huge_pmd() body (no FAULT_FLAG_VMA_LOCK arm) "
                f"not found in {MEMORY_C}")
    status, _results, detail = apply_steps(
        ctx, build_hugepage_fault_alloc_steps()
    )
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the two PatchGroup records, in registration order."""
    return [
        PatchGroup(
            "slab_alloc_free_hotpath",
            "share one next-object helper (with freepointer prefetch) between "
            "the single-object and bulk SLUB allocation fastpaths, and resolve "
            "the slab handle once in kmem_cache_free() before cache_from_obj() "
            "reassigns the cache (5.15 page-typed adaptation of "
            "ABK_ABI_PATCH_SUITE's slab_alloc_free_hotpath)",
            [
                "ABK_ABI_PATCH_SUITE slab_alloc_free_hotpath "
                "(patch_slab_alloc_free_hotpath, lines 2519-2653)",
                "no upstream commit: the suite's own mm/slub.c hotpath graft, "
                "re-anchored onto the 5.15 6-argument slab_free() shape",
            ],
            [SLUB_C],
            _slab_alloc_free_apply,
        ),
        PatchGroup(
            "hugepage_fault_alloc_fastpath",
            "split anonymous THP fault-time allocation into file-local "
            "prepare / fallback / alloc / charge / prep / map helpers and route "
            "mm/memory.c's anonymous huge-PMD branch through its own helper, "
            "keeping THP fault-fallback accounting and PMD fault routing intact "
            "(5.15 page-typed adaptation of ABK_ABI_PATCH_SUITE's "
            "hugepage_fault_alloc_fastpath)",
            [
                "ABK_ABI_PATCH_SUITE hugepage_fault_alloc_fastpath "
                "(patch_hugepage_fault_alloc_fastpath, lines 2655-2772)",
                "no upstream commit: the suite's own file-local helper split, "
                "shipped in its 5.15 branch (page-typed, khugepaged_enter() "
                "return value preserved)",
            ],
            [HUGE_C, MEMORY_C],
            _hugepage_fault_alloc_apply,
        ),
    ]
