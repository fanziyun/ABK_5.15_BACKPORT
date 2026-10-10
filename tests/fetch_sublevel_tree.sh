#!/usr/bin/env bash
# Fetch a minimal reference tree for the one supported android13-5.15 baseline.
#
# Only the files the module's groups touch are downloaded (via gitiles, so no
# clone of the ~4GB kernel history is needed).  The result is a directory that
# tests/smoke.sh and tests/step_audit.py accept as a source tree.
#
# Usage:
#   bash tests/fetch_sublevel_tree.sh <branch> <outdir>
#   bash tests/fetch_sublevel_tree.sh --self-test-decode   # decode() only
#   bash tests/fetch_sublevel_tree.sh --decode-only          # decode stdin->stdout
#
# Since Batch 44 the rolling branch is the only supported baseline:
#   android13-5.15-lts                     SUBLEVEL rolls (217 as of 2026-09)
#
# The three release baselines that used to be listed here
# (deprecated/android13-5.15-2024-11 = .167, -2025-03 = .178,
# android13-5.15-2025-12 = .194) are no longer supported; nothing in the
# registry depends on them, but the audits will refuse them -- see
# tests/sublevel_matrix.py.
#
# Note this is a ROLLING branch: after re-fetching, re-key the lts row in
# tests/sublevel_matrix.py to the new Makefile SUBLEVEL and re-prove every set,
# or every audit fails with "no expectation recorded for sublevel ...".

set -euo pipefail

BRANCH="${1:-}"
OUTDIR="${2:-}"
SELF_TEST=0
DECODE_ONLY=0

# --self-test-decode and --decode-only exercise the decoder on its own and need
# no arguments.  Both are dispatched after decode() is defined, so the flag only
# reserves the slot here.
if [ "${1:-}" = "--self-test-decode" ]; then
  SELF_TEST=1
elif [ "${1:-}" = "--decode-only" ]; then
  DECODE_ONLY=1
elif [ -z "$BRANCH" ] || [ -z "$OUTDIR" ]; then
  sed -n '2,25p' "$0" >&2
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
  mm/vmstat.c
  mm/memcontrol.c
  # Batch 37 (MGLRU v4 performance series): the workingset and deactivation
  # groups anchor in these.
  mm/workingset.c
  mm/swap.c
  # Batch 49 (MGLRU v7.2 reclaim-loop rework): the prefault placement group
  # (6cbdd9726fb5) rewrites lru_gen_add_page()'s generation formula here.
  include/linux/mm_inline.h
  mm/zsmalloc.c
  include/linux/swap.h
  include/linux/cgroup-defs.h
  include/linux/cpuset.h
  include/linux/mmzone.h
  include/linux/memcontrol.h
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
  # The of/address.c ranges-parser overlay (scripts/stable_backport.sh
  # abk_stable_backport_overlay_of_address) requires the target file to exist
  # before it can read its rework gate, so smoke.sh aborted with "required file
  # not found" on every freshly fetched tree.  It is a file overlay, not a
  # Python graft, so no group declares it and step_audit's fixture-coverage
  # check cannot see the gap.
  drivers/of/address.c
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
  # Batch 35: the page-cache shadow-entry sweeps (mm/truncate.c) and the
  # struct zap_details the MADV_DONTNEED page-table reclaim marks (mm.h).
  mm/truncate.c
  include/linux/mm.h
  # Batch 34: the non-return per-CPU atomics become load LSE atomics
  # (mainline 535fdfc5a228) -- the whole group is this header.
  arch/arm64/include/asm/percpu.h
  # Batch 35: the FUSE write-path prefault (faa794dd2e17).  The module's first
  # group in fs/fuse/ -- the file is the whole group.
  fs/fuse/file.c
  # Batch 40: the erofs readahead temporary-buffer relaxation (d9281660ff3f).
  # The module's first group in fs/erofs/.  Five files carry anchors: the
  # request struct (compress.h), the two decompressors that allocate the
  # temporary bounce pages, the pcluster the state bit rides on (zdata.h --
  # note the struct is in the header on 5.15, not in zdata.c as upstream has
  # it), and zdata.c, which decides on the readahead path that those
  # allocations are allowed to fail.
  fs/erofs/compress.h
  fs/erofs/decompressor.c
  fs/erofs/decompressor_lzma.c
  fs/erofs/zdata.c
  fs/erofs/zdata.h
  # Batch 41: vm_kcompressd_swapout -- the module's first mm/page_io.c group,
  # and the file the whole payload lives in (upstream's carrier, a struct
  # pglist_data field run, is KMI-frozen on this baseline, so nothing else is
  # touched).  Every anchor is pristine text no other group writes, and the
  # frontswap_store() block the shape probe discriminates sits in the same
  # translation unit as the engine.
  mm/page_io.c
  # Batch 70: smaps_migration_guard -- the module's first fs/proc group;
  # the helper plus five caller guards all live in this one file.
  fs/proc/task_mmu.c
)

# gitiles serves the body as base64 wrapped at 76 columns.
#
# A body carrying a byte outside the base64 alphabet must fail loudly.  The
# failure this guards is silent by construction: shifted text still looks like
# source, so it passes every check in acceptable() below (size, NUL-free,
# not-HTML) and lands in the tree as a file nobody questions.  That is not
# hypothetical -- fs/erofs/compress.h once arrived as
# "DD..X-License-Identifier" in place of "/* SPDX-License-Identifier", and
# step_audit then reported erofs_readahead_relaxed_gfp as blocked_by_shape on a
# pristine tree, which reads as anchor drift rather than a broken fetch.
#
# Both backends therefore *validate*, and they validate differently, which is
# why both are here rather than one being a fallback:
#   - python3: b64decode() without validate=True silently DISCARDS bytes outside
#     the alphabet and returns success (measured: a body carrying a stray byte
#     decoded to the correct 40 bytes with rc=0).  validate=True raises.
#   - base64:  GNU coreutils >= 9 is already strict (rc=1 on the same body).
#
# The CR/LF strip is load-bearing rather than decorative, and it is what the
# python3 branch needs: with validate=True a newline is "not base64 data", so a
# still-wrapped 76-column body is rejected outright (measured) unless the
# wrapping is stripped first.
decode() {
  if [ -n "${ABK_FETCH_DECODER:-}" ]; then
    _d_backend="${ABK_FETCH_DECODER}"
  elif command -v base64 >/dev/null 2>&1; then
    _d_backend=base64
  else
    _d_backend=python3
  fi
  case "$_d_backend" in
  base64)
    tr -d '\r\n' | base64 -d
    ;;
  python3)
    tr -d '\r\n' | python3 -c 'import base64,binascii,sys
try:
    sys.stdout.buffer.write(base64.b64decode(sys.stdin.buffer.read(), validate=True))
except (binascii.Error, ValueError) as e:
    sys.stderr.write("abk: gitiles body is not clean base64: %s\n" % e)
    sys.exit(1)'
    ;;
  *)
    echo "abk: unknown ABK_FETCH_DECODER '$_d_backend' (want base64 or python3)" >&2
    return 1
    ;;
  esac
}

# Both backends run over the same two bodies, so neither can regress into
# silent-skipping on its own.  The python3 one is the one that could: without
# validate=True it accepts the corrupted body and exits 0, so weakening the
# check turns this red instead of quietly passing on base64's strictness.
decode_backend_available() {
  command -v "$1" >/dev/null 2>&1
}

self_test_decode() {
  _s_rc=0
  # The clean body as a pre-encoded literal, NOT `printf ... | base64`: under
  # `set -euo pipefail` a pipe through a missing `base64` returns 127 and set -e
  # aborts the whole function here -- which is exactly the host (python3, no
  # base64) decode()'s python3 fallback and the loop's per-backend skip exist
  # to cover, so building the vector with base64 would skip the python3 backend
  # on the one host it must be tested on.  This is base64 of
  # "/* SPDX-License-Identifier: GPL-2.0 */\nint x;\n".
  _s_clean="LyogU1BEWC1MaWNlbnNlLUlkZW50aWZpZXI6IEdQTC0yLjAgKi8KaW50IHg7Cg=="
  for _s_backend in base64 python3; do
    if ! decode_backend_available "$_s_backend"; then
      echo "self-test: $_s_backend not on this host, skipped"
      continue
    fi
    if printf '%s\n' "$_s_clean" | ABK_FETCH_DECODER="$_s_backend" decode | grep -q 'SPDX-License-Identifier'; then
      echo "self-test: $_s_backend decodes the clean body"
    else
      echo "self-test: $_s_backend FAILED to decode the clean body" >&2
      _s_rc=1
    fi
    # The case with teeth: a stray byte inside the stream.  Without
    # validate=True the python3 backend accepts this and exits 0.
    if printf '%s\n' "${_s_clean:0:8}!${_s_clean:8}" | ABK_FETCH_DECODER="$_s_backend" decode >/dev/null 2>&1; then
      echo "self-test: $_s_backend ACCEPTED a corrupted body (must be refused)" >&2
      _s_rc=1
    else
      echo "self-test: $_s_backend refused the corrupted body"
    fi
  done
  return "$_s_rc"
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

if [ "$SELF_TEST" = "1" ]; then
  self_test_decode && exit 0
  exit 1
fi

if [ "$DECODE_ONLY" = "1" ]; then
  decode
  exit $?
fi

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
