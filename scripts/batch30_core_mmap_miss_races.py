from __future__ import annotations

from abk_backport_engine import apply_steps

FILEMAP_C = "mm/filemap.c"

T = True


_ASYNC_OLD = (
    "\t/* If we don't want any read-ahead, don't bother */\n"
    "\tif (vmf->vma->vm_flags & VM_RAND_READ || !ra->ra_pages)\n"
    "\t\treturn fpin;\n"
    "\tmmap_miss = READ_ONCE(ra->mmap_miss);\n"
    "\tif (mmap_miss)\n"
    "\t\tWRITE_ONCE(ra->mmap_miss, --mmap_miss);\n"
    "\tif (PageReadahead(page)) {\n"
)

_ASYNC_NEW = (
    "\t/* If we don't want any read-ahead, don't bother */\n"
    "\tif (vmf->vma->vm_flags & VM_RAND_READ || !ra->ra_pages)\n"
    "\t\treturn fpin;\n"
    "\t/*\n"
    "\t * If the page is locked, we're likely racing against another fault.\n"
    "\t * Don't touch the mmap_miss counter to avoid decreasing it multiple\n"
    "\t * times for a single page and break the balance with mmap_miss\n"
    "\t * increase in do_sync_mmap_readahead().\n"
    "\t */\n"
    "\tif (likely(!PageLocked(page))) {\n"
    "\t\tmmap_miss = READ_ONCE(ra->mmap_miss);\n"
    "\t\tif (mmap_miss)\n"
    "\t\t\tWRITE_ONCE(ra->mmap_miss, --mmap_miss);\n"
    "\t}\n"
    "\tif (PageReadahead(page)) {\n"
)


def build_steps():
    return [
        (FILEMAP_C, _ASYNC_OLD, _ASYNC_NEW, T),
    ]


def _mmap_miss_race_apply(ctx):
    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    return [
        PatchGroup(
            "readahead_mmap_miss_race",
            "skip the mmap_miss decrement in do_async_mmap_readahead() while the "
            "page is locked, so concurrent faults on one page cannot drive the "
            "per-file counter down repeatedly and keep mmap read-around enabled "
            "under memory pressure (e338d8353154, v6.18; no Cc: stable, so it "
            "never reached 5.15.y)",
            [
                "e338d8353154 (v6.18) -- concurrent-fault mmap_miss guard",
            ],
            [FILEMAP_C],
            _mmap_miss_race_apply,
        ),
    ]
