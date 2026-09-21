# v7.2 MGLRU reclaim-loop series — fetched, examined, not ported

The 12 commits of Kairui Song's "mm/mglru: improve reclaim loop and dirty folio"
v7 (cover `0491e9f75c15`), all saved here as `<sha>.patch`, fetched with

    curl -sSL https://github.com/torvalds/linux/commit/<sha>.patch

(`github.com/.../commit/<sha>.patch` is served by github.com directly and does
**not** consume the `api.github.com` 60/hour budget, unlike
`api.github.com/repos/torvalds/linux/commits/<sha>` — worth remembering, the API
budget was exhausted part-way through the v7.2 survey).

**Verdict: not portable to this baseline.** The first survey pass rated the
series strongly recommended on the premise that 5.15's `isolate_pages()` /
`evict_pages()` are a one-to-one match for 7.2's `scan_folios()` /
`evict_folios()`. That is wrong: the names correspond, the decomposition does
not. The series rests on five intervening refactors 5.15 does not have —
`should_run_aging()` 6-arg-and-computes-nr_to_scan vs 7.2's 4-arg pure
predicate, `get_nr_to_scan()` 4-arg-with-out-param vs 3-arg, the top loop named
`lru_gen_shrink_lruvec()` with `can_swap` threaded through vs 7.2's
`try_to_shrink_lruvec()` with `swappiness`, `scan_folios()`'s `isolatedp`
out-param plus `isolate_folios()`'s cross-type `total_scanned`, and
`for_each_evictable_type()` / `root_reclaim()` (zero occurrences in 5.15's
`mm/vmscan.c`).

Per-commit disposition, the reasoning, and the safety analysis are in
`docs/survey_7_2_mm_reclaim.md` §1 and `CHANGELOG.md#batch-39` §4. Short version:

| sha | disposition |
|---|---|
| `790d3abeca09` | no-op — 5.15's variable names are not the ones renamed |
| `aa6ef5b159dc` | blocked — 5.15's `scan_pages()` hardcodes `remaining = MAX_LRU_BATCH` and takes no `nr_to_scan` |
| `163bc3d68c9f` | blocked — needs the two signature changes above |
| `6e9be217a3ce` | blocked — depends on `aa6ef5b159dc` |
| `3a72e078b4a3` | blocked — needs the `isolatedp` out-param chain |
| `12316f7902f8` | largely already present — 5.15's `get_nr_to_scan()` returns `nr_to_scan` after aging, i.e. it already ages-then-scans |
| `16b475d2ac3c` | already present — 5.15's `isolate_pages()` only falls back when `scanned == 0` |
| `acd22fbb9f47` | already present — 5.15's `isolate_page()` has no such check |
| `75d4c3f5fb98` | blocked — 5.15's `sort_page()` already diverts dirty/writeback pages pre-isolation |
| `f37d3708b676` | blocked by idempotency collision — the block it relocates is this module's own `mglru_wake_flushers` output (trap 5) |
| `32d87083ee97` | blocked — 5.15's `page_inc_gen()` still sets `PG_reclaim` when reclaiming |
| `6cbdd9726fb5` | blocked — touches `mm/swap.c`, absent on 5.15 |

Upstream's own numbers for the series (throughput +29%, latency −23%,
workingset refault −43% for `f37d3708b676` alone) come from YCSB/MongoDB on a
server; none of them is a Snapdragon measurement and none is claimed as a
device-side result.
