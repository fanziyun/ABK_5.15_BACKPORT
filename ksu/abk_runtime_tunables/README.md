# ABK 5.15 Runtime Tunables (KernelSU module)

The runtime companion of the [ABK 5.15 LTS backport](../..). The kernel grafts
in that repository ship mechanisms; this module drives them, because a mechanism
nothing triggers changes nothing.

Two jobs, in order of importance:

1. **Keep the zram algorithm policy in force** on every boot (hardcoded, not
   configurable -- see below).
2. **Drive the recompression sweeps** through the kernel's async worker, follow
   each sweep with a gated zsmalloc compaction pass (see Configuration), and
   report (or optionally tune) the other runtime knobs the grafts expose:
   MGLRU, THP, `vm.swappiness`, the schedutil smart-freq policy, dynamic
   readahead and cgroup proactive reclaim.

Since Batch 11 the policy has two enforcement layers, and only the fallback one
is this module:

| kernel | who decides the compressors | what the module does |
|---|---|---|
| built from this repo (lock on by default) | the kernel, at device creation, from the read-only `zram.abk_comp_algo`/`zram.abk_recomp_algo`; every later write to `comp_algorithm`/`recomp_algorithm` is accepted and ignored | verifies, re-asserts the 25%-of-RAM cap, and keeps its hands off the bring-up -- so the ROM's own setup (writeback backing device included) runs untouched |
| anything older (no lock) | whoever wins the pre-`disksize` window | owns the bring-up: `swapoff -> reset -> algorithms -> writeback -> disksize -> mem_limit -> mkswap -> swapon`, and re-checks it every `zram.reassert_interval_sec` |

## Why zram needed a companion module at all

Measured on a Redmi K70 (`vermeer`, android13-5.15-lts 5.15.215, KernelSU,
HyperOS port):

| observation | evidence |
|---|---|
| the graft is live | `zram.abk_recomp_algo` exists, `recomp_algorithm` is armed, `recompress_async`/`idle`/`compact` exist |
| recompression had **never run** | `mm_stat = 4096 62 …` (one page ever stored), `io_stat` all zero |
| the ROM's own zram owner leaves a dominated primary | `comp_algorithm` showed `[lz4hc]` while `CONFIG_ZRAM_DEF_COMP="lz4kd"`; a write after boot returns `-EBUSY` |
| **a root writer can still take the primary over** | on the *unlocked* build a `swapoff` + `reset` + rebuild left the device on `[deflate]` (the kernel's own default is `lz4kd`, so it was written by hand) and the module did not notice for up to `zram.recomp.interval_sec` |
| the ROM's zram daemon is dead | `CONFIG_ZRAM_WRITEBACK` is off, so the `writeback*` nodes do not exist; `mmd.setup_complete` is unset and `init.svc.mmd` does not exist. The ROM also sets `vendor.zram.disable=1` in `/product/etc/build.prop`, and the real bring-up owner is `/vendor/bin/init.kernel.post_boot.sh: configure_zram_parameters()` (disksize + `mkswap` + `swapon -p 32758`, no algorithm write at all) |

## The fixed policy (and why you cannot change it)

Compressing an exact 64 MiB ELF corpus on a single core, on a throwaway zram
device (`/sys/class/zram-control/hot_add`, live swap untouched):

| algo | compressed | ratio | compress | decompress | constant text corpus |
|---|---|---|---|---|---|
| `lz4` | 31.19 MB | 2.15x | 0.60 s (107 MB/s) | 865 MB/s | -- |
| **`lz4kd`** | **31.06 MB** | **2.16x** | **0.57 s (112 MB/s)** | **955 MB/s** | **901 KB** |
| `lz4hc` | 29.31 MB | 2.29x | 1.13 s (57 MB/s) | 901 MB/s | 999 KB |
| **`zstd`** | **24.21 MB** | **2.77x** | 0.73 s (88 MB/s) | 330 MB/s | **868 KB** |
| `deflate` | 23.51 MB | 2.85x | 2.29 s (28 MB/s) | 208 MB/s | -- |

* **primary = `lz4kd`** -- fastest both ways and the best ratio of the fast
  family (it is also the vendor's own `CONFIG_ZRAM_DEF_COMP`).
* **secondary = `zstd`** -- 22% smaller than `lz4kd`; the async worker pays the
  compression cost, and 330 MB/s of fault-time decompression is still far above
  any UFS read.
* **`lz4hc` is dominated in both slots** -- as a primary it doubles the
  compression cost on the hot swapout/fault path for a 5.6% ratio gain, and as
  a secondary it loses to `zstd`. On repetitive data it is even worse than
  `lz4kd`.

Hence: **there is no algorithm knob, no `mem_limit` knob and no swap-size knob
in this module**, and the kernel side of Batch 11 makes the selection itself
read-only:

* `zram.abk_comp_algo` (0444, default `lz4kd`) and `zram.abk_recomp_algo` (0444,
  default `zstd`) are read at device creation; a build without `lz4kd` keeps its
  own `CONFIG_ZRAM_DEF_COMP` and says so in the kernel log.
* `zram.abk_lock_algo` (0444, default `Y`) points both `comp_algorithm` and
  `recomp_algorithm` at a store that **reports success and keeps the locked
  value**. `-EPERM` was rejected deliberately: Android 16's `mmd_setup` aborts
  its whole zram bring-up -- writeback backing device and `mmd.setup_complete`
  included -- when an algorithm write fails, so refusing would re-create the
  very exclusivity Batch 11 removes.
* The lock survives `reset`: `zram_destroy_comps()` clears `comps[]`, never
  `comp_algs[]`, so a rebuild picks the locked names up again.
* Only the boot cmdline can differ (`zram.abk_lock_algo=0`,
  `zram.abk_comp_algo=lz4`); `action.sh unlock` prints exactly that.

Posture:

* **On a locked kernel a root shell cannot switch the algorithm**: the write is
  a reported no-op and the node keeps `[lz4kd]`. On an older kernel it can
  (anyone with root can win the pre-`disksize` window), and the module then
  repairs the device within `zram.reassert_interval_sec` seconds (default 60).
* The rewrite is skipped when the swap area is already in use beyond a 256 MiB
  slack. `swapoff` unswaps everything (no data is lost), so the gate bounds the
  transient RAM spike rather than protecting data: at boot the ROM initialises
  zram seconds before this module reaches it, and later in the boot the same gate
  keeps a device under pressure from being pushed over the edge.
* **Writeback and the algorithm policy no longer exclude each other.** The
  backing device is written in the same pre-`disksize` window and `reset` drops
  it (`reset_bdev()`), which is why the old module traded one for the other. The
  rewrite now remembers the attachment before the reset and puts it back in the
  same window, with the ROM's own `writeback_limit` preserved; when the kernel
  exposes writeback and nobody owns it, the module attaches a sparse backing
  file itself (`zram.writeback=auto`: `/data/per_boot/zram/zram_swap` -- the
  same place Android's `mmd_setup` uses -- behind a loop device, limit bounded,
  compressed writeback on where the kernel has it).
* Writeback is **not** enabled on the reference device: the kernel has no
  `CONFIG_ZRAM_WRITEBACK`, the ROM sets `vendor.zram.disable=1` and its `mmd`
  never completes. `zram.writeback=auto` therefore only acts if you turn the
  feature on in the kernel (`ABK_515_DEFCONFIG_ROM=1` adds
  `CONFIG_ZRAM_WRITEBACK=y`).

## Configuration (`tunables.conf`)

`key=value`, `#` comments, **empty value = leave the kernel alone**. Unknown
keys are reported in logcat (`ABK-Tunables`) and ignored.

| key | default | meaning |
|---|---|---|
| `zram.recomp.enable` | `1` | drive age-marked recompression sweeps (on by default, together with the compaction gate below -- both ride this clock) |
| `zram.recomp.idle_age_sec` | `3600` | mark only pages untouched this long |
| `zram.recomp.interval_sec` | `1800` | seconds between sweeps |
| `zram.recomp.threshold` | `0` | only recompress entries at least this large |
| `zram.recomp.mode` | `async` | `async` (kernel worker) or `sync` |
| `zram.compact.enable` | `1` | after each sweep tick, run one gated `compact` pass (rides the sweep clock, so `zram.recomp.enable=0` stops it too) |
| `zram.compact.min_waste_mb` | `50` | only compact when `mem_used_total − compr_data_size` exceeds this many MiB; 1..1024 |
| `zram.compact.waste_pct` | `15` | ... and the overhead exceeds this percentage of the compressed size (both gates must agree); 1..500 |
| `zram.writeback` | `auto` | `auto` attaches a backing file when the kernel supports writeback and nobody owns it; `off` only preserves an existing one |
| `zram.writeback.size_mb` | `1024` | backing file size, sparse, 64..8192 |
| `zram.reassert_interval_sec` | `60` | how often the supervisor re-checks the policy; on an unlocked kernel this is how long a runtime algorithm switch survives (5..3600) |
| `vm.swappiness` | *(empty)* | 0..300 |
| `vm.page_cluster` | *(empty)* | 0..8 |
| `vm.watermark_scale_factor` | *(empty)* | 1..3000 |
| `vm.min_free_kbytes` | *(empty)* | 1024..1048576 |
| `lru_gen.enable` | `0` | `1` turns MGLRU on (kernel has the code, ships it off) |
| `lru_gen.min_ttl_ms` | *(empty)* | MGLRU min TTL |
| `thp.mode` | *(empty)* | `always`/`madvise`/`never`; `madvise` is what makes `MADV_COLLAPSE` reachable |
| `sched.abk_sf_enable` | *(empty)* | `0`/`1` |
| `sched.abk_sf_floor_pct` | *(empty)* | 0..100 |
| `sched.abk_sf_sustained_ms` | *(empty)* | 1..60000 |
| `sched.abk_sf_exit_ms` | *(empty)* | 1..60000 |
| `readahead.dynamic_readahead` | *(empty)* | `0`/`1` |
| `cfr.enable` | `0` | proactive reclaim of cached groups |
| `cfr.interval_sec` | `300` | sweep interval |
| `cfr.freeze` | `0` | quiesce a group during its sweep (undone in the same sweep) |
| `cfr.quota_mb` | *(empty)* | cap the reclaimed bytes per group |
| `cfr.group` | *(empty)* | group names to sweep; empty = every `uid_*` group (this ROM names its groups: `cfr.group=freeze-app game`) |
| `report.logcat` | `1` | `0` silences the logcat mirror |

**Measure before you enable the opt-in knobs.** `lru_gen`, `thp` and
`swappiness` change global reclaim behaviour; `abk_sf` may over-boost on a
kernel booted with `sysctl.kernel.sched_pelt_multiplier=4` (the policy clamps
`util` to `capacity`, so the 90%-sustained test can saturate -- check whether
`scaling_cur_freq` sits at the floor permanently before leaving it armed).

The compaction gate exists because of a measured kill-storm on device: after an
app-cleaner killed every user process, `mm_stat` showed `mem_used_total` 352 MB
for a `compr_data_size` of 186 MB -- **89% of the zram footprint was zsmalloc
fragmentation** (freed swap slots leave dead zspages behind, and those zspages
stop serving their size class). One full pass via `/sys/block/zram0/compact`
took under a second on a big core and returned 105 MB. A pass on a healthy
device (post-compaction overhead measured: 3.3%) is pure CPU for nothing, so
both gates must call the device fragmented before the module writes anything.

## Using it

* `action.sh status` (or the KernelSU action button) prints the policy, the
  live zram state (including the lock and the writeback attachment), every graft
  knob the kernel exposes, supervisor health and the tail of the module log.
* `action.sh pass` re-asserts the policy and runs one age-marked sweep.
* `action.sh takeover` rewrites zram immediately (algorithms, cap, swap,
  writeback) even when the policy is already in force.
* `action.sh unlock` prints how the kernel-side lock is changed (boot cmdline).
* Progress goes to **`state/abk_runtime_tunables.log`** (capped at 64 KiB) and,
  best-effort, to logcat under the tag `ABK-Tunables` -- logcat was measured to
  be unreliable on the target ROM (empty for the `shell` user and for a
  `u:r:ksu:s0` writer), so the file is the source of truth. `report.logcat=0`
  silences only the logcat mirror.

The long-running parts are two supervisors started by `service.sh`
(`--supervise-zram`, `--supervise-cfr`), each recording its own pid under
`state/`.

## Notes, limits and testing seams

* cgroup proactive reclaim walks both layouts: v2
  (`/sys/fs/cgroup[/apps]/uid_*` with `memory.current` + `cgroup.freeze`) and v1
  (`/dev/memcg[/apps]/uid_*` with `memory.usage_in_bytes` + `freezer.state`),
  because this ROM mounts the memory controller on v1. The sweep itself is
  `tools/cached_freeze_reclaim.sh` in the parent repository, shipped into `bin/`
  by `embed.conf`; the supervisor here only schedules it in one-shot mode and
  logs the outcome, so there is one implementation for the CLI and for the
  module. On v1 a group also needs `memory.reclaim`, which only exists with that
  repository's `memcg_v1_reclaim` graft -- without it the tool says so instead of
  reporting a successful no-op. This ROM keeps the memory controller on v1 with
  **named** groups (`freeze-app`, `game`, `mimd`, `protect_memcg_*`) and no
  `uid_*` at all, so a per-UID sweep finds nothing here: select the groups to
  sweep with `cfr.group` (or run the tool by hand with `--group NAME --list`
  first).
* No SELinux rules are shipped: every node the module writes was verified
  writable from KernelSU's root context with no `avc: denied`.
* Nothing outside `/sys`, `/proc`, `/dev`, `/data/per_boot/zram` (the writeback
  backing file, and only when the kernel supports writeback and
  `zram.writeback` is not `off`) and the module's own directory is written. No
  system/vendor file is replaced.
* `ABK_SYS_ROOT`, `ABK_MEMCG_ROOT`, `ABK_PROC_SWAPS`, `ABK_MEMINFO`,
  `ABK_SWAPON`, `ABK_SWAPOFF`, `ABK_MKSWAP`, `ABK_LOSETUP`, `ABK_DD`,
  `ABK_ZRAM_NODE`, `ABK_ZRAM_WB_DIR`, `ABK_ZRAM_WB_FILE`, `ABK_STATE_DIR`,
  `ABK_RUN_DIR`, `ABK_CONF`, `ABK_RECOMP_TOOL`, `ABK_CFR_TOOL` and `ABK_STDOUT`
  exist so the policy can be exercised against a fixture tree by the
  repository's tests. They are not user-facing knobs.
* **Shell arithmetic is not portable on this device**: `/system/bin/sh` is
  Android's mksh and there `15561024 * 1024` is `-1245380608` and even
  `[ 17179869184 -gt 0 ]` is false, while KernelSU's busybox ash is 64-bit. Every
  byte count the policy touches (RAM in bytes, a 16 GiB `disksize`, the reclaim
  quota, per-group `memory.current`) therefore goes through `awk`
  (`abk_mul_div`, `abk_mem_pct_bytes`, `abk_gt`, `abk_le`) instead of `$(())` or
  `[ -gt ]`. Both symptoms were measured on device: a wrapped `mem_limit` is
  refused by the kernel (the cap silently stays off, with only a warning in the
  log), and a false `-gt` made the module treat a healthy 16 GiB device as
  sizeless and rewrite it to `MemTotal/2`.
* The writeback path itself (backing file + loop device + `backing_dev`) is
  **not fully hardware-verified on the reference device**, because that kernel
  has no `CONFIG_ZRAM_WRITEBACK`. The backing-file half is: the helper creates a
  sparse 1 GiB file (1 MiB on disk), takes a free loop device and attaches it,
  measured on device. Every step is best-effort -- a failure logs and continues
  without writeback rather than blocking the swap bring-up.
