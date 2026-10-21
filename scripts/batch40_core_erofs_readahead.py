from __future__ import annotations

from abk_backport_engine import apply_steps

COMPRESS_H = "fs/erofs/compress.h"
DECOMPRESSOR_C = "fs/erofs/decompressor.c"
DECOMPRESSOR_LZMA_C = "fs/erofs/decompressor_lzma.c"
ZDATA_C = "fs/erofs/zdata.c"
ZDATA_H = "fs/erofs/zdata.h"

T = True


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

_LZMA_LABEL_OLD = (
    "\t}\n"
    "\tif (no < nrpages_out && strm->buf.out)\n"
)

_LZMA_LABEL_NEW = (
    "\t}\n"
    "failed:\n"
    "\tif (no < nrpages_out && strm->buf.out)\n"
)


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


_RESET_OLD = (
    "\tcl->nr_pages = 0;\n"
    "\tcl->vcnt = 0;\n"
)

_RESET_NEW = (
    "\tcl->nr_pages = 0;\n"
    "\tcl->vcnt = 0;\n"
    "\tpcl->besteffort = false;\n"
)

REQ_GFP_FIELD = "\tgfp_t gfp;"
PCLUSTER_BESTEFFORT = "\tbool besteffort;"
SET_MODE_LINE = "\tclt->pcl->besteffort |= !fe->readahead;"
REQ_GFP_INIT = "\t\t\t\t\t.gfp = pcl->besteffort ?"
RESET_LINE = "\tpcl->besteffort = false;"
UPSTREAM_ONLY_MARKER = "fillgaps"


def build_steps():
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

    if REQ_GFP_FIELD in text and PCLUSTER_BESTEFFORT in text:
        return "already_present", "gfp field and pcluster bit both present"

    if UPSTREAM_ONLY_MARKER in text:
        return "blocked_by_shape", (
            "post-v6.9 request struct (fillgaps present) without the group's "
            "own edit: unexpected shape, not grafted")

    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
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
