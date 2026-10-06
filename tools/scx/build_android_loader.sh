#!/usr/bin/env bash
# Cross-build the sched_ext loader for aarch64-linux-android.
#
#   NDK=<ndk-dir> bash tools/scx/build_android_loader.sh <grafted-kernel-tree> <outdir>
#
# Run this on the host that owns the NDK: the toolchain binaries ship for the
# host, so on a Windows box this is Git Bash (or any shell that can execute the
# NDK's clang.exe), and <grafted-kernel-tree> has to be a path that host can
# read -- on WSL that is \\\\wsl.localhost\\<distro>\\home\\... .
#
# Why it is not part of tools/build_scx_artifacts.sh: that script runs inside a
# Linux kernel tree and is invoked from the kernel side; this one needs the
# NDK, which the kernel side does not have.  Both are build-host helpers and
# neither ships to a device.
#
# What it assembles, all measured (docs/survey_sched_ext_gap.md sections 2k-2m):
#
#   * headers: the grafted tree's tools/include (uapi, for <linux/bpf.h>), its
#     tools/arch (the uapi asm shims reach into it relatively) and tools/lib/bpf
#     (libbpf itself).
#   * tools/scx/libelf-shim: libelf, reduced to the seventeen functions libbpf
#     calls; the NDK ships no libelf.
#   * tools/scx/android-compat: the kernel non-uapi headers/macros libbpf needs
#     that bionic does not have (linux/err.h, linux/list.h, linux/filter.h,
#     linux/ring_buffer.h, and the macro set in kernel-macros.h).
#   * libbpf compiled from source for the triplet, minus the TUs the loader
#     never calls (the static ELF writer, XDP/TC/netlink, ringbuf, USDT).
set -euo pipefail

TREE="${1:-}"
OUTDIR="${2:-build/scx-android}"
NDK="${NDK:-/c/Users/Administrator/AppData/Local/Android/Sdk/ndk/28.2.13676358}"
API="${ANDROID_API:-30}"

MODULE_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
BIN="$NDK/toolchains/llvm/prebuilt/windows-x86_64/bin"
SYSROOT="$NDK/toolchains/llvm/prebuilt/windows-x86_64/sysroot"

if [ -z "$TREE" ] || [ ! -d "$TREE/tools/lib/bpf" ]; then
  sed -n '2,20p' "$0" >&2
  exit 2
fi
if [ ! -x "$BIN/clang.exe" ]; then
  echo "build_android_loader: no NDK clang at $BIN/clang.exe (set NDK=...)" >&2
  exit 2
fi

WORK="$OUTDIR/work"
rm -rf "$WORK"
mkdir -p "$WORK/obj" "$WORK/inc/bpf" "$WORK/inc/libbpf-src" "$WORK/inc/tools"

echo "build_android_loader: staging headers"
cp -r "$TREE/tools/include" "$WORK/inc/tools/include"
cp -r "$TREE/tools/arch" "$WORK/inc/tools/arch"
cp "$TREE"/tools/lib/bpf/*.h "$WORK/inc/bpf/"
for f in "$TREE"/tools/lib/bpf/*.c "$TREE"/tools/lib/bpf/*.h; do
  cp "$f" "$WORK/inc/libbpf-src/"
done

CFLAGS="--target=aarch64-linux-android$API --sysroot=$SYSROOT -O2 -Wall -Wno-switch-enum -Werror -fvisibility=hidden -D_LARGEFILE64_SOURCE -D_FILE_OFFSET_BITS=64"
INCARGS="-I$MODULE_DIR/tools/scx/android-compat -I$MODULE_DIR/tools/scx/libelf-shim -I$WORK/inc/libbpf-src -I$WORK/inc -I$WORK/inc/tools/include/uapi -include $MODULE_DIR/tools/scx/android-compat/kernel-macros.h"

echo "build_android_loader: compiling libbpf for aarch64-linux-android$API"
for f in "$WORK"/inc/libbpf-src/*.c; do
  case "$(basename "$f")" in
    linker.c|netlink.c|nlattr.c|xsk.c|ringbuf.c|usdt.c)
      # not called by the loader; several of them need kernel-only headers
      # (asm/barrier.h) or bionic headers that fight the kernel uapi ones
      # (arpa/inet.h, linux/in.h)
      continue ;;
  esac
  # shellcheck disable=SC2086
  "$BIN/clang.exe" $CFLAGS $INCARGS -c "$f" -o "$WORK/obj/$(basename "$f" .c).o"
done
"$BIN/llvm-ar.exe" rcs "$WORK/libbpf.a" "$WORK"/obj/*.o

echo "build_android_loader: linking the loader"
# shellcheck disable=SC2086
"$BIN/clang.exe" $CFLAGS $INCARGS -o "$OUTDIR/scx_loader" \
  "$MODULE_DIR/tools/scx/scx_loader.c" \
  "$MODULE_DIR/tools/scx/libelf-shim/elf_shim.c" "$WORK/libbpf.a" -lz

mkdir -p "$OUTDIR"
ls -l "$OUTDIR/scx_loader"
"$BIN/llvm-readelf.exe" -h "$OUTDIR/scx_loader" 2>/dev/null | grep -E 'Class|Machine|Type' || true
echo "build_android_loader: $OUTDIR/scx_loader"
