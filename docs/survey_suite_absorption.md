# ABK_ABI_PATCH_SUITE absorption survey (Batch 15)

Batch 15 retires the suite-preference rule: the ABK_ABI_PATCH_SUITE
optimization inventory is absorbed into this module so the module is
self-sufficient (see "Suite absorption" in `docs/porting_policy.md`).
This doc records **what the suite actually carried**, **what is genuinely
absorbable onto android13-5.15**, and **what is not, with the evidence**.

Two facts about the suite's shape drive everything below.

## 1. The suite's registry is report metadata only

`ABK_ABI_PATCH_SUITE/scripts/abk_feature_porting.py` declares
`PatchGroup(key, summary)` records in a module-level `PATCH_GROUPS` tuple
(:18-105), but nothing dispatches on them: they are emitted into the report's
`patch_groups` field (`build_report()` :3784-3790).  Application is imperative
`replace_once(text, old, new, label)` / `text.replace(old, new, 1)` calls inside
`patch_*(common_root)` functions.

**The quality gap that matters:** those calls are guarded by `if old in text:`.
An anchor that does not match is therefore a **silent no-op that still reports
success**.  This module's `replace_once` takes a `required` flag and aborts the
whole group transactionally instead, and `tests/step_audit.py` rejects a
`required` step that reports `already_present`.  Absorption therefore does not
copy the suite's edit mechanism — it re-derives every edit as a `required`
anchor step and proves uniqueness against four pristine trees.

## 2. What the suite claimed (KABI)

Exactly two structs, five slots claimed by the suite; four are still claimed here
(Batch 16 released the `sched_entity` slot-4 row):

| struct | slot | field | file |
|---|---|---|---|
| `struct sched_entity` | 1 | `u64 deadline` | `include/linux/sched.h` |
| `struct sched_entity` | 2 | `u64 min_vruntime` | `include/linux/sched.h` |
| `struct sched_entity` | 3 | `s64 vlag` | `include/linux/sched.h` |
| ~~`struct sched_entity`~~ | ~~4~~ | ~~`u64 slice`~~ | released by Batch 16: written once at `abk_eevdf_slice()`, read nowhere |
| `struct request_queue` | 1 | `unsigned int async_depth` | `include/linux/blkdev.h` |

Slots 2-4 of `request_queue` stay `ANDROID_KABI_RESERVE`.  The suite also
recognises an older broken packing (`USE(3, struct{u64 min_slice; u64 max_slice;})`)
and upgrades it, and restores `struct sched_rt_entity` slots to bare `RESERVE`
if a previous run claimed them.

These are precisely the slots this module's red line used to forbid.  Removing
the red line and absorbing the features are the same decision, and the result is
**not** "both modules can coexist": this module and the suite are now mutually
exclusive, because a doubly-claimed KABI slot is a hard KMI break.

## 3. File-layout feasibility probe (measured)

The suite targets mixed baselines, so file existence was probed in the real
trees before any porting: `common-5.15-2024-11` (5.15.167) vs `common-6.1-src`.

### Present in both — absorbable in principle

`kernel/pid.c`, `fs/file.c`, `mm/slub.c`, `mm/huge_memory.c`, `mm/memory.c`,
`include/linux/blkdev.h`, `block/{blk-core,blk-mq-sched,blk-sysfs,elevator,mq-deadline,bfq-iosched,kyber-iosched}.c`,
`include/linux/sched/nohz.h`, `include/linux/tick.h`, `kernel/time/tick-sched.c`,
`kernel/sched/idle.c`, `kernel/bpf/helpers.c`, `include/linux/sched.h`,
`kernel/sched/fair.c`.

### Absent on 5.15 — blocks the feature as written

| probe | 5.15 | 6.1 | consequence |
|---|---|---|---|
| `mm/swap.h` | absent | present (152 lines) | `swap_table_phase2_large_folios` anchors on a file that does not exist; 5.15 keeps swap internals in `include/linux/swap.h` + `mm/swap_state.c` |
| `io_uring/` layout | `Makefile`, `io-wq.c`, `io-wq.h`, `io_uring.c` | 56 files | the suite's `io_uring/{register,net,filetable,refs,opdef,rw,poll,…}.c` targets do not exist |
| `io_uring/io_uring.c` size | **11,116 lines** (monolith) | 4,363 lines | even the one file that exists has a different internal shape, so its anchors do not transfer |

### Absent in **both** 5.15 and 6.1 — the group is dead code

| probe | 5.15 | 6.1 | consequence |
|---|---|---|---|
| `defer_timer_wq_op` | 0 | 0 | `bpf_timer_bpf_wq_lockless` cannot anchor even on the suite's own assumed shape; its `supports_bpf_wq()` precheck already returns `blocked_by_missing_anchor` |
| `bpf_wq` | 0 | 0 | same |
| `bpf_async_use_direct_start` | 0 | 0 | same |
| `compressed_wb` (zram) | 0 | 0 | the suite's `zram_compressed_writeback` adds a **control-surface stub with no I/O implementation** |
| `compressed_writeback` (zram) | 0 | 0 | same |
| `zs_obj_read_begin` / `zs_obj_read_end` (zsmalloc) | 0 | 0 | the *real* compressed-writeback needs a zsmalloc mapping API that does not exist in either tree |

## 4. Verdicts

### Absorbed (implemented as this module's own groups)

| suite group | subsystem | notes |
|---|---|---|
| `pid_alloc_hotpath_phase2` | `kernel/pid.c` | `-ENOSPC`→`-EAGAIN`, single `idr_preload(GFP_KERNEL)` retry |
| `fd_alloc_hotpath` | `fs/file.c` | composes with this module's existing `fdtable_alloc_conventions`; see the batch module for the ordering constraint |
| `close_range_hotpath` | `fs/file.c` | bitmap-driven batched range traversal |
| `slab_alloc_free_hotpath` | `mm/slub.c` | bulk-free backend, free-side validation split, bulk-alloc prefetch |
| `hugepage_fault_alloc_fastpath` | `mm/huge_memory.c`, `mm/memory.c` | anon THP fault-time helper split |
| `blk_mq_async_depth` | `include/linux/blkdev.h` + 8 block files | `request_queue` slot 1 |
| EEVDF family | `include/linux/sched.h`, `kernel/sched/fair.c` | `sched_entity` slots 1-3 (slot 4 released by Batch 16) |
| `nohz_field_refinement` | `include/linux/sched/nohz.h`, `kernel/time/tick-sched.c` | tick state accessors |
| `avg_idle_preemption_mode` | `kernel/sched/{core,fair,idle,sched.h}` | drops `wake_avg_idle` prediction |

### Not absorbed — recorded so this is not relitigated

| suite group | verdict | evidence |
|---|---|---|
| `io_uring_nowait_core`, `io_uring_nowait_rw_net`, `io_uring_cbpf_filters`, `io_uring_non_circular_sq` | **not portable as written** | they target the post-5.18 split `io_uring/` layout; 5.15 ships an 11,116-line `io_uring/io_uring.c` monolith.  The suite's own backlog already classifies the cBPF / non-circular-SQ / zcrx items as `blocked_by_missing_anchor` on the older single-file layout.  A hand-port onto the monolith is a separate project, not an absorption. |
| `io_uring_support_modules` | **classification only** | grades 16 support modules against a 7.0.12 reference tree and writes nothing.  Nothing to absorb. |
| `io_uring_large_rx_buffer_zcrx` | **marker-only** | inserts one comment before `io_recvzc_prep()` and reports `partial`.  Absorbing it would create a phantom group. |
| `sched_eevdf_runtime_state_phase3` | **marker-only** | inserts one comment; the real phase-3 semantics land inside `patch_sched_pick_logic()`.  Not a separate feature. |
| `bpf_timer_bpf_wq_lockless` | **anchor absent in 5.15 and 6.1** | `defer_timer_wq_op` / `bpf_wq` are 0 hits in both trees. |
| `zram_compressed_writeback` | **stub only** | adds a `compressed_wb` flag + sysfs attribute with no I/O path; the real feature needs `zs_obj_read_begin/end`, absent from both trees.  Independently confirmed by the Batch 14 audit (`research/zram_writeback_plan.md` §6). |
| `swap_table_phase2_large_folios` | **needs re-anchoring; ported only if it genuinely lands** | `mm/swap.h` does not exist on 5.15. |
| `tcp_socket_layout_reduction` | **not a feature here** | the suite itself records `blocked_by_layout`. |
| `ipv6_tcp_output_path` | **not a feature here** | the suite itself records `report_only`. |

## 5. Deliberately unchanged sibling boundaries

Absorbing the ABK suite does **not** touch the other sibling boundaries:

- `fs/f2fs` and `drivers/scsi/ufs` remain F2FS/UFS-suite territory and are still
  off limits for this module.
- The suite's non-optimization children are out of scope: `security_backport`
  (security-only fixes are excluded by this module's own scope rule),
  `display_release_spoof` / `boot_image_logging` (anti-detection display
  tricks), `abi_bridge` / `abi_fixups` (7.0.12 loader glue), `network_porting`
  and `framebuffer_bootlog` (both paused in the suite).
- `scripts/abk_storage_whole_target.py` in the suite is orphaned dead code
  (nothing invokes it) and is not a source for anything.
