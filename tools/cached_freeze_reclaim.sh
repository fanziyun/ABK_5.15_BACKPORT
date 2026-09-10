#!/bin/bash
# cached_freeze_reclaim.sh - userspace half of the cached-freeze-reclaim line
# (Batch 10-3).
#
# AOSP's CachedAppOptimizer parks a cached app's threads in the cgroup v2
# freezer and then writes the memcg's memory.current into its memory.reclaim
# file, evicting file pages and swapping anonymous pages into zram.  The
# reclaim write is the part this daemon automates for devices that want it
# decoupled from the platform policy:
#
#   * default: reclaim only.  For each cached UID group it writes
#     memory.current (AOSP's "maximal reclaim") into memory.reclaim, or the
#     --quota-mb cap when one is given;
#   * --freeze: additionally quiesce the group first (cgroup.freeze=1),
#     reclaim, then thaw it (cgroup.freeze=0) so the sweep never leaves an
#     app parked.
#
# Lifecycle unfreezing (intent / job / activity resume) stays with the
# platform ActivityManager; this script deliberately does not reimplement it,
# which is why freeze is opt-in and always undone within one sweep.
#
# Observability: the kernel graft surfaces cfr_reclaim_* counters in
# memory.stat and emits abk_cfr_freeze/abk_cfr_thaw tracepoints, so a sweep is
# verifiable with `grep cfr_reclaim memory.stat`, `am freeze/unfreeze` and
# Perfetto's Freezer track.
#
# Usage:
#   cached_freeze_reclaim.sh [--dry-run] [--freeze] [--interval SECS]
#                            [--quota-mb N] [--cgroup-root PATH] [--uid UID ...]
# Defaults: interval 60s, no quota cap (0 = write memory.current whole),
# no freezing, cgroup v2 root at /sys/fs/cgroup.
# Exit codes: 0 ok, 2 bad usage, 1 runtime failure.
set -euo pipefail

DRY_RUN=0
DO_FREEZE=0
INTERVAL=60
QUOTA_MB=0
CGROUP_ROOT="/sys/fs/cgroup"
UIDS=()

usage() {
  sed -n '2,31p' "$0"
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --freeze) DO_FREEZE=1; shift ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --quota-mb) QUOTA_MB="$2"; shift 2 ;;
    --cgroup-root) CGROUP_ROOT="$2"; shift 2 ;;
    --uid) UIDS+=("$2"); shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done

case "$INTERVAL" in ''|*[!0-9]*) echo "bad --interval: $INTERVAL" >&2; exit 2;; esac
case "$QUOTA_MB" in ''|*[!0-9]*) echo "bad --quota-mb: $QUOTA_MB" >&2; exit 2;; esac

# quota 0 means "no cap": write memory.current whole, matching AOSP.
quota_bytes=0
if [ "$QUOTA_MB" -gt 0 ]; then
  quota_bytes=$((QUOTA_MB * 1024 * 1024))
fi

# UID dirs: explicit --uid list, else every apps/uid_* dir under the root.
uid_dirs=()
if [ "${#UIDS[@]}" -gt 0 ]; then
  for u in "${UIDS[@]}"; do
    uid_dirs+=("$CGROUP_ROOT/apps/uid_$u")
  done
else
  for d in "$CGROUP_ROOT"/apps/uid_*/; do
    [ -d "$d" ] || continue
    uid_dirs+=("${d%/}")
  done
fi

sweep_once() {
  local dir cur freeze_file
  for dir in "${uid_dirs[@]}"; do
    [ -f "$dir/memory.current" ] || continue
    [ -f "$dir/memory.reclaim" ] || continue

    cur="$(cat "$dir/memory.current" 2>/dev/null || true)"
    case "$cur" in ''|*[!0-9]*) continue;; esac
    [ "$cur" -gt 0 ] || continue

    if [ "$quota_bytes" -gt 0 ] && [ "$cur" -gt "$quota_bytes" ]; then
      cur="$quota_bytes"
    fi

    if [ "$DRY_RUN" = 1 ]; then
      if [ "$DO_FREEZE" = 1 ]; then
        echo "[dry-run] freeze+reclaim+thaw $cur bytes from $dir"
      else
        echo "[dry-run] reclaim $cur bytes from $dir"
      fi
      continue
    fi

    freeze_file="$dir/cgroup.freeze"
    if [ "$DO_FREEZE" = 1 ] && [ -f "$freeze_file" ]; then
      echo 1 > "$freeze_file" 2>/dev/null || continue
    fi

    if ! echo "$cur" > "$dir/memory.reclaim" 2>/dev/null; then
      echo "reclaim failed for $dir (group gone?)" >&2
    fi

    if [ "$DO_FREEZE" = 1 ] && [ -f "$freeze_file" ]; then
      echo 0 > "$freeze_file" 2>/dev/null || true
    fi
  done
}

if [ "${CFR_ONE_SHOT:-0}" = 1 ]; then
  sweep_once
  exit 0
fi

# Refuse to start a long-running loop on a host with no cached-app groups:
# otherwise the daemon sleeps forever doing nothing but hiding the mismatch.
if [ "${#uid_dirs[@]}" -eq 0 ] && [ "${CFR_ALLOW_EMPTY:-0}" != 1 ]; then
  echo "cached_freeze_reclaim: no uid_* groups under $CGROUP_ROOT/apps" >&2
  exit 1
fi

while true; do
  sweep_once
  sleep "$INTERVAL"
done
