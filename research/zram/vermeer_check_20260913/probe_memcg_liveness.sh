#!/system/bin/sh
echo "--- /dev/memcg tree (2 levels, dirs only) ---"
find /dev/memcg -maxdepth 2 -type d 2>/dev/null | sed 's/^/  /'
echo "--- freeze-app children ---"
ls -d /dev/memcg/freeze-app/*/ 2>/dev/null | sed 's/^/  /' || echo "  (none)"
echo "--- graft liveness: minimal 4KiB reclaim on apps group ---"
echo "  before: $(grep cfr_reclaim /dev/memcg/apps/memory.stat 2>/dev/null | tr '\n' ' ')"
echo 4096 > /dev/memcg/apps/memory.reclaim 2>/dev/null
echo "  write-exit=$?"
echo "  after : $(grep cfr_reclaim /dev/memcg/apps/memory.stat 2>/dev/null | tr '\n' ' ')"
echo "--- v2 side: any uid_* or app groups? ---"
find /sys/fs/cgroup -maxdepth 2 -name 'uid_*' -o -maxdepth 2 -name 'apps' 2>/dev/null | head -n 6
echo "done"
