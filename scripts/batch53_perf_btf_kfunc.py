"""Batch 53 (sched_ext S1b): the kfunc allow-list API and the arm64 override.

Three groups, all measured on the android13-5.15-lts reference tree:

* ``btf_kfunc_id_set_api`` -- ``register_btf_kfunc_id_set()`` plus the id-set
  registry the verifier consults.  Source ``dee872e124e8`` ("bpf: Populate
  kfunc BTF ID sets in struct btf", v5.18) for the hook and the registry, with
  the ``struct btf_kfunc_id_set`` member shape from ``a4703e318432`` ("bpf:
  Switch to new kfunc flags infrastructure", v5.19) and the
  ``BTF_SET8_START`` / ``BTF_ID_FLAGS`` macro spelling from ``ab21d6063c01``
  ("bpf: Introduce 8-byte BTF set", v5.19).
* ``struct_ops_kfunc_allow`` -- the verifier gate becomes "the program type's
  own callback, or a registered id set".  Only the additive half of
  ``b202d8442222`` ("bpf: Remove check_kfunc_call callback and old kfunc BTF ID
  API", v5.18) is carried; the removal sweep over kernel/bpf, net/ and
  kernel/trace/ buys SCX nothing.
* ``arm64_jit_kfunc_call`` -- ``bpf_jit_supports_kfunc_call()`` returning true.
  Source ``b5e975d256db`` ("bpf, arm64: Enable kfunc call", v5.19), carried
  verbatim.

Why this is the S1b gap rather than a missing helper: on this tree
``bpf_struct_ops_verifier_ops`` in ``kernel/bpf/bpf_struct_ops.c`` is an empty
struct, so ``env->ops->check_kfunc_call`` is NULL for
``BPF_PROG_TYPE_STRUCT_OPS`` and no kfunc is callable from a struct_ops program;
and ``arch/arm64/net/bpf_jit_comp.c`` does not override
``kernel/bpf/core.c``'s ``__weak bpf_jit_supports_kfunc_call()`` (which returns
false), so ``add_kfunc_call()`` refuses every kfunc call on arm64 outright.
Both have to be fixed before ``kernel/sched/ext.c`` (S2) can run at all.

What is deliberately dropped, measured and not assumed: the kfunc flags.
``ab21d6063c01`` emits a ``struct btf_id_set8`` whose entries carry a per-id
flags word, and ``a4703e318432`` derives KF_ACQUIRE / KF_RELEASE / KF_RET_NULL /
KF_TRUSTED_ARGS from it inside the verifier.  This tree has no
``btf_id_set8``, no ``KF_*`` flag at all (``grep -c KF_ACQUIRE`` over
``include/`` is 0), no ``btf_kfunc_meta()`` and no ref_obj_id plumbing for a
kfunc return, and the ``.BTF_ids`` section layout is parsed out of the raw
section by the ``resolve_btfids`` host tool (``tools/bpf/resolve_btfids/main.c``
knows the ``btf_id_set`` shape only -- its one ``qsort`` at main.c:637 is the whole
of the format handling).  Carrying the real set8 therefore means changing that
host tool as well, with no compile gate available here to prove the change, so
the folded form keeps the emitted section byte-identical and registers the ids,
which is what the payload needs to get past the gate.  The cost is recorded
explicitly: a leaked ``scx_bpf_get_idle_cpumask()`` reference is not diagnosed,
and a ``KF_TRUSTED_ARGS`` argument is not checked for trust.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

T = True  # required step

BTF_IDS_H = "include/linux/btf_ids.h"
BTF_H = "include/linux/btf.h"
BTF_C = "kernel/bpf/btf.c"
VERIFIER_C = "kernel/bpf/verifier.c"
ARM64_JIT = "arch/arm64/net/bpf_jit_comp.c"


# ---------------------------------------------------------------------------
# include/linux/btf_ids.h: the BTF_SET8 spelling, folded onto btf_id_set.
# ---------------------------------------------------------------------------

_BTF_IDS_OLD = """#endif /* CONFIG_DEBUG_INFO_BTF */

#ifdef CONFIG_NET
"""

_BTF_IDS_NEW = """#endif /* CONFIG_DEBUG_INFO_BTF */

/*
 * sailboat_btf_kfunc_id_set_api: ab21d6063c01 ("bpf: Introduce 8-byte BTF
 * set", v5.19) spells these macros with an 8-byte set type so that
 * BTF_ID_FLAGS can carry a kfunc's verifier flags next to its id.  This tree
 * has neither that type nor any KF_* flag, and the .BTF_ids section is
 * parsed out of the raw section by the resolve_btfids host tool, so both set
 * macros are the existing BTF_SET_START / BTF_SET_END and BTF_ID_FLAGS drops
 * its flag arguments: an id is registered, its flags are not.
 */
#define BTF_SET8_START(name)		BTF_SET_START(name)
#define BTF_SET8_END(name)		BTF_SET_END(name)
#define BTF_ID_FLAGS(prefix, name, ...)	BTF_ID(prefix, name)

#ifdef CONFIG_NET
"""


# ---------------------------------------------------------------------------
# include/linux/btf.h: the set type, the registration struct, the prototypes.
# ---------------------------------------------------------------------------

_BTF_H_API_OLD = """struct btf_show;

extern const struct file_operations btf_fops;
"""

_BTF_H_API_NEW = """struct btf_show;
/* sailboat_btf_kfunc_id_set_api: the kfunc allow-list registration API. */
struct btf_id_set;
struct module;

/*
 * One registered kfunc id set.  The member shape is a4703e318432 ("bpf:
 * Switch to new kfunc flags infrastructure", v5.19): a single .set pointer,
 * not dee872e124e8 (v5.18)'s four check/acquire/release/ret_null pointers,
 * because the file that registers these is the 6.6 kernel/sched/ext.c.  The
 * set it points at is this tree's struct btf_id_set -- see BTF_SET8_START/END
 * in linux/btf_ids.h for why it is not the upstream 8-byte set.
 */
struct btf_kfunc_id_set {
	struct module *owner;
	const struct btf_id_set *set;
};

extern const struct file_operations btf_fops;
"""

_BTF_H_PROTO_OLD = """struct btf *bpf_prog_get_target_btf(const struct bpf_prog *prog);
#else
"""

_BTF_H_PROTO_NEW = """struct btf *bpf_prog_get_target_btf(const struct bpf_prog *prog);
/* sailboat_btf_kfunc_id_set_api: register / look up kfunc id sets. */
int register_btf_kfunc_id_set(enum bpf_prog_type prog_type,
			      const struct btf_kfunc_id_set *kset);
bool btf_kfunc_id_set_contains(enum bpf_prog_type prog_type, u32 kfunc_btf_id);
#else
"""

_BTF_H_STUBS_OLD = """	return NULL;
}
#endif
"""

_BTF_H_STUBS_NEW = """	return NULL;
}
static inline int register_btf_kfunc_id_set(enum bpf_prog_type prog_type,
					    const struct btf_kfunc_id_set *kset)
{
	return 0;
}
static inline bool btf_kfunc_id_set_contains(enum bpf_prog_type prog_type,
					     u32 kfunc_btf_id)
{
	return false;
}
#endif
"""


# ---------------------------------------------------------------------------
# kernel/bpf/btf.c: the registry itself.
# ---------------------------------------------------------------------------

_BTF_C_OLD = """bool btf_id_set_contains(const struct btf_id_set *set, u32 id)
{
	return bsearch(&id, set->ids, set->cnt, sizeof(u32), btf_id_cmp_func) != NULL;
}

enum {
	BTF_MODULE_F_LIVE = (1 << 0),
"""

_BTF_C_NEW = """bool btf_id_set_contains(const struct btf_id_set *set, u32 id)
{
	return bsearch(&id, set->ids, set->cnt, sizeof(u32), btf_id_cmp_func) != NULL;
}

/*
 * sailboat_btf_kfunc_id_set_api: the kfunc allow-list registry.
 *
 * Whether a program type may call a kernel function is decided here as well as
 * by its own verifier ops: the 5.15 form is a hardcoded bool callback, and a
 * program type whose verifier ops carry none (BPF_PROG_TYPE_STRUCT_OPS -- see
 * the empty bpf_struct_ops_verifier_ops in kernel/bpf/bpf_struct_ops.c) can
 * call no kfunc at all until a set is registered for it.
 *
 * dee872e124e8 keeps the sets in struct btf, one table per BTF, and resolves
 * the owner to a BTF through the module registry.  This tree has neither that
 * table nor that lookup, and the only registrant is built into vmlinux, so the
 * table is global and keyed by program-type hook alone; a module-owned set is
 * refused outright rather than kept, because nothing here would pin the
 * registering module against an unload and its .BTF_ids memory would be freed
 * under the verifier.
 *
 * Known divergence from upstream, stated rather than implied: sets are not
 * scoped to the BTF that registered them, so an id registered for a hook is
 * reachable by every program of that type.  Every id this tree registers comes
 * from vmlinux, which is exactly the scope upstream gives a vmlinux set, and
 * registration is permanent there too (upstream has no unregister path).
 */
enum btf_kfunc_hook {
	BTF_KFUNC_HOOK_XDP,
	BTF_KFUNC_HOOK_TC,
	BTF_KFUNC_HOOK_STRUCT_OPS,
	BTF_KFUNC_HOOK_MAX,
};

#define BTF_KFUNC_SET_MAX	8

static const struct btf_id_set *btf_kfunc_sets[BTF_KFUNC_HOOK_MAX][BTF_KFUNC_SET_MAX];
static int btf_kfunc_set_cnt[BTF_KFUNC_HOOK_MAX];
static DEFINE_SPINLOCK(btf_kfunc_sets_lock);

static int bpf_prog_type_to_kfunc_hook(enum bpf_prog_type prog_type)
{
	switch (prog_type) {
	case BPF_PROG_TYPE_XDP:
		return BTF_KFUNC_HOOK_XDP;
	case BPF_PROG_TYPE_SCHED_CLS:
		return BTF_KFUNC_HOOK_TC;
	case BPF_PROG_TYPE_STRUCT_OPS:
		return BTF_KFUNC_HOOK_STRUCT_OPS;
	default:
		return BTF_KFUNC_HOOK_MAX;
	}
}

/*
 * This function must be invoked only from initcalls/module init functions, as
 * upstream: the table above is sized for the handful of sets built-in code
 * registers before userspace exists, and every lookup that races it happens
 * after boot.  A caller that registered later would have to re-derive the
 * bound and the locking.
 */
int register_btf_kfunc_id_set(enum bpf_prog_type prog_type,
			      const struct btf_kfunc_id_set *kset)
{
	unsigned long flags;
	int hook;

	if (!kset || !kset->set || !kset->set->cnt)
		return 0;

	if (kset->owner) {
		pr_err("sailboat_btf_kfunc_id_set_api: module-owned id sets are unsupported");
		return -EOPNOTSUPP;
	}

	hook = bpf_prog_type_to_kfunc_hook(prog_type);
	if (hook >= BTF_KFUNC_HOOK_MAX) {
		pr_err("sailboat_btf_kfunc_id_set_api: no kfunc hook for program type %d",
		       prog_type);
		return -EINVAL;
	}

	spin_lock_irqsave(&btf_kfunc_sets_lock, flags);
	if (btf_kfunc_set_cnt[hook] >= BTF_KFUNC_SET_MAX) {
		spin_unlock_irqrestore(&btf_kfunc_sets_lock, flags);
		pr_err("sailboat_btf_kfunc_id_set_api: too many id sets for kfunc hook %d",
		       hook);
		return -E2BIG;
	}
	btf_kfunc_sets[hook][btf_kfunc_set_cnt[hook]++] = kset->set;
	spin_unlock_irqrestore(&btf_kfunc_sets_lock, flags);

	return 0;
}

bool btf_kfunc_id_set_contains(enum bpf_prog_type prog_type, u32 kfunc_btf_id)
{
	unsigned long flags;
	bool found = false;
	int hook, i;

	hook = bpf_prog_type_to_kfunc_hook(prog_type);
	if (hook >= BTF_KFUNC_HOOK_MAX)
		return false;

	spin_lock_irqsave(&btf_kfunc_sets_lock, flags);
	for (i = 0; i < btf_kfunc_set_cnt[hook]; i++) {
		if (btf_id_set_contains(btf_kfunc_sets[hook][i], kfunc_btf_id)) {
			found = true;
			break;
		}
	}
	spin_unlock_irqrestore(&btf_kfunc_sets_lock, flags);

	return found;
}

enum {
	BTF_MODULE_F_LIVE = (1 << 0),
"""


# ---------------------------------------------------------------------------
# kernel/bpf/verifier.c: the gate.
# ---------------------------------------------------------------------------

_VERIFIER_OLD = """	if (!env->ops->check_kfunc_call ||
	    !env->ops->check_kfunc_call(func_id)) {
"""

_VERIFIER_NEW = """	/* sailboat_struct_ops_kfunc_allow: a kfunc is allowed if the program
	 * type's own verifier ops allow it (the 5.15 form) or if an id set was
	 * registered for it.  The second half is the only way a
	 * BPF_PROG_TYPE_STRUCT_OPS program can call a kfunc at all --
	 * bpf_struct_ops_verifier_ops carries no check_kfunc_call -- and it is
	 * additive on purpose: b202d8442222 (the removal of the callback and of
	 * the old kfunc BTF ID API) is not carried, so the program types that
	 * have a callback keep it.
	 */
	if ((!env->ops->check_kfunc_call ||
	     !env->ops->check_kfunc_call(func_id)) &&
	    !btf_kfunc_id_set_contains(resolve_prog_type(env->prog), func_id)) {
"""


# ---------------------------------------------------------------------------
# arch/arm64/net/bpf_jit_comp.c: the arch override.
# ---------------------------------------------------------------------------

_ARM64_OLD = """u64 bpf_jit_alloc_exec_limit(void)
{
	return VMALLOC_END - VMALLOC_START;
}
"""

_ARM64_NEW = """/* sailboat_arm64_jit_kfunc_call: b5e975d256db ("bpf, arm64: Enable kfunc
 * call") -- the arch override of kernel/bpf/core.c's __weak false default,
 * without which add_kfunc_call() refuses every kfunc call on arm64.  Nothing
 * else is needed: the JIT's BPF_CALL case resolves the target through
 * bpf_jit_get_func_addr(), which returns __bpf_call_base + imm for any
 * src_reg other than BPF_PSEUDO_CALL, and add_kfunc_call() stores exactly that
 * before fixup_kfunc_call() writes it into the instruction.  The s32 imm is
 * wide enough because arm64 keeps its text within 2 GB of the kernel region
 * (b2eed9b58811), which is the property the upstream commit cites.
 */
bool bpf_jit_supports_kfunc_call(void)
{
	return true;
}

u64 bpf_jit_alloc_exec_limit(void)
{
	return VMALLOC_END - VMALLOC_START;
}
"""


def _btf_kfunc_id_set_api_apply(ctx):
    """The kfunc allow-list registration API and its registry."""
    status, _results, detail = apply_steps(ctx, [
        (BTF_IDS_H, _BTF_IDS_OLD, _BTF_IDS_NEW, T),
        (BTF_H, _BTF_H_API_OLD, _BTF_H_API_NEW, T),
        (BTF_H, _BTF_H_PROTO_OLD, _BTF_H_PROTO_NEW, T),
        (BTF_H, _BTF_H_STUBS_OLD, _BTF_H_STUBS_NEW, T),
        (BTF_C, _BTF_C_OLD, _BTF_C_NEW, T),
    ])
    return status, detail


def _struct_ops_kfunc_allow_apply(ctx):
    """The verifier gate: a registered id set, or the program type's callback."""
    status, _results, detail = apply_steps(ctx, [
        (VERIFIER_C, _VERIFIER_OLD, _VERIFIER_NEW, T),
    ])
    return status, detail


def _arm64_jit_kfunc_call_apply(ctx):
    """Let the arm64 JIT call a kernel function."""
    status, _results, detail = apply_steps(ctx, [
        (ARM64_JIT, _ARM64_OLD, _ARM64_NEW, T),
    ])
    return status, detail


def build_groups(PatchGroup):
    """Return the three PatchGroup records, in registration order."""
    return [
        PatchGroup(
            "btf_kfunc_id_set_api",
            "the kfunc allow-list registration API: BTF_SET8_START/END + "
            "BTF_ID_FLAGS folded onto this tree's struct btf_id_set, the "
            "btf_kfunc_id_set registration struct, and the per-program-type "
            "id-set registry register_btf_kfunc_id_set() fills and "
            "btf_kfunc_id_set_contains() reads (dee872e124e8, mainline v5.18; "
            "member shape from a4703e318432, macro spelling from "
            "ab21d6063c01, both v5.19).  The kfunc flags ab21d6063c01 carries "
            "in struct btf_id_set8 are deliberately NOT represented: they "
            "would need the .BTF_ids section layout to change, which the "
            "resolve_btfids host tool parses, and this tree has no KF_* flag "
            "and no btf_kfunc_meta() to consume one.  Ids are registered, "
            "flags are not -- so reference pairing for KF_ACQUIRE/KF_RELEASE "
            "and KF_TRUSTED_ARGS argument trust are not enforced.  Module "
            "owned sets are refused instead of kept without module-BTF "
            "bookkeeping.  Nothing consumes this API until S2 overlays "
            "kernel/sched/ext.c.",
            [
                "dee872e124e8 (mainline v5.18, the registry and the hook)",
                "a4703e318432 (mainline v5.19, the single .set member shape)",
                "ab21d6063c01 (mainline v5.19, BTF_SET8/BTF_ID_FLAGS; the "
                "flag half is not carried)",
            ],
            [BTF_IDS_H, BTF_H, BTF_C],
            _btf_kfunc_id_set_api_apply,
        ),
        PatchGroup(
            "struct_ops_kfunc_allow",
            "the verifier gate in check_kfunc_call() becomes 'the program "
            "type's own ops callback allows it, OR an id set registered for "
            "that program type contains it'.  On this tree "
            "bpf_struct_ops_verifier_ops is an empty struct, so "
            "env->ops->check_kfunc_call is NULL for BPF_PROG_TYPE_STRUCT_OPS "
            "and no kfunc is callable from a struct_ops program at all -- "
            "which is exactly what sched_ext needs.  Additive by design: "
            "b202d8442222 (the removal of check_kfunc_call and of the old "
            "kfunc BTF ID API) is not carried, so the program types that have "
            "a callback -- tcp_ca, test_run -- keep working through it.",
            [
                "b202d8442222 (mainline v5.18; only the additive half of the "
                "series is carried, the callback removal is not)",
            ],
            [VERIFIER_C],
            _struct_ops_kfunc_allow_apply,
        ),
        PatchGroup(
            "arm64_jit_kfunc_call",
            "bpf_jit_supports_kfunc_call() returning true on arm64.  "
            "kernel/bpf/core.c's __weak default returns false, and "
            "add_kfunc_call() gates on it, so without this override every "
            "kfunc call on arm64 is refused with 'JIT does not support "
            "calling kernel function' regardless of the allow-list.  The "
            "JIT's BPF_CALL path already encodes an absolute target through "
            "bpf_jit_get_func_addr(), and add_kfunc_call() already stores the "
            "address relative to __bpf_call_base, so the override is the "
            "whole of the arm64 side (b5e975d256db).",
            ["b5e975d256db (mainline v5.19, carried verbatim)"],
            [ARM64_JIT],
            _arm64_jit_kfunc_call_apply,
        ),
    ]
