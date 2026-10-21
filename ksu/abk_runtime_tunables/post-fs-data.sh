#!/system/bin/sh
set -u

MODDIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
. "$MODDIR/common.sh"

mkdir -p "$ABK_STATE_DIR" "$ABK_RUN_DIR" 2>/dev/null || true

abk_log "post-fs-data: kernel $(uname -r 2>/dev/null)"
abk_cfg_lint
abk_apply_early_knobs
abk_selinux_apply_rules
abk_log "post-fs-data: done"
