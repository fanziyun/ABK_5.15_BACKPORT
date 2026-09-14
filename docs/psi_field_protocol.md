# Per-cgroup PSI accounting: on-device protocol

The companion module can switch per-cgroup pressure accounting off. This document is
the evidence behind that, what the switch actually does on this tree, and the
measurement that decides whether it ships enabled.

Device: vermeer, android13-5.15-lts 5.15.216, ABK v0.29.0 (Batch 24), KernelSU root.
Everything below was taken on that device unless stated otherwise.

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

## 4. The measurement (two boots)

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