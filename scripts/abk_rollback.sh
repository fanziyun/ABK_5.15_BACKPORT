#!/usr/bin/env bash
# Roll back every file this module (or a sibling module using the same
# convention) touched, restoring the <file>.abk-orig snapshots.
#
# Two snapshot conventions are handled:
#   <file>.abk-orig   the original bytes; restore them over the target.
#   <file>.abk-new    a zero-byte marker that says "this target did not exist
#                     before the module created it" -- there is nothing to
#                     restore, so the target, the marker and any .abk-orig next
#                     to it are all removed.  The module may also have written
#                     that .abk-orig as an empty diff base (what
#                     tests/config_gate_audit.py attributes a module-added
#                     file's CONFIG gates against); it is optional here.
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

case "$mode" in
  --list|--apply) ;;
  *)
    printf '[ABK module][error] unknown mode: %s\n' "$mode" >&2
    exit 1
    ;;
esac

# Created files first: removing the .abk-orig diff base (when one was written)
# keeps the restore sweep below from "restoring" a zero-byte file over a target
# that should have been removed.
mapfile -t created < <(find "$common_dir" -name '*.abk-new' -type f)
if [ "${#created[@]}" -gt 0 ]; then
  for marker in "${created[@]}"; do
    target="${marker%.abk-new}"
    case "$mode" in
      --list)
        printf 'would delete %s\n' "$target"
        ;;
      --apply)
        rm -f "$target" "$marker" "$target.abk-orig"
        printf '[ABK module] rollback: removed %s\n' "$target"
        ;;
    esac
  done
fi

mapfile -t backups < <(find "$common_dir" -name '*.abk-orig' -type f)

if [ "${#backups[@]}" -eq 0 ]; then
  if [ "${#created[@]}" -eq 0 ]; then
    printf '[ABK module] rollback: no .abk-orig snapshots under %s\n' "$common_dir"
  fi
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
  esac
done
