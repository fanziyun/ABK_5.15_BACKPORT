# Upstream survey: Linux 7.2 MM/Reclaim candidates for the android13-5.15 baseline

Scope: commits that touch `mm/` and are reachable from tag `v7.2` but **not**
`v7.1` — 366 of them — screened for backport value onto android13-5.15
(5.15.167/.178/.194/.216), targeting a mobile Snapdragon SoC.
Features/optimizations/structural refactors only; pure security fixes out of
scope by policy (`porting_policy.md`), and a fix rides along only when its
5.15.y backport text is already available verbatim.

Landed as **Batch 38** (`CHANGELOG.md#batch-38`, v0.43.0): six groups in
`scripts/batch38_core_mm_safety_perf.py`.

## Method — and why a committer-date window does not work

**A committer date does not delimit a kernel release.** Subsystem trees commit
patches weeks before Linus pulls them, so a large share of v7.2 commits carry
committer dates in April/May. My first attempt (committer date
2026-06-13..2026-08-16) covered only **121 of the 366**, missed **245**, and
admitted **432 post-7.2 master commits**.

The correct method is tag reachability: list `path=mm` under `sha=v7.1` and
under `sha=v7.2` and difference the two. Cross-checked against cgit's
`log/mm/?id=v7.1..v7.2` full range walk — the two agree exactly (the 245
commits in the difference all appear in cgit's 366; nothing is unaccounted
for). 50 commits were individually re-verified as "in v7.2, not in v7.1".

> kernel.org became unreachable mid-survey (Anubis challenge / DNS to a
> non-routable address). Patches were taken from
> `https://api.github.com/repos/torvalds/linux/commits/<sha>`, which returns
> `commit.message` plus `files[].patch`. Unauthenticated budget is 60
> requests/hour per IP, so the deepest analysis went to the highest-value
> candidates and the rest are classified by subject + file list only.

## Target-machine facts (measured on the reference trees, not inferred)

| Fact | Value | Consequence |
|---|---|---|
| `CONFIG_LRU_GEN` | **y** | MGLRU is **live** on the device — MGLRU work is not theoretical |
| `CONFIG_KSM` | **not set** | every `ksm/*` commit is inapplicable |
| `CONFIG_DAMON` / `DAMON_PADDR` / `DAMON_RECLAIM` | **all y** | `mm/damon/reclaim` is reachable (but see caveat below) |
| `CONFIG_TRANSPARENT_HUGEPAGE` + `_MADVISE` | y / y | THP exists but is madvise-only, not always |
| `CONFIG_INIT_ON_ALLOC_DEFAULT_ON` + `CONFIG_KASAN_HW_TAGS` | y / y | the per-page clear loop in `post_alloc_hook()` really runs on every allocation |
| `CONFIG_ZSMALLOC` / `CONFIG_ZRAM` | m / m | zram/zsmalloc paths are hot |
| `CONFIG_TASKS_TRACE_RCU` (not `CONFIG_TASKS_RCU`) | y / not set | Tasks-RCU quiescent-state reporting compiles to a no-op here |
| `mm/swap.c` | **does not exist** | 5.15's swap-offset machinery is in `swap_slots.c` / `swapfile.c` / `internal.h` |
| `i_mmap_lock` | the 5.15 name | 7.x's `i_mmap_rwsem` is a rename and must be mapped |
| `do_swap_page()` calls `lru_add_drain()` | yes (`mm/memory.c:1741`, `:1768`) | lru-drain removal has a target here |

The reference trees carry only 76 files. **`mm/rmap.c`, `mm/workingset.c`,
`mm/percpu.c`, `mm/page-writeback.c`, `mm/zswap.c`, `mm/shmem.c`, `mm/slab.h`,
`mm/swap.c`, `mm/page_io.c`, `mm/vmstat.c`, `include/linux/highmem.h` and
`mm/damon/*` are not in them**, so every conclusion on those paths is marked
"not verifiable locally". `mm/damon/reclaim.c` deserves special mention:
upstream v5.15 has **no such file at all**, yet the target CI config has
`CONFIG_DAMON_RECLAIM=y`, so it is a vendor/ACK addition whose shape is
unverified — all damon/reclaim conclusions are blocked on fetching it.

---

## Strongly recommended (6 of the 8 landed as Batch 38)

### 1. MGLRU reclaim-loop restructure + dirty writeback series

Kairui Song (Tencent), "mm/mglru: improve reclaim loop and dirty folio" v7,
cover `0491e9f75c15`, 12 commits, **all touching only `mm/vmscan.c`** (sole
exception `6cbdd9726fb5`, which also touches `mm/swap.c` — absent on 5.15 —
plus `mm/workingset.c` and `include/linux/mm_inline.h`).

| sha | subject | lines |
|---|---|---|
| `790d3abeca09` | rename variables related to aging and rotation (rename only) | +7/-7 |
| `aa6ef5b159dc` | relocate the LRU scan batch limit to callers (no behaviour change) | +9/-7 |
| `163bc3d68c9f` | **restructure the reclaim loop** (compute the scan number once, decouple aging/rotation) | +36/-36 |
| `6e9be217a3ce` | **use a smaller batch for reclaim** (less lock contention, no over-reclaim) | +1/-1 |
| `3a72e078b4a3` | scan and count the exact number of folios | +29/-29 |
| `12316f7902f8` | **don't abort scan immediately right after aging** | +9/-3 |
| `16b475d2ac3c` | avoid reclaim type fall back when isolation makes no progress (holds swappiness) | +7/-2 |
| `acd22fbb9f47` | remove redundant swap constrained check upon isolation (also unblocks lazyfree) | +0/-6 |
| `75d4c3f5fb98` | **use the common routine for dirty/writeback reactivation** | +0/-19 |
| `f37d3708b676` | **simplify and improve dirty writeback handling** (move the dirty flush into the loop) | +16/-25 |
| `32d87083ee97` | remove no longer used reclaim argument for folio protection | +4/-7 |
| `6cbdd9726fb5` | use folio_mark_accessed to replace folio_set_active | +25/-9 ⚠ touches `mm/swap.c` |

1. **Problem**: the loop recomputes the scan number every iteration and couples
   that calculation to aging and rotation; dirty-writeback handling differs
   from classical LRU (dirty pages are moved to the second-oldest generation
   instead of being reactivated, so they keep reappearing at the LRU tail);
   the dirty flush only runs after the whole reclaim loop, so it rarely
   triggers. Production saw OOM caused by a passive flusher. `12316f7902f8`
   matters most for a phone: once aging fires the reclaimer aborts, so under
   concurrent reclaim **every** reclaimer can fail, worst case an early OOM.
2. **Benefit** (upstream, YCSB/MongoDB — server load): the series reports "up
   to ~30% increase in some workloads like MongoDB with YCSB and a huge
   decrease in file refault, **no swap involved**. Other common benchmarks have
   no regression, and LOC is reduced, with less unexpected OOM, too".
   `f37d3708b676` measured alone: throughput 62485 → 80857 ops/s (**+29%**),
   average latency 500.97 → 386.65 us (**−23%**), pgpgin 159.3M → 112.2M
   (**−30%**), **workingset_refault_file 34.5M → 19.5M (−43%)**. The last is
   the phone-relevant one: refault feeds LMKD and Android's memory policy, and
   pgpgin is storage read-in. **No Snapdragon data; no device-side speedup
   claimed.**
3. **Already in 5.15?** No. 5.15's MGLRU is the 6.1 minimal implementation plus
   Batch 37-mglru's port of the v6.14 series (`9cbfd1c3c83b` cover, 6 groups).
4. **Difficulty**: hard, but less than it first looks. I initially assumed
   5.15 had only a single `isolate_pages()` and needed the 6.x scan/evict split
   introduced first — **measurement disproved that**: 5.15 already has
   `isolate_pages()` (`mm/vmscan.c:4800`, which calls `scan_pages()`) and
   `evict_pages()` (`:4841`, which calls `shrink_page_list()`), a one-to-one
   match for 7.2's `scan_folios()`/`evict_folios()`. The difference is only
   page-vs-folio form and the scan-budget arithmetic, i.e. exactly the shape
   rewrite Batch 37-mglru already performed for the v6.14 series. Two members
   were additionally confirmed directly portable: `75d4c3f5fb98` deletes a
   branch that exists verbatim in 5.15's `evict_pages()`
   (`PageReclaim() && (PageDirty(page) || PageWriteback(page))`), and
   `6e9be217a3ce` has a counterpart in `isolate_pages()`'s `scanned` return
   chain.
5. **Verdict**: **strongly recommended** — the only systematic, quantified,
   genuinely-live (`CONFIG_LRU_GEN=y`) MM performance series in v7.2. Risk: the
   numbers are server-side and need a device A/B to confirm direction.

### 2. `aaa98b100ea8` — mm/vmstat: don't take the zone lock reading /proc/buddyinfo

`frag_show()` passes `nolock=false`, but `frag_show_print()` only reads
`zone->free_area[order].nr_free`, so every buddyinfo read takes every zone's
`spin_lock_irqsave`. Android's lmkd/dumpsys and vendor fragmentation monitors
poll this file precisely when the allocator is hottest. No benchmark numbers,
but one fewer zone lock + IRQ-off window per read. Same defect on 5.15.
Difficulty: **trivial** — flip one boolean argument `false` → `true`, and the
print function provably reads a single `unsigned long`. Best value-per-risk in
the whole population.

### 3. `b001cf7d16dd` — mm/page_alloc: replace kernel_init_pages() with batch page clearing

With `init_on_alloc` on, `kernel_init_pages()` clears pages one at a time via
`clear_highpage_kasan_tagged()`, paying a `kmap_local_page()`/`kunmap_local()`
pair per page and preventing the arch primitive from working on a contiguous
range. Upstream: allocating 8192 x 2MB HugeTLB (16 GB) with `init_on_alloc=1`
went 0.445s → 0.166s (**−62.7%, 2.68x**); Graph500 kernel time −50.3% (64C128T)
and −39.0% (16C32T).

On 5.15 the target exists verbatim and **really runs**: `mm/page_alloc.c:1398-1412`
is the per-page loop (named `kernel_init_free_pages()` there, called from
`post_alloc_hook()`), and `CONFIG_INIT_ON_ALLOC_DEFAULT_ON=y` +
`CONFIG_KASAN_HW_TAGS=y` makes `kasan_has_integrated_init()` false, so `init`
stays true and **every allocation walks the loop**. Naming caveat: 5.15's
contiguous-clear primitive is `clear_huge_page()` in `include/linux/highmem.h`
(7.2's `clear_pages()` is the later rename), and that header is not in the
reference trees, so the helper name must be confirmed on a real checkout
before anchoring. Difficulty: moderate (+11/-7, but a highmem helper is
involved and the call site is inlined in `post_alloc_hook()`).

### 4. `9b0fcac3cfe7` — mm/filemap: don't count FAULT_FLAG_TRIED retries as mmap hits

`filemap_map_pages()` decrements `ra->mmap_miss` for every PTE it maps. When
the synchronous-mmap-readahead fault returns `VM_FAULT_RETRY` and the retry
finds the folio the same miss brought in, the decrement cancels the miss it
should have recorded, so the counter stays below its increment side, the "this
file is random, stop read-ahead" test stops firing, and evicted pages get read
in over and over. 5.15's own comment at the site documents the same
over-crediting ("If it fails, we'll come back to filemap_fault()
non-speculative case which will update mmap_miss a second time. This is not
ideal").

Upstream, 20 GiB larger-than-memory random access: 223.4 GiB read / 101.3 s →
**1.01 GiB / 4.79 s** (~21x less I/O); stride-2053 409.6 GiB / 193.7 s →
0.97 GiB / 3.69 s; stride-4099 406.5 GiB / 134.2 s → 0.98 GiB / 3.50 s;
sequential unchanged. Difficulty: **trivial**. This is the highest
value-per-risk item of the 366, and it continues the same accounting line as
Batch 30's `readahead_mmap_miss_race`.

**5.15 site — corrected.** 5.15 carries *two* `mmap_miss` decrements and the
first survey pass picked the wrong one:

| 5.15 site | shape | mainline analogue | touched by `9b0fcac3cfe7`? |
|---|---|---|---|
| `filemap_fault()`, ~`:3121` | `if (!(vmf->vma->vm_flags & VM_RAND_READ) && ra->ra_pages) { ... WRITE_ONCE(ra->mmap_miss, --mmap_miss); }` | `do_async_mmap_readahead()` (today's `mm/filemap.c:3477`) | **no** |
| `filemap_map_pages()`, ~`:3421` | `if (mmap_miss > 0) mmap_miss--;` on a function-local counter, written back once at the end | the block upstream patches (`mm/filemap.c:3984`) | **yes** |

The two are easy to conflate: both read `ra->mmap_miss`, both sit on the
readahead path, and the `filemap_fault()` block's own comment ("we'll come
back to filemap_fault() non-speculative case which will update mmap_miss a
second time. This is not ideal") describes the same over-crediting. The
original survey text said the decrement was "at `mm/filemap.c:3121-3125` …
inside `filemap_map_pages()`' per-PTE loop" — the line number is the
`filemap_fault()` one and the function name is wrong, so the first graft
attempt patched the wrong site with the right-looking anchor. Batch 38 now
edits `filemap_map_pages()`, and `implementation_audit.py` pins it there with
a forbidden-needle on the old text so the mistake cannot recur silently.

**Scope limit.** mainline's block also carries `(map_ret & VM_FAULT_NOPAGE)`
and `!folio_test_workingset(folio)` terms; 5.15's unconditional per-PTE
decrement has neither (it decrements for every page in the `do`-`while`,
including ones whose PTE was already present). Only the new
`FAULT_FLAG_TRIED` term is portable without restructuring the loop, so only
that term is added — the pre-existing over-decrement stays as upstream found
it. The edit can only ever *skip* a decrement (the `mmap_miss > 0` bound check
is preserved), so it cannot introduce an underflow.

Its pair `0b9c0aeba938` (count only the faulting address as an mmap hit) is
**not** grafted: it deletes the decrement from
`filemap_map_folio_range()`/`filemap_map_order0_folio()`, neither of which
exists on 5.15, and the 5.15-native equivalent has to be hand-written against
`filemap_map_pages()`' per-PTE loop — easy to get subtly wrong.

### 5. `e923bd21058e` — mm/huge_memory: unlock i_mmap_rwsem before releasing after-split folios

`__folio_split()` dereferenced `mapping` after unlocking/freeing the
after-split folios, so a concurrent `evict()`/`iput()` could RCU-free the inode
before `i_mmap_unlock_read()` — a KASAN slab use-after-free. Reachable via
`memory_failure()` splitting a poisoned shmem-THP tail past EOF.

Same defect shape on 5.15 (`__split_huge_page()` takes `i_mmap_lock_read()` at
`mm/huge_memory.c:2680` and releases at `:2758`, across the call that frees
pages); only the lock name differs. **Upstream already backported it to
5.15.y** as `f87c08060818` (2026-08-19), and the repo policy is to mirror the
5.15.y text rather than the mainline version, so the graft is a verbatim copy
plus a mechanical `i_mmap_rwsem` → `i_mmap_lock` rename. Verified on the
android13-5.15-lts tree (SUBLEVEL 216): all three hunks are already present, so
that row belongs in `PRE_APPLIED`.

### 6. Three small latency/lock-contention fixes

**(a) memcg dying bailouts** — `0beeaf14e7b9` (`memory.high`),
`e13f634f50d5` (`memory.max`), `757dd8193f6c` (`memory.reclaim`),
`10228e0a5123` (memcg v1 `limit_in_bytes`/`memsw.limit_in_bytes`/`force_empty`).

These handlers reclaim synchronously in the writer's context while holding a
kernfs active reference. If a concurrent `cgroup_rmdir()` gets there first it
blocks in `kernfs_drain()` under `cgroup_mutex`, and because the writer is
still reclaiming it never finishes, so everything else queues behind
`cgroup_mutex`. Upstream measured a 159 s `cgdelete` and a 182 s hang for an
unrelated `/proc/<pid>/cgroup` reader. On a phone this is exactly the
app-teardown stall, and `MAX_RECLAIM_RETRIES`' no-progress guard does not cover
the slow-device case.

5.15 has all four loops with the same structure (`memory_high_write()`
`mm/memcontrol.c:6399`, `memory_max_write()` `:6448`, `mem_cgroup_resize_max()`
`:3433`, `mem_cgroup_force_empty()` `:3575`), each already carrying a
`signal_pending()` guard; `memcg_is_dying()` is absent, so it is added to
`include/linux/memcontrol.h` (upstream's location, `CONFIG_MEMCG` block plus an
`#else` stub) testing the `CSS_DYING` flag directly — 5.15's `kill_css()`
(`kernel/cgroup/cgroup.c:5720`) sets `CSS_DYING` **before** `css_clear_dir()`,
which is the ordering the fix depends on. Note 5.15 has no
`mm/memcontrol-v1.c`: the v1 loops live in `memcontrol.c`. The proactive-reclaim
bail-out must be placed **after** Batch 37's `proactive_reclaim_suspend_abort`
rewritten block, not inside it — see the trap-5 note in
`CHANGELOG.md#batch-38`.

**(b) `a4519e5b648a` — mm/swap_state: remove unnecessary lru_add_drain() from
readahead.** `swap_cluster_readahead()` / `swap_vma_readahead()` end with a
2.6.12-era unconditional `lru_add_drain()`. The folios sit in the per-CPU batch
and drain naturally, so the flush only buys a `lruvec_lock` acquisition (and a
possible IPI) per swap-in readahead — upstream measured ~28k calls/min on a
176-CPU host under memory pressure. This is the **only** one of the five
lru-drain-removal commits with a 5.15 target (`mm/swap_state.c:659` and `:831`
still carry the calls, comment intact); the other four target the folio-era
anon-fault path 5.15 does not have. Difficulty: trivial (delete two lines).
Because it is a line deletion whose `new` would be a prefix of `old`, both
anchors carry the trailing `skip:` label and blank-line/comment context so the
edit cannot short-circuit to `already_present` (`docs/group_recipe.md` trap 1).

**(c) `25f52e812168` — mm/vmscan: report RCU-tasks quiescent states in
shrink_lruvec().** The scan loop only called `cond_resched()`, a no-op on a
PREEMPTION kernel, and involuntary preemption is not a Tasks-RCU quiescent
state, so a task in long reclaim becomes an `rcu_tasks` holdout and stalls
grace periods for minutes. `cond_resched_tasks_rcu_qs()` exists in 5.15
(`include/linux/rcupdate.h`) and is an **unconditional** macro
(`do { rcu_tasks_qs(current, false); cond_resched(); } while (0)`), so it
always compiles and `rcu_tasks_qs()` no-ops when Tasks-RCU is off. Upstream
backported it to 5.15.y as `4cdc1bdf4094` (2026-09-14). On this target the GKI
config enables `CONFIG_TASKS_TRACE_RCU` (for BPF) rather than
`CONFIG_TASKS_RCU`, so the graft compiles to the same `cond_resched()` as
before: **no device-side benefit is claimed.**

---

## Worth considering

| sha | subject | assessment |
|---|---|---|
| `e1c345582c97` | filemap: fewer unnecessary xarray lookups in `filemap_get_read_batch()` | App cold start (APK/dex/oat mmap reads) is this hot path; upstream's 4K read test had the function's own overhead drop 2.91% → 2.53% (−13% of itself; small in absolute terms). 5.15 tests the boundary only after `xas_next()`, missing the order-0 early return. 5.15 uses a pagevec loop, so this needs hand re-derivation — not a cherry-pick |
| `32cd1afeca96` | same idea for `filemap_get_folios_contig()` | 5.15 has no such function (the analogue is `filemap_get_pages()`, `:2545`). Only worth doing alongside `e1c345582c97` |
| `9f2fd03c9bde` | page_alloc: use existing highatomic reserves on the buddy fastpath | Reduces to one line (`ALLOC_HARDER` → `ALLOC_HIGH` in `rmqueue_buddy()`, `mm/page_alloc.c:3892-3909`). But the win covers only non-`__GFP_ATOMIC` `__GFP_HIGH` order>0 allocations, which are rare on a phone; the only evidence is a UDP/NAPI test. Also lands in the AOSP rmqueue region with vendor deltas |
| `5af3f83dcf2e` | shmem: fix data-race in `shmem_fault` | syzbot-reported; six `READ_ONCE`/`WRITE_ONCE` sites, no structural or config dependency, applies almost verbatim on v5.15.194 (the six unannotated sites are at the same places). tmpfs/ashmem fault paths are hot on Android, but **expect no measurable speedup** |
| `bc34e87a51d9` | mm, swap: delay and unify memcg lookup and charging for swapin | Touches `include/linux/memcontrol.h`, `mm/internal.h`, `mm/memcontrol.c`, `mm/swap_state.c` — all in the reference trees, so 5.15 has a carrier. Self-described "no user-observable behaviour change", so more refactor than win |
| `bebee474c1c1` | mm, swap: move common swap cache operations into standalone helpers | `mm/swap_state.c` only (+100/-46); has a carrier. Looks like a refactor but is a prerequisite for `bc34e87a51d9` |
| `838376c60df0` | memcontrol: hoist `pstatc_pcpu` assignment out of the CPU loop | memcg stat-refresh micro-optimisation. Upstream says only "no functional change", no numbers — benefit dubious |
| `65180e9663c7` | memory: flatten `alloc_anon_folio()` retry loop | `mm/memory.c` +17/-17. Self-described "No functional change intended", a clean-up for a later patch — value lies in what follows |
| `23378be820a3` | page_alloc: don't overload migratetype in `find_suitable_fallback()` | `mm/compaction.c` + `mm/internal.h` + `mm/page_alloc.c` (+35/-20), all in the reference trees. The function body and `__rmqueue_fallback()`'s body contain **no** vendor hooks, so re-anchoring is lower-risk than the deferred Gorman series. But the cover letter says "No functional change intended" — upstream itself calls it a refactor |
| `32a2b73ec232` | compaction: cap `compact_gap()` at `COMPACT_CLUSTER_MAX` | +5/-5, `include/linux/compaction.h` + `mm/vmscan.c`. Tiny, but it is the tail of a direct-compaction series whose earlier parts are not in 7.2 |
| `4c0ed883e051` | page_alloc: fix `defrag_mode` for non-reclaimable allocations | `mm/page_alloc.c` +12/-1. Belongs to the defrag_mode series 5.15 does not have; needs a check that the site is separable |
| `0fc52deec106` | slub: detach and reattach partial slabs in batch | The only slab change with real numbers (will-it-scale mmap **+2~5%**). But it is a **rewrite, not a port**: 5.15's `get_partial_node()` has no `pc->slabs`. A smaller 5.15-native win exists — batch `put_cpu_partial()`'s N `local_lock_irqsave` calls into one |
| `0453f857eb32` | damon/reclaim: add `autotune_monitoring_intervals` | `CONFIG_DAMON_RECLAIM=y`, but `mm/damon/` is not in the reference trees and upstream v5.15 has no `mm/damon/reclaim.c` — the target's copy is a vendor/ACK addition of unverified shape. **Blocked** on fetching that file |
| `66366d291f66` | swap: `cond_resched()` in `swap_reclaim_full_clusters` to prevent softlockup | zram/swap full-cluster reclaim is a long loop. 5.15's equivalent path is in `mm/swap_slots.c`, not in the reference trees |
| `f2a950170f7a` | vmpressure: skip socket pressure for costly order reclaim | Low priority; message not retrieved |
| `88d6f128d06d` | track DONTCACHE dirty pages per `bdi_writeback` | Saves one `mapping->host` dereference, but lands in `page-writeback.c` / `backing-dev.h`, neither in the reference trees |
| `e26f7a91de5c` + `430e4cdcc600` | fix anon-only reclaim evicting file pages when `swappiness=max` | Clean reproducer (64 MB of file cache wiped by `echo "64M swappiness=max" > memory.reclaim`). **Conditional**: the module's `memory.reclaim` must first accept the string `max`, otherwise the bug being fixed is unreachable |

**Already landed — do not re-recommend**: `dc37771a43d4` (Batch 37's
`proactive_reclaim_suspend_abort`) and `9669b87065a6` (Batch 37's
`lru_add_drain_dead_folios`).

---

## Not recommended

### A. The premise does not exist / the target function is absent on 5.15

- `59e88952a827` + `9909b088b1f0` (zsmalloc lock-free class lookup): 5.15's
  `mm/zsmalloc.c` contains **no `pool->lock` at all** (grep = 0 on all five
  baselines), so the read side they delete is not there. This confirms the
  repo's existing Batch-33 exclusion, which **still holds**. `7ef28e8b8142`
  landed as Batch 33.
- `f276408a8167` (zsmalloc `zs_page_migrate()` lock release order): 5.15 is
  already LIFO.
- `b89a64105622` (`madvise_collapse` underflow): 5.15 has **no
  `MADV_COLLAPSE`** (grep over `mm/`, `include/`, `arch/` = 0).
- `878f41243c0d` (page_isolation safe folio reads): 5.15 has no
  `page_is_unmovable()`; the function post-dates 5.15.
- `7441d6348c70` (`snapshot_page()` reads `__page_2`): no such function.
- `c494788faffe` (GUP-fast fallback): no `gup_fast_folio_allowed()`.
- `bd1e4c4aa469` (hugetlb vmemmap empty-list TLB flush): no
  `__hugetlb_vmemmap_optimize_folios()`.
- `9218800076ec` (mincore `xa_is_value()`): no `mincore_swap()`.
- `d4c63a378b01` (mlock `walk_page_range_vma()`): the helper is ~6.6 and absent.
- `f928145cbcb5` (page_io: don't nest queue_lock under rcu): pure prep for a
  7.1 blkcg rework; no phone-visible effect.
- `efe8f86c0916` (migrate: report Tasks-RCU QS):
  `cond_resched_tasks_rcu_qs()` is a no-op on this GKI config, and 5.15 has no
  `migrate_pages_batch()`.
- `8725ae13f0ca` + `881adc51b29b` (percpu: avoid IO/FS reclaim): fix a 6.16
  regression. 5.15's `is_atomic = (gfp & GFP_KERNEL) != GFP_KERNEL` already
  sends `GFP_NOFS`/`GFP_NOIO` down the atomic branch, so the described cycle
  cannot form; and `881adc51b29b` also needs the 6.18
  `memalloc_apply_gfp_scope()`.
- `7e8756d7ad22` (defrag_mode non-movable reclaim storm) and `1b4b697a5743`
  (non-movable direct compaction): both depend on the `defrag_mode` /
  `__rmqueue_claim()` machinery 5.15 lacks; relaxing the pollution gate alone
  carries real migratetype-pollution risk. The sibling prep commits
  `aee220f565cc`, `819bfbd47e49`, `7ae5a5d8eca5`, `04ad9908b031` are excluded
  too (`819bfbd47e49` alone would trip the new `VM_WARN_ON_ONCE` because 5.15's
  compaction still writes `set_page_private(page, order)`).
- `7da7d599b8a8` (compaction `free_pages_prepare()` handling): fixes a 6.13+
  >0-order folio-compaction path; 5.15's `compaction_free()` never calls it.
- `52dcc33881e5` / `3105ae628fb7` / `21be908d39e3` / `03eaf4c44512` /
  `3a01af828138` and the whole nolock/frozen-pages cluster: 5.15 has no
  `free_pages_nolock()`/`alloc_pages_nolock()`/`FPI_TRYLOCK`/`ALLOC_TRYLOCK` —
  all prep for a 7.1 feature.
- `bf4ade9dbd76` / `afe9ae3fda96` (memcg objcg sequence): target the 6.12+
  slab-common rewrite; 5.15's `obj_cgroup_release()`/`drain_obj_stock()` still
  use `obj_cgroup_uncharge_pages()`.
- `4fe4ea4cbe75` / `4259767463b3` (uffd-wp batching): simultaneously missing
  batched file-folio unmap (6.13) and uffd-wp PTE markers (6.6).
- `13a1e1a61885` (revert "limit filemap_fault readahead to VMA boundaries"):
  5.15 never had the regression. Verify only.
- `baff6d2d2708` (constify nodemask): pure type refactor, 11 files, collides
  with AOSP vendor signatures.

### B. Pure refactor / no behaviour change

- The slab `alloc_flags` / `slab_alloc_context` / `SLAB_ALLOC_NOLOCK` series
  (`203890bbd292`, `74d224ac9288`, `574d3961d37e`, `c90a004a9372`,
  `b5ecc070d196`, `d659903674af`, `ef7f97aa2ce8`, `f39062e83cd5`,
  `f6d50ab29afd`, `5fd1c77e97de`, `1a787779fe2a`, `6dbded46e447`,
  `30222639602c`, `71553a606759`, `6372aac4d41f`) — all paving the way for
  `kfree_rcu_nolock()`.
- `3bc999d944b3` (`kfree_rcu_nolock()`), `acc6fdade62c` (`struct
  kvfree_rcu_head`), `69abda97a09b` (deferred free handling sheaves): consume
  a per-cpu sheaf allocator 5.15 does not have.
- `d4404b0f5b8b` / `c4ec6cb55750` / `7def2e8549e5` / `060324c55310` (slab
  objcg / obj_ext): depend on the 6.13+ obj_ext split.
- `af9ea231c0b4` (slub bulk remote free lost objects): fixes
  `free_to_pcs_bulk()`, absent on 5.15 (which uses
  `build_detached_freelist()` + `slab_free()`), and upstream itself says it is
  untriggerable.
- `29b3b6cde5f2` / `e1fa26489025` / `efa6a5b7bbb1` (slub NUMA policy
  dedup / `free_to_pcs_bulk` refactor): target functions absent on 5.15.
- `9338b189be68` (slub pfmemalloc into the barn): no barn/sheaf on 5.15.
- `dc795d4c0282` (slub: defer freelist construction after bulk allocation):
  the −42.6..−70.5% ns/obj headline is a synthetic best case for a bulk-refill
  path 5.15 does not have (5.15's `allocate_slab()` builds a freelist that
  becomes the cpu freelist and is consumed — nothing is thrown away), and the
  natural implementation would move freelist construction **into**
  disabled-interrupt regions (`load_freelist` runs under `local_lock_irqsave`)
  — a net latency loss on a phone. **Prototype only.**
- `7e230738746c` (node partial slab state helpers): scaffolding for
  `0fc52deec106`, depends on `struct slab`'s `slab_set_node_partial()`; 5.15's
  `__add_partial()`/`remove_partial()` are already the natural shape.
- `ba7425312607` + `93a51a74d7de` (optimistic `__slab_try_return_freelist()` +
  simplified slab return): the former needs the 7.x lockless-freelist-counters
  refactor; the latter is only valid once the former lands. **Drop both
  together.**
- `e562904fc173` (empty sheaf helpers for oversized sheaves): the sheaf
  abstraction is absent on 5.15.
- `648927ceb840` (red zoning / `orig_size` zeroing): fixes code from 6.2 on;
  5.15 has no `zero_size`.
- `19b206b9534a` (`kmalloc_nolock` retry preserves size): `kmalloc_nolock()`
  is 6.6+, absent on 5.15.
- `feb662d9168b` (compiler-assisted slab cache partitioning): needs new
  compiler support, and `gki_defconfig` enables no partition option.
- The whole khugepaged mTHP series (`90ed32d00054`, `b7f16963efe7`,
  `472ebd691d97`, `d04fa025671e`, `45b310faeefa`, `5a9c05d86683`,
  `da98790891a4`, `e22f2fe72c74`, `3a460f245f00`, `2c7d0fa84dcf`,
  `5fea7eb1a331`, `c85418be9666`, `b5b8b329c4b4`, `ceaa0b641311` — 14 commits,
  all-or-nothing): requires porting the 6.1/6.2-era multi-size THP stack first
  (`huge_anon_orders_*`, `thp_vma_allowable_orders()`, per-size sysfs,
  `MTHP_STAT_*`), thousands of lines. There is already a measured verdict in
  this repo: Batch 37-mglru abandoned `a52dcec56c5b` (PTE-mapped large folios)
  because "the benefit depends on large folios and this device is 4KB +
  madvise-only THP ≈ 0". `b89a64105622` must move with `90ed32d00054`, but
  `MADV_COLLAPSE` does not exist on 5.15, so it is directly inapplicable.
- "Remove CONFIG_READ_ONLY_THP_FOR_FS and enable file THP for writable files"
  (Zi Yan, 14 commits: `1b6444331beb`, `4e3d769bf0fc`, `cd2d3d1f26c2`,
  `8a088263a097`, `7c16f524f20c`, `efc36a715383`, `e4df0f37bd95`,
  `044925f9b565`, `15e2258e2555`): `gki_defconfig` does **not** set
  `CONFIG_READ_ONLY_THP_FOR_FS`, and no 5.15 filesystem supports PMD-order
  large folios, so the entire series is dead on this configuration.
  `1b6444331beb` also breaks the khugepaged selftests and cannot land alone.
- The page_alloc header-split / rename cluster (`d0850de699b0`,
  `a4f6c0f83d91`, `6f414dbf2178`, `5df11ba0eb5a`, `4b2b50a9f899`,
  `52b71fe00058`, `e47b037e4208`, `d413d243c3dc`, `cdea9364e477`,
  `ba26999e7c2e`, `2fc4c1d51d87`): prep for the 7.1 nolock allocator.
- `874611d193f2` (NUMA ratios only on sysctl write) and `4fd1c85cc031`
  (page_owner hardening): the former has no measurable benefit and no Android
  poller; the latter is `CONFIG_PAGE_OWNER` debug-only.
- `90f01f5d6ba5` (remove `page_mapped()`), `088a2353d714` (remove
  `PageWriteback` mentions), `c565c009d0c0` (use `mapping_mapped`),
  `6f64c06f4309` (merge writeout into pageout), `d94d0f9c153f`
  (`PAGE_`→`FOLIO_` rename), `0ba14428abc2` (`swap.c`→`folio.c` rename): pure
  clean-up. `6f64c06f4309` is the one to watch — the subject reads like a big
  reclaim change but it only merges `writeout()` into its sole caller
  `pageout()`.
- `0ef8faff490b`, `0c3a350d13ce`, `b9fe373e7d3c`, `15e2258e2555`: part of the
  READ_ONLY_THP_FOR_FS removal chain or with no 5.15 carrier.
- `747beac67911` (delete unused `wb_writeout_inc()`): dead code, no benefit.
- `9669b87065a6` (preemptively free dead folios during lru_add drain):
  **already landed** as Batch 37's `lru_add_drain_dead_folios`.
- `96d2d9acef49` (ksm: optimise `rmap_walk_ksm`): **inapplicable** —
  `CONFIG_KSM` is not set on GKI. (It carries an impressive number — 200–700 ms
  → <1 ms with 20,000 VMAs on one anon_vma — but the machine never runs it.)

---

## Verification

```bash
python3 -m py_compile scripts/*.py tests/*.py
python3 tests/stable_5_15_test.py
python3 tests/step_audit.py build/abk-trees/194        # structure + idempotency
python3 tests/implementation_audit.py build/abk-trees/194
bash tests/smoke.sh build/abk-trees/194                # two passes + rollback
```

Reference trees: `bash tests/fetch_sublevel_tree.sh android13-5.15-2025-12 <outdir>`
(fetches via `android.googlesource.com`, which is unaffected by the kernel.org
outage). The `config_gate_audit.py` lane needs a build artefact and runs at
release time.

Evidence boundary: every upstream number above is server-side (YCSB/MongoDB,
Graph500, 20 GiB larger-than-memory file tests, RADXA O6, Neoverse-V2). **No
Snapdragon measurement exists**, so none of it is stated as a device-side
result.
