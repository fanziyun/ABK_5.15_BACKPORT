set -u
F=/data/local/tmp/b17
WBD=/data/per_boot/zram
IMG="$WBD/b17_test.img"
D=/sys/block/zram1

echo "=== clean slate: remove old zram1 ==="
echo 1 > $D/reset 2>/dev/null
losetup -d /dev/block/loop50 2>/dev/null
echo 1 > /sys/class/zram-control/hot_remove 2>/dev/null
sleep 1
echo "zram devices: $(ls -d /sys/block/zram* | tr '\n' ' ')"

echo
echo "=== data: 32MiB of /system/lib64 ELF (compressible, distinct pages) ==="
cat /system/lib64/*.so 2>/dev/null | head -c 33554432 > "$F/data"
ls -l "$F/data"; md5sum "$F/data"

echo
echo "=== fresh zram1 with a zram_data_file backing file ==="
ID=$(cat /sys/class/zram-control/hot_add)
D=/sys/block/zram$ID
rm -f "$IMG"
dd if=/dev/zero of="$IMG" bs=1048576 count=1 seek=511 2>/dev/null
ls -lZ "$IMG"
LOOP=$(losetup -f | tr -d ' \r\n')
losetup "$LOOP" "$IMG" && echo "loop=$LOOP  dev=zram$ID"
echo 32 > $D/writeback_batch_size
echo 0  > $D/compressed_writeback
echo 0  > $D/writeback_limit_enable
echo "$LOOP" > $D/backing_dev
echo 67108864 > $D/disksize
echo "backing=$(cat $D/backing_dev) disksize=$(cat $D/disksize)"

echo
echo "=== fill + read back (before writeback) ==="
dd if="$F/data" of=/dev/block/zram$ID bs=1048576 count=32 2>&1 | tail -1
echo "mm_stat: $(cat $D/mm_stat)"
dd if=/dev/block/zram$ID of="$F/out1" bs=1048576 count=32 2>/dev/null
md5sum "$F/out1"

echo
echo "=== idle + writeback (batch=32, cwb=0) ==="
echo all > $D/idle
echo idle > $D/writeback; echo "  rc=$?"
echo "  bd_stat: $(cat $D/bd_stat)"
echo "  io_stat: $(cat $D/io_stat)"
echo "  loop sectors written: $(awk '{print $7}' /sys/block/$(basename $LOOP)/stat)"

echo
echo "=== read back AFTER writeback (via backing device) ==="
dd if=/dev/block/zram$ID of="$F/out2" bs=1048576 count=32 2>/dev/null
md5sum "$F/out2"
echo
echo "=== avc denials since? ==="
dmesg | grep -c 'avc: *denied' || true
dmesg | grep 'avc: *denied' | tail -3
