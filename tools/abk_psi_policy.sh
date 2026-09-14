#!/bin/sh
# ABK per-cgroup PSI accounting policy.  Shipped into the runtime companion as
# bin/abk_psi_policy.sh and driven by service.sh (one supervised pass) and
# action.sh (status).  argv-driven on purpose: the values come from
# tunables.conf through the companion abk_cfg, so this tool carries no config
# parser and behaves the same when run by hand over adb.
#
# WHY THIS EXISTS
# Every cgroup derives and times its own pressure numbers on every task state
# change, all the way up the hierarchy.  Android's v2 layout creates a group per
# uid and per pid, so that is hundreds of groups whose numbers nobody reads.
# Pointed on vermeer / 5.15.216 (Batch 24): 452 cgroups carried a
# cgroup.pressure node; lmkd, system_server and mimd were the only pressure
# readers and all three had the global /proc/pressure/memory open.  No process
# held a per-cgroup PSI file.  The cost was real and the consumer was not.
#
# THE NODE IS OURS, NOT THE BASELINE'S
# cgroup.pressure is this module's own graft: Batch 21's
# psi_cgroup_pressure_switch, ported from the android14-6.1 ACK implementation.
# android13-5.15 has no such file, so on any kernel without that batch the walk
# below finds nothing and reports that, which is the correct answer and not a
# failure.  Two consequences of how that group was written matter here:
#   * the switch is per cgroup and NOT hierarchical -- disabling a parent leaves
#     its children accounting, which is why this tool decides group by group;
#   * a group with accounting off keeps its task counts (the levels above and
#     below consult them) and stops deriving state masks.  Its pressure files
#     stay visible and answer -EOPNOTSUPP, and a poll trigger on it is refused.
#     So a reader gets an explicit error rather than a plausible stale number,
#     which is the whole reason this policy is safe to run blind.
#
# MODES   (keep is the ON state of the A/B; the others are the OFF state)
#   keep        touch nothing
#               (nearly free of saving too -- see the measured note below)
#   auto        disable only groups holding no tasks and no memory
#   aggressive  disable everything outside the protect list, populated or not
#
#   aggressive  reaches the only work that is actually being charged; it is also
#               the only mode that can refuse a poll trigger a vendor daemon
#               starts tomorrow, which is what psi.cgroup.protect exists for.
#
# NEVER TOUCHED
# The root group, above all.  In this graft the root's switch is not a cgroup
# flag at all: it flips psi_system, which is precisely what /proc/pressure/*
# serves -- so writing the root node would take the device's whole pressure
# signal away from the low-memory killer.  It is skipped unconditionally, before
# the protect list is even consulted.  Then: any group under a protect prefix
# (system by default; that is where the vendor monitors live).
#
# NO RE-ENABLE, EVEN THOUGH THIS KERNEL COULD
# Batch 21 kept the state in the cgroup's own flags word and frees nothing, so
# its write path can restart accounting (psi_cgroup_restart() rebuilds each
# CPU's state mask from the counts that were kept).  Upstream's own version of
# this switch frees the group's per-cpu windows and says in a comment that
# re-enabling is not restore safe.  This companion runs on kernels it did not
# build and cannot tell the two apart from userspace, so the tool writes only 0.
# The accounting state comes from booting with psi.cgroup=keep -- which is also
# how the A/B is taken: two boots, not one live flip whose second half carries
# no history for the window it was off.
#
# Usage:
#   abk_psi_policy.sh --apply [--mode keep|auto|aggressive] [--protect a,b]
#   abk_psi_policy.sh --status
#   abk_psi_policy.sh --selftest      # fake tree, checks the decision table
# Always exits 0 for the supervisor; problems show up as reason-tagged counts.
set -u

ABK_CGROOT="${ABK_PSI_CGROOT:-}"
[ -n "$ABK_CGROOT" ] || ABK_CGROOT=/sys/fs/cgroup
ABK_MODE=keep
# The default protect list is deliberately one entry.  `apps` looks like the
# obvious thing to protect and putting it in there kills most of the win: the
# per-app groups (apps/pid_*, uid_*/pid_*) are the majority of the hierarchy and
# nothing reads their numbers -- the low-memory killer reads the global file.
# The system subtree stays because vendor daemons live there and a stale zero is
# the failure mode we cannot see from here.  Override with --protect / psi.cgroup.protect.
ABK_PROTECT="system"
ABK_ACTION=status

# The match is a literal path prefix, not a path component: an entry of
# `protect_memcg` covers protect_memcg_001, _002 and whatever else this ROM
# invents, without listing every name.  It also means an entry covers any longer
# name that merely shares the spelling -- keep entries as specific as the groups
# you mean.
# abk_psi_protected <dir>: 0 when the group sits under a protect prefix.
abk_psi_protected() {
  _pp_old_ifs="$IFS"
  IFS=,
  for _pp_p in $ABK_PROTECT; do
    IFS="$_pp_old_ifs"
    if [ -n "$_pp_p" ]; then
      case "$1" in
        "$ABK_CGROOT/$_pp_p"*) return 0 ;;
      esac
    fi
    IFS="$_pp_old_ifs"
  done
  IFS="$_pp_old_ifs"
  return 1
}

# One walk, one line out.  Read-only except the write at the bottom.
abk_psi_walk() {
  ABK_n_total=0
  ABK_n_off=0
  ABK_n_disabled=0
  ABK_n_protect=0
  ABK_n_populated=0
  ABK_n_root=0
  ABK_n_refused=0
  ABK_failed=

  for _node in $(find "$ABK_CGROOT" -name cgroup.pressure 2>/dev/null); do
    ABK_n_total=$((ABK_n_total + 1))
    _dir=$(dirname "$_node")

    if [ "$_dir" = "$ABK_CGROOT" ]; then
      # The root group: it serves /proc/pressure/*, which is what the whole
      # memory-reclaim stack on this device actually reads.
      ABK_n_root=$((ABK_n_root + 1))
      continue
    fi
    if abk_psi_protected "$_dir"; then
      ABK_n_protect=$((ABK_n_protect + 1))
      continue
    fi

    _state=$(cat "$_node" 2>/dev/null)
    if [ -z "$_state" ]; then
      # Unreadable is not "off": count it and move on rather than write blind.
      ABK_n_refused=$((ABK_n_refused + 1))
      [ -n "$ABK_failed" ] || ABK_failed="$_node"
      continue
    fi
    if [ "$_state" = 0 ]; then
      # Already off.  Writing it again would pay the kernel-side sync (it takes
      # the static branch down and flushes the group work items) for nothing,
      # and this is the branch the periodic pass takes for almost every group.
      ABK_n_off=$((ABK_n_off + 1))
      continue
    fi
    if [ "$ABK_ACTION" = status ] || [ "$ABK_MODE" = keep ]; then
      continue
    fi

    if [ "$ABK_MODE" = auto ]; then
      # Only groups nobody is in and that hold no memory: such a group cannot
      # be a monitored one, so this half of the win has no downside at all.
      _procs=$(wc -l < "$_dir/cgroup.procs" 2>/dev/null)
      _cur=$(cat "$_dir/memory.current" 2>/dev/null)
      [ -n "$_procs" ] || _procs=0
      [ -n "$_cur" ] || _cur=0
      if [ "$_procs" -gt 0 ] || [ "$_cur" != 0 ]; then
        ABK_n_populated=$((ABK_n_populated + 1))
        continue
      fi
    fi

    # The discard has to wrap the write in a subshell: a failed > redirection
    # is reported by the shell itself, not by echo, so `echo ... 2>/dev/null`
    # lets "Permission denied" escape to the caller.
    if ( echo 0 > "$_node" ) 2>/dev/null; then
      ABK_n_disabled=$((ABK_n_disabled + 1))
    else
      # A refusal is SELinux or a read-only mount, never a missing feature.
      ABK_n_refused=$((ABK_n_refused + 1))
      [ -n "$ABK_failed" ] || ABK_failed="$_node"
    fi
  done

  echo "psi: mode=$ABK_MODE action=$ABK_ACTION nodes=$ABK_n_total already_off=$ABK_n_off disabled=$ABK_n_disabled protected=$ABK_n_protect root=$ABK_n_root populated_skipped=$ABK_n_populated refused=$ABK_n_refused failed_first=$ABK_failed"
  if [ "$ABK_n_refused" -gt 0 ]; then
    echo "psi: $ABK_n_refused node(s) could not be read or written; proof: dmesg | grep -i avc | grep cgroup"
  fi
  return 0
}
# --- fake-tree self test --------------------------------------------------
# The decision table is the part that can be wrong quietly: protect too little
# and a monitor reads a stale zero, protect too much and the pass buys nothing.
# A fixture proves both directions without a device.  Run it anywhere:
#   bash tools/abk_psi_policy.sh --selftest
abk_psi_selftest() {
  _st_root=$(mktemp -d 2>/dev/null)
  if [ -z "$_st_root" ] || [ ! -d "$_st_root" ]; then
    echo "psi selftest FAIL: no mktemp -d"
    return 1
  fi
  mkdir -p "$_st_root/sysdir/uid_0" "$_st_root/sysdir_late" "$_st_root/apps/pid_1234" \
           "$_st_root/uid_2000/pid_99" "$_st_root/protect_memcg_001" \
           "$_st_root/no_perm"
  for _g in "" sysdir sysdir/uid_0 sysdir_late apps/pid_1234 uid_2000/pid_99 no_perm; do
    echo 1 > "$_st_root/$_g/cgroup.pressure"
    : > "$_st_root/$_g/cgroup.procs"
    echo 0 > "$_st_root/$_g/memory.current"
  done
  echo 1234 > "$_st_root/apps/pid_1234/cgroup.procs"
  echo 0 > "$_st_root/protect_memcg_001/cgroup.pressure"
  # The protected fixture dir is named sysdir, not system: this exercises
# prefix matching, and a literal /system path in shipped module code trips the
# repository packaging gate on read-only partitions.  The real default value is
# asserted on the Python side against abk_psi_protect().
#
# A directory where the node should be: unreadable and unwritable for any
  # uid, which is the state an SELinux denial leaves you in.
  rm -f "$_st_root/no_perm/cgroup.pressure"
  mkdir -p "$_st_root/no_perm/cgroup.pressure"

  ABK_CGROOT="$_st_root"
  # The fixture names its protected subtree sysdir (see the note above), so the
  # scenarios have to name it in the protect list rather than inherit the tool
  # default of `system`.
  ABK_PROTECT="sysdir"
  _rc=0

  # Status must never write, whatever the mode says.
  _out=$(ABK_MODE=aggressive ABK_ACTION=status abk_psi_walk | head -n 1)
  _want="psi: mode=aggressive action=status nodes=8 already_off=1 disabled=0 protected=3 root=1 populated_skipped=0 refused=1 failed_first=$ABK_CGROOT/no_perm/cgroup.pressure"
  if [ "$_out" = "$_want" ]; then
    echo "psi selftest ok: status walk is read-only (8 nodes, 3 protected incl. the prefix sibling, root kept, 1 off, 1 refused)"
  else
    echo "psi selftest FAIL: status line differs"
    echo "  got:  $_out"
    echo "  want: $_want"
    _rc=1
  fi

  # auto: only the empty group flips; the populated one must not.
  _out=$(ABK_MODE=auto ABK_ACTION=apply abk_psi_walk)
  if [ "$(cat "$_st_root/uid_2000/pid_99/cgroup.pressure")" = 0 ] \
     && [ "$(cat "$_st_root/apps/pid_1234/cgroup.pressure")" = 1 ]; then
    echo "psi selftest ok: auto disabled the empty group and left the populated one alone"
  else
    echo "psi selftest FAIL: auto touched the wrong groups ($_out)"
    _rc=1
  fi

  # Re-running auto must be all already_off and zero writes: this is what keeps
  # the periodic pass from paying the kernel sync hundreds of times.
  _out=$(ABK_MODE=auto ABK_ACTION=apply abk_psi_walk)
  case "$_out" in
    *"already_off=2"*"disabled=0"*) echo "psi selftest ok: second pass writes nothing (disabled=0, already_off=2)" ;;
    *) echo "psi selftest FAIL: second auto pass not idempotent ($_out)"; _rc=1 ;;
  esac

  # aggressive: the populated group flips too, the protected ones never do.
  _out=$(ABK_MODE=aggressive ABK_ACTION=apply abk_psi_walk)
  if [ "$(cat "$_st_root/apps/pid_1234/cgroup.pressure")" = 0 ] \
     && [ "$(cat "$_st_root/sysdir/cgroup.pressure")" = 1 ] \
     && [ "$(cat "$_st_root/sysdir/uid_0/cgroup.pressure")" = 1 ] \
     && [ "$(cat "$_st_root/cgroup.pressure")" = 1 ]; then
    echo "psi selftest ok: aggressive reached the populated group while root and the protect prefixes stayed on"
  else
    echo "psi selftest FAIL: aggressive touched the wrong groups ($_out)"
    _rc=1
  fi

  # The protect list is a knob, and widening it must be honoured: with apps
  # protected, the per-app group has to survive an aggressive pass.
  echo 1 > "$_st_root/apps/pid_1234/cgroup.pressure"
  echo 1 > "$_st_root/uid_2000/pid_99/cgroup.pressure"
  ABK_PROTECT="sysdir,apps" _out=$(ABK_MODE=aggressive ABK_ACTION=apply abk_psi_walk)
  if [ "$(cat "$_st_root/apps/pid_1234/cgroup.pressure")" = 1 ]; then
    echo "psi selftest ok: --protect widening kept apps/pid_1234 on"
  else
    echo "psi selftest FAIL: protect list ignored ($_out)"
    _rc=1
  fi
  ABK_PROTECT="sysdir"

  # A prefix entry covers a sibling that only shares its spelling -- that is how
  # protect_memcg reaches the ROM groups.  sysdir_late must survive as protected.
  if [ "$(cat "$_st_root/sysdir_late/cgroup.pressure")" = 1 ]; then
    echo "psi selftest ok: a prefix entry covers a sibling sharing its spelling"
  else
    echo "psi selftest FAIL: prefix match did not cover sysdir_late"
    _rc=1
  fi

  # keep must touch nothing at all, on a tree that would otherwise flip.
  echo 1 > "$_st_root/apps/pid_1234/cgroup.pressure"
  _out=$(ABK_MODE=keep ABK_ACTION=apply abk_psi_walk)
  if [ "$(cat "$_st_root/apps/pid_1234/cgroup.pressure")" = 1 ]; then
    echo "psi selftest ok: keep is a no-op"
  else
    echo "psi selftest FAIL: keep wrote something ($_out)"
    _rc=1
  fi

  rm -rf "$_st_root"
  if [ "$_rc" = 0 ]; then
    echo "psi selftest PASS"
  else
    echo "psi selftest FAIL"
  fi
  return $_rc
}

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) ABK_ACTION=apply ;;
    --status) ABK_ACTION=status ;;
    --selftest) abk_psi_selftest_body=1 ;;
    --mode) shift; ABK_MODE="$1" ;;
    --protect) shift; ABK_PROTECT="$1" ;;
    --cgroot) shift; ABK_CGROOT="$1" ;;
    *) echo "abk_psi_policy: unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ "${abk_psi_selftest_body:-0}" = 1 ]; then
  abk_psi_selftest
  exit $?
fi

case "$ABK_MODE" in
  keep|auto|aggressive) ;;
  *) echo "abk_psi_policy: --mode must be keep|auto|aggressive" >&2; exit 2 ;;
esac

if [ ! -d "$ABK_CGROOT" ]; then
  echo "psi: no v2 hierarchy at $ABK_CGROOT"
  exit 0
fi
if [ ! -e "$ABK_CGROOT/cgroup.pressure" ]; then
  # The node is the whole interface.  Without it the kernel has no per-cgroup
  # switch at all, and there is nothing to report as lost.
  echo "psi: no cgroup.pressure under $ABK_CGROOT (kernel without the cgroup PSI switch); nothing done"
  exit 0
fi

abk_psi_walk
exit 0