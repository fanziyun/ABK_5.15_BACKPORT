# Per-cgroup PSI accounting: on-device protocol

The companion module can switch per-cgroup pressure accounting off. This document is
the evidence behind that, what the switch actually does on this tree, and the
measurement that decides whether it ships enabled.

Device: vermeer, android13-5.15-lts 5.15.216, ABK v0.29.0 (Batch 24), KernelSU root.
Everything below was taken on that device unless stated otherwise.

**Batch 26 correction**: the kernel everything below was taken on put
`cgroup_disable=pressure` in its command line, so `cgroup.pressure` did not exist
on it (measured: 0 nodes) and the accounting the switch is meant to turn off was
already off device-wide.  See §2 -- the pointing run reproduced below therefore
describes a kernel *without* that token, which is what
`ABK_515_DEFCONFIG_PSI=1` builds.

## 1. The switch, and where it comes from

`cgroup.pressure` is **this module's own graft**, not something the baseline carries:
Batch 21's `psi_cgroup_pressure_switch`, ported from the android14-6.1 ACK
implementation. android13-5.15 has no such file. A kernel built without Batch 21
therefore has nothing to switch, and the companion pass reports `nodes=0` -- that is
the correct answer, not a failure.

How the port behaves, from the group itself:

| property | consequence for a policy pass |
|---|---|
| state is the `CGRP_PSI_DISABLED` bit of the cgroup's own `flags` word | no struct layout moves; reading the file costs nothing extra |
| **not hierarchical** | a disabled parent leaves its children accounting, so the pass must decide group by group |
| a disabled group keeps its task counts | the levels above and below still see who is where; only state derivation and timing stop |
| its pressure files answer `-EOPNOTSUPP`, and `poll` triggers on it are refused | a reader fails loudly instead of tuning on a stale number |
| the root group's switch drives `psi_system`, not a cgroup flag | **writing the root node turns off the whole device's pressure signal**, which is what lmkd reads. Never written, in any mode |

## 2. The pointing run

Before switching anything off, the question is who reads these numbers. Measured:

* **452** cgroups carried a `cgroup.pressure` node.
* **314** of them held tasks at the moment of the sweep (Android creates a group per
  uid and per pid, so a populated group is the normal case, not the exception).
* The only processes with any pressure file open were `lmkd`, `system_server` and
  `mimd`, and every one of them had **`/proc/pressure/memory`** -- the global file,
  served by `psi_system` through the root group, which this switch does not touch.
* **No process had any per-cgroup PSI file open.**

> **Corrected by Batch 26.**  None of the four bullets above is reproducible on
> a kernel built from this repository's template: the AOSP `android13-5.15-lts`
> `gki_defconfig` ships `CONFIG_CMDLINE="... cgroup_disable=pressure"` with
> `CONFIG_CMDLINE_EXTEND=y`, `cgroup_psi_enabled()` is therefore false for the
> whole boot, `psi_init()` disables the `psi_cgroups_enabled` static branch (the
> per-cgroup accounting itself) and `cgroup_addrm_files()` never creates a
> `CFTYPE_PRESSURE` file -- `cgroup.pressure` included.  Measured on the flashed
> kernel: `find /sys/fs/cgroup -name cgroup.pressure | wc -l` = **0**, and
> `cpu.pressure` / `memory.pressure` / `io.pressure` are gone too.  The numbers
> below were measured on a kernel without that token; to make them hold on the
> target device the kernel has to be built with `ABK_515_DEFCONFIG_PSI=1`
> (Batch 26), which drops the token -- and with it re-enables the accounting, or
> the switch would have nothing to switch off.

Reproduce it (the second command is the one that matters):

```sh
su -c 'find /sys/fs/cgroup -name cgroup.pressure | wc -l'
# how many of them hold tasks
su -c 'find /sys/fs/cgroup -name cgroup.pressure -print | while read f; do
       n=$(wc -l < "$(dirname "$f")/cgroup.procs" 2>/dev/null)
       [ "$n" -gt 0 ] && echo "$f"
     done | wc -l'
su -c 'for p in /proc/[0-9]*; do for l in "$p"/fd/*; do
       t=$(readlink "$l" 2>/dev/null) || continue
       case "$t" in *pressure*) echo "$(basename $p) $t";; esac;
     done; done 2>/dev/null | sort -u'
```

The third command is the whole case: it lists every open pressure file on the device
with its owner. Anything absent from it has no reader right now.

## 3. Why `auto` is a no-op, and `aggressive` is the only real mode

The per-cgroup work happens in `psi_group_change()`, driven by a task changing state.
A group with no tasks in it never runs that code, so switching it off saves nothing.
With 314 of 452 groups populated, `auto` (empty groups only) reaches the 138 groups
that were already free. It is a no-op with extra steps.

`aggressive` reaches the populated groups, which is where the charged work is. It is
also the only mode that can refuse a poll trigger on a group a vendor daemon decides
to watch tomorrow -- which is what `psi.cgroup.protect` is for, matched as a literal
path prefix so `protect_memcg` covers every group this ROM names that way.

That asymmetry is why the shipped default is `keep`: the mode with an upside is also
the mode with a risk, and the difference between them is a number, not an argument.

## 4. The measurement

**Storm cost, measured on vermeer / 5.15.216 (2026-09-15).**  A fork storm bills
about 1.3 ms of `system_usec` per fork (2000 forks: 2.6 s system, 1 s wall).  A wake
storm is meant to be almost free -- two blocking transitions per round trip and
nothing else -- and it now is: 2000 wakes bill 0.30 s of `system_usec`, ~0.15 ms per
round trip.  It was not until this run.  The loop used `printf`, and on this ROM
`/system/bin/sh` is Android mksh where `printf` is a **tracked alias for
`/system/bin/printf`** -- an external binary, not a builtin.  Measured: 500 calls cost
6.3 s of wall time, the same 12.6 ms a `fork+exec` of `/system/bin/true` costs, so
each nominal "wake" created two processes and billed ~30 ms of CPU.  A default run
(`--storm both --forks 20000 --wakes 50000 --rounds 3`) therefore needed hours per
A/B while measuring process creation rather than the event the switch skips.  `echo`
is a builtin on the same ROM (500 calls: 0.01 s) and is what the loop uses now.
Consequence for older numbers: the three runs quoted in `CHANGELOG.md#batch-26` §5.1
were taken with the `printf` loop, so about seven eighths of the process creations in
them came from the wake half of the storm, and no A/B taken before companion v0.9.3
contains a wake measurement.

**What can invalidate a run.**  The arms are ordinary groups; the companion's own
periodic policy pass (`service.sh --supervise-psi`, every `psi.cgroup.interval_sec`,
five minutes by default) disables every unprotected group it finds, arms included.
A run that crosses a tick would compare two disabled arms and report the difference
as a saving, so the bench re-reads both arm nodes before every round and aborts on a
mismatch (v0.9.3).  Stop the supervisor for the duration --
`pkill -9 -f 'service.sh --supervise-psi'`, then `sh service.sh --supervise-psi` via
`setsid` to bring it back, or reboot -- or measure on a `keep` boot.

**Batch 26 update.**  The shipped bench now takes the A/B *inside one boot*:
two sibling groups under `--cgroot`, the identical storm run in each with the
rounds alternating, and the bill read back from each group's own `cpu.stat`
(`--mode ab`).  That removes the between-boot drift the two-boot loop below was
built to live with, and it refuses to print a comparison when the kernel has no
`cgroup.pressure`, when a group is not being billed at that depth, or when the
arms did not land on `on=1 off=0`.  `--mode single` bills one storm in one group
and works on any kernel, which is how the instrument itself is checked.

The two-boot loop below still is the protocol of record when the thing being
measured is the *policy pass* rather than the switch: it is what the supervisor
does at boot, and its cost is part of the comparison.


Re-enabling a group is supported on this graft (`psi_cgroup_restart()` rebuilds each
CPU's state mask from the counts it kept) and explicitly *not* restore safe in
upstream's version of the same switch, which frees the group's per-cpu windows. The
companion runs on kernels it did not build and cannot tell the two apart, so
`bin/abk_psi_policy.sh` never writes `1`. Consequence for the protocol: the two
states are **two boots**, never one live flip -- the half of a session after a
re-enable has no history for the window it was off.

The kernel is already on the device; the module lives under `/data/adb/modules/`, so
this loop costs reboots, not flashes:

```sh
# A: the accounting state
su -c "sed -i s/^psi.cgroup=.*/psi.cgroup=keep/ /data/adb/modules/abk_runtime_tunables/tunables.conf"; reboot
# ... after boot:
su -c /data/adb/modules/abk_runtime_tunables/bin/abk_psi_bench.sh --storm fork --forks 16000 --rounds 3

# B: the disabled state
su -c "sed -i s/^psi.cgroup=.*/psi.cgroup=aggressive/ /data/adb/modules/abk_runtime_tunables/tunables.conf"; reboot
su -c /data/adb/modules/abk_runtime_tunables/bin/abk_psi_policy.sh --status   # confirm it took
su -c /data/adb/modules/abk_runtime_tunables/bin/abk_psi_bench.sh --storm fork --forks 16000 --rounds 3
```

Each round prints `busy_jiffies`, and each is labelled with the state *measured from
the tree* (`state=nodes=N on=X off=Y`) rather than with the config key, because a
refused write would otherwise mislabel a whole run. `--forks`/`--wakes` must be
identical between the two boots.

Linearity was checked before trusting the number, on a host with the same code path:
1000 / 4000 / 16000 forks measured 145 / 590 / 2330 busy jiffies -- a linear response
with about 4% spread between rounds, so the storm is the limiting factor and the
comparison is not noise.

## 5. The decision rule

`busy_jiffies` is the whole verdict, and it is small. Take the minimum of the rounds
in each boot (the minimum is the round with the least interference from anything else
on the device) and compare it across the two boots:

* **A saving worth having** (call it 2% of the storm's CPU or more, reproducibly):
  ship `aggressive`, with `psi.cgroup.protect` covering whatever the ROM names its own
  memory groups after.
* **No measurable saving** (within a few percent either way): ship `keep` and keep the
  tool, because the pass cost is not free either -- a supervisor walking 452 nodes
  every 300 s is overhead bought to remove overhead, and it only pays off if the
  overhead it removes is real.
* **Anything regresses**: `keep`. Specifically check `dmesg | grep -i avc | grep
  cgroup` (a denied write means the policy never took effect, which would make a
  "no change" result meaningless), and re-check the mimd/lmkd behaviour --
  `protect_memcg_*` groups belong to the ROM's own memory daemon.

Two further checks that a jiffy number cannot show:

```sh
# the pass actually reached the groups, and the log says what it decided
su -c "grep 'psi: mode=' /data/adb/modules/abk_runtime_tunables/state/abk_runtime_tunables.log | tail -3"
# the global signal the low-memory killer depends on is untouched
su -c cat /proc/pressure/memory
```

## 6. What this protocol does not cover

* Any device other than this one. The mode vocabulary is generic; the numbers, the
  group count, the `protect_memcg` prefix and the reader list are this device's.
* Battery or thermal effect, which a 7-second fork storm does not measure.
* The kernel-side default. Turning per-cgroup accounting off in the kernel (a boot-time
  default instead of a userspace pass) would remove the walk entirely and reach every
  group from the first state change, but it also diverges from upstream semantics for
  every consumer on the device at once, on a tree whose whole purpose is to stay
  upstream-shaped. That is a different batch and a different argument; this document is
  only about what a module can decide per device.