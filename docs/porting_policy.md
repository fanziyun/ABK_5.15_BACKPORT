# Porting policy and three-module compatibility

## Scope rules

1. **Features/optimizations/structural refactors only.** Pure security or
   bug fixes are not ported for their own sake (they arrive with newer
   sublevels anyway); fix-flavored commits may ride along only when they are
   semantically inseparable from a feature being grafted.
2. **Upstream-faithful forms.** Group content mirrors the 5.15.y backport
   text (not the mainline version) so trees land in the exact shape newer
   5.15.y sublevels expect.
3. **KMI red lines.**
   - Never claim `sched_entity` KABI slots 1–4 (ABK_ABI_PATCH_SUITE EEVDF)
     or `request_queue` slot 1 (ABI suite async_depth).
   - New `task_struct`/exported-struct fields must reuse a free
     `ANDROID_KABI_RESERVE` slot via `ANDROID_KABI_USE` (current free set in
     android13-5.15: task_struct slots 1–8; this module uses slot 8 for
     `kstack_offset`).
   - Bitfield removals are only accepted when a following `unsigned :0`
     force-alignment pins the layout (PSI `sched_psi_wake_requeue` case).
4. **Degrade, never half-patch.** Required-anchor misses abort the whole
   group transactionally (no file writes); optional steps may degrade the
   group to `partial`. Unknown shapes on the hard fdtable group abort the
   build with a precise message (same protection style as the ABI suite's
   `fd_alloc_hotpath`).
5. **Never take DVFS ownership that the tree already has.** A frequency
   *floor* is only meaningful while the owner of the policy still leaves
   headroom. A FAS-style owner (vendor WALT/FAS, e.g. driven by a scheduler
   profile such as Scene's `sceneFAS`) requests `min == max == its own target`
   on every change, so a floor clamped to `policy->max` re-states the
   frequency that is already applied: the cluster can go up and can never come
   down. Note that this is *not* avoided by the tree running a foreign
   governor: the policy samples in `android_vh_scheduler_tick` and applies in
   `android_vh_cpufreq_resolve_freq`, both reached whatever the governor is, so
   an armed pre-10-5 floor ratchets a `walt` policy too — which is what was
   measured on kalama (SM8550): `waltgov_next_freq` computed 766 MHz for a
   cluster at 22% demand while it held 1785600 for a whole game session, and
   the pin released when the knob was turned off. `schedutil_smart_policy` is
   therefore disabled by default, and the payload refuses to act unless the
   governor really is `schedutil` and the range really is a range;
   `floor >= policy->max` (or `<= policy->min`, which cpufreq's own clamp
   already satisfies) is a no-op by construction. A grafted tree can be told
   from an ungrafted one from userspace because the 10-5 payload also exposes
   the read-only `abk_sf_boosting` node.
   `tools/abk_fas_check.sh` (shipped as `bin/abk_fas_check.sh`) is the
   read-only check that tells a healthy single-point owner from a lock.

   The same rule reaches further than frequency, and that is the part to
   internalise before adding any cpufreq-adjacent group: **`scaling_max_freq` is
   also a capacity node.** EAS and WALT scale a policy's reported capacity by
   `scaling_max_freq / cpuinfo_max_freq` (the `fmax_capacity` /
   `rq_cpu_capacity_orig` pair of the `update_cpu_capacity` tracepoint), so
   whoever owns that ceiling is editing *placement* as well — cap a super core
   at 27% of its frequency and the placer sees a core smaller than the mid
   cluster and stops putting work on it, at any util. Measured on the same
   device: `rq_cpu_capacity_orig` of the prime policy crawled 277 → 400 → 549 →
   672 of 1024 while the mid cluster stood at 585-749, and the super core was
   the smaller core on offer in 5 of 16 sampled polls. A group that lifts such a
   ceiling to help throughput would be stealing that ownership, and **placement
   failure is silent**: unlike a frequency, no node reports "this core was
   never eligible", so it can only be seen by comparing the scaled capacities.

## 6.1-origin line (Batch 3+)

The `android14-6.1` ACK branch (the only 6.1 ACK line) is a second feature
source for this module, in addition to the upstream 5.15.y stream. See
`docs/survey_6_1_ack.md` for the candidate inventory and the
suite-preference cross-check.

- **Source form = ACK 6.1 tree text.** Group content mirrors the
  android14-6.1 shape (including its stable-backports of later mainline
  features, e.g. the kernfs PSI polling rework), adapted to the 5.15
  baseline shapes where the ACK form sits on 6.1-only infrastructure.
- **Suite preference.** For every candidate the ABK_ABI_PATCH_SUITE
  inventory is checked first; suite-covered optimizations are NOT
  re-implemented here (build with the suite instead). The exclusion list
  is recorded in the survey doc.
- **KMI red lines, extended.** The ACK 6.1 `psi_group` pointer/parent
  restructure (which rewrites `struct cgroup`) is NOT portable to
  android13-5.15 — PSI features are grafted onto the embedded-psi_group
  shapes instead (heap-only `psi_trigger` wrappers, percpu-internal
  `psi_group_cpu` states, enum additions). Vendor tracepoints are additive
  and may be introduced; new struct members never grow KMI-visible
  structs.
- **Shape probes handle lineage drift.** e.g. the lazy-preemption group
  detects whether the tree already carries
  `android_vh_set_tsk_need_resched_lazy` (newer 5.15 ACK snapshots do) and
  lands the full mechanism on the 2024-11 baseline; the graft marker
  doubles as the idempotency probe.

## Batch 6: config lane, family gate, marker policy

- **Config lane.** The children always received `--defconfig` but never used
  it. `GraftContext.enable_configs()` now rewrites the three possible shapes
  of a symbol (target value, `# CONFIG_x is not set`, another value, or
  absent), snapshots through `.abk-orig`, and refuses to write a defconfig
  outside `KERNEL_ROOT` (`report_only` with the reason). `config_enablement`
  turns on the module's own symbols (`ZRAM_TRACK_ENTRY_ACTIME`,
  `ZRAM_MULTI_COMP`) by default; `ABK_515_DEFCONFIG_ALIGN=1` additionally
  aligns six 6.6-GKI defaults whose 5.15 code exists. CI's
  `custom_kernel_options` still owns one-off config input; the module only
  owns its own feature gates.
- **Family gate.** A non-`android13-5.15` family now produces `report_only`
  for every group without reading files; `--allow-unsupported` (shell:
  `ABK_515_ALLOW_UNSUPPORTED=1`) is the explicit escape hatch. The old
  message-only warning is gone.
- **Marker policy.** New lines introduced by this module carry
  `ABK stable_515_backport:`. Upstream-shape rewrites stay byte-faithful
  (target form is the idempotency probe) so a baseline that already carries
  the commit is never touched just to add a comment.
- **No-op guard.** `apply_steps()` treats an empty or fully-unmatched step
  list as `blocked_by_missing_anchor`, and `run_child()` now refuses both
  directions: degraded groups that wrote, and groups that claim an edit
  without changing any file content.


## Supported baselines (sublevel matrix)

The engine gates purely on **text anchors**; `ctx.sub_level` reaches the report
and nothing else (it is never compared). A group whose upstream commit the
target baseline already carries therefore reports `already_present` — that is a
success, not a degradation. All three android13-5.15 combinations CI accepts
(`build.yml` `KNOWN_KERNEL_PAIRS`) are supported by the same injection string:

| sublevel | AOSP branch | os_patch_level | core pass 1 | perf pass 1 |
|---|---|---|---|---|
| 167 | `deprecated/android13-5.15-2024-11` | 2024-11 | 27 applied | 13 applied |
| 178 | `deprecated/android13-5.15-2025-03` | 2025-03 | 27 applied | 12 applied + 1 present |
| 194 | `android13-5.15-2025-12` | 2025-12 | 24 applied + 3 present | 11 applied + 2 present |

A second pass is `already_present` for every group on all three (the untracked
`android13-5.15-lts` row, keyed to the fetched tree's `SUBLEVEL = 216`, carries
the two Batch-2-era perf debts — see `KNOWN_DEBT` in `tests/sublevel_matrix.py`).
Groups the baseline pre-empts:

- **178** — `sched_nohz_idle_balance_series` (5.15.174).
- **194** — the 178 set plus `fdtable_alloc_conventions` (5.15.191),
  `pagealloc_cpuset_bailout` (5.15.191), `cgroup_destroy_wq_split` (5.15.194)
  and `semaphore_wake_q` (5.15.180).

Batch 14's three zram writeback groups (`zram_wb_teardown`,
`zram_writeback_bounds`, `zram_wb_limit_align`) apply on **all** of them and add
no `PRE_APPLIED`/`KNOWN_DEBT` row. Note what they do *not* do: they deliberately
leave `zram_reset_device()` alone, because the earlier `zram_recompression` group
anchors on that function's whole pristine body — see
`research/zram_writeback_plan.md` §10.5 and the `batch14_core_zram_writeback`
module docstring before editing either one.

The expectations live in `tests/sublevel_matrix.py`, which both `tests/smoke.sh`
and `tests/step_audit.py` read (keyed by the tree's Makefile `SUBLEVEL`, or
`ABK_TEST_SUB_LEVEL`). Fetch a reference tree for any of them with
`bash tests/fetch_sublevel_tree.sh <branch> <outdir>` — it pulls only the ~44
files the groups touch, so no kernel clone is needed. Adding a baseline means
adding a matrix entry; it does not mean adding version gating.

The android13-5.15-lts tree (recorded at 5.15.211; the branch has since rolled
-- the matrix row is keyed to the fetched tree's Makefile `SUBLEVEL`, 216 as of
the 2026-09 re-fetch, re-proven on that tree) is a fourth fixture only:
`step_audit.py` audits it against a matrix row whose two known debts are
recorded (`randomize_kstack_pertask` and `blk_mq_suspend_wakeup_abort`, both
`blocked_by_shape`). lts is not a CI combination and nothing gates on it. It is
a rolling branch, so re-check its `PRE_APPLIED` row and re-key it when
re-fetching the tree —
`sched_rt_optimizations` (5.15.202) and `sched_dst_group_allowed_stats` (5.15.212)
have since landed there and moved from drift to pre-applied.

Note that `fdtable_alloc_conventions` reporting `already_present` on 194 means
`fs/file.c` carries **no** module marker there — the 5.15.195 `replace_fd()`
hunk therefore lives in its own group (`fdtable_replace_fd_errno`) rather than
as a step inside the conventions group, which short-circuits before its steps
run on any tree at 5.15.191 or newer.

## Three-module composition (all after_patch)

CI executes injected modules in input order, so the canonical input is:

1. `ABK_F2FS_FIX_MODULE` children (`storage_ufs_rollback`,
   `storage_block_rollback`, `storage_f2fs_rollback`,
   `storage_common_fixups`) — restore the storage baseline first; its
   `git apply --reverse --check` breaks if anything rewrites block//f2fs
   before it.
2. **This module** (`stable_backport_core`, `stable_perf_backport`,
   `stable_display_fix`) — forward grafts onto the settled baseline; the
   display child only touches `drivers/gpu/drm/drm_atomic_helper.c` and is
   order-independent.
3. `ABK_ABI_PATCH_SUITE` children — the fdtable probe then detects the
   upstream shape this module landed and takes its adapt branch instead of
   its fallback rewrite.

## Shape registry

| probe (engine) | true when | consumers |
|---|---|---|
| `suite_fdtable_fallback` | fs/file.c contains the suite's `nr = ALIGN(slots_wanted, BITS_PER_LONG)` fallback body (helper local + `abk_fdtable_slots_wanted`) | fdtable group → composed variant: the suite's body is rewritten onto the upstream 5.15.191 target; helpers/prechecks stay; drift degrades to `skip_suite_processed` |
| `suite_touched(file)` | file carries `/* ABK feature_porting:` / `/* ABK security_update_backport:` markers | fdtable group and future groups sharing suite files |
| `fdtable_upstream_shape` | slots_wanted signature + `roundup_pow_of_two(slots_wanted)` and no suite `ALIGN(slots_wanted, ...)` capacity line (the suite's unused helper may remain) | fdtable idempotency (covers both injection orders) |
| sched.h SysVIPC tail | `ANDROID_KABI_USE(6, struct sysv_sem sysvsem)` present (ABK's kernel-specific patch reuses task_struct slots 6/7/8 for sysvsem/sysvshm behind `#ifdef CONFIG_SYSVIPC`) | kstack group moves to the still-free slot 5; a `kstack_offset inside task_struct` range check then fails the group loudly on any uncovered shape |
| `block_rolled_back` | the F2FS suite's block rollback already removed its monthly sentinel from blk-mq.c | informational; records the composition in reports |

Group chaining: `pagealloc_highatomic_reserve_semantics` (5.15.188-.218)
builds on `pagealloc_min_reserve_semantics` (5.15.171) output and rewrites its
`__zone_watermark_ok()` hunk onto the final form.  The earlier group therefore
recognizes the superseding shape (`ALLOC_RESERVES` in mm/internal.h) and
reports `already_present` on re-runs - both orders of "only one of the two
applied" stay idempotent.

Batch 8's `pagealloc_fallback_reuse` is a three-file page-allocation chain:
`mm/internal.h` and `mm/compaction.c` move `find_suitable_fallback()` to the
claimable/-2 result form, while `mm/page_alloc.c` keeps the 5.15 vendor-hook
shape and splits the fallback claim and steal phases. `rmqueue_bulk()` carries
the phase state only while its zone lock is held; `rmqueue_buddy()` starts from
`RMQUEUE_NORMAL` for each independent allocation.

Batch 8's `rcu_nocb_cpu_default_all` is a three-file opt-in source graft:
`kernel/rcu/Kconfig` adds the configuration symbol, the kernel-parameter
documentation records explicit-mask precedence, and `kernel/rcu/tree_nocb.h`
allocates the 5.15 mask when no boot mask was supplied before setting it to all
possible CPUs. The option remains `default n`; only a device benchmark may justify
enabling it in a product defconfig.

The AOSP android13-5.15 line never took the upstream 5.15.171 Gorman rework:
167, 178, 194 and the current `android13-5.15-lts` (.211) all still carry
`ALLOC_HARDER 0x10` / `ALLOC_HIGH 0x20` in `mm/internal.h` and the
single-argument `gfp_to_alloc_flags(gfp_t gfp_mask)`.  Both page_alloc groups
therefore report `applied` on every supported sublevel; the "high version
sensitivity" of that region applies to upstream vanilla trees, not to this
baseline family.

Footprint disjointness (verified against the F2FS suite script and its
`android13-5.15-2024-11_r14` patches): the F2FS suite touches
`drivers/scsi/ufs/`, `block/` (one hunk in `blk_mq_delay_run_hw_queues()`),
`fs/f2fs/*`, `include/trace/events/f2fs.h`, plus optional `dm/` and
`fs/crypto/`. This module's only shared file is `block/blk-mq.c`, and its
hunks live in `blk_mq_hctx_notify_offline()` — disjoint from both the F2FS
hunk and the ABI suite's blk-mq regions.

## Runtime companion and the ROM-integration config tier

Batch 11 adds a **runtime companion** (`ksu/abk_runtime_tunables/`), which is a
distribution asset rather than a graft: it registers no `PatchGroup`, never
writes into the kernel tree, and `after_patch` merely packs it
(`scripts/build_ksu_module.py`) and injects it into the AnyKernel3 tree
(`scripts/ak3_bundle_ksu_module.py`, idempotent marker-delimited block, skipped
with a warning when there is no AK3 tree, `ABK_515_KSU_MODULE=0` to disable).
The same change makes the secondary compressor policy a constant instead of a
knob: `zram.abk_recomp_algo` defaults to `zstd` and is exposed `0444`, so no
runtime write — not even from KernelSU's root, verified on device — can put the
dominated `lz4hc` back. The companion module ("non-`PatchGroup` asset") ships on
the `stable_backport_core` child, whose `module.conf` row advertises it through
`magisk_module_name` / `magisk_module_url`.

Batch 12 (`zram_algo_lock`) moved that policy into the kernel, because userspace
cannot defend it. Both `comp_algorithm` and `recomp_algorithm` are writable only
before `disksize`, so the whole policy belonged to whoever won that single
window — on the target device a root writer won it and left the primary on
`deflate` while the companion was installed. The group now selects the primary
in `zram_add()` from the read-only `zram.abk_comp_algo` (default `lz4kd`,
falling back to the build's `CONFIG_ZRAM_DEF_COMP`) and, in a `late_initcall`,
points both `DEVICE_ATTR_RW` stores at a function that **reports success and
keeps the locked value** (`zram.abk_lock_algo`, `0444`, default `Y`). Reporting
success rather than `-EPERM` is part of the contract, not an oversight: Android
16's `mmd_setup` aborts its whole zram bring-up — writeback backing device and
`mmd.setup_complete` included — when an algorithm write fails.

That is also what removes the config-tier conflict below. `reset` drops a
writeback backing device (`reset_bdev()`), so repairing the algorithm used to
cost the writeback setup; with the lock the algorithm needs no repair at all,
and the companion only ever attaches/re-attaches a backing device without
touching the compressor selection. The anchor rule the group follows is worth
noting for future batches: it inserts only at block **boundaries** (the pristine
`default_compressor` line Batch 10-4's parameter block opens with, the pristine
`zram_debugfs_register(zram);` call, and after the pristine
`module_init(zram_init);` line Batch 10-1 inserts before) — editing inside
another group's replacement text breaks that group's second-pass idempotency and
is what `step_audit.py` catches.

One gap is deliberately **not** closed by the companion: the ROM's own zram
daemon (`mmd_setup`, Android 16's Rust memory daemon) needs
`CONFIG_ZRAM_WRITEBACK`, which no userspace can supply. Enabling it is a
config-tier decision (`ABK_515_DEFCONFIG_ROM=1` → `CONFIG_ZRAM_WRITEBACK=y`,
off by default) and it now **coexists** with the algorithm policy: the companion
takes writeback ownership only when the kernel exposes the node and nobody owns
it (`backing_dev` is `none` and `mmd.setup_complete` is unset), preserves an
existing attachment across any rewrite it has to do, and does nothing at all on
a locked kernel whose policy is already in force. Note the overlap with
`ABK_ABI_PATCH_SUITE`, which owns zram **writeback code**; this module would only
flip the Kconfig symbol, never the code, and any build enabling it must be
verified with that suite injected.

## Report contract

Each child writes `<report_dir>/<child>_report.json` + `.md` (default
`$KERNEL_ROOT/abk_5_15_backport_reports/<child>/`) with per-group status:
`applied / partial / already_present / skip_suite_processed /
report_only / blocked_by_missing_anchor / blocked_by_shape`. `report_only` is
produced by the family gate (any non-android13-5.15 lineage) and by groups
whose target file is outside the kernel tree (the defconfig lane refuses to
write beyond KERNEL_ROOT so rollback can always restore it). Reports are also
`.abk-orig`-snapshotted across runs.

## Backups and rollback

All writes go through `write_text()`, snapshotting to `<file>.abk-orig`
exactly once (never overwritten). `scripts/abk_rollback.sh <common-dir>
--apply` restores every snapshot tree-wide; `--list` dry-runs.
