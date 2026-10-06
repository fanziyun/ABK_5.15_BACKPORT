# sched_ext on android13-5.15: precedent, dependency closure, payload choice

Scope: whether the BPF extensible scheduler class (SCX) can land on the single
supported baseline — `android13-5.15-lts`, reference tree
`build/abk-trees/220-verify`, `SUBLEVEL = 220` — as a graft in this registry,
and which payload to bring if it can.  An earlier pass concluded "not portable"
on two grounds; the KMI half was wrong (see §4) and the precedent half was
missing (see §1), so that verdict is withdrawn and replaced by the staged port in
§2-§6.

## 0. Verdict

SCX is portable to this baseline, as a **multi-batch project**, not as one
bounded group.  Measured closure (§2, §2a, §2b): two real gaps on 5.15 — the kfunc
registration API (S1b, additive, measured against v5.18) and arm64 BPF
trampolines (S1, carrying a measured interface re-author, not a verbatim copy) —
plus the SCX payload itself, the sched-core wiring, one KABI pointer slot and a
userspace loader.  **S1 is done**: its three groups landed as Batch 51
(`arm64_insn_load_literal`, `arm64_bpf_text_poke`) and Batch 52
(`arm64_bpf_trampoline`), so arm64 `struct_ops` is reachable from this tree.
S1b is done too (Batch 53), and S2 is half done: the payload landed as
Batch 54 / v0.57.0 and its build wiring (S2b-1) as Batch 55 / v0.58.0, so the
class is compiled and registered but unreachable; S2b-2 (the functional hooks)
and S3 (the loader) remain.  Payload choice
is OPPO's 6.6 variant (§3), the smallest one known to build on a GKI+vendor-module
tree.

## 1. Precedent: SCX has been carried onto kernels older than 6.12

| source | kernel | evidence |
|---|---|---|
| `OnePlusOSS/android_kernel_common_oneplus_sm8750`, branch `oneplus/sm8750_b_16.0.0_oneplus_13` | 6.6 (`Makefile`: `VERSION = 6`, `PATCHLEVEL = 6`) | `kernel/sched/ext.c` (113,324 B, 3,978 lines) + `kernel/sched/ext.h`; `kernel/Kconfig.preempt` gains `config SCHED_CLASS_EXT` (`depends on BPF_SYSCALL && BPF_JIT`); `arch/arm64/configs/gki_defconfig` carries `CONFIG_SCHED_CLASS_EXT=y`; built through `kernel/sched/build_policy.c:58` (`# include "ext.c"`); `kernel/sched/core.c` carries the integration hooks (`scx_prio_less`, `scx_switched_all`, `scx_enabled`, `scx_can_stop_tick`, `scx_pre_fork`, `task_on_scx`) |
| same vendor, 6.1 branch `oneplus/sm8650_v_15.0.0_oneplus12` | 6.1 | vendor glue only — `vendor/oplus/kernel/cpu/sched_ext/{main.c,hmbird_sched_proc.h,BUILD.bazel,oplus_local_modules.bzl}`; no `kernel/sched/ext.c` in that common tree |
| `Rtoax/linux-5.10.13` | repository name says 5.10.13, `master` `Makefile` is `VERSION = 6`, `PATCHLEVEL = 1` | `kernel/sched/` carries `ext.c`, `ext.h`, `ext_idle.c`, `ext_internal.h` — a third-party old-kernel carry, with a file split that does not exist upstream |

Open item: no 5.10 tree was located; the repository named `linux-5.10.13` has
only `master`, and that branch is 6.1.  A 5.10 provenance would be recorded here
if supplied.

## 2. Dependency closure measured against the real 5.15 branch

Method: take the payload's referenced kernel API surface (`bpf_*` / `scx_bpf_*`
tokens from OPPO's `ext.c`), subtract the union of identifiers present in the
5.15 BPF core (`kernel/bpf/{helpers,verifier,syscall,btf,core,trampoline,bpf_struct_ops}.c`,
`include/linux/{bpf,btf}.h`, `arch/arm64/net/bpf_jit_comp.c`).  Result: the only
tokens left are `scx_bpf_*` (defined by `ext.c` itself) — i.e. the payload does
not depend on post-5.15 BPF core helpers.  The two genuine gaps are outside that
token class and are listed separately below.

Present on 5.15.220:

| item | evidence |
|---|---|
| `struct bpf_struct_ops` | `include/linux/bpf.h:1046` |
| struct_ops / trampoline / BTF objects | `kernel/bpf/bpf_struct_ops.c`, `kernel/bpf/trampoline.c`, `kernel/bpf/btf.c` in the branch's `kernel/bpf/` listing |
| `bpf_trampoline_link_prog` | declared in `include/linux/bpf.h` |
| BPF config | `CONFIG_BPF_SYSCALL=y`, `CONFIG_BPF_JIT=y`, `CONFIG_BPF_JIT_ALWAYS_ON=y`, `CONFIG_DEBUG_INFO_BTF=y` (`arch/arm64/configs/gki_defconfig`) |
| `bpf_for_each_map_elem`, `bpf_timer_*` | `kernel/bpf/helpers.c`, `kernel/bpf/verifier.c` |

Missing on 5.15.220:

| item | why it is required | evidence |
|---|---|---|
| `register_btf_kfunc_id_set` and the `btf_kfunc_id_set` allow-list | both payload variants register their kfunc sets (OPPO's `ext.c`: 6 declared sets / 6 registrations; mainline 6.12 `ext.c`: 10) | 5.15 has the kfunc *call* machinery but no allow-list at all — 0 hits for `btf_kfunc_id_set` in `include/linux/btf.h` and `kernel/bpf/btf.c` (§2b) |
| arm64 BPF trampolines / `bpf_arch_text_poke` | `ext.c` registers `struct bpf_struct_ops bpf_sched_ext_ops`; struct_ops dispatch calls BPF programs through trampolines | `arch/arm64/net/bpf_jit_comp.c` has 0 occurrences of `trampoline`/`text_poke`; arm64 support landed upstream after 5.15 |
| the post-5.15 generic trampoline interface (`struct bpf_tramp_links`, `struct bpf_tramp_run_ctx`) | the v6.1 arm64 trampoline is written against it, not against 5.15's `struct bpf_tramp_progs` / `__bpf_prog_enter(prog)` | measured in §2a |
| `aarch64_insn_gen_load_literal()` | `bpf_arch_text_poke()` builds its PLT entry from it | absent from 5.15 `arch/arm64/include/asm/insn.h`, present in v6.1; **landed by `arm64_insn_load_literal`** (§2a) |
| the `A64_LS_IMM` family (`A64_STR64I` / `A64_LDR64I`) | the trampoline saves and restores arguments through them | 5.15 `arch/arm64/net/bpf_jit.h` has **no** immediate-offset A64 load/store macro at all; landed with the same group (§2a) |
| `kernel/sched/build_policy.c` | the 6.6/6.12 build wiring includes `ext.c` from it | 5.15's `kernel/sched/` listing has no `build_policy.c`, so the Makefile form must be used instead (§3) |

Not required by either payload variant (they are a *userspace scheduler*
dependency, not an `ext.c` dependency): `bpf_cpumask` (`kernel/bpf/cpumask.c`
does not exist on 5.15), `bpf_task_acquire/release`, `bpf_rcu_read_lock`,
`bpf_dynptr`, `bpf_loop`, `bpf_cast_to_kern_ctx`, `bpf_rdonly_cast`.  Both
`ext.c` variants have zero occurrences of these tokens; a scheduler ported from
upstream `sched-ext/scx` may still pull some of them in, which is a selection
criterion for §6.

## 2a. S1 measurement: what the arm64 trampoline actually needs

Measured against **v6.1**, the release the arm64 trampoline landed in — not
v6.12, which only carries later fixes.  Sources fetched and compared: the
`android13-5.15-lts` and `v6.1` copies of `arch/arm64/net/bpf_jit_comp.c`,
`arch/arm64/net/bpf_jit.h`, `arch/arm64/include/asm/insn.h`,
`kernel/bpf/trampoline.c`, `include/linux/bpf.h`, `arch/arm64/Kconfig`, plus the
seven v6.1 arm64 JIT patches.

**Size.** 5.15 `arch/arm64/net/bpf_jit_comp.c` is 1,215 lines with **0**
`trampoline`/`text_poke` occurrences; v6.1 is 2,226 lines with 29.  The raw file
diff is +1,142/-131, but most of that is a year of unrelated v6.1 churn.  The
trampoline work itself is seven commits, **+722/-28**, in two files only:

| commit | subject | +/− |
|---|---|---|
| `efc9909fdce0` | bpf, arm64: Add bpf trampoline for arm64 | +382/−3 |
| `b2ad54e1533e` | bpf, arm64: Implement bpf_arch_text_poke() for arm64 | +322/−14 |
| `33f32e5072b6` | bpf, arm64: Mark dummy_tramp as global | +1/−0 |
| `339ed900b307` | bpf, arm64: Fix compile error in dummy_tramp() | +2/−2 |
| `19f68ed6dc90` | bpf, arm64: Allocate program buffer using kvcalloc instead of kcalloc | +2/−2 |
| `aada47665546` | bpf, arm64: Fix bpf trampoline instruction endianness | +6/−6 |
| `eb707dde264a` | bpf: arm64: No support of struct argument in trampoline programs | +7/−1 |

**The constraint that decides the port shape.**  Both large commits are written
against the *post-5.15* generic trampoline interface:

| interface | 5.15 (`include/linux/bpf.h` / `kernel/bpf/trampoline.c`) | v6.1 |
|---|---|---|
| container passed to the arch builder | `struct bpf_tramp_progs *tprogs` | `struct bpf_tramp_links *tlinks` |
| per-program entry hook | `__bpf_prog_enter(prog)` → `u64`; `__bpf_prog_exit(prog, start)` | `__bpf_prog_enter(prog, run_ctx)`; `__bpf_prog_exit(prog, start, run_ctx)`; `struct bpf_tramp_run_ctx` |
| generic registration | `register_ftrace_direct()` / `unregister_ftrace_direct()` | `register_ftrace_direct_multi()` / `unregister_ftrace_direct_multi()` |

The arm64 payload references `bpf_tramp_links` 6 times and `bpf_tramp_run_ctx`
4 times, so a verbatim copy does not compile on 5.15.  Two ways out:

1. carry the generic rework (`bpf_tramp_progs`→`bpf_tramp_links`, the run-ctx
   signature, the multi registration) — that also rewrites
   `arch/x86/net/bpf_jit_comp.c` and the generic trap path, i.e. a tree-wide core
   change paid for one arch feature; or
2. **re-author** the two large hunks onto 5.15's interface — the difference is
   mechanical (`tlinks`→`tprogs`, `tlink->link.prog`→`prog`, drop the run-ctx
   argument and keep the `u64 start` return threading that 5.15's own x86
   trampoline already uses).

Decision: option 2.  The three generic registrations above are *not* called from
the arch payload, so 5.15's generic `kernel/bpf/trampoline.c` stays unchanged;
only the two shared signatures matter, and the arm64 build compile-checks both.

**One missing arch helper.**  `bpf_arch_text_poke()` builds a PLT entry from
`aarch64_insn_gen_load_literal()`, absent from 5.15
(`arch/arm64/include/asm/insn.h`: 0 hits; v6.1: 1).  It is a small, dependency-free
addition.

**fentry vs struct_ops on arm64.**  v6.1's `register_fentry()` returns
`-ENOTSUPP` for a ftrace-managed target when `tr->fops` was never allocated,
and `tr->fops` only exists under `CONFIG_DYNAMIC_FTRACE_WITH_DIRECT_CALLS` —
which neither 5.15 nor v6.1 arm64 selects (5.15 `arch/arm64/Kconfig` selects
`HAVE_DYNAMIC_FTRACE_WITH_REGS` only).  fentry/fexit on traced functions is
therefore out of reach on this arch either way; struct_ops — what SCX registers —
takes the `bpf_arch_text_poke()` path and is not affected.  This bounds S1's
claim: it makes arm64 struct_ops (and any non-traced attach target) work, not
fentry on arbitrary functions.

**Group split for S1** (perf child, in this order): `arm64_insn_load_literal`
(the instruction helper **and** the `bpf_jit.h` macro family it feeds — they are
one unit, because `A64_LS_IMM` expands to `aarch64_insn_gen_load_store_imm()`),
`arm64_bpf_text_poke` (`bpf_arch_text_poke()` + the patchsite + the PLT), then
`arm64_bpf_trampoline` (the two large hunks re-authored).  The seven upstream
commits are one series and are carried as one batch; the prior "at most one
`PatchGroup`" gate is withdrawn as measured-false.  All three have landed —
`arm64_insn_load_literal` and `arm64_bpf_text_poke` as Batch 51, the trampoline
itself as Batch 52 — and the trampoline is registered last because it is the
only consumer of the other two.  S1 is complete; what it unblocks is arm64
`struct_ops` (which takes the `bpf_arch_text_poke()` path), not fentry on an
ftrace-managed target.

**Measured corrections found while landing the groups** (they change what a
copy would have needed, so they are recorded rather than assumed):

- the encoder file is **`arch/arm64/lib/insn.c`** on this branch.
  `arch/arm64/kernel/insn.c` is a 404 on `android13-5.15-lts` — checked against
  the branch's own `arch/arm64/kernel/` directory listing, not inferred — so an
  upstream patch path of `kernel/insn.c` has to be re-pointed.  (The upstream
  `aarch64_insn_ldst_size[]` table and the `label_imm_common()` rename are *not*
  carried: the table does not exist on 5.15, so the size shift stays the switch
  the new generator uses, and `branch_imm_common()` still holds the range check.)
- 5.15 `arch/arm64/net/bpf_jit.h` has no `A64_LS_IMM` family at all, so
  `A64_STR64I()`/`A64_LDR64I()` — which v6.1 uses throughout the trampoline —
  arrive with the instruction helper, not with the trampoline group.
- v6.1's `aarch64_insn_gen_load_store_imm()` looks up
  `aarch64_insn_get_{ldr,str}_imm_value()`, but v6.1's own `insn.h` *also* still
  carries `aarch64_insn_get_{load,store}_imm_value()` with a byte-identical
  mask/value pair (`0x3FC00000` / `0x39000000` and `0x39400000`), and 5.15 has
  only that older pair.  The generator is therefore written against the pair
  this tree already has — adding the v6.1 names would define two identical
  matchers next to each other.  The `AARCH64_INSN_LDST_{LOAD,STORE}_IMM_OFFSET`
  enum values *are* carried: 5.15's `enum aarch64_insn_ldst_type` has no
  immediate-offset form.
- `19f68ed6dc90` (kvcalloc for `ctx.offset`) is **not** carried: `kvcalloc()`/
  `kvfree()` are declared in `include/linux/mm.h`, which
  `arch/arm64/net/bpf_jit_comp.c` does not include on 5.15 (its include set is
  `filter.h`, `skbuff.h` → `dma-mapping.h`, `slab.h`, …).  Pulling `mm.h` in
  for one allocation helper is a divergence with no bearing on the trampoline;
  the allocation stays `kcalloc()`/`kfree()` as the pristine file has it.
  `linux/sizes.h` needs no include of its own for the opposite reason — it
  arrives through `filter.h` → `skbuff.h` → `dma-mapping.h`, so `SZ_128M` in
  `is_long_jump()` resolves.
- the arm64 prologue on 5.15 has **no `paciasp`**, so v6.1's
  `PROLOGUE_OFFSET = BTI_INSNS + 2 + PAC_INSNS + 8` becomes
  `BTI_INSNS + 2 + 7` here and `POKE_OFFSET` is `BTI_INSNS + 1`.  Getting this
  wrong is silent: `build_prologue()` only `pr_err_once`s and returns `-1` when
  the measured prologue length disagrees, which degrades the whole program to the
  interpreter.
- `CONFIG_ARM64_BTI_KERNEL` appears on added lines as an `IS_ENABLED()` mention
  (`emit_bti()`, `BTI_INSNS`, and the upstream `#if` inside the `dummy_tramp`
  asm).  `tests/config_gate_audit.py` class A flags exactly that if the build's
  `.config` has the symbol off; the symbol `default y`s in arm64 Kconfig when
  BTI is available, and every added mention is correct-by-construction when it is
  off (no landing pad is needed, `BTI_INSNS` is 0).  `config_gate_audit` could
  not be run locally (no build `.config`), so this is the one gate Batch 51
  leaves to the CI build; if it fires, the fix is a `DARK_GATES` entry stating
  that, not a code change.
- the trampoline hunks themselves (Batch 52) needed three more measured adjustments, all of which would have been compile errors or silent runtime misbehaviour if the v6.1 text had been copied:
  1. 5.15's `kernel/bpf/trampoline.c` hands the arch builder a `struct bpf_tramp_progs` (`progs[]` + `nr_progs`), not `struct bpf_tramp_links` (`links[]` of `struct bpf_tramp_link *`), and its `__bpf_prog_enter` / `__bpf_prog_exit` take `(prog)` / `(prog, start)` with no `bpf_tramp_run_ctx`; the port drops the run-ctx frame and the cookie slot entirely.
  2. `aada47665546` (the `__le32` + `cpu_to_le32()` fix) is not a separate concern here: 5.15's `struct jit_ctx::image` is already `__le32 *` and `emit()` already does `cpu_to_le32()`, so the commit's "before" form is a hard sparse/compile error and the fix is folded into the port.
  3. v6.1 stores the start time returned by `__bpf_prog_enter()` into x20 *after* the branch placeholder, so the `skip_exec_of_prog` path reaches the exit call with x20 holding whatever the caller left there.  This tree's `update_prog_stats()` accepts any `start > NO_START_TIME` (=1), so with bpf stats on it would book that value as the program's runtime.  The 5.15 x86 trampoline — written against this same interface — saves first; the port follows x86 and stores x20 before the branch.
  `eb707dde264a` ("No support of struct argument in trampoline programs") is deliberately **not** carried: it guards on `m->arg_flags[i] & BTF_FMODEL_STRUCT_ARG`, and 5.15's `struct btf_func_model` has no `arg_flags[]` at all because trampoline struct arguments only landed upstream after 5.15.  The guard would not compile, and there is nothing here for it to reject.

Rejected alternatives: porting the v6.1 generic trampoline rework (option 1, a core
change for no SCX benefit); copying v6.12's arm64 JIT wholesale (larger, and its
extras — direct-call JIT, callee-saved trimming — are separate optimizations, not
trampoline prerequisites).

## 2b. S1b measurement: the kfunc registration API

The second gap of §2 is not one function.  5.15 has the kfunc *call* machinery —
`struct bpf_kfunc_desc_tab`, `add_kfunc_call()`, `btf_check_kfunc_arg_match()`
in `kernel/bpf/btf.c` — but no *allow-list*: whether a kernel BTF function may be
called is decided by the program type's own hardcoded callback,

```c
if (!env->ops->check_kfunc_call ||
    !env->ops->check_kfunc_call(func_id))
        return -EINVAL;
```

(`kernel/bpf/verifier.c:6839` on 5.15).  `btf_kfunc_id_set` does not exist on
5.15 at all (`grep -c btf_kfunc_id_set include/linux/btf.h kernel/bpf/btf.c`:
0 / 0).

Upstream introduced the registration API in **v5.18** (v5.17 still has 0 hits):
`struct btf_kfunc_id_set` / `enum btf_kfunc_type` / `btf_kfunc_filter_t` in
`include/linux/btf.h`, and `btf_populate_kfunc_set()` /
`__btf_kfunc_id_set_contains()` / `btf_kfunc_id_set_contains()` /
`register_btf_kfunc_id_set()` in `kernel/bpf/btf.c` (the registration entry
point is itself ~30 lines, `EXPORT_SYMBOL_GPL`).  v6.1's verifier consumes the
id-set directly (`verifier.c:7697` `btf_kfunc_id_set_contains(desc_btf,
resolve_prog_type(env->prog), func_id)`) instead of the ops callback.

`ext.c` needs it: the OPPO payload declares six `btf_kfunc_id_set` objects and
calls `register_btf_kfunc_id_set()` six times, all `BPF_PROG_TYPE_STRUCT_OPS`
(`kernel/sched/ext.c:3350..3970`).

Backport shape: **additive**.  Carry the v5.18 registration API and let the
verifier accept a kfunc when *either* the existing `ops->check_kfunc_call()` or a
registered id-set allows it.  Removing the per-program-type callbacks (what
upstream did in the same series) is a tree-wide sweep over `kernel/bpf/*`,
`net/` and `kernel/trace/` and buys SCX nothing, so it is not carried.  The
delicate part is classification: the id-set path must also yield the
`KF_ACQUIRE` / `KF_RELEASE` / `KF_RET_NULL` information that 5.15's
bool-returning callback conflates, because `check_kfunc_call()` branches on it.

**Batch 53 实测修正（落地时按 6.6 版 `ext.c` 逐行对照后重测；上面「只有注册 API 一个缺口」的估计偏小）**：
闭合是三块，缺一块就编不过。① `ext.c` 用的是 `BTF_SET8_START` / `BTF_SET8_END` /
`BTF_ID_FLAGS` 这套拼写，由 `ab21d6063c01`（bpf: Introduce 8-byte BTF set）在 **v5.19** 引入，
5.15 的 `btf_ids.h` 只有 `BTF_SET_START/END`；② `struct btf_kfunc_id_set` 要 **v5.19**
`a4703e318432`（bpf: Switch to new kfunc flags infrastructure）的单 `.set` 指针形态，
v5.18 `dee872e124e8` 的四指针形态套不上 `ext.c` 的 `.set = &scx_kfunc_ids_init`；
③ arm64 的 `bpf_jit_supports_kfunc_call()`（`b5e975d256db`）是独立的一环：本树
`arch/arm64/net/bpf_jit_comp.c` 没有覆盖 `kernel/bpf/core.c` 的 `__weak` 默认（返回 false），
`add_kfunc_call()` 在 JIT 支持性检查上就拒绝，allow-list 再全也没用。

**「KF_ACQUIRE / KF_RELEASE 分类」这个难点的实测结论是不带**：本树没有任何 `KF_*`
（`grep -c KF_ACQUIRE` 于 `include/` 为 0），没有 `btf_kfunc_meta()`，也没有 kfunc 返回值的
`ref_obj_id` 记账；而 flags 与 id 同行要求 `struct btf_id_set8`，其 `.BTF_ids` 段布局由
`resolve_btfids` 宿主工具从裸段解析（`tools/bpf/resolve_btfids/main.c` 只认 `btf_id_set` 形状，
格式处理全部就是 main.c:637 那一次 `qsort`），换布局必须同时改该工具，而本机没有编译门可以证明。
所以 Batch 53 把 `BTF_SET8_*` 折到既有 `BTF_SET_*`（发出的段逐字节不变）、`BTF_ID_FLAGS`
丢弃 flag 实参：**id 注册，flag 不注册**。代价是成对释放不再被 verifier 诊断、
`KF_TRUSTED_ARGS` 不再检查 —— 已用 `REQUIRED_ABSENT` 钉住 `btf_id_set8` /
`enum btf_kfunc_type` / `acquire_set` / `btf_get_module_btf` / `kfunc_set_tab` 不得出现，
避免日后半途带上一半。注册表形态也随之改为全局按 program-type hook（本树没有 per-BTF 的
`kfunc_set_tab`、也没有 `btf_get_module_btf()`），并拒收模块拥有的 id set。

## 2c. 编译实测：vendored 引擎在 5.15 上编不过（2026-10-06）

Batch 55 声称「载荷参与编译」，但当时没有任何一道门跑编译器——七门全是文本门。Batch 56 补上了这道门：
在一棵 `CONFIG_SCHED_CLASS_EXT=y` 的 `android13-5.15-lts`（5.15.220）树上用 clang 编 `kernel/sched/
sched_ext_glue.o`，**结论是 vendored 字节直接编不过**。与此同时同一棵树上 `kernel/sched/core.o`、
`fair.o`、`idle.o`、`debug.o`、`kernel/fork.o` 五个对象**全部编过**，所以问题不在 Batch 55 的接线，
而在 `ext.c` 自身：它是对着 6.6 的 sched core 与 BPF verifier 接口写的，而本分支的 BPF 核心反而比 6.6
更新（`btf_struct_access` / `check_member` 已是新形态）。

复现（`tools/compile_probe.sh`，build-host 助手，不进设备、不写 `embed.conf`）：

```sh
cd <tree> && scripts/config --enable SCHED_CLASS_EXT && make ARCH=arm64 LLVM=1 olddefconfig
sh tools/compile_probe.sh <tree>                                  # 默认编 glue + core/fair/idle/debug/fork
PROBE_OBJS=kernel/sched/sched_ext_glue.o sh tools/compile_probe.sh <tree>   # 只看载荷
```

`-ferror-limit=0` 下共 12 类（12 类去掉后仍有 19 条 error 行，重复 include 一类的 20 条已由本批修掉）：

| # | 位置（`kernel/sched/ext.c`） | 症状 | 本分支的实际形态 | 归口 |
|---|---|---|---|---|
| 1 | 2040 / 2042 / 2449 | `struct affinity_context` 不完整类型；`set_cpus_allowed_common()` 少一个参数；`.set_cpus_allowed` 函数指针类型不符 | 5.15 是 `set_cpus_allowed(p, mask, flags)` + `set_cpus_allowed_common(p, mask, flags)`；`struct affinity_context` 到 6.2 才有 | glue 垫片（结构体 + 双参转三参宏） |
| 2 | 2257 | `sched_weight_to_cgroup()` 未声明 | 该 helper 6.6 才进 `kernel/sched/sched.h`（`static inline`） | glue 垫片（照 6.6 定义补） |
| 3 | 2298 | `p->sched_prop` 不是 `task_struct` 成员 | 它是 OPPO `CONFIG_SLIM_SCHED` 的**厂商字段**（6.6 树里位于 `#ifdef CONFIG_SLIM_SCHED` 的 KABI 槽 1），与 SCX 无关 | 需决定；**不占 KABI 槽**（Batch 16 的原则是删死 KMI 面，不是加） |
| 4 | 2631 / 2669 / 2977 | `SCHED_CHANGE_BLOCK` 未定义 | 该 guard 宏 6.2 才有，6.6 由 `sched_change_guard_init/fini` 支撑 | glue 垫片或按 5.15 的 `deactivate_task`/`activate_task` + `check_class_changed` 展开 |
| 5 | 2673 / 2980 | `__setscheduler_prio` 不可见 | 5.15 是 `core.c:6958` 的 `static` | registry 组：去 `static` + 进 `sched.h` 声明 |
| 6 | 2677 / 2983 | `check_class_changed` 不可见 | 5.15 是 `core.c:2152` 的 `static inline` | 同上 |
| 7 | 3114 | `.btf_struct_access` 字段是 7 参形态，载荷给 4 参 | 本分支已带 ACK 回移的 `(log, btf, t, off, size, atype, next_btf_id)` | **必须改载荷**：函数指针类型宏桥不了 |
| 8 | 3123 / 3159 | `__btf_member_bit_offset` 未声明 | 5.15 叫 `btf_member_bit_offset()`（`include/linux/btf.h:196`），语义逐字相同 | glue 垫片（一行 `#define`） |
| 9 | 3206 | `struct bpf_struct_ops.check_member` 是 2 参形态，载荷给 3 参 | 本分支是 `(t, member)` | **必须改载荷** |
| 10 | 3221 | `sysrq_key_op.handler` 要 `void (int)`，载荷是 `void (u8)` | 5.15 的 `struct sysrq_key_op.handler` 收 `int` | **必须改载荷**（一行） |
| 11 | 3253 | `for_each_cpu_andnot` 未定义 | 5.15 无此宏 | glue 垫片（用 `for_each_cpu` + `cpumask_test_cpu` 展开，注意体内 `break`/`continue`） |
| 12 | 3327 | `__diag_ignore_all` 未定义 | 5.15 只有 `__diag_ignore(compiler, version, option, comment)` | glue 垫片 |

第 1、2、8、11、12 类是纯接口垫片，可以全部放进模块自写的 glue TU（它本来就是「载荷缺的那层」）；
第 5、6 类是接线（把 5.15 已有的两个 helper 变成可见），属于 registry 组；第 3、4 类要决定策略；
**第 7、9、10 类是函数指针/签名不匹配，C 预处理器无法桥接，必须编辑 `ext.c`。**
（以上「归口」是 Batch 56 的初判；Batch 57 落地后的实测落点见 §2d —— 第 1 类最终也是
载荷编辑而不是宏垫片：函数**定义**的参数表预处理器改不了，只能改载荷或改指针类型，
而本树 sched_class 的字段类型由 5.15 定死；第 4 类的 guard 因 gnu89 与 core.c 的两个
static inline 依赖，落 core.c 而非 glue。）

**对「逐字节 vendor 字节」约定的结论**：`files/kernel/sched/ext.c` 仍然按 URL 逐字节归档并钉 sha256
（可复现性不变），但**落到树里必须经过一层有标记的 5.15 适配**——S2b-2 的实质因此不是「加 fork 钩子」，
而是「载荷适配层 + 钩子」。适配层落在哪里（overlay 内的 Python 适配器，还是 registry 组 + 调整 overlay 顺序）
见 §5 与 `plan.md` 的 S2b-2 分组。

## 2d. 适配层落地：载荷首次编过（2026-10-06，Batch 57）

Batch 56 量出 12 类接口漂移后，Batch 57 把适配层落完，并在**同一棵已配置的
5.15.220 树**上用同一道编译门复测：`tools/compile_probe.sh` 在整模块已 graft 的
树上编 6 个对象（`sched_ext_glue.o` + `core.o`/`fair.o`/`idle.o`/`debug.o`/
`kernel/fork.o`），**6 个全部编过**（`OK (6 object(s) built)`）。至此「这份载荷
能不能在这条分支上编译」由否证变为肯定，S2b-2 只剩功能钩子。

12 类的落点（逐条对应 §2c 的表）：

| # | 落点 | 形态 |
|---|---|---|
| 1 | 载荷编辑 | `set_cpus_allowed_scx()` 改成 5.15 的 `(p, newmask, flags)`；不定义 `struct affinity_context` |
| 2 | glue 垫片 | `sched_weight_to_cgroup()` 照 6.6 定义（`CGROUP_WEIGHT_*` 经 sched.h → cgroup.h 可达） |
| 3 | 载荷编辑 | OPPO 的 `p->sched_prop = 0;` 删除（厂商 KABI 成员，本树无载体，SCX 只写不读） |
| 4 | registry 组 | `SCHED_CHANGE_BLOCK` 的 guard 落 `core.c`，三处调用点按 5.15 展开成显式块 |
| 5/6 | registry 组 | `__setscheduler_prio()` 去 `static`、`check_class_changed()` 去 `static inline`，声明进 `kernel/sched/sched.h` |
| 7 | 载荷编辑 | `btf_struct_access()` 改成本树的 7 参形态（ACK 回移） |
| 8 | glue 垫片 | `#define __btf_member_bit_offset btf_member_bit_offset` |
| 9 | 载荷编辑 | `check_member()` 改成 2 参 |
| 10 | 载荷编辑 | sysrq handler 改成 `int` |
| 11 | glue 垫片 | `for_each_cpu_andnot()` + `sailboat_cpumask_next_andnot()`（5.15 两者都没有） |
| 12 | glue 垫片 | `__diag_ignore_all()` → `__diag_ignore(clang, 11, ...)`；本树 `compiler-clang.h` 只定义 `_11`/`_23`，照 6.6 写 `_13` 是未定义宏错误 |

**两处只有编译器能发现的第二层陷阱**（都属于 AGENTS.md 的「按目标树核 helper」一类）：

1. `SCHED_CHANGE_BLOCK` 的 6.2 宏把 guard 声明在 for-init 里，而本分支仍是
   `-std=gnu89`：clang 以 `-Wgcc-compat` 报错（`CONFIG_WERROR=y` 下即 error）。
   把 guard 整体挪进 glue 也不行 —— 它的函数体要调 `dequeue_task()` /
   `enqueue_task()`，而这两个在本树是 `core.c` 的 `static inline`（体内带
   `trace_android_rvh_*` 厂商钩），glue TU 看不到，复制一份又会分叉记账。故 guard
   落 core.c、调用点展开，宏本身不携带。
2. `__setscheduler_prio()` 与 `check_class_changed()` 在 5.15 都是 TU 内可见
   （`static` / `static inline`），载荷是独立 TU，必须去静态化并在 sched.h 声明；
   去静态化之后 `-Wmissing-prototypes`（W=1）又要求原型 —— 两件事是同一件事的两半。

**载荷编辑的记账方式**：`files/kernel/sched/ext.c` 仍按 URL 逐字节保存、sha256
钉死（本批一行未动），适配由 registry 组 `sched_ext_payload_adapt` 在 overlay 把
文件创建出来之后施加，编辑点全部带 `sailboat_sched_ext_payload_adapt` 标记。
overlay 因此自本批起在 perf child **之前**先物化载荷（该 child 要改它），child
之后再跑一次以写空的 `.abk-orig` diff base；而「已安装」的判据同时接受逐字节
相同与带标记的适配产物，免得把自家适配过的 ext.c 当成外来文件。两道树级审计的
fixture（`tests/audit_fixture.py`）也据此把 overlay 创建的文件从 `files/` 供给
step_audit / implementation_audit —— 否则针对载荷的组会在参考树上以
`source tree is missing kernel/sched/ext.c` 收场，读起来像参考树坏了。

**第三个缺陷只有端到端门能发现**：`scripts/abk_common.py` 的 `write_text()` 原先
对创建型文件也做 `<file>.abk-orig` 快照，于是 ext.c 的 diff base 被填成**适配前的
载荷**（113 KB），`config_gate_audit` 只会把适配的几行当成新增代码、载荷内部的门不再
被审计。修法：带 `<file>.abk-new` 的文件不快照，base 保持为空——这正是创建型文件
约定的原意；`tests/smoke.sh` 的「创建型文件 diff base 必须为空」断言就是抓到它的
那道门。

## 2e. 功能钩子落地：任务生命周期（2026-10-06，Batch 58）

载荷编过之后，「类可达」还差两件事：把任务放进 class 的**调用点**，以及让
`SCHED_EXT` 通过策略校验。后者在本树是 `kernel/sched/sched.h` 的
`valid_policy()`（只接受 idle/fair/rt/dl），属于「可达性批次」；前者按内核路径天然
分成两半——**任务生命周期**（fork / `sched_setscheduler()` / 销毁）与**调度核心**
（pick 路径 / tick / idle / debugfs）。Batch 58 落前半。

| 5.15 落点 | 原形 | 本批动作 |
|---|---|---|
| `sched_fork()` | `return -EAGAIN` on DL priority | 前置 `scx_pre_fork(p)`；选类处加 `task_on_scx()` 分支；DL 分支改 `goto out_cancel`（新增标签，调用 `scx_cancel_fork()`） |
| `sched_cgroup_fork()` | `void`，无返回值 | 改 `int`，末尾 `return scx_fork(p);`（原型在 `include/linux/sched/task.h` 同步） |
| `sched_post_fork()` | 只调 `uclamp_post_fork()` | 末尾调 `scx_post_fork()`（fork reader lock 的成功路径释放） |
| `sched_cancel_fork()` | **不存在**（6.4 才加） | 新增，调 `scx_cancel_fork()` |
| `copy_process()` 失败链 | 无 `bad_fork_sched_cancel_fork` 标签 | `perf_event_init_task()` 失败与 `sched_cgroup_fork()` 的拒绝都汇到新标签 |
| `__put_task_struct()` | 只做 `io_uring_free()` 等 | 前置 `sched_ext_free(tsk)` |
| `__setscheduler_prio()` | 只判 dl/rt/fair | 加 `task_on_scx()` 分支（该函数是 Batch 57 去静态化的那个，见下） |
| `__sched_setscheduler()` | 只拒 `rq->stop` | 紧随其后调 `scx_check_setscheduler()` |

**三处只有内核结构能证伪的东西**：

1. **5.15 的 `sched_fork()` 没有 unwind 点。** DL 拒绝发生在 `scx_pre_fork()`
   取到 fork reader lock **之后**，直接 `return` 会永久泄漏；6.6 的形状（`int ret` +
   `out_cancel:`）必须补出来。
2. **`sched_cancel_fork()` 与它的标签在本树都不存在。** 另有一件是查出来的：本树
   fork 失败走 `delayed_free_task()` → `free_task()`，**不经过
   `__put_task_struct()`**，所以 `sched_ext_free()` 的 `list_del_init()` 只会看到
   `scx_post_fork()` 已链接的节点——这是把调用点放在 6.6 位置的前提，也意味着 fork
   失败路径上那点 entity 尚未回收（已记入 CHANGELOG 的「已知残留」）。
3. **trap 5 再碰一次。** `sched_ext_setscheduler_hooks` 改写的
   `__setscheduler_prio()` 正是 `sched_ext_core_visibility` 生成的文本，后者因此
   补了自身 marker 探针，否则二遍会以 `blocked_by_missing_anchor` 收场。

**落地分组**：`sched_ext_fork_hooks`（fork 路径）、`sched_ext_fork_failure_path`（`copy_process()` 的 unwind 标签）、`sched_ext_task_teardown`（`__put_task_struct()`）、`sched_ext_setscheduler_hooks`（选类与策略门）、`sched_ext_payload_task_guard`（载荷守卫）。**载荷守卫**：这批钩子是 `scx_pre_fork()`/`sched_ext_free()` 的第一批调用者，随即
暴露了归档载荷的两处缺陷——分配失败静默穿过（后续四处解引用 `p->scx`），以及
`tasks_node` 从未初始化、分配出的 entity 从未释放。按「分配失败 = 该任务不进入
SCX」补齐四处守卫，并补 `INIT_LIST_HEAD()` 与缺失的 `kfree()`；全部带
`sailboat_sched_ext_payload_task_guard` 标记，`files/` 内字节不变。

**验证**：`tools/compile_probe.sh` 在该树上 6 个对象全部编过（含
`kernel/fork.o`），七道文本门全绿。**可达性仍未改变**：`valid_policy()` 与 pick
路径都是下一批的事，本节表格里没有一处在没有 BPF 调度器时会影响调度行为。

## 2f. 功能钩子落地：调度核心（2026-10-06，Batch 59）

任务生命周期之后，剩下的是「已经在 class 上的任务怎么被选中」。本批落 pick 路径、tick 看门狗与 idle 过渡，外加一处载荷头文件里必须改的东西 —— class 表遍历方向。

| 5.15 落点 | 原形 | 本批动作 |
|---|---|---|
| `kernel/sched/ext.h` 的 `next_active_class()` | `class++` + `__sched_class_highest`/`__sched_class_lowest` | 改 `class--` + 本树 `sched_class_highest`/`sched_class_lowest` |
| `__pick_next_task()` | fair 快路径 + `for_each_class()` | 入口 `if (scx_enabled()) goto restart;`；循环改 `for_each_active_class()` 并调 `scx_notify_pick_next_task()` |
| `put_prev_task_balance()` | `for_class_range(prev->sched_class, idle)` | 改 `for_balance_class_range()`，让 `prev` 高于 ext 时也跑 `balance_scx()` |
| `scheduler_tick()` | 无 SCX 调用 | rq 解锁之后、`perf_event_task_tick()` 之前调 `scx_notify_sched_tick()` |
| `set_next_task_idle()` / `put_prev_task_idle()` | 无 SCX 调用 | 分别调 `scx_update_idle(rq, true)` / `scx_update_idle(rq, false)` |

落地分组：`sched_ext_active_class`（载荷头文件）、`sched_ext_pick_path`（pick 与 balance）、`sched_ext_tick_watchdog`、`sched_ext_idle_hook`；注册顺序即本表自上而下。

**表遍历方向（本批唯一必须动载荷的地方）**：载荷按 6.6 写，用 `class++` 在 `__sched_class_highest`/`__sched_class_lowest` 之间走，而这两个名字是 6.12 的链接器段符号，本树命中 0。5.15 的表是 `SCHED_DATA` 数组：地址升序 = 优先级降序（idle, ext, fair, rt, dl, stop），`kernel/sched/sched.h` 的 `for_class_range()` 用 `class--` 从 `sched_class_highest` 往下走。ext 在两种布局里都夹在 idle 与 fair 之间，所以跳过逻辑（`scx_switched_all()` 跳 fair、`!scx_enabled()` 跳 ext）与方向无关，只有步进方向与两个界名要改。**反例**：给 6.12 那两个名字补一组同名宏会让遍历从最低优先级开始，比不改更坏 —— 所以这里的适配落在载荷副本上，与 `sched_ext_payload_adapt` 同一约定。

**未落地**：`ext` debugfs（`kernel/sched/debug.c` 得先进 `FETCH_FILES`/`AUDIT_FILES` 并重取参考树）、`valid_policy()`/`normal_policy()` 与 defconfig tier。类仍不可达；本批全部钩子在 `scx_enabled()` 为假时与既有行为一致 —— walk 跳过 ext，其余调用各自带 static-key 早退。

## 2g. 可达性落地：策略门 / 优先级区间 / struct_ops 类型 / debugfs（2026-10-06，Batch 60）

Batch 58/59 把调用点接完，但类仍然**不可达**：syscall 在 `valid_policy()` 就被 `-EINVAL` 挡回，BPF 侧也没有 `sched_ext_ops` 这个 value type 可以绑定。本批把四道门一次关掉。

| 5.15 落点 | 原形 | 本批动作 |
|---|---|---|
| `kernel/sched/sched.h` 的 `fair_policy()` | 只认 SCHED_NORMAL / SCHED_BATCH | 还原 6.6 的 `normal_policy()`（`CONFIG_SCHED_CLASS_EXT` 下 SCHED_EXT 也算「normal」），`fair_policy()` 改为 `normal_policy(policy) \|\| SCHED_BATCH` |
| `sched_get_priority_max()` / `min()` | `case SCHED_IDLE:` 之后没有 SCHED_EXT | 各加 `case SCHED_EXT:`（loader 要先问这个策略的区间） |
| `kernel/bpf/bpf_struct_ops_types.h` | 只有 `tcp_congestion_ops` | 加 `#ifdef CONFIG_SCHED_CLASS_EXT` + `BPF_STRUCT_OPS_TYPE(sched_ext_ops)` |
| `kernel/sched/debug.c` 的 `sched_init_debug()` | 无 ext 项 | 加 `debugfs_create_file("ext", 0444, debugfs_sched, NULL, &sched_ext_fops)` |

落地分组：`sched_ext_policy_valid`（策略门）、`sched_ext_priority_range`（区间）、`sched_ext_struct_ops_type`（BPF 注册表）、`sched_ext_debugfs`。

**为什么 SCHED_EXT 必须走 `fair_policy()`、而不是只放宽 `valid_policy()`**：`__setscheduler_params()` 的 `else if (fair_policy(policy))` 分支负责写 `p->static_prio`，随后的 `set_load_weight()` 负责写 `p->se.load`。SCX 任务虽然由 BPF 调度器挑选，这两个字段仍必须合法（weight 也是 `scx_bpf_task_*()` 系列与 cgroup 权重换算的输入）。只放宽 `valid_policy()` 会让任务带着没初始化的权重进 class。

**为什么注册表在 `bpf_struct_ops_types.h`**：这个头被 `kernel/bpf/bpf_struct_ops.c` 用同一个宏定义**包含四次**（extern + map value 结构、enum、注册数组、`BTF_TYPE_EMIT`），所以一行 `BPF_STRUCT_OPS_TYPE(sched_ext_ops)` 同时产出四样东西；缺了它，payload 里那个 `bpf_sched_ext_ops` 没有可绑定的 type id，`sched_ext_ops` 类型的 struct_ops map 根本载不进来。

**fixture**：`kernel/bpf/bpf_struct_ops_types.h` 与 `kernel/sched/debug.c` 是本批新进 `FETCH_FILES` 的两个文件；两棵参考树已重取（远端仍是 `SUBLEVEL = 220`，行未变），`AUDIT_FILES`/`SMOKE_FILES` 同步补齐。

**defconfig 取舍**：`CONFIG_SCHED_CLASS_EXT` 继续留在 **module tier**（`_MODULE_CONFIGS`）。它是 static-key 控制的 class，没有 BPF 调度器时既无任务也无行为变化，代价只是构建体积；改放 ROM tier 会让整段 S2 在默认构建里成为死代码。

**未落地**：S3 用户态 loader（Android 无 systemd，随 companion 下发 `service.sh`）与整条链的真机验证。

## 2h. S3 用户态（一）：最小调度器先落地（2026-10-06，Batch 61）

S2 落地后类可达，但没有任何用户态程序能把它打开。S3 因此拆两步：先证明 **BPF 侧**编得过、且只调用本载荷注册过的 kfunc（本批），再落 loader 与随 companion 下发的打包（下一步）。

**本批产物**：`tools/scx/abk_scx_min.bpf.c` + `tools/scx/abk_scx_compat.h` —— 单 DSQ（FIFO、默认 slice、无 vtime、无 per-CPU DSQ）的最小 SCX 调度器，只接 `select_cpu`/`enqueue`/`dispatch`/`init`/`exit` 五个 op，且**从不调用 `scx_bpf_switch_all()`** —— 因此默认只接管显式标为 `SCHED_EXT` 的任务。`tools/build_scx_artifacts.sh`（build-host 助手，不进 `embed.conf`）从**已嫁接**的内核树 dump `vmlinux.h` 并编 `.bpf.o`。

**为什么必须用已嫁接树的 BTF**：`struct sched_ext_ops` 是载荷自带的类型，只有嫁接之后再编的内核才会把它放进 `vmlinux` 的 `.BTF`。实测：树上现存的 `vmlinux`（10-01 编，早于 Batch 54）在 `.BTF` 里 `grep sched_ext_ops` **0 命中**；本批因此重跑了整棵树的 `make ARCH=arm64 LLVM=1 vmlinux`（顺带成为一次全内核编译门，见 §2i）。

**kfunc 面收口**：载荷注册 21 个 `scx_bpf_*`，最小调度器只用其中 4 个（`dispatch`/`create_dsq`/`destroy_dsq`/`consume`）。单测把「compat 头里 extern 的集合」与「载荷 `BTF_ID_FLAGS(func, ...)` 集合」做成子集断言，把 `SCX_SLICE_DFL` 的数值对齐到载荷头文件的定义，并禁止 `scx_bpf_switch_all` —— 于是调度器不可能编在「本内核没有的 kfunc」上，也不会把整机交给 SCX。

**实测的 loader 构建约束（下一步要用）**：loader 要 libbpf，libbpf 要 libelf。

| 目标 | 现状 | 结论 |
|---|---|---|
| host x86_64 | `/usr/lib/libelf.a`、`libelf.h`/`gelf.h` 齐；内核树 `tools/lib/bpf` 可编 | 可做 loader 的编译/链接门 |
| device aarch64-linux-android | NDK 28.2.13676358 内 `find -iname '*elf*'` **0 命中** | 需先把 elfutils 的 libelf 交叉编到该三元组，或换一个自带 libelf 的包 |
| BPF 编译 | 本机 clang LLVM 22.1.8，`--print-targets` 含 `bpf` | 调度器可编（本批已做） |
| bpftool | 系统未装，内核树 `tools/bpf/bpftool` 源码在 | 由 `build_scx_artifacts.sh` 现编 |

`tools/build_scx_artifacts.sh` 把 aarch64 这一条打出来就停 —— 不伪造成「已构建」。

## 2i. 整内核编译门与调度器实编（2026-10-06，Batch 61）

Batch 56 起，编译验证只到 7 个对象（`tools/compile_probe.sh`）。本批在已嫁接的 5.15.220 树上跑完整棵树：

```
make ARCH=arm64 -j8 LLVM=1 vmlinux   ->  rc=0，2667 个 CC，产出 vmlinux 362,184,544 B（含 BTFIDS/SORTTAB/SYSMAP）
```

这是 Batch 54–60 的 SCX 嫁接第一次以**整内核规模**编译。三点实测：

- **vmlinux 必须晚于嫁接**：重编前，树上那份 10-01 的 vmlinux 在 `.BTF` 里 `sched_ext_ops` 命中 **0**；重编后 `bpftool btf dump file vmlinux format c` 的输出里 `grep -c '^struct sched_ext_ops {'` = **1**。所以「对着哪一棵树编调度器」不是形式问题。
- **`.config` 被 syncconfig 重写过**：该树 `.config` 由模块 defconfig lane 生成，首次 `make` 触发 NEW 符号提示（243 行 `Error in reading or end of file.`，即对 NEW 符号取默认 n）。事后核对 `CONFIG_SCHED_CLASS_EXT=y` 与 `CONFIG_DEBUG_INFO_BTF=y` 仍在。
- **bpftool 需现编**：系统未装，脚本从该内核树 `tools/bpf/bpftool` 编出来（libelf 系统有）。

调度器实编（`tools/build_scx_artifacts.sh <tree>`）：

| 产物 | 结果 |
|---|---|
| `vmlinux.h` | 2,904,667 B，dump 自重编后的 vmlinux |
| `abk_scx_min.bpf.o` | 617,936 B（含 `.BTF` 与 `.BTF.ext`） |
| 段 | 5 个 `struct_ops/...` 程序 + `struct_ops.s/abk_scx_min_init`（sleepable）+ `.struct_ops` map（0x150 B）+ `.rel.struct_ops` |
| 未定义符号 | **恰好 4 个**：`scx_bpf_dispatch` / `scx_bpf_create_dsq` / `scx_bpf_destroy_dsq` / `scx_bpf_consume` —— 与单测的「调用集合 ⊆ 注册集合」断言一致 |

loader 的 aarch64-android 构建仍停在 §2h 记录的工具链缺口上（NDK 无 libelf）。

## 2j. loader 落地与 host 门（2026-10-06，Batch 62）

`tools/scx/scx_loader.c`（五个子命令，默认不动作）+ `tools/build_scx_artifacts.sh` 的 `BUILD_HOST_LOADER=1`：编 libbpf → 链 loader（`-Werror`）→ 用 `selftest` 解析 `.bpf.o`。

实测（host x86_64，WSL）：

- **libbpf**：`make -C <tree>/tools/lib/bpf` 直接编成（系统 libelf/zlib 都在）。
- **loader**：链接通过。首个编译在 `-Werror` 下报 `SCHED_NORMAL undeclared` —— glibc 只有 `SCHED_OTHER`，内核/bionic 才两个名字都认；loader 因此自带 `#ifndef SCHED_NORMAL / #define SCHED_NORMAL 0`。
- **`selftest`**：`object: abk_scx_min`；`map: abk_scx_min_ops type=26 key=4 value=336`（type 26 = `BPF_MAP_TYPE_STRUCT_OPS`，value 336 B = 段表里的 `.struct_ops` 0x150）；5 个 program 的 section 与 Batch 61 的段表逐个一致（`struct_ops/…select_cpu|enqueue|dispatch|exit` + `struct_ops.s/…init`）。这就是「这个对象 libbpf 能理解」的可执行证据 —— 没有内核、更没有设备。

设备侧仍缺 libelf（§2h），所以交叉链与 companion 打包是下一步。

## 2k. S3b-2：companion 策略落地，与「二进制从哪来」的结论（2026-10-06，Batch 63）

**策略已落地**（默认关，三道前置条件各自记因）：`ksu/abk_runtime_tunables/scx-policy.sh` + 三个新 tunables 键（`scx.enabled` / `scx.mark_pids` / `scx.reassert_interval_sec`）+ `service.sh`/`action.sh` 接线。细节见 CHANGELOG#batch-63。

**一个结构性结论（它决定了二进制怎么来）**：调度器对象必须对着**已嫁接且已重编**的内核编（§2i 实测：嫁接前的 vmlinux BTF 里 `sched_ext_ops` 命中 0），而 ABK 的注入阶段（`after_patch` / `before_build`）都发生在内核构建**之前**。于是：

- `after_patch` 阶段**不可能**现产 `.bpf.o` —— 那时还没有嫁接后的 vmlinux；
- 所以产物只能由维护者或一个独立作业产出后随包下发（或另找一条不依赖目标 BTF 的路）。

**aarch64-android loader 的三条候选路线**（都未落地，按代价排序）：

| 路线 | 做法 | 代价 / 风险 |
|---|---|---|
| ① 交叉编 elfutils 的 libelf | 用 NDK 把 `libelf/` 编到 `aarch64-linux-android`，再编 libbpf → loader | elfutils 需要一份手写的 config.h/gnulib 面，迭代成本中等；产出最正统 |
| ② 构建机 / CI 产出二进制 | 在 Linux 主机上编好 arm64 loader 再进 zip | 仍要一条能编 android 目标的链（同样要 libelf），只是把成本挪到 CI |
| ③ 最小 libelf 垫片 | 只实现 libbpf 用到的那组 `elf_*`/`gelf_*`（约 300–500 行），host 上用 `selftest` 先验证，再交叉编 | 需要精确覆盖 libbpf 的调用面；验证路径清楚（host 跑通即证明垫片可用） |

**共同前提**：`.bpf.o` 本身已经能产出（Batch 61 实测 617,936 B，段表与「恰好 4 个 kfunc 符号」都对），所以卡点只在 loader 的 libelf 这一环。

## 2l. loader 的 Android 路：只读 libelf 垫片（2026-10-06，Batch 64）

libbpf 要 libelf，而 NDK 里没有（§2k 的三条路线）。本批取路线 ③：`tools/scx/libelf-shim/` 实现 **libbpf 实际用到的那 17 个函数**（外加 5 个仅为链接存在的声明/写侧桩），只读、只支持 ELF64 小端、每段单块数据、写侧一律失败。

**正确性不是论证出来的，是量出来的**：`BUILD_SHIM_LOADER=1` 会把同一份 `.bpf.o` 的 `selftest` 跑两遍 —— 一遍真 libelf、一遍垫片 —— 然后 `diff`。实测输出完全一致：

```
build_scx_artifacts: shim parse == libelf parse (selftest identical)
object: abk_scx_min
map:    abk_scx_min_ops          type=26 key=4 value=336
prog:   abk_scx_min_select_cpu   section=struct_ops/abk_scx_min_select_cpu
... （5 个 program）
ops map: abk_scx_min_ops
```

**首次尝试被这个 diff 抓到的两处**（都是「不跑就不知道」的类型）：

1. `gelf_getshdr` 在真 libelf 里返回 **`GElf_Shdr *`**，不是 int —— libbpf 写的是 `if (gelf_getshdr(...) != &sh)`，返回 int 时 `-Werror` 直接拦下。
2. `elf_nextscn(elf, NULL)` 必须从**段 1** 开始。libbpf 的循环是 `scn = NULL; while ((scn = elf_nextscn(elf, scn))) { idx++; ... }`，它假定第一个真实段就是 1；垫片若从段 0 起步，每个索引都偏移一位，于是 libbpf 把对象解析成垃圾（表现为 `skipping section(1) (size 0)` 与后面的段越界告警 + 段错误），而 `elf_getdata`/`gelf_getshdr` 单独看全是对的。

**未落地**：`aarch64-linux-android` 的交叉编译本身（NDK clang + 这个垫片）、产物随包下发、真机 A/B。

## 2m. Android 交叉编译完成（2026-10-06，Batch 65）

垫片（§2l）之后，本批把 loader 真的编成 Android 二进制，并把两个产物接进下发链路。

**产物**（提交在 `tools/scx/prebuilt/`）：

| 文件 | 大小 | sha256 |
|---|---|---|
| `scx_loader` | 251,848 B | `3b89a96c12bfdabd1625c92983af577f6741de0dd001829fd258fb06c256234e` |
| `abk_scx_min.bpf.o` | 617,936 B | `1767e64d4edb42a48c907160866bc935222daeeb7fd997d377d1a0c049905866` |

`readelf -h/-d`：ELF64、DYN（PIE）、Machine AArch64；NEEDED 仅 `libz.so`/`libdl.so`/`libc.so` —— **没有 libelf**。

**配方**：`tools/scx/build_android_loader.sh`（NDK clang，`aarch64-linux-android30`）。三块支撑：

1. `libelf-shim/`（§2l）；
2. `android-compat/`：`linux/err.h`、`linux/list.h`、`linux/filter.h`（`BPF_*_INSN`）、`linux/ring_buffer.h` + `kernel-macros.h`（ARRAY_SIZE / container_of / likely / roundup / min / max / READ_ONCE / WRITE_ONCE / `__poll_t` / `in_addr_t`）；
3. 跳过 loader 不调用的 libbpf TU：`linker.c`（静态 ELF 写侧）、`netlink.c`/`nlattr.c`/`xsk.c`/`ringbuf.c`/`usdt.c`（需要内核专有头 `asm/barrier.h`，或与内核 uapi 冲突的 bionic 头链）。

**四处实测的墙**：① 内核 uapi 与 bionic 头互相遮蔽 —— 内核 `linux/compiler_types.h` 会 `#include <linux/compiler-gcc.h>`（直接包含即 `#error`），而 bionic 的 `sys/types.h` 又会拉内核 `linux/posix_types.h`，故采用「bionic 优先 + compat 补齐」；② `in_addr_t` 在本构建里没有任何头定义（bionic 从被遮蔽的 `linux/in.h` 取）⇒ compat 里补 typedef；③ `BPF_*_INSN` 宏只存在于内核 tools 副本 ⇒ `linux/filter.h` 整份进 compat；④ 若让 bionic 优先，libbpf 自带的 `struct bpf_core_relo` 回退定义与 NDK 较新的 `linux/bpf.h` 撞名 ⇒ **内核 uapi 必须在 bionic 之前**，冲突的少数 TU 只能按「不调用」排除。

**为什么提交二进制**：模块在 `after_patch` 打包，早于内核构建；调度器要的 BTF 只存在于嫁接后的 vmlinux ⇒ 现编不成立。`embed.conf` 两行把 `tools/scx/prebuilt/*` 映射到模块 `bin/*`，正是 `scx-policy.sh` 查找的位置；策略仍默认关。

**未落地**：真机。

## 2n. 真机验证的入口（2026-10-06，Batch 66）

S0–S3 的代码全部落地后，剩下的判断只有设备能给出。本批把它做成两件事：一个只读 verdict 工具 `tools/abk_scx_check.sh`（随 companion 下发），和 `plan.md` 里的四步验证计划。

工具区分四种状态（退出码即接口）：`0` 已挂载且有 SCHED_EXT 任务、`2` `scx.enabled=0` 未尝试、`3` switch 开但引擎未挂载、`4` 已挂载但零任务。第 4 种是**刻意的起点**：`scx.mark_pids` 留空时挂在 class 上但不移动任何任务，正是首次真机测试该处的状态，一个布尔值会把它和「没挂上」混淆。

计数用 `/proc/<pid>/stat` 的第 41 项（policy）—— 已与 `fs/proc/array.c` 的 `do_task_stat()` 字段顺序核对（`rt_priority` 之后即 `policy`），并用 `ps -o policy` 在 policy 0 上交叉验证；因 `comm` 可含空格与 `)`，解析用贪婪 `sed 's/^.*) //'` 后取第 39 项。

四步（细节在 plan.md）：① 默认关 → 期望 exit 2 且 class/artefact 都在；② `scx.enabled=1` + 空 mark list → 期望 exit 4（只验挂载路径）；③ 点名 surfaceflinger 与目标 App 主线程 → 期望 exit 0 并记录任务数；④ 稳定后才做冷启动/帧率 A/B，且每次连同本工具输出留档，否则无法把差异归因到 SCX 还是其余旋钮。**Tier B/D/E 的取舍以此为前置**（清单与判据见 [survey §2](docs/survey_sched_ext_gap.md) 与 plan.md 的同名小节）。

## 2o. Tier B 的 uclamp 旋钮（默认关，2026-10-06，Batch 67）

计划里 Tier B 唯一零内核改动的一项：用 cgroup v2 的 `cpu.uclamp.min`（`CONFIG_UCLAMP_TASK_GROUP=y`，已在该树 `.config` 核实）抬高指定 cgroup 的利用率下限。

**格式来自内核源码**：`kernel/sched/core.c` 的 `capacity_from_percent()` 接受 `max` 或十进制百分数（`50`=50%、`12.5`=12.5%，>100 `-ERANGE`）。模块在写之前自己校验并拒绝非法值 —— 内核静默忽略的写入等于「开了但没生效」，这是本仓已经付过一次的学费（Batch 42 `cap_pct`）。

**fixture 实跑**（假 `ABK_SYS_ROOT`，六个用例）：两旋钮皆空 → 不写 + 记「缺哪半」；只给值 → 不写；`50`+`top-app` → 只有 top-app 变 50；`12.5` + 两个组（含一个不存在）→ 两个真组各 12.5、不存在的 warn 跳过；`150` → 未写 + `WARN refusing`；`max` → 接受。

**未落地**：真机 A/B（设一个组 + 一个百分比，同口径冷启动/帧率，连同 `abk_scx_check.sh` 与 `abk_fas_check.sh` 的输出留档）。

## 2p. arm64 产物在主机上的执行证据（2026-10-06，Batch 68）

Batch 65 只证明了产物「是 AArch64 ELF」；`readelf` 看不见代码路径。本批补上执行证据。

**命令**（WSL 侧，`NDK` 可自动探测；需要先跑过一次 `build_android_loader.sh` 以 staging 头文件）：

```
bash tools/scx/run_arm64_selftest.sh <grafted-tree> build/scx-android
```

**实测输出**：

```
run_arm64_selftest: linking a static aarch64 loader for qemu-user
  build/scx-android/scx_loader_static: ELF 64-bit LSB executable, ARM aarch64,
  statically linked, for Android 30, built by NDK r28c
run_arm64_selftest: qemu-user selftest
  exit 0
  object: abk_scx_min
  map:    abk_scx_min_ops          type=26 key=4 value=336
  ... （5 个 program）
run_arm64_selftest: usage path (must be exit 2)
  exit 2
run_arm64_selftest: load path on a kernel without sched_ext (must fail cleanly)
  exit 1   scx_loader: loading … failed: Function not implemented
run_arm64_selftest: SELFTEST IDENTICAL ACROSS ARCH
```

**为什么需要 static**：提交的 loader 是 dynamic bionic，PT_INTERP 指向 `/system/bin/linker64`，NDK 不提供该文件，主机上无法直接执行；static 版本跑的是同一份 libbpf + 垫片 + 解析代码，因此它验证的是代码路径而不是打包形式（后者由 `readelf` 证明）。

**边界**：`run` 的 `Function not implemented` 说明主机内核没有 sched_ext struct_ops —— attach 只能在设备上验证。

## 3. Payload choice

| candidate | size | notes |
|---|---|---|
| OPPO 6.6 `kernel/sched/ext.c` | 113,324 B / 3,978 lines | proven on a GKI + vendor-module arm64 tree; registers kfunc sets via an `ext.h`-local `__bpf_kfunc`; carries 79 `scx_bpf_*` kfuncs; the intended default |
| mainline android16-6.12 `kernel/sched/ext.c` | 219,742 B / 7,570 lines | upstream shape, 159 `scx_bpf_*` sites, 12 `BTF_KFUNC` sets; paired header `include/linux/sched/ext.h` is 7,015 B; only chosen if a feature the OPPO variant lacks is required |
| `Rtoax` 6.1 split (`ext.c` + `ext_idle.c` + `ext_internal.h`) | not measured | a third-party split; provenance and license headers need checking before use |

**Measured at landing (Batch 54): the payload is three files, not two.**  The
table's `kernel/sched/ext.c` is only the engine.  `struct sched_ext_ops` and
`struct sched_ext_entity` live in `include/linux/sched/ext.h`, and `ext.c`
includes neither that header nor `sched.h` — OPPO compiles it from
`build_policy.c`, which has both.  The shipped set (sha256 pinned in
`tests/stable_5_15_test.py`, byte-for-byte as fetched):

| file | bytes | sha256 (prefix/suffix) |
|---|---|---|
| `include/linux/sched/ext.h` | 21,113 | `d0601a9b` … `e81d7a94` |
| `kernel/sched/ext.h` | 8,607 | `459c86fd` … `612ace1a` |
| `kernel/sched/ext.c` | 113,324 | `98860acf` … `0ef93801` |

`ext.c` still carries two commented-out OPPO `slim_walt` lines (a vendor
scheduler include and a call site).  They are comments, they are not upstream
text and not this module's, and they are kept so the file stays reproducible
from its URL; `files/README.md` lists them and `tests/stable_5_15_test.py`
pins that they are the only vendor residue.

Build wiring on 5.15 (no `build_policy.c`): the engine has to arrive as its own
object, so an anchor adds a `CONFIG_SCHED_CLASS_EXT` entry to
`kernel/sched/Makefile` next to the existing `obj-y` block.  **That is not
sufficient on its own**: OPPO's `ext.c` starts with no includes at all — it is
compiled as `# include "ext.c"` from `kernel/sched/build_policy.c`, which has
already pulled in `sched.h` and `ext.h` — so a standalone `ext.o` would not
know what a `struct rq` is.  5.15 has no `build_policy.c`, and OPPO's cannot be
shipped verbatim either: it also `#include`s `idle.c`, `rt.c`,
`cpudeadline.c`, `pelt.c`, `cputime.c` and `deadline.c`, which 5.15 already
compiles as their own objects, so the duplicate symbols would collide.  The glue
S2b-1 added (`files/kernel/sched/sched_ext_glue.c`, Batch 55) is therefore a small
module-authored translation unit -- the 6.6 tree's header set, minus
linux/seqlock_api.h (absent on 5.15) and minus every `#include "*.c"` line -- that
includes `ext.c` at the end; `kernel/sched/Makefile` builds
`sched_ext_glue.o` from it.  It deliberately does not re-include the unguarded
`kernel/sched/ext.h` (sched.h already pulls it in).  The three overlaid engine files
stay upstream/vendor bytes only.

## 4. KMI: a pointer in a KABI reserve slot, not an embedded struct

`struct sched_ext_entity` is far larger than any single KABI slot, so the task
pointer must be indirect.  Precedent, on the same kind of GKI tree as this one:

```c
/* include/linux/sched.h, OPPO 6.6 (inside #ifdef CONFIG_SLIM_SCHED) */
ANDROID_KABI_USE(1, unsigned long sched_prop);
ANDROID_KABI_USE(2, struct sched_ext_entity *scx);
ANDROID_KABI_USE(3, struct task_dma_buf_info *dmabuf_info);
```

This module already arbitrates its own `task_struct` slot in
`scripts/abk_stable_perf.py:354-380` — slot 8 by default, slot 5 when the tree
carries SysVIPC's `ANDROID_KABI_USE(6, struct sysv_sem sysvsem)`.  SCX needs one
more free slot, chosen by the same read-the-tree-first rule; a pointer fits in one
`u64`.  No other exported structure needs a new field.

## 5. Repository mechanics for the payload

SCX would be the **second whole-file payload** in this repository (the first is
`files/drivers/of/address.c`), which `files/README.md` permits only when an
anchor cannot express the shape — a 113 KB new subsystem qualifies.  Required
pieces:

- **landed as Batch 54 / v0.57.0** — the three payload files, the
  `files/README.md` entry (rationale, both gate probes, both snapshot
  conventions, the OPPO-only `slim_walt` comments) and the upstream copyright
  headers kept verbatim;
- **measured as Batch 56 / v0.59.0** — byte-for-byte is not enough to build
  it.  The first real compile of the payload's translation unit
  (`tools/compile_probe.sh`, §2c) fails with 12 classes of 5.15/6.6 interface
  drift, three of them function-pointer signatures no preprocessor shim can
  bridge.  The file therefore stays archived byte-for-byte with its sha256 pin
  (that is the provenance property) and gets a **marked 5.15 adaptation** at the
  point it is materialised.  Which layer carries that adaptation -- a Python
  adapter invoked by the overlay, or a `PatchGroup` plus a reordering that runs
  the overlay before the perf child -- is the open decision for S2b-2, together
  with the five glue-TU shims, the two `core.c` helpers that have to become
  visible, the `SCHED_CHANGE_BLOCK` expansion and the `sched_prop` policy call;
- **landed as Batch 57 / v0.60.0** — the adaptation, and the answer to the
  open decision recorded above: it is **three registry groups**
  (`sched_ext_core_visibility`, `sched_ext_change_guard`,
  `sched_ext_payload_adapt`), not a Python adapter, and
  abk_stable_backport_overlay_sched_ext() now runs **before** the perf child so
  the groups have a file to anchor on (the same overlay is re-run after the child
  to write the empty `.abk-orig` diff base, which needs the Makefile entry the
  child adds).  Per-class placement and the two second-layer traps the compiler
  found (gnu89 `-Wgcc-compat` on the 6.2 block macro, and
  `dequeue_task()`/`enqueue_task()` being core.c-local static inlines) are in
  SS2d.  With it applied, `tools/compile_probe.sh` builds all six objects on a
  configured 5.15.220 tree; the class is still unreachable, because
  `valid_policy()` rejects `SCHED_EXT` and no functional hook is wired.
- **landed as Batch 54** — `abk_stable_backport_overlay_sched_ext()` in
  `scripts/stable_backport.sh`, riding the perf child's dispatch exactly as
  `abk_stable_backport_overlay_of_address()` rides core.  It *creates* rather
  than rewrites, so it needs a snapshot convention the revert never did: a
  zero-byte `<file>.abk-new` marker that `scripts/abk_rollback.sh` turns into
  a delete, plus an empty `<file>.abk-orig` written only once
  `abk_stable_backport_sched_ext_wired()` finds the Makefile entry (while the
  payload is inert, an empty diff base would make `config_gate_audit` report
  every internal gate in `ext.c` as added code that never compiles).  It never
  overwrites a foreign `ext.c`;
- **landed as Batch 54** — the payload pin in `tests/stable_5_15_test.py`
  (`test_sched_ext_payload`: the sha256 of all three upstream files, the LF
  check, the glue unit's structural checks (includes ext.c, no duplicate ext.h
  inclusion, no policy .c includes), the
  overlay/rollback wiring, and a guard that no registry group targets the
  payload before its wiring lands) and the create/idempotent/rollback assertions
  in `tests/smoke.sh`.  Note the §5 estimate that the existing shipped-inventory
  assertion just needs extending was wrong: the only `rglob` inventory in that
  file belongs to the KernelSU companion directories, and neither payload file
  was pinned by any test before this batch;
- **landed as Batch 55 / v0.58.0, split in two** -- S2b-1 is the build wiring
  (seven groups: `sched_ext_kconfig`, `sched_ext_uapi`,
  `sched_ext_task_slot`, `sched_ext_rq_state`, `sched_ext_class_order`,
  `sched_ext_build` and `sched_ext_init`).  Measured against 5.15, it
  needed more than the estimate above: a **module-authored glue translation
  unit** (`files/kernel/sched/sched_ext_glue.c`), because `ext.c` has no include
  block and 5.15 has no `build_policy.c` to include it from (and OPPO's cannot
  be shipped -- it also includes the policy .c files 5.15 builds separately);
  a `sched_class_above()` whose polarity matches 5.15's downward class walk;
  and the `init_sched_ext_class()` call, without which a registered-but-disabled
  class dereferences an unallocated `rq->scx`.  The `task_struct` pointer is
  KABI slot 7 (slot 8 is the Batch-16 kstack member) and the rq pointer is rq's
  slot 1.  S2b-2 remains: the fork/tick/pick/setscheduler hooks, `valid_policy()`/
  `normal_policy()`, `scx_update_idle()` and the debugfs dump; the
  `/sys/kernel/sched_ext/*` control files live in `ext.c` itself and appear
  once a BPF scheduler is loaded.

Coexistence: the default is `SCX_OPS_SWITCH_PARTIAL`, so SCX owns only tasks
explicitly marked `SCHED_EXT`; the CFS path keeps serving everything else.  That
is also what keeps this graft from competing with the vendor WALT/FAS stack for
scheduling and frequency ownership (Batch 10-5/10-6 boundary).  Unloading the BPF
scheduler, `SysRq-S`, an internal error or a runnable stall all return every task
to CFS.

## 6. Go / no-go gate before the payload lands

Proceed with S1 (the two infrastructure backports) only if all hold:

1. the arm64 trampoline + `bpf_arch_text_poke` backport lands as a bounded set of
   groups **without touching the generic BPF core** and compiles on
   `android13-5.15-lts` arm64 — §2a measures three groups over two files,
   re-authored onto 5.15's `bpf_tramp_progs` / `__bpf_prog_enter(prog)` interface
   (a verbatim v6.1 copy does not compile: `bpf_tramp_links` 6 hits,
   `bpf_tramp_run_ctx` 4 hits).  The earlier "at most one `PatchGroup`" wording
   is withdrawn as a measured-false constraint;
2. no new CONFIG gate is introduced that the defconfig lane cannot resolve
   (`tests/config_gate_audit.py` stays green, or the symbol is recorded);
3. `CONFIG_SCHED_CLASS_EXT` resolves to `y` in the built `.config`;
4. the BPF-side regression (fentry/struct_ops) passes on device before the SCX
   payload is added.

If any fails, SCX stays at `[~]` with the S1 results recorded.

Status after Batch 52: item 1 is discharged on the structure half — the three
groups landed over two files (`arch/arm64/net/bpf_jit_comp.c`,
`arch/arm64/net/bpf_jit.h`) plus the instruction helper
(`arch/arm64/include/asm/insn.h`, `arch/arm64/lib/insn.c`), and nothing in
`kernel/bpf/` or `arch/x86/` was touched.  Its compile half follows the ABK CI
build, because a local kernel compile is not part of this repository's gate set.
Status after Batch 55: item 2 is discharged for the SCX side -- `config_gate_audit`
now runs (each batch since 53 re-runs it against the reference device config) and
the five gates the payload introduces are recorded in `DARK_GATES` with reasons.
Item 3 is discharged in the tier sense: `CONFIG_SCHED_CLASS_EXT` exists (Batch
55's Kconfig group), the module tier enables it, and both of its dependencies
(`BPF_SYSCALL`, `BPF_JIT`) are `y` in that config -- the built `.config` is
what the ABK CI run will show.  Item 4 (a device result) is still open and now
also waits on S2b-2: the class is compiled and registered but no task can enter
it, so there is nothing to measure on device yet.

Status after Batch 56: item 1's "compiles on `android13-5.15-lts` arm64" half is
**discharged negatively and precisely**.  A local compile gate now exists
(`tools/compile_probe.sh`) and was run on a configured 5.15.220 tree: the
module's own objects (`core.o`, `fair.o`, `idle.o`, `debug.o`, `kernel/fork.o`)
all build with the whole module applied, while the payload's translation unit
fails with 12 classes of 5.15/6.6 interface drift (SS2c).  The engine is therefore
*in* the build but does not compile, which the earlier text gates could not
distinguish -- so S2b-2 is no longer "add the fork hooks", it is "adapt the
payload to 5.15, then add the hooks", and the `SCX_OPS_SWITCH_PARTIAL`
coexistence argument in SS3/SS6 stays untested until that lands.

Status after Batch 57: the compile half of item 1 is **discharged positively** --
the adaptation layer (SS2d) lands the five preprocessor shims, the two core.c
helpers, the re-carried `SCHED_CHANGE_BLOCK` guard and the marked ext.c edits,
and `tools/compile_probe.sh` then builds all six objects on the same configured
5.15.220 tree (`OK (6 object(s) built)`).  Item 3 stays discharged in the tier
sense.  Batch 58 landed the first half of S2b-2b (§2e): the fork path,
`sched_setscheduler()`'s guard and task teardown, plus the payload guard those
hooks made reachable -- and the probe still builds all six objects.  Item 4 is
still open and now waits on the second half: `valid_policy()` in
`kernel/sched/sched.h` still rejects `SCHED_EXT`, so no task can enter the class
and there is nothing to measure on device.

## 7. Open items

- 5.10 provenance (see §1).
- Which upstream window OPPO's `ext.c` corresponds to, and what a later SCX
  upgrade would additionally need (`bpf_cpumask` and friends, §2).
- Whether the chosen userspace scheduler avoids the post-5.15 kfunc families; if
  not, those backports are added to the project.
- SELinux policy and the absence of systemd on Android (the loader is shipped
  through the KernelSU companion, not an init service).

## 8. Reproduce

```bash
# 5.15 side: which BPF objects exist, and whether the two gaps are real
curl -s 'https://android.googlesource.com/kernel/common/+/refs/heads/android13-5.15-lts/kernel/bpf/' \
  | grep -o '/kernel/bpf/[a-z0-9_]*\.c' | sed 's|.*/||' | sort -u
curl -s 'https://android.googlesource.com/kernel/common/+/refs/heads/android13-5.15-lts/arch/arm64/net/bpf_jit_comp.c?format=TEXT' \
  | base64 -d | grep -c -i 'trampoline\|text_poke'      # 0
curl -s 'https://android.googlesource.com/kernel/common/+/refs/heads/android13-5.15-lts/include/linux/bpf.h?format=TEXT' \
  | base64 -d | grep -n 'struct bpf_struct_ops'

# S1 side (§2a): the v6.1 arm64 trampoline series and the interface it assumes
T=https://kernel.googlesource.com/pub/scm/linux/kernel/git/torvalds/linux/+/refs/tags/v6.1
A=https://android.googlesource.com/kernel/common/+/refs/heads/android13-5.15-lts
for f in arch/arm64/net/bpf_jit_comp.c arch/arm64/net/bpf_jit.h \
         arch/arm64/include/asm/insn.h kernel/bpf/trampoline.c include/linux/bpf.h; do
  curl -s "$T/$f?format=TEXT" | base64 -d > "v6.1-$(basename $f)"
  curl -s "$A/$f?format=TEXT" | base64 -d > "5.15-$(basename $f)"
done
grep -c -i 'trampoline\|text_poke' 5.15-bpf_jit_comp.c    # 0   (v6.1: 29)
for c in efc9909fdce0 b2ad54e1533e 33f32e5072b6 339ed900b307 19f68ed6dc90 aada47665546 eb707dde264a; do
  curl -sL "https://github.com/torvalds/linux/commit/$c.patch" | grep -c '^+'   # 722 added in total
done
grep -n 'bpf_tramp_links\|bpf_tramp_run_ctx' v6.1-bpf_jit_comp.c
grep -n 'bpf_tramp_progs\|__bpf_prog_enter' 5.15-trampoline.c   # 5.15 interface

# S1b side (§2b): the kfunc allow-list does not exist on 5.15
grep -c btf_kfunc_id_set 5.15-btf.h 5.15-btf.c                      # 0 0
grep -n 'check_kfunc_call' 5.15-verifier.c                          # verifier.c:6839 gates on ops->check_kfunc_call
curl -s "$T/v5.17/include/linux/btf.h?format=TEXT" | base64 -d | grep -c btf_kfunc_id_set   # 0
curl -s "$T/v5.18/include/linux/btf.h?format=TEXT" | base64 -d | grep -c btf_kfunc_id_set   # 3 (struct)
curl -s "$T/v5.18/kernel/bpf/btf.c?format=TEXT" | base64 -d | grep -n register_btf_kfunc_id_set
grep -c 'btf_kfunc_id_set' oppo_ext.c                               # 6 declared sets, 6 register_btf_kfunc_id_set() calls

# vendor side: the payload and its wiring
R=OnePlusOSS/android_kernel_common_oneplus_sm8750; BR=oneplus/sm8750_b_16.0.0_oneplus_13
curl -s "https://raw.githubusercontent.com/$R/$BR/kernel/sched/ext.c" | wc -c        # 113324
curl -s "https://raw.githubusercontent.com/$R/$BR/kernel/sched/build_policy.c" | sed -n '58p'
curl -s "https://raw.githubusercontent.com/$R/$BR/include/linux/sched.h" | grep -n 'sched_ext_entity'
curl -s "https://raw.githubusercontent.com/$R/$BR/kernel/Kconfig.preempt" | grep -n -A3 'SCHED_CLASS_EXT'
curl -s "https://raw.githubusercontent.com/$R/$BR/arch/arm64/configs/gki_defconfig" | grep SCHED_CLASS_EXT
```

## 9. Provenance

The SCX code itself is `Copyright (c) 2022 Meta Platforms, Inc. and affiliates`,
`Copyright (c) 2022 Tejun Heo <tj@kernel.org>`, `Copyright (c) 2022 David Vernet
<dvernet@meta.com>`, GPL-2.0 (headers preserved verbatim in both variants
inspected).  The OPPO tree is used here as a **reference** — no vendor code is
copied into this repository by this document, and the payload, when it lands, is
the upstream file with its original headers.  Entries for whatever is finally
grafted belong in `docs/attribution.md`.
