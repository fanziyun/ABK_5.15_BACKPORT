#!/system/bin/sh
# ABK probe: zram writeback + proactive reclaim (cfr) component check
MOD=/data/adb/modules/abk_runtime_tunables
ZD=/sys/block/zram0

echo "===== identity ====="
echo "kernel      : $(uname -r)"
echo "uptime      : $(uptime)"
echo "module ver  : $(grep -E '^version=' $MOD/module.prop 2>/dev/null)"

echo ""
echo "===== zram writeback ====="
echo "-- sysfs nodes present in $ZD --"
for f in writeback backing_dev compressed_writeback writeback_limit writeback_limit_enable writeback_stats; do
  if [ -e "$ZD/$f" ]; then
    echo "  $f : PRESENT ($(stat -c %A "$ZD/$f" 2>/dev/null))"
  else
    echo "  $f : MISSING"
  fi
done
echo "-- node values --"
[ -e "$ZD/backing_dev" ] && echo "  backing_dev         = [$(cat $ZD/backing_dev 2>/dev/null)]"
[ -e "$ZD/writeback_limit" ] && echo "  writeback_limit     = $(cat $ZD/writeback_limit 2>/dev/null)"
[ -e "$ZD/writeback_limit_enable" ] && echo "  writeback_limit_ena = $(cat $ZD/writeback_limit_enable 2>/dev/null)"
[ -e "$ZD/writeback_stats" ] && echo "  writeback_stats     = $(cat $ZD/writeback_stats 2>/dev/null | tr '\n' ' ')"
echo "-- kernel config --"
_CFG=""
for c in /proc/config.gz /vendor/config/$(uname -r | cut -d+ -f1).gz; do
  [ -r "$c" ] && { _CFG="$c"; break; }
done
if [ -n "$_CFG" ]; then
  zcat "$_CFG" 2>/dev/null | grep -E 'CONFIG_ZRAM(=|_)|WRITEBACK' | sed 's/^/  /'
else
  echo "  no readable config.gz (IKCONFIG off)"
fi
echo "-- module policy --"
grep -E '^zram\.writeback' $MOD/tunables.conf 2>/dev/null | sed 's/^/  tunables.conf: /'
echo "-- backing file + loop --"
ls -l /data/per_boot/zram/ 2>/dev/null | sed 's/^/  /' || true
[ -d /data/per_boot/zram ] || echo "  /data/per_boot/zram : dir absent (never created)"
losetup -a 2>/dev/null | sed 's/^/  losetup: /' || true
losetup -a 2>/dev/null | grep -q . || echo "  losetup: no loop devices attached"
echo "-- log lines (writeback) --"
grep -i writeback $MOD/state/abk_runtime_tunables.log 2>/dev/null | tail -n 8 | sed 's/^/  /' || true
grep -i writeback $MOD/state/abk_runtime_tunables.log 2>/dev/null | tail -n 1 | grep -q . || echo "  (no writeback lines in module log)"

echo ""
echo "===== proactive reclaim (cfr) ====="
echo "-- module policy --"
grep -E '^cfr\.' $MOD/tunables.conf 2>/dev/null | sed 's/^/  tunables.conf: /'
echo "-- sweep tool --"
ls -l $MOD/bin/cached_freeze_reclaim.sh 2>/dev/null | sed 's/^/  /' || echo "  bin/cached_freeze_reclaim.sh MISSING"
echo "-- supervisor state --"
_P=$(cat $MOD/state/cfr.pid 2>/dev/null | tr -d ' \n')
if [ -n "$_P" ]; then
  echo "  cfr.pid = $_P"
  if [ -d "/proc/$_P" ]; then
    echo "  ALIVE: $(tr '\0' ' ' < /proc/$_P/cmdline 2>/dev/null)"
  else
    echo "  pid $_P STALE (proc gone)"
  fi
else
  echo "  cfr.pid absent"
fi
echo "-- matching processes --"
ps -A -o PID,S,ARGS 2>/dev/null | grep -E 'supervise-(cfr|zram)|cached_freeze' | grep -v grep | sed 's/^/  /' || echo "  none"
echo "-- reclaim prerequisites: cgroup mounts --"
mount 2>/dev/null | grep -E 'cgroup|memcg' | sed 's/^/  /'
echo "  v2 memory.reclaim : $([ -e /sys/fs/cgroup/memory.reclaim ] && echo present || echo absent)"
echo "  v1 /dev/memcg      : $([ -d /dev/memcg ] && echo mounted || echo absent)"
echo "  v1 memory.reclaim  : $([ -e /dev/memcg/memory.reclaim ] && echo present || echo absent)"
echo "  v1 freezer.state   : $([ -e /dev/memcg/freezer.state ] && echo present || echo absent)"
echo "-- reclaimable groups sample --"
ls -d /sys/fs/cgroup/uid_* 2>/dev/null | head -n 3 | sed 's/^/  v2: /'
ls -d /dev/memcg/*app* /dev/memcg/*game* /dev/memcg/uid_* 2>/dev/null | head -n 6 | sed 's/^/  v1: /'
echo "-- log lines (proactive) --"
grep -iE 'proactive|cfr' $MOD/state/abk_runtime_tunables.log 2>/dev/null | tail -n 8 | sed 's/^/  /' || true
grep -iqE 'proactive|cfr' $MOD/state/abk_runtime_tunables.log 2>/dev/null || echo "  (no cfr/proactive lines in module log)"

echo ""
echo "===== swap context ====="
cat /proc/swaps | sed 's/^/  /'
echo "  mm_stat     = $(cat $ZD/mm_stat 2>/dev/null)"
echo "  io_stat wb  = $(cat $ZD/io_stat 2>/dev/null | awk '{print "rec",$1,"wr",$3,"failed",$5}')"
echo "probe done"
