#!/usr/bin/env bash
# Run the aarch64 loader on this host and prove it parses the shipped object.
#
#   bash tools/scx/run_arm64_selftest.sh <grafted-kernel-tree> [workdir]
#
# The committed loader is a *dynamic* bionic binary: it needs /system/bin/linker64
# and the Android libc, so it cannot run on a build host.  This script therefore
# links a second, **static** aarch64 build of the same sources, runs it under
# qemu-user, and diffs its "selftest" against the host's x86_64 build.  That is
# what turns "the artefact is an AArch64 ELF" into "the AArch64 code path parses
# this object the same way the x86_64 one does" -- before anyone flashes it.
#
# It also exercises the two CLI paths that must not crash: usage (no arguments)
# and a load that the kernel refuses.
#
# Run it in WSL (or any host with qemu-user and the NDK reachable from it); the
# headers staged by tools/scx/build_android_loader.sh in <workdir>/work/inc are
# reused, so run that first.
set -u

TREE="${1:-}"
WORKDIR="${2:-build/scx-android}"
# This script runs where qemu-user *and* the NDK are both reachable, which in
# practice is WSL with the Windows SDK mounted under /mnt/c.  Accept either
# mount style so an explicit NDK= is never required.
NDK="${NDK:-}"
if [ -z "$NDK" ]; then
  for _cand in \
      /mnt/c/Users/Administrator/AppData/Local/Android/Sdk/ndk/28.2.13676358 \
      /c/Users/Administrator/AppData/Local/Android/Sdk/ndk/28.2.13676358; do
    if [ -x "$_cand/toolchains/llvm/prebuilt/windows-x86_64/bin/clang.exe" ]; then
      NDK="$_cand"
      break
    fi
  done
fi

MODULE_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
BIN="$NDK/toolchains/llvm/prebuilt/windows-x86_64/bin"
SYSROOT="$NDK/toolchains/llvm/prebuilt/windows-x86_64/sysroot"
QEMU="${QEMU:-qemu-aarch64-static}"
OBJ="${OBJ:-$MODULE_DIR/tools/scx/prebuilt/abk_scx_min.bpf.o}"

if [ -z "$TREE" ] || [ ! -d "$TREE/tools/lib/bpf" ]; then
  sed -n '2,12p' "$0" >&2
  exit 2
fi
if ! command -v "$QEMU" >/dev/null 2>&1; then
  echo "run_arm64_selftest: $QEMU not found (install qemu-user or set QEMU=...)" >&2
  exit 2
fi
INC="$WORKDIR/work/inc"
if [ -z "$NDK" ] || [ ! -x "$BIN/clang.exe" ]; then
  echo "run_arm64_selftest: no NDK clang (set NDK=<ndk-dir>)" >&2
  exit 2
fi
if [ ! -d "$INC/libbpf-src" ]; then
  echo "run_arm64_selftest: $INC is not staged; run tools/scx/build_android_loader.sh first" >&2
  exit 2
fi

if ! command -v wslpath >/dev/null 2>&1; then
  echo "run_arm64_selftest: needs wslpath (run this under WSL)" >&2
  exit 2
fi
w() { wslpath -w "$1"; }
INCARGS="-I$(w "$MODULE_DIR/tools/scx/android-compat") -I$(w "$MODULE_DIR/tools/scx/libelf-shim") -I$(w "$INC/libbpf-src") -I$(w "$INC") -I$(w "$INC/tools/include/uapi")"
CFLAGS="--target=aarch64-linux-android30 --sysroot=$(w "$SYSROOT") -O2 -Wall -Wno-switch-enum -fvisibility=hidden -D_LARGEFILE64_SOURCE -D_FILE_OFFSET_BITS=64 -static"

echo "run_arm64_selftest: linking a static aarch64 loader for qemu-user"
# shellcheck disable=SC2086
"$BIN/clang.exe" $CFLAGS $INCARGS -include "$(w "$MODULE_DIR/tools/scx/android-compat/kernel-macros.h")" \
  -o "$(w "$WORKDIR/scx_loader_static")" \
  "$(w "$MODULE_DIR/tools/scx/scx_loader.c")" \
  "$(w "$MODULE_DIR/tools/scx/libelf-shim/elf_shim.c")" \
  "$(w "$WORKDIR/work/libbpf.a")" -lz || exit 1
file "$WORKDIR/scx_loader_static" 2>/dev/null | sed 's/^/  /'

echo "run_arm64_selftest: qemu-user selftest"
"$QEMU" "$WORKDIR/scx_loader_static" selftest "$OBJ" > "$WORKDIR/selftest-arm64.txt" 2>&1
_rc_arm=$?
echo "  exit $_rc_arm"
cat "$WORKDIR/selftest-arm64.txt" | sed 's/^/  /'

echo "run_arm64_selftest: usage path (must be exit 2)"
"$QEMU" "$WORKDIR/scx_loader_static" > "$WORKDIR/usage-arm64.txt" 2>&1
_rc_usage=$?
echo "  exit $_rc_usage"
head -2 "$WORKDIR/usage-arm64.txt" | sed 's/^/  /'

echo "run_arm64_selftest: load path on a kernel without sched_ext (must fail cleanly)"
"$QEMU" "$WORKDIR/scx_loader_static" run "$OBJ" > "$WORKDIR/run-arm64.txt" 2>&1
_rc_run=$?
echo "  exit $_rc_run"
tail -1 "$WORKDIR/run-arm64.txt" | sed 's/^/  /'

echo "run_arm64_selftest: host x86_64 selftest"
BUILD_HOST_LOADER=1 bash "$MODULE_DIR/tools/build_scx_artifacts.sh" "$TREE" "$WORKDIR/host" >"$WORKDIR/host-build.log" 2>&1 || {
  echo "run_arm64_selftest: the host build failed; see $WORKDIR/host-build.log" >&2
  exit 1
}
"$WORKDIR/host/scx_loader" selftest "$WORKDIR/host/abk_scx_min.bpf.o" > "$WORKDIR/selftest-x86.txt" 2>&1

if ! diff -u "$WORKDIR/selftest-x86.txt" "$WORKDIR/selftest-arm64.txt"; then
  echo "run_arm64_selftest: the aarch64 parse differs from the x86_64 one" >&2
  exit 1
fi
echo "run_arm64_selftest: SELFTEST IDENTICAL ACROSS ARCH"
[ "$_rc_usage" -eq 2 ] || { echo "run_arm64_selftest: the usage path did not exit 2" >&2; exit 1; }
[ "$_rc_run" -ne 0 ] || { echo "run_arm64_selftest: a load on a kernel without sched_ext must fail" >&2; exit 1; }
exit 0
