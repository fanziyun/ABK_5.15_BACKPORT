#!/usr/bin/env bash
# Fetch a minimal reference tree for one android13-5.15 GKI baseline.
#
# Only the files the module's groups touch are downloaded (via gitiles, so no
# clone of the ~4GB kernel history is needed).  The result is a directory that
# tests/smoke.sh and tests/step_audit.py accept as a source tree.
#
# Usage:
#   bash tests/fetch_sublevel_tree.sh <branch> <outdir>
#
# Known android13-5.15 branches (see build.yml KNOWN_KERNEL_PAIRS):
#   deprecated/android13-5.15-2024-11   SUBLEVEL 167  (os_patch_level 2024-11)
#   deprecated/android13-5.15-2025-03   SUBLEVEL 178  (os_patch_level 2025-03)
#   android13-5.15-2025-12              SUBLEVEL 194  (os_patch_level 2025-12)

set -euo pipefail

BRANCH="${1:-}"
OUTDIR="${2:-}"

if [ -z "$BRANCH" ] || [ -z "$OUTDIR" ]; then
  sed -n '2,17p' "$0" >&2
  exit 2
fi

BASE="https://android.googlesource.com/kernel/common/+/refs/heads/$BRANCH"

# Every path the groups read or write, plus Makefile (sublevel) and the
# defconfig the children require on the command line.
FETCH_FILES=(
  Makefile
  arch/arm64/configs/gki_defconfig
  Documentation/admin-guide/kernel-parameters.txt
  fs/file.c
  mm/page_alloc.c
  mm/compaction.c
  mm/internal.h
  kernel/rcu/Kconfig
  kernel/rcu/tree_nocb.h
  mm/oom_kill.c
  mm/vmscan.c
  mm/memcontrol.c
  mm/zsmalloc.c
  include/linux/swap.h
  include/linux/cgroup-defs.h
  include/linux/cpuset.h
  include/linux/mmzone.h
  include/linux/randomize_kstack.h
  include/linux/sched.h
  include/linux/psi_types.h
  include/linux/psi.h
  include/linux/zsmalloc.h
  include/trace/hooks/dtask.h
  include/trace/hooks/rwsem.h
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
  kernel/locking/mutex.c
  kernel/locking/rwsem.c
  init/main.c
  net/core/sock.c
  block/blk-mq.c
  drivers/block/zram/Kconfig
  drivers/block/zram/zram_drv.h
  drivers/block/zram/zram_drv.c
  mm/Kconfig
  mm/khugepaged.c
  mm/madvise.c
  include/linux/huge_mm.h
  include/uapi/asm-generic/mman-common.h
  drivers/gpu/drm/drm_atomic_helper.c
)

decode() {
  if command -v base64 >/dev/null 2>&1; then
    base64 -d
  else
    python3 -c 'import base64,sys; sys.stdout.buffer.write(base64.b64decode(sys.stdin.buffer.read()))'
  fi
}

mkdir -p "$OUTDIR"
echo "fetching ${#FETCH_FILES[@]} files from $BRANCH into $OUTDIR"

failed=0
for rel in "${FETCH_FILES[@]}"; do
  dst="$OUTDIR/$rel"
  mkdir -p "$(dirname "$dst")"
  # gitiles serves base64 with ?format=TEXT; retry because it rate-limits.
  if ! curl -sSL --max-time 180 --retry 3 --retry-delay 2 \
      "$BASE/$rel?format=TEXT" | decode > "$dst"; then
    echo "  FAILED $rel" >&2
    failed=$((failed + 1))
    continue
  fi
  # A rate-limited or missing path yields an empty/HTML body, not source.
  size=$(wc -c < "$dst")
  if [ "$size" -lt 200 ]; then
    echo "  SUSPECT $rel ($size bytes) - probably rate-limited, re-run" >&2
    failed=$((failed + 1))
  fi
done

if [ "$failed" -ne 0 ]; then
  echo "$failed file(s) did not download cleanly; re-run to fill the gaps" >&2
  exit 1
fi

sublevel="$(awk '$1 == "SUBLEVEL" && $2 == "=" { print $3; exit }' "$OUTDIR/Makefile")"
echo "OK: $OUTDIR is 5.$(awk '$1 == "PATCHLEVEL" && $2 == "=" { print $3; exit }' "$OUTDIR/Makefile").$sublevel"
echo "next: ABK_TEST_SUB_LEVEL=$sublevel bash tests/smoke.sh $OUTDIR"
