"""Batch 30: the mmap_miss counter stops double-decrementing under concurrent faults.

Sourcing (read as a real patch, kept in ``research/readahead_618/``):

* ``e338d8353154`` ("mm: readahead: improve mmap_miss heuristic for concurrent
  faults", Roman Gushchin, merged by akpm 2025-09-13 for v6.18) guards the
  mmap_miss decrement in ``do_async_mmap_readahead()`` with a lock test.  When
  two or more threads fault on the same page, every one of them used to decrement
  the per-file ``ra->mmap_miss`` for that one page; the counter then undershot
  its increment side in ``do_sync_mmap_readahead()``, so the "this file is
  random-access, stop reading ahead" decision stopped firing and mmap
  read-around stayed enabled while the file's pages were being evicted and
  re-faulted under memory pressure.  Upstream's evidence is a Google production
  fleet measurement (several hundred thousand hosts, weeks): 10-20x fewer
  containers stuck in a reclaim cycle, meaningfully less CPU in direct reclaim,
  no regressions.  There is no per-device A/B, so this batch does not claim a
  device-side speedup -- it claims alignment with upstream and the removal of
  the duplicate decrement.

Why the baseline does not have it: the commit carries neither ``Fixes:`` nor
``Cc: stable``, so it never reached 5.15.y -- it is absent from all four baselines
this module audits, android13-5.15-lts (the newest 5.15 tree here) included.
Manual backport is therefore the only path.

5.15 shape differences (verified against all four reference trees):

* 5.15 has no folios in this function: the guard is ``PageLocked(page)`` and the
  comment says "page".  The rest matches upstream hunk for hunk, so the port
  keeps the upstream comment and adds no ABK marker -- this is an
  upstream-shape rewrite, and a baseline that ever carries the commit is left
  byte-identical.
* the anchor (the VM_RAND_READ early return through ``if (PageReadahead(page))``)
  is unique in ``mm/filemap.c`` and byte-identical on 167/178/194/216, so one
  group covers every baseline.  android13-5.15-lts carries an ACK-only
  ``trace_android_vh_do_async_mmap_readahead(vmf, page, &skip)`` prologue
  earlier in the same function; the anchor does not touch it, and the decrement
  this group guards sits after it either way.

Deliberately NOT ported (recorded so this does not get re-litigated).  5.15 moves
this counter in four places -- one increment, three decrements -- and this batch
touches the concurrent-fault decrement only, leaving the other three as they are:

* the increment side: ``do_sync_mmap_readahead()`` (untouched -- it is what the
  guard restores the balance with).
* ``filemap_map_pages()`` (fault-around) batches its own decrement into a local
  and writes it back once.  It is a bulk-mapping path, not the concurrent-fault
  path this commit targets, so it stays untouched here.
* the 5.15-only ``FAULT_FLAG_SPECULATIVE`` branch in ``filemap_fault()`` holds a
  third decrement, the one whose own comment admits it can fire twice for a
  single page.  Upstream deleted the speculative-fault path outright, so no
  upstream commit fixes it; it is a 5.15-only remnant, and fixing it would be a
  local design decision rather than a backport.
* the later upstream symmetry series is a separate decision, not a portability
  limit.  ``eb4c458a9803`` (VM_SEQ_READ, v6.20) would in fact have three sites
  to touch on 5.15 -- 5.15's ``do_sync_mmap_readahead()`` returns early for
  VM_SEQ_READ *before* the increment, so the asymmetry it fixes is real here --
  and ``2f5e0477276b`` (VM_EXEC, v6.20) has no carrier at all, being built on
  ``exec_folio_order()`` and the VM_EXEC readahead path, neither of which exists
  on 5.15 (both verified absent from the reference trees).  This batch is the
  single commit ``e338d8353154``; the symmetry series changes behaviour for
  VM_SEQ_READ/VM_EXEC mappings and would need its own evidence.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

FILEMAP_C = "mm/filemap.c"

T = True  # required step

# ---------------------------------------------------------------------------
# Step 1: do_async_mmap_readahead() -- only the fault that wins the page lock
# moves the counter.
#
# The anchor starts at the function's own "if we don't want any read-ahead"
# return (the ACK prologue above it differs between baselines, this does not)
# and ends at the PageReadahead() test the guard has to stay in front of.  It is
# unique in the file: the speculative path's copy of the same two lines carries
# an inline declaration ("unsigned int mmap_miss = READ_ONCE(...)"), so it cannot
# match this text.
# ---------------------------------------------------------------------------

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
    """Return the ordered (rel, old, new, required) steps of this group."""
    return [
        (FILEMAP_C, _ASYNC_OLD, _ASYNC_NEW, T),
    ]


def _mmap_miss_race_apply(ctx):
    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the single Batch-30 PatchGroup record."""
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
