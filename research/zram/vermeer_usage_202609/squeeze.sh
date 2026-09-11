#!/system/bin/sh
# Extreme memory squeeze experiment.
# Phase 1: kill every app-domain process (uid 10000..99999)
# Phase 2: mark ALL zram pages idle + async recompress to zstd
# Phase 3: zsmalloc compaction passes
# Phase 4: drop page cache
# Snapshots after every phase so the effect is separable.

SNAP() {
  echo "==== $1 ===="
  echo "up=$(cut -d'.' -f1 /proc/uptime)s"
  grep -E "MemFree|MemAvailable|SwapCached|^Cached|^Slab" /proc/meminfo
  grep zram0 /proc/swaps | awk '{print "swap_used_kb="$4}'
  awk '{printf "mm: orig=%d compr=%d used=%d same=%d compacted=%d huge=%d\n", $1, $2, $3, $6, $7, $8}' /sys/block/zram0/mm_stat
  echo ""
}

kill_apps() {
  for p in /proc/[0-9]*; do
    uid=$(stat -c %u "$p" 2>/dev/null)
    case "$uid" in ''|*[!0-9]*) continue ;; esac
    [ "$uid" -ge 10000 ] && [ "$uid" -le 99999 ] || continue
    pid=${p#/proc/}
    [ "$pid" = "$$" ] && continue
    name=$(cat "$p/comm" 2>/dev/null)
    case "$name" in
      *com.miui.home*|*ndroid.systemui*|*launcher*|*touchassistant*|*migt.service*) continue ;;
    esac
    kill -9 "$pid" 2>/dev/null
  done
}

SNAP "phase0 baseline"

# ---- Phase 1: kill apps (twice: HyperOS relaunches fast) ----
kill_apps; sleep 8; kill_apps; sleep 8; kill_apps
SNAP "phase1 after kill-all x3"

# ---- Phase 2: recompress everything (mark all idle, zstd, threshold 0) ----
TOOL=/data/adb/modules/abk_runtime_tunables/bin/zram_recompress_trigger.sh
if [ -f "$TOOL" ]; then
  sh "$TOOL" --mark-idle --mode async --threshold 0
  echo "trigger_rc=$?"
else
  echo all > /sys/block/zram0/idle
  echo "all 0" > /sys/block/zram0/recompress_async
  echo "manual_rc=$?"
fi
# let the async worker drain; poll until compr size stabilises
PREV=0
N=0
while [ $N -lt 24 ]; do
  sleep 10
  CUR=$(awk '{print $2}' /sys/block/zram0/mm_stat)
  if [ "$CUR" = "$PREV" ]; then
    D=$(awk '/zram_recompd/ {print $1}' /proc/[0-9]*/stat 2>/dev/null | head -1)
    : # stable sample; keep one more to be sure
  fi
  if [ "$CUR" = "$PREV" ] && [ $N -ge 4 ]; then
    break
  fi
  PREV=$CUR
  N=$((N+1))
done
SNAP "phase2 after full zstd recompression"

# ---- Phase 3: zsmalloc compaction x3 ----
for i in 1 2 3; do
  echo 100 > /sys/block/zram0/compact 2>/dev/null
  echo "compact_rc_$i=$?"
  sleep 3
done
SNAP "phase3 after compaction x3"

# ---- Phase 4: drop caches ----
sync; sync
echo 3 > /proc/sys/vm/drop_caches
sleep 3
SNAP "phase4 after drop_caches"

# ---- final idle-state check after rebound window ----
sleep 60
SNAP "final (T+60s, services relaunching)"
