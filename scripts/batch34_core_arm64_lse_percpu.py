"""Batch 34: non-return per-CPU atomics switch from store to load LSE atomics.

Source: mainline ``535fdfc5a228`` (v6.18, Catalin Marinas; merged into
arm64-fixes by Will Deacon, 2025-11-11; Reported-by / Tested-by Paul E.
McKenney, Reviewed-by Palmer Dabbelt).  The commit carries no benchmark
numbers of its own -- it fixes a pathological per-CPU atomic behaviour Paul
E. McKenney found on the SRCU lock path, discussed at
https://lore.kernel.org/r/e7d539ed-ced0-4b96-8ecd-048a5b803b85@paulmck-laptop

Mechanism.  Under FEAT_LSE the non-return ``this_cpu_*()`` atomics compile to
STADD/STCLR/STSET, which on many microarchitectures execute "far" (in the
interconnect or memory subsystem) unless the line already sits in L1.  Load
atomics -- LDADD/LDCLR/LDSET with a destination register that is unused but
*not* XZR -- execute "near" (L1).  Back-to-back STADD, as in
``srcu_read_{lock,unlock}*()``, additionally pays the default posting
behaviour.  The patch therefore rewrites the LSE branch of
``__PERCPU_OP_CASE()`` to give the atomic a ``[tmp]`` destination and flips
the three ``PERCPU_OP()`` instantiations from stadd/stclr/stset to
ldadd/ldclr/ldset.  ``PERCPU_RET_OP(add, add, ldadd)`` is already a load
atomic and stays untouched, and the non-LSE ``stxr``/``ldxr`` fallback in the
same macro is byte-identical before and after.

5.15 shape: byte-identical to the upstream ``old`` form on every baseline
this module tracks -- verified on the 5.15.194 reference tree; the macro
family (``__PERCPU_OP_CASE`` / ``PERCPU_OP`` / ``PERCPU_RET_OP``) and the
``ARM64_LSE_ATOMIC_INSN`` alternative are all present unchanged since long
before 5.15.  Both steps are verbatim upstream hunks, so this is an
upstream-shape rewrite: no ABK marker, the target form doubles as the
idempotency probe, and a future baseline that carries ``535fdfc5a228``
itself reports ``already_present`` byte-identically (Batch 31 precedent).

Counter-evidence that must travel with this graft.  The change later caused a
measured regression: the bpf-next series "bpf: Optimize recursion detection
on arm64" (merge ``c2f2f005a1c2``, 2025-12-21) states in its cover letter
that ``535fdfc5a228`` "seems to have caused a regression on the fentry
benchmark" -- on Neoverse-V2 (KVM, 8 CPU), ``bench trig-fentry`` measures
51.770 M/s with the commit reverted versus 43.271 M/s on bpf-next/master
with it in -- and that enabling it on x86-64 regresses 30%, so the BPF fix
was made arm64-only.  That series rewrote BPF's recursion detection to be
non-atomic to win the throughput back.

Why that regression does not apply here, checked before grafting: 5.15's
``kernel/bpf/trampoline.c`` does carry the recursion-detection path
(``__this_cpu_inc_return(*(prog->active))`` -- a *return* op, already LDADD
via ``PERCPU_RET_OP``; ``__this_cpu_dec(*(prog->active))`` -- the non-return
op this group flips from STADD to LDADD).  But arm64 in 5.15 has no BPF
trampoline support at all: ``arch/arm64/net/bpf_jit_comp.c`` contains no
trampoline code and arm64's Kconfig lacks the trampoline capability selects
(both arrived upstream later than 5.15), so ``__bpf_prog_enter*`` /
``__bpf_prog_exit*`` are compiled but never called on an arm64 5.15 build --
the fentry-benchmark regression surface does not exist on this baseline.
The tradeoff (BPF fentry throughput vs SRCU lock latency) and this
disposition are recorded in ``CHANGELOG.md`` (Batch 34).

Evidence strength.  Every number above -- Paul's SRCU finding and the BPF
regression alike -- was measured on Neoverse V2 / ARM server cores.  This
module's target devices are Qualcomm Snapdragon; whether the "far" vs "near"
execution split behaves the same there is unverified.  This group therefore
claims NO speedup (Batch 28 precedent: no device A/B, no claim).  It records
what changed, where the upstream evidence came from, and that transferability
is unknown.

KMI: pure header asm-macro rewrite -- no struct layout, no exported symbol,
no KABI slot is touched.  Trap 6 applies with full force (a widely included
header with inline asm; no text audit can see whether the expansion still
compiles), so the ABK CI arm64 build across all four baselines is the only
real gate here -- the four tree-level audits are necessary but not
sufficient for this batch.

Graft boundary: no other group writes ``arch/arm64/include/asm/percpu.h``,
and nothing in this module reads it, so no shape probe and no ordering
constraint are needed.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

PERCPU_H = "arch/arm64/include/asm/percpu.h"

T = True

# ---------------------------------------------------------------------------
# Step 1: __PERCPU_OP_CASE()'s LSE branch gains a [tmp] destination register,
# turning the store form (STADD/STCLR/STSET) into the load form
# (LDADD/LDCLR/LDSET).  The LL/SC branch and the output operands are context,
# not payload: [tmp] already exists there as "=&r".
# ---------------------------------------------------------------------------

_LSE_INSN_OLD = (
    '\t\t#op_lse "\\t%" #w "[val], %[ptr]\\n"\t\t\t\\\n'
    "\t\t__nops(3))\t\t\t\t\t\t\\\n"
)

_LSE_INSN_NEW = (
    '\t\t#op_lse "\\t%" #w "[val], %" #w "[tmp], %[ptr]\\n"\t\\\n'
    "\t\t__nops(3))\t\t\t\t\t\t\\\n"
)

# ---------------------------------------------------------------------------
# Step 2: instantiate the three non-return ops as load atomics, carrying
# upstream's own comment and the lore link verbatim.  The RET_OP line below is
# trailing context -- it is already ldadd and must not be edited.
# ---------------------------------------------------------------------------

_OPS_OLD = (
    "PERCPU_OP(add, add, stadd)\n"
    "PERCPU_OP(andnot, bic, stclr)\n"
    "PERCPU_OP(or, orr, stset)\n"
    "PERCPU_RET_OP(add, add, ldadd)\n"
)

_OPS_NEW = (
    "/*\n"
    " * Use value-returning atomics for CPU-local ops as they are more likely\n"
    ' * to execute "near" to the CPU (e.g. in L1$).\n'
    " *\n"
    " * https://lore.kernel.org/r/e7d539ed-ced0-4b96-8ecd-048a5b803b85@paulmck-laptop\n"
    " */\n"
    "PERCPU_OP(add, add, ldadd)\n"
    "PERCPU_OP(andnot, bic, ldclr)\n"
    "PERCPU_OP(or, orr, ldset)\n"
    "PERCPU_RET_OP(add, add, ldadd)\n"
)


def build_steps():
    """Two required steps, both verbatim upstream hunks.

    All required: a tree that flipped only some of the three ops (or only the
    macro, not the instantiations) would compile fine and quietly keep one
    store-form op -- exactly the silent half-graft a required chain refuses
    to write.
    """
    return [
        (PERCPU_H, _LSE_INSN_OLD, _LSE_INSN_NEW, T),
        (PERCPU_H, _OPS_OLD, _OPS_NEW, T),
    ]


def _arm64_lse_percpu_load_atomics_apply(ctx):
    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the single Batch-34 PatchGroup record."""
    return [
        PatchGroup(
            "arm64_lse_percpu_load_atomics",
            "arm64: use load LSE atomics (LDADD/LDCLR/LDSET) for the "
            "non-return per-CPU operations so they execute near (L1) instead "
            "of far in the interconnect -- KMI-neutral header asm rewrite; "
            "upstream evidence is Neoverse-V2-only, no speedup claimed",
            [
                "535fdfc5a228 (v6.18, arm64-fixes; lore "
                "e7d539ed-ced0-4b96-8ecd-048a5b803b85@paulmck-laptop)",
            ],
            [PERCPU_H],
            _arm64_lse_percpu_load_atomics_apply,
        ),
    ]
