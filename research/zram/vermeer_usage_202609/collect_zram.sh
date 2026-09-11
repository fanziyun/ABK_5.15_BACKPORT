#!/system/bin/sh
# zram anomaly diagnostics collector
echo "===== uname ====="
uname -a
echo
echo "===== /proc/swaps ====="
cat /proc/swaps
echo
for z in /sys/block/zram*; do
  echo "===== $z ====="
  for f in disksize comp_algorithm recomp_algorithm backing_dev writeback_limit writeback_stats idle_threshold max_comp_streams; do
    if [ -e "$z/$f" ]; then
      echo "--- $f ---"
      cat "$z/$f" 2>/dev/null
    fi
  done
  for f in mm_stat mem_used_total mem_used_max compr_data_size same_pages pages_compacted huge_pages; do
    if [ -e "$z/$f" ]; then
      echo "--- $f ---"
      cat "$z/$f" 2>/dev/null
    fi
  done
  echo
done
echo "===== /proc/meminfo (swap/zswap lines) ====="
grep -iE 'swap|zswap|zram' /proc/meminfo
echo
echo "===== /proc/zraminfo ====="
cat /proc/zraminfo 2>/dev/null
echo
echo "===== vm.swappiness / overcommit ====="
cat /proc/sys/vm/swappiness 2>/dev/null
cat /proc/sys/vm/overcommit_memory 2>/dev/null
echo
echo "===== zsmalloc pool class stats ====="
for z in /sys/kernel/debug/zsmalloc/*; do
  [ -e "$z" ] || continue
  echo "--- $z ---"
  cat "$z" 2>/dev/null
done
echo
echo "===== abk module params (if present) ====="
for p in /sys/module/zram/parameters/*; do
  [ -e "$p" ] || continue
  echo "$p = $(cat "$p" 2>/dev/null)"
done
echo
echo "===== KernelSU / abk runtime tunables ====="
ls -la /data/adb/ksu 2>/dev/null
cat /data/adb/ksu/abk_runtime_tunables/* 2>/dev/null
