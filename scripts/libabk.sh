#!/usr/bin/env bash

abk_log() {
  printf '[ABK module] %s\n' "$*"
}

abk_warn() {
  printf '[ABK module][warn] %s\n' "$*" >&2
}

abk_die() {
  printf '[ABK module][error] %s\n' "$*" >&2
  exit 1
}

abk_require_env() {
  local name
  for name in "$@"; do
    if [ -z "${!name:-}" ]; then
      abk_die "required environment variable is empty: $name"
    fi
  done
}

abk_common_dir() {
  abk_require_env KERNEL_ROOT
  printf '%s/common\n' "$KERNEL_ROOT"
}

abk_require_file() {
  local path="$1"
  [ -f "$path" ] || abk_die "required file not found: $path"
}

abk_require_dir() {
  local path="$1"
  [ -d "$path" ] || abk_die "required directory not found: $path"
}

abk_kernel_make_value() {
  local key="$1"
  local makefile
  makefile="$(abk_common_dir)/Makefile"
  abk_require_file "$makefile"
  awk -v key="$key" '$1 == key && $2 == "=" { print $3; exit }' "$makefile"
}

abk_kernel_version() {
  local version patchlevel sublevel
  version="$(abk_kernel_make_value VERSION)"
  patchlevel="$(abk_kernel_make_value PATCHLEVEL)"
  sublevel="$(abk_kernel_make_value SUBLEVEL)"
  printf '%s.%s.%s\n' "$version" "$patchlevel" "$sublevel"
}

abk_stage_is() {
  local expected="$1"
  [ "${CUSTOM_EXTERNAL_MODULE_STAGE:-}" = "$expected" ]
}

abk_python() {
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
  elif command -v python >/dev/null 2>&1; then
    command -v python
  else
    abk_die "no python interpreter found (need python3 to run the stable backport grafts)"
  fi
}
