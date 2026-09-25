# ABK 5.15 LTS Backport

> [中文](README.md)

## Features

### Memory and swap

| Feature | Description |
|---|---|
| **zram recompression** | Pages idle past a threshold are recompressed with zstd, so the same RAM holds more apps |
| **Compressor lock** | The primary/secondary algorithms are pinned to the measured pair and made **read-only**, so the ROM cannot later switch them |
| **zram writeback** | Upstream fixes landed after the 5.15 branch froze this code: reset no longer walks past the table, the scan bound is derived under the lock, and the write budget is page-aligned |
| **Proactive reclaim** | `memory.reclaim` gets decaying batch sizes, a `swappiness=` argument, and no longer blocks system suspend |
| **kcompressd offload** | Compress-and-swap-out moves off kswapd onto a per-node kthread, so reclaim latency excludes compression cost |
| **Low-memory fast fail** | Under pressure, THP-class high-order allocations fall back to their caller after one try instead of blocking in direct reclaim |
| **Dynamic readahead** | Halved readahead windows for background tasks, saving memory on low-RAM devices |
| **MGLRU** | Aging feedback, refault detection and workingset cleanup at the upstream v6.14 versions |
| **Page alloc / cgroup / fault paths** | Cpuset insane-config bail-out, lock-free percpu freelist reads, per-memcg proactive reclaim, hugepage fault fast path |

### Scheduler and responsiveness

- **EEVDF scheduler** — the fair scheduler 5.15 natively lacks, with wakeup
  preemption and `PREEMPT_SHORT`;
- **NOHZ idle balance / RT scan optimizations / steal-time accounting**;
- **PSI extensions** — IRQ pressure tracking and a per-cgroup pressure switch;
- **schedutil smart-freq** — holds a frequency floor under burst load, with an
  optional ceiling clamp (off by default);
- **blk-mq async depth**, plus locking, semaphore and socket-release
  simplifications.

### Files and processes

- Hot paths for fd-table allocation, `close_range`, slab alloc/free and fault
  allocation;
- the erofs read path requests its temporary bounce pages with `GFP_NOWAIT`, so a
  readahead shot no longer enters direct reclaim;
- arm64 LSE percpu atomics, batched TLB flushes, batched page-cache shadow
  eviction, and FUSE's write-path prefault moved out of the critical path.

### Display fix

`stable_display_fix` removes the `drm: Add valid clones check` added in 5.15.185
— that check makes every vendor `msm_drm` atomic commit fail with `-EINVAL`.

---

## Children

| child id | content | groups |
|---|---|---:|
| `stable_backport_core` | memory / reclaim / zram / fs-mm hot paths | 66 |
| `stable_perf_backport` | scheduler / PSI / block / DVFS policy | 24 |
| `stable_display_fix` | the drm black-screen fix | 1 |

**91 graft groups** in total; one injection string covers 5.15.167 / .178 / .194.

Supported baselines: `5.15.167` (android13-5.15-2024-11), `5.15.178`
(-2025-03), `5.15.194` (-2025-12), `android13-5.15-lts`.

---

## How to use

### 1. Injection

Put this into `custom_external_modules` (CI runs entries in input order, all at
`after_patch`):

```
set:https://github.com/fanziyun/ABK_5.15_BACKPORT.git#stable_backport_core;after_patch|set:https://github.com/fanziyun/ABK_5.15_BACKPORT.git#stable_perf_backport;after_patch|set:https://github.com/fanziyun/ABK_5.15_BACKPORT.git#stable_display_fix;after_patch
```

Single child only:

```
set:https://github.com/fanziyun/ABK_5.15_BACKPORT.git#stable_display_fix;after_patch
```

### 2. Optional

| variable | effect |
|---|---|
| `ABK_515_DEFCONFIG_ALIGN=1` | adds the 6.6-GKI config deltas (LRU_GEN, BBR, BLK_WBT, …) whose 5.15 code already exists |
| `ABK_515_DEFCONFIG_ROM=1` | ROM-integration tier: enables `CONFIG_ZRAM_WRITEBACK` (off by default) |
| `ABK_515_DEFCONFIG_PSI=1` | per-cgroup PSI tier: drops `cgroup_disable=pressure` (off by default) |
| `ABK_515_KSU_MODULE=0` | skip packing the KernelSU module |

### 3. After flashing

When the build has an AnyKernel3 tree, the **KernelSU module installs itself
with the flash**: it holds the zram policy, drives the recompression sweeps and
toggles the pressure switches. Without an AK3 tree the step warns and continues;
the zip at `build/ksu/abk_runtime_tunables.zip` can also be flashed by hand.

Knobs:

- `/sys/module/page_alloc/parameters/abk_gfp_fastfail*` — high-order fast-fail
- `/sys/module/cpufreq_schedutil/parameters/abk_sf_*` / `abk_sc_*` — smart-freq
  floor and cap
- `/sys/module/readahead/parameters/dynamic_readahead` — dynamic readahead
- `zram.abk_comp_algo` / `zram.abk_lock_algo` / `zram.abk_recomp_algo` — the
  compressor lock
- `vm.kcompressd` — the offload thread's soft limit

Full knob table:
[ksu/abk_runtime_tunables/README.md](ksu/abk_runtime_tunables/README.md).

---

## Verification

```bash
python3 -m py_compile scripts/*.py tests/*.py                  # syntax gate
bash -n setup.sh scripts/*.sh tests/*.sh tools/*.sh ksu/*/*.sh # shell syntax gate
python3 tests/stable_5_15_test.py                              # unit tests (no tree needed)
bash tests/fetch_sublevel_tree.sh android13-5.15-2025-12 /tmp/tree194   # fetch a reference tree
python3 tests/implementation_audit.py /tmp/tree194             # content audit
python3 tests/step_audit.py /tmp/tree194                       # per-step anchor audit
bash tests/smoke.sh /tmp/tree194                               # end-to-end + rollback
```

---

## Repository layout

| path | content |
|---|---|
| `scripts/` | **the graft logic itself**: anchor engine + the three children's group registry |
| `tests/` | unit tests, reference-tree fetcher |
| `docs/` | project documentation, attribution |
| `tools/` | 5 device-facing CLIs |
| `ksu/` | KernelSU module sources |
| `plan.md` | implementation checklist |
| `CHANGELOG.md` | per-batch reports |
| `research/` | upstream patch archive and reference trees |

---

## Index

- measurements — [CHANGELOG.md](CHANGELOG.md)
- how a feature was surveyed and weighed — `docs/survey_*.md`
- grafting rules — [docs/porting_policy.md](docs/porting_policy.md)
- how to add a new graft group — [docs/group_recipe.md](docs/group_recipe.md)

---

## About

An independent, unofficial backport of upstream Linux commits, unaffiliated with
any vendor's or distribution's official kernel release.

This kernel integrates and adapts the work of the following developers and
projects (in no particular order):

| developer / project | contribution |
|---|---|
| Sergey Senozhatsky (Google) | zram recompression, the writeback series |
| Mel Gorman | the page_alloc series |
| Yu Zhao (Google) | the MGLRU series, batched page-cache shadow clearing |
| Minchan Kim | zram backing device, idle/huge-page writeback |
| Jiayuan Chen | memcg dying bail-outs (4) |
| Richard Chang (Google) | zram compressed writeback, proactive-reclaim suspend abort |
| K Prateek Nayak (AMD) | scheduler idle load balancing |
| Shakeel Butt | memcg stats and shadow optimization (3) |
| firelzrd | Kcompressd-Unofficial |
| Qualcomm Technologies / QuIC | WALT smart_freq |
| OPLUS | `mi_dynamic_readahead` |
| OPPO | the `android_vh_customize_alloc_gfp` vendor hook |

This project's own code is released under `GPL-2.0`.

Full copyright attribution, per-commit credits and the author list are in
[docs/attribution.md](docs/attribution.md).

This project makes use of AI-assisted development.
