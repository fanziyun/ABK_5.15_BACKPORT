"""Batch 40: erofs readahead temporary buffers become best-effort.

Survey source and full evidence: ``research/erofs_readahead/`` (device premise
check) and ``CHANGELOG.md#batch-40``.  One group, grafting mainline
``d9281660ff3f`` ("erofs: relaxed temporary buffers allocation on readahead",
Chunhai Guo, vivo, v6.9) into 5.15's page-shaped erofs decompressors.

What upstream changed and why
-----------------------------

erofs decompresses LZ4 **in place**: the pages holding the compressed data are
reused as the output buffer.  LZ4 needs a sliding window, and when the window
runs past the pages that happen to be mapped, the kernel allocates
short-lived bounce pages (``Z_EROFS_SHORTLIVED_PAGE``) for the duration of the
request.  Both 5.15 decompressors asked for those pages with

    erofs_allocpage(pagepool, GFP_KERNEL | __GFP_NOFAIL)

unconditionally -- so every readahead shot that needed a bounce page could
enter direct reclaim and wait for it, even though a readahead that fails is
harmless and gets retried by the next synchronous access.

Upstream added a ``gfp_t gfp`` to ``struct z_erofs_decompress_req``, set it per
pcluster, and made the readahead path use ``GFP_NOWAIT | __GFP_NORETRY`` while
synchronous reads keep ``GFP_KERNEL | __GFP_NOFAIL``.  Measured on **ARM64
Android devices (8-core, 8 GB) running a 5.15 LTS kernel with EROFS 4k
pclusters**, multi-app launch average: 3364 -> 2684 ms (-20.21%, 64k sliding
window) and 2079 -> 1610 ms (-22.56%, 16k window), with the system image size
essentially unchanged.  Those are upstream's numbers on upstream's benchmark;
this module claims no independent measurement of them, and the premise that
makes them applicable here is recorded below and in
``research/erofs_readahead/vermeer_check_20260922.md``.

The premise, checked on the device rather than assumed
------------------------------------------------------

``d9281660ff3f`` is only live where the mounted erofs really is compressed, so
it was verified per-inode (the superblock's ``feature_incompat`` alone is not
conclusive -- ``LZ4_0PADDING`` and ``lz4_max_distance`` carry benign defaults
on uncompressed images): pulling the first 16 MB of ``/dev/block/dm-N`` and
walking the directory tree with 5.15's on-disk format reaches real files that
are marked ``EROFS_INODE_FLAT_COMPRESSION``.  On vermeer (5.15.216,
``CONFIG_EROFS_FS_ZIP=y``, LZMA off, ``EROFS_FS_PCPU_KTHREAD_HIPRI=y``) those
are ``/vendor/build.prop`` (nid 110, 16314 B, 2 compressed blocks) and a
root-image file at nid 640; ``u1.lz4_max_distance = 65535`` gives a 64 KiB
window and there is no ``BIG_PCLUSTER`` bit, i.e. 4 KiB pclusters -- which is
the 64k-window column of upstream's table, not the 16k one.

Where the 5.15 target shape differs from upstream's
---------------------------------------------------

Six differences, each checked against the reference trees before the anchors
were written.  All are 5.15-shape rewrites of the *same* edit, so -- like
Batch 35's FUSE prefault -- no ``ABK stable_515_backport:`` marker is added on
any of these lines: a future baseline that carries ``d9281660ff3f`` itself must
stay byte-identical and report ``already_present``.

1. ``struct z_erofs_pcluster`` lives in ``fs/erofs/zdata.h`` on 5.15, not in
   ``zdata.c`` (upstream moved it in v6.6).  The new bit is placed after
   ``algorithmformat``, immediately before the ``compressed_pages[]`` flexible
   array; the slab is sized by ``struct_size(a, compressed_pages, maxpages)``
   (``zdata.c``, ``z_erofs_create_pcluster_pool()``) so it grows automatically,
   and ``kmem_cache_zalloc()`` zeroes it, which is what makes the initial
   ``besteffort == false`` true.  Upstream's anchor is ``bool multibases;``,
   which 5.15 does not have.
2. **The polarity trap.**  Upstream named the bit ``besteffort`` and comments
   it "whether extra buffer allocations are best-effort", but then sets it
   with ``besteffort |= !ra`` and reads ``besteffort ? GFP_KERNEL |
   __GFP_NOFAIL : GFP_NOWAIT | __GFP_NORETRY``.  So ``besteffort == true``
   means *must succeed*, i.e. the name reads inverted against its own comment.
   Verified from upstream's own callers rather than from the name:
   ``z_erofs_read_folio()`` passes ``false`` (synchronous -> NOFAIL) and
   ``z_erofs_readahead()`` passes ``true`` (-> NOWAIT).  Copying the name
   without the polarity is exactly how this graft would ship inverted, so
   ``tests/stable_5_15_test.py`` and ``tests/implementation_audit.py`` both pin
   the two sides by their flags, not by the field name.
3. 5.15 does not need upstream's new ``bool ra`` parameter at all.
   ``struct z_erofs_decompress_frontend`` already has ``bool readahead``
   (``zdata.c``), left false by ``DECOMPRESS_FRONTEND_INIT()`` and set true
   only by ``z_erofs_readahead()``; ``z_erofs_pcluster_readmore()`` already
   reads it.  So ``z_erofs_do_read_page()`` reads ``fe->readahead`` directly
   and no signature or call site changes -- strictly smaller than upstream's
   form.  ``fe->pcl`` also does not exist on 5.15 (the frontend has no
   ``pcl`` member); the pcluster is ``clt->pcl``, ``clt`` being the local
   ``&fe->clt``.  ``clt->pcl`` is guaranteed non-NULL on the success path:
   ``z_erofs_collector_begin()`` dereferences it in its own ``out:`` block
   before returning 0.
4. The request initialiser in ``z_erofs_decompress_pcluster()`` is a 5.15
   struct literal whose last member is ``.partial_decoding = partial`` with no
   trailing comma; the new ``.gfp`` member needs that comma added.  Upstream's
   literal ends on ``.fillgaps = pcl->multibases,``, a member 5.15 lacks.
5. Upstream resets the bit next to ``pcl->multibases = false;``.  5.15 has no
   such pcluster-state block; the reset goes with the collection teardown
   (``cl->nr_pages = 0; cl->vcnt = 0;``), immediately before
   ``WRITE_ONCE(pcl->next, Z_EROFS_PCLUSTER_NIL)`` -- the same position
   relative to the consumer, namely the end of ``z_erofs_decompress_pcluster()``.
   That ordering is what keeps a stale bit safe: the only way to reach
   decompression with ``besteffort == false`` is for every collector of that
   pcluster to have been a readahead, and a stale ``true`` degrades to
   pre-patch behaviour (NOFAIL), never to a failing synchronous read.
6. Two upstream hunks have no 5.15 carrier and are deliberately not carried:
   ``decompressor_deflate.c`` (5.15 has no DEFLATE decompressor) and the LZMA
   hunk at the ``rq->fillgaps`` dedup branch (5.15's LZMA decompressor has no
   ``fillgaps`` use).  The LZMA bounce-page hunk and its new ``failed:`` label
   do land; 5.15's LZMA function has only ``again:`` labels and no ``failed:``,
   so the label is free.

The safety argument, traced rather than assumed
-----------------------------------------------

Turning an infallible allocation into a fallible one on the readahead path is
the whole point of the commit, so the retry that makes it safe was traced in
this tree: ``z_erofs_lz4_prepare_dstpages()`` now returns ``-ENOMEM``
(``zdata.c``; ``z_erofs_lz4_decompress()`` propagates it), the pages end up
not-uptodate with ``SetPageError()`` rather than silently zero, and the next
synchronous access runs ``z_erofs_readpage()``, which consults neither the
error bit nor ``readahead`` and re-enters with ``f.readahead == false`` --
i.e. ``GFP_KERNEL | __GFP_NOFAIL``.  This is upstream's design; it is recorded
here as the reason the graft is safe on 5.15, not as an independent
measurement.

Graft boundary: this is the module's first group in ``fs/erofs/``, no other
group writes any of these five files, and nothing here edits generated text,
so there is no ordering constraint and no shape probe on another group's
payload is needed.  The group does probe its own payload (see ``_apply``) so a
tree that already carries the commit short-circuits instead of half-applying.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

COMPRESS_H = "fs/erofs/compress.h"
DECOMPRESSOR_C = "fs/erofs/decompressor.c"
DECOMPRESSOR_LZMA_C = "fs/erofs/decompressor_lzma.c"
ZDATA_C = "fs/erofs/zdata.c"
ZDATA_H = "fs/erofs/zdata.h"

T = True

# ---------------------------------------------------------------------------
# Step 1: the request carries the allocation flags to use for extra pages.
# ---------------------------------------------------------------------------

_REQ_STRUCT_OLD = (
    "\t/* indicate the algorithm will be used for decompression */\n"
    "\tunsigned int alg;\n"
    "\tbool inplace_io, partial_decoding;\n"
    "};\n"
)

_REQ_STRUCT_NEW = (
    "\t/* indicate the algorithm will be used for decompression */\n"
    "\tunsigned int alg;\n"
    "\tbool inplace_io, partial_decoding;\n"
    "\n"
    "\t/* allocation flags for extra temporary buffers */\n"
    "\tgfp_t gfp;\n"
    "};\n"
)

# ---------------------------------------------------------------------------
# Step 2: LZ4's bounce page honours them, and may now fail.
# ---------------------------------------------------------------------------

_LZ4_ALLOC_OLD = (
    "\t\t\tvictim = erofs_allocpage(pagepool,\n"
    "\t\t\t\t\t\t GFP_KERNEL | __GFP_NOFAIL);\n"
    "\t\t\tset_page_private(victim, Z_EROFS_SHORTLIVED_PAGE);\n"
)

_LZ4_ALLOC_NEW = (
    "\t\t\tvictim = erofs_allocpage(pagepool, rq->gfp);\n"
    "\t\t\tif (!victim)\n"
    "\t\t\t\treturn -ENOMEM;\n"
    "\t\t\tset_page_private(victim, Z_EROFS_SHORTLIVED_PAGE);\n"
)

# ---------------------------------------------------------------------------
# Step 3: so does LZMA's bounce page.  Upstream also renames the parameter
# (pagepool -> pgpl) in this commit; that is cosmetic and deliberately not
# carried, so the 5.15 name stays and only the actual edit lands.
# ---------------------------------------------------------------------------

_LZMA_ALLOC_OLD = (
    "\t\t\ttmppage = erofs_allocpage(pagepool,\n"
    "\t\t\t\t\t\t  GFP_KERNEL | __GFP_NOFAIL);\n"
    "\t\t\tset_page_private(tmppage, Z_EROFS_SHORTLIVED_PAGE);\n"
    "\t\t\tcopy_highpage(tmppage, rq->in[j]);\n"
    "\t\t\trq->in[j] = tmppage;\n"
)

_LZMA_ALLOC_NEW = (
    "\t\t\ttmppage = erofs_allocpage(pagepool, rq->gfp);\n"
    "\t\t\tif (!tmppage) {\n"
    "\t\t\t\terr = -ENOMEM;\n"
    "\t\t\t\tgoto failed;\n"
    "\t\t\t}\n"
    "\t\t\tset_page_private(tmppage, Z_EROFS_SHORTLIVED_PAGE);\n"
    "\t\t\tcopy_highpage(tmppage, rq->in[j]);\n"
    "\t\t\trq->in[j] = tmppage;\n"
)

# ... and the branch above needs somewhere to land.  5.15's function has
# ``again:`` labels only, so ``failed:`` is free.
_LZMA_LABEL_OLD = (
    "\t}\n"
    "\tif (no < nrpages_out && strm->buf.out)\n"
)

_LZMA_LABEL_NEW = (
    "\t}\n"
    "failed:\n"
    "\tif (no < nrpages_out && strm->buf.out)\n"
)

# ---------------------------------------------------------------------------
# Step 4: the pcluster carries the mode.  On 5.15 the struct is in zdata.h.
# ---------------------------------------------------------------------------

_PCLUSTER_OLD = (
    "\t/* I: compression algorithm format */\n"
    "\tunsigned char algorithmformat;\n"
)

_PCLUSTER_NEW = (
    "\t/* I: compression algorithm format */\n"
    "\tunsigned char algorithmformat;\n"
    "\n"
    "\t/* L: whether extra buffer allocations are best-effort */\n"
    "\tbool besteffort;\n"
)

# ---------------------------------------------------------------------------
# Step 5: the collector records which mode it is in.  Upstream threads a new
# ``bool ra`` parameter; 5.15 already has the same fact on the frontend.
# ---------------------------------------------------------------------------

_SET_MODE_OLD = (
    "\terr = z_erofs_collector_begin(clt, inode, map);\n"
    "\tif (err)\n"
    "\t\tgoto err_out;\n"
)

_SET_MODE_NEW = (
    "\terr = z_erofs_collector_begin(clt, inode, map);\n"
    "\tif (err)\n"
    "\t\tgoto err_out;\n"
    "\tclt->pcl->besteffort |= !fe->readahead;\n"
)

# ---------------------------------------------------------------------------
# Step 6: the decompressor reads it.
# ---------------------------------------------------------------------------

_REQ_INIT_OLD = (
    "\t\t\t\t\t.partial_decoding = partial\n"
    "\t\t\t\t }, pagepool);\n"
)

_REQ_INIT_NEW = (
    "\t\t\t\t\t.partial_decoding = partial,\n"
    "\t\t\t\t\t.gfp = pcl->besteffort ?\n"
    "\t\t\t\t\t\tGFP_KERNEL | __GFP_NOFAIL :\n"
    "\t\t\t\t\t\tGFP_NOWAIT | __GFP_NORETRY\n"
    "\t\t\t\t }, pagepool);\n"
)

# ---------------------------------------------------------------------------
# Step 7: and the pcluster is put back in its pristine state.
# ---------------------------------------------------------------------------

_RESET_OLD = (
    "\tcl->nr_pages = 0;\n"
    "\tcl->vcnt = 0;\n"
)

_RESET_NEW = (
    "\tcl->nr_pages = 0;\n"
    "\tcl->vcnt = 0;\n"
    "\tpcl->besteffort = false;\n"
)

# Payload probes: one symbol this group writes, per file that carries state.
# The struct field is the earliest of the five edits, so a tree that already
# has it carries the whole commit.
REQ_GFP_FIELD = "\tgfp_t gfp;"
PCLUSTER_BESTEFFORT = "\tbool besteffort;"
SET_MODE_LINE = "\tclt->pcl->besteffort |= !fe->readahead;"
REQ_GFP_INIT = "\t\t\t\t\t.gfp = pcl->besteffort ?"
RESET_LINE = "\tpcl->besteffort = false;"
# Upstream's own shape, used only to tell "a later tree" from "a broken one".
UPSTREAM_ONLY_MARKER = "fillgaps"


def build_steps():
    """Seven required steps over five files.

    All required: this is one cohesive edit whose halves are each individually
    compilable but wrong (a ``gfp`` nobody sets would leave every read with
    ``GFP_NOWAIT``; a mode nobody reads is dead state), and ``apply_steps`` is
    transactional, so a required miss on any baseline writes nothing at all.
    """
    return [
        (COMPRESS_H, _REQ_STRUCT_OLD, _REQ_STRUCT_NEW, T),
        (DECOMPRESSOR_C, _LZ4_ALLOC_OLD, _LZ4_ALLOC_NEW, T),
        (DECOMPRESSOR_LZMA_C, _LZMA_ALLOC_OLD, _LZMA_ALLOC_NEW, T),
        (DECOMPRESSOR_LZMA_C, _LZMA_LABEL_OLD, _LZMA_LABEL_NEW, T),
        (ZDATA_H, _PCLUSTER_OLD, _PCLUSTER_NEW, T),
        (ZDATA_C, _SET_MODE_OLD, _SET_MODE_NEW, T),
        (ZDATA_C, _REQ_INIT_OLD, _REQ_INIT_NEW, T),
        (ZDATA_C, _RESET_OLD, _RESET_NEW, T),
    ]


def _erofs_readahead_relaxed_gfp_apply(ctx):
    try:
        text = ctx.read(COMPRESS_H)
    except FileNotFoundError:
        return "blocked_by_shape", COMPRESS_H + ": file absent"

    # This group's own payload, checked first so a tree that carries the commit
    # short-circuits instead of half-applying (docs/group_recipe.md trap 4).
    if REQ_GFP_FIELD in text and PCLUSTER_BESTEFFORT in text:
        return "already_present", "gfp field and pcluster bit both present"

    # A post-v6.9 erofs has upstream's layout (pageofs_in/fillgaps in the
    # request struct) without this module's edit only if it is some other
    # lineage's tree; degrade rather than graft onto an unexpected shape.
    if UPSTREAM_ONLY_MARKER in text:
        return "blocked_by_shape", (
            "post-v6.9 request struct (fillgaps present) without the group's "
            "own edit: unexpected shape, not grafted")

    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the single Batch-40 PatchGroup record."""
    return [
        PatchGroup(
            "erofs_readahead_relaxed_gfp",
            "erofs decompression asks for its temporary bounce pages with "
            "GFP_NOWAIT|__GFP_NORETRY on the readahead path and keeps "
            "GFP_KERNEL|__GFP_NOFAIL for synchronous reads, instead of making "
            "every readahead shot wait in direct reclaim for a page it can "
            "live without",
            ["d9281660ff3f (mainline v6.9, 'erofs: relaxed temporary buffers "
             "allocation on readahead')"],
            [COMPRESS_H, DECOMPRESSOR_C, DECOMPRESSOR_LZMA_C, ZDATA_C, ZDATA_H],
            _erofs_readahead_relaxed_gfp_apply,
        ),
    ]
