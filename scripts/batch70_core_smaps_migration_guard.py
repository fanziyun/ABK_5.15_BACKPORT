"""Batch 70: smaps migration-entry PageLocked race guard.

Field evidence: minidump MD_RST_STAT.BIN.txt:777
``kernel BUG at include/linux/swapops.h:267`` via
``smaps_pte_range -> smaps_pte_entry -> pfn_swap_entry_to_page`` when
``AnrConsumer`` reads ``/proc/pid/smaps_rollup``. ``pfn_swap_entry_to_page()``
carries ``BUG_ON(!PageLocked(p))`` for migration entries, but the smaps
walkers never hold the target page lock: a concurrent migration / compaction
unlock plus ``remove_migration_ptes`` (or pfn reuse) in the window between the
PTE snapshot and the ``PageLocked`` check trips the unconditional ``BUG_ON``
and panics the device. smaps is the victim, not the cause.

What this group does: insert one file-static helper above ``smaps_pte_entry``
that resolves a migration entry to ``struct page`` WITHOUT the lock assertion
(``pfn_to_page(swp_offset(entry))`` plus an ``smp_rmb`` ordering the pfn read
ahead of the ``PageLocked`` flags read), returning NULL unless the page is
currently locked -- i.e. unless ``pfn_swap_entry_to_page()``'s own
``BUG_ON(!PageLocked())`` predicate holds; then route all five
``pfn_swap_entry_to_page()`` callers in ``fs/proc/task_mmu.c`` through it:

  1. ``smaps_pte_entry`` (the crash site, task_mmu.c:551)
  2. ``smaps_pmd_entry`` (THP migration, task_mmu.c:586)
  3. ``smaps_hugetlb_range`` (task_mmu.c:734)
  4. ``pte_to_pagemap_entry`` (task_mmu.c:1442 -- the pagemap PTE
     walker is this file's per-entry helper, not a range function)
  5. ``pagemap_pmd_range`` (task_mmu.c:1505, under ``VM_BUG_ON``)

Behaviour on the race: the accounting call is skipped for that entry (smaps
callers already ``return`` on NULL; the pagemap PMD caller already tolerates a
NULL page). Steady state is byte-identical in outcome: a locked migration page
resolves exactly as before, an unlocked one previously panicked and now
contributes nothing for that sample.

Why a helper instead of five inline guards: one NULL/locked contract to audit
instead of five copies, and the helper is the group's idempotency probe (trap 5:
no later group edits this file, but the probe keeps the second pass honest).

Scope notes:

* Upstream-shape hardening with a ``sailboat_`` marker (anchor policy: a graft
has to be findable in a fetched tree). The marker rides in the helper comment,
so a baseline that ever grows an equivalent upstream guard still reports
``applied`` on the second pass rather than ``already_present`` -- stated cost,
accepted.
* Security-only fixes normally arrive with sublevel rolls, but this one is a
stability fix for a panic observed on the supported tree, in the same smaps
walker family Batch 35 already documents as unhardened on 5.15 (Batch 35
withdrew its own smaps crasher rather than hardening the walker; this hardens
it).
* ``fs/proc/task_mmu.c`` joins ``FETCH_FILES`` / ``AUDIT_FILES`` / ``SMOKE_FILES``
with this batch; it was absent because no group touched it until now.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

TASK_MMU = "fs/proc/task_mmu.c"

T = True

# The helper: migration entry -> page without the BUG_ON contract.
# Anchored on smaps_pte_entry's signature, which is unique in the file.
_HELPER_OLD = (
    "static void smaps_pte_entry(pte_t *pte, unsigned long addr,\n"
    "\t\tstruct mm_walk *walk)\n"
    "{\n"
)

_HELPER_NEW = (
    "/*\n"
    " * sailboat_smaps_migration_guard: resolve a migration entry to its page\n"
    " * WITHOUT pfn_swap_entry_to_page()'s BUG_ON(!PageLocked()) contract. The\n"
    " * smaps/pagemap walkers hold the page-table lock but never the target\n"
    " * page lock, so a migration entry whose page is no longer locked (the\n"
    " * migration completed, or the pfn was reused) makes that BUG_ON panic\n"
    " * the device (field BUG via smaps_rollup -> smaps_pte_range ->\n"
    " * smaps_pte_entry, swapops.h:267). PageLocked(p) here IS the BUG_ON's own\n"
    " * predicate: a page that passes it resolves exactly as\n"
    " * pfn_swap_entry_to_page() would, and one that fails is skipped (NULL)\n"
    " * instead of panicking. The smp_rmb() orders the entry/pfn read ahead of\n"
    " * the PageLocked() flags read. This suppresses the panic and resolves by\n"
    " * pfn like the upstream helper -- it does not add a page-identity\n"
    " * re-validation the walkers' page-table lock does not already give.\n"
    " * NULL means \"skip this entry\"; every caller below already tolerates\n"
    " * it (smaps returns on NULL, pagemap_pmd_range checks page before use).\n"
    " */\n"
    "static struct page *abk_smaps_migration_page(swp_entry_t entry)\n"
    "{\n"
    "\tstruct page *p;\n"
    "\n"
    "\tif (!is_migration_entry(entry))\n"
    "\t\treturn NULL;\n"
    "\tp = pfn_to_page(swp_offset(entry));\n"
    "\tsmp_rmb();\n"
    "\tif (!PageLocked(p))\n"
    "\t\treturn NULL;\n"
    "\treturn p;\n"
    "}\n"
    "\n"
) + _HELPER_OLD

# 1) smaps_pte_entry: the crash site.
_PTE_OLD = (
    "\t\t} else if (is_pfn_swap_entry(swpent)) {\n"
    "\t\t\tif (is_migration_entry(swpent))\n"
    "\t\t\t\tmigration = true;\n"
    "\t\t\tpage = pfn_swap_entry_to_page(swpent);\n"
    "\t\t}\n"
)

_PTE_NEW = (
    "\t\t} else if (is_pfn_swap_entry(swpent)) {\n"
    "\t\t\tif (is_migration_entry(swpent)) {\n"
    "\t\t\t\tmigration = true;\n"
    "\t\t\t\tpage = abk_smaps_migration_page(swpent);\n"
    "\t\t\t} else {\n"
    "\t\t\t\tpage = pfn_swap_entry_to_page(swpent);\n"
    "\t\t\t}\n"
    "\t\t}\n"
)

# 2) smaps_pmd_entry: THP migration entry under is_swap_pmd.
_PMD_OLD = (
    "\t\tif (is_migration_entry(entry)) {\n"
    "\t\t\tmigration = true;\n"
    "\t\t\tpage = pfn_swap_entry_to_page(entry);\n"
    "\t\t}\n"
)

_PMD_NEW = (
    "\t\tif (is_migration_entry(entry)) {\n"
    "\t\t\tmigration = true;\n"
    "\t\t\tpage = abk_smaps_migration_page(entry);\n"
    "\t\t}\n"
)

# 3) smaps_hugetlb_range: unconditional pfn_swap_entry_to_page on any
# pfn-swap entry. Migration entries take the guarded path; device-private /
# device-exclusive keep the direct call (their contract is not PageLocked).
_HUGE_OLD = (
    "\t\tif (is_pfn_swap_entry(swpent))\n"
    "\t\t\tpage = pfn_swap_entry_to_page(swpent);\n"
)

_HUGE_NEW = (
    "\t\tif (is_migration_entry(swpent))\n"
    "\t\t\tpage = abk_smaps_migration_page(swpent);\n"
    "\t\telse if (is_pfn_swap_entry(swpent))\n"
    "\t\t\tpage = pfn_swap_entry_to_page(swpent);\n"
)

# 4) pte_to_pagemap_entry().
_PAGEMAP_PTE_OLD = (
    "\t\tmigration = is_migration_entry(entry);\n"
    "\t\tif (is_pfn_swap_entry(entry))\n"
    "\t\t\tpage = pfn_swap_entry_to_page(entry);\n"
)

_PAGEMAP_PTE_NEW = (
    "\t\tmigration = is_migration_entry(entry);\n"
    "\t\tif (is_migration_entry(entry))\n"
    "\t\t\tpage = abk_smaps_migration_page(entry);\n"
    "\t\telse if (is_pfn_swap_entry(entry))\n"
    "\t\t\tpage = pfn_swap_entry_to_page(entry);\n"
)

# 5) pagemap_pmd_range (under VM_BUG_ON(!is_pmd_migration_entry(pmd))).
_PAGEMAP_PMD_OLD = (
    "\t\t\tVM_BUG_ON(!is_pmd_migration_entry(pmd));\n"
    "\t\t\tmigration = is_migration_entry(entry);\n"
    "\t\t\tpage = pfn_swap_entry_to_page(entry);\n"
)

_PAGEMAP_PMD_NEW = (
    "\t\t\tVM_BUG_ON(!is_pmd_migration_entry(pmd));\n"
    "\t\t\tmigration = is_migration_entry(entry);\n"
    "\t\t\tpage = abk_smaps_migration_page(entry);\n"
)

# Pins for the audits and the unit test.
HELPER_DEF = "static struct page *abk_smaps_migration_page("
HELPER_MARKER = "sailboat_smaps_migration_guard"
PTE_GUARD = "page = abk_smaps_migration_page(swpent);"
PMD_GUARD = "page = abk_smaps_migration_page(entry);"


def build_steps():
    """One helper plus five caller guards, all required and in order."""
    return [
        (TASK_MMU, _HELPER_OLD, _HELPER_NEW, T),
        (TASK_MMU, _PTE_OLD, _PTE_NEW, T),
        (TASK_MMU, _PMD_OLD, _PMD_NEW, T),
        (TASK_MMU, _HUGE_OLD, _HUGE_NEW, T),
        (TASK_MMU, _PAGEMAP_PTE_OLD, _PAGEMAP_PTE_NEW, T),
        (TASK_MMU, _PAGEMAP_PMD_OLD, _PAGEMAP_PMD_NEW, T),
    ]


def _smaps_migration_guard_apply(ctx):
    try:
        text = ctx.read(TASK_MMU)
    except FileNotFoundError:
        return "blocked_by_shape", TASK_MMU + ": file absent"
    # The marker is part of the match, not decoration: AGENTS.md's idempotency
    # rule requires it, and the reason is concrete here.  This file is written by
    # one group only, so the helper can only be here because this group put it
    # here -- but matching on HELPER_DEF alone means a tree whose comment was
    # re-anchored (reflow, re-indentation, licence-header move) is skipped as
    # already_present even though its graft no longer carries a findable marker.
    #
    # The fix is a refusal, NOT a fall-through into apply_steps: the helper step's
    # `new` block would no longer match (the marker is gone) while its `old` block
    # still matches -- the pre-existing anchor lines sit directly below the
    # grafted helper -- so apply_steps would insert a *second* copy of a static
    # function.  That is a redefinition, i.e. a compile error on a tree that was
    # previously fine.  Measured, not assumed: the helper count went 1 -> 2.
    if HELPER_DEF in text:
        if HELPER_MARKER in text:
            return "already_present", "the smaps migration guard is already applied"
        return (
            "blocked_by_shape",
            TASK_MMU + ": the helper is present but its " + HELPER_MARKER
            + " marker is not; refusing rather than inserting a second copy",
        )
    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the single Batch-70 smaps guard PatchGroup record."""
    return [
        PatchGroup(
            "smaps_migration_guard",
            "smaps/pagemap walkers resolve migration entries through a "
            "PageLocked-checked helper instead of pfn_swap_entry_to_page(), "
            "so a migration that completes between the PTE snapshot and the "
            "check skips the entry instead of tripping swapops.h:267 BUG_ON",
            [
                "field panic MD_RST_STAT.BIN.txt:777 "
                "(smaps_rollup -> smaps_pte_range -> pfn_swap_entry_to_page)",
            ],
            [TASK_MMU],
            _smaps_migration_guard_apply,
        ),
    ]
