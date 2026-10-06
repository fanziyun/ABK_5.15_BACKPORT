// SPDX-License-Identifier: LGPL-2.1 OR BSD-2-Clause
/*
 * Read-only libelf for the sched_ext loader's Android build -- implementation.
 * See libelf.h for the scope contract; the short version is: seventeen
 * functions, ELF64 little-endian, one data chunk per section, write side stubs.
 *
 * The parsing is deliberately strict: a malformed or unexpected object makes
 * the shim return NULL/-1 with a message rather than decode into garbage, since
 * the only consumer is libbpf and the only input is the .bpf.o this repository
 * builds.
 */
#include "libelf.h"
#include "gelf.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

struct elf_shim_scn {
	struct elf_shim *elf;
	size_t idx;
	Elf_Data data;
};

struct elf_shim {
	unsigned char *owned;	/* elf_begin's copy; NULL for elf_memory */
	const unsigned char *buf;
	size_t size;
	int cls;
	Elf64_Ehdr eh;
	Elf64_Shdr *shdrs;
	struct elf_shim_scn *scns;
	size_t shnum;
};

static char shim_errbuf[192] = "no error";

static void shim_fail(const char *msg, const char *detail)
{
	if (detail)
		snprintf(shim_errbuf, sizeof(shim_errbuf), "%s: %s", msg, detail);
	else
		snprintf(shim_errbuf, sizeof(shim_errbuf), "%s", msg);
}

const char *elf_errmsg(int error)
{
	(void)error;
	return shim_errbuf;
}

int elf_version(unsigned int version)
{
	return version == EV_CURRENT ? EV_CURRENT : 0;
}

int elf_kind(Elf *elf)
{
	return elf ? ELF_K_ELF : ELF_K_NONE;
}

/* Parse the image into headers + per-section handles.  Returns 0 or -1. */
static int shim_parse(Elf *elf)
{
	size_t i;

	if (elf->size < sizeof(Elf64_Ehdr)) {
		shim_fail("truncated ELF header", NULL);
		return -1;
	}
	if (memcmp(elf->buf, ELFMAG, SELFMAG) != 0) {
		shim_fail("not an ELF object", NULL);
		return -1;
	}
	if (elf->buf[EI_CLASS] != ELFCLASS64) {
		shim_fail("only ELF64 objects are supported", NULL);
		return -1;
	}
	if (elf->buf[EI_DATA] != ELFDATA2LSB) {
		shim_fail("only little-endian objects are supported", NULL);
		return -1;
	}
	elf->cls = ELFCLASS64;
	memcpy(&elf->eh, elf->buf, sizeof(elf->eh));

	if (elf->eh.e_shnum == 0 || elf->eh.e_shoff == 0) {
		shim_fail("object has no section headers", NULL);
		return -1;
	}
	if (elf->eh.e_shentsize != sizeof(Elf64_Shdr)) {
		shim_fail("unexpected section header size", NULL);
		return -1;
	}
	if (elf->eh.e_shoff + (size_t)elf->eh.e_shnum * sizeof(Elf64_Shdr)
	    > elf->size) {
		shim_fail("section headers run past the end of the object", NULL);
		return -1;
	}

	elf->shnum = elf->eh.e_shnum;
	elf->shdrs = calloc(elf->shnum, sizeof(*elf->shdrs));
	elf->scns = calloc(elf->shnum, sizeof(*elf->scns));
	if (!elf->shdrs || !elf->scns) {
		shim_fail("out of memory", NULL);
		return -1;
	}
	memcpy(elf->shdrs, elf->buf + elf->eh.e_shoff,
	       elf->shnum * sizeof(*elf->shdrs));

	for (i = 0; i < elf->shnum; i++) {
		Elf64_Shdr *sh = &elf->shdrs[i];
		struct elf_shim_scn *scn = &elf->scns[i];

		scn->elf = elf;
		scn->idx = i;
		scn->data.d_version = EV_CURRENT;
		scn->data.d_type = ELF_T_BYTE;
		scn->data.d_off = 0;
		scn->data.d_align = 8;
		scn->data.d_size = sh->sh_size;
		if (sh->sh_type == SHT_NOBITS) {
			scn->data.d_buf = NULL;
			continue;
		}
		if (sh->sh_offset + sh->sh_size > elf->size) {
			shim_fail("a section runs past the end of the object", NULL);
			return -1;
		}
		scn->data.d_buf = (void *)(elf->buf + sh->sh_offset);
	}
	return 0;
}

static void shim_free(Elf *elf)
{
	if (!elf)
		return;
	free(elf->owned);
	free(elf->shdrs);
	free(elf->scns);
	free(elf);
}

Elf *elf_memory(char *image, size_t size)
{
	Elf *elf;

	if (!image || size == 0) {
		shim_fail("empty image", NULL);
		return NULL;
	}
	elf = calloc(1, sizeof(*elf));
	if (!elf) {
		shim_fail("out of memory", NULL);
		return NULL;
	}
	elf->buf = (const unsigned char *)image;
	elf->size = size;
	if (shim_parse(elf)) {
		shim_free(elf);
		return NULL;
	}
	return elf;
}

Elf *elf_begin(int fd, Elf_Cmd cmd, Elf *ref)
{
	unsigned char *buf = NULL;
	size_t size = 0, got = 0;
	Elf *elf;

	(void)cmd;
	(void)ref;

	if (lseek(fd, 0, SEEK_END) < 0) {
		shim_fail("cannot size the object", strerror(errno));
		return NULL;
	}
	size = (size_t)lseek(fd, 0, SEEK_CUR);
	if (size == 0 || lseek(fd, 0, SEEK_SET) < 0) {
		shim_fail("empty object", NULL);
		return NULL;
	}

	buf = malloc(size);
	if (!buf) {
		shim_fail("out of memory", NULL);
		return NULL;
	}
	while (got < size) {
		ssize_t n = read(fd, buf + got, size - got);

		if (n < 0) {
			if (errno == EINTR)
				continue;
			shim_fail("cannot read the object", strerror(errno));
			free(buf);
			return NULL;
		}
		if (n == 0)
			break;
		got += (size_t)n;
	}
	if (got != size) {
		shim_fail("short read on the object", NULL);
		free(buf);
		return NULL;
	}

	elf = calloc(1, sizeof(*elf));
	if (!elf) {
		shim_fail("out of memory", NULL);
		free(buf);
		return NULL;
	}
	elf->owned = buf;
	elf->buf = buf;
	elf->size = size;
	if (shim_parse(elf)) {
		shim_free(elf);
		return NULL;
	}
	return elf;
}

int elf_end(Elf *elf)
{
	int rc = elf ? 0 : -1;

	shim_free(elf);
	return rc;
}

Elf_Scn *elf_getscn(Elf *elf, size_t index)
{
	if (!elf || index >= elf->shnum)
		return NULL;
	return &elf->scns[index];
}

/*
 * libelf's walk starts at section 1, not 0: the null section is not a section
 * a caller can do anything with, and libbpf's own index counter assumes it
 * ("scn = NULL; while ((scn = elf_nextscn(elf, scn))) { idx++; ... }" -- the
 * first real section must therefore be index 1).  Returning section 0 here
 * shifts every index by one and the object is parsed as garbage.
 */
Elf_Scn *elf_nextscn(Elf *elf, Elf_Scn *scn)
{
	if (!elf || elf->shnum <= 1)
		return NULL;
	if (!scn)
		return &elf->scns[1];
	if (scn->idx + 1 >= elf->shnum)
		return NULL;
	return &elf->scns[scn->idx + 1];
}

size_t elf_ndxscn(Elf_Scn *scn)
{
	return scn ? scn->idx : SHN_UNDEF;
}

Elf_Data *elf_getdata(Elf_Scn *scn, Elf_Data *data)
{
	if (!scn || data)
		return NULL;
	return &scn->data;
}

Elf_Data *elf_rawdata(Elf_Scn *scn, Elf_Data *data)
{
	return elf_getdata(scn, data);
}

char *elf_strptr(Elf *elf, size_t section, size_t offset)
{
	struct elf_shim_scn *scn;

	if (!elf || section >= elf->shnum)
		return NULL;
	scn = &elf->scns[section];
	if (!scn->data.d_buf || offset >= scn->data.d_size)
		return NULL;
	return (char *)scn->data.d_buf + offset;
}

int elf_getshdrstrndx(Elf *elf, size_t *dst)
{
	if (!elf || !dst)
		return -1;
	if (elf->eh.e_shstrndx == SHN_XINDEX) {
		shim_fail("extended section-name index is not supported", NULL);
		return -1;
	}
	if (elf->eh.e_shstrndx >= elf->shnum) {
		shim_fail("section-name index out of range", NULL);
		return -1;
	}
	*dst = elf->eh.e_shstrndx;
	return 0;
}

int gelf_getclass(Elf *elf)
{
	return elf ? elf->cls : ELFCLASSNONE;
}

GElf_Ehdr *gelf_getehdr(Elf *elf, GElf_Ehdr *dst)
{
	if (!elf || !dst)
		return NULL;
	memcpy(dst, &elf->eh, sizeof(*dst));
	return dst;
}

GElf_Shdr *gelf_getshdr(Elf_Scn *scn, GElf_Shdr *dst)
{
	if (!scn || !dst)
		return NULL;
	memcpy(dst, &scn->elf->shdrs[scn->idx], sizeof(*dst));
	return dst;
}

GElf_Sym *gelf_getsym(Elf_Data *data, int ndx, GElf_Sym *dst)
{
	size_t off;

	if (!data || !data->d_buf || !dst || ndx < 0)
		return NULL;
	off = (size_t)ndx * sizeof(GElf_Sym);
	if (off + sizeof(GElf_Sym) > data->d_size)
		return NULL;
	memcpy(dst, (const char *)data->d_buf + off, sizeof(*dst));
	return dst;
}

GElf_Rel *gelf_getrel(Elf_Data *data, int ndx, GElf_Rel *dst)
{
	size_t off;

	if (!data || !data->d_buf || !dst || ndx < 0)
		return NULL;
	off = (size_t)ndx * sizeof(GElf_Rel);
	if (off + sizeof(GElf_Rel) > data->d_size)
		return NULL;
	memcpy(dst, (const char *)data->d_buf + off, sizeof(*dst));
	return dst;
}

GElf_Rela *gelf_getrela(Elf_Data *data, int ndx, GElf_Rela *dst)
{
	size_t off;

	if (!data || !data->d_buf || !dst || ndx < 0)
		return NULL;
	off = (size_t)ndx * sizeof(GElf_Rela);
	if (off + sizeof(GElf_Rela) > data->d_size)
		return NULL;
	memcpy(dst, (const char *)data->d_buf + off, sizeof(*dst));
	return dst;
}

/* Write side: only referenced from dead code; failing loudly is the contract. */
Elf_Scn *elf_newscn(Elf *elf)
{
	(void)elf;
	shim_fail("the libelf shim is read-only", NULL);
	return NULL;
}

Elf_Data *elf_newdata(Elf_Scn *scn)
{
	(void)scn;
	shim_fail("the libelf shim is read-only", NULL);
	return NULL;
}

off_t elf_update(Elf *elf, Elf_Cmd cmd)
{
	(void)elf;
	(void)cmd;
	shim_fail("the libelf shim is read-only", NULL);
	return -1;
}
