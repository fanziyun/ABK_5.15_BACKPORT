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

- `python3 -m py_compile scripts/*.py tests/*.py && bash -n setup.sh scripts/*.sh tests/*.sh`
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
- Full smoke (two-pass idempotency + rollback):
  `bash tests/smoke.sh <tree>`.
- Extend `tests/smoke.sh` grep assertions if the group lands a load-bearing
  marker — gate the assertion on `sublevel_matrix.applies()` when the marker
  only exists on baselines where the group really rewrites the file.  Then tick
  the `plan.md` checkbox and bump `ABK_MODULE_VERSION`/`ABK_MODULE_SET_VERSION`
  in `module.conf`.

## Step-authoring traps (compile-breaking, hidden at group level)

Three traps slip past group statuses ("applied") and only surface at compile
time; `tests/step_audit.py` catches them on a pristine tree:

1. **Replacement blocks that pre-exist in the file.** `replace_once` checks
   the *new* block first (idempotency).  If `new` is a common pattern or a
   prefix of `old` (line deletions!), the step short-circuits to
   `already_present` and the real edit never lands.  Fix: re-anchor `old` and
   `new` with unique surrounding context (e.g. the `return newf;` before the
   `out_release:` label) so `new` cannot exist before the step runs.
2. **Comment-structure damage.** A new block that starts with the ` */`
   terminator closes the enclosing comment and turns the following comment
   body lines into code (broke the whole `sched/features.h` translation unit
   in CI).  Comment *additions* must be inserted *before* the closing ` */`.
3. **A step stranded behind an early return.** A group whose `_apply` probes
   a shape and returns `already_present` before calling `apply_steps` will
   never run *any* of its steps on a tree matching that shape.  If one hunk
   comes from a later sublevel than the shape probe recognizes, it needs its
   own group — this is why the 5.15.195 `replace_fd()` fix is
   `fdtable_replace_fd_errno` rather than a step inside
   `fdtable_alloc_conventions` (which returns early from 5.15.191 onwards).

Traps 1 and 2 are enforced per step by `tests/step_audit.py`: every step of a
group that must really apply reports `applied` on the pristine tree (never
`already_present`), and the `/*`/`*/`, `{`/`}` and `#if`/`#endif` balance of
each touched file is unchanged.  Trap 3 shows up as a group reporting
`already_present` on a newer baseline while the hunk you expected is missing —
run the dry-run on all three baselines to catch it.
