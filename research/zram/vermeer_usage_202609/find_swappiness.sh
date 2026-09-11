#!/system/bin/sh
# find where swappiness=100 comes from + who set up zram
echo "== swappiness default now =="
cat /proc/sys/vm/swappiness
echo
echo "== swappiness in userspace config dirs =="
grep -rn swappiness /vendor/etc /odm/etc /system/etc /system_ext/etc /product/etc 2>/dev/null | head -n 30
echo
echo "== zram/disksize/swapon in config dirs =="
grep -rln -e zram0 /vendor/etc /odm/etc /vendor/bin 2>/dev/null | head -n 20
echo
echo "== fstab zram lines =="
grep -rn zram /vendor/etc/fstab* /odm/etc/fstab* /proc 2>/dev/null | grep -v /proc | head -n 10
ls /vendor/etc/fstab* /odm/etc/fstab* 2>/dev/null
echo
echo "== zram-control (hot_add) =="
ls -la /sys/class/zram-control 2>/dev/null
echo
echo "== mm-events / perfd zram props =="
getprop | grep -i zram
echo
echo "== memory extension =="
getprop | grep -iE 'memext|memoryext|ram_ext|zram_size|swap'
settings get global ram_extension_size 2>/dev/null
settings get secure ram_extension_size 2>/dev/null
