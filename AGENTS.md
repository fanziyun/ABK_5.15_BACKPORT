# AGENTS.md

ABK external `module_set` that grafts upstream kernel features / optimizations /
structural refactors onto the `android13-5.15` GKI baselines 5.15.167 / .178 / .194
(and audits the `android13-5.15-lts` .211 rolling branch). **This is not a kernel
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
`tests/fetch_sublevel_tree.sh` and `docs/porting_policy.md`.

```bash
python3 -m py_compile scripts/*.py tests/*.py     # syntax gate
bash -n setup.sh scripts/*.sh tests/*.sh tools/*.sh  # shell syntax gate
python3 tests/stable_5_15_test.py                 # unit tests (no kernel tree needed)
python3 tests/step_audit.py <tree>                # per-step: anchors land, structure balanced, idempotent
python3 tests/implementation_audit.py <tree>      # content: no phantom groups, features really present
bash tests/smoke.sh <tree>                        # end-to-end: 2-pass idempotency + rollback
```

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

Also verify every helper the ported code calls against **its own tree**, not the
source tree (convention traps — compile clean, behave wrong). The pinned example:
`hugepage_vma_revalidate()` returns **0 on success** on 5.15 but `SCAN_SUCCEED`
(=1) from 6.1, so copy 6.1's `if (result != SCAN_SUCCEED)` inverts success. Look at
the other callers in the same file; `implementation_audit.py` pins these as
required strings.

## Red lines

- **KMI**: new exported-struct fields only reuse a free `ANDROID_KABI_RESERVE`
  slot via `ANDROID_KABI_USE`. This module uses `task_struct` slot 8; if ABK's
  kernel-specific patch has reused slots 6/7/8 (SysVIPC), move to slot 5. Never
  claim `sched_entity` slots 1–4 or `request_queue` slot 1 (ABI-suite territory).
- **Scope**: features/optimizations/refactors only. Security-only fixes (they
  arrive with newer sublevels) are excluded.
- **Family gate**: a non-`android13-5.15` lineage produces `report_only` for every
  group and reads/writes nothing; `--allow-unsupported` (shell
  `ABK_515_ALLOW_UNSUPPORTED=1`) is the explicit override.
- **Do not touch `fs/f2fs` or `drivers/scsi/ufs`** — sibling-suite territory.
- **Composition order** (all `after_patch`): storage-rollback modules first, this
  module second, ABK_ABI_PATCH_SUITE last. Not a load order for the display child
  (drm-only, order-independent).

## Source-of-truth docs

- `README.md` — overview, injection string, per-child contents.
- `docs/porting_policy.md` — scope, KMI red lines, shape registry, three-module
  composition, report contract.
- `docs/group_recipe.md` — the add-a-group recipe and the traps above.
- `plan.md` — living backlog (written in Chinese; status markers `[ ]`/`[~]`/`[x]`/
  `[-]`). Each landed batch bumps `module.conf`'s version.
- `docs/survey_5_15_168_218.md`, `docs/survey_6_1_ack.md`, `docs/survey_6_6_ack.md` —
  candidate inventories; check the ABI-suite exclusion list before porting anything.

## How ABK runs this module (the external-module contract)

This is an ABK (`AnyBase Kernel`) external module. ABK's build workflow
(`.github/workflows/build.yml` in the ABK repo) injects it by cloning the repo and
running `bash setup.sh` **from the checked-out module dir** — once per stage. Two
stages, each a separate run:
- `after_patch` — the real graft (this module does all work here).
- `before_build` — accepted but a no-op for this module.

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
- `ABK_515_ALLOW_UNSUPPORTED`, `ABK_515_DEFCONFIG_ALIGN` are **module-specific
  overrides set by the user**, not by ABK.

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
/ `ABK_MODULE_RECOMMENDED_STAGES`.

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
