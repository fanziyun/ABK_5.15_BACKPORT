# ABK 5.15 LTS Backport

An ABK external `module_set` that grafts **feature / optimization /
structural** commits from the upstream `5.15.y` LTS stream
(v5.15.167..v5.15.218) onto the `android13-5.15` GKI baseline (5.15.167,
`android13-5.15-2024-11`). Pure security fixes are out of scope.

Every graft is a **bounded anchor script**: each group declares the tree
shapes it accepts, rewrites only exact-match regions through transactional
`replace_once` steps, and degrades to a reported status (`blocked_by_shape`,
`already_present`, …) when an anchor is absent — the kernel tree is never
half-patched. There are no `.patch` payloads to review or keep in sync; the
Python registry *is* the patch set, and every edit carries an
`ABK stable_515_backport:` marker that doubles as its idempotency anchor.

## Children

| child id | content |
|---|---|
| `stable_backport_core` | fd-table allocation conventions (5.15.191, incl. INT_MAX guard), page_alloc ALLOC_MIN_RESERVE semantics (5.15.171), THP `__GFP_THISNODE` no-reclaim (5.15.202), cpuset insane-config early bail-out (5.15.191), percpu pagelist lock-free reads (5.15.200), cgroup root_list RCU (5.15.168), cgroup destroy-wq split (5.15.194), per-memcg proactive reclaim via `memory.reclaim` (android14-6.1) |
| `stable_perf_backport` | NOHZ idle-balance series (5.15.174), PSI psi_flags migration (5.15.179), RT scan optimizations (5.15.202/.212), per-task kstack randomization via KABI slot 8 (5.15.210), `__release_sock` cond_resched reduction (5.15.197), semaphore wake_q (5.15.180), blk-mq suspend wakeup abort (5.15.198), PSI IRQ pressure tracking, PSI trigger kernfs polling, lazy-preemption + mutex/rwsem wakeup vendor hooks (android14-6.1) |

Since Batch 3 the module also grafts selected **android14-6.1 ACK line**
features (the only 6.1 ACK branch): `memory.reclaim` proactive reclaim,
PSI IRQ tracking, PSI trigger kernfs polling, and the lazy-preemption /
lock-wakeup vendor-hook families.  Those groups mirror the ACK 6.1 form
adapted to the 5.15 baseline shapes, keep the KMI untouched (heap-internal
wrappers, percpu states, additive tracepoints only), and skip anything the
ABK_ABI_PATCH_SUITE already covers (see `docs/survey_6_1_ack.md`).

KMI red lines are built in: new exported-struct fields only ever reuse free
`ANDROID_KABI_RESERVE` slots (this module uses `task_struct` slot 8), and
every group reports instead of forcing when the tree does not match. See
`docs/survey_5_15_168_218.md` for the candidate analysis and `plan.md` for
the living backlog.

## Injection (ABK CI)

Trigger `kernel-a13-5-15.yml` with `sub_level: 167` (os_patch_level
`2024-11`) and put the module into `custom_external_modules`:

```
set:https://github.com/xingguangcuican6666/ABK_5.15_backport.git#stable_backport_core;after_patch|set:https://github.com/xingguangcuican6666/ABK_5.15_backport.git#stable_perf_backport;after_patch
```

The children read `KERNEL_ROOT`, `DEFCONFIG`,
`CUSTOM_EXTERNAL_MODULE_STAGE` and `ABK_BUILD_*` from the ABK environment.
Both children are idempotent; running them is safe at any point after the
kernel patches are applied.

## Coexistence with other ABK modules

The module is self-contained and injectable on its own. If the same build
also carries storage-rollback or feature-graft modules, this order is still
recommended (CI executes `custom_external_modules` entries in input order,
all at `after_patch`):

1. storage rollback children first (their reverse-apply must see the
   pristine monthly tree),
2. this module second (forward grafts onto the settled baseline),
3. other feature-graft modules last — their fd-table probes detect the
   upstream shape this module lands and adapt instead of double-rewriting.

The order is no longer a hard requirement: when ABK_ABI_PATCH_SUITE runs
first anyway, this module's fd-table group recognizes the suite's fallback
`alloc_fdtable()` and composes the upstream 5.15.191 conventions on top of
it (the suite's helpers and `expand_files()`/`alloc_fd()` prechecks stay in
place), so all 14 groups land in either injection order.

The full input string for the F2FS + ABI-suite combination, the shape
registry and the KMI compatibility matrix are documented in
`docs/porting_policy.md`.

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
