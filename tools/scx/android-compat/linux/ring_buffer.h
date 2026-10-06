/* SPDX-License-Identifier: GPL-2.0 */
/*
 * The perf ring-buffer accessors libbpf uses (ring_buffer_read_head /
 * ring_buffer_write_tail), for the Android loader build.
 *
 * The kernel's tools/include version reaches for asm/barrier.h and the kernel's
 * READ_ONCE/smp_* helpers.  The pairing its own comment describes is an acquire
 * load of ->data_head and a release store of ->data_tail, which is exactly what
 * this spells with the C11 atomics clang lowers to ldar/stlr on aarch64.
 */
#ifndef __ABK_ANDROID_LINUX_RING_BUFFER_H
#define __ABK_ANDROID_LINUX_RING_BUFFER_H

#include <linux/perf_event.h>
#include <linux/types.h>

static inline __u64 ring_buffer_read_head(struct perf_event_mmap_page *base)
{
	return __atomic_load_n(&base->data_head, __ATOMIC_ACQUIRE);
}

static inline void ring_buffer_write_tail(struct perf_event_mmap_page *base,
					  __u64 tail)
{
	__atomic_store_n(&base->data_tail, tail, __ATOMIC_RELEASE);
}

#endif /* __ABK_ANDROID_LINUX_RING_BUFFER_H */
