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

## Three-module composition (all after_patch)

CI executes injected modules in input order, so the canonical input is:

1. `ABK_F2FS_FIX_MODULE` children (`storage_ufs_rollback`,
   `storage_block_rollback`, `storage_f2fs_rollback`,
   `storage_common_fixups`) — restore the storage baseline first; its
   `git apply --reverse --check` breaks if anything rewrites block//f2fs
   before it.
2. **This module** (`stable_backport_core`, `stable_perf_backport`) —
   forward grafts onto the settled baseline.
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

Footprint disjointness (verified against the F2FS suite script and its
`android13-5.15-2024-11_r14` patches): the F2FS suite touches
`drivers/scsi/ufs/`, `block/` (one hunk in `blk_mq_delay_run_hw_queues()`),
`fs/f2fs/*`, `include/trace/events/f2fs.h`, plus optional `dm/` and
`fs/crypto/`. This module's only shared file is `block/blk-mq.c`, and its
hunks live in `blk_mq_hctx_notify_offline()` — disjoint from both the F2FS
hunk and the ABI suite's blk-mq regions.

## Report contract

Each child writes `<report_dir>/<child>_report.json` + `.md` (default
`$KERNEL_ROOT/abk_5_15_backport_reports/<child>/`) with per-group status:
`applied / partial / already_present / skip_suite_processed /
skip_f2fs_rolled_back / report_only / blocked_by_missing_anchor /
blocked_by_shape`. Reports are also `.abk-orig`-snapshotted across runs.

## Backups and rollback

All writes go through `write_text()`, snapshotting to `<file>.abk-orig`
exactly once (never overwritten). `scripts/abk_rollback.sh <common-dir>
--apply` restores every snapshot tree-wide; `--list` dry-runs.
