#!/bin/sh
# zram_recompress_trigger.sh - drive the zram recompression paths.
#
# ZRAM_MULTI_COMP ships the machinery but nothing triggers it: a page only
# gets recompressed when userspace (a) marks stored pages idle and (b) asks
# for a recompression pass.  This tool does both, and refuses to pretend it
# worked when the kernel side is not actually armed -- or when the secondary
# compressor is the same algorithm as the primary, where a pass burns CPU and
# rewrites identical bytes for zero ratio gain.
#
# Typical use (two phases, in this order):
#   once, shortly after boot:   zram_recompress_trigger.sh --idle-age 3600
#   periodically thereafter:    zram_recompress_trigger.sh --daemon
#
# --idle-age SECS marks only the pages nothing has touched for SECS seconds
# (the kernel reads entry activity times under CONFIG_ZRAM_TRACK_ENTRY_ACTIME),
# so a later pass recompresses genuinely cold pages.  --mark-idle is the blunt
# variant: it writes "all", which marks every stored page cold *now*, so use it
# on its own and not in front of the pass you want to be age-driven.  Marking
# is a one-shot action: the daemon loop never re-marks unless
# --mark-each-pass is given explicitly, because marking and recompressing in
# the same instant recompresses everything every time.
#
# THE MARK IS ONE-SHOT FOR A HARDER REASON.  mark_idle() *sets* ZRAM_IDLE on
# every page older than the cutoff, and a recompression clears the flag without
# refreshing the page's age: zram_recompress() reads through
# zram_read_from_zspool(), not zram_accessed(), so ac_time keeps its old value.
# A page the last sweep just recompressed therefore still looks "older than the
# cutoff" and the next mark hands it straight back.  Since the kernel's sweep
# always restarts at index 0 and spends its max_pages budget on attempts -- even
# on ones that cannot improve anything -- marking before every pass pins a
# capped sweep to the same first max_pages entries forever and the rest of the
# device is never reached.  A capped sweep makes progress only because a pass
# clears IDLE and nothing puts it back: mark, then pass with --no-mark until
# the caller decides to re-mark.
#
# An uncapped pass is an unbounded amount of work: it attempts every idle
# entry on the device, in index order, and the CPU bill is paid on a phone
# that is trying to stay quiet.  max_pages (mainline 34efe1c3b688, grafted by
# this module as Batch 24) bounds the number of *attempts*; the scan restarts
# at index 0 each pass, so a cap drains a prefix of the cold set per pass and
# later passes continue to work through the ones the age mark still reports.
# A kernel without that graft ignores an unknown parameter rather than
# failing the write, so passing --max-pages to an older kernel is harmless --
# it just behaves as if the option had not been given.
#
# Options:
#   --device N        zram device index (default 0)
#   --mark-idle       mark every stored page idle ("all"), then pass once
#   --idle-age SECS   mark pages untouched for >= SECS seconds idle, then pass
#   --mode sync|async async (default) uses recompress_async
#   --threshold N     only recompress entries >= N bytes (default 0 = all)
#   --max-pages N     attempt at most N entries per pass (default 0 = no cap)
#   --interval SECS   seconds between passes with --daemon (default 1800)
#   --daemon          keep sweeping (marks once unless --mark-each-pass)
#   --mark-each-pass  with --daemon: re-run the mark step every pass
#   --no-mark         skip the mark step: pass only (keeps a capped sweep
#                     advancing instead of re-draining the same prefix)
#   --allow-same-algo run even when the secondary equals the primary
#   --status          print the device state and exit
#   --dry-run         report what would be written, write nothing
#   --sys-root PATH   sysfs root (default /sys; for testing)
#   -h, --help        this text
# Exit codes: 0 ok, 1 runtime failure (including "recompression is not armed",
# which is the condition this tool exists to surface), 2 bad usage, 3 the
# secondary compressor equals the primary one (a guaranteed no-op pass).
set -eu

DEV=0
MARK_IDLE=0
IDLE_AGE=
MODE=async
THRESHOLD=0
MAX_PAGES=0
INTERVAL=1800
DAEMON=0
MARK_EACH_PASS=0
NO_MARK=0
ALLOW_SAME_ALGO=0
DRY_RUN=0
STATUS=0
SYS_ROOT=/sys

usage() {
  # The manual is everything between the shebang and the first line of code, so
  # a line added to it cannot silently truncate the help the way a hardcoded
  # range does.  (This file carried `sed -n '2,51p'` until --no-mark pushed the
  # first line of code past 51; cached_freeze_reclaim.sh already does it this
  # way for the same reason.)
  sed -n '2,/^[^#]/p' "$0" | sed '$d'
  exit 2
}

fail() {
  echo "zram_recompress_trigger: $*" >&2
  exit 1
}

# Argument validation is a usage error (2), not a runtime failure (1).
usage_fail() {
  echo "zram_recompress_trigger: $*" >&2
  exit 2
}

# Exit code 3 has its own meaning, so it cannot go through fail().
same_algo() {
  echo "zram_recompress_trigger: $*" >&2
  exit 3
}

while [ $# -gt 0 ]; do
  case "$1" in
    --device) DEV="$2"; shift 2 ;;
    --mark-idle) MARK_IDLE=1; shift ;;
    --idle-age) IDLE_AGE="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --threshold) THRESHOLD="$2"; shift 2 ;;
    --max-pages) MAX_PAGES="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --daemon) DAEMON=1; shift ;;
    --mark-each-pass) MARK_EACH_PASS=1; shift ;;
    --no-mark) NO_MARK=1; shift ;;
    --allow-same-algo) ALLOW_SAME_ALGO=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --status) STATUS=1; shift ;;
    --sys-root) SYS_ROOT="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) usage_fail "unknown argument: $1" ;;
  esac
done

case "$DEV" in ''|*[!0-9]*) usage_fail "bad --device: $DEV" ;; esac
case "$THRESHOLD" in ''|*[!0-9]*) usage_fail "bad --threshold: $THRESHOLD" ;; esac
case "$MAX_PAGES" in ''|*[!0-9]*) usage_fail "bad --max-pages: $MAX_PAGES" ;; esac
case "$INTERVAL" in ''|*[!0-9]*) usage_fail "bad --interval: $INTERVAL" ;; esac
case "$MODE" in sync|async) ;; *) usage_fail "bad --mode: $MODE (want sync or async)" ;; esac
if [ -n "$IDLE_AGE" ]; then
  case "$IDLE_AGE" in ''|*[!0-9]*) usage_fail "bad --idle-age: $IDLE_AGE" ;; esac
  [ "$IDLE_AGE" -gt 0 ] || usage_fail "--idle-age must be > 0 (use --mark-idle for all)"
fi
[ "$MARK_IDLE" = 0 ] || [ -z "$IDLE_AGE" ] \
  || usage_fail "--mark-idle and --idle-age are mutually exclusive"
[ "$DAEMON" = 0 ] || [ "$MARK_EACH_PASS" = 0 ] || [ -n "$IDLE_AGE" ] \
  || [ "$MARK_IDLE" = 1 ] \
  || usage_fail "--mark-each-pass needs --mark-idle or --idle-age"
# --no-mark says "do not mark"; --mark-idle says "mark every page".  Honouring
# both would mean picking a winner, so refuse to guess.
[ "$NO_MARK" = 0 ] || [ "$MARK_IDLE" = 0 ] \
  || usage_fail "--no-mark and --mark-idle are mutually exclusive"
[ "$NO_MARK" = 0 ] || [ "$MARK_EACH_PASS" = 0 ] \
  || usage_fail "--no-mark and --mark-each-pass are mutually exclusive"

DIR=$SYS_ROOT/block/zram$DEV
[ -d "$DIR" ] || fail "$DIR does not exist"

ALGO="$(cat "$DIR/recomp_algorithm" 2>/dev/null || true)"
STAT="$(cat "$DIR/mm_stat" 2>/dev/null || true)"

# Active algorithm of a comp_algorithm-style file: the bracketed token, the
# `algo=<name>` argument a pre-disksize write carries, or the last
# whitespace-separated token when the file carries a bare name (which is how a
# caller writes it, and how the test fixtures store it).
active_algo() {
  local file bracketed arg
  file="$1"
  [ -f "$file" ] || return 0
  bracketed="$(sed -n 's/.*\[\([^]]*\)\].*/\1/p' "$file" 2>/dev/null | head -n 1)"
  if [ -n "$bracketed" ]; then
    printf '%s\n' "$bracketed"
    return 0
  fi
  arg="$(sed -n 's/.*algo=\([^[:space:]]*\).*/\1/p' "$file" 2>/dev/null | head -n 1)"
  if [ -n "$arg" ]; then
    printf '%s\n' "$arg"
    return 0
  fi
  awk 'NR==1 { print $NF }' "$file" 2>/dev/null
}

PRIMARY_ALGO="$(active_algo "$DIR/comp_algorithm")"
SECONDARY_ALGO="$(active_algo "$DIR/recomp_algorithm")"

if [ "$STATUS" = 1 ]; then
  echo "zram$DEV disksize   = $(cat "$DIR/disksize" 2>/dev/null)"
  echo "zram$DEV algorithm  = $(cat "$DIR/comp_algorithm" 2>/dev/null)"
  echo "zram$DEV primary    = ${PRIMARY_ALGO:-unknown}"
  echo "zram$DEV secondary  = ${SECONDARY_ALGO:-none}"
  echo "zram$DEV recomp     = [$ALGO]"
  echo "zram$DEV mm_stat    = $STAT"
  if [ "$MAX_PAGES" -gt 0 ]; then
    echo "zram$DEV pass cap   = $MAX_PAGES attempted entries per pass"
    echo "                 (needs the kernel max_pages graft; an older kernel"
    echo "                  ignores the parameter and the pass stays unbounded)"
  else
    echo "zram$DEV pass cap   = none (a pass attempts every idle entry)"
  fi
  [ -n "$ALGO" ] || echo "zram$DEV: recompression is NOT armed (no secondary compressor)"
  if [ -n "$PRIMARY_ALGO" ] && [ "$PRIMARY_ALGO" = "$SECONDARY_ALGO" ]; then
    echo "zram$DEV: primary and secondary are both $PRIMARY_ALGO, so a pass"
    echo "         cannot shrink anything (see --allow-same-algo)"
  fi
  exit 0
fi

# The single most likely failure is a silently unarmed kernel: with an empty
# recomp_algorithm every recompress pass returns success without touching a
# page.  Say so loudly instead of reporting a successful no-op.
if [ -z "$ALGO" ]; then
  echo "zram$DEV: recompression is NOT armed: $DIR/recomp_algorithm is empty," >&2
  echo "so every pass would be a no-op.  Register a secondary compressor with" >&2
  echo "the module parameter zram.abk_recomp_algo (default zstd, read-only)," >&2
  echo "or write 'algo=<name> priority=1' to $DIR/recomp_algorithm before" >&2
  echo "disksize is set." >&2
  exit 1
fi

if [ "$MODE" = async ] && [ ! -e "$DIR/recompress_async" ]; then
  fail "$DIR/recompress_async is missing; use --mode sync"
fi

# Same-algorithm passes rewrite identical bytes: CPU burned, ratio unchanged.
if [ "$ALLOW_SAME_ALGO" = 0 ] && [ -n "$PRIMARY_ALGO" ] \
   && [ "$PRIMARY_ALGO" = "$SECONDARY_ALGO" ]; then
  same_algo "secondary compressor equals the primary ($PRIMARY_ALGO); a pass" \
            "would be a no-op.  Pick a stronger secondary (zstd), or pass" \
            "--allow-same-algo to run anyway."
fi

# 2nd field of mm_stat is compr_data_size -- the number that must go down.
compr_size() {
  awk '{ print $2 }' "$DIR/mm_stat" 2>/dev/null || echo 0
}

mark() {
  local value
  [ "$NO_MARK" = 0 ] || return 0
  if [ "$MARK_IDLE" = 1 ]; then
    value=all
  else
    [ -n "$IDLE_AGE" ] || return 0
    value="$IDLE_AGE"
  fi

  if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] echo $value > $DIR/idle"
  else
    echo "$value" > "$DIR/idle"
  fi
}

pass() {
  local args node cap
  args="type=idle threshold=$THRESHOLD"
  [ "$MAX_PAGES" -eq 0 ] || args="$args max_pages=$MAX_PAGES"
  if [ "$MODE" = async ]; then
    node="$DIR/recompress_async"
  else
    node="$DIR/recompress"
  fi
  cap=""
  [ "$MAX_PAGES" -eq 0 ] || cap=", cap $MAX_PAGES attempts"

  if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] echo \"$args\" > $node"
    return 0
  fi

  before="$(compr_size)"
  echo "$args" > "$node"
  after="$(compr_size)"
  echo "zram$DEV: pass done (compr_data_size $before -> $after$cap)"
}

# The mark step is a one-shot action: marking and recompressing in the same
# instant would recompress every stored page on every pass.
mark

if [ "$DAEMON" = 0 ]; then
  pass
  exit 0
fi

while :; do
  [ "$MARK_EACH_PASS" = 0 ] || mark
  pass
  sleep "$INTERVAL"
done
