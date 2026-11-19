from __future__ import annotations

ZRAM_C = "drivers/block/zram/zram_drv.c"

T = True


_PARAM_OLD = (
    '/* Module params (documentation at end) */\n'
    'static unsigned int num_devices = 1;\n'
)

_PARAM_NEW = (
    '/* Module params (documentation at end) */\n'
    'static unsigned int num_devices = 1;\n'
    '\n'
    '/*\n'
    ' * sailboat_zram_disksize_1to1: every disksize write is overridden to\n'
    ' * total RAM, so the zram:RAM size ratio stays 1:1 whatever userspace asks.\n'
    ' */\n'
    'static bool abk_zram_disksize_1to1 = true;\n'
    'module_param_named(abk_disksize_1to1, abk_zram_disksize_1to1, bool, 0444);\n'
    'MODULE_PARM_DESC(abk_disksize_1to1,\n'
    '\t"ABK: force zram disksize to 1:1 with RAM (read-only)");\n'
)

_DISKSIZE_OLD = (
    '\tdisksize = memparse(buf, NULL);\n'
    '\tif (!disksize)\n'
    '\t\treturn -EINVAL;\n'
    '\n'
    '\tdown_write(&zram->init_lock);\n'
)

_DISKSIZE_NEW = (
    '\tdisksize = memparse(buf, NULL);\n'
    '\tif (!disksize)\n'
    '\t\treturn -EINVAL;\n'
    '\n'
    '\t/*\n'
    '\t * sailboat_zram_disksize_1to1: ignore the requested size and use the\n'
    '\t * usable physical memory instead, so swap stays at 1:1 with RAM.\n'
    '\t */\n'
    '\tif (abk_zram_disksize_1to1) {\n'
    '\t\tu64 abk_ram = (u64)totalram_pages() << PAGE_SHIFT;\n'
    '\n'
    '\t\tif (abk_ram && abk_ram != PAGE_ALIGN(disksize))\n'
    '\t\t\tpr_info("zram: disksize %llu overridden to %llu (1:1 with RAM)\\n",\n'
    '\t\t\t\tdisksize, abk_ram);\n'
    '\t\tif (abk_ram)\n'
    '\t\t\tdisksize = abk_ram;\n'
    '\t}\n'
    '\n'
    '\tdown_write(&zram->init_lock);\n'
)


def build_steps():
    return [
        (ZRAM_C, _PARAM_OLD, _PARAM_NEW, T),
        (ZRAM_C, _DISKSIZE_OLD, _DISKSIZE_NEW, T),
    ]
