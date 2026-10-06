#!/bin/sh
# abk_scx_check.sh - is a sched_ext scheduler attached, and is it moving anything?
#
# The SCX switch has three separate preconditions (the tunables switch, a kernel
# that carries the class, and the two build-host artefacts) and one thing that
# actually matters after them: whether any task is running on SCHED_EXT.  Those
# are four different states, and a single "is it on?" answer would hide three of
# them -- in particular the state a first device test deliberately starts from,
# *attached and moving nothing* (scx.mark_pids empty), which is indistinguishable
# from "not working" unless the tool says so.
#
# This script writes nothing.  It reads:
#
#   /sys/kernel/debug/sched/ext     the engine dump (Batch 60 debugfs file); its
#                                   "enabled" line is the attach verdict
#   /proc/config.gz                 whether the class is compiled in, for the
#                                   case where debugfs is not mounted
#   the companion tunables.conf     scx.enabled / scx.mark_pids, and whether
#                                   bin/scx_loader + bin/abk_scx_min.bpf.o exist
#   /proc/<pid>/stat field 41       the numeric policy: 7 is SCHED_EXT.  comm
#                                   can contain spaces and ")", so the parse
#                                   strips through the last ") " instead of
#                                   counting words from the start
#
# Exit codes, so a script can use it:
#   0  attached and at least one task on SCHED_EXT
#   2  scx.enabled is not 1 -- the switch is off, nothing was attempted
#   3  switch on but the engine is not attached (class or artefacts missing)
#   4  attached, but no task is on SCHED_EXT (expected with an empty mark list)

case "${1:-}" in
  -h|--help)
    echo "usage: abk_scx_check.sh"
    echo ""
    echo "Read-only verdict for the sched_ext switch: whether the engine is"
    echo "attached, which tasks run on SCHED_EXT, and which of the three"
    echo "preconditions is missing when it is not."
    echo "exit: 0 attached+used, 2 switch off, 3 switch on but not attached,"
    echo "      4 attached but no task on SCHED_EXT"
    exit 0
    ;;
esac

MODDIR=${MODDIR:-/data/adb/modules/abk_runtime_tunables}
CONF="$MODDIR/tunables.conf"
EXT=/sys/kernel/debug/sched/ext
RC=0

SAY() { echo "  $*"; }
flag() { [ "$RC" -eq 0 ] && RC="$1"; }

cfg() { # <key> <default>
  [ -f "$CONF" ] || { echo "$2"; return; }
  _v=$(awk -v k="$1" '
    /^[ \t]*#/ { next }
    {
      eq = index($0, "=")
      if (eq == 0) next
      key = substr($0, 1, eq - 1)
      gsub(/[ \t]/, "", key)
      if (key != k) next
      v = substr($0, eq + 1)
      gsub(/^[ \t]+|[ \t]+$/, "", v)
      print v
      exit
    }' "$CONF" 2>/dev/null)
  [ -n "$_v" ] && echo "$_v" || echo "$2"
}

echo "== ABK sched_ext check =="
SAY "switch (scx.enabled): $(cfg scx.enabled 0)"
SAY "mark list           : $(cfg scx.mark_pids -)"
SAY "loader present      : $([ -x "$MODDIR/bin/scx_loader" ] && echo yes || echo no)"
SAY "object present      : $([ -f "$MODDIR/bin/abk_scx_min.bpf.o" ] && echo yes || echo no)"

kernel_ready=no
if [ -r "$EXT" ]; then
  kernel_ready=yes
  SAY "class present       : yes (debugfs)"
elif [ -r /proc/config.gz ] && command -v zcat >/dev/null 2>&1 \
     && zcat /proc/config.gz 2>/dev/null | grep -q '^CONFIG_SCHED_CLASS_EXT=y$'; then
  kernel_ready=yes
  SAY "class present       : yes (CONFIG_SCHED_CLASS_EXT=y, no debugfs mount)"
else
  SAY "class present       : no (no debugfs file and /proc/config.gz says no)"
fi

enabled=no
if [ -r "$EXT" ]; then
  echo "  --- $EXT ---"
  while IFS= read -r _l; do
    echo "    $_l"
    case "$_l" in
      enabled*1*) enabled=yes ;;
    esac
  done < "$EXT"
fi
if [ "$enabled" = yes ]; then SAY "engine attached     : yes"; else SAY "engine attached     : no"; fi

# Tasks on SCHED_EXT: /proc/<pid>/stat, policy is field 41.
_count=0
_list=""
for _st in /proc/[0-9]*/stat; do
  [ -r "$_st" ] || continue
  _rest=$(sed 's/^.*) //' "$_st" 2>/dev/null)
  _pol=$(printf '%s' "$_rest" | cut -d" " -f39)
  [ "$_pol" = "7" ] || continue
  _pid=${_st#/proc/}
  _pid=${_pid%/stat}
  _comm=$(sed 's/^.*(\(.*\)).*/\1/' "$_st" 2>/dev/null)
  _count=$((_count + 1))
  [ "$_count" -le 10 ] && _list="$_list $_comm($_pid)"
done
SAY "SCHED_EXT tasks     : $_count$_list"
if command -v pidof >/dev/null 2>&1; then
  _lp=$(pidof scx_loader 2>/dev/null)
  [ -n "$_lp" ] && SAY "loader pid          : $_lp"
fi

echo ""
if [ "$(cfg scx.enabled 0)" != "1" ]; then
  echo "ABK SCX: switch off (scx.enabled=0) -- nothing attempted"
  flag 2
elif [ "$enabled" != yes ]; then
  echo "ABK SCX: switch on but the engine is not attached"
  [ "$kernel_ready" = yes ] || echo "         the kernel has no sched_ext class"
  echo "         (check the loader log and the bin/ artefacts; see the module README)"
  flag 3
elif [ "$_count" -eq 0 ]; then
  echo "ABK SCX: attached and moving nothing (scx.mark_pids is empty)"
  echo "         set scx.mark_pids to the pids this A/B should own, then re-run"
  flag 4
else
  echo "ABK SCX: attached, $_count task(s) on SCHED_EXT"
fi
[ "$RC" -eq 0 ] && echo "ABK SCX CHECK OK"
exit "$RC"
