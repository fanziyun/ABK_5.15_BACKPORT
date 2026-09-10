#!/bin/sh
# autofdo_515_profile.sh - collect and validate a device-specific AutoFDO
# profile for an android13-5.15 kernel.
#
# This is a build-engineering tool, not a graft: it never edits a kernel tree
# and never registers a PatchGroup.  It turns
#
#   init -> record -> convert -> validate -> build-env
#
# into a workspace that records exactly which kernel, toolchain and device a
# profile came from, so a profile from another build (or another kernel line)
# cannot be fed to clang by accident -- the two hard gates are:
#
#   * init     accepts only a 5.15 tree (VERSION/PATCHLEVEL from its Makefile);
#   * validate recomputes the vmlinux hash and refuses a profile whose recorded
#              build no longer matches the binary it will be applied to.
#
# It only *produces* the profile.  If the 5.15 tree has no
# scripts/Makefile.autofdo (and the Makefile include that wires it up), the
# Android Common AutoFDO build integration has to be applied separately first;
# init warns about that and records it, rather than touching the tree.
#
# The device-side simpleperf flags are toolchain/SoC-specific: the defaults here
# are the ones documented by Android Common, and `--create-llvm-prof-args` /
# --duration / --frequency exist so they can be corrected for the target without
# editing this script.  Always re-check them against the target simpleperf
# before an A/B benchmark.
#
# Usage:
#   autofdo_515_profile.sh init    --kernel-root <5.15-tree> --vmlinux <vmlinux>
#                                  --output-dir <ws> [--source-revision REV]
#                                  [--toolchain ID]
#   autofdo_515_profile.sh record  --output-dir <ws> [--device SERIAL]
#                                  [--duration SECS] [--frequency HZ]
#                                  [--simpleperf ON_DEVICE_PATH]
#   autofdo_515_profile.sh convert --output-dir <ws> --create-llvm-prof PATH
#                                  [--host-simpleperf PATH] [--kallsyms FILE]
#                                  [--llvm-profdata PATH]
#                                  [--create-llvm-prof-args "ARGS"]
#   autofdo_515_profile.sh validate  --output-dir <ws>
#   autofdo_515_profile.sh build-env --output-dir <ws>
#
# Exit codes: 0 ok, 2 bad usage, 1 a gate failed (wrong kernel line, missing
# artifact, vmlinux hash mismatch, tool or device unavailable).
set -eu

MANIFEST_NAME="manifest.env"
AUTOFDO_NAME="kernel.autofdo"
PROFDATA_NAME="kernel.llvm_profdata"
PERF_NAME="perf.data"
FORMAT="abk-autofdo-515-v1"

die() {
  echo "autofdo_515_profile: $*" >&2
  exit 1
}

usage_fail() {
  echo "autofdo_515_profile: $*" >&2
  echo "run 'autofdo_515_profile.sh' without arguments for the usage text" >&2
  exit 2
}

usage() {
  sed -n '2,51p' "$0"
  exit 2
}

note() {
  echo "autofdo_515_profile: $*"
}

warn() {
  echo "autofdo_515_profile: WARN: $*" >&2
}

# --- small helpers -------------------------------------------------------

now_utc() {
  date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || echo unknown
}

# sha256_file <path>: sha256sum, then the macOS/openssl/python fallbacks.  The
# tool runs on the build host, never on the device, so host tooling is fine --
# but a host without sha256sum should not silently skip the hard gate.
sha256_file() {
  _sf_path="$1"
  [ -f "$_sf_path" ] || return 1
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$_sf_path" | awk '{ print $1 }'
    return 0
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$_sf_path" | awk '{ print $1 }'
    return 0
  fi
  if command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$_sf_path" | awk '{ print $NF }'
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$_sf_path" <<'PY'
import hashlib, sys, pathlib
print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PY
    return 0
  fi
  return 1
}

# make_value <makefile> <KEY>: the first `KEY = value` assignment.  CR is
# stripped: a kernel tree checked out on Windows (core.autocrlf) has a CRLF
# Makefile, and a trailing CR would make "5.15.167" compare unequal to "5.15".
make_value() {
  sed -n "s/^$2[[:space:]]*=[[:space:]]*//p" "$1" 2>/dev/null | head -n 1 | tr -d '\r'
}

kernel_version() {
  _kv_root="$1"
  _kv_makefile="$_kv_root/Makefile"
  [ -f "$_kv_makefile" ] || return 1
  _kv_v="$(make_value "$_kv_makefile" VERSION)"
  _kv_p="$(make_value "$_kv_makefile" PATCHLEVEL)"
  _kv_s="$(make_value "$_kv_makefile" SUBLEVEL)"
  [ -n "$_kv_v" ] && [ -n "$_kv_p" ] || return 1
  if [ -n "$_kv_s" ]; then
    printf '%s.%s.%s\n' "$_kv_v" "$_kv_p" "$_kv_s"
  else
    printf '%s.%s\n' "$_kv_v" "$_kv_p"
  fi
}

manifest_path() {
  printf '%s/%s\n' "$1" "$MANIFEST_NAME"
}

manifest_get() { # <ws> <key>
  _mg_manifest="$(manifest_path "$1")"
  [ -f "$_mg_manifest" ] || return 1
  sed -n "s/^$2=//p" "$_mg_manifest" | head -n 1 | tr -d '\r'
}

manifest_put() { # <ws> <key> <value> (append; last one wins for manifest_get)
  _mp_manifest="$(manifest_path "$1")"
  printf '%s=%s\n' "$2" "$3" >> "$_mp_manifest"
}

require_dir() {
  [ -d "$1" ] || die "$1 is not a directory"
}

require_file() {
  [ -f "$1" ] || die "$1 does not exist"
}

# --- init ----------------------------------------------------------------

cmd_init() {
  kernel_root=""
  output_dir=""
  vmlinux=""
  source_revision=""
  toolchain=""

  while [ $# -gt 0 ]; do
    case "$1" in
      --kernel-root) kernel_root="$2"; shift 2 ;;
      --output-dir) output_dir="$2"; shift 2 ;;
      --vmlinux) vmlinux="$2"; shift 2 ;;
      --source-revision) source_revision="$2"; shift 2 ;;
      --toolchain) toolchain="$2"; shift 2 ;;
      -h|--help) usage ;;
      *) usage_fail "init: unknown argument $1" ;;
    esac
  done

  [ -n "$kernel_root" ] || usage_fail "init needs --kernel-root"
  [ -n "$output_dir" ] || usage_fail "init needs --output-dir"
  [ -n "$vmlinux" ] || usage_fail "init needs --vmlinux"
  require_dir "$kernel_root"
  require_file "$vmlinux"

  version="$(kernel_version "$kernel_root")" \
    || die "$kernel_root/Makefile has no VERSION/PATCHLEVEL; is this a kernel tree?"

  case "$version" in
    5.15*) ;;
    *) die "kernel line is $version, not 5.15; refusing to record an AutoFDO workspace for it" ;;
  esac

  vmlinux_hash="$(sha256_file "$vmlinux")" \
    || die "cannot hash $vmlinux (need sha256sum, shasum, openssl or python3)"
  vmlinux_abs="$(cd "$(dirname "$vmlinux")" && pwd)/$(basename "$vmlinux")"
  root_abs="$(cd "$kernel_root" && pwd)"
  output_abs="$(cd "$output_dir" 2>/dev/null && pwd || echo "$output_dir")"
  mkdir -p "$output_dir"
  output_abs="$(cd "$output_dir" && pwd)"

  if [ -z "$source_revision" ]; then
    source_revision="$(git -C "$kernel_root" rev-parse HEAD 2>/dev/null || true)"
  fi
  if [ -z "$toolchain" ]; then
    _tc_cc="${CC:-cc}"
    if command -v "$_tc_cc" >/dev/null 2>&1; then
      toolchain="$("$_tc_cc" --version 2>/dev/null | head -n 1)"
    fi
  fi
  [ -n "$toolchain" ] || toolchain="unknown"

  config_path="$kernel_root/.config"
  config_hash="missing"
  if [ -f "$config_path" ]; then
    config_hash="$(sha256_file "$config_path" || echo unhashable)"
  fi

  autofdo_integration="present"
  [ -f "$kernel_root/scripts/Makefile.autofdo" ] || autofdo_integration="missing"

  {
    printf 'format=%s\n' "$FORMAT"
    printf 'created_utc=%s\n' "$(now_utc)"
    printf 'kernel_version=%s\n' "$version"
    printf 'kernel_root=%s\n' "$root_abs"
    printf 'vmlinux_path=%s\n' "$vmlinux_abs"
    printf 'vmlinux_sha256=%s\n' "$vmlinux_hash"
    printf 'kernel_config=%s\n' "$config_hash"
    printf 'source_revision=%s\n' "$source_revision"
    printf 'toolchain=%s\n' "$toolchain"
    printf 'makefile_autofdo=%s\n' "$autofdo_integration"
    printf 'host=%s\n' "$(uname -srm 2>/dev/null || echo unknown)"
  } > "$(manifest_path "$output_abs")"

  note "workspace: $output_abs"
  note "kernel:    $version ($source_revision)"
  note "vmlinux:   sha256 $vmlinux_hash"
  if [ "$autofdo_integration" = "missing" ]; then
    warn "$root_abs/scripts/Makefile.autofdo is missing, so the kernel tree has no"
    warn "AutoFDO build integration yet; apply the Android Common AutoFDO patch"
    warn "separately.  This tool only produces the profile, it never edits the tree."
  fi
  note "next: autofdo_515_profile.sh record --output-dir $output_abs --device <serial>"
  return 0
}

# --- record --------------------------------------------------------------

cmd_record() {
  output_dir=""
  device="${ANDROID_SERIAL:-}"
  duration="60"
  frequency="1000"
  simpleperf="/system/bin/simpleperf"

  while [ $# -gt 0 ]; do
    case "$1" in
      --output-dir) output_dir="$2"; shift 2 ;;
      --device) device="$2"; shift 2 ;;
      --duration) duration="$2"; shift 2 ;;
      --frequency) frequency="$2"; shift 2 ;;
      --simpleperf) simpleperf="$2"; shift 2 ;;
      -h|--help) usage ;;
      *) usage_fail "record: unknown argument $1" ;;
    esac
  done

  [ -n "$output_dir" ] || usage_fail "record needs --output-dir"
  require_dir "$output_dir"
  [ -f "$(manifest_path "$output_dir")" ] || die "no $MANIFEST_NAME in $output_dir; run init first"
  command -v adb >/dev/null 2>&1 || die "adb is not on PATH"

  if [ -z "$device" ]; then
    device="$(adb devices 2>/dev/null | awk 'NR > 1 && $2 == "device" { print $1; exit }')"
    [ -n "$device" ] || die "no device: pass --device or set ANDROID_SERIAL"
  fi

  device_tmp="/data/local/tmp/abk_autofdo"
  if ! adb -s "$device" shell "mkdir -p $device_tmp" >/dev/null 2>&1; then
    die "cannot reach device $device over adb"
  fi

  note "recording $duration s at ${frequency} Hz on $device"
  if ! adb -s "$device" shell \
      "$simpleperf record -a -g -e cpu-clock -f $frequency \
        -o $device_tmp/$PERF_NAME --duration $duration" >/dev/null 2>&1; then
    die "simpleperf record failed on $device; check the SoC/toolchain-specific flags"
  fi
  if ! adb -s "$device" pull "$device_tmp/$PERF_NAME" "$output_dir/$PERF_NAME" >/dev/null 2>&1; then
    die "could not pull $device_tmp/$PERF_NAME from $device"
  fi
  adb -s "$device" shell "rm -rf $device_tmp" >/dev/null 2>&1 || true

  manifest_put "$output_dir" "device_serial" "$device"
  manifest_put "$output_dir" "device_fingerprint" \
    "$(adb -s "$device" shell getprop ro.build.fingerprint 2>/dev/null | tr -d '\r')"
  manifest_put "$output_dir" "device_kernel" \
    "$(adb -s "$device" shell uname -r 2>/dev/null | tr -d '\r')"
  manifest_put "$output_dir" "perf_data_sha256" "$(sha256_file "$output_dir/$PERF_NAME" || echo unhashable)"

  note "perf data: $output_dir/$PERF_NAME"
  note "next: autofdo_515_profile.sh convert --output-dir $output_dir --create-llvm-prof <path>"
  return 0
}

# --- convert -------------------------------------------------------------

cmd_convert() {
  output_dir=""
  create_llvm_prof=""
  host_simpleperf=""
  kallsyms=""
  llvm_profdata=""
  extra_args=""

  while [ $# -gt 0 ]; do
    case "$1" in
      --output-dir) output_dir="$2"; shift 2 ;;
      --create-llvm-prof) create_llvm_prof="$2"; shift 2 ;;
      --host-simpleperf) host_simpleperf="$2"; shift 2 ;;
      --kallsyms) kallsyms="$2"; shift 2 ;;
      --llvm-profdata) llvm_profdata="$2"; shift 2 ;;
      --create-llvm-prof-args) extra_args="$2"; shift 2 ;;
      -h|--help) usage ;;
      *) usage_fail "convert: unknown argument $1" ;;
    esac
  done

  [ -n "$output_dir" ] || usage_fail "convert needs --output-dir"
  [ -n "$create_llvm_prof" ] || usage_fail "convert needs --create-llvm-prof"
  require_dir "$output_dir"
  [ -f "$(manifest_path "$output_dir")" ] || die "no $MANIFEST_NAME in $output_dir; run init first"
  require_file "$output_dir/$PERF_NAME"
  [ -x "$create_llvm_prof" ] || die "$create_llvm_prof is not executable"

  vmlinux="$(manifest_get "$output_dir" vmlinux_path)"
  [ -n "$vmlinux" ] || die "$MANIFEST_NAME has no vmlinux_path"
  require_file "$vmlinux"

  profile_in="$output_dir/$PERF_NAME"
  # Optional symbolication pre-step: an ELF vmlinux is normally enough for
  # create_llvm_prof, but a kernel whose symbols are not in the image needs
  # simpleperf inject with a kallsyms file.  Both must be given together.
  if [ -n "$kallsyms" ] || [ -n "$host_simpleperf" ]; then
    [ -n "$kallsyms" ] && [ -n "$host_simpleperf" ] \
      || usage_fail "convert wants --kallsyms and --host-simpleperf together"
    [ -x "$host_simpleperf" ] || die "$host_simpleperf is not executable"
    require_file "$kallsyms"
    if ! "$host_simpleperf" inject -i "$profile_in" -o "$output_dir/$PERF_NAME.injected" \
         --kallsyms "$kallsyms" >/dev/null 2>&1; then
      die "simpleperf inject failed; drop --kallsyms/--host-simpleperf if this simpleperf has no inject"
    fi
    profile_in="$output_dir/$PERF_NAME.injected"
    manifest_put "$output_dir" "kallsyms_sha256" "$(sha256_file "$kallsyms" || echo unhashable)"
  fi

  # shellcheck disable=SC2086 # extra_args is a deliberately word-split flag list
  if ! "$create_llvm_prof" --binary "$vmlinux" --profile "$profile_in" \
       --out "$output_dir/$AUTOFDO_NAME" --format=extbinary $extra_args; then
    die "create_llvm_prof failed"
  fi
  [ -s "$output_dir/$AUTOFDO_NAME" ] || die "$AUTOFDO_NAME was not produced"

  manifest_put "$output_dir" "create_llvm_prof" "$create_llvm_prof"
  manifest_put "$output_dir" "create_llvm_prof_args" "${extra_args:-<none>}"
  manifest_put "$output_dir" "autofdo_sha256" "$(sha256_file "$output_dir/$AUTOFDO_NAME" || echo unhashable)"

  # The indexed format is a convenience for toolchains that want
  # -fprofile-sample-use with a merged profile; not every host has the tool.
  if [ -z "$llvm_profdata" ] && command -v llvm-profdata >/dev/null 2>&1; then
    llvm_profdata="$(command -v llvm-profdata)"
  fi
  if [ -n "$llvm_profdata" ] && [ -x "$llvm_profdata" ]; then
    if "$llvm_profdata" merge -sample -o "$output_dir/$PROFDATA_NAME" \
         "$output_dir/$AUTOFDO_NAME" >/dev/null 2>&1; then
      manifest_put "$output_dir" "llvm_profdata_sha256" \
        "$(sha256_file "$output_dir/$PROFDATA_NAME" || echo unhashable)"
    else
      warn "llvm-profdata merge failed; $PROFDATA_NAME is optional, validate does not require it"
    fi
  else
    warn "llvm-profdata not found; $PROFDATA_NAME is optional, validate does not require it"
  fi

  note "profile:  $output_dir/$AUTOFDO_NAME"
  note "validate: autofdo_515_profile.sh validate --output-dir $output_dir"
  return 0
}

# --- validate / build-env ------------------------------------------------

check_workspace() { # <ws>: the hard gates, shared by validate and build-env
  _cw_ws="$1"
  [ -d "$_cw_ws" ] || die "$_cw_ws is not a directory"
  [ -f "$(manifest_path "$_cw_ws")" ] || die "no $MANIFEST_NAME in $_cw_ws"

  _cw_format="$(manifest_get "$_cw_ws" format)"
  [ "$_cw_format" = "$FORMAT" ] \
    || die "manifest format is '$_cw_format', expected '$FORMAT'"

  _cw_version="$(manifest_get "$_cw_ws" kernel_version)"
  case "$_cw_version" in
    5.15*) ;;
    *) die "profile is for kernel line '$_cw_version', not 5.15" ;;
  esac

  _cw_vmlinux="$(manifest_get "$_cw_ws" vmlinux_path)"
  [ -n "$_cw_vmlinux" ] || die "manifest has no vmlinux_path"
  [ -f "$_cw_vmlinux" ] || die "recorded vmlinux $_cw_vmlinux no longer exists"

  _cw_want="$(manifest_get "$_cw_ws" vmlinux_sha256)"
  _cw_have="$(sha256_file "$_cw_vmlinux" || true)"
  [ -n "$_cw_have" ] || die "cannot hash $_cw_vmlinux"
  if [ "$_cw_want" != "$_cw_have" ]; then
    die "vmlinux hash mismatch: the profile was recorded against $_cw_want but $_cw_vmlinux is now $_cw_have"
  fi

  [ -s "$_cw_ws/$AUTOFDO_NAME" ] \
    || die "$AUTOFDO_NAME is missing or empty; run convert first (or fetch the profile)"
  if [ -e "$_cw_ws/$PROFDATA_NAME" ] && [ ! -s "$_cw_ws/$PROFDATA_NAME" ]; then
    warn "$PROFDATA_NAME is empty; it is optional, but a truncated one is suspicious"
  fi

  note "ok: $AUTOFDO_NAME matches $(_cw_version) built from $_cw_vmlinux"
  return 0
}

cmd_validate() {
  output_dir=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --output-dir) output_dir="$2"; shift 2 ;;
      -h|--help) usage ;;
      *) usage_fail "validate: unknown argument $1" ;;
    esac
  done
  [ -n "$output_dir" ] || usage_fail "validate needs --output-dir"
  check_workspace "$output_dir"
  if [ -z "$(manifest_get "$output_dir" device_serial || true)" ]; then
    warn "the manifest has no device_serial: this workspace was not recorded from a device"
  fi
  return 0
}

cmd_build_env() {
  output_dir=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --output-dir) output_dir="$2"; shift 2 ;;
      -h|--help) usage ;;
      *) usage_fail "build-env: unknown argument $1" ;;
    esac
  done
  [ -n "$output_dir" ] || usage_fail "build-env needs --output-dir"
  check_workspace "$output_dir"

  _be_root="$(manifest_get "$output_dir" kernel_root)"
  if [ -n "$_be_root" ] && [ ! -f "$_be_root/scripts/Makefile.autofdo" ]; then
    warn "$_be_root/scripts/Makefile.autofdo is missing: apply the Android Common"
    warn "AutoFDO integration to the kernel tree before building with these variables"
  fi

  printf 'CONFIG_AUTOFDO_CLANG=y\n'
  printf 'CLANG_AUTOFDO_PROFILE=%s/%s\n' "$(cd "$output_dir" && pwd)" "$AUTOFDO_NAME"
  printf '# ABK: export these (or pass them to make) for the %s build\n' \
    "$(manifest_get "$output_dir" kernel_version)"
  return 0
}

# --- dispatch ------------------------------------------------------------

[ $# -gt 0 ] || usage

command="$1"
shift

case "$command" in
  init) cmd_init "$@" ;;
  record) cmd_record "$@" ;;
  convert) cmd_convert "$@" ;;
  validate) cmd_validate "$@" ;;
  build-env) cmd_build_env "$@" ;;
  -h|--help) usage ;;
  *) usage_fail "unknown command: $command" ;;
esac
