#!/system/bin/sh
# common.sh - shared helpers for the ABK 5.15 runtime-tunables module.
#
# Sourced by post-fs-data.sh / service.sh / action.sh.  Keep it POSIX sh:
# /system/bin/sh is Android's mksh and there is no bash on the device.

ABK_TAG="ABK-Tunables"
ABK_VERSION="v0.2.0"

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
abk_mul_div() {
  awk -v a="$1" -v b="$2" -v c="$3" \
    'BEGIN { printf "%d\n", a * b / c }' 2>/dev/null
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

abk_checkpoint() { # <first> <step> <limit>
  _cp_first="$1"
  _cp_step="$2"
  _cp_limit="$3"
  _cp_attempt=0
  while [ "$_cp_attempt" -lt "$_cp_limit" ]; do
    if [ "$_cp_attempt" -eq 0 ]; then
      "$_cp_first"
    else
      "$_cp_step"
    fi
    _cp_attempt=$(( _cp_attempt + 1 ))
    [ "$_cp_attempt" -ge "$_cp_limit" ] || sleep 3
  done
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

abk_apply_early_knobs() {
  abk_apply_vm_knobs
  abk_apply_lru_gen
  abk_apply_thp
  abk_apply_sched_knobs
  abk_apply_readahead_knob
  return 0
}
