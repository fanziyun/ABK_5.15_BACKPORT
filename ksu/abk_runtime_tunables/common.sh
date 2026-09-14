#!/system/bin/sh
# common.sh - shared helpers for the ABK 5.15 runtime-tunables module.
#
# Sourced by post-fs-data.sh / service.sh / action.sh.  Keep it POSIX sh:
# /system/bin/sh is Android's mksh and there is no bash on the device.

ABK_TAG="ABK-Tunables"
ABK_VERSION="v0.7.0"

# --- hardcoded zram policy -------------------------------------------------
# Constants on purpose, not configuration.  Measured on the target device
# (vermeer, android13-5.15-lts 5.15.215) by compressing an exact 64 MiB ELF
# corpus on a single core, on a throwaway zram device:
#
#   algo      compressed   ratio   compress           decompress
#   lz4       31.19 MB     2.15x   0.60 s (107 MB/s)   865 MB/s
#   lz4kd     31.06 MB     2.16x   0.57 s (112 MB/s)   955 MB/s
#   lz4hc     29.31 MB     2.29x   1.13 s  (57 MB/s)   901 MB/s
#   zstd      24.21 MB     2.77x   0.73 s  (88 MB/s)   330 MB/s
#   deflate   23.51 MB     2.85x   2.29 s  (28 MB/s)   208 MB/s
#
# lz4kd is the best fast primary (fastest both ways, best ratio of the fast
# family, and the vendor's own CONFIG_ZRAM_DEF_COMP); zstd is the best
# secondary (22% smaller than lz4kd; the async worker pays the compression and
# 330 MB/s of fault-time decompression is still far above UFS).  lz4hc is
# dominated in both slots.  The ROM's own memory daemon leaves the primary on
# lz4hc before disksize, and the node is -EBUSY afterwards, so the module takes
# the whole bring-up over rather than asking userspace nicely.  See README.md
# for why there is no knob for any of this.
ABK_ZRAM_DEV="0"
ABK_ZRAM_PRIMARY="lz4kd"
ABK_ZRAM_SECONDARY="zstd"
ABK_ZRAM_MEM_LIMIT_PCT=25   # cap on compressed zram memory, percent of RAM
# `swapoff` reads every swapped page back into RAM (no data is lost), so this
# gate is not about data loss: it bounds the transient RAM spike.  At boot the
# ROM's own zram owner initialises the device a few seconds before this module
# gets to it, and a busy boot can have a little swapped out by then; later in
# the boot the same gate protects a device that is already under pressure.
ABK_ZRAM_MIN_FREE_MB=256
ABK_ZRAM_WAIT_SECS=45       # wait for the ROM's own zram bring-up before ours
ABK_ZRAM_REASSERT_SECS=60   # how often the policy is re-checked once running

# --- test / diagnostic seams ----------------------------------------------
# Every device path and helper command can be redirected, so the policy can be
# exercised against a fixture tree.  These are not user-facing knobs.
ABK_SYS_ROOT="${ABK_SYS_ROOT:-/sys}"
ABK_PROC_SWAPS="${ABK_PROC_SWAPS:-/proc/swaps}"
ABK_MEMINFO="${ABK_MEMINFO:-/proc/meminfo}"
# The kernel's FAS foreground-app registration (metis exposes proc_show_fas /
# proc_write_fas).  Read-only for this module: it reports who owns DVFS, it is
# never written here.
ABK_FAS_NODE="${ABK_FAS_NODE:-/proc/fas}"
ABK_SWAPOFF="${ABK_SWAPOFF:-swapoff}"
ABK_SWAPON="${ABK_SWAPON:-swapon}"
ABK_MKSWAP="${ABK_MKSWAP:-mkswap}"
ABK_LOSETUP="${ABK_LOSETUP:-losetup}"
ABK_DD="${ABK_DD:-dd}"
ABK_ZRAM_NODE="${ABK_ZRAM_NODE:-/dev/block/zram0}"
# Where a writeback backing file goes: the ROM's per-boot directory, the same
# one Android's own mmd_setup uses for its zram backing device.
ABK_ZRAM_WB_DIR="${ABK_ZRAM_WB_DIR:-/data/per_boot/zram}"
ABK_ZRAM_WB_FILE="${ABK_ZRAM_WB_FILE:-$ABK_ZRAM_WB_DIR/zram_swap}"
ABK_STDOUT="${ABK_STDOUT:-0}"

MODDIR="${MODDIR:-${0%/*}}"
ABK_STATE_DIR="${ABK_STATE_DIR:-$MODDIR/state}"
ABK_RUN_DIR="${ABK_RUN_DIR:-$MODDIR/run}"
ABK_CONF="${ABK_CONF:-$MODDIR/tunables.conf}"

abk_report_stdout() {
  if [ "$ABK_STDOUT" = "1" ]; then
    echo "$*"
  fi
  return 0
}

# The module's own progress log lives in the module directory as well as in
# logcat: logcat is best-effort (some ROMs restrict it, and a kernel-domain or
# su-domain writer can be denied silently), while a boot that fails invisibly is
# the one thing this module cannot afford.  `action.sh status` prints the tail.
ABK_LOG_MAX_BYTES=65536

abk_log_file() {
  printf '%s/%s\n' "$ABK_STATE_DIR" "abk_runtime_tunables.log"
}

abk_log_append() {
  _la_level="$1"
  _la_message="$2"
  _la_file="$(abk_log_file)"
  mkdir -p "$ABK_STATE_DIR" 2>/dev/null || true

  # Size probe guarded by -f: `< file` on a missing file makes the shell print
  # its own error (the redirect fails before the command's 2>/dev/null applies),
  # which would put a scary line in the middle of every first boot.
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
#
# Shell arithmetic is NOT portable here: /system/bin/sh is Android's mksh and on
# the target ROM wraps at 2^31 (measured: `15561024 * 1024` -> -1245380608),
# while KernelSU's busybox ash does not.  Every byte count the policy touches is
# already past that limit on any >= 2 GiB device, and a wrapped value silently
# turns the compressed-memory cap into a negative one -- which the kernel then
# refuses, leaving the cap off with only a warning in the log.  awk is 64-bit
# everywhere, so the arithmetic goes through it.
#
# The output format matters as much as the arithmetic: printf "%d" casts through
# the awk build's int, and the first on-device boot of the -202609202 build
# caught one service-context awk clamping 15889203200 (MemTotal bytes) to
# 2147483647 there -- the cap briefly landed as 512 MiB-1 (rounded to 512 MiB by
# the kernel) before the 60 s re-check wrote the true 25 %.  "%.0f" converts
# straight from the double, which is exact for any integer below 2^53, and no
# byte count this module touches comes near that.
abk_mul_div() {
  awk -v a="$1" -v b="$2" -v c="$3" \
    'BEGIN { printf "%.0f\n", a * b / c }' 2>/dev/null
}

# abk_gt <a> <b> / abk_le <a> <b> -> numeric comparison, 64-bit.
#
# Same trap as abk_mul_div: on this ROM's mksh even `[ 17179869184 -gt 0 ]` is
# false (it parses the operands through the same 32-bit arithmetic, where that
# literal is 0), so a swap size above 2 GiB looked like "no size" and the module
# silently fell back to MemTotal/2 -- halving a 16 GiB device.  Comparing in awk
# keeps every decision independent of which shell runs the script.
abk_gt() {
  awk -v a="$1" -v b="$2" 'BEGIN { exit !(a > b) }' 2>/dev/null
}

abk_le() {
  awk -v a="$1" -v b="$2" 'BEGIN { exit !(a <= b) }' 2>/dev/null
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
# The keys the module understands.  Unknown keys are reported once and ignored
# so a typo cannot change behaviour silently.
abk_known_keys() {
  cat <<'EOF'
zram.recomp.enable
zram.recomp.idle_age_sec
zram.recomp.interval_sec
zram.recomp.threshold
zram.recomp.mode
zram.compact.enable
zram.compact.min_waste_mb
zram.compact.waste_pct
zram.writeback
zram.writeback.size_mb
zram.reassert_interval_sec
vm.swappiness
vm.page_cluster
vm.watermark_scale_factor
vm.min_free_kbytes
lru_gen.enable
lru_gen.min_ttl_ms
thp.mode
sched.abk_sf_enable
sched.abk_sf_floor_pct
sched.abk_sf_sustained_ms
sched.abk_sf_exit_ms
readahead.dynamic_readahead
cfr.enable
cfr.interval_sec
cfr.freeze
cfr.quota_mb
cfr.group
report.logcat
EOF
}

# abk_cfg <key> <default>: value from tunables.conf, else the default.
# An empty value in the file means "use the default"; for the vm/thp/sched keys
# the default is empty, which the appliers read as "leave the kernel alone".
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
  mkdir -p "$ABK_STATE_DIR" 2>/dev/null || true
  printf '%s\n' "$2" > "$(abk_state_path "$1")" 2>/dev/null || true
}

# --- pid files and supervision -------------------------------------------
abk_pid_alive() {
  [ -n "$1" ] || return 1
  [ -r "/proc/$1" ] || return 1
  return 0
}

abk_pid_live() {
  abk_pid_alive "$(abk_state_get "$1.pid")"
}

abk_pid_write() {
  mkdir -p "$ABK_STATE_DIR" 2>/dev/null || true
  abk_state_set "$1.pid" "$2"
}

# --- small probes --------------------------------------------------------
abk_getprop() {
  getprop "$1" 2>/dev/null | tr -d '\r'
}

abk_meminfo_kb() {
  awk -v k="$1" '$1 == k":" { print $2; exit }' "$ABK_MEMINFO" 2>/dev/null
}

abk_mem_total_bytes() {
  _mt_kb="$(abk_meminfo_kb MemTotal)"
  abk_is_uint "$_mt_kb" || return 1
  abk_mul_div "$_mt_kb" 1024 1
  return 0
}

# abk_mem_pct_bytes <percent>: that share of RAM, in bytes (64-bit safe).
abk_mem_pct_bytes() {
  _mp_bytes="$(abk_mem_total_bytes)" || return 1
  abk_mul_div "$_mp_bytes" "$1" 100
  return 0
}

# --- non-zram runtime knobs ----------------------------------------------
# Every knob is applied only when the matching key is non-empty, so an
# unconfigured module changes nothing outside its own zram policy.

abk_apply_vm_knobs() {
  _vk_val="$(abk_cfg vm.swappiness '')"
  if [ -n "$_vk_val" ]; then
    if _vk_c="$(abk_clamp_uint "$_vk_val" 0 300)"; then
      abk_write /proc/sys/vm/swappiness "$_vk_c" \
        && abk_log "vm.swappiness=$_vk_c"
    else
      abk_warn "vm.swappiness: '$_vk_val' is not a number, ignored"
    fi
  fi

  _vk_val="$(abk_cfg vm.page_cluster '')"
  if [ -n "$_vk_val" ]; then
    if _vk_c="$(abk_clamp_uint "$_vk_val" 0 8)"; then
      abk_write /proc/sys/vm/page-cluster "$_vk_c" \
        && abk_log "vm.page-cluster=$_vk_c"
    else
      abk_warn "vm.page_cluster: '$_vk_val' is not a number, ignored"
    fi
  fi

  _vk_val="$(abk_cfg vm.watermark_scale_factor '')"
  if [ -n "$_vk_val" ]; then
    if _vk_c="$(abk_clamp_uint "$_vk_val" 1 3000)"; then
      abk_write /proc/sys/vm/watermark_scale_factor "$_vk_c" \
        && abk_log "vm.watermark_scale_factor=$_vk_c"
    else
      abk_warn "vm.watermark_scale_factor: '$_vk_val' is not a number, ignored"
    fi
  fi

  _vk_val="$(abk_cfg vm.min_free_kbytes '')"
  if [ -n "$_vk_val" ]; then
    if _vk_c="$(abk_clamp_uint "$_vk_val" 1024 1048576)"; then
      abk_write /proc/sys/vm/min_free_kbytes "$_vk_c" \
        && abk_log "vm.min_free_kbytes=$_vk_c"
    else
      abk_warn "vm.min_free_kbytes: '$_vk_val' is not a number, ignored"
    fi
  fi

  return 0
}

abk_apply_lru_gen() {
  _lg_node="$ABK_SYS_ROOT/kernel/mm/lru_gen/enabled"
  [ -e "$_lg_node" ] || return 0
  if [ "$(abk_cfg lru_gen.enable 0)" = "1" ]; then
    if abk_write "$_lg_node" y; then
      abk_log "lru_gen.enabled=$(abk_read "$_lg_node") (MGLRU on)"
    fi
  fi
  _lg_ttl="$(abk_cfg lru_gen.min_ttl_ms '')"
  _lg_ttl_node="$ABK_SYS_ROOT/kernel/mm/lru_gen/min_ttl_ms"
  if [ -n "$_lg_ttl" ] && [ -e "$_lg_ttl_node" ]; then
    if _lg_c="$(abk_clamp_uint "$_lg_ttl" 0 1000000)"; then
      abk_write "$_lg_ttl_node" "$_lg_c" \
        && abk_log "lru_gen.min_ttl_ms=$_lg_c"
    else
      abk_warn "lru_gen.min_ttl_ms: '$_lg_ttl' is not a number, ignored"
    fi
  fi
  return 0
}

abk_apply_thp() {
  _thp_mode="$(abk_cfg thp.mode '')"
  [ -n "$_thp_mode" ] || return 0
  case "$_thp_mode" in
    always|madvise|never) ;;
    *)
      abk_warn "thp.mode: '$_thp_mode' is not always|madvise|never, ignored"
      return 0
      ;;
  esac
  abk_write "$ABK_SYS_ROOT/kernel/mm/transparent_hugepage/enabled" "$_thp_mode" \
    && abk_log "thp.mode=$_thp_mode"
  return 0
}

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

  return 0
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
  abk_log "dvfs: fas=${_rs_fas:-no /proc/fas} abk_sf_enable=${_rs_enable:-absent} abk_sf_boosting=${_rs_boost:-absent}"

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

abk_apply_readahead_knob() {
  _ra_node="$ABK_SYS_ROOT/module/readahead/parameters/dynamic_readahead"
  [ -e "$_ra_node" ] || return 0
  _ra_val="$(abk_cfg readahead.dynamic_readahead '')"
  [ -n "$_ra_val" ] || return 0
  case "$_ra_val" in
    0|1|Y|N|y|n)
      abk_write "$_ra_node" "$_ra_val" \
        && abk_log "dynamic_readahead=$_ra_val"
      ;;
    *) abk_warn "readahead.dynamic_readahead: '$_ra_val' is not 0|1, ignored" ;;
  esac
  return 0
}

# --- the one SELinux rule this module needs -------------------------------
# The zram writeback data path is kernel-side: the loop worker, a kernel thread
# in u:r:kernel:s0, is what reads and writes the backing file.  Android's policy
# has no rule for that direction, so every page returns -EIO and writeback moves
# nothing -- measured on vermeer / 5.15.216, where even the ROM's own backing
# store had written 0 pages since boot (bd_stat 0 0 0) while SELinux was
# Enforcing.  KernelSU can add the rule from a module, which is the only route
# that does not need a ROM rebuild or a relaxed boot policy; sepolicy.rule ships
# one deliberately narrow allow (kernel -> zram_data_file, read + write).
#
# Nothing here is fatal, and nothing here weakens the policy: where the manager
# loads module rules itself this call is a redundant no-op, and where the rule
# cannot be added the device keeps the writeback it had -- which is the silent
# 0-page one, so the log line matters.
abk_selinux_apply_rules() {
  _sa_rule="$MODDIR/sepolicy.rule"
  if [ ! -f "$_sa_rule" ]; then
    abk_log "selinux: no sepolicy.rule shipped; leaving policy untouched"
    return 0
  fi
  for _sa_ks in /data/adb/ksud /data/adb/ksu/bin/ksud; do
    [ -x "$_sa_ks" ] || continue
    # This ksud reports 0 for a duplicate and even for an unresolvable symbol,
    # so the status means "statement submitted", never "rule took effect"; the
    # device-side proof is bd_stat moving off zero (action.sh prints it).
    if "$_sa_ks" sepolicy apply "$_sa_rule" >/dev/null 2>&1; then
      abk_log "selinux: submitted $_sa_rule via $_sa_ks"
    else
      abk_warn "selinux: $_sa_ks sepolicy apply failed; writeback stays at 0 pages under Enforcing"
    fi
    return 0
  done
  abk_log "selinux: no ksud on PATH; the manager's own sepolicy.rule loader owns $_sa_rule"
  return 0
}

abk_apply_early_knobs() {
  abk_apply_vm_knobs
  abk_apply_lru_gen
  abk_apply_thp
  abk_apply_sched_knobs
  abk_apply_readahead_knob
  abk_report_dvfs_state
  return 0
}
