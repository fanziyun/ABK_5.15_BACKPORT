#!/usr/bin/env bash
# Build-host helper (NOT shipped to a device): build the sched_ext userspace
# artefacts this module's S3 work needs.
#
#   bash tools/build_scx_artifacts.sh <kernel-tree> [outdir]
#
# <kernel-tree> must be the *grafted* tree: the scheduler is compiled against a
# vmlinux.h dumped from that tree's own BTF, so it only sees struct
# sched_ext_ops if Batch 54-60's payload was applied before the kernel was
# built.  A vmlinux built before the graft has no sched_ext_ops in its BTF and
# the BPF compile fails with "unknown type name 'struct sched_ext_ops'" -- that
# failure is the check, not an accident.
#
# What it produces:
#   <outdir>/vmlinux.h            BTF dump of the grafted kernel
#   <outdir>/abk_scx_min.bpf.o    the minimal SCX scheduler, clang -target bpf
#
# The loader (scx_loader.c) is built in the next step of S3; this script prints
# the two toolchains it needs and the measured reason the Android one is not
# assembled here (see docs/survey_sched_ext_gap.md section 2h).
#
# Env:
#   CC_BPF   clang used for the -target bpf compile (default: clang)
#   BPFTOOL  bpftool binary (default: <tree>/tools/bpf/bpftool/bpftool)
set -euo pipefail

TREE="${1:-}"
OUTDIR="${2:-build/scx}"
if [ -z "$TREE" ] || [ ! -f "$TREE/Makefile" ] || [ ! -f "$TREE/.config" ]; then
  sed -n '2,25p' "$0" >&2
  exit 2
fi
if [ ! -f "$TREE/vmlinux" ]; then
  echo "build_scx_artifacts: $TREE/vmlinux is missing; build the grafted tree first" >&2
  exit 2
fi

MODULE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CC_BPF="${CC_BPF:-clang}"
BPFTOOL="${BPFTOOL:-$TREE/tools/bpf/bpftool/bpftool}"

mkdir -p "$OUTDIR"

# bpftool is what turns BTF into a C header.  Building it from the same tree
# keeps the dump's types identical to the kernel that will load the program.
if [ ! -x "$BPFTOOL" ]; then
  echo "build_scx_artifacts: building bpftool from $TREE"
  make -C "$TREE/tools/bpf/bpftool" -j"$(nproc)" >/dev/null
fi

echo "build_scx_artifacts: dumping vmlinux.h ($("$BPFTOOL" --version 2>/dev/null || echo bpftool))"
"$BPFTOOL" btf dump file "$TREE/vmlinux" format c > "$OUTDIR/vmlinux.h"

# The type the whole exercise depends on: without it the on-device attach would
# have no kernel type to bind to, and the compile below would fail.
grep -q '^struct sched_ext_ops {' "$OUTDIR/vmlinux.h" || {
  echo "build_scx_artifacts: $TREE/vmlinux BTF has no struct sched_ext_ops;" >&2
  echo "  the kernel was built before the sched_ext graft was applied" >&2
  exit 3
}

echo "build_scx_artifacts: compiling the scheduler with $CC_BPF"
"$CC_BPF" -target bpf -D__TARGET_ARCH_arm64 -O2 -g -Wall \
  -I"$OUTDIR" -I"$TREE/tools/lib" -I"$TREE/tools/lib/bpf" \
  -c "$MODULE_DIR/tools/scx/abk_scx_min.bpf.c" \
  -o "$OUTDIR/abk_scx_min.bpf.o"

echo "build_scx_artifacts: objects"
"$BPFTOOL" gen object "$OUTDIR/abk_scx_min.bpf.o" 2>/dev/null \
  || llvm-objdump -h "$OUTDIR/abk_scx_min.bpf.o" | sed -n '1,20p'

# ---------------------------------------------------------------------------
# The loader.  It links libbpf, and libbpf needs libelf.
#
# Host: libelf is installed, so the loader is built here as a compile *and link*
# gate (BUILD_HOST_LOADER=1).  Running it on the host is what makes this more
# than a compile: "selftest" parses the .bpf.o with libbpf and prints the
# struct_ops map and its programs, so a broken object is caught without a
# kernel, let alone a device.
#
# Device (aarch64-linux-android): the NDK ships no libelf, so the cross build
# needs elfutils' libelf for the triplet first.  This script prints that and
# stops -- it does not fake a device binary.
# ---------------------------------------------------------------------------
build_host_loader() {
  echo "build_scx_artifacts: building libbpf + the host loader (real libelf)"
  make -C "$TREE/tools/lib/bpf" -j"$(nproc)" >/dev/null
  "${CC_HOST:-cc}" -O2 -Wall -Werror \
    -I"$TREE/tools/lib" -I"$TREE/tools/lib/bpf" -I"$TREE/tools/include" \
    -o "$OUTDIR/scx_loader" "$MODULE_DIR/tools/scx/scx_loader.c" \
    "$TREE/tools/lib/bpf/libbpf.a" -lelf -lz
  echo "build_scx_artifacts: host loader -> $OUTDIR/scx_loader"
}

# The Android path: libbpf *without* libelf.
#
# The NDK ships no libelf, so the device loader cannot link against it.  This
# builds libbpf against tools/scx/libelf-shim -- the read-only slice of the API
# libbpf actually uses, implemented over <elf.h> -- links the loader against it,
# and then proves the result the only way that matters here: run the same
# "selftest" through both loaders and diff the output.  A shim that parsed the
# object differently from libelf would show up as a diff, and this host check is
# what makes the shim safe to hand to the cross compiler.
#
# libbpf's linker.c (its static ELF writer, bpf_linker__*) is skipped: it is the
# only file that needs the write side, and the loader never calls it.
build_shim_loader() {
  _shim="$MODULE_DIR/tools/scx/libelf-shim"
  _out="$OUTDIR/shim"
  _inc="-I$_shim -I$TREE/tools/lib/bpf -I$TREE/tools/lib -I$TREE/tools/include -I$TREE/tools/include/uapi"
  _cflags="-g -O2 -Wall -Wno-switch-enum -Werror -fvisibility=hidden -D_LARGEFILE64_SOURCE -D_FILE_OFFSET_BITS=64"

  rm -rf "$_out" && mkdir -p "$_out"

  echo "build_scx_artifacts: building libbpf against the libelf shim"
  for _f in "$TREE"/tools/lib/bpf/*.c; do
    case "$_f" in
      */linker.c) continue ;;
    esac
    # shellcheck disable=SC2086
    "${CC_HOST:-cc}" $_cflags $_inc -c "$_f" -o "$_out/$(basename "$_f" .c).o" || return 1
  done
  ar rcs "$_out/libbpf-shim.a" "$_out"/*.o || return 1

  # shellcheck disable=SC2086
  "${CC_HOST:-cc}" $_cflags $_inc -o "$_out/scx_loader_shim" \
    "$MODULE_DIR/tools/scx/scx_loader.c" "$_shim/elf_shim.c" \
    "$_out/libbpf-shim.a" -lz || return 1

  echo "build_scx_artifacts: comparing the shim parse against the real one"
  "$OUTDIR/scx_loader" selftest "$OUTDIR/abk_scx_min.bpf.o" > "$_out/selftest-real.txt"
  "$_out/scx_loader_shim" selftest "$OUTDIR/abk_scx_min.bpf.o" > "$_out/selftest-shim.txt"
  if ! diff -u "$_out/selftest-real.txt" "$_out/selftest-shim.txt"; then
    echo "build_scx_artifacts: the libelf shim parses the object differently from libelf" >&2
    return 1
  fi
  echo "build_scx_artifacts: shim parse == libelf parse (selftest identical)"
  cat "$_out/selftest-shim.txt"
}

if [ "${BUILD_SHIM_LOADER:-0}" = "1" ]; then
  build_host_loader
  build_shim_loader
elif [ "${BUILD_HOST_LOADER:-0}" = "1" ]; then
  build_host_loader
  "$OUTDIR/scx_loader" selftest "$OUTDIR/abk_scx_min.bpf.o"
else
  echo "build_scx_artifacts: loader not built"
  echo "  BUILD_HOST_LOADER=1  link the loader against the host's libelf"
  echo "  BUILD_SHIM_LOADER=1  also build it against tools/scx/libelf-shim (the Android path)"
  echo "  the cross compile to aarch64-linux-android still needs the NDK plus this shim"
fi
