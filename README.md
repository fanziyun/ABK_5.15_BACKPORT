# ABK 5.15 LTS Backport

ABK external `module_set` that grafts **feature / optimization / structural**
commits from the upstream `5.15.y` LTS stream (v5.15.167..v5.15.218) onto the
`android13-5.15` GKI baseline (5.15.167, `android13-5.15-2024-11`). Pure
security fixes are out of scope. Implemented entirely as bounded anchor
grafts (Python + `replace_once`), in the same style as
[ABK_ABI_PATCH_SUITE](https://github.com/fanziyun/ABK_ABI_PATCH_SUITE) —
no `.patch` payloads.

## Children

| child id | content |
|---|---|
| `stable_backport_core` | fd-table allocation conventions (5.15.191, incl. INT_MAX guard), page_alloc ALLOC_MIN_RESERVE semantics (5.15.171), THP `__GFP_THISNODE` no-reclaim (5.15.202), cpuset insane-config early bail-out (5.15.191), percpu pagelist lock-free reads (5.15.200), cgroup root_list RCU (5.15.168), cgroup destroy-wq split (5.15.194) |
| `stable_perf_backport` | NOHZ idle-balance series (5.15.174), PSI psi_flags migration (5.15.179), RT scan optimizations (5.15.202/.212), per-task kstack randomization via KABI slot 8 (5.15.210), `__release_sock` cond_resched reduction (5.15.197), semaphore wake_q (5.15.180), blk-mq suspend wakeup abort (5.15.198) |

Every group degrades to a reported status (`blocked_by_shape`,
`skip_suite_processed`, …) when its anchor is absent — the build is never
half-patched. See `docs/survey_5_15_168_218.md` for the full candidate
analysis and `plan.md` for the living backlog.

## Composition with sibling modules

Canonical `custom_external_modules` input (order matters; CI executes in
input order, all at `after_patch`):

```
set:https://github.com/xingguangcuican6666/ABK_F2FS_FIX_MODULE.git#storage_ufs_rollback;after_patch|set:https://github.com/xingguangcuican6666/ABK_F2FS_FIX_MODULE.git#storage_block_rollback;after_patch|set:https://github.com/xingguangcuican6666/ABK_F2FS_FIX_MODULE.git#storage_f2fs_rollback;after_patch|set:https://github.com/xingguangcuican6666/ABK_F2FS_FIX_MODULE.git#storage_common_fixups;after_patch|set:https://github.com/xingguangcuican6666/ABK_5.15_backport.git#stable_backport_core;after_patch|set:https://github.com/xingguangcuican6666/ABK_5.15_backport.git#stable_perf_backport;after_patch|set:https://github.com/fanziyun/ABK_ABI_PATCH_SUITE.git#display_release_spoof;after_patch|set:https://github.com/fanziyun/ABK_ABI_PATCH_SUITE.git#abi_bridge;after_patch|set:https://github.com/fanziyun/ABK_ABI_PATCH_SUITE.git#security_backport;after_patch|set:https://github.com/fanziyun/ABK_ABI_PATCH_SUITE.git#feature_porting_core;after_patch|set:https://github.com/fanziyun/ABK_ABI_PATCH_SUITE.git#feature_porting_backlog;after_patch
```

Ordering rationale: the F2FS suite's `git apply --reverse` storage rollback
must see the pristine monthly tree; this module grafts forward on the
settled baseline; the ABI suite runs last — its `fd_alloc_hotpath` probe
detects the upstream fdtable shape this module lands (slots_wanted /
roundup_pow_of_two / ERR_PTR conventions) and adapts instead of double-
rewriting. Details and the KMI red lines: `docs/porting_policy.md`.

## CI usage (ABK)

Trigger `kernel-a13-5-15.yml` with `sub_level: 167` (os_patch_level
`2024-11`) and paste the module string above into `custom_external_modules`.
The children read `KERNEL_ROOT`, `DEFCONFIG`,
`CUSTOM_EXTERNAL_MODULE_STAGE`, `ABK_BUILD_*` from the ABK environment.

## Local verification

```bash
python3 -m py_compile scripts/*.py tests/stable_5_15_test.py
bash -n setup.sh scripts/*.sh tests/*.sh
python3 tests/stable_5_15_test.py
bash tests/smoke.sh /path/to/android13-5.15-common-kernel-tree
```

`tests/smoke.sh` builds a disposable KERNEL_ROOT from the given tree, runs
`setup.sh` for both children twice, asserts the report statuses and in-tree
markers, then exercises the rollback path.

Dry-run (statuses only, no writes):

```bash
python3 scripts/abk_stable_core.py --common-dir <tree> --defconfig <tree>/arch/arm64/configs/gki_defconfig \
  --report-dir /tmp/r --sub-level 167 --family android13-5.15 --dry-run
```

Rollback: `bash scripts/abk_rollback.sh <kernel-common-dir> --list` then
`--apply`.

## Reports

`$KERNEL_ROOT/abk_5_15_backport_reports/<child>/<child>_report.{json,md}`
per run: shapes, per-group status, applied commits. Extend the module via
`docs/group_recipe.md`.
