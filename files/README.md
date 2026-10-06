File payloads overlaid into the kernel tree by `scripts/stable_backport.sh`.

Most of this module is a Python graft registry (no file payloads). The
exceptions are whole files an anchor cannot express:

- `drivers/of/address.c` — the pre-rework devicetree `ranges`
  flags parser, carrying 5.15.216's unrelated `__of_get_dma_parent`
  `of_node_get()` refcount fix.  `abk_stable_backport_overlay_of_address()`
  copies it over the tree only when the target still carries the 5.15.213
  ranges rework (`flag_cells` / `"default-flags"` markers), so it is a no-op on
  any tree already in the pre-rework form.  It reverts the window-split
  that left the Qualcomm SM8550 PCIe WLAN endpoint's BAR0 unplaceable (dead
  `wlan0` on 5.15.216/lts).  The original is snapshotted to
  `drivers/of/address.c.abk-orig`; `scripts/abk_rollback.sh` restores it.

- `include/linux/sched/ext.h` (21,113 B), `kernel/sched/ext.h` (8,607 B),
  `kernel/sched/ext.c` (113,324 B) and `kernel/sched/sched_ext_glue.c` — the
  sched_ext (SCX) BPF extensible scheduler class, 143,044 bytes across the three
  upstream files plus the module-authored compilation unit that builds them.
  `abk_stable_backport_overlay_sched_ext()` creates them on a tree that does
  not already carry SCX; a target that exists with different content is left
  untouched with a warning, so a vendor tree's own `ext.c` is never clobbered.
  Source: `OnePlusOSS/android_kernel_common_oneplus_sm8750`, branch
  `oneplus/sm8750_b_16.0.0_oneplus_13` (6.6) — the smallest variant known to
  build on a GKI + vendor-module arm64 tree; `docs/survey_sched_ext_gap.md` §3
  records why it was chosen over the mainline 6.12 payload.  The three files are
  byte-for-byte as fetched (sha256 pinned in `tests/stable_5_15_test.py`) with
  their upstream copyright headers intact.  Two dead OPPO traces remain in
  `ext.c` and are not upstream text: `//#include "./slim_walt.c"` and
  `//slim_walt_enable(true);`.  Both are comments, and they are kept so the
  file stays reproducible from its URL.

  `kernel/sched/sched_ext_glue.c` is the one file here that is not upstream
  text.  It exists because the payload's `ext.c` carries no include block of
  its own: on the 6.6 tree it is compiled by textual inclusion from
  `kernel/sched/build_policy.c`, which has already pulled in every header it
  needs, and 5.15 has no `build_policy.c` (its policy files are separate
  objects).  The glue unit supplies the headers and then `#include "ext.c"`,
  and `kernel/sched/Makefile` builds `sched_ext_glue.o` rather than `ext.o`.
  It deliberately does not include `kernel/sched/ext.h` (that header has no
  include guard and `sched.h` already pulls it in), does not include the
  policy `.c` files OPPO's `build_policy.c` does, whose objects 5.15 already
  builds, and does not include `autogroup.h`/`stats.h`: 6.6 moved both into
  `build_policy.c`, but 5.15's `kernel/sched/sched.h` still includes them at
  its tail and neither header has an include guard, so listing them again
  redefines every helper they define (measured: 20 errors, fixed in Batch 56).

  Batch 54 shipped the three engine files inert; Batch 55 added the glue unit
  and the build wiring (the `CONFIG_SCHED_CLASS_EXT` symbol, the Makefile
  object rule, `struct scx_rq`/`rq->scx`, the `task_struct` slot, the
  `SCHED_DATA` slot and the `init_sched_ext_class()` call); Batch 56 ran the
  first real compiler over it (`tools/compile_probe.sh`, a configured
  android13-5.15-lts tree + clang) and measured 12 classes of 5.15/6.6 interface
  drift -- `struct affinity_context`, `set_cpus_allowed_common()`,
  `sched_weight_to_cgroup()`, the vendor `sched_prop` field,
  `SCHED_CHANGE_BLOCK`, the two core.c statics, the 7-arg
  `btf_struct_access`, `__btf_member_bit_offset`, the 2-arg
  `bpf_struct_ops.check_member`, the `sysrq_key_op.handler` signature,
  `for_each_cpu_andnot` and `__diag_ignore_all` (full table:
  `docs/survey_sched_ext_gap.md` §2c).

  **Batch 57 is the adaptation layer, and it is why the glue unit carries a
  second, marked block.**  Five of the twelve classes are preprocessor-level and
  live at the bottom of this file, between the include block and
  `#include "ext.c"`: `sched_weight_to_cgroup()`,
  `for_each_cpu_andnot()` (plus `sailboat_cpumask_next_andnot()`, since 5.15
  has neither), the `__btf_member_bit_offset` alias and
  `__diag_ignore_all()` (mapped onto `__diag_ignore(clang, 11, ...)` -- this
  tree's `compiler-clang.h` defines `_11`/`_23`, so 6.6's `_13` is an
  undefined macro here).  The rest cannot go in the glue unit and is landed by
  three registry groups (`batch57_perf_sched_ext_adapt`):
  `sched_ext_core_visibility` makes core.c's `__setscheduler_prio()` and
  `check_class_changed()` non-static for the payload's separate translation
  unit; `sched_ext_change_guard` re-carries 6.2's `SCHED_CHANGE_BLOCK` guard
  into core.c (where `dequeue_task()`/`enqueue_task()`, whose bodies carry
  the vendor trace hooks, are visible -- and because the 6.2 macro's for-init
  declaration is rejected under `-std=gnu89` by clang's `-Wgcc-compat`);
  `sched_ext_payload_adapt` makes the eight marked edits to the archived
  `ext.c` (callback and function-pointer signatures, the vendor
  `sched_prop` write dropped rather than given a KABI slot, and the three
  guard call sites open-coded).  With that applied, `tools/compile_probe.sh`
  builds all seven objects on a configured 5.15.220 tree (`OK (7 object(s)
  built)`).

  **Batch 59 adapts `kernel/sched/ext.h` the same way**, and for the same
  reason: the archived `next_active_class()` walks the class table with
  `class++` between 6.12's linker-section names `__sched_class_highest` /
  `__sched_class_lowest`, which do not exist on this baseline.  5.15 hands the
  table to the linker as the `SCHED_DATA` array, ordered by ascending address =
  descending priority (idle, ext, fair, rt, dl, stop), and
  `kernel/sched/sched.h` walks it downwards with `class--`.  ext sits between
  idle and fair in both layouts, so only the step direction and the two bound
  names change; `sched_ext_active_class` applies exactly that, in the tree.

  Every archived file stays byte-for-byte as fetched in `files/` -- the
  adaptation is applied to the *tree*, not here.  `tests/smoke.sh` asserts both
  halves for the two files the registry edits (marker present, 6.6 shape gone)
  and byte-identity for the other two.

  A new group that adapts a payload file must also add its marker to the
  allow-list in `abk_stable_backport_overlay_sched_ext()` (git-bash:
  `scripts/stable_backport.sh`).  That predicate is what distinguishes "this
  module's marked adaptation" from "a foreign `ext.c`", and a target it does
  not recognise is left alone on the second overlay call -- the call that writes
  the empty `.abk-orig` diff base -- so the tree ends up with a payload file
  that `config_gate_audit` cannot attribute.

  The class is reachable since Batch 60: `sched_ext_policy_valid` restores 6.6's
  `normal_policy()`/`fair_policy()` split so `valid_policy()` accepts
  `SCHED_EXT`, `sched_ext_priority_range` gives the policy a static priority and
  a load weight, `sched_ext_struct_ops_type` lets the BPF syscall bind
  `sched_ext_ops`, and the `ext` debugfs file reports the engine state.
  `tools/scx/` is the userspace loader that attaches a scheduler; what remains
  unproven is the device A/B, not the reachability.

Snapshot convention.  `drivers/of/address.c` only ever rewrites an existing
file, so it uses the module-wide `<file>.abk-orig` + restore convention.  A
file this module *creates* needs more than that: the target carries a zero-byte
`<file>.abk-new` marker, which is what makes `scripts/abk_rollback.sh` delete
it instead of restoring it.  Once the build wiring is present (the
`abk_stable_backport_sched_ext_wired()` probe on the Makefile entry) the target
also gets an empty `<file>.abk-orig` — the diff base
`tests/config_gate_audit.py` attributes a created file's `CONFIG_*` gates
against.  The base is withheld until the wiring is present on purpose: an empty
base on an inert payload would make the audit report every internal gate in
`ext.c` as "added code that never compiles", which is trivially true for a file
nothing includes.  Since Batch 55 the wiring is always there, so every created
file gets both markers; `tests/smoke.sh` asserts that they appear on pass 1,
are untouched on pass 2, and are gone after rollback, and that the diff base is
empty.  `tests/stable_5_15_test.py` pins the wiring probe and the
withheld-until-wired condition.
