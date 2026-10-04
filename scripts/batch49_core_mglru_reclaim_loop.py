"""Batch 49: MGLRU v7.2 reclaim-loop rework -- the Kairui Song v7 series.

Survey source: ``docs/survey_7_2_mm_reclaim.md`` §1, whose first pass rated the
series "not portable as a graft" and named this route instead: land the series'
**end state** as independently auditable groups on the 6.1-shape (page-based)
MGLRU this baseline carries.  Twelve upstream commits (cover ``0491e9f75c15``,
"mm/mglru: improve reclaim loop and dirty folio" v7), archived verbatim in
``research/upstream-5.15.y/patches/`` with a per-hunk inventory in
``research/upstream-5.15.y/hunks.txt`` (23 hunks, every one accounted for).

The twelve are a linear diff chain over the same ~300 lines, so the registry
sees them as **three atomic groups** (later chain members rewrite earlier ones'
new text -- splitting further would manufacture trap-5 collisions), each with a
group-level shape probe on its own final-shape symbol:

* ``mglru_reclaim_loop_rework`` -- ``790d3abeca09`` (rename, folded into the
  re-authored names), ``aa6ef5b159dc`` (batch limit moves to callers),
  ``163bc3d68c9f`` (the loop restructure), ``6e9be217a3ce`` (MIN_LRU_BATCH
  reclaim batches), ``3a72e078b4a3`` (exact isolation accounting),
  ``16b475d2ac3c`` (no type fallback on mere zero-isolation),
  ``12316f7902f8`` (aging no longer aborts the pass unconditionally).
  End state: ``should_run_aging()`` is a 4-arg pure predicate (the evictable
  size walk moves to upstream's ``lruvec_evictable_size()`` helper),
  ``get_nr_to_scan()`` only computes the scan budget (below-min/low gate,
  offline-memcg scrape, ``>> sc->priority``), and ``lru_gen_shrink_lruvec()``
  computes the budget once, runs aging as an explicit loop step, clamps each
  batch to ``MIN_LRU_BATCH`` and decrements the budget by what was scanned.
* ``mglru_dirty_reclaim_rework`` -- ``75d4c3f5fb98`` (dirty/writeback pages go
  through the common ``shrink_page_list()`` reactivation instead of being
  diverted to the next generation), ``acd22fbb9f47`` (see the deviation note),
  ``f37d3708b676`` (per-batch flusher wakeup replaces the once-per-loop one),
  ``32d87083ee97`` (``page_inc_gen()`` loses its ``reclaiming``/PG_reclaim
  dance -- "no more need to clean reclaim bit as the common routine will make
  use of it").  This is the group the reported benefit lives in: upstream
  measured f37d3708b676 alone at +29% throughput / -23% latency / -30% pgpgin
  / **-43% workingset_refault_file** (MongoDB/YCSB, server; no Snapdragon
  number is claimed here).
* ``mglru_prefault_accessed_placement`` -- ``6cbdd9726fb5``: refaulted
  workingset pages still get ``PG_active`` (active generations), prefaulted
  file pages instead get ``mark_page_accessed()`` -> ``PG_referenced`` so the
  placement formula puts them one generation up, and the WORKINGSET_ACTIVATE
  counter stops counting non-workingset prefaults.

5.15-shape decisions (each is a deliberate deviation from the upstream text,
pinned by ``tests/implementation_audit.py``):

* **Names and the export stay 5.15's**: ``sort_page``/``isolate_page``/
  ``scan_pages``/``isolate_pages``/``evict_pages``/``lru_gen_shrink_lruvec``
  keep their names and ``isolate_page()`` keeps its ``EXPORT_SYMBOL_GPL``
  signature -- upstream's folio-era renames (``sort_folio``/``isolate_folio``/
  ``scan_folios``/``try_to_shrink_lruvec``) buy nothing here.
* **``need_rotate``/``MEMCG_LRU_YOUNG`` is dropped**: the 6.x memcg-LRU
  rotation (``shrink_one()``/``shrink_many()``) has no 5.15 carrier and
  ``lru_gen_shrink_lruvec()`` is void to its callers.  ``root_reclaim()`` is
  carried (upstream's own predicate, incl. root-cgroup reclaim) and its
  ``should_age`` break ends the pass instead of rotating.
* **kswapd aging stays deferred** to ``lru_gen_age_node()`` (the old
  ``get_nr_to_scan()`` "leave the work to lru_gen_age_node()" branch): on 5.15
  that function owns the node-wide pass and the min_ttl/OOM policy, and making
  kswapd age inline is a behaviour change this batch does not need to buy.
* **``get_nr_to_scan()`` keeps 5.15's protection gate** (below-min and
  below-low-without-memcg_low_reclaim) in place of upstream's
  ``apply_proportional_protection()`` (absent here).
* **``isolate_page()`` keeps its ``may_writepage && __GFP_IO`` guard**, so
  ``acd22fbb9f47`` is a no-op on this baseline: the guard is 5.15's later
  refinement of the check upstream deleted (its commit message calls the old
  check "redundant ... since shrink_folio_list() already handles all these
  cases with proper granularity" -- the 5.15 form gates only write-inhibited
  dirty/unswappable pages out of isolation, and deleting it would feed
  ``shrink_page_list()`` pages it cannot page out).  ``ClearPageReclaim()``
  goes as upstream does; ``ClearPageReferenced()`` stays (5.15-only line,
  shrink_page_list()'s fresh-pass accounting relies on it).
* **``isolate_scanned``/``type_scanned`` are not carried**: upstream threads
  them into ``trace_mm_vmscan_lru_shrink_inactive()``, which this baseline's
  ``evict_pages()`` has never called.  ``isolate_type`` is carried (PGSTEAL
  and ``need_swapping`` need the isolated type).
* **``reclaim_throttle(VMSCAN_THROTTLE_WRITEBACK)`` is dropped** from
  f37d3708b676's block: 5.15 has neither the function nor the enum (grepped),
  exactly as ``mglru_wake_flushers`` dropped it.  The per-batch block is the
  tree's own classical idiom (``shrink_inactive_list()``):
  ``if (stat.nr_unqueued_dirty == nr_taken) wakeup_flusher_threads(...)``.
* **trap 5, mechanically**: this group chain rewrites every line
  ``mglru_wake_flushers`` generates (its ``sort_page`` accounting, the
  ``scan_pages`` ``file_taken`` line, the ``evict_pages`` ``unqueued_dirty``
  accumulation and the whole end-of-loop block).  That group therefore gets a
  shape probe on the flusher comment f37d3708b676 *moves* rather than deletes,
  so pass two reports ``already_present`` instead of re-appending.  The same
  applies to ``mglru_rework_aging_feedback``/``mglru_rework_type_selection``,
  whose generated ``try_to_inc_min_seq()``/``evict_pages``/``isolate_pages``
  text this chain rewrites and whose ``evictable_min_seq``/summed-tier symbols
  survive -- they carry their own probes now.
* **6cbdd9726fb5's ``lru_gen_set_refs()`` hunk is not carried**: this
  baseline's aging walk promotes through ``page_update_gen()`` and has no
  refs-setting walk helper at all (the only refs ladder is ``page_inc_refs()``
  from ``mark_page_accessed()``, which the swap.c step routes correctly), and
  ``page_inc_refs()``' referenced->workingset->counter ladder already is the
  "promote on second access" behaviour the hunk adds upstream.
* G3's mm_inline.h hunk re-authors upstream's ``lru_gen_folio_seq()`` formula
  onto 5.15's ``lru_gen_add_page()`` three-case shape: upstream moves
  referenced pages from the oldest to the second oldest generation, so the
  5.15 "second oldest" bucket (``min_seq[type] + 1``) gains a
  ``PageReferenced(page)`` case.
* G2's ``lru_cache_add()`` step keeps the
  ``trace_android_vh_lru_cache_add_page_activate()`` vendor hook on the
  activation path it names; the hook now fires for workingset (activate)
  decisions only, since prefaulted non-workingset pages no longer activate.

Nothing here is a new CONFIG gate (all text sits inside the existing
``CONFIG_LRU_GEN`` block, and ``LRU_GEN_ENABLED`` is this module's default
tier), no struct field is added, and no exported symbol changes shape.

Sources (verbatim, in ``research/upstream-5.15.y/patches/``):
``790d3abeca09``, ``aa6ef5b159dc``, ``163bc3d68c9f``, ``3a72e078b4a3``,
``16b475d2ac3c``, ``6e9be217a3ce``, ``12316f7902f8``, ``acd22fbb9f47``,
``75d4c3f5fb98``, ``f37d3708b676``, ``32d87083ee97``, ``6cbdd9726fb5``.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

VMSCAN_C = "mm/vmscan.c"
SWAP_C = "mm/swap.c"
WORKINGSET_C = "mm/workingset.c"
MM_INLINE_H = "include/linux/mm_inline.h"

T = True

# Group-level shape probes (docs/group_recipe.md trap 5 / the zram_recompression
# precedent): one of the group's own added symbols that its successors keep.
LOOP_PROBE = "static unsigned long lruvec_evictable_size(struct lruvec *lruvec, int swappiness)"
DIRTY_PROBE = "if (stat.nr_unqueued_dirty == isolated) {"
PLACEMENT_PROBE = "sailboat_mglru_prefault_accessed_placement"


# ---------------------------------------------------------------------------
# mglru_reclaim_loop_rework: 790d3abeca09 + aa6ef5b159dc + 163bc3d68c9f +
# 6e9be217a3ce + 3a72e078b4a3 + 16b475d2ac3c + 12316f7902f8
# ---------------------------------------------------------------------------

_LOOP_STEPS = [
    # -- should_run_aging(): the size walk becomes lruvec_evictable_size()
    #    and the function becomes the 4-arg pure predicate (163bc3d68c9f) --
    (VMSCAN_C,
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
     "/* sailboat_mglru_reclaim_loop_rework: v7.2 163bc3d68c9f -- the evictable\n"
     " * size walk moves out of should_run_aging() into upstream's own\n"
     " * lruvec_evictable_size() helper shape. */\n"
     "static unsigned long lruvec_evictable_size(struct lruvec *lruvec, int swappiness)\n"
     "{\n"
     "\tint gen, type, zone;\n"
     "\tunsigned long seq, total = 0;\n"
     "\tstruct lru_gen_struct *lrugen = &lruvec->lrugen;\n"
     "\tDEFINE_MAX_SEQ(lruvec);\n"
     "\tDEFINE_MIN_SEQ(lruvec);\n"
     "\n"
     "\tfor_each_evictable_type(type, swappiness) {\n"
     "\t\tfor (seq = min_seq[type]; seq <= max_seq; seq++) {\n"
     "\t\t\tgen = lru_gen_from_seq(seq);\n"
     "\n"
     "\t\t\tfor (zone = 0; zone < MAX_NR_ZONES; zone++)\n"
     "\t\t\t\ttotal += max_t(long, READ_ONCE(lrugen->nr_pages[gen][type][zone]),\n"
     "\t\t\t\t\t\t0);\n"
     "\t\t}\n"
     "\t}\n"
     "\n"
     "\treturn total;\n"
     "}\n"
     "\n"
     "/* sailboat_mglru_reclaim_loop_rework: v7.2 163bc3d68c9f -- 4-arg pure\n"
     " * predicate; the nr_to_scan computation moves to get_nr_to_scan(). */\n"
     "static bool should_run_aging(struct lruvec *lruvec, unsigned long max_seq,\n"
     "\t\t\t     struct scan_control *sc, int swappiness)\n"
     "{\n"
     "\tDEFINE_MIN_SEQ(lruvec);\n"
     "\n"
     "\t/* have to run aging, since eviction is not possible anymore */\n"
     "\tif (evictable_min_seq(min_seq, swappiness) + MIN_NR_GENS > max_seq)\n"
     "\t\treturn true;\n"
     "\n"
     "\t/* try to avoid aging, do gentle reclaim at the default priority */\n"
     "\tif (sc->priority == DEF_PRIORITY)\n"
     "\t\treturn false;\n"
     "\n"
     "\t/* better to run aging even though eviction is still possible */\n"
     "\treturn evictable_min_seq(min_seq, swappiness) + MIN_NR_GENS == max_seq;\n"
     "}\n",
     T),
    # -- age_lruvec(): mechanical fallout of the new predicate signature; the
    #    min_ttl size test keeps the exact old nr_to_scan value --
    (VMSCAN_C,
     "static bool age_lruvec(struct lruvec *lruvec, struct scan_control *sc, unsigned long min_ttl)\n"
     "{\n"
     "\tbool need_aging;\n"
     "\tunsigned long nr_to_scan;\n"
     "\tint swappiness = get_swappiness(lruvec, sc);\n"
     "\tstruct mem_cgroup *memcg = lruvec_memcg(lruvec);\n"
     "\tDEFINE_MAX_SEQ(lruvec);\n"
     "\tDEFINE_MIN_SEQ(lruvec);\n"
     "\n"
     "\tVM_WARN_ON_ONCE(sc->memcg_low_reclaim);\n"
     "\n"
     "\tmem_cgroup_calculate_protection(NULL, memcg);\n"
     "\n"
     "\tif (mem_cgroup_below_min(memcg))\n"
     "\t\treturn false;\n"
     "\n"
     "\tneed_aging = should_run_aging(lruvec, max_seq, min_seq, sc, swappiness, &nr_to_scan);\n"
     "\n"
     "\tif (min_ttl) {\n"
     "\t\tint gen = lru_gen_from_seq(evictable_min_seq(min_seq, swappiness));\n"
     "\t\tunsigned long birth = READ_ONCE(lruvec->lrugen.timestamps[gen]);\n"
     "\n"
     "\t\tif (time_is_after_jiffies(birth + min_ttl))\n"
     "\t\t\treturn false;\n"
     "\n"
     "\t\t/* the size is likely too small to be helpful */\n"
     "\t\tif (!nr_to_scan && sc->priority != DEF_PRIORITY)\n"
     "\t\t\treturn false;\n"
     "\t}\n",
     "static bool age_lruvec(struct lruvec *lruvec, struct scan_control *sc, unsigned long min_ttl)\n"
     "{\n"
     "\tbool need_aging;\n"
     "\tunsigned long evictable;\n"
     "\tint swappiness = get_swappiness(lruvec, sc);\n"
     "\tstruct mem_cgroup *memcg = lruvec_memcg(lruvec);\n"
     "\tDEFINE_MAX_SEQ(lruvec);\n"
     "\tDEFINE_MIN_SEQ(lruvec);\n"
     "\n"
     "\tVM_WARN_ON_ONCE(sc->memcg_low_reclaim);\n"
     "\n"
     "\tmem_cgroup_calculate_protection(NULL, memcg);\n"
     "\n"
     "\tif (mem_cgroup_below_min(memcg))\n"
     "\t\treturn false;\n"
     "\n"
     "\tneed_aging = should_run_aging(lruvec, max_seq, sc, swappiness);\n"
     "\n"
     "\t/* the exact value should_run_aging() used to report as nr_to_scan */\n"
     "\tevictable = lruvec_evictable_size(lruvec, swappiness);\n"
     "\tif (mem_cgroup_online(memcg))\n"
     "\t\tevictable >>= sc->priority;\n"
     "\n"
     "\tif (min_ttl) {\n"
     "\t\tint gen = lru_gen_from_seq(evictable_min_seq(min_seq, swappiness));\n"
     "\t\tunsigned long birth = READ_ONCE(lruvec->lrugen.timestamps[gen]);\n"
     "\n"
     "\t\tif (time_is_after_jiffies(birth + min_ttl))\n"
     "\t\t\treturn false;\n"
     "\n"
     "\t\t/* the size is likely too small to be helpful */\n"
     "\t\tif (!evictable && sc->priority != DEF_PRIORITY)\n"
     "\t\t\treturn false;\n"
     "\t}\n",
     T),
    # -- try_to_inc_min_seq(): void, no success flag (3a72e078b4a3) --
    (VMSCAN_C,
     "static bool try_to_inc_min_seq(struct lruvec *lruvec, int swappiness)\n"
     "{\n"
     "\tint gen, type, zone;\n"
     "\tbool success = false;\n"
     "\tstruct lru_gen_struct *lrugen = &lruvec->lrugen;\n",
     "static void try_to_inc_min_seq(struct lruvec *lruvec, int swappiness)\n"
     "{\n"
     "\tint gen, type, zone;\n"
     "\tstruct lru_gen_struct *lrugen = &lruvec->lrugen;\n",
     T),
    (VMSCAN_C,
     "\t\treset_ctrl_pos(lruvec, type, true);\n"
     "\t\tWRITE_ONCE(lrugen->min_seq[type], min_seq[type]);\n"
     "\t\tsuccess = true;\n"
     "\t}\n"
     "\n"
     "\treturn success;\n"
     "}\n",
     "\t\treset_ctrl_pos(lruvec, type, true);\n"
     "\t\tWRITE_ONCE(lrugen->min_seq[type], min_seq[type]);\n"
     "\t}\n"
     "}\n",
     T),
    # -- scan_pages(): the batch limit comes in as nr_to_scan (aa6ef5b159dc)
    #    and the exact isolated count goes out as *isolatedp (3a72e078b4a3) --
    (VMSCAN_C,
     "static int scan_pages(struct lruvec *lruvec, struct scan_control *sc,\n"
     "\t\t      int type, int tier, struct list_head *list)\n"
     "{\n"
     "\tint i;\n"
     "\tint gen;\n"
     "\tenum vm_event_item item;\n"
     "\tint sorted = 0;\n"
     "\tint scanned = 0;\n"
     "\tint isolated = 0;\n"
     "\tint remaining = MAX_LRU_BATCH;\n"
     "\tstruct lru_gen_struct *lrugen = &lruvec->lrugen;\n"
     "\tstruct mem_cgroup *memcg = lruvec_memcg(lruvec);\n"
     "\n"
     "\tVM_WARN_ON_ONCE(!list_empty(list));\n",
     "static int scan_pages(struct lruvec *lruvec, struct scan_control *sc,\n"
     "\t\t      int type, int tier, unsigned long nr_to_scan,\n"
     "\t\t      struct list_head *list, int *isolatedp)\n"
     "{\n"
     "\tint i;\n"
     "\tint gen;\n"
     "\tenum vm_event_item item;\n"
     "\tint sorted = 0;\n"
     "\tint scanned = 0;\n"
     "\tint isolated = 0;\n"
     "\tunsigned long remaining = nr_to_scan;\n"
     "\tstruct lru_gen_struct *lrugen = &lruvec->lrugen;\n"
     "\tstruct mem_cgroup *memcg = lruvec_memcg(lruvec);\n"
     "\n"
     "\tVM_WARN_ON_ONCE(nr_to_scan > MAX_LRU_BATCH);\n"
     "\tVM_WARN_ON_ONCE(!list_empty(list));\n",
     T),
    (VMSCAN_C,
     "\t/*\n"
     "\t * There might not be eligible pages due to reclaim_idx, may_unmap and\n"
     "\t * may_writepage. Check the remaining to prevent livelock if it's not\n"
     "\t * making progress.\n"
     "\t */\n"
     "\treturn isolated || !remaining ? scanned : 0;\n"
     "}\n",
     "\t*isolatedp = isolated;\n"
     "\treturn scanned;\n"
     "}\n",
     T),
    # -- isolate_pages(): total_scanned across types, isolate_type out,
    #    fallback only when a type scanned nothing (3a72e078b4a3 +
    #    16b475d2ac3c) --
    (VMSCAN_C,
     "static int isolate_pages(struct lruvec *lruvec, struct scan_control *sc, int swappiness,\n"
     "\t\t\t int *type_scanned, struct list_head *list)\n"
     "{\n"
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
     "/* sailboat_mglru_reclaim_loop_rework: v7.2 3a72e078b4a3 + 16b475d2ac3c --\n"
     " * exact isolation accounting across evictable types; no type fallback\n"
     " * when the type scanned but isolated nothing. */\n"
     "static int isolate_pages(struct lruvec *lruvec, struct scan_control *sc, int swappiness,\n"
     "\t\t\t unsigned long nr_to_scan, struct list_head *list,\n"
     "\t\t\t int *isolated, int *isolate_type)\n"
     "{\n"
     "\tint i;\n"
     "\tint total_scanned = 0;\n"
     "\tint type = get_type_to_scan(lruvec, swappiness);\n"
     "\n"
     "\tfor_each_evictable_type(i, swappiness) {\n"
     "\t\tint scanned;\n"
     "\t\tint tier = get_tier_idx(lruvec, type);\n"
     "\n"
     "\t\tscanned = scan_pages(lruvec, sc, type, tier, nr_to_scan, list, isolated);\n"
     "\n"
     "\t\ttotal_scanned += scanned;\n"
     "\t\tif (*isolated) {\n"
     "\t\t\t*isolate_type = type;\n"
     "\t\t\tbreak;\n"
     "\t\t}\n"
     "\t\t/*\n"
     "\t\t * If scanned > 0 and isolated == 0, avoid falling back to the\n"
     "\t\t * other type, as this type remains sufficient. Falling back\n"
     "\t\t * too readily can disrupt the positive_ctrl_err() bias.\n"
     "\t\t */\n"
     "\t\tif (!scanned)\n"
     "\t\t\ttype = !type;\n"
     "\t}\n"
     "\n"
     "\treturn total_scanned;\n"
     "}\n",
     T),
    # -- evict_pages(): nr_to_scan in, try_to_inc_min_seq() flushes before and
    #    after isolation, no min-GENS scanned=0 hack (3a72e078b4a3) --
    (VMSCAN_C,
     "static int evict_pages(struct lruvec *lruvec, struct scan_control *sc, int swappiness,\n"
     "\t\t       bool *need_swapping)\n"
     "{\n"
     "\tint type;\n"
     "\tint scanned;\n"
     "\tint reclaimed;\n"
     "\tLIST_HEAD(list);\n"
     "\tLIST_HEAD(clean);\n"
     "\tstruct page *page;\n"
     "\tstruct page *next;\n"
     "\tenum vm_event_item item;\n"
     "\tstruct reclaim_stat stat;\n"
     "\tstruct lru_gen_mm_walk *walk;\n"
     "\tbool skip_retry = false;\n"
     "\tstruct lru_gen_struct *lrugen = &lruvec->lrugen;\n"
     "\tstruct mem_cgroup *memcg = lruvec_memcg(lruvec);\n"
     "\tstruct pglist_data *pgdat = lruvec_pgdat(lruvec);\n"
     "\n"
     "\tspin_lock_irq(&lruvec->lru_lock);\n"
     "\n"
     "\tscanned = isolate_pages(lruvec, sc, swappiness, &type, &list);\n"
     "\n"
     "\tscanned += try_to_inc_min_seq(lruvec, swappiness);\n"
     "\n"
     "\tif (evictable_min_seq(lrugen->min_seq, swappiness) + MIN_NR_GENS > lrugen->max_seq)\n"
     "\t\tscanned = 0;\n"
     "\n"
     "\tspin_unlock_irq(&lruvec->lru_lock);\n"
     "\n"
     "\tif (list_empty(&list))\n"
     "\t\treturn scanned;\n",
     "static int evict_pages(struct lruvec *lruvec, struct scan_control *sc, int swappiness,\n"
     "\t\t       unsigned long nr_to_scan, bool *need_swapping)\n"
     "{\n"
     "\tint scanned, reclaimed;\n"
     "\tint isolated = 0, type = 0;\n"
     "\tLIST_HEAD(list);\n"
     "\tLIST_HEAD(clean);\n"
     "\tstruct page *page;\n"
     "\tstruct page *next;\n"
     "\tenum vm_event_item item;\n"
     "\tstruct reclaim_stat stat;\n"
     "\tstruct lru_gen_mm_walk *walk;\n"
     "\tbool skip_retry = false;\n"
     "\tstruct mem_cgroup *memcg = lruvec_memcg(lruvec);\n"
     "\tstruct pglist_data *pgdat = lruvec_pgdat(lruvec);\n"
     "\n"
     "\tspin_lock_irq(&lruvec->lru_lock);\n"
     "\n"
     "\t/* In case page deletion left empty old gens, flush them */\n"
     "\ttry_to_inc_min_seq(lruvec, swappiness);\n"
     "\n"
     "\tscanned = isolate_pages(lruvec, sc, swappiness, nr_to_scan, &list,\n"
     "\t\t\t\t&isolated, &type);\n"
     "\n"
     "\t/* Scanning may have emptied the oldest gen, flush it */\n"
     "\tif (scanned)\n"
     "\t\ttry_to_inc_min_seq(lruvec, swappiness);\n"
     "\n"
     "\tspin_unlock_irq(&lruvec->lru_lock);\n"
     "\n"
     "\tif (list_empty(&list))\n"
     "\t\treturn scanned;\n",
     T),
    # -- run_eviction(): the debugfs path keeps MAX_LRU_BATCH batches
    #    (aa6ef5b159dc) --
    (VMSCAN_C,
     "static int run_eviction(struct lruvec *lruvec, unsigned long seq, struct scan_control *sc,\n"
     "\t\t\tint swappiness, unsigned long nr_to_reclaim)\n"
     "{\n"
     "\tDEFINE_MAX_SEQ(lruvec);\n",
     "static int run_eviction(struct lruvec *lruvec, unsigned long seq, struct scan_control *sc,\n"
     "\t\t\tint swappiness, unsigned long nr_to_reclaim)\n"
     "{\n"
     "\tint nr_batch;\n"
     "\tDEFINE_MAX_SEQ(lruvec);\n",
     T),
    (VMSCAN_C,
     "\t\tif (!evict_pages(lruvec, sc, swappiness, NULL))\n"
     "\t\t\treturn 0;\n",
     "\t\tnr_batch = min_t(unsigned long, nr_to_reclaim - sc->nr_reclaimed,\n"
     "\t\t\t\t MAX_LRU_BATCH);\n"
     "\t\tif (!evict_pages(lruvec, sc, swappiness, nr_batch, NULL))\n"
     "\t\t\treturn 0;\n",
     T),
    # -- get_nr_to_scan(): budget only (163bc3d68c9f) --
    (VMSCAN_C,
     "static unsigned long get_nr_to_scan(struct lruvec *lruvec, struct scan_control *sc,\n"
     "\t\t\t\t    int swappiness, bool *need_aging)\n"
     "{\n"
     "\tunsigned long nr_to_scan;\n"
     "\tstruct mem_cgroup *memcg = lruvec_memcg(lruvec);\n"
     "\tDEFINE_MAX_SEQ(lruvec);\n"
     "\tDEFINE_MIN_SEQ(lruvec);\n"
     "\n"
     "\tif (mem_cgroup_below_min(memcg) ||\n"
     "\t    (mem_cgroup_below_low(memcg) && !sc->memcg_low_reclaim))\n"
     "\t\treturn 0;\n"
     "\n"
     "\t*need_aging = should_run_aging(lruvec, max_seq, min_seq, sc, swappiness, &nr_to_scan);\n"
     "\tif (!*need_aging)\n"
     "\t\treturn nr_to_scan;\n"
     "\n"
     "\t/* skip the aging path at the default priority */\n"
     "\tif (sc->priority == DEF_PRIORITY)\n"
     "\t\tgoto done;\n"
     "\n"
     "\t/* leave the work to lru_gen_age_node() */\n"
     "\tif (current_is_kswapd())\n"
     "\t\treturn 0;\n"
     "\n"
     "\tif (try_to_inc_max_seq(lruvec, max_seq, sc, swappiness, false))\n"
     "\t\treturn nr_to_scan;\n"
     "done:\n"
     "\treturn evictable_min_seq(min_seq, swappiness) + MIN_NR_GENS <= max_seq ?\n"
     "\t\tnr_to_scan : 0;\n"
     "}\n",
     "/* sailboat_mglru_reclaim_loop_rework: v7.2 163bc3d68c9f -- budget only;\n"
     " * the aging decision moved into the loop.  This baseline has no\n"
     " * apply_proportional_protection(); the below-min/below-low gate is its\n"
     " * proportional protection. */\n"
     "static unsigned long get_nr_to_scan(struct lruvec *lruvec, struct scan_control *sc,\n"
     "\t\t\t\t    struct mem_cgroup *memcg, int swappiness)\n"
     "{\n"
     "\tunsigned long evictable;\n"
     "\n"
     "\tif (mem_cgroup_below_min(memcg) ||\n"
     "\t    (mem_cgroup_below_low(memcg) && !sc->memcg_low_reclaim))\n"
     "\t\treturn 0;\n"
     "\n"
     "\tevictable = lruvec_evictable_size(lruvec, swappiness);\n"
     "\n"
     "\t/* try to scrape all its memory if this memcg was deleted */\n"
     "\tif (!mem_cgroup_online(memcg))\n"
     "\t\treturn evictable;\n"
     "\n"
     "\treturn evictable >> sc->priority;\n"
     "}\n",
     T),
    # -- lru_gen_shrink_lruvec(): the restructured loop (163bc3d68c9f),
    #    MIN_LRU_BATCH batches (6e9be217a3ce), aging-then-continue with the
    #    root_reclaim()/should_age break (12316f7902f8).  The flusher block
    #    after the loop belongs to mglru_wake_flushers and is left for
    #    mglru_dirty_reclaim_rework to retire --
    (VMSCAN_C,
     "static void lru_gen_shrink_lruvec(struct lruvec *lruvec, struct scan_control *sc)\n"
     "{\n"
     "\tstruct blk_plug plug;\n"
     "\tbool need_aging = false;\n"
     "\tbool need_swapping = false;\n"
     "\tunsigned long scanned = 0;\n"
     "\tunsigned long reclaimed = sc->nr_reclaimed;\n"
     "\tDEFINE_MAX_SEQ(lruvec);\n"
     "\n"
     "\tlru_add_drain();\n"
     "\n"
     "\tblk_start_plug(&plug);\n"
     "\n"
     "\tset_mm_walk(lruvec_pgdat(lruvec));\n"
     "\n"
     "\twhile (true) {\n"
     "\t\tint delta;\n"
     "\t\tint swappiness = get_swappiness(lruvec, sc);\n"
     "\t\tunsigned long nr_to_scan;\n"
     "\n"
     "\t\tnr_to_scan = get_nr_to_scan(lruvec, sc, swappiness, &need_aging);\n"
     "\t\tif (!nr_to_scan)\n"
     "\t\t\tgoto done;\n"
     "\n"
     "\t\tdelta = evict_pages(lruvec, sc, swappiness, &need_swapping);\n"
     "\t\tif (!delta)\n"
     "\t\t\tgoto done;\n"
     "\n"
     "\t\tscanned += delta;\n"
     "\t\tif (scanned >= nr_to_scan)\n"
     "\t\t\tbreak;\n"
     "\n"
     "\t\tif (should_abort_scan(lruvec, max_seq, sc, need_swapping))\n"
     "\t\t\tbreak;\n"
     "\n"
     "\t\tcond_resched();\n"
     "\t}\n",
     "/* sailboat_mglru_reclaim_loop_rework: v7.2 12316f7902f8 -- upstream's\n"
     " * root_reclaim() predicate (direct reclaim and root-cgroup reclaim). */\n"
     "static bool root_reclaim(struct scan_control *sc)\n"
     "{\n"
     "\treturn !sc->target_mem_cgroup ||\n"
     "\t       mem_cgroup_is_root(sc->target_mem_cgroup);\n"
     "}\n"
     "\n"
     "static void lru_gen_shrink_lruvec(struct lruvec *lruvec, struct scan_control *sc)\n"
     "{\n"
     "\tstruct blk_plug plug;\n"
     "\tbool need_swapping = false;\n"
     "\tbool should_age = false;\n"
     "\tunsigned long nr_to_scan;\n"
     "\tunsigned long reclaimed = sc->nr_reclaimed;\n"
     "\tint swappiness = get_swappiness(lruvec, sc);\n"
     "\tstruct mem_cgroup *memcg = lruvec_memcg(lruvec);\n"
     "\n"
     "\tlru_add_drain();\n"
     "\n"
     "\tblk_start_plug(&plug);\n"
     "\n"
     "\tset_mm_walk(lruvec_pgdat(lruvec));\n"
     "\n"
     "\t/* sailboat_mglru_reclaim_loop_rework: v7.2 163bc3d68c9f +\n"
     "\t * 6e9be217a3ce + 12316f7902f8 -- the scan budget is computed once,\n"
     "\t * aging is an explicit loop step, and batches are MIN_LRU_BATCH. */\n"
     "\tnr_to_scan = get_nr_to_scan(lruvec, sc, memcg, swappiness);\n"
     "\twhile (nr_to_scan > 0) {\n"
     "\t\tunsigned long nr_batch;\n"
     "\t\tint delta;\n"
     "\t\tDEFINE_MAX_SEQ(lruvec);\n"
     "\n"
     "\t\tif (mem_cgroup_below_min(memcg))\n"
     "\t\t\tbreak;\n"
     "\n"
     "\t\tif (should_run_aging(lruvec, max_seq, sc, swappiness)) {\n"
     "\t\t\tshould_age = true;\n"
     "\t\t\t/*\n"
     "\t\t\t * kswapd leaves the node-wide aging to lru_gen_age_node(),\n"
     "\t\t\t * which owns the min_ttl/OOM policy on this baseline, and\n"
     "\t\t\t * stops this lruvec as it is low on cold pages -- the\n"
     "\t\t\t * pre-restructure behaviour, kept deliberately.\n"
     "\t\t\t */\n"
     "\t\t\tif (current_is_kswapd())\n"
     "\t\t\t\tbreak;\n"
     "\t\t\ttry_to_inc_max_seq(lruvec, max_seq, sc, swappiness, false);\n"
     "\t\t}\n"
     "\n"
     "\t\tnr_batch = min_t(unsigned long, nr_to_scan, MIN_LRU_BATCH);\n"
     "\t\tdelta = evict_pages(lruvec, sc, swappiness, nr_batch, &need_swapping);\n"
     "\t\tif (!delta)\n"
     "\t\t\tbreak;\n"
     "\n"
     "\t\tif (should_abort_scan(lruvec, max_seq, sc, need_swapping))\n"
     "\t\t\tbreak;\n"
     "\n"
     "\t\t/*\n"
     "\t\t * Root reclaim needs rotation when low on cold pages for better\n"
     "\t\t * fairness.  Cgroup reclaim gets fairness from the iterator.\n"
     "\t\t * This baseline has no memcg rotation, so this break just ends\n"
     "\t\t * the pass.\n"
     "\t\t */\n"
     "\t\tif (root_reclaim(sc) && should_age)\n"
     "\t\t\tbreak;\n"
     "\n"
     "\t\tnr_to_scan -= delta;\n"
     "\t\tcond_resched();\n"
     "\t}\n",
     T),
    # -- the loop tail reads should_age now, and the goto target goes away --
    (VMSCAN_C,
     "\t/* see the comment in lru_gen_age_node() */\n"
     "\tif (sc->nr_reclaimed - reclaimed >= MIN_LRU_BATCH && !need_aging)\n"
     "\t\tsc->memcgs_need_aging = false;\n"
     "done:\n"
     "\tclear_mm_walk();\n",
     "\t/* see the comment in lru_gen_age_node() */\n"
     "\tif (sc->nr_reclaimed - reclaimed >= MIN_LRU_BATCH && !should_age)\n"
     "\t\tsc->memcgs_need_aging = false;\n"
     "\n"
     "\tclear_mm_walk();\n",
     T),
]


def mglru_reclaim_loop_rework_apply(ctx):
    """v7.2 0491e9f75c15 series, loop half -- see the module docstring."""
    try:
        probe = ctx.read(VMSCAN_C)
    except FileNotFoundError:
        probe = ""
    if LOOP_PROBE in probe:
        return "already_present", "the reclaim-loop rework is already in vmscan.c"
    status, _results, detail = apply_steps(ctx, _LOOP_STEPS)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# mglru_dirty_reclaim_rework: 75d4c3f5fb98 + acd22fbb9f47 + f37d3708b676 +
# 32d87083ee97
# ---------------------------------------------------------------------------

_DIRTY_STEPS = [
    # -- sort_page(): whole-function rewrite in one step -- the dirty/writeback
    #    bookkeeping and the waiting-for-writeback divert go (75d4c3f5fb98) and
    #    the page_inc_gen() call sites lose the reclaiming argument
    #    (32d87083ee97).  One step, because the deletion-only forms would make
    #    each replacement block a substring of the pristine function (trap 1):
    #    dirty/writeback pages are isolated now and reactivated by the common
    #    shrink_page_list() routine --
    (VMSCAN_C,
     "static bool sort_page(struct lruvec *lruvec, struct page *page, struct scan_control *sc,\n"
     "\t\t       int tier_idx)\n"
     "{\n"
     "\tbool success;\n"
     "\tbool dirty, writeback;\n"
     "\tint gen = page_lru_gen(page);\n"
     "\tint type = page_is_file_lru(page);\n"
     "\tint zone = page_zonenum(page);\n"
     "\tint delta = thp_nr_pages(page);\n"
     "\tint refs = page_lru_refs(page);\n"
     "\tint tier = lru_tier_from_refs(refs);\n"
     "\tstruct lru_gen_struct *lrugen = &lruvec->lrugen;\n"
     "\n"
     "\tVM_WARN_ON_ONCE_PAGE(gen >= MAX_NR_GENS, page);\n"
     "\n"
     "\t/* unevictable */\n"
     "\tif (!page_evictable(page)) {\n"
     "\t\tsuccess = lru_gen_del_page(lruvec, page, true);\n"
     "\t\tVM_WARN_ON_ONCE_PAGE(!success, page);\n"
     "\t\tSetPageUnevictable(page);\n"
     "\t\tadd_page_to_lru_list(page, lruvec);\n"
     "\t\t__count_vm_events(UNEVICTABLE_PGCULLED, delta);\n"
     "\t\treturn true;\n"
     "\t}\n"
     "\n"
     "\t/* dirty lazyfree */\n"
     "\tif (type == LRU_GEN_FILE && PageAnon(page) && PageDirty(page)) {\n"
     "\t\tsuccess = lru_gen_del_page(lruvec, page, true);\n"
     "\t\tVM_WARN_ON_ONCE_PAGE(!success, page);\n"
     "\t\tSetPageSwapBacked(page);\n"
     "\t\tadd_page_to_lru_list_tail(page, lruvec);\n"
     "\t\treturn true;\n"
     "\t}\n"
     "\n"
     "\t/* promoted */\n"
     "\tif (gen != lru_gen_from_seq(lrugen->min_seq[type])) {\n"
     "\t\tlist_move(&page->lru, &lrugen->lists[gen][type][zone]);\n"
     "\t\treturn true;\n"
     "\t}\n"
     "\n"
     "\t/* protected */\n"
     "\tif (tier > tier_idx) {\n"
     "\t\tint hist = lru_hist_from_seq(lrugen->min_seq[type]);\n"
     "\n"
     "\t\tgen = page_inc_gen(lruvec, page, false);\n"
     "\t\tlist_move_tail(&page->lru, &lrugen->lists[gen][type][zone]);\n"
     "\n"
     "\t\tWRITE_ONCE(lrugen->protected[hist][type][tier],\n"
     "\t\t\t   lrugen->protected[hist][type][tier] + delta);\n"
     "\t\treturn true;\n"
     "\t}\n"
     "\n"
     "\t/* ineligible */\n"
     "\tif (zone > sc->reclaim_idx || skip_cma(page, sc)) {\n"
     "\t\tgen = page_inc_gen(lruvec, page, false);\n"
     "\t\tlist_move_tail(&page->lru, &lrugen->lists[gen][type][zone]);\n"
     "\t\treturn true;\n"
     "\t}\n"
     "\n"
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
     "\t    (type == LRU_GEN_FILE && dirty)) {\n"
     "\t\tgen = page_inc_gen(lruvec, page, true);\n"
     "\t\tlist_move(&page->lru, &lrugen->lists[gen][type][zone]);\n"
     "\t\treturn true;\n"
     "\t}\n"
     "\n"
     "\treturn false;\n"
     "}\n",
     "static bool sort_page(struct lruvec *lruvec, struct page *page, struct scan_control *sc,\n"
     "\t\t       int tier_idx)\n"
     "{\n"
     "\tbool success;\n"
     "\tint gen = page_lru_gen(page);\n"
     "\tint type = page_is_file_lru(page);\n"
     "\tint zone = page_zonenum(page);\n"
     "\tint delta = thp_nr_pages(page);\n"
     "\tint refs = page_lru_refs(page);\n"
     "\tint tier = lru_tier_from_refs(refs);\n"
     "\tstruct lru_gen_struct *lrugen = &lruvec->lrugen;\n"
     "\n"
     "\tVM_WARN_ON_ONCE_PAGE(gen >= MAX_NR_GENS, page);\n"
     "\n"
     "\t/* unevictable */\n"
     "\tif (!page_evictable(page)) {\n"
     "\t\tsuccess = lru_gen_del_page(lruvec, page, true);\n"
     "\t\tVM_WARN_ON_ONCE_PAGE(!success, page);\n"
     "\t\tSetPageUnevictable(page);\n"
     "\t\tadd_page_to_lru_list(page, lruvec);\n"
     "\t\t__count_vm_events(UNEVICTABLE_PGCULLED, delta);\n"
     "\t\treturn true;\n"
     "\t}\n"
     "\n"
     "\t/* dirty lazyfree */\n"
     "\tif (type == LRU_GEN_FILE && PageAnon(page) && PageDirty(page)) {\n"
     "\t\tsuccess = lru_gen_del_page(lruvec, page, true);\n"
     "\t\tVM_WARN_ON_ONCE_PAGE(!success, page);\n"
     "\t\tSetPageSwapBacked(page);\n"
     "\t\tadd_page_to_lru_list_tail(page, lruvec);\n"
     "\t\treturn true;\n"
     "\t}\n"
     "\n"
     "\t/* promoted */\n"
     "\tif (gen != lru_gen_from_seq(lrugen->min_seq[type])) {\n"
     "\t\tlist_move(&page->lru, &lrugen->lists[gen][type][zone]);\n"
     "\t\treturn true;\n"
     "\t}\n"
     "\n"
     "\t/* protected */\n"
     "\tif (tier > tier_idx) {\n"
     "\t\tint hist = lru_hist_from_seq(lrugen->min_seq[type]);\n"
     "\n"
     "\t\tgen = page_inc_gen(lruvec, page);\n"
     "\t\tlist_move_tail(&page->lru, &lrugen->lists[gen][type][zone]);\n"
     "\n"
     "\t\tWRITE_ONCE(lrugen->protected[hist][type][tier],\n"
     "\t\t\t   lrugen->protected[hist][type][tier] + delta);\n"
     "\t\treturn true;\n"
     "\t}\n"
     "\n"
     "\t/* ineligible */\n"
     "\tif (zone > sc->reclaim_idx || skip_cma(page, sc)) {\n"
     "\t\tgen = page_inc_gen(lruvec, page);\n"
     "\t\tlist_move_tail(&page->lru, &lrugen->lists[gen][type][zone]);\n"
     "\t\treturn true;\n"
     "\t}\n"
     "\n"
     "\t/*\n"
     "\t * sailboat_mglru_dirty_reclaim_rework: v7.2 75d4c3f5fb98 +\n"
     "\t * 32d87083ee97 -- no more special handling for dirty/writeback/\n"
     "\t * locked pages; the common shrink_page_list() routine reactivates\n"
     "\t * them like the classical LRU, which keeps their tier bits and\n"
     "\t * rotates them back to the tail once writeback is done.\n"
     "\t */\n"
     "\treturn false;\n"
     "}\n",
     T),
    # -- isolate_page(): PG_reclaim is the common routine's now
    #    (75d4c3f5fb98); the 5.15-only ClearPageReferenced() stays --
    (VMSCAN_C,
     "\t/* for shrink_page_list() */\n"
     "\tClearPageReclaim(page);\n"
     "\tClearPageReferenced(page);\n",
     "\t/* for shrink_page_list(); PG_reclaim is left to the common routine */\n"
     "\tClearPageReferenced(page);\n",
     T),
    # -- page_inc_gen(): the reclaiming/PG_reclaim dance goes (32d87083ee97) --
    (VMSCAN_C,
     "/* protect pages accessed multiple times through file descriptors */\n"
     "static int page_inc_gen(struct lruvec *lruvec, struct page *page, bool reclaiming)\n"
     "{\n",
     "/* protect pages accessed multiple times through file descriptors */\n"
     "static int page_inc_gen(struct lruvec *lruvec, struct page *page)\n"
     "{\n",
     T),
    (VMSCAN_C,
     "\t\tnew_flags = old_flags & ~(LRU_GEN_MASK | LRU_REFS_MASK | LRU_REFS_FLAGS);\n"
     "\t\tnew_flags |= (new_gen + 1UL) << LRU_GEN_PGOFF;\n"
     "\t\t/* for end_page_writeback() */\n"
     "\t\tif (reclaiming)\n"
     "\t\t\tnew_flags |= BIT(PG_reclaim);\n"
     "\t} while (!try_cmpxchg(&page->flags, &old_flags, new_flags));\n",
     "\t\tnew_flags = old_flags & ~(LRU_GEN_MASK | LRU_REFS_MASK | LRU_REFS_FLAGS);\n"
     "\t\tnew_flags |= (new_gen + 1UL) << LRU_GEN_PGOFF;\n"
     "\t} while (!try_cmpxchg(&page->flags, &old_flags, new_flags));\n",
     T),
    # -- the inc_min_seq() call site goes single-arg (32d87083ee97); the two
    #    sort_page() sites moved with the whole-function rewrite above --
    (VMSCAN_C,
     "\t\t\tnew_gen = page_inc_gen(lruvec, page, false);\n"
     "\t\t\tlist_move_tail(&page->lru, &lrugen->lists[new_gen][type][zone]);\n",
     "\t\t\tnew_gen = page_inc_gen(lruvec, page);\n"
     "\t\t\tlist_move_tail(&page->lru, &lrugen->lists[new_gen][type][zone]);\n",
     T),
    # -- scan_pages(): the file_taken accumulation goes with the old flusher
    #    accounting (f37d3708b676) --
    (VMSCAN_C,
     "\t__count_vm_events(PGSCAN_ANON + type, isolated);\n"
     "\tif (type == LRU_GEN_FILE)\n"
     "\t\tsc->nr.file_taken += isolated;\n"
     "\n"
     "\t*isolatedp = isolated;\n",
     "\t__count_vm_events(PGSCAN_ANON + type, isolated);\n"
     "\n"
     "\t*isolatedp = isolated;\n",
     T),
    # -- evict_pages(): per-batch flusher wakeup replaces both the unqueued
    #    accumulation and the once-per-loop block (f37d3708b676).  The
    #    reclaim_throttle(VMSCAN_THROTTLE_WRITEBACK) tail is dropped exactly
    #    as mglru_wake_flushers dropped it: 5.15 has neither symbol.  The
    #    condition body is the tree's own shrink_inactive_list() idiom --
    (VMSCAN_C,
     "retry:\n"
     "\treclaimed = shrink_page_list(&list, pgdat, sc, &stat, false);\n"
     "\tsc->nr.unqueued_dirty += stat.nr_unqueued_dirty;\n"
     "\tsc->nr_reclaimed += reclaimed;\n"
     "\n"
     "\tlist_for_each_entry_safe_reverse(page, next, &list, lru) {\n",
     "retry:\n"
     "\treclaimed = shrink_page_list(&list, pgdat, sc, &stat, false);\n"
     "\tsc->nr_reclaimed += reclaimed;\n"
     "\n"
     "\t/*\n"
     "\t * If too many file cache in the coldest generation can't be evicted\n"
     "\t * due to being dirty, wake up the flusher.\n"
     "\t */\n"
     "\tif (stat.nr_unqueued_dirty == isolated) {\n"
     "\t\twakeup_flusher_threads(WB_REASON_VMSCAN);\n"
     "\t}\n"
     "\n"
     "\tlist_for_each_entry_safe_reverse(page, next, &list, lru) {\n",
     T),
    # -- the once-per-loop flusher block goes (f37d3708b676).  The anchor
    #    starts inside mglru_reclaim_loop_rework's loop tail (the budget
    #    decrement) so the replacement block is not a pristine substring --
    (VMSCAN_C,
     "\t\tnr_to_scan -= delta;\n"
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
     "\t\tnr_to_scan -= delta;\n"
     "\t\tcond_resched();\n"
     "\t}\n"
     "\n"
     "\t/* see the comment in lru_gen_age_node() */\n",
     T),
]


def mglru_dirty_reclaim_rework_apply(ctx):
    """v7.2 0491e9f75c15 series, dirty/writeback half -- see the docstring."""
    try:
        probe = ctx.read(VMSCAN_C)
    except FileNotFoundError:
        probe = ""
    if DIRTY_PROBE in probe:
        return "already_present", "the dirty-reclaim rework is already in vmscan.c"
    status, _results, detail = apply_steps(ctx, _DIRTY_STEPS)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ---------------------------------------------------------------------------
# mglru_prefault_accessed_placement: 6cbdd9726fb5
# ---------------------------------------------------------------------------

_PLACEMENT_STEPS = [
    # -- mm_inline.h: referenced pages join the second oldest bucket, as
    #    upstream's lru_gen_folio_seq() formula does --
    (MM_INLINE_H,
     "\tif (PageActive(page))\n"
     "\t\tseq = lrugen->max_seq;\n"
     "\telse if ((type == LRU_GEN_ANON && !PageSwapCache(page)) ||\n"
     "\t\t (PageReclaim(page) &&\n"
     "\t\t  (PageDirty(page) || PageWriteback(page))))\n"
     "\t\tseq = lrugen->min_seq[type] + 1;\n"
     "\telse\n"
     "\t\tseq = lrugen->min_seq[type];\n",
     "\tif (PageActive(page))\n"
     "\t\tseq = lrugen->max_seq;\n"
     "\telse if ((type == LRU_GEN_ANON && !PageSwapCache(page)) ||\n"
     "\t\t (PageReclaim(page) &&\n"
     "\t\t  (PageDirty(page) || PageWriteback(page))))\n"
     "\t\tseq = lrugen->min_seq[type] + 1;\n"
     "\t/*\n"
     "\t * sailboat_mglru_prefault_accessed_placement: v7.2 6cbdd9726fb5 --\n"
     "\t * prefaulted file pages are mark_page_accessed()d on fault (see\n"
     "\t * lru_cache_add()), and PG_referenced places them one generation\n"
     "\t * up, like folio_test_referenced() does in upstream's\n"
     "\t * lru_gen_folio_seq().\n"
     "\t */\n"
     "\telse if (PageReferenced(page))\n"
     "\t\tseq = lrugen->min_seq[type] + 1;\n"
     "\telse\n"
     "\t\tseq = lrugen->min_seq[type];\n",
     T),
    # -- swap.c lru_cache_add(): refaulted workingset pages still activate,
    #    prefaulted file pages get mark_page_accessed() instead --
    (SWAP_C,
     "\ttrace_android_vh_lru_cache_add(page);\n"
     "\t/* see the comment in lru_gen_add_page() */\n"
     "\tif (lru_gen_enabled() && !PageUnevictable(page) &&\n"
     "\t    lru_gen_in_fault() && !(current->flags & PF_MEMALLOC)) {\n"
     "\t\tbool bypass = false;\n"
     "\n"
     "\t\ttrace_android_vh_lru_cache_add_page_activate(page, &bypass);\n"
     "\t\tif (!bypass)\n"
     "\t\t\tSetPageActive(page);\n"
     "\t}\n",
     "\ttrace_android_vh_lru_cache_add(page);\n"
     "\t/*\n"
     "\t * sailboat_mglru_prefault_accessed_placement: v7.2 6cbdd9726fb5 --\n"
     "\t * for refaulted workingset pages, set PG_active so they can be\n"
     "\t * added to active generations.  For prefaulted file pages,\n"
     "\t * mark_page_accessed() sets PG_referenced so lru_gen_add_page()\n"
     "\t * places them into the second oldest generation.\n"
     "\t */\n"
     "\tif (lru_gen_enabled() && !PageUnevictable(page) &&\n"
     "\t    lru_gen_in_fault() && !(current->flags & PF_MEMALLOC)) {\n"
     "\t\tif (PageWorkingset(page)) {\n"
     "\t\t\tbool bypass = false;\n"
     "\n"
     "\t\t\ttrace_android_vh_lru_cache_add_page_activate(page, &bypass);\n"
     "\t\t\tif (!bypass)\n"
     "\t\t\t\tSetPageActive(page);\n"
     "\t\t} else if (!PageReferenced(page)) {\n"
     "\t\t\tmark_page_accessed(page);\n"
     "\t\t}\n"
     "\t}\n",
     T),
    # -- workingset.c lru_gen_refault(): WORKINGSET_ACTIVATE counts only the
    #    workingset refaults that really activate --
    (WORKINGSET_C,
     "\tatomic_long_add(delta, &lrugen->refaulted[hist][type][tier]);\n"
     "\tmod_lruvec_state(lruvec, WORKINGSET_ACTIVATE_BASE + type, delta);\n"
     "\n"
     "\t/*\n"
     "\t * Count the following two cases as stalls:\n"
     "\t * 1. For pages accessed through page tables, hotter pages pushed out\n"
     "\t *    hot pages which refaulted immediately.\n"
     "\t * 2. For pages accessed multiple times through file descriptors,\n"
     "\t *    numbers of accesses might have been out of the range.\n"
     "\t */\n"
     "\tif (lru_gen_in_fault() || refs == BIT(LRU_REFS_WIDTH)) {\n"
     "\t\tSetPageWorkingset(page);\n"
     "\t\tmod_lruvec_state(lruvec, WORKINGSET_RESTORE_BASE + type, delta);\n"
     "\t}\n",
     "\tatomic_long_add(delta, &lrugen->refaulted[hist][type][tier]);\n"
     "\n"
     "\t/*\n"
     "\t * Count the following two cases as stalls:\n"
     "\t * 1. For pages accessed through page tables, hotter pages pushed out\n"
     "\t *    hot pages which refaulted immediately.\n"
     "\t * 2. For pages accessed multiple times through file descriptors,\n"
     "\t *    numbers of accesses might have been out of the range.\n"
     "\t */\n"
     "\tif (lru_gen_in_fault() || refs == BIT(LRU_REFS_WIDTH)) {\n"
     "\t\tSetPageWorkingset(page);\n"
     "\t\t/*\n"
     "\t\t * sailboat_mglru_prefault_accessed_placement: v7.2 6cbdd9726fb5\n"
     "\t\t * -- see lru_cache_add(), where SetPageActive() happens for\n"
     "\t\t * workingset pages only; non-workingset prefaults no longer\n"
     "\t\t * count as activations.\n"
     "\t\t */\n"
     "\t\tif (lru_gen_in_fault())\n"
     "\t\t\tmod_lruvec_state(lruvec, WORKINGSET_ACTIVATE_BASE + type, delta);\n"
     "\t\tmod_lruvec_state(lruvec, WORKINGSET_RESTORE_BASE + type, delta);\n"
     "\t}\n",
     T),
]


def mglru_prefault_accessed_placement_apply(ctx):
    """v7.2 6cbdd9726fb5 -- see the module docstring."""
    try:
        probe = ctx.read(MM_INLINE_H)
    except FileNotFoundError:
        probe = ""
    if PLACEMENT_PROBE in probe:
        return "already_present", "the prefault placement rework is already applied"
    status, _results, detail = apply_steps(ctx, _PLACEMENT_STEPS)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(patch_group_cls):
    """The three groups, in dependency order (loop -> dirty -> placement)."""
    return [
        patch_group_cls(
            "mglru_reclaim_loop_rework",
            "MGLRU v7.2: reclaim loop restructure -- one-shot scan budget, explicit "
            "aging step, MIN_LRU_BATCH batches, exact isolation accounting (series "
            "cover 0491e9f75c15)",
            ["790d3abeca09/aa6ef5b159dc/163bc3d68c9f/6e9be217a3ce/3a72e078b4a3/"
             "16b475d2ac3c/12316f7902f8 (v7.2)"],
            [VMSCAN_C],
            mglru_reclaim_loop_rework_apply,
        ),
        patch_group_cls(
            "mglru_dirty_reclaim_rework",
            "MGLRU v7.2: dirty/writeback pages go through the common "
            "shrink_page_list() reactivation with per-batch flusher wakeup "
            "(75d4c3f5fb98/acd22fbb9f47/f37d3708b676/32d87083ee97)",
            ["75d4c3f5fb98/acd22fbb9f47/f37d3708b676/32d87083ee97 (v7.2)"],
            [VMSCAN_C],
            mglru_dirty_reclaim_rework_apply,
        ),
        patch_group_cls(
            "mglru_prefault_accessed_placement",
            "MGLRU v7.2: prefaulted file pages are mark_page_accessed()d into the "
            "second oldest generation, refaulted workingset pages still activate "
            "(6cbdd9726fb5)",
            ["6cbdd9726fb5 (v7.2)"],
            [MM_INLINE_H, SWAP_C, WORKINGSET_C],
            mglru_prefault_accessed_placement_apply,
        ),
    ]
