#!/system/bin/sh

ABK_ZRAM_DIR="$ABK_SYS_ROOT/block/zram$ABK_ZRAM_DEV"
ABK_ZRAM_LOCK_PARAM="$ABK_SYS_ROOT/module/zram/parameters/abk_lock_algo"
ABK_ZRAM_COMP_PARAM="$ABK_SYS_ROOT/module/zram/parameters/abk_comp_algo"

abk_zram_present() {
  [ -d "$ABK_ZRAM_DIR" ] && [ -e "$ABK_ZRAM_DIR/disksize" ]
}

abk_zram_active_algo() {
  _za_file="$1"
  [ -f "$_za_file" ] || return 0
  _za_text="$(cat "$_za_file" 2>/dev/null || true)"

  _za_bracket="$(printf '%s\n' "$_za_text" | sed -n 's/.*\[\([^]]*\)\].*/\1/p' | head -n 1)"
  if [ -n "$_za_bracket" ]; then
    printf '%s\n' "$_za_bracket"
    return 0
  fi

  _za_arg="$(printf '%s\n' "$_za_text" | sed -n 's/.*algo=\([^[:space:]]*\).*/\1/p' | head -n 1)"
  if [ -n "$_za_arg" ]; then
    printf '%s\n' "$_za_arg"
    return 0
  fi

  printf '%s\n' "$_za_text" | awk 'NR==1 { print $NF }'
  return 0
}

abk_zram_primary() {
  abk_zram_active_algo "$ABK_ZRAM_DIR/comp_algorithm"
}

abk_zram_secondary() {
  abk_zram_active_algo "$ABK_ZRAM_DIR/recomp_algorithm"
}

abk_zram_initstate() {
  abk_read "$ABK_ZRAM_DIR/initstate" | tr -d ' \n'
}

abk_zram_disksize() {
  abk_read "$ABK_ZRAM_DIR/disksize" | tr -d ' \n'
}

abk_zram_mem_limit() {
  awk '{ print $4 }' "$ABK_ZRAM_DIR/mm_stat" 2>/dev/null || true
}

abk_zram_swap_field() {
  awk -v dev="$ABK_ZRAM_NODE" -v f="$1" '
    $1 == dev { print $f; exit }' "$ABK_PROC_SWAPS" 2>/dev/null
}

abk_zram_swap_total_kb() { abk_zram_swap_field 3; }
abk_zram_swap_used_kb()  { abk_zram_swap_field 4; }
abk_zram_swap_prio()     { abk_zram_swap_field 5; }

abk_zram_swap_on() {
  [ -n "$(abk_zram_swap_total_kb)" ]
}

abk_zram_swap_idle_enough() {
  _si_used="$(abk_zram_swap_used_kb)"
  if [ -z "$_si_used" ]; then
    return 0
  fi
  abk_is_uint "$_si_used" || return 1
  _si_limit=$(( ABK_ZRAM_MIN_FREE_MB * 1024 ))
  abk_le "$_si_used" "$_si_limit"
}

abk_zram_writeback_capable() {
  [ -e "$ABK_ZRAM_DIR/writeback" ]
}

abk_zram_mmd_done() {
  _md="$(abk_getprop mmd.setup_complete)"
  [ "$_md" = "true" ]
}

abk_zram_backing_dev() {
  _bd_val="$(abk_read "$ABK_ZRAM_DIR/backing_dev" | tr -d ' \n')"
  case "$_bd_val" in none) return 0 ;; esac
  printf '%s\n' "$_bd_val"
  return 0
}

abk_zram_writeback_owned() {
  [ -n "$(abk_zram_backing_dev)" ] && return 0
  abk_zram_mmd_done && return 0
  return 1
}

abk_zram_writeback_mode() {
  case "$(abk_cfg zram.writeback off)" in
    off|0|no) printf 'off\n' ;;
    *) printf 'auto\n' ;;
  esac
}

abk_zram_wb_backoff_active() {
  _wbo_until="$(abk_state_get zram_wb_retry_after)"
  abk_is_uint "$_wbo_until" || return 1
  _wbo_now="$(abk_now_secs)"
  abk_is_uint "$_wbo_now" || return 1
  abk_gt "$_wbo_until" "$_wbo_now"
}

abk_zram_wb_backoff_arm() {
  _wba_now="$(abk_now_secs)"
  abk_is_uint "$_wba_now" || return 0
  abk_state_set zram_wb_retry_after \
    "$(awk -v n="$_wba_now" -v d="$ABK_ZRAM_WB_RETRY_SECS" \
        'BEGIN { printf "%.0f\n", n + d }')"
}

abk_zram_wb_backoff_clear() {
  rm -f "$(abk_state_path zram_wb_retry_after)" 2>/dev/null || true
}

abk_boot_completed() {
  [ "$(abk_getprop sys.boot_completed)" = "1" ]
}

abk_zram_writeback_unwanted() {
  abk_zram_writeback_capable || return 0
  [ "$(abk_zram_writeback_mode)" = "off" ] && return 0
  return 1
}

abk_zram_writeback_wanted() {
  abk_zram_writeback_capable || return 1
  [ "$(abk_zram_writeback_mode)" = "auto" ] || return 1
  abk_zram_writeback_owned && return 1
  abk_zram_wb_backoff_active && return 1
  abk_boot_completed || return 1
  return 0
}

abk_zram_writeback_size_mb() {
  _ws_mb="$(abk_cfg zram.writeback.size_mb 1024)"
  abk_is_uint "$_ws_mb" || _ws_mb=1024
  [ "$_ws_mb" -ge 64 ] || _ws_mb=64
  [ "$_ws_mb" -le 8192 ] || _ws_mb=8192
  printf '%s\n' "$_ws_mb"
}

abk_zram_create_backing_dev() {
  _cb_file="$ABK_ZRAM_WB_FILE"
  _cb_mb="$(abk_zram_writeback_size_mb)"
  _cb_dir="${_cb_file%/*}"

  mkdir -p "$_cb_dir" 2>/dev/null || {
    abk_warn "writeback: cannot create $_cb_dir"
    return 1
  }
  chmod 0770 "$_cb_dir" 2>/dev/null || true
  rm -f "$_cb_file" 2>/dev/null || true

  if ! "$ABK_DD" if=/dev/zero of="$_cb_file" bs=1048576 count=1 \
       seek="$(( _cb_mb - 1 ))" >/dev/null 2>&1; then
    abk_warn "writeback: cannot create a ${_cb_mb}MiB backing file at $_cb_file"
    return 1
  fi

  _cb_loop="$("$ABK_LOSETUP" -f 2>/dev/null | tr -d ' \r\n')"
  case "$_cb_loop" in
    /dev/*) ;;
    *)
      abk_warn "writeback: no free loop device ($ABK_LOSETUP -f said '$_cb_loop')"
      return 1
      ;;
  esac
  if ! "$ABK_LOSETUP" "$_cb_loop" "$_cb_file" >/dev/null 2>&1; then
    abk_warn "writeback: $ABK_LOSETUP $_cb_loop $_cb_file failed"
    return 1
  fi
  printf '%s\n' "$_cb_loop"
  return 0
}

abk_zram_backing_dev_ours() {
  _bo_dev="$1"
  [ -n "$_bo_dev" ] || return 1
  _bo_val="$(abk_read "$ABK_SYS_ROOT/block/${_bo_dev##*/}/loop/backing_file" | tr -d '\n')"
  case "$_bo_val" in
    "$ABK_ZRAM_WB_FILE"|"$ABK_ZRAM_WB_FILE (deleted)") return 0 ;;
  esac
  return 1
}

abk_zram_release_backing_dev() {
  _rb_dev="$1"
  abk_zram_backing_dev_ours "$_rb_dev" || return 0
  if [ "${ABK_DRY_RUN:-0}" = "1" ]; then
    abk_log "[dry-run] $ABK_LOSETUP -d $_rb_dev; rm -f $ABK_ZRAM_WB_FILE"
    return 0
  fi
  if "$ABK_LOSETUP" -d "$_rb_dev" >/dev/null 2>&1; then
    abk_log "writeback: released $_rb_dev ($ABK_ZRAM_WB_FILE)"
  else
    abk_warn "writeback: cannot detach $_rb_dev"
  fi
  rm -f "$ABK_ZRAM_WB_FILE" 2>/dev/null || true
  return 0
}

abk_zram_drop_stale_backing_file() {
  abk_zram_writeback_unwanted || return 0
  [ -z "$(abk_zram_backing_dev)" ] || return 0
  [ -e "$ABK_ZRAM_WB_FILE" ] || return 0
  if [ "${ABK_DRY_RUN:-0}" = "1" ]; then
    abk_log "[dry-run] rm -f $ABK_ZRAM_WB_FILE"
    return 0
  fi
  if rm -f "$ABK_ZRAM_WB_FILE" 2>/dev/null; then
    abk_log "writeback: removed the stale backing file $ABK_ZRAM_WB_FILE"
  else
    abk_warn "writeback: cannot remove $ABK_ZRAM_WB_FILE"
  fi
  return 0
}

abk_zram_attach_writeback() {
  _aw_reuse="$1"
  _aw_limit="$2"
  abk_zram_writeback_capable || return 0

  if [ -n "$_aw_reuse" ]; then
    _aw_dev="$_aw_reuse"
    abk_is_uint "$_aw_limit" \
      || _aw_limit=$(( $(abk_zram_writeback_size_mb) * 256 ))
  else
    _aw_dev="$(abk_zram_create_backing_dev)" || return 1
    _aw_limit=$(( $(abk_zram_writeback_size_mb) * 256 ))
  fi

  if ! abk_write "$ABK_ZRAM_DIR/backing_dev" "$_aw_dev"; then
    abk_warn "writeback: cannot attach $_aw_dev"
    return 1
  fi

  if [ -e "$ABK_ZRAM_DIR/compressed_writeback" ]; then
    abk_write "$ABK_ZRAM_DIR/compressed_writeback" 1 || true
  fi

  abk_write "$ABK_ZRAM_DIR/writeback_limit" "$_aw_limit" || true
  abk_write "$ABK_ZRAM_DIR/writeback_limit_enable" 1 || true
  abk_log "writeback: backing device $_aw_dev (limit ${_aw_limit} blocks)"
  return 0
}

abk_zram_saved_disksize() {
  _sd_live="$(abk_zram_disksize)"
  if abk_is_uint "$_sd_live" && abk_gt "$_sd_live" 0; then
    printf '%s\n' "$_sd_live"
    return 0
  fi
  _sd_prev="$(abk_state_get disksize)"
  if abk_is_uint "$_sd_prev" && abk_gt "$_sd_prev" 0; then
    abk_warn "live disksize is unset; reusing the last applied value $_sd_prev"
    printf '%s\n' "$_sd_prev"
    return 0
  fi
  _sd_mem="$(abk_mem_total_bytes)" || return 1
  abk_mul_div "$_sd_mem" 1 2
  return 0
}

abk_zram_set_algorithms() {
  abk_write "$ABK_ZRAM_DIR/comp_algorithm" "$ABK_ZRAM_PRIMARY" || true
  abk_write "$ABK_ZRAM_DIR/recomp_algorithm" \
    "algo=$ABK_ZRAM_SECONDARY priority=1" || true

  abk_zram_algorithms_ok
}

abk_zram_algorithms_ok() {
  _ao_p="$(abk_zram_primary)"
  _ao_s="$(abk_zram_secondary)"
  if [ "$_ao_p" != "$ABK_ZRAM_PRIMARY" ]; then
    abk_warn "primary algorithm is '$_ao_p', wanted '$ABK_ZRAM_PRIMARY'"
    return 1
  fi
  if [ "$_ao_s" != "$ABK_ZRAM_SECONDARY" ]; then
    abk_warn "secondary algorithm is '$_ao_s', wanted '$ABK_ZRAM_SECONDARY'"
    return 1
  fi
  return 0
}

abk_zram_kernel_locked() {
  case "$(abk_read "$ABK_ZRAM_LOCK_PARAM" | tr -d ' \n')" in
    Y|y|1) return 0 ;;
    *) return 1 ;;
  esac
}

abk_zram_set_mem_limit() {
  _ml_val="$(abk_mem_pct_bytes "$ABK_ZRAM_MEM_LIMIT_PCT")" || return 0
  abk_is_uint "$_ml_val" || return 0
  _ml_val="$(abk_align_down "$_ml_val" "$ABK_ZRAM_MEM_LIMIT_ALIGN")"
  abk_is_uint "$_ml_val" || return 0
  abk_gt "$_ml_val" 0 || return 0

  _ml_seen="$(abk_zram_mem_limit)"
  if [ "$_ml_seen" = "$_ml_val" ]; then
    return 0
  fi

  abk_write "$ABK_ZRAM_DIR/mem_limit" "$_ml_val" || return 0
  _ml_seen="$(abk_zram_mem_limit)"
  if [ "$_ml_seen" = "$_ml_val" ]; then
    abk_log "zram mem_limit=$_ml_val bytes (${ABK_ZRAM_MEM_LIMIT_PCT}% of RAM)"
  else
    abk_warn "mem_limit readback is '$_ml_seen', wanted $_ml_val"
  fi
  return 0
}

abk_zram_mount_swap() {
  _ms_size="$1"
  _ms_prio="$2"
  abk_is_uint "$_ms_prio" || _ms_prio=0

  abk_write "$ABK_ZRAM_DIR/disksize" "$_ms_size" || return 1
  abk_zram_set_mem_limit

  if [ "${ABK_DRY_RUN:-0}" = "1" ]; then
    abk_log "[dry-run] $ABK_MKSWAP $ABK_ZRAM_NODE"
    abk_log "[dry-run] $ABK_SWAPON -p $_ms_prio $ABK_ZRAM_NODE"
    return 0
  fi

  if ! "$ABK_MKSWAP" "$ABK_ZRAM_NODE" >/dev/null 2>&1; then
    abk_warn "mkswap failed on $ABK_ZRAM_NODE"
    return 1
  fi
  if ! "$ABK_SWAPON" -p "$_ms_prio" "$ABK_ZRAM_NODE" >/dev/null 2>&1; then
    abk_warn "swapon failed on $ABK_ZRAM_NODE"
    return 1
  fi
  abk_state_set disksize "$_ms_size"
  return 0
}

abk_zram_mount_swap_area() {
  _ma_prio="$1"
  abk_is_uint "$_ma_prio" || _ma_prio=0

  if [ "${ABK_DRY_RUN:-0}" = "1" ]; then
    abk_log "[dry-run] $ABK_MKSWAP $ABK_ZRAM_NODE"
    abk_log "[dry-run] $ABK_SWAPON -p $_ma_prio $ABK_ZRAM_NODE"
    return 0
  fi

  abk_zram_set_mem_limit
  _ma_n=0
  _ma_mk=0
  while [ "$_ma_n" -lt "$ABK_ZRAM_SWAP_RETRIES" ]; do
    if abk_zram_swap_on; then
      return 0
    fi
    if [ "$_ma_mk" = 0 ] \
        && "$ABK_MKSWAP" "$ABK_ZRAM_NODE" >/dev/null 2>&1; then
      _ma_mk=1
    fi
    if [ "$_ma_mk" = 1 ] \
        && "$ABK_SWAPON" -p "$_ma_prio" "$ABK_ZRAM_NODE" >/dev/null 2>&1; then
      abk_state_set disksize "$(abk_zram_disksize)"
      return 0
    fi
    _ma_n=$(( _ma_n + 1 ))
    [ "$_ma_n" -ge "$ABK_ZRAM_SWAP_RETRIES" ] || sleep 1
  done
  abk_zram_swap_on
}

abk_zram_takeover() {
  if ! abk_zram_present; then
    abk_warn "no $ABK_ZRAM_DIR; nothing to configure"
    return 1
  fi

  _to_size="$(abk_zram_saved_disksize)" || {
    abk_warn "cannot determine a swap size; leaving zram alone"
    return 1
  }
  _to_prio="$(abk_zram_swap_prio)"
  abk_is_uint "$_to_prio" || _to_prio=0

  _to_wb_dev=""
  _to_wb_limit=""
  _to_wb_enable=""
  _to_wb_release=""
  if abk_zram_writeback_capable; then
    _to_wb_dev="$(abk_zram_backing_dev)"
    _to_wb_limit="$(abk_read "$ABK_ZRAM_DIR/writeback_limit" | tr -d ' \n')"
    _to_wb_enable="$(abk_read "$ABK_ZRAM_DIR/writeback_limit_enable" | tr -d ' \n')"
  fi
  if [ -n "$_to_wb_dev" ] && abk_zram_writeback_unwanted \
     && abk_zram_backing_dev_ours "$_to_wb_dev"; then
    _to_wb_release="$_to_wb_dev"
    _to_wb_dev=""
    _to_wb_limit=""
    _to_wb_enable=""
  fi

  # Never reset with swap in use: in-use pages would be lost.
  if ! abk_zram_swap_idle_enough; then
    if [ -n "$_to_wb_dev" ]; then
      abk_warn "swap in use ($(abk_zram_swap_used_kb) KiB): keeping the live writeback device ($_to_wb_dev) instead of rewriting"
    else
      abk_warn "swap in use ($(abk_zram_swap_used_kb) KiB > ${ABK_ZRAM_MIN_FREE_MB} MiB); leaving zram alone"
    fi
    return 1
  fi

  _to_what="primary=$ABK_ZRAM_PRIMARY secondary=$ABK_ZRAM_SECONDARY"
  if [ -n "$_to_wb_dev" ]; then
    _to_what="$_to_what writeback=$_to_wb_dev (preserved)"
  elif abk_zram_writeback_wanted; then
    _to_what="$_to_what writeback=new"
  fi
  abk_log "rewrite: size=$_to_size prio=$_to_prio $_to_what"

  if abk_zram_swap_on; then
    if [ "${ABK_DRY_RUN:-0}" = "1" ]; then
      abk_log "[dry-run] $ABK_SWAPOFF $ABK_ZRAM_NODE"
    elif ! "$ABK_SWAPOFF" "$ABK_ZRAM_NODE" >/dev/null 2>&1; then
      abk_warn "swapoff failed; leaving zram alone"
      return 1
    fi
  fi

  if [ "${ABK_DRY_RUN:-0}" != "1" ]; then
    abk_write "$ABK_ZRAM_DIR/reset" 1 || true
    _to_n=0
    while [ "$_to_n" -lt 10 ] && [ "$(abk_zram_initstate)" = "1" ]; do
      sleep 1
      _to_n=$(( _to_n + 1 ))
    done
  fi

  if [ -n "$_to_wb_release" ]; then
    abk_zram_release_backing_dev "$_to_wb_release"
  fi

  if ! abk_zram_set_algorithms; then
    abk_warn "algorithm rewrite failed; restoring the saved configuration"
    if [ "$(abk_zram_initstate)" = "1" ]; then
      abk_zram_mount_swap_area "$_to_prio" || true
    else
      abk_zram_mount_swap "$_to_size" "$_to_prio" || true
    fi
    abk_zram_swap_on || abk_warn "RECOVERY FAILED: no swap area is mounted"
    return 1
  fi

  _to_wb_ok=0
  if [ -n "$_to_wb_dev" ]; then
    if abk_zram_attach_writeback "$_to_wb_dev" "$_to_wb_limit"; then
      _to_wb_ok=1
      [ -z "$_to_wb_enable" ] || [ "$_to_wb_enable" = "1" ] \
        || abk_write "$ABK_ZRAM_DIR/writeback_limit_enable" "$_to_wb_enable" \
        || true
    else
      abk_warn "writeback device $_to_wb_dev could not be re-attached; trying a fresh backing device"
    fi
  fi
  if [ "$_to_wb_ok" = 0 ] && abk_zram_writeback_wanted; then
    if abk_zram_attach_writeback "" ""; then
      _to_wb_ok=1
    else
      abk_zram_wb_backoff_arm
      abk_warn "writeback could not be set up; standing down auto-attach for ${ABK_ZRAM_WB_RETRY_SECS}s (continuing without it)"
    fi
  fi
  [ "$_to_wb_ok" = 0 ] || abk_zram_wb_backoff_clear

  if ! abk_zram_mount_swap "$_to_size" "$_to_prio"; then
    abk_warn "swap remount failed; retrying the swap area"
    if [ "$(abk_zram_initstate)" = "1" ]; then
      abk_zram_mount_swap_area "$_to_prio" || true
    else
      abk_zram_mount_swap "$_to_size" "$_to_prio" || true
    fi
    if [ "$(abk_zram_initstate)" != "1" ] || ! abk_zram_swap_on; then
      abk_warn "RECOVERY FAILED: no swap area is mounted"
      return 1
    fi
    abk_log "swap area recovered on the retry"
  fi

  _to_state="$(abk_zram_initstate)"
  if [ "$_to_state" != "1" ]; then
    abk_warn "zram initstate is '$_to_state' after the rewrite"
    return 1
  fi
  if ! abk_zram_swap_on; then
    abk_warn "no swap area on $ABK_ZRAM_NODE after the rewrite"
    return 1
  fi

  abk_log "zram ready: primary=$(abk_zram_primary) secondary=$(abk_zram_secondary) size=$_to_size prio=$_to_prio"
  return 0
}

abk_zram_need_rewrite() {
  abk_zram_algorithms_ok || return 0
  [ "$(abk_zram_initstate)" = "1" ] || return 0
  abk_zram_writeback_wanted && return 0
  return 1
}

abk_zram_need_swap_area() {
  [ "$(abk_zram_initstate)" = "1" ] || return 1
  abk_zram_algorithms_ok || return 1
  abk_zram_writeback_wanted && return 1
  abk_zram_swap_on && return 1
  return 0
}

abk_zram_ensure() {
  if ! abk_zram_present; then
    abk_warn "no $ABK_ZRAM_DIR; nothing to configure"
    return 1
  fi

  abk_zram_drop_stale_backing_file

  if ! abk_zram_need_rewrite; then
    if abk_zram_need_swap_area; then
      abk_log "swap area missing on an initialised device; repairing without a reset"
      if abk_zram_mount_swap_area "$(abk_zram_swap_prio)"; then
        abk_log "swap area repaired: prio=$(abk_zram_swap_prio)"
        return 0
      fi
      abk_warn "swap area repair failed; the supervisor will retry"
      return 1
    fi
    abk_zram_set_mem_limit
    return 0
  fi

  abk_log "zram policy not in force (primary=$(abk_zram_primary) secondary=$(abk_zram_secondary) initstate=$(abk_zram_initstate))$(abk_zram_swap_on || printf ' swap=off')"
  if abk_zram_kernel_locked; then
    abk_log "the kernel locks the compressors (zram.abk_lock_algo=Y, zram.abk_comp_algo=$(abk_read "$ABK_ZRAM_COMP_PARAM" | tr -d ' \n'))"
  fi
  abk_zram_takeover
}

abk_zram_reassert() {
  if abk_zram_present && ! abk_zram_algorithms_ok; then
    abk_warn "algorithms changed behind the module (primary=$(abk_zram_primary) secondary=$(abk_zram_secondary)); re-applying"
  fi
  abk_zram_ensure
}

abk_zram_wait_ready() {
  _wr_n=0
  while [ "$_wr_n" -lt "$ABK_ZRAM_WAIT_SECS" ]; do
    if [ "$(abk_zram_initstate)" = "1" ]; then
      abk_log "zram initialised after ${_wr_n}s"
      return 0
    fi
    if abk_zram_mmd_done; then
      abk_log "mmd.setup_complete after ${_wr_n}s"
      return 0
    fi
    sleep 1
    _wr_n=$(( _wr_n + 1 ))
  done
  abk_warn "zram not initialised after ${ABK_ZRAM_WAIT_SECS}s; continuing anyway"
  return 0
}

abk_zram_compact_if_fragmented() {
  [ "$(abk_cfg zram.compact.enable 1)" = "1" ] || return 0

  _cz_node="$ABK_ZRAM_DIR/compact"
  [ -e "$_cz_node" ] || return 0

  _cz_val="$(abk_cfg zram.compact.min_waste_mb 50)"
  if ! _cz_min_mb="$(abk_clamp_uint "$_cz_val" 1 1024)"; then
    abk_warn "zram.compact.min_waste_mb: '$_cz_val' is not a number in 1..1024, using 50"
    _cz_min_mb=50
  fi
  _cz_val="$(abk_cfg zram.compact.waste_pct 15)"
  if ! _cz_pct="$(abk_clamp_uint "$_cz_val" 1 500)"; then
    abk_warn "zram.compact.waste_pct: '$_cz_val' is not a number in 1..500, using 15"
    _cz_pct=15
  fi

  _cz_verdict="$(abk_read "$ABK_ZRAM_DIR/mm_stat" \
    | awk -v min_mb="$_cz_min_mb" -v pct="$_cz_pct" '
        NF >= 3 {
          compr = $2; used = $3
          if (used - compr > min_mb * 1048576 \
              && used * 100 > compr * (100 + pct)) print "go", used
          else print "skip"
          exit
        }')"
  case "$_cz_verdict" in
    go\ *) _cz_before="${_cz_verdict#go }" ;;
    *) return 0 ;;
  esac

  if abk_write "$_cz_node" 100; then
    _cz_after="$(abk_read "$ABK_ZRAM_DIR/mm_stat" | awk 'NF >= 3 { print $3; exit }')"
    _cz_freed="$(printf '%s %s\n' "$_cz_before" "${_cz_after:-$_cz_before}" \
      | awk '{ print $1 - $2 }')"
    abk_log "zsmalloc compaction: used $_cz_before -> ${_cz_after:-?} bytes (reclaimed $_cz_freed)"
  fi
  return 0
}

abk_zram_writeback_sweep() {
  _wb_trigger="$(abk_cfg zram.writeback.trigger off)"
  case "$_wb_trigger" in
    off) return 0 ;;
    huge|idle) ;;
    *) abk_warn "zram.writeback.trigger: '$_wb_trigger' is not off/huge/idle; writeback left alone"
       return 0 ;;
  esac

  abk_zram_writeback_capable || return 0
  _wb_bdev="$(abk_read "$ABK_ZRAM_DIR/backing_dev" | tr -d ' \n')"
  [ -n "$_wb_bdev" ] || return 0

  _wb_val="$(abk_cfg zram.writeback.budget_mb 256)"
  if ! _wb_mb="$(abk_clamp_uint "$_wb_val" 1 65536)"; then
    abk_warn "zram.writeback.budget_mb: '$_wb_val' is not a number in 1..65536, using 256"
    _wb_mb=256
  fi
  _wb_blocks="$(abk_mul_div "$_wb_mb" 256 1)"

  _wb_sectors="$(abk_read "$ABK_SYS_ROOT/block/${_wb_bdev##*/}/size" | tr -d ' \n')"
  if abk_is_uint "$_wb_sectors" && abk_gt "$_wb_sectors" 0; then
    _wb_cap="$(abk_mul_div "$_wb_sectors" 1 8)"
    abk_le "$_wb_blocks" "$_wb_cap" || _wb_blocks="$_wb_cap"
  fi

  if [ "$_wb_trigger" = idle ]; then
    _wb_val="$(abk_cfg zram.writeback.idle_age_sec 7200)"
    if ! _wb_age="$(abk_clamp_uint "$_wb_val" 60 2592000)"; then
      abk_warn "zram.writeback.idle_age_sec: '$_wb_val' is not a number in 60..2592000, using 7200"
      _wb_age=7200
    fi
    if ! abk_write "$ABK_ZRAM_DIR/idle" "$_wb_age"; then
      abk_warn "writeback idle sweep skipped: the idle node refused an age of ${_wb_age}s"
      return 0
    fi
  fi

  abk_write "$ABK_ZRAM_DIR/writeback_limit" "$_wb_blocks" || return 0
  abk_write "$ABK_ZRAM_DIR/writeback_limit_enable" 1 || return 0

  _wb_before="$(abk_read "$ABK_ZRAM_DIR/bd_stat" | tr -s ' ')"
  if [ ! -e "$ABK_ZRAM_DIR/writeback" ]; then
    abk_warn "writeback sweep skipped: $ABK_ZRAM_DIR/writeback is missing"
    return 0
  fi
  echo "$_wb_trigger" > "$ABK_ZRAM_DIR/writeback" 2>/dev/null
  _wb_rc=$?
  _wb_after="$(abk_read "$ABK_ZRAM_DIR/bd_stat" | tr -s ' ')"

  if [ "$_wb_rc" -ne 0 ] && [ "$_wb_before" = "$_wb_after" ]; then
    abk_warn "writeback sweep moved nothing (rc=$_wb_rc, bd_stat $_wb_after): under Enforcing this is the -EIO of a missing sepolicy rule for the backing file's type, or a budget that was already spent"
  else
    abk_log "writeback sweep mode=$_wb_trigger budget=${_wb_blocks} blocks: bd_stat $_wb_before -> $_wb_after (rc=$_wb_rc)"
  fi
  return 0
}

abk_kcompressd_monitor_tick() {
  _kcm_line="$(abk_kcompressd_state_line)"
  abk_log "$_kcm_line"

  _kcm_thr="$(printf '%s\n' "$_kcm_line" \
    | sed -n 's/^kcompressd: .*threshold=\([0-9][0-9]*\).*$/\1/p')"
  _kcm_depth="$(printf '%s\n' "$_kcm_line" \
    | sed -n 's/^kcompressd: .*depth=\([0-9][0-9]*\).*$/\1/p')"

  _kcm_limit="$(abk_cfg kcompressd.depth_warn '')"
  abk_is_uint "$_kcm_limit" || _kcm_limit=""
  if [ "$_kcm_limit" = 0 ]; then
    _kcm_limit=""
  fi
  if [ -z "$_kcm_limit" ]; then
    _kcm_limit="$_kcm_thr"
  fi
  if [ "$_kcm_limit" = 0 ]; then
    _kcm_limit=""
  fi

  if [ -z "$_kcm_depth" ] || [ -z "$_kcm_limit" ]; then
    ABK_KCD_BAD_STREAK=0
    return 0
  fi

  if abk_le "$_kcm_limit" "$_kcm_depth"; then
    abk_warn "kcompressd: queue saturated (depth=$_kcm_depth >= threshold=$_kcm_limit); head-drain is engaged, so each enqueue also pays one synchronous write"
  fi

  _kcm_swc="$(abk_meminfo_kb SwapCached)"
  _kcm_warn_mb="$(abk_cfg kcompressd.swapcached_warn_mb 256)"
  abk_is_uint "$_kcm_warn_mb" || _kcm_warn_mb=256
  _kcm_warn_kb="$(abk_mul_div "$_kcm_warn_mb" 1024 1)"
  if abk_is_uint "$_kcm_swc" && abk_le "$_kcm_depth" "$_kcm_limit" \
    && abk_le "$_kcm_warn_kb" "$_kcm_swc"; then
    ABK_KCD_BAD_STREAK=$(( ${ABK_KCD_BAD_STREAK:-0} + 1 ))
    if [ "$ABK_KCD_BAD_STREAK" -ge 2 ]; then
      abk_warn "kcompressd: written-but-not-dropped shape (depth=$_kcm_depth <= threshold=$_kcm_limit, SwapCached=${_kcm_swc}kB >= ${_kcm_warn_mb}MiB) for $ABK_KCD_BAD_STREAK consecutive checks"
      ABK_KCD_BAD_STREAK=0
    fi
  else
    ABK_KCD_BAD_STREAK=0
  fi
  return 0
}

abk_zram_supervisor_main() {
  abk_pid_write zram "$$"
  _zs_tool="${ABK_RECOMP_TOOL:-$MODDIR/bin/zram_recompress_trigger.sh}"
  _zs_age="$(abk_cfg zram.recomp.idle_age_sec 900)"
  _zs_interval="$(abk_cfg zram.recomp.interval_sec 1800)"
  _zs_threshold="$(abk_cfg zram.recomp.threshold 0)"
  _zs_max_pages="$(abk_cfg zram.recomp.max_pages 131072)"
  _zs_mode="$(abk_cfg zram.recomp.mode async)"
  _zs_reassert="$(abk_cfg zram.reassert_interval_sec "$ABK_ZRAM_REASSERT_SECS")"

  abk_is_uint "$_zs_age" || _zs_age=900
  abk_is_uint "$_zs_interval" || _zs_interval=1800
  abk_is_uint "$_zs_threshold" || _zs_threshold=0
  abk_is_uint "$_zs_max_pages" || _zs_max_pages=131072
  abk_is_uint "$_zs_reassert" || _zs_reassert="$ABK_ZRAM_REASSERT_SECS"
  case "$_zs_mode" in sync|async) ;; *) _zs_mode=async ;; esac
  [ "$_zs_interval" -ge 60 ] || _zs_interval=60
  [ "$_zs_reassert" -ge 5 ] || _zs_reassert=5

  _zs_per_sweep=$(( _zs_interval / _zs_reassert ))
  [ "$_zs_per_sweep" -ge 1 ] || _zs_per_sweep=1

  _zs_wb_interval="$(abk_cfg zram.writeback.interval_sec 86400)"
  abk_is_uint "$_zs_wb_interval" || _zs_wb_interval=86400
  [ "$_zs_wb_interval" -ge 60 ] || _zs_wb_interval=60
  _zs_per_wb=$(( _zs_wb_interval / _zs_reassert ))
  [ "$_zs_per_wb" -ge 1 ] || _zs_per_wb=1

  _zs_mark_interval="$(abk_cfg zram.recomp.mark_interval_sec 3600)"
  abk_is_uint "$_zs_mark_interval" || _zs_mark_interval=3600
  [ "$_zs_mark_interval" -ge 60 ] || _zs_mark_interval=60
  _zs_per_mark=$(( _zs_mark_interval / _zs_interval ))
  [ "$_zs_per_mark" -ge 1 ] || _zs_per_mark=1

  _zs_kcd_interval="$(abk_cfg kcompressd.monitor_interval_sec 600)"
  abk_is_uint "$_zs_kcd_interval" || _zs_kcd_interval=600
  [ "$_zs_kcd_interval" -ge 60 ] || _zs_kcd_interval=60
  _zs_per_kcd=$(( _zs_kcd_interval / _zs_reassert ))
  [ "$_zs_per_kcd" -ge 1 ] || _zs_per_kcd=1
  _zs_pol_interval="$(abk_cfg policy.reassert_interval_sec 300)"
  abk_is_uint "$_zs_pol_interval" || _zs_pol_interval=300
  [ "$_zs_pol_interval" -ge 60 ] || _zs_pol_interval=60
  [ "$_zs_pol_interval" -le 86400 ] || _zs_pol_interval=86400
  _zs_per_pol=$(( _zs_pol_interval / _zs_reassert ))
  [ "$_zs_per_pol" -ge 1 ] || _zs_per_pol=1

  if [ ! -f "$_zs_tool" ]; then
    abk_warn "$_zs_tool is missing; recompression sweeps disabled"
    return 1
  fi

  abk_log "recompression supervisor up: age=${_zs_age}s interval=${_zs_interval}s mark=${_zs_mark_interval}s mode=$_zs_mode threshold=$_zs_threshold cap=${_zs_max_pages} reassert=${_zs_reassert}s compact=$(abk_cfg zram.compact.enable 1)>$(abk_cfg zram.compact.min_waste_mb 50)MB+$(abk_cfg zram.compact.waste_pct 15)%"

  _zs_wb_mode="$(abk_cfg zram.writeback.trigger off)"
  if [ "$_zs_wb_mode" != "off" ]; then
    abk_log "writeback sweeps armed: mode=$_zs_wb_mode interval=${_zs_wb_interval}s budget=$(abk_cfg zram.writeback.budget_mb 256)MiB"
  fi

  _zs_tick=0
  _zs_wb_tick=0
  _zs_kcd_tick="$_zs_per_kcd"
  _zs_pol_tick=0
  ABK_KCD_BAD_STREAK=0
  _zs_mark_tick="$_zs_per_mark"
  _zs_mark_arg=""
  while :; do
    abk_zram_reassert || true

    _zs_kcd_tick=$(( _zs_kcd_tick + 1 ))
    if [ "$_zs_kcd_tick" -ge "$_zs_per_kcd" ]; then
      _zs_kcd_tick=0
      abk_kcompressd_monitor_tick || true
    fi

    _zs_pol_tick=$(( _zs_pol_tick + 1 ))
    if [ "$_zs_pol_tick" -ge "$_zs_per_pol" ]; then
      _zs_pol_tick=0
      abk_apply_policy_knobs || true
    fi

    _zs_wb_tick=$(( _zs_wb_tick + 1 ))
    if [ "$_zs_wb_tick" -ge "$_zs_per_wb" ]; then
      _zs_wb_tick=0
      abk_zram_writeback_sweep || true
    fi

    _zs_tick=$(( _zs_tick + 1 ))
    if [ "$_zs_tick" -ge "$_zs_per_sweep" ]; then
      _zs_tick=0
      _zs_mark_tick=$(( _zs_mark_tick + 1 ))
      if [ "$_zs_mark_tick" -ge "$_zs_per_mark" ]; then
        _zs_mark_tick=0
        _zs_mark_arg=""
      else
        _zs_mark_arg="--no-mark"
      fi
      # shellcheck disable=SC2086 # _zs_mark_arg is a deliberate flag list
      sh "$_zs_tool" --sys-root "$ABK_SYS_ROOT" --device "$ABK_ZRAM_DEV" \
        --idle-age "$_zs_age" --mode "$_zs_mode" \
        --threshold "$_zs_threshold" --max-pages "$_zs_max_pages" \
        $_zs_mark_arg >/dev/null 2>&1
      _zs_rc=$?
      case "$_zs_rc" in
        0) abk_log "recompression sweep done (age=${_zs_age}s mode=$_zs_mode cap=$_zs_max_pages${_zs_mark_arg:+ mark=skipped})" ;;
        3) abk_warn "recompression sweep refused: the secondary equals the primary" ;;
        *) abk_warn "recompression sweep failed (rc=$_zs_rc)" ;;
      esac
      abk_zram_compact_if_fragmented || true
    fi
    sleep "$_zs_reassert"
  done
}

ABK_MEMCG_ROOT="${ABK_MEMCG_ROOT:-/dev/memcg}"

abk_cfr_supervisor_main() {
  abk_pid_write cfr "$$"
  _cf_tool="${ABK_CFR_TOOL:-$MODDIR/bin/cached_freeze_reclaim.sh}"
  _cf_interval="$(abk_cfg cfr.interval_sec 300)"
  _cf_freeze="$(abk_cfg cfr.freeze 0)"
  _cf_quota="$(abk_cfg cfr.quota_mb '')"
  _cf_group="$(abk_cfg cfr.group '')"
  _cf_roots="$(abk_cfg cfr.cgroup_root '')"
  _cf_frozen="$(abk_cfg cfr.frozen_only 0)"
  _cf_freezer="$(abk_cfg cfr.freezer_root '')"
  _cf_cached="$(abk_cfg cfr.cached_only 0)"
  abk_is_uint "$_cf_interval" || _cf_interval=300
  [ "$_cf_interval" -ge 30 ] || _cf_interval=30
  case "$_cf_freeze" in 0|1) ;; *) _cf_freeze=0 ;; esac
  case "$_cf_frozen" in 0|1) ;; *) _cf_frozen=0 ;; esac
  case "$_cf_cached" in 0|1) ;; *) _cf_cached=0 ;; esac
  abk_is_uint "$_cf_quota" || _cf_quota=0
  [ "$_cf_frozen" = 1 ] || _cf_freezer=""

  if [ ! -f "$_cf_tool" ]; then
    abk_warn "$_cf_tool is missing; proactive reclaim disabled"
    return 1
  fi

  _cf_group_args=""
  for _cf_g in $_cf_group; do
    _cf_group_args="$_cf_group_args --group $_cf_g"
  done
  _cf_root_args=""
  for _cf_r in $_cf_roots; do
    _cf_root_args="$_cf_root_args --cgroup-root $_cf_r"
  done

  abk_log "proactive reclaim supervisor up: interval=${_cf_interval}s freeze=$_cf_freeze quota=${_cf_quota}MiB groups=${_cf_group:-<uid_*>} roots=${_cf_roots:-<default>} frozen_only=$_cf_frozen cached_only=$_cf_cached freezer_root=${_cf_freezer:-<none>}"
  while :; do
    _cf_args="--cgroup-root $ABK_SYS_ROOT/fs/cgroup --cgroup-root $ABK_MEMCG_ROOT$_cf_root_args"
    [ "$_cf_freeze" = "1" ] && _cf_args="$_cf_args --freeze"
    [ "$_cf_quota" -gt 0 ] && _cf_args="$_cf_args --quota-mb $_cf_quota"
    [ "$_cf_frozen" = "1" ] && _cf_args="$_cf_args --frozen-only"
    [ -n "$_cf_freezer" ] && _cf_args="$_cf_args --freezer-root $_cf_freezer"
    [ "$_cf_cached" = "1" ] && _cf_args="$_cf_args --cached-only"
    _cf_args="$_cf_args$_cf_group_args"

    # shellcheck disable=SC2086 # _cf_args is a deliberately word-split flag list
    if CFR_ONE_SHOT=1 sh "$_cf_tool" $_cf_args >/dev/null 2>&1; then
      abk_log "proactive reclaim sweep done (freeze=$_cf_freeze quota=${_cf_quota}MiB groups=${_cf_group:-<uid_*>} frozen_only=$_cf_frozen cached_only=$_cf_cached)"
    else
      abk_warn "proactive reclaim found no reclaimable group (groups=${_cf_group:-<uid_*>} frozen_only=$_cf_frozen cached_only=$_cf_cached)"
    fi
    sleep "$_cf_interval"
  done
}
