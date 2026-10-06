#!/system/bin/sh
# uclamp-policy.sh - the utilisation-clamp floor half of the runtime-tunables
# module.
#
# What it is for: the placer ranks a task partly by how much utilisation the
# scheduler assumes for it.  cpu.uclamp.min is the *floor* of that assumption,
# so raising it for a cgroup biases its tasks upward in placement and can cut
# wake-up latency on the render path -- with no kernel change at all, because
# this is a cgroup v2 file the baseline already exposes
# (CONFIG_UCLAMP_TASK_GROUP=y; verified on the built android13-5.15 tree, where
# CONFIG_UCLAMP_TASK=y too).
#
# OFF BY DEFAULT, and it takes both knobs to do anything: value and group list.
# A missing half logs which half and writes nothing.
#
# The value format is the kernel's, and it is not a guess:
# kernel/sched/core.c's capacity_from_percent() accepts either the literal
# "max" or a decimal percent parsed with UCLAMP_PERCENT_SHIFT -- so "50" is 50%,
# "12.5" is 12.5%, and anything above 100 is -ERANGE.  A malformed value is
# refused *here*, before the write: a rejected write into uclamp changes nothing
# silently, and "armed but never executed" is the failure this repository has
# already paid for once (the Batch 42 cap_pct).
#
# Why a supervisor: the value is re-asserted on a timer for the same reason the
# MGLRU switch is -- cgroup managers and the ROM's own init write these files,
# and a group created after boot only sees the value on the next tick.

abk_uclamp_value() {
  abk_cfg boost.uclamp_min ''
}

abk_uclamp_groups() {
  abk_cfg boost.groups ''
}

# "max", or a decimal percent in [0, 100].  awk does the numeric check so a
# malformed value cannot reach the kernel file at all.
abk_uclamp_valid() {
  [ "$1" = "max" ] && return 0
  printf '%s\n' "$1" | awk '
    $0 ~ /^[0-9]+(\.[0-9]+)?$/ && ($0 + 0) <= 100 { exit 0 }
    { exit 1 }' 2>/dev/null
}

abk_uclamp_path() { # <group>
  printf '%s/fs/cgroup/%s/cpu.uclamp.min\n' "$ABK_SYS_ROOT" "$1"
}

abk_uclamp_apply() {
  _ua_val="$(abk_uclamp_value)"
  _ua_grps="$(abk_uclamp_groups)"

  if [ -z "$_ua_val" ] || [ -z "$_ua_grps" ]; then
    abk_log "boost: off (set both boost.uclamp_min and boost.groups to arm it)"
    return 0
  fi
  if ! abk_uclamp_valid "$_ua_val"; then
    abk_warn "boost: refusing '$_ua_val' (want 'max' or a percent in [0,100])"
    return 0
  fi

  for _ua_grp in $_ua_grps; do
    _ua_grp="${_ua_grp#/}"
    _ua_path="$(abk_uclamp_path "$_ua_grp")"
    if [ ! -e "$_ua_path" ]; then
      abk_warn "boost: $_ua_path absent (no cpu controller in $_ua_grp?)"
      continue
    fi
    if [ ! -w "$_ua_path" ]; then
      abk_warn "boost: $_ua_path is not writable"
      continue
    fi
    if printf '%s\n' "$_ua_val" > "$_ua_path" 2>/dev/null; then
      abk_log "boost: $_ua_grp cpu.uclamp.min <= $_ua_val (now $(abk_read "$_ua_path" | tr -d '\n'))"
    else
      abk_warn "boost: write to $_ua_path refused"
    fi
  done
}

abk_uclamp_supervisor_main() {
  _us_iv="$(abk_clamp_uint "$(abk_cfg boost.interval_sec 60)" 10 3600)"
  while :; do
    abk_uclamp_apply
    sleep "$_us_iv"
  done
}

abk_uclamp_status() {
  if ! command -v abk_show >/dev/null 2>&1; then
    abk_show() { printf '%-26s %s\n' "$1" "$2"; }
  fi
  abk_show "boost.uclamp_min" "$(abk_uclamp_value)"
  abk_show "boost.groups" "$(abk_uclamp_groups)"
  abk_show "boost.interval_sec" "$(abk_cfg boost.interval_sec 60)"
  for _uc_grp in $(abk_uclamp_groups); do
    _uc_grp="${_uc_grp#/}"
    _uc_path="$(abk_uclamp_path "$_uc_grp")"
    if [ -e "$_uc_path" ]; then
      abk_show "cpu.uclamp.min $_uc_grp" "$(abk_read "$_uc_path" | tr -d '\n')"
    else
      abk_show "cpu.uclamp.min $_uc_grp" "absent"
    fi
  done
  if abk_pid_live uclamp; then
    abk_show "boost supervisor" "running (pid $(abk_state_get uclamp.pid))"
  else
    abk_show "boost supervisor" "not running"
  fi
}
