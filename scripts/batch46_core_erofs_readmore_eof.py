from __future__ import annotations

from abk_backport_engine import apply_steps

ZDATA_C = "fs/erofs/zdata.c"

T = True

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

EOF_GUARD = "cur < i_size_read(inode)"
LOOP_CONDITION_NEW = "\twhile ((cur >= end) && (cur < i_size_read(inode))) {\n"


def build_steps():
    return [
        (ZDATA_C, _READMORE_OLD, _READMORE_NEW, T),
    ]


def _erofs_readmore_past_eof_apply(ctx):
    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
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
