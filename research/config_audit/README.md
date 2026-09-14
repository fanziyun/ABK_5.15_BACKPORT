# config_gate_audit evidence

`vermeer-5.15.216-ci.config` is the `.config` of a **real device build** of this
module, kept so the one audit that needs a build artefact can be re-run without
a build:

    python tests/config_gate_audit.py <patched-216-tree> \
      --config research/config_audit/vermeer-5.15.216-ci.config

Provenance:

- source: `adb shell su -c "cp /proc/config.gz /data/local/tmp/"`, i.e. the
  running kernel on the Redmi K70 Pro (`vermeer`, 23113RKC6C).
- that kernel is the ABK CI build of run **34863020987** (android13 / 5.15 /
  sub_level X / lts, module at `fanziyun/ABK_5.15_BACKPORT` origin/main
  `7a529b8` = Batch 18, plus `custom_kernel_options=CONFIG_ZRAM_WRITEBACK=y`).
- flashed into `boot_a` on 2026-09-15 and verified there by hash
  (`ce52c7d2...`); `uname -r` = `5.15.216-android13-8-g5bfe2b8c1439`.
- 7016 lines, 8 `ABK_*` symbols.  The module version in that build is v0.23.0,
  so the gates it covers are the ones this table had at Batch 18; Batch 19/20
  add no Kconfig symbol, so the file is still the current tiers' artefact.

Result on 2026-09-15: `CONFIG GATE AUDIT OK` — 36 added gate lines (class A),
22 upstream gates containing added code (class B), 2 recorded `DARK_GATES`.