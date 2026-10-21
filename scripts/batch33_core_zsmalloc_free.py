from __future__ import annotations

from abk_backport_engine import apply_steps

ZSMALLOC_C = "mm/zsmalloc.c"

T = True


_ZF_OLD = (
    "static void __free_zspage(struct zs_pool *pool, struct size_class *class,\n"
    "\t\t\t\tstruct zspage *zspage)\n"
    "{\n"
    "\tstruct page *page, *next;\n"
    "\tenum fullness_group fg;\n"
    "\tunsigned int class_idx;\n"
    "\n"
    "\tget_zspage_mapping(zspage, &class_idx, &fg);\n"
    "\n"
    "\tassert_spin_locked(&class->lock);\n"
    "\n"
    "\tVM_BUG_ON(get_zspage_inuse(zspage));\n"
    "\tVM_BUG_ON(fg != ZS_EMPTY);\n"
    "\n"
    "\tnext = page = get_first_page(zspage);\n"
    "\tdo {\n"
    "\t\tVM_BUG_ON_PAGE(!PageLocked(page), page);\n"
    "\t\tnext = get_next_page(page);\n"
    "\t\treset_page(page);\n"
    "\t\tunlock_page(page);\n"
    "\t\tdec_zone_page_state(page, NR_ZSPAGES);\n"
    "\t\tput_page(page);\n"
    "\t\tpage = next;\n"
    "\t} while (page != NULL);\n"
    "\n"
    "\tcache_free_zspage(pool, zspage);\n"
    "\n"
    "\tzs_stat_dec(class, OBJ_ALLOCATED, class->objs_per_zspage);\n"
    "\tatomic_long_sub(class->pages_per_zspage,\n"
    "\t\t\t\t\t&pool->pages_allocated);\n"
    "}\n"
)

_ZF_NEW = (
    "/*\n"
    " * ABK stable_515_backport: 7ef28e8b8142.  The page-return half of\n"
    " * __free_zspage() moves into its own helper so zs_free() can hand the\n"
    " * pages back to the buddy allocator *after* dropping class->lock:\n"
    " * put_page() reaches the zone lock under memory pressure, and waiting for\n"
    " * it with class->lock held blocks every other zs_free() on the same size\n"
    " * class behind it.  The locked wrapper below keeps the class bookkeeping\n"
    " * (class->stats.objs[] is a plain unsigned long) and stays the only entry\n"
    " * point for free_zspage() and async_free_zspage().\n"
    " */\n"
    "static inline void __free_zspage_lockless(struct zs_pool *pool,\n"
    "\t\t\t\tstruct zspage *zspage)\n"
    "{\n"
    "\tstruct page *page, *next;\n"
    "\tenum fullness_group fg;\n"
    "\tunsigned int class_idx;\n"
    "\n"
    "\tget_zspage_mapping(zspage, &class_idx, &fg);\n"
    "\n"
    "\tVM_BUG_ON(get_zspage_inuse(zspage));\n"
    "\tVM_BUG_ON(fg != ZS_EMPTY);\n"
    "\n"
    "\tnext = page = get_first_page(zspage);\n"
    "\tdo {\n"
    "\t\tVM_BUG_ON_PAGE(!PageLocked(page), page);\n"
    "\t\tnext = get_next_page(page);\n"
    "\t\treset_page(page);\n"
    "\t\tunlock_page(page);\n"
    "\t\tdec_zone_page_state(page, NR_ZSPAGES);\n"
    "\t\tput_page(page);\n"
    "\t\tpage = next;\n"
    "\t} while (page != NULL);\n"
    "\n"
    "\tcache_free_zspage(pool, zspage);\n"
    "}\n"
    "\n"
    "static void __free_zspage(struct zs_pool *pool, struct size_class *class,\n"
    "\t\t\t\tstruct zspage *zspage)\n"
    "{\n"
    "\tassert_spin_locked(&class->lock);\n"
    "\n"
    "\t__free_zspage_lockless(pool, zspage);\n"
    "\n"
    "\tzs_stat_dec(class, OBJ_ALLOCATED, class->objs_per_zspage);\n"
    "\tatomic_long_sub(class->pages_per_zspage,\n"
    "\t\t\t\t\t&pool->pages_allocated);\n"
    "}\n"
)


_ZS_FREE_DECL_OLD = (
    "void zs_free(struct zs_pool *pool, unsigned long handle)\n"
    "{\n"
    "\tstruct zspage *zspage;\n"
    "\tstruct page *f_page;\n"
)

_ZS_FREE_DECL_NEW = (
    "void zs_free(struct zs_pool *pool, unsigned long handle)\n"
    "{\n"
    "\tstruct zspage *zspage;\n"
    "\tstruct zspage *zspage_to_free = NULL;\n"
    "\tstruct page *f_page;\n"
)


_ZS_FREE_TAIL_OLD = (
    "\tif (likely(!isolated))\n"
    "\t\tfree_zspage(pool, class, zspage);\n"
    "out:\n"
    "\n"
    "\tspin_unlock(&class->lock);\n"
)

_ZS_FREE_TAIL_NEW = (
    "\t/*\n"
    "\t * ABK stable_515_backport: 7ef28e8b8142.  Take the zspage off its class\n"
    "\t * and do the class bookkeeping under class->lock, but return its pages\n"
    "\t * to the buddy allocator outside it: put_page() reaches the zone lock\n"
    "\t * under memory pressure, and holding class->lock across that blocks\n"
    "\t * every other zs_free() on this size class behind it.\n"
    "\t */\n"
    "\tif (likely(!isolated)) {\n"
    "\t\tif (trylock_zspage(zspage)) {\n"
    "\t\t\tremove_zspage(class, zspage, ZS_EMPTY);\n"
    "\t\t\tzs_stat_dec(class, OBJ_ALLOCATED,\n"
    "\t\t\t\t\tclass->objs_per_zspage);\n"
    "\t\t\tzspage_to_free = zspage;\n"
    "\t\t} else {\n"
    "\t\t\tkick_deferred_free(pool);\n"
    "\t\t}\n"
    "\t}\n"
    "out:\n"
    "\n"
    "\tspin_unlock(&class->lock);\n"
    "\n"
    "\tif (zspage_to_free) {\n"
    "\t\t__free_zspage_lockless(pool, zspage_to_free);\n"
    "\t\tatomic_long_sub(class->pages_per_zspage,\n"
    "\t\t\t\t\t&pool->pages_allocated);\n"
    "\t}\n"
)

LOCKLESS_HELPER = ("static inline void __free_zspage_lockless(struct zs_pool *pool,\n"
                   "\t\t\t\tstruct zspage *zspage)")
FREE_OUTSIDE_LOCK = "if (zspage_to_free) {\n\t\t__free_zspage_lockless(pool, zspage_to_free);"
MARKER = "ABK stable_515_backport: 7ef28e8b8142"
LOCKED_TAIL = "if (likely(!isolated))\n\t\tfree_zspage(pool, class, zspage);"


def build_steps():
    return [
        (ZSMALLOC_C, _ZF_OLD, _ZF_NEW, T),
        (ZSMALLOC_C, _ZS_FREE_DECL_OLD, _ZS_FREE_DECL_NEW, T),
        (ZSMALLOC_C, _ZS_FREE_TAIL_OLD, _ZS_FREE_TAIL_NEW, T),
    ]


def _zs_free_out_of_lock_apply(ctx):
    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    return [
        PatchGroup(
            "zsmalloc_free_zspage_out_of_lock",
            "shrink zs_free()'s class->lock critical section: return a dead "
            "zspage's pages to the buddy allocator after the lock is dropped, "
            "instead of waiting for zone->lock with class->lock held",
            [
                "7ef28e8b8142 (mainline, 'mm/zsmalloc: drop class lock before "
                "freeing zspage'; series 'mm/zsmalloc: reduce lock contention "
                "in zs_free()' v6, patch 3 of 4)",
            ],
            [ZSMALLOC_C],
            _zs_free_out_of_lock_apply,
        ),
    ]
