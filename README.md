# ABK 5.15 LTS Backport

An ABK external `module_set` that grafts **feature / optimization /
structural** commits from the upstream `5.15.y` LTS stream
(v5.15.167..v5.15.218) onto the `android13-5.15` GKI baselines 5.15.167
(`android13-5.15-2024-11`), 5.15.178 (`-2025-03`) and 5.15.194 (`-2025-12`).
Pure security fixes are out of scope.

Every graft is a **bounded anchor script**: each group declares the tree
shapes it accepts, rewrites only exact-match regions through transactional
`replace_once` steps, and degrades to a reported status (`blocked_by_shape`,
`already_present`, …) when an anchor is absent — the kernel tree is never
half-patched. There are no `.patch` payloads to review or keep in sync; the
Python registry *is* the patch set, and every edit carries an
`ABK stable_515_backport:` marker that doubles as its idempotency anchor.
The marker rule is precise: module-introduced lines (new struct fields, new
helpers, new vendor hooks, new UAPI and config entries) carry the marker;
upstream-shape rewrites instead *are* their own target form, so idempotency
comes from the resulting text and a baseline that already carries the commit
is left byte-identical rather than touched up with a comment.

## Children

| child id | content |
|---|---|
| `stable_backport_core` | fd-table allocation conventions (5.15.191, incl. INT_MAX guard) and the 5.15.195 `replace_fd()` errno fix, page_alloc ALLOC_MIN_RESERVE semantics (5.15.171), THP `__GFP_THISNODE` no-reclaim (5.15.202), cpuset insane-config early bail-out (5.15.191), percpu pagelist lock-free reads (5.15.200), cgroup root_list RCU (5.15.168), cgroup destroy-wq split (5.15.194), per-memcg proactive reclaim via `memory.reclaim` (android14-6.1), zram recompression (android15-6.6 / 6.2 series) with a read-only `zstd` secondary compressor, the Batch 12 zram algorithm lock (read-only `zram.abk_comp_algo` / `zram.abk_lock_algo`; both algorithm stores become reported no-ops, so no runtime writer can reassign them — and a refused write would abort Android's `mmd_setup`, writeback included), zsmalloc zspage chain-size sizing (android15-6.6 / 6.2 series), `MADV_COLLAPSE` (android14-6.1), Batch 8 page_alloc fallback-mode reuse and claimability cleanup (android15-6.6 / 6.12), opt-in `RCU_NOCB_CPU_DEFAULT_ALL`, plus the module's defconfig lane that actually enables the recompression symbols |
| `stable_perf_backport` | NOHZ idle-balance series (5.15.174), PSI psi_flags migration (5.15.179), RT scan optimizations (5.15.202/.212), per-task kstack randomization via KABI slot 8 (5.15.210 — the slot probe knows all four `task_struct` shapes, including the lts one where AOSP owns slot 1), excess steal time dropped instead of carried forward (5.15.179, inert on bare metal by construction, live in a KVM/AVF guest), `__release_sock` cond_resched reduction (5.15.197), semaphore wake_q (5.15.180), blk-mq suspend wakeup abort (5.15.198), blk-mq quiesced elevator switch on queue reinit (5.15.209), PSI IRQ pressure tracking, PSI trigger kernfs polling, the per-cgroup PSI accounting switch `cgroup.pressure` (android14-6.1 — carried in the cgroup's own flags word, because `struct psi_group` is embedded in `struct cgroup` there and a new member would move the members after it; the ACK 6.1 psi_group pointer/parent rework stays unported), the PSI ONCPU state-mask sync (`TSK_ONCPU` is a bit of the state mask instead of a `tasks[]` counter, which also retires the `identical_state` guess in `psi_task_switch()`), lazy-preemption + mutex/rwsem wakeup vendor hooks (android14-6.1) |
| `stable_display_fix` | removal of the 5.15.185 `drm: Add valid clones check` encoder validation (the Concurrent Writeback series) from `drivers/gpu/drm/drm_atomic_helper.c`; the check makes every vendor `msm_drm` atomic commit fail with `-EINVAL` on 5.15.185+ (2025-07 / 2025-09 / 2025-12) and the lts branch, so the panel stays black while touch/fingerprint keep working; on 5.15.167/.178 (which never carried the check) the group reports `already_present` and writes nothing |
| `stable_backport_core` (Batch 9-1) | `dynamic_readahead_lowmem`: dynamic readahead (OPLUS/Xiaomi `mi_dynamic_readahead`) as a GKI built-in — a `core_initcall` in `mm/readahead.c` registers the `android_vh_ra_tuning_max_page` / `android_vh_tune_mmap_readaround` vendor-hook callbacks so low-memory background (cpuset "background") tasks get halved readahead windows and shrunk mmap read-around, behind `CONFIG_ABK_DYNAMIC_READAHEAD` with a `readahead.dynamic_readahead=0` runtime disable |
| `stable_backport_core` (Batch 13) | `customize_alloc_gfp_vh` + `gfp_pressure_fastfail`: the `android_vh_customize_alloc_gfp` vendor hook grafted verbatim from android15-6.6 (commit `4466afd69452`; declare in `include/trace/hooks/mm.h`, call between the nodemask restore and `__alloc_pages_slowpath()`, export in `drivers/android/vendor_hooks.c`), plus its ABK consumer: while `si_mem_available()` sits below `abk_gfp_fastfail_pct`% (default 50) of the summed high watermarks, slowpath attempts of order >= `abk_gfp_fastfail_order` (default 9 — the THP class) gain `__GFP_NORETRY|__GFP_NOWARN`, so a fragmented, nearly-full phone fails those requests into their callers' fallback after one direct-reclaim/compaction try instead of stalling the faulting task; knobs at `/sys/module/page_alloc/parameters/`, off with `page_alloc.abk_gfp_fastfail=0` |
| `stable_backport_core` (Batch 14) | zram **writeback correctness** (the branch froze this code in 2022 and never got the later fixes — `linux-5.15.y` at SUBLEVEL 220 does not have them either): `zram_wb_teardown` releases a backing device that was attached before `disksize` (upstream `74363ec674cb`; the upstream form deletes `zram_reset_device()`'s early return, this line calls `reset_bdev()` from `zram_remove()` instead, because `zram_reset_device()` is the anchor of the earlier recompression group), with the `zram_meta_free()` NULL-table guard; `zram_writeback_bounds` derives the `writeback_store()` scan bound and the `page_index=` range check **under** `init_lock` (upstream `894913e2d35c`, Cc: stable) so a racing reset that re-initialises a smaller `disksize` cannot walk past the new table, plus `cond_resched()` in the sweep (`424d0e5828ad`) -- and keeps PAGE mode's single-iteration bound through that derivation, because the loop counts iterations from `index`: without it `page_index=N` swept to the end of the device instead of one page, and `N>=1` walked `zram_slot_lock()` past `zram->table` and panicked the kernel on vermeer (v0.30.1); `zram_wb_limit_align` adds mainline's `rounddown(val, PAGE_SIZE / 4096)` guard so a 16 KiB-page build cannot underflow `bd_wb_limit` and silently switch the flash-wear cap off. Evidence, dependency graph and the explicit not-ported list (6.16 writeback ABI rework, bio batching, compressed writeback, `huge_idle`) are in `research/zram_writeback_plan.md` |
| `stable_backport_core` + `stable_perf_backport` (Batch 15) | **ABK_ABI_PATCH_SUITE absorption.** The suite-preference rule is retired: this module now carries that suite's optimization inventory itself, and the two are **mutually exclusive** (both claim `sched_entity` 1–4 and `request_queue` 1). Core gains `pid_alloc_hotpath_phase2` (`idr_preload(GFP_KERNEL)` single retry), `fd_alloc_hotpath` (the `abk_expand_files_needed()` precheck — the suite's capacity half is deliberately *not* ported because this child's `fdtable_alloc_conventions` owns that text and the suite's helper name is one of this module's own suite-detection markers), `close_range_hotpath` (bitmap walk in `__range_close()`), `slab_alloc_free_hotpath` and `hugepage_fault_alloc_fastpath`; perf gains `blk_mq_async_depth` (`request_queue` slot 1), the EEVDF family (`sched_entity` slots 1–4, registered `pick_logic`-before-`core_fields` so the slots are claimed only once the `fair.c` logic landed — `sizeof(struct sched_entity)` measured unchanged at 512 B under `CONFIG_WERROR=y`), `nohz_field_refinement` and `avg_idle_preemption_mode`. **Four latent defects in the suite's own implementations were fixed on the way in**: an `alloc_pid()` retry using `continue` in a descending loop (could return a pid whose `numbers[0].nr` was never written), a request count assigned into bfq/kyber's *per-word bit cap* (both throttles were dead code), an unconditional `rq->idle_stamp` sample that reads nanoseconds-since-boot on a CPU that never passed `newidle_balance()`, and a `reweight_entity()` whole-body anchor that matched no sublevel on 5.15.194/.216 and would have silently skipped while reporting success. The `step_audit` monkeypatch hole that had left every `scripts/batchNN_*.py` group (including Batch 14) unaudited was also closed. What could not be absorbed, with evidence, is in `docs/survey_suite_absorption.md` |
| `stable_backport_core` (Batch 31) | `arm64_pte_mkwrite_clean`: the 5.15.196 arm64 `pte_mkwrite()` dirty guard (mainline `143937ca51cc`, v6.18 — "avoid always making PTE dirty in pte_mkwrite()"; stable `8a2375b0e9b8`). `PAGE_SHARED` on arm64 is *clean by default* (`PTE_RDONLY\|PTE_WRITE`), so clearing `PTE_RDONLY` is exactly what makes a page hardware-dirty (`pte_hw_dirty()`): every caller that made a **clean** pte writable reported an unwritten page dirty, and `try_to_unmap()` turns that into `set_page_dirty()` at reclaim. The fix clears `PTE_RDONLY` only for an already software-dirty PTE. The target form is the **5.15.y** form (`pte_mkwrite()`, not mainline's `pte_mkwrite_novma()`, the name the v6.6 rename `2f0584f3f4bd` introduced, which anchors nowhere here); the live 5.15 call sites are the transient-unmap restorers (`remove_migration_pte()`, `do_numa_page()`, userfaultfd), not the commit message's `do_swap_page()` case, which pairs mkwrite with mkdirty on 5.15. Upstream-shape rewrite, so no ABK marker: on the lts baseline the file stays byte-identical and the group reports `already_present`. This is the module's first `arch/arm64` C group — `arch/arm64/include/asm/pgtable.h` joined the fixture lists |
| `stable_backport_core` (Batch 37) | the **memory-reclaim path**: six upstream commits on the `memory.reclaim` line, in dependency order. `0388536ac291` (v6.6) caps one reclaim call at `SWAP_CLUSTER_MAX` and `287d5fedb377` (v6.9, `Fixes:` the former) replaces that cap with a decaying batch `(nr_to_reclaim - nr_reclaimed) / 4` -- the pair is inseparable, because the fixed 32-page cap cost more in reclaim start/stop cycles than its accuracy bought (upstream: 13742 vs 67352 pages/sec on a full root-cgroup reclaim). `410abb20acae` adds `MIN_SWAPPINESS`/`MAX_SWAPPINESS` and `68cd9050d871` (v6.11, same series) adds the `swappiness=<val>` nested key to `memory.reclaim`, carrying the value to `get_scan_count()`/`get_swappiness()` through a new `sc_swappiness()` accessor so a proactive reclaimer no longer rewrites the global `vm.swappiness` to steer file-vs-anon balance; the manual documents the key (`cgroup-v2.rst`). `dc37771a43d4` (v7.2, `Fixes: 287d5fedb377`) lets the PM freezer interrupt a proactive reclaim -- the MGLRU inner loop gains the signal check and the handler returns `-ERESTARTSYS`, which restarts the syscall after resume instead of failing the suspend (the Android-measured suspend timeout this module's `cached_freeze_reclaim` line is about). `9669b87065a6` (v7.2) frees a page that is already dead before it reaches the LRU: the `lru_add` batch is filtered on its final reference and `release_pages()` tolerates the vacated slot, in 5.15's `pagevec`/`page` form (upstream's `folio_unqueue_deferred_split()` is deliberately not carried: a deferred-split page is always compound, and a compound page drains the `lru_add` pagevec immediately, so the entries that really linger are order-0 pages for which `page[2].deferred_list` is a neighbouring allocation). Everything in the chain but the last group rewrites `memory_reclaim()` -- text this module itself generates -- so each superseded group stops on a probe of its successor (AGENTS.md trap 5) |

Since Batch 3 the module also grafts selected **android14-6.1 ACK line**
features (the only 6.1 ACK branch): `memory.reclaim` proactive reclaim,
PSI IRQ tracking, PSI trigger kernfs polling, the per-cgroup `cgroup.pressure`
accounting switch, the ONCPU state-mask sync, and the lazy-preemption /
lock-wakeup vendor-hook families.
Those groups mirror the ACK 6.1 form adapted to the 5.15 baseline shapes and
keep the KMI untouched (heap-internal wrappers, percpu states, additive
tracepoints, and — for `cgroup.pressure` — an existing cgroup flag bit instead
of the ACK `struct psi_group::enabled` member, since `struct psi_group` is
embedded in `struct cgroup` on this baseline).

**Batch 15 absorbs the ABK_ABI_PATCH_SUITE optimization inventory**, so this
module no longer defers to that suite — it carries the features itself and the
suite must not be co-injected (both would claim `sched_entity` 1–4 and
`request_queue` 1).  See "Suite absorption" in `docs/porting_policy.md` for the
absorbed group list and `docs/survey_6_1_ack.md` / `docs/survey_6_6_ack.md` for
the provenance of the old exclusion list.

Batch 6 adds two 6.2-origin pieces that only exist in the android15-6.6
line (`zram recompression`, `ZSMALLOC_CHAIN_SIZE` zspage sizing), the 6.1
`MADV_COLLAPSE` synchronous THP collapse, and the first real use of the
`DEFCONFIG` the CLI always demanded: `config_enablement` enables the module's
own symbols by default and, with `ABK_515_DEFCONFIG_ALIGN=1`, also the
android15-6.6 GKI config deltas whose 5.15 code already exists
(`LRU_GEN_ENABLED`, BBR, `BLK_WBT`, cgroup IO throttling, delay accounting);
`ABK_515_DEFCONFIG_ROM=1` adds the ROM-integration tier
(`CONFIG_ZRAM_WRITEBACK=y`, off by default — see the runtime-companion section),
and `ABK_515_DEFCONFIG_PSI=1` adds the per-cgroup PSI tier: the baseline tree
puts `cgroup_disable=pressure` in `CONFIG_CMDLINE`, which switches per-cgroup
accounting off device-wide *and* hides every `CFTYPE_PRESSURE` file — including
Batch 21's `cgroup.pressure` — so the tier drops that one token to make the
switch, and the companion's PSI policy, reachable (off by default: with the
token gone every group pays until the policy pass turns it off again).
Unsupported lineage is now a real gate too: outside android13-5.15 every group
reports `report_only` and nothing is written unless `ABK_515_ALLOW_UNSUPPORTED=1`
is set.

Batch 8 adds `pagealloc_fallback_reuse` and the opt-in
`rcu_nocb_cpu_default_all` source graft. The 5.15-shaped allocator separates
fallback claiming from single-page stealing, reuses the successful phase across
one locked `rmqueue_bulk()` refill, and updates the compaction caller for the
new `find_suitable_fallback()` result convention. The AOSP page-allocation
vendor hooks and 5.15 `steal_suitable_fallback()` behavior remain intact. The
RCU option remains disabled by default until device benchmarks pass. The
remaining Batch 8 entries are conditional device benchmarks, a separate
AutoFDO build project, or independent MM/VFS and sibling-suite work.

KMI red lines are built in: new exported-struct fields only ever reuse free
`ANDROID_KABI_RESERVE` slots (this module uses `task_struct` slot 8), and
every group reports instead of forcing when the tree does not match. See
`docs/survey_5_15_168_218.md` for the candidate analysis and `plan.md` for
the living backlog.

## Injection (ABK CI)

Trigger `kernel-a13-5-15.yml` with any of the three supported android13-5.15
combinations and put the module into `custom_external_modules`:

| `sub_level` | `os_patch_level` | AOSP branch |
|---|---|---|
| 167 | 2024-11 | `deprecated/android13-5.15-2024-11` |
| 178 | 2025-03 | `deprecated/android13-5.15-2025-03` |
| 194 | 2025-12 | `android13-5.15-2025-12` |

```
set:https://github.com/fanziyun/ABK_5.15_BACKPORT.git#stable_backport_core;after_patch|set:https://github.com/fanziyun/ABK_5.15_BACKPORT.git#stable_perf_backport;after_patch|set:https://github.com/fanziyun/ABK_5.15_BACKPORT.git#stable_display_fix;after_patch
```

The display fix child is independently injectable: a build that only needs
the black-screen fix can carry just
`set:https://github.com/fanziyun/ABK_5.15_BACKPORT.git#stable_display_fix;after_patch`.

One injection string covers all three: the engine gates on text anchors, never
on the sublevel, so a group whose upstream commit the baseline already carries
reports `already_present` instead of `applied`. On 5.15.178 that is one group
(the 5.15.174 NOHZ series); on 5.15.194 it is six (fd-table conventions,
cpuset bail-out, cgroup destroy-wq split, NOHZ series, excess steal time,
semaphore wake_q). The per-sublevel expectations are in
`tests/sublevel_matrix.py` and `docs/porting_policy.md`.

The baseline-neutral rule also works across the newer android13-5.15-lts
tree: `tests/sublevel_matrix.py` keeps an `.216` fixture row (re-keyed from the
`.211` one when the rolling branch moved) with the groups that branch carries
already. **Since Batch 19 that row has no `KNOWN_DEBT` entry at all** — the two
`.211` blockers (`randomize_kstack_pertask` KABI-slot drift,
`blk_mq_suspend_wakeup_abort` shape) were closed rather than recorded, so every
group of every child is expected to land on all four supported baselines.

The children read `KERNEL_ROOT`, `DEFCONFIG`,
`CUSTOM_EXTERNAL_MODULE_STAGE` and `ABK_BUILD_*` from the ABK environment.
Both children are idempotent; running them is safe at any point after the
kernel patches are applied.

## Runtime companion (KernelSU module)

The grafts ship mechanisms; a mechanism nothing triggers changes nothing. On the
device this repository targets (Redmi K70 / `vermeer`, android13-5.15-lts
5.15.215) the measurement was blunt: `mm_stat` showed one page ever stored,
`io_stat` was all zeros, and the ROM's own zram owner had left the primary
compressor on the dominated `lz4hc` before `disksize` — after which the node is
`-EBUSY` and no userspace can repair it.

`ksu/abk_runtime_tunables/` is a flashable KernelSU module that closes that gap:

* it keeps the **measured** zram policy in force — primary `lz4kd`, secondary
  `zstd`, `mem_limit` 25% of RAM, swap size inherited from the ROM — with no
  configuration knob for any of it. Since the `zram_algo_lock` graft the policy
  is enforced *in the kernel* (`zram.abk_comp_algo` / `zram.abk_lock_algo`, both
  `0444`; both algorithm stores accept a write and keep the locked value), so a
  root writer cannot switch it either, and the module does not have to fight for
  the pre-`disksize` window. On a kernel without the lock the module owns the
  bring-up instead and re-checks it every `zram.reassert_interval_sec`;
* it preserves a `CONFIG_ZRAM_WRITEBACK` attachment across any rewrite it has to
  do — the backing device lives in that same pre-`disksize` window and `reset`
  drops it, which is why the two used to exclude each other — and attaches a
  sparse backing file itself when writeback is available and unowned
  (`zram.writeback=auto`). On the reference device's stock kernel writeback is
  simply absent (`CONFIG_ZRAM_WRITEBACK` off, `vendor.zram.disable=1`,
  `mmd.setup_complete` unset), so `ABK_515_DEFCONFIG_ROM=1` is the tier that
  turns it on — and it also ships the kernel-domain SELinux rules that path
  needs (`allow kernel zram_data_file file { read write }` and
  `allow kernel extm_data_file file { read write }` -- one per backing-file
  type, because the ROM's extm backs its loop device with `extm_data_file`
  rather than `zram_data_file` -- submitted at post-fs-data), because the reads
  and writes of a backing file happen in the loop worker, a kernel thread in
  `u:r:kernel:s0`. On the ROM tier under
  Enforcing the missing rule made writeback report success while moving nothing,
  the ROM's own backing device included (Batch 18);
* it drives age-marked recompression sweeps through the kernel's async worker
  (this is the only part that is on by default);
* it reports — or optionally applies — the remaining runtime knobs: MGLRU, THP,
  `vm.swappiness`, the schedutil smart-freq policy, dynamic readahead,
  cgroup-v1 proactive reclaim, and per-cgroup pressure (PSI) accounting;
* it switches off per-cgroup PSI accounting for the groups nobody reads
  (`psi.cgroup`, Batch 25 — the `cgroup.pressure` node is this module's own Batch 21
  graft, so the batch adds a policy and not a graft). Measured on the target before any
  of it was written: 452 groups carried the node, 314 held tasks, and the only pressure
  readers on the device (`lmkd`, `system_server`, `mimd`) all had the **global** file
  open, which the root group serves and this switch does not touch. So the root group is
  never written, the tool never writes `1` (upstream's version of the switch frees the
  group's per-cpu windows and the companion cannot tell the kernels apart), and the
  shipped default stays `keep` until the two-boot A/B in
  [`docs/psi_field_protocol.md`](docs/psi_field_protocol.md) decides — the same
  measurement is what proved `auto` (empty groups only) a no-op worth saying out loud,
  and why `bin/abk_psi_bench.sh` exists to take that measurement in one command;
* it records **who owns CPU frequency** at boot: one log line per cpufreq policy
  (governor, `cur/min/max`, `total_trans`, DMIPS `arch`, and `cap_view`) plus the
  kernel's FAS registration (`/proc/fas`).  `cap_view` is the policy's capacity as
  the scheduler ranks by it — DMIPS scaled by `scaling_max_freq / cpuinfo_max_freq`
  — so the line also shows what a *ceiling* holder is doing to **placement**: a
  super core capped to a third of its frequency is a core the EAS/WALT placer
  will not pick for an app launch (Batch 10-6, measured on SM8550).  It warns when
  that inversion is live, and separately when `abk_sf_enable` is armed while some
  policy is foreign-governored or pinned at `min == max`: fatal for a pre-10-5
  payload, whose floor is reached through the governor-independent `android_vh`
  hooks and ratchets such a cluster to its ceiling anyway (the measured
  1785600-with-`walt`-computing-766-MHz case), while the Batch 10-5 payload stands
  down there by its ownership gate and the knob ships off. The bundled
  `bin/abk_fas_check.sh` answers the same questions on demand, including a
  load/decay probe that tells a healthy single-point owner apart from a lock, and
  its own exit code (4) for a super core capped out of the placement decision.
  Its companion for the launch case is `bin/abk_launch_bench.sh` (Batch 27),
  which force-stops and re-launches each app and reports the `TotalTime` median
  against what the launch window actually looked like -- per-cluster `cap_view`
  and ceiling movement, the share of main-thread samples on the biggest cluster,
  the cpuset the app was in, and the pages the launch read off storage.  It ends
  in one verdict: cpuset bound, ceiling bound, placement bound with no measured
  cause, or "neither rule fired" with the recipe.  The storage's share is only
  claimed from a two-arm comparison (`--save` then `--compare`), because `pgpgin`
  is machine-wide and dropping the page cache was measured here to cost 5-340x
  the pages for 11-29% more wall clock.  It refuses to measure a phone that is
  asleep or locked -- a launched app then never becomes the top app, so every
  launch reads "cpuset bound": true of that state, false of the phone in a hand.
  See the module's [README](ksu/abk_runtime_tunables/README.md)
  for the algorithm measurements, the knob table and the trade-offs.

It is a distribution asset, not a graft: no `PatchGroup`, no kernel-tree writes.
`after_patch` packs it (`scripts/build_ksu_module.py` →
`build/ksu/abk_runtime_tunables.zip`) and bundles it into the AnyKernel3 tree
(`scripts/ak3_bundle_ksu_module.py`), so flashing the kernel also installs the
policy. Without an AnyKernel3 tree the step warns and continues (or set
`ABK_515_KSU_MODULE=0` to skip it); the standalone zip can always be flashed or
`ksud module install`-ed by hand.

```bash
python3 scripts/build_ksu_module.py              # build/ksu/abk_runtime_tunables.zip
python3 scripts/ak3_bundle_ksu_module.py inject --zip <AK3.zip> --output <out.zip>
python3 scripts/ak3_bundle_ksu_module.py verify --zip <out.zip>
```

## Coexistence with other ABK modules

The module is self-contained and injectable on its own. If the same build
also carries storage-rollback or feature-graft modules, this order is still
recommended (CI executes `custom_external_modules` entries in input order,
all at `after_patch`):

1. storage rollback children first (their reverse-apply must see the
   pristine monthly tree),
2. this module second (forward grafts onto the settled baseline),
3. other feature-graft modules last — their fd-table probes detect the
   upstream shape this module lands and adapt instead of double-rewriting.

The order is no longer a hard requirement: when ABK_ABI_PATCH_SUITE runs
first anyway, this module's fd-table group recognizes the suite's fallback
`alloc_fdtable()` and composes the upstream 5.15.191 conventions on top of
it (the suite's helpers and `expand_files()`/`alloc_fd()` prechecks stay in
place), so every core group lands in either injection order.

The core child carries 52 groups (the 11 pre-Batch-6 grafts plus
`config_enablement`, `zsmalloc_chain_size`, `madvise_collapse`,
`pagealloc_fallback_reuse`, `rcu_nocb_cpu_default_all`, `dynamic_readahead_lowmem`,
the Batch 10 line (`zram_async_recompress`, `cached_freeze_reclaim`,
`zram_secondary_comp`, `memcg_v1_reclaim`), Batch 12's `zram_algo_lock`,
Batch 13's hook + policy pair (`customize_alloc_gfp_vh`,
`gfp_pressure_fastfail`), Batch 14's zram writeback correctness trio
(`zram_wb_teardown`, `zram_writeback_bounds`, `zram_wb_limit_align`), Batch
15's five fs/pid/MM hot-path groups, Batch 17's zram writeback trio
(`zram_writeback_batching` -- several bios in flight, `ZRAM_UNDER_WB` as the
in-flight marker, with the upstream `wb_ctl` UAF and blk_idx-leak fixes built
in -- `zram_wb_batch_size`, and `zram_compressed_writeback`), Batch 24's
recompression pass cap (`zram_recompress_max_pages`: `max_pages` on both
recompress nodes plus the guard that rejects an unrecognised `type=`), and
Batch 30's `readahead_mmap_miss_race` (the v6.18 mmap_miss concurrent-fault
guard in `mm/filemap.c`, the module's only group in that file), Batch 31's
`arm64_pte_mkwrite_clean` (the 5.15.196 `pte_mkwrite()` dirty guard, the
module's first `arch/arm64` C source), Batch 32's written-back-slot release
(`zram_wb_slot_preserve`: no `zram_free_page()` over a `ZRAM_WB` slot, so the
metadata the read and recompression paths need survives and `->huge_pages` is
decremented exactly once), and Batch 33's zsmalloc free-path split
(`zsmalloc_free_zspage_out_of_lock`: a dead zspage's pages go back to the buddy
allocator after `class->lock` is dropped), Batch 34's `arm64_lse_percpu_load_atomics`
(`__PERCPU_OP_CASE()`'s LSE branch and its three `PERCPU_OP()` instantiations
flip from `stadd`/`stclr`/`stset` to `ldadd`/`ldclr`/`ldset`, so the instructions
execute "near" instead of "far"), Batch 35's page-cache/page-table
line (the shadow-entry pair `truncate_shadow_batch` + `truncate_shadow_batch_sweep`,
which clears a pagevec's shadow entries under one `i_pages` acquisition and then
in one tree traversal, and the MADV_DONTNEED pair `madvise_pt_reclaim` +
`madvise_batch_tlb_flush`, which hands an emptied PTE page back and gathers the
whole request's TLB flushes in one `mmu_gather`), Batch 36's FUSE
write-path prefault move (`fuse_prefault_out_of_write_path`:
`fuse_fill_write_pages()` faults its source buffer in only where the copy made
no progress -- the module's only group in `fs/fuse/`), and Batch 37's six-group
memory-reclaim chain (`proactive_reclaim_batch_fidelity` +
`proactive_reclaim_decaying_batches` -- the batch a `memory.reclaim` write uses
decays from a quarter of the outstanding request instead of being capped at
`SWAP_CLUSTER_MAX`, the pair `0388536ac291`/`287d5fedb377` upstream keeps
together -- `reclaim_swappiness_defines` and
`proactive_reclaim_swappiness_arg`, which add `swappiness=<val>` to
`memory.reclaim` and carry it to the scan balance through a new
`sc_swappiness()` accessor, `proactive_reclaim_suspend_abort`, which lets the PM
freezer interrupt a proactive reclaim instead of failing the suspend, and
`lru_add_drain_dead_folios`, which frees a page that is already dead before it
reaches the LRU -- the module's first `mm/swap.c` group); the
perf child carries 23, including the five Batch 15 scheduler/block groups and
the PSI line (`psi_irq_tracking`, `psi_cgroup_pressure_switch`,
`psi_oncpu_state_mask`); the display child carries 1, for 76 groups in total.
`tests/sublevel_matrix.py` `GROUP_COUNTS` must match exactly — the unit tests
assert it against the registry.

The full input string for the F2FS + ABI-suite combination, the shape
registry and the KMI compatibility matrix are documented in
`docs/porting_policy.md`.

## Local verification

```bash
python3 -m py_compile scripts/*.py tests/*.py
bash -n setup.sh scripts/*.sh tests/*.sh tools/*.sh ksu/*/*.sh
python3 tests/stable_5_15_test.py
python3 tests/implementation_audit.py /path/to/android13-5.15-common-kernel-tree
bash tests/smoke.sh /path/to/android13-5.15-common-kernel-tree
```

`tests/stable_5_15_test.py` also pins the companion module: deterministic packing,
the AK3 injection being idempotent and self-verifying, the algorithm policy's
constants and operation order, and the device scripts' behaviour against a fixture
sysfs tree (the rewrite itself, the "swap is in use" refusal, establishing a
writeback backing device when nobody owns one, preserving a live one across a
rewrite, doing nothing at all on a kernel that locks the compressors, and the
supervisor's liveness). It also pins the three defconfig tiers
(`ABK_515_DEFCONFIG_ALIGN`, `ABK_515_DEFCONFIG_ROM`) and the `zram_algo_lock`
group's own fixture.

`tests/implementation_audit.py` then asserts the graft content is real (no
"applied with zero edits" phantom groups, feature symbols actually present, and
the 5.15-specific calling conventions a 6.x-sourced graft has to be rewritten
to), and `tests/smoke.sh` builds a disposable KERNEL_ROOT from the given tree,
runs `setup.sh` for both children twice, asserts the report statuses and in-tree
markers, then exercises the rollback path. Expected statuses come from
`tests/sublevel_matrix.py`, keyed by the tree's Makefile `SUBLEVEL` (override
with `ABK_TEST_SUB_LEVEL`).

To verify a baseline you don't have checked out, fetch just the ~82 files the
groups touch:

```bash
bash tests/fetch_sublevel_tree.sh android13-5.15-2025-12 /tmp/tree194
```

Then run the per-step audit (every step `applied` unless the baseline already
carries the group — including steps skipped because an earlier step in the same
group pre-created their replacement text — comment/brace/`#ifdef` balance
preserved, second pass a byte-identical no-op) and the end-to-end smoke:

```bash
python3 tests/step_audit.py /tmp/tree194
```

Dry-run (statuses only, no writes):

```bash
python3 scripts/abk_stable_core.py --common-dir <tree> --defconfig <tree>/arch/arm64/configs/gki_defconfig \
  --report-dir /tmp/r --sub-level 194 --family android13-5.15 --dry-run
```

Rollback: `bash scripts/abk_rollback.sh <kernel-common-dir> --list` then
`--apply`.

## AutoFDO profile tooling

Batch 8's AutoFDO entry is a **build-engineering tool, not a graft**:
`tools/autofdo_515_profile.sh` collects and validates a device-specific 5.15
AutoFDO profile and never edits a kernel tree or registers a `PatchGroup`. It
runs as `init → record → convert → validate → build-env`:

```bash
tools/autofdo_515_profile.sh init --kernel-root <5.15-tree> --vmlinux <vmlinux> --output-dir <ws>
tools/autofdo_515_profile.sh record --output-dir <ws> --device <serial>
tools/autofdo_515_profile.sh convert --output-dir <ws> \
  --host-simpleperf <simpleperf> --create-llvm-prof <create_llvm_prof> --kallsyms <kernel.kallsyms>
tools/autofdo_515_profile.sh validate --output-dir <ws>
tools/autofdo_515_profile.sh build-env --output-dir <ws>
```

The hard gates are `init` (only a `5.15` kernel identity is accepted) and
`validate` (the profile binary's `vmlinux` hash must match the recorded build, so
a 6.6/6.12 profile or a profile from another build is rejected). The tool records
kernel revision, toolchain and `.config` fingerprints in `manifest.env`, and
`build-env` prints the `CONFIG_AUTOFDO_CLANG=y` / `CLANG_AUTOFDO_PROFILE=…`
variables for the exact 5.15 build. If the 5.15 tree lacks
`scripts/Makefile.autofdo` (and its `Makefile` include), the Android Common
AutoFDO build integration must be applied separately first — the tool only
produces the profile, it never patches the tree. The device-side
`simpleperf record`/`inject` flags are toolchain/SoC-specific and must be
validated against the target simpleperf before the A/B benchmark (boot, cold/warm
launch, Binder, power, image size).

## Reports

`$KERNEL_ROOT/abk_5_15_backport_reports/<child>/<child>_report.{json,md}`
per run: shapes, per-group status, applied commits. Extend the module via
`docs/group_recipe.md`.

## License

GPL-2.0-only. The text is reproduced below and in [`LICENSE`](LICENSE);
upstream attribution is in [`docs/attribution.md`](docs/attribution.md).

```text
                    GNU GENERAL PUBLIC LICENSE
                       Version 2, June 1991

 Copyright (C) 1989, 1991 Free Software Foundation, Inc.,
 <https://fsf.org/>
 Everyone is permitted to copy and distribute verbatim copies
 of this license document, but changing it is not allowed.

                            Preamble

  The licenses for most software are designed to take away your
freedom to share and change it.  By contrast, the GNU General Public
License is intended to guarantee your freedom to share and change free
software--to make sure the software is free for all its users.  This
General Public License applies to most of the Free Software
Foundation's software and to any other program whose authors commit to
using it.  (Some other Free Software Foundation software is covered by
the GNU Lesser General Public License instead.)  You can apply it to
your programs, too.

  When we speak of free software, we are referring to freedom, not
price.  Our General Public Licenses are designed to make sure that you
have the freedom to distribute copies of free software (and charge for
this service if you wish), that you receive source code or can get it
if you want it, that you can change the software or use pieces of it
in new free programs; and that you know you can do these things.

  To protect your rights, we need to make restrictions that forbid
anyone to deny you these rights or to ask you to surrender the rights.
These restrictions translate to certain responsibilities for you if you
distribute copies of the software, or if you modify it.

  For example, if you distribute copies of such a program, whether
gratis or for a fee, you must give the recipients all the rights that
you have.  You must make sure that they, too, receive or can get the
source code.  And you must show them these terms so they know their
rights.

  We protect your rights with two steps: (1) copyright the software, and
(2) offer you this license which gives you legal permission to copy,
distribute and/or modify the software.

  Also, for each author's protection and ours, we want to make certain
that everyone understands that there is no warranty for this free
software.  If the software is modified by someone else and passed on, we
want its recipients to know that what they have is not the original, so
that any problems introduced by others will not reflect on the original
authors' reputations.

  Finally, any free program is threatened constantly by software
patents.  We wish to avoid the danger that redistributors of a free
program will individually obtain patent licenses, in effect making the
program proprietary.  To prevent this, we have made it clear that any
patent must be licensed for everyone's free use or not licensed at all.

  The precise terms and conditions for copying, distribution and
modification follow.

                    GNU GENERAL PUBLIC LICENSE
   TERMS AND CONDITIONS FOR COPYING, DISTRIBUTION AND MODIFICATION

  0. This License applies to any program or other work which contains
a notice placed by the copyright holder saying it may be distributed
under the terms of this General Public License.  The "Program", below,
refers to any such program or work, and a "work based on the Program"
means either the Program or any derivative work under copyright law:
that is to say, a work containing the Program or a portion of it,
either verbatim or with modifications and/or translated into another
language.  (Hereinafter, translation is included without limitation in
the term "modification".)  Each licensee is addressed as "you".

Activities other than copying, distribution and modification are not
covered by this License; they are outside its scope.  The act of
running the Program is not restricted, and the output from the Program
is covered only if its contents constitute a work based on the
Program (independent of having been made by running the Program).
Whether that is true depends on what the Program does.

  1. You may copy and distribute verbatim copies of the Program's
source code as you receive it, in any medium, provided that you
conspicuously and appropriately publish on each copy an appropriate
copyright notice and disclaimer of warranty; keep intact all the
notices that refer to this License and to the absence of any warranty;
and give any other recipients of the Program a copy of this License
along with the Program.

You may charge a fee for the physical act of transferring a copy, and
you may at your option offer warranty protection in exchange for a fee.

  2. You may modify your copy or copies of the Program or any portion
of it, thus forming a work based on the Program, and copy and
distribute such modifications or work under the terms of Section 1
above, provided that you also meet all of these conditions:

    a) You must cause the modified files to carry prominent notices
    stating that you changed the files and the date of any change.

    b) You must cause any work that you distribute or publish, that in
    whole or in part contains or is derived from the Program or any
    part thereof, to be licensed as a whole at no charge to all third
    parties under the terms of this License.

    c) If the modified program normally reads commands interactively
    when run, you must cause it, when started running for such
    interactive use in the most ordinary way, to print or display an
    announcement including an appropriate copyright notice and a
    notice that there is no warranty (or else, saying that you provide
    a warranty) and that users may redistribute the program under
    these conditions, and telling the user how to view a copy of this
    License.  (Exception: if the Program itself is interactive but
    does not normally print such an announcement, your work based on
    the Program is not required to print an announcement.)

These requirements apply to the modified work as a whole.  If
identifiable sections of that work are not derived from the Program,
and can be reasonably considered independent and separate works in
themselves, then this License, and its terms, do not apply to those
sections when you distribute them as separate works.  But when you
distribute the same sections as part of a whole which is a work based
on the Program, the distribution of the whole must be on the terms of
this License, whose permissions for other licensees extend to the
entire whole, and thus to each and every part regardless of who wrote it.

Thus, it is not the intent of this section to claim rights or contest
your rights to work written entirely by you; rather, the intent is to
exercise the right to control the distribution of derivative or
collective works based on the Program.

In addition, mere aggregation of another work not based on the Program
with the Program (or with a work based on the Program) on a volume of
a storage or distribution medium does not bring the other work under
the scope of this License.

  3. You may copy and distribute the Program (or a work based on it,
under Section 2) in object code or executable form under the terms of
Sections 1 and 2 above provided that you also do one of the following:

    a) Accompany it with the complete corresponding machine-readable
    source code, which must be distributed under the terms of Sections
    1 and 2 above on a medium customarily used for software interchange; or,

    b) Accompany it with a written offer, valid for at least three
    years, to give any third party, for a charge no more than your
    cost of physically performing source distribution, a complete
    machine-readable copy of the corresponding source code, to be
    distributed under the terms of Sections 1 and 2 above on a medium
    customarily used for software interchange; or,

    c) Accompany it with the information you received as to the offer
    to distribute corresponding source code.  (This alternative is
    allowed only for noncommercial distribution and only if you
    received the program in object code or executable form with such
    an offer, in accord with Subsection b above.)

The source code for a work means the preferred form of the work for
making modifications to it.  For an executable work, complete source
code means all the source code for all modules it contains, plus any
associated interface definition files, plus the scripts used to
control compilation and installation of the executable.  However, as a
special exception, the source code distributed need not include
anything that is normally distributed (in either source or binary
form) with the major components (compiler, kernel, and so on) of the
operating system on which the executable runs, unless that component
itself accompanies the executable.

If distribution of executable or object code is made by offering
access to copy from a designated place, then offering equivalent
access to copy the source code from the same place counts as
distribution of the source code, even though third parties are not
compelled to copy the source along with the object code.

  4. You may not copy, modify, sublicense, or distribute the Program
except as expressly provided under this License.  Any attempt
otherwise to copy, modify, sublicense or distribute the Program is
void, and will automatically terminate your rights under this License.
However, parties who have received copies, or rights, from you under
this License will not have their licenses terminated so long as such
parties remain in full compliance.

  5. You are not required to accept this License, since you have not
signed it.  However, nothing else grants you permission to modify or
distribute the Program or its derivative works.  These actions are
prohibited by law if you do not accept this License.  Therefore, by
modifying or distributing the Program (or any work based on the
Program), you indicate your acceptance of this License to do so, and
all its terms and conditions for copying, distributing or modifying
the Program or works based on it.

  6. Each time you redistribute the Program (or any work based on the
Program), the recipient automatically receives a license from the
original licensor to copy, distribute or modify the Program subject to
these terms and conditions.  You may not impose any further
restrictions on the recipients' exercise of the rights granted herein.
You are not responsible for enforcing compliance by third parties to
this License.

  7. If, as a consequence of a court judgment or allegation of patent
infringement or for any other reason (not limited to patent issues),
conditions are imposed on you (whether by court order, agreement or
otherwise) that contradict the conditions of this License, they do not
excuse you from the conditions of this License.  If you cannot
distribute so as to satisfy simultaneously your obligations under this
License and any other pertinent obligations, then as a consequence you
may not distribute the Program at all.  For example, if a patent
license would not permit royalty-free redistribution of the Program by
all those who receive copies directly or indirectly through you, then
the only way you could satisfy both it and this License would be to
refrain entirely from distribution of the Program.

If any portion of this section is held invalid or unenforceable under
any particular circumstance, the balance of the section is intended to
apply and the section as a whole is intended to apply in other
circumstances.

It is not the purpose of this section to induce you to infringe any
patents or other property right claims or to contest validity of any
such claims; this section has the sole purpose of protecting the
integrity of the free software distribution system, which is
implemented by public license practices.  Many people have made
generous contributions to the wide range of software distributed
through that system in reliance on consistent application of that
system; it is up to the author/donor to decide if he or she is willing
to distribute software through any other system and a licensee cannot
impose that choice.

This section is intended to make thoroughly clear what is believed to
be a consequence of the rest of this License.

  8. If the distribution and/or use of the Program is restricted in
certain countries either by patents or by copyrighted interfaces, the
original copyright holder who places the Program under this License
may add an explicit geographical distribution limitation excluding
those countries, so that distribution is permitted only in or among
countries not thus excluded.  In such case, this License incorporates
the limitation as if written in the body of this License.

  9. The Free Software Foundation may publish revised and/or new versions
of the General Public License from time to time.  Such new versions will
be similar in spirit to the present version, but may differ in detail to
address new problems or concerns.

Each version is given a distinguishing version number.  If the Program
specifies a version number of this License which applies to it and "any
later version", you have the option of following the terms and conditions
either of that version or of any later version published by the Free
Software Foundation.  If the Program does not specify a version number of
this License, you may choose any version ever published by the Free Software
Foundation.

  10. If you wish to incorporate parts of the Program into other free
programs whose distribution conditions are different, write to the author
to ask for permission.  For software which is copyrighted by the Free
Software Foundation, write to the Free Software Foundation; we sometimes
make exceptions for this.  Our decision will be guided by the two goals
of preserving the free status of all derivatives of our free software and
of promoting the sharing and reuse of software generally.

                            NO WARRANTY

  11. BECAUSE THE PROGRAM IS LICENSED FREE OF CHARGE, THERE IS NO WARRANTY
FOR THE PROGRAM, TO THE EXTENT PERMITTED BY APPLICABLE LAW.  EXCEPT WHEN
OTHERWISE STATED IN WRITING THE COPYRIGHT HOLDERS AND/OR OTHER PARTIES
PROVIDE THE PROGRAM "AS IS" WITHOUT WARRANTY OF ANY KIND, EITHER EXPRESSED
OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE.  THE ENTIRE RISK AS
TO THE QUALITY AND PERFORMANCE OF THE PROGRAM IS WITH YOU.  SHOULD THE
PROGRAM PROVE DEFECTIVE, YOU ASSUME THE COST OF ALL NECESSARY SERVICING,
REPAIR OR CORRECTION.

  12. IN NO EVENT UNLESS REQUIRED BY APPLICABLE LAW OR AGREED TO IN WRITING
WILL ANY COPYRIGHT HOLDER, OR ANY OTHER PARTY WHO MAY MODIFY AND/OR
REDISTRIBUTE THE PROGRAM AS PERMITTED ABOVE, BE LIABLE TO YOU FOR DAMAGES,
INCLUDING ANY GENERAL, SPECIAL, INCIDENTAL OR CONSEQUENTIAL DAMAGES ARISING
OUT OF THE USE OR INABILITY TO USE THE PROGRAM (INCLUDING BUT NOT LIMITED
TO LOSS OF DATA OR DATA BEING RENDERED INACCURATE OR LOSSES SUSTAINED BY
YOU OR THIRD PARTIES OR A FAILURE OF THE PROGRAM TO OPERATE WITH ANY OTHER
PROGRAMS), EVEN IF SUCH HOLDER OR OTHER PARTY HAS BEEN ADVISED OF THE
POSSIBILITY OF SUCH DAMAGES.

                     END OF TERMS AND CONDITIONS

            How to Apply These Terms to Your New Programs

  If you develop a new program, and you want it to be of the greatest
possible use to the public, the best way to achieve this is to make it
free software which everyone can redistribute and change under these terms.

  To do so, attach the following notices to the program.  It is safest
to attach them to the start of each source file to most effectively
convey the exclusion of warranty; and each file should have at least
the "copyright" line and a pointer to where the full notice is found.

    <one line to give the program's name and a brief idea of what it does.>
    Copyright (C) <year>  <name of author>

    This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 2 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along
    with this program; if not, see <https://www.gnu.org/licenses/>.

Also add information on how to contact you by electronic and paper mail.

If the program is interactive, make it output a short notice like this
when it starts in an interactive mode:

    Gnomovision version 69, Copyright (C) year name of author
    Gnomovision comes with ABSOLUTELY NO WARRANTY; for details type `show w'.
    This is free software, and you are welcome to redistribute it
    under certain conditions; type `show c' for details.

The hypothetical commands `show w' and `show c' should show the appropriate
parts of the General Public License.  Of course, the commands you use may
be called something other than `show w' and `show c'; they could even be
mouse-clicks or menu items--whatever suits your program.

You should also get your employer (if you work as a programmer) or your
school, if any, to sign a "copyright disclaimer" for the program, if
necessary.  Here is a sample; alter the names:

  Yoyodyne, Inc., hereby disclaims all copyright interest in the program
  `Gnomovision' (which makes passes at compilers) written by James Hacker.

  <signature of Moe Ghoul>, 1 April 1989
  Moe Ghoul, President of Vice

This General Public License does not permit incorporating your program into
proprietary programs.  If your program is a subroutine library, you may
consider it more useful to permit linking proprietary applications with the
library.  If this is what you want to do, use the GNU Lesser General
Public License instead of this License.
```

## Author and project

**ABK 5.15 LTS Backport** — a standalone feature, optimization,
structural-refactor backport and display-bringup fix line for the
`android13-5.15` GKI baselines (5.15.167 / 5.15.178 / 5.15.194), currently
v0.44.0.

**Author: FanZiyun** ([@fanziyun](https://github.com/fanziyun))

- Repository: <https://github.com/fanziyun/ABK_5.15_BACKPORT>

Copyright (C) 2026 FanZiyun

An independent, unofficial backport of upstream Linux commits, unaffiliated
with any vendor's or distribution's official kernel release.
