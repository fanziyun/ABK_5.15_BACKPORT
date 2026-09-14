#!/system/bin/sh
set -u
CTL=/sys/class/zram-control
F=/data/local/tmp/b17
IMG=/data/per_boot/zram/b17_test.img
DATA=$F/data
MB=32

cs()  { awk '/^voluntary_ctxt_switches/{print $2}' /proc/$$/status; }
ics() { awk '/^nonvoluntary_ctxt_switches/{print $2}' /proc/$$/status; }
jj()  { awk '{print $14+$15}' /proc/$$/stat; }
up()  { awk '{print $1}' /proc/uptime | tr -d '.'; }
h8()  { printf '%s' "$1" | cut -c1-8; }

wipe() {
  for d in /sys/block/zram1 /sys/block/zram2 /sys/block/zram3 /sys/block/zram4; do
    [ -d "$d" ] || continue
    id=$(printf '%s' "$d" | sed 's|.*zram||')
    echo 1 > $d/reset 2>/dev/null
    echo "$id" > $CTL/hot_remove 2>/dev/null
  done
  for l in $(losetup -a | grep b17_test.img | cut -d: -f1); do losetup -d "$l" 2>/dev/null; done
  rm -f "$IMG"
}

run_case() {
  b=$1; c=$2
  wipe
  id=$(cat $CTL/hot_add)
  d=/sys/block/zram$id
  dd if=/dev/zero of="$IMG" bs=1048576 count=1 seek=511 2>/dev/null
  loop=$(losetup -f | tr -d ' \r\n')
  losetup "$loop" "$IMG" || { echo "losetup failed"; return 1; }

  echo "$b" > $d/writeback_batch_size
  echo "$c" > $d/compressed_writeback
  echo 0   > $d/writeback_limit_enable
  echo "$loop" > $d/backing_dev
  echo 67108864 > $d/disksize

  dd if="$DATA" of=/dev/block/zram$id bs=1048576 count=$MB 2>/dev/null
  dd if=/dev/block/zram$id of="$F/rb1" bs=1048576 count=$MB 2>/dev/null
  m1=$(md5sum "$F/rb1" | cut -d' ' -f1)
  ms1=$(cat $d/mm_stat | tr -s ' ' | cut -d' ' -f1-2)

  echo all > $d/idle
  c0=$(cs); i0=$(ics); j0=$(jj); u0=$(up)
  echo idle > $d/writeback; rc=$?
  u1=$(up); c1=$(cs); i1=$(ics); j1=$(jj)

  dd if=/dev/block/zram$id of="$F/rb2" bs=1048576 count=$MB 2>/dev/null
  m2=$(md5sum "$F/rb2" | cut -d' ' -f1)

  printf 'batch=%-4s cwb=%s | rc=%s wall=%sms vcs=%-6s ivcs=%-4s cpu_j=%s | bd=[%s] | md5 %s %s %s | orig/compr=%s\n' \
    "$b" "$c" "$rc" "$(( (u1-u0)*10 ))" "$((c1-c0))" "$((i1-i0))" "$((j1-j0))" \
    "$(cat $d/bd_stat | tr -s ' ' | sed 's/^ //;s/ $//')" \
    "$(h8 "$m1")" "$( [ "$m1" = "$m2" ] && echo EQ || echo NE )" "$(h8 "$m2")" "$ms1"

  echo 1 > $d/reset
  losetup -d "$loop"
  echo "$id" > $CTL/hot_remove
}

echo "selinux before: $(getenforce)"
setenforce 0
echo "selinux during: $(getenforce)"
echo
echo "data md5: $(md5sum $DATA | cut -d' ' -f1)  size=$(wc -c < $DATA)"
echo
echo "### matrix A: compressed_writeback=0"
run_case 1 0
run_case 32 0
run_case 256 0
echo
echo "### matrix B: compressed_writeback=1"
run_case 1 1
run_case 32 1
run_case 256 1
echo
echo "### repeat: the two key cells again"
run_case 1 0
run_case 32 0
echo
wipe
setenforce 1
echo "selinux after: $(getenforce)"
echo "zram devices left: $(ls -d /sys/block/zram* | tr '\n' ' ')"
echo "loops on test img: $(losetup -a | grep -c b17_test.img)"
