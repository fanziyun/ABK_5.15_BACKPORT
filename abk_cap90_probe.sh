#!/system/bin/sh
# Does cap_pct=90 actually clamp now?  Two measurements:
#   1. the vendor's own scaling_max_freq pin range, and how often the gate could
#      pass (min < cap < max),
#   2. whether abk_sc_capped ever changes -- the only signal that the clamp ran,
#      since the hook sits before the freq-table lookup, which rounds the cap UP
#      to the next table step, so cur_freq == cap is not the thing to look for.
SP=/sys/module/cpufreq_schedutil/parameters
P=/sys/devices/system/cpu/cpufreq

echo "=== 20s idle: vendor pin range + gate state per sample ==="
n=0
while [ $n -lt 40 ]; do
  out=""
  for pol in 0 3 7; do
    hw=$(cat $P/policy$pol/cpuinfo_max_freq)
    cap=$((hw * 90 / 100))
    mn=$(cat $P/policy$pol/scaling_min_freq)
    mx=$(cat $P/policy$pol/scaling_max_freq)
    if [ "$mn" -lt "$cap" ] && [ "$cap" -lt "$mx" ]; then g=OPEN; else g=clsd; fi
    out="$out p${pol}=${mx}($((mx*100/hw))%)/$g"
  done
  echo "  $out capped=$(cat $SP/abk_sc_capped)"
  n=$((n + 1))
  sleep 0.5
done

echo
echo "=== now with our own load on every core: watch capped and cur ==="
for i in 1 2 3 4 5 6 7 8; do sh -c 'while : ; do : ; done' & done
n=0
while [ $n -lt 30 ]; do
  echo "  cur p0=$(cat $P/policy0/scaling_cur_freq) p3=$(cat $P/policy3/scaling_cur_freq) p7=$(cat $P/policy7/scaling_cur_freq)  max p7=$(cat $P/policy7/scaling_max_freq)  capped=$(cat $SP/abk_sc_capped) boost=$(cat $SP/abk_sc_boosting)"
  n=$((n + 1))
  sleep 0.4
done
kill %1 %2 %3 %4 %5 %6 %7 %8 2>/dev/null
echo "  (load stopped)"

echo
echo "=== 6s after load dropped ==="
i=0
while [ $i -lt 6 ]; do
  echo "  capped=$(cat $SP/abk_sc_capped) boost=$(cat $SP/abk_sc_boosting) cur_p7=$(cat $P/policy7/scaling_cur_freq)"
  i=$((i + 1))
  sleep 1
done
