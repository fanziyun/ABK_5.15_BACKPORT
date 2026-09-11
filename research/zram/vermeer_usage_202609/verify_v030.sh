#!/system/bin/sh
# Live-verify the v0.3.0 companion in place: copy the pushed files, restart the
# zram supervisor and exercise abk_zram_compact_if_fragmented against the real
# nodes.
MD=/data/adb/modules/abk_runtime_tunables
LOG="$MD/state/abk_runtime_tunables.log"
SRC=/data/local/tmp/module_new

cp "$SRC/zram-policy.sh" "$SRC/common.sh" "$SRC/tunables.conf" "$SRC/module.prop" "$MD/"
chmod 0755 "$MD/zram-policy.sh" "$MD/common.sh"
echo "== deployed version line =="
grep version "$MD/module.prop"

pkill -9 -f 'service.sh --supervise-zram' 2>/dev/null
sleep 2
setsid sh "$MD/service.sh" --supervise-zram >/dev/null 2>&1 &
sleep 4

echo "== supervisor process =="
ps -A -o PID,NAME,ARGS | grep 'supervise-zram' | grep -v grep
echo "== supervisor up line (must carry the compact gate config) =="
tail -n 2 "$LOG"

echo
echo "== gate test 1: healthy device (real gates 50MB/15%) must be a no-op =="
B=$(grep -c 'zsmalloc compaction:' "$LOG" 2>/dev/null)
MODDIR="$MD" MD="$MD" sh -c '. "$MD/common.sh"; . "$MD/zram-policy.sh"; abk_zram_compact_if_fragmented; echo healthy_rc=$?'
A=$(grep -c 'zsmalloc compaction:' "$LOG" 2>/dev/null)
echo "healthy: log_lines_before=$B after=$A (equal = correctly silent)"

echo
echo "== gate test 2: force thresholds to 1MB/1% -> must write the real compact node =="
printf 'zram.compact.min_waste_mb=1\nzram.compact.waste_pct=1\n' > /data/local/tmp/ct.conf
M1=$(awk '{print $3}' /sys/block/zram0/mm_stat)
MODDIR="$MD" MD="$MD" ABK_CONF=/data/local/tmp/ct.conf sh -c '. "$MD/common.sh"; . "$MD/zram-policy.sh"; abk_zram_compact_if_fragmented; echo forced_rc=$?'
M2=$(awk '{print $3}' /sys/block/zram0/mm_stat)
C=$(grep -c 'zsmalloc compaction:' "$LOG" 2>/dev/null)
echo "forced: log_lines_before=$A after=$C mm_used $M1 -> $M2"
rm -f /data/local/tmp/ct.conf

echo
echo "== final state =="
grep zram0 /proc/swaps | awk '{print "swap_used_kb="$4}'
cat /sys/block/zram0/mm_stat
