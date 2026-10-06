/* SPDX-License-Identifier: GPL-2.0 */
/*
 * The handful of kernel macros libbpf uses, for the Android loader build.
 *
 * libbpf is written to compile with the kernel's tools/include headers, which
 * bring a whole macro world (and linux/compiler_types.h) that does not mix with
 * bionic's headers.  The measured intersection is six macros: the build
 * force-includes this file (-include) so each one is present before any NDK
 * header is reached, and every definition is guarded so a real one (NDK or
 * kernel) still wins.
 */
#ifndef __ABK_KERNEL_MACROS_H
#define __ABK_KERNEL_MACROS_H

#include <stddef.h>

#ifndef ARRAY_SIZE
#define ARRAY_SIZE(arr) (sizeof(arr) / sizeof((arr)[0]))
#endif

#ifndef container_of
#define container_of(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))
#endif

#ifndef likely
#define likely(x)	__builtin_expect(!!(x), 1)
#endif

#ifndef unlikely
#define unlikely(x)	__builtin_expect(!!(x), 0)
#endif

#ifndef __printf
#define __printf(a, b)	__attribute__((format(printf, a, b)))
#endif

#ifndef __always_inline
#define __always_inline	inline __attribute__((always_inline))
#endif

#ifndef READ_ONCE
#define READ_ONCE(x)	(*(volatile __typeof__(x) *)&(x))
#endif

#ifndef WRITE_ONCE
#define WRITE_ONCE(x, val) (*(volatile __typeof__(x) *)&(x) = (val))
#endif

/* The kernel's poll-event type; bionic's linux/eventpoll.h still spells the
 * EPOLL* constants with it. */
typedef unsigned int __poll_t;

/* bionic gets in_addr_t from its own <linux/in.h>, which this build shadows
 * with the kernel's uapi copy (that one has struct in_addr but not the
 * typedef).  Nothing else in this include set defines it, so it is spelled
 * here. */
#include <stdint.h>
typedef uint32_t in_addr_t;

#ifndef roundup
#define roundup(x, y) ((((x) + ((y) - 1)) / (y)) * (y))
#endif

#ifndef rounddown
#define rounddown(x, y) (((x) / (y)) * (y))
#endif

#ifndef min
#define min(x, y) ({ __typeof__(x) _min1 = (x); __typeof__(y) _min2 = (y); \
		_min1 < _min2 ? _min1 : _min2; })
#endif

#ifndef max
#define max(x, y) ({ __typeof__(x) _max1 = (x); __typeof__(y) _max2 = (y); \
		_max1 > _max2 ? _max1 : _max2; })
#endif

#endif /* __ABK_KERNEL_MACROS_H */
