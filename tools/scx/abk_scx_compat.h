/* SPDX-License-Identifier: GPL-2.0 */
/*
 * BPF-side declarations for the minimal SCX scheduler.
 *
 * The scheduler is compiled with clang -target bpf against a vmlinux.h dumped
 * from the *grafted* kernel's BTF, so struct sched_ext_ops and struct
 * scx_exit_info come from the target kernel itself.  Two things a vmlinux.h
 * cannot carry are spelled here:
 *
 *  - the kfunc prototypes.  Each is an extern in the .ksyms section, which is
 *    what libbpf's kfunc resolution looks up in the kernel's BTF.  Only kfuncs
 *    the landed payload registers may appear; tests/stable_5_15_test.py checks
 *    this file's extern set against the BTF_ID_FLAGS(func, ...) set in
 *    files/kernel/sched/ext.c so a scheduler cannot be built against a kfunc
 *    this kernel does not have.
 *
 *  - the constants that are #defines in include/linux/sched/ext.h rather than
 *    enum values, so BTF does not emit them (SCX_SLICE_DFL).  The value is
 *    pinned against the payload header by the same unit test; it is copied
 *    here, not invented.
 *
 * The DSQ id is this scheduler's own (a user DSQ), not one of the builtin
 * ids -- the builtin range is above SCX_DSQ_FLAG_BUILTIN and is never a valid
 * argument to scx_bpf_create_dsq().
 */
#ifndef __ABK_SCX_COMPAT_H
#define __ABK_SCX_COMPAT_H

/* include/linux/sched/ext.h: SCX_SLICE_DFL = 20 * NSEC_PER_MSEC */
#define ABK_SCX_SLICE_DFL	(20ULL * 1000ULL * 1000ULL)

/* This scheduler's own DSQ.  User DSQ ids start at 1. */
#define ABK_SCX_DSQ_ID		1

/* include/linux/sched/ext.h declares these; the payload defines and registers
 * every one of them (files/kernel/sched/ext.c, BTF_ID_FLAGS(func, ...)). */
extern void scx_bpf_dispatch(struct task_struct *p, u64 dsq_id, u64 slice,
			     u64 enq_flags) __ksym;
extern s32 scx_bpf_create_dsq(u64 dsq_id, s32 node) __ksym;
extern void scx_bpf_destroy_dsq(u64 dsq_id) __ksym;
extern bool scx_bpf_consume(u64 dsq_id) __ksym;

#endif /* __ABK_SCX_COMPAT_H */
