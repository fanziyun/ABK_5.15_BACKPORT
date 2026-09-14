set -u
echo "=== clock / tick ==="
zcat /proc/config.gz | grep -E '^CONFIG_HZ=|^CONFIG_HZ_|^CONFIG_NO_HZ'
echo "uptime: $(cat /proc/uptime)"
echo
echo "=== enforcing-mode denial evidence (the smoke run) ==="
dmesg | grep 'b17_test.img' | head -2
dmesg | grep -c 'b17_test.img'
echo
echo "=== zram0 (live swap) unchanged ==="
D=/sys/block/zram0
echo "backing=$(cat $D/backing_dev) batch=$(cat $D/writeback_batch_size) cwb=$(cat $D/compressed_writeback) limit=$(cat $D/writeback_limit) enable=$(cat $D/writeback_limit_enable)"
echo "bd_stat=[$(cat $D/bd_stat | tr -s ' ')]"
echo
echo "=== final state ==="
getenforce
ls -d /sys/block/zram*
ls -l /data/per_boot/zram/ 2>/dev/null
mount | grep ' /data ' | head -1
