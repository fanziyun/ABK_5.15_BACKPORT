# EROFS upstream candidate survey (android13-5.15)

Status: `docs/survey_erofs_upstream.md` · Target: android13-5.15 GKI
(android13-5.15-lts; Batch 44 dropped .167/.178/.194) · **Research + one landed
group.** §4.1 landed as `erofs_readmore_past_eof` in **Batch 46 (v0.49.0)**
(`CHANGELOG.md#batch-46`); every other candidate below is still a survey record
with **no `PatchGroup` registered, no code change, no version bump**.
Companion to `docs/survey_popsicle_w_611.md`, which
records that the Xiaomi `popsicle-w-oss` branch carries **no `fs/` at all**, so
it is not a source for anything here either.

`fs/erofs` was never inside this module's red lines. Batch 43 retired the
`fs/f2fs` / `drivers/scsi/ufs` exclusion; EROFS did not need it -- the module's
first EROFS group landed in Batch 40.

## 0. EROFS does not touch /data

Verified, not assumed. On a GKI Android device EROFS mounts the **read-only**
system partitions (`/system`, `/product`, `/vendor`, `/odm`, `/system_ext`);
`/data` is **f2fs**, and `/data`'s compression is f2fs-internal and unrelated.
So every candidate below buys **cold app launch and APK / dex / oat / lib reads
off the system partitions**, and **nothing on `/data`**.

The `/data` read lever is f2fs itself (Batch 43 opened that path, but per
`docs/survey_popsicle_w_611.md` the Xiaomi branch has no f2fs to harvest, so the
source has to be upstream) plus the `mm/` readahead / `block/elevator.c` work
this repo already carries (Batch 9-1 `dynamic_readahead_lowmem`, Batch 40,
Batch 42). Everything in this document answers "what EROFS can still give",
which is a system-partition question, not a `/data` question.

## 1. Method and what is verified

Upstream commit subjects, authors, dates and file lists came from
`https://api.github.com/repos/torvalds/linux/commits/<sha>`; the verbatim patch
of the top candidate from `<sha>.patch`. Baseline shapes come from the reference
trees in `build/abk-trees/<sublevel>`, **re-fetched 2026-09-26** -- the copies
on disk predated `FETCH_FILES`' `fs/erofs` entries and contained no `fs/erofs/`
at all, which is why an earlier pass could only assert "`fs/` is not in the
tree" without counting anything.

After the re-fetch: 92 files per tree; `tests/step_audit.py` audits 91 groups
on 216 with a byte-identical second pass; `tests/implementation_audit.py` OK on
216; `tests/smoke.sh` OK on 216 (two passes, rollback verified byte-identical);
and `erofs_readahead_relaxed_gfp` reports `applied` on the supported baseline.

**Verified directly**: anchor counts, function signatures, `inode` scope, the
defconfig lines, per-file baseline equality, and all 12 upstream commit
ids/subjects/authors/dates.

**Release versions were settled by containment, not by author date.** Every
version in this document comes from the GitHub compare API
(`/compare/<tag>...<sha>`), reading `status` / `ahead_by` / `behind_by` to decide
whether the commit is an ancestor of a tag. An author date inside a merge window
is not evidence: `936aa701d82d` (author 2023-07-10) is ancestor of v6.5 and ahead
of v6.4; `e080a26725fb` (2024-08-19) is ahead of v6.10 and ancestor of v6.11;
`2349d2fa02db` (2024-09-05) is diverged from v6.11 and ancestor of v6.12;
`d9281660ff3f` (2024-01-26) is already an ancestor of v6.8.

**One discrepancy found in this repo's own record, left for the maintainer.**
`scripts/batch40_core_erofs_readahead.py` and the Batch 40 changelog describe
`d9281660ff3f` as **v6.9**; the containment test says **v6.8** (it is already an
ancestor of v6.8, and its author date is after v6.7's release). This predicate
of this survey (Batch 40 being landed, its group name, its anchors) is
unaffected, but the version attribution in the landed record should be checked
before it is relied on.

**Rests on the research pass, not re-verified here**: any per-baseline claim
about files `FETCH_FILES` does not carry (`super.c`, `sysfs.c`, `utils.c`,
`internal.h`, `dir.c`, and everything else in the 22-file `fs/erofs`). Those are
flagged where they matter (section 6).

## 2. The supported baseline: one shape group

This section was written before Batch 44, when `.167/.178/.194` were also
supported. Those three are identical to each other and differ from `lts`, so the
table used to be a two-shape-group story. **Batch 44 dropped them, so the
support-relevant column is `216` alone**; the pre-lts column is kept only as
provenance, because the deltas below are what a `latest`-based evaluator would
also hit and because the five-file comparison is how these commits were pinned
in the first place.

| file | 167 / 178 / 194 | **216 (supported)** |
|---|---|---|
| `compress.h` | same as 216 | — |
| `decompressor_lzma.c` | same as 216 | — |
| `decompressor.c` | **differs** | — |
| `zdata.c` | **differs** | — |
| `zdata.h` | **differs** | — |

The three deltas, all present on the supported baseline:

| file | delta | commit | in scope? |
|---|---|---|---|
| `zdata.h:180` | `min_t` -> `MIN_T` in `Z_EROFS_VMAP_ONSTACK_PAGES` | 5.15.y stable `3d1169785a9c` | no -- pure rename |
| `decompressor.c` | unsigned-underflow guard in `z_erofs_lz4_handle_overlap()` | 5.15.y stable `778acd52e949` | no -- security |
| `zdata.c` | `memalloc_noio_save/restore()` around `z_erofs_decompressqueue_work()` | mainline `c23df30915f8` / `d6565ea662e1` | no -- correctness |

**Provenance trap on the first two rows: neither is a mainline commit.** Mainline
deleted `fs/erofs/zdata.h` outright in v6.3 (`a9a94d937334` "erofs: move zdata.h
into zdata.c", confirmed: `removed fs/erofs/zdata.h +0 -177`), so no mainline
commit can touch that path -- `3d1169785a9c` resolves against `torvalds/linux`
only because it is a **5.15.y stable backport**, and its GitHub API file diff is
computed against its 5.15.y parent. Confirmed by compare API: `v6.18...
3d1169785a9c` reports `diverged`, i.e. the commit is not an ancestor of mainline
v6.18 at all. The same applies to `778acd52e949`. Do not cite either as upstream.

Anchoring note: `MIN_T` **is** available on the supported baseline (other fetched
files use it too), so the delta is a cosmetic rename local to `zdata.h:180`
rather than a missing macro. It still means no group may anchor across
`Z_EROFS_VMAP_ONSTACK_PAGES` on `lts`.

**Consequence: a new EROFS group needs one shape probe, on `216`.** The
two-probe requirement this section used to state died with the three release
baselines; there is no second shape to probe on the supported target.

## 3. What the supported baseline already carries (verified)

| feature | lts (216) | evidence |
|---|---|---|
| `EROFS_FS_PCPU_KTHREAD` (+ `HIPRI`) | y | `CONFIG_EROFS_FS_PCPU_KTHREAD=y` / `_HIPRI=y` in the `gki_defconfig` |
| LZ4 / LZ4HC, microLZMA | y | `decompressor.c` / `decompressor_lzma.c` |
| big pcluster / multi-block | y | `Z_EROFS_PCLUSTER_FULL_LENGTH`, `pcluster_pool[]` in `zdata.c` |
| tail-packing (inline last block) | y | `EROFS_INODE_FLAT_INLINE` path in `zdata.c` |
| uncompressed direct I/O via iomap | y | `erofs_readahead = iomap_readahead(rac, &erofs_iomap_ops)` |
| **large folio** | n | `mapping_set_large_folios` absent; upstream is 6.7+ |
| ZSTD / DEFLATE | n | no `decompressor_zstd.c` / `_deflate.c`; upstream 6.10 / 6.6 |
| compressed fragments, interlaced | n | upstream 6.1 series |
| fscache / `EROFS_FS_ONDEMAND` | n | upstream 6.1/6.2 |
| file-backed mounts | n | upstream 6.12/6.13 -- already a recorded exclusion (`docs/batch8_long_term.md`) |

`PCPU_KTHREAD=y` in the defconfig matters: the per-CPU decompression
worker pool is **live on device**, not dead config, so a change to its bring-up
has a real effect. It also means upstream's later "use HIPRI by default"
(`cf7f2732b4b8`) is a no-op here -- the defconfig already names it.

The read path is effectively frozen in this family: across 167 -> 216 the only
upstream sync that arrived was three correctness/security fixes plus one rename
(section 2). That is the headline finding: **the portable read-path
optimization upstream already handed out is the one Batch 40 landed.**

## 4. Candidates, cheapest first

All commit ids below were checked against `torvalds/linux`; subject, author and
date are as listed.

### 4.1 `936aa701d82d` -- avoid useless loops in `z_erofs_pcluster_readmore()` past EOF (v6.5, 2023-07-10, Chunhai Guo / vivo) -- **LANDED, Batch 46**

Verbatim one-line change, `fs/erofs/zdata.c`:

```c
-	while (cur >= end) {
+	while ((cur >= end) && (cur < i_size_read(inode))) {
```

The commit message frames it strictly as an efficiency problem -- reading at a
large offset past EOF loops 4,691,368 times and takes about 27 seconds
(`offset = 19217289215`, `inode_size = 1442672`). **No CVE, no security
wording**, so it is in scope under the module's "optimizations only" rule.

Why it is the cheapest possible EROFS group:

- `while (cur >= end)` occurs **exactly once** in `zdata.c` on the supported
  baseline (counted);
- the signature
  `z_erofs_pcluster_readmore(struct z_erofs_decompress_frontend *f, struct readahead_control *rac, erofs_off_t end, struct page **pagepool, bool backmost)`
  is the supported shape (checked);
- `inode` is already in scope -- `f->inode` is assigned once above the anchor,
  and `inode->i_mapping` is used immediately after it (`zdata.c:1560`);
- `i_size_read` is not otherwise present in the function, so no re-declaration
  and no collision;
- upstream-shape edit, so **no `/* ABK stable_515_backport: */` marker** -- a
  baseline that later carries the commit stays byte-identical;
- one file, one required step, and that file is already in `FETCH_FILES`.

**Honest caveat.** The trigger is a read beyond EOF at a very large page
offset. It is safe and nearly free, but the everyday access pattern that hits
it is rare, so no visible gain should be claimed. It is a robustness win, not a
measured speedup.

Trap to respect: anchor on the `while` line alone. The `backmost` branch above
it differs from upstream in shape.

### 4.2 `12bf25d1659b` -- lazily initialize per-CPU workers and CPU hotplug hooks (v6.16, 2025-05-06, Sandeep Dhavale / Google)

The only candidate here from a Google author, which makes it a natural fit for
a GKI tree. What it buys: stop starting and stopping the per-CPU decompression
workers on every CPU hotplug / suspend-resume, and stop allocating them until
the first EROFS mount. Measurable on suspend/resume, not on throughput.

Rename adaptation required, so this is **not** a verbatim paste:

- upstream renamed `..._zip_subsystem` -> `..._subsystem` in **v6.11** by
  `5a7cce827ee9` "erofs: refine `z_erofs_{init,exit}_subsystem()`"
  (2024-07-09) -- not before it. Verified by symbol probe across the baseline
  and v6.9/v6.10 archives: `z_erofs_init_zip_subsystem` is still the name there,
  and `z_erofs_init_subsystem` appears from v6.11 onward.
- **`z_erofs_init_super()` is introduced by this very commit**, it is not a
  pre-existing upstream helper that 5.15 merely lacks. So the graft cannot "call
  the existing helper" -- it has to invent the mount-time entry point itself.
  The baseline brings the entire subsystem up in `erofs_module_init()`
  (`fs/erofs/super.c`), so `z_erofs_init_pcpu_workers(sb)` has to be wired into
  `erofs_fill_super()`'s success path instead, with the two calls and their
  `err_*` labels deleted from `z_erofs_init_zip_subsystem()`.

Traps recorded: (a) the `#else` inline stubs must move **inside** the
`CONFIG_EROFS_FS_PCPU_KTHREAD` block, otherwise `z_erofs_init_pcpu_workers()`
references the undeclared `erofs_percpu_workers_initialized` when the config is
off; (b) `z_erofs_destroy_pcpu_workers()` must be the **first** statement of
`z_erofs_exit_zip_subsystem()`, before `destroy_workqueue()`; (c) this lets an
EROFS mount fail where it previously could not -- keep every step `required` so
a miss writes nothing.

Needs `fs/erofs/super.c`, which `FETCH_FILES` does not carry (section 6).

### 4.3 `99486c511f68` + `1a2180f6859c` -- PSI memstall accounting for the compressed address space (v6.1, Christoph Hellwig / 2022-09-15) + fix (v6.13, 2024-11-27)

Closes EROFS's blind spot in `tasks[NR_MEMSTALL]`, the signal LMKD and
`psi_monitor` consume. Verified prerequisites: `psi_memstall_enter/leave()` are
declared in `include/linux/psi.h` on the supported baseline, `CONFIG_PSI=y` in the
four defconfigs, and `mm/filemap.c` calls `psi_memstall_enter()` on the normal
path -- the gap is confined to EROFS's compressed address space.

**This is the most valuable candidate and also the only one that can leak a
memory-stall window.** Two traps:

1. **API convention.** The 5.15 signature is
   `(sb, f, pagepool, fgq, force_fg)` and allocates bios with the
   **two-argument** `bio_alloc(GFP_NOIO, BIO_MAX_VECS)` + `bio_set_dev()`, not
   upstream's `bio_alloc(mdev.m_bdev, BIO_MAX_VECS, REQ_OP_READ, GFP_NOIO)`.
   Anchor the `PageWorkingset()` insert on `if (!bio) {`, **not** on
   `submit_bio_retry:` -- on 5.15 that label contains no `bio_alloc`.
2. **Leave-site ordering.** `1a2180f6859c` moves `psi_memstall_leave()` out of
   `if (bio)` to after the `do`/`while`, but 5.15 has an earlier
   `if (!*force_fg && !nr_bios) { kvfree(); return; }` return. `kvfree()` is
   non-blocking, yet skipping the leave leaks `pflags` and every later
   `psi_memstall_enter()` then opens a stall window that never closes. Put the
   `leave()` **before** that `return`.

This is the `hugepage_vma_revalidate()` class of trap from `AGENTS.md`: it
compiles clean and behaves wrong. If landed, pin the `psi_memstall_leave()`
sites in `implementation_audit.py`.

### 4.4 `97cf5d53b481` -- get rid of unneeded `GFP_NOFS` (v6.8, 2024-01-24, Jingbo Xu / alibaba)

Two of the four hunks are relevant on 5.15 (the others touch `fs/erofs/fscache.c`,
which does not exist here): `z_erofs_alloc_pcluster()` / `erofs_insert_workgroup()`
stop holding `GFP_NOFS`, letting reclaim make progress. Micro change.

### 4.5 `db80b98305f7` -- sysfs node to drop internal caches (v6.13, 2024-11-13)

Value-1 half only. The baseline already has
`erofs_try_to_free_all_cached_pages()`, so the sysfs wiring is a small graft.
Needs `fs/erofs/sysfs.c` and `fs/erofs/internal.h`, neither in `FETCH_FILES`.
Rated **partially portable**: a user-facing knob with no clear consumer is
outside this module's "no feature without a product consumer" instinct, so it
is listed last.

## 5. Excluded, with reasons

| candidate | verdict | reason |
|---|---|---|
| `7c35de4df105` ZSTD (v6.10), `ffa09b3bd024` DEFLATE (v6.6) | scope | new decompressors; a shipped ROM's `mkfs.erofs` will not emit them. This is the recorded P2/P3 `erofs_large_folio_zstd` |
| `ce529cc25b18` (v6.2), `e080a26725fb` (v6.11) large folios | needs infra | `mapping_set_large_folios` is 6.7; needs the `large_folio_mthp_substrate` project first |
| `0ee3a0d59e00` sub-page compressed blocks (v6.8) | needs infra | depends on the folio chain |
| `b15b2e307c3a` + `5c2a64252c5d` + `fdffc091e6f9` fragments / interlaced (v6.1) | needs infra | `EROFS_MAP_PARTIAL_REF`, `fe->pcl->partial`, multibases all absent |
| `e7933278b442` inplace-decompression success rate (v6.1) | needs infra | depends on `5c2a64252c5d` |
| `2349d2fa02db` sunset NOFAILs (v6.12) | needs infra | the operative hunk (`pcl->besteffort`) is already Batch 40's target form; the rest needs the 6.x `compressed_bvecs` / `io->eio` substrate. Version matters: it is v6.12, the same neighbourhood as `db80b98305f7` and `fb176750266a`, not the 6.13 series |
| `0f6273ab4637` LZ4 reserved pool (v6.10) | excluded | already adjudicated in Batch 40: needs v6.8's global `z_erofs_gbufpool` |
| `8b465fecc35a` flatdev, `6a318ccd7e08` long xattr prefixes, `0f24e3c05afe` 48-bit layout, `c36ec00d7f67` fsoffset | scope | on-disk-format features; only useful if the vendor's `mkfs.erofs` emits them |
| `b4a29efc5146` DEFLATE via Intel QAT (v6.16) | excluded | vendor hardware |
| `770c8d55c428` `lib/iov_iter` slab folio refcount | excluded | 5.15 has neither `page_folio()` nor `folio_test_slab()` |
| pulling a newer `lib/lz4` | excluded | 6 commits between v5.15 and v7.2, and upstream says "No logic changes" -- erofs decompression lives in `fs/erofs/`, not in zram's crypto layer |

## 6. `FETCH_FILES` gaps -- a prerequisite, not a detail

`tests/fetch_sublevel_tree.sh` carries exactly five `fs/erofs` paths
(`compress.h`, `decompressor.c`, `decompressor_lzma.c`, `zdata.c`, `zdata.h`).
A group anchored in a sixth file fails the tree-level audits for a missing tree
file, which is the same failure mode as a stale fixture and easy to misdiagnose.

| candidate | extra files needed in `FETCH_FILES` |
|---|---|
| `936aa701d82d` (4.1) | **none** |
| `99486c511f68` + `1a2180f6859c` (4.3) | none beyond the five |
| `12bf25d1659b` (4.2) | `fs/erofs/super.c`, `fs/erofs/internal.h` |
| `97cf5d53b481` (4.4) | `fs/erofs/utils.c`, `fs/erofs/internal.h` |
| `db80b98305f7` (4.5) | `fs/erofs/sysfs.c`, `fs/erofs/internal.h` |

After growing `FETCH_FILES`, re-run `tools/fetch_all_trees.sh` (it gap-fills and
is re-runnable, about 2 rounds / 3 minutes).

## 7. Recommendation

**Landed: `936aa701d82d` (4.1)** — Batch 46 / v0.49.0, group
`erofs_readmore_past_eof`. It went in alone, as this section recommended: one
file, one line, a unique anchor on the supported baseline, zero config
dependency, the file already in `FETCH_FILES`, and the same author and the same
read path as Batch 40. Treated as a robustness win and no speedup claimed.

Two things were settled while landing it that this document did not yet record,
and both change what a future pass has to check:

* **The function was diffed against tag v6.5, not just the anchor.** Three
  differences exist (signature, `backmost` branch, loop-body control flow) and
  none of them is the loop condition — which is what makes upstream's reasoning
  transfer. The one that bites is the `backmost` branch: v6.5 recomputes `end`
  from `f->headoffset` and expands with `headoffset` rather than
  `readahead_pos(rac)`. So the anchor starts at
  `cur = map->m_la + map->m_llen - 1;` — those three lines are byte-identical on
  both sides, the branch above them is not.
* **`i_size_read()` availability was checked through the include chain**
  (`zdata.c` → `zdata.h` → `internal.h` → `<linux/fs.h>`; an unconditional
  `static inline` there). It had to be: a missing declaration is a C-level error
  none of the three text audits can see. `i_size_read` appears **zero** times in
  `zdata.c` on the supported baseline, so there is no other reader to confuse it
  with.

Then **`12bf25d1659b` (4.2)** as its own batch, after growing `FETCH_FILES` with
`fs/erofs/super.c` and `fs/erofs/internal.h`: it is the only Google-authored
candidate, its preconditions are already `y` on device, and it is observable on
suspend/resume rather than on throughput.

Defer the **PSI pair (4.3)** to its own batch. It is the most valuable -- it
closes a real hole in the signal Android's LMKD consumes -- but it is also the
only one that can leak a memory-stall window, so it needs its `leave()` sites
pinned in `implementation_audit.py` before landing, not after.

Defer everything else. **Nothing in this survey moves `/data`.**
