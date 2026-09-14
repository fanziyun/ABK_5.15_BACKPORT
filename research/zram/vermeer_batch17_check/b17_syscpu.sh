#!/system/bin/sh
set -u
CTL=/sys/class/zram-control
F=/data/local/tmp/b17
IMG=/data/per_boot/zram/b17_test.img
DATA=$F/data64
MB=64

# system-wide non-idle jiffies (user+nice+system+irq+softirq+steal)
sycpu() { awk '/^cpu /{print $2+$3+$4+$7+$8+$9}' /proc/stat; }
jj()    { awk '{print $14+$15}' /proc/$$/stat; }
up()    { awk '{print $1}' /proc/uptime | tr -d '.'; }

wipe() {
  for d in /sys/block/zram1 /sys/block/zram2; do
    [ -d "$d" ] || continue
    id=$(printf '%s' "$d" | sed 's|.*zram||')
    echo 1 > $d/reset 2>/dev/null
    echo "$id" > $CTL/hot_remove 2>/dev/null
  done
  for l in $(losetup -a | grep b17_test.img | cut -d: -f1); do losetup -d "$l" 2>/dev/null; done
  rm -f "$IMG"
}

run_case() {
  c=$1; r=$2
  wipe
  id=$(cat $CTL/hot_add)
  d=/sys/block/zram$id
  dd if=/dev/zero of="$IMG" bs=1048576 count=1 seek=255 2>/dev/null
  loop=$(losetup -f | tr -d ' \r\n'); losetup "$loop" "$IMG" || return 1
  echo 32 > $d/writeback_batch_size
  echo "$c" > $d/compressed_writeback
  echo 0   > $d/writeback_limit_enable
  echo "$loop" > $d/backing_dev
  echo 134217728 > $d/disksize
  dd if="$DATA" of=/dev/block/zram$id bs=1048576 count=$MB 2>/dev/null
  echo all > $d/idle

  p0=$(sycpu); q0=$(jj); u0=$(up)
  echo idle > $d/writeback; rc=$?
  u1=$(up); q1=$(jj); p1=$(sycpu)

  r0=$(sycpu); q2=$(jj); v0=$(up)
  dd if=/dev/block/zram$id of="$F/sink" bs=1048576 count=$MB 2>/dev/null
  v1=$(up); q3=$(jj); r1=$(sycpu)

  printf 'rep=%s cwb=%s rc=%s | WB: wall=%-6s sh_cpu_j=%-4s sys_cpu_j=%-4s | RB: wall=%-6s sh_cpu_j=%-4s sys_cpu_j=%-4s | bd=[%s] sum=%s\n' \
    "$r" "$c" "$rc" "$(( (u1-u0)*10 ))" "$((q1-q0))" "$((p1-p0))" "$(( (v1-v0)*10 ))" "$((q3-q2))" "$((r1-r0))" \
    "$(cat $d/bd_stat | tr -s ' ' | sed 's/^ //;s/ $//')" "$(md5sum $F/sink | cut -c1-8)"
  rm -f "$F/sink"
  echo 1 > $d/reset
  losetup -d "$loop"
  echo "$id" > $CTL/hot_remove
}

setenforce 0
for r in 1 2 3; do
  run_case 0 $r
  run_case 1 $r
done
wipe
setenforce 1
echo "selinux: $(getenforce)  zram left: $(ls -d /sys/block/zram* | tr '\n' ' ')"
