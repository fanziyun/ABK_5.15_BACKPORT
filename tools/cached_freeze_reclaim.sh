#!/bin/sh
# cached_freeze_reclaim.sh - userspace half of the cached-freeze-reclaim line
# (Batch 10-3).
#
# AOSP's CachedAppOptimizer parks a cached app's threads in the cgroup freezer
# and then writes the memcg's memory.current into its memory.reclaim file,
# evicting file pages and swapping anonymous pages into zram.  The reclaim write
# is the part this daemon automates for devices that want it decoupled from the
# platform policy:
#
#   * default: reclaim only.  For each cached UID group it writes memory.current
#     (AOSP's "maximal reclaim") into memory.reclaim, or the --quota-mb cap when
#     one is given;
#   * --freeze: additionally quiesce the group first, reclaim, then thaw it so
#     the sweep never leaves an app parked.
#
# Both cgroup layouts are supported, because the memory controller's mount
# decides which one a device has:
#
#   v2  /sys/fs/cgroup[/apps]/uid_*   memory.current      cgroup.freeze (0|1)
#   v1  /dev/memcg[/apps]/uid_*       memory.usage_in_bytes  freezer.state (FROZEN|THAWED)
#
# On v1 the memory.reclaim file only exists when the kernel carries this
# module's memcg_v1_reclaim graft (mem_cgroup_legacy_files[]); groups without it
# are skipped rather than reported as reclaimed.  The default roots are
# /sys/fs/cgroup and /dev/memcg, and --cgroup-root can name others.
#
# Lifecycle unfreezing (intent / job / activity resume) stays with the platform
# ActivityManager; this script deliberately does not reimplement it, which is why
# freeze is opt-in and always undone within one sweep.
#
# Observability: the kernel graft surfaces cfr_reclaim_* counters in
# memory.stat (v2) and memcg_stat_show (v1), so a sweep is verifiable with
# `grep cfr_reclaim memory.stat`, `am freeze/unfreeze` and Perfetto's Freezer
# track.  --list shows what the script would touch, without touching it.
#
# Group selection: by default every uid_* group is a target, which matches the
# AOSP layout.  Some ROMs keep the memory controller on v1 and name their groups
# instead (HyperOS: freeze-app, game, mimd, protect_memcg_*); those need
# --group NAME, which is explicit on purpose so a sweep can never wander into a
# vendor group nobody asked for.
#
# Usage:
#   cached_freeze_reclaim.sh [--dry-run] [--freeze] [--list] [--interval SECS]
#                            [--quota-mb N] [--cgroup-root PATH] [--uid UID] ...
#                            [--group NAME] ...
# Defaults: interval 60s, no quota cap (0 = write memory.current whole),
# no freezing, roots /sys/fs/cgroup then /dev/memcg, per-UID groups.
# Environment: CFR_ONE_SHOT=1 runs a single sweep and exits (used by the
# runtime companion module's supervisor); CFR_ALLOW_EMPTY=1 lets the daemon loop
# start even when no cached group is visible.
# Exit codes: 0 ok, 2 bad usage, 1 runtime failure (including "no cached group
# found", which is the condition that would otherwise hide a silent no-op).
set -eu

DRY_RUN=0
DO_FREEZE=0
DO_LIST=0
INTERVAL=60
QUOTA_MB=0
ROOTS=""
UIDS=""
GROUP_NAMES=""
DEFAULT_ROOTS="/sys/fs/cgroup /dev/memcg"

usage() {
  sed -n '2,53p' "$0"
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --freeze) DO_FREEZE=1; shift ;;
    --list) DO_LIST=1; shift ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --quota-mb) QUOTA_MB="$2"; shift 2 ;;
    --cgroup-root) ROOTS="$ROOTS $2"; shift 2 ;;
    --uid) UIDS="$UIDS $2"; shift 2 ;;
    --group) GROUP_NAMES="$GROUP_NAMES $2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done

case "$INTERVAL" in ''|*[!0-9]*) echo "bad --interval: $INTERVAL" >&2; exit 2;; esac
case "$QUOTA_MB" in ''|*[!0-9]*) echo "bad --quota-mb: $QUOTA_MB" >&2; exit 2;; esac
for u in $UIDS; do
  case "$u" in ''|*[!0-9]*) echo "bad --uid: $u" >&2; exit 2;; esac
done
for g in $GROUP_NAMES; do
  # A group is a single path component: never a path, never a traversal.
  case "$g" in
    ''|*/*|*..*) echo "bad --group: $g" >&2; exit 2;;
    *[!A-Za-z0-9_.-]*) echo "bad --group: $g" >&2; exit 2;;
  esac
done
[ -n "$ROOTS" ] || ROOTS="$DEFAULT_ROOTS"

# quota 0 means "no cap": write memory.current whole, matching AOSP.
# Through awk on purpose: /system/bin/sh is Android's mksh and wraps at 2^31 on
# some ROMs (measured on the target device), and a 4 GiB quota is exactly at
# that boundary -- a wrapped negative quota silently reclaims nothing.
quota_bytes=0
if [ "$QUOTA_MB" -gt 0 ]; then
  quota_bytes="$(awk -v mb="$QUOTA_MB" 'BEGIN { printf "%d\n", mb * 1048576 }')"
fi

# Every uid_* group under the configured roots that actually exposes
# memory.reclaim; a v1 group without the graft is not a reclaim target.
discover_groups() {
  for root in $ROOTS; do
    for parent in "$root/apps" "$root"; do
      [ -d "$parent" ] || continue
      for dir in "$parent"/uid_*; do
        [ -d "$dir" ] || continue
        [ -f "$dir/memory.reclaim" ] || continue
        printf '%s\n' "$dir"
      done
    done
  done
  return 0
}

# groups_for: discovery, narrowed to the --uid list when one was given
groups_for() {
  if [ -z "$UIDS" ] && [ -z "$GROUP_NAMES" ]; then
    discover_groups
    return 0
  fi

  # --uid and --group both name a leaf directory under each root (and under
  # each root's apps/), so they share one resolution loop.
  _gf_names=""
  for _gf_uid in $UIDS; do
    _gf_names="$_gf_names
uid_$_gf_uid"
  done
  for _gf_group in $GROUP_NAMES; do
    _gf_names="$_gf_names
$_gf_group"
  done

  for _gf_root in $ROOTS; do
    for _gf_parent in "$_gf_root/apps" "$_gf_root"; do
      [ -d "$_gf_parent" ] || continue
      while read -r _gf_name; do
        [ -n "$_gf_name" ] || continue
        _gf_dir="$_gf_parent/$_gf_name"
        [ -d "$_gf_dir" ] || continue
        [ -f "$_gf_dir/memory.reclaim" ] || continue
        printf '%s\n' "$_gf_dir"
      done <<EOF
$_gf_names
EOF
    done
  done
  return 0
}

# The memory figure differs by layout and is the one number the sweep needs.
group_bytes() {
  if [ -f "$1/memory.current" ]; then
    cat "$1/memory.current" 2>/dev/null || true
  else
    cat "$1/memory.usage_in_bytes" 2>/dev/null || true
  fi
}

# Freezer control differs by layout too; a group with neither cannot be frozen,
# which the caller treats as "reclaim without freezing" rather than an error.
set_frozen() {
  if [ -f "$1/cgroup.freeze" ]; then
    echo "$2" > "$1/cgroup.freeze" 2>/dev/null || return 1
    return 0
  fi
  if [ -f "$1/freezer.state" ]; then
    if [ "$2" = "1" ]; then
      echo FROZEN > "$1/freezer.state" 2>/dev/null || return 1
    else
      echo THAWED > "$1/freezer.state" 2>/dev/null || return 1
    fi
    return 0
  fi
  return 1
}

CFR_FOUND=0
CFR_SWEPT=0

sweep_once() {
  CFR_FOUND=0
  CFR_SWEPT=0
  CFR_GROUPS="$(groups_for)"

  while read -r dir; do
    [ -n "$dir" ] || continue
    CFR_FOUND=$(( CFR_FOUND + 1 ))

    cur="$(group_bytes "$dir" | tr -d ' \r\n')"
    cur_ok=1
    case "$cur" in ''|*[!0-9]*) cur_ok=0 ;; esac

    # --list is a diagnosis: report every discovered group even when it has
    # nothing charged right now, otherwise "no output, exit 0" looks exactly
    # like "nothing was found" when the truth is "found, currently empty".
    if [ "$DO_LIST" = 1 ]; then
      if [ "$cur_ok" = 1 ]; then
        printf '%s  %s bytes\n' "$dir" "$cur"
      else
        printf '%s  (no size file)\n' "$dir"
      fi
      continue
    fi

    [ "$cur_ok" = 1 ] || continue
    # Byte counts, so the comparison goes through awk: /system/bin/sh is
    # Android's mksh and on some ROMs wraps numeric comparison at 2^31
    # (measured on the target device), which would mis-rank groups by an
    # order of magnitude -- or drop every group as "empty".
    awk -v a="$cur" 'BEGIN { exit !(a > 0) }' || continue
    if [ "$quota_bytes" -gt 0 ] \
       && ! awk -v a="$cur" -v b="$quota_bytes" 'BEGIN { exit !(a <= b) }'; then
      cur="$quota_bytes"
    fi

    if [ "$DRY_RUN" = 1 ]; then
      if [ "$DO_FREEZE" = 1 ]; then
        echo "[dry-run] freeze+reclaim+thaw $cur bytes from $dir"
      else
        echo "[dry-run] reclaim $cur bytes from $dir"
      fi
      CFR_SWEPT=$(( CFR_SWEPT + 1 ))
      continue
    fi

    frozen=0
    if [ "$DO_FREEZE" = 1 ] && set_frozen "$dir" 1; then
      frozen=1
    fi

    if ! echo "$cur" > "$dir/memory.reclaim" 2>/dev/null; then
      echo "reclaim failed for $dir (group gone?)" >&2
    else
      CFR_SWEPT=$(( CFR_SWEPT + 1 ))
    fi

    if [ "$frozen" = 1 ]; then
      set_frozen "$dir" 0 || echo "could not thaw $dir" >&2
    fi
  done <<EOF
$CFR_GROUPS
EOF

  [ "$CFR_FOUND" -gt 0 ]
}

if [ "$DO_LIST" = 1 ]; then
  sweep_once
  [ "$CFR_FOUND" -gt 0 ] || {
    echo "cached_freeze_reclaim: no reclaimable group (uid_* or --group) under:$ROOTS" >&2
    exit 1
  }
  exit 0
fi

if [ "${CFR_ONE_SHOT:-0}" = 1 ]; then
  sweep_once || {
    echo "cached_freeze_reclaim: no reclaimable group (uid_* or --group) under:$ROOTS" >&2
    exit 1
  }
  exit 0
fi

# Refuse to start a long-running loop on a host with no cached-app groups:
# otherwise the daemon sleeps forever doing nothing but hiding the mismatch.
if ! sweep_once && [ "${CFR_ALLOW_EMPTY:-0}" != 1 ]; then
  echo "cached_freeze_reclaim: no reclaimable group (uid_* or --group) under:$ROOTS" >&2
  echo "  (v1 devices expose memory.reclaim only with this module's memcg_v1_reclaim graft)" >&2
  echo "  (some ROMs name their groups instead of using uid_*: pass --group NAME)" >&2
  echo "  (set CFR_ALLOW_EMPTY=1 to run the loop anyway)" >&2
  exit 1
fi

while true; do
  sweep_once || true
  sleep "$INTERVAL"
done
