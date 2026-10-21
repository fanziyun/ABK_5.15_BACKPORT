# -*- coding: utf-8 -*-

import re

__all__ = [
    "build_hook_steps", "build_policy_steps",
    "HOOK_MM_OLD", "HOOK_MM_NEW", "HOOK_PA_OLD", "HOOK_PA_NEW",
    "HOOK_VH_OLD", "HOOK_VH_NEW", "POLICY_OLD", "POLICY_NEW",
    "T",
]

T = True


def _tabs(text):
    text = re.sub(r"(?m)^    +", lambda m: "\t" * (len(m.group(0)) // 4), text)
    return text.rstrip("\n") + "\n"


HOOK_MM_OLD = "#endif /* _TRACE_HOOK_MM_H */\n"
HOOK_MM_NEW = (
    "DECLARE_HOOK(android_vh_customize_alloc_gfp,\n"
    "\tTP_PROTO(gfp_t *alloc_gfp, unsigned int order),\n"
    "\tTP_ARGS(alloc_gfp, order));\n"
    "\n"
    "#endif /* _TRACE_HOOK_MM_H */\n"
)

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

HOOK_VH_OLD = "EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_vmscan_kswapd_done);\n"
HOOK_VH_NEW = (
    "EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_vmscan_kswapd_done);\n"
    "EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_customize_alloc_gfp);\n"
)


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
    return [
        ("include/trace/hooks/mm.h", HOOK_MM_OLD, HOOK_MM_NEW, T),
        ("mm/page_alloc.c", HOOK_PA_OLD, HOOK_PA_NEW, T),
        ("drivers/android/vendor_hooks.c", HOOK_VH_OLD, HOOK_VH_NEW, T),
    ]


def build_policy_steps():
    return [
        ("mm/page_alloc.c", POLICY_OLD, POLICY_NEW, T),
    ]
