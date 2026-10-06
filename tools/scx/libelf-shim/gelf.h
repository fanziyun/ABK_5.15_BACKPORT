/* SPDX-License-Identifier: LGPL-2.1 OR BSD-2-Clause */
/* The 64-bit view of the ELF structures, as libbpf uses them. */
#ifndef __ABK_GELF_SHIM_H
#define __ABK_GELF_SHIM_H

#include "libelf.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef Elf64_Ehdr GElf_Ehdr;
typedef Elf64_Shdr GElf_Shdr;
typedef Elf64_Sym GElf_Sym;
typedef Elf64_Rel GElf_Rel;
typedef Elf64_Rela GElf_Rela;

typedef Elf64_Addr GElf_Addr;
typedef Elf64_Off GElf_Off;
typedef Elf64_Half GElf_Half;
typedef Elf64_Word GElf_Word;
typedef Elf64_Sword GElf_Sword;
typedef Elf64_Xword GElf_Xword;
typedef Elf64_Sxword GElf_Sxword;

/* The 64-bit accessors libbpf uses for symbol and relocation fields. */
#define GELF_ST_BIND(val)	ELF64_ST_BIND(val)
#define GELF_ST_TYPE(val)	ELF64_ST_TYPE(val)
#define GELF_ST_INFO(bind, type)	ELF64_ST_INFO(bind, type)
#define GELF_R_SYM(val)		ELF64_R_SYM(val)
#define GELF_R_TYPE(val)	ELF64_R_TYPE(val)
#define GELF_R_INFO(sym, type)	ELF64_R_INFO(sym, type)

int gelf_getclass(Elf *elf);
GElf_Ehdr *gelf_getehdr(Elf *elf, GElf_Ehdr *dst);
GElf_Shdr *gelf_getshdr(Elf_Scn *scn, GElf_Shdr *dst);
GElf_Sym *gelf_getsym(Elf_Data *data, int ndx, GElf_Sym *dst);
GElf_Rel *gelf_getrel(Elf_Data *data, int ndx, GElf_Rel *dst);
GElf_Rela *gelf_getrela(Elf_Data *data, int ndx, GElf_Rela *dst);

#ifdef __cplusplus
}
#endif
#endif /* __ABK_GELF_SHIM_H */
