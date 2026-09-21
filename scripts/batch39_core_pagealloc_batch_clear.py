"""Batch 39: v7.2 mm/page_alloc batch page clearing.

Survey source: ``docs/survey_7_2_mm_reclaim.md`` §1d.  One group, grafting
mainline ``b001cf7d16dd`` (Hrushikesh Salunke, v7.2) into 5.15's page-shaped
``kernel_init_free_pages()``.

Upstream's change.  With ``init_on_alloc`` enabled, ``kernel_init_pages()``
cleared every page one at a time through ``clear_highpage_kasan_tagged()``,
paying a ``kmap_local_page()``/``kunmap_local()`` round trip per page and
denying the architecture's clearing primitive a contiguous range.  Upstream
replaced it with a single ``clear_pages()`` over the whole allocation on
!HIGHMEM and kept the per-page loop for HIGHMEM, where the pages really do
need a kmap.  Reported on 8192 x 2 MB HugeTLB (16 GB) with ``init_on_alloc=1``:
0.445 s -> 0.166 s (-62.7%); Graph500 kernel time -50.3% (64C128T) /
-39.0% (16C32T).  Those are server numbers and are not claimed here.

Why the 5.15 target shape differs from upstream's.  Two things, both checked
against the 5.15.194 reference tree before writing the anchor:

* 5.15 has **no** ``clear_pages()``.  Its only range-looking primitive,
  ``clear_huge_page()`` (declared ``include/linux/mm.h:3266``, defined
  ``mm/memory.c:5899``), is *not* a contiguous clear -- it funnels into
  ``process_huge_page()``/``clear_subpage()`` and still issues one
  ``clear_user_highpage()`` per subpage.  So the batch primitive has to be
  spelled inline; upstream's own author note ("move
  clear_highpages_kasan_tagged() to page_alloc.c") does the same.
* 5.15's loop inlines what 7.x factored into ``clear_highpage_kasan_tagged()``:
  the tag reset, the clear and the tag restore are three explicit statements
  per page.  That shape is preserved verbatim in the HIGHMEM branch.

The function keeps its 5.15 name.  Upstream renamed ``kernel_init_pages`` ->
``clear_highpages_kasan_tagged`` because after the change it is no longer only
about free pages; the same is true of 5.15's ``kernel_init_free_pages()``
(it serves ``post_alloc_hook()`` too).  The rename buys nothing and costs a
second and third anchor on the hottest allocator path in the tree, so it is
deliberately not carried.

Safety, proven rather than assumed.  The dropped per-page tag pair is the only
semantic difference, so it is discharged hunk by hunk:

* ``page_kasan_tag_reset()`` is ``page_kasan_tag_set(page, 0xff)``
  (``include/linux/mm.h:1587``) and ``page_kasan_tag_set()`` writes the saved
  value back through ``try_cmpxchg`` (``:1571``).  The pair is therefore a
  read-modify-write that ends where it started: the observable state of
  ``page->flags`` is unchanged, so dropping it cannot alter any recorded tag.
* The reset's only other effect would be on the address handed to
  ``clear_highpage()``.  It has none: on arm64 ``page_address()`` is
  ``lowmem_page_address()`` (``include/linux/mm.h:1702`` ->
  ``page_address_virtual()``), which is ``__va()`` of the PFN and carries no
  KASAN tag.  ``kasan_reset_tag()`` is carried anyway to mirror upstream; it
  is provably inert here.
* The regime in which the function is live and the pair would have been live
  too is unreachable.  ``post_alloc_hook()`` clears ``init`` via
  ``kasan_has_integrated_init()``, which on 5.15 is
  ``kasan_hw_tags_enabled()`` (``include/linux/kasan.h:86``); the only way past
  that is ``should_skip_kasan_unpoison()`` returning true, whose last line is
  ``init_tags || (flags & __GFP_SKIP_KASAN_UNPOISON)``
  (``mm/page_alloc.c:2537``).  ``__GFP_ZEROTAGS`` is consumed by the
  ``init_tags`` branch above (which sets ``init = false`` itself) and
  ``__GFP_SKIP_KASAN_UNPOISON`` has **no user anywhere in the tree** -- both
  were grepped for.  ``free_pages_prepare()`` needs
  ``CONFIG_INIT_ON_FREE_DEFAULT_ON``, which ``gki_defconfig`` does not set.
* ``!IS_ENABLED(CONFIG_HIGHMEM)`` is arm64's configuration, so every page
  reachable here is in the linear map and ``page_address()`` is valid.  HIGHMEM
  keeps the per-page loop byte for byte.
* ``free_pages_prepare()`` asserts ``!PageTail(page)`` and both callers pass
  ``1 << order`` from a head page, so the range really is contiguous -- which
  is the precondition a single memset needs.

Benefit on this target.  ``CONFIG_INIT_ON_ALLOC_DEFAULT_ON=y``
(``gki_defconfig:670``) keeps the loop live for ordinary allocations, and the
saved work per page is a ``preempt_disable()``/``pagefault_disable()`` pair
plus a call, not just the memset itself -- 5.15's ``clear_highpage()`` is
``kmap_atomic()``/``clear_page()``/``kunmap_atomic()`` (``highmem.h``), and
with ``CONFIG_HIGHMEM=n`` ``kmap_atomic()`` still disables preemption and
page faults.  No device-side number is claimed.

No KMI impact: the function is static, no struct and no exported symbol is
touched, and no other group writes ``mm/page_alloc.c``'s init path.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

PAGE_ALLOC_C = "mm/page_alloc.c"

T = True

_BATCH_CLEAR_OLD = (
    "static void kernel_init_free_pages(struct page *page, int numpages)\n"
    "{\n"
    "\tint i;\n"
    "\n"
    "\t/* s390's use of memset() could override KASAN redzones. */\n"
    "\tkasan_disable_current();\n"
    "\tfor (i = 0; i < numpages; i++) {\n"
    "\t\tu8 tag = page_kasan_tag(page + i);\n"
    "\t\tpage_kasan_tag_reset(page + i);\n"
    "\t\tclear_highpage(page + i);\n"
    "\t\tpage_kasan_tag_set(page + i, tag);\n"
    "\t}\n"
    "\tkasan_enable_current();\n"
    "}\n"
)

_BATCH_CLEAR_NEW = (
    "static void kernel_init_free_pages(struct page *page, int numpages)\n"
    "{\n"
    "\t/* s390's use of memset() could override KASAN redzones. */\n"
    "\tkasan_disable_current();\n"
    "\tif (!IS_ENABLED(CONFIG_HIGHMEM)) {\n"
    "\t\t/*\n"
    "\t\t * ABK stable_515_backport: v7.2 b001cf7d16dd -- one memset over\n"
    "\t\t * the contiguous range instead of numpages kmap_atomic()/\n"
    "\t\t * clear_page()/kunmap_atomic() round trips.  The per-page KASAN\n"
    "\t\t * tag pair is a no-op on this baseline and is dropped; HIGHMEM\n"
    "\t\t * keeps the per-page form because it needs kmap.\n"
    "\t\t */\n"
    "\t\tmemset(kasan_reset_tag(page_address(page)), 0,\n"
    "\t\t       (unsigned long)numpages * PAGE_SIZE);\n"
    "\t} else {\n"
    "\t\tint i;\n"
    "\n"
    "\t\tfor (i = 0; i < numpages; i++) {\n"
    "\t\t\tu8 tag = page_kasan_tag(page + i);\n"
    "\n"
    "\t\t\tpage_kasan_tag_reset(page + i);\n"
    "\t\t\tclear_highpage(page + i);\n"
    "\t\t\tpage_kasan_tag_set(page + i, tag);\n"
    "\t\t}\n"
    "\t}\n"
    "\tkasan_enable_current();\n"
    "}\n"
)


def _pagealloc_batch_clear_apply(ctx):
    steps = [
        (PAGE_ALLOC_C, _BATCH_CLEAR_OLD, _BATCH_CLEAR_NEW, T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the Batch-39 PatchGroup records."""
    return [
        PatchGroup(
            "pagealloc_batch_clear",
            "kernel_init_free_pages() clears the whole contiguous allocation "
            "with one memset instead of numpages kmap_atomic()/clear_page()/"
            "kunmap_atomic() round trips; init_on_alloc is default-on in the "
            "GKI defconfig, so this is the per-page clear the allocator runs "
            "on every allocation",
            ["b001cf7d16dd (v7.2)"],
            [PAGE_ALLOC_C],
            _pagealloc_batch_clear_apply,
        ),
    ]
