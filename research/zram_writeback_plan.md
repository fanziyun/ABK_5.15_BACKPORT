# ZRAM Writeback Upstream Sync Plan

**Status:** audit + plan complete; the P0/P1 batch it recommends has since landed as
**Batch 14** (`scripts/batch14_core_zram_writeback.py`, module `0.19.0`). §10.1–§10.4 below
record the *design as analysed*; the **shipped** shape differs in one load-bearing way and
the rationale is in §10.5 — read that before touching the group.

**Date of evidence:** 2026-09-14 (audit). **Date of implementation:** same session; the
landed batch was verified with `step_audit.py` + `implementation_audit.py` +
`smoke.sh` on 5.15.167/.178/.194/.216.

**Verification of the commit corpus (added after the audit):** every upstream hash cited in
this document was re-checked against the live GitHub API; the machine-readable result is
`research/zram_wb_audit/verified_commits.tsv` (45 hashes; subject, author and date all
confirmed, 0 fetch failures). The four ACK cherry-pick hashes quoted in §2–§3 were *not*
re-verified against the ACK trees by this script — they come from the branch fetches listed
in `research/zram_wb_audit/stable_515_zram.json` — so treat those as second-hand.

## 0. Evidence base and method

Every fact below was fetched during this audit. Because the harness `web_fetch`/`web_search`
tools were unavailable (DNS proxy + a 404 search endpoint), the research was driven with
`curl` from `pwsh`, which reaches the real network. Raw artefacts are kept next to this file:

| artefact | how it was produced |
|---|---|
| `research/upstream-zram/commits.csv` | GitHub API `commits?path=drivers/block/zram/zram_drv.c`, 4 pages × 100 = **400 commits** (2014-07-02 → 2026-08-10) |
| `research/upstream-zram/patches/*.patch` | `https://github.com/torvalds/linux/commit/<sha>.patch` — **80 real patch mails** with diffs, `Fixes:` tags and cover letters (this audit's 66 + 14 recovered from the earlier `research/zram_cwb/` reconnaissance) |
| `research/upstream-zram/zram_drv_master.{c,h}` + `zram.rst.master` | `raw.githubusercontent.com` master (`v7.3-rc3` era, 3323 lines) |
| `research/upstream-zram/zram_drv_linux-5.15.y.c` | `gregkh/linux` branch `linux-5.15.y`, Makefile `SUBLEVEL = 220` |
| `research/upstream-zram/zram.rst.v5.15` | ABI baseline for the doc diff |
| `research/zram_wb_audit/verified_commits.tsv` | `research/zram_wb_audit/verify_commits.sh` — GitHub API `commits/<sha>` for all 45 cited hashes |
| local baselines | `abk515_ref_{167,178,194,211}` and `build/abk-trees/{167,178,194,216}` |
| suite cross-check | `../ABK_ABI_PATCH_SUITE/scripts/abk_feature_porting.py` |

Release tags resolved by fetching the tag objects (`v6.12` = 2024-11-17, `v6.6` = 2024-10,
`v6.16` = 2025-07-27, `v6.18` = 2025-11-30, `v6.19` = 2026-02-08, `v7.0` = 2026-04-12,
`v7.1` = 2026-06-14, current mainline = `v7.3-rc3`).

Triage rule used throughout: a commit is only a candidate if the code it patches **exists in
the 5.15 shape**. The 2023 and later zram writeback rewrites (UNDER_WB removal, post-processing
slot lists, bio batching, per-slot `spinlock_t`) replaced the primitives 5.15 uses, so most
modern fixes are *inapplicable*, not "missing".

---

## 1. Current 5.15 Status

### 1.1 What the target tree actually contains

Audited `drivers/block/zram/{zram_drv.c,zram_drv.h,Kconfig}` from `abk515_ref_194`
(`android13-5.15-2025-12`, SUBLEVEL 194), line-referenced:

* `zram_drv.h`: `ZRAM_FLAG_SHIFT 24`; flags `ZRAM_LOCK/ZRAM_SAME/ZRAM_WB/ZRAM_UNDER_WB/ZRAM_HUGE/ZRAM_IDLE`;
  `zram_stats` gains `bd_count/bd_reads/bd_writes` under `CONFIG_ZRAM_WRITEBACK`;
  `struct zram` has `backing_dev`, `wb_limit_lock`, `wb_limit_enable`, `bd_wb_limit`, `bdev`,
  `bitmap`, `nr_pages`. **No `compressed_wb`, no `wb_batch_size`.**
* `Kconfig`: `ZRAM_WRITEBACK` only. (`ZRAM_TRACK_ENTRY_ACTIME`/`ZRAM_MULTI_COMP` are added by this
  module's own Batch 4 group, not by the baseline.)
* `zram_drv.c`:
  * `idle_store()` (`"all"`) marks `ZRAM_IDLE`, explicitly skipping `ZRAM_UNDER_WB` (a race fix is already present).
  * `writeback_limit_{store,show}`, `writeback_limit_enable_{store,show}` — guarded by `wb_limit_lock`, **present**.
  * `backing_dev_store()` — `filp_open_block` + `blkdev_get_by_dev` + `kvzalloc` bitmap; has the
    **zero-sized-device rejection** (the `be48c412f6eb` hunk, lines 501–506).
  * `reset_bdev()` — releases `blkdev_put`+`filp_close`, frees bitmap.
  * `alloc_block_bdev()`/`free_block_bdev()` — bitmap allocator; `WARN_ON_ONCE` on double free.
  * `zram_page_end_io()`, `read_from_bdev_async()` (uses `bio_chain(bio,parent)`; `bio_alloc(GFP_ATOMIC,1)`),
    `read_from_bdev_sync()` (`#if PAGE_SIZE != 4096`, on-stack work + `flush_work`), `read_from_bdev()`.
  * `writeback_store()` — inputs `idle`, `huge`, `page_index=<N>`; **one `submit_bio_wait()` write per page**
    with `bio_init(&bio, &bio_vec, 1)`; sets `ZRAM_UNDER_WB`, reads via `zram_bvec_read()`, then
    `zram_free_page()` + `ZRAM_WB`; decrements `bd_wb_limit` only on success.
  * `bd_stat_show()` — reports `bd_count/bd_reads/bd_writes` in 4 K units, already **identical to mainline**.
  * sysfs: `backing_dev`(RW), `writeback`(WO), `writeback_limit`(RW), `writeback_limit_enable`(RW), `bd_stat`(RO).

### 1.2 Status table

| Feature / fix | Current 5.15 status | Note |
|---|---|---|
| backing device + free-space bitmap | **complete** | matches what upstream shipped |
| incompressible (`huge`) writeback | **complete** | |
| idle writeback + `ZRAM_IDLE` race fix | **complete** | |
| `writeback_limit` / `_enable` | **present but落后 (missing one later fix)** | identical to the 5.16 original; missing the `rounddown()` overflow guard |
| `bd_stat` | **complete** | modern form already; no action |
| `huge_idle` mode | **missing** | upstream 5.15.14 (`30226b69f876`), never backported to android13-5.15 |
| `incompressible` mode | **missing** | upstream 5.19 (`b46f9ea3cb35`) |
| `page_index` ABI | **present, old form** | single index, one bio per call |
| `type=` / `page_indexes=` / ranges (6.16 rework) | **missing** | `cf42d4cccf0d`, v6.16 |
| writeback bio batching + `writeback_batch_size` | **missing** | `f405066a1f0d`+`e828cccb72ed`, v6.19 |
| compressed writeback | **missing** | `d38fab605c66`, v7.0 |
| `UNDER_WB`-free writeback / pp-slot target selection | **implemented differently** (upstream 2024 rewrite) | a different mechanism, not a superset |
| `writeback_store()` scan-bound race fix | **missing** | `894913e2d35c`, v7.3-rc, Cc: stable |
| `read_block_state()` scan-bound race fix | **missing** | `391f057f44a5`, same series; only relevant with `ZRAM_MEMORY_TRACKING` |
| backing-device leak on uninit ZRAM | **missing** | `74363ec674cb`; android14-6.1/15-6.6/16-6.12 **do** carry it, 5.15 does not |
| zero-sized device rejection | **already present** | came in with `be48c412f6eb` via ACK, not by this module |
| `cond_resched()` in the writeback loop | **missing** | `424d0e5828ad` |
| `zram_read_from_zspool()` in writeback | **implemented differently** | this module's recompression graft adds an *equivalent* helper; note the 4-arg vs 3-arg signature |
| modern `dev_lock`/`guard()`/`sysfs_emit()` infrastructure | **not present** | 5.15 uses `init_lock` + `scnprintf`; every modern diff must be re-anchored |
| **ABK_ABI_PATCH_SUITE "compressed_writeback"** | **not the upstream feature** | see §6 — it is a control-surface graft with no I/O implementation |

### 1.3 The one crate that must not be carried over

`ABK_ABI_PATCH_SUITE` already declares `bool compressed_wb;`, `compressed_writeback_store/show`,
`DEVICE_ATTR_RW(compressed_writeback)` and `zram->compressed_wb = false;` on the 5.15/6.1 target.
Its own report strings admit the scope: *"compressed_writeback stays a compatibility
control-surface graft and does not absorb abk zram algorithm assets"*. Nothing in the suite's
zram path touches `zram_bvec_write()`, `writeback_store()` or the bio submission, so
`echo yes > compressed_writeback` sets a flag. **Upstream never had a flag-only version** —
v7.0's `d38fab605c66` is the real implementation (227+/53−), and it uses a *different field
semantic* (`compressed_wb` gates `read_from_zspool_raw()` vs `read_from_zspool()`).

---

## 2. P0

### P0-1 · `writeback_store()` computes its scan bound outside `init_lock`

```
Feature:            stable-bounds for the writeback scan
Upstream status:    fixed upstream in v7.3-rc; Cc: stable@vger.kernel.org
Introduced:         894913e2d35c ("zram: fix out-of-bounds access in writeback_store()"),
                    Longlong Xia, 2026-08-04 (series "zram: fix stale scan bounds after
                    reinitialization", patch 1 of 2); twin 391f057f44a5 for read_block_state()
Important commits:  894913e2d35c (writeback_store), 391f057f44a5 (read_block_state)
                    Fixes: a939888ec38b ("zram: support idle/huge page writeback")
Current 5.15 status: NOT present on any baseline, nor on linux-5.15.y (SUBLEVEL 220)
Missing pieces:     5.15 reads `zram->disksize >> PAGE_SHIFT` at function entry
                    (zram_drv.c:630 in 5.15.194) and only then takes `down_read(init_lock)`.
                    `zram_reset_device()` rewrites `zram->disksize` and frees/reallocates
                    `zram->table` under `down_write(init_lock)`. A reset+reconfiguration with a
                    smaller disksize between the two points leaves `nr_pages` describing the old
                    table while the loop walks the new one -> slot flags and `zram->table[index]`
                    accesses past the end of the allocation.
Dependencies:       none (macro substitution on the 5.15 `init_lock` form). Upstream's patch is
                    against the rewritten `dev_lock` + `lo/hi` form and does NOT apply as-is.
Conflict with ABK:  touches the region between `writeback_store()`'s declaration block and
                    `down_read(&zram->init_lock)`. This module's `zram_recompression` group rewrites
                    `writeback_store`'s callee (`zram_bvec_read`), not this region -> compatible,
                    but the new group must be registered **after** `zram_recompression`.
Backport difficulty: small — one 5.15-specific edit:
                    remove the initialiser `= zram->disksize >> PAGE_SHIFT` (declare `nr_pages;`),
                    move the `page_index` range check out of the pre-lock parse, and assign
                    `nr_pages` immediately after the `init_done(zram)` check. The `index >= nr_pages`
                    guard must run under the read lock to keep the bound consistent.
Correctness impact: HIGH — out-of-bounds slot access = memory corruption / kernel crash. Trigger
                    is a reset racing a writeback, i.e. exactly the Android userspace pattern
                    (mmd/recompress daemons rewrite disksize between sweeps).
RAM impact:         none
CPU impact:         none
I/O impact:         none
Power impact:       none
Flash wear impact:  none
Android relevance:  high — Android is the only consumer that resets and re-sizes zram at runtime
Priority:           P0
Recommended action: logical backport as its own group (e.g. `zram_writeback_bounds`) in
                    `stable_backport_core`; document the 5.15 `init_lock` adaptation in the group
                    docstring so the next reader does not "fix" it back to the upstream shape.
```

### P0-2 · backing device is never released when ZRAM is destroyed uninitialised

```
Feature:            full teardown of an uninitialised device
Upstream status:    merged v6.16 (2024-12-09, mm tree); Cc: stable@vger.kernel.org
Introduced:         74363ec674cb ("zram: fix uninitialized ZRAM not releasing backing device"),
                    Kairui Song, 2024-12-09; series partner be48c412f6eb (already in 5.15)
Important commits:  74363ec674cb  Fixes: 013bf95a83ec
Current 5.15 status: NOT present on 167/178/194/216, nor on linux-5.15.y. Confirmed present on
                    ACK android14-6.1 (ac3b5366b9), android15-6.6 (0b5b0b6556), android16-6.12
                    (6fb92e9a52) — so it is the *one* writeback fix Android already considers
                    necessary, and 5.15 is the only branch without it.
Missing pieces:     (a) `zram_meta_free()` lacks the `if (!zram->table) return;` guard and does
                    not NULL `zram->table`; (b) `zram_reset_device()` still early-returns when
                    `!init_done(zram)`, so `reset_bdev()` (and therefore `blkdev_put`) never runs
                    on a device that only had `backing_dev` written.
Dependencies:       `zs_destroy_pool()` is called with `pool->size_class[i]` inside its loop and is
                    NOT NULL-tolerant, so it MUST NOT be reached with a NULL pool. The 5.15
                    `zram_meta_free()` calls it unconditionally; the new `if (!zram->table) return;`
                    guard is what makes the reset path safe. This is the load-bearing reason the
                    upstream hunk cannot be split.
Conflict with ABK:  `zram_meta_free()`/`zram_reset_device()` are untouched by every existing
                    group — lowest-conflict candidate in this plan.
Backport difficulty: small — the upstream diff is 4+/5− and applies to 5.15 with only the
                    function-line drift (5.15's `zram_reset_device()` at ~1694, `zram_meta_free()`
                    at ~1148).
Correctness impact: HIGH — writing `backing_dev` and then `reset`/`rmmod` without a `disksize`
                    write leaks the `struct file` and the exclusive `blkdev_get_by_dev(zram)`
                    holder, pinning the backing block device (and zram itself) forever.
RAM impact:         none
CPU impact:         none
I/O impact:         none
Power impact:       none
Flash wear impact:  none
Android relevance:  high — the companion module and the ROM both write `backing_dev` before
                    `disksize`; a failed bring-up that aborts before `disksize` is exactly this case.
Priority:           P0
Recommended action: logical backport (same group as P0-1 or its own `zram_wb_teardown`);
                    add the `!/zram->table` guard and the reset early-return removal in one
                    transactional group (required steps) so a partial tree can never call
                    `zs_destroy_pool(NULL)`.
```

---

## 3. P1

### P1-1 · `writeback_limit` underflows on page sizes > 4 K

```
Feature:            page-size-aligned writeback budget
Upstream status:    present in current mainline only (the `rounddown()` guard); no dedicated
                    commit hash of its own — it is inside the writeback_limit lineage that 5.15
                    already carries in its original 5.16 form
Introduced:         original feature bb416d18b850 ("zram: writeback throttle", Minchan Kim,
                    2018-12-28); the alignment guard appears in the mainline file body today with
                    an explanatory comment ("when the page size is set to 16KB and bd_wb_limit is
                    set to 3, a single write-back operation will cause bd_wb_limit to become -1")
Important commits:  bb416d18b850 (feature), 1d69a3f8ae77 (idle writeback fixes/cleanup);
                    mainline `writeback_limit_store()` line 627 = `val = rounddown(val, PAGE_SIZE / 4096);`
Current 5.15 status: `writeback_limit_store()`/`_show()` exist and are byte-equivalent to the
                    5.16 originals; the `rounddown` guard is absent, as is the comment.
Missing pieces:     the clamp. 5.15 charges `zram->bd_wb_limit -= 1UL << (PAGE_SHIFT - 12)` per
                    successful page; with `PAGE_SIZE == 16384` that is 4 per page, so a budget of
                    1..3 wraps the u64 to ~2^64 and the budget becomes unlimited — the limit
                    silently stops limiting.
Dependencies:       none. One statement, no API. `rounddown()` is available in v5.15 via
                    `include/linux/math.h` (reached through `linux/kernel.h`) — verified by
                    fetching the v5.15 header, so no `#include` is needed.
Conflict with ABK:  none (the two sysfs stores are untouched by every group).
Backport difficulty: trivial (one line) — but note it is **unobservable on a 4 K-page build**,
                    so it cannot be regression-tested on the current GKI arm64 config.
Correctness impact: medium-high — "obviously wrong behaviour": a safety mechanism designed to
                    cap flash wear turns itself off. Only reachable when `PAGE_SIZE > 4 K`.
RAM impact:         none
CPU impact:         none
I/O impact:         none
Power impact:       none
Flash wear impact:  HIGH when triggered (unbounded writeback budget)
Android relevance:  medium-today / high-soon — Android 15/16 GKI is moving to 16 K pages; the
                    5.15 GKI arm64 builds audited here are 4 K, so this is insurance, not a live fix.
Priority:           P1
Recommended action: add to the same group as P0-1 (or the P0-2 group) as an `optional` step?
                    NO — make it a required step of its own group so its absence is visible in
                    `implementation_audit.py`. It is a one-line anchor with zero risk.
```

### P1-2 · unbounded writeback loop: no `cond_resched()`

```
Feature:            rescheduling point in the writeback sweep
Upstream status:    landed 2024-12-18, first released in v6.14
Introduced:         424d0e5828ad ("zram: cond_resched() in writeback loop"), Sergey Senozhatsky
Important commits:  424d0e5828ad; adjacent b8d3ff7bb511 ("use zram_read_from_zspool() in writeback")
                    and ef932cd23b78 ("factor out ZRAM_HUGE write") are the same review series
Current 5.15 status: absent. `writeback_store()`'s `for (; nr_pages != 0; index++, nr_pages--)`
                    loop can iterate over every slot of a multi-GiB disk while holding
                    `down_read(init_lock)` and, per page, an `alloc_page(GFP_KERNEL)` +
                    `zram_bvec_read()` (decompression) + a synchronous `submit_bio_wait()`.
Missing pieces:     a `cond_resched()` on the loop. The upstream hunk sits in the rewritten
                    batched loop; the 5.15 insertion point is inside `writeback_store()`'s
                    `for` body (after the `next:` label's `zram_slot_unlock()`).
Dependencies:       none.
Conflict with ABK:  same function as the P0-1 edit; ordering matters (P0-1 first).
Backport difficulty: trivial, but the step must be anchored on text this module itself may have
                    changed, so it belongs in the same group as P0-1 to avoid anchor collisions.
Correctness impact: medium — "writeback 失效/stability": a multi-second non-preemptible loop
                    holding a read lock delays every reset/disksize store behind it and can
                    trip RCU/soft-lockup watchdogs on a loaded phone.
RAM impact:         none
CPU impact:         neutral (tiny cost, large latency win)
I/O impact:         neutral
Power impact:       neutral
Flash wear impact:  none
Android relevance:  high — Android's recompress sweeps and idle marking run concurrently with
                    writeback, and the ROM's watchdogs are aggressive.
Priority:           P1
Recommended action: bundle with P0-1 in one `zram_writeback_bounds` group (three steps: bound
                    move, range-check move, `cond_resched()`), clear in the docstring that only
                    the first two are the upstream `894913e2d35c` hunk.
```

### P1-3 · writeback bio batching + `writeback_batch_size` (scoped analysis)

```
Feature:            multiple in-flight writeback bios, run-time configurable
Upstream status:    merged for v6.19 (both commits dated 2025-11-22, released 2026-02-08)
Introduced:         f405066a1f0d ("zram: introduce writeback bio batching") + e828cccb72ed
                    ("zram: add writeback batch size device attr"), Sergey Senozhatsky,
                    series "zram: introduce writeback bio batching", v6 (6 patches)
Important commits:  f405066a1f0d (301+/68−), e828cccb72ed (41+/6−, adds `wb_batch_size`,
                    default 32, `!val` rejected), e87ddea34567 ("rework bdev block allocation"),
                    then the fallout: bf62f69574b1 (UAF, Cc: stable), 3e8d8eb8d7f5 (blk_idx leak),
                    bf989ade270d (error propagation)
Current 5.15 status: absent. 5.15 still carries the original design comment — "XXX: A single page
                    IO would be inefficient for write but it would be not bad as starter" — and
                    submits exactly one `submit_bio_wait()` REQ_SYNC bio per page inside the loop.
Missing pieces:     the whole `zram_wb_ctl`/`zram_wb_req` mechanism: a per-writeback batch of
                    requests, `req->bio` with `bi_end_io = zram_writeback_endio`, completion moved
                    out of the submit path into `zram_complete_done_reqs()` driven by a wait queue,
                    `num_inflight` accounting, and split `zram_account_writeback_submit()` /
                    `_rollback()` helpers. In 5.15 the *success* accounting happens after
                    `submit_bio_wait()` returns, so the ordering the upstream cover letter
                    describes ("adjust wb_limit before submission") does not apply.
Dependencies:       5.15 has `bio_init`/`bio_alloc`, `bio_chain`, `atomic_t`, `wait_queue_head_t`,
                    `kfree_rcu` — all present. What 5.15 does NOT have is the post-processing slot
                    machinery the batched loop is written against (`zram_pp_ctl`/`zram_pp_slot`,
                    `select_pp_slot()`, `release_pp_slot()`, `ZRAM_PP_SLOT`, `ZRAM_INCOMPRESSIBLE`),
                    which arrived with 330edc2bc059/5e99893444a0/b967fa1ba72b (v6.13 era) and with
                    this module's own recompression graft. Therefore a faithful port is really a
                    port of 4 upstream series.
Conflict with ABK:  `writeback_store()`, `struct zram`, `zram_drv.h` stats and the sysfs attr
                    group. The module's `zram_recompression` graft rewrote part of that function;
                    `ABK_ABI_PATCH_SUITE` adds an attr to the same `zram_disk_attrs[]` array.
Backport difficulty: LARGE. Two viable logical backports, neither a cherry-pick:
                    (a) *asynchronous writeback, borrowed design*: submit each page's bio
                        asynchronously, cap in-flight with a counter/wait-queue, complete on
                        `bi_end_io`, `wait_event` for the tail. ~150–250 new lines in
                        `writeback_store()` + new `struct zram` fields. Gives real queue-depth
                        parallelism without porting the pp-slot rework.
                    (b) *blk-plug batching*: keep synchronous submission, wrap a run of pages in
                        `blk_start_plug()/blk_finish_plug()`, batch `alloc_block_bdev()` calls.
                        Much smaller, but the upstream authors explicitly rejected blk-plug
                        because "writeback IO patterns are expected to be random".
Correctness impact: medium — the mechanism introduces its own race class (bf62f69574b1 UAF in
                    `zram_writeback_endio`, fixed only in v7.3-rc) and its own leak class
                    (3e8d8eb8d7f5). Any port must include both fixes from day one.
RAM impact:         explicit and bounded: a batch holds `wb_batch_size` `struct zram_wb_req`
                    (+ `struct bio`) and `wb_batch_size` pages. At the default 32 and 4 K pages
                    that is ~128 KiB of pages plus the request array, allocated **per writeback
                    invocation** (the upstream code allocates the batch inside the write path,
                    not at probe — verified in the master body: `init_wb_ctl()` is called from the
                    writeback entry, and it degrades gracefully by shrinking the batch when
                    allocation fails).
CPU impact:         slightly higher (completion bookkeeping, spinlock, wakeups),
                    offset by far fewer submit/complete round-trips.
I/O impact:         the point of the change: throughput up, latency down on UFS/eMMC by keeping
                    several requests in flight. Upstream provides NO benchmark numbers (see §9).
Power impact:       likely positive (shorter busy windows), unquantified upstream.
Flash wear impact:  none directly (same bytes); possibly better scheduling/less write amplification.
Android relevance:  medium — writeback is not enabled on the reference device today, so there is
                    nothing to speed up yet. It becomes relevant only after the ROM tier
                    (`ABK_515_DEFCONFIG_ROM=1`) makes writeback live.
Priority:           P1 (only if writeback is actually shipped; otherwise P2)
Recommended action: do NOT attempt the faithful port now. Defer; if a batched writeback is
                    wanted, write option (a) as a *new implementation* documented as
                    "design borrowed from f405066a1f0d", and port bf62f69574b1 + 3e8d8eb8d7f5 in
                    the same group so the known bugs never exist in this tree.
```

---

## 4. P2

### P2-1 · compressed writeback

```
Feature:            store the compressed zsmalloc object on the backing device, decompress on read
Upstream status:    merged for v7.0 (2025-12-01, released 2026-04-12), attribute renamed
                    2026-02-26 before release
Introduced:        4c1d61389e8e ("zram: introduce writeback_compressed device attribute") →
                    ba4c3698e696 ("zram: rename writeback_compressed device attr") →
                    d38fab605c66 ("zram: introduce compressed data writeback"), all 2025-12-01,
                    series "zram: introduce compressed data writeback", v2; Richard Chang +
                    rewrites by Sergey Senozhatsky
Important commits:  d38fab605c66 (227+/53−), 4c1d61389e8e, ba4c3698e696,
                    3bf1c285dc40 ("clear trailing bytes", 2026-05-26), bf989ade270d
Current 5.15 status: absent (and see §6: the suite's `compressed_writeback` attr is a stub)
Missing pieces — mechanism as actually implemented upstream:
  * `read_from_zspool_raw(zram, page, index)`: read the object with the *primary* comp stream
    (only for its `local_copy` bounce buffer, "in case if object spans two physical pages"),
    `memcpy_to_page(page, 0, src, size)`, then `memzero_page(page, size, PAGE_SIZE - size)`.
  * `read_compressed_page(zram, page, index)`: `zs_obj_read_begin()` + `zcomp_decompress()`.
  * `read_from_zspool()` becomes a dispatcher (SAME / compressed / incompressible).
  * The backing device stores the **raw compressed object bytes followed by zero padding**;
    there is no header, no side metadata, and one backing block still equals one page.
  * The read path identifies a compressed backing page purely by `zram->compressed_wb`, not by
    any per-page marker — so toggling the flag after pages were written back mis-reads them.
    Upstream's doc therefore says "should be configured before the zramX device is initialized"
    and the store returns `-EBUSY` once `init_done()`.
  * `zram_async_read_endio()` cannot decompress in IRQ context, so compressed read-back is
    deferred to `system_highpri_wq` via `INIT_WORK/DEFERRED`.
Dependencies on 5.15 — the hard part:
  * `zs_obj_read_begin(pool, handle, size, local_copy)` / `zs_obj_read_end` are the **modern
    zsmalloc mapping API**; they do not exist in 5.15 (grep: only `zs_map_object`,
    `zs_unmap_object`, `__zs_map_object`, `area->vm_mm == ZS_MM_RO`). The commit that ports zram
    to it is `82f91900c722` ("zram: switch to new zsmalloc object mapping API", 2025-03-03), which
    is itself part of a zsmalloc rewrite (zpdesc/folio conversion, 7d2e1a6950, 4610d35c14).
    **`zs_lookup_class_index`—the one zsmalloc API this module already adds—is NOT it.**
  * The object can span two physical pages, which is why upstream needs a bounce buffer
    (`zcomp_strm::local_copy`). On 5.15 the read-only `zs_map_object(..., ZS_MM_RO)` path maps via
    the per-cpu mapping area; whether a single `memcpy` out of it is valid for a cross-page object
    must be proven per class before relying on it — do not assume.
  * The remaining helpers are **verified present in v5.15** by fetching the v5.15 headers:
    `memcpy_to_page()` and `memzero_page()` (`include/linux/highmem.h`), `bio_inc_remaining()`
    and `bio_chain()` (`include/linux/bio.h`), `system_highpri_wq`, `kfree_rcu`.
    So the *page/bio/workqueue* half of the feature ports without new APIs; the zsmalloc
    mapping API is the only genuine blocker.
Conflict with ABK:  (1) `ABK_ABI_PATCH_SUITE` already creates `compressed_wb` +
                    `compressed_writeback` on the same file → the real feature collides by name,
                    and the stub would have to be removed or absorbed.
                    (2) This module's recompression graft already adds a **different**
                    `zram_read_from_zspool()`. Upstream's real function is 3-arg
                    `(zram, page, index)`; the graft added a 4-arg
                    `(zram, page, index, size)`-era helper adapted to `comps[prio]`. Introducing a
                    second, upstream-named `zram_read_from_zspool()` in the same file is a
                    guaranteed compile-time collision — the port must instead *adapt the existing
                    helper* or rename deliberately.
Backport difficulty: LARGE. Requires: a raw-object read helper (new zsmalloc API or a proven
                    `zs_map_object`-based equivalent), an incompressible-page write helper
                    (`read_incompressible_page`/`write_incompressible_page` in master are from the
                    2024/2025 refactors), a deferred-decompression work path on the async read,
                    and reconciliation with both ABK grafts above.
Correctness impact: medium — the feature is only safe when set before `disksize`; a wrong flag
                    timing makes already-written pages unreadable. Also has a known data-leak
                    class (trailing bytes) that the zero-fill fixes.
RAM impact:         small positive (no page-sized decompression scratch per read in the write
                    direction), but it *keeps* the compressed object in zsmalloc while the page is
                    on the backing device, so zram's own footprint is unchanged relative to idle
                    writeback.
CPU impact:         positive *in principle*: the whole point is "avoiding decompression overhead"
                    on the writeback path, i.e. pages that are written back but rarely read back no
                    longer cost a decompression. This is the feature's only justification.
I/O impact:         neutral to slightly negative: the compressed object is smaller than 4 K, but
                    one backing block is still consumed per page, so the same number of 4 K I/Os
                    happens (the padding is zeroed and written). Upstream does not claim fewer I/Os.
Power impact:       claimed positive by the cover letter ("potential CPU and battery wastage"),
                    NOT measured in the patch series.
Flash wear impact:  neutral (same blocks written; content is smaller but blocks are fixed size).
Android relevance:  medium — Android is the most likely beneficiary (swap-in rate after writeback
                    is low), but nothing in Android 5.15 ships it and the ABK target device does
                    not even enable `CONFIG_ZRAM_WRITEBACK`.
Priority:           P2
Recommended action: defer. If it is ever attempted, treat it as a *new feature on top of the ABK
                    recompression graft*, first landing `82f91900c722`-equivalent zsmalloc mapping
                    support in `mm/zsmalloc.c` + `include/linux/zsmalloc.h`, and write down the
                    backing-image format so a future reader knows a `compressed_wb` toggle mid-life
                    corrupts the mapping. Do not enable by default.
```

### P2-2 · writeback interface rework (`type=` / `page_indexes=` / LOW-HIGH ranges)

```
Feature:            key=value writeback ABI with page ranges and multiple params per call
Upstream status:    in v6.16 (2025-07-27) and in ACK android16-6.12 (UPSTREAM 47a0b8f4c7)
Introduced:         cf42d4cccf0d ("zram: modernize writeback interface"), Sergey Senozhatsky,
                    2025-03-27
Important commits:  cf42d4cccf0d (the rework); the modern grammar is
                    `writeback_store()`'s parse loop + the `PAGE_WRITEBACK`/`HUGE_WRITEBACK`
                    (1<<0)/`IDLE_WRITEBACK` (1<<1)/`INCOMPRESSIBLE_WRITEBACK` (1<<2) bitmask,
                    `huge_idle` (5.15.14 upstream, never in android13-5.15), `incompressible`
Current 5.15 status: the OLD interface only — `idle` | `huge` | `page_index=<N>`, exactly one page
                    per call. Old inputs verified from the 5.15 file, not from memory.
Missing pieces:     `type=<...>` parsing, `page_indexes=LOW-HIGH` (+ multiple ranges in one call),
                    repeated `page_index=` params, the bitmask modes, and the `lo/hi` scan bounds
                    that make all of this efficient.
Dependencies:       5.15's `writeback_store()` is a *scan* loop, not a range walker, so the rework
                    is not an add-on: it changes the loop's shape (upstream's version walks
                    `lo..hi` and selects post-processing slots via the pp-list). Faithfully
                    porting the ABI therefore requires the same pp-slot machinery that P1-3 needs.
Conflict with ABK:  `writeback_store()` (shared with `zram_recompression`), and the classic
                    "modern ABI exists therefore backport it" trap the user warned about.
Backport difficulty: LARGE, and low value for 5.15 (see next row).
Correctness impact: none (no bug is fixed by the rework itself)
RAM impact:         none
CPU impact:         small positive only for *userspace* (fewer syscalls: the doc says "This reduces
                    the number of syscalls, but more importantly this enables optimal
                    post-processing target selection strategy")
I/O impact:         neutral-to-positive, indirectly (better target selection)
Power impact:       unquantified
Flash wear impact:  neutral
Android relevance:  LOW. No Android 5.15 userspace is known to depend on the new grammar; ACK
                    5.15 does not carry it (only 6.12 does, because Android 16's mmd ships on
                    6.12+). Migrating the ABI on 5.15 would add maintenance surface with no
                    in-tree consumer, and `page_index=<N>` remains the documented 5.15 form.
Priority:           P3 / N-A for 5.15 (the user's own criterion: "if it is only a modern Linux
                    user interface without an Android 5.15 consumer → P2/P3/N-A")
Recommended action: do not port. Record the decision here so it is not relitigated. If a
                    *specific* 5.15 ROM component is ever found issuing `type=` or
                    `page_indexes=`, revisit with that evidence, not with the mainline doc.
```

### P2-3 · `huge_idle` / `incompressible` writeback modes

```
Feature:            additional writeback target-selection modes
Upstream status:    30226b69f876 ("zram: add a huge_idle writeback mode", 2022-04-29, in v5.14
                    upstream) and b46f9ea3cb35 ("zram: add incompressible writeback", 2022-11-09,
                    in v5.19), plus 77db7bb56bd7 (read_block_state incompressible flag)
Current 5.15 status: absent — `huge_idle` and `incompressible` are not accepted by 5.15's
                    `writeback_store()` (only `idle`/`huge`/`page_index=`), and the 5.15
                    `Documentation/admin-guide/blockdev/zram.rst` does not mention them.
Missing pieces:     mode parsing plus, for `incompressible`, the `ZRAM_INCOMPRESSIBLE` flag —
                    which this module's `zram_recompression` graft **already adds** to
                    `zram_drv.h` and `zram_free_page()`. So `incompressible` mode is closer to
                    being available than it looks.
Dependencies:       `huge_idle` = `HUGE|IDLE` bit test — trivial. `incompressible` needs a policy
                    decision (5.15 marks incompressible pages `ZRAM_HUGE` via `zs_huge_class_size()`,
                    the modern flag is a different predicate).
Conflict with ABK:  `ZRAM_INCOMPRESSIBLE` is defined by the recompression group; a mode group
                    must be ordered after it and must not redefine the flag.
Backport difficulty: small for `huge_idle`; small-medium for `incompressible`.
Correctness impact: none
RAM/CPU/I/O/Power:  none directly (it only changes which pages are selected)
Flash wear impact:  `huge_idle` narrows what gets written back (idle *and* huge), i.e. a policy knob
Android relevance:  low — no Android 5.15 user needs either mode; the documented 5.15 flow is
                    `idle` + `huge` driven by the ROM
Priority:           P3
Recommended action: skip for now; record as a cheap follow-up if a ROM asks for `huge_idle`.
```

---

## 5. N/A (with reasons, so they are not re-opened)

| Upstream item | commits | Why N/A on android13-5.15 |
|---|---|---|
| Writeback rewrite: drop `ZRAM_UNDER_WB` | `5e99893444a0`, with `330edc2bc059` (target selection) and `b967fa1ba72b` | Replaces the 5.15 handshake wholesale with post-processing slot lists (`zram_pp_ctl`, `zram_pp_slot`, `ZRAM_PP_SLOT`). Not a fix that can be lifted; it is a different design. The 5.15 race it removes is already covered by `idle_store()`'s `ZRAM_UNDER_WB` check. |
| Batched/pleroma of 2023 refactors | `a70aae12502b`, `79c744eeaa8e`, `889ae9169b45`, `a0b81ae7a4ff`, `fd45af53e220`, `1e9460d132cc`, `0cd97a0372f2` | Pure refactors of the read/`zram_bdev_*` paths against the pp-slot redesign. No behaviour delta worth the churn; would collide with the recompression graft's `zram_bvec_read` rewrite. |
| `zram: convert to bdev_open_by_dev()` | `eed993a09103` | Requires the 6.5 block-layer `bdev_open_by_dev()`/`struct bdev_handle` API; `abk_*_515` has no such helper and the 5.15 `blkdev_get_by_dev(bdev, mode, holder)` form works. |
| `bd_stat` / `writeback_stats` | lineage of `77db7bb56bd7` | 5.15 **already** ships `bd_stat_show()` in the modern 3-column 4 K form. Nothing missing. §1 table. |
| `ZRAM_TRACK_ENTRY_ACTIME` / `ZRAM_MULTI_COMP` / recompression | `f85219096648`, `d37da422edb0` | Already implemented in this module (Batch 4 / 10-1 / 11). Adding them again is the "don't duplicate ABK" red line. |
| `read_block_state()` bounds race | `391f057f44a5` | Same defect class as P0-1 but the file is `CONFIG_ZRAM_MEMORY_TRACKING` debugfs-only. Fold into P0-1's group **only if** that config is ever enabled; otherwise leave out to keep the group one-anchor. |
| zsmalloc non-writeback work (chain size, `zpdesc`/folio conversion, compaction, class selection) | `4ff93b292c`, `6260ae3583`, `7d2e1a6950`, `4610d35c14`, … | Category D per the audit: not a writeback dependency. The chain-size family is already grafted by this module. Only `82f91900c722` (new mapping API) is a *writeback* dependency, and only for P2-1. |
| `block: change exported IO accounting interface` | `5f0614a55e` | Block-layer API move to `disk_start_io_acct(bdev,...)`; belongs to the ABI suite / block-layer territory and is already resolved in 5.15's own shape. |
| io_uring / cgroup writeback / `blk_plug` writeback submission | — | No upstream zram consumer; the batching cover letter explicitly rejects blk-plug. |

---

## 6. Existing ABK Conflict

Two independent collision surfaces, and one of them is a *trap*.

**1. `ABK_ABI_PATCH_SUITE` — "compressed writeback" is a stub, and it is on the same file.**
Verified in `ABK_ABI_PATCH_SUITE/scripts/abk_feature_porting.py::patch_zram_compressed_writeback()`
(called at line 4235 of its main path):
* it only `ensure_contains()`-checks the 5.15 writeback surface (`ZRAM_WB`, `ZRAM_UNDER_WB`,
  `struct file *backing_dev`, `idle_store`, `writeback*_store`, `backing_dev_store`, the attrs);
* it then *inserts* `compressed_writeback_store/show`, `bool compressed_wb;`,
  `zram->compressed_wb = false;` and adds the attr to `zram_disk_attrs[]`;
* its marker is `/* ABK feature_porting: zram compressed writeback graft. */`;
* its self-declared scope: *"compressed_writeback stays a compatibility control-surface graft and
  does not absorb abk zram algorithm assets"*, and it never touches `zram_bvec_write()`,
  `writeback_store()` or any bio.
Consequences for this plan:
* `implementation_audit.py` for this module must not assert anything about `compressed_writeback`.
* If P2-1 is ever implemented, the real feature must *replace or absorb* that stub; a second
  `compressed_writeback` attr definition is a compile error.
* Confirmation of the existing policy (`docs/porting_policy.md`): the suite owns zram **writeback
  code**, this module would only flip the Kconfig symbol. The P0 candidates below are therefore a
  scope question the maintainer must answer explicitly (see §12, assumption A2).

**2. This module's own zram grafts share `writeback_store()` and `zram_drv.h`.**
* `zram_recompression` rewrites `__zram_bvec_read()` into a wrapper over a **new, ABK-authored**
  `zram_read_from_zspool()` (4-arg era: `(zram, page, index)` plus `comps[prio]`), changes
  `zram->comp` → `comps[...]`, adds `ZRAM_INCOMPRESSIBLE` and the priority bits to `flags`.
* `zram_secondary_comp` (Batch 10-4) and `zram_algo_lock` (Batch 11) add module params and a
  `late_initcall` in the same file.
Ordering rule derived from the audit: any new writeback group must be registered **after**
`zram_recompression` (so its anchors see the post-graft text) and must not re-anchor on text that
`zram_recompression` produces. The P0-1 anchor lives *above* the first `zram_recompression` edit in
`writeback_store()`, so it is safe, but `step_audit.py` must be extended to prove it.
* **Name collision to record now:** upstream's real `zram_read_from_zspool(zram, page, index)`
  (3-arg, dispatcher) is NOT the grafted one. Any future port of `b8d3ff7bb511`/`d38fab605c66`
  must reconcile the two, not add a second definition.

---

## 7. Android 5.15 Compatibility

| Dimension | Assessment |
|---|---|
| **KMI/KABI** | All P0/P1 items are internal to `drivers/block/zram/`. P0-1/P0-2/P1-1 touch no struct that Android exports. P1-3 adds fields to `struct zram` (private, `zram_drv.h`, not in any KMI list). **No `ANDROID_KABI_RESERVE` slot is needed anywhere in this plan.** |
| **GKI / family gate** | Unchanged: the groups run only for `android13-5.15`; other lineages stay `report_only`. |
| **Block layer API** | P0-1, P0-2, P1-1, P1-2 use only 5.15-present APIs. P1-3 needs `bio_init/bio_alloc/bio_chain/atomic_t/wait_queue_head_t/kfree_rcu` — all present in 5.15 (`bio_chain` already used by `read_from_bdev_async`). P2-1 needs the **missing** modern zsmalloc mapping API. |
| **MM / page API** | Verified present in v5.15: `memcpy_to_page`, `memzero_page`, `rounddown` (`include/linux/math.h`, included via `kernel.h`), `zs_map_object`. P2-1's blocker is therefore the zsmalloc mapping API, not the page helpers. P0/P1 need nothing beyond `alloc_page`/`kmap_atomic`. |
| **workqueue API** | P0/P1 none. P2-1 needs `system_highpri_wq` + `INIT_WORK` (present in 5.15); 5.15 already has the on-stack-work pattern in `read_from_bdev_sync()` guarded by `#if PAGE_SIZE != 4096`. |
| **zsmalloc API** | The only 5.15-missing zsmalloc symbol this plan needs is for P2-1 (`zs_obj_read_begin/end`). The module already adds `zs_lookup_class_index` for recompression; that is unrelated. |
| **ABI / behaviour change** | None. No P0/P1 item changes an existing sysfs semantic: `writeback` keeps `idle`/`huge`/`page_index=`, `writeback_limit` keeps its units, `bd_stat` keeps its layout. |
| **Code size** | P0-1+P0-2+P1-1+P1-2 (recommended) ≈ 30 lines across one file, 3–4 steps. P1-3 option (a) ≈ 200 lines. P2-1 ≈ 300+ lines plus a zsmalloc API. |
| **Dependency count** | Recommended batch: **0 new kernel APIs**. |
| **Maintenance cost** | Low for the batch, because it is written in 5.15's own idiom (`init_lock`, `scnprintf`, `bio_init`) rather than adopting the `dev_lock`/`guard()`/`sysfs_emit()` shape — deliberately, so that later upstream backports of the *same* logic do not have to be redesigned twice. |
| **Test difficulty** | Highest-risk part is P0-1's trigger (reset racing writeback). It is provable structurally (`step_audit`+`implementation_audit` anchors) but a *runtime* proof needs a device with `CONFIG_ZRAM_WRITEBACK=y` + a deliberate reset/writeback race. On the reference device writeback is not compiled in (`research/zram/vermeer_check_20260913/FINDINGS.md`), so the honest status is "structurally verified, runtime-unverified". |

---

## 8. Performance Impact

Reported per axis, and deliberately over-split (the point of the request):

| candidate | RAM savings | CPU | compression cost | I/O throughput | I/O latency | write amp. | flash wear | power | fragmentation | reclaim |
|---|---|---|---|---|---|---|---|---|---|---|
| P0-1 bounds fix | none | none | none | none | none | none | none | none | none | none — **pure correctness** |
| P0-2 teardown fix | none (frees a pinned `struct file`/bdev ref) | none | none | none | none | none | none | none | none | none |
| P1-1 `rounddown` | none | none | none | none | none | none | **prevents unbounded wear** on 16 K pages | none | none | none |
| P1-2 `cond_resched` | none | ~0 | none | none | **improves responsiveness** (bounded non-preemptible window) | none | none | positive (shorter IRQ-off/wakeup stalls) | none | helps reclaim/allocation latency |
| P1-3 batching | **costs** ~128 KiB + request array transiently at default 32 | slightly higher | none | **the stated purpose: higher** | **lower** (queue depth) | none measured | none measured | claimed positive, unmeasured | none | none |
| P2-1 compressed wb | none in zram (object stays resident until `ZRAM_WB` is set; then freed as in idle wb) | **lower on the writeback path** (no decompression for pages never read back) | decompression moves from writeback time to read-back time | same number of 4 K I/Os | read-back latency **higher** (async read-back needs a deferred workqueue step) | none | none (same blocks, smaller payload in a fixed-size block) | claimed positive, unmeasured | none | none |

Certainty labels:
* P0-1, P0-2, P1-1 **benefit is certain** (they are correctness, not performance).
* P1-2 benefit is **certain in kind** (a reschedule point in a long loop) though unmeasured here.
* P1-3, P2-1 benefits are **scenario-dependent**: they only matter when writeback is enabled and
  only pay off if the backing device / read-back pattern matches the assumption. Both are
  **theoretical in this project** because the audited device does not build writeback.

**Upstream benchmark data: none for any candidate in this plan.** The batching cover letter argues
from queue depth and the compressed-writeback cover letter argues from "potential CPU and battery
wastage", but neither series provides measured numbers, and the user's rule applies — no numbers
are invented here.

---

## 9. Dependency Graph

```
[already in 5.15] 013bf95a83ec  backing_dev interface
[already in 5.15] 1363d4662a0d  backing-device free-space bitmap
[already in 5.15] db8ffbd4e763  incompressible page writeback
[already in 5.15] 8e654f8fbff5  read page from backing device
[already in 5.15] bb416d18b850  writeback throttle  ──► writeback_limit / _enable
[already in 5.15] a939888ec38b  idle/huge writeback + ZRAM_IDLE/UNDER_WB
[already in 5.15] 0d8359620d9b  page writeback (`page_index=`)
[already in 5.15] be48c412f6eb  zero-sized backing device rejection   (came via ACK)
[already in 5.15] bd_stat_show  (modern 3-column form)

P0-1  zram_writeback_bounds
├── prerequisite: none
├── main:         894913e2d35c   (adapted: init_lock, 5.15 scan-loop shape)
├── bundled:      424d0e5828ad   (P1-2, cond_resched — same loop, same anchor region)
└── optional:     391f057f44a5   (only if ZRAM_MEMORY_TRACKING is enabled)

P0-2  zram_wb_teardown
├── prerequisite: be48c412f6eb   (already present — assert, do not re-add)
├── main:         74363ec674cb
└── CONSTRAINT:   the `if (!zram->table) return;` guard MUST land in the same
                  transaction as removing zram_reset_device()'s early return,
                  because zs_destroy_pool() is not NULL-tolerant in 5.15.

P1-1  zram_wb_limit_align
└── main: the `rounddown(val, PAGE_SIZE / 4096)` guard from mainline
          writeback_limit_store(); lineage bb416d18b850 → 1d69a3f8ae77

P1-3  writeback bio batching                       [DEFERRED, not recommended now]
├── prereq MISSING on 5.15: 330edc2bc059 pp-slot target selection
├── prereq MISSING on 5.15: 5e99893444a0 remove UNDER_WB
├── prereq MISSING on 5.15: b967fa1ba72b do-not-mark-idle
├── prereq MISSING on 5.15: ef932cd23b78 factor out ZRAM_HUGE write
├── prereq MISSING on 5.15: zram_read_from_zspool() in writeback (b8d3ff7bb511)
├── main:                f405066a1f0d + e828cccb72ed + e87ddea34567
└── must ship with:      bf62f69574b1 (UAF) + 3e8d8eb8d7f5 (blk_idx leak)
   → verdict: the dependency set is 5 series, not 1. If attempted, do it as a new
     implementation that borrows the design, not as a backport.

P2-1  compressed writeback                         [DEFERRED]
├── prereq MISSING: 82f91900c722 zram→new zsmalloc mapping API
├── prereq MISSING: zsmalloc zpdesc/folio mapping rewrite (7d2e1a6950, 4610d35c14)
├── prereq CONFLICT: ABK recompression already defines zram_read_from_zspool()
├── prereq CONFLICT: ABK_ABI_PATCH_SUITE already defines compressed_writeback/compressed_wb
├── main:               d38fab605c66 (+ 4c1d61389e8e, ba4c3698e696)
└── must ship with:     3bf1c285dc40 (zero trailing bytes)

P2-2  writeback ABI rework (6.16)                  [N/A for 5.15]
└── main: cf42d4cccf0d  — depends on the pp-slot target selection above;
          no Android 5.15 consumer identified.
```

---

## 10. Recommended Backport Order

Ordered by the user's ranking — correctness > stability > RAM > I/O > CPU > power >
feature completeness > debug — **not** by upstream date.

1. **P0-2 `zram_wb_teardown`** first, despite P0-1 being the more severe bug, because it is the
   only candidate that Android 6.1/6.6/6.12 already carry (so it is de-risked and pre-agreed
   upstream), it is 4+/5−, and its one dangerous interaction (`zs_destroy_pool(NULL)`) is fully
   understood and closed by the guard in the same hunk.
2. **P0-1 `zram_writeback_bounds` + P1-2 `cond_resched`** as one group: the bound move and the
   range-check move are the upstream `894913e2d35c` hunk re-anchored to `init_lock`; the
   `cond_resched()` rides along because it lands in the identical anchor region and splitting it
   would create two groups fighting over the same function.
3. **P1-1 `zram_wb_limit_align`** as its own one-line group so its presence/absence is visible in
   `implementation_audit.py`. Zero risk; silent value the day a 16 K-page GKI build appears.
4. **Stop.** Re-measure before going further: nothing else is recommended until
   `CONFIG_ZRAM_WRITEBACK` is actually enabled on a target (`ABK_515_DEFCONFIG_ROM=1`) and someone
   can show writeback throughput/latency numbers on that device. Batching (P1-3) and compressed
   writeback (P2-1) are optimisations for a path this project currently does not ship.
5. If/when step 4 produces evidence: batching (P1-3, option (a) as a *borrowed design* including
   `bf62f69574b1` + `3e8d8eb8d7f5` from day one) before compressed writeback, because batching has
   the smaller dependency set and is orthogonal to the recompression graft, whereas P2-1 collides
   with two ABK artefacts by name.
6. `huge_idle` (P3) only on explicit ROM demand.

### 10.1 Implementation shape (decision-complete)

Work lives in `scripts/abk_stable_core.py` (the fs/mm/cgroup child), consistent with
`zram_recompression` living there.

> **§10.1 is the design as analysed, not as shipped.** A3 in particular cannot land (it
> collides with `zram_recompression`'s anchor on the same function); the shipped shape and the
> full rationale are in **§10.5**. Groups B and C shipped exactly as written here.

**Group A — `zram_wb_teardown`** (P0-2), `files=["drivers/block/zram/zram_drv.c"]`:

| step | anchor (5.15.194 text) | replacement | required |
|---|---|---|---|
| A1 | `zram_meta_free()` head: `\tsize_t num_pages = disksize >> PAGE_SHIFT;\n\tsize_t index;\n\n\t/* Free all pages` | same + `\tif (!zram->table)\n\t\treturn;\n` | yes |
| A2 | `\tzs_destroy_pool(zram->mem_pool);\n\tvfree(zram->table);\n}` | + `\tzram->table = NULL;` | yes |
| A3 | `zram_reset_device()`: `\tzram->limit_pages = 0;\n\n\tif (!init_done(zram)) {\n\t\tup_write(&zram->init_lock);\n\t\treturn;\n\t}\n\n` | `\tzram->limit_pages = 0;\n\n` | **superseded — see §10.5** |

A3 as written is textually a *deletion*, which is exactly `step_audit.py` trap 1 (a `new` block
that is a prefix of `old`, or pre-exists) — the `new` block must be re-anchored with unique
trailing context so the step is provably `applied`, not `already_present`. Re-anchoring it that
way is what then collides with `zram_recompression`, whose anchor is the function's whole
pristine body; the shipped group deletes nothing and adds `reset_bdev(zram);` to
`zram_remove()` instead (§10.5). A1/A2 ship as written. Mark the inserted lines with an
`/* ABK stable_515_backport: ... */` comment so second-pass idempotency holds and rollback is
meaningful.

**Group B — `zram_writeback_bounds`** (P0-1 + P1-2), same single file:

| step | change | required |
|---|---|---|
| B1 | `writeback_store()`: `unsigned long nr_pages = zram->disksize >> PAGE_SHIFT;` → `unsigned long nr_pages;` | yes |
| B2 | move the `index >= nr_pages` guard out of the pre-lock `page_index=` parse into the post-lock region (assign `nr_pages = zram->disksize >> PAGE_SHIFT;` and validate `index` immediately after the `init_done(zram)` check) | yes |
| B3 | `cond_resched();` at the end of the loop body, before the `next:` label | yes |

B1/B2 are a *single logical change split in two steps*; they must be `required` together so a
partial tree can never keep the stale bound while dropping the old guard.

**Group C — `zram_wb_limit_align`** (P1-1), same file:
`writeback_limit_store()`: after `if (kstrtoull(buf, 10, &val))\n\t\treturn ret;` insert
`\n\tval = rounddown(val, PAGE_SIZE / 4096);\n` plus the upstream explanatory comment, before
`down_read(&zram->init_lock);`.

Explicit non-steps: do **not** touch `writeback_limit`'s RW→WO attribute type (a modern cosmetic
delta with no 5.15 consumer), do **not** adopt `sysfs_emit()`/`dev_lock`/`guard()`, and do **not**
add `compressed_writeback` or `writeback_batch_size` attributes.

### 10.2 Registration and bookkeeping (must be done in the same commit)

* `scripts/abk_stable_core.py`: append the three `PatchGroup(...)` records after `zram_recompression`
  (and after `zram_secondary_comp`/`zram_algo_lock`, which are imported `PatchGroup`s appended to
  `PATCH_GROUPS`). Order is load-bearing: Group B anchors inside `writeback_store()`, whose callee
  `zram_recompression` rewrites.
* `tests/sublevel_matrix.py`: `GROUP_COUNTS["stable_backport_core"] 24 → 27` (**as shipped**).
  `PRE_APPLIED` stayed unchanged for 167/178/194/216 and no `KNOWN_DEBT` row was needed — all
  three groups really apply on a pristine tree. (The draft expected a row for the
  zero-sized-device hunk; §10.5 records why that hunk is not a dependency after all.) If a group
  is later made to report `already_present` on a baseline, it must be recorded there instead, and
  `stable_5_15_test.py` asserts the two stay consistent.
* `tests/implementation_audit.py` `REQUIRED_CONTENT` (**as shipped**): `core:zram_wb_teardown`
  (`"if (!zram->table)\n\t\treturn;"`, `"zram->table = NULL;"`, and the `zram_remove()` marker +
  `reset_bdev` call), `core:zram_writeback_bounds` (the `ABK stable_515_backport: 894913e2d35c`
  markers, `"\tunsigned long nr_pages;"` + the post-lock `nr_pages = ...` / `index >= nr_pages`
  pair, `cond_resched()`), `core:zram_wb_limit_align`
  (`rounddown(val, PAGE_SIZE / 4096)`). The needles are marker-prefixed where the bare C also
  exists in the *upstream* 2026 shape, so a baseline that ever backports the commit itself still
  satisfies the audit instead of silently passing on the wrong text. A `REQUIRED_ABSENT` entry
  asserting that this module introduces **no** `compressed_writeback` / `writeback_batch_size`
  symbol is still outstanding (suite boundary, §6).
* `tests/step_audit.py`: no new file paths (all three groups touch only `zram_drv.c`, already in
  the list).
* `tests/smoke.sh`: no new grep assertion is required if the groups' `required` steps already make a
  silent no-op impossible; if a marker is added, gate it on `sublevel_matrix.applies()`.
* `docs/porting_policy.md` + `plan.md`: record the P0/P1 decisions and the P3/N-A verdicts from §4/§5
  so the ABI rework is not re-litigated; tick the existing `zram_writeback_limit_audit` backlog line
  (plan.md:321) with the §1.2 answer (5.15 **does** carry `writeback_limit`; the missing piece is the
  alignment guard alone).
* `module.conf`: bump `ABK_MODULE_VERSION` and `ABK_MODULE_SET_VERSION` (**as shipped:**
  `0.18.0` → `0.19.0`).
* `research/upstream-zram/`: keep the 68 `.patch` files (repo convention stores upstream patches
  under `research/`; `patches/` at the repo root stays empty by design).
* ABK CI: the compile is the only real gate for the A3 deletion and the B2 code move — keep the CI
  run in the loop and read `zram_drv.o`'s warnings.

### 10.3 Verification (extend, do not replace, the documented order)

```bash
python3 -m py_compile scripts/*.py tests/*.py
bash -n setup.sh scripts/*.sh tests/*.sh tools/*.sh ksu/*/*.sh
python3 tests/stable_5_15_test.py                 # GROUP_COUNTS assertion must pass
python3 tests/step_audit.py  build/abk-trees/167  # and 178, 194, 216
python3 tests/implementation_audit.py build/abk-trees/167   # and 178, 194, 216
bash tests/smoke.sh build/abk-trees/194           # 2-pass idempotency + rollback
```
Plus a per-baseline dry-run of the child (no writes) as documented in `docs/group_recipe.md`, and
a two-pass idempotency check that each new group's *second* run reports `already_present` (the
`/* ABK stable_515_backport: ... */` markers are the anchors that make this true).

### 10.4 Acceptance criteria

1. On pristine 167/178/194/216 trees all three groups report `applied` (never
   `already_present`, never `blocked_by_*`), and their steps' `{`/`}`//`#if` balance is unchanged.
2. A second pass over each tree is byte-identical, and `abk_rollback.sh --apply` restores every
   touched file from `<file>.abk-orig`.
3. `implementation_audit.py`'s new `forbidden` assertions hold: this module adds no
   `compressed_writeback` / `writeback_batch_size` symbol (suite/scope boundary intact).
4. ABK CI compiles the resulting `drivers/block/zram/zram_drv.o` with `CONFIG_ZRAM_WRITEBACK=y`
   and `CONFIG_ZRAM_MULTI_COMP=y` (i.e. with the module's own recompression graft active) — the
   ordering constraint from §6 is only truly proven here.
5. `CONFIG_ZRAM_WRITEBACK=n` must still compile (every new statement except A1's guard sits inside
   `#ifdef CONFIG_ZRAM_WRITEBACK` regions; A1/A2/A3 are unconditional and must not reference
   writeback-only fields).

---

## 11. Items explicitly marked "uncertain"

Following the user's rule 10, these are *not* asserted as benefits:

* **P1-3 batching throughput/latency numbers** — no upstream benchmark exists; the gain is
  plausible from queue depth alone, but unmeasured on UFS/eMMC and irrelevant until writeback is
  enabled on the device.
* **P2-1 compressed writeback CPU/power saving** — the cover letter asserts "potential CPU and
  battery wastage" without measurement. Whether it is a net win depends entirely on the
  writeback-then-read-back ratio, which nobody has published for Android.
* **P1-1 `rounddown` reachability** — the defect is real (arithmetic, visible in the code), but on
  the audited 4 K-page GKI builds it cannot trigger. It is insurance, so its *current* impact on
  this project is "none; prevents a future one".
* **P0-1 triggerability on the reference device** — writeback is not compiled in there, so the bug
  cannot fire today; it becomes live the moment `ABK_515_DEFCONFIG_ROM=1` is used. Its severity
  (OOB access) is certain from the code; its *probability* on this project is not quantified.
* **Any claim that 5.15 is missing `writeback_limit`/`bd_stat`** — the opposite is verified true,
  and `docs/survey_popsicle_w_611.md`'s suspicion ("需核对 android13-5.15 是否自带") resolves to
  "yes, it has them".
* **API availability** — not uncertain any more: `rounddown`, `memcpy_to_page`, `memzero_page`,
  `bio_chain`, `bio_inc_remaining` were each confirmed against the real v5.15 headers during this
  audit. What remains genuinely unproven is only behavioural (the reachability/benefit items
  above) and the 5.15 `zs_map_object(ZS_MM_RO)` cross-page read semantics for P2-1.

## 12. Assumptions a reviewer should confirm

* **A1** — Scope stays "writeback only": general zram/zsmalloc optimisations are out of this plan
  even where they touch the same file (§5 records why each was excluded).
* **A2** — The maintainer accepts that these P0 fix-groups edit `drivers/block/zram/zram_drv.c`,
  which `docs/porting_policy.md` describes as the `ABK_ABI_PATCH_SUITE`'s "zram writeback code"
  territory. The audit shows the suite's actual writeback footprint is a stub plus a config
  symbol, so there is no mechanical overlap — but the *policy* boundary is the maintainer's call,
  and the answer determines whether these groups land here or are handed to the suite.
* **A3** — `docs/group_recipe.md`'s "one `PatchGroup` per upstream commit" gives way to the
  grouping in §10.1 in two places (P1-2 bundled into the P0-1 group; P0-1's two edits as two steps
  of one group). This is deliberate: the steps share anchor regions and `replace_once`'s
  check-`new`-first behaviour would make separate groups collide (`step_audit.py` trap 2).
* **A4** — No new `ANDROID_KABI_RESERVE` slot is consumed, because no public struct changes.

---

## 10.5 What actually shipped (Batch 14, module 0.19.0) — and the one design change

Three groups in `scripts/batch14_core_zram_writeback.py`, registered at the end of
`stable_backport_core`'s `PATCH_GROUPS`, all confined to `drivers/block/zram/zram_drv.c`:

| group | upstream | steps | 167 | 178 | 194 | 216 |
|---|---|---|---|---|---|---|
| `zram_wb_teardown` | `74363ec674cb` | 3 | applied | applied | applied | applied |
| `zram_writeback_bounds` | `894913e2d35c` + `424d0e5828ad` | 4 | applied | applied | applied | applied |
| `zram_wb_limit_align` | mainline `writeback_limit_store()` guard | 1 | applied | applied | applied | applied |

All four baselines pass `tests/step_audit.py`, `tests/implementation_audit.py` and
`tests/smoke.sh`; `tests/sublevel_matrix.py` needed only `GROUP_COUNTS["stable_backport_core"]`
`24 → 27` (no `PRE_APPLIED` and no `KNOWN_DEBT` row is needed for any of the three).

### The design change a future reader must not "fix" back

**P0-2 is not implemented by deleting `zram_reset_device()`'s early return.** §10.1's A3 step
does exactly that, and it cannot work on this baseline. The reason is a *text-anchor* conflict,
not a semantic one:

* `zram_recompression` (registered **earlier**) rewrites `zram_reset_device()` and anchors on
  the function's **entire pristine body**, early return included.
* Any teardown step that deletes that early return changes the text the recompression anchor
  matches, so on the second pass `zram_recompression` reports `blocked_by_shape` and the whole
  child fails `step_audit` — the files stay byte-identical, but the audit is right that the
  group is no longer provably idempotent. Both directions were tried (deleting in the teardown
  group; keeping the guard and marking the spot in the recompression group) and each breaks the
  other.

The shipped fix therefore closes the identical leak **in `zram_remove()`**, which is the only
path that destroys an *uninitialised* zram device:

```c
	zram_reset_device(zram);
	/* ABK stable_515_backport: 74363ec674cb ... */
	reset_bdev(zram);
```

`reset_bdev()` releases the exclusive `blkdev_put` + `filp_close` holder when one is attached
and is a no-op otherwise; it is also an inline stub when `CONFIG_ZRAM_WRITEBACK=n`, so the call
is unconditional. Semantics are the same as upstream's: the early return only skipped
`reset_bdev()` for a device that never reached `disksize`, and `zram_remove()` is the path that
reaches such a device. The two `zram_meta_free()` steps (NULL-table guard, `zram->table = NULL`)
ship as analysed in §10.1 A1/A2 — they are what make the teardown safe if that function is ever
entered uninitialised, and they touch no text any other group produces.

### The `be48c412f6eb` question (5.15.167) — resolved by *not* porting it

§10.2 assumed "the zero-sized-device hunk is already there" on every baseline. It is not:
`be48c412f6eb` landed upstream in **5.15.168**, so the 2024-11 baseline (5.15.167) is the one
audited tree without it. A guard-only group was written and then removed, because its
replacement text necessarily *contains* the pristine guard block (the marker has to sit next to
the code it annotates) — so on 178/194/216, where the guard is upstream, the group cannot
distinguish "the baseline already carries this" from "an earlier step added it" and reports
`applied` where the matrix expects `already_present`. There is no text-only discriminator.

It also turned out to be unnecessary: the shipped teardown fix does not depend on the guard at
all (it is not editable input to `zram_remove()`), so 5.15.167 is covered like every other
baseline and no `KNOWN_DEBT` row is required. The guard remains a real, small robustness gap on
that one deprecated baseline; record it as a candidate for the suite if it ever matters.

### Traps hit, for `docs/group_recipe.md`'s list

1. **A replacement that ends with its own anchor.** The guard group's first draft put the ABK
   comment *before* the guard block, so the replacement was `"/* marker */\n" + old` and the
   edited file still contained `old`. `str.count(old)` therefore stayed 1 after a successful
   edit and the idempotency probe read as "never landed". `new.endswith(old)` / `old in new` is
   the cheap pre-flight check; §10.3's acceptance criteria should assert it for every step.
2. **Trap 1's mirror: an anchor that is a prefix of its own replacement.** A pure-deletion step
   (`new` = a prefix of `old`) is reported `already_present` by `replace_once`, which tests
   `new` first, while the edit never lands. Both `_A_VFREE_NEW` and `_A_REMOVE_NEW` are supersets
   of their anchors for this reason.
3. **"Unreachable" preconditions that are reachable.** The teardown group briefly probed for
   `be48c412f6eb` and gated on it; that made its outcome depend on an *earlier* group's output
   and produced a spurious `blocked_by_shape` once the recompression group started inserting
   its marker comment in the same function. Shape probes should assert *this* group's own
   anchors, not a neighbour's.

