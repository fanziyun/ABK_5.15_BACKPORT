#!/bin/sh
# zram_recompress_trigger.sh - drive the zram recompression paths.
#
# ZRAM_MULTI_COMP ships the machinery but nothing triggers it: a page only
# gets recompressed when userspace (a) marks stored pages idle and (b) asks
# for a recompression pass.  This tool does both, and refuses to pretend it
# worked when the kernel side is not actually armed.
#
# Typical use:
#   once, shortly after boot:   zram_recompress_trigger.sh --mark-idle
#   periodically thereafter:    zram_recompress_trigger.sh --daemon
#
# --mark-idle writes "all" to /sys/block/zramN/idle, flagging every currently
# stored page as cold.  Any later read of such a page clears the flag again,
# so the periodic pass only recompresses pages nothing has touched since --
# exactly the "idle pages" the feature is meant to shrink.  The pass itself
# goes through recompress_async (the kernel-side worker) unless --mode sync.
#
# Options:
#   --device N        zram device index (default 0)
#   --mark-idle       mark all stored pages idle before the pass
#   --mode sync|async async (default) uses recompress_async
#   --threshold N     only recompress entries >= N bytes (default 0 = all)
#   --interval SECS   seconds between passes with --daemon (default 1800)
#   --daemon          keep sweeping
#   --status          print the device state and exit
#   --dry-run         report what would be written, write nothing
#   --sys-root PATH   sysfs root (default /sys; for testing)
#   -h, --help        this text
# Exit codes: 0 ok, 2 bad usage, 1 runtime failure (including "recompression
# is not armed", which is the condition this tool exists to surface).
set -eu

DEV=0
MARK_IDLE=0
MODE=async
THRESHOLD=0
INTERVAL=1800
DAEMON=0
DRY_RUN=0
STATUS=0
SYS_ROOT=/sys

usage() {
  sed -n '2,30p' "$0"
  exit 2
}

fail() {
  echo "zram_recompress_trigger: $*" >&2
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --device) DEV="$2"; shift 2 ;;
    --mark-idle) MARK_IDLE=1; shift ;;
    --mode) MODE="$2"; shift 2 ;;
    --threshold) THRESHOLD="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --daemon) DAEMON=1; shift ;;
    --status) STATUS=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --sys-root) SYS_ROOT="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) fail "unknown argument: $1" ;;
  esac
done

case "$DEV" in ''|*[!0-9]*) fail "bad --device: $DEV" ;; esac
case "$THRESHOLD" in ''|*[!0-9]*) fail "bad --threshold: $THRESHOLD" ;; esac
case "$INTERVAL" in ''|*[!0-9]*) fail "bad --interval: $INTERVAL" ;; esac
case "$MODE" in sync|async) ;; *) fail "bad --mode: $MODE (want sync or async)" ;; esac

DIR=$SYS_ROOT/block/zram$DEV
[ -d "$DIR" ] || fail "$DIR does not exist"

ALGO="$(cat "$DIR/recomp_algorithm" 2>/dev/null || true)"
STAT="$(cat "$DIR/mm_stat" 2>/dev/null || true)"

if [ "$STATUS" = 1 ]; then
  echo "zram$DEV disksize   = $(cat "$DIR/disksize" 2>/dev/null)"
  echo "zram$DEV algorithm  = $(cat "$DIR/comp_algorithm" 2>/dev/null)"
  echo "zram$DEV recomp     = [$ALGO]"
  echo "zram$DEV mm_stat    = $STAT"
  [ -n "$ALGO" ] || echo "zram$DEV: recompression is NOT armed (no secondary compressor)"
  exit 0
fi

# The single most likely failure is a silently unarmed kernel: with an empty
# recomp_algorithm every recompress pass returns success without touching a
# page.  Say so loudly instead of reporting a successful no-op.
if [ -z "$ALGO" ]; then
  echo "zram$DEV: recompression is NOT armed: $DIR/recomp_algorithm is empty," >&2
  echo "so every pass would be a no-op.  Register a secondary compressor with" >&2
  echo "the module parameter zram.abk_recomp_algo (default lz4hc), or write" >&2
  echo "'algo=<name> priority=1' to $DIR/recomp_algorithm before disksize is set." >&2
  exit 1
fi

if [ "$MODE" = async ] && [ ! -e "$DIR/recompress_async" ]; then
  fail "$DIR/recompress_async is missing; use --mode sync"
fi

# 2nd field of mm_stat is compr_data_size -- the number that must go down.
compr_size() {
  awk '{ print $2 }' "$DIR/mm_stat" 2>/dev/null || echo 0
}

pass() {
  if [ "$MARK_IDLE" = 1 ]; then
    if [ "$DRY_RUN" = 1 ]; then
      echo "[dry-run] echo all > $DIR/idle"
    else
      echo all > "$DIR/idle"
    fi
  fi

  if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] echo \"type=idle threshold=$THRESHOLD\" > $DIR/recompress_$MODE"
    return 0
  fi

  before="$(compr_size)"
  if [ "$MODE" = async ]; then
    echo "type=idle threshold=$THRESHOLD" > "$DIR/recompress_async"
  else
    echo "type=idle threshold=$THRESHOLD" > "$DIR/recompress"
  fi
  after="$(compr_size)"
  echo "zram$DEV: pass done (compr_data_size $before -> $after)"
}

if [ "$DAEMON" = 0 ]; then
  pass
  exit 0
fi

while :; do
  pass
  sleep "$INTERVAL"
done
