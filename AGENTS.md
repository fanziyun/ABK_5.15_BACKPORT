# AGENTS.md

ABK external `module_set` that grafts upstream kernel features / optimizations /
structural refactors onto the `android13-5.15` GKI baselines 5.15.167 / .178 / .194
(and audits the `android13-5.15-lts` rolling branch — its matrix row is keyed to
the fetched tree's Makefile `SUBLEVEL`, currently .216). **This is not a kernel
source tree** — it is a Python registry that rewrites one.

## The one thing to internalize

The Python registry **is** the patch set. There are **no `.patch` payloads**: the
`patches/` and `files/` dirs are deliberately empty. Do not add a `.patch`; add a
`PatchGroup` record to the correct child script. Every edit is a group of ordered
`replace_once(ctx, old, new, required)` steps in `scripts/abk_stable_core.py`
(fs/mm/cgroup), `scripts/abk_stable_perf.py` (sched/net/locking/block), or
`scripts/abk_stable_display.py` (the single drm revert).

## Gating model

The engine gates purely on **text anchors**, never on `sub_level` — `ctx.sub_level`
only appears in reports. So: do **not** add version gating. A group whose upstream
commit the baseline already carries reports `already_present` (a success, not a
degradation). Per-baseline expectations live in `tests/sublevel_matrix.py`.

Idempotency rule (the anchor policy):
- New module-introduced lines (new fields, helpers, vendor hooks, UAPI/config)
  carry `/* ABK stable_515_backport: ... */`.
- Upstream-shape rewrites are **their own target form** — no comment added, so a
  baseline already carrying the commit is left byte-identical.

Every write snapshots `<file>.abk-orig` once; `scripts/abk_rollback.sh <common-dir>
[--apply|--list]` restores. **Never write outside `KERNEL_ROOT`** — the defconfig
lane refuses to (`report_only` with the reason) because rollback can only restore
paths under the tree.

## Adding a group (see `docs/group_recipe.md`)

1. Register: source the commit in `plan.md`, save the upstream `.patch` under
   `research/upstream-5.15.y/patches/`, convert to old/new blocks with
   `python research/hunks.py research/upstream-5.15.y/patches`.
2. Implement a `_xyz_apply(ctx)` function + a `PatchGroup(...)` entry in the right
   child. Split into `(rel, old, new, required)` steps. Keep rename→user chains
   `required` (transactional: a required miss writes nothing); cosmetic hunks
   `optional`.
3. Prove it (below), then tick the `plan.md` box and bump
   `ABK_MODULE_VERSION`/`ABK_MODULE_SET_VERSION` in `module.conf`.

## Verification (exact order)

A reference tree is required for the tree-level audits. Fetch one without cloning
history: `bash tests/fetch_sublevel_tree.sh <branch> <outdir>` (gitiles-encoded,
only the ~44 files the groups touch). Real branches per baseline are in
`tests/fetch_sublevel_tree.sh` and `docs/porting_policy.md`. `research/fetch_all_trees.sh`
brings down all four into `build/abk-trees/<sublevel>`, which is where the reference trees
this repository audits against live -- **`tmp/r167`/`tmp/r216` and friends are report
directories, not trees**, and every one of the three tree audits fails on them with
`reference tree is missing Documentation/admin-guide/cgroup-v2.rst`. A tree fetched before
`FETCH_FILES` grew entries is missing them too; re-fetch rather than working around it.

```bash
python3 -m py_compile scripts/*.py tests/*.py     # syntax gate
bash -n setup.sh scripts/*.sh tests/*.sh tools/*.sh ksu/*/*.sh  # shell syntax gate
python3 tests/stable_5_15_test.py                 # unit tests (no kernel tree needed)
python3 tests/step_audit.py <tree>                # per-step: anchors land, structure balanced, idempotent
python3 tests/implementation_audit.py <tree>      # content: no phantom groups, features really present
bash tests/smoke.sh <tree>                        # end-to-end: 2-pass idempotency + rollback
python3 tests/config_gate_audit.py <patched-tree> --config <.config>  # nothing added compiles out
```

The last one is the only audit that needs a **build artefact** instead of just a
tree: it diffs every file against its `.abk-orig` snapshot to attribute the CONFIG
gates this module added, then resolves each symbol against the `.config` the build
produced, failing on any gate that is off and not recorded in its `DARK_GATES`
table. A symbol a tier claims to enable but whose `.config` says `not set` is a
hard failure too — that is the signature of an unmet Kconfig dependency, which is
how Batch 8's RCU `offload_all` graft stayed dead while reporting `applied`
(see `CHANGELOG.md#batch-16`). Run it against a build made from the current tiers,
or a stale `.config` will (correctly) report the tier/config contradiction.

`bash -n` is **not** the shell that runs this code on the phone. Anything that
ships into the module (`tools/*.sh`, `ksu/**/*.sh`) must also pass `sh -n` under the
device's own shell (Android mksh) — push the file and run `su -c 'sh -n <path>'`.
Bash accepts constructs mksh rejects, and this bit for real: an apostrophe inside a
single-quoted `awk '...'` program in `abk_fas_check.sh` terminated that string, every
local gate stayed green, and the shipped tool died at its first sampling loop.
Build-host scripts (`scripts/abk_rollback.sh`, `scripts/stable_backport.sh`,
`scripts/libabk.sh`, `tests/smoke.sh`) are `#!/usr/bin/env bash` by design and are
excluded from that rule.

Layering reason: `step_audit.py` proves **structure** (anchors apply, no
`already_present` in a must-apply group, comment/brace/`#ifdef` balance kept,
second pass is byte-identical), `implementation_audit.py` proves **content**
(behaviour-visible symbols survive; the only check that catches a graft that
compiles but behaves like the source kernel), `smoke.sh` proves the whole child
path twice plus rollback. Adding a group almost always means extending all three.

`tests/step_audit.py` and `tests/smoke.sh` read the tree's Makefile `SUBLEVEL` and
look up expected statuses in `tests/sublevel_matrix.py`; override with
`ABK_TEST_SUB_LEVEL`. **Keep `sublevel_matrix.py` in sync when you touch the
registry**: `GROUP_COUNTS` must equal the number of `PatchGroup(...)` records in
each child, and any group whose commit a baseline already carries goes in
`PRE_APPLIED` (`stable_5_15_test.py` asserts the matrix matches the registry).

Dry-run a single child (statuses only, no writes):

```bash
python3 scripts/abk_stable_perf.py --common-dir <tree> \
  --defconfig <tree>/arch/arm64/configs/gki_defconfig --report-dir /tmp/r \
  --sub-level 167 --family android13-5.15 --dry-run
```

## Step-authoring traps (hidden behind a green group status)

`replace_once` checks the **new** block first (idempotency), so these silently
report `already_present` while the edit never lands — group stays "applied":

1. A `new` block that already exists in the pristine file (or is a prefix of
   `old`). Re-anchor `old`/`new` with unique surrounding context.
2. A step whose `new` verbatim contains a later step's `new` (building one
   replacement out of another *guarantees* it). Reordering steps does not help —
   make the two replacements textually distinct (different line wrapping of the
   same C suffices). This is the MADV_COLLAPSE `khugepaged_scan_file()` failure.
3. A `new` block that starts with `*/` closes the enclosing comment and turns the
   following comment body into code (broke `sched/features.h`). Insert comment
   additions **before** the closing `*/`.
4. A `_apply` that returns `already_present` early (on a shape probe) will never
   run *any* of its steps on that shape. A hunk from a later sublevel than the
   shape probe recognizes needs its **own group** (why the 5.15.195 `replace_fd()`
   fix is a separate group, not a step in `fdtable_alloc_conventions`).
5. A **later group that rewrites text an earlier group appended**. Idempotency
   means "my `new` block is already in the file": if another group edits it, the
   earlier group's `new` stops matching while its `old` anchor still does, so the
   second pass appends its payload **again** (Batch 21 produced two
   `psi_account_irqtime()` definitions that way — caught by `step_audit.py`'s
   *patched-tree* status assertion, not the pristine pass). Give the earlier group
   a shape probe on one of its own added symbols (`apply_steps` is transactional,
   so one is enough) and register the two in dependency order.
6. A **C-level** error no text audit can see. `module_param(name, type, perm)`
   compiles `name` as the *variable*, so a knob whose sysfs name differs from its
   variable must use `module_param_named(name, variable, type, perm)` or
   `module_param_string(name, var, len, perm)`. Batch 12 shipped
   `module_param(abk_lock_algo, ...)` next to `static bool abk_zram_lock_algo`
   and every local gate stayed green while ABK CI failed the TU with
   `use of undeclared identifier 'abk_lock_algo'`. Whenever a group introduces C,
   the compile is the only real gate: keep the ABK CI run in the loop, and pin the
   two-name form (plus a `module_param()`-vs-declaration sweep) in the unit test
   and `implementation_audit.py`.

7. A **reference to a symbol that only exists inside a CONFIG gate**, added outside
   that gate. The four tree-level audits never run a preprocessor, so they cannot
   see it: Batch 17's compressed-writeback helpers used `zram->bdev` /
   `zram->wb_compressed` / `stats.bd_reads` -- all declared inside
   `#ifdef CONFIG_ZRAM_WRITEBACK` in `zram_drv.h` -- while the config is *optional*
   (only the ROM tier turns it on), and every build that left it off died with
   `no member named 'bdev' in 'struct zram'` (Batch 23, CI run 34876820533).
   New text that touches a gated symbol carries the same gate, with the pristine
   call in the `#else` branch where a call site has to fall back.
   `implementation_audit.py` now checks this mechanically
   (`CONFIG_GATED_REFERENCES`).

Also verify every helper the ported code calls against **its own tree**, not the
source tree (convention traps — compile clean, behave wrong). The pinned example:
`hugepage_vma_revalidate()` returns **0 on success** on 5.15 but `SCAN_SUCCEED`
(=1) from 6.1, so copy 6.1's `if (result != SCAN_SUCCEED)` inverts success. Look at
the other callers in the same file; `implementation_audit.py` pins these as
required strings.

## Red lines

- **KMI**: new exported-struct fields only reuse a free `ANDROID_KABI_RESERVE`
  slot via `ANDROID_KABI_USE`; a group may instead carry state in an existing bit of
  a field it does not own (`cgroup.pressure` uses the cgroup's own `flags` word,
  because `struct psi_group` is embedded in `struct cgroup` on 5.15 and the ACK's
  `psi_group::enabled` member would move every member after it). This module uses
  `task_struct` slot 8; if ABK's
  kernel-specific patch has reused slots 6/7/8 (SysVIPC), move to slot 5. From
  Batch 15 this module **owns** `sched_entity` slots 1–3 (the absorbed EEVDF
  family) and `request_queue` slot 1 (the absorbed `blk_mq_async_depth`): the
  old "never claim these — ABI-suite territory" rule is retired, so they are
  claimed here. Batch 16 released `sched_entity` slot 4, which the suite claimed
  as `u64 slice`: nothing in the tree ever read that field, so it is
  `ANDROID_KABI_RESERVE(4)` again rather than dead frozen-ABI space.
- **Scope**: features/optimizations/refactors only. Security-only fixes (they
  arrive with newer sublevels) are excluded.
- **Family gate**: a non-`android13-5.15` lineage produces `report_only` for every
  group and reads/writes nothing; `--allow-unsupported` (shell
  `ABK_515_ALLOW_UNSUPPORTED=1`) is the explicit override.
- **Do not touch `fs/f2fs` or `drivers/scsi/ufs`** — sibling-suite territory.
- **Composition order** (all `after_patch`): storage-rollback modules first, this
  module second. **ABK_ABI_PATCH_SUITE must NOT be co-injected with a Batch 15
  build** — it claims the same `sched_entity` 1–4 / `request_queue` 1 slots this
  module now owns (slot 4 included: the suite claims it unconditionally, this
  module only stopped *using* it), and a double-claimed slot is a hard KMI break.
  Batch 15 absorbed that suite's optimization inventory
  (see "Suite absorption" in `docs/porting_policy.md`), so inject this module
  *instead of* it. Not a load
  order for the display child (drm-only, order-independent).

## Source-of-truth docs

- `README.md` — overview, injection string, per-child contents.
- `docs/porting_policy.md` — scope, KMI red lines, shape registry, three-module
  composition, report contract.
- `docs/group_recipe.md` — the add-a-group recipe and the traps above.
- `plan.md` — living backlog (written in Chinese; status markers `[ ]`/`[~]`/`[x]`/
  `[-]`). Each landed batch bumps `module.conf`'s version.
- `docs/survey_5_15_168_218.md`, `docs/survey_6_1_ack.md`, `docs/survey_6_6_ack.md` —
  candidate inventories. Since Batch 15 absorbed the ABK_ABI_PATCH_SUITE
  optimization inventory, those surveys' "suite-covered, rely on the suite"
  rows are provenance for what was absorbed, not an exclusion list to honour.

## How ABK runs this module (the external-module contract)

This is an ABK (`AnyBase Kernel`) external module. ABK's build workflow
(`.github/workflows/build.yml` in the ABK repo) injects it by cloning the repo and
running `bash setup.sh` **from the checked-out module dir** — once per stage. Two
stages, each a separate run:
- `after_patch` — the real graft, plus the runtime-companion bundle (below).
- `before_build` — accepted but a no-op for this module.

`after_patch` also builds and injects the **runtime companion**, a KernelSU module
(`ksu/abk_runtime_tunables/`, packed by `scripts/build_ksu_module.py` and bundled
into the AnyKernel3 tree by `scripts/ak3_bundle_ksu_module.py`) that keeps the
measured zram algorithm policy in force on device, drives the recompression
sweeps, and records **who owns CPU frequency** at boot. The policy itself lives
in the kernel since Batch 12 (`zram_algo_lock`: `zram.abk_comp_algo` /
`zram.abk_lock_algo`, both `0444`, and both
`comp_algorithm`/`recomp_algorithm` stores made reported no-ops), so the companion
only verifies, re-asserts the compressed-memory cap, runs one gated zsmalloc
`compact` pass after each sweep tick (both overhead gates must call the device
fragmented before the node is written; on by default since companion v0.4.0), and
preserves or establishes the zram writeback backing device; on a kernel without the
lock it falls back to owning the whole bring-up and re-checking it every
`zram.reassert_interval_sec`. Its DVFS duty is **report only**: `abk_report_dvfs_state()`
logs each cpufreq policy's governor, range, transition count and the capacity the
placer actually ranks by, because a FAS/WALT tree owns frequency and this module
must not (Batch 10-5/10-6; `tools/abk_fas_check.sh`, shipped as `bin/`, is the
on-demand verdict-carrying version). It is a distribution asset, **not** a graft: it
registers no `PatchGroup`, never writes into the kernel tree, and the whole step
is skipped with a warning when the build has no AnyKernel3 tree (or when
`ABK_515_KSU_MODULE=0`).

ABK exports these before invoking `setup.sh`; `setup.sh`/`stable_backport.sh` read
them (the kernel tree to graft is always under `KERNEL_ROOT`, with `common/` below):

- `KERNEL_ROOT` = `<workspace>/<android>-<kernel>-<sublevel>`; **the tree to graft
  is `<KERNEL_ROOT>/common`** (`abk_common_dir()` returns that).
- `DEFCONFIG` = `<KERNEL_ROOT>/common/arch/arm64/configs/gki_defconfig`.
- `CUSTOM_EXTERNAL_MODULE_STAGE` = the current stage.
- `ABK_BUILD_*` — `ANDROID_VERSION`, `KERNEL_VERSION`, `SUB_LEVEL`,
  `OS_PATCH_LEVEL`, … (`ABK_BUILD_SUB_LEVEL`, `ABK_BUILD_ANDROID_VERSION`,
  `ABK_BUILD_KERNEL_VERSION` drive the family/sublevel detection in
  `stable_backport.sh`). `ABK_FEATURE_*` are the build's feature toggles.
- `ABK_515_ALLOW_UNSUPPORTED`, `ABK_515_DEFCONFIG_ALIGN`,
  `ABK_515_DEFCONFIG_ROM` and `ABK_515_DEFCONFIG_PSI` are **module-specific
  overrides set by the user**, not by ABK. `ABK_515_KSU_MODULE=0` skips the
  runtime-companion bundle; `ABK_515_DEFCONFIG_ALIGN=1` adds the 6.6-GKI config
  deltas, `ABK_515_DEFCONFIG_ROM=1` the ROM-integration tier (currently
  `CONFIG_ZRAM_WRITEBACK=y`, off by default) and `ABK_515_DEFCONFIG_PSI=1` the
  per-cgroup PSI tier, which drops `cgroup_disable=pressure` from
  `CONFIG_CMDLINE` (the baseline token hides every `CFTYPE_PRESSURE` file and
  disables per-cgroup accounting device-wide, so the Batch 21 switch and the
  companion's PSI policy are otherwise unreachable — off by default). The tiers
  are additive and each is named in the config-lane detail string.

Injection goes into `custom_external_modules`, `|`-separated; the grammar:
- plain module: `module:repo;stage` (legacy `repo;stage`);
- **module-set child (this repo): `set:repo#child_id;stage`** → ABK sets
  `ABK_MODULE_ENTRY_KIND=module_set_child`, `ABK_MODULE_GROUP_REPO_URL=<repo>`,
  `ABK_MODULE_CHILD_ID=<child_id>`, and `setup.sh` dispatches on
  `ABK_MODULE_CHILD_ID` via `stable_backport.sh`.

`module.conf` declares the contract: `ABK_MODULE_KIND="module_set"` marks a set and
`ABK_MODULE_SET_ITEMS` lists children as
`child_id|name|description|repo_url|supported_stages|default_stage|recommended_stages|group_role|controllable|has_web_ui|magisk_module_name|magisk_module_url`.
Plain modules instead use `ABK_MODULE_SUPPORTED_STAGES` / `ABK_MODULE_DEFAULT_STAGE`
/ `ABK_MODULE_RECOMMENDED_STAGES`. The last two fields are what the ABK app reads to
offer a companion module (`ABK_MAGISK_MODULE_NAME` /
`ABK_MAGISK_MODULE_DOWNLOAD_URL` for plain modules). On this repo the companion is
**bundled into the AnyKernel3 zip** (`after_patch` → `ak3_bundle_ksu_module.py`,
under `abk-ksu-modules/`), so flashing the kernel installs it and the app has
nothing to download: the `stable_backport_core` row keeps the two fields as empty
placeholders on purpose (still 12 fields, still parseable).

Distribution assets live outside the graft: `tools/` (device-facing CLIs, shipped
into the companion module by `ksu/abk_runtime_tunables/embed.conf` so there is one
implementation) and `ksu/` (the KernelSU module source). `patches/` and `files/`
stay empty.

Each module ships its **own** `scripts/libabk.sh`; ABK provides nothing shared. The
reference template (`xingguangcuican6666/ABK_KSU_SANDBOX_MODULE`) has a fuller
helper set (`abk_kernel_version`, `abk_require_dir`, `abk_set_config`,
`abk_enable_config`, `abk_enable_lsm`) that this module's trimmed `libabk.sh`
omits — this module does config through the Python engine's
`GraftContext.enable_configs()`, not shell.

## Reports / runtime

Statuses per group: `applied / partial / already_present / skip_suite_processed /
report_only / blocked_by_missing_anchor / blocked_by_shape`. Reports go to
`$KERNEL_ROOT/abk_5_15_backport_reports/<child>/<child>_report.{json,md}`.
