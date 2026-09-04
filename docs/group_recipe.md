# Group recipe: adding a new backport group

The module is a registry: one `PatchGroup` entry per upstream commit (or
tightly-coupled series), implemented as ordered `replace_once` steps. A new
batch is three steps.

## 1. Register the candidate

- Pick the commit from `docs/survey_5_15_168_218.md` backlog (or survey a
  new one via `research/upstream-5.15.y/` path listings).
- Save the upstream `.patch` under `research/upstream-5.15.y/patches/`
  (`curl -L https://github.com/gregkh/linux/commit/<sha>.patch -o <sha>.patch`).
- Convert it to old/new blocks with the dev helper:
  `python research/hunks.py research/upstream-5.15.y/patches`.
- Add a checkbox line to `plan.md` with the sublevel and target child.

## 2. Implement the group

- Add a `_xyz_apply(ctx)` function and a `PatchGroup(...)` entry to the
  child script (`abk_stable_core.py` for fs/mm/cgroup, `abk_stable_perf.py`
  for sched/net/locking/block/misc).
- Split the patch into `(rel, old, new, required)` steps. Rules:
  - Keep rename→user chains **required** so a partial tree can never build;
    `apply_steps` is transactional (a required miss writes nothing).
  - Pure-comment/cosmetic hunks are `optional` (they degrade to `partial`).
  - Adapt old blocks to the *AOSP* shape where it differs from vanilla
    (vendor hooks in core.c/fair.c, AOSP dup_fd contract, KABI slots for
    any new struct field).
  - Mark grafted code with a `/* ABK stable_515_backport: ... */` comment
    (or `ANDROID_KABI_USE` slot comment) — this doubles as the idempotency
    anchor.
- If the group must never degrade (like the fdtable conventions), make it a
  `hard=True` group and raise `SystemExit` on unknown shapes.

## 3. Prove it

- `python3 -m py_compile scripts/*.py tests/*.py && bash -n setup.sh scripts/*.sh tests/*.sh tools/*.sh`
- `python3 tests/stable_5_15_test.py` (add fixture checks if the group
  introduces a new shape probe).
- Dry-run against the reference tree:
  `python3 scripts/abk_stable_perf.py --common-dir <tree> --defconfig <tree>/arch/arm64/configs/gki_defconfig --report-dir /tmp/r --sub-level 167 --family android13-5.15 --dry-run`
  — every anchor must report `applied`/`already_present`, never
  `missing_anchor`.
- Repeat the dry-run on every supported baseline (167/178/194 — see the
  sublevel matrix in `docs/porting_policy.md`).  Fetch the ones you don't have
  with `bash tests/fetch_sublevel_tree.sh <branch> <outdir>`.  If the group's
  upstream commit is already in one of those baselines, record it in
  `tests/sublevel_matrix.py` `PRE_APPLIED` and bump `GROUP_COUNTS` — the unit
  test asserts the matrix matches the registry, and `step_audit.py` uses it to
  decide whether a step is allowed to report `already_present`.
- Per-step audit on each baseline:
  `python3 tests/step_audit.py <tree>` (reads SUBLEVEL from the tree's
  Makefile; `ABK_TEST_SUB_LEVEL` overrides).
- Implementation audit on each baseline:
  `python3 tests/implementation_audit.py <tree>`.  Add the group's
  behaviour-visible strings to `REQUIRED_CONTENT` — including any 5.15-specific
  calling convention (see the convention traps below), which is the only check
  that can catch a graft that compiles but behaves like the source kernel.
- Full smoke (two-pass idempotency + rollback):
  `bash tests/smoke.sh <tree>`.
- Extend `tests/smoke.sh` grep assertions if the group lands a load-bearing
  marker — gate the assertion on `sublevel_matrix.applies()` when the marker
  only exists on baselines where the group really rewrites the file.  Then tick
  the `plan.md` checkbox and bump `ABK_MODULE_VERSION`/`ABK_MODULE_SET_VERSION`
  in `module.conf`.

## Step-authoring traps (compile-breaking, hidden at group level)

Four traps slip past group statuses ("applied") and only surface at compile
time; `tests/step_audit.py` catches them on a pristine tree:

1. **Replacement blocks that pre-exist in the file.** `replace_once` checks
   the *new* block first (idempotency).  If `new` is a common pattern or a
   prefix of `old` (line deletions!), the step short-circuits to
   `already_present` and the real edit never lands.  Fix: re-anchor `old` and
   `new` with unique surrounding context (e.g. the `return newf;` before the
   `out_release:` label) so `new` cannot exist before the step runs.
2. **A step that pre-creates a later step's replacement text.** Same
   short-circuit, but the collision is manufactured *inside the group*: if step
   N's `new` contains step N+1's `new` verbatim, step N+1 reports
   `already_present` and is silently skipped.  Building one replacement out of
   another (`_MC_FILE_STUB_NEW = _MC_FILE_SIG_NEW + body`) guarantees it —
   reordering the steps does not help, because `replace_once` looks at the
   replacement before the anchor either way.  Fix: make the two replacements
   textually distinct (different line wrapping of the same C is enough).  This
   is the MADV_COLLAPSE `khugepaged_scan_file()` failure: the CONFIG_SHMEM=y
   definition kept four parameters while its body and every caller moved to
   five, group status stayed `applied`, and CI's `mm/khugepaged.c` compile was
   the first check that noticed.
3. **Comment-structure damage.** A new block that starts with the ` */`
   terminator closes the enclosing comment and turns the following comment
   body lines into code (broke the whole `sched/features.h` translation unit
   in CI).  Comment *additions* must be inserted *before* the closing ` */`.
4. **A step stranded behind an early return.** A group whose `_apply` probes
   a shape and returns `already_present` before calling `apply_steps` will
   never run *any* of its steps on a tree matching that shape.  If one hunk
   comes from a later sublevel than the shape probe recognizes, it needs its
   own group — this is why the 5.15.195 `replace_fd()` fix is
   `fdtable_replace_fd_errno` rather than a step inside
   `fdtable_alloc_conventions` (which returns early from 5.15.191 onwards).

Traps 1–3 are enforced per step by `tests/step_audit.py`: every step of a
group that must really apply reports `applied` on the pristine tree (never
`already_present` — the audit consumes `apply_steps`' per-step return values,
so a mid-group collision fails too), and the `/*`/`*/`, `{`/`}` and
`#if`/`#endif` balance of each touched file is unchanged.  Trap 4 shows up as a
group reporting `already_present` on a newer baseline while the hunk you
expected is missing — run the dry-run on all supported baselines to catch it.

## Convention traps (compile clean, behave wrong)

A graft lifted from a newer kernel can compile perfectly and still be wrong
because the 5.15 helper it calls has a different return convention. Check every
helper the ported code calls against *its own tree*, not the source tree:

- `hugepage_vma_revalidate()` returns **0 on success** on 5.15 and
  `SCAN_SUCCEED` (= 1) from 6.1 onwards. Copying 6.1's
  `if (result != SCAN_SUCCEED)` makes success look like failure.
- The other callers in the same file are the reference: if they write
  `if (result)`, so should the graft.

`tests/implementation_audit.py` pins these as required content strings, which is
the only mechanism that catches them — `step_audit.py` and the compiler cannot.

