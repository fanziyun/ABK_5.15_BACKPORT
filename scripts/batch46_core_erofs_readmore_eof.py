"""Batch 46: erofs readmore stops at EOF.

Survey source: ``docs/survey_erofs_upstream.md`` section 4.1 (upstream patch
archived at ``research/erofs_readmore_eof/patches/936aa701d82d.patch``).  One
group, one file, one required step, grafting mainline ``936aa701d82d`` into
5.15's page-shaped ``z_erofs_pcluster_readmore()``.

What upstream changed and why
-----------------------------

V6.5, Chunhai Guo (vivo), 2023-07-10, "erofs: avoid useless loops in
``z_erofs_pcluster_readmore()`` when reading beyond EOF".  Verbatim, one line of
``fs/erofs/zdata.c``::

    -	while (cur >= end) {
    +	while ((cur >= end) && (cur < i_size_read(inode))) {

The loop walks *backwards* from ``map->m_la + map->m_llen - 1`` a page at a
time, prefetching the pages behind the one being read.  Its bound is the mapped
extent, not the file, so a read at a large page offset on a small inode starts
the loop far past EOF and spends the whole file's page count doing it.  Upstream's
reproducer: ``offset = 19217289215``, ``inode_size = 1442672`` -- 4,691,368
iterations, about 27 seconds.

Scope.  The commit has a ``Fixes:`` tag (``386292919c25``, the original readmore
series) and **no ``Cc: stable``**, so it is a v6.5 performance change, not a
backported fix: no 5.15 tree carries it, which is why the group cannot arrive
pre-applied on the rolling lts branch.  The commit message frames the problem
strictly as wasted work ("which is unnecessary should be prevented") -- there is
no CVE and no security wording, so it is inside this module's
features/optimizations-only scope.  The trigger is a read at a very large page
offset past EOF, which no ordinary access pattern produces; **no speedup is
claimed**, only that the pathological case stops existing.

Why the loop body transfers verbatim
------------------------------------

Checked rather than assumed, because the whole safety argument rests on it:
``z_erofs_pcluster_readmore()`` was diffed between the fetched 216 tree and
upstream v6.5 (``zdata.c`` at tag ``v6.5``).  Three differences, none of them
the loop:

* the **signature** -- v6.5 dropped the ``end`` parameter (it recomputes it from
  ``f->headoffset``) and the ``pagepool`` parameter (later submission rework),
  so it reads ``(f, rac, backmost)``.  5.15 keeps both.
* the ``backmost`` branch -- v6.5 computes ``end`` from
  ``headoffset + readahead_length(rac) - 1`` and expands with ``headoffset``;
  5.15 takes ``end`` from the caller and expands with ``readahead_pos(rac)``.
  v6.5 also fixed the "expend" typo in that branch's comment.  This is the
  difference the survey warned about: **the anchor must not reach into it**, so
  it starts at the ``cur = map->m_la + map->m_llen - 1;`` line.
* the loop body's control flow -- v6.5 uses ``if (page) { ... } else { ... }``,
  5.15 uses ``if (!page) goto skip;`` ... ``skip:``.  Semantically identical:
  a NULL page jumps to the tail, an uptodate page is unlocked and put and jumps
  to the tail, and everything else falls through.

So after the fix the only behavioural difference in the loop is the EOF guard
itself, which is what makes upstream's reasoning applicable here without
re-deriving it.

The safety argument
-------------------

The guard can only make the prefetch smaller; it cannot make a read wrong.
``z_erofs_pcluster_readmore()`` is a **prefetch**: the page the caller actually
wants is read unconditionally by the ``z_erofs_do_read_page()`` that follows the
``backmost`` call in ``z_erofs_readpage()``, and by the readahead loop itself in
``z_erofs_readahead()``.  Every page the guard now skips is a page at a file
offset past ``i_size``, which holds no file data, and if anyone needs it the
synchronous path re-enters with its own mapping and reads it properly.

One nuance stated rather than glossed: the guard tests the **byte offset**
``cur``, not the page index, so a ``cur`` inside the last partial page but at or
past ``i_size`` is skipped too.  That is upstream's exact form, not a porting
choice, and it is only reachable on the loop's first iteration -- every later
``cur`` is page-aligned-minus-one -- and it costs a prefetch, never a read.

Shape probes taken against the fetched 216 tree before the anchor was written:

* the three-line anchor occurs **exactly once** in ``fs/erofs/zdata.c`` (counted),
  and its three lines are byte-identical to v6.5's;
* ``inode`` is a non-const ``struct inode *`` bound at the top of the function, so
  it matches ``i_size_read()``'s ``const struct inode *``;
* ``i_size_read`` appears **zero** times in the file, so there is no other
  reader to confuse it with and no local to shadow;
* ``i_size_read()`` is an unconditional ``static inline`` in
  ``include/linux/fs.h`` (only its 32-bit-SMP body is conditional, irrelevant on
  arm64), and that header is reachable: ``zdata.c`` -> ``zdata.h`` ->
  ``internal.h`` -> ``<linux/fs.h>``.  This was checked because a missing
  declaration is the class of C-level error the three text audits cannot see.

Graft boundary.  ``fs/erofs/zdata.c`` is already in ``FETCH_FILES`` (Batch 40
put it there), so there is no fixture gap and no new fetch step.  Batch 40's
seven steps in this same file all anchor on text this group does not touch
(struct definition, the two ``erofs_allocpage()`` calls, the collector and
request initializers, the collection teardown), and this group's anchor is
not among them, so there is no ordering constraint and no trap-5 exposure.
The edit is an upstream-shape rewrite, so it carries no
``/* ABK stable_515_backport: */`` marker: a baseline that ever grows the
commit stays byte-identical and reports ``already_present``.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

ZDATA_C = "fs/erofs/zdata.c"

T = True

# The anchor starts at `cur = map->m_la + map->m_llen - 1;` because those three
# lines are byte-identical between the 5.15 baseline and v6.5, while the
# `backmost` branch above them is not.
_READMORE_OLD = (
    "\tcur = map->m_la + map->m_llen - 1;\n"
    "\twhile (cur >= end) {\n"
    "\t\tpgoff_t index = cur >> PAGE_SHIFT;\n"
)

_READMORE_NEW = (
    "\tcur = map->m_la + map->m_llen - 1;\n"
    "\twhile ((cur >= end) && (cur < i_size_read(inode))) {\n"
    "\t\tpgoff_t index = cur >> PAGE_SHIFT;\n"
)

# Upstream's own post-commit form, which the tests use to pin the guard's
# polarity: the EOF test is the *second* conjunct, so `cur >= end` still
# terminates the loop on its own and the new condition can only narrow it.
EOF_GUARD = "cur < i_size_read(inode)"
LOOP_CONDITION_NEW = "\twhile ((cur >= end) && (cur < i_size_read(inode))) {\n"


def build_steps():
    """One required step in one file."""
    return [
        (ZDATA_C, _READMORE_OLD, _READMORE_NEW, T),
    ]


def _erofs_readmore_past_eof_apply(ctx):
    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the single Batch-46 PatchGroup record."""
    return [
        PatchGroup(
            "erofs_readmore_past_eof",
            "z_erofs_pcluster_readmore() stops its backwards prefetch walk at "
            "the inode's size instead of at the mapped extent, so a read at a "
            "large page offset on a small file cannot spend the whole page "
            "count iterating past EOF",
            ["936aa701d82d (mainline v6.5, 'erofs: avoid useless loops in "
             "z_erofs_pcluster_readmore() when reading beyond EOF')"],
            [ZDATA_C],
            _erofs_readmore_past_eof_apply,
        ),
    ]
