from __future__ import annotations

from abk_backport_engine import apply_steps

SLUB_C = "mm/slub.c"
HUGE_C = "mm/huge_memory.c"
MEMORY_C = "mm/memory.c"

T = True


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
    return [
        (SLUB_C, _A_HELPER, _A_HELPER_NEW, T),
        (SLUB_C, _A_ALLOC_FAST, _A_ALLOC_FAST_NEW, T),
        (SLUB_C, _A_BULK_DECL, _A_BULK_DECL_NEW, T),
        (SLUB_C, _A_BULK_USE, _A_BULK_USE_NEW, T),
        (SLUB_C, _A_KFREE, _A_KFREE_NEW, T),
    ]


def build_hugepage_fault_alloc_steps():
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
    if ctx.suite_touched(SLUB_C):
        return ("skip_suite_processed",
                f"{SLUB_C} already carries ABK_ABI_PATCH_SUITE markers")
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
