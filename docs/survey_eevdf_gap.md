# EEVDF modern-difference audit — android13-5.15 (GKI) vs Linux 6.6 → 7.3

Scope: the EEVDF implementation that exists in this repo's 5.15 trees, compared against
upstream EEVDF **after** it landed. This is a gap audit, not a port plan: for every
candidate it states what problem the upstream commit solved, whether 5.15 already has an
equivalent, where the 5.15 anchor is, what it depends on, and whether it is worth porting
to a Snapdragon 8 Gen 2 / Android workload.

Baseline inspected: `build/abk-trees/167|178|194|216` (pristine ACK trees) plus the graft
this module installs (`scripts/batch15_perf_eevdf.py`, Batch 15, v0.20.0). Line numbers
below are measured on the **5.15.194** tree (`kernel/sched/fair.c` is 12,225 lines there;
`build/abk-trees/*` is a partial fetch — `include/linux/rbtree_augmented.h` is not in it).

Upstream data was taken from `git log`/`compare` on `torvalds/linux` (first-release
attribution via tag comparison, not author date) and from the commit bodies themselves.

> **Batch 28 landed the S and A tiers of this audit.**  The graded table in §3 is
> the state *before* that batch; §9 records what shipped, what each landed commit
> maps to, and what was left on the table and why.  Read §3 as the audit and §9
> as its outcome.

---

## 1. What "EEVDF on 5.15" actually is

Upstream EEVDF was merged in **v6.6** by the series ending in
`5e963f2bd465 sched/fair: Commit to EEVDF`. **5.15 has never had EEVDF** — `grep -rn
'vlag\|eevdf\|deadline' kernel/sched/fair.c` on the pristine tree returns zero hits.
Everything the tree calls "EEVDF" is installed by this module's Batch 15 graft, which
absorbed `ABK_ABI_PATCH_SUITE feature_porting/sched_eevdf_pick_logic`.

That graft is deliberately **not** upstream EEVDF's data structure. It says so itself:

> The selector keeps the legacy rb-tree ordering and re-derives the EEVDF quantities by
> scanning the tree, because 5.15 has no augmented cfs_rq (no sum_weight /
> sum_w_vruntime / zero_vruntime) and the sibling suite defers that augmentation
> explicitly. — `scripts/batch15_perf_eevdf.py:363-371`

Concretely, the installed shape is:

| upstream EEVDF (v6.6+) | this module's graft |
|---|---|
| rb-tree keyed on `se->deadline` | rb-tree still keyed on `se->vruntime` (`entity_before()` untouched) |
| augmented `se->min_vruntime` heap → prune by eligibility, O(log n) | no augmentation; `abk_pick_eevdf()` iterates every node |
| `cfs_rq::avg_vruntime` / `avg_load` maintained incrementally on enq/deq | `avg_vruntime()` re-derives by walking the whole tree on every call |
| `se->slice` (per-entity request size), `sched_slice()` **deleted** | `sched_slice()`/`sched_vslice()` kept; `abk_eevdf_slice()` wraps them |
| `check_preempt_wakeup()` asks `pick_eevdf()` | `check_preempt_wakeup()` **completely untouched** — still CFS `wakeup_preempt_entity()` + `wakeup_gran()` |
| `yield_task_fair()` bumps `se->deadline` | `yield_task_fair()` untouched — still `set_skip_buddy()` |
| cross-RQ move = `place_entity()` (lag) | cross-RQ move = `vruntime ± cfs_rq->min_vruntime` handshake, *then* the graft overwrites `vruntime` from `avg_vruntime()` |
| `SCHED_FEAT(PLACE_LAG / PLACE_DEADLINE_INITIAL / RUN_TO_PARITY / EEVDF)` | **zero** `sched_feat` — `kernel/sched/features.h` is not touched by this group at all |
| `se->rel_deadline` (v7.0) | reimplemented crudely as bit 63 of `se->min_vruntime` (`ABK_EEVDF_REL_DEADLINE_BIT`) |

### 1.1 The consequence that matters most

`abk_pick_eevdf()` (fair.c, installed in `pick_next_entity()` at 5.15.194 fair.c:4708)
loops over every rb node and calls `abk_eevdf_refresh_deadline()` **per node**:

- `abk_eevdf_refresh_deadline()` → `abk_eevdf_vslice()` → `sched_slice()` (walks the cgroup
  hierarchy), then `avg_vruntime(cfs_rq)` — a **full tree scan** — and
  `abk_eevdf_update_lag()` → another `avg_vruntime()` scan plus
  `abk_eevdf_max_slice()` — a **third full scan, whose per-node work again calls
  `sched_slice()`**.

So one `pick_next_entity()` costs roughly O(n²)–O(n³) in the runqueue depth, on **every
context switch and every tick** (`check_preempt_tick()` calls the same helper). Worse,
selection **mutates** `se->deadline`, `se->vlag` and `se->vruntime` as a side effect —
`check_preempt_tick()` and `entity_tick()` therefore perturb scheduler state merely by
asking who should run.

This is the single largest EEVDF-related cost in the tree, and it is exactly what the
v6.8 upstream pair (`2227a957e1d5` + `ee4373dc902c`) was written to remove.

---

## 2. Upstream EEVDF timeline (first release that contains the commit)

| release | commit | subject |
|---|---|---|
| v6.6 | `147f3efaa241` | sched/fair: Implement an EEVDF-like scheduling policy |
| v6.6 | `af4cf40470c2` | sched/fair: Add cfs_rq::avg_vruntime |
| v6.6 | `86bfbb7ce4f6` | sched/fair: Add lag based placement |
| v6.6 | `e0c2ff903c32` | sched/fair: Remove sched_feat(START_DEBIT) |
| v6.6 | `5e963f2bd465` | sched/fair: Commit to EEVDF |
| v6.6 | `e8f331bcc270` | sched/smp: Use lag to simplify cross-runqueue placement |
| v6.6 | `63304558ba5d` | sched/eevdf: Curb wakeup-preemption (introduces `RUN_TO_PARITY`) |
| v6.6 | `2f2fc17bab00` | sched/eevdf: Also update slice on placement |
| v6.6 | `650cad561cce` | sched/eevdf: Fix avg_vruntime() |
| v6.6 | `b01db23d5923` | sched/eevdf: Fix pick_eevdf() |
| v6.6 | `8dafa9d0eb1a` | sched/eevdf: Fix min_deadline heap integrity |
| v6.7 | `eab03c23c2a1` | sched/eevdf: Fix vruntime adjustment on reweight |
| v6.8 | `2227a957e1d5` | sched/eevdf: Sort the rbtree by virtual deadline |
| v6.8 | `ee4373dc902c` | sched/eevdf: O(1) fastpath for task selection |
| v6.9 | `11b1b8bc2b98` / `afae8002b4fd` / `1560d1f6eb6b` | reweight_entity() / reweight_eevdf() corrections |
| v6.12 | `152e11f6df29` + 20 more | delayed dequeue family |
| v6.12 | `85e511df3cec` | sched/eevdf: Allow shorter slices to wakeup-preempt (`PREEMPT_SHORT`) |
| v6.12 | `d4ac164bde7a` | Fix wakeup-preempt by checking cfs_rq->nr_running |
| v6.12 | `aef6987d8954` | Propagate min_slice up the cgroup hierarchy |
| v6.12 | `949090eaf0a3` / `2c2d9624697f` / `c40dd90ac045` | min_vruntime_copy removal, exec_clock removal, new-task vruntime |
| v6.13 | `4423af84b297` | optimize the PLACE_LAG when se->vlag is zero |
| v6.13 | `6d71a9c61604` | Fix EEVDF entity placement bug causing scheduling lag — **reverted in v7.0 by `101f3498b4bd`** |
| v6.15 | `f553741ac8c0` / `563bc2161b94` / `bbce3de72be5` | slice-protection cancel, min_slice propagation, `se->slice == U64_MAX` |
| v6.17 | `79104becf42b` | sched/fair: Forfeit vruntime on yield |
| v6.17 | `9de74a9850b9` / `3a0baa8e6c57` / `74eec63661d4` / `052c3d87c82e` | run-to-parity / stretch-preemption tail |
| v6.19 | `79f3f9bedd14` | Fix min_vruntime vs avg_vruntime |
| v6.19 | `85570f10a4c6` | sched/eevdf: Move to a single runqueue |
| v7.0 | `80390ead2080` | Separate se->vlag from se->vprot |

---

## 3. Graded candidates

Grade key: **S** strong yes · **A** worth porting · **B** study · **C** do not ·
**[-]** already present in 5.15 / covered by this module's graft.

### S — strong

#### S1. `63304558ba5d` — sched/eevdf: Curb wakeup-preemption (v6.6)

- **Problem solved.** EEVDF's lag placement puts a wakee left of `curr` *and* with an
  earlier deadline, so it immediately preempts. Upstream measured **over-scheduling**:
  `perf bench sched messaging -g 20` goes from 2,556,535 to 3,723,554 context switches
  (‑31 %) when `RUN_TO_PARITY` is off, i.e. the patch buys back ~1.17 M switches per run.
- **Equivalent in 5.15?** **No.** The graft has no run-to-parity concept and no
  `sched_feat`.
- **Missing location.** `abk_pick_eevdf()` inside `kernel/sched/fair.c` (installed right
  after the graft's helper block, i.e. between 5.15's `sched_vslice()` fair.c:718 and
  `#include "pelt.h"`); `set_next_entity()` fair.c:4663; `kernel/sched/features.h`
  (Android `ALT_PERIOD`/`BASE_SLICE` at features.h:96-97 — a new `SCHED_FEAT` goes after
  them).
- **Core logic.** `pick_eevdf()` gains, before the search loop:
  `if (sched_feat(RUN_TO_PARITY) && curr && curr->vlag == curr->deadline) return curr;`
  and `set_next_entity()` stashes the pick-time deadline:
  `/* HACK, stash a copy of deadline at the point of pick in vlag */ se->vlag = se->deadline;`
- **Dependencies.** None beyond one `SCHED_FEAT` line.
- **Independent backport?** Yes.
- **Difficulty.** Low — but **one adaptation is mandatory**: upstream can borrow
  `se->vlag` because upstream only writes `vlag` at dequeue. This module's
  `abk_eevdf_update_lag()` writes `se->vlag` all over the pick/tick path, so the stash
  would be clobbered. Use a new **`struct cfs_rq`** field (e.g. `u64 pick_deadline;`) as
  the stash instead — `struct cfs_rq` lives in `kernel/sched/sched.h` and is not part of
  the exported KMI, so this is free.
- **8 Gen 2 / Android benefit.** Direct: fewer context switches per unit work, which is
  both throughput and power on the big cluster; also reduces the jitter that short-slice
  UI threads see. This is the commit whose *absence* is most visible in the current tree.
- **Worth porting?** **Yes.**

#### S2. `147f3efaa241` — the `check_preempt_wakeup()` hunk only (v6.6)

- **Problem solved.** After EEVDF, placement and wakeup preemption must agree. Upstream
  replaced the CFS `wakeup_gran()` comparison with "ask the selector":

  ```c
  cfs_rq = cfs_rq_of(se);
  update_curr(cfs_rq);

  if (sched_feat(EEVDF)) {
          /* XXX pick_eevdf(cfs_rq) != se ? */
          if (pick_eevdf(cfs_rq) == pse)
                  goto preempt;
          return;
  }
  ```

- **Equivalent in 5.15?** **No** — and this is the sharpest gap. The graft replaces
  `pick_next_entity()` wholesale but never touches `check_preempt_wakeup()`
  (5.15.194 fair.c:7440-7547), so wakeup preemption on this tree is still governed by
  `sysctl_sched_wakeup_granularity`, i.e. by a *different policy* than placement. A wakee
  the selector would have chosen can still fail to preempt.
- **Missing location.** `check_preempt_wakeup()` fair.c:7511-7527 — the block between
  `update_curr(cfs_rq_of(se));` and the `if (wakeup_preempt_entity(se, pse) == 1)` ladder.
- **Core logic.** Insert the EEVDF branch above; the AOSP vendor hooks
  (`trace_android_rvh_check_preempt_wakeup_ignore` at 7453, `…_check_preempt_wakeup` at
  7512) stay in place, the branch goes after 7517.
- **Dependencies.** `abk_pick_eevdf()` (already installed). Follow-up fix **`d4ac164bde7a`
  (v6.12)** — "Fix wakeup-preempt by checking `cfs_rq->nr_running`" — is worth taking with
  it: the shorter-slice path used `rq->nr_running`, which fires a resched when a 1-task
  cfs_rq shares the CPU with an RT task (+2.2 % involuntary switches on hackbench).
  `d4ac164bde7a` is a one-line change (`rq->nr_running == 1` → `cfs_rq->nr_running == 1`)
  in `update_curr()`.
- **Independent backport?** Yes.
- **Difficulty.** Low (≈15 lines), but it **changes wakeup latency semantics** and
  interacts with `sched_lazy_preemption_hooks` (this module's lazy-preemption group wraps
  `resched_curr_lazy()`; the graft's new preemption points already bypass it — see
  `batch15_perf_eevdf.py:203-212`). Review the two together.
- **8 Gen 2 / Android benefit.** Wakeup latency for binder/input/UI threads is the single
  most user-visible scheduler property on a phone. Making the wakeup decision use the same
  eligibility+deadline rule as selection removes a whole class of "the task that should
  have run didn't".
- **Worth porting?** **Yes.**

#### S3. `af4cf40470c2` (v6.6) + `2227a957e1d5` + `ee4373dc902c` (v6.8) — kill the O(n²) selector

- **Problem solved.** Three separate upstream problems, one shared fix:
  - `af4cf40470c2` caches the weighted-average virtual time in `cfs_rq::avg_vruntime` /
    `avg_load`, maintained incrementally by `avg_vruntime_add()`/`avg_vruntime_sub()` in
    `__enqueue_entity()`/`__dequeue_entity()`, instead of recomputing.
  - `2227a957e1d5` **re-keys the rb-tree on `se->deadline`** (`entity_before()` becomes
    `(s64)(a->deadline - b->deadline) < 0`) and augments the tree with
    `se->min_vruntime = min(se->vruntime, left, right)` so eligibility can prune subtrees.
  - `ee4373dc902c` then adds the O(1) front door: "if the leftmost entity is eligible,
    take it" — Abel Wu's measurements show the fastpath serving 20–26 % of picks in
    netperf/tbench/schbench versus 0.1–1.7 % for the heap search.
- **Equivalent in 5.15?** **Functionally approximated, structurally absent.** The graft
  has an `avg_vruntime()` **function** doing a full scan; it has no `cfs_rq` accumulator,
  no augmentation, no deadline ordering and no fastpath.
- **Missing location.** `struct cfs_rq` (`kernel/sched/sched.h:539-634` on 5.15.194) needs
  `s64 avg_vruntime; u64 avg_load;`. `struct sched_entity`
  (`include/linux/sched.h:531-572`) needs the augmented key. `entity_before()`
  (fair.c:535), `__enqueue_entity()`/`__dequeue_entity()` (fair.c:583/589),
  `update_min_vruntime()` (fair.c:544) and `abk_pick_eevdf()` all change.
- **Core logic.** `rb_add_augmented_cached()` + `RB_DECLARE_CALLBACKS` heap; tree sorted on
  deadline; `min_vruntime` kept as the augmented aggregate; leftmost-eligible fastpath.
- **Dependencies and the one real infrastructure question.**
  `rb_add_augmented_cached()` **does not exist before v6.6** — it was added by this very
  series (verified: 0 hits in v5.15/v5.19/v6.1/v6.3/v6.5, 1 hit in v6.6). It is a ~30-line
  self-contained generic helper in `include/linux/rbtree_augmented.h`;
  `RB_DECLARE_CALLBACKS` and `rb_erase_augmented_cached` already exist in 5.15. **So this
  is a bounded addition, not a 6.x-infrastructure dependency** — but note the audit harness
  does not fetch that header (`tests/fetch_sublevel_tree.sh` has no rbtree entry), so
  `step_audit.py`/`implementation_audit.py` would need it added to `FETCH_FILES`.
  Also take **`b01db23d5923` (v6.6) "Fix pick_eevdf()"** — without it the heap search can
  miss the earliest *eligible* deadline by descending right; it is a prerequisite, not an
  optional extra.
- **Independent backport?** Yes, self-contained within `kernel/sched/`.
- **Difficulty.** **High.** This is the one item that is really "finish the EEVDF data
  structure", and it touches the same functions the rest of the module already rewrites
  (`reweight_entity`, `enqueue_entity`, `dequeue_entity`, `set_next_entity`,
  `put_prev_entity`, `check_preempt_tick`, `entity_tick`) — every existing EEVDF step
  anchor moves.
- **8 Gen 2 / Android benefit.** Largest of any item here, and it is a pure hot-path
  reduction on *every* context switch and tick. It also removes the selection-time
  mutation of `deadline`/`vlag`/`vruntime`, which is a correctness smell in the current
  graft independent of speed.
- **Worth porting?** **Yes — but only as a deliberate, self-contained batch, and only if
  the KMI budget below is accepted.** If that is out of scope, the cheaper fallback is in
  §7.

### A — worth porting

#### A1. `e8f331bcc270` — sched/smp: Use lag to simplify cross-runqueue placement (v6.6)

- **Problem solved.** 5.15 migrates an entity by subtracting `min_vruntime` on the source
  and adding it back on the destination, with a `min_vruntime_copy` retry loop on
  `!CONFIG_64BIT` and, on ACK, a `CONFIG_SCHED_CORE` `min_vruntime_fi` variant
  (`kernel/sched/sched.h:547-554`). Upstream: *"Using lag is both more correct and simpler
  when moving between runqueues. Notabe, min_vruntime() was invented as a cheap
  approximation of avg_vruntime() for this very purpose (SMP migration). Since we now have
  the real thing; use it."*
- **Equivalent in 5.15?** **No.** The graft adds lag placement *on top of* the CFS
  renormalisation rather than replacing it, so `enqueue_entity()` (fair.c:4460-4479) still
  does `se->vruntime += cfs_rq->min_vruntime` and then the graft overwrites `vruntime`
  from a scan-based `avg_vruntime()`.
- **Missing location.** `enqueue_entity()` fair.c:4460-4479; `dequeue_entity()`
  fair.c:4594-4616; `migrate_task_rq_fair()`; `task_fork_fair()` fair.c:11508;
  `detach_task_cfs_rq()`/`attach_task_cfs_rq()` fair.c:11626-11652.
- **Dependencies.** Wants `PLACE_LAG` semantics (the graft has an equivalent) and that
  `place_entity()` be called for `curr` too. Follow-on `949090eaf0a3` (v6.12) removes
  `min_vruntime_copy` entirely.
- **Independent backport?** Yes, but it is a behaviour change on the migration path.
- **Difficulty.** Medium.
- **8 Gen 2 / Android benefit.** Cross-CPU placement after migration is constant on a
  phone (binder wakeups between little/mid/big clusters). Removing the double
  renormalisation removes a source of unfairness/jitter; it is a correctness fix with a
  modest perf tail.
- **Worth porting?** Yes, after S3 (otherwise the two placements keep fighting).

#### A2. EEVDF yield: `147f3efaa241`'s `yield_task_fair()` hunk (v6.6) + `79104becf42b` (v6.17)

- **Problem solved.** Upstream `yield_task_fair()` on an EEVDF tree does
  `se->deadline += calc_delta_fair(se->slice, se);`. `79104becf42b` then fixes the runaway
  it causes: *"If a task yields, the scheduler may decide to pick it again … the deadline
  will be increased by the slice at each loop… Fix this by making the task forfeiting its
  remaining vruntime and pushing the deadline one slice ahead."*
- **Equivalent in 5.15?** **None.** `yield_task_fair()` (5.15.194 fair.c:7755-7784) is
  byte-identical to CFS — it only calls `set_skip_buddy(se)`. The graft's selector honours
  `skip` only as `if (cfs_rq->skip == se) continue;` with no second-best fallback.
- **Missing location.** `yield_task_fair()` fair.c:7755-7784.
- **Core logic.** `if (entity_eligible(cfs_rq, se)) { se->vruntime = se->deadline;
  se->deadline += calc_delta_fair(se->slice, se); update_min_vruntime(cfs_rq); }`
- **Dependencies.** `entity_eligible()` (the graft has `abk_eevdf_eligible()`);
  `update_min_vruntime()` is a different function on 5.15 than upstream post-6.6, so this
  hunk needs re-anchoring. `se->slice` does not exist — substitute the graft's
  `abk_eevdf_vslice()`.
- **Independent backport?** Yes.
- **Difficulty.** Low–medium.
- **8 Gen 2 / Android benefit.** `sched_yield()` is reached from ART, binder and futex/PI
  paths. Without the deadline bump, yield is a no-op that keeps re-selecting the yielder;
  with a careless bump you get the runaway `79104becf42b` fixes. Take both hunks together.
- **Worth porting?** Yes.

#### A3. `85e511df3cec` — Allow shorter slices to wakeup-preempt (`PREEMPT_SHORT`, v6.12)

- **Problem solved.** Short-slice (latency-oriented) tasks should be able to preempt
  longer-slice ones on wakeup. Upstream shows cyclictest max-delay becoming consistent and
  sum-delay dropping ~18 %, at the cost of more switches for the long-slice task.
- **Equivalent in 5.15?** **No.**
- **Dependencies.** Needs (a) S2 — wakeup preemption must go through the selector first,
  and (b) a per-entity slice notion (`se->slice` upstream; on 5.15 `sched_slice()` gives a
  load-proportional slice, which is *not* the same thing). Also take `d4ac164bde7a`
  (v6.12) and `9de74a9850b9` (v6.17, "Remove spurious shorter slice preemption") — the
  feature shipped with two follow-up corrections.
- **Independent backport?** Only after S2.
- **Difficulty.** Medium.
- **8 Gen 2 / Android benefit.** Real for latency-sensitive workloads; also the most
  "Android-shaped" of the upstream EEVDF features (small, latency-critical threads
  preempting bulk work). But it is a policy change that has needed three correction
  commits upstream — not a drive-by port.
- **Worth porting?** Yes, sequenced after S2, and only with the two follow-up fixes.

#### A4. `c40dd90ac045` — Initialize the vruntime of a new task when it is first enqueued (v6.12)

- **Problem solved.** `sched_cgroup_fork()` initialises `vruntime` on the *creating* CPU,
  but the task actually runs on the CPU chosen at `wake_up_new_task()`. Upstream passes
  `ENQUEUE_INITIAL` so `place_entity()` initialises it on the target rq.
- **Equivalent in 5.15?** **Partially and inconsistently.** 5.15 does
  `task_fork_fair()` → `place_entity(cfs_rq, se, 1)` (fair.c:11497) with `START_DEBIT`, and
  the graft's `abk_eevdf_place_entity()` takes an `initial` path — but only halves the
  vslice; it **does not** set `se->vruntime` from `avg_vruntime()` on the initial path
  (`abk_eevdf_apply_lag_placement()` is gated on `!initial`). So a forked entity inherits
  the parent's `curr->vruntime`.
- **Missing location.** `enqueue_entity()` fair.c:4494-4495; `task_fork_fair()`
  fair.c:11481-11510; `abk_eevdf_place_entity()`.
- **Independent backport?** The upstream commit itself is v6.12 and rides on the
  `ENQUEUE_INITIAL` flag; the *effect* can be reproduced inside the graft's initial path
  with a two-line change (set `vruntime = avg_vruntime(cfs_rq)` before the existing
  `initial` vslice halving).
- **Difficulty.** Low.
- **Benefit.** New-task placement: fork-heavy workloads (app launch, `zygote`) start
  threads at the wrong virtual time today.
- **Worth porting?** Yes, in the simplified form.

### B — study

Verified in §4.1 below: **every anchor in this table is absent from both the pristine and
the grafted tree (grep count 0).** What differs between rows is whether the *defect* the
upstream commit fixes is also absent — and one row hides a portable one-line fix that is
worth more than the commit it came from.

| commit | release | anchor | defect present in the graft? | verdict |
|---|---|---|---|---|
| `eab03c23c2a1` Fix vruntime adjustment on reweight | v6.7 | absent | **no** — the graft converts reweight into "dequeue → re-place", which upstream's own message considered and rejected on **cost** grounds, not correctness. **But the same commit also adds the `update_cfs_group()` guard, which the graft badly needs — see §4.2.** | split: the guard is **A**, the rest is C |
| `11b1b8bc2b98` / `afae8002b4fd` reweight corrections | v6.9 | absent | no — they repair `reweight_eevdf()`'s use of a V that the requeue had already invalidated; the graft never holds V across a reweight | C |
| `1560d1f6eb6b` Prevent vlag out of bounds in reweight_eevdf | v6.9 | absent | **no** — the graft clamps `se->vlag` in `abk_eevdf_update_lag()` *before* the reweight's `×old_weight` multiply, which is the same shape as the fix | C |
| `b01db23d5923` Fix pick_eevdf() | v6.6 | absent | n/a — and **superseded**: the v6.8 re-sort (`2227a957e1d5`) replaced the `min_deadline` heap search that this commit patches with a single-pass `min_vruntime`-guided search that already gives the correct answer | C — absorbed by S3 |
| `2f2fc17bab00` Also update slice on placement | v6.6 | absent | **no** — it fixes a stale per-entity `se->slice`; the graft recomputes `abk_eevdf_vslice()` on every placement, so a stale slice cannot exist | C |
| `c70fc32f4443` Adhere to place_entity() constraints | v6.16 | absent | **no** — it repairs fallout from `6d71a9c61604`, which upstream reverted in `101f3498b4bd`; it guards the empty-tree case via `cfs_rq->nr_queued`, and the graft's placement is already gated on `se->vlag \|\| rel_deadline` | C |
| `aef6987d8954` / `563bc2161b94` min_slice propagation | v6.12 / v6.15 | absent | feature absent, not a fix. Needs `se->min_slice` + `se->custom_slice` + the augmented `min_vruntime_cb_propagate` + 6.12's `dequeue_entities()` shape | C — KMI + dependency blocked |
| slice protection family (`f553741ac8c0` v6.15 and the 6.17/7.x tail) | v6.15+ | absent | feature absent. Needs `se->vprot` | C — no free KMI slot |
| `556146ce5e94` Avoid overflow in enqueue_entity | v7.x | absent | **no (by magnitude)** — see §4.3 | C |
| `b6eee96843e8` Fix overflow in vruntime_eligible | v7.x | absent | **no** — `vruntime_eligible()` does not exist; the graft's `abk_eevdf_eligible()` is a subtraction and a sign test with no multiplication to overflow | C |

### C — do not

| commit(s) | release | reason |
|---|---|---|
| `152e11f6df29` + `781773e3b680` + `54a58a787791` + `2e0199df252a` + `f12e148892ed` + `abc158c82ae5` + `fab4a808ba9f` and ~14 follow-ups (delayed dequeue) | v6.12 | needs `se->sched_delayed`, `cfs_rq->nr_delayed`, `h_nr_runnable` and a `dequeue_task()` core rework; it is a subsystem, has a long fix tail, and is KMI-hostile. Its PELT/util_est companions (`fc1892becd56`, `76f2f783294d`, `729288bc6856`, `3429dd57f0de`, `66951e4860d3`, `98442f0ccd82`) inherit the same dependency. |
| `6d71a9c61604` + `101f3498b4bd` | v6.13 / v7.0 | net zero — the placement fix was **reverted**; `c70fc32f4443` exists only because of the mess |
| `79f3f9bedd14` Fix min_vruntime vs avg_vruntime | v6.19 | the whole `min_vruntime` model it repairs is one this tree never adopted |
| `2c2d9624697f` remove `nr_spread_over`/`exec_clock` | v6.12 | removes `check_spread()`, which is a no-op outside `CONFIG_SCHED_DEBUG` |
| `e31488c9df27` remove `DOUBLE_TICK` | v6.13 | 5.15 ships `SCHED_FEAT(DOUBLE_TICK, false)`, so the branch is already inert |
| `85570f10a4c6` Move to a single runqueue; `4ff674fa986c`/`dcbc9d3f0e59` renames; `80390ead2080` split vlag/vprot | v6.19+ | structural rewrites of a data structure this tree does not have |

### [-] — already present, or covered by this module's graft

| commit | release | status on 5.15 |
|---|---|---|
| `86bfbb7ce4f6` Add lag based placement | v6.6 | covered — `abk_eevdf_preserved_lag()` / `abk_eevdf_apply_lag_placement()` |
| `650cad561cce` Fix avg_vruntime() | v6.6 | **covered** — the graft's `avg_vruntime()` carries the identical negative-average correction (`if (weighted < 0) weighted -= total - 1;`), which upstream solves as `if (left < 0) left -= (load - 1);` |
| `4423af84b297` PLACE_LAG fastpath when `vlag == 0` | v6.13 | covered — `abk_eevdf_place_entity()` is gated on `se->vlag \|\| rel_deadline` |
| `e0c2ff903c32` Remove `sched_feat(START_DEBIT)` | v6.6 | covered in effect — the graft overwrites `vruntime` after 5.15's START_DEBIT placement |
| `e4ec3318a17f` rename min_granularity → base_slice | v6.6 | N/A — 5.15 GKI keeps `sysctl_sched_min_granularity` plus the Android `ALT_PERIOD`/`BASE_SLICE` feats |
| `8dafa9d0eb1a`, `d2929762cc3f`, `f25b7b32b0db` heap-integrity fixes | v6.6 | N/A — there is no heap |
| `4c456c9ad334`, `fbb66ce0b1d6` | v6.7 / v6.8 | cosmetic (unused arguments/locals) |

---

## 4. B-tier feasibility — verified

**Method.** `build/abk-trees/194` (pristine ACK 5.15.194) was copied to `build/eevdf194`
and the current module (v0.30.1) applied to it in `setup.sh`'s order —
`abk_stable_core.py`, `abk_stable_perf.py`, `abk_stable_display.py`. All groups reported
`applied` / `already_present`. Every claim below is a grep against that tree, a grep against
the pristine tree, or a read of the emitted C — not an inference from the module's docs.
**All `fair.c:NNNN` line numbers in this section are the grafted tree** (`build/eevdf194`,
12,581 lines); §1 and §3 use the pristine tree (12,225 lines).

### 4.1 Anchor test (`kernel/sched/fair.c`, pristine vs grafted)

| upstream anchor used by the B candidates | pristine | grafted |
|---|---|---|
| `reweight_eevdf`, `min_deadline`, `deadline_gt`, `__pick_eevdf` | 0 | 0 |
| `entity_lag(`, `update_entity_lag`, `vruntime_eligible`, `entity_eligible` | 0 | 0 |
| `se->slice`, `sysctl_sched_base_slice`, `min_slice`, `custom_slice` | 0 | 0 |
| `se->rel_deadline`, `nr_queued`, `min_vruntime_cb_propagate` | 0 | 0 |
| `avg_vruntime_add`, `avg_vruntime_sub`, `__pick_root_entity` | 0 | 0 |
| `rb_add_augmented_cached`, `sched_feat(RUN_TO_PARITY)`, `se->vlag = se->deadline` | 0 | 0 |
| *(graft's own symbols, for contrast)* `abk_pick_eevdf`, `abk_eevdf_eligible` | 0 | 5 / 4 |

`sum_weight`, `sum_w_vruntime`, `zero_vruntime` each report 1 in the grafted tree; the hit
is the module's own comment at `kernel/sched/fair.c:733` — *"5.15 has no augmented cfs_rq
(no sum_weight / sum_w_vruntime / zero_vruntime)"*. There is no field, only the sentence.

**Conclusion: not one B-tier upstream patch has a landing site in this tree.** They cannot
be ported in any form; the question for each one is only whether the *defect* it fixes is
reproducible on the graft, and §3's B table answers that per row.

### 4.2 The one portable item hiding in the reweight family — `update_cfs_group()` guard

`eab03c23c2a1` carries two independent changes. The `reweight_eevdf()` rewrite has no anchor
here, but the second hunk does:

```c
 #else
-	shares   = calc_group_shares(gcfs_rq);
+	shares = calc_group_shares(gcfs_rq);
 #endif
-
-	reweight_entity(cfs_rq_of(se), se, shares);
+	if (unlikely(se->load.weight != shares))
+		reweight_entity(cfs_rq_of(se), se, shares);
```

On 5.15 the `#ifndef CONFIG_SMP` arm **already has** that early return
(`fair.c:3591-3592`); the `#else` arm does not, and GKI builds SMP. `CONFIG_CGROUP_SCHED=y`
is in `gki_defconfig`, so `CONFIG_FAIR_GROUP_SCHED` is on and this path is live.

Why it matters after the graft: pre-EEVDF `reweight_entity()` was cheap, which is why 5.15
calls it unconditionally. Post-graft it is the most expensive function in the file. For a
queued, non-current entity the emitted body (`fair.c:3399-3448`) is:

1. `abk_eevdf_update_lag()` → `avg_vruntime()` (full tree scan) + `abk_eevdf_lag_limit()` →
   `abk_eevdf_max_slice()` (full scan, each node calling `sched_slice()`);
2. `__dequeue_entity()` (rb erase);
3. `place_entity()` → `abk_eevdf_place_entity()` → `abk_eevdf_vslice()` +
   `abk_eevdf_preserved_lag()` (`abk_eevdf_total_weight()` full scan **plus** a second
   `max_slice()` scan) + `abk_eevdf_apply_lag_placement()` (`avg_vruntime()` again);
4. `__enqueue_entity()` (rb insert).

That is **five full-tree scans — two of them `max_slice()`, whose per-node work is a
`sched_slice()` hierarchy walk — plus an rb erase/insert, to discover that `shares` did not
change.**

It is on the tick path: `task_tick_fair()` (`fair.c:11813`) walks
`for_each_sched_entity(se)` and calls `entity_tick()` for the task **and every group entity
above it**; `entity_tick()` calls `update_cfs_group(curr)` (`fair.c:5141`), which reaches
`reweight_entity()` unconditionally. Every Android task lives in a cgroup, and
`group_cfs_rq(se)` is non-NULL precisely for the group entities, so this fires at every
cgroup level on every tick. `enqueue_task_fair()` and `dequeue_entity()` walk the same
`for_each_sched_entity` ladder and hit the group levels on every enqueue/dequeue, and
`sched_group_set_shares()` hits every level of every CPU on a cgroup weight change — though
on those paths `calc_group_shares()` is more likely to return a changed value, so the guard
helps least exactly where the call is least frequent.

Portability: one line, no new fields, no new helpers, upstream-validated in this exact
shape. The only semantic cost is that the guard also skips `dequeue_load_avg()` /
`enqueue_load_avg()`; upstream accepted that, and on 5.15 `entity_tick()` calls
`update_load_avg(cfs_rq, curr, UPDATE_TG)` immediately **before** `update_cfs_group(curr)`,
so PELT is synced anyway on the tick path.

Not measured: how often `calc_group_shares()` returns an unchanged value. The argument that
it is common rests on the tick path having no intervening load change — reasoned, not
instrumented. This should be probed before it is claimed as a win.

**Re-graded: A.** It is a hot-path reduction in a path the graft made expensive, and it is
cheaper and lower-risk than anything in S.

### 4.3 The overflow family

- `b6eee96843e8` fixes `vruntime_eligible()`'s `key * load` product. `vruntime_eligible()`
  does not exist here (grep 0), and the graft's `abk_eevdf_eligible()` is
  `return (s64)(avruntime - se->vruntime) >= 0;` — a subtraction and a sign test, with no
  multiplication. **Structurally impossible. Not applicable.**
- `556146ce5e94` fixes `place_entity()`'s `lag *= load + weight`. The graft has the same
  *shape* at `fair.c:923` (`abk_eevdf_preserved_lag()`:
  `lag = div_s64(lag * (load + weight), load);`), so this is the one row worth checking by
  magnitude rather than by anchor. Bounds on 5.15:
  - `lag` is clamped by `abk_eevdf_lag_limit()` to `calc_delta_fair(max_slice + min_gran, se)`;
  - `load` is a sum of **unscaled** weights (`abk_eevdf_total_weight()` applies
    `scale_load_down`), bounded by `nr × 88761`;
  - for equal weights `sched_slice()` returns ≈ `sysctl_sched_min_granularity` regardless of
    `nr`, so `max_slice` only grows with *weight imbalance*.
  - `calc_delta_fair(delta, se)` on 5.15 is `delta / w_u` (`delta * NICE_0_LOAD /
    (w_u << 10)`), so the product reduces to `≈ nr × min_granularity × w_max / w_u` — it is
    driven by the **weight ratio and the runqueue depth**, not by the raw slice.
  - Worked worst cases, with `sysctl_sched_min_granularity` = 3 ms and `NICE_0_LOAD` = 1024:

    | runqueue | `lag_limit` | `load` | product | headroom vs 9.22e18 |
    |---|---|---|---|---|
    | 1× nice-20 + 4095× nice-19 (nr 4096) | 4.8e8 | 1.5e5 | **7.3e13** | 1.3e5× |
    | 1× nice-20 + 99× nice-19 (nr 100) | 2.0e7 | 9.0e4 | 1.8e12 | 5.1e6× |
    | 4096× nice-0, balanced | 5.9e3 | 4.2e6 | 2.5e10 | 3.8e8× |

  The first row is the true worst plausible case and it is a deliberately absurd runqueue
  (4096 runnable entities in one cfs_rq, on a phone). Even there the product is
  **7.3e13 against an s64 limit of 9.22e18 — about 10⁵× of headroom.** A realistic Android
  cfs_rq depth of ~100 gives ~5×10⁶× of headroom.
  `se->vlag × old_weight` in `reweight_entity()` (`fair.c:3427`) is bounded the same way.
  The commit's own trigger is a 256-CPU machine running the `zero_vruntime` accumulator
  model, which is not what runs here.
  **Not applicable in any realistic Android configuration** — but this is a magnitude
  argument computed from the emitted code, not a measurement, and it rests on
  `abk_eevdf_lag_limit()` staying O(nr)-ish. If S3 ever lands, `sum_w_vruntime` replaces
  this arithmetic and the question must be re-opened. **Note:** `abk_eevdf_scale_rel_deadline()`
  (`fair.c:822`, product at `fair.c:833`) uses `div_u64`, i.e. **unsigned** 64-bit
  truncation, on `deadline × old_weight`. That is the one place where an overflow would be
  silent rather than beneficial, and its operands are bounded by the same `vslice`-sized
  figure — worth a second look if anyone touches the multiplier.

### 4.4 One incidental finding worth recording

`fair.c:955` in `abk_eevdf_refresh_deadline()`:

```c
	if (se->deadline && (s64)(se->vruntime - se->deadline) < 0)
		return false;
```

`se->deadline` is an absolute vruntime-space value, and the graft initialises it from
`se->vruntime + vslice`. `se->deadline` is only 0 for an entity that has never been placed,
so the `&&` guard is dead in practice — but if it ever *were* 0 the entity would be treated
as having an expired slice. Not a bug worth a fix on its own; recorded because §4.1 shows
how little of the upstream shape is present, and this kind of "guard against a state that
cannot occur" is the same class of thing.

## 5. The KMI budget is the binding constraint

`struct sched_entity` on 5.15 GKI (`include/linux/sched.h:531-572`) has exactly four
`ANDROID_KABI_RESERVE` slots. This module already owns three
(`ANDROID_KABI_USE(1, u64 deadline)`, `(2, u64 min_vruntime)`, `(3, s64 vlag)`) and
released slot 4 back to `ANDROID_KABI_RESERVE(4)` in Batch 16. Upstream's field set is:

| | v6.6 | v7.2 |
|---|---|---|
| `sched_entity` EEVDF fields | `deadline`, `min_vruntime`, `vlag`, `slice` (4) | `deadline`, `min_vruntime`, `min_slice`, `max_slice`, `vlag`, `vprot`, `slice` + `rel_deadline`/`custom_slice`/`sched_delayed` (7 + 3 bits) |
| `cfs_rq` EEVDF fields | `avg_vruntime`, `avg_load` | `sum_w_vruntime`, `sum_weight`, `zero_vruntime`, `sum_shift` |

Consequences:

- **The v6.6 core (including S1 and S3) fits exactly** — 4 fields, all four slots.
- **Everything from v6.12 onward does not fit.** `min_slice`, `max_slice`, `vprot` and
  `sched_delayed` are 4 more fields against 0 free slots. That is why `PREEMPT_SHORT`'s
  full upstream form, slice protection and delayed dequeue are graded B/C above — not
  because they are uninteresting, but because the KMI would have to grow.
- `struct cfs_rq` is in `kernel/sched/sched.h`, is not an exported struct, and is not in
  any KMI-checked symbol — adding fields there is free and is the right place for the
  S1 stash and the S3 accumulators.
- Existing naming collision to be careful about: slot 2 is already spelled `min_vruntime`,
  but the graft uses it as a **relative-deadline tag holder** (bit 63 + 63-bit payload),
  not as upstream's augmented aggregate. Reusing the name for S3 is fine; reusing the
  *meaning* silently is not.

---

## 6. The twelve requested topics, answered

| # | topic | state on 5.15-EEVDF | verdict |
|---|---|---|---|
| 1 | `place_entity()` | rewritten by the graft's `abk_eevdf_place_entity()`; lag + deadline + rel-deadline all present | present. Gap: initial placement does not use `avg_vruntime()` (→ A4); 5.15's `entity_is_long_sleeper()` path is still evaluated first and then overwritten |
| 2 | `se->vlag` | present (KABI slot 3), with clamp and reweight rescale | present. Gap: upstream's `vlag` doubles as the run-to-parity stash **and** as `vprot`'s sibling — neither use exists here (→ S1, C) |
| 3 | `avg_vruntime()` | present as an O(n) function; **no `cfs_rq` accumulator** | partial. The caching is the whole point upstream (→ S3) |
| 4 | `avg_slice` | **does not exist upstream** (0 hits in `torvalds/linux`); the nearest concepts are `cfs_rq::min_slice`/`se->max_slice` (v6.12+) and `se->slice` | nothing to port by that name; the underlying need is per-entity slice + slice aggregation, which is KMI-blocked (→ B) |
| 5 | entity enqueue/dequeue | graft snapshots lag + rel-deadline in `enqueue_entity`/`dequeue_entity` | present. Gaps: still does the CFS `± min_vruntime` renormalisation *and* the EEVDF placement (→ A1); no `avg_vruntime_add/sub` (→ S3) |
| 6 | delayed dequeue | **absent**, no `sched_feat`, no `se->sched_delayed` | genuinely missing feature; graded C on feasibility (KMI + subsystem size), not on value |
| 7 | reweight | `reweight_entity()` rewritten to dequeue → rescale vlag/rel-deadline → `place_entity()` → re-enqueue | present, different mechanism. The upstream v6.7/v6.9 reweight corrections therefore have no anchor (→ B) |
| 8 | yield | `yield_task_fair()` **untouched** — still pure CFS `set_skip_buddy()` | genuinely missing (→ A2) |
| 9 | wakeup / task placement | `select_task_rq_fair()`, `wake_affine*()`, `find_idlest_*()` untouched; placement still uses load, not lag | missing the lag-based cross-rq placement (→ A1). Note `wake_affine_weight()` here is `cpu_load()`/`task_h_load()`-based, which is a separate 5.15→6.x delta, not EEVDF |
| 10 | preemption | `check_preempt_tick()` has an EEVDF branch **prepended to** the legacy check; `entity_tick()` got a new resched block above the `nr_running > 1` gate; `check_preempt_wakeup()` untouched | partial, and the graft over-preempts because there is no run-to-parity (→ S1, S2) |
| 11 | lag computation/update | `abk_eevdf_update_lag()`, clamp via a max-slice-derived limit | present. Gap: limit formula is `calc_delta_fair(max_slice + sysctl_sched_min_granularity, se)`, not upstream's `lag_limit` = min(2·slice, TICK_NSEC)-derived bound, and `avg_vruntime()` inside it is a full scan |
| 12 | EEVDF ↔ PELT / schedutil | the graft does not touch PELT or `cpufreq_schedutil.c`. Upstream's EEVDF/PELT interactions are almost all inside delayed dequeue (`fc1892becd56`, `76f2f783294d`, `729288bc6856`, `3429dd57f0de`) | nothing separable to port. The one independent item is `6d2051403d6c` "Update util_est after updating util_avg during dequeue" (v7.x), which is a general schedutil correctness fix, not an EEVDF one |

---

## 7. Non-upstream annex: the graft-internal hot-path fix

If S3 is judged out of scope ("do not re-port EEVDF"), the dominant cost of the *current*
selector can still be removed without any upstream patch, by two local changes to
`abk_pick_eevdf()` / its helpers:

1. **Hoist the shared quantities out of the per-node loop.** `avg_vruntime(cfs_rq)` and
   `abk_eevdf_total_weight(cfs_rq)` are invariant across the loop iteration in
   `abk_pick_eevdf()`; `abk_eevdf_refresh_deadline()` calls them per node. Compute them
   once per pick and pass them down. That alone takes the pick from O(n²) to O(n).
2. **Stop mutating during selection.** Today `abk_pick_eevdf()` writes `se->deadline`,
   `se->vlag` and `se->vruntime` for every candidate it inspects, and it is called from
   `check_preempt_tick()` and `entity_tick()` as well as from `pick_next_entity()`. A
   read-only selector (refresh only the entity actually chosen) removes scheduler state
   perturbation caused by *asking* who should run.

Both are small, local, and preserve the existing anchors — worth doing as their own batch
regardless of whether S3 is ever attempted, and they make S3's diff smaller by removing
the `refresh_deadline`-in-loop pattern first.

### 7.1 The overflow mitigation from the Android common kernel — **already present**

`1119609dce0875` ("ANDROID: if EEVDF scheduling fail, picking leftmost, to avoid NULL
pointer", jiangyongfu-bit / johnstultz-work) is an **android common kernel** patch, not
mainline, which is why it sits in this annex rather than in §3. It is the downstream
*mitigation* for the same bug `b6eee96843e8` fixes upstream (§4.3): `vruntime_eligible()`
keeps returning false because `(vruntime - cfs_rq->min_vruntime) * load` overflows, the
selector finds nothing eligible, returns NULL, and its caller dereferences it. The commit
carries the crash: `Unable to handle kernel NULL pointer dereference at virtual address
00000000000000a0`, `lr : pick_next_task_fair+0x240/0x670`, with `vruntime = 0x1FD286B87E3`,
`cfs_rq->min_vruntime = 0x9E040BCE59A` and `avg_load = 0x16E699` — which is what the
overflow argument is computed from.

Its two hunks, and why neither is needed here:

1. **Selector: `if (!best) best = __pick_first_entity(cfs_rq);` — already implemented, and
   in effect identical.** `abk_pick_eevdf()` carries
   `if (!best) best = curr && curr->on_rq ? curr : __pick_first_entity(cfs_rq);`
   (`scripts/batch15_perf_eevdf.py:1138`). The `curr` arm is dead rather than an extra
   policy: line 1106 already nulls an ineligible or off-rq `curr`, and line 1120 promotes
   any surviving `curr` to `best`, so reaching the fallback with `curr != NULL` is
   impossible — the expression reduces exactly to the patch's leftmost pick.
2. **`pick_next_entity()`: `if (se && se->sched_delayed)` — not applicable.** 5.15 has no
   `se->sched_delayed` and no delayed dequeue; that field is 6.12+ and is KMI-blocked here
   because the `sched_entity` reserve run is exhausted (§5, AGENTS.md).

The patch's `printk_deferred("EEVDF scheduling fail, picking leftmost\n")` should **not** be
copied either, and the reason is a real difference in shape. Upstream tests `cfs_rq->skip`
*outside* the selector, so an empty `best` there means the scheduler is genuinely
inconsistent. This reconstruction tests skip **inside** the scan
(`if (cfs_rq->skip == se) continue;`, `scripts/batch15_perf_eevdf.py:1132`), so "found no
eligible entity" is also reached in a legitimate case — the eligible leftmost *is* the skip
buddy — where the fallback is doing exactly its job. Copying the printk would have bought
log spam in normal operation.

What was actually missing was never the logic but the **proof**: nothing asserted the
fallback exists, so the guarantee this patch is about could have been deleted silently —
the same class of gap Batch 28 found elsewhere. It is now pinned in
`tests/implementation_audit.py` (`perf:sched_eevdf_pick_logic`).

One residual, left honest: the guarantee is that the selector never returns NULL *when it
finds no eligible entity*. If the rb-tree is genuinely empty **and** `curr` is NULL, the
fallback returns NULL — as does upstream's, which cannot fall back to a non-existent
entity. On 5.15 that state is unreachable from `pick_next_entity()`: `nr_running > 0` is
established before the pick, and with no delayed dequeue every on-rq entity is in the tree.

---

## 8. The three most worthwhile missing optimizations

1. **`63304558ba5d` (v6.6) — RUN_TO_PARITY / curb wakeup-preemption.** ~12 lines plus one
   `SCHED_FEAT`, no infrastructure, and it addresses a behaviour the current graft
   demonstrably has (an EEVDF selector with no run-to-parity over-schedules; upstream's own
   measurement is −31 % context switches). One mandatory adaptation: the pick-time deadline
   stash cannot use `se->vlag` here because the graft owns `vlag` for lag — put it in a new
   `struct cfs_rq` field.
2. **`147f3efaa241`'s `check_preempt_wakeup()` hunk (v6.6), with `d4ac164bde7a` (v6.12).**
   The graft replaced the selector but left wakeup preemption on the old
   `wakeup_granularity` criterion, so placement and preemption disagree. This is the
   cheapest change with a directly user-visible effect (wakeup latency of binder/UI/input
   threads), and it needs no new fields.
3. **`af4cf40470c2` (v6.6) + `2227a957e1d5` + `ee4373dc902c` (v6.8), with `b01db23d5923`
   (v6.6) as a prerequisite.** The augmented, deadline-ordered tree and its O(1) front door
   replace an O(n²)–O(n³) scan that runs on every context switch and every tick, and remove
   selection-time mutation of `deadline`/`vlag`/`vruntime`. Largest win, highest cost; it
   consumes the last free `sched_entity` KABI slot (`slice`) and adds the 30-line
   `rb_add_augmented_cached()` to `include/linux/rbtree_augmented.h`. Take it as its own
   batch, or take §7's local refactor instead and stop there.

### Classification of the three

- **Genuinely missing functionality:** all three. None of them exists in any form in the
  current tree.
- **Already-existing functionality (do not re-port):** lag-based placement
  (`86bfbb7ce4f6`), `avg_vruntime()`'s negative-average correction (`650cad561cce`), the
  `PLACE_LAG` zero-lag fastpath (`4423af84b297`), START_DEBIT removal in effect
  (`e0c2ff903c32`), `vlag` clamping (`1560d1f6eb6b`'s intent).
- **Follow-up bugfix only (no standalone value):** `d4ac164bde7a`, `9de74a9850b9`,
  `b01db23d5923`, `c70fc32f4443`, and the whole `reweight_eevdf()` correction tail
  (`eab03c23c2a1`, `11b1b8bc2b98`, `afae8002b4fd`, `1560d1f6eb6b`).
- **Meaningful only for specific workloads:** `85e511df3cec`/`PREEMPT_SHORT` (latency-
  critical short-slice threads — real on Android, but it has needed three upstream
  corrections), delayed dequeue (context-switch-heavy thread pools; blocked by KMI and
  scope), `79104becf42b` (only matters once the v6.6 yield form is adopted).

### Promoted out of B by the feasibility sweep

The §4 sweep moved exactly one item: the **`update_cfs_group()` guard** buried in
`eab03c23c2a1` (§4.2). It is one line, needs no field, and removes ~4 full-tree scans plus
an rb erase/insert at every cgroup level on every tick. The "three most worthwhile" list
above is unchanged — S1 and S2 are still cheaper *and* more visible, and S3 is still the
largest win — but the guard is now the lowest-risk item on the board and a reasonable
**#4**, schedulable on its own before any of S.

Two other B rows were narrowed rather than promoted, and both are recorded so they are not
re-examined: `b01db23d5923` is **absorbed by S3** (the v6.8 re-sort replaced the
`min_deadline` heap search it patches with a single-pass search that already returns the
right answer — verified against `fair.c` at tag `v6.8`), and `b6eee96843e8` is
**structurally impossible** here because the graft's eligibility test has no multiplication
in it.

---

## 9. What was checked and deliberately not recommended

- No patch whose value is a **rename or field reorganisation** (`4ff674fa986c`,
  `dcbc9d3f0e59`, `2c2d9624697f`, `80390ead2080`).
- No patch that is **net-zero upstream** (`6d71a9c61604` + its revert `101f3498b4bd`).
- No patch that repairs a model this tree never adopted (`79f3f9bedd14`'s
  `min_vruntime` vs `avg_vruntime`, `85570f10a4c6`'s single runqueue).
- No patch requiring a scheduler subsystem (`152e11f6df29`'s delayed dequeue family).
- Nothing from the Android `ALT_PERIOD` / `BASE_SLICE` `sched_feat` pair is removed or
  bypassed by any recommendation above; the v6.8 tree ordering changes *which key* the
  rb-tree sorts on, but the slice arithmetic stays `sched_slice()`-based, so those two
  vendor feats remain live.

---

## 9. Batch 28: what actually landed

`scripts/batch15_perf_eevdf.py` was rebuilt in place (it stays the EEVDF family's
single owner — a second file editing the same helper block would re-trigger
`docs/group_recipe.md` trap 5).  The perf child now registers **three** groups
instead of two, and `GROUP_COUNTS["stable_perf_backport"]` goes 22 → 23.

| group | file(s) | what it carries |
|---|---|---|
| `sched_eevdf_pick_logic` | `kernel/sched/fair.c` | 21 steps: the accumulators, the rebuilt helper set, and the rewiring of `update_curr` / `update_min_vruntime` / `__enqueue_entity` / `__dequeue_entity` / `pick_next_entity` / `reweight_entity` / `enqueue_entity` / `dequeue_entity` / `place_entity` / `check_preempt_tick` / `set_next_entity` / `check_preempt_wakeup` / `yield_task_fair` |
| `sched_eevdf_core_fields` | `include/linux/sched.h` | the four `ANDROID_KABI_USE` slots (unchanged mechanism, slot 4 re-claimed) |
| `sched_eevdf_modern_fields` | `kernel/sched/sched.h`, `kernel/sched/features.h` | the three `cfs_rq` fields and the two switches |

### Audit item → landed commit

| audit grade | commit | landed as |
|---|---|---|
| S1 | `63304558ba5d` (v6.6) RUN_TO_PARITY | `sched_feat(RUN_TO_PARITY)` + the early return in `abk_pick_eevdf()` + the `cfs_rq::abk_pick_deadline` stash taken in `set_next_entity()` |
| S2 | `147f3efaa241` (v6.6) `check_preempt_wakeup()` hunk | the `abk_pick_eevdf(cfs_rq, se) == pse` rule replacing the `wakeup_gran()` ladder |
| S3 (partly) | `af4cf40470c2` (v6.6) `cfs_rq::avg_vruntime`; `ee4373dc902c` (v6.8) O(1) fastpath | the `abk_avg_vruntime_*` accumulators, the O(1) `avg_vruntime()`, the deadline refresh moved into `update_curr()`, and a read-only O(n) selector |
| A2 | `147f3efaa241` yield hunk + `79104becf42b` (v6.17) | the forfeit in `yield_task_fair()` |
| A3 | `85e511df3cec` (v6.12) PREEMPT_SHORT | `sched_feat(PREEMPT_SHORT)` + `abk_eevdf_preempt_short()` |
| A4 | `c40dd90ac045` (v6.12) new-task vruntime | the `initial` branch of `abk_eevdf_place_entity()` |
| — | `d4ac164bde7a` (v6.12) | not applicable: it repairs the `update_curr()`-side preempt-short path, and this port puts the rule in `check_preempt_wakeup()` instead, where `rq->nr_running` is not consulted |

### Audit item → deliberately NOT landed

| commit | why |
|---|---|
| `2227a957e1d5` + `b01db23d5923` (v6.8/v6.6) deadline-ordered augmented rbtree | re-keys the tree for every caller of `__pick_first_entity()` and needs `rb_add_augmented_cached()`, which 5.15 lacks. Batch 28 already removed the quadratic term; the remaining O(n) → O(log n) is the next batch, and it is the only audit item whose difficulty is "high" |
| `e8f331bcc270` (v6.6) lag-based cross-rq placement | needs `enqueue_entity` + `dequeue_entity` + `detach_task_cfs_rq` + `attach_task_cfs_rq` + `task_fork_fair` changed together; a partial change corrupts `vruntime` |
| `152e11f6df29` (v6.12) delayed dequeue | `se->sched_delayed`, `cfs_rq->nr_delayed`, `dequeue_task()` rework |
| slice protection / `min_slice` / `max_slice` (v6.12+) | `se->vprot`, `se->min_slice`, `se->max_slice` — no reserve slot left |

### Cost model, before and after

The Batch 15 selector called `abk_eevdf_refresh_deadline()` once per tree node,
and each call walked the tree twice (`avg_vruntime()` plus a `max_slice()` scan
that itself called `sched_slice()` per node).  Batch 28 makes
`abk_eevdf_refresh_deadline()` O(1) (it is now `update_deadline()`: two
comparisons and one `calc_delta_fair`), calls it from `update_curr()` instead of
from the selector, and drops the two scans.  `abk_pick_eevdf()` is now a single
O(n) pass with O(1) per node and no writes to scheduler state.

### Verification run for Batch 28

| level | check | result |
|---|---|---|
| syntax | `python3 -m py_compile scripts/*.py tests/*.py` | pass |
| unit | `python3 tests/stable_5_15_test.py` | pass |
| structure | `python3 tests/step_audit.py` on 5.15.167 / .178 / .194 / .216 | pass on all four (perf child 182 / 182 / 180 / 171 steps, second pass byte-identical) |
| content | `python3 tests/implementation_audit.py` on all four | pass |
| end-to-end | `bash tests/smoke.sh` on .194 and .216 | pass, rollback byte-identical |
| compile | `rebuild.sh --reseed` on the android13-5.15.216-lts tree, `CONFIG_WERROR=y` | `kernel/sched/core.o` and `kernel/sched/fair.o` compile with no diagnostic; `abk_pick_eevdf` and `abk_eevdf_preempt_short` are real symbols in `System.map` (the other helpers are inlined) |
| device | flashed to `boot_a` on vermeer (Redmi K70, SD 8 Gen 2) | boots; `/proc/kallsyms` carries `abk_eevdf_preempt_short`, which Batch 15 never had; 0 scheduler WARNs, 0 stalls under an 8-hog load; KernelSU root intact (so no KMI break) |

Two things that row is careful about, because both are easy to get wrong:

* **`uname -r` and `/proc/version` are not evidence here.**  `localversion` comes
  from the source tree and `KBUILD_BUILD_TIMESTAMP` is pinned, so both strings are
  byte-identical before and after this batch.  The only discriminating check is a
  symbol that Batch 15 did not have.
* **The `mm/page_alloc.c` boot warnings are pre-existing, and that was measured,
  not inferred.**  Restoring `boot_a.orig` and booting it produced the same five
  `WARNING: CPU` sites in the same order (`mm/page_alloc.c:4091`,
  `mm/page_alloc.c:5865`, `drivers/base/core.c:1318`, two
  `kernel/irq/manage.c:791`).  Both mm sites are mainline checks whose predicate is
  the *caller's* gfp flags (`__GFP_NOFAIL && order > 1`; `order >= MAX_ORDER`
  without `__GFP_NOWARN`), reached from vendor `modprobe` at early boot.  That A/B
  also exercised the rollback path end to end.

No performance A/B was run, so this audit claims no speedup.  Upstream's "-31 %
context switches" figure for `RUN_TO_PARITY` is from `perf bench sched messaging`
on x86; it is not a number for this phone and is not reported as one.

### 9.1 A side finding: this build is not bit-reproducible

Three builds were made.  `build28` and `build28b` had **identical inputs**; their
`Image`s differ by **1,110 bytes out of 40.7 MB**, all clustered in the
BTF/debug-metadata region — the code is byte-identical.  `build27` differed from
`build28` only in eight comment lines, and their `Image`s differ by **8,554,807
bytes (~20 %)**: ThinLTO partitions on a hash of the module content, so editing a
comment re-partitions the binary.

The consequence is a methodological one, not a defect in this batch: **`Image`
sha256 is not a valid identity for "the same kernel"** under
`CONFIG_LTO_CLANG_THIN=y` + `CONFIG_DEBUG_INFO_BTF=y`, and a same-source
`verify-parity.py` run will report `Image` as DIFFERENT without that meaning
divergence.  The only sound same-kernel checks are a code-section comparison, or a
functional probe with discriminating power (the `kallsyms` one above).

Because of this, the flashed image is the one built from the exact audited repo
state (`Image` sha256 `581bc3df…`), verified by readback before the reboot — an
earlier build with the pre-rename comments was flashed first, and although its
emitted C differs only in comments, it was replaced rather than described as
equivalent.
