

from __future__ import annotations

from abk_backport_engine import apply_steps

READAHEAD = "mm/readahead.c"
SWAP_STATE = "mm/swap_state.c"
INTERNAL_H = "mm/internal.h"

T = True

GROUP_MARKER = "sailboat_swapin_readahead_window"
CLAMP_DEF = "unsigned int abk_swapin_window_clamp(unsigned int pages)"
GATE_DEF = "bool abk_swapin_shrink_gate(void)"
CLAMP_CALL = "pages = abk_swapin_window_clamp(pages);"
RA_TAIL_ANCHOR = "EXPORT_SYMBOL(readahead_expand);"

PARAM_LINES = (
    "module_param_named(swapin_window_enable, swapin_window_enable, bool, 0644);\n",
    "module_param_named(swapin_boost_enable, swapin_boost_enable, bool, 0644);\n",
    "module_param_named(swapin_boost_pages, swapin_boost_pages, uint, 0644);\n",
    "module_param_named(swapin_shrink_enable, swapin_shrink_enable, bool, 0644);\n",
    "module_param_named(swapin_shrink_pages, swapin_shrink_pages, uint, 0644);\n",
    "module_param_named(swapin_window_background_only,\n"
    "\t\t   swapin_window_background_only, bool, 0644);\n",
    "module_param_named(swapin_vma_ra, enable_vma_readahead, bool, 0644);\n",
)
PARAM_PINS = (
    "swapin_window_enable",
    "swapin_boost_enable",
    "swapin_boost_pages",
    "swapin_shrink_enable",
    "swapin_shrink_pages",
    "swapin_window_background_only",
    "swapin_vma_ra",
)

_RA_INCLUDES_OLD = (
    "#include <linux/fadvise.h>\n"
    "#include <linux/sched/mm.h>\n"
    "#include <trace/hooks/mm.h>\n"
    "\n"
    '#include "internal.h"\n'
)

_RA_INCLUDES_NEW = (
    "#include <linux/fadvise.h>\n"
    "#include <linux/sched/mm.h>\n"
    "#include <trace/hooks/mm.h>\n"
    "#include <linux/moduleparam.h>\n"
    "\n"
    '#include "internal.h"\n'
)

_RA_TAIL_OLD = "EXPORT_SYMBOL(readahead_expand);\n"

_RA_TAIL_NEW_HEAD = (
    """EXPORT_SYMBOL(readahead_expand);

/* sailboat_swapin_readahead_window */
#ifdef CONFIG_SWAP
extern bool enable_vma_readahead;

static bool swapin_window_enable __read_mostly = true;
static bool swapin_boost_enable __read_mostly = true;
static unsigned int swapin_boost_pages __read_mostly = 8;
static bool swapin_shrink_enable __read_mostly = true;
static unsigned int swapin_shrink_pages __read_mostly = 1;
static bool swapin_window_background_only __read_mostly = true;

"""
)
_RA_TAIL_NEW = (
    _RA_TAIL_NEW_HEAD
    + "".join(PARAM_LINES)
    + (
        """
#define ABK_SWAPIN_WINDOW_MAX	(1U << SWAP_RA_ORDER_CEILING)

bool abk_swapin_shrink_gate(void)
{
	if (!swapin_window_background_only)
		return true;

#ifdef CONFIG_ABK_DYNAMIC_READAHEAD
	return abk_dra_is_background_task();
#else
	return false;
#endif
}

unsigned int abk_swapin_window_clamp(unsigned int pages)
{
	if (!swapin_window_enable)
		return pages;

	if (swapin_boost_enable && pages < swapin_boost_pages) {
		unsigned int boost = swapin_boost_pages;

		if (boost > ABK_SWAPIN_WINDOW_MAX)
			boost = ABK_SWAPIN_WINDOW_MAX;
		if (boost > pages)
			pages = roundup_pow_of_two(boost);
	}

	if (swapin_shrink_enable && pages > swapin_shrink_pages &&
	    abk_swapin_shrink_gate()) {
		if (swapin_shrink_pages)
			pages = rounddown_pow_of_two(swapin_shrink_pages);
		else
			pages = 1;
	}

	return pages;
}
#endif 
"""
    )
)

_SS_VMA_RA_OLD = "static bool enable_vma_readahead __read_mostly = true;\n"

_SS_VMA_RA_NEW = (
    """/* sailboat_swapin_readahead_window */
bool enable_vma_readahead __read_mostly = true;
"""
)

_SS_SWAPIN_OLD = (
    "\t/* Don't shrink readahead too fast */\n"
    "\tlast_ra = prev_win / 2;\n"
    "\tif (pages < last_ra)\n"
    "\t\tpages = last_ra;\n"
    "\n"
    "\treturn pages;\n"
    "}\n"
)

_SS_SWAPIN_NEW = (
    """	/* Don't shrink readahead too fast */
	last_ra = prev_win / 2;
	if (pages < last_ra)
		pages = last_ra;

	/* sailboat_swapin_readahead_window */
	pages = abk_swapin_window_clamp(pages);

	return pages;
}
"""
)

_SS_CLUSTER_OLD = (
    "\tatomic_set(&last_readahead_pages, pages);\n"
    "\n"
    "\treturn pages;\n"
    "}\n"
)

_SS_CLUSTER_NEW = (
    """	atomic_set(&last_readahead_pages, pages);

	/* sailboat_swapin_readahead_window */
	pages = abk_swapin_window_clamp(pages);

	return pages;
}
"""
)

_INTERNAL_OLD = (
    "extern int isolate_lru_page(struct page *page);\n"
    "extern void putback_lru_page(struct page *page);\n"
)

_INTERNAL_NEW = (
    """extern int isolate_lru_page(struct page *page);
extern void putback_lru_page(struct page *page);

/* sailboat_swapin_readahead_window */
extern unsigned int abk_swapin_window_clamp(unsigned int pages);
extern bool abk_swapin_shrink_gate(void);
"""
)


def build_steps():
    
    return [
        (READAHEAD, _RA_INCLUDES_OLD, _RA_INCLUDES_NEW, T),
        (READAHEAD, _RA_TAIL_OLD, _RA_TAIL_NEW, T),
        (SWAP_STATE, _SS_VMA_RA_OLD, _SS_VMA_RA_NEW, T),
        (SWAP_STATE, _SS_SWAPIN_OLD, _SS_SWAPIN_NEW, T),
        (SWAP_STATE, _SS_CLUSTER_OLD, _SS_CLUSTER_NEW, T),
        (INTERNAL_H, _INTERNAL_OLD, _INTERNAL_NEW, T),
    ]


def _swapin_window_apply(ctx):
    try:
        ra_text = ctx.read(READAHEAD)
        ss_text = ctx.read(SWAP_STATE)
        ih_text = ctx.read(INTERNAL_H)
    except FileNotFoundError as exc:
        return "blocked_by_shape", f"{exc.filename}: file absent"

    marked = sum(
        1 for text in (ra_text, ss_text, ih_text) if GROUP_MARKER in text
    )
    if marked == 3:
        return "already_present", "the swap-in readahead window policy is already applied"
    if marked:
        return "blocked_by_shape", (
            "sailboat_swapin_readahead_window marker in only "
            f"{marked} of 3 files; refusing to patch a partially applied tree"
        )

    probe = [
        (READAHEAD, RA_TAIL_ANCHOR, "EXPORT_SYMBOL(readahead_expand)"),
        (SWAP_STATE, _SS_SWAPIN_OLD, "__swapin_nr_pages() floor + return"),
        (SWAP_STATE, _SS_CLUSTER_OLD, "swapin_nr_pages() (cluster) return"),
    ]
    for rel, anchor, what in probe:
        text = ra_text if rel == READAHEAD else ss_text
        if anchor not in text:
            return "blocked_by_shape", f"{rel}: {what} not in the expected shape"

    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    
    return [
        PatchGroup(
            "swapin_readahead_window",
            "put a tunable boost/shrink policy on the swap-in readahead window "
            "after the kernel's own floor: raise it to a power-of-two floor, "
            "cap it for the tasks the background classifier identifies, and "
            "expose the VMA-based swap-in switch (enable_vma_readahead) as "
            "swapin_vma_ra -- all arithmetic, no lock/sleep/allocation in the "
            "fault path",
            [
                "local policy on the 5.15.220 mm/swap_state.c window heuristic "
                "(no upstream commit; zram-only swap means the window is a "
                "decompression burst on the faulting CPUs)",
            ],
            [READAHEAD, SWAP_STATE, INTERNAL_H],
            _swapin_window_apply,
        ),
    ]
