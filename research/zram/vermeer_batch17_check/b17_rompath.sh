#!/system/bin/sh
# b17_rompath.sh -- does the module's rule also open the ROM's OWN writeback
# path (zram0 + its mmd-attached loop49)?  Batch 17 found that device had never
# written a single page back (bd_stat 0 0 0) because the same kernel-domain AVC
# blocked it.
#
# Two phases, because the first one is the interesting negative control:
#   P1  'echo idle > writeback' with no page marked idle -> rc=0, nothing moves
#       (the ROM itself never writes /sys/block/zram0/idle, so this is what its
#        own daemon would see)
#   P2  'echo all > idle' first, the way this module's sweep does it -> the
#       writeback really happens
# Bounded on purpose: a 16 MiB budget is armed for the test, and the device's
# original writeback_limit / writeback_limit_enable are restored afterwards.
# No setenforce anywhere.
set -u
D=/sys/block/zram0
SAVE=/data/local/tmp/b17/rompath.saved

orig_limit=$(cat $D/writeback_limit 2>/dev/null | tr -d ' \r\n')
orig_enable=$(cat $D/writeback_limit_enable 2>/dev/null | tr -d ' \r\n')
printf '%s %s\n' "$orig_limit" "$orig_enable" > "$SAVE"

echo "selinux=$(getenforce) backing_dev=$(cat $D/backing_dev | tr -d ' \r\n') limit=$orig_limit limit_enable=$orig_enable"
echo "bd_stat before: [$(cat $D/bd_stat | tr -s ' ')]  io_stat: [$(cat $D/io_stat | tr -s ' ')]"

phase() {
  label=$1; mark=$2
  echo 4096 > $D/writeback_limit
  echo 1    > $D/writeback_limit_enable
  [ "$mark" = "all" ] && echo all > $D/idle
  n0=$(dmesg 2>/dev/null | wc -l)
  b0=$(cat $D/bd_stat | tr -s ' ' | sed 's/^ //;s/ $//')
  u0=$(awk '{print $1}' /proc/uptime | tr -d '.')
  echo idle > $D/writeback; rc=$?
  u1=$(awk '{print $1}' /proc/uptime | tr -d '.')
  b1=$(cat $D/bd_stat | tr -s ' ' | sed 's/^ //;s/ $//')
  avc=$(dmesg 2>/dev/null | tail -n +$((n0 + 1)) | grep 'avc: *denied' | grep -c zram)
  printf '%-22s mark=%-4s rc=%s wall=%-6sms bd [%s] -> [%s] avc_zram=%s\n' \
    "$label" "$mark" "$rc" "$(( (u1-u0)*10 ))" "$b0" "$b1" "$avc"
  dmesg 2>/dev/null | tail -n +$((n0 + 1)) | grep 'avc: *denied' | grep zram | head -2
}

phase "P1 no idle marking" "none"
phase "P2 pages marked idle" "all"

echo "swap_zram0_lines=$(grep -c zram0 /proc/swaps)"
echo "writeback_limit_now=$(cat $D/writeback_limit | tr -d ' \r\n')"

# restore the device's own writeback accounting
echo "$orig_limit"  > $D/writeback_limit 2>/dev/null
echo "$orig_enable" > $D/writeback_limit_enable 2>/dev/null
echo "restored: limit=$(cat $D/writeback_limit | tr -d ' \r\n') limit_enable=$(cat $D/writeback_limit_enable | tr -d ' \r\n')"
