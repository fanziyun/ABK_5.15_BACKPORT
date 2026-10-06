/* SPDX-License-Identifier: GPL-2.0 */
/*
 * The kernel's linux/err.h, for the Android loader build.
 *
 * libbpf includes <linux/err.h> and uses exactly the ERR_PTR family.  The
 * kernel's tools/include/linux/err.h cannot be used here: it is written against
 * the kernel's own macros (__force, __must_check) which come from the kernel's
 * compiler_types.h, and pulling that header into a bionic compile collides with
 * the NDK's own linux headers (bionic's sys/types.h includes
 * <linux/posix_types.h>, which the NDK provides and the kernel tree does not).
 * So the four macros libbpf needs are spelled here instead.
 */
#ifndef __ABK_ANDROID_LINUX_ERR_H
#define __ABK_ANDROID_LINUX_ERR_H

#include <stdbool.h>

#define MAX_ERRNO	4095

#define IS_ERR_VALUE(x) ((unsigned long)(void *)(x) >= (unsigned long)-MAX_ERRNO)

static inline void *ERR_PTR(long error)
{
	return (void *)error;
}

static inline long PTR_ERR(const void *ptr)
{
	return (long)ptr;
}

static inline bool IS_ERR(const void *ptr)
{
	return IS_ERR_VALUE((unsigned long)ptr);
}

static inline bool IS_ERR_OR_NULL(const void *ptr)
{
	return !ptr || IS_ERR(ptr);
}

static inline void *ERR_CAST(const void *ptr)
{
	return (void *)ptr;
}

static inline int PTR_ERR_OR_ZERO(const void *ptr)
{
	if (IS_ERR(ptr))
		return (int)PTR_ERR(ptr);
	return 0;
}

#endif /* __ABK_ANDROID_LINUX_ERR_H */
