#!/system/bin/sh
set -u

MODDIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
. "$MODDIR/common.sh"

mkdir -p "$ABK_STATE_DIR" "$ABK_RUN_DIR" 2>/dev/null || true

abk_log "post-fs-data: kernel $(uname -r 2>/dev/null)"
abk_cfg_lint

_ae_gov="$(abk_gov_enforce)"
abk_log "gov: $_ae_gov"
abk_apply_sched_knobs
abk_report_dvfs_state

abk_log "post-fs-data: done"
