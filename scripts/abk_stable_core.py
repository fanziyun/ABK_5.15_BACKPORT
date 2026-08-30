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
        else (
            "fs/file.c",
            "out_release:\n\tkmem_cache_free(files_cachep, newf);\n\treturn ERR_PTR(error);\n}\n",
            "}\n",
            T,
        )
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
        ("fs/file.c", _FD_REPLACE_FD_OLD, _FD_REPLACE_FD_NEW, F),
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
        ("fs/file.c", _FD_REPLACE_FD_OLD, _FD_REPLACE_FD_NEW, F),
    ]
    # Vanilla punch_hole dup_fd variant: accepted in place of the AOSP one.
    if "sane_fdtable_size(old_fdt, max_fds)" not in text_probe:
        steps[6] = ("fs/file.c", _FD_DUPFD_VANILLA_OLD, _FD_DUPFD_VANILLA_NEW, T)
        steps[7] = ("fs/file.c", "out_release:\n\tkmem_cache_free(files_cachep, newf);\n\treturn ERR_PTR(error);\n}\n",
                    "}\n", T)

    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        raise SystemExit(
            "stable_backport_core/fdtable_alloc_conventions: fs/file.c matches no known "
            f"shape (pristine monthly, upstream 5.15.191, or suite-processed); {detail}"
        )
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
         "\t\tstatic_branch_enable(&cpusets_insane_config_key);\n"
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


PATCH_GROUPS = [
    PatchGroup(
        "fdtable_alloc_conventions",
        "alloc_fdtable() slots_wanted/ERR_PTR conventions + INT_MAX guard (5.15.191)",
        ["04a2c4b4511d (5.15.191)", "1d3b4bec3ce5 (5.15.191)", "ff8ec0dbe0150 (5.15.195)"],
        ["fs/file.c"],
        _fdtable_apply,
        hard=True,
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
    if ctx.family != "android13-5.15":
        print(f"[ABK stable_515_backport] unsupported family {ctx.family}; "
              "all groups stay report-only")
    run_child("stable_backport_core", PATCH_GROUPS, ctx, args)


if __name__ == "__main__":
    main()
