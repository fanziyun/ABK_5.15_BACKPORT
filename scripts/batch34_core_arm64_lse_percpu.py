from __future__ import annotations

from abk_backport_engine import apply_steps

PERCPU_H = "arch/arm64/include/asm/percpu.h"

T = True


_LSE_INSN_OLD = (
    '\t\t#op_lse "\\t%" #w "[val], %[ptr]\\n"\t\t\t\\\n'
    "\t\t__nops(3))\t\t\t\t\t\t\\\n"
)

_LSE_INSN_NEW = (
    '\t\t#op_lse "\\t%" #w "[val], %" #w "[tmp], %[ptr]\\n"\t\\\n'
    "\t\t__nops(3))\t\t\t\t\t\t\\\n"
)


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
