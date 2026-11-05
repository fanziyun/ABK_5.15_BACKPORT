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

# A .abk-orig sidecar whose first line is this sentinel marks a path the module
# created; rollback deletes it instead of restoring it.
NEW_FILE_SENTINEL="sailboat_moon_kmsg: new file created by the module; delete on rollback"

if [ "${#backups[@]}" -eq 0 ]; then
  printf '[ABK module] rollback: no .abk-orig snapshots under %s\n' "$common_dir"
  exit 0
fi

for backup in "${backups[@]}"; do
  original="${backup%.abk-orig}"
  if [ "$(head -n 1 "$backup")" = "$NEW_FILE_SENTINEL" ]; then
    case "$mode" in
      --list)
        printf 'would delete %s (module-created file)\n' "$original"
        ;;
      --apply)
        rm -f "$original" "$backup"
        printf '[ABK module] rollback: deleted %s (module-created file)\n' "$original"
        ;;
      *)
        printf '[ABK module][error] unknown mode: %s\n' "$mode" >&2
        exit 1
        ;;
    esac
    continue
  fi
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
