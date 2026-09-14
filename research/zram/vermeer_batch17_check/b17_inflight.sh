#!/system/bin/sh
# b17_inflight.sh -- direct on-device measurement of zram writeback bio batching,
# and of the compressed-writeback CPU trade, with SELinux left Enforcing.
#
# Why this script exists, and why it is not a re-run of b17_ab.sh:
#
#  1. Batching in this port is NOT "fewer or larger bios".  The batched path
#     (scripts/batch17_core_zram_writeback.py) still builds exactly one request
#     per page -- bio_init(&req->bio, &req->bio_vec, 1) +
#     bio_add_page(&req->bio, req->page, PAGE_SIZE, 0) -- and submits them
#     asynchronously up to wb_batch_size.  So request counts, bio counts and
#     total sectors are IDENTICAL for every batch size, and the only direct
#     observable that can distinguish batch=1 from batch=32 is how many of those
#     requests are in flight at the same time.  That is what this script reads,
#     from the block_rq_issue / block_rq_complete tracepoints filtered to the
#     test loop device.  (block_bio_complete is useless here: it only fires for
#     bios carrying BIO_TRACE_COMPLETION, i.e. under blktrace.)
#
#     The reported metrics are:
#       inflight max          peak submit/complete overlap == the real batch depth
#       time-weighted avg     integral of in-flight over the window / window
#       issue gap p50/p90     microseconds between consecutive submissions; a
#                             synchronous one-page-per-wait loop is paced by the
#                             round trip, a batched one is not
#
#  2. Batch 17 measured everything under 'setenforce 0' because the kernel
#     domain had no permission on the backing file.  Batch 18 (companion
#     v0.7.0) submitted the rule that fixes it, so the whole matrix can now be
#     measured with the policy untouched.  This script never calls setenforce
#     and aborts if the device is not Enforcing.
#
# Two modes:
#   sh b17_inflight.sh trace   # batch in {1,8,32,256}: in-flight depth + wall/CPU
#   sh b17_inflight.sh cwb     # batch=32, cwb in {0,1}: write/read-back CPU trade
#
# Isolated zramN (hot_add) only.  zram0 is never touched; nothing is left behind.
set -u

CTL=/sys/class/zram-control
F=/data/local/tmp/b17
IMG=/data/per_boot/zram/b17_probe.img
DATA=$F/data
MB=32
DISKSIZE=67108864
TR=/sys/kernel/tracing
MODE=trace
[ $# -ge 1 ] && MODE="$1"

ID=""; D=""; LOOP=""

now_cs() { awk '{print $1}' /proc/uptime | tr -d '.'; }
cs()     { awk '/^voluntary_ctxt_switches/{print $2}' /proc/$$/status; }
jj()     { awk '{print $14+$15}' /proc/$$/stat; }
sycpu()  { awk '/^cpu /{print $2+$3+$4+$7+$8+$9}' /proc/stat; }
wbs()    { cat "$D/bd_stat" | tr -s ' ' | sed 's/^ //;s/ $//'; }

# ---- SELinux gate: this script must never weaken the policy ----------------
enf=$(getenforce 2>/dev/null)
if [ "$enf" != "Enforcing" ]; then
  echo "ABORT: SELinux is '$enf', expected Enforcing."
  echo "       Batch 18's rule is what makes this measurable; do not run this"
  echo "       with the policy relaxed -- b17_ab.sh did that and its numbers"
  echo "       are labelled permissive-only for exactly this reason."
  exit 2
fi

mkdir -p "$F"
if [ ! -f "$DATA" ] || [ "$(wc -c < "$DATA")" -lt 33554432 ]; then
  cat /system/lib64/*.so 2>/dev/null | head -c 33554432 > "$DATA"
fi

# ---- tracefs plumbing --------------------------------------------------------
BUFSZ_SAVED=$(cat $TR/buffer_size_kb 2>/dev/null)

tp_off() {
  echo 0 > $TR/tracing_on 2>/dev/null
  for e in block_rq_issue block_rq_complete; do
    echo 0 > $TR/events/block/$e/enable 2>/dev/null
    echo 0 > $TR/events/block/$e/filter 2>/dev/null
  done
}

tp_on() {   # $1 = loop device node
  maj=$(cat /sys/block/$(basename "$1")/dev | cut -d: -f1)
  min=$(cat /sys/block/$(basename "$1")/dev | cut -d: -f2)
  raw=$(( maj * 1048576 + min ))
  echo 4096 > $TR/buffer_size_kb 2>/dev/null
  for e in block_rq_issue block_rq_complete; do
    echo "dev == $raw" > $TR/events/block/$e/filter
    echo 1 > $TR/events/block/$e/enable
  done
  echo 0 > $TR/trace
  echo 1 > $TR/tracing_on
}

# in-flight depth + issue-gap shape, from one pass over the captured trace.
# Timeline keys are microsecond*2 (+1 for issue) so a plain 'sort -n' orders the
# events by time and puts a completion before an issue at the same microsecond
# (conservative for the peak).
an_trace() {   # $1 = trace file ; prints "max twavg issues completes sectors p50 p90"
  awk -F': ' '
    function us(t, a) { split(t, a, "."); return a[1] * 1000000 + a[2] }
    /block_rq_issue:/    { split($1, a, " "); print us(a[length(a)]) * 2 + 1, "+1" }
    /block_rq_complete:/ { split($1, a, " "); print us(a[length(a)]) * 2,     "-1" }
  ' "$1" > $F/tl
  sect=$(awk -F': ' '/block_rq_issue:/ {
            if (match($3, /\+ [0-9]+/)) s += substr($3, RSTART + 2, RLENGTH - 2)
          } END { print s + 0 }' "$1")
  set -- $(sort -n $F/tl | awk -v sect="$sect" '
    { cur += $2
      if (cur > max) max = cur
      if (n > 0) area += pcur * ($1 - pt)
      pcur = cur; pt = $1
      if (n == 0) t0 = $1
      n++
      if ($2 == "+1") ni++; else nc++
    }
    END {
      span = pt - t0
      printf "%d %.2f %d %d %d", max + 0, (span > 0 ? area / span : 0), ni + 0, nc + 0, sect + 0
    }')
  awk '$2 == "+1" { t = int(($1 - 1) / 2); if (n) print t - p; p = t; n++ }' $F/tl \
    | sort -n > $F/gaps
  ng=$(wc -l < $F/gaps)
  if [ "$ng" -ge 2 ]; then
    g50=$(sed -n "$(( (ng + 1) / 2 ))p" $F/gaps)
    g90=$(sed -n "$(( ng * 9 / 10 ))p" $F/gaps)
  else
    g50=-1; g90=-1
  fi
  echo "$1 $2 $3 $4 $5 $g50 $g90"
}

# ---- device plumbing ---------------------------------------------------------
wipe() {
  tp_off
  for d in /sys/block/zram1 /sys/block/zram2 /sys/block/zram3 /sys/block/zram4; do
    [ -d "$d" ] || continue
    id=$(printf '%s' "$d" | sed 's|.*zram||')
    echo 1 > $d/reset 2>/dev/null
    echo "$id" > $CTL/hot_remove 2>/dev/null
  done
  for l in $(losetup -a | grep b17_probe.img | cut -d: -f1); do losetup -d "$l" 2>/dev/null; done
  rm -f "$IMG"
  ID=""; D=""; LOOP=""
}

teardown() {
  tp_off
  [ -n "$D" ]    && echo 1 > $D/reset 2>/dev/null
  [ -n "$LOOP" ] && losetup -d "$LOOP" 2>/dev/null
  [ -n "$ID" ]   && echo "$ID" > $CTL/hot_remove 2>/dev/null
  rm -f "$IMG"
  ID=""; D=""; LOOP=""
}

setup() {   # $1 = batch size, $2 = compressed_writeback
  wipe
  ID=$(cat $CTL/hot_add)
  D=/sys/block/zram$ID
  dd if=/dev/zero of="$IMG" bs=1048576 count=1 seek=511 2>/dev/null
  LOOP=$(losetup -f | tr -d ' \r\n')
  losetup "$LOOP" "$IMG" || { echo "losetup failed" >&2; return 1; }
  # order matters: compressed_writeback is only writable before disksize
  echo "$1" > $D/writeback_batch_size
  echo "$2" > $D/compressed_writeback
  echo 0   > $D/writeback_limit_enable
  echo "$LOOP" > $D/backing_dev
  echo "$DISKSIZE" > $D/disksize
}

# fill, verify the fill is readable, THEN mark idle (a read clears ZRAM_IDLE,
# so 'echo all > idle' has to be the last thing before the writeback)
prep_data() {
  dd if="$DATA" of=/dev/block/zram$ID bs=1048576 count=$MB 2>/dev/null
  dd if=/dev/block/zram$ID of=$F/rb1 bs=1048576 count=$MB 2>/dev/null
  M1=$(md5sum $F/rb1 | cut -d' ' -f1)
  echo all > $D/idle
}

post_check() {
  dd if=/dev/block/zram$ID of=$F/rb2 bs=1048576 count=$MB 2>/dev/null
  M2=$(md5sum $F/rb2 | cut -d' ' -f1)
  if [ "$M1" = "$M2" ]; then EQ=EQ; else EQ=NE; fi
}

# ---- mode 1: batching --------------------------------------------------------
case_trace() {   # $1 = batch, $2 = 1 if traced
  b=$1; traced=$2
  setup "$b" 0 || return 1
  prep_data
  if [ "$traced" = "1" ]; then tp_on "$LOOP"; fi
  c0=$(cs); j0=$(jj); u0=$(now_cs)
  echo idle > $D/writeback; rc=$?
  u1=$(now_cs); j1=$(jj); c1=$(cs)
  if [ "$traced" = "1" ]; then
    cp $TR/trace $F/trace.b$b 2>/dev/null
    tp_off
  fi
  post_check
  if [ "$traced" = "1" ]; then
    set -- $(an_trace $F/trace.b$b)
    printf 'batch=%-4s cwb=0 traced=1 | rc=%s wall=%-6sms vcs=%-6s cpu_j=%-3s | rq issue=%-5s complete=%-5s sect=%-6s | inflight max=%-4s time-weighted avg=%-6s | issue gap p50=%-6s p90=%-5s | bd=[%s] md5=%s\n' \
      "$b" "$rc" "$(( (u1 - u0) * 10 ))" "$((c1 - c0))" "$((j1 - j0))" \
      "$3" "$4" "$5" "$1" "$2" "$6" "$7" "$(wbs)" "$EQ"
  else
    printf 'batch=%-4s cwb=0 traced=0 | rc=%s wall=%-6sms vcs=%-6s cpu_j=%-3s | (untraced) | bd=[%s] md5=%s\n' \
      "$b" "$rc" "$(( (u1 - u0) * 10 ))" "$((c1 - c0))" "$((j1 - j0))" "$(wbs)" "$EQ"
  fi
  teardown
}

# ---- mode 2: compressed writeback trade --------------------------------------
toms() { printf '%s' "$1" | awk '{ gsub(/s$/, ""); n = split($0, a, "m"); printf "%d", a[1] * 60000 + a[2] * 1000 }'; }

case_cwb() {   # $1 = cwb, $2 = rep
  c=$1; r=$2
  setup 32 "$c" || return 1
  prep_data
  p0=$(sycpu); j0=$(jj); u0=$(now_cs)
  echo idle > $D/writeback; rc=$?
  u1=$(now_cs); j1=$(jj); p1=$(sycpu)
  r0=$(sycpu); u2=$(now_cs)
  { time dd if=/dev/block/zram$ID of=$F/sink bs=1048576 count=$MB 2>/dev/null; } 2> $F/time.$$
  u3=$(now_cs); r1=$(sycpu)
  rb_u=$(awk '{ for (i = 1; i <= NF; i++) if ($i == "user") print $(i - 1) }' $F/time.$$)
  rb_s=$(awk '{ for (i = 1; i <= NF; i++) if ($i == "system") print $(i - 1) }' $F/time.$$)
  if [ -n "$rb_u" ]; then rb_u=$(toms "$rb_u"); else rb_u=-1; fi
  if [ -n "$rb_s" ]; then rb_s=$(toms "$rb_s"); else rb_s=-1; fi
  M2=$(md5sum $F/sink | cut -d' ' -f1)
  if [ "$M1" = "$M2" ]; then EQ=EQ; else EQ=NE; fi
  avc=$(dmesg 2>/dev/null | tail -300 | grep -c 'avc: *denied.*zram')
  printf 'rep=%s cwb=%s batch=32 | rc=%s | WB wall=%-6sms self_j=%-3s sys_j=%-4s | RB wall=%-6sms child_user=%-5sms child_sys=%-5sms sys_j=%-4s | bd=[%s] md5=%s zram_avc=%s\n' \
    "$r" "$c" "$rc" "$(( (u1 - u0) * 10 ))" "$((j1 - j0))" "$((p1 - p0))" \
    "$(( (u3 - u2) * 10 ))" "$rb_u" "$rb_s" "$((r1 - r0))" "$(wbs)" "$EQ" "$avc"
  rm -f $F/sink $F/time.$$
  teardown
}

# ---- run ---------------------------------------------------------------------
echo "selinux=$(getenforce) kernel=$(uname -r) zram_wb=$(zcat /proc/config.gz 2>/dev/null | grep -c '^CONFIG_ZRAM_WRITEBACK=y')"
echo "data=$(wc -c < $DATA) bytes md5=$(md5sum $DATA | cut -d' ' -f1)"
echo "zram0 untouched: backing=$(cat /sys/block/zram0/backing_dev | tr -d ' \r\n') bd=[$(cat /sys/block/zram0/bd_stat | tr -s ' ' | sed 's/^ //;s/ $//')]"
echo

if [ "$MODE" = "trace" ]; then
  echo "### batching: in-flight depth per wb_batch_size (32MiB, cwb=0)"
  for b in 1 8 32 256; do case_trace $b 1; done
  echo
  echo "### same configs without tracing (clean wall/CPU)"
  for b in 1 8 32 256; do case_trace $b 0; done
elif [ "$MODE" = "cwb" ]; then
  echo "### compressed writeback: write-side vs read-back CPU (32MiB, batch=32)"
  for r in 1 2 3; do
    case_cwb 0 $r
    case_cwb 1 $r
  done
else
  echo "usage: sh b17_inflight.sh [trace|cwb]" >&2
fi

echo
wipe
if [ -n "$BUFSZ_SAVED" ]; then echo "$BUFSZ_SAVED" > $TR/buffer_size_kb 2>/dev/null; fi
echo "final: selinux=$(getenforce) zram=$(ls -d /sys/block/zram* | tr '\n' ' ') loops_on_probe=$(losetup -a | grep -c b17_probe.img)"
