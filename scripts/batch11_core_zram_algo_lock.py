# -*- coding: utf-8 -*-
"""Batch 11: the zram algorithm lock.

Batch 10-4 registered the secondary compressor at device-creation time and the
runtime companion module (ksu/abk_runtime_tunables) forces the primary.  Both
live in userspace, and userspace cannot defend the two nodes:

  * `comp_algorithm` / `recomp_algorithm` are writable only *before* `disksize`
    (`init_done()` -> `-EBUSY` afterwards), and `disksize` is written once per
    boot by whoever brings zram up.  The algorithm is therefore decided by
    whichever writer wins that single window -- the ROM's boot script, a ROM
    daemon, or a root shell.  On the target device a root writer did win it
    after the companion module's takeover and left the primary on `deflate`,
    with nothing in userspace able to repair it without a `swapoff` + `reset`.
  * That repair is what made the algorithm policy and CONFIG_ZRAM_WRITEBACK
    mutually exclusive: a writeback backing device is attached in the same
    pre-`disksize` window (`backing_dev` is `-EBUSY` once initialized) and
    `reset_bdev()` drops it on `reset`.

The lock moves the decision into the kernel, so the window stops mattering:

  * the primary is selected in `zram_add()` from a read-only parameter
    (`zram.abk_comp_algo`, default `lz4kd`, keeping the build's
    `CONFIG_ZRAM_DEF_COMP` when that backend is not compiled in);
  * `late_initcall()` points the `.store` callback of both attributes at a
    function that reports the attempt and returns success.

Accepting the write instead of failing it is deliberate: Android 16's
`mmd_setup` aborts its whole zram bring-up -- writeback backing device and the
`mmd.setup_complete` property that enables the `mmd` daemon included -- when an
algorithm write fails, so `-EPERM` there would re-create the exclusivity this
batch exists to remove.

The selection also survives `reset`: `zram_destroy_comps()` clears `comps[]`,
never `comp_algs[]` (Batch 10-4), and no later write can change what a rebuild
picks up.

Insertion points are block *boundaries*, never the inside of a replacement an
earlier group owns (a later edit inside e.g. `__comp_algorithm_store()`'s text
would break that group's idempotency): the pristine
`static const char *default_compressor = ...` line that Batch 10-4's parameter
block opens with, the pristine `zram_debugfs_register(zram);` call that follows
the primary assignment in `zram_add()`, and the pristine
`module_init(zram_init);` line that Batch 10-1's engine is inserted *before*.
"""

__all__ = ["build_steps", "T"]


T = True

# --- the read-only policy parameters --------------------------------------
# Inserted ahead of Batch 10-4's parameter block, which opens with this line.
_PARAM_OLD = "static const char *default_compressor = CONFIG_ZRAM_DEF_COMP;\n"

_PARAM_NEW = (
    "#ifdef CONFIG_ZRAM_MULTI_COMP\n"
    "/*\n"
    " * ABK stable_515_backport: Batch 11 - the zram algorithm lock.\n"
    " *\n"
    " * The pair is a measurement, not a preference: on the target device lz4kd\n"
    " * is the fastest primary in both directions (112 MB/s compress, 955 MB/s\n"
    " * decompress on an exact 64 MiB ELF corpus) and zstd the best\n"
    " * ratio-per-cost secondary (2.77x against lz4kd's 2.16x), while lz4hc --\n"
    " * what the ROM's userspace daemon picked -- is dominated in both slots.\n"
    " *\n"
    " * Both sysfs nodes close at `disksize`, so the selection used to belong to\n"
    " * whoever won that one window, and repairing it afterwards costs a\n"
    " * swapoff+reset -- which also drops a writeback backing device attached in\n"
    " * the same window.  Locking the choice at creation closes the window from\n"
    " * the kernel side; see abk_zram_locked_algo_store() for why a write that\n"
    " * cannot win is reported as success instead of refused.\n"
    " *\n"
    " * 0444 on purpose: only the boot cmdline can differ from the measurement.\n"
    " */\n"
    "static bool abk_zram_lock_algo = true;\n"
    "module_param(abk_lock_algo, bool, 0444);\n"
    "MODULE_PARM_DESC(abk_lock_algo,\n"
    "\t\"ABK: lock the zram compressors against runtime writes (read-only)\");\n"
    "\n"
    "static char abk_zram_comp_algo[CRYPTO_MAX_ALG_NAME] = \"lz4kd\";\n"
    "module_param_string(abk_comp_algo, abk_zram_comp_algo,\n"
    "\t\t    sizeof(abk_zram_comp_algo), 0444);\n"
    "MODULE_PARM_DESC(abk_comp_algo,\n"
    "\t\"ABK: locked primary zram compressor (read-only; a name this build lacks keeps the default)\");\n"
    "#endif\n"
    "\n"
    "static const char *default_compressor = CONFIG_ZRAM_DEF_COMP;\n"
)

# --- the primary is chosen at device creation ------------------------------
# Inserted directly after the primary assignment, before the next pristine
# statement of zram_add(), so Batch 10-4's block and the recompression group's
# `comp_algs[]` assignment both stay byte-identical.
_PRIMARY_OLD = "\tzram_debugfs_register(zram);\n"

_PRIMARY_NEW = (
    "#ifdef CONFIG_ZRAM_MULTI_COMP\n"
    "\t/* ABK stable_515_backport: Batch 11 - lock the primary before the disk\n"
    "\t * can be created, so a build whose CONFIG_ZRAM_DEF_COMP is a dominated\n"
    "\t * compressor still starts from the measured one.  Best effort: a tree\n"
    "\t * without the vendor's lz4kd backend keeps its build default.\n"
    "\t */\n"
    "\tif (abk_zram_lock_algo && abk_zram_comp_algo[0]) {\n"
    "\t\tif (zcomp_available_algorithm(abk_zram_comp_algo)) {\n"
    "\t\t\tchar *abk_prim = kstrdup(abk_zram_comp_algo, GFP_KERNEL);\n"
    "\n"
    "\t\t\tif (abk_prim)\n"
    "\t\t\t\tcomp_algorithm_set(zram, ZRAM_PRIMARY_COMP,\n"
    "\t\t\t\t\t\t   abk_prim);\n"
    "\t\t} else {\n"
    "\t\t\tpr_warn(\"zram%d: locked compressor %s unavailable, keeping %s\\n\",\n"
    "\t\t\t\tdevice_id, abk_zram_comp_algo, default_compressor);\n"
    "\t\t}\n"
    "\t}\n"
    "#endif\n"
    "\n"
    "\tzram_debugfs_register(zram);\n"
)

# --- and no later write can change it -------------------------------------
# Appended after module_init() rather than before it: Batch 10-1's engine is
# inserted immediately in front of that line, and inserting between the two
# would break that group's replacement block.
_LOCK_OLD = "module_init(zram_init);\n"

_LOCK_BODY = (
    "#ifdef CONFIG_ZRAM_MULTI_COMP\n"
    "/*\n"
    " * ABK stable_515_backport: Batch 11 - the locked stores.\n"
    " *\n"
    " * comp_algorithm and recomp_algorithm are both writable only before\n"
    " * `disksize`, and that window is also where a writeback backing device is\n"
    " * attached; a bad algorithm choice could therefore only be repaired by a\n"
    " * reset that throws the writeback setup away.  With the choice locked at\n"
    " * device creation, the window stops being contested and a later write is\n"
    " * accepted and ignored: callers that check the return value (Android's\n"
    " * mmd_setup aborts its whole zram setup, writeback included, on a failed\n"
    " * algorithm write) must not see a failure here.\n"
    " */\n"
    "static void abk_zram_algo_lock_report(const char *node, const char *buf)\n"
    "{\n"
    "\tchar abk_req[CRYPTO_MAX_ALG_NAME];\n"
    "\tsize_t abk_n = strlen(buf);\n"
    "\n"
    "\tif (abk_n >= sizeof(abk_req))\n"
    "\t\tabk_n = sizeof(abk_req) - 1;\n"
    "\tmemcpy(abk_req, buf, abk_n);\n"
    "\twhile (abk_n > 0 && (abk_req[abk_n - 1] == '\\n' ||\n"
    "\t\t\t     abk_req[abk_n - 1] == '\\r' ||\n"
    "\t\t\t     abk_req[abk_n - 1] == ' '))\n"
    "\t\tabk_n--;\n"
    "\tabk_req[abk_n] = '\\0';\n"
    "\n"
    "\tpr_info_ratelimited(\"zram: %s is locked to '%s', ignoring a write of '%s'\\n\",\n"
    "\t\t\t    node, abk_zram_comp_algo, abk_req);\n"
    "}\n"
    "\n"
    "static ssize_t abk_zram_locked_algo_store(struct device *dev,\n"
    "\t\t\t\t\t  struct device_attribute *attr,\n"
    "\t\t\t\t\t  const char *buf, size_t len)\n"
    "{\n"
    "\tabk_zram_algo_lock_report(attr->attr.name, buf);\n"
    "\treturn len;\n"
    "}\n"
    "\n"
    "/*\n"
    " * The attribute structs are global and dev_attr_store() dereferences\n"
    " * ->store on every write, so pointing them at the locked store in a late\n"
    " * initcall covers the devices zram_init() already created as well as any\n"
    " * later hot_add.\n"
    " */\n"
    "static int __init abk_zram_algo_lock_init(void)\n"
    "{\n"
    "\tif (!abk_zram_lock_algo)\n"
    "\t\treturn 0;\n"
    "\n"
    "\tdev_attr_comp_algorithm.store = abk_zram_locked_algo_store;\n"
    "\tdev_attr_recomp_algorithm.store = abk_zram_locked_algo_store;\n"
    "\tpr_info(\"zram: compressors locked to %s + %s, runtime writes ignored\\n\",\n"
    "\t\tabk_zram_comp_algo, abk_zram_recomp_algo[0] ? abk_zram_recomp_algo : \"none\");\n"
    "\treturn 0;\n"
    "}\n"
    "late_initcall(abk_zram_algo_lock_init);\n"
    "#endif /* CONFIG_ZRAM_MULTI_COMP */\n"
)

_LOCK_NEW = "module_init(zram_init);\n\n" + _LOCK_BODY


def build_steps():
    return [
        ("drivers/block/zram/zram_drv.c", _PARAM_OLD, _PARAM_NEW, T),
        ("drivers/block/zram/zram_drv.c", _PRIMARY_OLD, _PRIMARY_NEW, T),
        ("drivers/block/zram/zram_drv.c", _LOCK_OLD, _LOCK_NEW, T),
    ]
