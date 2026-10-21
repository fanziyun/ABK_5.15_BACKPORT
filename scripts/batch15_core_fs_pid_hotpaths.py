from __future__ import annotations

from abk_backport_engine import apply_steps

PID_C = "kernel/pid.c"
FILE_C = "fs/file.c"

T = True


_PID_DECL_OLD = (
    "\tstruct upid *upid;\n"
    "\tint retval = -ENOMEM;\n"
)

_PID_DECL_NEW = (
    "\tstruct upid *upid;\n"
    "\tint retval = -ENOMEM;\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: pid_alloc_hotpath_phase2.  idr_preload()\n"
    "\t * can come up short for the GFP_ATOMIC idr_alloc_cyclic() below\n"
    "\t * (the idr has to grow a node the preloaded pool did not cover), and\n"
    "\t * the allocation then reports -ENOMEM from inside pidmap_lock even\n"
    "\t * though this caller is allowed to sleep.  One retry with a freshly\n"
    "\t * preloaded pool turns that spurious -ENOMEM into a successful fork;\n"
    "\t * a second failure is fatal.\n"
    "\t */\n"
    "\tbool retried_preload;\n"
)

_PID_PRELOAD_OLD = (
    "\t\t\tset_tid_size--;\n"
    "\t\t}\n"
    "\n"
    "\t\tidr_preload(GFP_KERNEL);\n"
    "\t\tspin_lock_irq(&pidmap_lock);\n"
    "\n"
    "\t\tif (tid) {\n"
)

_PID_PRELOAD_NEW = (
    "\t\t\tset_tid_size--;\n"
    "\t\t}\n"
    "\n"
    "\t\tretried_preload = false;\n"
    "\t\t/* ABK stable_515_backport: the retry re-enters here -- same\n"
    "\t\t * namespace level, set_tid bookkeeping not repeated. */\n"
    "retry_preload:\n"
    "\t\tidr_preload(GFP_KERNEL);\n"
    "\t\tspin_lock_irq(&pidmap_lock);\n"
    "\n"
    "\t\tif (tid) {\n"
)

_PID_RETRY_OLD = (
    "\t\tspin_unlock_irq(&pidmap_lock);\n"
    "\t\tidr_preload_end();\n"
    "\n"
    "\t\tif (nr < 0) {\n"
    "\t\t\tretval = (nr == -ENOSPC) ? -EAGAIN : nr;\n"
    "\t\t\tgoto out_free;\n"
    "\t\t}\n"
    "\n"
    "\t\tpid->numbers[i].nr = nr;\n"
    "\t\tpid->numbers[i].ns = tmp;\n"
    "\t\ttmp = tmp->parent;\n"
    "\t}\n"
)

_PID_RETRY_NEW = (
    "\t\tspin_unlock_irq(&pidmap_lock);\n"
    "\t\tidr_preload_end();\n"
    "\n"
    "\t\tif (nr < 0) {\n"
    "\t\t\t/*\n"
    "\t\t\t * ABK stable_515_backport: retry this namespace level once\n"
    "\t\t\t * under a fresh GFP_KERNEL preload.  Deliberately a `goto`\n"
    "\t\t\t * and not `continue`: `continue` would run the for-loop's\n"
    "\t\t\t * `i--`, skipping a level whose pid->numbers[i] entry has\n"
    "\t\t\t * not been written yet.\n"
    "\t\t\t */\n"
    "\t\t\tif (nr == -ENOMEM && !retried_preload) {\n"
    "\t\t\t\tretried_preload = true;\n"
    "\t\t\t\tgoto retry_preload;\n"
    "\t\t\t}\n"
    "\t\t\tretval = (nr == -ENOSPC) ? -EAGAIN : nr;\n"
    "\t\t\tgoto out_free;\n"
    "\t\t}\n"
    "\n"
    "\t\tpid->numbers[i].nr = nr;\n"
    "\t\tpid->numbers[i].ns = tmp;\n"
    "\t\ttmp = tmp->parent;\n"
    "\t}\n"
)


_FD_HELPER_OLD = (
    "#define fdt_words(fdt) ((fdt)->max_fds / BITS_PER_LONG) // words in ->open_fds\n"
)

_FD_HELPER_NEW = (
    "#define fdt_words(fdt) ((fdt)->max_fds / BITS_PER_LONG) // words in ->open_fds\n"
    "\n"
    "/*\n"
    " * ABK stable_515_backport: fd_alloc_hotpath.  expand_files() already\n"
    " * reports \"nothing done\" (0) for a descriptor the current table covers,\n"
    " * so the two hot-path callers below can skip the call -- and its second\n"
    " * files_fdtable() lookup -- with one comparison.\n"
    " */\n"
    "static inline bool abk_expand_files_needed(const struct fdtable *fdt,\n"
    "\t\t\t\t\t   unsigned int nr)\n"
    "{\n"
    "\treturn nr >= fdt->max_fds;\n"
    "}\n"
    "\n"
)

_FD_EXPAND_OLD = (
    "repeat:\n"
    "\tfdt = files_fdtable(files);\n"
    "\n"
    "\t/* Do we need to expand? */\n"
    "\tif (nr < fdt->max_fds)\n"
    "\t\treturn expanded;\n"
    "\n"
    "\t/* Can we expand? */\n"
    "\tif (nr >= sysctl_nr_open)\n"
    "\t\treturn -EMFILE;\n"
)

_FD_EXPAND_NEW = (
    "repeat:\n"
    "\tfdt = files_fdtable(files);\n"
    "\n"
    "\t/* Do we need to expand? */\n"
    "\tif (!abk_expand_files_needed(fdt, nr))\n"
    "\t\treturn expanded;\n"
    "\n"
    "\t/* Can we expand? */\n"
    "\tif (nr >= sysctl_nr_open)\n"
    "\t\treturn -EMFILE;\n"
)

_FD_ALLOC_OLD = (
    "\terror = -EMFILE;\n"
    "\tif (fd >= end)\n"
    "\t\tgoto out;\n"
    "\n"
    "\terror = expand_files(files, fd);\n"
    "\tif (error < 0)\n"
    "\t\tgoto out;\n"
    "\n"
    "\t/*\n"
    "\t * If we needed to expand the fs array we\n"
    "\t * might have blocked - try again.\n"
    "\t */\n"
    "\tif (error)\n"
    "\t\tgoto repeat;\n"
)

_FD_ALLOC_NEW = (
    "\terror = -EMFILE;\n"
    "\tif (fd >= end)\n"
    "\t\tgoto out;\n"
    "\n"
    "\t/* ABK stable_515_backport: fd_alloc_hotpath -- fdt was read at the top\n"
    "\t * of this iteration, so the growth check needs no call. */\n"
    "\tif (abk_expand_files_needed(fdt, fd)) {\n"
    "\t\terror = expand_files(files, fd);\n"
    "\t\tif (error < 0)\n"
    "\t\t\tgoto out;\n"
    "\n"
    "\t\t/*\n"
    "\t\t * If we needed to expand the fs array we\n"
    "\t\t * might have blocked - try again.\n"
    "\t\t */\n"
    "\t\tif (error)\n"
    "\t\t\tgoto repeat;\n"
    "\t}\n"
)

_FD_UNUSED_OLD = (
    "int get_unused_fd_flags(unsigned flags)\n"
    "{\n"
    "\treturn __get_unused_fd_flags(flags, rlimit(RLIMIT_NOFILE));\n"
    "}\n"
)

_FD_UNUSED_NEW = (
    "int get_unused_fd_flags(unsigned flags)\n"
    "{\n"
    "\t/* ABK stable_515_backport: fd_alloc_hotpath -- deliberately a bare\n"
    "\t * tail call so the hot open()/dup() entry points stay on alloc_fd().\n"
    "\t */\n"
    "\treturn __get_unused_fd_flags(flags, rlimit(RLIMIT_NOFILE));\n"
    "}\n"
)


_CR_PICK_OLD = (
    "\tspin_lock(&files->file_lock);\n"
    "\tfdt = files_fdtable(files);\n"
    "\tif (fd >= fdt->max_fds) {\n"
    "\t\tfile = ERR_PTR(-EINVAL);\n"
    "\t\tgoto out_unlock;\n"
    "\t}\n"
    "\tfd = array_index_nospec(fd, fdt->max_fds);\n"
)

_CR_PICK_NEW = (
    "\tspin_lock(&files->file_lock);\n"
    "\tfdt = files_fdtable(files);\n"
    "\tif (fd >= fdt->max_fds) {\n"
    "\t\tfile = ERR_PTR(-EINVAL);\n"
    "\t\tgoto out_unlock;\n"
    "\t}\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: close_range_hotpath.  __clear_open_fd()\n"
    "\t * clears the open bit under this same lock, and the fd-array slot is\n"
    "\t * NULLed before it, so a clear bit means the load below cannot return\n"
    "\t * a file.  Answering from the bitmap skips array_index_nospec() and\n"
    "\t * the fd-array cache line for every descriptor the range walk skips.\n"
    "\t */\n"
    "\tif (!fd_is_open(fd, fdt)) {\n"
    "\t\tfile = ERR_PTR(-EBADF);\n"
    "\t\tgoto out_unlock;\n"
    "\t}\n"
    "\tfd = array_index_nospec(fd, fdt->max_fds);\n"
)

_CR_RANGE_OLD = (
    "static inline void __range_close(struct files_struct *cur_fds, unsigned int fd,\n"
    "\t\t\t\t unsigned int max_fd)\n"
    "{\n"
    "\twhile (fd <= max_fd) {\n"
    "\t\tstruct file *file;\n"
    "\n"
    "\t\tfile = pick_file(cur_fds, fd++);\n"
    "\t\tif (!IS_ERR(file)) {\n"
    "\t\t\t/* found a valid file to close */\n"
    "\t\t\tfilp_close(file, cur_fds);\n"
    "\t\t\tcond_resched();\n"
    "\t\t\tcontinue;\n"
    "\t\t}\n"
    "\n"
    "\t\t/* beyond the last fd in that table */\n"
    "\t\tif (PTR_ERR(file) == -EINVAL)\n"
    "\t\t\treturn;\n"
    "\t}\n"
    "}\n"
)

_CR_RANGE_NEW = (
    "/*\n"
    " * ABK stable_515_backport: close_range_hotpath.  Walk open_fds instead of\n"
    " * probing every descriptor in [fd, max_fd]: a range close of a large span\n"
    " * used to pay one locked pick_file() per descriptor, almost all of them\n"
    " * -EBADF misses.  The table is re-read under rcu_read_lock() after each\n"
    " * filp_close() -- the same context __range_cloexec() and __close_range()\n"
    " * use for last_fd()/open_fds -- and max_fd is re-clamped to it, so a\n"
    " * concurrent expansion cannot make the walk read past the live table.\n"
    " */\n"
    "static inline void __range_close(struct files_struct *cur_fds, unsigned int fd,\n"
    "\t\t\t\t unsigned int max_fd)\n"
    "{\n"
    "\tstruct fdtable *fdt;\n"
    "\tunsigned int n;\n"
    "\n"
    "\trcu_read_lock();\n"
    "\tfdt = files_fdtable(cur_fds);\n"
    "\tn = last_fd(fdt);\n"
    "\tmax_fd = min(max_fd, n);\n"
    "\tfd = find_next_bit(fdt->open_fds, max_fd + 1, fd);\n"
    "\trcu_read_unlock();\n"
    "\n"
    "\twhile (fd <= max_fd) {\n"
    "\t\tstruct file *file;\n"
    "\n"
    "\t\tfile = pick_file(cur_fds, fd);\n"
    "\t\tif (!IS_ERR(file)) {\n"
    "\t\t\t/* found a valid file to close */\n"
    "\t\t\tfilp_close(file, cur_fds);\n"
    "\t\t\tcond_resched();\n"
    "\t\t} else if (PTR_ERR(file) == -EINVAL) {\n"
    "\t\t\t/* beyond the last fd in that table */\n"
    "\t\t\treturn;\n"
    "\t\t}\n"
    "\n"
    "\t\trcu_read_lock();\n"
    "\t\tfdt = files_fdtable(cur_fds);\n"
    "\t\tmax_fd = min(max_fd, last_fd(fdt));\n"
    "\t\tfd = find_next_bit(fdt->open_fds, max_fd + 1, fd + 1);\n"
    "\t\trcu_read_unlock();\n"
    "\t}\n"
    "}\n"
)


def build_pid_alloc_steps():
    return [
        (PID_C, _PID_DECL_OLD, _PID_DECL_NEW, T),
        (PID_C, _PID_PRELOAD_OLD, _PID_PRELOAD_NEW, T),
        (PID_C, _PID_RETRY_OLD, _PID_RETRY_NEW, T),
    ]


def build_fd_alloc_steps():
    return [
        (FILE_C, _FD_HELPER_OLD, _FD_HELPER_NEW, T),
        (FILE_C, _FD_EXPAND_OLD, _FD_EXPAND_NEW, T),
        (FILE_C, _FD_ALLOC_OLD, _FD_ALLOC_NEW, T),
        (FILE_C, _FD_UNUSED_OLD, _FD_UNUSED_NEW, T),
    ]


def build_close_range_steps():
    return [
        (FILE_C, _CR_PICK_OLD, _CR_PICK_NEW, T),
        (FILE_C, _CR_RANGE_OLD, _CR_RANGE_NEW, T),
    ]


def _pid_alloc_apply(ctx):
    try:
        text = ctx.read(PID_C)
    except FileNotFoundError:
        return "blocked_by_shape", f"{PID_C}: file absent"
    for probe, why in (
        ("struct pid *alloc_pid(struct pid_namespace *ns, pid_t *set_tid,",
         "alloc_pid()"),
        ("\t\tidr_preload(GFP_KERNEL);\n", "idr_preload(GFP_KERNEL) protocol"),
        ("\tidr_preload_end();\n", "idr_preload_end() protocol"),
    ):
        if probe not in text:
            return "blocked_by_shape", f"{why} not found in pid.c"
    status, _results, detail = apply_steps(ctx, build_pid_alloc_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _fd_alloc_apply(ctx):
    try:
        text = ctx.read(FILE_C)
    except FileNotFoundError:
        return "blocked_by_shape", f"{FILE_C}: file absent"
    if ctx.suite_touched(FILE_C):
        return (
            "skip_suite_processed",
            "fs/file.c already carries the ABK_ABI_PATCH_SUITE fd allocation "
            "hotpath graft (abk_fdtable_slots_wanted/ABK feature_porting "
            "marker); Batch 15 absorbs that group, so its copy is left alone",
        )
    if (
        "alloc_fdtable(unsigned int slots_wanted)" not in text
        or "roundup_pow_of_two(slots_wanted)" not in text
    ):
        return (
            "blocked_by_shape",
            "fs/file.c does not carry the upstream 5.15.191 fdtable shape yet; "
            "fdtable_alloc_conventions must run before fd_alloc_hotpath",
        )
    for probe, why in (
        ("#define fdt_words(fdt)", "fdt_words() helper site"),
        ("static int expand_files(struct files_struct *files, unsigned int nr)",
         "expand_files()"),
        ("static int alloc_fd(unsigned start, unsigned end, unsigned flags)",
         "alloc_fd()"),
        ("int get_unused_fd_flags(unsigned flags)\n", "get_unused_fd_flags()"),
    ):
        if probe not in text:
            return "blocked_by_shape", f"{why} not found in fs/file.c"
    status, _results, detail = apply_steps(ctx, build_fd_alloc_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _close_range_apply(ctx):
    try:
        text = ctx.read(FILE_C)
    except FileNotFoundError:
        return "blocked_by_shape", f"{FILE_C}: file absent"
    if ctx.suite_touched(FILE_C):
        return (
            "skip_suite_processed",
            "fs/file.c already carries the ABK_ABI_PATCH_SUITE close_range() "
            "bitmap graft (ABK feature_porting marker); Batch 15 absorbs that "
            "group, so its copy is left alone",
        )
    for probe, why in (
        ("static struct file *pick_file(struct files_struct *files, unsigned fd)",
         "pick_file()"),
        ("static inline void __range_close(struct files_struct *cur_fds, unsigned int fd,",
         "__range_close()"),
        ("static inline unsigned last_fd(struct fdtable *fdt)", "last_fd()"),
        ("\t\tfile = ERR_PTR(-EINVAL);\n", "pick_file() 5.15 error convention"),
    ):
        if probe not in text:
            return "blocked_by_shape", f"{why} not found in fs/file.c"
    status, _results, detail = apply_steps(ctx, build_close_range_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    return [
        PatchGroup(
            "pid_alloc_hotpath_phase2",
            "alloc_pid() preload/retry on the 5.15 pid-namespace shape: "
            "idr_preload(GFP_KERNEL) is retried once (same namespace level) "
            "after a GFP_ATOMIC -ENOMEM under pidmap_lock; "
            "idr_alloc_cyclic()'s -ENOSPC stays translated to -EAGAIN; no "
            "pid_namespace layout change",
            [
                "ABK_ABI_PATCH_SUITE pid_alloc_hotpath_phase2 "
                "(abk_feature_porting.patch_pid_alloc, absorbed by Batch 15)",
                "the suite's `continue` retry is corrected to a same-level "
                "`goto retry_preload` -- see the module docstring",
                "no upstream commit: alloc_pid() is byte-identical and "
                "retry-free in the 5.15/6.1/6.6 reference trees; the -ENOSPC "
                "-> -EAGAIN half is already present on every 5.15 baseline",
            ],
            [PID_C],
            _pid_alloc_apply,
        ),
        PatchGroup(
            "fd_alloc_hotpath",
            "fd allocation hot path on top of the 5.15.191 fd-table "
            "conventions: abk_expand_files_needed() lets expand_files() and "
            "alloc_fd() answer the growth question with one comparison "
            "instead of an expand_files() call on the common path; the "
            "suite's alloc_fdtable() capacity rewrite is NOT ported because "
            "fdtable_alloc_conventions owns that text and runs first",
            [
                "ABK_ABI_PATCH_SUITE fd_alloc_hotpath "
                "(abk_feature_porting.patch_fd_alloc_hotpath, absorbed by "
                "Batch 15), minus its abk_fdtable_slots_wanted() helper and "
                "alloc_fdtable() capacity rewrite",
                "composition: requires fdtable_alloc_conventions (5.15.191, "
                "04a2c4b4511d + 1d3b4bec3ce5) to have landed the upstream "
                "slots_wanted/ERR_PTR alloc_fdtable()",
            ],
            [FILE_C],
            _fd_alloc_apply,
        ),
        PatchGroup(
            "close_range_hotpath",
            "close_range() walks the open_fds bitmap instead of probing every "
            "descriptor in the range: pick_file() gains a bitmap early-out "
            "(-EBADF) and __range_close() advances fd-to-fd through "
            "find_next_bit(), re-reading the table under rcu_read_lock() "
            "after each filp_close(); syscall-visible behaviour unchanged",
            [
                "ABK_ABI_PATCH_SUITE close_range_hotpath "
                "(abk_feature_porting.patch_close_range_hotpath, absorbed by "
                "Batch 15), in its 5.15 (range_new_5_15) form -- the "
                "abk_close_range_limit()/abk_pick_file_for_close() helpers "
                "belong to the caller-locked 6.1 shape and are not ported",
            ],
            [FILE_C],
            _close_range_apply,
        ),
    ]
