#!/bin/sh
# ABK per-cgroup PSI A/B harness.  Shipped as bin/abk_psi_bench.sh.
#
# What it decides
# Whether turning per-cgroup pressure accounting off (abk_psi_policy.sh) is a
# win on this device.  The kernel-side saving is charged per task state change
# and multiplied by hierarchy depth, so the measurement has to be a *fixed
# amount of state changes* with the CPU they cost read back out of /proc, not a
# feel test.
#
# Why two boots and not one flip
# Re-enabling a group is not restore safe upstream (its per-cpu windows are
# freed), so the two states are two boots: one with psi.cgroup=keep, one with
# psi.cgroup=auto or aggressive.  Each boot runs this tool; the lines are
# comparable because the state is measured from the tree, not assumed from the
# config key.  The protocol and the decision rule are in docs/psi_field_protocol.md.
#
# Usage:
#   abk_psi_bench.sh [--storm fork|wake|both] [--rounds 3] [--forks 20000]
#                    [--wakes 50000] [--cgroot /sys/fs/cgroup]
# Reads only /proc and the cgroup tree (the probe reads state, writes nothing).
set -u

ABK_CGROOT="${ABK_PSI_CGROOT:-}"
[ -n "$ABK_CGROOT" ] || ABK_CGROOT=/sys/fs/cgroup
ABK_STORM=both
ABK_ROUNDS=3
ABK_FORKS=20000
ABK_WAKES=50000
# Overridable so the harness can be exercised off-device (a host has no
# /system/bin), while the shipped default stays the Android one.
# A bare command name, found through PATH: the device has /system/bin on it and
# a host has /bin.  Spelling the partition out here would put a read-only
# partition path into shipped module code, which the repository packaging gate
# forbids -- and this file ships into bin/.  Override for host testing with
# ABK_BENCH_BIN.
ABK_BIN="${ABK_BENCH_BIN:-true}"
while [ $# -gt 0 ]; do
  case "$1" in
    --storm) shift; ABK_STORM="$1" ;;
    --rounds) shift; ABK_ROUNDS="$1" ;;
    --forks) shift; ABK_FORKS="$1" ;;
    --wakes) shift; ABK_WAKES="$1" ;;
    --cgroot) shift; ABK_CGROOT="$1" ;;
    *) echo "abk_psi_bench: unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

case "$ABK_STORM" in fork|wake|both) ;; *) echo "abk_psi_bench: --storm must be fork|wake|both" >&2; exit 2 ;; esac
if ! command -v "$ABK_BIN" >/dev/null 2>&1; then
  echo "abk_psi_bench: no '$ABK_BIN' on PATH; cannot generate a fork storm"
  exit 0
fi

# Machine-wide USER_HZ *busy* jiffies from the aggregate cpu line: user, nice,
# system, irq, softirq, steal.  Idle and iowait are left out on purpose -- on a
# 8-core phone they would add ~800 ticks per second of nothing happening, which
# buries the difference this measurement is about.
#
# Two traps that this function has already eaten once, both silent:
#   * the program is /^cpu  / with slashes.  Written as ^cpu the shell passes a
#     caret to awk, which is a syntax error, and an error printed to /dev/null
#     looks exactly like a reading of zero.
#   * no stderr discard here at all: a failed read must be visible, and the
#     caller checks for an empty value rather than arithmetic-ing it to 0.
abk_jiffies() {
  awk '/^cpu  / { print $2 + $3 + $4 + $7 + $8 + $9 }' /proc/stat
  return 0
}

# Same line, everything including idle: the noise check.  If busy per wall
# second is plausible but this moved far more than the storm accounts for,
# something else on the device was working and the round is not comparable.
abk_jiffies_total() {
  awk '/^cpu  / { t = 0; for (i = 2; i <= NF; i++) t += $i; print t }' /proc/stat
  return 0
}

abk_now() {
  date +%s 2>/dev/null
  return 0
}

# Which state is this boot actually in?  Counted from the tree, because a
# refused write or a stale config would otherwise mislabel a whole run.
abk_state_probe() {
  _p_on=0
  _p_off=0
  _p_node=0
  if [ -d "$ABK_CGROOT" ]; then
    for _f in $(find "$ABK_CGROOT" -name cgroup.pressure 2>/dev/null); do
      _p_node=$((_p_node + 1))
      _p_v=$(cat "$_f" 2>/dev/null)
      case "$_p_v" in
        0) _p_off=$((_p_off + 1)) ;;
        1) _p_on=$((_p_on + 1)) ;;
      esac
    done
  fi
  echo "nodes=$_p_node on=$_p_on off=$_p_off"
  return 0
}

# The work.  One fork of a program that does nothing: the cost left over is
# kernel bookkeeping, and task new/delete is exactly what the cgroup PSI path
# is charged for.  The wake storm is the other half: a task that sleeps and
# wakes is a state change with no work attached to it.
abk_storm_fork() {
  _i=0
  while [ "$_i" -lt "$ABK_FORKS" ]; do
    "$ABK_BIN" &
    _i=$((_i + 1))
    if [ $((_i % 64)) -eq 0 ]; then
      wait
    fi
  done
  wait
  return 0
}

abk_storm_wake() {
  _i=0
  while [ "$_i" -lt "$ABK_WAKES" ]; do
    sleep 0
    _i=$((_i + 1))
  done
  return 0
}

echo "psi_bench: storm=$ABK_STORM rounds=$ABK_ROUNDS forks=$ABK_FORKS wakes=$ABK_WAKES cgroot=$ABK_CGROOT"
echo "psi_bench: state_probe: $(abk_state_probe)"
echo "psi_bench: idle: $(abk_state_probe)" > /dev/null

_r=1
while [ "$_r" -le "$ABK_ROUNDS" ]; do
  # Let the machine settle first: a leftover sweep or app launch moves the
  # jiffies baseline more than this switch does.
  sleep 5
  _j0=$(abk_jiffies)
  _s0=$(abk_jiffies_total)
  _t0=$(abk_now)
  if [ -z "$_j0" ] || [ -z "$_s0" ]; then
    echo "psi_bench: /proc/stat unreadable; refusing to print a zero as a measurement"
    exit 1
  fi
  case "$ABK_STORM" in
    fork) abk_storm_fork ;;
    wake) abk_storm_wake ;;
    both) abk_storm_fork; abk_storm_wake ;;
  esac
  _j1=$(abk_jiffies)
  _s1=$(abk_jiffies_total)
  _t1=$(abk_now)
  _jd=$((_j1 - _j0))
  _wd=$((_t1 - _t0))
  [ "$_wd" -lt 1 ] && _wd=1
  # jiffies per wall second is the machine-wide busy ratio during the storm;
  # the number to compare between boots at identical work.
  _busy=$((_jd / _wd))
  _sd=$((_s1 - _s0))
  echo "psi_bench: round=$_r wall_sec=$_wd busy_jiffies=$_jd total_jiffies=$_sd busy_per_sec=$_busy state=$(abk_state_probe)"
  _r=$((_r + 1))
done

echo "psi_bench: how to read this"
echo "psi_bench:   compare busy_jiffies across the two boots at the same --forks/--wakes; lower wins."
echo "psi_bench:   linearity is what makes that valid: 1000/4000/16000 forks measured 145/590/2330"
echo "psi_bench:   busy jiffies on a WSL host, so the storm is the limiting factor and not noise."
echo "psi_bench:   busy_per_sec is only a wall-time sanity read; total_jiffies shows other machines"
echo "psi_bench:   work (apps, sweeps) that moved during the round and broke the comparison."
echo "psi_bench:   the off state is not free either: the pass cost is the abk_psi_policy.sh line in"
echo "psi_bench:   the module log, and a supervisor that re-walks the tree every interval pays it."
exit 0