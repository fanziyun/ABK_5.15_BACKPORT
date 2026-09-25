#!/usr/bin/env bash
# Sequential gap-fill for all four reference trees into build/abk-trees (survives
# WSL restarts; /tmp does not).  Rate-limit friendly: one pass at a time, sleep
# between passes, gap-filling re-runs.
set -u
cd "$(dirname "$0")/.."
for round in 1 2 3 4 5 6; do
  ok=1
  for pair in "deprecated/android13-5.15-2024-11 167" "deprecated/android13-5.15-2025-03 178" "android13-5.15-2025-12 194" "android13-5.15-lts 216"; do
    set -- $pair
    if ! bash tests/fetch_sublevel_tree.sh "$1" "build/abk-trees/$2" >/dev/null 2>&1; then
      ok=0
    fi
  done
  echo "round $round complete ok=$ok $(date +%T)"
  [ "$ok" -eq 1 ] && break
  sleep 75
done
echo FINAL-STATE
for d in 167 178 194 216; do
  n=$(find "build/abk-trees/$d" -type f 2>/dev/null | wc -l)
  echo "$d files=$n"
done
