# ABK 5.15 Runtime Tunables (KernelSU module)

The runtime companion of the [ABK 5.15 LTS backport](../..). The kernel grafts
in that repository ship mechanisms; this module drives them, because a mechanism
nothing triggers changes nothing.

Four jobs, in order of importance:

1. **Keep the zram algorithm policy in force** on every boot (hardcoded, not
   configurable -- see below).
2. **Add the kernel-domain SELinux rules the zram writeback path needs.**
   Without them a writeback is denied at the first page and moves nothing while
   reporting success (see "SELinux" below).
3. **Drive the writeback sweep** when it is asked for. The kernel only moves a
   page when userspace tells it to, and nothing else on this ROM does, so
   attaching a backing device is not enough on its own. `zram.writeback.trigger`
   is the switch; it is off by default because a sweep spends flash.
4. **Drive the recompression sweeps** through the kernel's async worker, follow
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
* Writeback is **not** enabled on the reference device's stock kernel: that
  build has no `CONFIG_ZRAM_WRITEBACK`, the ROM sets `vendor.zram.disable=1`
  and its `mmd` never completes. `zram.writeback=auto` therefore only acts if
  you turn the feature on in the kernel (`ABK_515_DEFCONFIG_ROM=1` adds
  `CONFIG_ZRAM_WRITEBACK=y`) -- and once it is on, the SELinux rule below
  decides whether a writeback moves anything at all.

## SELinux: the kernel-domain rules writeback needs

The zram writeback data path is kernel-side. Whoever attaches the loop device,
the reads and writes of the backing file happen in the **loop worker**, a kernel
thread running as `u:r:kernel:s0`. Android's policy has no rule for that
direction, so under Enforcing the first page returns `-EIO`,
`alloc_block_bdev()` frees the reserved block again, and writeback **reports
success while moving nothing**.

Measured on the target device (vermeer, android13-5.15 5.15.216, ROM-tier
kernel, SELinux Enforcing, rule submitted at run time):

| observation | number |
|---|---|
| the module's own backing store (`/data/per_boot/zram`), pages written since boot | `bd_stat = 0 0 0` |
| a manual 16 MiB writeback budget, no rule | 0 of 4096 pages written |
| the same budget, rule submitted | exactly 4096 pages (`bd_stat = 4096 0 4096`) |
| a 32 MiB writeback plus full read-back, rule submitted | `rc=0`, no surviving AVC, md5 identical before/after |

So the module ships `sepolicy.rule` and submits it through
`ksud sepolicy apply` in the **post-fs-data** stage. That timing is the point:
the rule has to exist before something attaches a backing device, because from
that moment on the denied side is a kernel thread. On a manager that loads
module `sepolicy.rule` files itself the call is a redundant no-op (submitting
the same rule twice is not an error on this `ksud`), and where no manager can
add it the module logs the failure and carries on -- it never relaxes the
policy to get its way.

Each rule is deliberately one least-privilege `allow`, and there are two of
them because the rule has to name the file *type* of the backing store actually
in use -- which depends on who attached it:

```
allow kernel zram_data_file file { read write }
allow kernel extm_data_file file { read write }
```

The module's own backing file lives under `/data/per_boot/zram/` and carries
`zram_data_file`. The ROM's memory extension (Xiaomi `extm`) backs *its* loop
device with `/data/extm/extm_file`, which carries `extm_data_file` -- and a
preserved ROM attachment is the case the module prefers, since it restores what
it finds rather than trading it away. Measured on the same device on 2026-09-15:

```
u:object_r:extm_data_file:s0  /data/extm/extm_file
u:object_r:zram_data_file:s0  /data/per_boot/zram
```

so a build carrying only the first rule matches nothing on that device and
every page still returns `-EIO` with `bd_stat` pinned at `0 0 0`. Naming a type
the ROM does not define is harmless: this `ksud` reports success for an
unresolvable symbol anyway.

`read` and `write` are all the loop worker needs, because the kernel never
resolves the path itself: `losetup` opened the file from the root domain, and
only I/O on the already-open file happens in the kernel domain. A 32 MiB
writeback-and-read-back cycle leaves no other denial.

Read the verdict node, not the attachment, to see whether it took effect:
`action.sh status` prints `bd_stat`, and "backing device attached, `bd_stat`
`0 0 0`" is exactly the shape of a writeback that is being denied.

KernelSU applies a policy patch in memory, so a reboot drops it -- which is why
this module re-submits the file at every boot instead of once at install. That
path was verified end to end: with v0.7.0 installed (and the running module
directory still without a `sepolicy.rule`), a reboot produced the boot-log line
`selinux: submitted ... via /data/adb/ksud`, after which a 32 MiB writeback and
read-back moved 7690 pages with an identical payload and zero AVCs, with no
manual `ksud` call anywhere in that boot.

## Configuration (`tunables.conf`)

`key=value`, `#` comments, **empty value = leave the kernel alone**. Unknown
keys are reported in logcat (`ABK-Tunables`) and ignored.

| key | default | meaning |
|---|---|---|
| `zram.recomp.enable` | `1` | drive age-marked recompression sweeps (on by default, together with the compaction gate below -- both ride this clock) |
| `zram.recomp.idle_age_sec` | `3600` | mark only pages untouched this long |
| `zram.recomp.interval_sec` | `1800` | seconds between sweeps |
| `zram.recomp.mark_interval_sec` | `86400` | seconds between **mark** steps. Only a mark makes new cold pages eligible; the sweeps in between pass `--no-mark`, because a mark sets `ZRAM_IDLE` on every page older than the age cutoff and a recompressed page never has its age refreshed (`zram_recompress()` reads through `zram_read_from_zspool()`, not `zram_accessed()`), so marking before every pass would hand a capped sweep the same prefix forever. This is the knob that bounds what a capped sweep can reach: one cycle drains `mark_interval_sec / interval_sec` sweeps × `max_pages` entries -- 3 GiB of cold pages at the defaults. |
| `zram.recomp.threshold` | `0` | only recompress entries at least this large |
| `zram.recomp.max_pages` | `16384` | how many entries **one** sweep may attempt (0 = no cap). An uncapped pass walks every idle entry in index order -- tens of seconds of one core on a full device; 16384 attempted entries is 64 MiB of pages. Requires the kernel's `max_pages` parameter (module Batch 24): on a kernel without it the parameter is ignored and the sweep stays unbounded, exactly as before that batch. |
| `zram.recomp.mode` | `async` | `async` (kernel worker) or `sync` |
| `zram.compact.enable` | `1` | after each sweep tick, run one gated `compact` pass (rides the sweep clock, so `zram.recomp.enable=0` stops it too) |
| `zram.compact.min_waste_mb` | `50` | only compact when `mem_used_total − compr_data_size` exceeds this many MiB; 1..1024 |
| `zram.compact.waste_pct` | `15` | ... and the overhead exceeds this percentage of the compressed size (both gates must agree); 1..500 |
| `zram.writeback` | `auto` | `auto` attaches a backing file when the kernel supports writeback and nobody owns it; `off` only preserves an existing one |
| `zram.writeback.size_mb` | `1024` | backing file size, sparse, 64..8192 |
| `zram.writeback.trigger` | `off` | the sweep the kernel does not run by itself: `off` touches nothing, `huge` moves incompressible pages only (frees 1:1 against the flash it spends -- the cheap mode), `idle` also moves cold compressed pages (about a third of the flash back, so only for a device against its `mem_limit`) |
| `zram.writeback.budget_mb` | `256` | flash a sweep may spend, in MiB, re-armed before every pass and clamped down to the backing device's size so a pass cannot run it out of space; 1..65536 |
| `zram.writeback.interval_sec` | `86400` | seconds between sweeps -- daily by default, which is the shape the ROM's own extm used (`persist.miui.extm.daily_flush_count` is a *daily* budget) |
| `zram.writeback.idle_age_sec` | `7200` | `trigger=idle` only: mark a page idle only if nothing touched it for this long. Never `all`, which would move every page on the device in one pass; 60..2592000 |
| `zram.reassert_interval_sec` | `60` | how often the supervisor re-checks the policy; on an unlocked kernel this is how long a runtime algorithm switch survives (5..3600) |
| `vm.swappiness` | *(empty)* | 0..300 |
| `vm.page_cluster` | *(empty)* | 0..8 |
| `vm.watermark_scale_factor` | *(empty)* | 1..3000 |
| `vm.min_free_kbytes` | *(empty)* | 1024..1048576 |
| `lru_gen.enable` | `0` | `1` turns MGLRU on (kernel has the code, ships it off) |
| `lru_gen.min_ttl_ms` | *(empty)* | MGLRU min TTL |
| `thp.mode` | *(empty)* | `always`/`madvise`/`never`; `madvise` is what makes `MADV_COLLAPSE` reachable |
| `sched.abk_sf_enable` | `0` | the smart-freq floor; leave at `0` whenever a vendor FAS/WALT governor owns DVFS (see below) |
| `sched.abk_sf_floor_pct` | *(empty)* | 0..100 |
| `sched.abk_sf_sustained_ms` | *(empty)* | 1..60000 |
| `sched.abk_sf_exit_ms` | *(empty)* | 1..60000 |
| `readahead.dynamic_readahead` | *(empty)* | `0`/`1` |
| `psi.cgroup` | `keep` | per-cgroup pressure accounting: `keep` (the built-in default, and the shipped value) leaves every group as the kernel set it; `auto` switches off only groups holding no tasks and no memory, which this device measured to be a no-op; `aggressive` switches off everything outside the protect list |
| `psi.cgroup.protect` | `system` built-in, `system,protect_memcg` shipped | path **prefixes** that keep their accounting -- narrow on purpose, and the shipped value covers this ROM's own memory-daemon groups |
| `psi.cgroup.interval_sec` | `300` | seconds between passes; a pass only reaches groups that are still on, so a repeat walk writes nothing (min 60) |
| `cfr.enable` | `0` | proactive reclaim of cached groups |
| `cfr.interval_sec` | `300` | sweep interval |
| `cfr.freeze` | `0` | quiesce a group during its sweep (undone in the same sweep) |
| `cfr.quota_mb` | *(empty)* | cap the reclaimed bytes per group |
| `cfr.group` | *(empty)* | group names to sweep; empty = every `uid_*` group (this ROM names its groups: `cfr.group=freeze-app game`) |
| `report.logcat` | `1` | `0` silences the logcat mirror |

**Measure before you enable the opt-in knobs.** `lru_gen`, `thp` and
`swappiness` change global reclaim behaviour.

`abk_sf` (the Batch 10-4/10-5 schedutil smart-freq floor) ships disabled because
of what was measured here, not because of a theory.  A FAS-style owner keeps
`min == max == its own target`, and the floor is clamped to `policy->max`, so on
such a policy "raise to the floor" degenerates into "keep the frequency already
applied" and every downscale is cancelled.  That is *not* avoided by the tree
running a foreign governor: the policy samples in `android_vh_scheduler_tick` and
applies in `android_vh_cpufreq_resolve_freq`, both reached whichever governor owns
the policy.  Which is how the first shipped form (Batch 10-4c -- still what a
pre-10-5 kernel runs) froze a cluster at 1785600 for a whole game session while
`waltgov` computed 766 MHz from 22% demand, and why Batch 10-5 added
`abk_sf_dvfs_owned()` to make a current payload stand down there.  The two payload
generations are tellable apart from userspace: only 10-5 exposes the read-only
`abk_sf_boosting` node.

The same ceiling reaches *placement*, which no frequency check can see.  EAS and
WALT rank cores by DMIPS capacity scaled by the policy's own ceiling, so whoever
writes `scaling_max_freq` also decides how big each core looks.  Measured on
SM8550 with a userspace scheduler profile in play: the super core's capacity
became `277 of 1024` while the mid cluster stood at `749 of 855`, and across 10 s
windows the super core was **no bigger than a mid core in a third to a half of the
polls** -- so app launches ran on the mid cluster and the prime sat at its floor
with nothing on it.  The fix belongs to whoever writes that ceiling (raise the
super core's ceiling in the profile, or stop the profile writing
`policy*/scaling_max_freq`); no governor change and nothing in this module can
undo an inverted capacity ranking.

The module logs both pictures at boot (`dvfs: ...`, one line per policy, with
`arch=` the DMIPS capacity and `cap_view=` the scaled one, plus `/proc/fas`),
warns when the biggest core is not the biggest core on offer, and warns -- fatally
for a pre-10-5 payload, which has no ownership gate -- when `abk_sf_enable=Y` is
armed while some policy is foreign-governored or pinned at `min == max`.  It ships
`bin/abk_fas_check.sh`, which decides between a healthy single-point owner and a
lock -- `--sample N` over a real workload (it counts the polls where the capacity
inversion is live and exits 4 when that share reaches `--invert-pct`, default 5%),
or `--probe` to apply load and confirm the frequency comes back down.  It also
refuses to read a quiet `abk_sf_enable=N` as safety on a pre-10-5 payload: the
knob is runtime-only there, so a reboot can re-arm an ungated floor (pin
`sched.abk_sf_enable=0` in this file instead).  Only arm
`abk_sf` on a device whose governor really is `schedutil`, and re-check with
`--probe` after.

The compaction gate exists because of a measured kill-storm on device: after an
app-cleaner killed every user process, `mm_stat` showed `mem_used_total` 352 MB
for a `compr_data_size` of 186 MB -- **89% of the zram footprint was zsmalloc
fragmentation** (freed swap slots leave dead zspages behind, and those zspages
stop serving their size class). One full pass via `/sys/block/zram0/compact`
took under a second on a big core and returned 105 MB. A pass on a healthy
device (post-compaction overhead measured: 3.3%) is pure CPU for nothing, so
both gates must call the device fragmented before the module writes anything.

## Per-cgroup pressure accounting (the switch nobody is reading)

Every cgroup derives and times its own pressure numbers on every task state
change, all the way up the hierarchy. On this device that is **452 groups**,
because Android's v2 layout creates one per uid and one per pid. Pointed before
switching anything: the only pressure readers on the device were `lmkd`,
`system_server` and `mimd`, and all three had the global
`/proc/pressure/memory` open -- which is served by the root group and is not
this switch at all. **No process had any per-cgroup PSI file open.** So the work
was being charged for a consumer that did not exist, and it was charged on the
state changes of every task in every one of those groups.

`cgroup.pressure` is this module's own graft -- Batch 21's
`psi_cgroup_pressure_switch`, ported from the android14-6.1 ACK implementation
(android13-5.15 has no such file). That is why there is no new group here: on a
kernel built with Batch 21 or later this is policy only, and on one without it
the pass finds nothing and says `nodes=0`. Two properties of how that group was
written decide the shape of the policy:

* the switch is **not hierarchical** -- turning off a parent leaves its children
  accounting, so the pass has to decide group by group, which it does;
* a group that is off keeps its **task counts** (the levels above and below read
  them) and stops deriving state masks; its pressure files answer
  `-EOPNOTSUPP` and a `poll` trigger on it is refused. A monitor therefore gets
  an explicit error, not a plausible stale zero.

**The root group is never written, whatever the mode says.** In this graft the
root's switch is not a cgroup flag -- it flips `psi_system`, the global numbers.
Writing it would take pressure visibility away from the low-memory killer, which
is the opposite of the point.

**Why `protect=system` and not `system,apps`.** Adding `apps` looks prudent and
removes most of the win: `apps/pid_*` and `uid_*/pid_*` are the bulk of the
hierarchy and nothing reads them. The `system` subtree stays because that is
where a vendor monitor could be hiding, and this is the one failure mode the
measurement cannot show you -- a reader that does not exist today.

**There is no runtime "turn it back on", and that is a deliberate limit.**
Batch 21 keeps the state in a flag bit, frees nothing, and has a restart path
(`psi_cgroup_restart()` rebuilds each CPU's state mask from the counts it kept),
so a round trip is in fact safe *on this kernel*. Upstream's own version of the
switch frees the group's per-cpu windows and states that re-enabling is not
restore safe. This companion also runs on kernels it did not build, and cannot
tell the two apart from userspace -- so `bin/abk_psi_policy.sh` writes only `0`.
The accounting state comes from booting with `psi.cgroup=keep`.

That is also why the measurement is **two boots** rather than one live flip: the
half of a session after a re-enable carries no history for the window it was off.
`bin/abk_psi_bench.sh` runs a fixed amount of task state change work and reads
the CPU back out of `/proc/stat`; it labels each round with the state *measured
from the tree*, not with the config key, because a refused write would otherwise
mislabel a whole run. The protocol, and what decides the shipped default, is
`docs/psi_field_protocol.md` in the parent repository.

## Using it

* `action.sh status` (or the KernelSU action button) prints the policy, the
  live zram state (including the lock and the writeback attachment), every graft
  knob the kernel exposes, supervisor health and the tail of the module log.
* `action.sh pass` re-asserts the policy and runs one age-marked sweep.
* `action.sh takeover` rewrites zram immediately (algorithms, cap, swap,
  writeback) even when the policy is already in force.
* `action.sh unlock` prints how the kernel-side lock is changed (boot cmdline).
* `bin/abk_psi_policy.sh --status` reports the per-cgroup PSI tree as it stands
  (nodes, how many are off, how many are protected, how many were refused);
  `--apply --mode auto|aggressive` runs one pass by hand, and `--selftest`
  checks the decision table against a fixture tree with no device at all.
* `bin/abk_psi_bench.sh --rounds 3` runs the A/B measurement described above.
* `bin/abk_launch_bench.sh` (or `action.sh launch [warm|drop] [iters]`) measures
  cold launch: it force-stops each app, runs `am start -W`, and reports the
  `TotalTime` median next to what the launch window actually looked like --
  per-cluster `cap_view` and ceiling movement, the share of main-thread samples
  that landed on the biggest cluster, the cpuset the app was in and whether that
  cpuset even lists the super core, and the pages the launch read off storage.
  It ends in one of four verdicts: **cpuset bound** (the super core is not in the
  app's cpuset at all), **ceiling bound** (a measured cap inversion -- the case
  Batch 10-6 costed at 16% on this ROM), **placement bound, cause not measured**
  (the super core was available and the launch still did not run there), and
  **"neither rule fired"** with the two-arm recipe.
  The storage's share is deliberately *not* claimed from one arm: `pgpgin` is a
  machine-wide counter, and measured here, dropping the page cache made every
  launch read 5 to 340 times more pages for only 11% to 29% more wall clock --
  so a launch that causes reads is not a launch that waited for them. Run
  `--mode warm --save FILE` then `--mode drop --compare FILE`, and the tool
  reports the per-app delta and only calls storage a lever above 15%.
  `--selftest` exercises the decision logic on a fixture with no device. It
  **refuses to measure a phone that is asleep or on its lock screen** and
  re-checks both before every launch: with the screen off, or the keyguard up, a
  launched app never becomes the top app, so it stays in the foreground cpuset
  and every launch then reads "cpuset bound" -- true of that state, false of the
  phone in a hand -- while `am start -W` quietly stops reporting a `LaunchState`
  at all. Wake and unlock it, or pass `--allow-screen-off` to say you mean that
  state.
* Progress goes to **`state/abk_runtime_tunables.log`** (capped at 64 KiB) and,
  best-effort, to logcat under the tag `ABK-Tunables` -- logcat was measured to
  be unreliable on the target ROM (empty for the `shell` user and for a
  `u:r:ksu:s0` writer), so the file is the source of truth. `report.logcat=0`
  silences only the logcat mirror.

The long-running parts are three supervisors started by `service.sh`
(`--supervise-zram`, `--supervise-cfr`, `--supervise-psi`), each recording its
own pid under `state/`. The PSI one is not started at all when `psi.cgroup=keep`
-- one decision, no idle loop -- and it stops itself after its first pass if every
write was refused, because re-walking hundreds of nodes every five minutes to be
told `EACCES` again is a supervisor that has become the cost it was meant to
remove.

## Notes, limits and testing seams

* The PSI pass has seams so its decision table is testable without a device:
  `ABK_PSI_CGROOT` / `--cgroot` point the walk at a fixture, `ABK_PSI_TOOL`
  redirects the companion at the tool, `ABK_BENCH_BIN` gives the bench a host
  binary to fork. `bash tools/abk_psi_policy.sh --selftest` builds a fake tree
  (root, protected subtree, populated group, empty group, already-off group,
  unreadable node) and asserts each of them, including that a second pass writes
  nothing. It caught two real defects while being written: a protect default that
  covered `apps` (killing most of the win) and a failed `> node` whose
  `Permission denied` came from the *shell*, not from `echo`, so it escaped
  `2>/dev/null` and polluted the log.

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
  **named** groups (`freeze-app`, `game`, `mimd`, `protect_memcg_*`), and its
  per-UID groups sit one level below the roots the tool searches: all 87 of them
  live under `mimd`, none at the top. A per-UID sweep of the default roots
  therefore finds nothing here, and there are two ways to reach the real ones.
  Name the groups directly with `cfr.group` (`freeze-app`, `game`) -- both read 0
  except at the instant the ROM parks an app, so this route reclaims almost
  nothing. Or name the deeper directory as an extra root with
  `cfr.cgroup_root=/dev/memcg/mimd`, which reaches 1.5 GiB across 87 groups; but
  a per-UID tree holds the app on screen as well as the cached ones, so that
  route needs one of the two cached-app filters below.
* **Which groups are cached apps, and why there are two filters.** Neither
  filter is a guess about names: both ask the platform. `cfr.cached_only` asks
  the rank -- AOSP puts every process of a cached app at or above
  `CACHED_APP_MIN_ADJ` (900) and nothing a user can see reaches it -- and
  `cfr.frozen_only` asks the freezer. The rank is the wider of the two and the
  one that does not wait to be told: the freezer only parks a process after it
  has been cached a while. Measured on this device 2026-09-16 (after an
  installed extension that had been doing the freezing was switched off, which
  is what makes the numbers the platform's own): **0** frozen in the first
  minutes after boot, then exactly **1** -- a GMS unstable process at adj 945 --
  stable there for the next seven minutes, while `--cached-only` selected **14**
  groups in the same run. `cfr.frozen_only` stays for the device that freezes
  more eagerly, and because it is the stronger promise of the two: a frozen app
  cannot fault its pages back, a merely cached one can. Both filters require
  **every** task in the group to qualify, not any of them, because the write
  reclaims the group as a whole. They compose. A bounded sweep of the
  `--cached-only` set moved `cfr_reclaim_reclaimed` from 0 to 29 448 pages, and
  two control groups holding a visible process (`uid_10142` at adj 0, `uid_10205`
  at 100) moved by 0.2% and 0.7% across it -- runtime noise, not reclaim, where a
  reclaim would have taken up to the 16 MiB quota from each. Run the tool by hand
  with `--list` first; it prints what it would touch and names each group it left
  out.
* Exactly **two** SELinux rules are shipped (`sepolicy.rule`, submitted at
  post-fs-data): `allow kernel zram_data_file file { read write }` and
  `allow kernel extm_data_file file { read write }` -- one per backing-file
  type, for the kernel-domain writeback path described above. Every sysfs/proc
  node the module writes was verified writable from KernelSU's root context with
  no `avc: denied`, so no other rule is needed -- and none is added.
* Nothing outside `/sys`, `/proc`, `/dev`, `/data/per_boot/zram` (the writeback
  backing file, and only when the kernel supports writeback and
  `zram.writeback` is not `off`) and the module's own directory is written. No
  system/vendor file is replaced.
* The one node the module writes whose effect lands outside `/sys` is
  `zram0/writeback`, and only under `zram.writeback.trigger`. It writes into
  the backing device that is already attached -- the ROM's own extm file
  included, when that is the device the ROM attached. With the shipped
  `trigger=off` the module never writes that node.
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
  The awk itself needs one guard too: `printf "%d"` converts through the awk
  build's int, and the first boot of the v0.6.1 module measured one
  service-context awk clamping `MemTotal` bytes to `2147483647` there -- the
  25 % cap briefly landed as 512 MiB (the kernel rounded the `536870911` it was
  given; the WARN and the supervisor's next pass say the rest). Every awk
  format that can print a byte count now uses `%.0f`, exact for integers below
  2^53. `abk_gt`/`abk_le` print nothing and `cap_view` prints a capacity
  (≤ 1024), so `%d` is provably safe where they keep it.
* The writeback path (backing file + loop device + `backing_dev`) is verified
  on device against the ROM-tier kernel (`ABK_515_DEFCONFIG_ROM=1`,
  `CONFIG_ZRAM_WRITEBACK=y`): the helper's half creates a sparse 1 GiB file
  (1 MiB on disk), takes a free loop device and attaches it, and a 32 MiB
  writeback through a module-shaped attachment moves exactly the pages it should
  with the payload verified identical before and after. What that same run also
  exposed is the SELinux dependency above -- without the rule the numbers are
  bit-identical to "no writeback at all". Every step stays best-effort: a
  failure logs and continues without writeback rather than blocking the swap
  bring-up.