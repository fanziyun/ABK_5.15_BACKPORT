# sailboat 附加模块（二）— ABK cpufreq/scheduler runtime tunables

The scheduling half, split out of the ABK runtime tunables module (sailboat addon 1). It owns two things and nothing else:

* **the cpufreq governor** — every policy is held on `schedutil` (see below), because that is the one governor the two smart-freq payloads act under;
* **the smart-freq band** — `schedutil_smart_policy`'s floor and Batch 42's cap, which the ABK kernel module grafts into `kernel/sched/cpufreq_schedutil.c`.

The grafts themselves stay in the kernel module: a KernelSU module is userspace and cannot carry compiled kernel code. What lives here is the userspace that arms the band, keeps the governor in force, and reports who owns the frequency range.

Install it with the KernelSU manager, or `ksud module install sailboat_addon_2.zip`. It is bundled into the AnyKernel3 zip alongside addon 1, so flashing the kernel installs both.

## Why this is a separate module at all

The band is inert unless the policy's governor is `schedutil`, and the ROM leaves it on `walt` outside games. Making that governor global is the precondition for the band meaning anything, and it is also the only capability here that has to *fight* for the node it writes. Keeping it apart from the memory/zram half means the scheduling policy can be changed, disabled or discarded without touching the rest, and without either module reading a node the other owns.

## The governor

Measured on vermeer / android13-5.15 5.15.216 with SELinux Enforcing:

* the policy starts on `walt`, and `scaling_governor` ships **0444 root:root**, so a root shell's write returns `EACCES`;
* **`chmod 0644` on the node, as root, is accepted**, and the write then succeeds. The owner stays root, so `0644` grants nothing to a non-root caller — it is the same permission the ROM itself puts on the node while it is driving the governor (its own userspace has to be able to switch to `schedutil` in a game). Nothing here relaxes SELinux;
* a kernel module cannot do it instead: the cpufreq symbols the GKI exports cover `cpufreq_register_governor`, `cpufreq_get_policy` and `cpufreq_update_policy` but no governor *switch* (`cpufreq_set_policy` is not exported), and `drivers/cpufreq/cpufreq.c` is built into vmlinux, so a graft cannot reach it.

**The `chmod` buys the write, not the outcome.** The vendor writes `walt` back on a trigger nobody has pinned down — observed once, 34 s after boot, with the permissions still the `0644` this module had left behind (so the value is rewritten without the node being re-locked), and *not* during a 60 s watch. So the revert is event-driven rather than continuous. `sched.abk_governor_interval_sec=30` is therefore sized for "catches a change within a minute", not for "keeps up with the writer". Because a game is a state the vendor *also* switches to `schedutil` for, gaming costs three reads and no write.

The supervisor stops itself after three consecutive rounds in which **every** policy refused — a locked node or an SELinux denial does not start working later, and a graph of identical failures is the signal. One cluster refusing while the others hold does not stop it, because a single policy can come and go with CPU hotplug. It logs only when the verdict line changes, so a device that stays on `schedutil` produces one boot line and then silence.

`action.sh status` prints the wanted governor next to what the policies actually report, plus whether the supervisor is alive — those are different numbers by design, and the gap between them is the thing being watched. `action.sh governor` applies it once now and prints the same one-line verdict the supervisor logs.

**Reverting:** `sched.abk_governor=` (empty) stops the supervisor and leaves the ROM's own choice alone; nothing else in the module depends on the governor being held.

## The band

`floor_pct` is a floor: while any CPU of a policy has been at ≥90% of its capacity for 300 ms, `resolve_freq` raises the target to at least `floor_pct` of `cpuinfo.max_freq`. `cap_pct` is a ceiling: above it the target is clamped, unless requests have stayed above it for 300 ms. Together they are a `[floor_pct, cap_pct]` band that demand moves freely inside, with the floor catching dips during sustained load and the cap catching peaks above it.

| key | default | meaning |
|---|---|---|
| `sched.abk_governor` | `schedutil` | the governor held on every policy; **empty leaves the ROM's own choice alone** |
| `sched.abk_governor_interval_sec` | `30` | how often the governor supervisor re-asserts (5..3600) |
| `sched.abk_sf_enable` | `1` | the smart-freq floor |
| `sched.abk_sf_floor_pct` | `85` | the floor end of the band; **must stay below `sched.abk_sc_cap_pct`** |
| `sched.abk_sf_sustained_ms` | `300` | ms at ≥90% of capacity before a CPU starts boosting (1..60000) |
| `sched.abk_sf_exit_ms` | `250` | ms below 70% before a boosting CPU clears (1..60000) |
| `sched.abk_sc_enable` | `1` | the Batch 42 smart_freq cap; same ownership gates as the floor |
| `sched.abk_sc_cap_pct` | `90` | the ceiling end of the band; **must stay above `sched.abk_sf_floor_pct`**, and `100` is no clamp |
| `sched.abk_sc_hold_ms` | `300` | ms the request must stay above the cap before it is released (1..60000) |
| `sched.abk_sc_release_pct` | `70` | every CPU of the policy below it, for `release_ms`, re-asserts the cap |
| `sched.abk_sc_release_ms` | `250` | (1..60000) |
| `report.logcat` | `1` | mirror the module log into logcat as well |

`sched.abk_sc_entry_pct` has no knob here on purpose. It feeds nothing but the read-only `abk_sc_boosting` reason election — it gates no decision of its own — so a writable copy would be the number reached for first when chasing the floor's old 70–90% dead band, and moving it changes nothing. Read it with `action.sh status`.

**Why `cap_pct` is 90 and not the 95 it first shipped:** measured on vermeer / 5.15.216 after the governor was put on `schedutil` globally, the vendor pins `scaling_max_freq` to 92-94% of `cpuinfo.max_freq` on all three policies. `abk_sc_owns()` refuses when `cap >= policy->max`, so a cap at or above those pins is refused on every policy every time — which is what 95 was: armed, and never once run. 90 puts the cap below all three pins and keeps a 5-point band, the narrowest the band is allowed to be before it becomes a lock. If the vendor's pins move up again, check `abk_sc_capped` and the `cap gate` rows in `action.sh status`: unchanged forever means the cap is being refused for this reason again.

Two facts about the band, both learned the hard way:

* **`cap_pct` must stay above `floor_pct`.** The cap registers a second probe at `late_initcall_sync`, so it runs *after* the floor's raise. At `cap_pct <= floor_pct` the floor lifts, the cap clamps back, on every `resolve_freq` call forever — and the cluster pins at that one frequency the moment sustained load appears. That is the Batch 10-4c ratchet, and it is the whole reason the cap was written. Want a tighter ceiling? Lower `floor_pct` first and keep the gap; do not push `cap_pct` down to meet it.
* **The cap clamps a target the frequency table then rounds back up.** The hook sits inside `__resolve_freq()` after the policy min/max clamp and before the freq-table lookup, so `*target_freq = cap` is followed by a lookup that rounds **up** to the lowest table entry ≥ cap. Measured with `hold_ms` raised so the cap could not release: a cap of 2410752 on policy3 landed on 2457600 (the next entry) while the vendor's pin was 2457600 — i.e. no effect — and a cap of 2298624 landed on 2323200, which is below the pin and *is* an effect. So a cap only bites when it falls at or below the table entry immediately under the vendor's pin.

## `abk_fas_check.sh`

`bin/abk_fas_check.sh` answers the question the boot report cannot: is a cluster genuinely owned by one authority (healthy), parked idle, or locked? `--sample N` counts the polls where the capacity inversion is live and exits 4 when that share reaches `--invert-pct` (default 5%); `--probe` applies load and confirms the frequency comes back down, which is the only way to tell a park from a lock. It is read-only. `action.sh status` prints the exact invocation.

## Files

| file | role |
|---|---|
| `post-fs-data.sh` | early stage: the governor, then the band, then the DVFS report |
| `service.sh` | late stage: the governor supervisor, spawned only when a governor is named |
| `common.sh` | helpers plus the ten functions this module owns |
| `tunables.conf` | every key, with the measured reasons behind the values |
| `action.sh` | `status` and `governor` |
| `embed.conf` | maps `tools/abk_fas_check.sh` into `bin/` |
| `bin/abk_fas_check.sh` | the on-demand park-or-lock verdict |
