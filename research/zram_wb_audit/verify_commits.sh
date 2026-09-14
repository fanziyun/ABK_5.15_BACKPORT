#!/usr/bin/env bash
# Reproduce research/zram_wb_audit/verified_commits.tsv: check every upstream
# commit hash cited in research/zram_writeback_plan.md against the live GitHub
# API (subject, author, date) and record whether a local patch copy exists.
#
# Read-only with respect to the module: it writes only into
# research/zram_wb_audit/.  Run from the repo root:
#
#   bash research/zram_wb_audit/verify_commits.sh
#
# Network access is required (api.github.com).  The API is rate limited to 60
# unauthenticated requests per hour and this script makes 45, so avoid running
# it repeatedly in one hour; set GITHUB_TOKEN to raise the limit.
set -u
cd "$(dirname "$0")/../.." || exit 1
OUT=research/zram_wb_audit/verified_commits.tsv

HASHES=(
  894913e2d35c 391f057f44a5 74363ec674cb be48c412f6eb 013bf95a83ec
  424d0e5828ad b8d3ff7bb511 ef932cd23b78
  bb416d18b850 1d69a3f8ae77
  f405066a1f0d e828cccb72ed e87ddea34567 bf62f69574b1 3e8d8eb8d7f5 bf989ade270d
  4c1d61389e8e ba4c3698e696 d38fab605c66 3bf1c285dc40
  82f91900c722 7d2e1a6950 4610d35c14
  cf42d4cccf0d 30226b69f876 b46f9ea3cb35 77db7bb56bd7
  5e99893444a0 330edc2bc059 b967fa1ba72b
  a70aae12502b 79c744eeaa8e 889ae9169b45 a0b81ae7a4ff fd45af53e220 1e9460d132cc 0cd97a0372f2
  eed993a09103 f85219096648 d37da422edb0 5f0614a55e db8ffbd4e763 8e654f8fbff5
  1363d4662a0d 0d8359620d9b a939888ec38b
)

: > "$OUT"
printf 'sha\tsubject\tauthor\tdate\tlocal_patch\n' >> "$OUT"

for h in "${HASHES[@]}"; do
  local_patch="no"
  [ -f "research/upstream-zram/patches/$h.patch" ] && local_patch="yes"
  [ -f "research/upstream-zram/patches/p_$h.patch" ] && local_patch="yes"

  auth=()
  [ -n "${GITHUB_TOKEN:-}" ] && auth=(-H "Authorization: Bearer $GITHUB_TOKEN")
  body=$(curl -sS --max-time 25 "${auth[@]}" \
    "https://api.github.com/repos/torvalds/linux/commits/$h" 2>/dev/null)
  if [ -z "$body" ]; then
    printf '%s\tFETCH_FAILED\t-\t-\t%s\n' "$h" "$local_patch" >> "$OUT"
    continue
  fi
  parsed=$(printf '%s' "$body" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("PARSE_FAIL\t-\t-"); raise SystemExit
if "commit" not in d:
    print("NOT_FOUND\t-\t-"); raise SystemExit
c = d["commit"]
print("%s\t%s\t%s" % (c["message"].split("\n", 1)[0],
                      c["author"]["name"], c["author"]["date"]))
' 2>/dev/null)
  printf '%s\t%s\t%s\n' "$h" "$parsed" "$local_patch" >> "$OUT"
  sleep 0.7
done

echo "wrote $OUT ($(wc -l < "$OUT") lines)"
