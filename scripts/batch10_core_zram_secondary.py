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

Default is ``lz4hc``: it compresses better than the ``lz4`` / vendor ``lz4kd``
primaries these devices run while decoding at plain-lz4 speed, so recompressed
pages do not slow page-fault reads down.  ``zram.abk_recomp_algo=`` (empty)
restores the old no-secondary behaviour; ``zram.abk_recomp_algo=zstd`` trades
decode cost for a better ratio.

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
    " * lz4hc is the default because it beats the lz4 / lz4kd primaries on\n"
    " * ratio while decoding at lz4 speed, so page-fault reads do not get\n"
    " * slower.  Empty disables; zstd favours ratio over decode cost.\n"
    " */\n"
    "static char abk_zram_recomp_algo[CRYPTO_MAX_ALG_NAME] = \"lz4hc\";\n"
    "module_param_string(abk_recomp_algo, abk_zram_recomp_algo,\n"
    "\t\t    sizeof(abk_zram_recomp_algo), 0644);\n"
    "MODULE_PARM_DESC(abk_recomp_algo,\n"
    "\t\"ABK: secondary zram compressor enabling recompression (empty disables)\");\n"
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
