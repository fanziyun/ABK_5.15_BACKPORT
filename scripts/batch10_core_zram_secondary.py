# -*- coding: utf-8 -*-
"""Batch 10-4a: zram secondary compressor registration.

CONFIG_ZRAM_MULTI_COMP only ships the *machinery*.  `zram_recompress()` walks
`zram->comps[prio]` and returns 0 without doing anything while every secondary
slot is NULL, and `recomp_algorithm_store()` refuses to fill one once the
device is initialized (`-EBUSY`).  Android writes `disksize` during early boot,
so a userspace writer loses that race and the whole recompression path -- the
synchronous `recompress` from Batch 4 and the asynchronous `recompress_async`
from Batch 10-1 -- stays a silent no-op.  That is exactly what the on-device
check showed: `recomp_algorithm` empty, `zram_recompd` thread created by the
async dispatch, and no page ever recompressed.

Registering the secondary compressor at device-creation time removes the race:
`zram_add()` runs before any `disksize` write, and `zram_destroy_comps()` only
clears `comps[]`, never `comp_algs[]`, so the choice also survives a `reset`.

Default is ``zstd``, and the parameter is **read-only** (0444).

Both halves were measured on the target device (vermeer, android13-5.15-lts
5.15.215) by compressing an exact 64 MiB corpus of `/system/lib64/*.so` into a
throwaway zram device created through `/sys/class/zram-control/hot_add`
(single core; live swap untouched):

    algo      compressed   ratio   compress          decompress
    lz4       31.19 MB     2.15x   0.60 s (107 MB/s)  865 MB/s
    lz4kd     31.06 MB     2.16x   0.57 s (112 MB/s)  955 MB/s
    lz4hc     29.31 MB     2.29x   1.13 s  (57 MB/s)  901 MB/s
    zstd      24.21 MB     2.77x   0.73 s  (88 MB/s)  330 MB/s
    deflate   23.51 MB     2.85x   2.29 s  (28 MB/s)  208 MB/s

`lz4hc` is dominated in *both* slots: as a primary it doubles the compression
cost on the hot swapout/write path for a 5.6% ratio gain, and as a secondary it
loses to `zstd` by a wide margin.  On a highly repetitive corpus it is even
worse than `lz4kd` (999 KB vs 901 KB).  The primary stays whatever the build
selects (`CONFIG_ZRAM_DEF_COMP`, `lz4kd` on this device -- the vendor's
fast-ratio variant) and the secondary is the ratio-first `zstd`: the async
worker pays the compression cost in the background, and 330 MB/s of page-fault
decompression remains far above any UFS read.

The parameter is deliberately not writable.  On the target ROM a userspace
daemon (`mmd_setup`, Android 16's Rust memory daemon) wrote `comp_algorithm`
before `disksize` and left the primary on the dominated `lz4hc` -- after boot
the node is `-EBUSY` and nobody can repair it.  A 0444 parameter was verified on
the device to reject writes even from KernelSU's root (`Permission denied`), so
the secondary cannot be reassigned at runtime: only the boot cmdline is left,
and the runtime companion module (ksu/abk_runtime_tunables) owns the primary.
``zram.abk_recomp_algo=`` (empty, cmdline only) restores the old
no-secondary behaviour.

Both anchors are pristine text and disjoint from every other group's
replacement blocks (the Batch 4 `disksize_store` body is deliberately not
touched -- editing inside it would break that group's second-pass idempotency):

  * `static const char *default_compressor = CONFIG_ZRAM_DEF_COMP;` -- the
    parameter definition, placed before `zram_add()` uses it;
  * `device_id = ret;` in `zram_add()` -- where the per-device registration
    happens, with `comp_algorithm_set()` (defined earlier in the file) taking
    ownership of the kstrdup'd name.
"""

__all__ = ["build_steps", "T"]


T = True

_PARAM_OLD = "static const char *default_compressor = CONFIG_ZRAM_DEF_COMP;\n"

_PARAM_NEW = (
    "static const char *default_compressor = CONFIG_ZRAM_DEF_COMP;\n"
    "\n"
    "#ifdef CONFIG_ZRAM_MULTI_COMP\n"
    "/*\n"
    " * ABK stable_515_backport: Batch 10-4 secondary zram compressor.\n"
    " *\n"
    " * Registering it at device-creation time is what actually makes\n"
    " * recompression happen: recomp_algorithm_store() rejects any later\n"
    " * attempt with -EBUSY once Android has written disksize at boot, and\n"
    " * zram_recompress() is a no-op while every secondary comp slot is NULL.\n"
    " *\n"
    " * zstd by default: measured on the target device against an exact 64 MiB\n"
    " * ELF corpus, zstd is 22% smaller than the lz4kd primary (24.21 vs 31.06\n"
    " * MB) at 88 MB/s compress / 330 MB/s decompress, while lz4hc buys only\n"
    " * 5.6% over lz4kd for twice the compression cost -- dominated in both the\n"
    " * primary and the secondary slot.  The async worker pays the compression,\n"
    " * and 330 MB/s of fault-time decompression is still far above UFS.\n"
    " *\n"
    " * 0444 on purpose: a ROM userspace daemon selected lz4hc before disksize\n"
    " * and the node is -EBUSY afterwards, so a writable parameter would only\n"
    " * invite the same bad choice.  Verified on-device that even KernelSU root\n"
    " * cannot write a 0444 parameter; only the boot cmdline can differ.\n"
    " */\n"
    "static char abk_zram_recomp_algo[CRYPTO_MAX_ALG_NAME] = \"zstd\";\n"
    "module_param_string(abk_recomp_algo, abk_zram_recomp_algo,\n"
    "\t\t    sizeof(abk_zram_recomp_algo), 0444);\n"
    "MODULE_PARM_DESC(abk_recomp_algo,\n"
    "\t\"ABK: secondary zram compressor enabling recompression (read-only; empty disables)\");\n"
    "#endif\n"
)

_ADD_OLD = "\tdevice_id = ret;\n"

_ADD_NEW = (
    "\tdevice_id = ret;\n"
    "\n"
    "#ifdef CONFIG_ZRAM_MULTI_COMP\n"
    "\t/* ABK stable_515_backport: Batch 10-4: fill the secondary comp slot\n"
    "\t * before any disksize write can initialize the device (best effort:\n"
    "\t * a failure here only means recompression stays disabled).\n"
    "\t */\n"
    "\tif (!zram->comp_algs[ZRAM_SECONDARY_COMP] && abk_zram_recomp_algo[0]) {\n"
    "\t\tif (zcomp_available_algorithm(abk_zram_recomp_algo)) {\n"
    "\t\t\tchar *abk_alg = kstrdup(abk_zram_recomp_algo, GFP_KERNEL);\n"
    "\n"
    "\t\t\tif (abk_alg) {\n"
    "\t\t\t\tcomp_algorithm_set(zram, ZRAM_SECONDARY_COMP, abk_alg);\n"
    "\t\t\t\tpr_info(\"zram%d: recompression enabled, secondary compressor %s\\n\",\n"
    "\t\t\t\t\tdevice_id, abk_zram_recomp_algo);\n"
    "\t\t\t}\n"
    "\t\t} else {\n"
    "\t\t\tpr_warn(\"zram%d: secondary compressor %s unavailable, recompression disabled\\n\",\n"
    "\t\t\t\tdevice_id, abk_zram_recomp_algo);\n"
    "\t\t}\n"
    "\t}\n"
    "#endif\n"
)


def build_steps():
    return [
        ("drivers/block/zram/zram_drv.c", _PARAM_OLD, _PARAM_NEW, T),
        ("drivers/block/zram/zram_drv.c", _ADD_OLD, _ADD_NEW, T),
    ]
