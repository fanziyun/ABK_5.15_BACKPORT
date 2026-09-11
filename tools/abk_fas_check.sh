#!/bin/sh
# abk_fas_check.sh - is that a frequency ceiling, or is it a lock?
#
# A cpufreq policy whose scaling_min_freq equals scaling_max_freq is not broken
# by that fact alone: a FAS-style owner (vendor WALT/FAS, or a userspace
# scheduler profile driving it) sets min == max == its own target every time it
# changes frequency, so the healthy state and the stuck state are identical in a
# snapshot.  Only time and demand separate them:
#
#   healthy  the single point moves (many distinct points, transitions piling
#            up) and sits below the ceiling when the cluster is lightly loaded;
#   stuck    the single point does not move, it equals the ceiling, and the
#            governor keeps computing a lower frequency that is never applied.
#
# That is what this tool answers, and it writes nothing.  It then looks at the
# two ways a cluster stops tracking demand because of somebody else's design
# rather than because of a bug:
#
#   A userspace ceiling holder.  Whoever owns scaling_max_freq also owns what
#   the scheduler thinks each core is *worth*: WALT/EAS recompute a policy's
#   capacity as cpu_capacity * scaling_max_freq / cpuinfo_max_freq (the
#   fmax_capacity / rq_cpu_capacity_orig pair of the update_cpu_capacity
#   tracepoint), so a profile that keeps the super core at 27% of its ceiling
#   hands the placer a core that looks *smaller* than the mid cores -- and light
#   work, an app launch, is then never put on it.  Measured on SM8550 with a
#   userspace scheduler profile in play: capacity_orig of the super core crawled
#   277 -> 400 -> 549 -> 672 of 1024 while the mid cluster sat at 585-749, and a
#   cold launch sampled the app's UI thread on the super core 5 times in 36 with
#   that ceiling in place against 11 times in 37 with it released -- same build,
#   same app, only the ceiling's owner was stopped.  Section 3 counts the polls
#   where that inversion is actually in effect.
#
#   The ABK schedutil smart-freq floor (Batch 10-4) armed on a device whose
#   policies run a foreign governor.  That floor is *not* reached through
#   schedutil: Batch 10-4c moved sampling to android_vh_scheduler_tick and the
#   floor to android_vh_cpufreq_resolve_freq precisely so it would run under any
#   governor -- so on a vendor-walt tree an armed floor does act, and because it
#   is clamped to policy->max while a FAS owner holds min == max == its target,
#   "raise to the floor" re-states the frequency already applied and swallows
#   every downscale.  Measured on SM8550: waltgov computed 766 MHz for a cluster
#   at 22% demand while that cluster sat at 1785600 until the knob was turned
#   off.  Batch 10-5 added abk_sf_dvfs_owned(), which makes a *current* payload
#   stand down there and ships disabled by default; this tool tells the two
#   generations apart, because abk_sf_boosting only exists from 10-5 on.
#
# Usage:
#   abk_fas_check.sh                 snapshot + one-shot verdicts
#   abk_fas_check.sh --sample 30     statistics over 30 s of the real workload
#   abk_fas_check.sh --probe         6 s of all-core load, then watch the decay
#
# Options:
#   --sample SECS   poll scaling_cur_freq for SECS seconds (~5 Hz per policy)
#   --probe         apply load, then verify the frequency comes back down
#   --load-secs N   load duration for --probe (default 6)
#   --decay-secs N  decay window for --probe (default 12)
#   --max-mode N    largest allowed share of one single point, percent
#                   (default 60)
#   --min-rate N    cpufreq transitions per second below which a concentrated
#                   policy counts as frozen (default 2)
#   --invert-pct N  share of the sampling window, in percent, during which the
#                   super core may look no bigger than a weaker cluster before
#                   the ceiling holding it down is reported (default 5)
#   --min-busy N    aggregate machine busy percent the sampling window must
#                   reach before a motionless policy is called frozen rather than
#                   parked (default 5)
#   --sys-root PATH sysfs root (default /sys; for testing)
#   --proc-root PATH procfs root (default /proc; for testing)
#   --quiet         only print verdict and failure lines
#   -h, --help      this text
# Exit codes: 0 healthy (or idle, so the sampling pass had nothing to judge),
# 1 a policy looks pinned, or the smart-freq floor is armed on a policy a FAS
# owner holds, 3 --probe found no decay after load, 4 the super core is starved
# of capacity by somebody else's ceiling, 2 bad usage.
# ----8<---- end of help
set -eu

SAMPLE=0
PROBE=0
LOAD_SECS=6
DECAY_SECS=12
MIN_RATE=2
MAX_MODE=60
INVERT_PCT=5
MIN_BUSY=5
SYS_ROOT=/sys
PROC_ROOT=/proc
QUIET=0

usage() {
  sed -n '2,/end of help/p' "$0"
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --sample) SAMPLE="${2:-}"; shift 2 ;;
    --probe) PROBE=1; shift ;;
    --load-secs) LOAD_SECS="${2:-}"; shift 2 ;;
    --decay-secs) DECAY_SECS="${2:-}"; shift 2 ;;
    --min-rate) MIN_RATE="${2:-}"; shift 2 ;;
    --max-mode) MAX_MODE="${2:-}"; shift 2 ;;
    --invert-pct) INVERT_PCT="${2:-}"; shift 2 ;;
    --min-busy) MIN_BUSY="${2:-}"; shift 2 ;;
    --sys-root) SYS_ROOT="${2%/}"; shift 2 ;;
    --proc-root) PROC_ROOT="${2%/}"; shift 2 ;;
    --quiet) QUIET=1; shift ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
  for _n in SAMPLE LOAD_SECS DECAY_SECS MIN_RATE MAX_MODE INVERT_PCT MIN_BUSY; do
    eval "_v=\$$_n"
    case "$_v" in
      ''|*[!0-9]*) echo "--$_n wants a number, got '$_v'" >&2; exit 2 ;;
    esac
  done
done

CPUFREQ="$SYS_ROOT/devices/system/cpu/cpufreq"
SF_PARAMS="$SYS_ROOT/module/cpufreq_schedutil/parameters"
FAS_NODE="$PROC_ROOT/fas"
# Scratch for the sampling window.  /data/local/tmp is where this runs on device
# (no /tmp there); off device the caller's TMPDIR wins, and plain /tmp is the
# fallback, so --sample does not abort under `set -e` on a test host.
_abk_tmp() {
  for _t in "${TMPDIR:-}" /data/local/tmp /tmp .; do
    [ -n "$_t" ] && [ -d "$_t" ] && { printf '%s' "$_t"; return 0; }
  done
  printf '.'
}
SAMPLES="$(_abk_tmp)/.abk_fas_check.$$.txt"
RC=0

SAY() { [ "$QUIET" = "1" ] || echo "$*"; }
flag() { [ "$RC" -gt "$1" ] || RC="$1"; }
# One fork per node: every cpufreq node this tool reads is a single line, and the
# sampling loop needs the polls to be cheap enough to actually fill the window.
rd() { head -n 1 "$1" 2>/dev/null || true; }
now() { cut -d. -f1 "$PROC_ROOT/uptime"; }
policies() {
  for _d in "$CPUFREQ"/policy*; do
    [ -d "$_d" ] || continue
    printf '%s\n' "$_d"
  done
}
POLICY_LIST="$(policies)"
# cpuinfo_min_freq, cpuinfo_max_freq and the DMIPS capacity never change, so the
# sampling loop must not re-read them: they are cached as "name:min:max:arch".
CONSTS=""
for _d in $POLICY_LIST; do
  _pa_cpu="$(cut -d' ' -f1 "$_d/affected_cpus" 2>/dev/null)"
  _pa_arch=""
  [ -n "$_pa_cpu" ] && _pa_arch="$(rd "$SYS_ROOT/devices/system/cpu/cpu$_pa_cpu/cpu_capacity")"
  CONSTS="$CONSTS ${_d##*/}:$(rd "$_d/cpuinfo_min_freq"):$(rd "$_d/cpuinfo_max_freq"):${_pa_arch:-0}"
done
const_for() {
  for _kv in $CONSTS; do
    [ "${_kv%%:*}" = "$1" ] || continue
    printf '%s' "${_kv#*:}"
    return 0
  done
  printf '0:0:0'
}
# cpu_totals: "<busy> <total>" jiffies from /proc/stat, for measuring whether
# anything actually wanted to run during a sampling window.
cpu_totals() {
  awk '/^cpu /{print $2+$3+$4+$7+$8+$9, $2+$3+$4+$5+$6+$7+$8+$9+$10}' \
    "$PROC_ROOT/stat" 2>/dev/null || true
}
# cap_view <arch> <ceiling> <cpuinfo_max>: the capacity the placer actually ranks
# cores by.  WALT/EAS scale the DMIPS capacity by how far a ceiling holder lets
# the policy go, so this is also what a userspace limiter does to *placement*, not
# only to frequency.  Echoes 0 when an input is missing so callers skip it.
#
# The product goes through awk because shell arithmetic cannot hold it: 855 *
# 2803200 is 2.4e9 and this ROM's /system/bin/sh wraps at 2^31 (measured, and the
# same trap the runtime module documents for zram byte math).  It is exact here
# and in the runtime module's boot report because this number exists to be
# compared against rq_cpu_capacity_orig from the tracepoint; pre-dividing to keep
# it in shell would have been cheaper in forks and would have disagreed by
# truncation.
cap_view() {
  for _cv in "$1" "$2" "$3"; do
    case "$_cv" in ''|*[!0-9]*) echo 0; return 0 ;; esac
  done
  [ "$3" -gt 0 ] || { echo 0; return 0; }
  awk -v a="$1" -v f="$2" -v m="$3" 'BEGIN { printf "%d\n", a * f / m }'
}

[ -d "$CPUFREQ" ] || { echo "no cpufreq policies under $CPUFREQ" >&2; exit 2; }

# --- 1. ownership snapshot --------------------------------------------------
SF_ENABLE="$(rd "$SF_PARAMS/abk_sf_enable")"
SF_BOOST="$(rd "$SF_PARAMS/abk_sf_boosting")"
SAY "abk smart-freq    enable=${SF_ENABLE:-<absent>} boosting=${SF_BOOST:-<absent>}"
if [ -e "$FAS_NODE" ]; then
  SAY "fas registration  $(rd "$FAS_NODE")"
else
  SAY "fas registration  $FAS_NODE absent (not a FAS kernel)"
fi

case "$SF_ENABLE" in
  '') : ;;
  Y|y|1)
    _foreign=0
    _pinned=0
    for _d in $POLICY_LIST; do
      case "$(rd "$_d/scaling_governor")" in
        schedutil) : ;;
        *) _foreign=1 ;;
      esac
      _mn="$(rd "$_d/scaling_min_freq")"
      [ -n "$_mn" ] && [ "$_mn" = "$(rd "$_d/scaling_max_freq")" ] && _pinned=1
    done
    if [ "$_foreign" = "1" ] || [ "$_pinned" = "1" ]; then
      if [ -e "$SF_PARAMS/abk_sf_boosting" ]; then
        # A Batch 10-5 payload carries abk_sf_dvfs_owned(), so it stands down on
        # exactly these policies.  Arming it still buys nothing, so say so.
        SAY "note: abk_sf_enable=Y while a policy is foreign-governored or pinned at min == max."
        SAY "      This payload has the 10-5 ownership gates, so the floor stands down there --"
        SAY "      sched.abk_sf_enable=0 states that intent (it is the shipped default)."
      else
        # No abk_sf_boosting node means a pre-10-5 payload, which registers its
        # hooks from android_vh_scheduler_tick / android_vh_cpufreq_resolve_freq:
        # governor-independent, so it is live here and has no ownership gate.
        echo "FAIL: abk_sf_enable=Y on a pre-10-5 payload (no abk_sf_boosting node)," \
             "and a policy is foreign-governored or pinned at min == max."
        echo "      That payload has no ownership gate: the floor is clamped to policy->max,"
        echo "      so 'raise to the floor' re-states the frequency already applied and every"
        echo "      downscale is swallowed.  Measured on SM8550 under walt+FAS: a cluster at"
        echo "      22% demand held at 1785600 for a whole game session while waltgov"
        echo "      computed 766 MHz."
        echo "      Fix now: echo 0 > $SF_PARAMS/abk_sf_enable (or sched.abk_sf_enable=0 in"
        echo "      the runtime module's tunables.conf); durably, graft the Batch 10-5 payload,"
        echo "      which defers on its own."
        flag 1
      fi
    fi
    ;;
esac

# --- 2. per-policy single-point check --------------------------------------
# _big/_oth track the strongest and the strongest-appearing-competitor cluster by
# DMIPS capacity, so the inversion check below compares the super core with the
# core that is currently winning against it.
_big=""; _bigarch=0; _bigcapv=0
_oth=""; _otharch=0; _othcapv=0
for _d in $POLICY_LIST; do
  _n="${_d##*/}"
  _cur="$(rd "$_d/scaling_cur_freq")"
  _min="$(rd "$_d/scaling_min_freq")"
  _max="$(rd "$_d/scaling_max_freq")"
  _info="$(rd "$_d/cpuinfo_max_freq")"
  _kv="$(const_for "$_n")"
  _r="${_kv#*:}"; _arch="${_r##*:}"
  _capv="$(cap_view "${_arch:-0}" "$_max" "${_info:-0}")"
  SAY "$_n  gov=$(rd "$_d/scaling_governor") cur=$_cur min=$_min max=$_max cpuinfo_max=${_info:-?} trans=$(rd "$_d/stats/total_trans") arch=${_arch:-?} cap_view=${_capv:-?}"
  if [ -n "$_min" ] && [ "$_min" = "$_max" ] && [ "$_min" = "${_info:-}" ]; then
    echo "WARN: $_n is pinned to cpuinfo_max_freq exactly ($_min);" \
         "a cap set to the ceiling is indistinguishable from a lock."
    flag 1
  fi
  [ "${_capv:-0}" -gt 0 ] || continue
  if [ "$_arch" -gt "$_bigarch" ]; then
    if [ -n "$_big" ] && [ "$_bigcapv" -gt "$_othcapv" ]; then
      _oth="$_big"; _otharch="$_bigarch"; _othcapv="$_bigcapv"
    fi
    _big="$_n"; _bigarch="$_arch"; _bigcapv="$_capv"
  elif [ "$_capv" -gt "$_othcapv" ]; then
    _oth="$_n"; _otharch="$_arch"; _othcapv="$_capv"
  fi
done

if [ -n "$_big" ] && [ -n "$_oth" ] && [ "$_othcapv" -ge "$_bigcapv" ]; then
  echo "WARN: right now $_big looks like $_bigcapv of $_bigarch to the placer while" \
       "$_oth looks like $_othcapv of $_otharch: the biggest core in the SoC is not" \
       "the biggest core on offer, so nothing light will be put on it."
  echo "      Its ceiling is $_big's own scaling_max_freq -- a userspace profile that" \
      "writes that node is choosing the placement, not just the frequency."
  echo "      (snapshot only; --sample says how much of the time this holds)"
fi

# --- 3. statistics over a real workload ------------------------------------
if [ "$SAMPLE" -gt 0 ]; then
  SAY ""
  SAY "sampling ${SAMPLE}s"
  # The park/lock distinction needs to know whether anything wanted to run during
  # the window, and /proc/loadavg cannot answer that: its 1-minute average lags
  # reality by minutes, so any burst just past the window reads as "under load"
  # and turns a parked cluster into a frozen one.  Measure the run queue instead
  # -- aggregate busy time between the first and last poll -- and keep loadavg
  # only as context in the output.
  _load="$(cut -d' ' -f1 "$PROC_ROOT/loadavg" 2>/dev/null || echo 0)"
  _cpu0="$(cpu_totals)"
  : > "$SAMPLES"
  _polls=0
  _inv=0
  _inv_worst=0
  _inv_case=""
  _end=$(( $(now) + SAMPLE ))
  while [ "$(now)" -lt "$_end" ]; do
    _pbig=""; _pbigarch=0; _pbigcapv=0
    _poth=""; _potharch=0; _pothcapv=0
    for _d in $POLICY_LIST; do
      _n="${_d##*/}"
      _c="$(rd "$_d/scaling_cur_freq")"
      _t="$(rd "$_d/stats/total_trans")"
      _mn="$(rd "$_d/scaling_min_freq")"
      _mx="$(rd "$_d/scaling_max_freq")"
      _kv="$(const_for "$_n")"
      _cmn="${_kv%%:*}"
      _r="${_kv#*:}"
      _cinfo="${_r%%:*}"
      _carch="${_r##*:}"
      _ccapv="$(cap_view "${_carch:-0}" "$_mx" "${_cinfo:-0}")"
      printf '%s %s %s %s %s %s %s %s %s\n' "$_n" "$_c" "$_t" "$_mn" "$_mx" \
        "$_cmn" "$_cinfo" "$(now)" "${_carch:-0}" >> "$SAMPLES"
      [ "${_ccapv:-0}" -gt 0 ] || continue
      if [ "${_carch:-0}" -gt "$_pbigarch" ]; then
        if [ -n "$_pbig" ] && [ "$_pbigcapv" -gt "$_pothcapv" ]; then
          _poth="$_pbig"; _potharch="$_pbigarch"; _pothcapv="$_pbigcapv"
        fi
        _pbig="$_d"; _pbigarch="$_carch"; _pbigcapv="$_ccapv"
      elif [ "$_ccapv" -gt "$_pothcapv" ]; then
        _poth="$_d"; _potharch="$_carch"; _pothcapv="$_ccapv"
      fi
    done
    _polls=$(( _polls + 1 ))
    if [ -n "$_pbig" ] && [ -n "$_poth" ] && [ "$_pothcapv" -ge "$_pbigcapv" ]; then
      _inv=$(( _inv + 1 ))
      _gap=$(( _pothcapv - _pbigcapv ))
      if [ "$_gap" -ge "$_inv_worst" ]; then
        _inv_worst="$_gap"
        _inv_case="${_pbig##*/}=$_pbigcapv/${_pbigarch}k vs ${_poth##*/}=$_pothcapv/${_potharch}k"
      fi
    fi
    sleep 0.2
  done
  _cpu1="$(cpu_totals)"
  _b0="${_cpu0%% *}"; _r="${_cpu0#* }"; _t0="${_r%% *}"
  _b1="${_cpu1%% *}"; _r="${_cpu1#* }"; _t1="${_r%% *}"
  _busy=0
  case "$_b0$_t0$_b1$_t1" in ''|*[!0-9]*) : ;; *)
    if [ $(( _t1 - _t0 )) -gt 0 ]; then
      _busy=$(( (_b1 - _b0) * 100 / (_t1 - _t0) ))
    fi ;;
  esac
  SAY "window: $_polls polls, machine busy ${_busy}% (loadavg $_load)"
  awk -v maxmode="$MAX_MODE" -v minrate="$MIN_RATE" -v busy="$_busy" -v minbusy="$MIN_BUSY" '
    {
      p = $1; f = $2 + 0; tr = $3 + 0
      cnt[p SUBSEP f]++; tot[p]++; seen[p] = 1
      if (!(p in first_tr)) { first_tr[p] = tr; first_t[p] = $8 + 0 }
      last_tr[p] = tr; last_t[p] = $8 + 0
      if (f > 0 && (hmin[p] == 0 || f < hmin[p])) hmin[p] = f
      if (f > hmax[p]) hmax[p] = f
      if (cmin[p] == 0) cmin[p] = $6 + 0
      if (cmax[p] == 0) cmax[p] = $7 + 0
      if ($4 != "" && $4 == $5) pinned[p]++
      # What the ceiling did to the capacity the placer sees: this is the number
      # a userspace limiter is really editing.
      cl = $5 + 0
      if (cl > 0) cen[p SUBSEP cl]++
      if ($9 + 0 > 0 && $7 + 0 > 0) {
        # Exact, the same product as cap_view() and the runtime module report:
        # this column exists to be compared against rq_cpu_capacity_orig.  (No
        # apostrophes in here: a quote inside this single-quoted program ends it,
        # and only the device shell -- mksh -- reliably rejects that.)
        cv = int($9 * cl / $7)
        if (capmin[p] == 0 || cv < capmin[p]) capmin[p] = cv
        if (cv > capmax[p]) capmax[p] = cv
      }
    }
    END {
      bad = 0
      for (p in seen) {
        d = 0; best = 0
        for (k in cnt) {
          split(k, a, SUBSEP)
          if (a[1] == p) { d++; if (cnt[k] > best) best = cnt[k] }
        }
        dc = 0
        for (k in cen) { split(k, ca, SUBSEP); if (ca[1] == p) dc++ }
        mode = 100 * best / tot[p]
        win = last_t[p] - first_t[p]; if (win <= 0) win = 1
        rate = (last_tr[p] - first_tr[p]) / win
        # Sitting still has several causes and only one of them is a lock:
        # parked at the bottom of the table is idle, and a quiet workload still
        # transitions.  Holding one point *above* the floor while barely
        # transitioning is the ratchet signature -- but only if something
        # wanted to run; with an empty run queue it is a park, and --probe is
        # what tells a park from a lock.
        v = "MOVING"
        if (d == 1 && hmin[p] <= cmin[p]) v = "IDLE"
        else if (d <= 2 && mode >= maxmode && rate < minrate)
            v = (busy + 0 < minbusy + 0) ? "PARKED_IDLE" : "FROZEN_UNDER_LOAD"
        printf "%-9s points=%-3d mode=%5.1f%% range=%d-%d floor=%d ceil=%d trans/s=%4.1f pinned=%d/%d ceilings=%-2d cap_view=%d-%d  %s\n",
               p, d, mode, hmin[p], hmax[p], cmin[p], cmax[p], rate,
               pinned[p] + 0, tot[p], dc, capmin[p], capmax[p], v
        if (v == "FROZEN_UNDER_LOAD") bad++
        if (v == "PARKED_IDLE") parked++
      }
      if (!bad) print "verdict: no policy is frozen above its floor while work is running"
      else printf "note: %d policy/policies held one point with the machine %d%% busy. A ceiling\n" \
                  "      holder that pins min == max produces this picture with no bug anywhere: run\n" \
                  "      --probe, and compare cap_view against the profile that owns the node.\n", bad, busy
      if (parked) printf "note: %d policy/policies parked high with the machine %d%% busy" \
                         " (<%d%%): a FAS owner that was never asked to re-evaluate." \
                         " Run --probe to tell a park from a lock.\n", parked, busy, minbusy
      exit(bad ? 1 : 0)
    }' "$SAMPLES" || flag 1
  # Was the super core the biggest core on offer for the whole window, or only
  # for a slice of it?  A ceiling that ramps (a rate-limited profile climbing out
  # of its idle table) inverts the ranking exactly when a launch needs the core
  # most, which is why a snapshot can look fine while the phone still feels slow.
  if [ "$_polls" -gt 0 ]; then
    _invpct=$(( _inv * 100 / _polls ))
    if [ "$_inv" = "0" ]; then
      SAY "cap inversion: 0/$_polls polls -- the biggest core stayed the biggest core on offer"
    elif [ "$_invpct" -ge "$INVERT_PCT" ]; then
      echo "FAIL: in $_inv/$_polls polls ($_invpct%) the super core had no more capacity in the" \
           "placer's eyes than a weaker cluster (worst: $_inv_case)."
      echo "      WALT/EAS scale cpu_capacity by scaling_max_freq, so whoever owns that node is" \
           "choosing where light work runs, not only how fast it runs."
      echo "      Fix: raise the super core's ceiling in that profile until its scaled capacity" \
           "beats the mid cluster's, or stop the profile from writing policy*/scaling_max_freq."
      flag 4
    else
      SAY "cap inversion: $_inv/$_polls polls ($_invpct%, worst $_inv_case)" \
        "-- transient, under --invert-pct $INVERT_PCT"
    fi
  fi
  rm -f "$SAMPLES"
fi

# --- 4. active probe: load, then decay ------------------------------------
if [ "$PROBE" = "1" ]; then
  SAY ""
  SAY "probing: ${LOAD_SECS}s all-core load, then ${DECAY_SECS}s of decay"
  _pids=""
  _i=0
  while [ "$_i" -lt 8 ]; do
    sh -c 'while :; do :; done' & _pids="$_pids $!"
    _i=$((_i + 1))
  done
  sleep "$LOAD_SECS"
  _peak=""
  for _d in $POLICY_LIST; do _peak="$_peak ${_d##*/}=$(rd "$_d/scaling_cur_freq")"; done
  for _p in $_pids; do kill "$_p" 2>/dev/null || true; done
  SAY "  peak$_peak"
  _stuck=""
  _end=$(( $(now) + DECAY_SECS ))
  while [ "$(now)" -lt "$_end" ]; do
    _still=""
    for _d in $POLICY_LIST; do
      _c="$(rd "$_d/scaling_cur_freq")"
      _m="$(rd "$_d/cpuinfo_max_freq")"
      [ -n "$_c" ] && [ -n "$_m" ] || continue
      # A ceiling is only a lock if nothing is running and it never leaves.
      if [ "$_c" -gt $(($_m - _m / 20)) ]; then _still="$_still ${_d##*/}"; fi
    done
    [ -n "$_still" ] || { _stuck=""; break; }
    _stuck="$_still"
    sleep 1
  done
  _after=""
  for _d in $POLICY_LIST; do _after="$_after ${_d##*/}=$(rd "$_d/scaling_cur_freq")"; done
  SAY "  decayed to$_after"
  if [ -n "$_stuck" ]; then
    echo "FAIL: still at the ceiling with nothing running:$_stuck"
    echo "      Check abk_sf_enable first, then which profile wrote the caps."
    flag 3
  else
    SAY "  verdict: DVFS still owns the frequency (it falls back when idle)"
  fi
fi

echo ""
case "$RC" in
  0) echo "ABK FAS CHECK OK" ;;
  1) echo "ABK FAS CHECK: at least one policy does not look FAS-driven" ;;
  3) echo "ABK FAS CHECK: no decay after load" ;;
  4) echo "ABK FAS CHECK: the super core is being capped out of the placement decision" ;;
esac
exit "$RC"
