# tools/scx -- the sched_ext userspace side

The kernel side of sched_ext (the class, its hooks, its reachability gates and
the `ext` debugfs dump) is landed by Batches 54-60 of the module. These are the
three userspace pieces that attach a scheduler to it, plus the libelf shim that
makes an Android build possible at all.

| file | what it is |
|---|---|
| `abk_scx_min.bpf.c` | the minimal scheduler: one global FIFO DSQ, five ops (`select_cpu`, `enqueue`, `dispatch`, `init`, `exit`), default slice. It never calls `scx_bpf_switch_all()`, so only tasks explicitly set to `SCHED_EXT` are taken. |
| `abk_scx_compat.h` | the BPF-side kfunc declarations (`.ksyms` externs) and the constants a vmlinux.h cannot carry. Only four kfuncs are declared, and the unit tests pin that set against the payload's registered `BTF_FLAGS(func, ...)` set. |
| `scx_loader.c` | the loader (`selftest`, `status`, `run`, `mark`, `unmark`). It attaches the struct_ops map with libbpf, marks the pids it was given, and detaches + hands them back to `SCHED_NORMAL` on exit. No subcommand means usage and exit 2. |
| `libelf-shim/` | libelf, reduced to what libbpf uses (17 functions, ELF64 little-endian, read-only). Exists because the Android NDK ships no libelf; see below. |

## Building

```
bash tools/build_scx_artifacts.sh <grafted-kernel-tree> <outdir>
```

<grafted-kernel-tree> must be a tree where the sched_ext payload was applied
**before** it was built: the scheduler is compiled against a `vmlinux.h` dumped
from that tree's own BTF, and a kernel built before the graft has no
`struct sched_ext_ops` in its BTF. The builder checks that and fails with the
reason instead of letting clang report an unknown type.

* plain: dumps `vmlinux.h`, builds `abk_scx_min.bpf.o`
  (`clang -target bpf`), and prints the loader's toolchain requirements
* `BUILD_HOST_LOADER=1`: also builds libbpf and links the loader against the
  host's libelf (a compile **and link** gate), then runs `selftest`
* `BUILD_SHIM_LOADER=1`: also builds libbpf against `libelf-shim/`, links the
  loader against that, and **diffs** the two `selftest` outputs

That diff is the point of the shim: the two loaders must describe the object
identically (`struct_ops` map type 26, value 336, and the five program
sections), which is what makes it safe to hand the shim to a cross compiler.

## The libelf shim, and the Android build

The loader links libbpf, libbpf needs libelf, and the Android NDK ships none.
Three ways out were considered (recorded in
`docs/survey_sched_ext_gap.md` section 2k):

1. cross-build elfutils' libelf for `aarch64-linux-android`,
2. build the loader on a host/CI that provides libelf, or
3. provide the slice of libelf libbpf actually uses.

This tree takes (3). The shim is deliberately narrow -- read-only, ELF64
little-endian, one data chunk per section, write-side entry points fail -- and
its correctness is not argued, it is measured: with `BUILD_SHIM_LOADER=1` the
same `selftest` runs through a real-libelf loader and a shim-linked one and the
outputs must be identical. Two things the first attempt got wrong and the diff
caught: `gelf_getshdr` returns `GElf_Shdr *` (not an int), and libelf's
`elf_nextscn(elf, NULL)` starts at section **1** -- libbpf's own index counter
assumes it, and returning section 0 shifts every index by one.

## The Android cross build

```
NDK=<ndk-dir> bash tools/scx/build_android_loader.sh <grafted-tree> <outdir>
```

Run it on the host that owns the NDK (the toolchain binaries are host binaries,
so on Windows that is Git Bash, and `<grafted-tree>` has to be readable from
there -- under WSL, `//wsl$/archlinux/home/...`). It compiles libbpf for
`aarch64-linux-android30` against the shim and the compat headers and links
`scx_loader`; the result is a 64-bit AArch64 PIE that needs only
`libc.so`/`libdl.so`/`libz.so` -- no libelf, which is the whole point.

Three pieces make that work, all measured:

* `libelf-shim/` replaces libelf (the NDK has none).
* `android-compat/` supplies what libbpf needs and bionic does not have:
  `linux/err.h`, `linux/list.h`, `linux/filter.h` (the `BPF_*_INSN`
  macros), `linux/ring_buffer.h`, plus the macro set in
  `kernel-macros.h` (ARRAY_SIZE, container_of, likely/unlikely, roundup,
  min/max, READ_ONCE/WRITE_ONCE, `__poll_t`, `in_addr_t`).
* the libbpf translation units the loader never calls are skipped --
  `linker.c`, `netlink.c`, `nlattr.c`, `xsk.c`, `ringbuf.c`, `usdt.c`
  -- because several of them need kernel-only headers (`asm/barrier.h`) or
  bionic headers that fight the kernel uapi ones this build uses.

The two artefacts are committed under `prebuilt/` and mapped into the module's
`bin/` by `ksu/abk_runtime_tunables/embed.conf`:

| artefact | how it was produced |
|---|---|
| `prebuilt/abk_scx_min.bpf.o` | `tools/build_scx_artifacts.sh` against a grafted 5.15.220 tree (617,936 B) |
| `prebuilt/scx_loader` | the command above (251,848 B, ELF64 AArch64, PIE) |

They are build products, committed so the KernelSU module can ship them at all:
the module is packed during `after_patch`, which runs **before** the kernel is
built, so the post-graft BTF the scheduler needs does not exist yet at pack
time. Rebuilding either one is the two commands above; replacing them in
`prebuilt/` is the whole update.

### Running the artefact on a build host

The committed loader is dynamic bionic (it wants `/system/bin/linker64`, which
the NDK does not ship), so it cannot run on a build host as-is.  Linking the same
sources **static** and running them under qemu-user can:

```
bash tools/scx/run_arm64_selftest.sh <grafted-tree> build/scx-android
```

It prints the static binary's `selftest`, the usage path (exit 2), a load on a
host kernel without sched_ext (exit 1, `Function not implemented`) and finally
`SELFTEST IDENTICAL ACROSS ARCH` when the aarch64 parse matches the x86_64
build's byte for byte.  What that does **not** cover is attaching: that needs a
kernel with the class and the privileges, i.e. the device.

Not verified: anything on a device. The companion module's `scx-policy.sh`
ships the switch off and refuses to start unless `bin/scx_loader` and
`bin/abk_scx_min.bpf.o` are both present, so a build without those artefacts is
a no-op with a stated reason.
