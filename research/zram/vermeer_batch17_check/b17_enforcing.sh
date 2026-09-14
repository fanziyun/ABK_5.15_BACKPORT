#!/system/bin/sh
# b17_enforcing.sh -- does a KernelSU kernel-domain sepolicy rule open the zram
# writeback path while SELinux stays Enforcing?
#
# Batch 17 measured the writeback behaviour with 'setenforce 0', because under
# Enforcing the loop worker (u:r:kernel:s0) has no permission on the backing
# file and every page degrades to -EIO.  This script never touches setenforce:
# it is run twice -- before and after 'ksud sepolicy apply' -- and its job is to
# show the difference, plus any AVC that survives the rule.
#
# Usage: sh b17_enforcing.sh <phase-label>
set -u
CTL=/sys/class/zram-control
F=/data/local/tmp/b17
IMG=/data/per_boot/zram/b17_test.img
DATA=$F/data
MB=32
PHASE="${1:-unlabelled}"

h8() { printf '%s' "$1" | cut -c1-8; }

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

  echo all > $d/idle
  u0=$(awk '{print $1}' /proc/uptime | tr -d '.')
  n0=$(dmesg 2>/dev/null | wc -l)
  echo idle > $d/writeback; rc=$?
  u1=$(awk '{print $1}' /proc/uptime | tr -d '.')
  avc=$(dmesg 2>/dev/null | tail -n +$((n0 + 1)) | grep -c 'avc: *denied')
  avc1=$(dmesg 2>/dev/null | tail -n +$((n0 + 1)) | grep 'avc: *denied' | head -2 | tr -s ' ')

  dd if=/dev/block/zram$id of="$F/rb2" bs=1048576 count=$MB 2>/dev/null
  m2=$(md5sum "$F/rb2" | cut -d' ' -f1)

  printf 'phase=%-12s batch=%-4s cwb=%s rc=%s wall=%-6sms bd=[%s] io=[%s] avc=%s md5 %s %s %s\n' \
    "$PHASE" "$b" "$c" "$rc" "$(( (u1-u0)*10 ))" \
    "$(cat $d/bd_stat | tr -s ' ' | sed 's/^ //;s/ $//')" \
    "$(cat $d/io_stat | tr -s ' ' | sed 's/^ //;s/ $//')" \
    "$avc" "$(h8 "$m1")" "$( [ "$m1" = "$m2" ] && echo EQ || echo NE )" "$(h8 "$m2")"
  [ -n "$avc1" ] && printf '    avc: %s\n' "$avc1"

  echo 1 > $d/reset
  losetup -d "$loop"
  echo "$id" > $CTL/hot_remove
}

echo "selinux: $(getenforce)  config ZRAM_WRITEBACK: $(zcat /proc/config.gz 2>/dev/null | grep -c '^CONFIG_ZRAM_WRITEBACK=y')"
echo "data md5: $(md5sum $DATA | cut -d' ' -f1)  size=$(wc -c < $DATA)"
run_case 32 0
run_case 1  0
wipe
echo "selinux after: $(getenforce)  zram left: $(ls -d /sys/block/zram* | tr '\n' ' ')"
