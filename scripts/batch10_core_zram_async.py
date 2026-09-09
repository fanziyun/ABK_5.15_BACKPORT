# -*- coding: utf-8 -*-
"""Batch 10-1: zram async recompress (plan A, kcompressd-style soft
decoupling).

The QPACE write-path machinery in the popsicle-w-oss diff (bio submit ->
ring -> completion handlers -> zram_write_finish) is bound to the 6.12 zram
shape and the QTI hardware engine.  Plan A keeps the generic idea -- a
dedicated worker thread drains an explicit job queue -- but applies it to
the *recompress* path the module already grafted onto 5.15 (android15-6.6
zram_recompression): a new per-device sysfs node ("recompress_async", same
type/threshold/algo grammar as the sync "recompress") scans cheaply in the
sysfs writer and hands every candidate page's decompress/recompress work to
a per-device "zram_recompd" kthread_worker.  The synchronous node keeps its
exact semantics.  No crypto_acomp is introduced: with software scomp
backends crypto_acomp compresses synchronously anyway, so the real async
comes from the dedicated kthread reusing the existing zcomp streams.

Graft-boundary contract (why this shape): replace_once idempotency requires
every earlier group's replacement text to survive as a contiguous block, so
nothing here edits inside the zram_recompression group's blocks and no field
is added to struct zram.  All edits land on pristine, never-replaced text:

  * the kthread.h include goes after the pristine part_stat.h include;
  * forward declarations + the DEVICE_ATTR go right after the pristine
    `#include "zram_drv.h"` / DEFINE_IDR block;
  * the sysfs-list entry is wrapped in its own CONFIG_ZRAM_MULTI_COMP guard
    and prepended before the pristine dev_attr_comp_algorithm entry;
  * the whole engine is appended just before the pristine module_init line,
    after every function it uses;
  * the worker lifetime is tracked in a small global {zram, worker} map
    (no struct zram change), and the reset-side drain in the recompression
    group's zram_reset_device() text calls abk_zram_recomp_drain() before
    comps/table teardown.

No exported-struct fields, no KABI slots, no new Kconfig.
"""

import re

__all__ = ["build_steps", "T"]


def _tabs(text):
    """Convert leading 4-space indentation (readable source) to kernel tabs."""
    text = re.sub(r"(?m)^    +",
                  lambda m: "\t" * (len(m.group(0)) // 4), text)
    return text.rstrip("\n") + "\n"


T = True

# ---------------------------------------------------------------------------
# 1) zram_drv.c: kthread.h include on pristine include text.
# ---------------------------------------------------------------------------

_C_INC_OLD = (
    "#include <linux/part_stat.h>\n"
    "\n"
    "#include \"zram_drv.h\"\n"
)

_C_INC_NEW = (
    "#include <linux/part_stat.h>\n"
    "#include <linux/kthread.h>\n"
    "\n"
    "#include \"zram_drv.h\"\n"
)

# ---------------------------------------------------------------------------
# 2) zram_drv.c: forward declarations (drain helper always; the async store
#    and its DEVICE_ATTR under CONFIG_ZRAM_MULTI_COMP).  Pristine anchor.
# ---------------------------------------------------------------------------

_DECL_OLD = (
    "#include \"zram_drv.h\"\n"
    "\n"
    "static DEFINE_IDR(zram_index_idr);\n"
)

_DECL_NEW = (
    "#include \"zram_drv.h\"\n"
    "\n"
    "/* ABK stable_515_backport: Batch 10-1 async recompress forward decls */\n"
    "static void abk_zram_recomp_drain(struct zram *zram);\n"
    "#ifdef CONFIG_ZRAM_MULTI_COMP\n"
    "static ssize_t recompress_async_store(struct device *dev,\n"
    "\t\t\t\t\t      struct device_attribute *attr,\n"
    "\t\t\t\t\t      const char *buf, size_t len);\n"
    "static DEVICE_ATTR_WO(recompress_async);\n"
    "#endif\n"
    "\n"
    "static DEFINE_IDR(zram_index_idr);\n"
)

# ---------------------------------------------------------------------------
# 3) zram_drv.c sysfs attribute list: guarded prefix before the pristine
#    comp_algorithm entry (never inside the recompression list block).
# ---------------------------------------------------------------------------

_ATTR_OLD = "\t&dev_attr_comp_algorithm.attr,\n"
_ATTR_NEW = (
    "#ifdef CONFIG_ZRAM_MULTI_COMP\n"
    "\t&dev_attr_recompress_async.attr,\n"
    "#endif\n"
    "\t&dev_attr_comp_algorithm.attr,\n"
)

# ---------------------------------------------------------------------------
# 4) zram_drv.c: the engine, appended before the pristine module_init line.
#    The drain + {zram, worker} map are unconditional; the job queue, the
#    worker and recompress_async_store (which call the CONFIG_ZRAM_MULTI_COMP
#    zram_recompress()) are guarded.
# ---------------------------------------------------------------------------

_ENGINE = _tabs(r"""
/* ABK stable_515_backport: Batch 10-1 async recompress engine (plan A).
 * Per-device "zram_recompd" kthread_worker drained by abk_zram_recomp_drain()
 * from zram_reset_device() before comps/table teardown.  Slots are
 * revalidated under the slot lock in the worker, exactly like the
 * synchronous path.
 */
struct abk_zram_recomp_map {
    struct list_head node;
    struct zram *zram;
    struct kthread_worker *worker;
};

static LIST_HEAD(abk_zram_recomp_map_head);
static DEFINE_MUTEX(abk_zram_recomp_map_lock);

static void abk_zram_recomp_drain(struct zram *zram)
{
    struct abk_zram_recomp_map *entry, *tmp;

    mutex_lock(&abk_zram_recomp_map_lock);
    list_for_each_entry_safe(entry, tmp, &abk_zram_recomp_map_head, node) {
        if (entry->zram != zram)
            continue;
        list_del(&entry->node);
        mutex_unlock(&abk_zram_recomp_map_lock);

        kthread_destroy_worker(entry->worker);
        kfree(entry);

        return;
    }
    mutex_unlock(&abk_zram_recomp_map_lock);
}

#ifdef CONFIG_ZRAM_MULTI_COMP
struct abk_zram_recomp_job {
    struct kthread_work work;
    struct zram *zram;
    u32 index;
    u32 threshold;
    u32 prio;
    u32 prio_max;
    bool idle;
};

static void abk_zram_recomp_work(struct kthread_work *work)
{
    struct abk_zram_recomp_job *job =
        container_of(work, struct abk_zram_recomp_job, work);
    struct zram *zram = job->zram;
    struct page *page;

    page = alloc_page(GFP_NOIO | __GFP_HIGHMEM);
    if (page) {
        zram_slot_lock(zram, job->index);
        if (zram_allocated(zram, job->index) &&
            !zram_test_flag(zram, job->index, ZRAM_WB) &&
            !zram_test_flag(zram, job->index, ZRAM_UNDER_WB) &&
            !zram_test_flag(zram, job->index, ZRAM_SAME) &&
            !zram_test_flag(zram, job->index, ZRAM_INCOMPRESSIBLE) &&
            (!job->idle ||
             zram_test_flag(zram, job->index, ZRAM_IDLE)))
            zram_recompress(zram, job->index, page,
                            job->threshold, job->prio, job->prio_max);
        zram_slot_unlock(zram, job->index);
        __free_page(page);
    }
    kfree(job);
}

static int abk_zram_recomp_enqueue(struct zram *zram, u32 index,
                                   u32 threshold, u32 prio, u32 prio_max,
                                   bool idle)
{
    struct abk_zram_recomp_map *entry, *found = NULL;
    struct abk_zram_recomp_job *job;

    mutex_lock(&abk_zram_recomp_map_lock);
    list_for_each_entry(entry, &abk_zram_recomp_map_head, node) {
        if (entry->zram == zram) {
            found = entry;
            break;
        }
    }
    if (!found) {
        entry = kmalloc(sizeof(*entry), GFP_KERNEL);
        if (entry) {
            entry->worker = kthread_create_worker(0, "zram_recompd");
            if (IS_ERR(entry->worker)) {
                int err = PTR_ERR(entry->worker);

                kfree(entry);
                mutex_unlock(&abk_zram_recomp_map_lock);
                return err;
            }
            entry->zram = zram;
            list_add(&entry->node, &abk_zram_recomp_map_head);
            found = entry;
        }
    }
    if (!found) {
        mutex_unlock(&abk_zram_recomp_map_lock);
        return -ENOMEM;
    }
    mutex_unlock(&abk_zram_recomp_map_lock);

    job = kmalloc(sizeof(*job), GFP_KERNEL);
    if (!job)
        return -ENOMEM;

    kthread_init_work(&job->work, abk_zram_recomp_work);
    job->zram = zram;
    job->index = index;
    job->threshold = threshold;
    job->prio = prio;
    job->prio_max = prio_max;
    job->idle = idle;
    kthread_queue_work(found->worker, &job->work);

    return 0;
}

static ssize_t recompress_async_store(struct device *dev,
                                      struct device_attribute *attr,
                                      const char *buf, size_t len)
{
    u32 prio = ZRAM_SECONDARY_COMP, prio_max = ZRAM_MAX_COMPS;
    struct zram *zram = dev_to_zram(dev);
    unsigned long nr_pages = zram->disksize >> PAGE_SHIFT;
    char *args, *param, *val, *algo = NULL;
    u32 mode = 0, threshold = 0;
    unsigned long index;
    ssize_t ret;

    args = skip_spaces(buf);
    while (*args) {
        args = next_arg(args, &param, &val);

        if (!val || !*val)
            return -EINVAL;

        if (!strcmp(param, "type")) {
            if (!strcmp(val, "idle"))
                mode = RECOMPRESS_IDLE;
            if (!strcmp(val, "huge"))
                mode = RECOMPRESS_HUGE;
            if (!strcmp(val, "huge_idle"))
                mode = RECOMPRESS_IDLE | RECOMPRESS_HUGE;
            continue;
        }

        if (!strcmp(param, "threshold")) {
            ret = kstrtouint(val, 10, &threshold);
            if (ret)
                return ret;
            continue;
        }

        if (!strcmp(param, "algo")) {
            algo = val;
            continue;
        }
    }

    if (threshold >= huge_class_size)
        return -EINVAL;

    down_read(&zram->init_lock);
    if (!init_done(zram)) {
        ret = -EINVAL;
        goto release_init_lock;
    }

    if (algo) {
        bool found = false;

        for (; prio < ZRAM_MAX_COMPS; prio++) {
            if (!zram->comp_algs[prio])
                continue;
            if (!strcmp(zram->comp_algs[prio], algo)) {
                prio_max = min(prio + 1, ZRAM_MAX_COMPS);
                found = true;
                break;
            }
        }

        if (!found) {
            ret = -EINVAL;
            goto release_init_lock;
        }
    }

    ret = len;
    for (index = 0; index < nr_pages; index++) {
        bool candidate = false;
        int err = 0;

        zram_slot_lock(zram, index);

        if (!zram_allocated(zram, index))
            goto abk_async_next;

        if (mode & RECOMPRESS_IDLE &&
            !zram_test_flag(zram, index, ZRAM_IDLE))
            goto abk_async_next;

        if (mode & RECOMPRESS_HUGE &&
            !zram_test_flag(zram, index, ZRAM_HUGE))
            goto abk_async_next;

        if (zram_test_flag(zram, index, ZRAM_WB) ||
            zram_test_flag(zram, index, ZRAM_UNDER_WB) ||
            zram_test_flag(zram, index, ZRAM_SAME) ||
            zram_test_flag(zram, index, ZRAM_INCOMPRESSIBLE))
            goto abk_async_next;

        candidate = true;
abk_async_next:
        zram_slot_unlock(zram, index);
        if (!candidate)
            continue;

        err = abk_zram_recomp_enqueue(zram, index, threshold, prio,
                                      prio_max, mode & RECOMPRESS_IDLE);
        if (err) {
            ret = err;
            break;
        }
    }

release_init_lock:
    up_read(&zram->init_lock);
    return ret;
}
#endif /* CONFIG_ZRAM_MULTI_COMP */
""")

_MODULE_INIT_OLD = "module_init(zram_init);\n"
_MODULE_INIT_NEW = (
    "\n" + _ENGINE + "\n" + _MODULE_INIT_OLD
)


def build_steps():
    return [
        ("drivers/block/zram/zram_drv.c", _C_INC_OLD, _C_INC_NEW, T),
        ("drivers/block/zram/zram_drv.c", _DECL_OLD, _DECL_NEW, T),
        ("drivers/block/zram/zram_drv.c", _ATTR_OLD, _ATTR_NEW, T),
        ("drivers/block/zram/zram_drv.c", _MODULE_INIT_OLD, _MODULE_INIT_NEW, T),
    ]
