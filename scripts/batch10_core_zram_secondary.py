# -*- coding: utf-8 -*-

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
