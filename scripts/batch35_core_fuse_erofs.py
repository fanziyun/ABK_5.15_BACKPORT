from __future__ import annotations

from abk_backport_engine import apply_steps

FUSE_FILE_C = "fs/fuse/file.c"

T = True


_PREFAULT_HEAD_OLD = (
    " again:\n"
    "\t\terr = -EFAULT;\n"
    "\t\tif (fault_in_iov_iter_readable(ii, bytes))\n"
    "\t\t\tbreak;\n"
    "\n"
    "\t\terr = -ENOMEM;\n"
    "\t\tpage = grab_cache_page_write_begin(mapping, index, 0);\n"
)

_PREFAULT_HEAD_NEW = (
    " again:\n"
    "\t\terr = -ENOMEM;\n"
    "\t\tpage = grab_cache_page_write_begin(mapping, index, 0);\n"
)


_PREFAULT_RETRY_OLD = (
    "\t\tif (!tmp) {\n"
    "\t\t\tunlock_page(page);\n"
    "\t\t\tput_page(page);\n"
    "\t\t\tgoto again;\n"
    "\t\t}\n"
)

_PREFAULT_RETRY_NEW = (
    "\t\tif (!tmp) {\n"
    "\t\t\tunlock_page(page);\n"
    "\t\t\tput_page(page);\n"
    "\n"
    "\t\t\t/*\n"
    "\t\t\t * Ensure forward progress by faulting in\n"
    "\t\t\t * while not holding the page lock:\n"
    "\t\t\t */\n"
    "\t\t\tif (fault_in_iov_iter_readable(ii, bytes)) {\n"
    "\t\t\t\terr = -EFAULT;\n"
    "\t\t\t\tbreak;\n"
    "\t\t\t}\n"
    "\n"
    "\t\t\tgoto again;\n"
    "\t\t}\n"
)

RETRY_PREFAULT = (
    "\t\t\tif (fault_in_iov_iter_readable(ii, bytes)) {\n"
    "\t\t\t\terr = -EFAULT;\n"
    "\t\t\t\tbreak;\n"
    "\t\t\t}"
)
LOOP_HEAD_PREFAULT = (
    " again:\n"
    "\t\terr = -EFAULT;\n"
    "\t\tif (fault_in_iov_iter_readable(ii, bytes))\n"
    "\t\t\tbreak;\n"
)


def build_steps():
    return [
        (FUSE_FILE_C, _PREFAULT_HEAD_OLD, _PREFAULT_HEAD_NEW, T),
        (FUSE_FILE_C, _PREFAULT_RETRY_OLD, _PREFAULT_RETRY_NEW, T),
    ]


def _fuse_prefault_out_of_write_path_apply(ctx):
    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    return [
        PatchGroup(
            "fuse_prefault_out_of_write_path",
            "prefault the FUSE write source buffer only where the copy made no "
            "progress, instead of on every retry of the fill loop -- one fewer "
            "userspace access per write(2) through a FUSE daemon",
            [
                "faa794dd2e17 (mainline v6.16, 'fuse: Move prefaulting out of "
                "hot write path')",
            ],
            [FUSE_FILE_C],
            _fuse_prefault_out_of_write_path_apply,
        ),
    ]
