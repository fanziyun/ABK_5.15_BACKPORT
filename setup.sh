#!/usr/bin/env bash
set -euo pipefail

MODULE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$MODULE_DIR/module.conf" ]; then
  # shellcheck disable=SC1091
  source "$MODULE_DIR/module.conf"
fi

# shellcheck disable=SC1091
source "$MODULE_DIR/scripts/libabk.sh"
# shellcheck disable=SC1091
source "$MODULE_DIR/scripts/stable_backport.sh"

abk_require_env KERNEL_ROOT DEFCONFIG CUSTOM_EXTERNAL_MODULE_STAGE

module_name="${ABK_MODULE_SET_NAME:-${ABK_MODULE_NAME:-ABK external module}}"
module_version="${ABK_MODULE_SET_VERSION:-${ABK_MODULE_VERSION:-unknown}}"

abk_log "module: $module_name"
abk_log "version: $module_version"
abk_log "stage: $CUSTOM_EXTERNAL_MODULE_STAGE"
abk_log "kernel root: $KERNEL_ROOT"
if [ -n "${ABK_MODULE_CHILD_ID:-}" ]; then
  abk_log "module-set child: ${ABK_MODULE_CHILD_ID}"
fi

# [PR 编译门禁端到端自检] 这行只存在于测试 PR 的 commit 上。它在 ABK 构建日志里出现，
# 就证明 ABK 真的 checkout 了 refs/pull/<N>/head（也就是本 PR 的 commit）并执行了本目录的
# setup.sh —— 光靠 gate 回写的 head_sha 只能证明派发 payload，证不了 checkout。
# 测试结束后随 PR 一起撤销，不要合进 main。
abk_log "ABK_CI_SELFTEST_MARKER=gate-e2e-v1"

case "$CUSTOM_EXTERNAL_MODULE_STAGE" in
  after_patch)
    abk_stable_backport_apply_selected
    abk_stable_backport_bundle_ksu_module
    ;;

  before_build)
    abk_log "before_build: no changes required"
    ;;

  *)
    abk_die "unsupported CUSTOM_EXTERNAL_MODULE_STAGE: $CUSTOM_EXTERNAL_MODULE_STAGE"
    ;;
esac

abk_log "done"
