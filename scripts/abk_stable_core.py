"""Child ``stable_backport_core``: upstream 5.15.y feature grafts (fs/mm/cgroup).

Carries the 5.15.191 fd-table allocation conventions, the ALLOC_MIN_RESERVE
rename with RT-task semantics (5.15.171), the __GFP_THISNODE THP no-reclaim
change (5.15.202), the cpuset insane-config early bail-out (5.15.191), the
percpu_pagelist_high_fraction lock-free read (5.15.200), the cgroup root_list
RCU conversion (5.15.168), and the cgroup destroy-workqueue split (5.15.194).

The fd-table group is hard: on a tree whose fs/file.c matches neither the
pristine monthly shape, the upstream shape, nor an already-processed shape
it aborts the build instead of half-patching.

Coexistence: standalone by default; if storage-rollback or other feature-
graft modules are injected in the same build, keep this child between them
(injection order in docs/porting_policy.md).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import abk_common as common  # noqa: E402
from abk_backport_engine import (  # noqa: E402
    PatchGroup,
    apply_steps,
    make_context,
    parse_args,
    run_child,
)

T = True  # required step
F = False  # optional step


# ---------------------------------------------------------------------------
# fd table allocation conventions (5.15.191: 04a2c4b4511d + 1d3b4bec3ce5)
# ---------------------------------------------------------------------------

_FD_DOC_TAIL_OLD = """ * The ALIGN(nr, BITS_PER_LONG) here is for clarity: since we just multiplied
 * by that "1024/sizeof(ptr)" before, we already know there are sufficient
 * clear low bits. Clang seems to realize that, gcc ends up being confused.
 *
 * On a 128-bit machine, the ALIGN() would actually matter. In the meantime,
 * let's consider it documentation (and maybe a test-case for gcc to improve
 * its code generation ;)
 */
static struct fdtable * alloc_fdtable(unsigned int nr)
{
	struct fdtable *fdt;
	void *data;
"""

_FD_DOC_TAIL_NEW = """ */
/* ABK stable_515_backport: fd table allocation conventions grafted from 5.15.191 (slots_wanted parameter and ERR_PTR conventions). */
static struct fdtable *alloc_fdtable(unsigned int slots_wanted)
{
	struct fdtable *fdt;
	unsigned int nr;
	void *data;
"""

_FD_SIZE_MATH_OLD = """	nr /= (1024 / sizeof(struct file *));
	nr = roundup_pow_of_two(nr + 1);
	nr *= (1024 / sizeof(struct file *));
	nr = ALIGN(nr, BITS_PER_LONG);
"""

_FD_SIZE_MATH_NEW = """	if (IS_ENABLED(CONFIG_32BIT) && slots_wanted < 256)
		nr = 256;
	else
		nr = roundup_pow_of_two(slots_wanted);
"""

_FD_CLAMP_OLD = """	 * Note that this can drive nr *below* what we had passed if sysctl_nr_open
	 * had been set lower between the check in expand_files() and here.  Deal
	 * with that in caller, it's cheaper that way.
	 *
	 * We make sure that nr remains a multiple of BITS_PER_LONG - otherwise
	 * bitmaps handling below becomes unpleasant, to put it mildly...
	 */
	if (unlikely(nr > sysctl_nr_open))
		nr = ((sysctl_nr_open - 1) | (BITS_PER_LONG - 1)) + 1;
"""

_FD_CLAMP_NEW = """	 * Note that this can drive nr *below* what we had passed if sysctl_nr_open
	 * had been set lower between the check in expand_files() and here.
	 *
	 * We make sure that nr remains a multiple of BITS_PER_LONG - otherwise
	 * bitmaps handling below becomes unpleasant, to put it mildly...
	 */
	if (unlikely(nr > sysctl_nr_open)) {
		nr = round_down(sysctl_nr_open, BITS_PER_LONG);
		if (nr < slots_wanted)
			return ERR_PTR(-EMFILE);
	}

	/* ABK stable_515_backport: fdtable allocation INT_MAX guard (5.15.191). */
	if (unlikely(nr > INT_MAX / sizeof(struct file *)))
		return ERR_PTR(-EMFILE);
"""

_FD_TAIL_OLD = """out_fdt:
	kfree(fdt);
out:
	return NULL;
}
"""

_FD_TAIL_NEW = """out_fdt:
	kfree(fdt);
out:
	return ERR_PTR(-ENOMEM);
}
"""

_FD_EXPAND_CALL_OLD = """	spin_unlock(&files->file_lock);
	new_fdt = alloc_fdtable(nr);
"""

_FD_EXPAND_CALL_NEW = """	spin_unlock(&files->file_lock);
	new_fdt = alloc_fdtable(nr + 1);
"""

_FD_EXPAND_CHECK_OLD = """	if (!new_fdt)
		return -ENOMEM;
	/*
	 * extremely unlikely race - sysctl_nr_open decreased between the check in
	 * caller and alloc_fdtable().  Cheaper to catch it here...
	 */
	if (unlikely(new_fdt->max_fds <= nr)) {
		__free_fdtable(new_fdt);
		return -EMFILE;
	}
"""

_FD_EXPAND_CHECK_NEW = """	if (IS_ERR(new_fdt))
		return PTR_ERR(new_fdt);
"""

_FD_DUPFD_AOSP_OLD = """		new_fdt = alloc_fdtable(open_files - 1);
		if (!new_fdt) {
			*errorp = -ENOMEM;
			goto out_release;
		}

		/* beyond sysctl_nr_open; nothing to do */
		if (unlikely(new_fdt->max_fds < open_files)) {
			__free_fdtable(new_fdt);
			*errorp = -EMFILE;
			goto out_release;
		}
"""

_FD_DUPFD_AOSP_NEW = """		/* ABK stable_515_backport: dup_fd keeps the errorp contract while alloc_fdtable() switches to the slots_wanted/ERR_PTR conventions. */
		new_fdt = alloc_fdtable(open_files);
		if (IS_ERR(new_fdt)) {
			kmem_cache_free(files_cachep, newf);
			*errorp = PTR_ERR(new_fdt);
			return NULL;
		}
"""

_FD_DUPFD_LABEL_OLD = """	return newf;

out_release:
	kmem_cache_free(files_cachep, newf);
out:
	return NULL;
}
"""

_FD_DUPFD_LABEL_NEW = """	return newf;

out:
	return NULL;
}
"""

# Vanilla punch_hole dup_fd tail: the AOSP/vanilla trees carrying
# `sane_fdtable_size(old_fdt, punch_hole)` end dup_fd() with an
# out_release: cleanup label and `return ERR_PTR(error);`.  Upstream
# 5.15.191 merges that path into ERR_CAST early returns, so the whole
# tail block must be replaced by the plain `return newf;` form.  The old
# label-only step ("out_release:\n...\n}\n" -> "}\n") was a no-op because
# replace_once() treats a bare "}\n" as already_present (any closing brace
# in the file matches), leaving an unused label that -Werror,-Wunused-label
# rejects.  The old text therefore spans from `return newf;` through the
# closing brace and the replacement is the target tail verbatim.
_FD_DUPFD_LABEL_VANILLA_OLD = """	return newf;

out_release:
	kmem_cache_free(files_cachep, newf);
	return ERR_PTR(error);
}
"""
_FD_DUPFD_LABEL_VANILLA_NEW = """	return newf;
}
"""

# Vanilla punch_hole dup_fd no longer uses `int error;`: the ERR_CAST early
# returns replace both `error = -ENOMEM; goto out_release;` paths, and the
# label tail that read `error` is gone.  Upstream 5.15.191 drops the
# declaration; keep the anchor scoped to the dup_fd header so other
# functions' `int error;` locals are untouched.
_FD_DUPFD_ERR_DECL_OLD = """	struct fdtable *old_fdt, *new_fdt;
	int error;

	newf = kmem_cache_alloc(files_cachep, GFP_KERNEL);
"""
_FD_DUPFD_ERR_DECL_NEW = """	struct fdtable *old_fdt, *new_fdt;

	newf = kmem_cache_alloc(files_cachep, GFP_KERNEL);
"""

_FD_DUPFD_VANILLA_OLD = """		new_fdt = alloc_fdtable(open_files - 1);
		if (!new_fdt) {
			error = -ENOMEM;
			goto out_release;
		}

		/* beyond sysctl_nr_open; nothing to do */
		if (unlikely(new_fdt->max_fds < open_files)) {
			__free_fdtable(new_fdt);
			error = -EMFILE;
			goto out_release;
		}
"""

_FD_DUPFD_VANILLA_NEW = """		/* ABK stable_515_backport: dup_fd adopts the ERR_CAST return contract of the 5.15.191 conventions. */
		new_fdt = alloc_fdtable(open_files);
		if (IS_ERR(new_fdt)) {
			kmem_cache_free(files_cachep, newf);
			return ERR_CAST(new_fdt);
		}
"""

_FD_REPLACE_FD_OLD = """	return do_dup2(files, file, fd, flags);
"""

_FD_REPLACE_FD_NEW = """	err = do_dup2(files, file, fd, flags);
	if (err < 0)
		return err;
	return 0;
"""

# Suite-fallback variants: when ABK_ABI_PATCH_SUITE ran first, alloc_fdtable()
# carries its "fd allocation hotpath" fallback (legacy nr parameter, helper
# local, ALIGN capacity, round-up clamp, INT_MAX -> return NULL).  These steps
# convert that body onto the same upstream 5.15.191 target as the pristine
# variant; the suite's helpers and expand_files()/alloc_fd() prechecks stay in
# place and keep working (the slot-count helper simply becomes unused).
_FD_SUITE_DOC_TAIL_OLD = """ * The ALIGN(nr, BITS_PER_LONG) here is for clarity: since we just multiplied
 * by that "1024/sizeof(ptr)" before, we already know there are sufficient
 * clear low bits. Clang seems to realize that, gcc ends up being confused.
 *
 * On a 128-bit machine, the ALIGN() would actually matter. In the meantime,
 * let's consider it documentation (and maybe a test-case for gcc to improve
 * its code generation ;)
 */
static struct fdtable * alloc_fdtable(unsigned int nr)
{
	struct fdtable *fdt;
	unsigned int slots_wanted = abk_fdtable_slots_wanted(nr);
	void *data;
"""

_FD_SUITE_SIZE_MATH_OLD = """	/*
	 * Keep the legacy file-local interface shape, but derive capacity from
	 * the requested slot count before dropping into the allocator.
	 */
	nr = ALIGN(slots_wanted, BITS_PER_LONG);
"""

_FD_SUITE_SIZE_MATH_NEW = """	/*
	 * Figure out how many fds we actually want to support in this fdtable.
	 * Allocation steps are keyed to the size of the fdarray, since it
	 * grows far faster than any of the other dynamic data. We try to fit
	 * the fdarray into page-tuned chunks: starting at 1024B and growing in
	 * powers of two from there on.
	 */
	if (IS_ENABLED(CONFIG_32BIT) && slots_wanted < 256)
		nr = 256;
	else
		nr = roundup_pow_of_two(slots_wanted);
"""

_FD_SUITE_CLAMP_OLD = """	 * Note that this can drive nr *below* what we had passed if sysctl_nr_open
	 * had been set lower between the check in expand_files() and here.  Deal
	 * with that in caller, it's cheaper that way.
	 *
	 * We make sure that nr remains a multiple of BITS_PER_LONG - otherwise
	 * bitmaps handling below becomes unpleasant, to put it mildly...
	 */
	if (unlikely(nr > sysctl_nr_open))
		nr = ((sysctl_nr_open - 1) | (BITS_PER_LONG - 1)) + 1;
	if (unlikely(nr > INT_MAX / sizeof(struct file *)))
		return NULL;
"""


def _suite_fallback_shape(text):
    """True for the suite's fallback alloc_fdtable() body we can compose onto."""
    return (
        "alloc_fdtable(unsigned int nr)" in text
        and "unsigned int slots_wanted = abk_fdtable_slots_wanted(nr);" in text
        and common.SUITE_FD_FALLBACK_ALIGN in text
    )


def _suite_composed_steps(dup_fd_aosp):
    label_step = (
        ("fs/file.c", _FD_DUPFD_LABEL_OLD, _FD_DUPFD_LABEL_NEW, T)
        if dup_fd_aosp
        else ("fs/file.c", _FD_DUPFD_LABEL_VANILLA_OLD, _FD_DUPFD_LABEL_VANILLA_NEW, T)
    )
    err_decl_step = (
        []
        if dup_fd_aosp
        else [("fs/file.c", _FD_DUPFD_ERR_DECL_OLD, _FD_DUPFD_ERR_DECL_NEW, T)]
    )
    return [
        ("fs/file.c", _FD_SUITE_DOC_TAIL_OLD, _FD_DOC_TAIL_NEW, T),
        ("fs/file.c", _FD_SUITE_SIZE_MATH_OLD, _FD_SUITE_SIZE_MATH_NEW, T),
        ("fs/file.c", _FD_SUITE_CLAMP_OLD, _FD_CLAMP_NEW, T),
        ("fs/file.c", _FD_TAIL_OLD, _FD_TAIL_NEW, T),
        ("fs/file.c", _FD_EXPAND_CALL_OLD, _FD_EXPAND_CALL_NEW, T),
        ("fs/file.c", _FD_EXPAND_CHECK_OLD, _FD_EXPAND_CHECK_NEW, T),
        ("fs/file.c", _FD_DUPFD_AOSP_OLD, _FD_DUPFD_AOSP_NEW, T)
        if dup_fd_aosp
        else ("fs/file.c", _FD_DUPFD_VANILLA_OLD, _FD_DUPFD_VANILLA_NEW, T),
        label_step,
        *err_decl_step,
    ]


def _fdtable_apply(ctx):
    # Order matters: the composed-after-suite check must see the upstream
    # shape through the suite's leftover (unused) helper, so it comes first.
    if ctx.fdtable_upstream_shape():
        return "already_present", "upstream fdtable conventions already present"
    text_probe = ctx.read("fs/file.c")
    if _suite_fallback_shape(text_probe):
        # Suite ran first: compose the upstream conventions over its fallback.
        dup_fd_aosp = "sane_fdtable_size(old_fdt, max_fds)" in text_probe
        status, _results, detail = apply_steps(
            ctx, _suite_composed_steps(dup_fd_aosp)
        )
        if status is None:
            # Suite text drifted from the shape we compose against; yield
            # gracefully instead of aborting the user's build.
            return (
                "skip_suite_processed",
                "suite fallback shape not recognized for composition; " + detail,
            )
        return status, detail
    if ctx.suite_touched("fs/file.c"):
        return (
            "skip_suite_processed",
            "fs/file.c already carries ABK_ABI_PATCH_SUITE markers",
        )

    steps = [
        ("fs/file.c", _FD_DOC_TAIL_OLD, _FD_DOC_TAIL_NEW, T),
        ("fs/file.c", _FD_SIZE_MATH_OLD, _FD_SIZE_MATH_NEW, T),
        ("fs/file.c", _FD_CLAMP_OLD, _FD_CLAMP_NEW, T),
        ("fs/file.c", _FD_TAIL_OLD, _FD_TAIL_NEW, T),
        ("fs/file.c", _FD_EXPAND_CALL_OLD, _FD_EXPAND_CALL_NEW, T),
        ("fs/file.c", _FD_EXPAND_CHECK_OLD, _FD_EXPAND_CHECK_NEW, T),
        ("fs/file.c", _FD_DUPFD_AOSP_OLD, _FD_DUPFD_AOSP_NEW, T),
        ("fs/file.c", _FD_DUPFD_LABEL_OLD, _FD_DUPFD_LABEL_NEW, T),
    ]
    # Vanilla punch_hole dup_fd variant: accepted in place of the AOSP one.
    if "sane_fdtable_size(old_fdt, max_fds)" not in text_probe:
        steps[6] = ("fs/file.c", _FD_DUPFD_VANILLA_OLD, _FD_DUPFD_VANILLA_NEW, T)
        steps[7] = ("fs/file.c", _FD_DUPFD_LABEL_VANILLA_OLD, _FD_DUPFD_LABEL_VANILLA_NEW, T)
        steps.insert(8, ("fs/file.c", _FD_DUPFD_ERR_DECL_OLD, _FD_DUPFD_ERR_DECL_NEW, T))

    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        raise SystemExit(
            "stable_backport_core/fdtable_alloc_conventions: fs/file.c matches no known "
            f"shape (pristine monthly, upstream 5.15.191, or suite-processed); {detail}"
        )
    return status, detail


# ---------------------------------------------------------------------------
# replace_fd() propagates do_dup2() errors (5.15.195, ff8ec0dbe0150)
# ---------------------------------------------------------------------------

def _replace_fd_errno_apply(ctx):
    """Own group so the fix lands on trees that already carry 5.15.191.

    The fdtable conventions group short-circuits to ``already_present`` the
    moment ``fdtable_upstream_shape()`` holds, which is true from 5.15.191
    onwards.  This hunk only appeared in 5.15.195, so as a step inside that
    group it could never reach a .191-.194 baseline.
    """
    status, _results, detail = apply_steps(
        ctx, [("fs/file.c", _FD_REPLACE_FD_OLD, _FD_REPLACE_FD_NEW, T)]
    )
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# page_alloc ALLOC_MIN_RESERVE semantics (5.15.171: 9cfe015 follow-ups)
# ---------------------------------------------------------------------------

def _min_reserve_apply(ctx):
    steps = [
        ("mm/internal.h",
         "#define ALLOC_HIGH\t\t 0x20 /* __GFP_HIGH set */",
         "#define ALLOC_MIN_RESERVE\t 0x20 /* __GFP_HIGH set. Allow access to 50%\n"
         "\t\t\t\t       * of the min watermark.\n"
         "\t\t\t\t       */",
         T),
        ("mm/page_alloc.c",
         "if (alloc_flags & ALLOC_HIGH)\n\t\tmin -= min / 2;",
         "if (alloc_flags & ALLOC_MIN_RESERVE)\n\t\tmin -= min / 2;",
         T),
        ("mm/page_alloc.c",
         "\t * __GFP_HIGH is assumed to be the same as ALLOC_HIGH\n",
         "\t * __GFP_HIGH is assumed to be the same as ALLOC_MIN_RESERVE\n",
         T),
        ("mm/page_alloc.c",
         "BUILD_BUG_ON(__GFP_HIGH != (__force gfp_t) ALLOC_HIGH);",
         "BUILD_BUG_ON(__GFP_HIGH != (__force gfp_t) ALLOC_MIN_RESERVE);",
         T),
        ("mm/page_alloc.c",
         "\t * set both ALLOC_HARDER (__GFP_ATOMIC) and ALLOC_HIGH (__GFP_HIGH).",
         "\t * set both ALLOC_HARDER (__GFP_ATOMIC) and ALLOC_MIN_RESERVE(__GFP_HIGH).",
         F),
        ("mm/page_alloc.c",
         "\t} else if (unlikely(rt_task(current)) && in_task())\n\t\talloc_flags |= ALLOC_HARDER;",
         "\t} else if (unlikely(rt_task(current)) && in_task())\n\t\talloc_flags |= ALLOC_MIN_RESERVE;",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        # The high-atomic reserve chain (pagealloc_highatomic_reserve_semantics)
        # restructures this group's __zone_watermark_ok() hunk onto the final
        # 5.15.218 form; recognize that superseding shape as our own end state.
        if "ALLOC_RESERVES" in ctx.read("mm/internal.h"):
            return "already_present", "superseded by pagealloc_highatomic_reserve_semantics"
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# High-atomic / min-reserve semantics, complete form
# (5.15.188-.218: ca8527f25736 + c1b8856c5a7d + 17dedfd6de69 + 85f58ee33c6c
#  + 4c4e238d3ada + 735457683e23)
#
# Runs after pagealloc_min_reserve_semantics, whose ALLOC_HIGH ->
# ALLOC_MIN_RESERVE rename this chain builds on.  The AOSP tree already
# carries the rmqueue_buddy() split (ca8527f) plus vendor traces, so only
# the flag-semantics hunks are grafted; the vendor CMA-first block and
# trace_mm_page_alloc_zone_locked() inside rmqueue_buddy() are preserved.
# ---------------------------------------------------------------------------

_ALLOC_CONT = "\t\t\t\t       "


def _highatomic_reserve_apply(ctx):
    steps = [
        # mm/internal.h: ALLOC_HARDER -> ALLOC_NON_BLOCK, aligned comments.
        ("mm/internal.h",
         "#define ALLOC_HARDER\t\t 0x10 /* try to alloc harder */\n"
         "#define ALLOC_MIN_RESERVE\t 0x20 /* __GFP_HIGH set. Allow access to 50%\n"
         "\t\t\t\t       * of the min watermark.\n"
         "\t\t\t\t       */",
         "#define ALLOC_NON_BLOCK\t\t 0x10 /* Caller cannot block. Allow access\n"
         + _ALLOC_CONT + "* to 25% of the min watermark or\n"
         + _ALLOC_CONT + "* 62.5% if __GFP_HIGH is set.\n"
         + _ALLOC_CONT + "*/\n"
         "#define ALLOC_MIN_RESERVE\t 0x20 /* __GFP_HIGH set. Allow access to 50%\n"
         + _ALLOC_CONT + "* of the min watermark.\n"
         + _ALLOC_CONT + "*/",
         T),
        # mm/internal.h: ALLOC_HIGHATOMIC + the below-min-watermark set.
        ("mm/internal.h",
         "#define ALLOC_KSWAPD\t\t0x800 /* allow waking of kswapd, __GFP_KSWAPD_RECLAIM set */",
         "#define ALLOC_HIGHATOMIC\t0x200 /* Allows access to MIGRATE_HIGHATOMIC */\n"
         "#define ALLOC_KSWAPD\t\t0x800 /* allow waking of kswapd, __GFP_KSWAPD_RECLAIM set */\n"
         "\n"
         "/* Flags that allow allocations below the min watermark. */\n"
         "#define ALLOC_RESERVES (ALLOC_NON_BLOCK|ALLOC_MIN_RESERVE|ALLOC_HIGHATOMIC|ALLOC_OOM)",
         T),
        # __zone_watermark_unusable_free(): reserves set, not just harder.
        ("mm/page_alloc.c",
         "\tconst bool alloc_harder = (alloc_flags & (ALLOC_HARDER|ALLOC_OOM));\n"
         "\tlong unusable_free = (1 << order) - 1;\n"
         "\n"
         "\t/*\n"
         "\t * If the caller does not have rights to ALLOC_HARDER then subtract\n"
         "\t * the high-atomic reserves. This will over-estimate the size of the\n"
         "\t * atomic reserve but it avoids a search.\n"
         "\t */\n"
         "\tif (likely(!alloc_harder))\n"
         "\t\tunusable_free += z->nr_reserved_highatomic;",
         "\tlong unusable_free = (1 << order) - 1;\n"
         "\n"
         "\t/*\n"
         "\t * If the caller does not have rights to reserves below the min\n"
         "\t * watermark then subtract the high-atomic reserves. This will\n"
         "\t * over-estimate the size of the atomic reserve but it avoids a search.\n"
         "\t */\n"
         "\tif (likely(!(alloc_flags & ALLOC_RESERVES)))\n"
         "\t\tunusable_free += z->nr_reserved_highatomic;",
         T),
        # __zone_watermark_ok(): reserve rights restructured.
        ("mm/page_alloc.c",
         "\tlong min = mark;\n"
         "\tint o;\n"
         "\tconst bool alloc_harder = (alloc_flags & (ALLOC_HARDER|ALLOC_OOM));\n"
         "\n"
         "\t/* free_pages may go negative - that's OK */\n"
         "\tfree_pages -= __zone_watermark_unusable_free(z, order, alloc_flags);\n"
         "\n"
         "\tif (alloc_flags & ALLOC_MIN_RESERVE)\n"
         "\t\tmin -= min / 2;\n"
         "\n"
         "\tif (unlikely(alloc_harder)) {\n"
         "\t\t/*\n"
         "\t\t * OOM victims can try even harder than normal ALLOC_HARDER\n"
         "\t\t * users on the grounds that it's definitely going to be in\n"
         "\t\t * the exit path shortly and free memory. Any allocation it\n"
         "\t\t * makes during the free path will be small and short-lived.\n"
         "\t\t */\n"
         "\t\tif (alloc_flags & ALLOC_OOM)\n"
         "\t\t\tmin -= min / 2;\n"
         "\t\telse\n"
         "\t\t\tmin -= min / 4;\n"
         "\t}",
         "\tlong min = mark;\n"
         "\tint o;\n"
         "\n"
         "\t/* free_pages may go negative - that's OK */\n"
         "\tfree_pages -= __zone_watermark_unusable_free(z, order, alloc_flags);\n"
         "\n"
         "\tif (unlikely(alloc_flags & ALLOC_RESERVES)) {\n"
         "\t\t/*\n"
         "\t\t * __GFP_HIGH allows access to 50% of the min reserve as well\n"
         "\t\t * as OOM.\n"
         "\t\t */\n"
         "\t\tif (alloc_flags & ALLOC_MIN_RESERVE) {\n"
         "\t\t\tmin -= min / 2;\n"
         "\n"
         "\t\t\t/*\n"
         "\t\t\t * Non-blocking allocations (e.g. GFP_ATOMIC) can\n"
         "\t\t\t * access more reserves than just __GFP_HIGH. Other\n"
         "\t\t\t * non-blocking allocations requests such as GFP_NOWAIT\n"
         "\t\t\t * or (GFP_KERNEL & ~__GFP_DIRECT_RECLAIM) do not get\n"
         "\t\t\t * access to the min reserve.\n"
         "\t\t\t */\n"
         "\t\t\tif (alloc_flags & ALLOC_NON_BLOCK)\n"
         "\t\t\t\tmin -= min / 4;\n"
         "\t\t}\n"
         "\n"
         "\t\t/*\n"
         "\t\t * OOM victims can try even harder than the normal reserve\n"
         "\t\t * users on the grounds that it's definitely going to be in\n"
         "\t\t * the exit path shortly and free memory. Any allocation it\n"
         "\t\t * makes during the free path will be small and short-lived.\n"
         "\t\t */\n"
         "\t\tif (alloc_flags & ALLOC_OOM)\n"
         "\t\t\tmin -= min / 2;\n"
         "\t}",
         T),
        # __zone_watermark_ok(): HIGHATOMIC/OOM may use the highatomic area.
        ("mm/page_alloc.c",
         "\t\tif (alloc_harder && !free_area_empty(area, MIGRATE_HIGHATOMIC))\n"
         "\t\t\treturn true;\n"
         "\t}",
         "\t\tif ((alloc_flags & (ALLOC_HIGHATOMIC|ALLOC_OOM)) &&\n"
         "\t\t    !free_area_empty(area, MIGRATE_HIGHATOMIC)) {\n"
         "\t\t\treturn true;\n"
         "\t\t}\n"
         "\t}",
         T),
        # get_page_from_freelist(): reserve highatomic only for HIGHATOMIC.
        ("mm/page_alloc.c",
         "\t\t\tif (unlikely(order && (alloc_flags & ALLOC_HARDER)))\n"
         "\t\t\t\treserve_highatomic_pageblock(page, zone, order);",
         "\t\t\tif (unlikely(alloc_flags & ALLOC_HIGHATOMIC))\n"
         "\t\t\t\treserve_highatomic_pageblock(page, zone, order);",
         T),
        # rmqueue_buddy(): HIGHATOMIC flag gates the highatomic steal
        # (AOSP's trace_mm_page_alloc_zone_locked stays).
        ("mm/page_alloc.c",
         "\t\tif (order > 0 && alloc_flags & ALLOC_HARDER) {\n"
         "\t\t\tpage = __rmqueue_smallest(zone, order, MIGRATE_HIGHATOMIC);\n"
         "\t\t\tif (page)\n"
         "\t\t\t\ttrace_mm_page_alloc_zone_locked(page, order, migratetype);\n"
         "\t\t}",
         "\t\tif (alloc_flags & ALLOC_HIGHATOMIC) {\n"
         "\t\t\tpage = __rmqueue_smallest(zone, order, MIGRATE_HIGHATOMIC);\n"
         "\t\t\tif (page)\n"
         "\t\t\t\ttrace_mm_page_alloc_zone_locked(page, order, migratetype);\n"
         "\t\t}",
         T),
        # rmqueue_buddy(): OOM and non-blocking failures may fall back to
        # the highatomic area before giving up.
        ("mm/page_alloc.c",
         "\t\t\tif (!page)\n"
         "\t\t\t\tpage = __rmqueue(zone, order, migratetype,\n"
         "\t\t\t\t\t\talloc_flags);\n"
         "\t\t}",
         "\t\t\tif (!page) {\n"
         "\t\t\t\tpage = __rmqueue(zone, order, migratetype,\n"
         "\t\t\t\t\t\talloc_flags);\n"
         "\n"
         "\t\t\t\t/*\n"
         "\t\t\t\t * If the allocation fails, allow OOM handling and\n"
         "\t\t\t\t * order-0 (atomic) allocs access to HIGHATOMIC\n"
         "\t\t\t\t * reserves as failing now is worse than failing a\n"
         "\t\t\t\t * high-order atomic allocation in the future.\n"
         "\t\t\t\t */\n"
         "\t\t\t\tif (!page && (alloc_flags & (ALLOC_OOM|ALLOC_NON_BLOCK)))\n"
         "\t\t\t\t\tpage = __rmqueue_smallest(zone, order, MIGRATE_HIGHATOMIC);\n"
         "\t\t\t}\n"
         "\t\t}",
         T),
        # gfp_to_alloc_flags(): order parameter for the HIGHATOMIC decision.
        ("mm/page_alloc.c",
         "gfp_to_alloc_flags(gfp_t gfp_mask)\n"
         "{\n"
         "\tunsigned int alloc_flags = ALLOC_WMARK_MIN | ALLOC_CPUSET;",
         "gfp_to_alloc_flags(gfp_t gfp_mask, unsigned int order)\n"
         "{\n"
         "\tunsigned int alloc_flags = ALLOC_WMARK_MIN | ALLOC_CPUSET;",
         T),
        ("mm/page_alloc.c",
         "\talloc_flags = gfp_to_alloc_flags(gfp_mask);",
         "\talloc_flags = gfp_to_alloc_flags(gfp_mask, order);",
         T),
        # gfp_to_alloc_flags(): non-blocking means !__GFP_DIRECT_RECLAIM;
        # HIGHATOMIC only for __GFP_HIGH; cpuset bypass only with reserves.
        ("mm/page_alloc.c",
         "\t * policy or is asking for __GFP_HIGH memory.  GFP_ATOMIC requests will\n"
         "\t * set both ALLOC_HARDER (__GFP_ATOMIC) and ALLOC_MIN_RESERVE(__GFP_HIGH).\n"
         "\t */\n"
         "\talloc_flags |= (__force int)\n"
         "\t\t(gfp_mask & (__GFP_HIGH | __GFP_KSWAPD_RECLAIM));\n"
         "\n"
         "\tif (gfp_mask & __GFP_ATOMIC) {\n"
         "\t\t/*\n"
         "\t\t * Not worth trying to allocate harder for __GFP_NOMEMALLOC even\n"
         "\t\t * if it can't schedule.\n"
         "\t\t */\n"
         "\t\tif (!(gfp_mask & __GFP_NOMEMALLOC))\n"
         "\t\t\talloc_flags |= ALLOC_HARDER;\n"
         "\t\t/*\n"
         "\t\t * Ignore cpuset mems for GFP_ATOMIC rather than fail, see the\n"
         "\t\t * comment for __cpuset_node_allowed().\n"
         "\t\t */\n"
         "\t\talloc_flags &= ~ALLOC_CPUSET;",
         "\t * policy or is asking for __GFP_HIGH memory.  GFP_ATOMIC requests will\n"
         "\t * set both ALLOC_NON_BLOCK and ALLOC_MIN_RESERVE(__GFP_HIGH).\n"
         "\t */\n"
         "\talloc_flags |= (__force int)\n"
         "\t\t(gfp_mask & (__GFP_HIGH | __GFP_KSWAPD_RECLAIM));\n"
         "\n"
         "\tif (!(gfp_mask & __GFP_DIRECT_RECLAIM)) {\n"
         "\t\t/*\n"
         "\t\t * Not worth trying to allocate harder for __GFP_NOMEMALLOC even\n"
         "\t\t * if it can't schedule.\n"
         "\t\t */\n"
         "\t\tif (!(gfp_mask & __GFP_NOMEMALLOC)) {\n"
         "\t\t\talloc_flags |= ALLOC_NON_BLOCK;\n"
         "\n"
         "\t\t\tif (order > 0 && (alloc_flags & ALLOC_MIN_RESERVE))\n"
         "\t\t\t\talloc_flags |= ALLOC_HIGHATOMIC;\n"
         "\t\t}\n"
         "\n"
         "\t\t/*\n"
         "\t\t * Ignore cpuset mems for non-blocking __GFP_HIGH (probably\n"
         "\t\t * GFP_ATOMIC) rather than fail, see the comment for\n"
         "\t\t * __cpuset_node_allowed().\n"
         "\t\t */\n"
         "\t\tif (alloc_flags & ALLOC_MIN_RESERVE)\n"
         "\t\t\talloc_flags &= ~ALLOC_CPUSET;",
         T),
        # slowpath: non-failing allocations get reserve access, not harder.
        ("mm/page_alloc.c",
         "\t\t/*\n"
         "\t\t * Help non-failing allocations by giving them access to memory\n"
         "\t\t * reserves but do not use ALLOC_NO_WATERMARKS because this\n"
         "\t\t * could deplete whole memory reserves which would just make\n"
         "\t\t * the situation worse\n"
         "\t\t */\n"
         "\t\tpage = __alloc_pages_cpuset_fallback(gfp_mask, order, ALLOC_HARDER, ac);",
         "\t\t/*\n"
         "\t\t * Help non-failing allocations by giving some access to memory\n"
         "\t\t * reserves normally used for high priority non-blocking\n"
         "\t\t * allocations but do not use ALLOC_NO_WATERMARKS because this\n"
         "\t\t * could deplete whole memory reserves which would just make\n"
         "\t\t * the situation worse.\n"
         "\t\t */\n"
         "\t\tpage = __alloc_pages_cpuset_fallback(gfp_mask, order, ALLOC_MIN_RESERVE, ac);",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        # Batch 8 adds the mode argument to the same rmqueue_buddy() call
        # after this group has installed the highatomic retry block.  Treat
        # that composed end state as already present on a second pass.
        page_alloc = ctx.read("mm/page_alloc.c")
        if ("enum rmqueue_mode rmqm = RMQUEUE_NORMAL" in page_alloc and
                "If the allocation fails, allow OOM handling" in page_alloc):
            return "already_present", "superseded by pagealloc_fallback_reuse"
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# THP __GFP_THISNODE: compact only, never reclaim (5.15.202, 0eac511c7657)
# ---------------------------------------------------------------------------

def _thisnode_thp_apply(ctx):
    old = """			    compact_result == COMPACT_DEFERRED)
				goto nopage;

			/*
			 * Looks like reclaim/compaction is worth trying, but
"""
    new = """			    compact_result == COMPACT_DEFERRED)
				goto nopage;

			/*
			 * THP page faults may attempt local node only first,
			 * but are then allowed to only compact, not reclaim,
			 * see alloc_pages_mpol().
			 *
			 * Compaction can fail for other reasons than those
			 * checked above and we don't want such THP allocations
			 * to put reclaim pressure on a single node in a
			 * situation where other nodes might have plenty of
			 * available memory.
			 */
			if (gfp_mask & __GFP_THISNODE)
				goto nopage;

			/*
			 * Looks like reclaim/compaction is worth trying, but
"""
    status, _results, detail = apply_steps(ctx, [("mm/page_alloc.c", old, new, T)])
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# cpuset insane-config early bail-out (5.15.191, c635a42d9b74)
# ---------------------------------------------------------------------------

def _cpuset_bailout_apply(ctx):
    steps = [
        ("include/linux/cpuset.h",
         "extern struct static_key_false cpusets_pre_enable_key;\nextern struct static_key_false cpusets_enabled_key;\nstatic inline bool cpusets_enabled(void)",
         "extern struct static_key_false cpusets_pre_enable_key;\nextern struct static_key_false cpusets_enabled_key;\nextern struct static_key_false cpusets_insane_config_key;\n\nstatic inline bool cpusets_enabled(void)",
         T),
        ("include/linux/cpuset.h",
         "\tstatic_branch_dec_cpuslocked(&cpusets_pre_enable_key);\n}\n\nextern int cpuset_init(void);",
         "\tstatic_branch_dec_cpuslocked(&cpusets_pre_enable_key);\n}\n\n"
         "/*\n"
         " * This will get enabled whenever a cpuset configuration is considered\n"
         " * unsupportable in general. E.g. movable only node which cannot satisfy\n"
         " * any non movable allocations (see update_nodemask). Page allocator\n"
         " * needs to make additional checks for those configurations and this\n"
         " * check is meant to guard those checks without any overhead for sane\n"
         " * configurations.\n"
         " */\n"
         "static inline bool cpusets_insane_config(void)\n"
         "{\n"
         "\treturn static_branch_unlikely(&cpusets_insane_config_key);\n"
         "}\n"
         "\n"
         "extern int cpuset_init(void);",
         T),
        ("include/linux/cpuset.h",
         "static inline bool cpusets_enabled(void) { return false; }\n\nstatic inline int cpuset_init(void) { return 0; }",
         "static inline bool cpusets_enabled(void) { return false; }\n\nstatic inline bool cpusets_insane_config(void) { return false; }\n\nstatic inline int cpuset_init(void) { return 0; }",
         T),
        ("include/linux/mmzone.h",
         "#define for_each_zone_zonelist(zone, z, zlist, highidx) \\\n\tfor_each_zone_zonelist_nodemask(zone, z, zlist, highidx, NULL)\n\n#ifdef CONFIG_SPARSEMEM",
         "#define for_each_zone_zonelist(zone, z, zlist, highidx) \\\n\tfor_each_zone_zonelist_nodemask(zone, z, zlist, highidx, NULL)\n\n"
         "/* Whether the 'nodes' are all movable nodes */\n"
         "static inline bool movable_only_nodes(nodemask_t *nodes)\n"
         "{\n"
         "\tstruct zonelist *zonelist;\n"
         "\tstruct zoneref *z;\n"
         "\tint nid;\n"
         "\n"
         "\tif (nodes_empty(*nodes))\n"
         "\t\treturn false;\n"
         "\n"
         "\t/*\n"
         "\t * We can chose arbitrary node from the nodemask to get a\n"
         "\t * zonelist as they are interlinked. We just need to find\n"
         "\t * at least one zone that can satisfy kernel allocations.\n"
         "\t */\n"
         "\tnid = first_node(*nodes);\n"
         "\tzonelist = &NODE_DATA(nid)->node_zonelists[ZONELIST_FALLBACK];\n"
         "\tz = first_zones_zonelist(zonelist, ZONE_NORMAL,\tnodes);\n"
         "\treturn (!z->zone) ? true : false;\n"
         "}\n"
         "\n\n#ifdef CONFIG_SPARSEMEM",
         T),
        ("kernel/cgroup/cpuset.c",
         "DEFINE_STATIC_KEY_FALSE(cpusets_pre_enable_key);\nDEFINE_STATIC_KEY_FALSE(cpusets_enabled_key);\n\n/* See \"Frequency meter\" comments, below. */",
         "DEFINE_STATIC_KEY_FALSE(cpusets_pre_enable_key);\nDEFINE_STATIC_KEY_FALSE(cpusets_enabled_key);\n\n"
         "/*\n"
         " * There could be abnormal cpuset configurations for cpu or memory\n"
         " * node binding, add this key to provide a quick low-cost judgement\n"
         " * of the situation.\n"
         " */\n"
         "DEFINE_STATIC_KEY_FALSE(cpusets_insane_config_key);\n"
         "\n/* See \"Frequency meter\" comments, below. */",
         T),
        ("kernel/cgroup/cpuset.c",
         "static DECLARE_WAIT_QUEUE_HEAD(cpuset_attach_wq);\n\n/*\n * Cgroup v2 behavior is used on the \"cpus\" and \"mems\" control files when",
         "static DECLARE_WAIT_QUEUE_HEAD(cpuset_attach_wq);\n\n"
         "static inline void check_insane_mems_config(nodemask_t *nodes)\n"
         "{\n"
         "\tif (!cpusets_insane_config() &&\n"
         "\t\tmovable_only_nodes(nodes)) {\n"
         "\t\tstatic_branch_enable_cpuslocked(&cpusets_insane_config_key);\n"
         "\t\tpr_info(\"Unsupported (movable nodes only) cpuset configuration detected (nmask=%*pbl)!\\n\"\n"
         "\t\t\t\"Cpuset allocations might fail even with a lot of memory available.\\n\",\n"
         "\t\t\tnodemask_pr_args(nodes));\n"
         "\t}\n"
         "}\n"
         "\n/*\n * Cgroup v2 behavior is used on the \"cpus\" and \"mems\" control files when",
         T),
        ("kernel/cgroup/cpuset.c",
         "\tif (retval < 0)\n\t\tgoto done;\n\n\tspin_lock_irq(&callback_lock);\n\tcs->mems_allowed = trialcs->mems_allowed;",
         "\tif (retval < 0)\n\t\tgoto done;\n\n\tcheck_insane_mems_config(&trialcs->mems_allowed);\n\n\tspin_lock_irq(&callback_lock);\n\tcs->mems_allowed = trialcs->mems_allowed;",
         T),
        ("kernel/cgroup/cpuset.c",
         "\tmems_updated = !nodes_equal(new_mems, cs->effective_mems);\n\n\tif (is_in_v2_mode())",
         "\tmems_updated = !nodes_equal(new_mems, cs->effective_mems);\n\n\tif (mems_updated)\n\t\tcheck_insane_mems_config(&new_mems);\n\n\tif (is_in_v2_mode())",
         T),
        ("mm/page_alloc.c",
         "\tif (!ac->preferred_zoneref->zone)\n\t\tgoto nopage;\n\n\tif (alloc_flags & ALLOC_KSWAPD)\n\t\twake_all_kswapds(order, gfp_mask, ac);",
         "\tif (!ac->preferred_zoneref->zone)\n\t\tgoto nopage;\n\n"
         "\t/*\n"
         "\t * Check for insane configurations where the cpuset doesn't contain\n"
         "\t * any suitable zone to satisfy the request - e.g. non-movable\n"
         "\t * GFP_HIGHUSER allocations from MOVABLE nodes only.\n"
         "\t */\n"
         "\tif (cpusets_insane_config() && (gfp_mask & __GFP_HARDWALL)) {\n"
         "\t\tstruct zoneref *z = first_zones_zonelist(ac->zonelist,\n"
         "\t\t\t\t\tac->highest_zoneidx,\n"
         "\t\t\t\t\t&cpuset_current_mems_allowed);\n"
         "\t\tif (!z->zone)\n"
         "\t\t\tgoto nopage;\n"
         "\t}\n"
         "\n\tif (alloc_flags & ALLOC_KSWAPD)\n\t\twake_all_kswapds(order, gfp_mask, ac);",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# percpu_pagelist_high_fraction lock-free reads (5.15.200, eda99622e6f3)
# ---------------------------------------------------------------------------

def _pagelist_lockfree_apply(ctx):
    old = """	int old_percpu_pagelist_high_fraction;
	int ret;

	mutex_lock(&pcp_batch_high_lock);
	old_percpu_pagelist_high_fraction = percpu_pagelist_high_fraction;

	ret = proc_dointvec_minmax(table, write, buffer, length, ppos);
	if (!write || ret < 0)
		goto out;

	/* Sanity checking to avoid pcp imbalance */
"""
    new = """	int old_percpu_pagelist_high_fraction;
	int ret;

	/*
	 * Avoid using pcp_batch_high_lock for reads as the value is read
	 * atomically and a race with offlining is harmless.
	 */

	if (!write)
		return proc_dointvec_minmax(table, write, buffer, length, ppos);

	mutex_lock(&pcp_batch_high_lock);
	old_percpu_pagelist_high_fraction = percpu_pagelist_high_fraction;

	ret = proc_dointvec_minmax(table, write, buffer, length, ppos);
	if (ret < 0)
		goto out;

	/* Sanity checking to avoid pcp imbalance */
"""
    status, _results, detail = apply_steps(ctx, [("mm/page_alloc.c", old, new, T)])
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# cgroup root_list RCU safety (5.15.168, de77545c72c4)
# ---------------------------------------------------------------------------

def _cgroup_rcu_apply(ctx):
    steps = [
        ("include/linux/cgroup-defs.h",
         "\n\t/* A list running through the active hierarchies */\n\tstruct list_head root_list;\n\n\t/* Hierarchy-specific flags */",
         "\n\t/* A list running through the active hierarchies */\n\tstruct list_head root_list;\n\tstruct rcu_head rcu;\n\n\t/* Hierarchy-specific flags */",
         T),
        ("kernel/cgroup/cgroup-internal.h",
         "#define for_each_root(root)\t\t\t\t\t\t\\\n\tlist_for_each_entry((root), &cgroup_roots, root_list)",
         "#define for_each_root(root)\t\t\t\t\t\t\\\n\tlist_for_each_entry_rcu((root), &cgroup_roots, root_list,\t\\\n\t\t\t\tlockdep_is_held(&cgroup_mutex))",
         T),
        ("kernel/cgroup/cgroup.c",
         "void cgroup_free_root(struct cgroup_root *root)\n{\n\tkfree(root);\n}",
         "void cgroup_free_root(struct cgroup_root *root)\n{\n\tkfree_rcu(root, rcu);\n}",
         T),
        ("kernel/cgroup/cgroup.c",
         "\tif (!list_empty(&root->root_list)) {\n\t\tlist_del(&root->root_list);\n\t\tcgroup_root_count--;\n\t}",
         "\tif (!list_empty(&root->root_list)) {\n\t\tlist_del_rcu(&root->root_list);\n\t\tcgroup_root_count--;\n\t}",
         T),
        ("kernel/cgroup/cgroup.c",
         "\t}\n\trcu_read_unlock();\n\n\tBUG_ON(!res);\n\treturn res;\n}",
         "\t}\n\trcu_read_unlock();\n\n\treturn res;\n}",
         T),
        ("kernel/cgroup/cgroup.c",
         "\tstruct cgroup *res = NULL;\n\n\tlockdep_assert_held(&cgroup_mutex);\n\tlockdep_assert_held(&css_set_lock);",
         "\tstruct cgroup *res = NULL;\n\n\tlockdep_assert_held(&css_set_lock);",
         T),
        ("kernel/cgroup/cgroup.c",
         "/*\n * Return the cgroup for \"task\" from the given hierarchy. Must be\n * called with cgroup_mutex and css_set_lock held.\n */",
         "/*\n * Return the cgroup for \"task\" from the given hierarchy. Must be\n * called with css_set_lock held to prevent task's groups from being modified.\n"
         " * Must be called with either cgroup_mutex or rcu read lock to prevent the\n * cgroup root from being destroyed.\n */",
         T),
        ("kernel/cgroup/cgroup.c",
         "\tINIT_LIST_HEAD(&root->root_list);\n\tatomic_set(&root->nr_cgrps, 1);",
         "\tINIT_LIST_HEAD_RCU(&root->root_list);\n\tatomic_set(&root->nr_cgrps, 1);",
         T),
        ("kernel/cgroup/cgroup.c",
         "\tlist_add(&root->root_list, &cgroup_roots);\n\tcgroup_root_count++;",
         "\tlist_add_rcu(&root->root_list, &cgroup_roots);\n\tcgroup_root_count++;",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# cgroup destroy workqueue split (5.15.194, f2795d1b9250)
# ---------------------------------------------------------------------------

def _cgroup_wq_split_apply(ctx):
    steps = [
        ("kernel/cgroup/cgroup.c",
         " * of concurrent destructions.  Use a separate workqueue so that cgroup\n"
         " * destruction work items don't end up filling up max_active of system_wq\n"
         " * which may lead to deadlock.\n"
         " */\n"
         "static struct workqueue_struct *cgroup_destroy_wq;",
         " * of concurrent destructions.  Use a separate workqueue so that cgroup\n"
         " * destruction work items don't end up filling up max_active of system_wq\n"
         " * which may lead to deadlock.\n"
         " *\n"
         " * A cgroup destruction should enqueue work sequentially to:\n"
         " * cgroup_offline_wq: use for css offline work\n"
         " * cgroup_release_wq: use for css release work\n"
         " * cgroup_free_wq: use for free work\n"
         " *\n"
         " * Rationale for using separate workqueues:\n"
         " * The cgroup root free work may depend on completion of other css offline\n"
         " * operations. If all tasks were enqueued to a single workqueue, this could\n"
         " * create a deadlock scenario where:\n"
         " * - Free work waits for other css offline work to complete.\n"
         " * - But other css offline work is queued after free work in the same queue.\n"
         " *\n"
         " * Example deadlock scenario with single workqueue (cgroup_destroy_wq):\n"
         " * 1. umount net_prio\n"
         " * 2. net_prio root destruction enqueues work to cgroup_destroy_wq (CPUx)\n"
         " * 3. perf_event CSS A offline enqueues work to same cgroup_destroy_wq (CPUx)\n"
         " * 4. net_prio cgroup_destroy_root->cgroup_lock_and_drain_offline.\n"
         " * 5. net_prio root destruction blocks waiting for perf_event CSS A offline,\n"
         " *    which can never complete as it's behind in the same queue and\n"
         " *    workqueue's max_active is 1.\n"
         " */\n"
         "static struct workqueue_struct *cgroup_offline_wq;\n"
         "static struct workqueue_struct *cgroup_release_wq;\n"
         "static struct workqueue_struct *cgroup_free_wq;",
         T),
        ("kernel/cgroup/cgroup.c",
         "\tINIT_RCU_WORK(&css->destroy_rwork, css_free_rwork_fn);\n\tqueue_rcu_work(cgroup_destroy_wq, &css->destroy_rwork);\n}\n\nstatic void css_release(struct percpu_ref *ref)",
         "\tINIT_RCU_WORK(&css->destroy_rwork, css_free_rwork_fn);\n\tqueue_rcu_work(cgroup_free_wq, &css->destroy_rwork);\n}\n\nstatic void css_release(struct percpu_ref *ref)",
         T),
        ("kernel/cgroup/cgroup.c",
         "\tINIT_WORK(&css->destroy_work, css_release_work_fn);\n\tqueue_work(cgroup_destroy_wq, &css->destroy_work);\n}\n\nstatic void init_and_link_css(struct cgroup_subsys_state *css,",
         "\tINIT_WORK(&css->destroy_work, css_release_work_fn);\n\tqueue_work(cgroup_release_wq, &css->destroy_work);\n}\n\nstatic void init_and_link_css(struct cgroup_subsys_state *css,",
         T),
        ("kernel/cgroup/cgroup.c",
         "\tlist_del_rcu(&css->rstat_css_node);\n\tINIT_RCU_WORK(&css->destroy_rwork, css_free_rwork_fn);\n\tqueue_rcu_work(cgroup_destroy_wq, &css->destroy_rwork);\n\treturn ERR_PTR(err);",
         "\tlist_del_rcu(&css->rstat_css_node);\n\tINIT_RCU_WORK(&css->destroy_rwork, css_free_rwork_fn);\n\tqueue_rcu_work(cgroup_free_wq, &css->destroy_rwork);\n\treturn ERR_PTR(err);",
         T),
        ("kernel/cgroup/cgroup.c",
         "\t\tINIT_WORK(&css->destroy_work, css_killed_work_fn);\n\t\tqueue_work(cgroup_destroy_wq, &css->destroy_work);",
         "\t\tINIT_WORK(&css->destroy_work, css_killed_work_fn);\n\t\tqueue_work(cgroup_offline_wq, &css->destroy_work);",
         T),
        ("kernel/cgroup/cgroup.c",
         "\tcgroup_destroy_wq = alloc_workqueue(\"cgroup_destroy\", 0, 1);\n\tBUG_ON(!cgroup_destroy_wq);\n\treturn 0;\n}\ncore_initcall(cgroup_wq_init);",
         "\tcgroup_offline_wq = alloc_workqueue(\"cgroup_offline\", 0, 1);\n\tBUG_ON(!cgroup_offline_wq);\n\n"
         "\tcgroup_release_wq = alloc_workqueue(\"cgroup_release\", 0, 1);\n\tBUG_ON(!cgroup_release_wq);\n\n"
         "\tcgroup_free_wq = alloc_workqueue(\"cgroup_free\", 0, 1);\n\tBUG_ON(!cgroup_free_wq);\n"
         "\treturn 0;\n}\ncore_initcall(cgroup_wq_init);",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# memcg memory.reclaim: per-memcg proactive reclaim (android14-6.1, 6.1.y)
# ---------------------------------------------------------------------------

def _memcg_reclaim_apply(ctx):
    # docs/group_recipe.md trap 5, and a chain rather than a single later
    # edit: Batch 37 rewrites the memory_reclaim() handler this group
    # generates -- its call site three times over (the SWAP_CLUSTER_MAX cap,
    # the decaying batch, the swappiness= argument) and its failure path
    # (-ERESTARTSYS) -- so on a second pass the handler's `new` block no
    # longer matches while its `old` anchor (the pristine
    # `static struct cftype memory_files[] = {`) still does, and the whole
    # function would be appended a second time.  The group stops on its own
    # marker instead: text it alone writes, and which no later group touches.
    try:
        _probe = ctx.read("mm/memcontrol.c")
    except FileNotFoundError:
        _probe = ""
    if _b37_rp.MEMORY_RECLAIM_MARKER in _probe:
        return "already_present", (
            "memory.reclaim is already in memcontrol.c")
    steps = [
        # swap.h: reclaim option bits next to the try_to_free prototypes
        ("include/linux/swap.h",
         "extern unsigned long try_to_free_pages(struct zonelist *zonelist, int order,\n"
         "\t\t\t\t\tgfp_t gfp_mask, nodemask_t *mask);\n"
         "extern unsigned long try_to_free_mem_cgroup_pages(struct mem_cgroup *memcg,\n"
         "\t\t\t\t\t\t  unsigned long nr_pages,\n"
         "\t\t\t\t\t\t  gfp_t gfp_mask,\n"
         "\t\t\t\t\t\t  bool may_swap);",
         "extern unsigned long try_to_free_pages(struct zonelist *zonelist, int order,\n"
         "\t\t\t\t\tgfp_t gfp_mask, nodemask_t *mask);\n"
         "\n"
         "/* ABK stable_515_backport: per-memcg proactive reclaim options (android14-6.1). */\n"
         "#define MEMCG_RECLAIM_MAY_SWAP (1 << 1)\n"
         "#define MEMCG_RECLAIM_PROACTIVE (1 << 2)\n"
         "extern unsigned long try_to_free_mem_cgroup_pages(struct mem_cgroup *memcg,\n"
         "\t\t\t\t\t\t  unsigned long nr_pages,\n"
         "\t\t\t\t\t\t  gfp_t gfp_mask,\n"
         "\t\t\t\t\t\t  unsigned int reclaim_options);",
         T),
        # vmscan.c: scan_control learns the proactive bit
        ("mm/vmscan.c",
         "\t/* Can pages be swapped as part of reclaim? */\n"
         "\tunsigned int may_swap:1;\n",
         "\t/* Can pages be swapped as part of reclaim? */\n"
         "\tunsigned int may_swap:1;\n"
         "\n"
         "\t/* ABK stable_515_backport: set for proactive memory.reclaim requests. */\n"
         "\tunsigned int proactive:1;\n",
         T),
        # vmscan.c: try_to_free_mem_cgroup_pages() takes reclaim options
        ("mm/vmscan.c",
         "unsigned long try_to_free_mem_cgroup_pages(struct mem_cgroup *memcg,\n"
         "\t\t\t\t\t   unsigned long nr_pages,\n"
         "\t\t\t\t\t   gfp_t gfp_mask,\n"
         "\t\t\t\t\t   bool may_swap)\n"
         "{\n"
         "\tunsigned long nr_reclaimed;\n"
         "\tunsigned int noreclaim_flag;\n"
         "\tstruct scan_control sc = {\n"
         "\t\t.nr_to_reclaim = max(nr_pages, SWAP_CLUSTER_MAX),\n"
         "\t\t.gfp_mask = (current_gfp_context(gfp_mask) & GFP_RECLAIM_MASK) |\n"
         "\t\t\t\t(GFP_HIGHUSER_MOVABLE & ~GFP_RECLAIM_MASK),\n"
         "\t\t.reclaim_idx = MAX_NR_ZONES - 1,\n"
         "\t\t.target_mem_cgroup = memcg,\n"
         "\t\t.priority = DEF_PRIORITY,\n"
         "\t\t.may_writepage = !laptop_mode,\n"
         "\t\t.may_unmap = 1,\n"
         "\t\t.may_swap = may_swap,\n"
         "\t};",
         "unsigned long try_to_free_mem_cgroup_pages(struct mem_cgroup *memcg,\n"
         "\t\t\t\t\t   unsigned long nr_pages,\n"
         "\t\t\t\t\t   gfp_t gfp_mask,\n"
         "\t\t\t\t\t   unsigned int reclaim_options)\n"
         "{\n"
         "\tunsigned long nr_reclaimed;\n"
         "\tunsigned int noreclaim_flag;\n"
         "\tstruct scan_control sc = {\n"
         "\t\t.nr_to_reclaim = max(nr_pages, SWAP_CLUSTER_MAX),\n"
         "\t\t.gfp_mask = (current_gfp_context(gfp_mask) & GFP_RECLAIM_MASK) |\n"
         "\t\t\t\t(GFP_HIGHUSER_MOVABLE & ~GFP_RECLAIM_MASK),\n"
         "\t\t.reclaim_idx = MAX_NR_ZONES - 1,\n"
         "\t\t.target_mem_cgroup = memcg,\n"
         "\t\t.priority = DEF_PRIORITY,\n"
         "\t\t.may_writepage = !laptop_mode,\n"
         "\t\t.may_unmap = 1,\n"
         "\t\t.may_swap = !!(reclaim_options & MEMCG_RECLAIM_MAY_SWAP),\n"
         "\t\t.proactive = !!(reclaim_options & MEMCG_RECLAIM_PROACTIVE),\n"
         "\t};",
         T),
        # vmscan.c: proactive reclaim does not pollute vmpressure (optional)
        ("mm/vmscan.c",
         "\t\t/* Record the group's reclaim efficiency */\n"
         "\t\tvmpressure(sc->gfp_mask, memcg, false,\n"
         "\t\t\t   sc->nr_scanned - scanned,\n"
         "\t\t\t   sc->nr_reclaimed - reclaimed);",
         "\t\t/* Record the group's reclaim efficiency */\n"
         "\t\tif (!sc->proactive)\n"
         "\t\t\tvmpressure(sc->gfp_mask, memcg, false,\n"
         "\t\t\t\t   sc->nr_scanned - scanned,\n"
         "\t\t\t\t   sc->nr_reclaimed - reclaimed);",
         F),
        ("mm/vmscan.c",
         "\t/* Record the subtree's reclaim efficiency */\n"
         "\tvmpressure(sc->gfp_mask, sc->target_mem_cgroup, true,\n"
         "\t\t   sc->nr_scanned - nr_scanned,\n"
         "\t\t   sc->nr_reclaimed - nr_reclaimed);",
         "\t/* Record the subtree's reclaim efficiency */\n"
         "\tif (!sc->proactive)\n"
         "\t\tvmpressure(sc->gfp_mask, sc->target_mem_cgroup, true,\n"
         "\t\t\t   sc->nr_scanned - nr_scanned,\n"
         "\t\t\t   sc->nr_reclaimed - nr_reclaimed);",
         F),
        ("mm/vmscan.c",
         "\tdo {\n"
         "\t\tvmpressure_prio(sc->gfp_mask, sc->target_mem_cgroup,\n"
         "\t\t\t\tsc->priority);\n"
         "\t\tsc->nr_scanned = 0;",
         "\tdo {\n"
         "\t\tif (!sc->proactive)\n"
         "\t\t\tvmpressure_prio(sc->gfp_mask, sc->target_mem_cgroup,\n"
         "\t\t\t\t\tsc->priority);\n"
         "\t\tsc->nr_scanned = 0;",
         F),
        # memcontrol.c: charge locals carry reclaim options instead of may_swap
        ("mm/memcontrol.c",
         "\tbool passed_oom = false;\n"
         "\tbool may_swap = true;\n"
         "\tbool drained = false;",
         "\tbool passed_oom = false;\n"
         "\tunsigned int reclaim_options = MEMCG_RECLAIM_MAY_SWAP;\n"
         "\tbool drained = false;",
         T),
        ("mm/memcontrol.c",
         "\t} else {\n"
         "\t\tmem_over_limit = mem_cgroup_from_counter(counter, memsw);\n"
         "\t\tmay_swap = false;\n"
         "\t}",
         "\t} else {\n"
         "\t\tmem_over_limit = mem_cgroup_from_counter(counter, memsw);\n"
         "\t\treclaim_options &= ~MEMCG_RECLAIM_MAY_SWAP;\n"
         "\t}",
         T),
        ("mm/memcontrol.c",
         "\t\tpsi_memstall_enter(&pflags);\n"
         "\t\tnr_reclaimed += try_to_free_mem_cgroup_pages(memcg, nr_pages,\n"
         "\t\t\t\t\t\t\t     gfp_mask, true);\n"
         "\t\tpsi_memstall_leave(&pflags);",
         "\t\tpsi_memstall_enter(&pflags);\n"
         "\t\tnr_reclaimed += try_to_free_mem_cgroup_pages(memcg, nr_pages,\n"
         "\t\t\t\t\t\t\t     gfp_mask,\n"
         "\t\t\t\t\t\t\t     MEMCG_RECLAIM_MAY_SWAP);\n"
         "\t\tpsi_memstall_leave(&pflags);",
         T),
        ("mm/memcontrol.c",
         "\tpsi_memstall_enter(&pflags);\n"
         "\tnr_reclaimed = try_to_free_mem_cgroup_pages(mem_over_limit, nr_pages,\n"
         "\t\t\t\t\t\t    gfp_mask, may_swap);\n"
         "\tpsi_memstall_leave(&pflags);",
         "\tpsi_memstall_enter(&pflags);\n"
         "\tnr_reclaimed = try_to_free_mem_cgroup_pages(mem_over_limit, nr_pages,\n"
         "\t\t\t\t\t\t    gfp_mask, reclaim_options);\n"
         "\tpsi_memstall_leave(&pflags);",
         T),
        ("mm/memcontrol.c",
         "\t\tif (!try_to_free_mem_cgroup_pages(memcg, 1,\n"
         "\t\t\t\t\tGFP_KERNEL, !memsw)) {\n"
         "\t\t\tret = -EBUSY;\n"
         "\t\t\tbreak;",
         "\t\tif (!try_to_free_mem_cgroup_pages(memcg, 1, GFP_KERNEL,\n"
         "\t\t\t\t\tmemsw ? 0 : MEMCG_RECLAIM_MAY_SWAP)) {\n"
         "\t\t\tret = -EBUSY;\n"
         "\t\t\tbreak;",
         T),
        # memcontrol.c: the three remaining bool call sites.  Left as `true`
        # they would silently mean "no swap": true converts to 1, while
        # MEMCG_RECLAIM_MAY_SWAP is (1 << 1), so vmscan's
        # `!!(reclaim_options & MEMCG_RECLAIM_MAY_SWAP)` evaluates to 0.
        ("mm/memcontrol.c",
         "\t\tprogress = try_to_free_mem_cgroup_pages(memcg, 1,\n"
         "\t\t\t\t\t\t\tGFP_KERNEL, true);\n",
         "\t\tprogress = try_to_free_mem_cgroup_pages(memcg, 1, GFP_KERNEL,\n"
         "\t\t\t\t\t\t\tMEMCG_RECLAIM_MAY_SWAP);\n",
         T),
        ("mm/memcontrol.c",
         "\t\treclaimed = try_to_free_mem_cgroup_pages(memcg, nr_pages - high,\n"
         "\t\t\t\t\t\t\t GFP_KERNEL, true);\n",
         "\t\treclaimed = try_to_free_mem_cgroup_pages(memcg, nr_pages - high,\n"
         "\t\t\t\t\tGFP_KERNEL, MEMCG_RECLAIM_MAY_SWAP);\n",
         T),
        ("mm/memcontrol.c",
         "\t\t\tif (!try_to_free_mem_cgroup_pages(memcg, nr_pages - max,\n"
         "\t\t\t\t\t\t\t  GFP_KERNEL, true))\n",
         "\t\t\tif (!try_to_free_mem_cgroup_pages(memcg, nr_pages - max,\n"
         "\t\t\t\t\tGFP_KERNEL, MEMCG_RECLAIM_MAY_SWAP))\n",
         T),
        # memcontrol.c: the memory.reclaim write handler (android14-6.1 form)
        ("mm/memcontrol.c",
         "static struct cftype memory_files[] = {",
         "/* ABK stable_515_backport: per-memcg proactive reclaim (android14-6.1 memory.reclaim). */\n"
         "static ssize_t memory_reclaim(struct kernfs_open_file *of, char *buf,\n"
         "\t\t\t      size_t nbytes, loff_t off)\n"
         "{\n"
         "\tstruct mem_cgroup *memcg = mem_cgroup_from_css(of_css(of));\n"
         "\tunsigned int nr_retries = MAX_RECLAIM_RETRIES;\n"
         "\tunsigned long nr_to_reclaim, nr_reclaimed = 0;\n"
         "\tunsigned int reclaim_options;\n"
         "\tint err;\n"
         "\n"
         "\tbuf = strstrip(buf);\n"
         "\terr = page_counter_memparse(buf, \"\", &nr_to_reclaim);\n"
         "\tif (err)\n"
         "\t\treturn err;\n"
         "\n"
         "\treclaim_options\t= MEMCG_RECLAIM_MAY_SWAP | MEMCG_RECLAIM_PROACTIVE;\n"
         "\twhile (nr_reclaimed < nr_to_reclaim) {\n"
         "\t\tunsigned long reclaimed;\n"
         "\n"
         "\t\tif (signal_pending(current))\n"
         "\t\t\treturn -EINTR;\n"
         "\n"
         "\t\t/*\n"
         "\t\t * This is the final attempt, drain percpu lru caches in the\n"
         "\t\t * hope of introducing more evictable pages for\n"
         "\t\t * try_to_free_mem_cgroup_pages().\n"
         "\t\t */\n"
         "\t\tif (!nr_retries)\n"
         "\t\t\tlru_add_drain_all();\n"
         "\n"
         "\t\treclaimed = try_to_free_mem_cgroup_pages(memcg,\n"
         "\t\t\t\t\t\tnr_to_reclaim - nr_reclaimed,\n"
         "\t\t\t\t\t\tGFP_KERNEL, reclaim_options);\n"
         "\n"
         "\t\tif (!reclaimed && !nr_retries--)\n"
         "\t\t\treturn -EAGAIN;\n"
         "\n"
         "\t\tnr_reclaimed += reclaimed;\n"
         "\t}\n"
         "\n"
         "\treturn nbytes;\n"
         "}\n"
         "\n"
         "static struct cftype memory_files[] = {",
         T),
        # memcontrol.c: the memory.reclaim cgroup-v2 file entry
        ("mm/memcontrol.c",
         "\t{\n"
         "\t\t.name = \"oom.group\",\n"
         "\t\t.flags = CFTYPE_NOT_ON_ROOT | CFTYPE_NS_DELEGATABLE,\n"
         "\t\t.seq_show = memory_oom_group_show,\n"
         "\t\t.write = memory_oom_group_write,\n"
         "\t},\n"
         "\t{ }\t/* terminate */",
         "\t{\n"
         "\t\t.name = \"oom.group\",\n"
         "\t\t.flags = CFTYPE_NOT_ON_ROOT | CFTYPE_NS_DELEGATABLE,\n"
         "\t\t.seq_show = memory_oom_group_show,\n"
         "\t\t.write = memory_oom_group_write,\n"
         "\t},\n"
         "\t{\n"
         "\t\t.name = \"reclaim\",\n"
         "\t\t.flags = CFTYPE_NS_DELEGATABLE,\n"
         "\t\t.write = memory_reclaim,\n"
         "\t},\n"
         "\t{ }\t/* terminate */",
         T),
        # cgroup-v2.rst: document the file itself.  5.15's manual predates
        # memory.reclaim (94968384dde1 lands in v5.16), so without this the
        # module would add a UAPI file that the kernel's own manual does not
        # describe -- and the swappiness= group below amends this very section,
        # so it has to exist for that hunk to be the upstream amendment rather
        # than an invention.  Text is 94968384dde1's, at the same place in the
        # list (after memory.max, before memory.oom.group).
        ("Documentation/admin-guide/cgroup-v2.rst",
         "\tutility is limited to providing the final safety net.\n"
         "\n"
         "  memory.oom.group\n",
         "\tutility is limited to providing the final safety net.\n"
         "\n"
         "  memory.reclaim\n"
         "\tA write-only nested-keyed file which exists for all cgroups.\n"
         "\n"
         "\tThis is a simple interface to trigger memory reclaim in the\n"
         "\ttarget cgroup.\n"
         "\n"
         "\tThis file accepts a single key, the number of bytes to reclaim.\n"
         "\tNo nested keys are currently supported.\n"
         "\n"
         "\tExample::\n"
         "\n"
         "\t  echo \"1G\" > memory.reclaim\n"
         "\n"
         "\tThe interface can be later extended with nested keys to\n"
         "\tconfigure the reclaim behavior. For example, specify the\n"
         "\ttype of memory to reclaim from (anon, file, ..).\n"
         "\n"
         "\tPlease note that the kernel can over or under reclaim from\n"
         "\tthe target cgroup. If less bytes are reclaimed than the\n"
         "\tspecified amount, -EAGAIN is returned.\n"
         "\n"
         "  memory.oom.group\n",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# MGLRU performance optimizations, v4 (v6.14, Yu Zhao) -- re-authored onto the
# 6.1-shape (page-based) MGLRU the android13-5.15 ACK carries.  The upstream
# diffs are folio-era and assume 6.2-6.13 intermediates that this baseline
# never gained, so every anchor below is the baseline's own text, not the
# upstream old block.
# ---------------------------------------------------------------------------

def _mglru_clean_workingset_apply(ctx):
    """v6.14 9cbfd1c3c83b (clean up workingset), portable remainder.

    Upstream moves the workingset_refault() lock assertion to cover both the
    conventional and the MGLRU paths.  This baseline's workingset_refault()
    already covers both paths from a single entry (lru_gen_refault() is called
    inline), so the assertion lands at the entry.  The other two hunks of the
    commit restructure workingset_test_recent()/lru_gen_test_recent(), which
    the 6.1-shape baseline does not have, and pin the eviction memcg for a
    caller that sleeps outside RCU -- the baseline path stays inside one
    rcu_read_lock() section and has no tryget/put pair to mirror, so those
    hunks are intentionally not carried.
    """
    steps = [
        # Anchored on the last two declarations: 5.15.194+ inserts the
        # android_vh_count_workingset_refault() hook between them and the MGLRU
        # dispatch, so a wider block only matches on .167/.178.  The
        # workingset_eviction() decl block also ends in "int memcgid;", hence
        # the leading "bool workingset;".
        ("mm/workingset.c",
         "\tbool workingset;\n"
         "\tint memcgid;\n",
         "\tbool workingset;\n"
         "\tint memcgid;\n"
         "\n"
         "\tVM_BUG_ON_PAGE(!PageLocked(page), page);\n",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _mglru_optimize_deactivation_apply(ctx):
    """v6.14 cc8ec7be78ff (optimize deactivation), pagevec re-authoring.

    Upstream short-circuits deactivate_file_folio()/folio_deactivate() under
    MGLRU: instead of shuffling the page between LRU lists just to drop its
    refs, clear LRU_REFS in place and skip the move entirely when the page
    already sits in the oldest generation.  This baseline drains deactivations
    through pagevecs (pagevec_lru_move_fn), so the same semantics land in
    deactivate_file_page()/deactivate_page()/lru_lazyfree_fn() with a
    page-based lru_gen_clear_refs() helper.
    """
    steps = [
        # lru_gen_clear_refs(): clear LRU_REFS in place, report whether the
        # page can avoid the LRU shuffle (already in the oldest generation).
        ("mm/swap.c",
         "#else\n"
         "static void page_inc_refs(struct page *page)\n"
         "{\n"
         "}\n"
         "#endif /* CONFIG_LRU_GEN */\n",
         "#else\n"
         "static void page_inc_refs(struct page *page)\n"
         "{\n"
         "}\n"
         "#endif /* CONFIG_LRU_GEN */\n"
         "\n"
         "#ifdef CONFIG_LRU_GEN\n"
         "/* ABK stable_515_backport: v6.14 cc8ec7be78ff, pagevec shape */\n"
         "static bool lru_gen_clear_refs(struct page *page)\n"
         "{\n"
         "\tint gen;\n"
         "\tint type;\n"
         "\tstruct lruvec *lruvec;\n"
         "\n"
         "\tgen = page_lru_gen(page);\n"
         "\tif (gen < 0)\n"
         "\t\treturn true;\n"
         "\n"
         "\ttype = page_is_file_lru(page);\n"
         "\t/*\n"
         "\t * LRU_REFS_FLAGS is a mm/vmscan.c file-local define on this tree\n"
         "\t * (BIT(PG_referenced) | BIT(PG_workingset)); spell it out here\n"
         "\t * because mm/swap.c cannot see it.\n"
         "\t */\n"
         "\tset_mask_bits(&page->flags, LRU_REFS_MASK | BIT(PG_referenced) |\n"
         "\t\t\t      BIT(PG_workingset), 0);\n"
         "\n"
         "\tlruvec = mem_cgroup_page_lruvec(page);\n"
         "\t/* whether can do without shuffling under the LRU lock */\n"
         "\treturn gen == lru_gen_from_seq(READ_ONCE(lruvec->lrugen.min_seq[type]));\n"
         "}\n"
         "#else\n"
         "static bool lru_gen_clear_refs(struct page *page)\n"
         "{\n"
         "\treturn false;\n"
         "}\n"
         "#endif /* CONFIG_LRU_GEN */\n",
         T),
        # PGDEACTIVATE accounting: under MGLRU every deactivation counts, not
        # just ones coming off the active list.
        ("mm/swap.c",
         "static void lru_deactivate_file_fn(struct page *page, struct lruvec *lruvec)\n"
         "{\n"
         "\tbool active = PageActive(page);\n",
         "static void lru_deactivate_file_fn(struct page *page, struct lruvec *lruvec)\n"
         "{\n"
         "\tbool active = PageActive(page) || lru_gen_enabled();\n",
         T),
        # lazyfree: drop the tier refs instead of the conventional PG_referenced.
        ("mm/swap.c",
         "\t\tdel_page_from_lru_list(page, lruvec);\n"
         "\t\tClearPageActive(page);\n"
         "\t\tClearPageReferenced(page);\n"
         "\t\t/*\n"
         "\t\t * Lazyfree pages are clean anonymous pages.  They have\n",
         "\t\tdel_page_from_lru_list(page, lruvec);\n"
         "\t\tClearPageActive(page);\n"
         "\t\tif (lru_gen_enabled())\n"
         "\t\t\tlru_gen_clear_refs(page);\n"
         "\t\telse\n"
         "\t\t\tClearPageReferenced(page);\n"
         "\t\t/*\n"
         "\t\t * Lazyfree pages are clean anonymous pages.  They have\n",
         T),
        # deactivate_file_page(): pages in the oldest generation need no move.
        ("mm/swap.c",
         "\tif (PageUnevictable(page))\n"
         "\t\treturn;\n"
         "\n"
         "\tif (likely(get_page_unless_zero(page))) {\n"
         "\t\tstruct pagevec *pvec;\n",
         "\tif (PageUnevictable(page))\n"
         "\t\treturn;\n"
         "\n"
         "\tif (lru_gen_enabled() && lru_gen_clear_refs(page))\n"
         "\t\treturn;\n"
         "\n"
         "\tif (likely(get_page_unless_zero(page))) {\n"
         "\t\tstruct pagevec *pvec;\n",
         T),
        # deactivate_page(): MGLRU decides via refs, the conventional LRU via
        # PG_active.
        ("mm/swap.c",
         "void deactivate_page(struct page *page)\n"
         "{\n"
         "\tif (PageLRU(page) && !PageUnevictable(page) &&\n"
         "\t    (PageActive(page) || lru_gen_enabled())) {\n"
         "\t\tstruct pagevec *pvec;\n"
         "\n"
         "\t\tlocal_lock(&lru_pvecs.lock);\n"
         "\t\tpvec = this_cpu_ptr(&lru_pvecs.lru_deactivate);\n",
         "void deactivate_page(struct page *page)\n"
         "{\n"
         "\tstruct pagevec *pvec;\n"
         "\n"
         "\tif (PageLRU(page) && !PageUnevictable(page)) {\n"
         "\t\tif (lru_gen_enabled() ? lru_gen_clear_refs(page)\n"
         "\t\t\t\t\t      : !PageActive(page))\n"
         "\t\t\treturn;\n"
         "\n"
         "\t\tlocal_lock(&lru_pvecs.lock);\n"
         "\t\tpvec = this_cpu_ptr(&lru_pvecs.lru_deactivate);\n",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _mglru_rework_aging_feedback_apply(ctx):
    """v6.14 798c0330c2ca (rework aging feedback), int-swappiness port.

    Carries: min_seq[] may drift apart by up to MAX_NR_GENS-MIN_NR_GENS-1;
    protected[] gains the first tier (tier-1 -> tier reindex); lru_gen_mm_walk
    carries the full swappiness value instead of a can_swap bool with the
    >MAX_SWAPPINESS "anon only" sentinel; the aging feedback simplifies to
    "age when the oldest evictable generation is exactly MIN_NR_GENS behind".
    Re-authored for the 6.1-shape functions (lru_gen_struct/lists, sort_page,
    evict_pages, age_lruvec, get_nr_to_scan with need_aging); the per-memcg
    swappiness read stays on the baseline's mem_cgroup_swappiness().
    MIN/MAX_SWAPPINESS and get_swappiness()'s !may_swap short-circuit are NOT
    added here: upstream 410abb20acae/68cd9050d871 own them and this module
    lands that series as the batch37 reclaim chain, registered after this
    group -- adding them twice would both duplicate the defines and pull the
    anchor out from under the swappiness-argument group.
    """
    steps = [
        # -- include/linux/mmzone.h: min_seq[] semantics, protected[] tier 0,
        #    walk swappiness --
        ("include/linux/mmzone.h",
         " * stored in min_seq[] separately for anon and file types as clean file pages\n"
         " * can be evicted regardless of swap constraints.\n"
         " *\n"
         " * Normally anon and file min_seq are in sync. But if swapping is constrained,\n"
         " * e.g., out of swap space, file min_seq is allowed to advance and leave anon\n"
         " * min_seq behind.\n",
         " * stored in min_seq[] separately for anon and file types so that they can be\n"
         " * incremented independently. Ideally min_seq[] are kept in sync when both anon\n"
         " * and file types are evictable. However, to adapt to situations like extreme\n"
         " * swappiness, they are allowed to be out of sync by at most\n"
         " * MAX_NR_GENS-MIN_NR_GENS-1.\n",
         T),
        ("include/linux/mmzone.h",
         "\t/* the first tier doesn't need protection, hence the minus one */\n"
         "\tunsigned long protected[NR_HIST_GENS][ANON_AND_FILE][MAX_NR_TIERS - 1];\n",
         "\t/* can only be modified under the LRU lock */\n"
         "\tunsigned long protected[NR_HIST_GENS][ANON_AND_FILE][MAX_NR_TIERS];\n",
         T),
        ("include/linux/mmzone.h",
         "\tint batched;\n"
         "\tbool can_swap;\n"
         "\tbool full_scan;\n",
         "\tint batched;\n"
         "\tint swappiness;\n"
         "\tbool full_scan;\n",
         T),
        # -- mm/vmscan.c: the two evictable-type macros --
        ("mm/vmscan.c",
         "#define for_each_gen_type_zone(gen, type, zone)\t\t\t\t\\\n"
         "\tfor ((gen) = 0; (gen) < MAX_NR_GENS; (gen)++)\t\t\t\\\n"
         "\t\tfor ((type) = 0; (type) < ANON_AND_FILE; (type)++)\t\\\n"
         "\t\t\tfor ((zone) = 0; (zone) < MAX_NR_ZONES; (zone)++)\n",
         "#define for_each_gen_type_zone(gen, type, zone)\t\t\t\t\\\n"
         "\tfor ((gen) = 0; (gen) < MAX_NR_GENS; (gen)++)\t\t\t\\\n"
         "\t\tfor ((type) = 0; (type) < ANON_AND_FILE; (type)++)\t\\\n"
         "\t\t\tfor ((zone) = 0; (zone) < MAX_NR_ZONES; (zone)++)\n"
         "\n"
         "#define evictable_min_seq(min_seq, swappiness)\t\t\t\t\\\n"
         "\tmin((min_seq)[!(swappiness)], (min_seq)[(swappiness) <= MAX_SWAPPINESS])\n"
         "\n"
         "#define for_each_evictable_type(type, swappiness)\t\t\t\\\n"
         "\tfor ((type) = !(swappiness); (type) <= ((swappiness) <= MAX_SWAPPINESS); (type)++)\n",
         T),
        # -- seq_is_valid(): each type may drift within its own bounds --
        ("mm/vmscan.c",
         "static bool __maybe_unused seq_is_valid(struct lruvec *lruvec)\n"
         "{\n"
         "\t/* see the comment on lru_gen_struct */\n"
         "\treturn get_nr_gens(lruvec, LRU_GEN_FILE) >= MIN_NR_GENS &&\n"
         "\t       get_nr_gens(lruvec, LRU_GEN_FILE) <= get_nr_gens(lruvec, LRU_GEN_ANON) &&\n"
         "\t       get_nr_gens(lruvec, LRU_GEN_ANON) <= MAX_NR_GENS;\n",
         "static bool __maybe_unused seq_is_valid(struct lruvec *lruvec)\n"
         "{\n"
         "\tint type;\n"
         "\n"
         "\tfor (type = 0; type < ANON_AND_FILE; type++) {\n"
         "\t\tint n = get_nr_gens(lruvec, type);\n"
         "\n"
         "\t\tif (n < MIN_NR_GENS || n > MAX_NR_GENS)\n"
         "\t\t\treturn false;\n"
         "\t}\n"
         "\n"
         "\treturn true;\n",
         T),
        # -- iterate_mm_list(): size check over the evictable types --
        ("mm/vmscan.c",
         "\tfor (type = !walk->can_swap; type < ANON_AND_FILE; type++) {\n"
         "\t\tsize += type ? get_mm_counter(mm, MM_FILEPAGES) :",
         "\tfor_each_evictable_type(type, walk->swappiness) {\n"
         "\t\tsize += type ? get_mm_counter(mm, MM_FILEPAGES) :",
         T),
        # -- should_skip_vma(): swappiness is the full value now --
        ("mm/vmscan.c",
         "\tif (vma_is_anonymous(vma))\n"
         "\t\treturn !walk->can_swap;\n",
         "\tif (vma_is_anonymous(vma))\n"
         "\t\treturn !walk->swappiness;\n",
         T),
        ("mm/vmscan.c",
         "\tif (shmem_mapping(mapping))\n"
         "\t\treturn !walk->can_swap;\n"
         "\n"
         "\t/* to exclude special mappings like dax, etc. */\n"
         "\treturn !mapping->a_ops->readpage;\n",
         "\tif (shmem_mapping(mapping))\n"
         "\t\treturn !walk->swappiness;\n"
         "\n"
         "\tif (walk->swappiness > MAX_SWAPPINESS)\n"
         "\t\treturn true;\n"
         "\n"
         "\t/* to exclude special mappings like dax, etc. */\n"
         "\treturn !mapping->a_ops->readpage;\n",
         T),
        # -- get_pfn_page(): the COW guard goes away with real swappiness --
        ("mm/vmscan.c",
         "static struct page *get_pfn_page(unsigned long pfn, struct mem_cgroup *memcg,\n"
         "\t\t\t\t struct pglist_data *pgdat, bool can_swap)\n"
         "{\n"
         "\tstruct page *page;\n"
         "\n"
         "\t/* try to avoid unnecessary memory loads */\n"
         "\tif (pfn < pgdat->node_start_pfn || pfn >= pgdat_end_pfn(pgdat))\n"
         "\t\treturn NULL;\n"
         "\n"
         "\tpage = compound_head(pfn_to_page(pfn));\n"
         "\tif (page_to_nid(page) != pgdat->node_id)\n"
         "\t\treturn NULL;\n"
         "\n"
         "\tif (page_memcg_rcu(page) != memcg)\n"
         "\t\treturn NULL;\n"
         "\n"
         "\t/* file VMAs can contain anon pages from COW */\n"
         "\tif (!page_is_file_lru(page) && !can_swap)\n"
         "\t\treturn NULL;\n"
         "\n"
         "\treturn page;\n"
         "}\n",
         "static struct page *get_pfn_page(unsigned long pfn, struct mem_cgroup *memcg,\n"
         "\t\t\t\t struct pglist_data *pgdat)\n"
         "{\n"
         "\tstruct page *page;\n"
         "\n"
         "\t/* try to avoid unnecessary memory loads */\n"
         "\tif (pfn < pgdat->node_start_pfn || pfn >= pgdat_end_pfn(pgdat))\n"
         "\t\treturn NULL;\n"
         "\n"
         "\tpage = compound_head(pfn_to_page(pfn));\n"
         "\tif (page_to_nid(page) != pgdat->node_id)\n"
         "\t\treturn NULL;\n"
         "\n"
         "\tif (page_memcg_rcu(page) != memcg)\n"
         "\t\treturn NULL;\n"
         "\n"
         "\treturn page;\n"
         "}\n",
         T),
        ("mm/vmscan.c",
         "\t\tpage = get_pfn_page(pfn, memcg, pgdat, walk->can_swap);\n"
         "\t\tif (!page)\n"
         "\t\t\tcontinue;\n"
         "\n"
         "\t\tif (!ptep_test_and_clear_young(args->vma, addr, pte + i))\n",
         "\t\tpage = get_pfn_page(pfn, memcg, pgdat);\n"
         "\t\tif (!page)\n"
         "\t\t\tcontinue;\n"
         "\n"
         "\t\tif (!ptep_test_and_clear_young(args->vma, addr, pte + i))\n",
         T),
        ("mm/vmscan.c",
         "\t\tpage = get_pfn_page(pfn, memcg, pgdat, walk->can_swap);\n"
         "\t\tif (!page)\n"
         "\t\t\tgoto next;\n",
         "\t\tpage = get_pfn_page(pfn, memcg, pgdat);\n"
         "\t\tif (!page)\n"
         "\t\t\tgoto next;\n",
         T),
        ("mm/vmscan.c",
         "\tstruct page *page = pvmw->page;\n"
         "\tbool can_swap = !page_is_file_lru(page);\n"
         "\tstruct mem_cgroup *memcg = page_memcg(page);\n",
         "\tstruct page *page = pvmw->page;\n"
         "\tstruct mem_cgroup *memcg = page_memcg(page);\n",
         T),
        ("mm/vmscan.c",
         "\t\tpage = get_pfn_page(pfn, memcg, pgdat, can_swap);\n"
         "\t\tif (!page)\n"
         "\t\t\tcontinue;\n"
         "\n"
         "\t\tif (!ptep_test_and_clear_young(pvmw->vma, addr, pte + i))\n",
         "\t\tpage = get_pfn_page(pfn, memcg, pgdat);\n"
         "\t\tif (!page)\n"
         "\t\t\tcontinue;\n"
         "\n"
         "\t\tif (!ptep_test_and_clear_young(pvmw->vma, addr, pte + i))\n",
         T),
        # -- inc_min_seq(): account protection at the page's own tier --
        ("mm/vmscan.c",
         "static bool inc_min_seq(struct lruvec *lruvec, int type, bool can_swap)\n"
         "{\n"
         "\tint zone;\n"
         "\tint remaining = MAX_LRU_BATCH;\n"
         "\tstruct lru_gen_struct *lrugen = &lruvec->lrugen;\n"
         "\tint new_gen, old_gen = lru_gen_from_seq(lrugen->min_seq[type]);\n"
         "\n"
         "\tif (type == LRU_GEN_ANON && !can_swap)\n"
         "\t\tgoto done;\n"
         "\n"
         "\t/* prevent cold/hot inversion if full_scan is true */\n"
         "\tfor (zone = 0; zone < MAX_NR_ZONES; zone++) {\n"
         "\t\tstruct list_head *head = &lrugen->lists[old_gen][type][zone];\n"
         "\n"
         "\t\twhile (!list_empty(head)) {\n"
         "\t\t\tstruct page *page = lru_to_page(head);\n"
         "\n"
         "\t\t\tVM_WARN_ON_ONCE_PAGE(PageUnevictable(page), page);\n"
         "\t\t\tVM_WARN_ON_ONCE_PAGE(PageActive(page), page);\n"
         "\t\t\tVM_WARN_ON_ONCE_PAGE(page_is_file_lru(page) != type, page);\n"
         "\t\t\tVM_WARN_ON_ONCE_PAGE(page_zonenum(page) != zone, page);\n"
         "\n"
         "\t\t\tnew_gen = page_inc_gen(lruvec, page, false);\n"
         "\t\t\tlist_move_tail(&page->lru, &lrugen->lists[new_gen][type][zone]);\n"
         "\n"
         "\t\t\tif (!--remaining)\n"
         "\t\t\t\treturn false;\n"
         "\t\t}\n"
         "\t}\n",
         "static bool inc_min_seq(struct lruvec *lruvec, int type, int swappiness)\n"
         "{\n"
         "\tint zone;\n"
         "\tint remaining = MAX_LRU_BATCH;\n"
         "\tstruct lru_gen_struct *lrugen = &lruvec->lrugen;\n"
         "\tint hist = lru_hist_from_seq(lrugen->min_seq[type]);\n"
         "\tint new_gen, old_gen = lru_gen_from_seq(lrugen->min_seq[type]);\n"
         "\n"
         "\tif (type ? swappiness > MAX_SWAPPINESS : !swappiness)\n"
         "\t\tgoto done;\n"
         "\n"
         "\t/* prevent cold/hot inversion if the type is evictable */\n"
         "\tfor (zone = 0; zone < MAX_NR_ZONES; zone++) {\n"
         "\t\tstruct list_head *head = &lrugen->lists[old_gen][type][zone];\n"
         "\n"
         "\t\twhile (!list_empty(head)) {\n"
         "\t\t\tstruct page *page = lru_to_page(head);\n"
         "\t\t\tint refs = page_lru_refs(page);\n"
         "\t\t\tint tier = lru_tier_from_refs(refs);\n"
         "\t\t\tint delta = thp_nr_pages(page);\n"
         "\n"
         "\t\t\tVM_WARN_ON_ONCE_PAGE(PageUnevictable(page), page);\n"
         "\t\t\tVM_WARN_ON_ONCE_PAGE(PageActive(page), page);\n"
         "\t\t\tVM_WARN_ON_ONCE_PAGE(page_is_file_lru(page) != type, page);\n"
         "\t\t\tVM_WARN_ON_ONCE_PAGE(page_zonenum(page) != zone, page);\n"
         "\n"
         "\t\t\tnew_gen = page_inc_gen(lruvec, page, false);\n"
         "\t\t\tlist_move_tail(&page->lru, &lrugen->lists[new_gen][type][zone]);\n"
         "\n"
         "\t\t\tWRITE_ONCE(lrugen->protected[hist][type][tier],\n"
         "\t\t\t\t   lrugen->protected[hist][type][tier] + delta);\n"
         "\n"
         "\t\t\tif (!--remaining)\n"
         "\t\t\t\treturn false;\n"
         "\t\t}\n"
         "\t}\n",
         T),
        # -- try_to_inc_min_seq(): per-type drift bound --
        ("mm/vmscan.c",
         "static bool try_to_inc_min_seq(struct lruvec *lruvec, bool can_swap)\n"
         "{\n"
         "\tint gen, type, zone;\n"
         "\tbool success = false;\n"
         "\tstruct lru_gen_struct *lrugen = &lruvec->lrugen;\n"
         "\tDEFINE_MIN_SEQ(lruvec);\n"
         "\n"
         "\tVM_WARN_ON_ONCE(!seq_is_valid(lruvec));\n"
         "\n"
         "\t/* find the oldest populated generation */\n"
         "\tfor (type = !can_swap; type < ANON_AND_FILE; type++) {\n"
         "\t\twhile (min_seq[type] + MIN_NR_GENS <= lrugen->max_seq) {\n"
         "\t\t\tgen = lru_gen_from_seq(min_seq[type]);\n"
         "\n"
         "\t\t\tfor (zone = 0; zone < MAX_NR_ZONES; zone++) {\n"
         "\t\t\t\tif (!list_empty(&lrugen->lists[gen][type][zone]))\n"
         "\t\t\t\t\tgoto next;\n"
         "\t\t\t}\n"
         "\n"
         "\t\t\tmin_seq[type]++;\n"
         "\t\t}\n"
         "next:\n"
         "\t\t;\n"
         "\t}\n"
         "\n"
         "\t/* see the comment on lru_gen_struct */\n"
         "\tif (can_swap) {\n"
         "\t\tmin_seq[LRU_GEN_ANON] = min(min_seq[LRU_GEN_ANON], min_seq[LRU_GEN_FILE]);\n"
         "\t\tmin_seq[LRU_GEN_FILE] = max(min_seq[LRU_GEN_ANON], lrugen->min_seq[LRU_GEN_FILE]);\n"
         "\t}\n"
         "\n"
         "\tfor (type = !can_swap; type < ANON_AND_FILE; type++) {\n"
         "\t\tif (min_seq[type] == lrugen->min_seq[type])\n"
         "\t\t\tcontinue;\n"
         "\n"
         "\t\treset_ctrl_pos(lruvec, type, true);\n"
         "\t\tWRITE_ONCE(lrugen->min_seq[type], min_seq[type]);\n"
         "\t\tsuccess = true;\n"
         "\t}\n"
         "\n"
         "\treturn success;\n"
         "}\n",
         "static bool try_to_inc_min_seq(struct lruvec *lruvec, int swappiness)\n"
         "{\n"
         "\tint gen, type, zone;\n"
         "\tbool success = false;\n"
         "\tstruct lru_gen_struct *lrugen = &lruvec->lrugen;\n"
         "\tDEFINE_MIN_SEQ(lruvec);\n"
         "\n"
         "\tVM_WARN_ON_ONCE(!seq_is_valid(lruvec));\n"
         "\n"
         "\t/* find the oldest populated generation */\n"
         "\tfor_each_evictable_type(type, swappiness) {\n"
         "\t\twhile (min_seq[type] + MIN_NR_GENS <= lrugen->max_seq) {\n"
         "\t\t\tgen = lru_gen_from_seq(min_seq[type]);\n"
         "\n"
         "\t\t\tfor (zone = 0; zone < MAX_NR_ZONES; zone++) {\n"
         "\t\t\t\tif (!list_empty(&lrugen->lists[gen][type][zone]))\n"
         "\t\t\t\t\tgoto next;\n"
         "\t\t\t}\n"
         "\n"
         "\t\t\tmin_seq[type]++;\n"
         "\t\t}\n"
         "next:\n"
         "\t\t;\n"
         "\t}\n"
         "\n"
         "\t/* see the comment on lru_gen_struct */\n"
         "\tif (swappiness && swappiness <= MAX_SWAPPINESS) {\n"
         "\t\tunsigned long seq = lrugen->max_seq - MIN_NR_GENS;\n"
         "\n"
         "\t\tif (min_seq[LRU_GEN_ANON] > seq && min_seq[LRU_GEN_FILE] < seq)\n"
         "\t\t\tmin_seq[LRU_GEN_ANON] = seq;\n"
         "\t\telse if (min_seq[LRU_GEN_FILE] > seq && min_seq[LRU_GEN_ANON] < seq)\n"
         "\t\t\tmin_seq[LRU_GEN_FILE] = seq;\n"
         "\t}\n"
         "\n"
         "\tfor_each_evictable_type(type, swappiness) {\n"
         "\t\tif (min_seq[type] <= lrugen->min_seq[type])\n"
         "\t\t\tcontinue;\n"
         "\n"
         "\t\treset_ctrl_pos(lruvec, type, true);\n"
         "\t\tWRITE_ONCE(lrugen->min_seq[type], min_seq[type]);\n"
         "\t\tsuccess = true;\n"
         "\t}\n"
         "\n"
         "\treturn success;\n"
         "}\n",
         T),
        # -- inc_max_seq(): no WARN, no while-retry --
        ("mm/vmscan.c",
         "static void inc_max_seq(struct lruvec *lruvec, bool can_swap, bool full_scan)\n"
         "{\n"
         "\tint prev, next;\n"
         "\tint type, zone;\n"
         "\tstruct lru_gen_struct *lrugen = &lruvec->lrugen;\n"
         "restart:\n"
         "\tspin_lock_irq(&lruvec->lru_lock);\n"
         "\n"
         "\tVM_WARN_ON_ONCE(!seq_is_valid(lruvec));\n"
         "\n"
         "\tfor (type = ANON_AND_FILE - 1; type >= 0; type--) {\n"
         "\t\tif (get_nr_gens(lruvec, type) != MAX_NR_GENS)\n"
         "\t\t\tcontinue;\n"
         "\n"
         "\t\tVM_WARN_ON_ONCE(!full_scan && (type == LRU_GEN_FILE || can_swap));\n"
         "\n"
         "\t\twhile (!inc_min_seq(lruvec, type, can_swap)) {\n"
         "\t\t\tspin_unlock_irq(&lruvec->lru_lock);\n"
         "\t\t\tcond_resched();\n"
         "\t\t\tspin_lock_irq(&lruvec->lru_lock);\n"
         "\t\t}\n"
         "\t\tif (inc_min_seq(lruvec, type, can_swap))\n"
         "\t\t\tcontinue;\n"
         "\n"
         "\t\tspin_unlock_irq(&lruvec->lru_lock);\n"
         "\t\tcond_resched();\n"
         "\t\tgoto restart;\n"
         "\n"
         "\t}\n",
         "static void inc_max_seq(struct lruvec *lruvec, int swappiness, bool full_scan)\n"
         "{\n"
         "\tint prev, next;\n"
         "\tint type, zone;\n"
         "\tstruct lru_gen_struct *lrugen = &lruvec->lrugen;\n"
         "restart:\n"
         "\tspin_lock_irq(&lruvec->lru_lock);\n"
         "\n"
         "\tVM_WARN_ON_ONCE(!seq_is_valid(lruvec));\n"
         "\n"
         "\tfor (type = 0; type < ANON_AND_FILE; type++) {\n"
         "\t\tif (get_nr_gens(lruvec, type) != MAX_NR_GENS)\n"
         "\t\t\tcontinue;\n"
         "\n"
         "\t\tif (inc_min_seq(lruvec, type, swappiness))\n"
         "\t\t\tcontinue;\n"
         "\n"
         "\t\tspin_unlock_irq(&lruvec->lru_lock);\n"
         "\t\tcond_resched();\n"
         "\t\tgoto restart;\n"
         "\t}\n",
         T),
        # -- try_to_inc_max_seq(): carry swappiness into the walk --
        ("mm/vmscan.c",
         "static bool try_to_inc_max_seq(struct lruvec *lruvec, unsigned long max_seq,\n"
         "\t\t\t       struct scan_control *sc, bool can_swap, bool full_scan)\n",
         "static bool try_to_inc_max_seq(struct lruvec *lruvec, unsigned long max_seq,\n"
         "\t\t\t       struct scan_control *sc, int swappiness, bool full_scan)\n",
         T),
        ("mm/vmscan.c",
         "\twalk->can_swap = can_swap;\n"
         "\twalk->full_scan = full_scan;\n",
         "\twalk->swappiness = swappiness;\n"
         "\twalk->full_scan = full_scan;\n",
         T),
        ("mm/vmscan.c",
         "\t\tinc_max_seq(lruvec, can_swap, full_scan);\n",
         "\t\tinc_max_seq(lruvec, swappiness, full_scan);\n",
         T),
        # -- should_run_aging(): evictable-only totals, simpler feedback --
        ("mm/vmscan.c",
         "static bool should_run_aging(struct lruvec *lruvec, unsigned long max_seq, unsigned long *min_seq,\n"
         "\t\t\t     struct scan_control *sc, bool can_swap, unsigned long *nr_to_scan)\n"
         "{\n"
         "\tint gen, type, zone;\n"
         "\tunsigned long old = 0;\n"
         "\tunsigned long young = 0;\n"
         "\tunsigned long total = 0;\n"
         "\tstruct lru_gen_struct *lrugen = &lruvec->lrugen;\n"
         "\tstruct mem_cgroup *memcg = lruvec_memcg(lruvec);\n"
         "\n"
         "\tfor (type = !can_swap; type < ANON_AND_FILE; type++) {\n"
         "\t\tunsigned long seq;\n"
         "\n"
         "\t\tfor (seq = min_seq[type]; seq <= max_seq; seq++) {\n"
         "\t\t\tunsigned long size = 0;\n"
         "\n"
         "\t\t\tgen = lru_gen_from_seq(seq);\n"
         "\n"
         "\t\t\tfor (zone = 0; zone < MAX_NR_ZONES; zone++)\n"
         "\t\t\t\tsize += max_t(long, READ_ONCE(lrugen->nr_pages[gen][type][zone]),\n"
         "\t\t\t\t\t\t0);\n"
         "\n"
         "\t\t\ttotal += size;\n"
         "\t\t\tif (seq == max_seq)\n"
         "\t\t\t\tyoung += size;\n"
         "\t\t\telse if (seq + MIN_NR_GENS == max_seq)\n"
         "\t\t\t\told += size;\n"
         "\t\t}\n"
         "\t}\n"
         "\n"
         "\t/* try to scrape all its memory if this memcg was deleted */\n"
         "\t*nr_to_scan = mem_cgroup_online(memcg) ? (total >> sc->priority) : total;\n"
         "\n"
         "\t/*\n"
         "\t * The aging tries to be lazy to reduce the overhead, while the eviction\n"
         "\t * stalls when the number of generations reaches MIN_NR_GENS. Hence, the\n"
         "\t * ideal number of generations is MIN_NR_GENS+1.\n"
         "\t */\n"
         "\tif (min_seq[!can_swap] + MIN_NR_GENS > max_seq)\n"
         "\t\treturn true;\n"
         "\tif (min_seq[!can_swap] + MIN_NR_GENS < max_seq)\n"
         "\t\treturn false;\n"
         "\n"
         "\t/*\n"
         "\t * It's also ideal to spread pages out evenly, i.e., 1/(MIN_NR_GENS+1)\n"
         "\t * of the total number of pages for each generation. A reasonable range\n"
         "\t * for this average portion is [1/MIN_NR_GENS, 1/(MIN_NR_GENS+2)]. The\n"
         "\t * aging cares about the upper bound of hot pages, while the eviction\n"
         "\t * cares about the lower bound of cold pages.\n"
         "\t */\n"
         "\tif (young * MIN_NR_GENS > total)\n"
         "\t\treturn true;\n"
         "\tif (old * (MIN_NR_GENS + 2) < total)\n"
         "\t\treturn true;\n"
         "\n"
         "\treturn false;\n"
         "}\n",
         "static bool should_run_aging(struct lruvec *lruvec, unsigned long max_seq, unsigned long *min_seq,\n"
         "\t\t\t     struct scan_control *sc, int swappiness, unsigned long *nr_to_scan)\n"
         "{\n"
         "\tint gen, type, zone;\n"
         "\tunsigned long size = 0;\n"
         "\tstruct lru_gen_struct *lrugen = &lruvec->lrugen;\n"
         "\tstruct mem_cgroup *memcg = lruvec_memcg(lruvec);\n"
         "\n"
         "\t*nr_to_scan = 0;\n"
         "\t/* have to run aging, since eviction is not possible anymore */\n"
         "\tif (evictable_min_seq(min_seq, swappiness) + MIN_NR_GENS > max_seq)\n"
         "\t\treturn true;\n"
         "\n"
         "\tfor_each_evictable_type(type, swappiness) {\n"
         "\t\tunsigned long seq;\n"
         "\n"
         "\t\tfor (seq = min_seq[type]; seq <= max_seq; seq++) {\n"
         "\t\t\tgen = lru_gen_from_seq(seq);\n"
         "\n"
         "\t\t\tfor (zone = 0; zone < MAX_NR_ZONES; zone++)\n"
         "\t\t\t\tsize += max_t(long, READ_ONCE(lrugen->nr_pages[gen][type][zone]),\n"
         "\t\t\t\t\t\t0);\n"
         "\t\t}\n"
         "\t}\n"
         "\n"
         "\t/* try to scrape all its memory if this memcg was deleted */\n"
         "\t*nr_to_scan = mem_cgroup_online(memcg) ? (size >> sc->priority) : size;\n"
         "\n"
         "\t/* better to run aging even though eviction is still possible */\n"
         "\treturn evictable_min_seq(min_seq, swappiness) + MIN_NR_GENS == max_seq;\n"
         "}\n",
         T),
        # -- age_lruvec(): birth time of the oldest evictable generation --
        ("mm/vmscan.c",
         "\tif (min_ttl) {\n"
         "\t\tint gen = lru_gen_from_seq(min_seq[LRU_GEN_FILE]);\n",
         "\tif (min_ttl) {\n"
         "\t\tint gen = lru_gen_from_seq(evictable_min_seq(min_seq, swappiness));\n",
         T),
        # -- get_nr_to_scan(): int swappiness end to end --
        ("mm/vmscan.c",
         "static unsigned long get_nr_to_scan(struct lruvec *lruvec, struct scan_control *sc,\n"
         "\t\t\t\t    bool can_swap, bool *need_aging)\n",
         "static unsigned long get_nr_to_scan(struct lruvec *lruvec, struct scan_control *sc,\n"
         "\t\t\t\t    int swappiness, bool *need_aging)\n",
         T),
        ("mm/vmscan.c",
         "\t*need_aging = should_run_aging(lruvec, max_seq, min_seq, sc, can_swap, &nr_to_scan);\n",
         "\t*need_aging = should_run_aging(lruvec, max_seq, min_seq, sc, swappiness, &nr_to_scan);\n",
         T),
        ("mm/vmscan.c",
         "\tif (try_to_inc_max_seq(lruvec, max_seq, sc, can_swap, false))\n"
         "\t\treturn nr_to_scan;\n"
         "done:\n"
         "\treturn min_seq[!can_swap] + MIN_NR_GENS <= max_seq ? nr_to_scan : 0;\n",
         "\tif (try_to_inc_max_seq(lruvec, max_seq, sc, swappiness, false))\n"
         "\t\treturn nr_to_scan;\n"
         "done:\n"
         "\treturn evictable_min_seq(min_seq, swappiness) + MIN_NR_GENS <= max_seq ?\n"
         "\t\tnr_to_scan : 0;\n",
         T),
        # -- sort_page(): protection counters reindex with the array --
        ("mm/vmscan.c",
         "\t\tWRITE_ONCE(lrugen->protected[hist][type][tier - 1],\n"
         "\t\t\t   lrugen->protected[hist][type][tier - 1] + delta);\n",
         "\t\tWRITE_ONCE(lrugen->protected[hist][type][tier],\n"
         "\t\t\t   lrugen->protected[hist][type][tier] + delta);\n",
         T),
        # -- reset_ctrl_pos(): tier 0 participates in the EMA --
        ("mm/vmscan.c",
         "\t\t\tsum = lrugen->avg_total[type][tier] +\n"
         "\t\t\t      atomic_long_read(&lrugen->evicted[hist][type][tier]);\n"
         "\t\t\tif (tier)\n"
         "\t\t\t\tsum += lrugen->protected[hist][type][tier - 1];\n"
         "\t\t\tWRITE_ONCE(lrugen->avg_total[type][tier], sum / 2);\n",
         "\t\t\tsum = lrugen->avg_total[type][tier] +\n"
         "\t\t\t      lrugen->protected[hist][type][tier] +\n"
         "\t\t\t      atomic_long_read(&lrugen->evicted[hist][type][tier]);\n"
         "\t\t\tWRITE_ONCE(lrugen->avg_total[type][tier], sum / 2);\n",
         T),
        ("mm/vmscan.c",
         "\t\tif (clear) {\n"
         "\t\t\tatomic_long_set(&lrugen->refaulted[hist][type][tier], 0);\n"
         "\t\t\tatomic_long_set(&lrugen->evicted[hist][type][tier], 0);\n"
         "\t\t\tif (tier)\n"
         "\t\t\t\tWRITE_ONCE(lrugen->protected[hist][type][tier - 1], 0);\n"
         "\t\t}\n",
         "\t\tif (clear) {\n"
         "\t\t\tatomic_long_set(&lrugen->refaulted[hist][type][tier], 0);\n"
         "\t\t\tatomic_long_set(&lrugen->evicted[hist][type][tier], 0);\n"
         "\t\t\tWRITE_ONCE(lrugen->protected[hist][type][tier], 0);\n"
         "\t\t}\n",
         T),
        # -- evict_pages(): stall detection over the evictable types --
        ("mm/vmscan.c",
         "\tstruct lru_gen_mm_walk *walk;\n"
         "\tbool skip_retry = false;\n"
         "\tstruct mem_cgroup *memcg = lruvec_memcg(lruvec);\n",
         "\tstruct lru_gen_mm_walk *walk;\n"
         "\tbool skip_retry = false;\n"
         "\tstruct lru_gen_struct *lrugen = &lruvec->lrugen;\n"
         "\tstruct mem_cgroup *memcg = lruvec_memcg(lruvec);\n",
         T),
        ("mm/vmscan.c",
         "\tscanned += try_to_inc_min_seq(lruvec, swappiness);\n"
         "\n"
         "\tif (get_nr_gens(lruvec, !swappiness) == MIN_NR_GENS)\n"
         "\t\tscanned = 0;\n",
         "\tscanned += try_to_inc_min_seq(lruvec, swappiness);\n"
         "\n"
         "\tif (evictable_min_seq(lrugen->min_seq, swappiness) + MIN_NR_GENS > lrugen->max_seq)\n"
         "\t\tscanned = 0;\n",
         T),
        # -- seq_show: the first tier's protection is real now --
        ("mm/vmscan.c",
         "\t\t\t\tif (tier)\n"
         "\t\t\t\t\tn[2] = READ_ONCE(lrugen->protected[hist][type][tier - 1]);\n",
         "\t\t\t\tn[2] = READ_ONCE(lrugen->protected[hist][type][tier]);\n",
         T),
        # -- lru_gen_shrink_lruvec(): get_swappiness() owns the whole value --
        ("mm/vmscan.c",
         "\twhile (true) {\n"
         "\t\tint delta;\n"
         "\t\tint swappiness;\n"
         "\t\tunsigned long nr_to_scan;\n"
         "\n"
         "\t\tif (sc->may_swap)\n"
         "\t\t\tswappiness = get_swappiness(lruvec, sc);\n"
         "\t\telse if (!cgroup_reclaim(sc) && get_swappiness(lruvec, sc))\n"
         "\t\t\tswappiness = 1;\n"
         "\t\telse\n"
         "\t\t\tswappiness = 0;\n"
         "\n"
         "\t\tnr_to_scan = get_nr_to_scan(lruvec, sc, swappiness, &need_aging);\n",
         "\twhile (true) {\n"
         "\t\tint delta;\n"
         "\t\tint swappiness = get_swappiness(lruvec, sc);\n"
         "\t\tunsigned long nr_to_scan;\n"
         "\n"
         "\t\tnr_to_scan = get_nr_to_scan(lruvec, sc, swappiness, &need_aging);\n",
         T),
        # -- run_aging()/run_cmd(): debugfs paths follow the same rules --
        ("mm/vmscan.c",
         "static int run_aging(struct lruvec *lruvec, unsigned long seq, struct scan_control *sc,\n"
         "\t\t     bool can_swap, bool full_scan)\n"
         "{\n"
         "\tDEFINE_MAX_SEQ(lruvec);\n"
         "\tDEFINE_MIN_SEQ(lruvec);\n"
         "\n"
         "\tif (seq < max_seq)\n"
         "\t\treturn 0;\n"
         "\n"
         "\tif (seq > max_seq)\n"
         "\t\treturn -EINVAL;\n"
         "\n"
         "\tif (!full_scan && min_seq[!can_swap] + MAX_NR_GENS - 1 <= max_seq)\n"
         "\t\treturn -ERANGE;\n"
         "\n"
         "\ttry_to_inc_max_seq(lruvec, max_seq, sc, can_swap, full_scan);\n",
         "static int run_aging(struct lruvec *lruvec, unsigned long seq, struct scan_control *sc,\n"
         "\t\t     int swappiness, bool full_scan)\n"
         "{\n"
         "\tDEFINE_MAX_SEQ(lruvec);\n"
         "\tDEFINE_MIN_SEQ(lruvec);\n"
         "\n"
         "\tif (seq < max_seq)\n"
         "\t\treturn 0;\n"
         "\n"
         "\tif (seq > max_seq)\n"
         "\t\treturn -EINVAL;\n"
         "\n"
         "\tif (!full_scan && evictable_min_seq(min_seq, swappiness) + MAX_NR_GENS - 1 <= max_seq)\n"
         "\t\treturn -ERANGE;\n"
         "\n"
         "\ttry_to_inc_max_seq(lruvec, max_seq, sc, swappiness, full_scan);\n",
         T),
        ("mm/vmscan.c",
         "\telse if (swappiness > 200)\n"
         "\t\tgoto done;\n",
         "\telse if (swappiness > MAX_SWAPPINESS + 1)\n"
         "\t\tgoto done;\n",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _mglru_rework_type_selection_apply(ctx):
    """v6.14 37a260870f2c (rework type selection).

    read_ctrl_pos() sums every tier up to the requested one (tier 0 gains a
    protection history via 798c0330c2ca); get_tier_idx() tightens the margin
    to 2:3; get_type_to_scan() compares the summed tiers of anon vs file and
    owns the swappiness sentinels itself; isolate_pages() drops the
    min_seq-based pre-choice and the shared tier index.  Lands after
    mglru_rework_aging_feedback, whose evictable_min_seq/for_each_evictable_type
    macros and reindexed protected[] this group's new code requires.
    """
    steps = [
        ("mm/vmscan.c",
         "static void read_ctrl_pos(struct lruvec *lruvec, int type, int tier, int gain,\n"
         "\t\t\t  struct ctrl_pos *pos)\n"
         "{\n"
         "\tstruct lru_gen_struct *lrugen = &lruvec->lrugen;\n"
         "\tint hist = lru_hist_from_seq(lrugen->min_seq[type]);\n"
         "\n"
         "\tpos->refaulted = lrugen->avg_refaulted[type][tier] +\n"
         "\t\t\t atomic_long_read(&lrugen->refaulted[hist][type][tier]);\n"
         "\tpos->total = lrugen->avg_total[type][tier] +\n"
         "\t\t     atomic_long_read(&lrugen->evicted[hist][type][tier]);\n"
         "\tif (tier)\n"
         "\t\tpos->total += lrugen->protected[hist][type][tier - 1];\n"
         "\tpos->gain = gain;\n"
         "}\n",
         "static void read_ctrl_pos(struct lruvec *lruvec, int type, int tier, int gain,\n"
         "\t\t\t  struct ctrl_pos *pos)\n"
         "{\n"
         "\tint i;\n"
         "\tstruct lru_gen_struct *lrugen = &lruvec->lrugen;\n"
         "\tint hist = lru_hist_from_seq(lrugen->min_seq[type]);\n"
         "\n"
         "\tpos->gain = gain;\n"
         "\tpos->refaulted = pos->total = 0;\n"
         "\n"
         "\tfor (i = tier % MAX_NR_TIERS; i <= min(tier, MAX_NR_TIERS - 1); i++) {\n"
         "\t\tpos->refaulted += lrugen->avg_refaulted[type][i] +\n"
         "\t\t\t\t  atomic_long_read(&lrugen->refaulted[hist][type][i]);\n"
         "\t\tpos->total += lrugen->avg_total[type][i] +\n"
         "\t\t\t      lrugen->protected[hist][type][i] +\n"
         "\t\t\t      atomic_long_read(&lrugen->evicted[hist][type][i]);\n"
         "\t}\n"
         "}\n",
         T),
        ("mm/vmscan.c",
         "\t/*\n"
         "\t * To leave a margin for fluctuations, use a larger gain factor (1:2).\n"
         "\t * This value is chosen because any other tier would have at least twice\n"
         "\t * as many refaults as the first tier.\n"
         "\t */\n"
         "\tread_ctrl_pos(lruvec, type, 0, 1, &sp);\n"
         "\tfor (tier = 1; tier < MAX_NR_TIERS; tier++) {\n"
         "\t\tread_ctrl_pos(lruvec, type, tier, 2, &pv);\n"
         "\t\tif (!positive_ctrl_err(&sp, &pv))\n"
         "\t\t\tbreak;\n"
         "\t}\n",
         "\t/*\n"
         "\t * To leave a margin for fluctuations, use a larger gain factor (2:3).\n"
         "\t * This value is chosen because any other tier would have at least twice\n"
         "\t * as many refaults as the first tier.\n"
         "\t */\n"
         "\tread_ctrl_pos(lruvec, type, 0, 2, &sp);\n"
         "\tfor (tier = 1; tier < MAX_NR_TIERS; tier++) {\n"
         "\t\tread_ctrl_pos(lruvec, type, tier, 3, &pv);\n"
         "\t\tif (!positive_ctrl_err(&sp, &pv))\n"
         "\t\t\tbreak;\n"
         "\t}\n",
         T),
        ("mm/vmscan.c",
         "static int get_type_to_scan(struct lruvec *lruvec, int swappiness, int *tier_idx)\n"
         "{\n"
         "\tint type, tier;\n"
         "\tstruct ctrl_pos sp, pv;\n"
         "\tint gain[ANON_AND_FILE] = { swappiness, 200 - swappiness };\n"
         "\n"
         "\t/*\n"
         "\t * Compare the first tier of anon with that of file to determine which\n"
         "\t * type to scan. Also need to compare other tiers of the selected type\n"
         "\t * with the first tier of the other type to determine the last tier (of\n"
         "\t * the selected type) to evict.\n"
         "\t */\n"
         "\tread_ctrl_pos(lruvec, LRU_GEN_ANON, 0, gain[LRU_GEN_ANON], &sp);\n"
         "\tread_ctrl_pos(lruvec, LRU_GEN_FILE, 0, gain[LRU_GEN_FILE], &pv);\n"
         "\ttype = positive_ctrl_err(&sp, &pv);\n"
         "\n"
         "\tread_ctrl_pos(lruvec, !type, 0, gain[!type], &sp);\n"
         "\tfor (tier = 1; tier < MAX_NR_TIERS; tier++) {\n"
         "\t\tread_ctrl_pos(lruvec, type, tier, gain[type], &pv);\n"
         "\t\tif (!positive_ctrl_err(&sp, &pv))\n"
         "\t\t\tbreak;\n"
         "\t}\n"
         "\n"
         "\t*tier_idx = tier - 1;\n"
         "\n"
         "\treturn type;\n"
         "}\n",
         "static int get_type_to_scan(struct lruvec *lruvec, int swappiness)\n"
         "{\n"
         "\tstruct ctrl_pos sp, pv;\n"
         "\n"
         "\tif (swappiness <= MIN_SWAPPINESS + 1)\n"
         "\t\treturn LRU_GEN_FILE;\n"
         "\n"
         "\tif (swappiness >= MAX_SWAPPINESS)\n"
         "\t\treturn LRU_GEN_ANON;\n"
         "\n"
         "\t/*\n"
         "\t * Compare the sum of all tiers of anon with that of file to determine\n"
         "\t * which type to scan.\n"
         "\t */\n"
         "\tread_ctrl_pos(lruvec, LRU_GEN_ANON, MAX_NR_TIERS, swappiness, &sp);\n"
         "\tread_ctrl_pos(lruvec, LRU_GEN_FILE, MAX_NR_TIERS, MAX_SWAPPINESS - swappiness, &pv);\n"
         "\n"
         "\treturn positive_ctrl_err(&sp, &pv);\n"
         "}\n",
         T),
        ("mm/vmscan.c",
         "\tint i;\n"
         "\tint type;\n"
         "\tint scanned;\n"
         "\tint tier = -1;\n"
         "\tDEFINE_MIN_SEQ(lruvec);\n"
         "\n"
         "\t/*\n"
         "\t * Try to make the obvious choice first. When anon and file are both\n"
         "\t * available from the same generation, interpret swappiness 1 as file\n"
         "\t * first and 200 as anon first.\n"
         "\t */\n"
         "\tif (!swappiness)\n"
         "\t\ttype = LRU_GEN_FILE;\n"
         "\telse if (min_seq[LRU_GEN_ANON] < min_seq[LRU_GEN_FILE])\n"
         "\t\ttype = LRU_GEN_ANON;\n"
         "\telse if (swappiness == 1)\n"
         "\t\ttype = LRU_GEN_FILE;\n"
         "\telse if (swappiness == 200)\n"
         "\t\ttype = LRU_GEN_ANON;\n"
         "\telse\n"
         "\t\ttype = get_type_to_scan(lruvec, swappiness, &tier);\n"
         "\n"
         "\tfor (i = !swappiness; i < ANON_AND_FILE; i++) {\n"
         "\t\tif (tier < 0)\n"
         "\t\t\ttier = get_tier_idx(lruvec, type);\n"
         "\n"
         "\t\tscanned = scan_pages(lruvec, sc, type, tier, list);\n"
         "\t\tif (scanned)\n"
         "\t\t\tbreak;\n"
         "\n"
         "\t\ttype = !type;\n"
         "\t\ttier = -1;\n"
         "\t}\n"
         "\n"
         "\t*type_scanned = type;\n"
         "\n"
         "\treturn scanned;\n"
         "}\n",
         "\tint i;\n"
         "\tint type = get_type_to_scan(lruvec, swappiness);\n"
         "\n"
         "\tfor_each_evictable_type(i, swappiness) {\n"
         "\t\tint scanned;\n"
         "\t\tint tier = get_tier_idx(lruvec, type);\n"
         "\n"
         "\t\t*type_scanned = type;\n"
         "\n"
         "\t\tscanned = scan_pages(lruvec, sc, type, tier, list);\n"
         "\t\tif (scanned)\n"
         "\t\t\treturn scanned;\n"
         "\n"
         "\t\ttype = !type;\n"
         "\t}\n"
         "\n"
         "\treturn 0;\n"
         "}\n",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _mglru_rework_refault_detection_apply(ctx):
    """v6.14 b1a71694fb00 (rework refault detection).

    The MGLRU recency test compared the shadow token against min_seq[type]
    exactly, so every token whose generation had aged out of the minimal
    window reported "not recent" and its refault was misattributed as a
    workingset activation; the reworked test accepts any eviction within the
    last MAX_NR_GENS generations of max_seq.  This is the bulk of the TPC-C
    workingset_refault_file reduction (-57%) the series reports.  The
    baseline's recency test is inlined in lru_gen_refault() (there is no
    lru_gen_test_recent() helper), and abs_diff() postdates 5.15, so the
    distance is computed locally.
    """
    steps = [
        # Anchored through memcg_id (with the underscore): lru_gen_eviction()
        # declares the same token/min_seq/lruvec trio and must stay untouched.
        ("mm/workingset.c",
         "\tint memcg_id;\n"
         "\tbool workingset;\n"
         "\tunsigned long token;\n"
         "\tunsigned long min_seq;\n",
         "\tint memcg_id;\n"
         "\tbool workingset;\n"
         "\tunsigned long token;\n"
         "\tunsigned long seq;\n"
         "\tunsigned long diff;\n",
         T),
        ("mm/workingset.c",
         "\tmod_lruvec_state(lruvec, WORKINGSET_REFAULT_BASE + type, delta);\n"
         "\n"
         "\tmin_seq = READ_ONCE(lrugen->min_seq[type]);\n"
         "\tif ((token >> LRU_REFS_WIDTH) != (min_seq & (EVICTION_MASK >> LRU_REFS_WIDTH)))\n"
         "\t\tgoto unlock;\n"
         "\n"
         "\thist = lru_hist_from_seq(min_seq);\n",
         "\tmod_lruvec_state(lruvec, WORKINGSET_REFAULT_BASE + type, delta);\n"
         "\n"
         "\t/* ABK stable_515_backport: v6.14 b1a71694fb00, abs_diff() is 6.9+ */\n"
         "\tseq = READ_ONCE(lrugen->max_seq) & (EVICTION_MASK >> LRU_REFS_WIDTH);\n"
         "\tdiff = seq > (token >> LRU_REFS_WIDTH) ?\n"
         "\t       seq - (token >> LRU_REFS_WIDTH) : (token >> LRU_REFS_WIDTH) - seq;\n"
         "\tif (diff >= MAX_NR_GENS)\n"
         "\t\tgoto unlock;\n"
         "\n"
         "\thist = lru_hist_from_seq(READ_ONCE(lrugen->min_seq[type]));\n",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _mglru_wake_flushers_apply(ctx):
    """v6.13 1bc542c6a0d1 (wake up flushers conditionally to avoid cgroup OOM).

    The MGLRU eviction path never woke flushers, so a cgroup full of dirty
    file pages at the tail of its LRU thrashed straight into memcg OOM.  Track
    dirty/unqueued-dirty file pages through sort_page()/scan_pages(), carry
    the unqueued-dirty count out of shrink_page_list(), and wake flushers when
    every file page taken turned out to be unqueued dirty.  The upstream
    hunk's shrink_node memset is unnecessary here: this baseline's
    shrink_node() already memsets sc->nr on every iteration.
    """
    steps = [
        ("mm/vmscan.c",
         "static bool sort_page(struct lruvec *lruvec, struct page *page, struct scan_control *sc,\n"
         "\t\t       int tier_idx)\n"
         "{\n"
         "\tbool success;\n",
         "static bool sort_page(struct lruvec *lruvec, struct page *page, struct scan_control *sc,\n"
         "\t\t       int tier_idx)\n"
         "{\n"
         "\tbool success;\n"
         "\tbool dirty, writeback;\n",
         T),
        ("mm/vmscan.c",
         "\t/* waiting for writeback */\n"
         "\tif (PageLocked(page) || PageWriteback(page) ||\n"
         "\t    (type == LRU_GEN_FILE && PageDirty(page))) {\n",
         "\tdirty = PageDirty(page);\n"
         "\twriteback = PageWriteback(page);\n"
         "\tif (type == LRU_GEN_FILE && dirty) {\n"
         "\t\tsc->nr.file_taken += delta;\n"
         "\t\tif (!writeback)\n"
         "\t\t\tsc->nr.unqueued_dirty += delta;\n"
         "\t}\n"
         "\n"
         "\t/* waiting for writeback */\n"
         "\tif (PageLocked(page) || writeback ||\n"
         "\t    (type == LRU_GEN_FILE && dirty)) {\n",
         T),
        ("mm/vmscan.c",
         "\t__count_memcg_events(memcg, item, isolated);\n"
         "\t__count_memcg_events(memcg, PGREFILL, sorted);\n"
         "\t__count_vm_events(PGSCAN_ANON + type, isolated);\n",
         "\t__count_memcg_events(memcg, item, isolated);\n"
         "\t__count_memcg_events(memcg, PGREFILL, sorted);\n"
         "\t__count_vm_events(PGSCAN_ANON + type, isolated);\n"
         "\tif (type == LRU_GEN_FILE)\n"
         "\t\tsc->nr.file_taken += isolated;\n",
         T),
        ("mm/vmscan.c",
         "retry:\n"
         "\treclaimed = shrink_page_list(&list, pgdat, sc, &stat, false);\n"
         "\tsc->nr_reclaimed += reclaimed;\n",
         "retry:\n"
         "\treclaimed = shrink_page_list(&list, pgdat, sc, &stat, false);\n"
         "\tsc->nr.unqueued_dirty += stat.nr_unqueued_dirty;\n"
         "\tsc->nr_reclaimed += reclaimed;\n",
         T),
        ("mm/vmscan.c",
         "\t\tcond_resched();\n"
         "\t}\n"
         "\n"
         "\t/* see the comment in lru_gen_age_node() */\n",
         "\t\tcond_resched();\n"
         "\t}\n"
         "\n"
         "\t/*\n"
         "\t * If too many file cache in the coldest generation can't be evicted\n"
         "\t * due to being dirty, wake up the flusher.\n"
         "\t */\n"
         "\tif (sc->nr.unqueued_dirty && sc->nr.unqueued_dirty == sc->nr.file_taken)\n"
         "\t\twakeup_flusher_threads(WB_REASON_VMSCAN);\n"
         "\n"
         "\t/* see the comment in lru_gen_age_node() */\n",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail

# reclaim-path chain (Batch 37): the memory.reclaim batch fidelity, the
# swappiness= argument, the suspend abort and the lru_add drain.
# Steps live in scripts/batch37_core_reclaim_paths.py.  Registered on its own
# below (after memcg_memory_reclaim / cached_freeze_reclaim / memcg_v1_reclaim,
# whose generated text it edits) for the trap-5 reason spelled out there.
# ---------------------------------------------------------------------------


def _zram_recompression_apply(ctx):
    """Port zram multi-compression recompression (android15-6.6 / 6.2 series).

    Adds ``ZRAM_MULTI_COMP`` (a second, possibly-slower-but-more-effective
    compressor) and ``ZRAM_TRACK_ENTRY_ACTIME`` (idle age tracking) to the
    5.15 zram driver, so idle/small pages can be re-compressed in place to
    shrink zram footprint.  The 6.6 source keeps ``ZRAM_FLAG_SHIFT 24`` (the
    priority bits live in free high bits of ``flags``); ``zram->comp`` becomes
    ``comps[ZRAM_PRIMARY_COMP]`` and ``zram->compressor`` becomes
    ``comp_algs[0]``.  ``zs_lookup_class_index()`` (a 6.6 zsmalloc API the
    recompress sizing path needs) is added to zsmalloc.
    """
    # docs/group_recipe.md trap 5: zram_recompress_max_pages rewrites text this
    # group appends -- recompress_store() is not pristine 5.15, it is generated
    # right here -- so per-step idempotency alone would let a second pass append
    # a second copy.  Probe one of this group's own symbols: no android13-5.15
    # baseline carries a zram_recompress() helper, so a first pass always
    # rewrites and only a second pass stops.
    try:
        _probe = ctx.read("drivers/block/zram/zram_drv.c")
    except FileNotFoundError:
        _probe = ""
    if _b24_zmp.RECOMPRESS_HELPER in _probe:
        return "already_present", ("the recompression graft is already in zram_drv.c")
    T = True
    steps = [
        # -- Kconfig -----------------------------------------------------
        ("drivers/block/zram/Kconfig",
         "config ZRAM_MEMORY_TRACKING\n"
         "\tbool \"Track zRam block status\"\n"
         "\tdepends on ZRAM && DEBUG_FS\n"
         "\thelp\n"
         "\t  With this feature, admin can track the state of allocated blocks\n"
         "\t  of zRAM. Admin could see the information via\n"
         "\t  /sys/kernel/debug/zram/zramX/block_state.",
         "config ZRAM_TRACK_ENTRY_ACTIME\n"
         "\tbool \"Track access time of zram entries\"\n"
         "\tdepends on ZRAM\n"
         "\thelp\n"
         "\t  With this feature zram tracks access time of every stored\n"
         "\t  entry (page), which can be used for a more fine grained IDLE\n"
         "\t  pages writeback.\n"
         "\n"
         "config ZRAM_MEMORY_TRACKING\n"
         "\tbool \"Track zRam block status\"\n"
         "\tdepends on ZRAM && DEBUG_FS\n"
         "\tselect ZRAM_TRACK_ENTRY_ACTIME\n"
         "\thelp\n"
         "\t  With this feature, admin can track the state of allocated blocks\n"
         "\t  of zRAM. Admin could see the information via\n"
         "\t  /sys/kernel/debug/zram/zramX/block_state.\n"
         "\n"
         "config ZRAM_MULTI_COMP\n"
         "\tbool \"Enable multiple compression streams\"\n"
         "\tdepends on ZRAM\n"
         "\thelp\n"
         "\t  This will enable multi-compression streams, so that ZRAM can\n"
         "\t  re-compress pages using a potentially slower but more effective\n"
         "\t  compression algorithm. Note, that IDLE page recompression\n"
         "\t  requires ZRAM_TRACK_ENTRY_ACTIME.",
         T),
        # -- zram_drv.h --------------------------------------------------
        ("drivers/block/zram/zram_drv.h",
         "#define ZRAM_FLAG_SHIFT 24\n\n"
         "/* Flags for zram pages (table[page_no].flags) */",
         "#define ZRAM_FLAG_SHIFT 24\n\n"
         "/* Only 2 bits are allowed for comp priority index */\n"
         "#define ZRAM_COMP_PRIORITY_MASK\t0x3\n\n"
         "/* Flags for zram pages (table[page_no].flags) */",
         T),
        ("drivers/block/zram/zram_drv.h",
         "\tZRAM_HUGE,\t/* Incompressible page */\n"
         "\tZRAM_IDLE,\t/* not accessed page since last idle marking */\n\n"
         "\t__NR_ZRAM_PAGEFLAGS,",
         "\tZRAM_HUGE,\t/* Incompressible page */\n"
         "\tZRAM_IDLE,\t/* not accessed page since last idle marking */\n"
         "\tZRAM_INCOMPRESSIBLE, /* none of the algorithms could compress it */\n\n"
         "\tZRAM_COMP_PRIORITY_BIT1, /* First bit of comp priority index */\n"
         "\tZRAM_COMP_PRIORITY_BIT2, /* Second bit of comp priority index */\n\n"
         "\t__NR_ZRAM_PAGEFLAGS,",
         T),
        ("drivers/block/zram/zram_drv.h",
         "\tunsigned long flags;\n"
         "#ifdef CONFIG_ZRAM_MEMORY_TRACKING\n"
         "\tktime_t ac_time;\n"
         "#endif\n"
         "};",
         "\tunsigned long flags;\n"
         "#ifdef CONFIG_ZRAM_TRACK_ENTRY_ACTIME\n"
         "\tktime_t ac_time;\n"
         "#endif\n"
         "};",
         T),
        ("drivers/block/zram/zram_drv.h",
         "struct zram {\n"
         "\tstruct zram_table_entry *table;\n"
         "\tstruct zs_pool *mem_pool;\n"
         "\tstruct zcomp *comp;\n",
         "#ifdef CONFIG_ZRAM_MULTI_COMP\n"
         "#define ZRAM_PRIMARY_COMP\t0U\n"
         "#define ZRAM_SECONDARY_COMP\t1U\n"
         "#define ZRAM_MAX_COMPS\t4U\n"
         "#else\n"
         "#define ZRAM_PRIMARY_COMP\t0U\n"
         "#define ZRAM_SECONDARY_COMP\t0U\n"
         "#define ZRAM_MAX_COMPS\t1U\n"
         "#endif\n\n"
         "struct zram {\n"
         "\tstruct zram_table_entry *table;\n"
         "\tstruct zs_pool *mem_pool;\n"
         "\tstruct zcomp *comps[ZRAM_MAX_COMPS];\n",
         T),
        ("drivers/block/zram/zram_drv.h",
         "\tu64 disksize;\t/* bytes */\n"
         "\tchar compressor[CRYPTO_MAX_ALG_NAME];\n",
         "\tu64 disksize;\t/* bytes */\n"
         "\tconst char *comp_algs[ZRAM_MAX_COMPS];\n"
         "\ts8 num_active_comps;\n",
         T),
        # -- zsmalloc.h --------------------------------------------------
        ("include/linux/zsmalloc.h",
         "size_t zs_huge_class_size(struct zs_pool *pool);",
         "size_t zs_huge_class_size(struct zs_pool *pool);\n"
         "unsigned int zs_lookup_class_index(struct zs_pool *pool,\n"
         "\t\t\t\t\t unsigned int size);",
         T),
        # -- zsmalloc.c --------------------------------------------------
        ("mm/zsmalloc.c",
         "unsigned long zs_get_total_pages(struct zs_pool *pool)",
         "unsigned int zs_lookup_class_index(struct zs_pool *pool,\n"
         "\t\t\t\t   unsigned int size)\n"
         "{\n"
         "\tstruct size_class *class;\n\n"
         "\tclass = pool->size_class[get_size_class_index(size)];\n\n"
         "\treturn class->index;\n"
         "}\n"
         "EXPORT_SYMBOL_GPL(zs_lookup_class_index);\n\n"
         "unsigned long zs_get_total_pages(struct zs_pool *pool)",
         T),
        # -- zram_drv.c: comp-priority helpers (insert after zram_set_obj_size) --
        ("drivers/block/zram/zram_drv.c",
         "static void zram_set_obj_size(struct zram *zram,\n"
         "\t\t\t\t\tu32 index, size_t size)\n"
         "{\n"
         "\tunsigned long flags = zram->table[index].flags >> ZRAM_FLAG_SHIFT;\n\n"
         "\tzram->table[index].flags = (flags << ZRAM_FLAG_SHIFT) | size;\n"
         "}",
         "static void zram_set_obj_size(struct zram *zram,\n"
         "\t\t\t\t\tu32 index, size_t size)\n"
         "{\n"
         "\tunsigned long flags = zram->table[index].flags >> ZRAM_FLAG_SHIFT;\n\n"
         "\tzram->table[index].flags = (flags << ZRAM_FLAG_SHIFT) | size;\n"
         "}\n\n"
         "static inline void zram_set_priority(struct zram *zram, u32 index,\n"
         "\t\t\t\t\t u32 prio)\n"
         "{\n"
         "\tprio &= ZRAM_COMP_PRIORITY_MASK;\n"
         "\tzram->table[index].flags &= ~((unsigned long)ZRAM_COMP_PRIORITY_MASK <<\n"
         "\t\t\t\t\t        ZRAM_COMP_PRIORITY_BIT1);\n"
         "\tzram->table[index].flags |= ((unsigned long)prio << ZRAM_COMP_PRIORITY_BIT1);\n"
         "}\n\n"
         "static inline u32 zram_get_priority(struct zram *zram, u32 index)\n"
         "{\n"
         "\tu32 prio = zram->table[index].flags >> ZRAM_COMP_PRIORITY_BIT1;\n\n"
         "\treturn prio & ZRAM_COMP_PRIORITY_MASK;\n"
         "}",
         T),
        # -- zram_drv.c: __zram_bvec_read becomes a thin wrapper over the new
        #    zram_read_from_zspool() helper (upstream 6.6 shape).  This MUST run
        #    before the write-path zcomp_stream_put() steps below: those anchors
        #    are bare `zcomp_stream_put(zram->comp);` lines whose first match in
        #    file order lives in this function, so rewriting the read path first
        #    is what keeps them landing in __zram_bvec_write.  It also removes
        #    the duplicate decompress path and defines the helper that
        #    zram_recompress() calls. --
        ("drivers/block/zram/zram_drv.c",
         "static int __zram_bvec_read(struct zram *zram, struct page *page, u32 index,\n"
         "\t\t\t\tstruct bio *bio, bool partial_io)\n"
         "{\n"
         "\tstruct zcomp_strm *zstrm;\n"
         "\tunsigned long handle;\n"
         "\tunsigned int size;\n"
         "\tvoid *src, *dst;\n"
         "\tint ret;\n\n"
         "\tzram_slot_lock(zram, index);\n"
         "\tif (zram_test_flag(zram, index, ZRAM_WB)) {\n"
         "\t\tstruct bio_vec bvec;\n\n"
         "\t\tzram_slot_unlock(zram, index);\n\n"
         "\t\tbvec.bv_page = page;\n"
         "\t\tbvec.bv_len = PAGE_SIZE;\n"
         "\t\tbvec.bv_offset = 0;\n"
         "\t\treturn read_from_bdev(zram, &bvec,\n"
         "\t\t\t\tzram_get_element(zram, index),\n"
         "\t\t\t\tbio, partial_io);\n"
         "\t}\n\n"
         "\thandle = zram_get_handle(zram, index);\n"
         "\tif (!handle || zram_test_flag(zram, index, ZRAM_SAME)) {\n"
         "\t\tunsigned long value;\n"
         "\t\tvoid *mem;\n\n"
         "\t\tvalue = handle ? zram_get_element(zram, index) : 0;\n"
         "\t\tmem = kmap_atomic(page);\n"
         "\t\tzram_fill_page(mem, PAGE_SIZE, value);\n"
         "\t\tkunmap_atomic(mem);\n"
         "\t\tzram_slot_unlock(zram, index);\n"
         "\t\treturn 0;\n"
         "\t}\n\n"
         "\tsize = zram_get_obj_size(zram, index);\n\n"
         "\tif (size != PAGE_SIZE)\n"
         "\t\tzstrm = zcomp_stream_get(zram->comp);\n\n"
         "\tsrc = zs_map_object(zram->mem_pool, handle, ZS_MM_RO);\n"
         "\tif (size == PAGE_SIZE) {\n"
         "\t\tdst = kmap_atomic(page);\n"
         "\t\tmemcpy(dst, src, PAGE_SIZE);\n"
         "\t\tkunmap_atomic(dst);\n"
         "\t\tret = 0;\n"
         "\t} else {\n"
         "\t\tdst = kmap_atomic(page);\n"
         "\t\tret = zcomp_decompress(zstrm, src, size, dst);\n"
         "\t\tkunmap_atomic(dst);\n"
         "\t\tzcomp_stream_put(zram->comp);\n"
         "\t}\n"
         "\tzs_unmap_object(zram->mem_pool, handle);\n"
         "\tzram_slot_unlock(zram, index);",
         "/*\n"
         " * Reads (decompresses if needed) a page from zspool (zsmalloc).\n"
         " * Corresponding ZRAM slot should be locked.\n"
         " */\n"
         "static int zram_read_from_zspool(struct zram *zram, struct page *page,\n"
         "\t\t\t\t u32 index)\n"
         "{\n"
         "\tstruct zcomp_strm *zstrm;\n"
         "\tunsigned long handle;\n"
         "\tunsigned int size;\n"
         "\tvoid *src, *dst;\n"
         "\tu32 prio;\n"
         "\tint ret;\n\n"
         "\thandle = zram_get_handle(zram, index);\n"
         "\tif (!handle || zram_test_flag(zram, index, ZRAM_SAME)) {\n"
         "\t\tunsigned long value;\n"
         "\t\tvoid *mem;\n\n"
         "\t\tvalue = handle ? zram_get_element(zram, index) : 0;\n"
         "\t\tmem = kmap_atomic(page);\n"
         "\t\tzram_fill_page(mem, PAGE_SIZE, value);\n"
         "\t\tkunmap_atomic(mem);\n"
         "\t\treturn 0;\n"
         "\t}\n\n"
         "\tsize = zram_get_obj_size(zram, index);\n\n"
         "\tprio = zram_get_priority(zram, index);\n"
         "\tif (size != PAGE_SIZE)\n"
         "\t\tzstrm = zcomp_stream_get(zram->comps[prio]);\n\n"
         "\tsrc = zs_map_object(zram->mem_pool, handle, ZS_MM_RO);\n"
         "\tif (size == PAGE_SIZE) {\n"
         "\t\tdst = kmap_atomic(page);\n"
         "\t\tmemcpy(dst, src, PAGE_SIZE);\n"
         "\t\tkunmap_atomic(dst);\n"
         "\t\tret = 0;\n"
         "\t} else {\n"
         "\t\tdst = kmap_atomic(page);\n"
         "\t\tret = zcomp_decompress(zstrm, src, size, dst);\n"
         "\t\tkunmap_atomic(dst);\n"
         "\t\tzcomp_stream_put(zram->comps[prio]);\n"
         "\t}\n"
         "\tzs_unmap_object(zram->mem_pool, handle);\n"
         "\treturn ret;\n"
         "}\n\n"
         "static int __zram_bvec_read(struct zram *zram, struct page *page, u32 index,\n"
         "\t\t\t\tstruct bio *bio, bool partial_io)\n"
         "{\n"
         "\tint ret;\n\n"
         "\tzram_slot_lock(zram, index);\n"
         "\tif (zram_test_flag(zram, index, ZRAM_WB)) {\n"
         "\t\tstruct bio_vec bvec;\n\n"
         "\t\tzram_slot_unlock(zram, index);\n\n"
         "\t\tbvec.bv_page = page;\n"
         "\t\tbvec.bv_len = PAGE_SIZE;\n"
         "\t\tbvec.bv_offset = 0;\n"
         "\t\treturn read_from_bdev(zram, &bvec,\n"
         "\t\t\t\tzram_get_element(zram, index),\n"
         "\t\t\t\tbio, partial_io);\n"
         "\t}\n\n"
         "\tret = zram_read_from_zspool(zram, page, index);\n"
         "\tzram_slot_unlock(zram, index);",
         T),
        # -- zram_drv.c: __zram_bvec_write uses primary comp --
        ("drivers/block/zram/zram_drv.c",
         "compress_again:\n"
         "\tzstrm = zcomp_stream_get(zram->comp);",
         "compress_again:\n"
         "\tzstrm = zcomp_stream_get(zram->comps[ZRAM_PRIMARY_COMP]);",
         T),
        # -- zram_drv.c: write-path stream puts use primary comp (4 sites) --
        ("drivers/block/zram/zram_drv.c",
         "\t\tzcomp_stream_put(zram->comp);",
         "\t\tzcomp_stream_put(zram->comps[ZRAM_PRIMARY_COMP]);",
         T),
        ("drivers/block/zram/zram_drv.c",
         "\t\tzcomp_stream_put(zram->comp);\n"
         "\t\tatomic64_inc(&zram->stats.writestall);",
         "\t\tzcomp_stream_put(zram->comps[ZRAM_PRIMARY_COMP]);\n"
         "\t\tatomic64_inc(&zram->stats.writestall);",
         T),
        ("drivers/block/zram/zram_drv.c",
         "\t\tzcomp_stream_put(zram->comp);\n"
         "\t\tzs_free(zram->mem_pool, handle);\n"
         "\t\treturn -ENOMEM;",
         "\t\tzcomp_stream_put(zram->comps[ZRAM_PRIMARY_COMP]);\n"
         "\t\tzs_free(zram->mem_pool, handle);\n"
         "\t\treturn -ENOMEM;",
         T),
        ("drivers/block/zram/zram_drv.c",
         "\tzcomp_stream_put(zram->comp);\n"
         "\tzs_unmap_object(zram->mem_pool, handle);",
         "\tzcomp_stream_put(zram->comps[ZRAM_PRIMARY_COMP]);\n"
         "\tzs_unmap_object(zram->mem_pool, handle);",
         T),
        # -- zram_drv.c: zram_free_page resets priority + INCOMPRESSIBLE so a
        #    reused slot is never decompressed with a stale comp priority.  The
        #    trailing WARN_ON_ONCE mask needs no change: the reset runs before
        #    the `out:` label, so every path reaching the WARN has priority 0
        #    (this is why upstream 6.6 leaves that mask alone). --
        ("drivers/block/zram/zram_drv.c",
         "\tif (zram_test_flag(zram, index, ZRAM_HUGE)) {\n"
         "\t\tzram_clear_flag(zram, index, ZRAM_HUGE);\n"
         "\t\tatomic64_dec(&zram->stats.huge_pages);\n"
         "\t}\n\n"
         "\tif (zram_test_flag(zram, index, ZRAM_WB)) {",
         "\tif (zram_test_flag(zram, index, ZRAM_HUGE)) {\n"
         "\t\tzram_clear_flag(zram, index, ZRAM_HUGE);\n"
         "\t\tatomic64_dec(&zram->stats.huge_pages);\n"
         "\t}\n\n"
         "\tif (zram_test_flag(zram, index, ZRAM_INCOMPRESSIBLE))\n"
         "\t\tzram_clear_flag(zram, index, ZRAM_INCOMPRESSIBLE);\n\n"
         "\tzram_set_priority(zram, index, 0);\n\n"
         "\tif (zram_test_flag(zram, index, ZRAM_WB)) {",
         T),
        # -- zram_drv.c: zram_free_page ac_time reset follows the new guard --
        ("drivers/block/zram/zram_drv.c",
         "\tunsigned long handle;\n\n"
         "#ifdef CONFIG_ZRAM_MEMORY_TRACKING\n"
         "\tzram->table[index].ac_time = 0;\n"
         "#endif",
         "\tunsigned long handle;\n\n"
         "#ifdef CONFIG_ZRAM_TRACK_ENTRY_ACTIME\n"
         "\tzram->table[index].ac_time = 0;\n"
         "#endif",
         T),
        # -- zram_drv.c: zram_accessed must write ac_time whenever the new
        #    ZRAM_TRACK_ENTRY_ACTIME guard is set.  The 5.15 baseline keeps two
        #    definitions (debugfs on / off) and only the debugfs-on one writes
        #    ac_time, so with MEMORY_TRACKING off (the GKI default) the field
        #    would be read by mark_idle() and never written -- every page then
        #    looks infinitely old.  Both definitions are rewritten in place;
        #    upstream 6.6 instead collapses them, but a pure deletion cannot be
        #    expressed as a replace_once step (the replacement would be a
        #    subset of the anchor and short-circuit to already_present). --
        ("drivers/block/zram/zram_drv.c",
         "static void zram_accessed(struct zram *zram, u32 index)\n"
         "{\n"
         "\tzram_clear_flag(zram, index, ZRAM_IDLE);\n"
         "\tzram->table[index].ac_time = ktime_get_boottime();\n"
         "}",
         "static void zram_accessed(struct zram *zram, u32 index)\n"
         "{\n"
         "\tzram_clear_flag(zram, index, ZRAM_IDLE);\n"
         "#ifdef CONFIG_ZRAM_TRACK_ENTRY_ACTIME\n"
         "\tzram->table[index].ac_time = ktime_get_boottime();\n"
         "#endif\n"
         "}",
         T),
        ("drivers/block/zram/zram_drv.c",
         "static void zram_accessed(struct zram *zram, u32 index)\n"
         "{\n"
         "\tzram_clear_flag(zram, index, ZRAM_IDLE);\n"
         "};",
         "static void zram_accessed(struct zram *zram, u32 index)\n"
         "{\n"
         "\tzram_clear_flag(zram, index, ZRAM_IDLE);\n"
         "#ifdef CONFIG_ZRAM_TRACK_ENTRY_ACTIME\n"
         "\tzram->table[index].ac_time = ktime_get_boottime();\n"
         "#endif\n"
         "};",
         T),
        # -- zram_drv.c: zram_destroy_comps helper tears down every active comp --
        ("drivers/block/zram/zram_drv.c",
         "static void zram_reset_device(struct zram *zram)",
         "static void zram_destroy_comps(struct zram *zram)\n"
         "{\n"
         "\tu32 prio;\n\n"
         "\tfor (prio = 0; prio < ZRAM_MAX_COMPS; prio++) {\n"
         "\t\tstruct zcomp *comp = zram->comps[prio];\n\n"
         "\t\tzram->comps[prio] = NULL;\n"
         "\t\tif (!comp)\n"
         "\t\t\tcontinue;\n"
         "\t\tzcomp_destroy(comp);\n"
         "\t\tzram->num_active_comps--;\n"
         "\t}\n"
         "}\n\n"
         "static void zram_reset_device(struct zram *zram)",
         T),
        # -- zram_drv.c: zram_reset_device destroys all comps --
        ("drivers/block/zram/zram_drv.c",
         "static void zram_reset_device(struct zram *zram)\n"
         "{\n"
         "\tstruct zcomp *comp;\n"
         "\tu64 disksize;\n\n"
         "\tdown_write(&zram->init_lock);\n\n"
         "\tzram->limit_pages = 0;\n\n"
         "\tif (!init_done(zram)) {\n"
         "\t\tup_write(&zram->init_lock);\n"
         "\t\treturn;\n"
         "\t}\n\n"
         "\tcomp = zram->comp;\n"
         "\tdisksize = zram->disksize;\n"
         "\tzram->disksize = 0;\n\n"
         "\tset_capacity_and_notify(zram->disk, 0);\n"
         "\tpart_stat_set_all(zram->disk->part0, 0);\n\n"
         "\tup_write(&zram->init_lock);\n"
         "\t/* I/O operation under all of CPU are done so let's free */\n"
         "\tzram_meta_free(zram, disksize);\n"
         "\tmemset(&zram->stats, 0, sizeof(zram->stats));\n"
         "\tzcomp_destroy(comp);\n"
         "\treset_bdev(zram);\n"
         "}",
         "static void zram_reset_device(struct zram *zram)\n"
         "{\n"
         "\tu64 disksize;\n\n"
         "\tdown_write(&zram->init_lock);\n\n"
         "\tzram->limit_pages = 0;\n\n"
         "\tif (!init_done(zram)) {\n"
         "\t\tup_write(&zram->init_lock);\n"
         "\t\treturn;\n"
         "\t}\n\n"
         "\tdisksize = zram->disksize;\n"
         "\tzram->disksize = 0;\n\n"
         "\tset_capacity_and_notify(zram->disk, 0);\n"
         "\tpart_stat_set_all(zram->disk->part0, 0);\n\n"
         "\t/* ABK stable_515_backport: Batch 10-1 drains async recompress jobs\n"
         "\t * before comps/table teardown so queued jobs never see a dead pool.\n"
         "\t */\n"
         "\tabk_zram_recomp_drain(zram);\n\n"
         "\tup_write(&zram->init_lock);\n"
         "\t/* I/O operation under all of CPU are done so let's free */\n"
         "\tzram_meta_free(zram, disksize);\n"
         "\tmemset(&zram->stats, 0, sizeof(zram->stats));\n"
         "\tzram_destroy_comps(zram);\n"
         "\treset_bdev(zram);\n"
         "}",
         T),
        # -- zram_drv.c: recompression machinery (needs zram_read_from_zspool,
        #    which the read-path rewrite above defines earlier in the file) --
        ("drivers/block/zram/zram_drv.c",
         "static void zram_bio_discard(struct zram *zram, u32 index,",
         "#ifdef CONFIG_ZRAM_MULTI_COMP\n"
         "/*\n"
         " * Decompress (unless it's ZRAM_HUGE) the page and attempt to compress\n"
         " * it using the provided (potentially more effective) algorithm priority.\n"
         " * The corresponding ZRAM slot should be locked.\n"
         " */\n"
         "static int zram_recompress(struct zram *zram, u32 index, struct page *page,\n"
         "\t\t\t   u32 threshold, u32 prio, u32 prio_max)\n"
         "{\n"
         "\tstruct zcomp_strm *zstrm = NULL;\n"
         "\tunsigned long handle_old, handle_new;\n"
         "\tunsigned int comp_len_old, comp_len_new;\n"
         "\tunsigned int class_index_old, class_index_new;\n"
         "\tu32 num_recomps = 0;\n"
         "\tvoid *src, *dst;\n"
         "\tint ret;\n\n"
         "\thandle_old = zram_get_handle(zram, index);\n"
         "\tif (!handle_old)\n"
         "\t\treturn -EINVAL;\n\n"
         "\tcomp_len_old = zram_get_obj_size(zram, index);\n"
         "\tif (comp_len_old < threshold)\n"
         "\t\treturn 0;\n\n"
         "\tret = zram_read_from_zspool(zram, page, index);\n"
         "\tif (ret)\n"
         "\t\treturn ret;\n\n"
         "\tzram_clear_flag(zram, index, ZRAM_IDLE);\n\n"
         "\tclass_index_old = zs_lookup_class_index(zram->mem_pool, comp_len_old);\n"
         "\tfor (; prio < prio_max; prio++) {\n"
         "\t\tif (!zram->comps[prio])\n"
         "\t\t\tcontinue;\n"
         "\t\tif (prio <= zram_get_priority(zram, index))\n"
         "\t\t\tcontinue;\n\n"
         "\t\tnum_recomps++;\n"
         "\t\tzstrm = zcomp_stream_get(zram->comps[prio]);\n"
         "\t\tsrc = kmap_atomic(page);\n"
         "\t\tret = zcomp_compress(zstrm, src, &comp_len_new);\n"
         "\t\tkunmap_atomic(src);\n\n"
         "\t\tif (ret) {\n"
         "\t\t\tzcomp_stream_put(zram->comps[prio]);\n"
         "\t\t\treturn ret;\n"
         "\t\t}\n\n"
         "\t\tclass_index_new = zs_lookup_class_index(zram->mem_pool, comp_len_new);\n"
         "\t\tif (class_index_new >= class_index_old ||\n"
         "\t\t    (threshold && comp_len_new >= threshold)) {\n"
         "\t\t\tzcomp_stream_put(zram->comps[prio]);\n"
         "\t\t\tcontinue;\n"
         "\t\t}\n"
         "\t\tbreak;\n"
         "\t}\n\n"
         "\tif (!zstrm)\n"
         "\t\treturn 0;\n\n"
         "\tif (class_index_new >= class_index_old) {\n"
         "\t\tif (num_recomps == zram->num_active_comps - 1)\n"
         "\t\t\tzram_set_flag(zram, index, ZRAM_INCOMPRESSIBLE);\n"
         "\t\treturn 0;\n"
         "\t}\n\n"
         "\tif (threshold && comp_len_new >= threshold)\n"
         "\t\treturn 0;\n\n"
         "\thandle_new = zs_malloc(zram->mem_pool, comp_len_new,\n"
         "\t\t\t       __GFP_KSWAPD_RECLAIM | __GFP_NOWARN |\n"
         "\t\t\t       __GFP_HIGHMEM | __GFP_MOVABLE);\n"
         "\tif (IS_ERR_VALUE(handle_new)) {\n"
         "\t\tzcomp_stream_put(zram->comps[prio]);\n"
         "\t\treturn PTR_ERR((void *)handle_new);\n"
         "\t}\n\n"
         "\tdst = zs_map_object(zram->mem_pool, handle_new, ZS_MM_WO);\n"
         "\tmemcpy(dst, zstrm->buffer, comp_len_new);\n"
         "\tzcomp_stream_put(zram->comps[prio]);\n\n"
         "\tzs_unmap_object(zram->mem_pool, handle_new);\n\n"
         "\tzram_free_page(zram, index);\n"
         "\tzram_set_handle(zram, index, handle_new);\n"
         "\tzram_set_obj_size(zram, index, comp_len_new);\n"
         "\tzram_set_priority(zram, index, prio);\n\n"
         "\tatomic64_add(comp_len_new, &zram->stats.compr_data_size);\n"
         "\tatomic64_inc(&zram->stats.pages_stored);\n\n"
         "\treturn 0;\n"
         "}\n\n"
         "#define RECOMPRESS_IDLE\t\t(1 << 0)\n"
         "#define RECOMPRESS_HUGE\t\t(1 << 1)\n\n"
         "static ssize_t recompress_store(struct device *dev,\n"
         "\t\t\t\tstruct device_attribute *attr,\n"
         "\t\t\t\tconst char *buf, size_t len)\n"
         "{\n"
         "\tu32 prio = ZRAM_SECONDARY_COMP, prio_max = ZRAM_MAX_COMPS;\n"
         "\tstruct zram *zram = dev_to_zram(dev);\n"
         "\tunsigned long nr_pages = zram->disksize >> PAGE_SHIFT;\n"
         "\tchar *args, *param, *val, *algo = NULL;\n"
         "\tu32 mode = 0, threshold = 0;\n"
         "\tunsigned long index;\n"
         "\tstruct page *page;\n"
         "\tssize_t ret;\n\n"
         "\targs = skip_spaces(buf);\n"
         "\twhile (*args) {\n"
         "\t\targs = next_arg(args, &param, &val);\n\n"
         "\t\tif (!val || !*val)\n"
         "\t\t\treturn -EINVAL;\n\n"
         "\t\tif (!strcmp(param, \"type\")) {\n"
         "\t\t\tif (!strcmp(val, \"idle\"))\n"
         "\t\t\t\tmode = RECOMPRESS_IDLE;\n"
         "\t\t\tif (!strcmp(val, \"huge\"))\n"
         "\t\t\t\tmode = RECOMPRESS_HUGE;\n"
         "\t\t\tif (!strcmp(val, \"huge_idle\"))\n"
         "\t\t\t\tmode = RECOMPRESS_IDLE | RECOMPRESS_HUGE;\n"
         "\t\t\tcontinue;\n"
         "\t\t}\n\n"
         "\t\tif (!strcmp(param, \"threshold\")) {\n"
         "\t\t\tret = kstrtouint(val, 10, &threshold);\n"
         "\t\t\tif (ret)\n"
         "\t\t\t\treturn ret;\n"
         "\t\t\tcontinue;\n"
         "\t\t}\n\n"
         "\t\tif (!strcmp(param, \"algo\")) {\n"
         "\t\t\talgo = val;\n"
         "\t\t\tcontinue;\n"
         "\t\t}\n"
         "\t}\n\n"
         "\tif (threshold >= huge_class_size)\n"
         "\t\treturn -EINVAL;\n\n"
         "\tdown_read(&zram->init_lock);\n"
         "\tif (!init_done(zram)) {\n"
         "\t\tret = -EINVAL;\n"
         "\t\tgoto release_init_lock;\n"
         "\t}\n\n"
         "\tif (algo) {\n"
         "\t\tbool found = false;\n\n"
         "\t\tfor (; prio < ZRAM_MAX_COMPS; prio++) {\n"
         "\t\t\tif (!zram->comp_algs[prio])\n"
         "\t\t\t\tcontinue;\n"
         "\t\t\tif (!strcmp(zram->comp_algs[prio], algo)) {\n"
         "\t\t\t\tprio_max = min(prio + 1, ZRAM_MAX_COMPS);\n"
         "\t\t\t\tfound = true;\n"
         "\t\t\t\tbreak;\n"
         "\t\t\t}\n"
         "\t\t}\n\n"
         "\t\tif (!found) {\n"
         "\t\t\tret = -EINVAL;\n"
         "\t\t\tgoto release_init_lock;\n"
         "\t\t}\n"
         "\t}\n\n"
         "\tpage = alloc_page(GFP_KERNEL);\n"
         "\tif (!page) {\n"
         "\t\tret = -ENOMEM;\n"
         "\t\tgoto release_init_lock;\n"
         "\t}\n\n"
         "\tret = len;\n"
         "\tfor (index = 0; index < nr_pages; index++) {\n"
         "\t\tint err = 0;\n\n"
         "\t\tzram_slot_lock(zram, index);\n\n"
         "\t\tif (!zram_allocated(zram, index))\n"
         "\t\t\tgoto next;\n\n"
         "\t\tif (mode & RECOMPRESS_IDLE &&\n"
         "\t\t    !zram_test_flag(zram, index, ZRAM_IDLE))\n"
         "\t\t\tgoto next;\n\n"
         "\t\tif (mode & RECOMPRESS_HUGE &&\n"
         "\t\t    !zram_test_flag(zram, index, ZRAM_HUGE))\n"
         "\t\t\tgoto next;\n\n"
         "\t\tif (zram_test_flag(zram, index, ZRAM_WB) ||\n"
         "\t\t    zram_test_flag(zram, index, ZRAM_UNDER_WB) ||\n"
         "\t\t    zram_test_flag(zram, index, ZRAM_SAME) ||\n"
         "\t\t    zram_test_flag(zram, index, ZRAM_INCOMPRESSIBLE))\n"
         "\t\t\tgoto next;\n\n"
         "\t\terr = zram_recompress(zram, index, page, threshold, prio, prio_max);\n"
         "next:\n"
         "\t\tzram_slot_unlock(zram, index);\n"
         "\t\tif (err) {\n"
         "\t\t\tret = err;\n"
         "\t\t\tbreak;\n"
         "\t\t}\n\n"
         "\t\tcond_resched();\n"
         "\t}\n\n"
         "\t__free_page(page);\n\n"
         "release_init_lock:\n"
         "\tup_read(&zram->init_lock);\n"
         "\treturn ret;\n"
         "}\n"
         "#endif\n\n"
         "static void zram_bio_discard(struct zram *zram, u32 index,",
         T),
        # -- zram_drv.c: mark_idle + idle_store age-marks idle pages -------
        ("drivers/block/zram/zram_drv.c",
         "static ssize_t idle_store(struct device *dev,\n"
         "\t\tstruct device_attribute *attr, const char *buf, size_t len)\n"
         "{\n"
         "\tstruct zram *zram = dev_to_zram(dev);\n"
         "\tunsigned long nr_pages = zram->disksize >> PAGE_SHIFT;\n"
         "\tint index;\n\n"
         "\tif (!sysfs_streq(buf, \"all\"))\n"
         "\t\treturn -EINVAL;\n\n"
         "\tdown_read(&zram->init_lock);\n"
         "\tif (!init_done(zram)) {\n"
         "\t\tup_read(&zram->init_lock);\n"
         "\t\treturn -EINVAL;\n"
         "\t}\n\n"
         "\tfor (index = 0; index < nr_pages; index++) {\n"
         "\t\t/*\n"
         "\t\t * Do not mark ZRAM_UNDER_WB slot as ZRAM_IDLE to close race.\n"
         "\t\t * See the comment in writeback_store.\n"
         "\t\t */\n"
         "\t\tzram_slot_lock(zram, index);\n"
         "\t\tif (zram_allocated(zram, index) &&\n"
         "\t\t\t\t!zram_test_flag(zram, index, ZRAM_UNDER_WB))\n"
         "\t\t\tzram_set_flag(zram, index, ZRAM_IDLE);\n"
         "\t\tzram_slot_unlock(zram, index);\n"
         "\t}\n\n"
         "\tup_read(&zram->init_lock);\n\n"
         "\treturn len;\n"
         "}",
         "/*\n"
         " * Mark all pages which are older than or equal to cutoff as IDLE.\n"
         " * Callers should hold the zram init lock in read mode.\n"
         " */\n"
         "static void mark_idle(struct zram *zram, ktime_t cutoff)\n"
         "{\n"
         "\tint is_idle = 1;\n"
         "\tunsigned long nr_pages = zram->disksize >> PAGE_SHIFT;\n"
         "\tint index;\n\n"
         "\tfor (index = 0; index < nr_pages; index++) {\n"
         "\t\tzram_slot_lock(zram, index);\n"
         "\t\tif (!zram_allocated(zram, index) ||\n"
         "\t\t    zram_test_flag(zram, index, ZRAM_WB) ||\n"
         "\t\t    zram_test_flag(zram, index, ZRAM_UNDER_WB) ||\n"
         "\t\t    zram_test_flag(zram, index, ZRAM_SAME)) {\n"
         "\t\t\tzram_slot_unlock(zram, index);\n"
         "\t\t\tcontinue;\n"
         "\t\t}\n\n"
         "#ifdef CONFIG_ZRAM_TRACK_ENTRY_ACTIME\n"
         "\t\tis_idle = !cutoff ||\n"
         "\t\t\tktime_after(cutoff, zram->table[index].ac_time);\n"
         "#endif\n"
         "\t\tif (is_idle)\n"
         "\t\t\tzram_set_flag(zram, index, ZRAM_IDLE);\n"
         "\t\telse\n"
         "\t\t\tzram_clear_flag(zram, index, ZRAM_IDLE);\n"
         "\t\tzram_slot_unlock(zram, index);\n"
         "\t}\n"
         "}\n\n"
         "static ssize_t idle_store(struct device *dev,\n"
         "\t\tstruct device_attribute *attr, const char *buf, size_t len)\n"
         "{\n"
         "\tstruct zram *zram = dev_to_zram(dev);\n"
         "\tktime_t cutoff_time = 0;\n\n"
         "\tif (!sysfs_streq(buf, \"all\")) {\n"
         "\t\tu64 age_sec;\n\n"
         "\t\tif (IS_ENABLED(CONFIG_ZRAM_TRACK_ENTRY_ACTIME) &&\n"
         "\t\t    !kstrtoull(buf, 0, &age_sec))\n"
         "\t\t\tcutoff_time = ktime_sub(ktime_get_boottime(),\n"
         "\t\t\t\t\tns_to_ktime(age_sec * NSEC_PER_SEC));\n"
         "\t\telse\n"
         "\t\t\treturn -EINVAL;\n"
         "\t}\n\n"
         "\tdown_read(&zram->init_lock);\n"
         "\tif (!init_done(zram)) {\n"
         "\t\tup_read(&zram->init_lock);\n"
         "\t\treturn -EINVAL;\n"
         "\t}\n\n"
         "\tmark_idle(zram, cutoff_time);\n"
         "\tup_read(&zram->init_lock);\n\n"
         "\treturn len;\n"
         "}",
         T),
        # -- zram_drv.c: comp_algorithm machinery (primary + secondary) --
        ("drivers/block/zram/zram_drv.c",
         "static ssize_t comp_algorithm_show(struct device *dev,\n"
         "\t\tstruct device_attribute *attr, char *buf)\n"
         "{\n"
         "\tsize_t sz;\n"
         "\tstruct zram *zram = dev_to_zram(dev);\n\n"
         "\tdown_read(&zram->init_lock);\n"
         "\tsz = zcomp_available_show(zram->compressor, buf);\n"
         "\tup_read(&zram->init_lock);\n\n"
         "\treturn sz;\n"
         "}\n\n"
         "static ssize_t comp_algorithm_store(struct device *dev,\n"
         "\t\tstruct device_attribute *attr, const char *buf, size_t len)\n"
         "{\n"
         "\tstruct zram *zram = dev_to_zram(dev);\n"
         "\tchar compressor[ARRAY_SIZE(zram->compressor)];\n"
         "\tsize_t sz;\n\n"
         "\tstrlcpy(compressor, buf, sizeof(compressor));\n"
         "\t/* ignore trailing newline */\n"
         "\tsz = strlen(compressor);\n"
         "\tif (sz > 0 && compressor[sz - 1] == '\\n')\n"
         "\t\tcompressor[sz - 1] = 0x00;\n\n"
         "\tif (!zcomp_available_algorithm(compressor))\n"
         "\t\treturn -EINVAL;\n\n"
         "\tdown_write(&zram->init_lock);\n"
         "\tif (init_done(zram)) {\n"
         "\t\tup_write(&zram->init_lock);\n"
         "\t\tpr_info(\"Can't change algorithm for initialized device\\n\");\n"
         "\t\treturn -EBUSY;\n"
         "\t}\n\n"
         "\tstrcpy(zram->compressor, compressor);\n"
         "\tup_write(&zram->init_lock);\n"
         "\treturn len;\n"
         "}",
         "/* Do not free statically defined compression algorithms */\n"
         "static void comp_algorithm_set(struct zram *zram, u32 prio,\n"
         "\t\t\t\t    const char *alg)\n"
         "{\n"
         "\tif (zram->comp_algs[prio] != default_compressor)\n"
         "\t\tkfree(zram->comp_algs[prio]);\n\n"
         "\tzram->comp_algs[prio] = alg;\n"
         "}\n\n"
         "static ssize_t __comp_algorithm_show(struct zram *zram, u32 prio, char *buf)\n"
         "{\n"
         "\tssize_t sz;\n\n"
         "\tdown_read(&zram->init_lock);\n"
         "\tsz = zcomp_available_show(zram->comp_algs[prio], buf);\n"
         "\tup_read(&zram->init_lock);\n\n"
         "\treturn sz;\n"
         "}\n\n"
         "static int __comp_algorithm_store(struct zram *zram, u32 prio, const char *buf)\n"
         "{\n"
         "\tchar *compressor;\n"
         "\tsize_t sz;\n\n"
         "\tsz = strlen(buf);\n"
         "\tif (sz >= CRYPTO_MAX_ALG_NAME)\n"
         "\t\treturn -E2BIG;\n\n"
         "\tcompressor = kstrdup(buf, GFP_KERNEL);\n"
         "\tif (!compressor)\n"
         "\t\treturn -ENOMEM;\n\n"
         "\tif (sz > 0 && compressor[sz - 1] == '\\n')\n"
         "\t\tcompressor[sz - 1] = 0x00;\n\n"
         "\tif (!zcomp_available_algorithm(compressor)) {\n"
         "\t\tkfree(compressor);\n"
         "\t\treturn -EINVAL;\n"
         "\t}\n\n"
         "\tdown_write(&zram->init_lock);\n"
         "\tif (init_done(zram)) {\n"
         "\t\tup_write(&zram->init_lock);\n"
         "\t\tkfree(compressor);\n"
         "\t\tpr_info(\"Can't change algorithm for initialized device\\n\");\n"
         "\t\treturn -EBUSY;\n"
         "\t}\n\n"
         "\tcomp_algorithm_set(zram, prio, compressor);\n"
         "\tup_write(&zram->init_lock);\n"
         "\treturn 0;\n"
         "}\n\n"
         "static ssize_t comp_algorithm_show(struct device *dev,\n"
         "\t\tstruct device_attribute *attr, char *buf)\n"
         "{\n"
         "\tstruct zram *zram = dev_to_zram(dev);\n\n"
         "\treturn __comp_algorithm_show(zram, ZRAM_PRIMARY_COMP, buf);\n"
         "}\n\n"
         "static ssize_t comp_algorithm_store(struct device *dev,\n"
         "\t\tstruct device_attribute *attr, const char *buf, size_t len)\n"
         "{\n"
         "\tstruct zram *zram = dev_to_zram(dev);\n"
         "\tint ret;\n\n"
         "\tret = __comp_algorithm_store(zram, ZRAM_PRIMARY_COMP, buf);\n"
         "\treturn ret ? ret : len;\n"
         "}",
         T),
        ("drivers/block/zram/zram_drv.c",
         "static ssize_t compact_store(struct device *dev,\n"
         "\t\tstruct device_attribute *attr, const char *buf, size_t len)\n"
         "{\n"
         "\tstruct zram *zram = dev_to_zram(dev);\n\n"
         "\tdown_read(&zram->init_lock);\n"
         "\tif (!init_done(zram)) {\n"
         "\t\tup_read(&zram->init_lock);\n"
         "\t\treturn -EINVAL;\n"
         "\t}\n\n"
         "\tzs_compact(zram->mem_pool);\n"
         "\tup_read(&zram->init_lock);\n\n"
         "\treturn len;\n"
         "}",
         "#ifdef CONFIG_ZRAM_MULTI_COMP\n"
         "static ssize_t recomp_algorithm_show(struct device *dev,\n"
         "\t\t\t\t     struct device_attribute *attr, char *buf)\n"
         "{\n"
         "\tstruct zram *zram = dev_to_zram(dev);\n"
         "\tssize_t sz = 0;\n"
         "\tu32 prio;\n\n"
         "\tfor (prio = ZRAM_SECONDARY_COMP; prio < ZRAM_MAX_COMPS; prio++) {\n"
         "\t\tif (!zram->comp_algs[prio])\n"
         "\t\t\tcontinue;\n\n"
         "\t\tsz += scnprintf(buf + sz, PAGE_SIZE - sz - 2, \"#%d: \", prio);\n"
         "\t\tsz += __comp_algorithm_show(zram, prio, buf + sz);\n"
         "\t}\n\n"
         "\treturn sz;\n"
         "}\n\n"
         "static ssize_t recomp_algorithm_store(struct device *dev,\n"
         "\t\t\t\t      struct device_attribute *attr, const char *buf,\n"
         "\t\t\t\t      size_t len)\n"
         "{\n"
         "\tstruct zram *zram = dev_to_zram(dev);\n"
         "\tint prio = ZRAM_SECONDARY_COMP;\n"
         "\tchar *args, *param, *val;\n"
         "\tchar *alg = NULL;\n"
         "\tint ret;\n\n"
         "\targs = skip_spaces(buf);\n"
         "\twhile (*args) {\n"
         "\t\targs = next_arg(args, &param, &val);\n\n"
         "\t\tif (!val || !*val)\n"
         "\t\t\treturn -EINVAL;\n\n"
         "\t\tif (!strcmp(param, \"algo\")) {\n"
         "\t\t\talg = val;\n"
         "\t\t\tcontinue;\n"
         "\t\t}\n\n"
         "\t\tif (!strcmp(param, \"priority\")) {\n"
         "\t\t\tret = kstrtoint(val, 10, &prio);\n"
         "\t\t\tif (ret)\n"
         "\t\t\t\treturn ret;\n"
         "\t\t\tcontinue;\n"
         "\t\t}\n"
         "\t}\n\n"
         "\tif (!alg)\n"
         "\t\treturn -EINVAL;\n\n"
         "\tif (prio < ZRAM_SECONDARY_COMP || prio >= ZRAM_MAX_COMPS)\n"
         "\t\treturn -EINVAL;\n\n"
         "\tret = __comp_algorithm_store(zram, prio, alg);\n"
         "\treturn ret ? ret : len;\n"
         "}\n"
         "#endif\n\n"
         "static ssize_t compact_store(struct device *dev,\n"
         "\t\tstruct device_attribute *attr, const char *buf, size_t len)\n"
         "{\n"
         "\tstruct zram *zram = dev_to_zram(dev);\n\n"
         "\tdown_read(&zram->init_lock);\n"
         "\tif (!init_done(zram)) {\n"
         "\t\tup_read(&zram->init_lock);\n"
         "\t\treturn -EINVAL;\n"
         "\t}\n\n"
         "\tzs_compact(zram->mem_pool);\n"
         "\tup_read(&zram->init_lock);\n\n"
         "\treturn len;\n"
         "}",
         T),
        # -- zram_drv.c: disksize_store creates primary comp into comps[0] --
        # -- zram_drv.c: disksize_store creates every configured comp --
        ("drivers/block/zram/zram_drv.c",
         "\tcomp = zcomp_create(zram->compressor);\n"
         "\tif (IS_ERR(comp)) {\n"
         "\t\tpr_err(\"Cannot initialise %s compressing backend\\n\",\n"
         "\t\t\t\tzram->compressor);\n"
         "\t\terr = PTR_ERR(comp);\n"
         "\t\tgoto out_free_meta;\n"
         "\t}\n\n"
         "\tzram->comp = comp;\n"
         "\tzram->disksize = disksize;",
         "\tfor (prio = 0; prio < ZRAM_MAX_COMPS; prio++) {\n"
         "\t\tif (!zram->comp_algs[prio])\n"
         "\t\t\tcontinue;\n\n"
         "\t\tcomp = zcomp_create(zram->comp_algs[prio]);\n"
         "\t\tif (IS_ERR(comp)) {\n"
         "\t\t\tpr_err(\"Cannot initialise %s compressing backend\\n\",\n"
         "\t\t\t       zram->comp_algs[prio]);\n"
         "\t\t\terr = PTR_ERR(comp);\n"
         "\t\t\tgoto out_free_comps;\n"
         "\t\t}\n\n"
         "\t\tzram->comps[prio] = comp;\n"
         "\t\tzram->num_active_comps++;\n"
         "\t}\n"
         "\tzram->disksize = disksize;",
         T),
        # -- zram_drv.c: disksize_store gains the prio loop variable --
        ("drivers/block/zram/zram_drv.c",
         "\tu64 disksize;\n"
         "\tstruct zcomp *comp;\n"
         "\tstruct zram *zram = dev_to_zram(dev);\n"
         "\tint err;\n",
         "\tu64 disksize;\n"
         "\tstruct zcomp *comp;\n"
         "\tstruct zram *zram = dev_to_zram(dev);\n"
         "\tint err;\n"
         "\tu32 prio;\n",
         T),
        # -- zram_drv.c: disksize_store error path frees every comp --
        ("drivers/block/zram/zram_drv.c",
         "out_free_meta:\n"
         "\tzram_meta_free(zram, disksize);\n"
         "out_unlock:",
         "out_free_comps:\n"
         "\tzram_destroy_comps(zram);\n"
         "\tzram_meta_free(zram, disksize);\n"
         "out_unlock:",
         T),
        # -- zram_drv.c: zram_add sets default primary comp --
        ("drivers/block/zram/zram_drv.c",
         "\tstrlcpy(zram->compressor, default_compressor, sizeof(zram->compressor));",
         "\tzram->comp_algs[ZRAM_PRIMARY_COMP] = default_compressor;\n"
         "\tzram->num_active_comps = 1;",
         T),
        # -- zram_drv.c: sysfs attrs for recompress/recomp_algorithm --
        ("drivers/block/zram/zram_drv.c",
         "static DEVICE_ATTR_RW(comp_algorithm);\n"
         "#ifdef CONFIG_ZRAM_WRITEBACK",
         "static DEVICE_ATTR_RW(comp_algorithm);\n"
         "#ifdef CONFIG_ZRAM_MULTI_COMP\n"
         "static DEVICE_ATTR_WO(recompress);\n"
         "static DEVICE_ATTR_RW(recomp_algorithm);\n"
         "#endif\n"
         "#ifdef CONFIG_ZRAM_WRITEBACK",
         T),
        ("drivers/block/zram/zram_drv.c",
         "\t&dev_attr_comp_algorithm.attr,\n"
         "#ifdef CONFIG_ZRAM_WRITEBACK\n"
         "\t&dev_attr_backing_dev.attr,",
         "\t&dev_attr_comp_algorithm.attr,\n"
         "#ifdef CONFIG_ZRAM_MULTI_COMP\n"
         "\t&dev_attr_recompress.attr,\n"
         "\t&dev_attr_recomp_algorithm.attr,\n"
         "#endif\n"
         "#ifdef CONFIG_ZRAM_WRITEBACK\n"
         "\t&dev_attr_backing_dev.attr,",
         T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# build config enablement (config lane)
#
# Everything before this batch shipped code and Kconfig symbols that nothing
# ever turned on: the defconfig was parsed off the command line and never
# written.  Default tier enables only what this module itself introduces; the
# wider android15-6.6 GKI config picture is opt-in so a plain injection never
# silently changes device behaviour.
# ---------------------------------------------------------------------------

_MODULE_CONFIGS = [
    # zram recompression (Batch 4): ZRAM_MULTI_COMP depends on the age tracking
    # symbol, and Kconfig leaves both off by default.
    ("ZRAM_TRACK_ENTRY_ACTIME", "y"),
    ("ZRAM_MULTI_COMP", "y"),
    # dynamic readahead (Batch 9-1): the grafted callbacks are inert until
    # this module-owned symbol is on, mirroring the OPLUS/Xiaomi module's
    # opt-in enablement.
    ("ABK_DYNAMIC_READAHEAD", "y"),
    # RCU_NOCB_CPU_DEFAULT_ALL (Batch 8): the graft adds the Kconfig symbol and
    # the ``offload_all`` machinery in kernel/rcu/tree_nocb.h, but every path
    # that sets ``offload_all = true`` is inside ``#if defined(CONFIG_...)``.
    # With the symbol unset the whole graft reduced to an always-false branch
    # (found by Batch 16's emptiness audit), so the tier has to enable it.
    ("RCU_NOCB_CPU_DEFAULT_ALL", "y"),
]

_ALIGN_CONFIGS = [
    # Enabled in the android15-6.6 GKI defconfig, absent from every
    # android13-5.15 baseline, and the code already exists in 5.15 for all of
    
    # them -- config-only deltas, so they change runtime behaviour.
    ("LRU_GEN_ENABLED", "y"),        # MGLRU on by default
    ("TCP_CONG_ADVANCED", "y"),      # prerequisite for BBR
    ("TCP_CONG_BBR", "y"),
    ("BLK_WBT", "y"),                # writeback throttling
    ("BLK_DEV_THROTTLING", "y"),     # cgroup io throttling
    ("TASK_DELAY_ACCT", "y"),        # delay accounting
]

# ROM-integration tier (ABK_515_DEFCONFIG_ROM=1), off by default: these change
# who owns a device, not just how fast it is, so they are never enabled by a
# plain injection.
_ROM_CONFIGS = [
    # This ROM's memory daemon (init.rc starts mmd_setup, Android 16's Rust
    # implementation) sets up a per-process zram writeback backing device and
    # only then sets mmd.setup_complete -- which is what enables the mmd
    # service.  Without CONFIG_ZRAM_WRITEBACK the writeback nodes do not exist,
    # mmd_setup never completes on a ROM that relies on it, and mmd stays dead
    # (`vendor.zram.disable=1` on the target ROM disables its setup outright).
    # It used to be mutually exclusive with the zram algorithm policy (both
    # write in the pre-`disksize` window); Batch 11's kernel-side lock removed
    # that, so the writeback owner and the algorithm policy now coexist.
    ("ZRAM_WRITEBACK", "y"),
]

# Every Kconfig symbol this module introduces into a baseline tree, mapped to
# the tier that enables it.  ``None`` means Kconfig itself supplies a workable
# default (a bool defaulting to ``y``, or a non-bool), so no tier is needed.
# tests/stable_5_15_test.py asserts this table and the tier lists agree, which
# is what stops a graft from being added with no way to ever compile: the
# Batch 8 RCU graft sat behind a symbol no tier enabled, so its whole payload
# compiled out and the group still reported "applied".
_INTRODUCED_KCONFIG = {
    "ZRAM_TRACK_ENTRY_ACTIME": "module",
    "ZRAM_MULTI_COMP": "module",
    "ABK_DYNAMIC_READAHEAD": "module",
    "RCU_NOCB_CPU_DEFAULT_ALL": "module",
    "ZSMALLOC_CHAIN_SIZE": None,   # int, Kconfig default 8
    "ZRAM_WRITEBACK": "rom",       # pre-existing symbol, enabled by the rom tier
}


# Per-cgroup PSI accounting tier (ABK_515_DEFCONFIG_PSI=1), off by default.
#
# The AOSP android13-5.15-lts gki_defconfig ships
# ``CONFIG_CMDLINE="stack_depot_disable=on kasan.stacktrace=off
# kvm-arm.mode=protected cgroup_disable=pressure"`` with
# CONFIG_CMDLINE_EXTEND=y, so the token is in every boot of every build from
# this tree.  It makes ``cgroup_psi_enabled()`` false, which does two things:
# ``psi_init()`` disables the ``psi_cgroups_enabled`` static branch (the
# per-cgroup accounting itself), and every ``CFTYPE_PRESSURE`` file is skipped
# at creation -- ``io.pressure``, ``memory.pressure``, ``cpu.pressure`` and
# **this module's ``cgroup.pressure``**.
#
# Batch 21's switch and the runtime companion's per-cgroup PSI policy both need
# that one file to exist, and the accounting they are meant to switch off has to
# be running for the switch to mean anything.  So the tier drops the token.  It
# is off by default because the trade is device-wide: with the token gone every
# one of the ~450 groups derives and times its own states until the companion's
# policy pass turns it off group by group, which is what the token did for free.
_PSI_CMDLINE_TOKEN = "cgroup_disable=pressure"


def _config_enablement_apply(ctx):
    align = os.environ.get("ABK_515_DEFCONFIG_ALIGN", "").strip() == "1"
    rom = os.environ.get("ABK_515_DEFCONFIG_ROM", "").strip() == "1"
    psi = os.environ.get("ABK_515_DEFCONFIG_PSI", "").strip() == "1"
    configs = list(_MODULE_CONFIGS)
    tiers = ["module-owned symbols only"]
    if align:
        configs += _ALIGN_CONFIGS
        tiers.append("6.6 GKI align")
    if rom:
        configs += _ROM_CONFIGS
        tiers.append("ROM integration")
    if psi:
        tiers.append("per-cgroup PSI accounting")
    status, detail = ctx.enable_configs(configs)
    if psi:
        psi_status, psi_detail = ctx.defconfig_drop_cmdline_token(
            _PSI_CMDLINE_TOKEN)
        if psi_status == "blocked_by_missing_anchor":
            status = psi_status
        elif psi_status == "applied":
            status = "applied"
        detail = f"{detail}; {psi_detail}"
    return status, f"{detail} [{', '.join(tiers)}]"


PATCH_GROUPS = [
    PatchGroup(
        "fdtable_alloc_conventions",
        "alloc_fdtable() slots_wanted/ERR_PTR conventions + INT_MAX guard (5.15.191)",
        ["04a2c4b4511d (5.15.191)", "1d3b4bec3ce5 (5.15.191)"],
        ["fs/file.c"],
        _fdtable_apply,
        hard=True,
    ),
    PatchGroup(
        "fdtable_replace_fd_errno",
        "replace_fd() returns 0 instead of do_dup2()'s positive fd (5.15.195)",
        ["ff8ec0dbe0150 (5.15.195)"],
        ["fs/file.c"],
        _replace_fd_errno_apply,
    ),
    PatchGroup(
        "pagealloc_min_reserve_semantics",
        "ALLOC_HIGH -> ALLOC_MIN_RESERVE with RT tasks treated as __GFP_HIGH (5.15.171)",
        ["92e52ff398b5 (5.15.171)", "9da195a2d35b (5.15.171)"],
        ["mm/internal.h", "mm/page_alloc.c"],
        _min_reserve_apply,
    ),
    PatchGroup(
        "pagealloc_highatomic_reserve_semantics",
        "high-atomic reserve semantics: ALLOC_NON_BLOCK/ALLOC_HIGHATOMIC/ALLOC_RESERVES with explicit watermark access rules (5.15.188-.218)",
        [
            "ca8527f25736 (5.15.188, base already in AOSP)",
            "c1b8856c5a7d (5.15.189)",
            "17dedfd6de69 (5.15.190)",
            "85f58ee33c6c (5.15.191)",
            "4c4e238d3ada (5.15.199)",
            "735457683e23 (5.15.218)",
        ],
        ["mm/internal.h", "mm/page_alloc.c"],
        _highatomic_reserve_apply,
    ),
    PatchGroup(
        "pagealloc_thisnode_thp_noreclaim",
        "THP __GFP_THISNODE allocations compact only, never direct-reclaim (5.15.202)",
        ["0eac511c7657 (5.15.202)"],
        ["mm/page_alloc.c"],
        _thisnode_thp_apply,
    ),
    PatchGroup(
        "pagealloc_cpuset_bailout",
        "bail out early when cpuset forbids every suitable zone (5.15.191)",
        ["c635a42d9b74 (5.15.191)"],
        ["include/linux/cpuset.h", "include/linux/mmzone.h", "kernel/cgroup/cpuset.c", "mm/page_alloc.c"],
        _cpuset_bailout_apply,
    ),
    PatchGroup(
        "pagealloc_high_fraction_lockfree",
        "percpu_pagelist_high_fraction reads without pcp_batch_high_lock (5.15.200)",
        ["eda99622e6f3 (5.15.200)"],
        ["mm/page_alloc.c"],
        _pagelist_lockfree_apply,
    ),
    PatchGroup(
        "cgroup_root_list_rcu",
        "cgroup root_list traversal and teardown become RCU-safe (5.15.168)",
        ["de77545c72c4 (5.15.168)"],
        ["include/linux/cgroup-defs.h", "kernel/cgroup/cgroup-internal.h", "kernel/cgroup/cgroup.c"],
        _cgroup_rcu_apply,
    ),
    PatchGroup(
        "zram_recompression",
        "zram multi-compression streams: idle/small page in-place recompression via a second compressor (android15-6.6 / 6.2 recompression series)",
        ["ACK android15-6.6 zram recompression (6.2 series; ZRAM_MULTI_COMP + ZRAM_TRACK_ENTRY_ACTIME)"],
        ["drivers/block/zram/Kconfig", "drivers/block/zram/zram_drv.h",
         "drivers/block/zram/zram_drv.c", "mm/zsmalloc.c", "include/linux/zsmalloc.h"],
        _zram_recompression_apply,
    ),
    PatchGroup(
        "cgroup_destroy_wq_split",
        "split cgroup_destroy_wq into offline/release/free workqueues (5.15.194)",
        ["f2795d1b9250 (5.15.194)"],
        ["kernel/cgroup/cgroup.c"],
        _cgroup_wq_split_apply,
    ),
    PatchGroup(
        "memcg_memory_reclaim",
        "per-memcg proactive reclaim via memory.reclaim, with reclaim options replacing may_swap (android14-6.1 / 6.1.y), documented in the cgroup v2 manual",
        ["memory.reclaim series (android14-6.1; mainline proactive reclaim)"],
        ["include/linux/swap.h", "mm/vmscan.c", "mm/memcontrol.c",
         "Documentation/admin-guide/cgroup-v2.rst"],
        _memcg_reclaim_apply,
    ),
    PatchGroup(
        "mglru_clean_workingset",
        "MGLRU v4: workingset_refault() lock assertion covers the MGLRU path too (v6.14 9cbfd1c3c83b; the test_recent restructure has no 6.1-shape counterpart)",
        ["9cbfd1c3c83b (v6.14)"],
        ["mm/workingset.c"],
        _mglru_clean_workingset_apply,
    ),
    PatchGroup(
        "mglru_optimize_deactivation",
        "MGLRU v4: deactivation clears LRU_REFS in place and skips the LRU shuffle when the page is in the oldest generation (v6.14 cc8ec7be78ff, re-authored onto pagevec drain)",
        ["cc8ec7be78ff (v6.14)"],
        ["mm/swap.c"],
        _mglru_optimize_deactivation_apply,
    ),
    PatchGroup(
        "mglru_rework_aging_feedback",
        "MGLRU v4: min_seq[] may drift by MAX_NR_GENS-MIN_NR_GENS-1, protected[] gains tier 0, full int swappiness with the >MAX_SWAPPINESS anon-only sentinel, simpler aging feedback (v6.14 798c0330c2ca)",
        ["798c0330c2ca (v6.14)"],
        ["include/linux/swap.h", "include/linux/mmzone.h", "mm/vmscan.c"],
        _mglru_rework_aging_feedback_apply,
    ),
    PatchGroup(
        "mglru_rework_type_selection",
        "MGLRU v4: type selection sums all tiers per type, tightens the tier margin to 2:3, and reads full-protection history (v6.14 37a260870f2c; needs mglru_rework_aging_feedback)",
        ["37a260870f2c (v6.14)"],
        ["mm/vmscan.c"],
        _mglru_rework_type_selection_apply,
    ),
    PatchGroup(
        "mglru_rework_refault_detection",
        "MGLRU v4: refault recency accepts any eviction within the last MAX_NR_GENS generations instead of an exact min_seq match (v6.14 b1a71694fb00; the TPC-C -57% workingset_refault_file result)",
        ["b1a71694fb00 (v6.14)"],
        ["mm/workingset.c"],
        _mglru_rework_refault_detection_apply,
    ),
    PatchGroup(
        "mglru_wake_flushers",
        "MGLRU: wake flushers when every file page taken in a reclaim cycle is unqueued dirty, keeping dirty-tail cgroups from memcg OOM (v6.13 1bc542c6a0d1)",
        ["1bc542c6a0d1 (v6.13)"],
        ["mm/vmscan.c"],
        _mglru_wake_flushers_apply,
    ),
]


def main():
    args = parse_args("stable_backport_core: 5.15.y fd/mm/cgroup feature grafts")
    ctx = make_context(args)
    enabled = ctx.family == "android13-5.15" or args.allow_unsupported
    if not enabled:
        print(f"[ABK stable_515_backport] unsupported family {ctx.family}; "
              "every group reports report_only and nothing is written "
              "(pass --allow-unsupported to override)")
    run_child("stable_backport_core", PATCH_GROUPS, ctx, args,
              enabled=enabled)


# ============================================================================
# Batch 6 additions.  Kept after the original module body so the diff of the
# earlier batches stays untouched; they are appended to PATCH_GROUPS at the
# bottom of the file, before the __main__ guard.
# ============================================================================
# ---------------------------------------------------------------------------
# zsmalloc zspage chain size (android15-6.6; the 6.2 class-sizing rework).
#
# The 5.15 baseline fixes a zspage at 2^2 pages and picks the chain length by
# best-used-percentage; 6.2 made the ceiling a tunable and minimised absolute
# waste instead, which is what actually shrinks small size classes.  The
# correlated ISOLATED_BITS widening ships too (a chain-size-8 zspage overflows
# the 5.15 3-bit `isolated` counter); the 6.3+ fullness rename, zs_page_migrate
# rework and zs_size_stat growth stay out, so NR_ZS_FULLNESS and the exported
# API hold.
# ---------------------------------------------------------------------------

_ZS_KCONFIG_STAT = (
    "config ZSMALLOC_STAT\n"
    "\tbool \"Export zsmalloc statistics\"\n"
    "\tdepends on ZSMALLOC\n"
    "\tselect DEBUG_FS\n"
    "\thelp\n"
    "\t  This option enables code in the zsmalloc to collect various\n"
    "\t  statistics about what's happening in zsmalloc and exports that\n"
    "\t  information to userspace via debugfs.\n"
    "\t  If unsure, say N.\n"
)

_ZS_KCONFIG_CHAIN = (
    "\n"
    "config ZSMALLOC_CHAIN_SIZE\n"
    "\tint \"Maximum number of physical pages per-zspage\"\n"
    "\tdefault 8\n"
    "\trange 4 16\n"
    "\tdepends on ZSMALLOC\n"
    "\thelp\n"
    "\t  This option sets the upper limit on the number of physical pages\n"
    "\t  that a zmalloc page (zspage) can consist of. The optimal zspage\n"
    "\t  chain size is calculated for each size class during the\n"
    "\t  initialization of the pool.\n"
)

_ZS_MACRO_OLD = (
    "/*\n"
    " * A single 'zspage' is composed of up to 2^N discontiguous 0-order (single)\n"
    " * pages. ZS_MAX_ZSPAGE_ORDER defines upper limit on N.\n"
    " */\n"
    "#define ZS_MAX_ZSPAGE_ORDER 2\n"
    "#define ZS_MAX_PAGES_PER_ZSPAGE (_AC(1, UL) << ZS_MAX_ZSPAGE_ORDER)\n"
)

_ZS_MACRO_NEW = (
    "/*\n"
    " * A single 'zspage' is composed of up to N discontiguous 0-order (single)\n"
    " * pages.  CONFIG_ZSMALLOC_CHAIN_SIZE bounds N; the class sizing pass picks\n"
    " * the chain length that wastes the least space per object, so the higher\n"
    " * ceiling is what pays back internal fragmentation in small classes.\n"
    " *\n"
    " * ABK stable_515_backport: zsmalloc chain size (android15-6.6 / 6.2)\n"
    " */\n"
    "#define ZS_MAX_PAGES_PER_ZSPAGE\t(_AC(CONFIG_ZSMALLOC_CHAIN_SIZE, UL))\n"
)

_ZS_SIZING_OLD = (
    "static int get_pages_per_zspage(int class_size)\n"
    "{\n"
    "\tint i, max_usedpc = 0;\n"
    "\t/* zspage order which gives maximum used size per KB */\n"
    "\tint max_usedpc_order = 1;\n"
    "\n"
    "\tfor (i = 1; i <= ZS_MAX_PAGES_PER_ZSPAGE; i++) {\n"
    "\t\tint zspage_size;\n"
    "\t\tint waste, usedpc;\n"
    "\n"
    "\t\tzspage_size = i * PAGE_SIZE;\n"
    "\t\twaste = zspage_size % class_size;\n"
    "\t\tusedpc = (zspage_size - waste) * 100 / zspage_size;\n"
    "\n"
    "\t\tif (usedpc > max_usedpc) {\n"
    "\t\t\tmax_usedpc = usedpc;\n"
    "\t\t\tmax_usedpc_order = i;\n"
    "\t\t}\n"
    "\t}\n"
    "\n"
    "\treturn max_usedpc_order;\n"
    "}\n"
)

_ZS_SIZING_NEW = (
    "static int calculate_zspage_chain_size(int class_size)\n"
    "{\n"
    "\tint i, min_waste = INT_MAX;\n"
    "\tint chain_size = 1;\n"
    "\n"
    "\t/* ABK stable_515_backport: pick the chain with the least hard waste */\n"
    "\tif (is_power_of_2(class_size))\n"
    "\t\treturn chain_size;\n"
    "\n"
    "\tfor (i = 1; i <= ZS_MAX_PAGES_PER_ZSPAGE; i++) {\n"
    "\t\tint waste;\n"
    "\n"
    "\t\twaste = (i * PAGE_SIZE) % class_size;\n"
    "\t\tif (waste < min_waste) {\n"
    "\t\t\tmin_waste = waste;\n"
    "\t\t\tchain_size = i;\n"
    "\t\t}\n"
    "\t}\n"
    "\n"
    "\treturn chain_size;\n"
    "}\n"
)


# ISOLATED_BITS accompanies the chain-size ceiling: the 5.15 baseline gives
# `isolated` 3 bits (max 7), which is enough for a 2^2-page zspage but not for
# CONFIG_ZSMALLOC_CHAIN_SIZE (default 8).  Without this, an 8-page zspage
# overflows `isolated`, corrupts the isolate/putback pairing and trips
# list_add double add in putback_zspage() under memory compaction.
_ZS_ISOLATED_BITS_OLD = "#define ISOLATED_BITS\t3\n"
_ZS_ISOLATED_BITS_NEW = "#define ISOLATED_BITS\t5\n"


def _zsmalloc_chain_size_apply(ctx):
    steps = [
        ("mm/Kconfig", _ZS_KCONFIG_STAT, _ZS_KCONFIG_STAT + _ZS_KCONFIG_CHAIN, T),
        ("mm/zsmalloc.c", _ZS_MACRO_OLD, _ZS_MACRO_NEW, T),
        ("mm/zsmalloc.c", _ZS_SIZING_OLD, _ZS_SIZING_NEW, T),
        ("mm/zsmalloc.c",
         "\t\tpages_per_zspage = get_pages_per_zspage(size);\n",
         "\t\tpages_per_zspage = calculate_zspage_chain_size(size);\n", T),
        ("mm/zsmalloc.c", _ZS_ISOLATED_BITS_OLD, _ZS_ISOLATED_BITS_NEW, T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail
# ---------------------------------------------------------------------------
# MADV_COLLAPSE (android14-6.1 / mainline 6.1)
#
# Synchronous THP collapse on demand.  The 6.1 form drives the 6.x
# hpage_collapse_scan_*() helpers with a caller-owned collapse_control; on
# this baseline those helpers still own the preallocated huge page, so the
# port drives them the way the khugepaged thread does and lifts the scan
# verdict through two new out-parameters (the thread passes NULL).
# ---------------------------------------------------------------------------

_MC_UAPI_ANCHOR = ("#define MADV_POPULATE_WRITE\t23\t"
                   "/* populate (prefault) page tables writable */")
# MADV_COLLAPSE keeps the upstream number and comment; the group marker
# lives with the implementation in mm/khugepaged.c.
_MC_UAPI_NEW = ("#define MADV_COLLAPSE\t25\t\t"
                "/* Synchronous hugepage collapse */")

# include/linux/huge_mm.h -- upstream 6.1's own declaration site, right after
# hugepage_madvise().  That header reaches mm/madvise.c through linux/mm.h, so
# no include has to be grafted, and mm/khugepaged.c gets the prototype for free
# (which keeps -Wmissing-prototypes quiet for the new non-static definition).
# No !THP stub is needed: the switch arm is wrapped in the same ifdef.
_MC_HEADER_ANCHOR = ("int hugepage_madvise(struct vm_area_struct *vma, unsigned long *vm_flags,\n"
                     "\t\t     int advice);")
_MC_HEADER_DECL = ("int madvise_collapse(struct vm_area_struct *vma,\n"
                   "\t\t     struct vm_area_struct **prev,\n"
                   "\t\t     unsigned long start, unsigned long end);")

# mm/madvise.c: three switch arms.  Collapse is fine under the read side of
# the mmap_lock, so it joins the madvise_need_mmap_write() exceptions.
_MC_NEED_MMAP_OLD = ("\tcase MADV_POPULATE_READ:\n"
                     "\tcase MADV_POPULATE_WRITE:\n"
                     "\t\treturn 0;")
_MC_NEED_MMAP_NEW = ("\tcase MADV_POPULATE_READ:\n"
                     "\tcase MADV_POPULATE_WRITE:\n"
                     "\tcase MADV_COLLAPSE:\n"
                     "\t\treturn 0;")

# The visit itself.  Upstream 6.1 leaves this case unconditional because it
# ships a !CONFIG_TRANSPARENT_HUGEPAGE stub in the header; on this baseline
# the ifdef is the cheaper equivalent and keeps THP-off trees untouched.
_MC_CASE_OLD = ("\tcase MADV_POPULATE_READ:\n"
                "\tcase MADV_POPULATE_WRITE:\n"
                "\t\treturn madvise_populate(vma, prev, start, end, behavior);")
_MC_CASE_NEW = (_MC_CASE_OLD + "\n"
                "#ifdef CONFIG_TRANSPARENT_HUGEPAGE\n"
                "\t/* ABK stable_515_backport: MADV_COLLAPSE (android14-6.1) */\n"
                "\tcase MADV_COLLAPSE:\n"
                "\t\treturn madvise_collapse(vma, prev, start, end);\n"
                "#endif")

# Acceptance list.  Deliberately not added to process_madvise_behavior_valid():
# the 5.15 process_madvise() path does not carry the range bookkeeping the
# collapse needs, so callers there keep getting -EINVAL.
_MC_VALID_OLD = ("#ifdef CONFIG_TRANSPARENT_HUGEPAGE\n"
                 "\tcase MADV_HUGEPAGE:\n"
                 "\tcase MADV_NOHUGEPAGE:\n"
                 "#endif")
_MC_VALID_NEW = ("#ifdef CONFIG_TRANSPARENT_HUGEPAGE\n"
                 "\tcase MADV_HUGEPAGE:\n"
                 "\tcase MADV_NOHUGEPAGE:\n"
                 "\tcase MADV_COLLAPSE:\n"
                 "#endif")

# The scan helpers signatures are unchanged in shape across 167/.178/.194/.216,
# so every step below is a single anchor set (see docs/porting_policy.md).
_MC_PMD_SIG_OLD = ("static int khugepaged_scan_pmd(struct mm_struct *mm,\n"
                   "\t\t\t       struct vm_area_struct *vma,\n"
                   "\t\t\t       unsigned long address,\n"
                   "\t\t\t       struct page **hpage)")
_MC_PMD_SIG_NEW = ("static int khugepaged_scan_pmd(struct mm_struct *mm,\n"
                   "\t\t\t       struct vm_area_struct *vma,\n"
                   "\t\t\t       unsigned long address,\n"
                   "\t\t\t       struct page **hpage, int *res)")
_MC_PMD_OUT_OLD = ('\t\t\t\t     none_or_zero, result, unmapped);\n'
                   '\treturn ret;\n'
                   '}')
_MC_PMD_OUT_NEW = ('\t\t\t\t     none_or_zero, result, unmapped);\n'
                   '\t/* ABK stable_515_backport: MADV_COLLAPSE needs the verdict, '
                   'not just the flag that the kthread cares about. */\n'
                   '\tif (res)\n'
                   '\t\t*res = result;\n'
                   '\treturn ret;\n'
                   '}')

# Both khugepaged_scan_file() definitions move to the new signature.  The
# CONFIG_SHMEM-off stub goes first because its old block (signature plus the
# BUILD_BUG() body) is unique, which leaves the bare two-line signature unique
# for the step that follows -- neither step depends on which occurrence
# str.replace() happens to hit.
#
# The stub's replacement must not contain _MC_FILE_SIG_NEW verbatim.  It is
# written first and replace_once() checks the *replacement* before the anchor
# (idempotency), so a stub carrying the next step's exact text makes that step
# short-circuit to already_present: the real CONFIG_SHMEM=y definition stays at
# four parameters while its body and every caller move to five, which is a
# compile error the group-level status cannot see (it did reach CI once).
# Hence the deliberately different line wrapping below -- same C signature,
# text that cannot collide with the step that follows.
_MC_FILE_STUB_OLD = ('static void khugepaged_scan_file(struct mm_struct *mm,\n'
                     '\t\tstruct file *file, pgoff_t start, struct page **hpage)\n'
                     '{\n'
                     '\tBUILD_BUG();\n'
                     '}')
_MC_FILE_SIG_OLD = ('static void khugepaged_scan_file(struct mm_struct *mm,\n'
                    '\t\tstruct file *file, pgoff_t start, struct page **hpage)')
_MC_FILE_SIG_NEW = ('static void khugepaged_scan_file(struct mm_struct *mm,\n'
                    '\t\tstruct file *file, pgoff_t start, struct page **hpage,\n'
                    '\t\tint *res)')
_MC_FILE_STUB_NEW = ('static void khugepaged_scan_file(struct mm_struct *mm, struct file *file,\n'
                     '\t\tpgoff_t start, struct page **hpage, int *res)\n'
                     '{\n'
                     '\tBUILD_BUG();\n'
                     '}')

# Verdict for the file/shmem path.  collapse_file() is void on this baseline,
# so only the pre-scan verdict is actionable; a range that passes every check
# is reported as collapsed, which is exactly what the kthread assumes too.
_MC_FILE_OUT_OLD = ('\t\t\tcollapse_file(mm, file, start, hpage, node);\n'
                    '\t\t}\n'
                    '\t}\n'
                    '\n'
                    '\t/* TODO: tracepoints */\n'
                    '}')
_MC_FILE_OUT_NEW = ('\t\t\tcollapse_file(mm, file, start, hpage, node);\n'
                    '\t\t}\n'
                    '\t}\n'
                    '\n'
                    '\tif (res)\n'
                    '\t\t*res = result;\n'
                    '\n'
                    '\t/* TODO: tracepoints */\n'
                    '}')

# The khugepaged thread itself passes NULL for both new out-parameters.
_MC_CALLER_OLD = ('\t\t\t\tkhugepaged_scan_file(mm, file, pgoff, hpage);\n'
                  '\t\t\t\tfput(file);\n'
                  '\t\t\t} else {\n'
                  '\t\t\t\tret = khugepaged_scan_pmd(mm, vma,\n'
                  '\t\t\t\t\t\tkhugepaged_scan.address,\n'
                  '\t\t\t\t\t\thpage);\n'
                  '\t\t\t}')
_MC_CALLER_NEW = ('\t\t\t\tkhugepaged_scan_file(mm, file, pgoff, hpage,\n'
                  '\t\t\t\t\t      NULL);\n'
                  '\t\t\t\tfput(file);\n'
                  '\t\t\t} else {\n'
                  '\t\t\t\tret = khugepaged_scan_pmd(mm, vma,\n'
                  '\t\t\t\t\t\tkhugepaged_scan.address,\n'
                  '\t\t\t\t\t\thpage, NULL);\n'
                  '\t\t\t}')

# madvise_collapse() itself, inserted right before khugepaged_scan_mm_slot().
# Split into line tuples so the C text stays readable and diffable; the
# semantics follow mainline 6.1: iterate PMD-aligned addresses, tolerate the
# whitelisted scan results, and map the last failure to an errno.
_MC_IMPL_A = (
    '/*\n'
    ' * ABK stable_515_backport: MADV_COLLAPSE (android14-6.1 / mainline 6.1)\n'
    ' *\n'
    ' * Synchronously collapse every PMD-aligned chunk of [start, end) into a\n'
    ' * huge page, regardless of the THP defrag setting of the process.\n'
    ' *\n'
    ' * On the 6.1 form the scan helpers take a caller-owned collapse_control;\n'
    ' * here they still own the preallocated huge page and the shared\n'
    ' * khugepaged_node_load[] scratch, so this drives them exactly the way\n'
    ' * the khugepaged thread does: hold mmap_lock for reading, and re-take it\n'
    ' * after a helper dropped it.\n'
    ' */\n'
    'static int madvise_collapse_errno(int r)\n'
    '{\n'
    '\t/*\n'
    '\t * MADV_COLLAPSE breaks from existing madvise(2) conventions to provide\n'
    '\t * actionable feedback to caller, so they may take an appropriate\n'
    '\t * fallback measure depending on the nature of the failure.\n'
    '\t */\n'
)

_MC_IMPL_B = (
    '\tswitch (r) {\n'
    '\tcase SCAN_ALLOC_HUGE_PAGE_FAIL:\n'
    '\t\treturn -ENOMEM;\n'
    '\tcase SCAN_CGROUP_CHARGE_FAIL:\n'
    '\t\treturn -EBUSY;\n'
    '\t/* Resource temporary unavailable - trying again might succeed */\n'
    '\tcase SCAN_PAGE_COUNT:\n'
    '\tcase SCAN_PAGE_LOCK:\n'
    '\tcase SCAN_PAGE_LRU:\n'
    '\tcase SCAN_DEL_PAGE_LRU:\n'
    '\tcase SCAN_SCAN_ABORT:\n'
    '\t\t/*\n'
    '\t\t * SCAN_SCAN_ABORT is not in the 6.1 list because that tree dropped\n'
    '\t\t * the shared node-load scratch; on this baseline it can also be a\n'
    '\t\t * transient effect of a concurrent kthread scan, so it is retryable.\n'
    '\t\t */\n'
    '\t\treturn -EAGAIN;\n'
    '\t/*\n'
    '\t * Other: retrying is unlikely to help; the error is intrinsic to the\n'
    '\t * specified memory range, and khugepaged will not be able to collapse\n'
    '\t * it either.\n'
    '\t */\n'
    '\tdefault:\n'
    '\t\treturn -EINVAL;\n'
    '\t}\n'
    '}\n'
)

_MC_IMPL_C = (
    '\n'
    'int madvise_collapse(struct vm_area_struct *vma, struct vm_area_struct **prev,\n'
    '\t\t     unsigned long start, unsigned long end)\n'
    '{\n'
    '\tstruct mm_struct *mm = vma->vm_mm;\n'
    '\tstruct page *hpage = NULL;\n'
    '\tunsigned long hstart, hend, addr;\n'
    '\tbool wait = true, mmap_locked = true;\n'
    '\tint thps = 0, last_fail = SCAN_FAIL;\n'
    '\n'
    '\tBUG_ON(vma->vm_start > start);\n'
    '\tBUG_ON(vma->vm_end < end);\n'
    '\n'
    '\t*prev = vma;\n'
    '\n'
    '\tif (!hugepage_vma_check(vma, vma->vm_flags))\n'
    '\t\treturn -EINVAL;\n'
    '\n'
    '\tmmgrab(mm);\n'
    '\tlru_add_drain_all();\n'
    '\n'
    '\thstart = (start + ~HPAGE_PMD_MASK) & HPAGE_PMD_MASK;\n'
    '\thend = end & HPAGE_PMD_MASK;\n'
)

_MC_IMPL_D = (
    '\n'
    '\tfor (addr = hstart; addr < hend; addr += HPAGE_PMD_SIZE) {\n'
    '\t\tint result = SCAN_FAIL;\n'
    '\n'
    '\t\tif (!mmap_locked) {\n'
    '\t\t\tcond_resched();\n'
    '\t\t\tmmap_read_lock(mm);\n'
    '\t\t\tmmap_locked = true;\n'
    '\t\t\t/*\n'
    '\t\t\t * ABK stable_515_backport: on this baseline\n'
    '\t\t\t * hugepage_vma_revalidate() returns 0 on success and a\n'
    '\t\t\t * scan code otherwise (6.1 returns SCAN_SUCCEED, which is\n'
    '\t\t\t * 1 here), so test it the way its other callers do.\n'
    '\t\t\t */\n'
    '\t\t\tresult = hugepage_vma_revalidate(mm, addr, &vma);\n'
    '\t\t\tif (result) {\n'
    '\t\t\t\tlast_fail = result;\n'
    '\t\t\t\tgoto out_nolock;\n'
    '\t\t\t}\n'
    '\n'
    '\t\t\thend = min(hend, vma->vm_end & HPAGE_PMD_MASK);\n'
    '\t\t}\n'
    '\n'
    '\t\tif (!khugepaged_prealloc_page(&hpage, &wait)) {\n'
    '\t\t\tlast_fail = SCAN_ALLOC_HUGE_PAGE_FAIL;\n'
    '\t\t\tgoto out_maybelock;\n'
    '\t\t}\n'
    '\n'
    '\t\tcond_resched();\n'
    '\n'
    '\t\tif (IS_ENABLED(CONFIG_SHMEM) && vma->vm_file) {\n'
    '\t\t\tstruct file *file = get_file(vma->vm_file);\n'
    '\t\t\tpgoff_t pgoff = linear_page_index(vma, addr);\n'
    '\n'
    '\t\t\tmmap_read_unlock(mm);\n'
    '\t\t\tmmap_locked = false;\n'
    '\t\t\tkhugepaged_scan_file(mm, file, pgoff, &hpage, &result);\n'
    '\t\t\tfput(file);\n'
    '\t\t} else {\n'
    '\t\t\tif (khugepaged_scan_pmd(mm, vma, addr, &hpage, &result))\n'
    '\t\t\t\tmmap_locked = false;\n'
    '\t\t}\n'
    '\n'
    '\t\tif (!mmap_locked)\n'
    '\t\t\t*prev = NULL;\t/* tell caller we dropped mmap_lock */\n'
)

_MC_IMPL_E = (
    '\n'
    '\t\tswitch (result) {\n'
    '\t\tcase SCAN_SUCCEED:\n'
    '\t\t\t++thps;\n'
    '\t\t\tbreak;\n'
    '\t\t/* Whitelisted set of results where continuing OK */\n'
    '\t\tcase SCAN_PMD_NULL:\n'
    '\t\tcase SCAN_EXCEED_NONE_PTE:\n'
    '\t\tcase SCAN_EXCEED_SWAP_PTE:\n'
    '\t\tcase SCAN_EXCEED_SHARED_PTE:\n'
    '\t\tcase SCAN_PTE_NON_PRESENT:\n'
    '\t\tcase SCAN_PTE_UFFD_WP:\n'
    '\t\tcase SCAN_PAGE_RO:\n'
    '\t\tcase SCAN_LACK_REFERENCED_PAGE:\n'
    '\t\tcase SCAN_PAGE_NULL:\n'
    '\t\tcase SCAN_PAGE_ANON:\n'
    '\t\tcase SCAN_PAGE_COMPOUND:\n'
    '\t\tcase SCAN_PAGE_HAS_PRIVATE:\n'
    '\t\tcase SCAN_SWAP_CACHE_PAGE:\n'
    '\t\tcase SCAN_PAGE_COUNT:\n'
    '\t\tcase SCAN_PAGE_LOCK:\n'
    '\t\tcase SCAN_PAGE_LRU:\n'
    '\t\tcase SCAN_DEL_PAGE_LRU:\n'
    '\t\tcase SCAN_SCAN_ABORT:\n'
    '\t\t\tlast_fail = result;\n'
    '\t\t\tbreak;\n'
    '\t\tdefault:\n'
    '\t\t\tlast_fail = result;\n'
    '\t\t\t/* Other error, exit */\n'
    '\t\t\tgoto out_maybelock;\n'
    '\t\t}\n'
    '\t}\n'
)

_MC_IMPL_F = (
    '\n'
    'out_maybelock:\n'
    '\t/* Caller expects us to hold mmap_lock on return */\n'
    '\tif (!mmap_locked)\n'
    '\t\tmmap_read_lock(mm);\n'
    'out_nolock:\n'
    '\tmmap_assert_locked(mm);\n'
    '\tif (!IS_ERR_OR_NULL(hpage))\n'
    '\t\tput_page(hpage);\n'
    '\tmmdrop(mm);\n'
    '\n'
    '\treturn thps == ((hend - hstart) >> HPAGE_PMD_SHIFT) ? 0\n'
    '\t\t\t: madvise_collapse_errno(last_fail);\n'
    '}\n'
    '\n'
)

_MC_SCAN_SLOT_SIG = ('static unsigned int khugepaged_scan_mm_slot(unsigned int pages,\n'
                     '\t\t\t\t\t    struct page **hpage)')
_MC_IMPL_OLD = _MC_SCAN_SLOT_SIG
_MC_IMPL_NEW = (_MC_IMPL_A + _MC_IMPL_B + _MC_IMPL_C + _MC_IMPL_D +
                _MC_IMPL_E + _MC_IMPL_F + _MC_SCAN_SLOT_SIG)


def _madvise_collapse_apply(ctx):
    # UAPI number and the extern declaration are single lines behind unique
    # anchors, which is what common.ensure_after() is for; both are required,
    # because a switch arm without the define would not compile.
    singles = (
        ("include/uapi/asm-generic/mman-common.h", _MC_UAPI_ANCHOR, _MC_UAPI_NEW),
        ("include/linux/huge_mm.h", _MC_HEADER_ANCHOR, _MC_HEADER_DECL),
    )
    staged = {}
    for rel, anchor, snippet in singles:
        text, status = common.ensure_after(ctx.read(rel), anchor, snippet)
        if status == "missing_anchor":
            return "blocked_by_shape", f"{rel}: no anchor for {snippet.splitlines()[0]}"
        if status == "applied":
            staged[rel] = text

    steps = [
        ("mm/madvise.c", _MC_NEED_MMAP_OLD, _MC_NEED_MMAP_NEW, T),
        ("mm/madvise.c", _MC_CASE_OLD, _MC_CASE_NEW, T),
        ("mm/madvise.c", _MC_VALID_OLD, _MC_VALID_NEW, T),
        ("mm/khugepaged.c", _MC_FILE_STUB_OLD, _MC_FILE_STUB_NEW, T),
        ("mm/khugepaged.c", _MC_FILE_SIG_OLD, _MC_FILE_SIG_NEW, T),
        ("mm/khugepaged.c", _MC_FILE_OUT_OLD, _MC_FILE_OUT_NEW, T),
        ("mm/khugepaged.c", _MC_PMD_SIG_OLD, _MC_PMD_SIG_NEW, T),
        ("mm/khugepaged.c", _MC_PMD_OUT_OLD, _MC_PMD_OUT_NEW, T),
        ("mm/khugepaged.c", _MC_CALLER_OLD, _MC_CALLER_NEW, T),
        ("mm/khugepaged.c", _MC_IMPL_OLD, _MC_IMPL_NEW, T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail

    for rel, text in staged.items():
        ctx.write(rel, text)
    if staged and status == "already_present":
        status = "applied"
    return status, detail


# Batch 6 groups.  Registered last so the graft order inside the child stays
# "code first, then the config that makes it reachable".
PATCH_GROUPS = PATCH_GROUPS + [
    PatchGroup(
        "config_enablement",
        "enable the module-owned zram recompression symbols in the build defconfig, optionally aligning with the android15-6.6 GKI config",
        ["ABK config lane: android15-6.6 gki_defconfig symbols absent from every android13-5.15 baseline"],
        ["arch/arm64/configs/gki_defconfig"],
        _config_enablement_apply,
    ),
    PatchGroup(
        "zsmalloc_chain_size",
        "make the zspage chain length a CONFIG_ZSMALLOC_CHAIN_SIZE bound chosen for minimal absolute waste (android15-6.6 / 6.2 class sizing)",
        ["ACK android15-6.6 zsmalloc chain size (6.2 series; not in android14-6.1)"],
        ["mm/Kconfig", "mm/zsmalloc.c"],
        _zsmalloc_chain_size_apply,
    ),
    PatchGroup(
        "madvise_collapse",
        "synchronous THP collapse via MADV_COLLAPSE (android14-6.1), driven by the existing 5.15 khugepaged scan helpers",
        ["MADV_COLLAPSE series (android14-6.1 / mainline 6.1)", "ACK android15-6.6 same form"],
        ["include/uapi/asm-generic/mman-common.h", "include/linux/huge_mm.h",
         "mm/madvise.c", "mm/khugepaged.c"],
        _madvise_collapse_apply,
    ),
]


# ===========================================================================
# Batch 8: page allocator fallback reuse (android15-6.6 / 6.12 series)
# ===========================================================================
#
# The 5.15 allocator has the same fallback policy as the source series, but
# still folds claiming and single-page stealing into __rmqueue_fallback().
# Keep the AOSP vendor hooks and 5.15's steal_suitable_fallback() contract,
# while separating the two modes so rmqueue_bulk() can remember which search
# phase produced the previous page under one zone lock.

_B8_FIND_OLD = """/*
 * Check whether there is a suitable fallback freepage with requested order.
 * If only_stealable is true, this function returns fallback_mt only if
 * we can steal other freepages all together. This would help to reduce
 * fragmentation due to mixed migratetype pages in one pageblock.
 */
int find_suitable_fallback(struct free_area *area, unsigned int order,
\t\t\tint migratetype, bool only_stealable, bool *can_steal)
{
\tint i;
\tint fallback_mt;

\tif (area->nr_free == 0)
\t\treturn -1;

\t*can_steal = false;
\tfor (i = 0;; i++) {
\t\tfallback_mt = fallbacks[migratetype][i];
\t\tif (fallback_mt == MIGRATE_TYPES)
\t\t\tbreak;

\t\tif (free_area_empty(area, fallback_mt))
\t\t\tcontinue;

\t\tif (can_steal_fallback(order, migratetype))
\t\t\t*can_steal = true;

\t\tif (!only_stealable)
\t\t\treturn fallback_mt;

\t\tif (*can_steal)
\t\t\treturn fallback_mt;
\t}

\treturn -1;
}
"""

_B8_FIND_NEW = """/*
 * Check whether there is a suitable fallback freepage with requested order.
 * If claimable is true, this function returns fallback_mt only if
 * we would do this whole-block claiming. This would help to reduce
 * fragmentation due to mixed migratetype pages in one pageblock.
 */
int find_suitable_fallback(struct free_area *area, unsigned int order,
\t\t\tint migratetype, bool claimable)
{
\tint i;

\t/* ABK stable_515_backport: distinguish an unclaimable order (-2) from
\t * an order whose fallback lists are empty (-1). */
\tif (claimable && !can_steal_fallback(order, migratetype))
\t\treturn -2;

\tif (area->nr_free == 0)
\t\treturn -1;

\tfor (i = 0; fallbacks[migratetype][i] != MIGRATE_TYPES; i++) {
\t\tint fallback_mt = fallbacks[migratetype][i];

\t\tif (!free_area_empty(area, fallback_mt))
\t\t\treturn fallback_mt;
\t}

\treturn -1;
}
"""

_B8_COMPACTION_DECL_OLD = """\t\tstruct free_area *area = &cc->zone->free_area[order];
\t\tbool can_steal;

\t\t/* Job done if page is free of the right migratetype */"""

_B8_COMPACTION_DECL_NEW = """\t\tstruct free_area *area = &cc->zone->free_area[order];

\t\t/* Job done if page is free of the right migratetype */"""

_B8_COMPACTION_CALL_OLD = """\t\tif (find_suitable_fallback(area, order, migratetype,
\t\t\t\t\t\ttrue, &can_steal) != -1) {"""

_B8_COMPACTION_CALL_NEW = """\t\tif (find_suitable_fallback(area, order, migratetype,
\t\t\t\t\t\ttrue) >= 0) {"""

_B8_FALLBACK_OLD = """/*
 * Try finding a free buddy page on the fallback list and put it on the free
 * list of requested migratetype, possibly along with other pages from the same
 * block, depending on fragmentation avoidance heuristics. Returns true if
 * fallback was found so that __rmqueue_smallest() can grab it.
 *
 * The use of signed ints for order and current_order is a deliberate
 * deviation from the rest of this file, to make the for loop
 * condition simpler.
 */
static __always_inline bool
__rmqueue_fallback(struct zone *zone, int order, int start_migratetype,
\t\t\t\t\t\tunsigned int alloc_flags)
{
\tstruct free_area *area;
\tint current_order;
\tint min_order = order;
\tstruct page *page;
\tint fallback_mt;
\tbool can_steal;

\t/*
\t * Do not steal pages from freelists belonging to other pageblocks
\t * i.e. orders < pageblock_order. If there are no local zones free,
\t * the zonelists will be reiterated without ALLOC_NOFRAGMENT.
\t */
\tif (alloc_flags & ALLOC_NOFRAGMENT)
\t\tmin_order = pageblock_order;

\t/*
\t * Find the largest available free page in the other list. This roughly
\t * approximates finding the pageblock with the most free pages, which
\t * would be too costly to do exactly.
\t */
\tfor (current_order = MAX_ORDER - 1; current_order >= min_order;
\t\t\t\t--current_order) {
\t\tarea = &(zone->free_area[current_order]);
\t\tfallback_mt = find_suitable_fallback(area, current_order,
\t\t\t\tstart_migratetype, false, &can_steal);
\t\tif (fallback_mt == -1)
\t\t\tcontinue;

\t\t/*
\t\t * We cannot steal all free pages from the pageblock and the
\t\t * requested migratetype is movable. In that case it's better to
\t\t * steal and split the smallest available page instead of the
\t\t * largest available page, because even if the next movable
\t\t * allocation falls back into a different pageblock than this
\t\t * one, it won't cause permanent fragmentation.
\t\t */
\t\tif (!can_steal && start_migratetype == MIGRATE_MOVABLE
\t\t\t\t\t&& current_order > order)
\t\t\tgoto find_smallest;

\t\tgoto do_steal;
\t}

\treturn false;

find_smallest:
\tfor (current_order = order; current_order < MAX_ORDER;
\t\t\t\t\t\t\tcurrent_order++) {
\t\tarea = &(zone->free_area[current_order]);
\t\tfallback_mt = find_suitable_fallback(area, current_order,
\t\t\t\tstart_migratetype, false, &can_steal);
\t\tif (fallback_mt != -1)
\t\t\tbreak;
\t}

\t/*
\t * This should not happen - we already found a suitable fallback
\t * when looking for the largest page.
\t */
\tVM_BUG_ON(current_order == MAX_ORDER);

do_steal:
\tpage = get_page_from_free_area(area, fallback_mt);

\tsteal_suitable_fallback(zone, page, alloc_flags, start_migratetype,
\t\t\t\t\t\t\t\tcan_steal);

\ttrace_mm_page_alloc_extfrag(page, order, current_order,
\t\tstart_migratetype, fallback_mt);

\treturn true;

}
"""

_B8_FALLBACK_NEW = """/*
 * ABK stable_515_backport: split fallback claim and single-page steal phases.
 * Try to allocate from a fallback migratetype by claiming the entire block,
 * i.e. converting it to the allocation's start migratetype.
 *
 * The use of signed ints for order and current_order is a deliberate
 * deviation from the rest of this file, to make the for loop
 * condition simpler.
 */
static __always_inline struct page *
__rmqueue_claim(struct zone *zone, int order, int start_migratetype,
\t\t\t\t\t\tunsigned int alloc_flags)
{
\tstruct free_area *area;
\tint current_order;
\tint min_order = order;
\tstruct page *page;
\tint fallback_mt;

\t/* ABK stable_515_backport: reuse the claim phase while zone->lock is held. */
\tif (alloc_flags & ALLOC_NOFRAGMENT)
\t\tmin_order = pageblock_order;

\tfor (current_order = MAX_ORDER - 1; current_order >= min_order;
\t\t\t\t--current_order) {
\t\tarea = &(zone->free_area[current_order]);
\t\tfallback_mt = find_suitable_fallback(area, current_order,
\t\t\t\t\t\t\tstart_migratetype, true);

\t\t/* No block in that order. */
\t\tif (fallback_mt == -1)
\t\t\tcontinue;

\t\t/* Advanced into orders too low to claim, abort. */
\t\tif (fallback_mt == -2)
\t\t\tbreak;

\t\tpage = get_page_from_free_area(area, fallback_mt);
\t\tsteal_suitable_fallback(zone, page, alloc_flags, start_migratetype,
\t\t\t\t\t\t\t\t\ttrue);
\t\tpage = __rmqueue_smallest(zone, order, start_migratetype);
\t\tif (page) {
\t\t\ttrace_mm_page_alloc_extfrag(page, order, current_order,
\t\t\t\t\t\t\t\tstart_migratetype, fallback_mt);
\t\t\treturn page;
\t\t}
\t}

\treturn NULL;
}

/*
 * ABK stable_515_backport: keep single-page stealing as a separate mode.
 * Try to steal a single page from some fallback migratetype. Leave the rest of
 * the block as its current migratetype, potentially causing fragmentation.
 */
static __always_inline struct page *
__rmqueue_steal(struct zone *zone, int order, int start_migratetype,
\t\t\t\t\t\tunsigned int alloc_flags)
{
\tstruct free_area *area;
\tint current_order;
\tstruct page *page;
\tint fallback_mt;

\tfor (current_order = order; current_order < MAX_ORDER; current_order++) {
\t\tarea = &(zone->free_area[current_order]);
\t\tfallback_mt = find_suitable_fallback(area, current_order,
\t\t\t\t\t\t\tstart_migratetype, false);
\t\tif (fallback_mt == -1)
\t\t\tcontinue;

\t\tpage = get_page_from_free_area(area, fallback_mt);
\t\tsteal_suitable_fallback(zone, page, alloc_flags, start_migratetype,
\t\t\t\t\t\t\t\t\tfalse);
\t\tpage = __rmqueue_smallest(zone, order, start_migratetype);
\t\tif (page) {
\t\t\ttrace_mm_page_alloc_extfrag(page, order, current_order,
\t\t\t\t\t\t\t\tstart_migratetype, fallback_mt);
\t\t\treturn page;
\t\t}
\t}

\treturn NULL;
}

/* ABK stable_515_backport: fallback search phase remembered by rmqueue_bulk. */
enum rmqueue_mode {
\tRMQUEUE_NORMAL,
\tRMQUEUE_CMA,
\tRMQUEUE_CLAIM,
\tRMQUEUE_STEAL,
};
"""

_B8_RMQUEUE_OLD = """/*
 * Do the hard work of removing an element from the buddy allocator.
 * Call me with the zone->lock already held.
 */
static __always_inline struct page *
__rmqueue(struct zone *zone, unsigned int order, int migratetype,
\t\t\t\t\t\tunsigned int alloc_flags)
{
\tstruct page *page = NULL;

\ttrace_android_vh_rmqueue_smallest_bypass(&page, zone, order, migratetype);
\tif (page)
\t\treturn page;

retry:
\tpage = __rmqueue_smallest(zone, order, migratetype);

\t/*
\t * let normal GFP_MOVABLE has chance to try MIGRATE_CMA
\t */
\tif (unlikely(!page) && (migratetype == MIGRATE_MOVABLE)) {
\t\tbool try_cma = false;
\t\ttrace_android_vh_rmqueue_cma_fallback(zone, order, &page);
\t\ttrace_android_vh_try_cma_fallback(zone, order, &try_cma);
\t\tif (try_cma)
\t\t\tpage = __rmqueue_cma_fallback(zone, order);
\t}

\tif (unlikely(!page) && __rmqueue_fallback(zone, order, migratetype,
\t\t\t\t\t\t  alloc_flags))
\t\tgoto retry;

\tif (page)
\t\ttrace_mm_page_alloc_zone_locked(page, order, migratetype);
\treturn page;
}
"""

_B8_RMQUEUE_NEW = """/*
 * Do the hard work of removing an element from the buddy allocator.
 * Call me with the zone->lock already held.
 */
static __always_inline struct page *
__rmqueue(struct zone *zone, unsigned int order, int migratetype,
\t\t\t\t\t\tunsigned int alloc_flags, enum rmqueue_mode *mode)
{
\tstruct page *page = NULL;

\ttrace_android_vh_rmqueue_smallest_bypass(&page, zone, order, migratetype);
\tif (page)
\t\treturn page;

\t/*
\t * First try the freelists of the requested migratetype, then try fallback
\t * modes with increasing levels of fragmentation risk. The fallback logic
\t * is expensive and rmqueue_bulk() keeps zone->lock held across the loop,
\t * so remember the successful mode for the next page in that batch.
\t */
\tswitch (*mode) {
\tcase RMQUEUE_NORMAL:
\t\tpage = __rmqueue_smallest(zone, order, migratetype);
\t\tif (page)
\t\t\tgoto out;
\t\tfallthrough;
\tcase RMQUEUE_CMA:
\t\tif (migratetype == MIGRATE_MOVABLE) {
\t\t\tbool try_cma = false;
\t\t\tbool from_cma = false;

\t\t\ttrace_android_vh_rmqueue_cma_fallback(zone, order, &page);
\t\t\ttrace_android_vh_try_cma_fallback(zone, order, &try_cma);
\t\t\tif (try_cma) {
\t\t\t\tpage = __rmqueue_cma_fallback(zone, order);
\t\t\t\tfrom_cma = !!page;
\t\t\t}
\t\t\tif (page) {
\t\t\t\tif (from_cma)
\t\t\t\t\t*mode = RMQUEUE_CMA;
\t\t\t\tgoto out;
\t\t\t}
\t\t}
\t\tfallthrough;
\tcase RMQUEUE_CLAIM:
\t\tpage = __rmqueue_claim(zone, order, migratetype, alloc_flags);
\t\tif (page) {
\t\t\t/* Replenished the preferred freelist, go back to normal mode. */
\t\t\t*mode = RMQUEUE_NORMAL;
\t\t\tgoto out;
\t\t}
\t\tfallthrough;
\tcase RMQUEUE_STEAL:
\t\tif (!(alloc_flags & ALLOC_NOFRAGMENT)) {
\t\t\tpage = __rmqueue_steal(zone, order, migratetype, alloc_flags);
\t\t\tif (page) {
\t\t\t\t*mode = RMQUEUE_STEAL;
\t\t\t\tgoto out;
\t\t\t}
\t\t}
\t\tbreak;
\t}

\tpage = NULL;
out:
\tif (page)
\t\ttrace_mm_page_alloc_zone_locked(page, order, migratetype);
\treturn page;
}
"""

_B8_BULK_DECL_OLD = """{
\tint i, allocated = 0;

\t/* Caller must hold IRQ-safe pcp->lock so IRQs are disabled. */"""

_B8_BULK_DECL_NEW = """{
\t/* ABK stable_515_backport: reuse the fallback mode for this locked batch. */
\tenum rmqueue_mode rmqm = RMQUEUE_NORMAL;
\tint i, allocated = 0;

\t/* Caller must hold IRQ-safe pcp->lock so IRQs are disabled. */"""

_B8_BULK_CALL_OLD = """\t\telse
\t\t\tpage = __rmqueue(zone, order, migratetype, alloc_flags);"""

_B8_BULK_CALL_NEW = """\t\telse
\t\t\tpage = __rmqueue(zone, order, migratetype, alloc_flags, &rmqm);"""

_B8_BUDDY_CALL_OLD = """\t\t\t\tif (try_cma)
\t\t\t\t\tpage = __rmqueue_cma(zone, order, migratetype,
\t\t\t\t\t\t\talloc_flags);
\t\t\t}
\t\t\tif (!page)
\t\t\t\tpage = __rmqueue(zone, order, migratetype,
\t\t\t\t\t\talloc_flags);"""

_B8_BUDDY_CALL_NEW = """\t\t\t\tif (try_cma)
\t\t\t\t\tpage = __rmqueue_cma(zone, order, migratetype,
\t\t\t\t\t\t\talloc_flags);
\t\t\t}
\t\t\tif (!page) {
\t\t\t\tenum rmqueue_mode rmqm = RMQUEUE_NORMAL;

\t\t\t\tpage = __rmqueue(zone, order, migratetype,
\t\t\t\t\t\talloc_flags, &rmqm);
\t\t\t}"""

# The CMA probe itself is nested one level deeper than the following fallback
# in the 5.15 function.  Build the highatomic variant from the exact compact
# anchor instead of indenting the whole mixed-scope block.
_B8_BUDDY_HIGHATOMIC_TAIL_OLD = """\t\t\tif (!page)
				page = __rmqueue(zone, order, migratetype,
						alloc_flags);"""
_B8_BUDDY_HIGHATOMIC_TAIL_NEW = """\t\t\tif (!page) {
				enum rmqueue_mode rmqm = RMQUEUE_NORMAL;

				page = __rmqueue(zone, order, migratetype,
						alloc_flags, &rmqm);
			}"""
_B8_BUDDY_HIGHATOMIC_RETRY = """\t\t\tif (!page) {
				page = __rmqueue(zone, order, migratetype,
						alloc_flags);

				/*
				 * If the allocation fails, allow OOM handling and
				 * order-0 (atomic) allocs access to HIGHATOMIC
				 * reserves as failing now is worse than failing a
				 * high-order atomic allocation in the future.
				 */
				if (!page && (alloc_flags & (ALLOC_OOM|ALLOC_NON_BLOCK)))
					page = __rmqueue_smallest(zone, order, MIGRATE_HIGHATOMIC);
			}"""
_B8_BUDDY_HIGHATOMIC_RETRY_NEW = """\t\t\tif (!page) {
				enum rmqueue_mode rmqm = RMQUEUE_NORMAL;

				page = __rmqueue(zone, order, migratetype,
						alloc_flags, &rmqm);

				/*
				 * If the allocation fails, allow OOM handling and
				 * order-0 (atomic) allocs access to HIGHATOMIC
				 * reserves as failing now is worse than failing a
				 * high-order atomic allocation in the future.
				 */
				if (!page && (alloc_flags & (ALLOC_OOM|ALLOC_NON_BLOCK)))
					page = __rmqueue_smallest(zone, order, MIGRATE_HIGHATOMIC);
			}"""
_B8_BUDDY_CALL_HIGHATOMIC_OLD = _B8_BUDDY_CALL_OLD.replace(
    _B8_BUDDY_HIGHATOMIC_TAIL_OLD, _B8_BUDDY_HIGHATOMIC_RETRY)
_B8_BUDDY_CALL_HIGHATOMIC_NEW = _B8_BUDDY_CALL_NEW.replace(
    _B8_BUDDY_HIGHATOMIC_TAIL_NEW, _B8_BUDDY_HIGHATOMIC_RETRY_NEW)


def _pagealloc_fallback_reuse_apply(ctx):
    page_alloc_probe = ctx.read("mm/page_alloc.c")
    buddy_call = (_B8_BUDDY_CALL_HIGHATOMIC_OLD,
                  _B8_BUDDY_CALL_HIGHATOMIC_NEW)
    if "If the allocation fails, allow OOM handling" not in page_alloc_probe:
        buddy_call = (_B8_BUDDY_CALL_OLD, _B8_BUDDY_CALL_NEW)
    steps = [
        ("mm/internal.h",
         "int find_suitable_fallback(struct free_area *area, unsigned int order,\n"
         "\t\t\tint migratetype, bool only_stealable, bool *can_steal);",
         "int find_suitable_fallback(struct free_area *area, unsigned int order,\n"
         "\t\t\tint migratetype, bool claimable);",
         T),
        ("mm/compaction.c", _B8_COMPACTION_DECL_OLD,
         _B8_COMPACTION_DECL_NEW, T),
        ("mm/compaction.c", _B8_COMPACTION_CALL_OLD,
         _B8_COMPACTION_CALL_NEW, T),
        ("mm/page_alloc.c", _B8_FIND_OLD, _B8_FIND_NEW, T),
        ("mm/page_alloc.c", _B8_RMQUEUE_OLD, _B8_RMQUEUE_NEW, T),
        ("mm/page_alloc.c", _B8_FALLBACK_OLD, _B8_FALLBACK_NEW, T),
        ("mm/page_alloc.c", _B8_BULK_DECL_OLD, _B8_BULK_DECL_NEW, T),
        ("mm/page_alloc.c", _B8_BULK_CALL_OLD, _B8_BULK_CALL_NEW, T),
        ("mm/page_alloc.c", buddy_call[0], buddy_call[1], T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


_B8_RCU_NOCB_KCONFIG_OLD = """\t  Say Y here if you need reduced OS jitter, despite added overhead.\n\t  Say N here if you are unsure.\n\nconfig TASKS_TRACE_RCU_READ_MB"""
_B8_RCU_NOCB_KCONFIG_NEW = """\t  Say Y here if you need reduced OS jitter, despite added overhead.\n\t  Say N here if you are unsure.\n\n# ABK stable_515_backport: RCU_NOCB_CPU_DEFAULT_ALL option from upstream.\nconfig RCU_NOCB_CPU_DEFAULT_ALL\n\tbool \"Offload RCU callback processing from all CPUs by default\"\n\tdepends on RCU_NOCB_CPU\n\tdefault n\n\thelp\n\t  Use this option to offload callback processing from all CPUs\n\t  by default, in the absence of the rcu_nocbs or nohz_full boot\n\t  parameter. This also avoids the need to use any boot parameters\n\t  to achieve the effect of offloading all CPUs on boot.\n\n\t  Say Y here if you want offload all CPUs by default on boot.\n\t  Say N here if you are unsure.\n\nconfig TASKS_TRACE_RCU_READ_MB"""

_B8_RCU_NOCB_DOC_NOHZ_OLD = """\t\t\tjust as if they had also been called out in the\n\t\t\trcu_nocbs= boot parameter."""
_B8_RCU_NOCB_DOC_NOHZ_NEW = """\t\t\tjust as if they had also been called out in the\n\t\t\trcu_nocbs= boot parameter.\n\n\t\t\tABK stable_515_backport: This argument takes precedence over\n\t\t\tCONFIG_RCU_NOCB_CPU_DEFAULT_ALL."""

_B8_RCU_NOCB_DOC_PARAM_OLD = """\t\t\twhich can be useful for HPC and real-time\n\t\t\tworkloads.  It can also improve energy efficiency\n\t\t\tfor asymmetric multiprocessors."""
_B8_RCU_NOCB_DOC_PARAM_NEW = """\t\t\twhich can be useful for HPC and real-time\n\t\t\tworkloads.  It can also improve energy efficiency\n\t\t\tfor asymmetric multiprocessors.\n\n\t\t\tABK stable_515_backport: This argument takes precedence over\n\t\t\tCONFIG_RCU_NOCB_CPU_DEFAULT_ALL."""

_B8_RCU_NOCB_INIT_OLD = """\tint cpu;\n\tbool need_rcu_nocb_mask = false;\n\tstruct rcu_data *rdp;\n\n#if defined(CONFIG_NO_HZ_FULL)"""
_B8_RCU_NOCB_INIT_NEW = """\tint cpu;\n\tbool need_rcu_nocb_mask = false;\n\t/* ABK stable_515_backport: default-all tracks whether no boot mask was supplied. */\n\tbool offload_all = false;\n\tstruct rcu_data *rdp;\n\n#if defined(CONFIG_RCU_NOCB_CPU_DEFAULT_ALL)\n\tif (!cpumask_available(rcu_nocb_mask)) {\n\t\tneed_rcu_nocb_mask = true;\n\t\toffload_all = true;\n\t}\n#endif /* #if defined(CONFIG_RCU_NOCB_CPU_DEFAULT_ALL) */\n\n#if defined(CONFIG_NO_HZ_FULL)"""
_B8_RCU_NOCB_NOHZ_OLD = """#if defined(CONFIG_NO_HZ_FULL)\n\tif (tick_nohz_full_running && cpumask_weight(tick_nohz_full_mask))\n\t\tneed_rcu_nocb_mask = true;\n#endif /* #if defined(CONFIG_NO_HZ_FULL) */"""
_B8_RCU_NOCB_NOHZ_NEW = """#if defined(CONFIG_NO_HZ_FULL)\n\tif (tick_nohz_full_running && cpumask_weight(tick_nohz_full_mask)) {\n\t\tneed_rcu_nocb_mask = true;\n\t\toffload_all = false; /* NO_HZ_FULL has its own mask. */\n\t}\n#endif /* #if defined(CONFIG_NO_HZ_FULL) */"""
_B8_RCU_NOCB_SETALL_OLD = """#if defined(CONFIG_NO_HZ_FULL)\n\tif (tick_nohz_full_running)\n\t\tcpumask_or(rcu_nocb_mask, rcu_nocb_mask, tick_nohz_full_mask);\n#endif /* #if defined(CONFIG_NO_HZ_FULL) */\n\n\tif (register_shrinker(&lazy_rcu_shrinker))"""
_B8_RCU_NOCB_SETALL_NEW = """#if defined(CONFIG_NO_HZ_FULL)\n\tif (tick_nohz_full_running)\n\t\tcpumask_or(rcu_nocb_mask, rcu_nocb_mask, tick_nohz_full_mask);\n#endif /* #if defined(CONFIG_NO_HZ_FULL) */\n\n\t/* ABK stable_515_backport: materialize the default all-CPU mask. */\n\tif (offload_all)\n\t\tcpumask_setall(rcu_nocb_mask);\n\n\tif (register_shrinker(&lazy_rcu_shrinker))"""


def _rcu_nocb_cpu_default_all_apply(ctx):
    kconfig = ctx.read("kernel/rcu/Kconfig")
    nocb = ctx.read("kernel/rcu/tree_nocb.h")
    params = ctx.read("Documentation/admin-guide/kernel-parameters.txt")
    if ("config RCU_NOCB_CPU_DEFAULT_ALL" in kconfig
            and "bool offload_all = false" in nocb
            and "CONFIG_RCU_NOCB_CPU_DEFAULT_ALL" in params):
        return "already_present", "RCU_NOCB_CPU_DEFAULT_ALL is already present"
    steps = [
        ("kernel/rcu/Kconfig", _B8_RCU_NOCB_KCONFIG_OLD,
         _B8_RCU_NOCB_KCONFIG_NEW, T),
        ("Documentation/admin-guide/kernel-parameters.txt",
        _B8_RCU_NOCB_DOC_NOHZ_OLD, _B8_RCU_NOCB_DOC_NOHZ_NEW, T),
        ("Documentation/admin-guide/kernel-parameters.txt",
         _B8_RCU_NOCB_DOC_PARAM_OLD, _B8_RCU_NOCB_DOC_PARAM_NEW, T),
        ("kernel/rcu/tree_nocb.h", _B8_RCU_NOCB_INIT_OLD,
         _B8_RCU_NOCB_INIT_NEW, T),
        ("kernel/rcu/tree_nocb.h", _B8_RCU_NOCB_NOHZ_OLD,
         _B8_RCU_NOCB_NOHZ_NEW, T),
        ("kernel/rcu/tree_nocb.h", _B8_RCU_NOCB_SETALL_OLD,
         _B8_RCU_NOCB_SETALL_NEW, T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


PATCH_GROUPS = PATCH_GROUPS + [
    PatchGroup(
        "pagealloc_fallback_reuse",
        "reuse rmqueue fallback modes across rmqueue_bulk() and avoid repeated claimability scans (android15-6.6 / 6.12)",
        [
            "e8400a074123 (android15-6.6 rmqueue_bulk fallback mode reuse)",
            "0b8d16680d9f (android15-6.6 find_suitable_fallback cleanup)",
        ],
        ["mm/page_alloc.c", "mm/compaction.c", "mm/internal.h"],
        _pagealloc_fallback_reuse_apply,
    ),
    PatchGroup(
        "rcu_nocb_cpu_default_all",
        "add an opt-in default mask that offloads RCU callbacks from every CPU (upstream rcu/nocb)",
        ["b37a667c6242 (rcu/nocb: add an option to offload all CPUs on boot)"],
        ["Documentation/admin-guide/kernel-parameters.txt", "kernel/rcu/Kconfig",
         "kernel/rcu/tree_nocb.h"],
        _rcu_nocb_cpu_default_all_apply,
    ),
]



# ---------------------------------------------------------------------------
# Batch 9-1: dynamic readahead (OPLUS mi_dynamic_readahead, GKI built-in)
#
# Source: OPLUS "dynamic_readahead" kernel module (Copyright 2020-2022 Oplus),
# shipped by Xiaomi as xiaomi-modules/mi_dynamic_readahead in
# MiCode/Xiaomi_Kernel_OpenSource.  The module only registered two android
# vendor-hook callbacks; android13-5.15 already carries both hook call
# sites (mm/readahead.c ondemand_readahead(), mm/filemap.c mmap read-around),
# so the port is a self-contained core_initcall registration in
# mm/readahead.c behind CONFIG_ABK_DYNAMIC_READAHEAD.  The xring
# (soc/xring/qos_inherit.h) key-task check is replaced by the original
# OPLUS cgroup test (cpuset "background"), which the Xiaomi copy still
# carries as a comment.
# ---------------------------------------------------------------------------

_DRA_RA_TAIL = (
    "/*\n"
    " * Initialise a struct file's readahead state.  Assumes that the caller has\n"
    " * memset *ra to zero.\n"
    " */"
)

_DRA_RA_OLD = (
    '#include "internal.h"\n'
    "\n"
    + _DRA_RA_TAIL
)

_DRA_RA_BLOCK = "\n".join([
    "#ifdef CONFIG_ABK_DYNAMIC_READAHEAD",
    "#include <linux/moduleparam.h>",
    "#include <linux/jiffies.h>",
    "#include <linux/cgroup.h>",
    "#include <linux/sched/rt.h>",
    "#include <linux/vmstat.h>",
    "",
    "/*",
    " * ABK stable_515_backport: dynamic readahead (Batch 9-1).",
    " *",
    ' * In-tree port of the OPLUS/Xiaomi "mi_dynamic_readahead" kernel module',
    " * (Copyright 2020-2022 Oplus, as shipped in Xiaomi_Kernel_OpenSource",
    " * xiaomi-modules/mi_dynamic_readahead): while memory is low, background",
    " * tasks get their sequential readahead window halved and their mmap",
    " * read-around window shrunk, cutting page-cache prefetch pressure during",
    " * reclaim.  The original module registered these two android vendor-hook",
    " * callbacks at module_init(); this built-in form does the same at",
    " * core_initcall.  Runtime disable on the command line:",
    " * readahead.dynamic_readahead=0 (or the same name under",
    " * /sys/module/readahead/parameters/).",
    " */",
    "static unsigned long abk_dra_high_wm;",
    "static unsigned long abk_dra_wm_stamp;",
    "static bool abk_dra_enable = true;",
    "",
    "/* Refresh the watermark sum at most once per second so the low-memory",
    " * gate tracks min_free_kbytes/hotplug changes instead of freezing at the",
    " * boot-time value. */",
    "static void abk_dra_refresh_high_wm(void)",
    "{",
    "\tstruct zone *zone;",
    "",
    "\tif (abk_dra_wm_stamp &&",
    "\t    time_is_after_jiffies(abk_dra_wm_stamp + HZ))",
    "\t\treturn;",
    "",
    "\tabk_dra_high_wm = 0;",
    "\tfor_each_zone(zone)",
    "\t\tabk_dra_high_wm += high_wmark_pages(zone);",
    "\tabk_dra_wm_stamp = jiffies;",
    "}",
    "",
    "static bool abk_dra_is_lowmem(void)",
    "{",
    "\tabk_dra_refresh_high_wm();",
    "\treturn global_zone_page_state(NR_FREE_PAGES) < abk_dra_high_wm;",
    "}",
    "",
    "static bool abk_dra_is_background_task(void)",
    "{",
    "\tstruct cgroup_subsys_state *css;",
    "\tconst char *kn_name;",
    "\tbool background = false;",
    "",
    "\tif (rt_task(current))",
    "\t\treturn false;",
    "",
    "\trcu_read_lock();",
    "\tcss = task_css(current, cpuset_cgrp_id);",
    "\tkn_name = css->cgroup->kn ? css->cgroup->kn->name : NULL;",
    '\tif (kn_name && !strncmp(kn_name, "background", strlen("background")))',
    "\t\tbackground = true;",
    "\trcu_read_unlock();",
    "",
    "\treturn background;",
    "}",
    "",
    "static void abk_dra_adjust_readahead(void *data,",
    "\t\tstruct readahead_control *ractl, unsigned long *max_pages)",
    "{",
    "\tif (abk_dra_enable && abk_dra_is_lowmem() &&",
    "\t    abk_dra_is_background_task())",
    "\t\t*max_pages = min_t(unsigned long, *max_pages,",
    "\t\t\t\t   ractl->ra->ra_pages / 2);",
    "}",
    "",
    "static void abk_dra_adjust_readaround(void *data, unsigned int ra_pages,",
    "\t\tpgoff_t pgoff, pgoff_t *start, unsigned int *size,",
    "\t\tunsigned int *async_size)",
    "{",
    "\tunsigned int dy_ra_pages;",
    "",
    "\tif (!abk_dra_enable || !abk_dra_is_lowmem() ||",
    "\t    !abk_dra_is_background_task())",
    "\t\treturn;",
    "",
    "\tdy_ra_pages = ra_pages / 2;",
    "\t*start = max_t(long, 0, pgoff - dy_ra_pages / 2);",
    "\t*size = dy_ra_pages;",
    "\t*async_size = dy_ra_pages / 4;",
    "}",
    "",
    "static int __init abk_dra_init(void)",
    "{",
    "\tint ret;",
    "",
    "\tabk_dra_refresh_high_wm();",
    "",
    "\tret = register_trace_android_vh_ra_tuning_max_page(",
    "\t\t\tabk_dra_adjust_readahead, NULL);",
    "\tif (ret)",
    "\t\treturn ret;",
    "",
    "\tret = register_trace_android_vh_tune_mmap_readaround(",
    "\t\t\tabk_dra_adjust_readaround, NULL);",
    "\tif (ret)",
    "\t\tunregister_trace_android_vh_ra_tuning_max_page(",
    "\t\t\t\tabk_dra_adjust_readahead, NULL);",
    "",
    "\treturn ret;",
    "}",
    "core_initcall(abk_dra_init);",
    "module_param_named(dynamic_readahead, abk_dra_enable, bool, 0644);",
    "#endif /* CONFIG_ABK_DYNAMIC_READAHEAD */",
])

_DRA_RA_NEW = (
    '#include "internal.h"\n'
    "\n"
    + _DRA_RA_BLOCK
    + "\n\n"
    + _DRA_RA_TAIL
)

_DRA_KC_OLD = (
    'menu "Memory Management options"\n'
    "\n"
    "config SELECT_MEMORY_MODEL"
)

_DRA_KC_NEW = (
    'menu "Memory Management options"\n'
    "\n"
    "# ABK stable_515_backport: Batch 9-1 dynamic readahead (OPLUS/Xiaomi)\n"
    "config ABK_DYNAMIC_READAHEAD\n"
    '\tbool "ABK dynamic readahead (low-memory background cap)"\n'
    "\thelp\n"
    "\t  In-tree port of the OPLUS/Xiaomi dynamic_readahead module (see\n"
    "\t  plan.md Batch 9-1): while memory is low, background cpuset tasks\n"
    "\t  get their readahead window halved and their mmap read-around\n"
    "\t  shrunk.  Runtime toggle on the command line:\n"
    "\t  readahead.dynamic_readahead=0 disables.\n"
    "\n"
    "config SELECT_MEMORY_MODEL"
)


def _dynamic_readahead_apply(ctx):
    steps = [
        ("mm/readahead.c", _DRA_RA_OLD, _DRA_RA_NEW, T),
        ("mm/Kconfig", _DRA_KC_OLD, _DRA_KC_NEW, T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


PATCH_GROUPS = PATCH_GROUPS + [
    PatchGroup(
        "dynamic_readahead_lowmem",
        "dynamic readahead: register low-memory background readahead/mmap read-around caps via the android vendor hooks (OPLUS mi_dynamic_readahead, GKI built-in)",
        [
            "OPLUS mi_dynamic_readahead kernel module",
            "(Xiaomi xiaomi-modules/mi_dynamic_readahead, dijun-v-oss)",
        ],
        ["mm/readahead.c", "mm/Kconfig"],
        _dynamic_readahead_apply,
    ),
]

# ============================================================================
# Batch 10-1: zram async recompress (plan A, kcompressd-style soft
# decoupling).  Steps live in scripts/batch10_core_zram_async.py; the sysfs
# scan stays synchronous while the recompress work drains on a per-device
# "zram_recompd" kthread_worker (QPACE generic-queue skeleton, no QTI).
# ============================================================================
import batch10_core_zram_async as _b10_zram  # noqa: E402


def _zram_async_recompress_apply(ctx):
    # Trap 5 again: zram_recompress_max_pages edits the body of
    # recompress_async_store(), which this group appends, so the group has to
    # stop on its own payload rather than on per-step idempotency.  The probe
    # is the forward declaration this group writes next to the include it
    # anchors on, i.e. text no other group produces.
    try:
        _probe = ctx.read("drivers/block/zram/zram_drv.c")
    except FileNotFoundError:
        _probe = ""
    if _b24_zmp.RECOMPRESS_ASYNC_STORE in _probe:
        return "already_present", ("the async recompress engine is already in zram_drv.c")
    status, _results, detail = apply_steps(ctx, _b10_zram.build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


PATCH_GROUPS = PATCH_GROUPS + [
    PatchGroup(
        "zram_async_recompress",
        "zram async recompress: recompress_store gains async=1; candidate pages drain on a per-device kthread_worker (QPACE soft decoupling, plan A)",
        [
            "popsicle-w-oss zram_drv QPACE async skeleton (generic queue part)",
            "Batch 10-1 plan A design (plan.md)",
        ],
        ["drivers/block/zram/zram_drv.c"],
        _zram_async_recompress_apply,
    ),
]

# ============================================================================
# Batch 10-3: cached freeze reclaim (generic name for memory-freeze).
# Steps live in scripts/batch10_core_cached_freeze_reclaim.py: per-reclaim
# accounting in the memory.reclaim handler + freezer tracepoints, all on
# pristine anchors (mm/memcontrol.c, kernel/cgroup/cgroup.c); the userspace
# daemon half is tools/cached_freeze_reclaim.sh.
# ============================================================================
import batch10_core_cached_freeze_reclaim as _b10_cfr  # noqa: E402


def _cached_freeze_reclaim_apply(ctx):
    status, _results, detail = apply_steps(ctx, _b10_cfr.build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


PATCH_GROUPS = PATCH_GROUPS + [
    PatchGroup(
        "cached_freeze_reclaim",
        "cached freeze reclaim: per-reclaim cfr_reclaim_* counters in memory.stat + abk_cfr_freeze/abk_cfr_thaw freezer tracepoints (Batch 10-3, generic memory-freeze name)",
        [
            "AOSP cached apps freezer (cgroup v2 freezer + memory.reclaim)",
            "Batch 10-3 semantics (plan.md)",
        ],
        ["mm/vmscan.c", "mm/memcontrol.c"],
        _cached_freeze_reclaim_apply,
    ),
]

# ============================================================================
# Batch 10-4: make the zram recompression and the memcg side actually reachable
# on the target device.  Steps live in scripts/batch10_core_zram_secondary.py
# and scripts/batch10_core_memcg_v1.py.
#
#  * zram_secondary_comp  -- ZRAM_MULTI_COMP was enabled but every secondary
#    comp slot stayed NULL (recomp_algorithm_store() only accepts a write
#    before disksize, which Android has already done by then), so both the
#    synchronous and the asynchronous recompress paths returned without
#    touching a page.  The secondary compressor is now registered in
#    zram_add(), before any disksize write can race it.
#  * memcg_v1_reclaim     -- the device mounts the memory controller as
#    cgroup v1, where memory.reclaim and the reclaim counters do not exist
#    (they are v2-only in this module).  The shared handler is declared for
#    the legacy cftype table and the counters are added to memcg_stat_show().
# ============================================================================
import batch10_core_zram_secondary as _b10_zsec  # noqa: E402
import batch10_core_memcg_v1 as _b10_v1  # noqa: E402


def _zram_secondary_comp_apply(ctx):
    status, _results, detail = apply_steps(ctx, _b10_zsec.build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _memcg_v1_reclaim_apply(ctx):
    status, _results, detail = apply_steps(ctx, _b10_v1.build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


PATCH_GROUPS = PATCH_GROUPS + [
    PatchGroup(
        "zram_secondary_comp",
        "zram secondary compressor registered at device creation, so ZRAM_MULTI_COMP recompression is no longer a silent no-op (zram.abk_recomp_algo, read-only 0444, default zstd)",
        [
            "Batch 10-1/10-4 on-device finding: recomp_algorithm empty, recompress paths no-op",
            "Batch 10-4 semantics (plan.md)",
        ],
        ["drivers/block/zram/zram_drv.c"],
        _zram_secondary_comp_apply,
    ),
    PatchGroup(
        "memcg_v1_reclaim",
        "cgroup-v1 proactive reclaim: memory.reclaim cftype + cfr_reclaim_* counters in memcg_stat_show, for devices whose memory controller is mounted v1",
        [
            "Batch 10-1/10-4 on-device finding: /sys/fs/cgroup memory controller absent, only /dev/memcg",
            "Batch 10-4 semantics (plan.md)",
        ],
        ["mm/memcontrol.c"],
        _memcg_v1_reclaim_apply,
    ),
]

# ============================================================================
# Batch 11: the zram algorithm lock.  Steps live in
# scripts/batch11_core_zram_algo_lock.py.
#
# Batch 10-4 made the secondary compressor reachable and the runtime companion
# module forces the primary, but both live in userspace and both nodes close at
# `disksize`: the policy belonged to whoever won that one window.  The lock
# selects the primary at device creation from a read-only parameter and makes
# every later write to comp_algorithm/recomp_algorithm a reported no-op, which
# is what lets the algorithm policy and CONFIG_ZRAM_WRITEBACK coexist -- a
# writeback backing device is attached in the same pre-`disksize` window and is
# dropped by `reset`, so repairing a bad algorithm choice used to cost the
# writeback setup.
# ============================================================================
import batch11_core_zram_algo_lock as _b11_zlock  # noqa: E402


def _zram_algo_lock_apply(ctx):
    status, _results, detail = apply_steps(ctx, _b11_zlock.build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


PATCH_GROUPS = PATCH_GROUPS + [
    PatchGroup(
        "zram_algo_lock",
        "zram algorithm lock: primary chosen at device creation from the read-only zram.abk_comp_algo (default lz4kd), and later writes to comp_algorithm/recomp_algorithm are accepted and ignored (zram.abk_lock_algo, read-only 0444)",
        [
            "Batch 11 on-device finding: a root writer still switched comp_algorithm after boot (deflate), and the only repair path (swapoff+reset) drops any writeback backing device",
            "Batch 11 semantics (plan.md)",
        ],
        ["drivers/block/zram/zram_drv.c"],
        _zram_algo_lock_apply,
    ),
]

# ============================================================================
# Batch 13: the android_vh_customize_alloc_gfp hook and its ABK policy.
# Steps live in scripts/batch13_core_gfp_customize_vh.py.
#
# The hook (android15-6.6 commit 4466afd69452, absent from every android13-5.15
# baseline and from 6.1) hands a module the slowpath gfp by pointer right
# before __alloc_pages_slowpath(): a rewrite point for high-order allocation
# policy.  The upstream graft is verbatim upstream-shape (no ABK marker), and
# the 5.15 slowpath-entry block is byte-identical to 6.6's because the cpuset
# fast-path series landed on android13-5.15 long ago.  The policy group is the
# consumer: while memory is low, order >= abk_gfp_fastfail_order (default 9)
# slowpath attempts gain __GFP_NORETRY|__GFP_NOWARN, so a fragmented
# near-full phone fails those requests into their callers' fallback (4K
# fault, -ENOMEM) after one direct-reclaim try instead of stalling the
# faulting task in the reclaim/compaction loop.  Measured at .167 the
# default is not a no-op for THP: GFP_TRANSHUGE_LIGHT carries no
# __GFP_NORETRY, so the defrag=madvise madvised fault and khugepaged's
# defrag allocations are exactly the retryable class the gate catches (see
# batch13_core_gfp_customize_vh.py).  Both groups refuse to apply without
# proof the hook can fire and compile (mm.h declare + page_alloc.c call
# site + header include), so a blocked hook group never leaves a payload
# calling an undeclared register_trace_ (compile trap, AGENTS.md trap 4/5).
# The companion finding -- android_rvh_wake_up_new_task is already present
# on all four baselines and needs no graft -- is pinned in
# tests/stable_5_15_test.py.
# ============================================================================
import batch13_core_gfp_customize_vh as _b13_gfp  # noqa: E402


def _customize_alloc_gfp_vh_apply(ctx):
    try:
        pa = ctx.read("mm/page_alloc.c")
    except OSError as exc:
        return "blocked_by_shape", f"cannot read mm/page_alloc.c: {exc}"
    # The grafted call line needs the hook header in the same TU; every
    # probed baseline includes it, but an include drop upstream would turn
    # the graft into a compile error no text anchor notices (AGENTS.md
    # trap 5).  Refuse instead of grafting C that cannot compile.
    if "#include <trace/hooks/mm.h>" not in pa:
        return "blocked_by_shape", ("mm/page_alloc.c does not include "
                                    "<trace/hooks/mm.h>; the hook call "
                                    "grafted there could not compile")
    status, _results, detail = apply_steps(ctx, _b13_gfp.build_hook_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _gfp_pressure_fastfail_apply(ctx):
    try:
        mm_h = ctx.read("include/trace/hooks/mm.h")
        pa = ctx.read("mm/page_alloc.c")
    except OSError as exc:
        return "blocked_by_shape", f"cannot read hook files: {exc}"
    if "android_vh_customize_alloc_gfp" not in mm_h:
        return "blocked_by_shape", ("customize_alloc_gfp hook not grafted yet "
                                    "(the policy needs customize_alloc_gfp_vh)")
    # The declaration alone is not enough for the payload to do anything:
    # probe the call site (a declared-but-never-called hook is inert) and
    # the header include the callback's register_trace_ symbol needs.
    if "trace_android_vh_customize_alloc_gfp(&alloc_gfp, order);" not in pa:
        return "blocked_by_shape", ("customize_alloc_gfp hook is not called "
                                    "in mm/page_alloc.c; the policy would be "
                                    "registered but never fire")
    if "#include <trace/hooks/mm.h>" not in pa:
        return "blocked_by_shape", ("mm/page_alloc.c does not include "
                                    "<trace/hooks/mm.h>; the payload's "
                                    "register_trace_ could not compile")
    status, _results, detail = apply_steps(ctx, _b13_gfp.build_policy_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


PATCH_GROUPS = PATCH_GROUPS + [
    PatchGroup(
        "customize_alloc_gfp_vh",
        "android_vh_customize_alloc_gfp vendor hook grafted verbatim from android15-6.6 (4466afd69452): slowpath-entry gfp rewrite point (mm.h declare + page_alloc call + vendor_hooks export, no ABK marker)",
        [
            "4466afd694520 (android15-6.6, Bug 337192903)",
        ],
        ["include/trace/hooks/mm.h", "mm/page_alloc.c",
         "drivers/android/vendor_hooks.c"],
        _customize_alloc_gfp_vh_apply,
    ),
    PatchGroup(
        "gfp_pressure_fastfail",
        "high-order slowpath fast-fail under memory pressure: while available pages < page_alloc.abk_gfp_fastfail_pct% (default 50) of the summed high watermarks, slowpath attempts of order >= abk_gfp_fastfail_order (default 9: THP-class) gain __GFP_NORETRY|__GFP_NOWARN; abk_gfp_fastfail=0 switches off (ABK policy on the Batch 13 hook)",
        [
            "ABK Batch 13 policy (plan.md) on android_vh_customize_alloc_gfp",
        ],
        ["include/trace/hooks/mm.h", "mm/page_alloc.c"],
        _gfp_pressure_fastfail_apply,
    ),
]

# ============================================================================
# Batch 14: zram writeback correctness fixes.
# Steps live in scripts/batch14_core_zram_writeback.py.
#
# android13-5.15 froze its zram writeback code around 2022: the writeback
# correctness work that followed in mainline never reached the branch, and it
# never reached linux-5.15.y either (verified against the linux-5.15.y tree at
# SUBLEVEL 220, which differs from the 5.15.194 baseline only by the
# zero-sized-backing-device hunk that arrived through ACK).
#
# Three independent defects, all confined to drivers/block/zram/zram_drv.c:
#
#   zram_wb_teardown       a backing device attached before `disksize` is never
#                          released (74363ec674cb), pinning the backing block
#                          device and zram itself until the module is unloaded.
#                          The NULL-table guard that keeps the reset path safe
#                          ships in the same transaction.
#   zram_writeback_bounds  the writeback scan bound comes from zram->disksize
#                          before init_lock, so a reset that re-initialises a
#                          smaller disksize lets the loop index past the new
#                          table (894913e2d35c, Cc: stable), plus the missing
#                          cond_resched() in the sweep (424d0e5828ad).
#   zram_wb_limit_align    writeback_limit underflows on PAGE_SIZE > 4KiB and
#                          silently stops capping flash wear.
#
# Ordering is load-bearing: registered after zram_recompression (whose graft
# rewrites writeback_store()'s callee and the comps teardown inside
# zram_reset_device()) and after zram_algo_lock (which owns the neighbouring
# comp_algorithm stores).  zram_wb_teardown deliberately does NOT edit
# zram_reset_device(): that function belongs to the recompression group, and
# removing its early return here made the recompression group miss its own
# anchor on every second pass (it anchors on the *whole* pristine body of the
# function, guard included).  The leak is therefore closed in zram_remove() --
# same effect, no shared text.
# ============================================================================
import batch14_core_zram_writeback as _b14_zwb  # noqa: E402

PATCH_GROUPS = PATCH_GROUPS + _b14_zwb.build_groups(PatchGroup)

# ============================================================================
# Batch 15: ABK_ABI_PATCH_SUITE absorption -- core fs/pid allocation hot paths.
# Steps live in scripts/batch15_core_fs_pid_hotpaths.py.
#
# Batch 15 retires the suite-preference rule (docs/porting_policy.md, "Suite
# absorption"), so the suite's optimization inventory is re-registered here
# instead of being deferred to a second external module.  Three groups:
#
#   pid_alloc_hotpath_phase2  retries idr_preload(GFP_KERNEL) once after a
#                             GFP_ATOMIC -ENOMEM inside alloc_pid().  NOTE: the
#                             suite's version used `continue;` in
#                             `for (i = ns->level; i >= 0; i--)`, which runs the
#                             increment expression -- on the common single-level
#                             namespace it left the loop and returned a pid whose
#                             numbers[0].nr was never written.  The absorbed form
#                             retries the SAME level through a retry_preload:
#                             label instead.
#   fd_alloc_hotpath          the abk_expand_files_needed() precheck helper plus
#                             its two call sites.  The suite's capacity half is
#                             deliberately NOT ported: this child's
#                             fdtable_alloc_conventions already owns that text
#                             (upstream 5.15.191 slots_wanted shape), and the
#                             suite's helper name is one of this module's own
#                             suite-detection markers.
#   close_range_hotpath       walks open_fds under rcu_read_lock() in
#                             __range_close(), the 5.15 shape.  The suite's
#                             caller-locked 6.1 helpers are not ported because on
#                             5.15 pick_file() takes files->file_lock itself, so
#                             the suite's lockdep assertion would be knowingly
#                             false.
#
# Ordering is load-bearing and enforced by a shape probe in _fd_alloc_apply():
# these groups must run AFTER fdtable_alloc_conventions (and
# fdtable_replace_fd_errno), and they report blocked_by_shape rather than
# half-grafting a precheck onto a table allocator whose shape they do not
# recognise.  Both fs/file.c groups return skip_suite_processed when the tree
# already carries ABK_ABI_PATCH_SUITE markers, because the suite inserts the same
# helper names -- re-grafting would be a duplicate definition.
# ============================================================================
import batch15_core_fs_pid_hotpaths as _b15_fspid  # noqa: E402

PATCH_GROUPS = PATCH_GROUPS + _b15_fspid.build_groups(PatchGroup)

# ============================================================================
# Batch 15: ABK_ABI_PATCH_SUITE absorption -- core MM hot paths.
# Steps live in scripts/batch15_core_mm_hotpaths.py.
#
#   slab_alloc_free_hotpath      a shared bulk-free backend, a free-side
#                                validation split and a bulk-alloc prefetch in
#                                mm/slub.c.
#   hugepage_fault_alloc_fastpath a file-local helper split for anonymous THP
#                                fault-time allocation that keeps the THP
#                                fault-fallback tracking and the PMD fault
#                                routing intact (mm/huge_memory.c, mm/memory.c).
#
# Both are appended after every pre-existing core group, so their mm/*.c anchors
# see the output of the khugepaged / MADV_COLLAPSE / THP and page_alloc groups
# that already touch the same files.
# ============================================================================
import batch15_core_mm_hotpaths as _b15_mm  # noqa: E402

PATCH_GROUPS = PATCH_GROUPS + _b15_mm.build_groups(PatchGroup)

# ============================================================================
# Batch 17: zram writeback bio batching + compressed writeback.
# Steps live in scripts/batch17_core_zram_writeback.py.
#
# android13-5.15 froze its zram writeback code around 2022, so neither the
# v6.19 batching series (f405066a1f0d + e828cccb72ed, plus the two post-merge
# fixes bf62f69574b1 and 3e8d8eb8d7f5) nor the v7.0 compressed-writeback series
# (d38fab605c66 + 4c1d61389e8e, renamed by ba4c3698e696, fixed by 3bf1c285dc40)
# ever reached the branch.  Three groups:
#
#   zram_writeback_batching    several writeback bios in flight instead of one
#                              submit_bio_wait() per page.  Upstream drives the
#                              batch from its post-processing slot machinery,
#                              which 5.15 does not have, so the in-flight state
#                              is expressed with ZRAM_UNDER_WB + ZRAM_IDLE.
#                              The wb_ctl UAF fix (kfree_rcu + rcu_read_lock in
#                              the completion callback) and the blk_idx leak fix
#                              are written in from the first new line, so the
#                              buggy shapes never exist in this tree.  Carries
#                              the write half of compressed writeback, because
#                              a later group may not edit this group s
#                              replacement blocks (see the graft-boundary
#                              contract in batch10_core_zram_async.py).
#   zram_wb_batch_size         the sysfs surface of e828cccb72ed, with a
#                              bounded pool (upstream stores any non-zero u32
#                              and allocates per unit).
#   zram_compressed_writeback  store raw zspool objects and decompress on
#                              demand on the read path; probes for the flag
#                              field the first group adds and degrades rather
#                              than half-patching a tree that cannot compile.
#
# Ordering is load-bearing: these run AFTER batch14, whose
# zram_writeback_bounds anchors inside writeback_store() -- and batch14 was
# re-anchored in the same change (its _B_POSTLOCK used to reach the
# page = alloc_page() line this batch owns) so that no group edits another
# group s replacement block.  They also run after zram_recompression, whose
# comps[]/priority bits and zram_read_from_zspool() these groups build on.
# ============================================================================
import batch17_core_zram_writeback as _b17_zwb  # noqa: E402

PATCH_GROUPS = PATCH_GROUPS + _b17_zwb.build_groups(PatchGroup)

# ============================================================================
# Batch 24: bound a recompression pass (max_pages).
# Steps live in scripts/batch24_core_zram_max_pages.py.
#
#   zram_recompress_max_pages  mainline 34efe1c3b688 (v6.10) adds max_pages to
#                              recompress_store() and 2f529e73d720 (v7.1)
#                              rejects an unrecognised type=; neither ever
#                              reached android13-5.15, whose recompression
#                              surface this module itself generated from
#                              android15-6.6.  The companion drives a sweep
#                              every zram.recomp.interval_sec, so without a cap
#                              one pass is an unbounded amount of CPU.
#
# Registered last and deliberately the first group in this module that rewrites
# another group's replacement block: its target is the generated
# recompress_store()/recompress_async_store(), not pristine text.  That is only
# safe because zram_recompression and zram_async_recompress now probe for their
# own payload (see the trap-5 notes there) -- register anything else in this
# region in the same dependency order.
# ============================================================================
import batch24_core_zram_max_pages as _b24_zmp  # noqa: E402

PATCH_GROUPS = PATCH_GROUPS + _b24_zmp.build_groups(PatchGroup)

# ============================================================================
# Batch 30: concurrent faults no longer double-decrement mmap_miss.
# Steps live in scripts/batch30_core_mmap_miss_races.py.
#
#   readahead_mmap_miss_race  mainline e338d8353154 (v6.18) puts the
#                             do_async_mmap_readahead() mmap_miss decrement
#                             behind a page-lock test, so several threads
#                             faulting the same page cannot each decrement the
#                             per-file counter for that one page (which used to
#                             keep mmap read-around enabled under memory
#                             pressure).  No Cc: stable, so 5.15.y never got it.
#
# Independent of every other group: mm/filemap.c is otherwise untouched by this
# module, and the anchor is unique in the file.  Position carries no meaning
# here, but keep this region append-only so the registration order in reports
# stays chronological.
# ============================================================================
import batch30_core_mmap_miss_races as _b30_mmr  # noqa: E402

PATCH_GROUPS = PATCH_GROUPS + _b30_mmr.build_groups(PatchGroup)

# ============================================================================
# Batch 31: arm64 pte_mkwrite() stops dirtying a clean PTE.
#
# Mainline 143937ca51cc (v6.18, Huang Ying) makes pte_mkwrite_novma() clear
# PTE_RDONLY only when the PTE is already software-dirty, so a page can be
# mapped writable *and* clean.  linux-5.15.y took the same patch as
# 8a2375b0e9b8 and it is in v5.15.196 onwards; android13-5.15-lts (SUBLEVEL
# 216) carries it, the three release baselines (167/178/194) do not.
#
# Why it matters on this baseline: arm64's protection_map defines PAGE_SHARED
# as "shared+writable pages are clean by default, hence PTE_RDONLY|PTE_WRITE",
# so clearing PTE_RDONLY is precisely what marks a page hardware-dirty
# (pte_hw_dirty() == pte_write() && !(pte_val(pte) & PTE_RDONLY)).  Every
# caller that makes a *clean* pte writable therefore reported the page dirty
# with nobody having written it, and try_to_unmap() turns that into
# set_page_dirty() at reclaim -- an unwritten page gets written back.  The live
# 5.15 call sites are the ones that restore a writable mapping after a
# transient unmap (remove_migration_pte(), do_numa_page(), userfaultfd); the
# motivating do_swap_page() case in the commit message is mainline-only, since
# 5.15's do_swap_page() pairs pte_mkwrite with pte_mkdirty.
#
# The target form is the 5.15.y form, not mainline's: 5.15 has pte_mkwrite(),
# not pte_mkwrite_novma() (that name arrives with the v6.6 rename 2f0584f3f4bd,
# the first step of the vma-aware pte_mkwrite() series), so a verbatim mainline
# hunk anchors nowhere on any of the four baselines.
#
# No ABK marker comment, deliberately: this is an upstream-shape rewrite, so the
# target form doubles as the idempotency probe -- on 216 the file is left
# byte-identical and the group reports already_present (PRE_APPLIED there).  A
# marker line would break that probe and turn 216 into blocked_by_shape.
# ============================================================================

_ARM64_PTE_MKWRITE_OLD = (
    "static inline pte_t pte_mkwrite(pte_t pte)\n"
    "{\n"
    "\tpte = set_pte_bit(pte, __pgprot(PTE_WRITE));\n"
    "\tpte = clear_pte_bit(pte, __pgprot(PTE_RDONLY));\n"
    "\treturn pte;\n"
    "}"
)

_ARM64_PTE_MKWRITE_NEW = (
    "static inline pte_t pte_mkwrite(pte_t pte)\n"
    "{\n"
    "\tpte = set_pte_bit(pte, __pgprot(PTE_WRITE));\n"
    "\tif (pte_sw_dirty(pte))\n"
    "\t\tpte = clear_pte_bit(pte, __pgprot(PTE_RDONLY));\n"
    "\treturn pte;\n"
    "}"
)


def _arm64_pte_mkwrite_clean_apply(ctx):
    """5.15.196's pte_mkwrite(): only clear PTE_RDONLY for a sw-dirty PTE."""
    status, _results, detail = apply_steps(
        ctx, [("arch/arm64/include/asm/pgtable.h",
               _ARM64_PTE_MKWRITE_OLD, _ARM64_PTE_MKWRITE_NEW, True)]
    )
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


PATCH_GROUPS = PATCH_GROUPS + [
    PatchGroup(
        "arm64_pte_mkwrite_clean",
        "arm64 pte_mkwrite() stops dirtying a clean PTE, so a writable mapping "
        "can report clean and an unwritten page is not written back at reclaim "
        "(5.15.196; mainline 143937ca51cc, v6.18)",
        ["8a2375b0e9b8 (5.15.196)", "143937ca51cc (v6.18)"],
        ["arch/arm64/include/asm/pgtable.h"],
        _arm64_pte_mkwrite_clean_apply,
    ),
]

# ============================================================================
# Batch 32: a written-back slot keeps its metadata, and ->huge_pages is
# decremented once.  Steps live in
# scripts/batch32_core_zram_wb_slot_preserve.py.
#
#   zram_wb_slot_preserve  mainline b0377ee80429 (mm-hotfixes-stable 2026-03,
#                          Fixes: d38fab605c667) / ACK android16-6.12
#                          37b72d525502.  It rewrites the
#                          zram_writeback_complete() that zram_writeback_batching
#                          generates and the huge block of zram_free_page()
#                          -- the second is a no-op on the *text*, but the
#                          accounting it guards is the other half of the fix.
#
# Registered last, after the two groups whose replacement blocks it edits
# (zram_writeback_batching) or which own the surrounding shape
# (zram_recompression).  Both are safe for the reason trap 5 describes: each
# probes its own payload, so a second pass reports already_present instead of
# re-appending.  zram_writeback_batching learned that probe in this batch; keep
# any further group that edits its text in the same dependency order.  (The
# mm/ and arch/arm64 groups above -- Batch 30, Batch 31 -- are independent of
# this one: different files, pristine anchors, so this dependency is only about
# the zram groups.)
# ============================================================================
import batch32_core_zram_wb_slot_preserve as _b32_zwbsp  # noqa: E402

PATCH_GROUPS = PATCH_GROUPS + _b32_zwbsp.build_groups(PatchGroup)

# ============================================================================
# Batch 33: a dead zspage's pages go back to the buddy allocator outside
# class->lock.
# Steps live in scripts/batch33_core_zsmalloc_free.py.
#
#   zsmalloc_free_zspage_out_of_lock
#                              mainline 7ef28e8b8142, patch 3 of the v6 series
#                              "mm/zsmalloc: reduce lock contention in
#                              zs_free()".  Patches 1-2 of that series delete a
#                              pool-level rwlock read side, and android13-5.15
#                              has no pool->lock at all (see the plan.md
#                              exclusion record), so only patch 3 applies --
#                              and it is the part this module's own
#                              zsmalloc_chain_size graft made longer.  No other
#                              group writes into zs_free(), __free_zspage() or
#                              free_zspage(), so there is nothing to order
#                              against.
# ============================================================================
import batch33_core_zsmalloc_free as _b33_zsf  # noqa: E402

PATCH_GROUPS = PATCH_GROUPS + _b33_zsf.build_groups(PatchGroup)

import batch35_core_pagecache_pt as _b35_pcpt  # noqa: E402

PATCH_GROUPS = PATCH_GROUPS + _b35_pcpt.build_groups(PatchGroup)
# ============================================================================
# Batch 34: the non-return per-CPU atomics become load LSE atomics.
# Steps live in scripts/batch34_core_arm64_lse_percpu.py.
#
#   arm64_lse_percpu_load_atomics
#                              mainline 535fdfc5a228 (v6.18, arm64-fixes).
#                              The LSE branch of __PERCPU_OP_CASE() grows a
#                              [tmp] destination and the three PERCPU_OP()
#                              instantiations flip from stadd/stclr/stset to
#                              ldadd/ldclr/ldset, so the instructions execute
#                              "near" (L1) instead of "far".  No other group
#                              writes arch/arm64/include/asm/percpu.h, so
#                              there is nothing to order against.  The
#                              upstream-measured BPF-fentry regression and why
#                              it does not apply to a 5.15 arm64 build, plus
#                              the no-speedup-claim rule, are in the child's
#                              module docstring and CHANGELOG.md (Batch 34).
# ============================================================================
import batch34_core_arm64_lse_percpu as _b34_alpa  # noqa: E402

PATCH_GROUPS = PATCH_GROUPS + _b34_alpa.build_groups(PatchGroup)

# ============================================================================
import batch36_core_memcg_stats_slim as _b36_mss  # noqa: E402

PATCH_GROUPS = PATCH_GROUPS + _b36_mss.build_groups(PatchGroup)

# Batch 35: the FUSE write path stops prefaulting its source buffer on every
# retry.  Steps live in scripts/batch35_core_fuse_erofs.py.
#
#   fuse_prefault_out_of_write_path
#                              mainline faa794dd2e17 (v6.16).  One file, and
#                              the module's first group in fs/fuse/ -- the
#                              file carries no other group's text, so there is
#                              nothing to order against.
#
# The erofs half of this batch does not land: fb176750266a + 6422cde1b0d5
# stand on the 5.15 -> 6.12 erofs evolution (erofs_buf/erofs_bread, the
# parallel erofs_fileio_aops, fs/erofs/fileio.c), none of which exists on any
# tracked baseline -- see the group module's docstring and the plan.md
# exclusion record.  770c8d55c428 (lib/iov_iter) is inapplicable: 5.15 has no
# page_folio()/folio_test_slab() in that file.  FUSE passthrough stays
# unported by decision (android13-5.15 ships its own on _IOW(229,126)).
#
# Renumbered from 34 when this branch merged main: the arm64 LSE group landed
# there first with the same number and the same version.  Its file keeps the
# batch34_ prefix because it was registered under that number on main; this
# one is batch35_.
# ============================================================================
import batch35_core_fuse_erofs as _b35_fuse  # noqa: E402

PATCH_GROUPS = PATCH_GROUPS + _b35_fuse.build_groups(PatchGroup)
# Batch 36: the memcg per-cpu stats objects shrink to the accounted items.
# Steps live in scripts/batch36_core_memcg_stats_slim.py.
#
#   memcg_stats_percpu_slim   mainline 70a64b7919cb + ff48c71c26aa (v6.10,
#                             Shakeel Butt) -- index the memcg stats arrays
#                             through item -> slot tables so the per-memcg,
#                             per-cpu objects only carry items memcg actually
#                             accounts.  Both commits land as one group: the
#                             dynamic-allocation half alone buys nothing.
#
# KMI reshape, deliberately not the upstream shape: the ABI XML
# (android/abi_gki_aarch64.xml) tracks struct mem_cgroup and struct
# mem_cgroup_per_node with full layouts, and 5.15 embeds the memcg_vmstats /
# lruvec_stats aggregates in them, so upstream's embedded-array shrink cannot
# land (see plan.md and the batch docstring for the measured verdict).  What
# lands instead keeps the header byte-identical and compacts only the two
# per-cpu heap objects behind the unchanged __percpu pointer fields, via
# private structs and __alloc_percpu_gfp().  The aggregates stay raw-indexed,
# so the rstat flush maps compact slots back to items.  The item tables are
# re-derived for 5.15 (memory_stats[]/memcg1_stats[] readers vs the full
# count_memcg_events* writer set) -- a missing item reads as zero and no text
# audit can see it, hence the implementation_audit pins.
#
# The header edit moves lruvec_page_state_local() out of line (its inline body
# would index the compacted object with raw offsets); static inline, no
# struct member moves, no export added.  No other group writes into the stats
# accessors, the rstat flush or this header, so there is nothing to order
# against.
# =====================================================================

# ============================================================================
# Batch 37: the memory-reclaim path -- proactive reclaim's batch fidelity, its
# swappiness= argument, the suspend abort, and the lru_add drain.
# Steps live in scripts/batch37_core_reclaim_paths.py.
#
#   proactive_reclaim_batch_fidelity  mainline 0388536ac291 (v6.6)
#   proactive_reclaim_decaying_batches
#                                     mainline 287d5fedb377 (v6.9,
#                                     Fixes: 0388536ac291)
#   reclaim_swappiness_defines        mainline 410abb20acae (v6.11 series p.2)
#   proactive_reclaim_swappiness_arg  mainline 68cd9050d871 (v6.11 series p.3)
#   proactive_reclaim_suspend_abort   mainline dc37771a43d4 (v7.2,
#                                     Fixes: 287d5fedb377)
#   lru_add_drain_dead_folios         mainline 9669b87065a6 (v7.2)
#
# Registered last, after memcg_memory_reclaim and cached_freeze_reclaim whose
# generated text they rewrite, and in dependency order among themselves: the
# first three rewrite the same memory_reclaim() call site in turn, and
# reclaim_swappiness_defines must precede the group whose parser validates
# against the constants it adds.  Each superseded group probes for content
# that outlives its successor, which is what keeps the second pass a no-op --
# docs/group_recipe.md trap 5, the Batch 21/24 remedy.  Keep any further group
# that edits this chain in the same order.
#
# Renumbered from 34 when this branch merged main: main had already released
# its own Batch 34 (arm64_lse_percpu_load_atomics), 35 and 36, so the
# reclaim-path chain takes the next free number and the next version.
# ============================================================================
import batch37_core_reclaim_paths as _b37_rp  # noqa: E402

PATCH_GROUPS = PATCH_GROUPS + _b37_rp.build_groups(PatchGroup)

if __name__ == "__main__":
    main()
