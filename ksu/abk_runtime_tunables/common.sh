#!/system/bin/sh

ABK_TAG="ABK-Tunables"
ABK_VERSION="$(sed -n 's/^version=//p' "${MODDIR:-$(cd "$(dirname "$0")" 2>/dev/null && pwd)}/module.prop" 2>/dev/null)"

ABK_ZRAM_DEV="0"
ABK_ZRAM_PRIMARY="lz4kd"
ABK_ZRAM_SECONDARY="zstd"
ABK_ZRAM_MEM_LIMIT_PCT=25
# 4K/16K/64K page builds' common multiple: the kernel's own PAGE_ALIGN() is a no-op.
ABK_ZRAM_MEM_LIMIT_ALIGN=65536
ABK_ZRAM_MIN_FREE_MB=256
ABK_ZRAM_WAIT_SECS=45
ABK_ZRAM_REASSERT_SECS=60
ABK_ZRAM_SWAP_RETRIES=3
ABK_ZRAM_WB_RETRY_SECS=3600

ABK_SYS_ROOT="${ABK_SYS_ROOT:-/sys}"
ABK_PROC_SWAPS="${ABK_PROC_SWAPS:-/proc/swaps}"
ABK_MEMINFO="${ABK_MEMINFO:-/proc/meminfo}"
ABK_KCD_VM_DIR="${ABK_KCD_VM_DIR:-/proc/sys/vm}"
ABK_CGROOT="${ABK_CGROOT:-$ABK_SYS_ROOT/fs/cgroup}"
ABK_SWAPOFF="${ABK_SWAPOFF:-swapoff}"
ABK_SWAPON="${ABK_SWAPON:-swapon}"
ABK_MKSWAP="${ABK_MKSWAP:-mkswap}"
ABK_LOSETUP="${ABK_LOSETUP:-losetup}"
ABK_DD="${ABK_DD:-dd}"
ABK_GETPROP="${ABK_GETPROP:-getprop}"
ABK_ZRAM_NODE="${ABK_ZRAM_NODE:-/dev/block/zram0}"
ABK_ZRAM_WB_DIR="${ABK_ZRAM_WB_DIR:-/data/per_boot/zram}"
ABK_ZRAM_WB_FILE="${ABK_ZRAM_WB_FILE:-$ABK_ZRAM_WB_DIR/zram_swap}"
ABK_STDOUT="${ABK_STDOUT:-0}"
ABK_SHELL="${ABK_SHELL:-/system/bin/sh}"

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

ABK_LOG_MAX_BYTES=65536

abk_log_file() {
  printf '%s/%s\n' "$ABK_STATE_DIR" "abk_runtime_tunables.log"
}

abk_log_append() {
  _la_level="$1"
  _la_message="$2"
  _la_file="$(abk_log_file)"
  mkdir -p "$ABK_STATE_DIR" 2>/dev/null || true

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

abk_now_secs() {
  date +%s 2>/dev/null
}

abk_is_uint() {
  case "$1" in
    ''|*[!0-9]*) return 1 ;;
    *) return 0 ;;
  esac
}

abk_clamp_uint() {
  _cu_v="$1"
  abk_is_uint "$_cu_v" || return 1
  [ "$_cu_v" -ge "$2" ] || _cu_v="$2"
  [ "$_cu_v" -le "$3" ] || _cu_v="$3"
  printf '%s\n' "$_cu_v"
  return 0
}

abk_mul_div() {
  awk -v a="$1" -v b="$2" -v c="$3" \
    'BEGIN { printf "%.0f\n", a * b / c }' 2>/dev/null
}

abk_gt() {
  awk -v a="$1" -v b="$2" 'BEGIN { exit !(a > b) }' 2>/dev/null
}

abk_le() {
  awk -v a="$1" -v b="$2" 'BEGIN { exit !(a <= b) }' 2>/dev/null
}

abk_align_down() {
  awk -v v="$1" -v a="$2" 'BEGIN { printf "%.0f\n", int(v / a) * a }' 2>/dev/null
}

abk_write() {
  _w_path="$1"
  _w_val="$2"
  # A missing node is a warning, never a hard failure: the kernel may not carry the graft.
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

abk_known_keys() {
  cat <<'EOF'
zram.recomp.enable
zram.recomp.idle_age_sec
zram.recomp.interval_sec
zram.recomp.mark_interval_sec
zram.recomp.threshold
zram.recomp.max_pages
zram.recomp.mode
zram.compact.enable
zram.compact.min_waste_mb
zram.compact.waste_pct
zram.writeback
zram.writeback.size_mb
zram.writeback.trigger
zram.writeback.budget_mb
zram.writeback.interval_sec
zram.writeback.idle_age_sec
zram.reassert_interval_sec
vm.swappiness
vm.page_cluster
vm.watermark_scale_factor
vm.min_free_kbytes
vm.reassert_interval_sec
lru_gen.enable
lru_gen.min_ttl_ms
thp.mode
readahead.dynamic_readahead
psi.cgroup
psi.cgroup.protect
psi.cgroup.interval_sec
cfr.enable
cfr.interval_sec
cfr.freeze
cfr.quota_mb
cfr.group
cfr.cgroup_root
cfr.frozen_only
cfr.freezer_root
cfr.cached_only
iorap.watch
iorap.min_uid
iorap.record_cool_min
iorap.kick_cool_sec
iorap.global_gap_sec
kcompressd.monitor_interval_sec
kcompressd.swapcached_warn_mb
kcompressd.depth_warn
fault_around.enable
fault_around.sys_uid
fault_around.app_only
fault_around.app_uids
swapin.window_enable
swapin.boost_enable
swapin.boost_pages
swapin.shrink_enable
swapin.shrink_pages
swapin.background_only
swapin.vma_ra
policy.reassert_interval_sec
report.logcat
EOF
}

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

# ps NAME is the exec'd argv[0] basename; comm is TASK_COMM_LEN-1 bytes, so the
# long name lives in a symlink and comm carries the short alias.
abk_supervisor_name() {
  case "$1" in
    zram) _sn_long=Sailboat_zram_supervisor; _sn_comm=Sail_zram ;;
    cfr) _sn_long=Sailboat_cfr_supervisor; _sn_comm=Sail_cfr ;;
    psi) _sn_long=Sailboat_psi_supervisor; _sn_comm=Sail_psi ;;
    lru_gen) _sn_long=Sailboat_lrugen_supervisor; _sn_comm=Sail_lrugen ;;
    iorap-watch) _sn_long=Sailboat_iorap_supervisor; _sn_comm=Sail_iorap ;;
    *) return 1 ;;
  esac
  if [ "${2:-long}" = "comm" ]; then
    printf '%s\n' "$_sn_comm"
  else
    printf '%s\n' "$_sn_long"
  fi
}

abk_set_comm() {
  [ -n "${1:-}" ] || return 0
  # echo without -n writes a newline, and comm takes the first byte of that.
  echo -n "$1" > "/proc/$$/comm" 2>/dev/null || true
  return 0
}

abk_getprop() {
  "$ABK_GETPROP" "$1" 2>/dev/null | tr -d '\r'
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

abk_mem_pct_bytes() {
  _mp_bytes="$(abk_mem_total_bytes)" || return 1
  abk_mul_div "$_mp_bytes" "$1" 100
  return 0
}


abk_kcompressd_sample() {
  _kcs_swp="$(abk_read "$ABK_KCD_VM_DIR/kcompressd_swapped" | tr -d ' \t\r\n')"
  _kcs_syn="$(abk_read "$ABK_KCD_VM_DIR/kcompressd_sync" | tr -d ' \t\r\n')"
  _kcs_enq="$(abk_read "$ABK_KCD_VM_DIR/kcompressd_enqueued" | tr -d ' \t\r\n')"
  _kcs_thr="$(abk_read "$ABK_KCD_VM_DIR/kcompressd" | tr -d ' \t\r\n')"

  case "$_kcs_thr" in ''|*[!0-9]*) _kcs_thr=absent ;; esac
  case "$_kcs_enq" in ''|*[!0-9]*) _kcs_enq=absent ;; esac
  case "$_kcs_swp" in ''|*[!0-9]*) _kcs_swp=absent ;; esac
  case "$_kcs_syn" in ''|*[!0-9]*) _kcs_syn=absent ;; esac

  _kcs_dep=absent
  if [ "$_kcs_enq" != absent ] && [ "$_kcs_swp" != absent ]; then
    _kcs_dep="$(awk -v e="$_kcs_enq" -v s="$_kcs_swp" \
      'BEGIN { d = e - s; if (d < 0) d = 0; printf "%.0f\n", d }' 2>/dev/null)"
    case "$_kcs_dep" in ''|*[!0-9]*) _kcs_dep=absent ;; esac
  fi

  ABK_KCD_THRESHOLD="$_kcs_thr"
  ABK_KCD_ENQUEUED="$_kcs_enq"
  ABK_KCD_SWAPPED="$_kcs_swp"
  ABK_KCD_SYNC="$_kcs_syn"
  ABK_KCD_DEPTH="$_kcs_dep"
  return 0
}

abk_kcompressd_state_line() {
  abk_kcompressd_sample
  _kl_thr1="$ABK_KCD_THRESHOLD"
  _kl_enq1="$ABK_KCD_ENQUEUED"
  _kl_swp1="$ABK_KCD_SWAPPED"
  _kl_syn1="$ABK_KCD_SYNC"
  _kl_dep1="$ABK_KCD_DEPTH"

  abk_kcompressd_sample
  _kl_thr2="$ABK_KCD_THRESHOLD"
  _kl_enq2="$ABK_KCD_ENQUEUED"
  _kl_swp2="$ABK_KCD_SWAPPED"
  _kl_syn2="$ABK_KCD_SYNC"
  _kl_dep2="$ABK_KCD_DEPTH"

  if [ "$_kl_dep2" = absent ] \
    || { [ "$_kl_dep1" != absent ] && abk_le "$_kl_dep2" "$_kl_dep1"; }; then
    ABK_KCD_THRESHOLD="$_kl_thr1"
    ABK_KCD_ENQUEUED="$_kl_enq1"
    ABK_KCD_SWAPPED="$_kl_swp1"
    ABK_KCD_SYNC="$_kl_syn1"
    ABK_KCD_DEPTH="$_kl_dep1"
  else
    ABK_KCD_THRESHOLD="$_kl_thr2"
    ABK_KCD_ENQUEUED="$_kl_enq2"
    ABK_KCD_SWAPPED="$_kl_swp2"
    ABK_KCD_SYNC="$_kl_syn2"
    ABK_KCD_DEPTH="$_kl_dep2"
  fi

  printf 'kcompressd: threshold=%s enqueued=%s swapped=%s sync=%s depth=%s\n' \
    "$ABK_KCD_THRESHOLD" "$ABK_KCD_ENQUEUED" "$ABK_KCD_SWAPPED" \
    "$ABK_KCD_SYNC" "$ABK_KCD_DEPTH"
  return 0
}


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

abk_reassert_lru_gen() {
  _rl_node="$ABK_SYS_ROOT/kernel/mm/lru_gen/enabled"
  [ -e "$_rl_node" ] || return 0
  [ "$(abk_cfg lru_gen.enable 0)" = "1" ] || return 0
  _rl_cur="$(abk_read "$_rl_node")"
  case "$_rl_cur" in
    *0|*2|*4|*6|*8|*a|*A|*c|*C|*e|*E)
      if abk_write "$_rl_node" y; then
        abk_warn "lru_gen.enabled was $_rl_cur (a writer turned MGLRU off); re-asserted: $(abk_read "$_rl_node")"
      fi
      ;;
  esac
  return 0
}

abk_lru_gen_supervisor_main() {
  abk_pid_write lru_gen "$$"
  _ls_reassert="$(abk_cfg vm.reassert_interval_sec 60)"
  abk_is_uint "$_ls_reassert" || _ls_reassert=60
  [ "$_ls_reassert" -ge 5 ] || _ls_reassert=5
  abk_log "lru_gen reassert supervisor up: interval=${_ls_reassert}s"
  while :; do
    abk_reassert_lru_gen || true
    sleep "$_ls_reassert"
  done
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

abk_psi_mode() {
  _pm_val="$(abk_cfg psi.cgroup keep)"
  case "$_pm_val" in
    keep|auto|aggressive)
      printf '%s\n' "$_pm_val"
      return 0
      ;;
  esac
  abk_warn "psi.cgroup='$_pm_val' is not keep|auto|aggressive; leaving the kernel alone"
  printf 'keep\n'
  return 0
}

abk_psi_protect() {
  _pp_val="$(abk_cfg psi.cgroup.protect system)"
  [ -n "$_pp_val" ] || _pp_val=system
  printf '%s\n' "$_pp_val"
  return 0
}

abk_psi_interval() {
  _pi_val="$(abk_cfg psi.cgroup.interval_sec 300)"
  abk_is_uint "$_pi_val" || _pi_val=300
  [ "$_pi_val" -ge 60 ] || _pi_val=60
  printf '%s\n' "$_pi_val"
  return 0
}

abk_psi_pass() {
  _pa_tool="${ABK_PSI_TOOL:-$MODDIR/bin/abk_psi_policy.sh}"
  if [ ! -f "$_pa_tool" ]; then
    abk_warn "$_pa_tool is missing; per-cgroup PSI policy not applied"
    return 1
  fi
  _pa_mode="$(abk_psi_mode)"
  _pa_protect="$(abk_psi_protect)"
  _pa_out="$(sh "$_pa_tool" --apply --mode "$_pa_mode" --protect "$_pa_protect" --cgroot "$ABK_CGROOT" 2>&1)"
  _pa_rc=$?
  ABK_PSI_LAST_LINE=""
  while IFS= read -r _pa_line; do
    [ -n "$_pa_line" ] || continue
    case "$_pa_line" in
      "psi: mode="*) ABK_PSI_LAST_LINE="$_pa_line" ;;
    esac
    case "$_pa_line" in
      *"disabled=0 "*) abk_log_append INFO "$_pa_line" ;;
      *) abk_log "$_pa_line" ;;
    esac
  done <<EOF
$_pa_out
EOF
  return $_pa_rc
}

abk_psi_state_line() {
  printf '%s\n' "${ABK_PSI_LAST_LINE:-}"
}

abk_psi_node_count() {
  if [ -d "$ABK_CGROOT" ]; then
    find "$ABK_CGROOT" -name cgroup.pressure 2>/dev/null | wc -l
  else
    echo 0
  fi
  return 0
}

abk_psi_supervisor_main() {
  abk_pid_write psi "$$"
  _ps_interval="$(abk_psi_interval)"
  abk_log "per-cgroup PSI supervisor up: mode=$(abk_psi_mode) protect=$(abk_psi_protect) interval=${_ps_interval}s"
  if [ "$(abk_psi_mode)" = auto ]; then
    abk_warn "psi.cgroup=auto reaches only task-empty groups, which cost nothing to begin with (measured no-op); use aggressive to affect accounted groups"
  fi
  _ps_first=1
  while :; do
    abk_psi_pass
    if [ "$_ps_first" = 1 ]; then
      _ps_first=0
      _ps_line="$(abk_psi_state_line)"
      case "$_ps_line" in
        *"already_off=0"*)
          case "$_ps_line" in
            *"disabled=0"*"refused=0"*|"") : ;;
            *"disabled=0"*)
              abk_warn "per-cgroup PSI: every write was refused; stopping the supervisor instead of re-walking $(abk_psi_node_count) nodes every ${_ps_interval}s ($_ps_line)"
              return 0
              ;;
          esac
          ;;
      esac
    fi
    sleep "$_ps_interval"
  done
}
abk_selinux_apply_rules() {
  _sa_rule="$MODDIR/sepolicy.rule"
  if [ ! -f "$_sa_rule" ]; then
    abk_log "selinux: no sepolicy.rule shipped; leaving policy untouched"
    return 0
  fi
  for _sa_ks in /data/adb/ksud /data/adb/ksu/bin/ksud; do
    [ -x "$_sa_ks" ] || continue
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

abk_norm_yn() {
  case "$1" in
    1|y|Y|yes|on|true) printf 'Y\n' ;;
    0|n|N|no|off|false) printf 'N\n' ;;
    *) return 1 ;;
  esac
  return 0
}

abk_sync_node() {
  _sn_path="$1"
  _sn_want="$2"
  _sn_tag="$3"
  [ -e "$_sn_path" ] || return 0
  _sn_cur="$(abk_read "$_sn_path" | tr -d ' \t\r\n')"
  [ "$_sn_cur" = "$_sn_want" ] && return 0
  abk_write "$_sn_path" "$_sn_want" && abk_log "$_sn_tag=$_sn_want"
  return 0
}

abk_apply_fault_around_knobs() {
  _fa_d="$ABK_SYS_ROOT/module/memory/parameters"
  _fa_v="$(abk_cfg fault_around.enable '')"
  if [ -n "$_fa_v" ]; then
    _fa_v="$(abk_norm_yn "$_fa_v")" || _fa_v=""
    [ -n "$_fa_v" ] && abk_sync_node \
      "$_fa_d/fault_around_enable" "$_fa_v" fault_around.enable
  fi
  _fa_v="$(abk_cfg fault_around.sys_uid '')"
  if [ -n "$_fa_v" ]; then
    _fa_v="$(abk_norm_yn "$_fa_v")" || _fa_v=""
    [ -n "$_fa_v" ] && abk_sync_node \
      "$_fa_d/fault_around_sys_uid" "$_fa_v" fault_around.sys_uid
  fi
  _fa_v="$(abk_cfg fault_around.app_only '')"
  if [ -n "$_fa_v" ]; then
    _fa_v="$(abk_norm_yn "$_fa_v")" || _fa_v=""
    [ -n "$_fa_v" ] && abk_sync_node \
      "$_fa_d/fault_around_app_only" "$_fa_v" fault_around.app_only
  fi
  _fa_list="$(abk_cfg fault_around.app_uids '')"
  [ -e "$_fa_d/fault_around_app" ] || return 0
  _fa_norm="$(printf '%s' "$_fa_list" | tr ',' ' ')"
  _fa_exp="$(for _fa_u in $_fa_norm; do
    if abk_is_uint "$_fa_u" && [ "$_fa_u" -ge 10000 ] && [ "$_fa_u" -le 99999 ]; then
      printf '%s\n' "$_fa_u"
    elif [ "$_fa_u" != "0" ]; then
      abk_warn "fault_around.app_uids: '$_fa_u' ignored"
    fi
  done | sort -n -u | head -n 64 | tr '\n' ',')"
  _fa_exp="${_fa_exp%,}"
  _fa_cur="$(abk_read "$_fa_d/fault_around_app" | tr -d ' \t\r\n')"
  [ "$_fa_cur" = "$_fa_exp" ] && return 0
  abk_write "$_fa_d/fault_around_app" 0 || return 0
  for _fa_u in $(printf '%s' "$_fa_exp" | tr ',' ' '); do
    abk_write "$_fa_d/fault_around_app" "$_fa_u" || return 0
  done
  abk_log "fault_around.app_uids=$_fa_exp"
  return 0
}

abk_apply_swapin_knobs() {
  _si_d="$ABK_SYS_ROOT/module/readahead/parameters"
  for _si_pair in \
      "swapin_window_enable:swapin.window_enable" \
      "swapin_boost_enable:swapin.boost_enable" \
      "swapin_boost_pages:swapin.boost_pages" \
      "swapin_shrink_enable:swapin.shrink_enable" \
      "swapin_shrink_pages:swapin.shrink_pages" \
      "swapin_window_background_only:swapin.background_only"; do
    _si_node="${_si_pair%%:*}"
    _si_key="${_si_pair#*:}"
    _si_v="$(abk_cfg "$_si_key" '')"
    [ -n "$_si_v" ] || continue
    case "$_si_key" in
      swapin.boost_pages|swapin.shrink_pages) ;;
      *) _si_v="$(abk_norm_yn "$_si_v")" || { abk_warn "$_si_key: '$_si_v' ignored"; continue; } ;;
    esac
    abk_sync_node "$_si_d/$_si_node" "$_si_v" "$_si_key"
  done
  _si_v="$(abk_cfg swapin.vma_ra '')"
  [ -n "$_si_v" ] || return 0
  case "$_si_v" in
    1|true|on) _si_w=true ;;
    0|false|off) _si_w=false ;;
    *) abk_warn "swapin.vma_ra: '$_si_v' ignored"; return 0 ;;
  esac
  abk_sync_node "$ABK_SYS_ROOT/kernel/mm/swap/vma_ra_enabled" \
    "$_si_w" swapin.vma_ra
  return 0
}

abk_apply_policy_knobs() {
  abk_apply_fault_around_knobs
  abk_apply_swapin_knobs
  return 0
}

abk_apply_early_knobs() {
  abk_apply_vm_knobs
  abk_apply_policy_knobs
  abk_apply_lru_gen
  abk_apply_thp
  abk_apply_readahead_knob
  return 0
}
