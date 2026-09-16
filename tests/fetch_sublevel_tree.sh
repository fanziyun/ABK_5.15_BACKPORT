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
  Documentation/admin-guide/cgroup-v2.rst
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
  include/trace/hooks/mm.h
  include/trace/hooks/rwsem.h
  drivers/android/vendor_hooks.c
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
  kernel/sched/cpufreq_schedutil.c
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
  mm/readahead.c
  mm/filemap.c
  mm/khugepaged.c
  mm/madvise.c
  include/linux/huge_mm.h
  include/uapi/asm-generic/mman-common.h
  drivers/gpu/drm/drm_atomic_helper.c
  # Batch 15 (ABK_ABI_PATCH_SUITE absorption): the files the absorbed
  # optimization groups anchor in.  Note io_uring/io_uring.c is the 5.15
  # monolith (~11k lines) -- 6.1 split it into 56 files, so the suite's
  # io_uring/*.c groups target a layout that does not exist here.
  kernel/pid.c
  mm/slub.c
  mm/huge_memory.c
  mm/memory.c
  mm/swap_state.c
  include/linux/blkdev.h
  block/blk-core.c
  block/blk-mq-sched.c
  block/blk-sysfs.c
  # blk_mq_quiesced_elevator_switch (5.15.209) moves the elevator_switch_mq()
  # declaration in here, and the file is the group's rename->user chain anchor.
  block/blk.h
  block/elevator.c
  block/mq-deadline.c
  block/bfq-iosched.c
  block/kyber-iosched.c
  include/linux/sched/nohz.h
  include/linux/tick.h
  kernel/time/tick-sched.c
  kernel/sched/idle.c
  kernel/bpf/helpers.c
  io_uring/io_uring.c
  # Batch 31: the arm64 pte_mkwrite() dirty guard (5.15.196).  The module's
  # first arch/arm64 C source -- the file is the whole group.
  arch/arm64/include/asm/pgtable.h
  # Batch 34: the non-return per-CPU atomics become load LSE atomics
  # (mainline 535fdfc5a228) -- the whole group is this header.
  arch/arm64/include/asm/percpu.h
)

decode() {
  if command -v base64 >/dev/null 2>&1; then
    base64 -d
  else
    python3 -c 'import base64,sys; sys.stdout.buffer.write(base64.b64decode(sys.stdin.buffer.read()))'
  fi
}

# A downloaded file is accepted only when it looks like source: large enough
# and free of NUL bytes.  A rate-limited gitiles reply is a short non-text body,
# and it used to be written straight over the destination -- so re-running to
# "fill the gaps" silently corrupted files that had already downloaded fine
# (observed: init/main.c and 5 others truncated to 6 bytes on a retry pass).
acceptable() {
  _a_f="$1"
  [ -s "$_a_f" ] || return 1
  _a_size=$(wc -c < "$_a_f")
  [ "$_a_size" -ge 200 ] || return 1
  # Binary junk (a truncated base64 body) decodes to bytes with NULs in them;
  # a NUL-free size that matches the file's own is source text.  Command
  # substitution cannot express a NUL pattern, so compare lengths instead.
  [ "$(tr -d '\000' < "$_a_f" | wc -c)" = "$_a_size" ] || return 1
  # A rate-limited reply is an HTML page, and it can easily clear 200 bytes.
  if head -c 512 "$_a_f" | tr 'A-Z' 'a-z' | grep -q "<\(html\|!doctype\|body\)"; then
    return 1
  fi
  return 0
}

mkdir -p "$OUTDIR"
echo "fetching ${#FETCH_FILES[@]} files from $BRANCH into $OUTDIR"

failed=0
for rel in "${FETCH_FILES[@]}"; do
  dst="$OUTDIR/$rel"
  # Gap-filling, not re-downloading: a complete tree is a no-op, so a retry
  # pass cannot damage a file that is already good.
  if acceptable "$dst"; then
    continue
  fi
  mkdir -p "$(dirname "$dst")"
  part="$dst.part"
  # gitiles serves base64 with ?format=TEXT; retry because it rate-limits.
  if ! curl -sSL --max-time 180 --retry 3 --retry-delay 2 \
      "$BASE/$rel?format=TEXT" | decode > "$part"; then
    echo "  FAILED $rel" >&2
    rm -f "$part"
    failed=$((failed + 1))
    continue
  fi
  if acceptable "$part"; then
    mv "$part" "$dst"
  else
    echo "  SUSPECT $rel ($(wc -c < "$part") bytes) - probably rate-limited, re-run" >&2
    rm -f "$part"
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
