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
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


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
         "static inline void __maybe_unused zram_set_priority(struct zram *zram, u32 index,\n"
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
        # -- zram_drv.c: read path selects comp by stored priority --
        ("drivers/block/zram/zram_drv.c",
         "\tzstrm = zcomp_stream_get(zram->comp);\n"
         "\tsrc = kmap_atomic(page);",
         "\tzstrm = zcomp_stream_get(zram->comps[zram_get_priority(zram, index)]);\n"
         "\tsrc = kmap_atomic(page);",
         T),
        ("drivers/block/zram/zram_drv.c",
         "\tzcomp_stream_put(zram->comp);",
         "\tzcomp_stream_put(zram->comps[zram_get_priority(zram, index)]);",
         T),
        # -- zram_drv.c: __zram_bvec_write uses primary comp --
        ("drivers/block/zram/zram_drv.c",
         "\tzstrm = zcomp_stream_get(zram->comp);",
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
         "\tstruct zcomp *comp;\n"
         "\tu64 disksize;\n"
         "\tu32 prio;\n\n"
         "\tdown_write(&zram->init_lock);\n\n"
         "\tzram->limit_pages = 0;\n\n"
         "\tif (!init_done(zram)) {\n"
         "\t\tup_write(&zram->init_lock);\n"
         "\t\treturn;\n"
         "\t}\n\n"
         "\tzram->num_active_comps = 0;\n"
         "\tdisksize = zram->disksize;\n"
         "\tzram->disksize = 0;\n\n"
         "\tset_capacity_and_notify(zram->disk, 0);\n"
         "\tpart_stat_set_all(zram->disk->part0, 0);\n\n"
         "\tup_write(&zram->init_lock);\n"
         "\t/* I/O operation under all of CPU are done so let's free */\n"
         "\tzram_meta_free(zram, disksize);\n"
         "\tmemset(&zram->stats, 0, sizeof(zram->stats));\n"
         "\tfor (prio = 0; prio < ZRAM_MAX_COMPS; prio++) {\n"
         "\t\tcomp = zram->comps[prio];\n"
         "\t\tif (comp)\n"
         "\t\t\tzcomp_destroy(comp);\n"
         "\t\tzram->comps[prio] = NULL;\n"
         "\t}\n"
         "\treset_bdev(zram);\n"
         "}",
         T),
        # -- zram_drv.c: zram_read_from_zspool + recompression machinery --
        ("drivers/block/zram/zram_drv.c",
         "static void zram_bio_discard(struct zram *zram, u32 index,",
         "/*\n"
         " * Reads (decompresses if needed) a page from zspool (zsmalloc).\n"
         " * Corresponding ZRAM slot should be locked.\n"
         " */\n"
         "static int __maybe_unused zram_read_from_zspool(struct zram *zram, struct page *page,\n"
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
         "\tif (size != PAGE_SIZE) {\n"
         "\t\tprio = zram_get_priority(zram, index);\n"
         "\t\tzstrm = zcomp_stream_get(zram->comps[prio]);\n"
         "\t}\n\n"
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
         "\tcomp = zcomp_create(zram->comp_algs[ZRAM_PRIMARY_COMP]);\n"
         "\tif (IS_ERR(comp)) {\n"
         "\t\tpr_err(\"Cannot initialise %s compressing backend\\n\",\n"
         "\t\t\t\tzram->comp_algs[ZRAM_PRIMARY_COMP]);\n"
         "\t\terr = PTR_ERR(comp);\n"
         "\t\tgoto out_free_meta;\n"
         "\t}\n\n"
         "\tzram->comps[ZRAM_PRIMARY_COMP] = comp;\n"
         "\tzram->disksize = disksize;",
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


def _config_enablement_apply(ctx):
    align = os.environ.get("ABK_515_DEFCONFIG_ALIGN", "").strip() == "1"
    configs = list(_MODULE_CONFIGS) + (list(_ALIGN_CONFIGS) if align else [])
    status, detail = ctx.enable_configs(configs)
    tier = "6.6 GKI align" if align else "module-owned symbols only"
    return status, f"{detail} [{tier}]"


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
        "per-memcg proactive reclaim via memory.reclaim, with reclaim options replacing may_swap (android14-6.1 / 6.1.y)",
        ["memory.reclaim series (android14-6.1; mainline proactive reclaim)"],
        ["include/linux/swap.h", "mm/vmscan.c", "mm/memcontrol.c"],
        _memcg_reclaim_apply,
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
# waste instead, which is what actually shrinks small size classes.  Only the
# sizing is ported: the 6.3+ fullness rename, zs_page_migrate rework and
# zs_size_stat growth stay out, so NR_ZS_FULLNESS and the exported API hold.
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


def _zsmalloc_chain_size_apply(ctx):
    steps = [
        ("mm/Kconfig", _ZS_KCONFIG_STAT, _ZS_KCONFIG_STAT + _ZS_KCONFIG_CHAIN, T),
        ("mm/zsmalloc.c", _ZS_MACRO_OLD, _ZS_MACRO_NEW, T),
        ("mm/zsmalloc.c", _ZS_SIZING_OLD, _ZS_SIZING_NEW, T),
        ("mm/zsmalloc.c",
         "\t\tpages_per_zspage = get_pages_per_zspage(size);\n",
         "\t\tpages_per_zspage = calculate_zspage_chain_size(size);\n", T),
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

# The scan helpers signatures are unchanged in shape across 167/.178/.194/.211,
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

# Both khugepaged_scan_file() definitions move to the new signature; the
# CONFIG_SHMEM-off stub is rewritten first so that the remaining occurrence of
# the old two-line signature is unique for the step that follows.
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
_MC_FILE_STUB_NEW = (_MC_FILE_SIG_NEW + '\n'
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
    '\t\t\tresult = hugepage_vma_revalidate(mm, addr, &vma);\n'
    '\t\t\tif (result != SCAN_SUCCEED) {\n'
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


if __name__ == "__main__":
    main()

