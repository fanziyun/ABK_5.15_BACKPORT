#!/bin/sh
# Local compile probe for the sched_ext (SCX) payload and its hooks.
#
# The repository's seven gates are all *text* gates: they prove the anchors land,
# the structure stays balanced, the behaviour-visible symbols survive and the
# CONFIG gates resolve -- but not one of them runs a compiler.  Every SCX batch
# therefore shipped with the compile gate unverified, and the first thing a real
# build found was a payload that does not compile on 5.15 at all (Batch 56).
#
# This is the missing gate.  It compiles the translation unit the payload is
# built through, plus every object this module rewrites for SCX, in a
# *configured* kernel tree with a working LLVM toolchain.  It is a build-host
# helper like tools/hunks.py and tools/fetch_all_trees.sh: it is not listed in
# ksu/abk_runtime_tunables/embed.conf, so it never ships to a device, and it
# writes nothing but build artifacts and its own log.
#
# Usage:
#   sh tools/compile_probe.sh <kernel-tree>
#
# The tree must already have a .config with CONFIG_SCHED_CLASS_EXT=y, i.e. it
# must have been built (or at least olddefconfig'ed) after the module was
# applied:
#
#   cd <tree>
#   scripts/config --enable SCHED_CLASS_EXT
#   make ARCH=arm64 LLVM=1 olddefconfig
#
# Env overrides:
#   PROBE_ARCH=arm64            ARCH= passed to make
#   PROBE_MAKE_ARGS="LLVM=1"    toolchain selection (LLVM=1 needs clang/lld)
#   PROBE_JOBS=<nproc>          -j value (only used when more than one object)
#   PROBE_OBJS="a.o b.o"        object list override
#   PROBE_LOG=<path>            where the build log goes
#
# Exit codes: 0 built, 1 compile errors (log has them), 2 setup problem.

TREE="$1"
if [ -z "$TREE" ]; then
  sed -n '2,40p' "$0" >&2
  exit 2
fi
if [ ! -d "$TREE" ]; then
  echo "compile_probe: not a directory: $TREE" >&2
  exit 2
fi
if [ ! -f "$TREE/Makefile" ] || [ ! -f "$TREE/.config" ]; then
  echo "compile_probe: $TREE is not a configured kernel tree (Makefile + .config)" >&2
  exit 2
fi

ARCH="$PROBE_ARCH"
[ -n "$ARCH" ] || ARCH=arm64
MAKE_ARGS="$PROBE_MAKE_ARGS"
[ -n "$MAKE_ARGS" ] || MAKE_ARGS=LLVM=1
JOBS="$PROBE_JOBS"
LOG="$PROBE_LOG"
[ -n "$LOG" ] || LOG=/tmp/abk_compile_probe.log

# The SCX translation unit first: it is the one that decides whether the 143 KB
# engine is real.  The rest are the objects this module rewrites for SCX or that
# the functional hooks land in, so the probe catches a break that only shows up
# once ext.c and the tree's own sched core meet.
OBJS="$PROBE_OBJS"
if [ -z "$OBJS" ]; then
  OBJS="kernel/sched/sched_ext_glue.o kernel/sched/core.o kernel/sched/fair.o kernel/sched/idle.o kernel/sched/debug.o kernel/fork.o kernel/bpf/bpf_struct_ops.o"
fi

if ! grep -q '^CONFIG_SCHED_CLASS_EXT=y' "$TREE/.config"; then
  echo "compile_probe: $TREE/.config has no CONFIG_SCHED_CLASS_EXT=y" >&2
  echo "  run: cd $TREE && scripts/config --enable SCHED_CLASS_EXT && make ARCH=$ARCH $MAKE_ARGS olddefconfig" >&2
  exit 2
fi

if [ -z "$JOBS" ]; then
  JOBS="$( (nproc 2>/dev/null) || echo 4 )"
fi

echo "compile_probe: tree=$TREE arch=$ARCH make_args=$MAKE_ARGS"
echo "compile_probe: objects=$OBJS"
echo "compile_probe: log=$LOG"

# One object is a fast, focused check; several is a wider one and deserves -j.
count=0
for _o in $OBJS; do
  count=$((count + 1))
done
if [ "$count" -gt 1 ]; then
  JOBS_ARG="-j$JOBS"
else
  JOBS_ARG=""
fi

# -k (keep going) so one broken translation unit does not mask the others: the
# first run of this probe stopped at the SCX unit and left "do the objects this
# module rewrites still build?" unanswered -- which is a question the probe
# exists to answer.  make still exits non-zero when any target failed.
# shellcheck disable=SC2086
( cd "$TREE" && make ARCH="$ARCH" $MAKE_ARGS -k $JOBS_ARG $OBJS ) >"$LOG" 2>&1
rc=$?

if [ "$rc" -eq 0 ]; then
  echo "compile_probe: OK ($count object(s) built)"
  exit 0
fi

echo "compile_probe: BUILD FAILED (make exit $rc)" >&2
# The first diagnostic per distinct file:line is what matters; the tail of a
# clang log is a cascade of follow-on errors from the same missing symbol.
errs="$(grep -E '^[^ ]+\.(c|h):[0-9]+:[0-9]+: error:' "$LOG" | sort -u)"
if [ -z "$errs" ]; then
  errs="$(grep -E 'error:' "$LOG" | sort -u | head -40)"
fi
n="$(printf '%s\n' "$errs" | grep -c . )"
echo "compile_probe: $n distinct compile error line(s):" >&2
printf '%s\n' "$errs" | head -40 >&2
echo "compile_probe: full log at $LOG" >&2
exit 1
