#!/bin/sh
# abk_launch_bench.sh - is a cold launch late because of the placer or because of
# the storage?   (Batch 27; the cold-launch work itself was Batch 10-6, in
# CHANGELOG.md, which is where the 2661 -> 2227 ms figure below was measured.)
#
# A launch that takes two seconds has two candidate owners and no node reports
# which one it is, so the answer has to come from measurement taken *during* the
# launch window rather than from a snapshot:
#
#   the placement / ceiling owner
#          WALT/EAS rank cores by cpu_capacity * scaling_max_freq /
#          cpuinfo_max_freq (the rq_cpu_capacity_orig pair of the
#          update_cpu_capacity tracepoint), so whoever holds scaling_max_freq
#          decides what each core is worth to the placer.  A profile that keeps
#          the super core at 58% hands the placer a super core barely bigger than
#          the mid cluster, and a cold launch -- light work, exactly the case
#          that gets placed by capacity -- then runs on the mid cores.
#          Measured on SM8550 with a userspace profile in play: releasing the
#          super core's ceiling and stopping its owner moved a cold launch of
#          com.netease.cloudmusic from 2661 ms to 2227 ms and the app's main
#          thread onto the super core from 5 polls in 36 to 11 in 37.
#
#   the storage
#          a launch whose pages are not in page cache pays for them before any
#          of the above matters.  This tool bills that as the pgpgin/pswpin delta
#          of the launch and as the /proc/pressure/io stall it produced -- but
#          pgpgin is a machine-wide counter, so it is evidence of traffic and
#          never an attribution: a launch that causes reads is not a launch that
#          waited for them.  The storage's share is the delta between the arm
#          that had the page cache and the arm that did not, which is why the
#          storage verdict is only printed when --compare supplies the other arm,
#          and why --save exists to hand it over.  Measured on the target device:
#          dropping the cache made every launch read 5 to 340 times more pages
#          for 11% to 29% more wall clock, so the two numbers really do have to
#          be told apart before either is called a cause.
#
# Both are recorded per launch, so one run answers "how slow" and "which of the
# two".  --mode drop repeats the run with the page cache dropped first (the I/O
# arm); --mode warm, force-stop only, is the arm a user actually feels.  The
# difference between the arms is the storage's share, so the two are meant to be
# run as a pair: --save on the first, --compare on the second.
#
# What it refuses to do
# Report a number it did not take.  A launch with no TotalTime, an app whose pid
# never appeared, or an unreadable /proc/vmstat is recorded as NA and kept out of
# every aggregate -- and a run in which *no* launch produced a TotalTime exits
# non-zero instead of printing 0 ms, because "measured nothing" and "measured
# instant" must not look alike.  It also refuses a placement verdict from a
# window it did not sample: the main-thread share needs at least --min-mt polls.
# And it refuses to measure a dozing device at all -- see the screen guard below,
# which is there because the first run of this tool did exactly that and produced
# a clean, reproducible, entirely wrong answer.
#
# Why the screen guard is not a nicety
#   Measured on the target ROM: with the display off, a launched app never
#   becomes the top app, so it stays in the foreground cpuset (cpus 0-6) instead
#   of moving to top-app (cpus 0-7).  The super core is then not merely
#   under-placed -- it is not in the app's cpuset at all.  A screen-off run
#   therefore reports "cpuset bound" for every launch: true of the screen-off
#   state, false of the phone in a hand.  The wakefulness is read once before the
#   first launch; --allow-screen-off is the explicit override.
#
# A device shell trap this tool is written around
#   On the target ROM /system/bin/sh (mksh) `|` is not usable as a pattern
#   delimiter inside ${var%%|*} / ${var#*|}.  Measured on device: with y holding
#   a literal pipe, ${y%%|*} returned empty and ${y#*|} stripped nothing.  List
#   splitting here therefore goes through IFS and `set --`; do not "simplify" a
#   split back into a parameter expansion.
#
# Usage:
#   abk_launch_bench.sh [--mode warm|drop] [--apps LIST] [--iters N]
#                       [--settle SECS] [--gap SECS] [--invert-pct N]
#                       [--min-mt N] [--allow-screen-off] [--selftest] [--quiet]
#   --mode warm   am force-stop, settle, then am start -W   (what a user feels)
#   --mode drop   the same, but drop_caches first           (the I/O arm)
#   --apps LIST   comma-separated pkg/component pairs
#   --settle N    seconds between force-stop and the launch (default 2)
#   --gap N       seconds between launches (default 3)
#   --min-mt N    main-thread samples a window needs before a ratio is reported
#                 (default 5; fewer reads as "not enough to say")
#   --allow-screen-off  measure anyway; the verdict then describes a dozing phone
#   --save FILE   write this run's per-launch record to FILE
#   --compare FILE  read a previous run's record (from --save) and report the
#                 per-app delta; this is what sizes the storage's share, and the
#                 storage verdict is not printed without it
#   --selftest    exercise the decision logic on a fake tree; needs no device
#   --quiet       only the table, the verdicts and the failure lines
#   --sys-root P, --proc-root P, --cpuset-root P, --am C, --pidof C, --dumpsys C
#                 roots and commands to read through, for the fixtures; the
#                 defaults are the device's own
# Exit codes: 0 no launch-window cap inversion was seen, 1 a policy is pinned or
# nothing could be measured, 4 the super core had no more capacity in the
# placer's eyes than a weaker cluster for at least --invert-pct of the launch
# window, 2 bad usage.
# ----8<---- end of help
set -u

ABK_MODE=warm
ABK_ITERS=8
ABK_SETTLE=2
ABK_GAP=3
ABK_INVERT_PCT=5
ABK_MIN_MT=5
ABK_QUIET=0
ABK_SELFTEST=0
ABK_SYS_ROOT=/sys
ABK_PROC_ROOT=/proc
ABK_CPUSET_ROOT="${ABK_LAUNCH_CPUSET_ROOT:-/dev/cpuset}"
ABK_AM="${ABK_LAUNCH_AM:-am}"
ABK_PIDOF="${ABK_LAUNCH_PIDOF:-pidof}"
ABK_DUMPSYS="${ABK_LAUNCH_DUMPSYS:-dumpsys}"
ABK_ALLOW_SCREEN_OFF=0
ABK_SAVE=""
ABK_COMPARE=""
# Default targets: the app the cold-launch work was first attributed on, plus
# four of different weight classes so one slow app cannot decide the verdict.
ABK_APPS_DEFAULT="com.netease.cloudmusic/com.netease.cloudmusic/.activity.IconChangeDefaultAlias,com.tencent.mm/com.tencent.mm/.ui.LauncherUI,com.taobao.idlefish/com.taobao.idlefish/com.taobao.fleamarket.home.activity.InitActivity,com.coolapk.market/com.coolapk.market/.view.main.MainActivity,com.android.settings/com.android.settings/.MainSettings"
ABK_APPS="$ABK_APPS_DEFAULT"

abk_usage() {
  sed -n '2,/end of help/p' "$0"
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --mode) ABK_MODE="${2:-}"; shift 2 ;;
    --apps) ABK_APPS="${2:-}"; shift 2 ;;
    --iters) ABK_ITERS="${2:-}"; shift 2 ;;
    --settle) ABK_SETTLE="${2:-}"; shift 2 ;;
    --gap) ABK_GAP="${2:-}"; shift 2 ;;
    --invert-pct) ABK_INVERT_PCT="${2:-}"; shift 2 ;;
    --min-mt) ABK_MIN_MT="${2:-}"; shift 2 ;;
    --sys-root) ABK_SYS_ROOT="${2%/}"; shift 2 ;;
    --proc-root) ABK_PROC_ROOT="${2%/}"; shift 2 ;;
    --cpuset-root) ABK_CPUSET_ROOT="${2%/}"; shift 2 ;;
    --am) ABK_AM="${2:-}"; shift 2 ;;
    --pidof) ABK_PIDOF="${2:-}"; shift 2 ;;
    --dumpsys) ABK_DUMPSYS="${2:-}"; shift 2 ;;
    --save) ABK_SAVE="${2:-}"; shift 2 ;;
    --compare) ABK_COMPARE="${2:-}"; shift 2 ;;
    --allow-screen-off) ABK_ALLOW_SCREEN_OFF=1; shift ;;
    --selftest) ABK_SELFTEST=1; shift ;;
    --quiet) ABK_QUIET=1; shift ;;
    -h|--help) abk_usage ;;
    *) echo "abk_launch_bench: unknown argument: $1" >&2; abk_usage ;;
  esac
done

case "$ABK_MODE" in warm|drop) ;; *) echo "abk_launch_bench: --mode must be warm|drop" >&2; exit 2 ;; esac
for _n in ABK_ITERS ABK_SETTLE ABK_GAP ABK_INVERT_PCT ABK_MIN_MT; do
  eval "_v=\$$_n"
  case "$_v" in ''|*[!0-9]*) echo "abk_launch_bench: $_n wants a number, got '$_v'" >&2; exit 2 ;; esac
done
[ "$ABK_ITERS" -ge 1 ] || { echo "abk_launch_bench: --iters must be >= 1" >&2; exit 2; }

# Parse --apps into ABK_LIST (space separated "pkg/component" entries).  A
# component may legitimately contain a slash (both "pkg/.Act" and "pkg/pkg.Act"
# are real), so the check is "the part before the first slash is a package name
# and something follows it" -- not "exactly one slash".  Entries that fail are
# refused here rather than turning into a launch of the empty string later.
ABK_LIST=""
_oldifs=$IFS
IFS=,
for _e in $ABK_APPS; do
  IFS=$_oldifs
  case "$_e" in
    */*) ;;
    *) echo "abk_launch_bench: --apps entry '$_e' is not pkg/component" >&2; exit 2 ;;
  esac
  _pkg="${_e%%/*}"; _cmp="${_e#*/}"
  case "$_pkg" in
    ''|*[!A-Za-z0-9._-]*) echo "abk_launch_bench: --apps entry '$_e' does not start with a package name" >&2; exit 2 ;;
  esac
  case "$_cmp" in
    ''|*[!A-Za-z0-9._/-]*) echo "abk_launch_bench: --apps entry '$_e' has a component a component cannot be" >&2; exit 2 ;;
  esac
  ABK_LIST="$ABK_LIST $_e"
  IFS=,
done
IFS=$_oldifs
[ -n "$ABK_LIST" ] || { echo "abk_launch_bench: --apps is empty" >&2; exit 2; }

# Scratch.  /data/local/tmp is where this runs on device (no /tmp there); off
# device the caller's TMPDIR wins and plain /tmp is the fallback, so a test host
# does not abort.
abk_tmp() {
  for _t in "${TMPDIR:-}" /data/local/tmp /tmp .; do
    [ -n "$_t" ] && [ -d "$_t" ] && { printf '%s' "$_t"; return 0; }
  done
  printf '.'
}
ABK_TMPD="$(abk_tmp)"
ABK_WORK="$ABK_TMPD/.abk_launch_bench.$$"
mkdir -p "$ABK_WORK" || { echo "abk_launch_bench: cannot create $ABK_WORK" >&2; exit 1; }
abk_cleanup() { rm -rf "$ABK_WORK"; }
trap 'abk_cleanup' EXIT INT TERM

CPUFREQ="$ABK_SYS_ROOT/devices/system/cpu/cpufreq"

abk_say() { [ "$ABK_QUIET" = "1" ] || echo "$*"; }
abk_now() { cut -d. -f1 "$ABK_PROC_ROOT/uptime" 2>/dev/null || echo 0; }
# Node reads go through the `read` builtin, not `head`/`cat`: the samplers poll
# at ~20 Hz and a fork per node per poll makes the instrument a measurable share
# of the workload it is measuring.  Measured in a fixture window: three polls
# with the fork-per-node form against fourteen when reading is a builtin, and a
# launch window is only a second or two long.
rd() {
  _rv=""
  read -r _rv _rjunk 2>/dev/null < "$1" || true
  printf '%s' "${_rv:-}"
}
# affected_cpus is a *list* ("0 1 2 3"): reading it into one variable keeps the
# inner spaces, where rd's word split would drop every cpu after the first.
rdlist() {
  _rl=""
  read -r _rl 2>/dev/null < "$1" || true
  printf '%s' "${_rl:-}"
}
# vmstat counter; empty (not 0) when the key is absent, so a caller can refuse.
vm() { awk -v k="$1" '$1 == k { print $2 }' "$ABK_PROC_ROOT/vmstat" 2>/dev/null; }
# PSI "some total" in usec.  The field is spelled total=<n>, not a bare number,
# so it is split rather than taken positionally: reading $5 straight returned the
# string "total=1234" and the launch line printed io_stall_us=NA.
psi_total() {
  awk '/^some/ { split($5, a, "="); print a[2] }' "$ABK_PROC_ROOT/pressure/$1" 2>/dev/null
}

# cap_view <arch> <ceiling> <cpuinfo_max> -- the capacity the placer actually
# ranks cores by.  awk, not shell: 855 * 2803200 is 2.4e9 and this ROM's
# /system/bin/sh wraps at 2^31 (measured earlier in abk_fas_check.sh, whose first
# version printed cap_view=-677).  Same formula, same file, either tool.
cap_view() {
  for _cv in "$1" "$2" "$3"; do
    case "$_cv" in ''|*[!0-9]*) echo 0; return 0 ;; esac
  done
  [ "$3" -gt 0 ] || { echo 0; return 0; }
  awk -v a="$1" -v f="$2" -v m="$3" 'BEGIN { printf "%d\n", a * f / m }'
}

# cpuinfo_min_freq, cpuinfo_max_freq and the DMIPS capacity never move, so they
# are read once and cached as "policy:min:max:arch"; the samplers must not pay
# for re-reading them at 20 Hz.
abk_policies() {
  for _d in "$CPUFREQ"/policy*; do
    [ -d "$_d" ] || continue
    printf '%s\n' "$_d"
  done
}
abk_const_for() {
  for _k in $CONSTS; do
    [ "${_k%%:*}" = "$1" ] || continue
    printf '%s' "${_k#*:}"
    return 0
  done
  printf '0:0:0'
}

# Why this device cannot be measured right now, or empty when it can.  Both
# reasons were paid for on the target ROM, and they are the same failure wearing
# two hats: on a phone with the screen off, and on a phone sitting on its lock
# screen, a launched app never becomes the top app, so it stays in the foreground
# cpuset (cpus 0-6) and the super core is not in its cpuset at all -- and
# `am start -W` stops reporting a LaunchState and a TotalTime.  Either one
# produces a complete, plausible table that describes a phone nobody can use.
abk_device_block() {
  command -v "$ABK_DUMPSYS" >/dev/null 2>&1 || return 0
  _w="$("$ABK_DUMPSYS" power 2>/dev/null | awk -F= '/mWakefulness=/ { print $2; exit }' | tr -d ' \r')"
  case "${_w:-}" in
    Awake|'') ;;
    *) printf 'the screen is off (mWakefulness=%s)' "$_w"; return 0 ;;
  esac
  # mDreamingLockscreen is the field that actually moves here; mShowingLockscreen
  # stayed false throughout while the keyguard was up and every launch was
  # swallowed, so it is not the one to test.
  _l="$("$ABK_DUMPSYS" window 2>/dev/null | awk '{ for (i = 1; i <= NF; i++) if ($i ~ /^mDreamingLockscreen=/) { split($i, a, "="); print a[2]; exit } }' | tr -d ' \r')"
  case "${_l:-}" in
    true) printf 'the lock screen is up (mDreamingLockscreen=true)' ;;
  esac
}

# Which cpuset the launching app lands in, and how many of the biggest cluster's
# cpus that set actually contains.  This exists because the two causes of "the
# launch never runs on the super core" are not the same fix: a ceiling that makes
# the core look small is fixed by the profile that writes scaling_max_freq, while
# a cpuset that does not list the core at all is fixed in the cpuset -- and no
# ceiling change can put a task on a cpu it is not allowed on.  Measured on the
# target ROM: /dev/cpuset/top-app/cpus is 0-7 but /dev/cpuset/foreground/cpus is
# 0-6, so which group a launch runs in decides whether cpu7 is even a candidate.
# A cpuset `cpus` file is in cpulist form ("0-3,5,7-9"), not a space separated
# list, so "0-7" is one token and matching a cpu against it by iteration would
# report that cpu7 is not allowed -- the exact opposite of the truth.  Expanded
# here, once per launch.
abk_expand_cpus() {
  _out=""
  _oldifs=$IFS
  IFS=,
  for _part in $1; do
    IFS=$_oldifs
    case "$_part" in
      *-*)
        _a="${_part%%-*}"; _b="${_part#*-}"
        case "$_a$_b" in
          ''|*[!0-9]*) ;;
          *)
            _c="$_a"
            while [ "$_c" -le "$_b" ]; do _out="$_out $_c"; _c=$((_c + 1)); done ;;
        esac ;;
      *) case "$_part" in ''|*[!0-9]*) ;; *) _out="$_out $_part" ;; esac ;;
    esac
    IFS=,
  done
  IFS=$_oldifs
  printf '%s' "$_out"
}
abk_app_cpus() {
  _pid="$($ABK_PIDOF "$1" 2>/dev/null | cut -d' ' -f1)"
  [ -n "$_pid" ] || { printf 'NA'; return 0; }
  _grp="$(awk -F: '/cpuset:/ { print $3 }' "$ABK_PROC_ROOT/$_pid/cgroup" 2>/dev/null | head -n 1)"
  [ -n "$_grp" ] || { printf 'NA'; return 0; }
  _cs="$(rdlist "$ABK_CPUSET_ROOT/${_grp#/}/cpus")"
  [ -n "$_cs" ] || { printf 'NA'; return 0; }
  abk_expand_cpus "$_cs"
}
# How many of the wanted cpus the allowed set contains: "<n>/<m>".
abk_count_in() {
  _n=0; _m=0
  for _c in $1; do
    _m=$((_m + 1))
    for _x in $2; do [ "$_x" = "$_c" ] && { _n=$((_n + 1)); break; }; done
  done
  printf '%s/%s' "$_n" "$_m"
}
abk_build_consts() {
  CONSTS=""
  POLICY_LIST="$(abk_policies)"
  for _d in $POLICY_LIST; do
    _pa_cpu="$(cut -d' ' -f1 "$_d/affected_cpus" 2>/dev/null)"
    _pa_arch=""
    [ -n "$_pa_cpu" ] && _pa_arch="$(rd "$ABK_SYS_ROOT/devices/system/cpu/cpu$_pa_cpu/cpu_capacity")"
    CONSTS="$CONSTS ${_d##*/}:$(rd "$_d/cpuinfo_min_freq"):$(rd "$_d/cpuinfo_max_freq"):${_pa_arch:-0}"
  done
}

# ---- samplers -------------------------------------------------------------
# Both stop on a flag file rather than on a signal, because `am start -W` is what
# they bracket and its return is the only event that may close the window.  One
# poll writes one line, so a sampler killed mid-window leaves a short file rather
# than a corrupted one.  The poll index is a counter, not a timestamp: whole
# seconds from /proc/uptime would collapse every poll of a launch into one.
abk_sample_freq() {
  _i=0
  while [ ! -f "$ABK_WORK/stop" ]; do
    _i=$((_i + 1))
    for _d in $POLICY_LIST; do
      printf '%s %s %s %s %s\n' "$_i" "${_d##*/}" "$(rd "$_d/scaling_cur_freq")" \
        "$(rd "$_d/scaling_min_freq")" "$(rd "$_d/scaling_max_freq")"
    done
    sleep 0.05
  done
}
# The launching process's main thread.  /proc/<pid>/stat is the thread group
# leader, so its processor field is the main thread's CPU.  The comm field can
# hold spaces, so it is stripped before the field is counted: after removing
# "pid (comm) " the original field 39 is field 37.
#
# The strip goes to the LAST ')', not the first '('.  An earlier version used
# ^[^(]*\) -- "everything up to the first paren" -- which on this ROM never
# matches at all, because the comm is itself parenthesised in the line
# (`4821 ((ease.cloudmusic)) ...`) and there is no ')' directly after the '('.
# The substitution silently did nothing and $37 then read cnswap, which is 0 for
# every process, so forty launches reported "0/714 main-thread samples on the
# super core": a perfectly shaped result that measured nothing.  Verified on
# device against the same line read both ways.
abk_sample_mt() {
  while [ ! -f "$ABK_WORK/stop" ]; do
    _pid="$($ABK_PIDOF "$1" 2>/dev/null | cut -d' ' -f1)"
    if [ -n "$_pid" ] && [ -r "$ABK_PROC_ROOT/$_pid/stat" ]; then
      _c="$(abk_proc_cpu "$ABK_PROC_ROOT/$_pid/stat")"
      [ -n "$_c" ] && printf '%s\n' "$_c" >> "$ABK_WORK/mt.raw"
    fi
    sleep 0.05
  done
}
# The processor field of a /proc/<pid>/stat line.  Split out of the sampler so
# the selftest can hold it against a fixture whose comm carries what this ROM's
# does -- see the note above; a comment cannot catch a substitution that quietly
# matches nothing, a fixture can.
abk_proc_cpu() {
  awk '{ sub(/^.*\) /, ""); print $37 }' "$1" 2>/dev/null
}

# Summarise one launch window.  Prints a fixed-order space-separated record; the
# caller splits it with `set --`, and that order is the contract:
#   polls cap_inv ceilings big_peak big_cpuinfo big_capview_min big_capview_max
abk_sum_freq() {
  awk -v consts="$CONSTS" '
    BEGIN {
      n = split(consts, kv, " ")
      for (i = 1; i <= n; i++) {
        split(kv[i], a, ":")
        cinfo[a[1]] = a[3] + 0
        carch[a[1]] = a[4] + 0
      }
    }
    {
      ts = $1; p = $2; cur = $3 + 0; mx = $5 + 0
      poll[ts] = 1
      if (cur > 0) {
        if (cmin[p] == 0 || cur < cmin[p]) cmin[p] = cur
        if (cur > cmax[p]) cmax[p] = cur
      }
      if (mx > 0) {
        ceil[p " " mx] = 1
        clr[ts " " p] = mx
      }
    }
    END {
      for (k in clr) {
        split(k, a, " "); p = a[2]
        cvt[k] = (cinfo[p] > 0 && carch[p] > 0) ? int(carch[p] * clr[k] / cinfo[p]) : 0
        if (cvt[k] > 0) npol[a[1]]++
      }
      # The inversion, computed per poll and not from the window aggregates: what
      # matters is whether the super core looked no bigger than the strongest
      # *weaker* cluster at the moment a placement was being made.  A poll that
      # caught only one cluster proves nothing either way -- the sampler is
      # stopped mid-write when the launch ends -- so those are dropped from both
      # the numerator and the denominator rather than counted as healthy.
      for (t in poll) {
        if (npol[t] + 0 < 2) continue
        np++
        bp = ""; ba = -1
        for (p in carch) {
          if (!((t " " p) in cvt)) continue
          if (cvt[t " " p] <= 0) continue
          if (carch[p] > ba) { ba = carch[p]; bp = p }
        }
        if (bp == "") continue
        bcv = cvt[t " " bp]
        oth = 0
        for (p in carch) {
          if (p == bp || carch[p] >= carch[bp]) continue
          if (!((t " " p) in cvt)) continue
          if (cvt[t " " p] > oth) oth = cvt[t " " p]
        }
        if (oth > 0 && oth >= bcv) inv++
        if (capmin[bp] == 0 || bcv < capmin[bp]) capmin[bp] = bcv
        if (bcv > capmax[bp]) capmax[bp] = bcv
      }
      bigp = ""; ba = -1
      for (p in carch) if (capmax[p] > 0 && carch[p] > ba) { ba = carch[p]; bigp = p }
      nb = 0
      for (k in ceil) { split(k, e, " "); if (e[1] == bigp) nb++ }
      printf "%d %d %d %d %d %d %d\n", np, inv + 0, nb, cmax[bigp] + 0,
             cinfo[bigp] + 0, capmin[bigp] + 0, capmax[bigp] + 0
    }' "$1"
}

# mt_polls mt_on_big <bigcpu list> <mt file>: the share of main-thread samples
# that landed on a CPU of the biggest cluster on offer.
abk_sum_mt() {
  awk -v cpus="$1" '
    BEGIN { n = split(cpus, c, " "); for (i = 1; i <= n; i++) if (c[i] != "") want[c[i]] = 1 }
    { np++; if ($1 in want) hit++ }
    END { printf "%d %d\n", np + 0, hit + 0 }' "$2"
}

# Two arms, per app: median of the baseline, median of this run, the delta and
# the delta as a share of the slower one.  Prints the table and a final
# "SHARE <percent>" line the caller reads.  Both files are the TSV --save writes.
abk_storage_share() {
  awk -F'\t' '
    # pref, not just the count: the keys in arr are spelled "<app> <n>", so
    # indexing the copy by the loop counter alone reads arr[1] and gets nothing.
    function med(arr, c, pref,   i, j, s, t) {
      for (i = 1; i <= c; i++) t[i] = arr[pref " " i]
      for (i = 1; i <= c; i++) for (j = i + 1; j <= c; j++)
        if (t[j] < t[i]) { s = t[i]; t[i] = t[j]; t[j] = s }
      if (c == 0) return -1
      return (c % 2) ? t[int((c + 1) / 2)] : int((t[int(c / 2)] + t[int(c / 2) + 1]) / 2)
    }
    FNR == NR { a = $1; if ($4 != "NA") { n = ++pn[a]; pt[a " " n] = $4 + 0 } next }
    { a = $1; if ($4 != "NA") { m = ++cn[a]; ct[a " " m] = $4 + 0 } seen[a] = 1 }
    END {
      printf "%-28s %9s %9s %8s %8s\n", "app", "base_ms", "this_ms", "delta_ms", "share"
      for (a in seen) {
        b = med(pt, pn[a] + 0, a); c = med(ct, cn[a] + 0, a)
        if (b < 0 || c < 0) continue
        d = c - b
        sh = (c > 0) ? int(100 * d / c) : 0
        printf "%-28s %9d %9d %8d %7d%%\n", a, b, c, d, sh
        tot += d; base += b; nn++
      }
      if (nn > 0 && base + tot > 0) {
        printf "%-28s %9d %9s %8d %7d%%\n", "ALL (mean of medians)", int(base / nn), "", int(tot / nn), int(100 * tot / (base + tot))
        printf "SHARE %d\n", int(100 * tot / (base + tot))
      } else {
        printf "SHARE\n"
      }
    }' "$1" "$2"
}

# ---- selftest -------------------------------------------------------------
# Exercises every decision this tool makes against a fake tree: a launch window
# with the super core capped out of the placement (must flag), one with it
# released (must not), a window with no samples (must not invent a ratio), and
# the 32-bit cap_view product.
abk_launch_selftest() {
  _st="$ABK_WORK/selftest"
  rm -rf "$_st"
  for _p in 0 7; do
    d="$_st/sys/devices/system/cpu/cpufreq/policy$_p"
    mkdir -p "$d" "$_st/sys/devices/system/cpu/cpu$_p"
    echo 300000 > "$d/cpuinfo_min_freq"
    echo 300000 > "$d/scaling_min_freq"
    if [ "$_p" = 0 ]; then
      echo 2016000 > "$d/cpuinfo_max_freq"
      echo "0 1 2" > "$d/affected_cpus"
      echo 855 > "$_st/sys/devices/system/cpu/cpu$_p/cpu_capacity"
    else
      echo 3187200 > "$d/cpuinfo_max_freq"
      echo "7" > "$d/affected_cpus"
      echo 1024 > "$_st/sys/devices/system/cpu/cpu$_p/cpu_capacity"
    fi
  done
  # The window summariser reads the cached constants, so they have to come from
  # the fixture exactly as they would from a device.
  ABK_SYS_ROOT="$_st/sys"
  CPUFREQ="$ABK_SYS_ROOT/devices/system/cpu/cpufreq"
  abk_build_consts
  _rc=0

  # A capped super core: cap_view 385 against the mid cluster at 855, which is
  # the "big core is not the big core on offer" case.
  _f="$_st/capped.txt"; : > "$_f"
  _i=1
  while [ "$_i" -le 20 ]; do
    printf '%s policy0 1785600 300000 1785600\n' "$_i" >> "$_f"
    printf '%s policy7 1200000 300000 1200000\n' "$_i" >> "$_f"
    _i=$((_i + 1))
  done
  set -- $(abk_sum_freq "$_f")
  if [ "$1" = "20" ] && [ "$2" = "20" ]; then
    echo "launch selftest ok: a capped super core counts as an inversion in every poll ($2/$1)"
  else
    echo "launch selftest FAIL: capped window not flagged (polls=$1 inv=$2, want 20/20)"; _rc=1
  fi

  # Same window, ceiling released: cap_view 1024 against 855.
  _f2="$_st/free.txt"; : > "$_f2"
  _i=1
  while [ "$_i" -le 20 ]; do
    printf '%s policy0 1785600 300000 1785600\n' "$_i" >> "$_f2"
    printf '%s policy7 3187200 300000 3187200\n' "$_i" >> "$_f2"
    _i=$((_i + 1))
  done
  set -- $(abk_sum_freq "$_f2")
  if [ "$2" = "0" ] && [ "$6" = "1024" ]; then
    echo "launch selftest ok: a released super core reads 1024/1024 and produces no inversion"
  else
    echo "launch selftest FAIL: released window wrong (inv=$2 capview_min=$6, want 0/1024)"; _rc=1
  fi

  # A rolling ceiling is reported as the number of points it took, not as a lock.
  # The mid cluster is included so these polls count at all (a poll that caught a
  # single cluster is dropped, see abk_sum_freq).
  _f3="$_st/ramp.txt"; : > "$_f3"
  _i=1
  while [ "$_i" -le 6 ]; do
    printf '%s policy0 1785600 300000 1785600\n' "$_i" >> "$_f3"
    printf '%s policy7 900000 300000 900000\n' "$_i" >> "$_f3"
    printf '%s policy7 1200000 300000 1200000\n' "$_i" >> "$_f3"
    printf '%s policy7 1843200 300000 1843200\n' "$_i" >> "$_f3"
    _i=$((_i + 1))
  done
  set -- $(abk_sum_freq "$_f3")
  if [ "$3" = "3" ] && [ "$1" = "6" ]; then
    echo "launch selftest ok: a ceiling that moves is reported as ceilings=3 over its 6 polls"
  else
    echo "launch selftest FAIL: ceilings=$3 polls=$1, want 3 over 6"; _rc=1
  fi

  # Main-thread ratio: 10 samples, 7 on the super core.
  : > "$_st/mt.txt"
  _i=1
  while [ "$_i" -le 10 ]; do
    if [ "$_i" -le 7 ]; then echo 7 >> "$_st/mt.txt"; else echo 3 >> "$_st/mt.txt"; fi
    _i=$((_i + 1))
  done
  set -- $(abk_sum_mt "7" "$_st/mt.txt")
  if [ "$1" = "10" ] && [ "$2" = "7" ]; then
    echo "launch selftest ok: main-thread placement is counted against the biggest cluster (7/10)"
  else
    echo "launch selftest FAIL: main-thread count wrong ($1/$2, want 10/7)"; _rc=1
  fi

  # No samples at all must read as 0/0, never as a ratio.
  : > "$_st/empty.txt"
  set -- $(abk_sum_mt "7" "$_st/empty.txt")
  if [ "$1" = "0" ] && [ "$2" = "0" ]; then
    echo "launch selftest ok: a pid that never appeared yields 0 polls, not a ratio"
  else
    echo "launch selftest FAIL: empty main-thread file invented ($1/$2)"; _rc=1
  fi

  # A cpuset cpus file is cpulist form.  "0-7" must read as containing cpu7 and
  # "0-6" must not -- iterating the raw token would get both backwards.
  _in1="$(abk_count_in "7" "$(abk_expand_cpus "0-7")")"
  _in2="$(abk_count_in "7" "$(abk_expand_cpus "0-6")")"
  if [ "$_in1" = "1/1" ] && [ "$_in2" = "0/1" ]; then
    echo "launch selftest ok: cpulist 0-7 contains cpu7 and 0-6 does not (1/1 against 0/1)"
  else
    echo "launch selftest FAIL: cpulist expansion wrong (got $_in1 and $_in2, want 1/1 and 0/1)"; _rc=1
  fi

  # The processor field, against the comm shape this ROM actually produces.  The
  # first version of this read sub(/^[^(]*\) /) -- "up to the first paren" -- which
  # never matched here and left $37 reading cnswap (0 for every process), so the
  # tool reported "0/714 samples on the super core" for forty launches.  Two
  # fixture lines: a paren-wrapped comm and one with a space in it.  The field
  # count is the real one: /proc/<pid>/stat puts 35 fields between state and
  # processor, so after the strip the processor is field 37.
  _fs="$_st/stat_fixture"
  _f1=""
  _i=1
  while [ "$_i" -le 35 ]; do _f1="$_f1 1"; _i=$((_i + 1)); done
  printf '4821 ((ease.cloudmusic)) S%s 7\n' "$_f1" > "$_fs"
  _pc="$(abk_proc_cpu "$_fs")"
  printf '12 (My App) S%s 3\n' "$_f1" > "$_fs"
  _pc2="$(abk_proc_cpu "$_fs")"
  if [ "$_pc" = "7" ] && [ "$_pc2" = "3" ]; then
    echo "launch selftest ok: the processor field survives a paren-wrapped and a spaced comm (7 and 3)"
  else
    echo "launch selftest FAIL: processor extraction got '$_pc' and '$_pc2', want 7 and 3"; _rc=1
  fi

  # cap_view must be the exact awk product.  A shell multiply of 855*2803200
  # overflows this ROM's 32-bit shell arithmetic -- the bug abk_fas_check.sh
  # already paid for -- so both of these would be wrong computed in shell.
  _cv="$(cap_view 855 2803200 2803200)"
  _cv2="$(cap_view 1024 1843200 3187200)"
  if [ "$_cv" = "855" ] && [ "$_cv2" = "592" ]; then
    echo "launch selftest ok: cap_view is the exact product (855/855, 592/1024 for a 58% ceiling)"
  else
    echo "launch selftest FAIL: cap_view got '$_cv' and '$_cv2', want 855 and 592"; _rc=1
  fi

  # The two-arm storage share.  The baseline is 1000 ms per app; this arm doubles
  # one app and leaves the other alone, so the mean rises 1000 -> 1500 and the
  # extra 500 of that 1500 is 33%.  The app whose launches failed to record must
  # drop out of the table rather than be counted as a zero.  printf, not echo:
  # echo does not expand \t in a POSIX sh and the fixture would be one long field.
  _ba="$_st/base.tsv"; _cu="$_st/cur.tsv"
  {
    printf 'com.example.half\t1\tCOLD\t1000\t10\t0\t0\t5\t0\t5\t1\t1800000\t3187200\t1/1\n'
    printf 'com.example.half\t2\tCOLD\t1000\t10\t0\t0\t5\t0\t5\t1\t1800000\t3187200\t1/1\n'
    printf 'com.example.same\t1\tCOLD\t1000\t10\t0\t0\t5\t0\t5\t1\t1800000\t3187200\t1/1\n'
    printf 'com.example.same\t2\tCOLD\t1000\t10\t0\t0\t5\t0\t5\t1\t1800000\t3187200\t1/1\n'
  } > "$_ba"
  {
    printf 'com.example.half\t1\tCOLD\t2000\t10\t0\t0\t5\t0\t5\t1\t1800000\t3187200\t1/1\n'
    printf 'com.example.half\t2\tCOLD\t2000\t10\t0\t0\t5\t0\t5\t1\t1800000\t3187200\t1/1\n'
    printf 'com.example.same\t1\tCOLD\t1000\t10\t0\t0\t5\t0\t5\t1\t1800000\t3187200\t1/1\n'
    printf 'com.example.none\t1\tCOLD\tNA\t10\t0\t0\t5\t0\t5\t1\t1800000\t3187200\t1/1\n'
  } > "$_cu"
  _sh="$(abk_storage_share "$_ba" "$_cu")"
  _shpc="$(printf '%s\n' "$_sh" | awk '/^SHARE/ { print $2 }')"
  if [ "$_shpc" = "33" ] && ! printf '%s\n' "$_sh" | grep -q "com.example.none"; then
    echo "launch selftest ok: the two-arm storage share is 33% here, and a launch with no TotalTime drops out"
  else
    echo "launch selftest FAIL: storage share got '${_shpc:-none}', want 33"; _rc=1
  fi

  if [ "$_rc" = "0" ]; then echo "launch selftest PASS"; else echo "launch selftest FAIL"; fi
  return "$_rc"
}

# ---- preflight ------------------------------------------------------------
CONSTS=""
POLICY_LIST=""
if [ "$ABK_SELFTEST" != "1" ]; then
  for _b in "$ABK_AM" "$ABK_PIDOF"; do
    command -v "$_b" >/dev/null 2>&1 || {
      echo "abk_launch_bench: no '$_b' on PATH; a run that cannot launch must not look like a fast one" >&2
      exit 1
    }
  done
  [ -d "$CPUFREQ" ] || { echo "abk_launch_bench: no cpufreq policies under $CPUFREQ" >&2; exit 1; }
  abk_build_consts
  # The device guard.  A missing dumpsys (a test host) is reported and skipped;
  # a phone that answers and is not usable stops the run.  This is checked again
  # before every launch below, not only here: a phone that locks or sleeps in the
  # middle of an arm is exactly how the first four runs of this tool produced
  # complete tables that described a phone behind its keyguard.
  _block="$(abk_device_block)"
  if [ -n "$_block" ]; then
    if [ "$ABK_ALLOW_SCREEN_OFF" != "1" ]; then
      echo "abk_launch_bench: refusing to measure: $_block." >&2
      echo "  On a phone like that, a launched app never becomes the top app: it stays in" >&2
      echo "  the foreground cpuset (cpus 0-6), so the super core is not in its cpuset at" >&2
      echo "  all and every launch reads 'cpuset bound'.  am start -W also stops reporting" >&2
      echo "  a LaunchState and a TotalTime, so those launches drop out and the median is" >&2
      echo "  taken over whichever ones happened to survive.  Both are artefacts of the" >&2
      echo "  state, not of the phone in a hand." >&2
      echo "  Wake and unlock it (input keyevent 224; wm dismiss-keyguard), or pass" >&2
      echo "  --allow-screen-off if that state is what you actually mean to measure." >&2
      exit 1
    fi
    echo "launch_bench: WARNING: $_block; --allow-screen-off given, results describe that state" >&2
  fi
fi

if [ "$ABK_SELFTEST" = "1" ]; then
  abk_launch_selftest
  exit $?
fi

# ---- where each cluster stands before we start -----------------------------
abk_say "launch_bench: mode=$ABK_MODE iters=$ABK_ITERS settle=${ABK_SETTLE}s gap=${ABK_GAP}s invert-pct=$ABK_INVERT_PCT"
abk_say "launch_bench: kernel=$(uname -r 2>/dev/null) uptime=$(abk_now)s MemAvailable_kB=$(awk '/MemAvailable/ { print $2 }' "$ABK_PROC_ROOT/meminfo" 2>/dev/null)"
echo "POLICY name cpus cpuinfo_min arch cpuinfo_max ceiling cap_view"
_bigarch=0; _bigcpu=""; _bigpol=""; _rc=0
for _d in $POLICY_LIST; do
  _n="${_d##*/}"
  _kv="$(abk_const_for "$_n")"
  _r="${_kv#*:}"; _imax="${_r%%:*}"; _arch="${_r##*:}"
  _cmin="$(rd "$_d/cpuinfo_min_freq")"
  _cap="$(rd "$_d/scaling_max_freq")"
  _aff="$(rdlist "$_d/affected_cpus")"
  _cv="$(cap_view "${_arch:-0}" "$_cap" "${_imax:-0}")"
  echo "POLICY $_n ${_aff:-NA} ${_cmin:-NA} ${_arch:-NA} ${_imax:-NA} ${_cap:-NA} ${_cv:-NA}"
  if [ "${_arch:-0}" -gt "$_bigarch" ]; then _bigarch="${_arch:-0}"; _bigcpu="$_aff"; _bigpol="$_n"; fi
  # Measured on this ROM: a ceiling set exactly to an applied point is the normal
  # shape of a FAS-style owner, so this is a note and not a failure by itself.
  _smin="$(rd "$_d/scaling_min_freq")"
  if [ -n "$_smin" ] && [ "$_smin" = "$_cap" ] && [ "$_smin" = "${_imax:-}" ]; then
    echo "note: $_n has scaling_min_freq == scaling_max_freq == cpuinfo_max_freq ($_cap)"
    _rc=1
  fi
done
echo "BIGCLUSTER pol=$_bigpol arch=$_bigarch cpus=${_bigcpu:-NA}"

# ---- the runs -------------------------------------------------------------
RES="$ABK_WORK/results.tsv"; : > "$RES"
_done_launches=0
_nostate=0
for _entry in $ABK_LIST; do
  _pkg="${_entry%%/*}"; _cmp="${_entry#*/}"
  _i=1
  while [ "$_i" -le "$ABK_ITERS" ]; do
    # Re-checked every launch, not just at startup: a phone that sleeps or locks
    # mid-arm is how the first four runs produced plausible-looking tables that
    # described a phone behind its keyguard.  A partial run that says why is worth
    # more than a complete one that lies.
    _block="$(abk_device_block)"
    if [ -n "$_block" ] && [ "$ABK_ALLOW_SCREEN_OFF" != "1" ]; then
      echo "FAIL: $_block after $_done_launches launch(es); the rest of this arm would" >&2
      echo "      describe a phone nobody can use (foreground cpuset, no LaunchState), so the" >&2
      echo "      run stops here.  Wake and unlock it and re-run, or pass --allow-screen-off" >&2
      echo "      to measure that state on purpose." >&2
      [ -n "$ABK_SAVE" ] && cp "$RES" "$ABK_SAVE" 2>/dev/null
      exit 1
    fi
    "$ABK_AM" force-stop "$_pkg" >/dev/null 2>&1 || true
    sleep "$ABK_SETTLE"
    if [ "$ABK_MODE" = drop ]; then
      sync 2>/dev/null || true
      ( echo 3 > "$ABK_PROC_ROOT/sys/vm/drop_caches" ) 2>/dev/null || {
        echo "abk_launch_bench: cannot drop caches (not root?); --mode drop needs it" >&2
        exit 1; }
      sleep 1
    fi
    rm -f "$ABK_WORK/stop" "$ABK_WORK/mt.raw"
    : > "$ABK_WORK/pol.raw"
    abk_sample_freq >> "$ABK_WORK/pol.raw" & _sf=$!
    abk_sample_mt "$_pkg" & _sm=$!
    _s0="$(vm pswpin)"; _g0="$(vm pgpgin)"; _io0="$(psi_total io)"
    _out="$("$ABK_AM" start -W -n "$_cmp" 2>/dev/null)"
    _s1="$(vm pswpin)"; _g1="$(vm pgpgin)"; _io1="$(psi_total io)"
    # One more settle inside the window: `am start -W` returns when the activity
    # is drawn, and the part of a launch that lands after it is where a placer
    # problem shows up.
    sleep 0.3
    : > "$ABK_WORK/stop"
    kill "$_sf" 2>/dev/null || true
    kill "$_sm" 2>/dev/null || true
    wait "$_sf" 2>/dev/null || true
    wait "$_sm" 2>/dev/null || true

    _state="$(printf '%s\n' "$_out" | awk '/LaunchState:/ { print $2 }')"
    _tt="$(printf '%s\n' "$_out" | awk '/TotalTime:/ { print $2 }')"
    _wt="$(printf '%s\n' "$_out" | awk '/WaitTime:/ { print $2 }')"
    case "${_tt:-}" in ''|*[!0-9]*) _tt=NA ;; esac
    case "${_wt:-}" in ''|*[!0-9]*) _wt=NA ;; esac
    [ -n "${_state:-}" ] || _state=NONE
    # `am start -W` reports a LaunchState when it actually drove the launch to a
    # drawn activity.  Three in a row without one means the launches are not
    # happening -- a locked phone and an uninstalled app look the same here -- and
    # a median over whichever launches happened to survive is not a measurement.
    case "$_state" in
      NONE|UNKNOWN) _nostate=$((_nostate + 1)) ;;
      *) _nostate=0 ;;
    esac
    if [ "$_nostate" -ge 3 ] && [ "$ABK_ALLOW_SCREEN_OFF" != "1" ]; then
      echo "FAIL: $_nostate launches in a row produced no LaunchState, so am start -W is not" >&2
      echo "      completing them and any median here would be over the survivors.  Check that" >&2
      echo "      the phone is awake, unlocked and that the app is launchable, then re-run." >&2
      [ -n "$ABK_SAVE" ] && cp "$RES" "$ABK_SAVE" 2>/dev/null
      exit 1
    fi
    case "${_s0:-}${_s1:-}" in ''|*[!0-9]*) _sw=NA ;; *) _sw=$((_s1 - _s0)) ;; esac
    case "${_g0:-}${_g1:-}" in ''|*[!0-9]*) _rd=NA ;; *) _rd=$((_g1 - _g0)) ;; esac
    case "${_io0:-}${_io1:-}" in ''|*[!0-9]*) _io=NA ;; *) _io=$((_io1 - _io0)) ;; esac

    set -- $(abk_sum_freq "$ABK_WORK/pol.raw")
    _polls=$1; _inv=$2; _ceil=$3; _peak=$4; _cinfo=$5; _capmin=$6; _capmax=$7
    set -- $(abk_sum_mt "${_bigcpu:-}" "$ABK_WORK/mt.raw")
    _mtp=$1; _mth=$2
    _cs="$(abk_app_cpus "$_pkg")"
    _csb=NA
    [ "$_cs" != "NA" ] && [ -n "${_bigcpu:-}" ] && _csb="$(abk_count_in "${_bigcpu:-}" "$_cs")"

    echo "LAUNCH app=$_pkg iter=$_i state=$_state total=$_tt wait=$_wt swapin=$_sw readin=$_rd io_stall_us=$_io polls=$_polls cap_inv=$_inv mt_polls=$_mtp mt_big=$_mth cset=$_cs cset_big=$_csb big_peak=$_peak big_cpuinfo=$_cinfo big_capview=$_capmin-$_capmax ceilings=$_ceil"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$_pkg" "$_i" "$_state" "$_tt" "$_rd" "$_sw" "$_io" "$_polls" "$_inv" \
      "$_mtp" "$_mth" "$_peak" "$_cinfo" "$_csb" >> "$RES"
    sleep "$ABK_GAP"
    _i=$((_i + 1))
    _done_launches=$((_done_launches + 1))
  done
done
rm -f "$ABK_WORK/stop"

# The per-launch TSV outlives the run when asked, because the storage's share is
# a two-arm measurement and the second arm has to be able to read the first.
if [ -n "$ABK_SAVE" ]; then
  if cp "$RES" "$ABK_SAVE" 2>/dev/null; then
    echo "SAVED $ABK_SAVE ($(wc -l < "$ABK_SAVE" | tr -d ' ') launches, mode=$ABK_MODE)"
  else
    echo "abk_launch_bench: could not save to $ABK_SAVE" >&2
  fi
fi

# ---- summary --------------------------------------------------------------
echo ""
awk -F'\t' '
  {
    app = $1; n[app]++
    if ($4 == "NA") { na[app]++ }
    else {
      c = ++cnt[app]; tt[app " " c] = $4 + 0
      # The first iteration of each app is the one a user actually feels: every
      # later iteration of the same app follows a launch that was already warm in
      # page cache, and on this ROM the difference is a factor of two.  Tracked by
      # iteration number rather than by arrival order so a re-ordered or partial
      # run still reports the same thing.
      if (!(app in firstit) || $2 + 0 < firstit[app]) { firstit[app] = $2 + 0; firsttt[app] = $4 + 0 }
    }
    if ($5 != "NA") { rdk[app " " n[app]] = $5 + 0 }
    if ($8 > 0) { polls += $8; inv += $9 }
    if ($10 > 0) { mtp += $10; mth += $11 }
    seen[app] = 1
  }
  END {
    printf "%-28s %5s %9s %9s %9s %9s %11s\n", "app", "n", "first_ms", "med_ms", "min_ms", "max_ms", "readin_pages"
    for (a in seen) {
      c = cnt[a] + 0
      for (i = 1; i <= c; i++) for (j = i + 1; j <= c; j++)
        if (tt[a " " j] < tt[a " " i]) { s = tt[a " " i]; tt[a " " i] = tt[a " " j]; tt[a " " j] = s }
      med = "NA"; mn = "NA"; mx = "NA"
      if (c > 0) {
        med = (c % 2) ? tt[a " " int((c + 1) / 2)] : int((tt[a " " int(c / 2)] + tt[a " " int(c / 2) + 1]) / 2)
        mn = tt[a " " 1]; mx = tt[a " " c]
      }
      rc = 0; s = 0
      for (i = 1; i <= n[a]; i++) if ((a " " i) in rdk) { s += rdk[a " " i]; rc++ }
      rdm = (rc == 0) ? "NA" : int(s / rc)
      printf "%-28s %5d %9s %9s %9s %9s %11s\n", a, c, (a in firsttt) ? firsttt[a] : "NA", med, mn, mx, rdm
      if (na[a] > 0) printf "%-28s   (%d of %d launches gave no TotalTime and are excluded)\n", "", na[a], n[a]
    }
    printf "\nWINDOW polls=%d cap_inv=%d", polls + 0, inv + 0
    if (polls > 0) printf " (%.1f%%)", 100 * inv / polls
    printf "  mt_polls=%d mt_on_big=%d", mtp + 0, mth + 0
    if (mtp > 0) printf " (%.1f%%)", 100 * mth / mtp
    printf "\n"
  }' "$RES"

set -- $(awk -F'\t' '{ p += $8; i += $9 } END { print p + 0, i + 0 }' "$RES")
_wp=$1; _wi=$2
set -- $(awk -F'\t' '{ p += $10; h += $11 } END { print p + 0, h + 0 }' "$RES")
_mp=$1; _mh=$2
_medrd="$(awk -F'\t' '$5 != "NA" { s += $5; c++ } END { print (c ? int(s / c) : "NA") }' "$RES")"
_oktt="$(awk -F'\t' '$4 != "NA" { c++ } END { print c + 0 }' "$RES")"
_ioavg="$(awk -F'\t' '$7 != "NA" { s += $7; c++ } END { print (c ? int(s / c) : "NA") }' "$RES")"
# Launches whose cpuset did not contain the biggest cluster at all: "<0/N>" over
# "<total measured>".  Read as the count of launches where the super core was not
# a candidate for any reason other than its ceiling.
set -- $(awk -F'\t' '
  $14 != "NA" && $14 != "" {
    n = split($14, a, "/")
    if (n == 2 && a[1] == 0 && a[2] > 0) excl++
    tot++
  }
  END { print excl + 0, tot + 0 }' "$RES")
_csexcl=$1; _cstot=$2

if [ "$_oktt" = "0" ]; then
  echo "FAIL: no launch produced a TotalTime; there is nothing to report and 0 ms would be a lie." >&2
  exit 1
fi

_invpct=0
[ "$_wp" -gt 0 ] && _invpct=$(( _wi * 100 / _wp ))
_mtpct=100
[ "$_mp" -ge "$ABK_MIN_MT" ] && _mtpct=$(( _mh * 100 / _mp ))

_placement=0
[ "$_wp" -gt 0 ] && [ "$_invpct" -ge "$ABK_INVERT_PCT" ] && _placement=2
if [ "$_placement" = "0" ] && [ "$_mp" -ge "$ABK_MIN_MT" ] && [ "$_mtpct" -lt 20 ]; then
  _placement=1
fi

if [ "$_mp" -ge "$ABK_MIN_MT" ]; then
  echo "BIGCLUSTER ${_bigpol:-?} (arch ${_bigarch:-0}) carried ${_mh}/${_mp} main-thread samples (${_mtpct}%)"
else
  echo "BIGCLUSTER ${_bigpol:-?} placement: only $_mp main-thread samples (<$ABK_MIN_MT), not enough to say"
fi

echo ""
if [ "$_cstot" -gt 0 ] && [ "$_csexcl" -gt 0 ]; then
  echo "CPUSET: in $_csexcl of $_cstot measured launches the biggest cluster (${_bigpol:-?}, cpus"
  echo "        ${_bigcpu:-?}) was not in the launching app's cpuset at all, so it was not a"
  echo "        candidate for any reason a ceiling can fix."
fi
if [ "$_cstot" -gt 0 ] && [ "$_csexcl" = "$_cstot" ]; then
  echo "VERDICT: cpuset bound. Every launch ran in a cpuset that does not list the biggest"
  echo "         cluster's cpus (${_bigcpu:-?}), so no ceiling change and no kernel graft can put"
  echo "         the launch there. Fix the cpuset the app is launched in -- on this ROM"
  echo "         /dev/cpuset/top-app/cpus lists cpu7 while /dev/cpuset/foreground/cpus stops at 6."
  exit 0
fi
if [ "$_placement" = "2" ]; then
  # The ceiling was measured holding the super core down -- the case Batch 10-6
  # proved is worth 16% on this ROM.
  echo "VERDICT: ceiling bound. In $_wi/$_wp launch-window polls ($_invpct%) the biggest core had no"
  echo "         more capacity in the placer's eyes than a weaker cluster, and $_mh/$_mp main-thread"
  echo "         samples (${_mtpct}%) landed on it."
  echo "         WALT/EAS scale cpu_capacity by scaling_max_freq, so whoever writes that node is"
  echo "         choosing where a launch runs, not only how fast. Stop that writer, or raise"
  echo "         ${_bigpol:-the super core}'s ceiling in its profile: the ceiling owner is the lever,"
  echo "         not a kernel graft."
  exit 4
fi
if [ "$_placement" = "1" ]; then
  # The super core was available -- cpuset lists it, and its ceiling was not
  # measurably inverting -- and the launch still did not run there.  Calling this
  # a ceiling problem would send the reader to the wrong file, so it is reported
  # as what it is: a placement outcome with no measured cause in this run.
  echo "VERDICT: placement bound, cause not measured. No cap inversion was seen (0/$_wp polls: the"
  echo "         biggest core stayed the biggest on offer), and ${_bigpol:-the super core} was in the"
  echo "         app's cpuset, yet only $_mh/$_mp main-thread samples (${_mtpct}%) landed on it --"
  echo "         below the 20% floor this tool treats as 'the launch ran there'."
  echo "         The lever is still on the placement side, but this run does not say which part:"
  echo "         trace sched_find_best_target / sched_switch during a launch (the Batch 10-6 method),"
  echo "         and check the ceiling's owner for a ramp that this window's polls missed."
  exit 4
fi

# ---- the storage's share, which needs both arms ---------------------------
# pgpgin is a machine-wide counter, so "this arm read N pages" is not an
# attribution and must not be turned into one.  What separates a launch that
# waited on storage from one that merely caused storage traffic is the delta
# between the arm that had the page cache and the arm that did not, so the
# storage verdict is only ever printed when --compare supplies the other arm.
if [ -n "$ABK_COMPARE" ]; then
  if [ ! -f "$ABK_COMPARE" ]; then
    echo "abk_launch_bench: --compare $ABK_COMPARE does not exist; the storage share is not computed" >&2
  else
    echo ""
    echo "STORAGE this arm is mode=$ABK_MODE; the baseline is $ABK_COMPARE"
    _share="$(abk_storage_share "$ABK_COMPARE" "$RES")"
    echo "$_share" | grep -v '^SHARE'
    _sharepc="$(echo "$_share" | awk '/^SHARE/ { print $2 }')"
    echo ""
    if [ -n "${_sharepc:-}" ] && [ "$_sharepc" -ge 15 ]; then
      echo "VERDICT: storage carries $_sharepc% of the launch. The arm without page cache is that much"
      echo "         slower than the arm with it, so a real share of the launch is the wait for pages"
      echo "         and prefetch is worth engineering -- the two hooks this module already rides"
      echo "         (android_vh_ra_tuning_max_page, android_vh_tune_mmap_readaround) widen the window"
      echo "         for the launching task. Note the userspace half of the launch stack (this ROM's"
      echo "         iorapd / xiaomi.launch_boost.readahead service) is not running at all: measured"
      echo "         init.svc.iorapd=stopped, persist.sys.stability.PrereadEnable=false, and the"
      echo "         readahead AIDL service is absent from the service list."
      exit 0
    fi
    echo "VERDICT: storage carries ${_sharepc:-0}% of the launch (under the 15% floor), so the wait for"
    echo "         pages is not what makes this launch slow even though it causes the traffic. Do not"
    echo "         spend the batch on prefetch. The placer was not the cause either in this pair of"
    echo "         runs ($_mh/$_mp on the biggest core, ${_invpct}% cap inversion), so the time is going"
    echo "         somewhere neither rule measured -- trace the launch (sched_switch, and the app's"
    echo "         binder waits) before choosing a lever."
    exit 0
  fi
fi

echo ""
echo "VERDICT: no placement problem was measured in this arm (${_invpct}% cap inversion, $_mh/$_mp"
echo "         main-thread samples on the biggest core). This arm read a median of ${_medrd} pages"
echo "         and stalled ${_ioavg} us in PSI io, but pgpgin is machine-wide and one arm cannot say"
echo "         whether those reads were on the critical path -- a launch that causes traffic is not"
echo "         a launch that waited for it."
echo "         Size the storage's share by running both arms, which is the only thing that answers it:"
echo "           abk_launch_bench.sh --mode warm --save /data/local/tmp/lb_warm.tsv"
echo "           abk_launch_bench.sh --mode drop --compare /data/local/tmp/lb_warm.tsv"
exit "$_rc"
