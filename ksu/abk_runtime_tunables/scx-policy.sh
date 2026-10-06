#!/system/bin/sh
# scx-policy.sh - the sched_ext (SCX) half of the ABK 5.15 runtime-tunables
# module.
#
# OFF BY DEFAULT, and it stays a no-op unless three separate things are true:
#
#   1. scx.enabled=1 in tunables.conf;
#   2. the running kernel has the class -- /sys/kernel/debug/sched/ext exists
#      (Batch 60's debugfs file) or, when debugfs is not mounted,
#      /proc/config.gz says CONFIG_SCHED_CLASS_EXT=y;
#   3. both artefacts are present in bin/: scx_loader and abk_scx_min.bpf.o.
#      They are built on a build host by tools/build_scx_artifacts.sh against a
#      kernel that already carries the graft -- never on the device, which has
#      no compiler.
#
# Each missing condition logs one line naming itself, so "the scheduler is not
# running" always has a stated reason instead of being a silent no-op.
#
# Why a supervisor and not a one-shot: the attach is owned by the loader
# process.  When it exits the kernel drops the scheduler and every task falls
# back to the built-in classes -- correct but not what the policy asked for --
# so the module keeps one supervisor whose only job is to notice that and
# re-attach.  The supervisor kills a loader left behind by a previous module
# version first: two loaders cannot own the class at once.
#
# Scope note: only the pids in scx.mark_pids are moved.  The scheduler itself
# never calls scx_bpf_switch_all(), so the built-in classes (and this ROM's
# vendor WALT/FAS tenants) keep every task the list does not name.

ABK_SCX_LOADER_NAME="scx_loader"
ABK_SCX_OBJ_NAME="abk_scx_min.bpf.o"
ABK_SCX_STATE_PATH="/sys/kernel/debug/sched/ext"
ABK_SCX_DEBUGFS_DIR="/sys/kernel/debug/sched"

abk_scx_loader_path() {
  printf '%s/bin/%s\n' "$MODDIR" "$ABK_SCX_LOADER_NAME"
}

abk_scx_obj_path() {
  printf '%s/bin/%s\n' "$MODDIR" "$ABK_SCX_OBJ_NAME"
}

# Does the running kernel carry the class at all?
abk_scx_kernel_ready() {
  [ -e "$ABK_SCX_STATE_PATH" ] && return 0
  if [ -r /proc/config.gz ]; then
    if command -v zcat >/dev/null 2>&1; then
      zcat /proc/config.gz 2>/dev/null | grep -q '^CONFIG_SCHED_CLASS_EXT=y$' && return 0
    fi
  elif [ -r /boot/config-$(uname -r) ]; then
    grep -q '^CONFIG_SCHED_CLASS_EXT=y$' "/boot/config-$(uname -r)" 2>/dev/null && return 0
  fi
  return 1
}

abk_scx_artifacts_ready() {
  [ -x "$(abk_scx_loader_path)" ] && [ -f "$(abk_scx_obj_path)" ]
}

abk_scx_reason() { # one line saying why it cannot run, or empty
  if [ "$(abk_cfg scx.enabled 0)" != "1" ]; then
    printf 'scx.enabled=0\n'
    return 0
  fi
  if ! abk_scx_kernel_ready; then
    printf 'kernel has no sched_ext class (CONFIG_SCHED_CLASS_EXT off, or debugfs and /proc/config.gz both absent)\n'
    return 0
  fi
  if ! abk_scx_artifacts_ready; then
    printf 'bin/%s or bin/%s is missing (built on a host, see tools/build_scx_artifacts.sh)\n' \
      "$ABK_SCX_LOADER_NAME" "$ABK_SCX_OBJ_NAME"
    return 0
  fi
  printf '\n'
}

# The pids named by scx.mark_pids, as "--pid N --pid M"; bad entries are dropped
# with a warning rather than handed to the loader.
abk_scx_pid_args() {
  _sp_ids="$(abk_cfg scx.mark_pids '')"
  _sp_out=""
  for _sp_id in $_sp_ids; do
    if abk_is_uint "$_sp_id" && [ "$_sp_id" -gt 0 ]; then
      _sp_out="$_sp_out --pid $_sp_id"
    else
      abk_warn "scx.mark_pids: ignoring '$_sp_id' (not a pid)"
    fi
  done
  printf '%s\n' "$_sp_out"
}

# One attach attempt; returns the loader's exit status.  The loader runs in the
# foreground here so the supervisor can see it exit.
abk_scx_attach_once() {
  _sa_pidargs="$(abk_scx_pid_args)"
  # shellcheck disable=SC2086
  "$(abk_scx_loader_path)" run "$(abk_scx_obj_path)" $_sa_pidargs
}

abk_scx_supervisor_main() {
  _sv_interval="$(abk_clamp_uint "$(abk_cfg scx.reassert_interval_sec 60)" 10 3600)"

  # A loader from a previous module version owns the class and the new one
  # cannot attach until it is gone.  SIGTERM on purpose: the loader detaches
  # and hands its marked pids back to SCHED_NORMAL on the way out.
  if command -v pkill >/dev/null 2>&1; then
    pkill -f "bin/$ABK_SCX_LOADER_NAME run" 2>/dev/null || true
    sleep 1
  fi

  while :; do
    abk_scx_attach_once
    _sv_rc=$?
    abk_warn "scx: scheduler exited (rc=$_sv_rc); re-attaching in ${_sv_interval}s"
    sleep "$_sv_interval"
  done
}

abk_scx_apply() {
  _sa_reason="$(abk_scx_reason)"
  if [ -n "$_sa_reason" ]; then
    abk_log "scx: not started -- $_sa_reason"
    return 0
  fi
  abk_spawn --supervise-scx scx
}

abk_scx_status() {
  # action.sh owns the one-line presenter; the fallback keeps this callable from
  # a context that did not source it (same format, so output does not change).
  if ! command -v abk_show >/dev/null 2>&1; then
    abk_show() { printf '%-26s %s\n' "$1" "$2"; }
  fi

  abk_show "scx.enabled" "$(abk_cfg scx.enabled 0)"
  abk_show "scx.mark_pids" "$(abk_cfg scx.mark_pids '')"
  if abk_scx_kernel_ready; then
    abk_show "kernel class" "present"
  else
    abk_show "kernel class" "absent (CONFIG_SCHED_CLASS_EXT off?)"
  fi
  if [ -x "$(abk_scx_loader_path)" ]; then
    abk_show "bin/$ABK_SCX_LOADER_NAME" "present"
  else
    abk_show "bin/$ABK_SCX_LOADER_NAME" "absent (build host artefact)"
  fi
  if [ -f "$(abk_scx_obj_path)" ]; then
    abk_show "bin/$ABK_SCX_OBJ_NAME" "present"
  else
    abk_show "bin/$ABK_SCX_OBJ_NAME" "absent (build host artefact)"
  fi
  if abk_pid_live scx; then
    abk_show "scx supervisor" "running (pid $(abk_state_get scx.pid))"
  elif [ -f "$(abk_state_path scx.pid)" ]; then
    abk_show "scx supervisor" "dead (stale pid $(abk_state_get scx.pid))"
  else
    abk_show "scx supervisor" "not running"
  fi
  if [ -r "$ABK_SCX_STATE_PATH" ]; then
    while IFS= read -r _ss_line; do
      abk_show "sched/ext" "$_ss_line"
    done < "$ABK_SCX_STATE_PATH"
  else
    abk_show "sched/ext" "absent"
  fi
}
