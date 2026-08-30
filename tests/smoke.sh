#!/usr/bin/env bash
# End-to-end smoke test for abk_5_15_backport.
#
# Builds a disposable KERNEL_ROOT from a source kernel tree (only the files
# the module touches), runs setup.sh for both children twice (idempotency),
# and asserts the reported statuses and in-tree markers.
#
# Usage:
#   bash tests/smoke.sh /path/to/android13-5.15-common-kernel-tree
#
# The source tree only needs the paths listed in SMOKE_FILES; a full checkout
# works as-is.

set -euo pipefail

MODULE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_TREE="${1:-${SMOKE_SOURCE_TREE:-}}"

if [ -z "$SOURCE_TREE" ]; then
  echo "usage: bash tests/smoke.sh /path/to/kernel-common-tree" >&2
  echo "(or set SMOKE_SOURCE_TREE; a tree at ../linux-common-android13-5.15 is picked up automatically)" >&2
  exit 2
fi
if [ ! -d "$SOURCE_TREE" ]; then
  echo "source tree not found: $SOURCE_TREE" >&2
  exit 2
fi

SMOKE_FILES=(
  Makefile
  fs/file.c
  mm/page_alloc.c
  mm/internal.h
  mm/oom_kill.c
  include/linux/cgroup-defs.h
  include/linux/cpuset.h
  include/linux/mmzone.h
  include/linux/randomize_kstack.h
  include/linux/sched.h
  kernel/cgroup/cgroup-internal.h
  kernel/cgroup/cgroup.c
  kernel/cgroup/cpuset.c
  kernel/sched/sched.h
  kernel/sched/core.c
  kernel/sched/fair.c
  kernel/sched/rt.c
  kernel/sched/features.h
  kernel/sched/stats.h
  kernel/sched/psi.c
  kernel/fork.c
  kernel/locking/semaphore.c
  init/main.c
  net/core/sock.c
  block/blk-mq.c
  arch/arm64/configs/gki_defconfig
)

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
KERNEL_ROOT="$WORK/root"
mkdir -p "$KERNEL_ROOT/common" "$KERNEL_ROOT/build/kernel"

for rel in "${SMOKE_FILES[@]}"; do
  mkdir -p "$KERNEL_ROOT/common/$(dirname "$rel")"
  cp "$SOURCE_TREE/$rel" "$KERNEL_ROOT/common/$rel"
done

export KERNEL_ROOT
export DEFCONFIG="$KERNEL_ROOT/common/arch/arm64/configs/gki_defconfig"
export CUSTOM_EXTERNAL_MODULE_STAGE="after_patch"
unset ABK_MODULE_CHILD_ID || true

python_bin="$(command -v python3 || command -v python)"

echo "== pass 1: graft =="
bash "$MODULE_DIR/setup.sh" >"$WORK/pass1.log" 2>&1 || { cat "$WORK/pass1.log"; exit 1; }
tail -2 "$WORK/pass1.log"
cp -r "$KERNEL_ROOT/abk_5_15_backport_reports" "$WORK/pass1_reports"

echo "== pass 2: idempotency =="
bash "$MODULE_DIR/setup.sh" >"$WORK/pass2.log" 2>&1 || { cat "$WORK/pass2.log"; exit 1; }
tail -2 "$WORK/pass2.log"

echo "== assertions =="
fail() { echo "FAIL: $*" >&2; exit 1; }

grep -q '{"applied": 7}' "$WORK/pass1.log" || fail "pass 1 did not report 7 applied groups per child"
grep -q '{"already_present": 7}' "$WORK/pass2.log" || fail "pass 2 was not idempotent"

for child in stable_backport_core stable_perf_backport; do
  [ -f "$WORK/pass1_reports/$child/${child}_report.json" ] || fail "missing pass1 report for $child"
  [ -f "$KERNEL_ROOT/abk_5_15_backport_reports/$child/${child}_report.json" ] || fail "missing pass2 report for $child"
  "$python_bin" - "$WORK/pass1_reports/$child/${child}_report.json" "$KERNEL_ROOT/abk_5_15_backport_reports/$child/${child}_report.json" <<'PY'
import json, sys
p1 = json.load(open(sys.argv[1]))
p2 = json.load(open(sys.argv[2]))
assert p1["status_summary"] == {"applied": 7}, p1["status_summary"]
assert p2["status_summary"] == {"already_present": 7}, p2["status_summary"]
PY
done

grep -q "alloc_fdtable(unsigned int slots_wanted)" "$KERNEL_ROOT/common/fs/file.c" \
  || fail "fdtable conventions marker missing"
grep -q "ABK stable_515_backport" "$KERNEL_ROOT/common/fs/file.c" \
  || fail "module marker missing in fs/file.c"
grep -q "ANDROID_KABI_USE(8" "$KERNEL_ROOT/common/include/linux/sched.h" \
  || fail "kstack KABI slot marker missing"
grep -q "cgroup_free_wq" "$KERNEL_ROOT/common/kernel/cgroup/cgroup.c" \
  || fail "cgroup wq split marker missing"
grep -q "__raise_softirq_irqoff(SCHED_SOFTIRQ);" "$KERNEL_ROOT/common/kernel/sched/core.c" \
  || fail "nohz core.c marker missing"

# rollback must restore the pristine tree
bash "$MODULE_DIR/scripts/abk_rollback.sh" "$KERNEL_ROOT/common" --list >/dev/null
bash "$MODULE_DIR/scripts/abk_rollback.sh" "$KERNEL_ROOT/common" --apply >/dev/null
[ -z "$(find "$KERNEL_ROOT/common" -name '*.abk-orig')" ] || fail "rollback left .abk-orig files behind"
if git -C "$SOURCE_TREE" rev-parse >/dev/null 2>&1 \
   && diff -q "$SOURCE_TREE/fs/file.c" "$KERNEL_ROOT/common/fs/file.c" >/dev/null 2>&1; then
  echo "rollback verified byte-identical for fs/file.c"
fi

echo "SMOKE OK"
