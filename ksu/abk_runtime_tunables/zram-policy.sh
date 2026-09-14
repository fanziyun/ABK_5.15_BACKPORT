#!/system/bin/sh
# zram-policy.sh - the zram half of the ABK 5.15 runtime-tunables module.
#
# The policy is fixed, not configurable (see README.md):
#   primary  = lz4kd   (best fast compressor measured on this SoC)
#   secondary = zstd   (best ratio per cost; the async worker pays for it)
#   mem_limit = 25% of RAM
#   swap size = whatever the ROM already had (preserved), else MemTotal/2
#
# Two kernels behave differently, and the module tells them apart instead of
# assuming:
#
#   * with the Batch 11 lock (zram.abk_lock_algo=Y, default) the compressors
#     are chosen by the kernel at device creation and a later write is accepted
#     and ignored.  The policy is in force from the first `disksize`, so the
#     module only re-asserts what a reset drops (the compressed-memory cap) and
#     verifies the rest.  Nothing has to be taken over, which is what lets the
#     ROM's own bring-up -- writeback backing device included -- run untouched.
#
#   * without the lock, the nodes are only writable before `disksize`, so
#     whoever wins that window decides the algorithm for the whole boot.  The
#     module then owns the bring-up: swapoff -> reset -> algorithms -> (writeback)
#     -> disksize -> mem_limit -> mkswap -> swapon, and a supervisor re-checks
#     the result every ABK_ZRAM_REASSERT_SECS.
#
# Writeback and the algorithm policy used to exclude each other, because the
# backing device is written in the same pre-`disksize` window and `reset_bdev()`
# drops it on a reset.  Now the module restores the attachment it found instead
# of sacrificing it, and attaches one itself when the kernel supports it and
# nobody owns it (zram.writeback=auto).
#
# Interlock that stays: a device whose swap area is in use is never rewritten
# just to rearrange algorithms -- `swapoff` pulls every swapped page back into
# RAM first, and that transient spike is what ABK_ZRAM_MIN_FREE_MB bounds.

ABK_ZRAM_DIR="$ABK_SYS_ROOT/block/zram$ABK_ZRAM_DEV"
ABK_ZRAM_LOCK_PARAM="$ABK_SYS_ROOT/module/zram/parameters/abk_lock_algo"
ABK_ZRAM_COMP_PARAM="$ABK_SYS_ROOT/module/zram/parameters/abk_comp_algo"

abk_zram_present() {
  [ -d "$ABK_ZRAM_DIR" ] && [ -e "$ABK_ZRAM_DIR/disksize" ]
}

# Active algorithm of a comp_algorithm-style node: the bracketed token, the
# `algo=<name>` argument we wrote before disksize, or the last token of the
# first line.
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

# --- /proc/swaps ---------------------------------------------------------
# Fields: Filename Type Size(KiB) Used(KiB) Priority
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

# The rewrite drops whatever is stored in zram, so it only runs while the swap
# area is effectively idle.  At boot that is the normal state (measured: 0 KiB
# used); later in the boot the gate protects live pages.
abk_zram_swap_idle_enough() {
  _si_used="$(abk_zram_swap_used_kb)"
  if [ -z "$_si_used" ]; then
    return 0
  fi
  abk_is_uint "$_si_used" || return 1
  _si_limit=$(( ABK_ZRAM_MIN_FREE_MB * 1024 ))
  # abk_le, not [ -le ]: this ROM's mksh parses operands through 32-bit
  # arithmetic, where a large "used" would compare as negative.
  abk_le "$_si_used" "$_si_limit"
}

abk_zram_writeback_capable() {
  [ -e "$ABK_ZRAM_DIR/writeback" ]
}

abk_zram_mmd_done() {
  _md="$(abk_getprop mmd.setup_complete)"
  [ "$_md" = "true" ]
}

# The backing device path, or empty for the kernel's "none".
abk_zram_backing_dev() {
  _bd_val="$(abk_read "$ABK_ZRAM_DIR/backing_dev" | tr -d ' \n')"
  case "$_bd_val" in none) return 0 ;; esac
  printf '%s\n' "$_bd_val"
  return 0
}

# Somebody else already relies on writeback here: a live backing device, or a
# completed ROM setup (mmd_setup only finishes after attaching one).
abk_zram_writeback_owned() {
  [ -n "$(abk_zram_backing_dev)" ] && return 0
  abk_zram_mmd_done && return 0
  return 1
}

# auto: attach one during the module's own bring-up when the kernel supports it
# and nobody owns it.  off: never attach one (an existing one is still kept).
abk_zram_writeback_mode() {
  case "$(abk_cfg zram.writeback auto)" in
    off|0|no) printf 'off\n' ;;
    *) printf 'auto\n' ;;
  esac
}

abk_zram_writeback_wanted() {
  abk_zram_writeback_capable || return 1
  [ "$(abk_zram_writeback_mode)" = "auto" ] || return 1
  abk_zram_writeback_owned && return 1
  return 0
}

abk_zram_writeback_size_mb() {
  _ws_mb="$(abk_cfg zram.writeback.size_mb 1024)"
  abk_is_uint "$_ws_mb" || _ws_mb=1024
  [ "$_ws_mb" -ge 64 ] || _ws_mb=64
  [ "$_ws_mb" -le 8192 ] || _ws_mb=8192
  printf '%s\n' "$_ws_mb"
}

# A loop device over a sparse file, the same shape Android's mmd_setup uses.
# Sparse on purpose: with compressed writeback the kernel only writes the 4 KiB
# blocks zram actually moves out, and nothing else is ever touched.  Prints the
# loop device path, or fails with a reason in the log.
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

  # seek= writes the last block: the file ends up exactly _cb_mb long while
  # every other block stays sparse.  (count=0 would leave a zero-length file,
  # which the kernel rejects as a backing device.)
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

# abk_zram_attach_writeback <reuse-device|''> <reuse-limit|''>
#
# Only valid before `disksize`: backing_dev_store() refuses an initialized
# device, and reset_bdev() has just closed whatever was attached, so this is
# where the writeback half of a rewrite is put back.
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

  # Newer kernels can store the written-back pages still compressed, so the
  # decompression is deferred to whoever reads the slot back instead of being
  # paid by the sweep.  It does NOT reduce the flash traffic: the bio is still
  # one full PAGE_SIZE per slot (the tail is zero-padded), so the backing
  # device sees the same 4 KiB writes either way -- measured on vermeer,
  # research/zram/vermeer_batch17_check/FINDINGS.md section 3.2.  The win is
  # CPU on the write path; the cost is CPU on the read path.
  if [ -e "$ABK_ZRAM_DIR/compressed_writeback" ]; then
    abk_write "$ABK_ZRAM_DIR/compressed_writeback" 1 || true
  fi

  # Bound how much flash the sweeps may consume; the unit is 4 KiB blocks.
  abk_write "$ABK_ZRAM_DIR/writeback_limit" "$_aw_limit" || true
  abk_write "$ABK_ZRAM_DIR/writeback_limit_enable" 1 || true
  abk_log "writeback: backing device $_aw_dev (limit ${_aw_limit} blocks)"
  return 0
}

# --- bring-up primitives -------------------------------------------------
abk_zram_saved_disksize() {
  _sd_live="$(abk_zram_disksize)"
  # abk_gt, not [ -gt ]: a 16 GiB disksize is 0 to this ROM's mksh, which would
  # make the module treat a perfectly good device as sizeless and halve it.
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
  # With the Batch 11 kernel lock these writes are accepted and ignored (the
  # locked names stay), so the readback below -- not the write -- decides.  On
  # a kernel without the lock both writes must land.
  abk_write "$ABK_ZRAM_DIR/comp_algorithm" "$ABK_ZRAM_PRIMARY" || true
  abk_write "$ABK_ZRAM_DIR/recomp_algorithm" \
    "algo=$ABK_ZRAM_SECONDARY priority=1" || true

  abk_zram_algorithms_ok
}

# Is the measured policy already in force on the device?
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

# The kernel's own lock: with it the compressors are chosen at device creation
# and a later write cannot change them, so no rewrite of the device is needed
# for the algorithm half of the policy at all.
abk_zram_kernel_locked() {
  case "$(abk_read "$ABK_ZRAM_LOCK_PARAM" | tr -d ' \n')" in
    Y|y|1) return 0 ;;
    *) return 1 ;;
  esac
}

abk_zram_set_mem_limit() {
  # 64-bit safe (see abk_mul_div): the product does not fit in the 32-bit
  # arithmetic this ROM's /system/bin/sh uses, and a wrapped value would be
  # rejected by the kernel, silently leaving the cap off.
  _ml_val="$(abk_mem_pct_bytes "$ABK_ZRAM_MEM_LIMIT_PCT")" || return 0
  abk_is_uint "$_ml_val" || return 0
  abk_write "$ABK_ZRAM_DIR/mem_limit" "$_ml_val" || return 0
  _ml_seen="$(abk_zram_mem_limit)"
  if [ "$_ml_seen" = "$_ml_val" ]; then
    abk_log "zram mem_limit=$_ml_val bytes (${ABK_ZRAM_MEM_LIMIT_PCT}% of RAM)"
  else
    abk_warn "mem_limit readback is '$_ml_seen', wanted $_ml_val"
  fi
  return 0
}

# abk_zram_mount_swap <disksize> [priority]
abk_zram_mount_swap() {
  _ms_size="$1"
  _ms_prio="$2"
  abk_is_uint "$_ms_prio" || _ms_prio=0

  abk_write "$ABK_ZRAM_DIR/disksize" "$_ms_size" || return 1
  abk_zram_set_mem_limit

  if [ "${ABK_DRY_RUN:-0}" = "1" ]; then
    abk_log "[dry-run] $_ABK_MKSWAP $_ABK_ZRAM_NODE"
    abk_log "[dry-run] $_ABK_SWAPON -p $_ms_prio $_ABK_ZRAM_NODE"
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

# --- takeover / rewrite --------------------------------------------------
# The rewrite is the repair path: it rebuilds the device with the measured
# algorithms, the compressed-memory cap, the swap area it found, and -- on a
# kernel that has it -- the writeback backing device, which `reset` drops
# (reset_bdev() closes it) and which is therefore restored in the same
# pre-`disksize` window it came from.
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
  if abk_zram_writeback_capable; then
    _to_wb_dev="$(abk_zram_backing_dev)"
    _to_wb_limit="$(abk_read "$ABK_ZRAM_DIR/writeback_limit" | tr -d ' \n')"
    _to_wb_enable="$(abk_read "$ABK_ZRAM_DIR/writeback_limit_enable" | tr -d ' \n')"
  fi

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

  if ! abk_zram_set_algorithms; then
    abk_warn "algorithm rewrite failed; restoring the saved configuration"
    abk_zram_mount_swap "$_to_size" "$_to_prio" \
      || abk_warn "RECOVERY FAILED: no swap area is mounted"
    return 1
  fi

  # Writeback is attached before disksize: the node is -EBUSY afterwards.
  if [ -n "$_to_wb_dev" ]; then
    if abk_zram_attach_writeback "$_to_wb_dev" "$_to_wb_limit"; then
      [ -z "$_to_wb_enable" ] || [ "$_to_wb_enable" = "1" ] \
        || abk_write "$ABK_ZRAM_DIR/writeback_limit_enable" "$_to_wb_enable" \
        || true
    else
      abk_warn "writeback device $_to_wb_dev could not be re-attached"
    fi
  elif abk_zram_writeback_wanted; then
    abk_zram_attach_writeback "" "" \
      || abk_warn "writeback could not be set up; continuing without it"
  fi

  if ! abk_zram_mount_swap "$_to_size" "$_to_prio"; then
    abk_warn "swap remount failed; retrying once"
    abk_zram_mount_swap "$_to_size" "$_to_prio" \
      || abk_warn "RECOVERY FAILED: no swap area is mounted"
    return 1
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

# Does the device still need the rewrite above?  Only three things justify it:
# an algorithm the policy does not want (only possible on a kernel without the
# Batch 11 lock), an uninitialised device / missing swap area, or writeback the
# kernel supports, our policy wants and nobody has claimed.
abk_zram_need_rewrite() {
  abk_zram_algorithms_ok || return 0
  [ "$(abk_zram_initstate)" = "1" ] || return 0
  abk_zram_swap_on || return 0
  abk_zram_writeback_wanted && return 0
  return 1
}

# The one entry point the service, the supervisors and action.sh share: put the
# policy in force, and do nothing (beyond the cap a reset would have dropped)
# when it already is.
abk_zram_ensure() {
  if ! abk_zram_present; then
    abk_warn "no $ABK_ZRAM_DIR; nothing to configure"
    return 1
  fi

  if ! abk_zram_need_rewrite; then
    abk_zram_set_mem_limit
    return 0
  fi

  abk_log "zram policy not in force (primary=$(abk_zram_primary) secondary=$(abk_zram_secondary) initstate=$(abk_zram_initstate))$(abk_zram_swap_on || printf ' swap=off')"
  if abk_zram_kernel_locked; then
    abk_log "the kernel locks the compressors (zram.abk_lock_algo=Y, zram.abk_comp_algo=$(abk_read "$ABK_ZRAM_COMP_PARAM" | tr -d ' \n'))"
  fi
  abk_zram_takeover
}

# Re-check that nothing switched the compressors behind us -- a ROM daemon, a
# root shell or an app with su can write the nodes before `disksize` and leave
# the device on an algorithm the policy does not want.  With the Batch 11
# kernel lock that write is a reported no-op, so this only ever fires on a
# kernel without it.
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

# --- fragmentation-gated zsmalloc compaction ------------------------------
# When a process dies its swap slots are freed in the middle of zspages, and
# those zspages stop serving their size class; the resulting overhead (and the
# measured kill-storm numbers behind this gate) is documented in README.md.
# Compacting a healthy device would be pure CPU for nothing, so one full pass
# runs only when BOTH gates say "fragmented":
#     mem_used_total - compr_data_size > zram.compact.min_waste_mb (MiB)
#     mem_used_total > compr_data_size * (1 + zram.compact.waste_pct / 100)
# All byte math lives in awk: both operands pass 2^31 on big devices and this
# ROM's shell arithmetic does not survive that (see abk_mul_div in common.sh).
abk_zram_compact_if_fragmented() {
  # Same enable convention as every other knob in the module: on only for "1".
  [ "$(abk_cfg zram.compact.enable 1)" = "1" ] || return 0

  _cz_node="$ABK_ZRAM_DIR/compact"
  [ -e "$_cz_node" ] || return 0   # kernel without the compact node

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

  # One awk pass over mm_stat: verdict + f3 (mem_used_total) together, so the
  # log line can never report numbers from a different parse than the gate.
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

  # "100" = the compact node's pass-size argument: 100 percent of zspages,
  # i.e. one full compaction pass.
  if abk_write "$_cz_node" 100; then
    _cz_after="$(abk_read "$ABK_ZRAM_DIR/mm_stat" | awk 'NF >= 3 { print $3; exit }')"
    _cz_freed="$(printf '%s %s\n' "$_cz_before" "${_cz_after:-$_cz_before}" \
      | awk '{ print $1 - $2 }')"
    abk_log "zsmalloc compaction: used $_cz_before -> ${_cz_after:-?} bytes (reclaimed $_cz_freed)"
  fi
  return 0
}

# --- recompression supervisor -------------------------------------------
# Two jobs on two different clocks: the policy is re-checked every
# zram.reassert_interval_sec (a writer that switches the compressors behind the
# module is repaired within that window), and the recompression sweeps run
# every zram.recomp.interval_sec.  The sweeps mark pages by age -- never "all",
# which would recompress everything every round -- and drive the async worker.
# Every sweep tick then ends with the gated zsmalloc compaction pass (see
# abk_zram_compact_if_fragmented): it fires only when the overhead gates call
# it fragmented, so a healthy device never pays for it.
abk_zram_supervisor_main() {
  abk_pid_write zram "$$"
  _zs_tool="${ABK_RECOMP_TOOL:-$MODDIR/bin/zram_recompress_trigger.sh}"
  _zs_age="$(abk_cfg zram.recomp.idle_age_sec 3600)"
  _zs_interval="$(abk_cfg zram.recomp.interval_sec 1800)"
  _zs_threshold="$(abk_cfg zram.recomp.threshold 0)"
  _zs_mode="$(abk_cfg zram.recomp.mode async)"
  _zs_reassert="$(abk_cfg zram.reassert_interval_sec "$ABK_ZRAM_REASSERT_SECS")"

  abk_is_uint "$_zs_age" || _zs_age=3600
  abk_is_uint "$_zs_interval" || _zs_interval=1800
  abk_is_uint "$_zs_threshold" || _zs_threshold=0
  abk_is_uint "$_zs_reassert" || _zs_reassert="$ABK_ZRAM_REASSERT_SECS"
  case "$_zs_mode" in sync|async) ;; *) _zs_mode=async ;; esac
  [ "$_zs_interval" -ge 60 ] || _zs_interval=60
  [ "$_zs_reassert" -ge 5 ] || _zs_reassert=5

  # Ticks, not `date +%s`: one sleep per tick, and the sweep runs on every
  # ticks_per_sweep-th tick.  A re-assert interval longer than the sweep
  # interval simply means one sweep per tick.
  _zs_per_sweep=$(( _zs_interval / _zs_reassert ))
  [ "$_zs_per_sweep" -ge 1 ] || _zs_per_sweep=1

  if [ ! -f "$_zs_tool" ]; then
    abk_warn "$_zs_tool is missing; recompression sweeps disabled"
    return 1
  fi

  abk_log "recompression supervisor up: age=${_zs_age}s interval=${_zs_interval}s mode=$_zs_mode threshold=$_zs_threshold reassert=${_zs_reassert}s compact=$(abk_cfg zram.compact.enable 1)>$(abk_cfg zram.compact.min_waste_mb 50)MB+$(abk_cfg zram.compact.waste_pct 15)%"

  # The tool's own --daemon loop would block this process, and the supervisor
  # has two jobs the tool cannot do: keep the policy in force between passes,
  # and stay alive -- with a log line -- when a pass fails.
  _zs_tick=0
  while :; do
    abk_zram_reassert || true

    _zs_tick=$(( _zs_tick + 1 ))
    if [ "$_zs_tick" -ge "$_zs_per_sweep" ]; then
      _zs_tick=0
      sh "$_zs_tool" --sys-root "$ABK_SYS_ROOT" --device "$ABK_ZRAM_DEV" \
        --idle-age "$_zs_age" --mode "$_zs_mode" \
        --threshold "$_zs_threshold" >/dev/null 2>&1
      _zs_rc=$?
      case "$_zs_rc" in
        0) abk_log "recompression sweep done (age=${_zs_age}s mode=$_zs_mode)" ;;
        3) abk_warn "recompression sweep refused: the secondary equals the primary" ;;
        *) abk_warn "recompression sweep failed (rc=$_zs_rc)" ;;
      esac
      # Sweep tick: also give zsmalloc one gated compaction pass (the sweep
      # itself churns objects, and app deaths between sweeps fragment too).
      # Rides this clock, so `zram.recomp.enable=0` stops it as well.
      abk_zram_compact_if_fragmented || true
    fi
    sleep "$_zs_reassert"
  done
}

# --- cgroup proactive reclaim supervisor (opt-in, cfr.enable=1) ----------
# The sweep itself lives in tools/cached_freeze_reclaim.sh and is shipped into
# bin/ by embed.conf: one implementation for the CLI and for this supervisor.
# That tool walks both layouts -- cgroup v2 (memory.current + cgroup.freeze) and
# cgroup v1, which is how this ROM mounts the memory controller at /dev/memcg
# (memory.usage_in_bytes + freezer.state + the memory.reclaim file the module's
# memcg_v1_reclaim graft adds).
ABK_MEMCG_ROOT="${ABK_MEMCG_ROOT:-/dev/memcg}"

abk_cfr_supervisor_main() {
  abk_pid_write cfr "$$"
  _cf_tool="${ABK_CFR_TOOL:-$MODDIR/bin/cached_freeze_reclaim.sh}"
  _cf_interval="$(abk_cfg cfr.interval_sec 300)"
  _cf_freeze="$(abk_cfg cfr.freeze 0)"
  _cf_quota="$(abk_cfg cfr.quota_mb '')"
  _cf_group="$(abk_cfg cfr.group '')"
  abk_is_uint "$_cf_interval" || _cf_interval=300
  [ "$_cf_interval" -ge 30 ] || _cf_interval=30
  case "$_cf_freeze" in 0|1) ;; *) _cf_freeze=0 ;; esac
  abk_is_uint "$_cf_quota" || _cf_quota=0

  if [ ! -f "$_cf_tool" ]; then
    abk_warn "$_cf_tool is missing; proactive reclaim disabled"
    return 1
  fi

  _cf_group_args=""
  for _cf_g in $_cf_group; do
    _cf_group_args="$_cf_group_args --group $_cf_g"
  done

  abk_log "proactive reclaim supervisor up: interval=${_cf_interval}s freeze=$_cf_freeze quota=${_cf_quota}MiB groups=${_cf_group:-<uid_*>}"
  while :; do
    _cf_args="--cgroup-root $ABK_SYS_ROOT/fs/cgroup --cgroup-root $ABK_MEMCG_ROOT"
    [ "$_cf_freeze" = "1" ] && _cf_args="$_cf_args --freeze"
    [ "$_cf_quota" -gt 0 ] && _cf_args="$_cf_args --quota-mb $_cf_quota"
    _cf_args="$_cf_args$_cf_group_args"

    # shellcheck disable=SC2086 # _cf_args is a deliberately word-split flag list
    if CFR_ONE_SHOT=1 sh "$_cf_tool" $_cf_args >/dev/null 2>&1; then
      abk_log "proactive reclaim sweep done (freeze=$_cf_freeze quota=${_cf_quota}MiB groups=${_cf_group:-<uid_*>})"
    else
      abk_warn "proactive reclaim found no reclaimable group (groups=${_cf_group:-<uid_*>})"
    fi
    sleep "$_cf_interval"
  done
}
