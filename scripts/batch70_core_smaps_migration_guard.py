from __future__ import annotations

from abk_backport_engine import apply_steps

TASK_MMU = "fs/proc/task_mmu.c"

T = True

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

HELPER_DEF = "static struct page *abk_smaps_migration_page("
HELPER_MARKER = "sailboat_smaps_migration_guard"
PTE_GUARD = "page = abk_smaps_migration_page(swpent);"
PMD_GUARD = "page = abk_smaps_migration_page(entry);"


def build_steps():
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
