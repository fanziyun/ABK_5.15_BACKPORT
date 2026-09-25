#!/system/bin/sh
# post-fs-data.sh - early stage of sailboat addon 2 (ABK cpufreq/scheduler
# tunables).
#
# Only the knobs that want to be in place before anything measures the device
# were ever here, and this module owns three, in this order:
#
#   1. the governor, because both smart-freq payloads gate on the policy's
#      governor being schedutil -- with any other governor they stand down, so
#      the band written second would be armed against a governor that ignores
#      it;
#   2. the band (abk_apply_sched_knobs): the floor and the cap percentages and
#      their three timed windows;
#   3. the DVFS ownership report, last, so the boot log records the state this
#      pass actually reached rather than the state it intended.
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
