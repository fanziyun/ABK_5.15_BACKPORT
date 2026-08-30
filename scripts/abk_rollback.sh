#!/usr/bin/env bash
# Roll back every file this module (or a sibling module using the same
# convention) touched, restoring the <file>.abk-orig snapshots.
#
# Usage:
#   bash scripts/abk_rollback.sh <kernel-common-dir> --list   # dry run
#   bash scripts/abk_rollback.sh <kernel-common-dir> --apply  # restore & delete

set -euo pipefail

common_dir="${1:-}"
mode="${2:---list}"

if [ -z "$common_dir" ] || [ ! -d "$common_dir" ]; then
  printf '[ABK module][error] usage: %s <kernel-common-dir> [--list|--apply]\n' "$0" >&2
  exit 1
fi

mapfile -t backups < <(find "$common_dir" -name '*.abk-orig' -type f)

if [ "${#backups[@]}" -eq 0 ]; then
  printf '[ABK module] rollback: no .abk-orig snapshots under %s\n' "$common_dir"
  exit 0
fi

for backup in "${backups[@]}"; do
  original="${backup%.abk-orig}"
  case "$mode" in
    --list)
      printf 'would restore %s\n' "$original"
      ;;
    --apply)
      cp -a "$backup" "$original"
      rm -f "$backup"
      printf '[ABK module] rollback: restored %s\n' "$original"
      ;;
    *)
      printf '[ABK module][error] unknown mode: %s\n' "$mode" >&2
      exit 1
      ;;
  esac
done
