set -u
CTL=/sys/class/zram-control
F=/data/local/tmp/b17
IMG=/data/per_boot/zram/b17_test.img
DATA=$F/data
MB=32

for l in $(losetup -a | grep b17_test.img | cut -d: -f1); do losetup -d "$l" 2>/dev/null; done
id=$(cat $CTL/hot_add); D=/sys/block/zram$id
rm -f "$IMG"; dd if=/dev/zero of="$IMG" bs=1048576 count=1 seek=511 2>/dev/null
loop=$(losetup -f | tr -d ' \r\n'); losetup "$loop" "$IMG"
echo "$loop" > $D/backing_dev; echo 67108864 > $D/disksize

echo "=== knob boundaries (device $D) ==="
for v in 0 1 32 256 257 1000000 abc -1; do
  echo "$v" > $D/writeback_batch_size 2>/dev/null; rc=$?
  printf '  writeback_batch_size <- %-8s rc=%s now=%s\n' "$v" "$rc" "$(cat $D/writeback_batch_size)"
done
echo 32 > $D/writeback_batch_size
for v in 0 1 2 abc; do
  echo "$v" > $D/compressed_writeback 2>/dev/null; rc=$?
  printf '  compressed_writeback  <- %-4s rc=%s now=%s\n' "$v" "$rc" "$(cat $D/compressed_writeback)"
done
echo 0 > $D/compressed_writeback
echo
echo "=== batch=0 behaviour (upstream parity check) ==="
echo 0 > $D/writeback_batch_size
dd if="$DATA" of=/dev/block/zram$id bs=1048576 count=$MB 2>/dev/null
echo all > $D/idle
echo idle > $D/writeback; echo "  writeback with batch=0: rc=$? bd=[$(cat $D/bd_stat | tr -s ' ')]"
echo 32 > $D/writeback_batch_size

echo
echo "=== writeback-limit accounting (Batch 17 moved the charge before submit) ==="
for cfg in "1 0" "32 0" "256 0"; do
  set -- $cfg; b=$1; c=$2
  echo 1 > $D/reset
  rm -f "$IMG"; dd if=/dev/zero of="$IMG" bs=1048576 count=1 seek=511 2>/dev/null
  loop=$(losetup -f | tr -d ' \r\n'); losetup "$loop" "$IMG"
  echo "$b" > $D/writeback_batch_size
  echo "$c" > $D/compressed_writeback
  echo 100 > $D/writeback_limit
  echo 1   > $D/writeback_limit_enable
  echo "$loop" > $D/backing_dev
  echo 67108864 > $D/disksize
  dd if="$DATA" of=/dev/block/zram$id bs=1048576 count=$MB 2>/dev/null
  echo all > $D/idle
  u0=$(awk '{print $1}' /proc/uptime | tr -d '.')
  echo idle > $D/writeback; rc=$?
  u1=$(awk '{print $1}' /proc/uptime | tr -d '.')
  printf '  batch=%-4s limit=100 -> rc=%s bd=[%s] io=[%s] limit_now=%s wall=%sms\n' \
    "$b" "$rc" "$(cat $D/bd_stat | tr -s ' ')" "$(cat $D/io_stat | tr -s ' ')" "$(cat $D/writeback_limit)" "$(( (u1-u0)*10 ))"
  echo 0 > $D/writeback_limit_enable
  echo 1 > $D/reset
  losetup -d "$loop"
done

echo
echo "=== teardown ==="
echo 1 > $D/reset; echo "$id" > $CTL/hot_remove
rm -f "$IMG"
echo "zram left: $(ls -d /sys/block/zram* | tr '\n' ' ')"
