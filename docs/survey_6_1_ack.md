# Upstream survey: android14-6.1 ACK line features for the 5.15 baseline

Scope: features carried by the Android Common Kernel **android14-6.1**
branch that are absent from the android13-5.15 2024-11 baseline
(5.15.167) and portable onto it as bounded anchor-script grafts.
Features/optimizations only; pure security fixes out of scope by policy
(`porting_policy.md`). Survey method: file-level diff of two shallow
clones (`android13-5.15` vs `android14-6.1`, plus the
`android13-5.15-2024-11_r14` tag as the true baseline), not commit
listing alone — the 6.1.y stable line absorbs later mainline features
that a mainline-6.1-only survey would miss.

Reference material lives under `research/ack-6.1/` (per-file diffs,
anchor dumps; patches are re-derived from the ACK tree text, matching
the anchor-script philosophy of this module).

## Branch landscape

- The **only 6.1 ACK branch is `android14-6.1`** (Android 14/15 GKI).
  `android13-6.1` and `android15-6.1` do not exist (Android 15's GKI is
  `android15-6.6`). Its HEAD is 6.1.176-era LTS + Android patches.
- MGLRU (`CONFIG_LRU_GEN=y`) is **already in the android13-5.15
  baseline** — only the default-on flag differs. Nothing to port.
- zsmalloc chain-size rework and the PSI kernfs-polling rework are
  6.2-origin; the zsmalloc series did **not** reach 6.1.y and is out of
  scope for this line (recorded for a future 6.6-line survey). The PSI
  kernfs-polling rework **was** backported onto android14-6.1 and is
  ported here in ACK-adapted form.

## Landed in this module (Batch 3, android14-6.1 line)

| group (child) | source form | target files |
|---|---|---|
| `memcg_memory_reclaim` (core) | android14-6.1 `memory.reclaim` + reclaim-options plumbing (`MEMCG_RECLAIM_MAY_SWAP/PROACTIVE`) | include/linux/swap.h, mm/vmscan.c, mm/memcontrol.c |
| `psi_irq_tracking` (perf) | mainline 6.1 `52b1364` PSI_IRQ, adapted to the 5.15 iterate_groups walk and embedded psi_group | psi_types.h, psi.c, stats.h, core.c |
| `psi_trigger_kernfs_polling` (perf) | ACK android14-6.1 backport of the kernfs PSI polling rework (psi_trigger_ext + pending_event + 1us windows), psi_group untouched | psi_types.h, psi.h, psi.c, cgroup.c |
| `sched_lazy_preemption_hooks` (perf) | ACK android14-6.1 lazy preemption via vendor hooks (`resched_curr_lazy` / `clear_curr_lazy` / `lock_delay_schedule` / `set_tsk_need_resched_lazy`) | include/trace/hooks/dtask.h, core.c, fair.c |
| `locking_wakeup_patch_hooks` (perf) | ACK android14-6.1 mutex/rwsem post-wakeup fixup hooks | dtask.h, rwsem.h, mutex.c, rwsem.c |

| `psi_oncpu_state_mask` (perf, Batch 22) | ACK android14-6.1 PSI internal sync: TSK_ONCPU becomes a state-mask bit, so `psi_group_cpu::tasks[]` drops to 4 counters and `psi_task_switch()` stops guessing with `identical_state`; still no `psi_group::parent` (the 5.15 walk is reused) | psi_types.h, psi.c |
| `psi_cgroup_pressure_switch` (perf, Batch 21) | ACK android14-6.1 `cgroup.pressure`: per-cgroup PSI accounting switch, re-based on the cgroup's `CGRP_PSI_DISABLED` flag bit instead of `psi_group::enabled` (the embedded psi_group cannot grow) | cgroup-defs.h, psi.h, psi.c, cgroup.c, cgroup-v2.rst |

### KMI decisions (why the full 6.1 PSI shape was NOT ported)

android14-6.1 changed `struct cgroup`'s `psi_group` from embedded to a
pointer plus a `->parent` chain (the per-cgroup toggling series). That
rewrites the layout of a heavily module-visible struct and is a stable
KMI red line for android13-5.15. The two PSI groups therefore port the
feature *semantics* onto the 5.15 shapes:

- `psi_trigger` is heap-allocated, so the `psi_trigger_ext` wrapper,
  `pending_event` flag and kernfs identities touch no KMI-visible
  layout.
- `psi_group_cpu` is percpu-internal, so the added `PSI_IRQ_FULL`
  state grows no exported struct.
- Enum entries (`psi_res`, `psi_states`) are not KMI-tracked; the
  `NR_PSI_STATES`/`NR_PSI_RESOURCES` values become auto-sized exactly
  as in the ACK tree.
- The ACK lazy-preemption hooks are additive vendor tracepoints; no
  struct layout changes. The 2024-11 baseline also needs the
  `set_tsk_need_resched_lazy` hook + `resched_curr()` gate that newer
  5.15 ACK snapshots already carry, so the group probes the tree shape
  and lands the full mechanism on the baseline.

## Suite-preference rule — SUPERSEDED by Batch 15

> This section records the rule as it stood when the survey was written.  It is
> **no longer the policy**: Batch 15 retired it and absorbed the suite's
> optimization inventory into this module (see "Suite absorption" in
> `docs/porting_policy.md` and `docs/survey_suite_absorption.md`, which carries
> the measured evidence for every feature, including the ones that turned out
> not to be portable onto android13-5.15 at all).  The list below is kept as
> provenance for *what* was absorbed.

For each candidate the ABK_ABI_PATCH_SUITE inventory was checked first.
The suite (feature source 7.0.12) does **not** carry any of the five
landed groups. Suite-covered hotpaths that overlap the 6.1 survey space
were excluded from this module by the old policy — the suite's groups were:
`fd_alloc_hotpath`, `close_range_hotpath`, `pid_alloc_hotpath_phase2`,
`slab_alloc_free_hotpath`, `hugepage_fault_alloc_fastpath`,
`io_uring_nowait_*`, `zram_compressed_writeback`, the EEVDF family and
`blk_mq_async_depth`. Composition probes on shared files
(`suite_touched`) guard the injection-order contract documented in
`porting_policy.md`.

## Deferred backlog

Everything the Batch-3 survey deferred is now resolved (Batch 21 closes the last
PSI entry); the remaining rows below record what was landed elsewhere and what
stays out of scope, so a future re-read does not re-open them.

- **per-VMA locks** — present in android14-6.1 (whole 6.4 design
  grafted onto 6.1); highest raw value (app-launch latency) but a real
  5.15 project: RCU VMA lifetime + fault-path conversion + `vm_area_struct`
  KABI slots; the ACK 6.1 series sits on maple-tree storage and is not
  directly graftable — reference the rbtree-era RFC design.
- **per-cgroup PSI toggling** (cgroup.pressure enable/disable) — **landed in
  Batch 21** (`psi_cgroup_pressure_switch`, v0.26.0). It was never actually
  blocked by the psi_group restructure: that restructure only exists to *store*
  the switch and to walk ancestors, and neither is needed here. The switch is
  the cgroup's own `CGRP_PSI_DISABLED` flag bit (no member moves), the 5.15
  `iterate_groups()` already walks the cgroup tree, and the root cgroup's switch
  — its `*.pressure` files are backed by `psi_system` — lives in psi.c. The one
  behaviour that is *not* ported is file hiding (`kernfs_show()`/`KERNFS_HIDDEN`
  arrive with this feature upstream and do not exist on 5.15); a disabled group
  reports `-EOPNOTSUPP` instead of frozen numbers.
- **DAMON sysfs control plane** — moderate size, modest phone value.
- **MADV_COLLAPSE** — 6.1 UAPI-only, value conditional on THP.
- **zram recompression** — 6.2-origin, not in 6.1.y.
- **PSI full sync (NR_ONCPU removal, TSK_ONCPU mask, parent chain)** — **landed in
  Batch 22** (`psi_oncpu_state_mask`, v0.27.0). It turned out not to be a
  "pure refactor with no user-visible effect": the counter could disagree with
  itself across a migration (the `psi: task underflow!` splats) and forced the
  `identical_state` heuristic into the switch. The parent-chain half stayed out
  (5.15 already walks the cgroup tree).  One deliberate omission: the ACK tree
  also asserts `lockdep_assert_rq_held(cpu_rq(cpu))` in `psi_group_change()`,
  which is a separate upstream change and would impose a precondition this repo
  cannot prove for every caller.

## Excluded (no action)

- MGLRU: already in the baseline (config-only difference).
- Maple tree / folio conversions: infrastructure substrate, not grafts.
- Memory tiering/demotion, io_uring 6.1 growth, binder 6.1 deltas
  (oneway spam / frozen state already in 5.15; the next binder perf
  generation is 6.6+): no bounded, phone-relevant 6.1 content.
- `android_vh_restore_curr_resched` / `android_vh_read_lazy_flag`:
  declared on android14-6.1 but without in-tree call sites — not
  ported (additive later if a consumer appears).
