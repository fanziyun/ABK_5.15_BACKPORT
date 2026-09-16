"""Batch 33: return a freed zspage's pages to the buddy allocator outside
``class->lock``.

Source: mainline ``7ef28e8b8142``, patch 3 of the v6 series "mm/zsmalloc:
reduce lock contention in zs_free()" (Wenchao Hao / Xueyuan Chen, Xiaomi;
merged by Andrew Morton, Reviewed-by Nhat Pham and Barry Song).  The patches
themselves live in ``research/zsmalloc_lockfree/``.

Only patch 3 is ported.  Patches 1-2 (``9909b088b1f0`` encodes the size_class
index into obj, ``59e88952a827`` drops ``pool->lock`` from zs_free on 64-bit)
delete a *pool-level* rwlock read side that android13-5.15 does not have:
``mm/zsmalloc.c`` has zero occurrences of ``pool->lock`` on every baseline this
module tracks, because 5.15's zs_free() already finds its class without one
(``pin_tag()`` -> ``obj_to_location()`` -> ``get_zspage_mapping()`` ->
``pool->size_class[]``).  Encoding class_idx would buy nothing here, and it
would move the handle's bit layout on a tree whose bit 0 is ``HANDLE_PIN_BIT``
-- the bit ``zs_page_migrate()`` re-takes through ``trypin_tag()`` before it
rewrites the PFN.  The exclusion is recorded in ``plan.md``.

What *does* exist on 5.15 is patch 3's target: zs_free() reaches
``free_zspage()`` -> ``__free_zspage()`` with ``class->lock`` held, and
``__free_zspage()`` calls ``put_page()`` once per component page.  Under
pressure that reaches the buddy allocator's zone lock, so the CPU waiting for
zone->lock holds ``class->lock`` and blocks every other zs_free() on the same
size class.  This module's own ``zsmalloc_chain_size`` graft lengthened that
section (``CONFIG_ZSMALLOC_CHAIN_SIZE``, default 8, instead of the 5.15
constant 4), so up to 8 pages go back under the lock rather than at most 4.

5.15 shape differences, verified symbol by symbol against the baselines:

* every dependency exists unconditionally -- ``trylock_zspage()``,
  ``kick_deferred_free()`` (stubbed without CONFIG_COMPACTION),
  ``is_zspage_isolated()``, ``remove_zspage()`` -- so no new line sits inside a
  CONFIG gate;
* ``remove_zspage()`` takes the fullness group here:
  ``remove_zspage(class, zspage, ZS_EMPTY)``;
* the per-class stat is ``zs_stat_dec(class, OBJ_ALLOCATED, ...)`` (upstream:
  ``class_stat_sub(class, ZS_OBJS_ALLOCATED, ...)``).  It stays under the lock:
  ``class->stats.objs[]`` is a plain ``unsigned long`` that ``zs_stat_dec()``
  updates with ``-=`` and ``zs_can_compact()`` reads through ``zs_stat_get()``.
  Only the ``atomic_long_sub()`` on ``pool->pages_allocated`` moves out;
* ``cache_free_zspage()`` still takes the pool here.

Freeing outside the lock is safe because the zspage is off every fullness list
(``remove_zspage()``), its pages are locked (``trylock_zspage()``) and it is not
isolated, so ``zs_compact()``, ``async_free_zspage()`` and
``zs_page_putback()`` cannot reach it, and migration needs ``lock_page()``.
``free_zspage()`` / ``__free_zspage()`` keep their locked form for the callers
that remain (``__zs_compact()`` and ``async_free_zspage()``).

Graft boundary: no other group writes into ``zs_free()``, ``__free_zspage()``
or ``free_zspage()``; ``zsmalloc_chain_size`` edits the sizing macros,
``get_pages_per_zspage()`` and ``ISOLATED_BITS`` -- disjoint regions -- so no
shape probe and no ordering constraint are needed.

No performance claim; the upstream numbers and why they do not transfer to a
per-size-class lock on a phone are in ``CHANGELOG.md`` (Batch 33).
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

ZSMALLOC_C = "mm/zsmalloc.c"

T = True

# ---------------------------------------------------------------------------
# Step 1: split __free_zspage() into the page-return half (no lock needed) and
# the locked wrapper.  Both VM_BUG_ONs stay in the lockless half so the
# zs_free() path keeps them (this is also what upstream did).
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Step 2: the zspage to be freed at the tail of zs_free().  Declared up front
# with the other locals so the unlocked tail can reach it.
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Step 3: the body.  trylock + remove + class bookkeeping stay under
# class->lock; the pages go back outside it.
# ---------------------------------------------------------------------------

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

# Symbols the unit test and the audits probe for.  LOCKLESS_HELPER is this
# group's own added function; the call-site fragment is what distinguishes a
# real port from one that only renamed the helper.
LOCKLESS_HELPER = ("static inline void __free_zspage_lockless(struct zs_pool *pool,\n"
                   "\t\t\t\tstruct zspage *zspage)")
FREE_OUTSIDE_LOCK = "if (zspage_to_free) {\n\t\t__free_zspage_lockless(pool, zspage_to_free);"
MARKER = "ABK stable_515_backport: 7ef28e8b8142"
# What must NOT survive in zs_free(): the old tail that freed the zspage with
# class->lock still held.
LOCKED_TAIL = "if (likely(!isolated))\n\t\tfree_zspage(pool, class, zspage);"


def build_steps():
    """Three required steps: the helper split, then zs_free()'s decl and tail.

    All required: a tree that learned ``__free_zspage_lockless()`` but never
    called it outside the lock would still compile and behave like the
    baseline, which is exactly the silent no-op this batch exists to avoid.
    """
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
    """Return the single Batch-30 PatchGroup record."""
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
