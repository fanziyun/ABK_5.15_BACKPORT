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

- `python3 -m py_compile scripts/*.py && bash -n setup.sh scripts/*.sh tests/smoke.sh`
- `python3 tests/stable_5_15_test.py` (add fixture checks if the group
  introduces a new shape probe).
- Dry-run against the reference tree:
  `python3 scripts/abk_stable_perf.py --common-dir <tree> --defconfig <tree>/arch/arm64/configs/gki_defconfig --report-dir /tmp/r --sub-level 167 --family android13-5.15 --dry-run`
  — every anchor must report `applied`/`already_present`, never
  `missing_anchor`.
- Full smoke (two-pass idempotency + rollback):
  `bash tests/smoke.sh <tree>`.
- Extend `tests/smoke.sh` grep assertions if the group lands a load-bearing
  marker, then tick the `plan.md` checkbox and bump
  `ABK_MODULE_VERSION`/`ABK_MODULE_SET_VERSION` in `module.conf`.

## Step-authoring traps (compile-breaking, hidden at group level)

Both traps slip past group statuses ("applied") and only surface at compile
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

Both rules are enforced per step by `tests/step_audit.py`: every step must
report `applied` on the pristine tree (never `already_present`), and the
`/*`/`*/` balance of each touched file must be unchanged.
