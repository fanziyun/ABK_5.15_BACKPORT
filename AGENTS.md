# AGENTS.md

ABK external `module_set` that grafts upstream kernel features / optimizations /
structural refactors onto the **`android13-5.15-lts` rolling branch** (its matrix
row is keyed to the fetched tree's Makefile `SUBLEVEL`, currently .220 — see
"Lts-only maintenance" below for what a roll means). Batch 44 dropped the
`5.15.167 / .178 / .194` release baselines. **This is not a kernel
source tree** — it is a Python registry that rewrites one.

## The one thing to internalize

The Python registry **is** the patch set. There are **no `.patch` payloads** and
`patches/` is empty: add a `PatchGroup` record to the correct child script instead.
The exceptions to "no payload files" are `files/drivers/of/address.c` and the
four sched_ext files — see "File payloads" below. Every edit is otherwise a group of ordered
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
  carry `/* sailboat_<group>: ... */`.
- Upstream-shape rewrites used to carry nothing, so a baseline already carrying
  the upstream commit stayed byte-identical.  **A grep marker is now allowed on
  those too** -- that is the point: a graft has to be findable in a fetched tree,
  in a running kernel's source, or in a bug report.  The cost is explicit and
  worth stating: such a baseline is no longer byte-identical, its group reports
  `applied` on a second pass instead of `already_present`, and the marker has to
  be part of the match before the edit is skipped.

One hard rule: the prefix for any **new** marker is `sailboat_` and nothing else.
`sailboat_` is taken from the companion modules'
`sailboat 附加模块（一）/（二）` display names, so the marker matches the
user-visible product name.  Registry groups landed earlier still carry the old
`ABK stable_515_backport` prefix, and `tests/implementation_audit.py` still pins
it as `MARKER`, so a `grep` for one prefix misses the other -- search for both.
Those old markers are **not** to be swapped: the marker text is anchor text, so
re-writing it means re-proving the group on the supported tree, and a group
whose `new` block another group edits must keep the exact bytes it was proved
with.  Markers must likewise never be retro-added to a landed
upstream-shape group.

Every write snapshots `<file>.abk-orig` once; `scripts/abk_rollback.sh <common-dir>
[--apply|--list]` restores. A file the module *creates* (only the sched_ext
payload does) has no original to snapshot: it carries a zero-byte `<file>.abk-new`
marker instead, which makes rollback delete it, plus an empty `<file>.abk-orig`
once its build wiring exists so `config_gate_audit` has a diff base. **Never write
outside `KERNEL_ROOT`** — the defconfig lane refuses to (`report_only` with the
reason) because rollback can only restore paths under the tree.

## Adding a group (see `docs/group_recipe.md`)

1. Register: source the commit in `plan.md`, save the upstream `.patch` under
   `research/upstream-5.15.y/patches/`, convert to old/new blocks with
   `python3 tools/hunks.py research/upstream-5.15.y/patches`.
   (`research/` is a **local-only, git-ignored workspace** — the `.patch` archive
   and the reference-tree snapshots are not published with this repo and live
   only in your own checkout. The converter itself *is* published: it sits in
   `tools/`, see "Distribution assets" below.)
2. Implement a `_xyz_apply(ctx)` function + a `PatchGroup(...)` entry in the right
   child. Split into `(rel, old, new, required)` steps. Keep rename→user chains
   `required` (transactional: a required miss writes nothing); cosmetic hunks
   `optional`.
3. Prove it (below), then tick the `plan.md` box and bump
   `ABK_MODULE_VERSION`/`ABK_MODULE_SET_VERSION` in `module.conf`.

## Verification (exact order)

A reference tree is required for the tree-level audits. Fetch one without cloning
history: `bash tests/fetch_sublevel_tree.sh <branch> <outdir>` (gitiles-encoded,
only the ~78 files the groups touch). Real branches per baseline are in
`tests/fetch_sublevel_tree.sh` and `docs/porting_policy.md`. `tools/fetch_all_trees.sh`
brings down all four into `build/abk-trees/<sublevel>`,
which is where the reference trees this repository audits against live --
**`tmp/r167`/`tmp/r216` and friends are report directories, not trees**, and every
one of the three tree audits fails on them with
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

None of those seven runs a compiler -- they are all text gates. A configured
kernel tree plus an LLVM toolchain adds the missing one:
`sh tools/compile_probe.sh <tree>` turns on `CONFIG_SCHED_CLASS_EXT` (the probe
requires the symbol to be on already) and builds
`kernel/sched/sched_ext_glue.o` plus `core.o`/`fair.o`/`idle.o`/`debug.o`/`fork.o`/`bpf_struct_ops.o`,
reporting the distinct error lines. It is a build-host helper like
`tools/hunks.py`, absent from `embed.conf`, and it is what found the SCX payload's
real failures in Batch 56. Run it whenever a batch adds C -- `step_audit` proves
an anchor landed, only the compiler proves the C is valid.  When a batch changes a
header most of the tree includes (the SCX work touches
`include/linux/sched.h`, `kernel/sched/sched.h` and
`include/linux/sched/ext.h`), the strongest form is a full
`make ARCH=arm64 LLVM=1 vmlinux` on the grafted tree: Batch 61 ran it, and it is
also what produces the post-graft BTF that `tools/build_scx_artifacts.sh`
compiles the sched_ext scheduler against.

The `config_gate_audit` is the only audit that needs a **build artefact** instead of just a
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
`ABK_TEST_SUB_LEVEL`. Since Batch 44 there is exactly **one** supported
sublevel, so a tree whose SUBLEVEL differs is refused by both scripts rather
than quietly audited. **Keep `sublevel_matrix.py` in sync when you touch the
registry**: `GROUP_COUNTS` must equal the number of `PatchGroup(...)` records in
each child, and any group whose commit the baseline already carries goes in
`PRE_APPLIED` (`stable_5_15_test.py` asserts the matrix matches the registry).

Dry-run a single child (statuses only, no writes):

```bash
python3 scripts/abk_stable_perf.py --common-dir <tree> \
  --defconfig <tree>/arch/arm64/configs/gki_defconfig --report-dir /tmp/r \
  --sub-level 216 --family android13-5.15 --dry-run
```

## Lts-only maintenance (the one rolling branch)

The single matrix row is keyed to the fetched tree's Makefile `SUBLEVEL`, so an
`android13-5.15-lts` roll **breaks the audits on purpose**:

```
no expectation recorded for sublevel '221'; supported sublevels: 220
```

That is the drift defence, not a bug. After every re-fetch:

1. re-key the row and re-prove every `PRE_APPLIED` set **on the new tree**;
2. re-run all four gates below;
3. if a group flipped to `already_present` because the branch absorbed it, move
   it into `PRE_APPLIED` — do not leave the old row claiming `applied`.

Why the CI compile gate is no substitute: it treats `already_present` as a
GOOD status and never consults the matrix. A roll that absorbs a commit whose
group is missing from `PRE_APPLIED` therefore stays green on CI forever. The
local `step_audit` / `implementation_audit` / `smoke` run is the only thing that
catches it, so it is mandatory after every lts re-fetch — not optional hygiene.

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
  `task_struct` slot 8 (Batch 16's `kstack_offset`) and slot 7 (Batch 55's
  `struct sched_ext_entity *scx`); if ABK's kernel-specific patch has reused
  slots 6/7/8 (SysVIPC), the kstack group moves to the still-free slot 5, while
  the SCX claim has no slot to move to and reports `blocked_by_missing_anchor`
  on that shape. From
  Batch 15 this module **owns** `sched_entity` slots 1–4 (the absorbed EEVDF
  family) and `request_queue` slot 1 (the absorbed `blk_mq_async_depth`): the
  old "never claim these — ABI-suite territory" rule is retired, so they are
  claimed here. Batch 16 released `sched_entity` slot 4, which the suite claimed
  as `u64 slice`, because nothing in the tree then read that field. **Batch 28
  re-claimed it** (`ANDROID_KABI_USE(4, u64 slice)`) once the rebuilt EEVDF
  payload gave `se->slice` real readers — the deadline refresh, the yield
  forfeit and `PREEMPT_SHORT`. That exhausts the `sched_entity` reserve run:
  the 6.12+ EEVDF fields (`min_slice`, `max_slice`, `vprot`, `sched_delayed`)
  have no slot and are deliberately not ported (`docs/survey_eevdf_gap.md`).
- **Scope**: features/optimizations/refactors only. Security-only fixes (they
  arrive with newer sublevels) are excluded.
- **Family gate**: a non-`android13-5.15` lineage produces `report_only` for every
  group and reads/writes nothing; `--allow-unsupported` (shell
  `ABK_515_ALLOW_UNSUPPORTED=1`) is the explicit override.
- **Storage lanes are open** (Batch 43 retired the old "Do not touch
  `fs/f2fs` or `drivers/scsi/ufs`" exclusion — sibling-suite territory). Both
  paths are now legal graft targets. The exclusion is replaced by a **hard
  ordering constraint** rather than by nothing: this module must be injected
  **after** `ABK_F2FS_FIX_MODULE`'s rollbacks — or that module must not be
  co-injected — because its rollback is `git apply --reverse --check` of
  `android13-5.15-*-*.patch`, which fails on a tree this module already rewrote,
  and its `.patch` contexts are keyed to `android13-5.15-2024-11_r14`. A storage
  group therefore may not compose with an unmodified F2FS suite; no group
  targets either path yet, so today this is a documented constraint, not a live
  breakage.
- **Composition order** (all `after_patch`): storage-rollback modules first, this
  module second. **ABK_ABI_PATCH_SUITE must NOT be co-injected with a Batch 15
  build** — it claims the same `sched_entity` 1–4 / `request_queue` 1 slots this
  module now owns (slot 4 included: the suite claims it unconditionally, this
  module only stopped *using* it), and a double-claimed slot is a hard KMI break.
  Batch 15 absorbed that suite's optimization inventory
  (see "Suite absorption" in `docs/porting_policy.md`), so inject this module
  *instead of* it. Batch 43 (above) turned the storage ordering from a courtesy
  into the only configuration in which an f2fs/ufs graft composes. Not a load
  order for the display child (drm-only, order-independent).

## Source-of-truth docs

- `README.md` (Chinese) and `README_en.md` — the user-facing overview: what the
  module does, the injection string, per-child contents. Deliberately *short*:
  per-batch technical detail (why a group landed the way it did, anchor shapes,
  KMI slots, device measurements) belongs in `CHANGELOG.md`, and the two READMEs
  point there rather than repeating it. Keep them that way when editing.
- `CHANGELOG.md` — the technical record of every landed batch; the first place
  to look for "why is this graft shaped like this". **Written for an outside
  reader, not as a work diary**: state the goal, the evidence and the verdict;
  never narrate who asked for it. Concretely — no `用户要求：…` /
  `维护者要求：…` attributions, no first person (`我` / `我们` / `笔者`), no
  "已与用户确认" / "未代用户删除". A requirement is a design fact, so write it as
  one: "目标：只维护 `android13-5.15-lts`", not "用户要求只维护 lts". The same
  rule applies to `plan.md`'s batch index lines, `docs/survey_*` and every doc
  this repo publishes — including entries already landed (a whole-repo sweep was
  done for Batch 45, so nothing predates it; new text must simply comply).
  (「用户」 meaning *userspace* or *end user* is a technical term and is fine; so
  is `自我 DoS`-style `自我` for self-.)
- `docs/porting_policy.md` — scope, KMI red lines, shape registry, three-module
  composition, report contract.
- `docs/group_recipe.md` — the add-a-group recipe and the traps above.
- `plan.md` — living backlog (written in Chinese; status markers `[ ]`/`[~]`/`[x]`/
  `[-]`). Each landed batch bumps `module.conf`'s version.
- `docs/survey_5_15_168_218.md`, `docs/survey_6_1_ack.md`, `docs/survey_6_6_ack.md`,
  `docs/survey_7_2_mm_reclaim.md` —
  candidate inventories. Since Batch 15 absorbed the ABK_ABI_PATCH_SUITE
  optimization inventory, those surveys' "suite-covered, rely on the suite"
  rows are provenance for what was absorbed, not an exclusion list to honour.
  `docs/survey_7_2_mm_reclaim.md` also records the release-delimiting method
  (compare tag reachability, never committer dates — subsystem trees commit
  weeks before Linus pulls) and, in its §4, a provenance correction: 5.15 has
  two `mmap_miss` decrements and the survey's first pass named the wrong one.
- `docs/survey_erofs_upstream.md` — the EROFS candidate inventory. Records that
  on the supported lts tree `decompressor.c`/`zdata.c`/`zdata.h` each carry a
  5.15.y-only delta absent from the pre-lts release baselines (its §2 predates
  Batch 44 and still shows the old four-baseline split; the shape conclusions
  for the supported tree are unaffected), and, in §0, that **EROFS is read-only
  system partitions and does not touch `/data`** (which is f2fs). Also lists the
  `FETCH_FILES` paths a
  group would have to add before its audits can run.

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
sweeps, re-asserts the MGLRU switch on a timer (since companion v0.18.0 — this
ROM's init writes `lru_gen/enabled` back to 0 after post-fs-data from its
`SmartCacheEnable` trigger, so the one-shot apply loses the write order), and
records **who owns CPU frequency** at boot. The policy itself lives
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
implementation) and `ksu/` (the KernelSU module source). `patches/` stays empty.

### File payloads (the exceptions)

Two payload sets exist, each for a shape no anchor graft can express.

`files/drivers/of/address.c` is a whole-file *revert* of the upstream `ranges`
flags parser rework. `scripts/stable_backport.sh`'s
`abk_stable_backport_overlay_of_address()` copies it over the tree **only** when
the target still carries the 5.15.213 rework (`flag_cells` /
`"default-flags"` text markers), so it is a no-op on a tree already in the
pre-rework form. It restores the single contiguous MMIO window whose split left
the Qualcomm SM8550 PCIe WLAN endpoint's 2 MB BAR0 unplaceable (dead `wlan0`).

`files/include/linux/sched/ext.h`, `files/kernel/sched/ext.h`,
`files/kernel/sched/ext.c` and `files/kernel/sched/sched_ext_glue.c` are the
sched_ext (SCX) BPF extensible scheduler class — a 143 KB new subsystem on a
baseline that has none, so there is no anchor shape to attach it to.
`abk_stable_backport_overlay_sched_ext()` creates them, never overwrites a foreign
`ext.c`, and withholds the empty `.abk-orig` diff base until
`abk_stable_backport_sched_ext_wired()` sees the Makefile entry — an empty base on
an inert payload would make `config_gate_audit` flag every internal gate in
`ext.c` as "added code that never compiles".

The first three files are vendor/upstream bytes (sha256-pinned); the fourth is
module-authored and exists because `ext.c` carries no include block: on the 6.6
tree it is textually included from `kernel/sched/build_policy.c`, which 5.15 does
not have (its policy files are separate objects). The glue unit supplies the
headers, does not re-include the unguarded `kernel/sched/ext.h`, and does not pull
in the policy `.c` files OPPO's build_policy.c does; `kernel/sched/Makefile`
builds `sched_ext_glue.o` rather than `ext.o`, and subtracts `autogroup.h` and
`stats.h` from the 6.6 header set (5.15's `sched.h` already includes both and
neither has a guard; listing them again is 20 redefinition errors). Batch 54
shipped the three engine files inert; Batch 55 added the glue unit and the
wiring (the `CONFIG_SCHED_CLASS_EXT` symbol, the Makefile rule, `struct scx_rq`
and `rq->scx`, the `task_struct` slot, the `SCHED_DATA` slot and the
`init_sched_ext_class()` call). **That wiring puts the engine in the build but
does not make the vendored bytes compile**: Batch 56 ran the first real
compiler over them (`tools/compile_probe.sh`) and measured 12 classes of
5.15/6.6 interface drift -- surveyed in `docs/survey_sched_ext_gap.md` §2c,
several of them function-pointer or language-level constructs no preprocessor
shim can bridge. **Batch 57 landed that marked 5.15 adaptation** and the probe
now builds all seven objects on a configured 5.15.220 tree (`OK (7 object(s)
built)`): five shims live in the glue unit (the cgroup weight helper,
`for_each_cpu_andnot()`, the BTF bit-offset alias and the diag suppression),
and three registry groups `batch57_perf_sched_ext_adapt` carry the rest --
making core.c's `__setscheduler_prio()`/`check_class_changed()` visible,
re-carrying 6.2's `SCHED_CHANGE_BLOCK` guard into core.c (where the tree's own
`dequeue_task()`/`enqueue_task()` are), and editing the archived `ext.c` at
the eight sites where a signature or the vendor `sched_prop` member differs.
The archived files stay byte-for-byte in `files/`: the adaptation is applied
to the tree, and `abk_stable_backport_overlay_sched_ext()` accepts a target
carrying one of the adaptation markers as installed so a second run does not
mistake this module's own edit for a foreign `ext.c`. **A new group that
adapts a payload file must add its marker to that allow-list**, or the second
overlay call never writes the file's empty `.abk-orig` diff base. Two payload
files are adapted in-tree today: `kernel/sched/ext.c` (Batch 57's eight sites
plus Batch 58's per-task guard) and `kernel/sched/ext.h` (Batch 59's
active-class walk).

Since then the functional hooks landed -- Batch 58 the task lifecycle, Batch 59
the pick path, the tick watchdog and the idle transition, Batch 60 the
reachability gates (`normal_policy()`/fair_policy(), the priority-range
syscalls, `BPF_STRUCT_OPS_TYPE(sched_ext_ops)` in
`kernel/bpf/bpf_struct_ops_types.h` and the `ext` debugfs file), which is why
the probe's object set now also carries `kernel/bpf/bpf_struct_ops.o`. The class
is selectable and bindable after Batch 60; what it still lacks is the userspace
loader that attaches a BPF scheduler (S3) and any device verification.

The rationale, the gate probes, both snapshot conventions and the rollback paths
are documented in `files/README.md`. Adding a further payload file needs the same
justification — a shape an anchor can express belongs in a `PatchGroup`, not here.

What the repo **publishes** is only what the module needs: the registry
(`scripts/`), its tests (`tests/`), the docs, `setup.sh`, `module.conf`, `public.md`
and the two distribution trees (`tools/`, `ksu/`). Everything else is **local-only
and git-ignored**: `research/` (upstream `.patch` archive, reference-tree snapshots,
per-batch device-check records), `build/abk-trees/`, `tmp/` and `tests/out/`. Doc
paths that point into `research/` therefore refer to the maintainer's checkout, not
to a published artefact.

`tools/` carries two kinds of script and the split matters: the six **device
CLIs** listed in `ksu/abk_runtime_tunables/embed.conf` ship verbatim into the
companion module's `bin/` and must survive Android mksh; `tools/hunks.py`,
`tools/fetch_all_trees.sh`, `tools/build_scx_artifacts.sh` and
`tools/compile_probe.sh` are **build-host dev helpers** (`bash`/`python3`; the
last two compile in a kernel tree) and are deliberately absent from
`embed.conf`, so the packager never copies them into the zip.  `tools/scx/`
holds the sched_ext userspace assets; the minimal scheduler's BPF source is
compiled by `tools/build_scx_artifacts.sh` and the resulting `.bpf.o`, together
with the cross-built loader, is what actually ships (the sources themselves are
not in `embed.conf`).  `tools/scx/libelf-shim/` is the
read-only slice of libelf the Android loader build links against (the NDK ships
none); `BUILD_SHIM_LOADER=1` proves it by parsing the same object through both
loaders and diffing the output, which is the only evidence that a shim is safe
to hand to a cross compiler.  `tools/scx/android-compat/` adds the kernel
headers and macros bionic lacks, `tools/scx/build_android_loader.sh` is the
NDK cross build, `tools/scx/run_arm64_selftest.sh` runs a static aarch64 build
under qemu-user and diffs its parse against the x86_64 one (the only host-side
evidence that the arm64 code path works, since the committed binary is dynamic
bionic), and `tools/scx/prebuilt/` holds the two committed build
products the companion ships (`embed.conf` maps them into the module's `bin/`);
regenerating them is the two commands in `tools/scx/README.md`.

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
