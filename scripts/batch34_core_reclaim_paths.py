"""Batch 34: the memory-reclaim path -- proactive reclaim's batch fidelity,
its swappiness argument, the suspend abort, and the lru_add drain.

Six upstream commits, all on the ``memory.reclaim`` line.  Five are the ones
this batch was scoped to; ``410abb20acae`` is the sixth and is carried because
the fifth commit's hunk *references* the two constants it adds (see group
``reclaim_swappiness_defines`` below) -- without it the port would have to
substitute literals for upstream's own names.

What the chain is, in upstream order:

  * ``0388536ac291`` (v6.6) caps one ``try_to_free_mem_cgroup_pages()`` call
    in ``memory_reclaim()`` at ``SWAP_CLUSTER_MAX``.  Before it the reclaimer
    keeps reclaiming towards the *original* ``sc->nr_to_reclaim`` after an
    inner loop already reclaimed part of the request, so the overreclaim is
    anywhere in [request, 2 * request).
  * ``287d5fedb377`` (v6.9, ``Fixes: 0388536ac291``) replaces the fixed 32-page
    cap with a decaying batch (``(nr_to_reclaim - nr_reclaimed) / 4``): the
    32-page cap cost more in reclaim start/stop cycles than it saved in error
    (13742 pages/sec vs 67352 on a full root-cgroup reclaim; the numbers are
    upstream's).  The two are inseparable -- landing either alone is a
    regression, which is why both are in this batch, and why the pair is
    registered in this order.
  * ``410abb20acae`` / ``68cd9050d871`` (v6.11, one series) add the
    ``swappiness=`` nested key to ``memory.reclaim`` and the
    ``MIN_SWAPPINESS``/``MAX_SWAPPINESS`` defines the parser validates against.
    The swappiness value travels to ``get_scan_count()``/``get_swappiness()``
    through a new ``sc_swappiness()`` accessor rather than through
    ``mem_cgroup_swappiness()`` directly.
  * ``dc37771a43d4`` (v7.2, ``Fixes: 287d5fedb377``) lets the PM freezer
    interrupt a proactive reclaim: the MGLRU inner loop gains the signal
    check, and the handler returns ``-ERESTARTSYS`` instead of ``-EINTR`` so
    the syscall restarts transparently after resume.  It sits after the
    decaying-batch group because the decaying batch is *why* the inner loop
    can run for seconds -- that commit is in its own Fixes: line.
  * ``9669b87065a6`` (v7.2) is the other end of the same path: a page whose
    last reference is the ``lru_add`` batch's own is dead before it reaches
    the LRU, so adding it only to remove it again costs two lruvec lock
    acquisitions.  Independent of the groups above (mm/swap.c).

Every target of the five ``memory.reclaim`` commits lives in text this module
generates itself (``memcg_memory_reclaim`` creates ``memory_reclaim()`` and
rewrites all seven ``try_to_free_mem_cgroup_pages()`` call sites, because
android13-5.15 has neither).  That makes this batch the third instance of the
``docs/group_recipe.md`` trap-5 shape -- after Batch 21/24 -- and it is a
*chain* rather than a single later edit: the same call site is rewritten four
times (0388536ac291, then 287d5fedb377, then 68cd9050d871).  So
``memcg_memory_reclaim`` and each superseded group probe for content that
survives its successor (``apply_steps`` is transactional, so one probe is
enough), and the registry keeps them in dependency order.  See
``PROBE_*`` below and the registration note in ``abk_stable_core.py``.

Nothing here is a new CONFIG gate and nothing here reads a symbol that lives
inside one: the only added line inside an existing gate is the signal check in
``should_abort_scan()``, which is inside ``#ifdef CONFIG_LRU_GEN`` already.

One 5.15-side caveat on the swappiness group: every supported baseline calls
``trace_android_vh_tune_swappiness()``, the ACK hook that is Android's own
swappiness override point -- from ``get_scan_count()`` on all four, and from
``get_swappiness()`` on 194 and the lts branch as well.  Upstream's edit moves
the *source* of the value (``mem_cgroup_swappiness()`` -> ``sc_swappiness()``)
and the hook then tunes whatever that holds, so on an ACK tree the vendor hook
still has the last word over a proactive ``swappiness=`` on the scan-balance
path.  That is the baseline's own policy and this port does not fight it; it is
also why the ``get_swappiness()`` step has two shapes.

Sources (verbatim, in ``research/upstream-5.15.y/patches/``):
``0388536ac291``, ``287d5fedb377``, ``410abb20acae``, ``68cd9050d871``,
``dc37771a43d4``, ``9669b87065a6``.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

MEMCONTROL_C = "mm/memcontrol.c"
VMSCAN_C = "mm/vmscan.c"
SWAP_H = "include/linux/swap.h"
CGROUP_V2_RST = "Documentation/admin-guide/cgroup-v2.rst"
SWAP_C = "mm/swap.c"

T = True

# ---------------------------------------------------------------------------
# Probes.  A group whose output a later group rewrites cannot rely on
# replace_once's new-block check (that text is gone by the second pass) nor on
# its `old` anchor (also gone), so it stops on a probe instead.  Each probe is
# content that outlives every later group in the chain.
# ---------------------------------------------------------------------------

# The batch_size declaration 287d5fedb377 adds, and which no later group
# touches: it is what survives once the call site has moved on.
DECAYING_BATCH_DECL = "unsigned long batch_size = (nr_to_reclaim - nr_reclaimed) / 4;"
# 0388536ac291's own target form.  It is in the tree only between that group
# and 287d5fedb377 -- and it is the form the group must also accept if a tree
# ever arrives carrying 0388536ac291 upstream-first without its follow-up.
SWAP_CLUSTER_BATCH = "min(nr_to_reclaim - nr_reclaimed, SWAP_CLUSTER_MAX),"
# 68cd9050d871's parser: the marker, the enum, and the argument it builds.
SWAPPINESS_ARG = "swappiness == -1 ? NULL : &swappiness"
# dc37771a43d4's two halves.
SUSPEND_ABORT_GUARD = "if (unlikely(sc->proactive && signal_pending(current)))"
SUSPEND_ABORT_ERRNO = "return -ERESTARTSYS;"
# 9669b87065a6's filter.
DEAD_PAGE_FILTER = "if (page_ref_freeze(page, 1)) {"

# The call site, before any of this batch's groups rewrite it -- text the
# memcg_memory_reclaim group generates.  Used as an anchor by the first group
# of the chain.
_CALL_OLD = (
    "\t\treclaimed = try_to_free_mem_cgroup_pages(memcg,\n"
    "\t\t\t\t\t\tnr_to_reclaim - nr_reclaimed,\n"
    "\t\t\t\t\t\tGFP_KERNEL, reclaim_options);\n"
)

# memcg_memory_reclaim's own marker on the handler it generates.  That group
# stops on it (trap 5): the call site inside the handler is rewritten three
# times by this batch, so on a second pass the handler's `new` block no longer
# matches while its `old` anchor still does -- which would append a second copy
# of the whole function.  The marker sits on the handler, is written by that
# group alone, and is not touched by any later group.
MEMORY_RECLAIM_MARKER = ("/* ABK stable_515_backport: per-memcg proactive "
                         "reclaim (android14-6.1 memory.reclaim). */")

# ===========================================================================
# 0388536ac291 -- mm:vmscan: fix inaccurate reclaim during proactive reclaim
# ===========================================================================

_C1_NEW = (
    "\t\treclaimed = try_to_free_mem_cgroup_pages(memcg,\n"
    "\t\t\t\t\tmin(nr_to_reclaim - nr_reclaimed, SWAP_CLUSTER_MAX),\n"
    "\t\t\t\t\tGFP_KERNEL, reclaim_options);\n"
)


def build_steps_batch_fidelity():
    """Upstream 0388536ac291 verbatim: one hunk, the call's first argument."""
    return [(MEMCONTROL_C, _CALL_OLD, _C1_NEW, T)]


def _batch_fidelity_apply(ctx):
    try:
        text = ctx.read(MEMCONTROL_C)
    except FileNotFoundError:
        return "blocked_by_shape", MEMCONTROL_C + ": file absent"
    # Trap 5: 287d5fedb377 replaces this group's only output, and 68cd9050d871
    # then replaces *its* output.  Stop on either successor's payload.
    if SWAP_CLUSTER_BATCH in text or DECAYING_BATCH_DECL in text:
        return "already_present", (
            "the proactive-reclaim batch is already capped and decaying")
    status, _results, detail = apply_steps(ctx, build_steps_batch_fidelity())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ===========================================================================
# 287d5fedb377 -- mm: memcg: use larger batches for proactive reclaim
# ===========================================================================

_BATCH_DECL_OLD = (
    "\treclaim_options\t= MEMCG_RECLAIM_MAY_SWAP | MEMCG_RECLAIM_PROACTIVE;\n"
    "\twhile (nr_reclaimed < nr_to_reclaim) {\n"
    "\t\tunsigned long reclaimed;\n"
)

_BATCH_DECL_NEW = (
    "\treclaim_options\t= MEMCG_RECLAIM_MAY_SWAP | MEMCG_RECLAIM_PROACTIVE;\n"
    "\twhile (nr_reclaimed < nr_to_reclaim) {\n"
    "\t\t/* Will converge on zero, but reclaim enforces a minimum */\n"
    "\t\tunsigned long batch_size = (nr_to_reclaim - nr_reclaimed) / 4;\n"
    "\t\tunsigned long reclaimed;\n"
)

_C2_CALL_OLD = (
    "\t\treclaimed = try_to_free_mem_cgroup_pages(memcg,\n"
    "\t\t\t\t\tmin(nr_to_reclaim - nr_reclaimed, SWAP_CLUSTER_MAX),\n"
    "\t\t\t\t\tGFP_KERNEL, reclaim_options);\n"
)

_C2_CALL_NEW = (
    "\t\treclaimed = try_to_free_mem_cgroup_pages(memcg,\n"
    "\t\t\t\t\tbatch_size, GFP_KERNEL, reclaim_options);\n"
)


def build_steps_decaying_batches():
    """Upstream 287d5fedb377 verbatim: the batch declaration, then the call.

    Both halves, and both required: a tree that learned the declaration but
    kept ``nr_to_reclaim - nr_reclaimed`` as the argument would still
    overreclaim, and one that learned the argument without the declaration
    would not compile.
    """
    return [
        (MEMCONTROL_C, _BATCH_DECL_OLD, _BATCH_DECL_NEW, T),
        (MEMCONTROL_C, _C2_CALL_OLD, _C2_CALL_NEW, T),
    ]


def _decaying_batches_apply(ctx):
    try:
        text = ctx.read(MEMCONTROL_C)
    except FileNotFoundError:
        return "blocked_by_shape", MEMCONTROL_C + ": file absent"
    # Trap 5 again: 68cd9050d871 rewrites the call this group produces.  The
    # declaration it adds is untouched by that group and survives to the end.
    if DECAYING_BATCH_DECL in text:
        return "already_present", ("the proactive-reclaim batch is already decaying")
    # A tree that carries 0388536ac291's cap but not this follow-up is the
    # regression this pair exists to avoid: refuse rather than apply half of it.
    status, _results, detail = apply_steps(ctx, build_steps_decaying_batches())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ===========================================================================
# 410abb20acae -- mm: add defines for min/max swappiness
# ===========================================================================

_DEFINES_OLD = (
    "#define MEMCG_RECLAIM_MAY_SWAP (1 << 1)\n"
    "#define MEMCG_RECLAIM_PROACTIVE (1 << 2)\n"
)

_DEFINES_NEW = _DEFINES_OLD + (
    "#define MIN_SWAPPINESS 0\n"
    "#define MAX_SWAPPINESS 200\n"
)

_SWAPPINESS_LITERAL_FP_OLD = "\tfp = (200 - swappiness) * (total_cost + 1);\n"
_SWAPPINESS_LITERAL_FP_NEW = "\tfp = (MAX_SWAPPINESS - swappiness) * (total_cost + 1);\n"

_SWAPPINESS_WRITE_OLD = (
    "\tstruct mem_cgroup *memcg = mem_cgroup_from_css(css);\n"
    "\n"
    "\tif (val > 200)\n"
    "\t\treturn -EINVAL;\n"
)

_SWAPPINESS_WRITE_NEW = (
    "\tstruct mem_cgroup *memcg = mem_cgroup_from_css(css);\n"
    "\n"
    "\tif (val > MAX_SWAPPINESS)\n"
    "\t\treturn -EINVAL;\n"
)


def build_steps_swappiness_defines():
    """Upstream 410abb20acae verbatim, adapted to the 5.15 call sites.

    The patch is the whole of that commit's reachable payload: the two
    defines, the ``get_scan_count()`` fraction, and the cgroup-v1
    ``memory.swappiness`` bound.  (The MGLRU reach of the same commit --
    ``get_type_to_scan()``, ``isolate_folios()``, ``lru_gen_run_cmd()`` --
    also has no first ``200`` literal here: it is inside ``CONFIG_LRU_GEN``
    and is left byte-identical, the same treatment this module gives every
    other MGLRU-only hunk.)

    The defines are anchored on the pair ``memcg_memory_reclaim`` installs,
    which is exactly where upstream put them (between ``MEMCG_RECLAIM_*`` and
    the ``try_to_free_mem_cgroup_pages()`` prototype).
    """
    return [
        (SWAP_H, _DEFINES_OLD, _DEFINES_NEW, T),
        (VMSCAN_C, _SWAPPINESS_LITERAL_FP_OLD, _SWAPPINESS_LITERAL_FP_NEW, T),
        (MEMCONTROL_C, _SWAPPINESS_WRITE_OLD, _SWAPPINESS_WRITE_NEW, T),
    ]


def _swappiness_defines_apply(ctx):
    status, _results, detail = apply_steps(ctx, build_steps_swappiness_defines())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ===========================================================================
# 68cd9050d871 -- mm: add swappiness= arg to memory.reclaim
# ===========================================================================

_PROTO_OLD = (
    "extern unsigned long try_to_free_mem_cgroup_pages(struct mem_cgroup *memcg,\n"
    "\t\t\t\t\t\t  unsigned long nr_pages,\n"
    "\t\t\t\t\t\t  gfp_t gfp_mask,\n"
    "\t\t\t\t\t\t  unsigned int reclaim_options);\n"
)

_PROTO_NEW = (
    "extern unsigned long try_to_free_mem_cgroup_pages(struct mem_cgroup *memcg,\n"
    "\t\t\t\t\t\t  unsigned long nr_pages,\n"
    "\t\t\t\t\t\t  gfp_t gfp_mask,\n"
    "\t\t\t\t\t\t  unsigned int reclaim_options,\n"
    "\t\t\t\t\t\t  int *swappiness);\n"
)

_SC_FIELD_OLD = (
    "\tunsigned long\tanon_cost;\n"
    "\tunsigned long\tfile_cost;\n"
    "\n"
    "\t/* Can active pages be deactivated as part of reclaim? */\n"
)

_SC_FIELD_NEW = (
    "\tunsigned long\tanon_cost;\n"
    "\tunsigned long\tfile_cost;\n"
    "\n"
    "#ifdef CONFIG_MEMCG\n"
    "\t/* Swappiness value for proactive reclaim. Always use sc_swappiness()! */\n"
    "\tint *proactive_swappiness;\n"
    "#endif\n"
    "\n"
    "\t/* Can active pages be deactivated as part of reclaim? */\n"
)

# The two sc_swappiness() definitions, one per side of the file's CONFIG_MEMCG
# split.  Anchored on the branch tails so each lands in the branch whose
# mem_cgroup_swappiness() it may call.
_SC_HELPER_OLD = (
    "static bool writeback_throttling_sane(struct scan_control *sc)\n"
    "{\n"
    "\tif (!cgroup_reclaim(sc))\n"
    "\t\treturn true;\n"
    "#ifdef CONFIG_CGROUP_WRITEBACK\n"
    "\tif (cgroup_subsys_on_dfl(memory_cgrp_subsys))\n"
    "\t\treturn true;\n"
    "#endif\n"
    "\treturn false;\n"
    "}\n"
    "#else\n"
)

_SC_HELPER_NEW = (
    "static bool writeback_throttling_sane(struct scan_control *sc)\n"
    "{\n"
    "\tif (!cgroup_reclaim(sc))\n"
    "\t\treturn true;\n"
    "#ifdef CONFIG_CGROUP_WRITEBACK\n"
    "\tif (cgroup_subsys_on_dfl(memory_cgrp_subsys))\n"
    "\t\treturn true;\n"
    "#endif\n"
    "\treturn false;\n"
    "}\n"
    "\n"
    "static int sc_swappiness(struct scan_control *sc, struct mem_cgroup *memcg)\n"
    "{\n"
    "\tif (sc->proactive && sc->proactive_swappiness)\n"
    "\t\treturn *sc->proactive_swappiness;\n"
    "\treturn mem_cgroup_swappiness(memcg);\n"
    "}\n"
    "#else\n"
)

_SC_HELPER_NO_MEMCG_OLD = (
    "static bool writeback_throttling_sane(struct scan_control *sc)\n"
    "{\n"
    "\treturn true;\n"
    "}\n"
    "#endif\n"
)

_SC_HELPER_NO_MEMCG_NEW = (
    "static bool writeback_throttling_sane(struct scan_control *sc)\n"
    "{\n"
    "\treturn true;\n"
    "}\n"
    "\n"
    "static int sc_swappiness(struct scan_control *sc, struct mem_cgroup *memcg)\n"
    "{\n"
    "\treturn READ_ONCE(vm_swappiness);\n"
    "}\n"
    "#endif\n"
)

_GET_SCAN_COUNT_OLD = (
    "	struct mem_cgroup *memcg = lruvec_memcg(lruvec);\n"
    "	unsigned long anon_cost, file_cost, total_cost;\n"
    "	int swappiness = mem_cgroup_swappiness(memcg);\n"
)

_GET_SCAN_COUNT_NEW = (
    "	struct mem_cgroup *memcg = lruvec_memcg(lruvec);\n"
    "	unsigned long anon_cost, file_cost, total_cost;\n"
    "	int swappiness = sc_swappiness(sc, memcg);\n"
)

_GET_SWAPPINESS_OLD = (
    "	if (!can_demote(pgdat->node_id, sc) &&\n"
    "\t\tmem_cgroup_get_nr_swap_pages(memcg) <= 0)\n"
    "\t\treturn 0;\n"
    "\n"
    "\treturn mem_cgroup_swappiness(memcg);\n"
    "}\n"
)

_GET_SWAPPINESS_NEW = (
    "	if (!can_demote(pgdat->node_id, sc) &&\n"
    "\t\tmem_cgroup_get_nr_swap_pages(memcg) <= 0)\n"
    "\t\treturn 0;\n"
    "\n"
    "\treturn sc_swappiness(sc, memcg);\n"
    "}\n"
)

# 194 and the lts branch carry an ACK vendor hook inside get_swappiness() as
# well: there the value is computed into a local, offered to the hook, then
# returned.  Same edit, one line up -- and the hook keeps tuning a *proactive*
# request's swappiness, which is what it is for.  (All four baselines call the
# hook from get_scan_count(), so the call alone does not identify the shape;
# that is why the selector below matches the block, not the hook.)
_VENDOR_SWAPPINESS_HOOK = "trace_android_vh_tune_swappiness(&swappiness);"

_GET_SWAPPINESS_HOOK_OLD = (
    "	if (!can_demote(pgdat->node_id, sc) &&\n"
    "\t\tmem_cgroup_get_nr_swap_pages(memcg) <= 0)\n"
    "\t\treturn 0;\n"
    "\n"
    "\tswappiness = mem_cgroup_swappiness(memcg);\n"
    "\ttrace_android_vh_tune_swappiness(&swappiness);\n"
)

_GET_SWAPPINESS_HOOK_NEW = (
    "	if (!can_demote(pgdat->node_id, sc) &&\n"
    "\t\tmem_cgroup_get_nr_swap_pages(memcg) <= 0)\n"
    "\t\treturn 0;\n"
    "\n"
    "\tswappiness = sc_swappiness(sc, memcg);\n"
    "\ttrace_android_vh_tune_swappiness(&swappiness);\n"
)

_TTFMCP_OLD = (
    "unsigned long try_to_free_mem_cgroup_pages(struct mem_cgroup *memcg,\n"
    "\t\t\t\t\t   unsigned long nr_pages,\n"
    "\t\t\t\t\t   gfp_t gfp_mask,\n"
    "\t\t\t\t\t   unsigned int reclaim_options)\n"
    "{\n"
    "\tunsigned long nr_reclaimed;\n"
    "\tunsigned int noreclaim_flag;\n"
    "\tstruct scan_control sc = {\n"
    "\t\t.nr_to_reclaim = max(nr_pages, SWAP_CLUSTER_MAX),\n"
)

_TTFMCP_NEW = (
    "unsigned long try_to_free_mem_cgroup_pages(struct mem_cgroup *memcg,\n"
    "\t\t\t\t\t   unsigned long nr_pages,\n"
    "\t\t\t\t\t   gfp_t gfp_mask,\n"
    "\t\t\t\t\t   unsigned int reclaim_options,\n"
    "\t\t\t\t\t   int *swappiness)\n"
    "{\n"
    "\tunsigned long nr_reclaimed;\n"
    "\tunsigned int noreclaim_flag;\n"
    "\tstruct scan_control sc = {\n"
    "\t\t.nr_to_reclaim = max(nr_pages, SWAP_CLUSTER_MAX),\n"
    "\t\t.proactive_swappiness = swappiness,\n"
)

_INCLUDE_OLD = (
    "#include <linux/fs.h>\n"
    "#include <linux/seq_file.h>\n"
    "#include <linux/vmpressure.h>\n"
)

_INCLUDE_NEW = (
    "#include <linux/fs.h>\n"
    "#include <linux/seq_file.h>\n"
    "#include <linux/parser.h>\n"
    "#include <linux/vmpressure.h>\n"
)

# The six call sites that pass NULL: every other try_to_free_mem_cgroup_pages()
# caller in the tree.  Without them the extra parameter would silently mean
# "no swappiness override" only by accident -- the prototypes would not match
# and the build would fail, which is the point of the pairing.
_CALL_RECLAIM_HIGH_OLD = (
    "\t\tnr_reclaimed += try_to_free_mem_cgroup_pages(memcg, nr_pages,\n"
    "\t\t\t\t\t\t\t     gfp_mask,\n"
    "\t\t\t\t\t\t\t     MEMCG_RECLAIM_MAY_SWAP);\n"
)

_CALL_RECLAIM_HIGH_NEW = (
    "\t\tnr_reclaimed += try_to_free_mem_cgroup_pages(memcg, nr_pages,\n"
    "\t\t\t\t\t\t\t     gfp_mask,\n"
    "\t\t\t\t\t\t\t     MEMCG_RECLAIM_MAY_SWAP,\n"
    "\t\t\t\t\t\t\t     NULL);\n"
)

_CALL_TRY_CHARGE_OLD = (
    "\tnr_reclaimed = try_to_free_mem_cgroup_pages(mem_over_limit, nr_pages,\n"
    "\t\t\t\t\t\t    gfp_mask, reclaim_options);\n"
)

_CALL_TRY_CHARGE_NEW = (
    "\tnr_reclaimed = try_to_free_mem_cgroup_pages(mem_over_limit, nr_pages,\n"
    "\t\t\t\t\t\t    gfp_mask, reclaim_options,\n"
    "\t\t\t\t\t\t    NULL);\n"
)

_CALL_RESIZE_MAX_OLD = (
    "\t\tif (!try_to_free_mem_cgroup_pages(memcg, 1, GFP_KERNEL,\n"
    "\t\t\t\t\tmemsw ? 0 : MEMCG_RECLAIM_MAY_SWAP)) {\n"
)

_CALL_RESIZE_MAX_NEW = (
    "\t\tif (!try_to_free_mem_cgroup_pages(memcg, 1, GFP_KERNEL,\n"
    "\t\t\t\t\tmemsw ? 0 : MEMCG_RECLAIM_MAY_SWAP,\n"
    "\t\t\t\t\tNULL)) {\n"
)

_CALL_FORCE_EMPTY_OLD = (
    "\t\tprogress = try_to_free_mem_cgroup_pages(memcg, 1, GFP_KERNEL,\n"
    "\t\t\t\t\t\t\tMEMCG_RECLAIM_MAY_SWAP);\n"
)

_CALL_FORCE_EMPTY_NEW = (
    "\t\tprogress = try_to_free_mem_cgroup_pages(memcg, 1, GFP_KERNEL,\n"
    "\t\t\t\t\t\t\tMEMCG_RECLAIM_MAY_SWAP,\n"
    "\t\t\t\t\t\t\tNULL);\n"
)

_CALL_HIGH_WRITE_OLD = (
    "\t\treclaimed = try_to_free_mem_cgroup_pages(memcg, nr_pages - high,\n"
    "\t\t\t\t\tGFP_KERNEL, MEMCG_RECLAIM_MAY_SWAP);\n"
)

_CALL_HIGH_WRITE_NEW = (
    "\t\treclaimed = try_to_free_mem_cgroup_pages(memcg, nr_pages - high,\n"
    "\t\t\t\t\tGFP_KERNEL, MEMCG_RECLAIM_MAY_SWAP,\n"
    "\t\t\t\t\tNULL);\n"
)

_CALL_MAX_WRITE_OLD = (
    "\t\t\tif (!try_to_free_mem_cgroup_pages(memcg, nr_pages - max,\n"
    "\t\t\t\t\tGFP_KERNEL, MEMCG_RECLAIM_MAY_SWAP))\n"
)

_CALL_MAX_WRITE_NEW = (
    "\t\t\tif (!try_to_free_mem_cgroup_pages(memcg, nr_pages - max,\n"
    "\t\t\t\t\tGFP_KERNEL, MEMCG_RECLAIM_MAY_SWAP, NULL))\n"
)

# The parser: upstream's enum + match_table, inserted immediately before the
# handler they belong to.  The anchor is this module's own marker line, which
# no other group writes (the Batch 10-4 forward declaration carries no marker
# and is not this text).
_TOKENS_OLD = (
    MEMORY_RECLAIM_MARKER + "\n"
    "static ssize_t memory_reclaim(struct kernfs_open_file *of, char *buf,\n"
)

_TOKENS_NEW = (
    "enum {\n"
    "\tMEMORY_RECLAIM_SWAPPINESS = 0,\n"
    "\tMEMORY_RECLAIM_NULL,\n"
    "};\n"
    "\n"
    "static const match_table_t tokens = {\n"
    "\t{ MEMORY_RECLAIM_SWAPPINESS, \"swappiness=%d\"},\n"
    "\t{ MEMORY_RECLAIM_NULL, NULL },\n"
    "};\n"
    "\n" +
    _TOKENS_OLD
)

_PARSER_OLD = (
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
)

_PARSER_NEW = (
    "\tstruct mem_cgroup *memcg = mem_cgroup_from_css(of_css(of));\n"
    "\tunsigned int nr_retries = MAX_RECLAIM_RETRIES;\n"
    "\tunsigned long nr_to_reclaim, nr_reclaimed = 0;\n"
    "\tint swappiness = -1;\n"
    "\tunsigned int reclaim_options;\n"
    "\tchar *old_buf, *start;\n"
    "\tsubstring_t args[MAX_OPT_ARGS];\n"
    "\n"
    "\tbuf = strstrip(buf);\n"
    "\n"
    "\told_buf = buf;\n"
    "\tnr_to_reclaim = memparse(buf, &buf) / PAGE_SIZE;\n"
    "\tif (buf == old_buf)\n"
    "\t\treturn -EINVAL;\n"
    "\n"
    "\tbuf = strstrip(buf);\n"
    "\n"
    "\twhile ((start = strsep(&buf, \" \")) != NULL) {\n"
    "\t\tif (!strlen(start))\n"
    "\t\t\tcontinue;\n"
    "\t\tswitch (match_token(start, tokens, args)) {\n"
    "\t\tcase MEMORY_RECLAIM_SWAPPINESS:\n"
    "\t\t\tif (match_int(&args[0], &swappiness))\n"
    "\t\t\t\treturn -EINVAL;\n"
    "\t\t\tif (swappiness < MIN_SWAPPINESS || swappiness > MAX_SWAPPINESS)\n"
    "\t\t\t\treturn -EINVAL;\n"
    "\t\t\tbreak;\n"
    "\t\tdefault:\n"
    "\t\t\treturn -EINVAL;\n"
    "\t\t}\n"
    "\t}\n"
)

_C3_CALL_OLD = (
    "\t\treclaimed = try_to_free_mem_cgroup_pages(memcg,\n"
    "\t\t\t\t\tbatch_size, GFP_KERNEL, reclaim_options);\n"
)

_C3_CALL_NEW = (
    "\t\treclaimed = try_to_free_mem_cgroup_pages(memcg,\n"
    "\t\t\t\t\tbatch_size, GFP_KERNEL,\n"
    "\t\t\t\t\treclaim_options,\n"
    "\t\t\t\t\tswappiness == -1 ? NULL : &swappiness);\n"
)

# The manual.  5.15's cgroup-v2.rst has no memory.reclaim section at all --
# the interface is this module's own graft (94968384dde1, v5.16, which is what
# memcg_memory_reclaim carries), so that group now documents it and this group
# applies upstream's amendment to that section: the two "no nested keys"
# paragraphs go, the nested-key table arrives.  Copied verbatim, including
# upstream's own column-0 placement of the table (it is outside the definition
# list upstream too).
_DOC_OLD = (
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
)

_DOC_NEW = (
    "\tExample::\n"
    "\n"
    "\t  echo \"1G\" > memory.reclaim\n"
    "\n"
    "\tPlease note that the kernel can over or under reclaim from\n"
    "\tthe target cgroup. If less bytes are reclaimed than the\n"
    "\tspecified amount, -EAGAIN is returned.\n"
    "\n"
    "The following nested keys are defined.\n"
    "\n"
    "\t  ==========            ================================\n"
    "\t  swappiness            Swappiness value to reclaim with\n"
    "\t  ==========            ================================\n"
    "\n"
    "\tSpecifying a swappiness value instructs the kernel to perform\n"
    "\tthe reclaim with that swappiness value. Note that this has the\n"
    "\tsame semantics as vm.swappiness applied to memcg reclaim with\n"
    "\tall the existing limitations and potential future extensions.\n"
)


def _get_swappiness_step(ctx):
    """Pick the ``get_swappiness()`` shape this tree carries.

    ``trace_android_vh_tune_swappiness()`` is an ACK hook, and 194 and the lts
    branch call it from ``get_swappiness()`` too: there the value is computed
    into a local and offered to the hook before being returned, where 167/178
    return ``mem_cgroup_swappiness()`` directly.  The edit is the same line
    either way and only its context differs, so the variant is chosen by
    matching the block itself -- not by sublevel (the engine never gates on the
    version) and not by the bare hook call (all four baselines call it from
    ``get_scan_count()``).

    Counting on the hook is also why the choice has to accept its own *output*:
    the second pass has to pick the same variant to report already_present.

    A tree with neither shape keeps the 167/178 variant, whose required step
    then reports the miss -- the right answer for a shape this port does not
    know.
    """
    try:
        text = ctx.read(VMSCAN_C)
    except FileNotFoundError:
        text = ""
    for step in ((VMSCAN_C, _GET_SWAPPINESS_HOOK_OLD, _GET_SWAPPINESS_HOOK_NEW, T),
                 (VMSCAN_C, _GET_SWAPPINESS_OLD, _GET_SWAPPINESS_NEW, T)):
        if step[1] in text or step[2] in text:
            return step
    return (VMSCAN_C, _GET_SWAPPINESS_OLD, _GET_SWAPPINESS_NEW, T)


def build_steps_swappiness_arg(swap_step=None):
    """Upstream 68cd9050d871, in the patch's own file order.

    Eighteen steps in mm/memcontrol.c/mm/vmscan.c/include/linux/swap.h plus
    the manual.  All required: the parameter is threaded through the prototype,
    the definition, the accessor pair, and every call site, and a half-threaded
    parameter is a compile error rather than a degraded graft -- so it must not
    be possible to land half of it.
    """
    if swap_step is None:
        swap_step = (VMSCAN_C, _GET_SWAPPINESS_OLD, _GET_SWAPPINESS_NEW, T)
    return [
        # include/linux/swap.h is 5.15's home for the prototype; upstream's
        # hunk targets the same declaration.
        (SWAP_H, _PROTO_OLD, _PROTO_NEW, T),
        (VMSCAN_C, _SC_FIELD_OLD, _SC_FIELD_NEW, T),
        (VMSCAN_C, _SC_HELPER_OLD, _SC_HELPER_NEW, T),
        (VMSCAN_C, _SC_HELPER_NO_MEMCG_OLD, _SC_HELPER_NO_MEMCG_NEW, T),
        (VMSCAN_C, _GET_SCAN_COUNT_OLD, _GET_SCAN_COUNT_NEW, T),
        swap_step,
        (VMSCAN_C, _TTFMCP_OLD, _TTFMCP_NEW, T),
        (MEMCONTROL_C, _INCLUDE_OLD, _INCLUDE_NEW, T),
        (MEMCONTROL_C, _CALL_RECLAIM_HIGH_OLD, _CALL_RECLAIM_HIGH_NEW, T),
        (MEMCONTROL_C, _CALL_TRY_CHARGE_OLD, _CALL_TRY_CHARGE_NEW, T),
        # Upstream's patch also carries mem_cgroup_resize_max() and
        # mem_cgroup_force_empty() from mm/memcontrol-v1.c, the post-5.18
        # split; on 5.15 both live in mm/memcontrol.c and are reached here.
        (MEMCONTROL_C, _CALL_RESIZE_MAX_OLD, _CALL_RESIZE_MAX_NEW, T),
        (MEMCONTROL_C, _CALL_FORCE_EMPTY_OLD, _CALL_FORCE_EMPTY_NEW, T),
        (MEMCONTROL_C, _CALL_HIGH_WRITE_OLD, _CALL_HIGH_WRITE_NEW, T),
        (MEMCONTROL_C, _CALL_MAX_WRITE_OLD, _CALL_MAX_WRITE_NEW, T),
        (MEMCONTROL_C, _TOKENS_OLD, _TOKENS_NEW, T),
        (MEMCONTROL_C, _PARSER_OLD, _PARSER_NEW, T),
        (MEMCONTROL_C, _C3_CALL_OLD, _C3_CALL_NEW, T),
        (CGROUP_V2_RST, _DOC_OLD, _DOC_NEW, T),
    ]


def _swappiness_arg_apply(ctx):
    status, _results, detail = apply_steps(
        ctx, build_steps_swappiness_arg(_get_swappiness_step(ctx)))
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ===========================================================================
# dc37771a43d4 -- mm: vmscan: abort proactive reclaim early when freezing
#                 for suspend
# ===========================================================================

_SHOULD_ABORT_OLD = (
    "static bool should_abort_scan(struct lruvec *lruvec, unsigned long seq,\n"
    "\t\t\t      struct scan_control *sc, bool need_swapping)\n"
    "{\n"
    "\tint i;\n"
    "\tDEFINE_MAX_SEQ(lruvec);\n"
    "\n"
)

_SHOULD_ABORT_NEW = (
    "static bool should_abort_scan(struct lruvec *lruvec, unsigned long seq,\n"
    "\t\t\t      struct scan_control *sc, bool need_swapping)\n"
    "{\n"
    "\tint i;\n"
    "\tDEFINE_MAX_SEQ(lruvec);\n"
    "\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: dc37771a43d4.  The PM freezer parks a task\n"
    "\t * by setting TIF_SIGPENDING; a task that does not reach the\n"
    "\t * refrigerator in time fails the suspend.  Since 287d5fedb377 the\n"
    "\t * proactive-reclaim batches decay from a quarter of the request, so\n"
    "\t * this inner loop can run for seconds on a multi-gigabyte target\n"
    "\t * without returning to the handler's own signal_pending() check.\n"
    "\t * Only sc->proactive is tested: reactive reclaim's per-iteration\n"
    "\t * target is bounded by get_scan_count(), so it returns to its outer\n"
    "\t * loop often enough on its own.\n"
    "\t */\n"
    "\tif (unlikely(sc->proactive && signal_pending(current)))\n"
    "\t\treturn true;\n"
    "\n"
)

_SIGNAL_OLD = (
    "\t\tif (signal_pending(current))\n"
    "\t\t\treturn -EINTR;\n"
    "\n"
    "\t\t/*\n"
    "\t\t * This is the final attempt, drain percpu lru caches in the\n"
)

_SIGNAL_NEW = (
    "\t\t/*\n"
    "\t\t * ABK stable_515_backport: dc37771a43d4.  -ERESTARTSYS, not\n"
    "\t\t * -EINTR: the freezer's fake signal then restarts the write\n"
    "\t\t * transparently after resume, while a real signal still either\n"
    "\t\t * restarts it (SA_RESTART) or is converted to -EINTR by the\n"
    "\t\t * signal layer.\n"
    "\t\t */\n"
    "\t\tif (signal_pending(current))\n"
    "\t\t\treturn -ERESTARTSYS;\n"
    "\n"
    "\t\t/*\n"
    "\t\t * This is the final attempt, drain percpu lru caches in the\n"
)


def build_steps_suspend_abort():
    """Upstream dc37771a43d4, both hunks.

    The MGLRU hunk is re-anchored, not copied: 5.15's ``should_abort_scan()``
    still takes the old ``(lruvec, seq, sc, need_swapping)`` signature (the
    parameters were dropped later), but the guard's job is the same -- it is
    the check the MGLRU scan and eviction loops call between batches -- so the
    signal test goes right after the function's locals, where upstream put it.

    The second hunk is the other end: upstream 7.2 keeps the proactive-reclaim
    loop in ``user_proactive_reclaim()`` in mm/vmscan.c, which 5.15 does not
    have.  On 5.15 that loop is this module's ``memory_reclaim()`` in
    mm/memcontrol.c, so the -ERESTARTSYS change lands there.
    """
    return [
        (VMSCAN_C, _SHOULD_ABORT_OLD, _SHOULD_ABORT_NEW, T),
        (MEMCONTROL_C, _SIGNAL_OLD, _SIGNAL_NEW, T),
    ]


def _suspend_abort_apply(ctx):
    status, _results, detail = apply_steps(ctx, build_steps_suspend_abort())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


# ===========================================================================
# 9669b87065a6 -- mm/lruvec: preemptively free dead folios during lru_add
#                 drain
# ===========================================================================

_LRU_ADD_OLD = (
    "void __pagevec_lru_add(struct pagevec *pvec)\n"
    "{\n"
    "\tint i;\n"
    "\tstruct lruvec *lruvec = NULL;\n"
    "\tunsigned long flags = 0;\n"
    "\n"
    "\tfor (i = 0; i < pagevec_count(pvec); i++) {\n"
    "\t\tstruct page *page = pvec->pages[i];\n"
    "\n"
    "\t\tlruvec = relock_page_lruvec_irqsave(page, lruvec, &flags);\n"
    "\t\t__pagevec_lru_add_fn(page, lruvec);\n"
    "\t}\n"
    "\tif (lruvec)\n"
    "\t\tunlock_page_lruvec_irqrestore(lruvec, flags);\n"
    "\trelease_pages(pvec->pages, pvec->nr);\n"
    "\tpagevec_reinit(pvec);\n"
    "}\n"
)

_LRU_ADD_NEW = (
    "void __pagevec_lru_add(struct pagevec *pvec)\n"
    "{\n"
    "\tint i;\n"
    "\tstruct lruvec *lruvec = NULL;\n"
    "\tunsigned long flags = 0;\n"
    "\tLIST_HEAD(pages_to_free);\n"
    "\n"
    "\tfor (i = 0; i < pagevec_count(pvec); i++) {\n"
    "\t\tstruct page *page = pvec->pages[i];\n"
    "\n"
    "\t\t/*\n"
    "\t\t * ABK stable_515_backport: 9669b87065a6.  A page whose only\n"
    "\t\t * reference is this batch's own is already dead.  Adding it to\n"
    "\t\t * the LRU only for release_pages() to take it off again costs\n"
    "\t\t * two lruvec lock acquisitions for nothing, and under pressure\n"
    "\t\t * those are the lock the reclaimer is waiting on.  Filter it\n"
    "\t\t * out here and free it with the rest of the batch below.\n"
    "\t\t *\n"
    "\t\t * Upstream also unqueues the page from the deferred-split list\n"
    "\t\t * at this point; on 5.15 that line is deliberately not carried.\n"
    "\t\t * A deferred-split page is always compound, and\n"
    "\t\t * pagevec_add_and_need_flush() drains the lru_add pagevec\n"
    "\t\t * immediately for a compound page, so what really lingers here\n"
    "\t\t * is an order-0 page -- for which page_deferred_list() is a\n"
    "\t\t * neighbouring allocation, not a list head -- and a THP that\n"
    "\t\t * does reach the allocator unqueues itself through\n"
    "\t\t * free_transhuge_page().\n"
    "\t\t *\n"
    "\t\t * PG_active can be set on a batched page (__lru_cache_activate_\n"
    "\t\t * page()) and PG_unevictable by the migration path; the folio\n"
    "\t\t * skips the cleanup in __pagevec_lru_add_fn(), so both are\n"
    "\t\t * cleared before it is freed.\n"
    "\t\t */\n"
    "\t\tif (page_ref_freeze(page, 1)) {\n"
    "\t\t\t__ClearPageActive(page);\n"
    "\t\t\t__ClearPageUnevictable(page);\n"
    "\t\t\tpvec->pages[i] = NULL;\n"
    "\t\t\tlist_add(&page->lru, &pages_to_free);\n"
    "\t\t\tcontinue;\n"
    "\t\t}\n"
    "\n"
    "\t\tlruvec = relock_page_lruvec_irqsave(page, lruvec, &flags);\n"
    "\t\t__pagevec_lru_add_fn(page, lruvec);\n"
    "\t}\n"
    "\tif (lruvec)\n"
    "\t\tunlock_page_lruvec_irqrestore(lruvec, flags);\n"
    "\n"
    "\tmem_cgroup_uncharge_list(&pages_to_free);\n"
    "\tfree_unref_page_list(&pages_to_free);\n"
    "\n"
    "\trelease_pages(pvec->pages, pvec->nr);\n"
    "\tpagevec_reinit(pvec);\n"
    "}\n"
)

# The batch consumer.  Upstream's third hunk is in folios_put_refs(); on 5.15
# the consumer of an add batch is release_pages(), which release_pages() is
# also the one __pagevec_lru_add() calls.
_RELEASE_OLD = (
    "\tfor (i = 0; i < nr; i++) {\n"
    "\t\tstruct page *page = pages[i];\n"
    "\n"
    "\t\t/*\n"
    "\t\t * Make sure the IRQ-safe lock-holding time does not get\n"
    "\t\t * excessive with a continuous string of pages from the\n"
    "\t\t * same lruvec. The lock is held only if lruvec != NULL.\n"
    "\t\t */\n"
)

_RELEASE_NEW = (
    "\tfor (i = 0; i < nr; i++) {\n"
    "\t\tstruct page *page = pages[i];\n"
    "\n"
    "\t\t/* A drained add batch may have freed this slot already. */\n"
    "\t\tif (!page)\n"
    "\t\t\tcontinue;\n"
    "\n"
    "\t\t/*\n"
    "\t\t * Make sure the IRQ-safe lock-holding time does not get\n"
    "\t\t * excessive with a continuous string of pages from the\n"
    "\t\t * same lruvec. The lock is held only if lruvec != NULL.\n"
    "\t\t */\n"
)


def build_steps_lru_add_drain():
    """Upstream 9669b87065a6, both halves, on 5.15's pagevec shape.

    mm/swap.c here is pre-folio-batch (v5.17 converted it): the add batch is
    ``struct pagevec``/``__pagevec_lru_add()`` rather than
    ``folio_batch``/``folio_batch_move_lru()``, and the batch consumer is
    ``release_pages()`` rather than ``folios_put_refs()``.  The filter is
    written against the page API for that reason -- ``page_ref_freeze()`` is
    the 5.15 spelling of ``folio_ref_freeze()`` and is already what vmscan.c
    uses for the same "only the batch's reference is left" test.

    Only the add path is touched, exactly as upstream does: the other pagevec
    consumers move pages between LRUs, they do not inherit them, so a dead
    page there is not the same case.
    """
    return [
        (SWAP_C, _LRU_ADD_OLD, _LRU_ADD_NEW, T),
        (SWAP_C, _RELEASE_OLD, _RELEASE_NEW, T),
    ]


def _lru_add_drain_apply(ctx):
    status, _results, detail = apply_steps(ctx, build_steps_lru_add_drain())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return this batch's six PatchGroup records, in dependency order.

    The first three all rewrite the ``memory_reclaim()`` call site the
    ``memcg_memory_reclaim`` group generates, and each is superseded by the
    next; ``docs/group_recipe.md`` trap 5 is why they carry probes and why the
    order is fixed.  ``reclaim_swappiness_defines`` must precede
    ``proactive_reclaim_swappiness_arg`` (that group's parser validates against
    the constants it adds), and ``proactive_reclaim_suspend_abort`` is last of
    the five because ``287d5fedb377`` is in its Fixes: line.
    """
    return [
        PatchGroup(
            "proactive_reclaim_batch_fidelity",
            "cap one proactive-reclaim call at SWAP_CLUSTER_MAX, so a request "
            "is not overreclaimed towards the original target after an inner "
            "loop already satisfied part of it",
            [
                "0388536ac291 (v6.6, 'mm:vmscan: fix inaccurate reclaim during "
                "proactive reclaim')",
            ],
            [MEMCONTROL_C],
            _batch_fidelity_apply,
        ),
        PatchGroup(
            "proactive_reclaim_decaying_batches",
            "replace that fixed 32-page cap with a decaying batch "
            "((reclaim - reclaimed) / 4): the cap's reclaim start/stop "
            "overhead cost more than its accuracy bought",
            [
                "287d5fedb377 (v6.9, 'mm: memcg: use larger batches for "
                "proactive reclaim', Fixes: 0388536ac291)",
            ],
            [MEMCONTROL_C],
            _decaying_batches_apply,
        ),
        PatchGroup(
            "reclaim_swappiness_defines",
            "MIN_SWAPPINESS / MAX_SWAPPINESS, and the three 200 literals they "
            "replace (the swappiness= parser of the next group validates "
            "against them)",
            [
                "410abb20acae (v6.11, 'mm: add defines for min/max swappiness', "
                "patch 2 of the 'Add swappiness argument to memory.reclaim' "
                "series -- carried because patch 3 names these constants)",
            ],
            [SWAP_H, VMSCAN_C, MEMCONTROL_C],
            _swappiness_defines_apply,
        ),
        PatchGroup(
            "proactive_reclaim_swappiness_arg",
            "memory.reclaim gains a swappiness=<val> nested key: the value "
            "rides on scan_control to get_scan_count()/get_swappiness() "
            "through a new sc_swappiness() accessor, so a proactive reclaimer "
            "no longer has to rewrite the global vm.swappiness to steer "
            "file-vs-anon balance",
            [
                "68cd9050d871 (v6.11, 'mm: add swappiness= arg to "
                "memory.reclaim', patch 3 of the same series)",
            ],
            [SWAP_H, VMSCAN_C, MEMCONTROL_C, CGROUP_V2_RST],
            _swappiness_arg_apply,
        ),
        PatchGroup(
            "proactive_reclaim_suspend_abort",
            "let the PM freezer interrupt a proactive reclaim: the MGLRU inner "
            "loop checks for a pending signal, and the handler returns "
            "-ERESTARTSYS so the syscall restarts after resume instead of "
            "failing the suspend",
            [
                "dc37771a43d4 (v7.2, 'mm: vmscan: abort proactive reclaim early "
                "when freezing for suspend', Fixes: 287d5fedb377)",
            ],
            [VMSCAN_C, MEMCONTROL_C],
            _suspend_abort_apply,
        ),
        PatchGroup(
            "lru_add_drain_dead_folios",
            "free a page that is already dead before it reaches the LRU, "
            "instead of adding it and taking it off again: the lru_add batch "
            "is filtered on its final reference, and the batch consumer "
            "tolerates the vacated slot",
            [
                "9669b87065a6 (v7.2, 'mm/lruvec: preemptively free dead folios "
                "during lru_add drain')",
            ],
            [SWAP_C],
            _lru_add_drain_apply,
        ),
    ]
