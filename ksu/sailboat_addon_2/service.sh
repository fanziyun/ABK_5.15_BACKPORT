#!/system/bin/sh
set -u

MODDIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
. "$MODDIR/common.sh"

case "${1:-}" in
  --supervise-gov) abk_set_comm "$(abk_supervisor_name gov comm)"; abk_gov_supervisor_main; exit 0 ;;
esac

mkdir -p "$ABK_STATE_DIR" "$ABK_RUN_DIR" 2>/dev/null || true

abk_spawn() {
  _sp_arg="$1"
  _sp_name="$2"

  if command -v pkill >/dev/null 2>&1; then
    pkill -9 -f "service.sh $_sp_arg" 2>/dev/null || true
    sleep 1
  elif abk_pid_live "$_sp_name"; then
    abk_log "$_sp_name supervisor already running (pid $(abk_state_get "$_sp_name.pid"))"
    return 0
  fi

  # ps NAME is argv[0]; pkill matches the cmdline, which still carries
  # "service.sh <arg>", so only the display name changes here.
  _sp_link=""
  _sp_long="$(abk_supervisor_name "$_sp_name")"
  if [ -n "$_sp_long" ]; then
    if [ -x "$ABK_SHELL" ]; then
      mkdir -p "$ABK_RUN_DIR" 2>/dev/null || true
      _sp_link="$ABK_RUN_DIR/$_sp_long"
      if [ "$(readlink "$_sp_link" 2>/dev/null)" != "$ABK_SHELL" ]; then
        rm -f "$_sp_link" 2>/dev/null || true
        ln -s "$ABK_SHELL" "$_sp_link" 2>/dev/null || true
      fi
      [ -x "$_sp_link" ] && [ ! -d "$_sp_link" ] || _sp_link=""
    fi
    [ -n "$_sp_link" ] \
      || abk_warn "$_sp_name supervisor: no long-name link in $ABK_RUN_DIR; falling back to sh"
  fi

  if [ -n "$_sp_link" ]; then
    setsid "$_sp_link" "$MODDIR/service.sh" "$_sp_arg" >/dev/null 2>&1 &
  else
    setsid sh "$MODDIR/service.sh" "$_sp_arg" >/dev/null 2>&1 &
  fi
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
