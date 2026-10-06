#!/usr/bin/env bash
# Fetch a minimal reference tree for the one supported android13-5.15 baseline.
#
# Only the files the module's groups touch are downloaded (via gitiles, so no
# clone of the ~4GB kernel history is needed).  The result is a directory that
# tests/smoke.sh and tests/step_audit.py accept as a source tree.
#
# Usage:
#   bash tests/fetch_sublevel_tree.sh <branch> <outdir>
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

if [ -z "$BRANCH" ] || [ -z "$OUTDIR" ]; then
  sed -n '2,23p' "$0" >&2
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
  # Batch 58 (sched_ext S2b-2b): the fork-hook prototypes this module changes
  # live here, and kernel/fork.c includes it.
  include/linux/sched/task.h
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
  # Batch 50 (Tier A1): sched_sis_util -- SIS_UTIL keeps its scan-depth hint in
  # struct sched_domain_shared (nr_idle_scan), so this header carries an anchor
  # for the first time.  kernel/sched/sched.h (cfs_rq::idle_nr_running) and
  # kernel/sched/features.h (SCHED_FEAT(SIS_UTIL)) are already in the tree.
  include/linux/sched/topology.h
  # sched_ext (S1/S1b): the arm64 BPF JIT gains a trampoline and
  # bpf_arch_text_poke(), and the kfunc allow-list API (btf_kfunc_id_set) is an
  # incremental verifier change.  bpf_jit_comp.c/bpf_jit.h/insn.h are the
  # module's first arch/arm64/net and first arch/arm64/include/asm/insn.h
  # entries; kernel/bpf/trampoline.c pins the 5.15 trampoline interface these
  # groups have to match (bpf_tramp_progs + __bpf_prog_enter(prog), which is
  # NOT the v6.1 bpf_tramp_links + run_ctx shape the upstream patches assume).
  arch/arm64/net/bpf_jit_comp.c
  arch/arm64/net/bpf_jit.h
  arch/arm64/include/asm/insn.h
  # the aarch64_insn_gen_load_literal()/gen_load_store_imm() implementation the
  # arm64 bpf_plt needs (struct bpf_plt emits A64_LDR64LIT).  Note the file is
  # arch/arm64/lib/insn.c on this branch -- arch/arm64/kernel/insn.c is a 404
  # here (verified against the branch's own directory listing), so an upstream
  # patch path of kernel/insn.c has to be re-pointed, not copied.
  arch/arm64/lib/insn.c
  include/linux/bpf.h
  kernel/bpf/trampoline.c
  include/linux/btf.h
  # Batch 53 (S1b): the kfunc allow-list API.  include/linux/btf_ids.h is where
  # the BTF_SET8_START/END + BTF_ID_FLAGS macros kernel/sched/ext.c (S2) spells
  # have to live; btf.c holds the set registry and verifier.c consults it.
  include/linux/btf_ids.h
  kernel/bpf/btf.c
  kernel/bpf/verifier.c
  # Batch 55 (sched_ext S2b-1): the build wiring that turns the Batch-54 payload
  # from inert text into a compiled kernel subsystem.  Kconfig.preempt carries
  # the new CONFIG_SCHED_CLASS_EXT symbol, include/uapi/linux/sched.h the
  # SCHED_EXT policy id the engine compares p->policy against, the sched
  # Makefile the object rule, and vmlinux.lds.h the SCHED_DATA slot that puts
  # ext_sched_class between fair and idle in the class array (5.15 lays the
  # array out idle-first and walks it with class--).
  kernel/Kconfig.preempt
  include/uapi/linux/sched.h
  kernel/sched/Makefile
  include/asm-generic/vmlinux.lds.h
  # Batch 60 (sched_ext S2b-2b, second half, part 2): the reachability batch.
  # kernel/bpf/bpf_struct_ops_types.h is 5.15's registry of struct_ops map
  # types -- BPF_STRUCT_OPS_TYPE(sched_ext_ops) has to go into it, or the BPF
  # syscall has no sched_ext_ops value type for a scheduler to bind to.
  # kernel/sched/debug.c's sched_init_debug() is where the engine exposes its
  # /sys/kernel/debug/sched/ext dump.  Both are new files for the fixture lists.
  kernel/bpf/bpf_struct_ops_types.h
  kernel/sched/debug.c
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
