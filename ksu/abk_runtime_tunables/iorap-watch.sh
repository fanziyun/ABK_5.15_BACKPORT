#!/system/bin/sh
# iorap-watch.sh - Stage 2b: the launch watcher that takes iorapd's place.
#
# Re-entered as `service.sh --supervise-iorap` from service.sh's abk_spawn.
# The kernel owns the mechanism (bg_launch_prefetch: record windows, bitmap
# records, the pre-launch kick); this watcher owns the policy -- it decides
# WHEN a uid gets recorded and WHEN it gets kicked, which is what iorapd's
# /dev/iorap handshake used to decide.
#
# Trigger source: /proc polling.  This ROM ships no per-launch logcat signal
# (the main buffer has no "START u0" and the events buffer has no am_start --
# both verified on vermeer), so the watcher snapshots /proc every ~300 ms and
# reacts to processes whose UID is an app UID.  The kernel keys the record
# table on UID, so the package name is never needed.
#
# The economics are the whole point.  One cold launch touches ~600 files and
# the record table holds 512 slots, so recording on EVERY launch evicts
# everything each time (measured on device with the naive flow: kicks=0 for
# every app but the most recently collected).  Match iorapd's economics
# instead -- record a uid ONCE per generation, kick it on every other launch:
#   - first sighting of a uid, or after iorap.record_cool_min: kick + record
#   - every other launch inside the generation: kick only
# A kick-only launch does not touch the record table at all (collection is
# window-gated), so a hot app's records survive the apps launched in between
# and every subsequent kick keeps hitting.  The kick fires before the record
# write on purpose: it re-reads the previous generation's pages while this
# launch is still spawning, and the record write then re-arms that uid's
# replay cooldowns for the next generation.

abk_iorap_main() {
  _io_k=/sys/module/filemap/parameters
  if [ ! -f "$_io_k/abk_lp_enable" ]; then
    abk_log "iorap-watch: kernel has no bg_launch_prefetch, watcher not started"
    return 0
  fi
  if [ "$(abk_cfg iorap.watch 1)" != "1" ]; then
    abk_log "iorap-watch: disabled by config (iorap.watch)"
    return 0
  fi
  _io_min_uid=$(abk_cfg iorap.min_uid 10000)
  _io_rec_cool=$(abk_cfg iorap.record_cool_min 360)
  _io_kick_cool=$(abk_cfg iorap.kick_cool_sec 60)
  _io_gap=$(abk_cfg iorap.global_gap_sec 2)
  _io_st="$ABK_STATE_DIR/iorap"
  _io_run="$ABK_RUN_DIR/iorap"
  mkdir -p "$_io_st" "$_io_run" 2>/dev/null || true

  abk_log "iorap-watch: started (min_uid=$_io_min_uid rec_cool=${_io_rec_cool}m kick_cool=${_io_kick_cool}s)"
  _io_first=1
  _io_last=0
  while :; do
    ls /proc 2>/dev/null | grep -E '^[0-9]+$' | sort > "$_io_run/cur" 2>/dev/null
    if [ "$_io_first" = 1 ]; then
      cp "$_io_run/cur" "$_io_run/prev" 2>/dev/null
      _io_first=0
      sleep 0.3
      continue
    fi
    # pids present now but absent last tick; comm is a C utility, so the
    # per-tick cost stays O(n log n) no matter how busy /proc is.
    _io_new=$(comm -13 "$_io_run/prev" "$_io_run/cur" 2>/dev/null)
    cp "$_io_run/cur" "$_io_run/prev" 2>/dev/null
    if [ -n "$_io_new" ]; then
      _io_now=$(date +%s)
      for _io_pid in $_io_new; do
        _io_uid=$(sed -n 's/^Uid:[[:space:]]\{1,\}\([0-9]\{1,\}\).*/\1/p' \
                  "/proc/$_io_pid/status" 2>/dev/null)
        [ -n "$_io_uid" ] || continue
        [ "$_io_uid" -ge "$_io_min_uid" ] 2>/dev/null || continue
        # per-uid kick cooldown: a spawn burst of the same app counts once
        _io_kt=0
        [ -f "$_io_st/kick.$_io_uid" ] && _io_kt=$(cat "$_io_st/kick.$_io_uid" 2>/dev/null)
        [ $(( _io_now - _io_kt )) -ge "$_io_kick_cool" ] 2>/dev/null || continue
        # global gap: a launcher fan-out of several apps still triggers one
        # at a time, oldest first; the rest are picked up on their next spawn
        [ $(( _io_now - _io_last )) -ge "$_io_gap" ] 2>/dev/null || continue
        _io_last=$_io_now
        echo "$_io_now" > "$_io_st/kick.$_io_uid" 2>/dev/null
        echo "$_io_uid" > "$_io_k/abk_lp_prefetch_uid" 2>/dev/null
        # record only once per generation
        _io_rt=-999999
        [ -f "$_io_st/rec.$_io_uid" ] && _io_rt=$(cat "$_io_st/rec.$_io_uid" 2>/dev/null)
        if [ $(( _io_now - _io_rt )) -ge $(( _io_rec_cool * 60 )) ] 2>/dev/null; then
          echo "$_io_now" > "$_io_st/rec.$_io_uid" 2>/dev/null
          echo "$_io_uid" > "$_io_k/abk_lp_record_uid" 2>/dev/null
          abk_log "iorap-watch: uid $_io_uid record+kick"
        fi
      done
    fi
    sleep 0.3
  done
}
