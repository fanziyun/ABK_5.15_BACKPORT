# Upstream survey: android15-6.6 ACK line features for the 5.15 baseline

Scope: features carried by the Android Common Kernel **android15-6.6**
branch (HEAD 6.6.142 on the `android15-6.6` branch) that are absent from the
android13-5.15 2024-11 baseline (5.15.167) and portable onto it as bounded
anchor-script grafts (the `ABK_5.15_backport` module_set).  Features /
optimizations only; pure security fixes out of scope by policy.

Survey method: file-level diff of two local WSL trees
(`common-6.6-src` = android15-6.6 @ 6.6.142 vs
`common-5.15-2024-11` = android13-5.15 @ 5.15.167), restricted to the
subsystems the module grafts (mm, kernel/sched, kernel/locking,
kernel/cgroup, fs, block, net/core, include/linux, include/trace/hooks,
drivers/block/zram, drivers/android, io_uring), cross-checked against the
ABK_ABI_PATCH_SUITE inventory and the KMI red lines.

## Result: most 6.6 content is suite-covered, KMI-blocked, or infrastructure

| candidate | verdict | reason |
|---|---|---|
| EEVDF scheduling (PLACE_LAG / PLACE_DEADLINE_INITIAL / RUN_TO_PARITY / HZ_BW / SIS_UTIL) | **suite** | ABK_ABI_PATCH_SUITE EEVDF family owns `sched_entity` KABI slots 1–4; excluded by contract |
| per-VMA locks | **deferred** | ACK 6.6 sits on maple-tree storage with `vm_area_struct` rework; needs RCU VMA lifetime + fault-path conversion + KABI growth — not a bounded anchor graft |
| per-cgroup PSI toggling / PSI parent-chain sync | **KMI red line** | rewrites `struct cgroup` layout |
| mm/khugepaged.c, folio, page_alloc, memcg reworks | **infra** | 6.2–6.6 infrastructure rewrites (khugepaged alone ≈ +1300/−925), not bounded grafts |
| new Android MM vendor hooks (THP gfp orders, alloc slowpath, CMA/contig, compaction, swap) | **hook-only** | additive tracepoints with real call sites but no in-tree mechanism consumer |
| zram **recompression** | **chosen (Batch 4)** | genuine self-contained mechanism (recompress idle/small pages with a second compressor); not covered by the suite (suite owns zram *writeback*) |
| binder next-gen perf | **deferred** | 5.15 already carries oneway-spam + frozen state; the 6.6+ generation is a larger driver effort |
| io_uring 6.2–6.6 growth | **suite** | suite owns io_uring NOWAIT / zcrx / cBPF / non-circular-SQ; the remaining pieces are infra |

## Batch 4 = zram recompression (source form android15-6.6 / 6.6.142)

Upstream: the recompression series (Sergey Senozhatsky, v6.2) adds
`CONFIG_ZRAM_MULTI_COMP` (a second, possibly-slower-but-more-effective
compression algorithm) so idle / small pages can be re-compressed in place
to shrink zram footprint and cut paging I/O.  Requires
`CONFIG_ZRAM_TRACK_ENTRY_ACTIME` (idle age tracking).

### What must change on android13-5.15 (5.15.167)

The 6.6 zram driver has accumulated 3+ years of non-recompression changes, so
this is a **port of the feature**, not a file copy.  The adaptation, by file:

- `drivers/block/zram/Kconfig` — add `ZRAM_TRACK_ENTRY_ACTIME`,
  `ZRAM_MULTI_COMP`; make `ZRAM_MEMORY_TRACKING` select
  `ZRAM_TRACK_ENTRY_ACTIME`.  (Keep the 5.15 `ZRAM` `depends on` line.)
- `drivers/block/zram/zram_drv.h` — add `ZRAM_INCOMPRESSIBLE`,
  `ZRAM_COMP_PRIORITY_BIT1/BIT2`, `ZRAM_COMP_PRIORITY_MASK`; move
  `ac_time` behind `CONFIG_ZRAM_TRACK_ENTRY_ACTIME`; add
  `ZRAM_PRIMARY_COMP`/`ZRAM_SECONDARY_COMP`/`ZRAM_MAX_COMPS`; change
  `struct zcomp *comp` → `struct zcomp *comps[ZRAM_MAX_COMPS]`,
  `char compressor[]` → `const char *comp_algs[ZRAM_MAX_COMPS]` +
  `s8 num_active_comps`.  **Decision: keep `ZRAM_FLAG_SHIFT 24`** (drop the
  6.6 `PAGE_SHIFT+1` shrink) to avoid a risky flags layout change — the
  priority bits live in free high bits of `flags`.
- `drivers/block/zram/zram_drv.c` — port `zram_set_priority` /
  `zram_get_priority`, `zram_accessed` (actime), `mark_idle(cutoff)` +
  idle_store age parsing, `zram_recompress()`, `recompress_store()`, the
  `recompress` sysfs attr + attribute table, multi-comp initialization in
  `compressor_store`/`zram_init`/`zram_reset_device`, and switch every
  `zram->comp` → `zram->comps[ZRAM_PRIMARY_COMP]` and
  `zram->compressor` → `zram->comp_algs[0]`.
- `drivers/block/zram/zcomp.c` / `.h` — effectively unchanged (multi-comp is
  orchestrated in `zram_drv.c` by creating multiple `zcomp` instances).
- `mm/zsmalloc.c` + `include/linux/zsmalloc.h` — add and export
  `zs_lookup_class_index()` (6.6 form: `pool->size_class[get_size_class_index(size)]
  ->index`), a 5.15-missing API the recompress sizing path needs.

### Primitive adaptation (5.15 vs 6.6) the recompress path needs

- `zram_read_from_zspool()` does not exist in 5.15; `zram_recompress()` must
  decompress via the existing 5.15 read helper.  In 5.15 `__zram_bvec_read()`
  (or `zram_read_page`) is the equivalent — the recompress step should read
  the page with the 5.15 helper rather than a 6.6-only function.
- `zs_lookup_class_index()` is 6.6-only ⇒ add to zsmalloc (above).
- `next_arg()`, `skip_spaces()`, `huge_class_size` are already present in
  5.15 — no port needed there.

### Suite-preference exclusion

ABK_ABI_PATCH_SUITE's `zram_compressed_writeback` covers zram **writeback**
(backing-dev), which is a distinct store/rw path in `zram_drv.c`.  Recompression
operates on in-memory zsmalloc objects and does not rewrite that path; the two
must compose.  The module's group preflights for the suite's writeback markers
and leaves that region alone.

### Port plan / status

- [x] survey + triage (this doc)
- [x] target `zram_drv.h` (research/zram/target/zram_drv.h)
- [x] target `Kconfig` (research/zram/target/Kconfig)
- [ ] `mm/zsmalloc.c`/`.h` `zs_lookup_class_index` addition
- [ ] `zram_drv.c` recompression adaptation (largest piece; validated via CI)
- [ ] `zram_recompression` `PatchGroup` + config/test counts + CI verify

Deferred (unchanged from the 6.1 survey): per-VMA locks, per-cgroup PSI
toggling, DAMON sysfs, MADV_COLLAPSE, zram-writeback (suite), EEVDF (suite).
