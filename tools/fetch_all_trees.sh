#!/usr/bin/env bash
# Fan-in for the one supported reference tree, into build/abk-trees (survives WSL
# restarts; /tmp does not).  Rate-limit friendly: one pass at a time, sleep
# between passes, gap-filling re-runs.
#
# Batch 44 dropped the 5.15.167/.178/.194 release baselines, so this fetches the
# rolling android13-5.15-lts branch only.  The outdir stays `build/abk-trees/216`
# -- tests/fetch_sublevel_tree.sh's header explains that a rolled branch has to
# be re-keyed in tests/sublevel_matrix.py.
set -u
cd "$(dirname "$0")/.."
for round in 1 2 3 4 5 6; do
  ok=1
  if ! bash tests/fetch_sublevel_tree.sh "android13-5.15-lts" "build/abk-trees/216" >/dev/null 2>&1; then
    ok=0
  fi
  echo "round $round complete ok=$ok $(date +%T)"
  [ "$ok" -eq 1 ] && break
  sleep 75
done
echo FINAL-STATE
for d in 216; do
  n=$(find "build/abk-trees/$d" -type f 2>/dev/null | wc -l)
  echo "$d files=$n"
  sub="$(awk '$1 == "SUBLEVEL" && $2 == "=" { print $3; exit }' "build/abk-trees/$d/Makefile" 2>/dev/null || true)"
  matrix="$(python3 - <<'PY'
import sys
sys.path.insert(0, "tests")
import sublevel_matrix
print(",".join(sublevel_matrix.SUPPORTED))
PY
)"
  if [ -n "$sub" ] && [ "$sub" != "$matrix" ]; then
    echo "  !! lts rolled to 5.15.$sub but sublevel_matrix.SUPPORTED is $matrix"
    echo "  !! re-key the row and re-prove every set; the audits refuse to run otherwise"
  else
    echo "  ok: Makefile SUBLEVEL $sub matches the matrix row"
  fi
done
