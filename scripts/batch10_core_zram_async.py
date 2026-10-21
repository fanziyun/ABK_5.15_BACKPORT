# -*- coding: utf-8 -*-

import re

__all__ = ["build_steps", "T"]


def _tabs(text):
    text = re.sub(r"(?m)^    +",
                  lambda m: "\t" * (len(m.group(0)) // 4), text)
    return text.rstrip("\n") + "\n"


T = True


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


_ATTR_OLD = "\t&dev_attr_comp_algorithm.attr,\n"
_ATTR_NEW = (
    "#ifdef CONFIG_ZRAM_MULTI_COMP\n"
    "\t&dev_attr_recompress_async.attr,\n"
    "#endif\n"
    "\t&dev_attr_comp_algorithm.attr,\n"
)


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
