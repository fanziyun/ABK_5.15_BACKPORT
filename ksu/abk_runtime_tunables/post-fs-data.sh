#!/system/bin/sh
# post-fs-data.sh - early stage of the ABK 5.15 runtime-tunables module.
#
# Only the knobs that want to be in place before the first real memory
# pressure are applied here: vm sysctls, MGLRU (configured), THP (opt-in), the
# schedutil smart-freq parameters (opt-in), the dynamic-readahead switch
# (opt-in) and the kernel-domain SELinux rule the zram writeback path needs.
# The rule has to be in place *before* something attaches a backing device,
# because from that moment on the denied side is a kernel thread; the zram work
# itself belongs to service.sh, because on this ROM the zram device is
# initialised late -- by the ROM's own mmd_setup at roughly 30 s.
set -u

MODDIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
. "$MODDIR/common.sh"

mkdir -p "$ABK_STATE_DIR" "$ABK_RUN_DIR" 2>/dev/null || true

abk_log "post-fs-data: kernel $(uname -r 2>/dev/null)"
abk_cfg_lint
abk_apply_early_knobs
abk_selinux_apply_rules
abk_log "post-fs-data: done"
