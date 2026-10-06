/* SPDX-License-Identifier: LGPL-2.1 OR BSD-2-Clause */
/*
 * Minimal read-only libelf for the sched_ext loader's Android build.
 *
 * Why this exists: libbpf needs libelf, and the Android NDK ships none, so the
 * loader cannot be cross-compiled for aarch64-linux-android without either
 * building elfutils for the triplet or providing the slice of the API libbpf
 * actually uses.  This is that slice -- the seventeen functions named below --
 * implemented over <elf.h> on an in-memory image.  It is NOT a general libelf:
 *
 *   * read-only.  The write-side entry points exist only so the linker has
 *     symbols (libbpf references a few from dead code); they fail.
 *   * ELF64 little-endian only.  That is what clang -target bpf emits and what
 *     this module ships; anything else is refused with a message rather than
 *     mis-parsed.
 *   * one data chunk per section, which is what the kernel's ELF format has.
 *
 * The build script compiles libbpf against these headers (so the ABI of
 * Elf_Data/Elf_Scn is defined here, not inherited from the host's libelf),
 * skips libbpf's linker.c -- its static ELF writer (bpf_linker__*), the only
 * file that needs the write side and the one the loader never calls -- and
 * links elf_shim.o.  The host then runs the same "selftest" as the real-libelf
 * build and the two outputs are compared.  See tools/scx/README.md.
 */
#ifndef __ABK_LIBELF_SHIM_H
#define __ABK_LIBELF_SHIM_H

#include <elf.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>	/* off_t, for the elf_update stub */

#ifdef __cplusplus
extern "C" {
#endif

typedef struct elf_shim Elf;
typedef struct elf_shim_scn Elf_Scn;

/* libelf's Elf_Type, only the member libbpf reads. */
typedef enum {
	ELF_T_BYTE = 0,
	ELF_T_SYM = 15,
	ELF_T_REL = 16,
	ELF_T_RELA = 17,
} Elf_Type;

/* Layout follows libelf's Elf_Data field for field: libbpf dereferences it. */
typedef struct {
	void *d_buf;
	Elf_Type d_type;
	unsigned int d_version;
	size_t d_size;
	int64_t d_off;
	size_t d_align;
} Elf_Data;

typedef enum {
	ELF_C_NULL = 0,
	ELF_C_READ = 1,
	ELF_C_RDWR = 2,
	ELF_C_WRITE = 3,
	ELF_C_CLR = 4,
	ELF_C_SET = 5,
	ELF_C_FDDONE = 6,
	ELF_C_FDREAD = 7,
	ELF_C_READ_MMAP = 8,
} Elf_Cmd;

#define EV_CURRENT	1
#define ELF_K_NONE	0
#define ELF_K_ELF	1

int elf_version(unsigned int version);
Elf *elf_memory(char *image, size_t size);
Elf *elf_begin(int fd, Elf_Cmd cmd, Elf *ref);
int elf_end(Elf *elf);
const char *elf_errmsg(int error);
int elf_kind(Elf *elf);

Elf_Scn *elf_getscn(Elf *elf, size_t index);
Elf_Scn *elf_nextscn(Elf *elf, Elf_Scn *scn);
size_t elf_ndxscn(Elf_Scn *scn);
Elf_Data *elf_getdata(Elf_Scn *scn, Elf_Data *data);
Elf_Data *elf_rawdata(Elf_Scn *scn, Elf_Data *data);
char *elf_strptr(Elf *elf, size_t section, size_t offset);
int elf_getshdrstrndx(Elf *elf, size_t *dst);

/* Write side: present for the linker, always failing. */
Elf_Scn *elf_newscn(Elf *elf);
Elf_Data *elf_newdata(Elf_Scn *scn);
off_t elf_update(Elf *elf, Elf_Cmd cmd);

#ifdef __cplusplus
}
#endif
#endif /* __ABK_LIBELF_SHIM_H */
