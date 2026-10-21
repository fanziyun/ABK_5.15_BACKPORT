"""Batch 73: swap-in readahead window policy (boost / shrink / vma_ra).

Background: the window a swap-in fault reads ahead is decided entirely in
``mm/swap_state.c`` by ``__swapin_nr_pages()`` (a hits/misses estimator capped
at ``1 << page_cluster`` and floored at half the previous window) and, on the
cluster path, by ``swapin_nr_pages()``.  Those heuristics are asymmetric: the
window grows on hits but only decays through the "don't shrink too fast" floor,
so a task that thrashes -- or one that has just stopped being sequential --
keeps queueing a window it no longer benefits from, while a task starting a
fresh sequential stream waits several faults before the window grows at all.
On a device whose only swap device is zram, that overshoot is not free: the
reads are decompressions on the same CPUs that are servicing the fault, and
larger windows raise the reclaim/swap-in feedback that keeps the process
resident in the first place.

What this group does: a two-sided, tunable policy applied where the kernel's
own window computation is final, in two places:

  1. ``mm/swap_state.c`` ``__swapin_nr_pages()``: clamp immediately before its
     ``return pages;`` -- i.e. *after* the ``last_ra = prev_win / 2`` floor, so
     the shrink side is not undone by the floor on the next fault.  This covers
     both the cluster path (``swapin_nr_pages()`` -> ``swap_cluster_readahead()``)
     and the vma path (``swap_ra_info()`` -> ``swap_vma_readahead()``).
  2. ``mm/swap_state.c`` ``swapin_nr_pages()`` (cluster path): the same clamp
     before its final ``return pages;``.  It is arithmetic on an already-clamped
     value there (the helper is idempotent), kept because the cluster path is
     where an operator tuning this policy looks first, and because the early
     ``max_pages <= 1`` return stays untouched.

The policy itself lives at the end of ``mm/readahead.c`` (the file whose module
namespace then owns the knobs) as two helpers:

  * ``abk_swapin_window_clamp(pages)`` -- pure arithmetic, no lock, no sleep, no
    allocation, so it is safe from the page-fault path:
      - boost: ``pages = roundup_pow_of_two(boost)`` when the configured floor is
        above the computed window and boost is enabled; the window must stay a
        power of two because ``swap_cluster_readahead()`` derives its alignment
        mask as ``pages - 1`` and the vma path aligns on it too.  The floor is
        clamped to ``1 << SWAP_RA_ORDER_CEILING`` -- the largest window
        ``swap_ra_info()`` can represent/pack (on 32-bit that is also the size
        of its on-stack pte cache) -- *before* the round-up: ``roundup_pow_of_two
        ()`` returns ``unsigned long``, and a floor in [2^31 + 1, 2^32 - 1]
        rounds to 2^32, which truncates to 0 in the returned ``unsigned int``
        and would make ``mask = 0 - 1`` read the whole swap device.  A window
        the kernel already produced above the ceiling is left alone.
      - shrink: ``pages = rounddown_pow_of_two(swapin_shrink_pages)`` when the
        configured ceiling is below the computed window, shrink is enabled and
        the background gate below is open.  Rounded down for the same mask
        requirement; 0 is treated as 1 (a window of 0 would make the cluster
        mask all-ones).
      - ``swapin_window_enable = 0`` returns ``pages`` unchanged, so the whole
        feature can be switched off without losing the per-knob settings.
  * ``abk_swapin_shrink_gate()`` -- true when the window may be shrunk.  With
    ``swapin_window_background_only`` (default on) it reuses Batch 9-1's
    background classifier (``abk_dra_is_background_task()``, the cgroup name
    test in ``mm/readahead.c``) instead of deriving a second one, so a shrink
    hits background scans and not the foreground app faulting on the same
    device.  Returning true unconditionally when the knob is off is the
    documented escape hatch for "shrink everything".

Defaults pick the performance-maximal setting: boost 8 pages, shrink 1 page
but background-only, ``swapin_vma_ra`` on.  Runtime knobs are all ``module_param_named`` under
``/sys/module/readahead/parameters/`` (``0644``).

``swapin_vma_ra`` is deliberately *not* a new variable: it is a second sysfs
handle onto the kernel's own ``enable_vma_readahead`` (``mm/swap_state.c``), so
``/sys/kernel/mm/swap/vma_ra_enabled`` and the module knob can never disagree.
That requires the variable to be non-static (one-line de-static in
``swap_state.c``) and an ``extern`` declaration in the block that registers the
parameter.

Config gates, both load-bearing rather than decoration:

  * The whole appended block is inside ``#ifdef CONFIG_SWAP``: both clamp call
    sites live in ``mm/swap_state.c``, which is only built with ``CONFIG_SWAP``,
    and the block's ``swapin_vma_ra`` parameter references a symbol defined
    there -- without the gate a ``CONFIG_SWAP=n`` kernel would fail to link.
  * The reuse of Batch 9-1's classifier is inside
    ``#ifdef CONFIG_ABK_DYNAMIC_READAHEAD`` with a closed-gate ``#else``
    (AGENTS.md trap 7: a reference to a symbol that only exists inside a config
    gate carries the same gate).  When that config is off every task would look
    like a background task, so the fallback keeps the gate closed instead of
    shrinking foreground faults.

Shape probe: the two ``mm/swap_state.c`` return statements and the
``mm/readahead.c`` tail anchor (``EXPORT_SYMBOL(readahead_expand);``) must be
present, otherwise the group reports ``blocked_by_shape`` and writes nothing
(``apply_steps`` is transactional).

Scope notes:

  * One ``sailboat_swapin_readahead_window`` marker per touched file, riding in
    the added comments; it is also the idempotency anchor.
  * No vendor hook is added and no new lock/sleep/allocation appears in the
    fault path; the clamp is a handful of arithmetic ops and the gate is only
    consulted when the clamp would actually shrink.
  * ``mm/internal.h`` declares the two helpers in the swap declaration block
    next to ``isolate_lru_page()``; ``mm/swap_state.c`` is the only caller.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

READAHEAD = "mm/readahead.c"
SWAP_STATE = "mm/swap_state.c"
INTERNAL_H = "mm/internal.h"

T = True

# Pins for the audits and the unit test.
GROUP_MARKER = "sailboat_swapin_readahead_window"
CLAMP_DEF = "unsigned int abk_swapin_window_clamp(unsigned int pages)"
GATE_DEF = "bool abk_swapin_shrink_gate(void)"
CLAMP_CALL = "pages = abk_swapin_window_clamp(pages);"
RA_TAIL_ANCHOR = "EXPORT_SYMBOL(readahead_expand);"

# The seven knobs, in the order they are registered: C source lines first, then
# the bare sysfs names for pins.  swapin_vma_ra is the one whose store writes an
# existing kernel variable rather than a variable this group introduces.
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

# --- mm/readahead.c: 1) the include the parameter block needs ----------------
#
# module_param_named() lives in <linux/moduleparam.h>.  Batch 9-1 includes it,
# but only inside its own CONFIG_ABK_DYNAMIC_READAHEAD block, so it cannot be
# relied on here: this group's parameters are unconditional (within
# CONFIG_SWAP).  Anchored on the include run plus "internal.h", which Batch 9-1
# inserts *after* and therefore does not disturb.
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

# --- mm/readahead.c: 2) the policy block at the end of the file --------------
#
# Append point is the file's last line (only EXPORT_SYMBOL(readahead_expand)
# there).  Keeping the block at the end means it sits after every function it
# touches (only Batch 9-1's classifier) and after the file's own includes.
_RA_TAIL_OLD = "EXPORT_SYMBOL(readahead_expand);\n"

_RA_TAIL_NEW_HEAD = (
    "EXPORT_SYMBOL(readahead_expand);\n"
    "\n"
    "/*\n"
    " * sailboat_swapin_readahead_window: swap-in readahead window policy.\n"
    " *\n"
    " * The window a swap-in fault reads ahead is computed in\n"
    " * mm/swap_state.c:__swapin_nr_pages() (hits/misses estimator, capped at\n"
    " * 1 << page_cluster, floored at half the previous window) and, on the\n"
    " * cluster path, in swapin_nr_pages().  That heuristic only decays through\n"
    " * the floor, so a task that stops being sequential keeps its window, and a\n"
    " * fresh sequential stream grows it slowly.  This block puts a two-sided\n"
    " * policy on top, applied where that computation is final (after the\n"
    " * floor, so the shrink side is not undone).\n"
    " *\n"
    " * Every knob is a module parameter of this file, i.e. it appears under\n"
    " * /sys/module/readahead/parameters/ with mode 0644:\n"
    " *\n"
    " *   swapin_window_enable            master switch; 0 returns the\n"
    " *                                   kernel's own window untouched\n"
    " *   swapin_boost_enable             raise the window to the floor below\n"
    " *   swapin_boost_pages              boost floor, rounded up to a power\n"
    " *                                   of two (default 8)\n"
    " *   swapin_shrink_enable            cap the window at the ceiling below\n"
    " *   swapin_shrink_pages             shrink ceiling, rounded down to a\n"
    " *                                   power of two (default 1)\n"
    " *   swapin_window_background_only   apply the shrink only to the tasks\n"
    " *                                   Batch 9-1's classifier calls\n"
    " *                                   background (default on)\n"
    " *   swapin_vma_ra                   sysfs handle onto the kernel's own\n"
    " *                                   enable_vma_readahead (VMA-based\n"
    " *                                   swap-in readahead), so it has no\n"
    " *                                   private copy that could disagree\n"
    " *                                   with /sys/kernel/mm/swap/\n"
    " *                                   vma_ra_enabled\n"
    " *\n"
    " * The clamp is a pure arithmetic decision: no lock, no sleep, no\n"
    " * allocation, so it is safe in the fault path.  The gate reuses Batch\n"
    " * 9-1's background test (cgroup name based) instead of deriving a second\n"
    " * classifier, and is only consulted when the clamp would really shrink.\n"
    " *\n"
    " * Gated on CONFIG_SWAP because both clamp call sites live in\n"
    " * mm/swap_state.c (only built with CONFIG_SWAP) and because\n"
    " * swapin_vma_ra references enable_vma_readahead, which that file defines.\n"
    " */\n"
    "#ifdef CONFIG_SWAP\n"
    "extern bool enable_vma_readahead;\n"
    "\n"
    "static bool swapin_window_enable __read_mostly = true;\n"
    "static bool swapin_boost_enable __read_mostly = true;\n"
    "static unsigned int swapin_boost_pages __read_mostly = 8;\n"
    "static bool swapin_shrink_enable __read_mostly = true;\n"
    "static unsigned int swapin_shrink_pages __read_mostly = 1;\n"
    "static bool swapin_window_background_only __read_mostly = true;\n"
    "\n"
)
_RA_TAIL_NEW = (
    _RA_TAIL_NEW_HEAD
    + "".join(PARAM_LINES)
    + (
        "\n"
        "/*\n"
        " * Largest window the policy will hand back.  swap_ra_info() packs the\n"
        " * window into SWAP_RA_WIN_SHIFT bits of swap_readahead_info and layers its\n"
        " * own ceiling on top (1 << SWAP_RA_ORDER_CEILING); on 32-bit that ceiling\n"
        " * is also the size of its on-stack pte cache, so a larger boost could not\n"
        " * be represented and would overrun that cache.\n"
        " */\n"
        "#define ABK_SWAPIN_WINDOW_MAX\t(1U << SWAP_RA_ORDER_CEILING)\n"
        "\n"
        "/*\n"
        " * May the swap-in window be shrunk right now?  With\n"
        " * swapin_window_background_only off, yes; with it on, only for a task that\n"
        " * Batch 9-1's classifier calls background.\n"
        " */\n"
        "bool abk_swapin_shrink_gate(void)\n"
        "{\n"
        "\tif (!swapin_window_background_only)\n"
        "\t\treturn true;\n"
        "\n"
        "#ifdef CONFIG_ABK_DYNAMIC_READAHEAD\n"
        "\treturn abk_dra_is_background_task();\n"
        "#else\n"
        "\t/*\n"
        "\t * Batch 9-1's classifier is not compiled in.  Without it every task\n"
        "\t * would look like a background task and the shrink would also hit\n"
        "\t * foreground faults, so keep the gate closed.\n"
        "\t */\n"
        "\treturn false;\n"
        "#endif\n"
        "}\n"
        "\n"
        "/*\n"
        " * Apply the boost/shrink policy to an already computed window.  Called\n"
        " * from mm/swap_state.c after the kernel's own floor, in the fault path, so\n"
        " * nothing here may block.\n"
        " */\n"
        "unsigned int abk_swapin_window_clamp(unsigned int pages)\n"
        "{\n"
        "\tif (!swapin_window_enable)\n"
        "\t\treturn pages;\n"
        "\n"
        "\t/*\n"
        "\t * Boost.  The window must stay a power of two: the cluster path\n"
        "\t * derives its alignment mask as (pages - 1) and the vma path aligns on\n"
        "\t * Clamp the floor to ABK_SWAPIN_WINDOW_MAX BEFORE the round-up, for\n"
        "\t * two reasons.  roundup_pow_of_two() returns unsigned long, so a\n"
        "\t * configured floor in [2^31 + 1, 2^32 - 1] rounds to 2^32, which\n"
        "\t * truncates to 0 in this unsigned int: the cluster path would then\n"
        "\t * derive mask = 0 - 1 = all-ones and read the whole swap device, so a\n"
        "\t * window of 0 must be impossible.  And a window the kernel already\n"
        "\t * produced above the ceiling (page_cluster > 5) must not be pulled\n"
        "\t * back down by the boost, hence the boost > pages test.\n"
        "\t */\n"
        "\tif (swapin_boost_enable && pages < swapin_boost_pages) {\n"
        "\t\tunsigned int boost = swapin_boost_pages;\n"
        "\n"
        "\t\tif (boost > ABK_SWAPIN_WINDOW_MAX)\n"
        "\t\t\tboost = ABK_SWAPIN_WINDOW_MAX;\n"
        "\t\tif (boost > pages)\n"
        "\t\t\tpages = roundup_pow_of_two(boost);\n"
        "\t}\n"
        "\n"
        "\t/*\n"
        "\t * Shrink, gated so a foreground fault keeps its window.  Rounded down\n"
        "\t * for the mask reason above; 0 would turn the cluster mask into\n"
        "\t * all-ones, so it is treated as one page.\n"
        "\t */\n"
        "\tif (swapin_shrink_enable && pages > swapin_shrink_pages &&\n"
        "\t    abk_swapin_shrink_gate()) {\n"
        "\t\tif (swapin_shrink_pages)\n"
        "\t\t\tpages = rounddown_pow_of_two(swapin_shrink_pages);\n"
        "\t\telse\n"
        "\t\t\tpages = 1;\n"
        "\t}\n"
        "\n"
        "\treturn pages;\n"
        "}\n"
        "#endif /* CONFIG_SWAP */\n"
    )
)

# --- mm/swap_state.c: 1) de-static enable_vma_readahead ---------------------
#
# swapin_vma_ra is a second sysfs handle onto the kernel's own switch, so the
# variable has to leave file scope.  The store is the parameter's own (bool
# ops), i.e. it writes this very variable, and the existing
# /sys/kernel/mm/swap/vma_ra_enabled store keeps writing the same one.
_SS_VMA_RA_OLD = "static bool enable_vma_readahead __read_mostly = true;\n"

_SS_VMA_RA_NEW = (
    "/*\n"
    " * sailboat_swapin_readahead_window: non-static so the readahead parameter\n"
    " * namespace can carry swapin_vma_ra as a second handle onto this switch.\n"
    " * Its store writes this variable, exactly like vma_ra_enabled_store()\n"
    " * below, so both nodes read back the same state.\n"
    " */\n"
    "bool enable_vma_readahead __read_mostly = true;\n"
)

# --- mm/swap_state.c: 2) clamp in __swapin_nr_pages() -----------------------
#
# The anchor starts at the "don't shrink too fast" floor on purpose: the clamp
# must come AFTER it, otherwise the floor would put the window back on the next
# fault and the shrink side would never take effect.
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
    "\t/* Don't shrink readahead too fast */\n"
    "\tlast_ra = prev_win / 2;\n"
    "\tif (pages < last_ra)\n"
    "\t\tpages = last_ra;\n"
    "\n"
    "\t/*\n"
    "\t * sailboat_swapin_readahead_window: apply the boost/shrink policy\n"
    "\t * last, so the floor above cannot put a shrunk window back.\n"
    "\t */\n"
    "\tpages = abk_swapin_window_clamp(pages);\n"
    "\n"
    "\treturn pages;\n"
    "}\n"
)

# --- mm/swap_state.c: 3) clamp in swapin_nr_pages() (cluster path) ----------
#
# Same policy before the final return.  The early "max_pages <= 1" return is
# deliberately left alone: a one-page cluster is already the smallest window.
_SS_CLUSTER_OLD = (
    "\tatomic_set(&last_readahead_pages, pages);\n"
    "\n"
    "\treturn pages;\n"
    "}\n"
)

_SS_CLUSTER_NEW = (
    "\tatomic_set(&last_readahead_pages, pages);\n"
    "\n"
    "\t/*\n"
    "\t * sailboat_swapin_readahead_window: the cluster path goes through the\n"
    "\t * same policy, so a shrink ceiling below page_cluster's own cap still\n"
    "\t * takes effect here.\n"
    "\t */\n"
    "\tpages = abk_swapin_window_clamp(pages);\n"
    "\n"
    "\treturn pages;\n"
    "}\n"
)

# --- mm/internal.h: the two helpers, in the swap declaration block ----------
_INTERNAL_OLD = (
    "extern int isolate_lru_page(struct page *page);\n"
    "extern void putback_lru_page(struct page *page);\n"
)

_INTERNAL_NEW = (
    "extern int isolate_lru_page(struct page *page);\n"
    "extern void putback_lru_page(struct page *page);\n"
    "\n"
    "/*\n"
    " * in mm/readahead.c (sailboat_swapin_readahead_window): policy applied to\n"
    " * the swap-in readahead window computed in mm/swap_state.c.  Both are\n"
    " * arithmetic only -- no lock, sleep or allocation -- so the fault path may\n"
    " * call them.  The gate's answer is only used when the clamp would shrink.\n"
    " */\n"
    "extern unsigned int abk_swapin_window_clamp(unsigned int pages);\n"
    "extern bool abk_swapin_shrink_gate(void);\n"
)


def build_steps():
    """Six required steps: the parameter include and the policy block in
    mm/readahead.c, then the de-static plus the two clamps in
    mm/swap_state.c, then the declarations in mm/internal.h."""
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
        # apply_steps is transactional, so a partially marked tree means
        # something else edited these files.  Refuse rather than append a
        # second copy of the parameter block next to a stale one.
        return "blocked_by_shape", (
            "sailboat_swapin_readahead_window marker in only "
            f"{marked} of 3 files; refusing to patch a partially applied tree"
        )

    # Shape probe: both window computations and the file tail this group
    # appends to must be the expected 5.15 shape.
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
    """Return the single Batch-73 swap-in readahead window PatchGroup record."""
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
