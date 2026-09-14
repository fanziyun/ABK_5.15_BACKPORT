set -u
D=/sys/block/zram1
echo "PAGE_WB_SIG unused here"
echo "limit=$(cat $D/writeback_limit) enable=$(cat $D/writeback_limit_enable) backing=$(cat $D/backing_dev) disksize=$(cat $D/disksize)"
echo "mm_stat: $(cat $D/mm_stat)"
echo "io_stat: $(cat $D/io_stat)  bd_stat: $(cat $D/bd_stat)"

echo
echo "--- attempt 1: as configured (limit_enable=1) ---"
echo all > $D/idle; echo "  idle rc=$?"
echo idle > $D/writeback; echo "  writeback rc=$?"
echo "  bd_stat: $(cat $D/bd_stat)"
echo "  io_stat: $(cat $D/io_stat)"

echo
echo "--- attempt 2: limit disabled ---"
echo 0 > $D/writeback_limit_enable; echo "  limit_enable=$(cat $D/writeback_limit_enable)"
echo all > $D/idle; echo "  idle rc=$?"
echo idle > $D/writeback; echo "  writeback rc=$?"
echo "  bd_stat: $(cat $D/bd_stat)"
echo "  io_stat: $(cat $D/io_stat)"
echo "  mm_stat: $(cat $D/mm_stat)"

echo
echo "--- dmesg tail ---"
dmesg | tail -6
