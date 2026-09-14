#!/system/bin/sh
echo "--- mimd/uid_* children: graft file present? ---"
for d in /dev/memcg/mimd/uid_10085 /dev/memcg/mimd/uid_10000 /dev/memcg/mimd/uid_10350 /dev/memcg/mimd/critical0; do
  echo "  $d : reclaim=$([ -f $d/memory.reclaim ] && echo Y || echo N) usage=$(cat $d/memory.usage_in_bytes 2>/dev/null)"
done
echo "--- mimd root group ---"
echo "  /dev/memcg/mimd : reclaim=$([ -f /dev/memcg/mimd/memory.reclaim ] && echo Y || echo N) usage=$(cat /dev/memcg/mimd/memory.usage_in_bytes 2>/dev/null)"
echo "--- v2 uid_* under system/: reclaim file? ---"
for d in /sys/fs/cgroup/system/uid_10085 /sys/fs/cgroup/system/uid_9999 /sys/fs/cgroup/system/uid_1021; do
  [ -d "$d" ] && echo "  $d : reclaim=$([ -f $d/memory.reclaim ] && echo Y || echo N) cur=$(cat $d/memory.current 2>/dev/null)"
done
ls -d /sys/fs/cgroup/system/uid_10000 2>/dev/null && echo "  (uid_10000 exists)"
echo "--- top-3 mimd children by usage ---"
for d in /dev/memcg/mimd/*/; do
  u=$(cat "$d/memory.usage_in_bytes" 2>/dev/null)
  case "$u" in ''|*[!0-9]*) continue;; esac
  echo "$u $d"
done | sort -rn | head -n 3
echo "done"
