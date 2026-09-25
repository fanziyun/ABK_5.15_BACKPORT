#!/system/bin/sh
# service.sh - late stage of sailboat addon 2 (ABK cpufreq/scheduler tunables).
#
# There is exactly one supervisor here: the one that holds the governor.  It is
# spawned only when a governor is actually named in tunables.conf, so an empty
# sched.abk_governor costs no process at all -- one decision here rather than a
# supervisor that wakes up to do nothing.
#
# The smart-freq band itself is applied once, at post-fs-data.  It is state, not
# a policy that needs defending: the payload carries its own windows, and a
# second writer re-stating the same numbers every 30 s would only race the
# vendor's own scene switches.
set -u

MODDIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
. "$MODDIR/common.sh"

case "${1:-}" in
  --supervise-gov) abk_gov_supervisor_main; exit 0 ;;
esac

mkdir -p "$ABK_STATE_DIR" "$ABK_RUN_DIR" 2>/dev/null || true

# abk_spawn <re-entry argument> <pid name>
abk_spawn() {
  _sp_arg="$1"
  _sp_name="$2"

  # A module update or a second service.sh run can leave an older supervisor
  # behind; it would keep enforcing with the previous copy of the script.  Kill
  # the matching ones first -- this process's own cmdline has no such argument,
  # so it never matches itself.  SIGKILL on purpose: the supervisor spends its
  # life in `sleep`, and a shell holding a foreground child defers SIGTERM until
  # that child exits, so a polite signal leaves it running for half an hour
  # (measured on a sibling module).  It owns no state, so there is nothing to
  # flush.
  if command -v pkill >/dev/null 2>&1; then
    pkill -9 -f "service.sh $_sp_arg" 2>/dev/null || true
    sleep 1
  elif abk_pid_live "$_sp_name"; then
    abk_log "$_sp_name supervisor already running (pid $(abk_state_get "$_sp_name.pid"))"
    return 0
  fi

  setsid sh "$MODDIR/service.sh" "$_sp_arg" >/dev/null 2>&1 &
  _sp_n=0
  while [ "$_sp_n" -lt 10 ]; do
    abk_pid_live "$_sp_name" && break
    sleep 1
    _sp_n=$(( _sp_n + 1 ))
  done

  if abk_pid_live "$_sp_name"; then
    abk_log "$_sp_name supervisor started (pid $(abk_state_get "$_sp_name.pid"))"
  else
    abk_warn "$_sp_name supervisor did not start"
  fi
  return 0
}

abk_log "service: kernel $(uname -r 2>/dev/null)"
abk_cfg_lint

if [ -n "$(abk_gov_wanted)" ]; then
  abk_spawn --supervise-gov gov
else
  abk_log "sched.abk_governor is empty: leaving the ROM's own governor in charge"
fi

abk_log "service: done"
