#!/system/bin/sh
# common.sh - sailboat addon 2 (ABK cpufreq/scheduler runtime tunables).
# Sourced by post-fs-data.sh / service.sh / action.sh.  POSIX sh only:
# /system/bin/sh is Android's mksh and there is no bash on the device.
#
# The helpers below are a deliberate copy of the subset this module needs -- a
# KernelSU module is flashed on its own and may not read another module's files,
# so the duplication is what makes the two modules independent.

ABK_TAG="ABK-Sched"
# Has to track module.prop: this banner is what the module manager shows, so a
# drift between the two makes action.sh report a version the manager never lists.
ABK_VERSION="v0.1.0"

# Redirectable device paths (test seams), not user-facing knobs.
ABK_SYS_ROOT="${ABK_SYS_ROOT:-/sys}"
# /proc/fas reports who owns DVFS; read-only here, and absent on a non-FAS
# kernel, which is a state to report rather than an error.
ABK_FAS_NODE="${ABK_FAS_NODE:-/proc/fas}"

MODDIR="${MODDIR:-${0%/*}}"
ABK_STATE_DIR="${ABK_STATE_DIR:-$MODDIR/state}"
ABK_RUN_DIR="${ABK_RUN_DIR:-$MODDIR/run}"
ABK_CONF="${ABK_CONF:-$MODDIR/tunables.conf}"
ABK_STDOUT="${ABK_STDOUT:-0}"

abk_report_stdout() {
  if [ "$ABK_STDOUT" = "1" ]; then
    echo "$*"
  fi
  return 0
}

# A boot that fails invisibly is the one thing this module cannot afford, so it
# keeps its own log (logcat is best-effort on some ROMs).
ABK_LOG_MAX_BYTES=65536

abk_log_file() {
  printf '%s/%s\n' "$ABK_STATE_DIR" "abk_sched_tunables.log"
}

abk_log_append() {
  _la_level="$1"
  _la_message="$2"
  _la_file="$(abk_log_file)"
  mkdir -p "$ABK_STATE_DIR" 2>/dev/null || true

  # -f guard: `< file` on a missing file prints the shell's own error into the
  # middle of the first boot.
  if [ -f "$_la_file" ]; then
    _la_size="$(wc -c < "$_la_file" 2>/dev/null | tr -d ' ')"
  else
    _la_size=0
  fi
  case "$_la_size" in
    ''|*[!0-9]*) _la_size=0 ;;
  esac
  if [ "$_la_size" -gt "$ABK_LOG_MAX_BYTES" ]; then
    tail -n 200 "$_la_file" > "$_la_file.tmp" 2>/dev/null \
      && mv "$_la_file.tmp" "$_la_file" 2>/dev/null
  fi

  printf '%s %s %s\n' "$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)" \
    "$_la_level" "$_la_message" >> "$_la_file" 2>/dev/null || true
  return 0
}

# report.logcat=0 silences the logcat mirror (the module log and action.sh stay).
abk_logcat_on() {
  if [ -z "${ABK_LOGCAT_ON:-}" ]; then
    ABK_LOGCAT_ON="$(abk_cfg report.logcat 1)"
  fi
  [ "$ABK_LOGCAT_ON" != "0" ]
}

abk_log() {
  abk_log_append INFO "$*"
  if abk_logcat_on; then
    log -t "$ABK_TAG" "$*" 2>/dev/null || true
  fi
  abk_report_stdout "$*"
  return 0
}

abk_warn() {
  abk_log_append WARN "$*"
  if abk_logcat_on; then
    log -t "$ABK_TAG" "WARN: $*" 2>/dev/null || true
  fi
  abk_report_stdout "WARN: $*"
  return 0
}

abk_read() {
  cat "$1" 2>/dev/null || true
}

abk_is_uint() {
  case "$1" in
    ''|*[!0-9]*) return 1 ;;
    *) return 0 ;;
  esac
}

# abk_clamp_uint <value> <min> <max> -> clamped value, or non-zero if not a uint
abk_clamp_uint() {
  _cu_v="$1"
  abk_is_uint "$_cu_v" || return 1
  [ "$_cu_v" -ge "$2" ] || _cu_v="$2"
  [ "$_cu_v" -le "$3" ] || _cu_v="$3"
  printf '%s\n' "$_cu_v"
  return 0
}

# abk_mul_div <a> <b> <c> -> a * b / c, 64-bit.
# mksh wraps at 2^31 (measured: `15561024 * 1024` -> -1245380608) while
# KernelSU's busybox ash does not, and this module's DMIPS capacity x
# cpuinfo.max_freq (1024 x 3187200) is past that limit.  awk is 64-bit
# everywhere.  "%.0f" rather than "%d": the latter casts through the awk build's
# int and once clamped a byte count to 2147483647 on a sibling module.
abk_mul_div() {
  awk -v a="$1" -v b="$2" -v c="$3" \
    'BEGIN { printf "%.0f\n", a * b / c }' 2>/dev/null
}

# abk_write <path> <value>: write a whole line, report but never abort
abk_write() {
  _w_path="$1"
  _w_val="$2"
  if [ ! -e "$_w_path" ]; then
    abk_warn "node missing: $_w_path"
    return 1
  fi
  if ! echo "$_w_val" > "$_w_path" 2>/dev/null; then
    abk_warn "write failed: $_w_path <- $_w_val"
    return 1
  fi
  return 0
}

# --- tunables.conf --------------------------------------------------------
# Only the scheduling keys: the zram/vm/psi keys belong to sailboat addon 1.
# An unrecognised key is reported once and ignored, so a typo changes nothing.
abk_known_keys() {
  cat <<'EOF'
sched.abk_governor
sched.abk_governor_interval_sec
sched.abk_sf_enable
sched.abk_sf_floor_pct
sched.abk_sf_sustained_ms
sched.abk_sf_exit_ms
sched.abk_sc_enable
sched.abk_sc_cap_pct
sched.abk_sc_hold_ms
sched.abk_sc_release_pct
sched.abk_sc_release_ms
report.logcat
EOF
}

# abk_cfg <key> <default>: value from tunables.conf, else the default.  An
# empty value means "leave the kernel alone", which is the sched default.
abk_cfg() {
  _c_key="$1"
  _c_default="$2"
  if [ -f "$ABK_CONF" ]; then
    _c_val="$(awk -v k="$_c_key" '
      /^[ \t]*#/ { next }
      {
        line = $0
        sub(/^[ \t]+/, "", line)
        sub(/[ \t]+$/, "", line)
        if (line == "") next
        eq = index(line, "=")
        if (eq == 0) next
        key = substr(line, 1, eq - 1)
        sub(/[ \t]+$/, "", key)
        if (key != k) next
        v = substr(line, eq + 1)
        sub(/^[ \t]+/, "", v)
        sub(/[ \t]+$/, "", v)
        print v
        exit
      }' "$ABK_CONF" 2>/dev/null)"
    if [ -n "$_c_val" ]; then
      printf '%s\n' "$_c_val"
      return 0
    fi
  fi
  printf '%s\n' "$_c_default"
  return 0
}

# abk_cfg_lint: warn about lines/keys the module will not honour
abk_cfg_lint() {
  if [ ! -f "$ABK_CONF" ]; then
    abk_warn "no tunables.conf; using built-in defaults"
    return 0
  fi
  mkdir -p "$ABK_RUN_DIR" 2>/dev/null || true
  abk_known_keys > "$ABK_RUN_DIR/.abk_known_keys" 2>/dev/null || true
  awk '
    /^[ \t]*#/ { next }
    /^[ \t]*$/ { next }
    {
      line = $0
      sub(/^[ \t]+/, "", line); sub(/[ \t]+$/, "", line)
      eq = index(line, "=")
      if (eq == 0) { print "MALFORMED " line; next }
      key = substr(line, 1, eq - 1)
      sub(/[ \t]+$/, "", key)
      print "KEY " key
    }' "$ABK_CONF" 2>/dev/null | while read -r tag value; do
      case "$tag" in
        MALFORMED) abk_warn "tunables.conf: ignoring '$value' (no key=value)" ;;
        KEY)
          if ! grep -qx "$value" "$ABK_RUN_DIR/.abk_known_keys" 2>/dev/null; then
            abk_warn "tunables.conf: unknown key '$value' ignored"
          fi
          ;;
      esac
    done
  return 0
}

# --- state ---------------------------------------------------------------
abk_state_path() {
  printf '%s/%s\n' "$ABK_STATE_DIR" "$1"
}

abk_state_get() {
  cat "$(abk_state_path "$1")" 2>/dev/null || true
}

abk_state_set() {
  printf '%s\n' "$2" > "$(abk_state_path "$1")" 2>/dev/null || true
}

# --- pid files and supervision -------------------------------------------
abk_pid_alive() {
  [ -n "$1" ] && [ -d "/proc/$1" ]
}

abk_pid_live() {
  _pl="$(abk_state_get "$1.pid")"
  abk_pid_alive "$_pl"
}

abk_pid_write() {
  mkdir -p "$ABK_STATE_DIR" 2>/dev/null || true
  printf '%s\n' "$2" > "$(abk_state_path "$1.pid")" 2>/dev/null || true
}

# --- the pieces this module owns -------------------------------------------
# Moved out of the ABK runtime tunables module unchanged, so the measured device
# facts in these comments travel with the code.


abk_apply_sched_knobs() {
  _sk_dir="$ABK_SYS_ROOT/module/cpufreq_schedutil/parameters"
  [ -d "$_sk_dir" ] || return 0

  _sk_val="$(abk_cfg sched.abk_sf_enable '')"
  if [ -n "$_sk_val" ]; then
    case "$_sk_val" in
      0|1)
        abk_write "$_sk_dir/abk_sf_enable" "$_sk_val" \
          && abk_log "abk_sf_enable=$_sk_val"
        ;;
      *) abk_warn "sched.abk_sf_enable: '$_sk_val' is not 0|1, ignored" ;;
    esac
  fi

  _sk_val="$(abk_cfg sched.abk_sf_floor_pct '')"
  if [ -n "$_sk_val" ]; then
    if _sk_c="$(abk_clamp_uint "$_sk_val" 0 100)"; then
      abk_write "$_sk_dir/abk_sf_floor_pct" "$_sk_c" \
        && abk_log "abk_sf_floor_pct=$_sk_c"
    else
      abk_warn "sched.abk_sf_floor_pct: '$_sk_val' is not a number, ignored"
    fi
  fi

  _sk_val="$(abk_cfg sched.abk_sf_sustained_ms '')"
  if [ -n "$_sk_val" ]; then
    if _sk_c="$(abk_clamp_uint "$_sk_val" 1 60000)"; then
      abk_write "$_sk_dir/abk_sf_sustained_ms" "$_sk_c" \
        && abk_log "abk_sf_sustained_ms=$_sk_c"
    else
      abk_warn "sched.abk_sf_sustained_ms: '$_sk_val' is not a number, ignored"
    fi
  fi

  _sk_val="$(abk_cfg sched.abk_sf_exit_ms '')"
  if [ -n "$_sk_val" ]; then
    if _sk_c="$(abk_clamp_uint "$_sk_val" 1 60000)"; then
      abk_write "$_sk_dir/abk_sf_exit_ms" "$_sk_c" \
        && abk_log "abk_sf_exit_ms=$_sk_c"
    else
      abk_warn "sched.abk_sf_exit_ms: '$_sk_val' is not a number, ignored"
    fi
  fi

  # --- Batch 42: the smart_freq *cap* -----------------------------------
  # Same shape as the floor above, and the same reason it is stated off in
  # tunables.conf: the kernel ships it off, and arming it on a device whose
  # governor is not schedutil is a no-op by construction (abk_sc_owns()
  # refuses), so a companion that armed it would only be arming a lie.
  #
  # abk_sc_entry_pct has no knob here on purpose.  It feeds nothing but the
  # read-only abk_sc_boosting reason election -- it gates no decision of its
  # own -- so a writable copy would invite someone to chase the floor's old
  # 70-90% dead band by raising a number that cannot move it.  Read it with
  # action.sh status if the election matters; the clamp is tuned by cap_pct
  # and the two release windows below.
  _sk_val="$(abk_cfg sched.abk_sc_enable '')"
  if [ -n "$_sk_val" ]; then
    case "$_sk_val" in
      0|1)
        abk_write "$_sk_dir/abk_sc_enable" "$_sk_val" \
          && abk_log "abk_sc_enable=$_sk_val"
        ;;
      *) abk_warn "sched.abk_sc_enable: '$_sk_val' is not 0|1, ignored" ;;
    esac
  fi

  _sk_val="$(abk_cfg sched.abk_sc_cap_pct '')"
  if [ -n "$_sk_val" ]; then
    if _sk_c="$(abk_clamp_uint "$_sk_val" 1 100)"; then
      abk_write "$_sk_dir/abk_sc_cap_pct" "$_sk_c" \
        && abk_log "abk_sc_cap_pct=$_sk_c"
    else
      abk_warn "sched.abk_sc_cap_pct: '$_sk_val' is not a number, ignored"
    fi
  fi

  _sk_val="$(abk_cfg sched.abk_sc_hold_ms '')"
  if [ -n "$_sk_val" ]; then
    if _sk_c="$(abk_clamp_uint "$_sk_val" 1 60000)"; then
      abk_write "$_sk_dir/abk_sc_hold_ms" "$_sk_c" \
        && abk_log "abk_sc_hold_ms=$_sk_c"
    else
      abk_warn "sched.abk_sc_hold_ms: '$_sk_val' is not a number, ignored"
    fi
  fi

  _sk_val="$(abk_cfg sched.abk_sc_release_pct '')"
  if [ -n "$_sk_val" ]; then
    if _sk_c="$(abk_clamp_uint "$_sk_val" 1 100)"; then
      abk_write "$_sk_dir/abk_sc_release_pct" "$_sk_c" \
        && abk_log "abk_sc_release_pct=$_sk_c"
    else
      abk_warn "sched.abk_sc_release_pct: '$_sk_val' is not a number, ignored"
    fi
  fi

  _sk_val="$(abk_cfg sched.abk_sc_release_ms '')"
  if [ -n "$_sk_val" ]; then
    if _sk_c="$(abk_clamp_uint "$_sk_val" 1 60000)"; then
      abk_write "$_sk_dir/abk_sc_release_ms" "$_sk_c" \
        && abk_log "abk_sc_release_ms=$_sk_c"
    else
      abk_warn "sched.abk_sc_release_ms: '$_sk_val' is not a number, ignored"
    fi
  fi

  return 0
}

# --- the governor every policy is held on ---------------------------------
# Measured on vermeer / android13-5.15 5.15.216 with SELinux Enforcing.  The
# policy starts on walt and scaling_governor is 0444 root:root, so a root
# shell's write is refused with EACCES -- while `chmod 0644` on the same node by
# the same root is accepted, and the write then succeeds.  The owner stays root,
# so 0644 hands nothing to a non-root caller; it is the same permission the ROM
# itself puts on this node when it is driving the governor (its own userspace
# has to be able to switch to schedutil in a game).  Nothing here relaxes
# SELinux, which is the one thing this module refuses to do -- see
# sepolicy.rule's note.
#
# A kernel module cannot do this instead: the cpufreq symbols the GKI exports
# to an out-of-tree module include cpufreq_register_governor, cpufreq_get_policy
# and cpufreq_update_policy but no governor *switch* (cpufreq_set_policy is not
# exported), and cpufreq.c is built into vmlinux, so the graft cannot reach it.
# The sysfs node is the only door.
#
# What the chmod buys is the write, not the outcome: the vendor rewrites walt
# later, on a trigger that has not been identified (it happened at least once
# inside 15 minutes and did not happen during a 60 s watch, and the node's
# permissions were still the 0644 this module had left behind at the time -- so
# the value is rewritten without the node being re-locked).  That is why there is
# a supervisor and not just a boot pass.

# abk_gov_wanted: the governor to hold everywhere, empty for "leave it alone".
abk_gov_wanted() {
  # abk_cfg's awk strips spaces and tabs but not a carriage return, and a
  # tunables.conf edited on Windows carries them: "schedutil\r" equals no
  # governor name, so every round would rewrite the node and never match, and
  # the log would fill with failures that have no cause on the device.  The
  # shipped file is LF (pinned in the test suite), so this is a net for an
  # operator's edit rather than a fix for a shipped defect.
  abk_cfg sched.abk_governor '' | tr -d '\r'
}

# abk_gov_interval: seconds between re-asserts.  Clamped, because a 0 or an
# empty line would otherwise turn into `sleep` with no argument.
abk_gov_interval() {
  _gi="$(abk_cfg sched.abk_governor_interval_sec 30 | tr -d '\r')"
  if ! _gi="$(abk_clamp_uint "$_gi" 5 3600)"; then
    _gi=30
  fi
  printf '%s\n' "$_gi"
}

# The policy directories, space separated, empty before cpufreq has sysfs.
# post-fs-data runs before some vendor bring-up, and an unexpanded glob is a
# literal path a `for` would then iterate as if it existed.
abk_gov_policies() {
  _gp_out=""
  for _gp_d in "$ABK_SYS_ROOT"/devices/system/cpu/cpufreq/policy*; do
    [ -d "$_gp_d" ] && _gp_out="$_gp_out $_gp_d"
  done
  printf '%s\n' "${_gp_out# }"
}

# abk_gov_set_one <policy-dir> <wanted> -> one status word on stdout:
#   same | set | no node | not offered | locked | refused | verify failed
# Returns 0 only when the policy ends up on the wanted governor.
#
# No logging here: the caller captures this function's stdout, and abk_log
# writes there too when ABK_STDOUT=1.
abk_gov_set_one() {
  _gs_dir="$1"
  _gs_want="$2"
  _gs_node="$_gs_dir/scaling_governor"
  _gs_name="${_gs_dir##*/}"

  if [ ! -e "$_gs_node" ]; then
    printf 'no node\n'
    return 1
  fi

  # Is the governor offered here at all?  A typo in tunables.conf, or a kernel
  # built without schedutil, would otherwise be re-written every interval and
  # never match -- a supervisor that logs a failure every 30 s for a reason it
  # could have said once.
  _gs_avail="$(abk_read_flat "$_gs_dir/scaling_available_governors")"
  case " $_gs_avail " in
    *" $_gs_want "*) : ;;
    *) printf 'not offered by %s\n' "$_gs_name"; return 1 ;;
  esac

  if [ "$(abk_read_flat "$_gs_node")" = "$_gs_want" ]; then
    printf 'same\n'
    return 0
  fi

  # The node the policy ships is 0444 and refuses a root write; chmod opens it.
  # Held here rather than applied unconditionally, so a ROM that already runs
  # the governor writable is never touched.
  if ! echo "$_gs_want" > "$_gs_node" 2>/dev/null; then
    if ! chmod 0644 "$_gs_node" 2>/dev/null; then
      printf 'locked\n'
      return 1
    fi
    if ! echo "$_gs_want" > "$_gs_node" 2>/dev/null; then
      printf 'refused\n'
      return 1
    fi
    printf 'unlocked+set\n'
  else
    printf 'set\n'
  fi

  # Read back.  A write that reports success and changes nothing is the failure
  # mode that makes this feature look armed while the device is still on walt,
  # and the only way to tell the two apart is to ask the node again.
  if [ "$(abk_read_flat "$_gs_node")" != "$_gs_want" ]; then
    printf 'verify failed\n'
    return 1
  fi
  return 0
}

# abk_gov_enforce: hold every policy on the wanted governor.  Always prints one
# compact line, in this module's log-line idiom:
#   ok=3 tot=3 set=0        (nothing to do)
#   ok=3 tot=3 set=3        (wrote all of them)
#   ok=2 tot=3 set=1 bad=1 why:policy7=refused
# Only the writes and the readbacks happen here; every human-readable line the
# caller wants is logged from the line this prints.
abk_gov_enforce() {
  _ge_want="$(abk_gov_wanted)"
  if [ -z "$_ge_want" ]; then
    printf 'disabled\n'
    return 0
  fi
  _ge_policies="$(abk_gov_policies)"
  if [ -z "$_ge_policies" ]; then
    printf 'no cpufreq sysfs\n'
    return 1
  fi

  _ge_tot=0
  _ge_ok=0
  _ge_set=0
  _ge_bad=0
  _ge_why=""
  for _ge_d in $_ge_policies; do
    _ge_tot=$((_ge_tot + 1))
    _ge_st="$(abk_gov_set_one "$_ge_d" "$_ge_want")"
    case "$_ge_st" in
      same)
        _ge_ok=$((_ge_ok + 1))
        ;;
      set|unlocked+set)
        _ge_ok=$((_ge_ok + 1))
        _ge_set=$((_ge_set + 1))
        ;;
      *)
        _ge_bad=$((_ge_bad + 1))
        _ge_why="$_ge_why ${_ge_d##*/}=$_ge_st"
        ;;
    esac
  done

  printf 'ok=%s tot=%s set=%s bad=%s' "$_ge_ok" "$_ge_tot" "$_ge_set" "$_ge_bad"
  [ -n "$_ge_why" ] && printf ' why:%s' "${_ge_why# }"
  printf '\n'
  [ "$_ge_bad" -eq 0 ] || return 1
  return 0
}

# abk_gov_state_line: what is in force, per policy, for the boot report and for
# action.sh status.  Deliberately reads the nodes rather than echoing the
# setting -- the setting is an intention, this is what the device says.
abk_gov_state_line() {
  _gl_want="$(abk_gov_wanted)"
  _gl_out="want=${_gl_want:-none}"
  for _gl_d in $(abk_gov_policies); do
    _gl_out="$_gl_out ${_gl_d##*/}=$(abk_read_flat "$_gl_d/scaling_governor")"
  done
  printf '%s\n' "$_gl_out"
}

# abk_gov_supervisor_main: re-assert the governor for as long as the device is
# up.  Logs only when the line changes, so a device that stays on schedutil
# produces one boot line and then silence, while a vendor that fights produces
# one line per change.
#
# Stops instead of spinning when the writes cannot work at all -- after three
# consecutive rounds where every policy refused, a graph would look identical:
# a locked node or an SELinux denial does not start working later.  This is the
# rule abk_psi_supervisor_main already follows for the same reason.
abk_gov_supervisor_main() {
  abk_pid_write gov "$$"
  _gm_interval="$(abk_gov_interval)"
  _gm_want="$(abk_gov_wanted)"
  abk_log "governor supervisor up: want=${_gm_want:-none} interval=${_gm_interval}s"
  _gm_prev=""
  _gm_streak=0
  while :; do
    _gm_line="$(abk_gov_enforce)"
    if [ "$_gm_line" != "$_gm_prev" ]; then
      abk_log "gov: $_gm_line"
      _gm_prev="$_gm_line"
    fi
    case "$_gm_line" in
      ok=0\ tot=0\ *) : ;;
      *" bad=0"*) _gm_streak=0 ;;
      *)
        # bad=N with N>0: only a full refusal (every policy) counts towards
        # stopping.  One cluster refusing while the others hold is worth
        # retrying, because a single policy can come and go with CPU hotplug.
        case "$_gm_line" in
          ok=0\ tot=[0-9]*\ *) _gm_streak=$((_gm_streak + 1)) ;;
        esac
        ;;
    esac
    if [ "$_gm_streak" -ge 3 ]; then
      abk_warn "governor: every policy refused the write three rounds running; stopping the supervisor. The device keeps whichever governor the ROM owns -- set sched.abk_governor= to stop asking. ($_gm_line)"
      return 0
    fi
    sleep "$_gm_interval"
  done
}

# abk_read_flat <path>: single-line node value, newline stripped.
abk_read_flat() {
  abk_read "$1" | tr -d '\n'
}

# --- DVFS ownership report --------------------------------------------------
# Whether FAS is driving, and whether this module's own smart-freq floor is
# staying out of its way, cannot be read off one node: a FAS owner keeps
# scaling_min_freq == scaling_max_freq == its current target, so a healthy
# cluster and a locked one look identical in a snapshot, and the cpufreq nodes
# are themselves permission-managed at runtime by whichever scheduler profile
# is in play.  Record the ownership picture once per boot so "the frequency
# stopped moving" starts from something in the log; the on-demand,
# verdict-carrying version of the same look (including an active load/decay
# probe) ships as bin/abk_fas_check.sh.
#
# The same pass scales each cluster's DMIPS capacity by its own ceiling, because
# that is the number EAS/WALT rank cores by: a profile that edits
# scaling_max_freq therefore also edits where light work is *placed*, and a
# cluster logged as no bigger than a weaker one explains "the super core is never
# used for anything" without any scheduler bug being involved.
abk_report_dvfs_state() {
  _rs_sf="$ABK_SYS_ROOT/module/cpufreq_schedutil/parameters"
  _rs_policies=""
  for _rs_d in "$ABK_SYS_ROOT"/devices/system/cpu/cpufreq/policy*; do
    [ -d "$_rs_d" ] && _rs_policies="$_rs_policies $_rs_d"
  done
  [ -n "$_rs_policies" ] || return 0

  _rs_fas="$(abk_read_flat "$ABK_FAS_NODE")"
  _rs_enable="$(abk_read_flat "$_rs_sf/abk_sf_enable")"
  _rs_boost="$(abk_read_flat "$_rs_sf/abk_sf_boosting")"
  # Batch 42 added the cap on the same hook and in the same file as the floor,
  # so its two nodes are the other half of the same picture: abk_sc_capped is
  # per-policy and says whose target is being suppressed right now, which is the
  # only way to tell "the cap is armed and idle" from "the cap is holding this
  # cluster" without reading frequencies and guessing.
  _rs_sc_enable="$(abk_read_flat "$_rs_sf/abk_sc_enable")"
  _rs_sc_capped="$(abk_read_flat "$_rs_sf/abk_sc_capped")"
  abk_log "dvfs: fas=${_rs_fas:-no /proc/fas} abk_sf_enable=${_rs_enable:-absent} abk_sf_boosting=${_rs_boost:-absent} abk_sc_enable=${_rs_sc_enable:-absent} abk_sc_capped=${_rs_sc_capped:-absent}"
  # What the device reports each policy's governor to be, against what this
  # module is holding.  The snapshot below is taken at post-fs-data, before any
  # vendor profile has switched anything, so on its own it reads "walt" even on
  # a device that runs schedutil all day; this line is what the earlier "
  # walt at boot, schedutil in a game" reasoning was reconstructed from by hand.
  # Read-only on purpose -- this whole function is the boot snapshot, and the
  # write that produced it was already logged by abk_apply_early_knobs().
  abk_log "gov: $(abk_gov_state_line)"

  _rs_foreign=0
  _rs_pinned=0
  _rs_big=""; _rs_bigarch=0; _rs_bigcapv=0
  _rs_oth=""; _rs_othcapv=0
  for _rs_p in $_rs_policies; do
    _rs_gov="$(abk_read_flat "$_rs_p/scaling_governor")"
    _rs_max="$(abk_read_flat "$_rs_p/scaling_max_freq")"
    _rs_imax="$(abk_read_flat "$_rs_p/cpuinfo_max_freq")"
    # The same glob trick reaches the DMIPS capacity of the policy's first cpu,
    # and scaling_max_freq/cpuinfo_max_freq turns it into the capacity EAS and
    # WALT actually rank cores by: a profile that writes that ceiling is editing
    # placement, not only frequency.
    _rs_cpu="$(cut -d' ' -f1 "$_rs_p/affected_cpus" 2>/dev/null)"
    _rs_arch=""
    [ -n "$_rs_cpu" ] && _rs_arch="$(abk_read_flat "$ABK_SYS_ROOT/devices/system/cpu/cpu$_rs_cpu/cpu_capacity")"
    _rs_capv=0
    _rs_ok=1
    for _rs_v in "$_rs_arch" "$_rs_max" "$_rs_imax"; do
      case "$_rs_v" in ''|*[!0-9]*) _rs_ok=0 ;; esac
    done
    # 855 * 2803200 is 2.4e9, past this ROM's mksh arithmetic, so the scaling goes
    # through abk_mul_div like every other product in this module.
    if [ "$_rs_ok" = "1" ] && [ "$_rs_imax" -gt 0 ]; then
      _rs_capv="$(abk_mul_div "$_rs_arch" "$_rs_max" "$_rs_imax")"
    fi
    _rs_min="$(abk_read_flat "$_rs_p/scaling_min_freq")"
    abk_log "dvfs: ${_rs_p##*/} gov=$_rs_gov cur=$(abk_read_flat "$_rs_p/scaling_cur_freq") min=$_rs_min max=$_rs_max trans=$(abk_read_flat "$_rs_p/stats/total_trans") arch=${_rs_arch:-?} cap_view=${_rs_capv:-?}"
    case "$_rs_gov" in
      '') : ;;
      schedutil) : ;;
      *) _rs_foreign=1 ;;
    esac
    [ -n "$_rs_min" ] && [ "$_rs_min" = "$_rs_max" ] && _rs_pinned=1
    [ "$_rs_capv" -gt 0 ] || continue
    if [ "${_rs_arch:-0}" -gt "$_rs_bigarch" ]; then
      if [ -n "$_rs_big" ] && [ "$_rs_bigcapv" -gt "$_rs_othcapv" ]; then
        _rs_oth="$_rs_big"; _rs_othcapv="$_rs_bigcapv"
      fi
      _rs_big="$_rs_p"; _rs_bigarch="$_rs_arch"; _rs_bigcapv="$_rs_capv"
    elif [ "$_rs_capv" -gt "$_rs_othcapv" ]; then
      _rs_oth="$_rs_p"; _rs_othcapv="$_rs_capv"
    fi
  done

  if [ -n "$_rs_big" ] && [ -n "$_rs_oth" ] && [ "$_rs_othcapv" -ge "$_rs_bigcapv" ]; then
    abk_warn "dvfs: ${_rs_big##*/} is capped to ${_rs_bigcapv}/${_rs_bigarch} of its DMIPS capacity while ${_rs_oth##*/} stands at ${_rs_othcapv}: the biggest core is not the biggest core on offer, so light work (an app launch) will not be placed on it. Whoever owns policy*/scaling_max_freq is choosing placement. bin/abk_fas_check.sh --sample 20 says how much of the time this holds."
  fi

  # The combination that pins a cluster by itself: this module's floor armed
  # while another owner holds the range.  The floor is applied from
  # android_vh_cpufreq_resolve_freq and sampled from android_vh_scheduler_tick --
  # both governor-independent -- so a foreign governor does not make it safe, and
  # a payload that predates Batch 10-5 has no ownership gate at all.  The
  # abk_sf_boosting node only exists from 10-5 on, which is how this module tells
  # the two payload generations apart from userspace.
  _rs_state=0
  [ "$_rs_foreign" = "1" ] && _rs_state=1
  [ "$_rs_pinned" = "1" ] && _rs_state=1
  case "$_rs_enable" in
    Y|y|1)
      if [ "$_rs_state" = "1" ]; then
        if [ "$_rs_boost" != "absent" ]; then
          abk_log "abk_sf_enable=Y while a policy is foreign-governored or pinned at min == max; this payload carries the Batch 10-5 ownership gates and stands down there, but sched.abk_sf_enable=0 is the shipped intent"
        else
          abk_warn "abk_sf_enable=Y on a pre-10-5 payload (no abk_sf_boosting node) while a policy is foreign-governored or pinned at min == max: that payload has no ownership gate, so the floor is clamped to policy->max and ratchets the cluster to its ceiling. Set sched.abk_sf_enable=0 and graft the current payload."
        fi
      fi
      ;;
  esac
  return 0
}
