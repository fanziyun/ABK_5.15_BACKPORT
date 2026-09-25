#!/system/bin/sh
# action.sh - manual entry point, also what the KernelSU action button runs.
#
#   action.sh [status]    what this module owns and what the device reports
#                         (default)
#   action.sh governor    hold the configured cpufreq governor once, now, and
#                         print the same one-line verdict the supervisor logs
#
# The split matters here more than anywhere else: addon 1's status used to carry
# these rows, and reading a knob this module no longer owns would have reported
# a node nobody writes.  Everything this file shows is scoped to the scheduling
# half.
set -u

MODDIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
. "$MODDIR/common.sh"

ABK_STDOUT=1

abk_show() {
  printf '%-26s %s\n' "$1" "$2"
}

abk_show_or_absent() { # <label> <path>
  if [ -e "$2" ]; then
    abk_show "$1" "$(abk_read "$2" | tr -d '\n')"
  else
    abk_show "$1" "absent"
  fi
}

abk_supervisor_state() { # <pid name> <label>
  if abk_pid_live "$1"; then
    abk_show "$2" "running (pid $(abk_state_get "$1.pid"))"
  elif [ -f "$(abk_state_path "$1.pid")" ]; then
    abk_show "$2" "dead (stale pid $(abk_state_get "$1.pid"))"
  else
    abk_show "$2" "not running"
  fi
}

abk_status_report() {
  echo "-- governor --"
  # The governor is reported from the nodes rather than from tunables.conf,
  # because the setting is an intention and the node is the truth: the vendor
  # rewrites walt on a trigger nobody has pinned down, which is exactly the
  # discrepancy this row is here to expose.  The supervisor row says whether
  # anything is still trying to close that gap.
  _sr_gov="$(abk_gov_wanted)"
  if [ -n "$_sr_gov" ]; then
    abk_show "governor wanted" "$_sr_gov"
  else
    abk_show "governor wanted" "(empty: the ROM keeps its own choice)"
  fi
  abk_show "governor in force" "$(abk_gov_state_line)"
  abk_show "re-assert interval" "$(abk_gov_interval)s"
  abk_supervisor_state gov "governor supervisor"

  echo "-- smart-freq payload --"
  # Both read-only nodes answer "who owns this range" without reading
  # frequencies and guessing: abk_sf_boosting is a CPU mask of the floor's
  # reason, abk_sc_capped is a policy->cpu mask of whose target the cap is
  # suppressing.  A node that never changes while the gate says it cannot run is
  # how the device told us the cap was inert.
  abk_show_or_absent "fas registration" "$ABK_FAS_NODE"
  for node in abk_sf_enable abk_sf_floor_pct abk_sf_sustained_ms abk_sf_exit_ms \
              abk_sc_enable abk_sc_cap_pct abk_sc_hold_ms abk_sc_release_pct \
              abk_sc_release_ms; do
    abk_show_or_absent "$node" \
      "$ABK_SYS_ROOT/module/cpufreq_schedutil/parameters/$node"
  done
  abk_show_or_absent "abk_sf_boosting" \
    "$ABK_SYS_ROOT/module/cpufreq_schedutil/parameters/abk_sf_boosting"
  abk_show_or_absent "abk_sc_boosting" \
    "$ABK_SYS_ROOT/module/cpufreq_schedutil/parameters/abk_sc_boosting"
  abk_show_or_absent "abk_sc_capped" \
    "$ABK_SYS_ROOT/module/cpufreq_schedutil/parameters/abk_sc_capped"

  echo "-- what the policies report --"
  _sr_dvfs=""
  for _sr_p in "$ABK_SYS_ROOT"/devices/system/cpu/cpufreq/policy*; do
    [ -d "$_sr_p" ] || continue
    _sr_dvfs="$_sr_dvfs ${_sr_p##*/}=$(abk_read_flat "$_sr_p/scaling_governor")/$(abk_read_flat "$_sr_p/scaling_cur_freq")"
  done
  abk_show "dvfs owners" "${_sr_dvfs# }"
  # The gate the cap has to pass, per policy, printed rather than left to be
  # recomputed by hand: cap >= scaling_max_freq is what makes the cap inert, and
  # that is a one-line read on device.
  for _sr_p in "$ABK_SYS_ROOT"/devices/system/cpu/cpufreq/policy*; do
    [ -d "$_sr_p" ] || continue
    _sr_hw="$(abk_read_flat "$_sr_p/cpuinfo_max_freq")"
    _sr_pct="$(abk_read_flat "$ABK_SYS_ROOT/module/cpufreq_schedutil/parameters/abk_sc_cap_pct")"
    _sr_mx="$(abk_read_flat "$_sr_p/scaling_max_freq")"
    _sr_cap="$(abk_mul_div "$_sr_hw" "${_sr_pct:-100}" 100)"
    _sr_verdict="closed (cap >= max)"
    if [ -n "$_sr_cap" ] && [ "$_sr_cap" -lt "$_sr_mx" ] 2>/dev/null; then
      _sr_verdict="open"
    fi
    abk_show "${_sr_p##*/} cap gate" \
      "cap=${_sr_cap} max=${_sr_mx} ${_sr_verdict}"
  done
  abk_show "fas health" \
    "$MODDIR/bin/abk_fas_check.sh --sample 20 (exit 1 = a policy looks pinned, 4 = the super core is capped out of placement)"

  echo
  echo "-- how to use this --"
  echo "  action.sh governor   apply the governor now and print the verdict"
  echo "  bin/abk_fas_check.sh --probe   apply load, then check the frequency"
  echo "                                 comes back down (park vs lock)"
}

case "${1:-status}" in
  status)
    abk_status_report
    ;;
  governor)
    # Hold the governor once, now, instead of waiting for the supervisor's next
    # tick.  It prints the same one-line verdict the supervisor logs, so a
    # manual run and a boot run read identically and can be pasted next to each
    # other in a bug report.
    abk_show "governor" "$(abk_gov_enforce)"
    ;;
  *)
    echo "usage: action.sh [status|governor]" >&2
    exit 2
    ;;
esac
