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
