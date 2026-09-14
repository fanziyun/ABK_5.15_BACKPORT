#!/system/bin/sh
T=/data/adb/modules/abk_runtime_tunables/bin/cached_freeze_reclaim.sh
echo "--- A: default uid_* discovery (--list, read-only) ---"
sh "$T" --list
echo "exit=$?"
echo "--- B: HyperOS named groups (--list, read-only) ---"
sh "$T" --cgroup-root /sys/fs/cgroup --cgroup-root /dev/memcg --group freeze-app --group game --list
echo "exit=$?"
echo "--- per-group node presence (v1 /dev/memcg) ---"
for g in apps freeze-app game protected-app; do
  r=N; f=N
  [ -f "/dev/memcg/$g/memory.reclaim" ] && r=Y
  [ -f "/dev/memcg/$g/freezer.state" ] && f=Y
  echo "  $g : memory.reclaim=$r freezer.state=$f usage=$(cat /dev/memcg/$g/memory.usage_in_bytes 2>/dev/null)"
done
echo "--- apps/ children with reclaim ---"
for d in /dev/memcg/apps/*/; do
  [ -f "$d/memory.reclaim" ] && echo "  $d $(cat $d/memory.usage_in_bytes 2>/dev/null)"
done | head -n 12
echo "--- cfr_reclaim counters (root memcg stat) ---"
grep cfr_reclaim /dev/memcg/memory.stat 2>/dev/null || echo "  (no cfr_reclaim lines)"
echo "done"
