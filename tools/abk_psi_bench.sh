#!/bin/sh
# ABK per-cgroup PSI A/B harness.  Shipped as bin/abk_psi_bench.sh.
#
# What it decides
# Whether turning per-cgroup pressure accounting off (abk_psi_policy.sh) is a
# win on this device.  The kernel-side saving is charged per task state change
# and multiplied by the number of ancestors walked, so the measurement has to
# be a fixed amount of state changes with the CPU they cost read back out.
#
# Why the bill comes from our own cgroup and not from /proc/stat
# The first version of this tool read machine-wide busy jiffies.  On the target
# phone the storm accounted for a third of the busy time and other people's
# work accounted for the rest (measured: busy_per_sec 357 out of 800 possible
# during a round), so the saving this switch can produce is a rounding error on
# that number.  A leaf cgroup's cpu.stat does attribute correctly here --
# measured on device, 3000 forks billed 29.1 s of usage_usec and the next 3000
# billed 30.8 s -- so the tool now bills its own work and reads its own bill.
# Background apps, lmkd and the recompression sweeps cannot get into it.
#
# Why the arms are two sibling groups in ONE boot
# The work has to run *inside* the group whose switch differs, because that is
# where the saving is charged.  So the tool builds one group, leaves its
# cgroup.pressure alone (=1), builds a sibling and writes 0 to it, and runs the
# identical storm in each, alternating rounds so any drift in machine state
# hits both arms.  Nothing that existed before the tool ran is switched, and
# both groups are removed on the way out, so the one-way nature of a disable
# costs nothing here: that constraint only bites on groups you intend to keep.
#
# What can invalidate a run
# The arms are not protected from the companion's own periodic policy pass: with
# psi.cgroup=aggressive the supervisor disables every unprotected group it finds
# every psi.cgroup.interval_sec (five minutes by default), and a run that crosses
# a tick would compare two disabled arms and call the difference a saving.  So
# every round re-reads both arm nodes and aborts if either moved -- and the
# operator has to stop the supervisor for the duration, or measure on a keep boot.
#
# What it refuses to do
# Print a comparison built on an instrument that is not reading.  Before the
# first real round the tool runs a warm-up storm and requires system_usec to
# move; if it does not, the group is not being billed (the cpu controller is
# not effective at that depth) and the tool stops instead of reporting two
# zeros as a tie.  Same rule for a missing cgroup.pressure: no node means the
# kernel has no switch, and an A/B of two identical arms is not an A/B.
#
# Usage:
#   abk_psi_bench.sh [--mode ab|single] [--arm on|off] [--storm fork|wake|both]
#                    [--rounds 3] [--forks 20000] [--wakes 50000]
#                    [--depth 2] [--cgroot /sys/fs/cgroup]
#   mode=ab      the paired comparison described above (needs cgroup.pressure)
#   mode=single  bill one storm in one group and report it; works on any kernel,
#                which is what makes the instrument checkable on a stock build.
# Writes only inside its own abk_psi_bench_* groups under --cgroot.
set -u

ABK_CGROOT="${ABK_PSI_CGROOT:-}"
[ -n "$ABK_CGROOT" ] || ABK_CGROOT=/sys/fs/cgroup
ABK_MODE=ab
ABK_ARM=on
ABK_STORM=both
ABK_ROUNDS=3
ABK_FORKS=20000
ABK_WAKES=50000
# Depth 2 matches what live tasks actually use on the target phone (measured:
# 314 of 904 tasks at depth 2, the other 590 in the root group, none deeper).
ABK_DEPTH=2
# Overridable so the harness can be exercised off-device (a host has no
# /system/bin), while the shipped default stays the Android one.  A bare command
# name found through PATH: spelling a partition out here would put a read-only
# partition path into shipped module code, which the packaging gate forbids.
ABK_BIN="${ABK_BENCH_BIN:-true}"

abk_usage() {
  # Everything above the first line of code, markers stripped.  A fixed line
  # range here silently truncated the usage section when the header grew.
  awk '/^[^#]/ { exit } /^#/ { sub(/^# ?/, ""); print }' "$0"
  return 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --mode) shift; ABK_MODE="$1" ;;
    --arm) shift; ABK_ARM="$1" ;;
    --storm) shift; ABK_STORM="$1" ;;
    --rounds) shift; ABK_ROUNDS="$1" ;;
    --forks) shift; ABK_FORKS="$1" ;;
    --wakes) shift; ABK_WAKES="$1" ;;
    --depth) shift; ABK_DEPTH="$1" ;;
    --cgroot) shift; ABK_CGROOT="$1" ;;
    -h|--help) abk_usage; exit 0 ;;
    *) echo "abk_psi_bench: unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

case "$ABK_MODE" in ab|single) ;; *) echo "abk_psi_bench: --mode must be ab|single" >&2; exit 2 ;; esac
case "$ABK_ARM" in on|off) ;; *) echo "abk_psi_bench: --arm must be on|off" >&2; exit 2 ;; esac
case "$ABK_STORM" in fork|wake|both) ;; *) echo "abk_psi_bench: --storm must be fork|wake|both" >&2; exit 2 ;; esac
if ! command -v "$ABK_BIN" >/dev/null 2>&1; then
  # Exit non-zero: this tool's whole job is producing a number, and a run that
  # generated no work must not look like a run that measured a saving of zero.
  echo "abk_psi_bench: no '$ABK_BIN' on PATH; cannot generate a storm" >&2
  exit 1
fi

abk_now() { date +%s 2>/dev/null; return 0; }

# One field of a group's cpu.stat, in USER_HZ-independent usec.  Empty output
# means the file is unreadable, which the caller must not turn into a zero.
abk_stat_field() {
  awk -v k="$2" '$1 == k { print $2 }' "$1" 2>/dev/null
  return 0
}

# The work.  A fork of a program that does nothing: what is left over is kernel
# bookkeeping, and task new/delete is what the cgroup PSI path is charged for.
abk_storm_fork() {
  _i=0
  while [ "$_i" -lt "$ABK_FORKS" ]; do
    "$ABK_BIN" &
    _i=$((_i + 1))
    if [ $((_i % 64)) -eq 0 ]; then wait; fi
  done
  wait
  return 0
}

# A synchronous ping-pong: the parent parks on a read, the child parks on a
# read, and each iteration is exactly two blocking transitions with no work
# attached -- the event the switch skips.  Both fifos stay open on held fds and
# every command in the loop is a shell builtin (echo, read, test), so no process
# is created per iteration and a difference between arms is attributable to
# wakeups rather than to exec.
#
# That last clause is the whole storm, and it has now been wrong twice:
#   * 'sleep 0' in a loop -- toybox sleep forks, so that was a fork storm
#     wearing a wake storm label.
#   * "printf 'x\n' >&3" -- on the target ROM (/system/bin/sh, Android mksh)
#     that is NOT a builtin: 'type printf' answers 'printf is a tracked alias
#     for /system/bin/printf', and measured on device 500 calls cost 6.3 s of
#     wall time, which is the same 12.6 ms a fork+exec of /system/bin/true
#     costs.  The storm was measuring two process creations per round trip,
#     billed about 30 ms of CPU each, and a default run (--wakes 50000,
#     storm=both, rounds=3) needed hours per A/B while measuring the wrong
#     event.  echo is a builtin on the same ROM (500 calls: 0.01 s) and is what
#     the loop uses now.  The pin that was meant to catch this checked for
#     'sleep 0' and for mkfifo; every local gate stayed green while the storm
#     churned processes, and only the device could see it.
#   * one fifo and no newline in the payload: the reader never saw a line, so it
#     woke once at close instead of ABK_WAKES times, and the round was billed
#     25 s of system_usec for work this loop did not do.
# Those are why --storm can be split: a storm you cannot bill is a storm you
# cannot claim a saving from.
#
# Two earlier shapes of this were wrong in opposite directions:
#   * "sleep 0" in a loop -- toybox sleep forks, so that was a fork storm
#     wearing a wake storm label.
#   * one fifo and no newline in the payload: the reader never saw a line, so it
#     woke once at close instead of ABK_WAKES times, and the round was billed
#     25 s of system_usec for work this loop did not do.
# That second one is why --storm can be split: a storm you cannot bill is a
# storm you cannot claim a saving from.
abk_storm_wake() {
  _d=$(mktemp -d 2>/dev/null) || {
    echo "abk_psi_bench: no mktemp, wake storm unavailable" >&2
    return 1
  }
  _wa="$_d/abk_wake_down"; _wb="$_d/abk_wake_up"
  if ! mkfifo "$_wa" 2>/dev/null || ! mkfifo "$_wb" 2>/dev/null; then
    echo "abk_psi_bench: cannot create fifos, wake storm unavailable" >&2
    rm -f "$_wa" "$_wb"; rmdir "$_d" 2>/dev/null
    return 1
  fi
  # Child order matters: it blocks opening _wa for read until the parent opens
  # the write end below, so starting it first cannot deadlock.
  # echo, not printf: see the note above -- printf forks an external binary on
  # the target ROM, which turns this storm into a fork storm in disguise.
  ( while read -r _x; do echo y; done < "$_wa" > "$_wb" ) &
  _w=$!
  exec 3>"$_wa"
  exec 4<"$_wb"
  _i=0
  while [ "$_i" -lt "$ABK_WAKES" ]; do
    echo x >&3
    read -r _y <&4
    _i=$((_i + 1))
  done
  # Closing the write end is what lets the child see EOF and exit.
  exec 3>&-
  exec 4<&-
  wait "$_w" 2>/dev/null
  rm -f "$_wa" "$_wb"; rmdir "$_d" 2>/dev/null
  return 0
}

abk_storm() {
  case "$ABK_STORM" in
    fork) abk_storm_fork ;;
    wake) abk_storm_wake ;;
    both) abk_storm_fork && abk_storm_wake ;;
  esac
  return 0
}

# Build <cgroot>/abk_psi_bench_<arm> plus ABK_DEPTH-1 levels below it, so the
# leaf the storm runs in sits at the same depth as a real app group.
abk_mkgroup() {
  _g="$ABK_CGROOT/abk_psi_bench_$1"
  _i=1
  while [ "$_i" -lt "$ABK_DEPTH" ]; do
    _g="$_g/l$_i"
    _i=$((_i + 1))
  done
  if ! mkdir -p "$_g" 2>/dev/null; then
    echo "abk_psi_bench: cannot create $_g" >&2
    return 1
  fi
  echo "$_g"
  return 0
}

# The pressure node of a group, wherever the hierarchy keeps it: the leaf, or
# the first ancestor that has one (our graft puts it on every group; a
# threaded-only subtree may not).
abk_pnode() {
  _d=$1
  while [ "$_d" != "$ABK_CGROOT" ] && [ -n "$_d" ] && [ "$_d" != "/" ]; do
    if [ -e "$_d/cgroup.pressure" ]; then echo "$_d/cgroup.pressure"; return 0; fi
    _d=$(dirname "$_d")
  done
  return 1
}

abk_rmgroupee() {
  _g=$1
  [ -n "$_g" ] || return 0
  # Move anything still in the leaf up to the root before removing the chain,
  # or rmdir fails and leaves a group behind holding a disabled switch.
  _p=$(cat "$_g/cgroup.procs" 2>/dev/null)
  for _pid in $_p; do
    ( echo "$_pid" > "$ABK_CGROOT/cgroup.procs" ) 2>/dev/null
  done
  _i=0
  _c="$_g"
  while [ "$_i" -lt "$ABK_DEPTH" ]; do
    rmdir "$_c" 2>/dev/null
    _c=$(dirname "$_c")
    [ "$_c" = "$ABK_CGROOT" ] && break
    _i=$((_i + 1))
  done
  return 0
}

echo "psi_bench: mode=$ABK_MODE storm=$ABK_STORM rounds=$ABK_ROUNDS forks=$ABK_FORKS wakes=$ABK_WAKES depth=$ABK_DEPTH cgroot=$ABK_CGROOT"

# ---------------- arm setup ----------------
ARM_ON_G=''; ARM_OFF_G=''
if [ "$ABK_MODE" = ab ]; then
  ARM_ON_G=$(abk_mkgroup on) || exit 1
  ARM_OFF_G=$(abk_mkgroup off) || { abk_rmgroupee "$ARM_ON_G"; exit 1; }
  _pon=$(abk_pnode "$ARM_ON_G") || { echo "psi_bench: no cgroup.pressure reachable from $ARM_ON_G; this kernel has no per-cgroup PSI switch, so there is no A/B to run (use --mode single to bill a storm anyway)" >&2; abk_rmgroupee "$ARM_ON_G"; abk_rmgroupee "$ARM_OFF_G"; exit 1; }
  _poff=$(abk_pnode "$ARM_OFF_G") || { echo "psi_bench: $ARM_OFF_G lost its pressure node" >&2; abk_rmgroupee "$ARM_ON_G"; abk_rmgroupee "$ARM_OFF_G"; exit 1; }
  _von=$(cat "$_pon" 2>/dev/null); _voff=$(cat "$_poff" 2>/dev/null)
  ( echo 0 > "$_poff" ) 2>/dev/null || { echo "psi_bench: refused writing 0 to $_poff (sepolicy or not root)" >&2; abk_rmgroupee "$ARM_ON_G"; abk_rmgroupee "$ARM_OFF_G"; exit 1; }
  _voff1=$(cat "$_poff" 2>/dev/null)
  case "$_von$_voff1" in
    10) ;;
    *) echo "psi_bench: arms did not land on on=1 off=0 (got $_von/$_voff1); refusing to compare" >&2; abk_rmgroupee "$ARM_ON_G"; abk_rmgroupee "$ARM_OFF_G"; exit 1 ;;
  esac
  echo "psi_bench: arms ready: on=$ARM_ON_G(off-by-default=$_von) off=$ARM_OFF_G(written=$_voff1)"
  G="$ARM_ON_G"
else
  G=$(abk_mkgroup "$ABK_ARM") || exit 1
  _pn=$(abk_pnode "$G") && echo "psi_bench: single arm pressure=$(cat "$_pn" 2>/dev/null)"
fi

# ---------------- instrument self-check ----------------
# Does this group get billed at all?  Nothing else in this tool means anything
# if the answer is no, so check it with a small storm and refuse if it is flat.
ABK_INSTRUMENT=flat
ABK_FORKS_SAVED=$ABK_FORKS; ABK_WAKES_SAVED=$ABK_WAKES
ABK_FORKS=800; ABK_WAKES=0
( echo $$ > "$G/cgroup.procs" ) 2>/dev/null || { echo "psi_bench: cannot move into $G" >&2; abk_rmgroupee "$ARM_ON_G"; abk_rmgroupee "$ARM_OFF_G"; [ "$ABK_MODE" = single ] && abk_rmgroupee "$G"; exit 1; }
_u0=$(abk_stat_field "$G/cpu.stat" system_usec)
if [ -z "$_u0" ]; then
  echo "psi_bench: $G/cpu.stat has no system_usec; cpu controller not effective here" >&2
else
  abk_storm_fork
  _u1=$(abk_stat_field "$G/cpu.stat" system_usec)
  if [ -z "$_u1" ]; then
    echo "psi_bench: system_usec unreadable after the warm-up storm" >&2
  elif [ "$((_u1 - _u0))" -le 0 ]; then
    echo "psi_bench: system_usec did not move ($_u0 -> $_u1): this group is not being billed at depth $ABK_DEPTH, so an A/B here would report two zeros as a tie" >&2
  else
    echo "psi_bench: instrument live: 800 forks billed $((_u1 - _u0)) usec of system_usec"
    ABK_INSTRUMENT=live
  fi
fi
ABK_FORKS=$ABK_FORKS_SAVED; ABK_WAKES=$ABK_WAKES_SAVED

# The verdict is a flag set above, not arithmetic re-derived here.  An earlier
# version of these four lines wrote "$(_u1:-0)" -- a command substitution, not a
# parameter expansion -- which every -n syntax gate accepted, then ran a
# nonexistent command, left _u1 at 0, and would have aborted a perfectly good
# A/B as a flat instrument.  Only the shell on the device can see that class of
# mistake, which is why the gate list says "and run it".
if [ "$ABK_INSTRUMENT" != live ]; then
  echo "psi_bench: instrument not live, no comparison made" >&2
  ( echo $$ > "$ABK_CGROOT/cgroup.procs" ) 2>/dev/null
  abk_rmgroupee "$ARM_ON_G"; abk_rmgroupee "$ARM_OFF_G"
  [ "$ABK_MODE" = single ] && abk_rmgroupee "$G"
  exit 1
fi

# ---------------- rounds ----------------
_on_tot=0; _off_tot=0; _n=0
_r=1
while [ "$_r" -le "$ABK_ROUNDS" ]; do
  if [ "$ABK_MODE" = ab ]; then
    # Alternate which arm goes first, so a slow machine drift does not always
    # penalise the same side.
    if [ $((_r % 2)) -eq 1 ]; then _order="$ARM_ON_G $ARM_OFF_G"; else _order="$ARM_OFF_G $ARM_ON_G"; fi
  else
    _order="$G"
  fi
  for _g in $_order; do
    sleep 3   # let the previous arm's teardown settle before billing the next

    # Integrity, before the round is billed: is the arm still armed?  The
    # companion's periodic policy pass disables every unprotected group it finds
    # (service.sh --supervise-psi, every psi.cgroup.interval_sec, five minutes by
    # default) and this tool's arms are not protected, so a run that crosses a
    # tick would silently compare two disabled arms and report the difference as
    # a saving.  Two reads per arm; the cost is nothing next to a storm.
    case "$_g" in
      *abk_psi_bench_on*) _want_v=1 ;;
      *abk_psi_bench_off*) _want_v=0 ;;
      *) _want_v= ;;
    esac
    if [ -n "$_want_v" ]; then
      _pn=$(abk_pnode "$_g") || _pn=""
      _pv=$(cat "$_pn" 2>/dev/null)
      if [ "$_pv" != "$_want_v" ]; then
        echo "psi_bench: arm state changed mid-run: $_g cgroup.pressure=$_pv want=$_want_v" >&2
        echo "psi_bench:   something outside this tool wrote that node.  Most likely the runtime" >&2
        echo "psi_bench:   companion's periodic psi.cgroup pass (service.sh --supervise-psi," >&2
        echo "psi_bench:   psi.cgroup.interval_sec).  Stop it for the run or take the A/B on a keep boot." >&2
        ( echo $$ > "$ABK_CGROOT/cgroup.procs" ) 2>/dev/null
        abk_rmgroupee "$ARM_ON_G"; abk_rmgroupee "$ARM_OFF_G"
        [ "$ABK_MODE" = single ] && abk_rmgroupee "$G"
        exit 1
      fi
    fi

    _s0=$(abk_stat_field "$_g/cpu.stat" system_usec)
    _u0=$(abk_stat_field "$_g/cpu.stat" user_usec)
    _t0=$(abk_now)
    if [ -z "$_s0" ] || [ -z "$_u0" ]; then
      echo "psi_bench: cpu.stat unreadable on $_g; aborting instead of billing zero" >&2
      abk_rmgroupee "$ARM_ON_G"; abk_rmgroupee "$ARM_OFF_G"; [ "$ABK_MODE" = single ] && abk_rmgroupee "$G"
      exit 1
    fi
    ( echo $$ > "$_g/cgroup.procs" ) 2>/dev/null
    abk_storm
    _s1=$(abk_stat_field "$_g/cpu.stat" system_usec)
    _u1=$(abk_stat_field "$_g/cpu.stat" user_usec)
    _t1=$(abk_now)
    _sd=$((_s1 - _s0)); _ud=$((_u1 - _u0)); _wd=$((_t1 - _t0))
    _tag=arm; case "$_g" in *_on/*|*_on) _tag=on ;; *_off/*|*_off) _tag=off ;; esac
    case "$_g" in *abk_psi_bench_on*) _tag=on ;; *abk_psi_bench_off*) _tag=off ;; *) _tag=single ;; esac
    echo "psi_bench: round=$_r arm=$_tag wall_sec=$_wd system_usec=$_sd user_usec=$_ud total_usec=$((_sd + _ud))"
    if [ "$_tag" = on ]; then _on_tot=$((_on_tot + _sd))
    elif [ "$_tag" = off ]; then _off_tot=$((_off_tot + _sd))
    fi
    _n=$((_n + 1))
  done
  _r=$((_r + 1))
done

# ---------------- teardown ----------------
( echo $$ > "$ABK_CGROOT/cgroup.procs" ) 2>/dev/null
abk_rmgroupee "$ARM_ON_G"; abk_rmgroupee "$ARM_OFF_G"
[ "$ABK_MODE" = single ] && abk_rmgroupee "$G"

echo "psi_bench: cleanup: groups left = $(find "$ABK_CGROOT" -maxdepth 1 -name 'abk_psi_bench_*' 2>/dev/null | wc -l)"
if [ "$ABK_MODE" = ab ] && [ "$_n" -gt 1 ]; then
  _diff=$((_on_tot - _off_tot))
  # Integer per-mille of the saving against the on arm.  Divide first: a full
  # run makes _diff * 10000 overflow the device shell's 32-bit arithmetic, and
  # that is not hypothetical -- the first version of this line printed 49
  # permille for a *negative* saving on the target device (2026-09-15,
  # 19914848 - 20245589).  Truncating the divisor keeps every intermediate
  # inside 32 bits; the reported figure stays integer per-mille.
  _pmt=0
  [ "$_on_tot" -gt 1000 ] && _pmt=$(( _diff / (_on_tot / 1000) ))
  echo "psi_bench: RESULT on_system_usec_total=$_on_tot off_system_usec_total=$_off_tot saving_usec=$_diff saving_permille=$_pmt"
  echo "psi_bench: how to read this: permille is the share of the storm's kernel time that per-cgroup"
  echo "psi_bench:   PSI accounting costs at depth $ABK_DEPTH.  Multiply by the share of real state"
  echo "psi_bench:   changes that happen outside the root group (measured 34.7% on this device: 314 of"
  echo "psi_bench:   904 tasks, all at depth 2) before you call it a device-level win."
  echo "psi_bench:   the off arm is not free: abk_psi_policy.sh pays a tree walk per pass, and a group"
  echo "psi_bench:   with no signal to report is a group the vendor daemons cannot see."
fi
exit 0
