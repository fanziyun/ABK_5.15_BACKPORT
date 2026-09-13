# -*- coding: utf-8 -*-
"""Batch 13: ``android_vh_customize_alloc_gfp`` (android15-6.6) + ABK policy.

Two cooperating groups live here:

1. ``build_hook_steps()`` -- the **upstream-shape** graft of the ACK hook
   ``android_vh_customize_alloc_gfp`` (introduced on the android15-6.6 line,
   commit ``4466afd69452088299671fcf3df891ba07daa8e0`` "ANDROID: vendor_hooks:
   add vendor hook for supporting customize alloc_gfp at alloc_page_slowpath",
   Bug 337192903; also on 6.12; absent from 6.1 and from every android13-5.15
   baseline including the .211+ lts branch).  Three one-line additions -- the
   ``DECLARE_HOOK`` in ``include/trace/hooks/mm.h``, the ``trace_`` call in
   ``__alloc_pages()`` between the cpuset-nodemask restore and the
   ``__alloc_pages_slowpath()`` entry, and the ``EXPORT_TRACEPOINT_SYMBOL_GPL``
   in ``drivers/android/vendor_hooks.c``.  The 5.15 trees carry the same
   slowpath-entry block byte-for-byte as 6.6 (the cpuset fast-path series
   landed on android13-5.15 long ago), so the hunks transplant verbatim.
   Deliberately **no ABK marker comment**: the graft's target form is the
   upstream form, so a future baseline that carries the commit itself
   short-circuits to ``already_present`` (docs/porting_policy.md anchor
   policy).  KMI: the graft only *adds* the ``__tracepoint_`` / key symbols
   of the new hook; no existing exported symbol changes and no struct field
   is touched.  ``CONFIG_ANDROID_VENDOR_HOOKS=y`` ships in gki_defconfig on
   every baseline.

2. ``build_policy_steps()`` -- the ABK consumer without which the hook is
   inert: a **high-order slowpath fast-fail policy**.  A request that missed
   ``get_page_from_freelist()`` is about to spend the slowpath's direct
   reclaim + compaction retry loop; on a fragmented, nearly-full phone that
   is a multi-millisecond stall of the faulting task.  While the system is
   actually low -- available pages below ``abk_gfp_fastfail_pct`` percent of
   the summed high watermarks, probed at most once per second, the same
   shape as the Batch 9-1 readahead gate -- the slowpath attempt of requests
   with order >= ``abk_gfp_fastfail_order`` gains ``__GFP_NORETRY |
   __GFP_NOWARN``: 5.15's NORETRY semantics allow one direct-reclaim try and
   forbid the retry/compaction loop, so the request fails quickly into its
   caller's fallback path (a 4K fault instead of a THP, -ENOMEM to a
   tolerant caller).  The default threshold 9 is the THP class order on
   4K-page arm64, and on *this* tree it is not a no-op: measured at
   ``.167``, ``GFP_TRANSHUGE_LIGHT`` carries ``__GFP_NOWARN`` but **not**
   ``__GFP_NORETRY``; ``vma_thp_gfp_mask()`` under the default
   defrag=madvise hands a madvised fault ``LIGHT | __GFP_DIRECT_RECLAIM``
   (retryable) and khugepaged's defrag path allocates ``GFP_TRANSHUGE``
   (also NORETRY-free) -- exactly the retryable slowpath attempts this
   policy turns into fast-fails, alongside hugetlb runtime growth and
   driver order-9 requests.  Boot-time CMA/hugetlb pool population is
   deliberately *not* covered (late_initcall below).
   Knobs land under ``/sys/module/page_alloc/parameters/`` (the built-in
   file-basename convention Batch 9-1 already relies on with
   ``readahead.dynamic_readahead``), and the payload is compiled only with
   ``CONFIG_ANDROID_VENDOR_HOOKS``.

The other hook this investigation looked at, ``android_rvh_wake_up_new_task``
(restricted, first statement of ``wake_up_new_task()``,
``include/trace/hooks/sched.h``), is carried by all four 5.15 baselines
already -- it needs no graft here, only a future callback registrant
(research/hooks_gfp_vs_wake_up_new_task.md).
"""

import re

__all__ = [
    "build_hook_steps", "build_policy_steps",
    "HOOK_MM_OLD", "HOOK_MM_NEW", "HOOK_PA_OLD", "HOOK_PA_NEW",
    "HOOK_VH_OLD", "HOOK_VH_NEW", "POLICY_OLD", "POLICY_NEW",
    "T",
]

T = True


def _tabs(text):
    """Re-indent a 4-space-drafted kernel C block with real tabs."""
    text = re.sub(r"(?m)^    +", lambda m: "\t" * (len(m.group(0)) // 4), text)
    return text.rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# 1) the hook itself -- upstream-shape, verbatim from android15-6.6
# ---------------------------------------------------------------------------

# include/trace/hooks/mm.h: the endif guard comment appears exactly once per
# baseline (167/178/194/lts probed); upstream placed the hook at the very end
# of the declaration list, which is exactly this position.
HOOK_MM_OLD = "#endif /* _TRACE_HOOK_MM_H */\n"
HOOK_MM_NEW = (
    "DECLARE_HOOK(android_vh_customize_alloc_gfp,\n"
    "\tTP_PROTO(gfp_t *alloc_gfp, unsigned int order),\n"
    "\tTP_ARGS(alloc_gfp, order));\n"
    "\n"
    "#endif /* _TRACE_HOOK_MM_H */\n"
)

# mm/page_alloc.c: __alloc_pages() slowpath entry.  Both lines are unique
# (1 hit each) on every probed baseline.
HOOK_PA_OLD = (
    "\tac.nodemask = nodemask;\n"
    "\n"
    "\tpage = __alloc_pages_slowpath(alloc_gfp, order, &ac);\n"
)
HOOK_PA_NEW = (
    "\tac.nodemask = nodemask;\n"
    "\ttrace_android_vh_customize_alloc_gfp(&alloc_gfp, order);\n"
    "\n"
    "\tpage = __alloc_pages_slowpath(alloc_gfp, order, &ac);\n"
)

# drivers/android/vendor_hooks.c: upstream exported it right after
# android_vh_vmscan_kswapd_done, which every baseline exports (1 hit).
HOOK_VH_OLD = "EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_vmscan_kswapd_done);\n"
HOOK_VH_NEW = (
    "EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_vmscan_kswapd_done);\n"
    "EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_customize_alloc_gfp);\n"
)


# ---------------------------------------------------------------------------
# 2) the ABK policy payload, appended after the last block of page_alloc.c
# ---------------------------------------------------------------------------

POLICY_OLD = (
    "\t\tif (managed_zone(zone))\n"
    "\t\t\treturn true;\n"
    "\t}\n"
    "\treturn false;\n"
    "}\n"
    "#endif /* CONFIG_ZONE_DMA */\n"
)

_POLICY_BLOCK = _tabs(r"""
#ifdef CONFIG_ANDROID_VENDOR_HOOKS
/*
 * ABK stable_515_backport: Batch 13 high-order slowpath fast-fail.
 *
 * Consumer of the android_vh_customize_alloc_gfp hook grafted from
 * android15-6.6 (commit 4466afd69452).  A request that missed the
 * buddy fast path is about to spend __alloc_pages_slowpath()'s direct
 * reclaim and compaction retry loop, which on a fragmented,
 * nearly-full phone is a multi-millisecond stall of the faulting
 * task.  While memory is low -- available pages below
 * abk_gfp_fastfail_pct percent of the summed high watermarks, probed
 * at most once per second like the Batch 9-1 readahead gate -- the
 * slowpath attempt gets __GFP_NORETRY and __GFP_NOWARN added for
 * orders at or above abk_gfp_fastfail_order: 5.15's NORETRY semantics
 * allow one direct-reclaim try and forbid the retry loop, so the
 * request fails into its caller's normal fallback (a 4K fault instead
 * of a THP, -ENOMEM to a tolerant caller) instead of churning.  On this
 * tree the default is not a no-op even for THP: GFP_TRANSHUGE_LIGHT has
 * __GFP_NOWARN but no __GFP_NORETRY, and with the default defrag=madvise a
 * madvised fault (LIGHT | __GFP_DIRECT_RECLAIM) and khugepaged's defrag
 * allocations (GFP_TRANSHUGE) are the retryable class this gate catches.
 * Boot-time CMA/hugetlb pool population runs before late_initcall and is
 * deliberately left retrying.  Switch off with
 * page_alloc.abk_gfp_fastfail=0.
 */
static bool abk_gfp_fastfail = true;
static unsigned int abk_gfp_fastfail_order = 9;
static unsigned int abk_gfp_fastfail_pct = 50;

module_param(abk_gfp_fastfail, bool, 0644);
MODULE_PARM_DESC(abk_gfp_fastfail,
    "high-order page-allocator slowpath attempts turn no-retry under memory pressure");
module_param(abk_gfp_fastfail_order, uint, 0644);
MODULE_PARM_DESC(abk_gfp_fastfail_order,
    "minimum order the fast-fail policy applies to (default 9: THP-class)");
module_param(abk_gfp_fastfail_pct, uint, 0644);
MODULE_PARM_DESC(abk_gfp_fastfail_pct,
    "pressure gate: available < pct% of the summed high watermarks (0: gate removed, always fast-fail)");

static unsigned long abk_gfp_high_wm;
static unsigned long abk_gfp_wm_stamp;

static bool abk_gfp_under_pressure(void)
{
    unsigned long limit, sum;
    unsigned int pct;

    pct = READ_ONCE(abk_gfp_fastfail_pct);
    if (!pct)
        return true;

    if (!READ_ONCE(abk_gfp_wm_stamp) ||
        !time_is_after_jiffies(READ_ONCE(abk_gfp_wm_stamp) + HZ)) {
        struct zone *zone;

        sum = 0;
        for_each_zone(zone)
            sum += high_wmark_pages(zone);
        WRITE_ONCE(abk_gfp_high_wm, sum);
        WRITE_ONCE(abk_gfp_wm_stamp, jiffies);
    }

    limit = mult_frac(READ_ONCE(abk_gfp_high_wm), pct, 100);
    return si_mem_available() < (long)limit;
}

static void abk_gfp_fastfail_hook(void *data, gfp_t *gfp, unsigned int order)
{
    if (!READ_ONCE(abk_gfp_fastfail))
        return;
    if (order < READ_ONCE(abk_gfp_fastfail_order))
        return;
    if (!abk_gfp_under_pressure())
        return;
    *gfp |= __GFP_NORETRY | __GFP_NOWARN;
}

static int __init abk_gfp_fastfail_init(void)
{
    return register_trace_android_vh_customize_alloc_gfp(
        abk_gfp_fastfail_hook, NULL);
}
late_initcall(abk_gfp_fastfail_init);
#endif /* CONFIG_ANDROID_VENDOR_HOOKS */
""")

POLICY_NEW = POLICY_OLD + "\n" + _POLICY_BLOCK.lstrip("\n")


def build_hook_steps():
    """The verbatim upstream graft: declare, call, export."""
    return [
        ("include/trace/hooks/mm.h", HOOK_MM_OLD, HOOK_MM_NEW, T),
        ("mm/page_alloc.c", HOOK_PA_OLD, HOOK_PA_NEW, T),
        ("drivers/android/vendor_hooks.c", HOOK_VH_OLD, HOOK_VH_NEW, T),
    ]


def build_policy_steps():
    """Append the ABK fast-fail policy to mm/page_alloc.c."""
    return [
        ("mm/page_alloc.c", POLICY_OLD, POLICY_NEW, T),
    ]
