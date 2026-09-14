#!/system/bin/sh
set -u
CTL=/sys/class/zram-control
F=/data/local/tmp/b17
IMG=/data/per_boot/zram/b17_test.img
DATA=$F/data64
MB=64

cs()  { awk '/^voluntary_ctxt_switches/{print $2}' /proc/$$/status; }
jj()  { awk '{print $14+$15}' /proc/$$/stat; }
up()  { awk '{print $1}' /proc/uptime | tr -d '.'; }

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
  b=$1; c=$2; r=$3
  wipe
  id=$(cat $CTL/hot_add)
  d=/sys/block/zram$id
  dd if=/dev/zero of="$IMG" bs=1048576 count=1 seek=255 2>/dev/null
  loop=$(losetup -f | tr -d ' \r\n'); losetup "$loop" "$IMG" || return 1
  echo "$b" > $d/writeback_batch_size
  echo "$c" > $d/compressed_writeback
  echo 0   > $d/writeback_limit_enable
  echo "$loop" > $d/backing_dev
  echo 134217728 > $d/disksize
  dd if="$DATA" of=/dev/block/zram$id bs=1048576 count=$MB 2>/dev/null
  echo all > $d/idle
  c0=$(cs); j0=$(jj); u0=$(up)
  echo idle > $d/writeback; rc=$?
  u1=$(up); j1=$(jj); c1=$(cs)
  printf 'rep=%s batch=%-3s cwb=%s rc=%s wall=%-6s vcs=%-6s cpu_j=%s bd=[%s]\n' \
    "$r" "$b" "$c" "$rc" "$(( (u1-u0)*10 ))" "$((c1-c0))" "$((j1-j0))" "$(cat $d/bd_stat | tr -s ' ' | sed 's/^ //;s/ $//')"
  echo 1 > $d/reset
  losetup -d "$loop"
  echo "$id" > $CTL/hot_remove
}

cat /system/lib64/*.so 2>/dev/null | head -c 67108864 > "$DATA"
echo "data: $(wc -c < $DATA) bytes  md5=$(md5sum $DATA | cut -d' ' -f1)"
setenforce 0
for r in 1 2 3; do
  run_case 1 0 $r
  run_case 1 1 $r
  run_case 32 0 $r
  run_case 32 1 $r
done
wipe
setenforce 1
echo "selinux: $(getenforce)  zram left: $(ls -d /sys/block/zram* | tr '\n' ' ')"
