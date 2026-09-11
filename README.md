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
| `stable_perf_backport` | NOHZ idle-balance series (5.15.174), PSI psi_flags migration (5.15.179), RT scan optimizations (5.15.202/.212), per-task kstack randomization via KABI slot 8 (5.15.210), `__release_sock` cond_resched reduction (5.15.197), semaphore wake_q (5.15.180), blk-mq suspend wakeup abort (5.15.198), PSI IRQ pressure tracking, PSI trigger kernfs polling, lazy-preemption + mutex/rwsem wakeup vendor hooks (android14-6.1) |
| `stable_display_fix` | removal of the 5.15.185 `drm: Add valid clones check` encoder validation (the Concurrent Writeback series) from `drivers/gpu/drm/drm_atomic_helper.c`; the check makes every vendor `msm_drm` atomic commit fail with `-EINVAL` on 5.15.185+ (2025-07 / 2025-09 / 2025-12) and the lts branch, so the panel stays black while touch/fingerprint keep working; on 5.15.167/.178 (which never carried the check) the group reports `already_present` and writes nothing |
| `stable_backport_core` (Batch 9-1) | `dynamic_readahead_lowmem`: dynamic readahead (OPLUS/Xiaomi `mi_dynamic_readahead`) as a GKI built-in — a `core_initcall` in `mm/readahead.c` registers the `android_vh_ra_tuning_max_page` / `android_vh_tune_mmap_readaround` vendor-hook callbacks so low-memory background (cpuset "background") tasks get halved readahead windows and shrunk mmap read-around, behind `CONFIG_ABK_DYNAMIC_READAHEAD` with a `readahead.dynamic_readahead=0` runtime disable |

Since Batch 3 the module also grafts selected **android14-6.1 ACK line**
features (the only 6.1 ACK branch): `memory.reclaim` proactive reclaim,
PSI IRQ tracking, PSI trigger kernfs polling, and the lazy-preemption /
lock-wakeup vendor-hook families.  Those groups mirror the ACK 6.1 form
adapted to the 5.15 baseline shapes, keep the KMI untouched (heap-internal
wrappers, percpu states, additive tracepoints only), and skip anything the
ABK_ABI_PATCH_SUITE already covers (see `docs/survey_6_1_ack.md`).

Batch 6 adds two 6.2-origin pieces that only exist in the android15-6.6
line (`zram recompression`, `ZSMALLOC_CHAIN_SIZE` zspage sizing), the 6.1
`MADV_COLLAPSE` synchronous THP collapse, and the first real use of the
`DEFCONFIG` the CLI always demanded: `config_enablement` enables the module's
own symbols by default and, with `ABK_515_DEFCONFIG_ALIGN=1`, also the
android15-6.6 GKI config deltas whose 5.15 code already exists
(`LRU_GEN_ENABLED`, BBR, `BLK_WBT`, cgroup IO throttling, delay accounting);
`ABK_515_DEFCONFIG_ROM=1` adds the ROM-integration tier
(`CONFIG_ZRAM_WRITEBACK=y`, off by default — see the runtime-companion section).
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
set:https://github.com/xingguangcuican6666/ABK_5.15_backport.git#stable_backport_core;after_patch|set:https://github.com/xingguangcuican6666/ABK_5.15_backport.git#stable_perf_backport;after_patch|set:https://github.com/xingguangcuican6666/ABK_5.15_backport.git#stable_display_fix;after_patch
```

The display fix child is independently injectable: a build that only needs
the black-screen fix can carry just
`set:https://github.com/xingguangcuican6666/ABK_5.15_backport.git#stable_display_fix;after_patch`.

One injection string covers all three: the engine gates on text anchors, never
on the sublevel, so a group whose upstream commit the baseline already carries
reports `already_present` instead of `applied`. On 5.15.178 that is one group
(the 5.15.174 NOHZ series); on 5.15.194 it is five (fd-table conventions,
cpuset bail-out, cgroup destroy-wq split, NOHZ series, semaphore wake_q). The
per-sublevel expectations are in `tests/sublevel_matrix.py` and
`docs/porting_policy.md`.

The baseline-neutral rule also works across the newer android13-5.15-lts
tree: `tests/sublevel_matrix.py` keeps a `.211` fixture row whose two known
debts (`randomize_kstack_pertask` KABI-slot drift, `blk_mq_suspend_wakeup_abort`
shape) are recorded so the local `.211` tree can be audited even though it is
not a CI combination.

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
  (`zram.writeback=auto`). On the reference device writeback is simply absent
  (`CONFIG_ZRAM_WRITEBACK` off, `vendor.zram.disable=1`, `mmd.setup_complete`
  unset), so `ABK_515_DEFCONFIG_ROM=1` is the tier that turns it on;
* it drives age-marked recompression sweeps through the kernel's async worker
  (this is the only part that is on by default);
* it reports — or optionally applies — the remaining runtime knobs: MGLRU, THP,
  `vm.swappiness`, the schedutil smart-freq policy, dynamic readahead and
  cgroup-v1 proactive reclaim;
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

The core child carries 22 groups (the 11 pre-Batch-6 grafts plus
`config_enablement`, `zsmalloc_chain_size`, `madvise_collapse`,
`pagealloc_fallback_reuse`, `rcu_nocb_cpu_default_all`, `dynamic_readahead_lowmem`,
the Batch 10 line (`zram_async_recompress`, `cached_freeze_reclaim`,
`zram_secondary_comp`, `memcg_v1_reclaim`) and Batch 12's `zram_algo_lock`); the
perf child carries 13; the display child carries 1, for 36 groups in total.
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

To verify a baseline you don't have checked out, fetch just the ~44 files the
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
