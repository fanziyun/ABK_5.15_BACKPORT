"""Batch 17: zram writeback bio batching + compressed writeback (5.15 re-anchored).

Three groups, all confined to ``drivers/block/zram/{zram_drv.c,zram_drv.h}``.
They exist because android13-5.15 froze its zram writeback code around 2022:
neither the v6.19 batching series nor the v7.0 compressed-writeback series ever
reached the branch (nor ``linux-5.15.y``, verified against the tree at SUBLEVEL
220, which differs from the 5.15.194 baseline only by the zero-sized-backing-
device hunk that arrived through ACK).

Provenance (every commit below was read as a real patch, not from memory):

* ``zram_writeback_batching`` -- upstream ``f405066a1f0d`` ("zram: introduce
  writeback bio batching", Sergey Senozhatsky, 2025-11-22, series v6, merged for
  v6.19) plus its two fixes, which are built in from the first new line so the
  buggy shape never exists in this tree:
    - ``bf62f69574b1`` ("zram: fix use-after-free in zram_writeback_endio",
      Richard Chang, 2026-05-12, Cc: stable, ``Fixes: f405066a1f0d``): the
      writeback task can free ``wb_ctl`` after ``num_inflight`` hits zero but
      before the completion callback reaches ``wake_up()``.  Fixed with
      ``kfree_rcu()`` + an RCU read section around the whole callback, exactly
      as upstream does;
    - ``3e8d8eb8d7f5`` ("zram: do not leak blk idx at the end of writeback",
      Sergey Senozhatsky, 2026-05-26, ``Fixes: f405066a1f0d``): a reserved but
      unused block index must be released before returning.  On 5.15 the index
      lives in a local (upstream moved it into the request), so the equivalent
      is the ``if (blk_idx) free_block_bdev(...)`` cleanup that must stay ahead
      of the drain -- and a request that is recycled must have its own
      ``blk_idx`` cleared, or it would free the slot block twice.
  The per-device ``wb_batch_size`` field and its ``32`` default come from
  ``e828cccb72ed`` ("zram: add writeback batch size device attr", same series),
  and the write half of ``d38fab605c66`` plus ``3bf1c285dc40`` are folded in
  here as well -- see the next bullet for why.

* ``zram_wb_batch_size`` -- the sysfs surface of ``e828cccb72ed`` only.

* ``zram_compressed_writeback`` -- the read half of ``d38fab605c66`` ("zram:
  introduce compressed data writeback", Richard Chang/Sergey Senozhatsky,
  2025-12-01, merged 2026-01-21 for v7.0) plus its attribute
  ``4c1d61389e8e``, under the renamed ABI ``ba4c3698e696``
  ("compressed_writeback", 2026-02-26, ``Fixes: 4c1d61389e8e``).  The only
  commit with ``Fixes: d38fab605c66`` is ``3bf1c285dc40`` ("clear trailing
  bytes of compressed writeback pages", 2026-05-26): the zeroing lives in
  ``zram_read_from_zspool_raw()`` in this batch first group.

Why the write half of compressed writeback is in the batching group: a later
group may not edit text an earlier group produced (``replace_once`` idempotency
test requires every earlier replacement block to survive as a contiguous string
-- the graft-boundary contract documented in
``scripts/batch10_core_zram_async.py``).  The write path is a branch inside the
batched loop and inside the completion helper, both of which this batch owns, so
the branch has to be part of that group.  The read path only redirects two
pristine ``__zram_bvec_read()`` call sites and adds a block of its own, so it
can be a separate group -- and it probes for the flag field the first group
adds, so a missing batching group degrades instead of half-patching.

5.15 equivalence notes (the parts of the upstream series that are NOT portable
verbatim, each verified against the target tree):

* upstream drives batching from the post-processing slot machinery
  (``zram_pp_ctl``/``zram_pp_slot``/``ZRAM_PP_SLOT``, v6.13 era) which 5.15 does
  not have; the in-flight window is expressed with the markers 5.15 already
  uses for it (``ZRAM_UNDER_WB`` + ``ZRAM_IDLE``);
* upstream reads raw objects with the modern ``zs_obj_read_begin()/end()`` API;
  5.15 ``zs_map_object()`` already returns a contiguous copy of the object
  (``mm/zsmalloc.c`` per-cpu ``vm_buf`` exists precisely for objects spanning
  two pages), so no zsmalloc rewrite is needed and ``zstrm->local_copy`` has no
  5.15 counterpart (``zstrm->buffer`` is the equivalent scratch buffer);
* 5.15 convention traps pinned by ``tests/implementation_audit.py``:
  ``zcomp_stream_put(zram->comps[prio])`` takes the comp (not the stream),
  ``zcomp_decompress()`` takes four arguments, ``zram_set_element()`` (not
  ``zram_set_handle()``) stores the block index, ``bio_init(&bio, &bvec, 1)`` +
  ``bio_set_dev()`` and ``bio_alloc(GFP_NOIO, 1)`` are the two- and three-
  argument forms, and ``zero_user()`` replaces 6.19 ``memset_page()``.

Ordering: registered after ``zram_recompression`` (whose ``comps[]``, priority
bits and ``zram_read_from_zspool()`` this batch builds on), after
``zram_algo_lock``, and after the Batch-14 writeback groups --
``zram_writeback_batching`` rewrites the sweep body that
``zram_writeback_bounds`` anchors in, and Batch 14 was re-anchored in the same
change so that neither group reaches into the other replacement block.  No group
here edits text another one produces.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

ZRAM_C = "drivers/block/zram/zram_drv.c"
ZRAM_H = "drivers/block/zram/zram_drv.h"

# Alias the required-flag for readability (the other batch modules use T/F).
T = True

# ---------------------------------------------------------------------------
# Group 1: zram_writeback_batching -- f405066a1f0d (+ bf62f69574b1,
# 3e8d8eb8d7f5, e828cccb72ed field/default, d38fab605c66 write half,
# 3bf1c285dc40).  Every anchor below is pristine text: the sweep rewrite stops
# one line short of the "next:" label Batch 14 writes, and starts one line after
# the bounds/range check it writes.
# ---------------------------------------------------------------------------

# -- zram_drv.c: rcu_read_lock()/kfree_rcu() need rcupdate.h.  Anchored on an
#    include pair no other group touches: the part_stat.h/zram_drv.h pair is
#    batch10's include block, and inserting between its lines would break it. --
_A_INC_OLD = (
    "#include <linux/highmem.h>\n"
    "#include <linux/slab.h>\n"
)

_A_INC_NEW = (
    "#include <linux/highmem.h>\n"
    "/* ABK stable_515_backport: bf62f69574b1 -- kfree_rcu()/rcu_read_lock(). */\n"
    "#include <linux/rcupdate.h>\n"
    "#include <linux/slab.h>\n"
)

# -- zram_drv.h: the two new per-device knobs, in upstream's field order ------
_A_FIELDS_OLD = (
    "\tbool wb_limit_enable;\n"
    "\tu64 bd_wb_limit;\n"
)

_A_FIELDS_NEW = (
    "\tbool wb_limit_enable;\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: e828cccb72ed, d38fab605c66.  wb_batch_size\n"
    "\t * is the maximum number of in-flight writeback bios (upstream default\n"
    "\t * 32); wb_compressed selects compressed (raw object) writeback, i.e.\n"
    "\t * upstream's compressed_writeback attribute.\n"
    "\t */\n"
    "\tbool wb_compressed;\n"
    "\tu32 wb_batch_size;\n"
    "\tu64 bd_wb_limit;\n"
)

# -- zram_drv.c: the batch machinery.  Inserted before the page_index signature
#    define, i.e. after read_from_bdev_async() and inside CONFIG_ZRAM_WRITEBACK,
#    so every helper it defines is in scope before writeback_store() uses it.
#    Anchoring on the define (not on read_from_bdev_async() tail) keeps the read
#    path free for a later group. --
_A_HELPERS_OLD = (
    "#define PAGE_WB_SIG \"page_index=\"\n"
)

_A_HELPERS_NEW = (
    "/*\n"
    " * ABK stable_515_backport: f405066a1f0d (+ bf62f69574b1, 3e8d8eb8d7f5).\n"
    " * Writeback bio batching, re-anchored to the 5.15 shape.  Upstream drives\n"
    " * this from its post-processing slot machinery (zram_pp_ctl/zram_pp_slot),\n"
    " * which 5.15 does not have, so the in-flight window is expressed with the\n"
    " * markers 5.15 already uses for it: ZRAM_UNDER_WB keeps concurrent\n"
    " * recompression (recompress_store(), abk_zram_recomp_work()) off the slot,\n"
    " * and ZRAM_IDLE is the \"slot neither freed nor rewritten\" snapshot that\n"
    " * zram_free_page() invalidates.\n"
    " *\n"
    " * A batch lives for one writeback_store() invocation and is always drained\n"
    " * before init_lock is dropped, so a reset or a device removal -- both of\n"
    " * which need the write lock and then free the table -- can never run against\n"
    " * an in-flight bio.\n"
    " */\n"
    "struct zram_wb_ctl {\n"
    "\t/* idle list is accessed only by the writeback task, no concurrency */\n"
    "\tstruct list_head idle_reqs;\n"
    "\t/* done list is accessed concurrently, protected by done_lock */\n"
    "\tstruct list_head done_reqs;\n"
    "\twait_queue_head_t done_wait;\n"
    "\tspinlock_t done_lock;\n"
    "\tatomic_t num_inflight;\n"
    "\tstruct rcu_head rcu;\n"
    "};\n"
    "\n"
    "struct zram_wb_req {\n"
    "\tunsigned long blk_idx;\n"
    "\t/* 5.15 has no post-processing slot handle: carry the index instead */\n"
    "\tu32 index;\n"
    "\tstruct page *page;\n"
    "\tstruct bio_vec bio_vec;\n"
    "\tstruct bio bio;\n"
    "\tstruct list_head entry;\n"
    "};\n"
    "\n"
    "static void release_wb_req(struct zram_wb_req *req)\n"
    "{\n"
    "\t__free_page(req->page);\n"
    "\tkfree(req);\n"
    "}\n"
    "\n"
    "static void release_wb_ctl(struct zram_wb_ctl *wb_ctl)\n"
    "{\n"
    "\tif (!wb_ctl)\n"
    "\t\treturn;\n"
    "\n"
    "\t/* We should never have inflight requests at this point */\n"
    "\tWARN_ON(atomic_read(&wb_ctl->num_inflight));\n"
    "\tWARN_ON(!list_empty(&wb_ctl->done_reqs));\n"
    "\n"
    "\twhile (!list_empty(&wb_ctl->idle_reqs)) {\n"
    "\t\tstruct zram_wb_req *req;\n"
    "\n"
    "\t\treq = list_first_entry(&wb_ctl->idle_reqs,\n"
    "\t\t\t\t       struct zram_wb_req, entry);\n"
    "\t\tlist_del(&req->entry);\n"
    "\t\trelease_wb_req(req);\n"
    "\t}\n"
    "\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: bf62f69574b1.  A completion callback can be\n"
    "\t * preempted between queueing its request and wake_up(), so the control\n"
    "\t * block is freed through RCU and the callback runs under rcu_read_lock()\n"
    "\t * -- see zram_writeback_endio() below.\n"
    "\t */\n"
    "\tkfree_rcu(wb_ctl, rcu);\n"
    "}\n"
    "\n"
    "static struct zram_wb_ctl *init_wb_ctl(struct zram *zram)\n"
    "{\n"
    "\tstruct zram_wb_ctl *wb_ctl;\n"
    "\tint i;\n"
    "\n"
    "\twb_ctl = kmalloc(sizeof(*wb_ctl), GFP_KERNEL);\n"
    "\tif (!wb_ctl)\n"
    "\t\treturn NULL;\n"
    "\n"
    "\tINIT_LIST_HEAD(&wb_ctl->idle_reqs);\n"
    "\tINIT_LIST_HEAD(&wb_ctl->done_reqs);\n"
    "\tatomic_set(&wb_ctl->num_inflight, 0);\n"
    "\tinit_waitqueue_head(&wb_ctl->done_wait);\n"
    "\tspin_lock_init(&wb_ctl->done_lock);\n"
    "\n"
    "\tfor (i = 0; i < zram->wb_batch_size; i++) {\n"
    "\t\tstruct zram_wb_req *req;\n"
    "\n"
    "\t\t/*\n"
    "\t\t * This is fatal only if we could not allocate a single request:\n"
    "\t\t * otherwise writeback proceeds with the requests that did\n"
    "\t\t * allocate, even if there is only one.\n"
    "\t\t */\n"
    "\t\treq = kzalloc(sizeof(*req), GFP_KERNEL | __GFP_NOWARN);\n"
    "\t\tif (!req)\n"
    "\t\t\tbreak;\n"
    "\n"
    "\t\treq->page = alloc_page(GFP_KERNEL | __GFP_NOWARN);\n"
    "\t\tif (!req->page) {\n"
    "\t\t\tkfree(req);\n"
    "\t\t\tbreak;\n"
    "\t\t}\n"
    "\n"
    "\t\tlist_add(&req->entry, &wb_ctl->idle_reqs);\n"
    "\t}\n"
    "\n"
    "\t/* We could not allocate any requests, so writeback is not possible */\n"
    "\tif (list_empty(&wb_ctl->idle_reqs)) {\n"
    "\t\trelease_wb_ctl(wb_ctl);\n"
    "\t\treturn NULL;\n"
    "\t}\n"
    "\n"
    "\treturn wb_ctl;\n"
    "}\n"
    "\n"
    "static void zram_account_writeback_rollback(struct zram *zram)\n"
    "{\n"
    "\tspin_lock(&zram->wb_limit_lock);\n"
    "\tif (zram->wb_limit_enable)\n"
    "\t\tzram->bd_wb_limit += 1UL << (PAGE_SHIFT - 12);\n"
    "\tspin_unlock(&zram->wb_limit_lock);\n"
    "}\n"
    "\n"
    "static void zram_account_writeback_submit(struct zram *zram)\n"
    "{\n"
    "\t/*\n"
    "\t * The budget is charged before submission: with a batch of bios in\n"
    "\t * flight, charging after completion would overshoot the configured\n"
    "\t * writeback limit by up to wb_batch_size pages.\n"
    "\t */\n"
    "\tspin_lock(&zram->wb_limit_lock);\n"
    "\tif (zram->wb_limit_enable && zram->bd_wb_limit > 0)\n"
    "\t\tzram->bd_wb_limit -= 1UL << (PAGE_SHIFT - 12);\n"
    "\tspin_unlock(&zram->wb_limit_lock);\n"
    "}\n"
    "\n"
    "/*\n"
    " * Runs in the writeback task (from zram_complete_done_reqs()), never from the\n"
    " * bio completion callback: zram_slot_lock() is a bit spin lock and the\n"
    " * writeback limit accounting takes zram->wb_limit_lock, so neither may be\n"
    " * taken from IRQ context.\n"
    " */\n"
    "static int zram_writeback_complete(struct zram *zram, struct zram_wb_req *req)\n"
    "{\n"
    "\tu32 index = req->index;\n"
    "\tu32 size = 0, prio = 0;\n"
    "\tbool huge = false;\n"
    "\tint err;\n"
    "\n"
    "\terr = blk_status_to_errno(req->bio.bi_status);\n"
    "\tif (err) {\n"
    "\t\t/*\n"
    "\t\t * Failed writeback requests are not accounted in the writeback\n"
    "\t\t * limit (if enabled), and their block is released again.\n"
    "\t\t */\n"
    "\t\tzram_account_writeback_rollback(zram);\n"
    "\t\tfree_block_bdev(zram, req->blk_idx);\n"
    "\t\tzram_slot_lock(zram, index);\n"
    "\t\tzram_clear_flag(zram, index, ZRAM_UNDER_WB);\n"
    "\t\tzram_clear_flag(zram, index, ZRAM_IDLE);\n"
    "\t\tzram_slot_unlock(zram, index);\n"
    "\t\treturn err;\n"
    "\t}\n"
    "\n"
    "\tif (zram->wb_compressed) {\n"
    "\t\t/*\n"
    "\t\t * ABK stable_515_backport: d38fab605c66.  The backing device holds\n"
    "\t\t * the raw object, so the slot has to keep the metadata the read\n"
    "\t\t * path needs to decompress it: zram_free_page() clears all of it.\n"
    "\t\t */\n"
    "\t\tsize = zram_get_obj_size(zram, index);\n"
    "\t\tprio = zram_get_priority(zram, index);\n"
    "\t\thuge = zram_test_flag(zram, index, ZRAM_HUGE);\n"
    "\t}\n"
    "\n"
    "\tatomic64_inc(&zram->stats.bd_writes);\n"
    "\tzram_slot_lock(zram, index);\n"
    "\t/*\n"
    "\t * The slot lock was released for the bio, so the slot can have changed\n"
    "\t * under us: slot_free(), or slot_free() plus zram_write_page().  Both\n"
    "\t * clear ZRAM_IDLE (zram_free_page() clears it), and idle_store() never\n"
    "\t * sets ZRAM_IDLE on a ZRAM_UNDER_WB slot, so ZRAM_IDLE is a sound \"this\n"
    "\t * is still the slot we read\" test.\n"
    "\t */\n"
    "\tif (!zram_allocated(zram, index) ||\n"
    "\t    !zram_test_flag(zram, index, ZRAM_IDLE)) {\n"
    "\t\tzram_clear_flag(zram, index, ZRAM_UNDER_WB);\n"
    "\t\tzram_clear_flag(zram, index, ZRAM_IDLE);\n"
    "\t\tfree_block_bdev(zram, req->blk_idx);\n"
    "\t\tgoto out;\n"
    "\t}\n"
    "\n"
    "\tzram_free_page(zram, index);\n"
    "\tzram_clear_flag(zram, index, ZRAM_UNDER_WB);\n"
    "\tzram_set_flag(zram, index, ZRAM_WB);\n"
    "\tzram_set_element(zram, index, req->blk_idx);\n"
    "\n"
    "\tif (zram->wb_compressed) {\n"
    "\t\tif (huge)\n"
    "\t\t\tzram_set_flag(zram, index, ZRAM_HUGE);\n"
    "\t\tzram_set_obj_size(zram, index, size);\n"
    "\t\tzram_set_priority(zram, index, prio);\n"
    "\t}\n"
    "\n"
    "\tatomic64_inc(&zram->stats.pages_stored);\n"
    "out:\n"
    "\tzram_slot_unlock(zram, index);\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
    "static void zram_writeback_endio(struct bio *bio)\n"
    "{\n"
    "\tstruct zram_wb_req *req = container_of(bio, struct zram_wb_req, bio);\n"
    "\tstruct zram_wb_ctl *wb_ctl = bio->bi_private;\n"
    "\tunsigned long flags;\n"
    "\n"
    "\t/* ABK stable_515_backport: bf62f69574b1 -- see release_wb_ctl(). */\n"
    "\trcu_read_lock();\n"
    "\tspin_lock_irqsave(&wb_ctl->done_lock, flags);\n"
    "\tlist_add(&req->entry, &wb_ctl->done_reqs);\n"
    "\tspin_unlock_irqrestore(&wb_ctl->done_lock, flags);\n"
    "\n"
    "\twake_up(&wb_ctl->done_wait);\n"
    "\trcu_read_unlock();\n"
    "}\n"
    "\n"
    "static void zram_submit_wb_request(struct zram *zram,\n"
    "\t\t\t\t   struct zram_wb_ctl *wb_ctl,\n"
    "\t\t\t\t   struct zram_wb_req *req)\n"
    "{\n"
    "\tzram_account_writeback_submit(zram);\n"
    "\tatomic_inc(&wb_ctl->num_inflight);\n"
    "\treq->bio.bi_private = wb_ctl;\n"
    "\tsubmit_bio(&req->bio);\n"
    "}\n"
    "\n"
    "static int zram_complete_done_reqs(struct zram *zram,\n"
    "\t\t\t\t   struct zram_wb_ctl *wb_ctl)\n"
    "{\n"
    "\tstruct zram_wb_req *req;\n"
    "\tunsigned long flags;\n"
    "\tint ret = 0, err;\n"
    "\n"
    "\twhile (atomic_read(&wb_ctl->num_inflight) > 0) {\n"
    "\t\tspin_lock_irqsave(&wb_ctl->done_lock, flags);\n"
    "\t\treq = list_first_entry_or_null(&wb_ctl->done_reqs,\n"
    "\t\t\t\t\t       struct zram_wb_req, entry);\n"
    "\t\tif (req)\n"
    "\t\t\tlist_del(&req->entry);\n"
    "\t\tspin_unlock_irqrestore(&wb_ctl->done_lock, flags);\n"
    "\n"
    "\t\t/* ->num_inflight > 0 doesn't mean we have done requests */\n"
    "\t\tif (!req)\n"
    "\t\t\tbreak;\n"
    "\n"
    "\t\terr = zram_writeback_complete(zram, req);\n"
    "\t\tif (err)\n"
    "\t\t\tret = err;\n"
    "\n"
    "\t\tatomic_dec(&wb_ctl->num_inflight);\n"
    "\t\t/*\n"
    "\t\t * The block index now belongs to the slot (success) or was released\n"
    "\t\t * (error, or the slot changed under us); clearing it keeps a\n"
    "\t\t * recycled request from releasing it a second time.\n"
    "\t\t */\n"
    "\t\treq->blk_idx = 0;\n"
    "\t\tlist_add(&req->entry, &wb_ctl->idle_reqs);\n"
    "\t}\n"
    "\n"
    "\treturn ret;\n"
    "}\n"
    "\n"
    "static struct zram_wb_req *zram_select_idle_req(struct zram_wb_ctl *wb_ctl)\n"
    "{\n"
    "\tstruct zram_wb_req *req;\n"
    "\n"
    "\treq = list_first_entry_or_null(&wb_ctl->idle_reqs,\n"
    "\t\t\t\t       struct zram_wb_req, entry);\n"
    "\tif (req)\n"
    "\t\tlist_del(&req->entry);\n"
    "\treturn req;\n"
    "}\n"
    "\n"
    "/*\n"
    " * ABK stable_515_backport: d38fab605c66 (+ 3bf1c285dc40).  Copy the raw\n"
    " * compressed object straight into the page that will be submitted to the\n"
    " * backing device: no decompression on the writeback path.  Upstream\n"
    " * read_from_zspool_raw() uses the modern zs_obj_read_begin()/end() API, which\n"
    " * 5.15 does not have; zs_map_object() already hands back a contiguous copy of\n"
    " * the object (mm/zsmalloc.c per-cpu vm_buf is allocated precisely for objects\n"
    " * that span two physical pages), so the mapping IS the bounce buffer and\n"
    " * zstrm->local_copy has no 5.15 counterpart.\n"
    " *\n"
    " * Must be called with the slot lock held: the handle is stable, and the object\n"
    " * is pinned, only while the slot is locked.\n"
    " */\n"
    "static int zram_read_from_zspool_raw(struct zram *zram, struct page *page,\n"
    "\t\t\t\t     u32 index)\n"
    "{\n"
    "\tunsigned long handle = zram_get_handle(zram, index);\n"
    "\tunsigned int size = zram_get_obj_size(zram, index);\n"
    "\tvoid *src;\n"
    "\n"
    "\tif (!handle || !size || size > PAGE_SIZE)\n"
    "\t\treturn -EINVAL;\n"
    "\n"
    "\tsrc = zs_map_object(zram->mem_pool, handle, ZS_MM_RO);\n"
    "\tmemcpy_to_page(page, 0, src, size);\n"
    "\tzs_unmap_object(zram->mem_pool, handle);\n"
    "\n"
    "\t/* Do not leak whatever the page held before into the backing device. */\n"
    "\tmemzero_page(page, size, PAGE_SIZE - size);\n"
    "\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
    "#define PAGE_WB_SIG \"page_index=\"\n"
)

# -- zram_drv.c: the two defaults, next to the writeback lock init ------------
_A_DEFAULT_OLD = (
    "#ifdef CONFIG_ZRAM_WRITEBACK\n"
    "\tspin_lock_init(&zram->wb_limit_lock);\n"
    "#endif\n"
)

_A_DEFAULT_NEW = (
    "#ifdef CONFIG_ZRAM_WRITEBACK\n"
    "\t/* ABK stable_515_backport: e828cccb72ed, d38fab605c66. */\n"
    "\tzram->wb_batch_size = 32;\n"
    "\tzram->wb_compressed = false;\n"
    "\tspin_lock_init(&zram->wb_limit_lock);\n"
    "#endif\n"
)

# -- zram_drv.c writeback_store(): declarations ------------------------------
_A_DECL_OLD = (
    "\tstruct bio bio;\n"
    "\tstruct bio_vec bio_vec;\n"
    "\tstruct page *page;\n"
    "\tssize_t ret = len;\n"
    "\tint mode, err;\n"
    "\tunsigned long blk_idx = 0;\n"
)

_A_DECL_NEW = (
    "\t/*\n"
    "\t * ABK stable_515_backport: f405066a1f0d.  The sweep no longer owns a\n"
    "\t * single staging page: every in-flight request carries its own, so a\n"
    "\t * batch of writeback bios can be submitted without serialising the\n"
    "\t * whole sweep on submit_bio_wait().  wb_ctl owns this call's pool.\n"
    "\t */\n"
    "\tstruct zram_wb_ctl *wb_ctl = NULL;\n"
    "\tstruct zram_wb_req *req = NULL;\n"
    "\tssize_t ret = len;\n"
    "\tint mode, err;\n"
    "\tunsigned long blk_idx = 0;\n"
)

# -- zram_drv.c writeback_store(): allocate the batch instead of one page ----
_A_ALLOC_OLD = (
    "\tpage = alloc_page(GFP_KERNEL);\n"
    "\tif (!page) {\n"
    "\t\tret = -ENOMEM;\n"
    "\t\tgoto release_init_lock;\n"
    "\t}\n"
)

_A_ALLOC_NEW = (
    "\t/*\n"
    "\t * ABK stable_515_backport: f405066a1f0d.  One request pool per writeback\n"
    "\t * invocation; init_wb_ctl() degrades to fewer requests (down to a\n"
    "\t * single one) when memory is tight, so writeback stays possible.\n"
    "\t */\n"
    "\twb_ctl = init_wb_ctl(zram);\n"
    "\tif (!wb_ctl) {\n"
    "\t\tret = -ENOMEM;\n"
    "\t\tgoto release_init_lock;\n"
    "\t}\n"
)

# -- zram_drv.c writeback_store(): the batched sweep.  The replacement stops
#    right before the "next:" label Batch 14's cond_resched() block owns, so
#    that block keeps living as a contiguous replacement of that group. -------
_A_LOOP_OLD = (
    "\tfor (; nr_pages != 0; index++, nr_pages--) {\n"
    "\t\tstruct bio_vec bvec;\n"
    "\n"
    "\t\tbvec.bv_page = page;\n"
    "\t\tbvec.bv_len = PAGE_SIZE;\n"
    "\t\tbvec.bv_offset = 0;\n"
    "\n"
    "\t\tspin_lock(&zram->wb_limit_lock);\n"
    "\t\tif (zram->wb_limit_enable && !zram->bd_wb_limit) {\n"
    "\t\t\tspin_unlock(&zram->wb_limit_lock);\n"
    "\t\t\tret = -EIO;\n"
    "\t\t\tbreak;\n"
    "\t\t}\n"
    "\t\tspin_unlock(&zram->wb_limit_lock);\n"
    "\n"
    "\t\tif (!blk_idx) {\n"
    "\t\t\tblk_idx = alloc_block_bdev(zram);\n"
    "\t\t\tif (!blk_idx) {\n"
    "\t\t\t\tret = -ENOSPC;\n"
    "\t\t\t\tbreak;\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\n"
    "\t\tzram_slot_lock(zram, index);\n"
    "\t\tif (!zram_allocated(zram, index))\n"
    "\t\t\tgoto next;\n"
    "\n"
    "\t\tif (zram_test_flag(zram, index, ZRAM_WB) ||\n"
    "\t\t\t\tzram_test_flag(zram, index, ZRAM_SAME) ||\n"
    "\t\t\t\tzram_test_flag(zram, index, ZRAM_UNDER_WB))\n"
    "\t\t\tgoto next;\n"
    "\n"
    "\t\tif (mode == IDLE_WRITEBACK &&\n"
    "\t\t\t  !zram_test_flag(zram, index, ZRAM_IDLE))\n"
    "\t\t\tgoto next;\n"
    "\t\tif (mode == HUGE_WRITEBACK &&\n"
    "\t\t\t  !zram_test_flag(zram, index, ZRAM_HUGE))\n"
    "\t\t\tgoto next;\n"
    "\t\t/*\n"
    "\t\t * Clearing ZRAM_UNDER_WB is duty of caller.\n"
    "\t\t * IOW, zram_free_page never clear it.\n"
    "\t\t */\n"
    "\t\tzram_set_flag(zram, index, ZRAM_UNDER_WB);\n"
    "\t\t/* Need for hugepage writeback racing */\n"
    "\t\tzram_set_flag(zram, index, ZRAM_IDLE);\n"
    "\t\tzram_slot_unlock(zram, index);\n"
    "\t\tif (zram_bvec_read(zram, &bvec, index, 0, NULL)) {\n"
    "\t\t\tzram_slot_lock(zram, index);\n"
    "\t\t\tzram_clear_flag(zram, index, ZRAM_UNDER_WB);\n"
    "\t\t\tzram_clear_flag(zram, index, ZRAM_IDLE);\n"
    "\t\t\tzram_slot_unlock(zram, index);\n"
    "\t\t\tcontinue;\n"
    "\t\t}\n"
    "\n"
    "\t\tbio_init(&bio, &bio_vec, 1);\n"
    "\t\tbio_set_dev(&bio, zram->bdev);\n"
    "\t\tbio.bi_iter.bi_sector = blk_idx * (PAGE_SIZE >> 9);\n"
    "\t\tbio.bi_opf = REQ_OP_WRITE | REQ_SYNC;\n"
    "\n"
    "\t\tbio_add_page(&bio, bvec.bv_page, bvec.bv_len,\n"
    "\t\t\t\tbvec.bv_offset);\n"
    "\t\t/*\n"
    "\t\t * XXX: A single page IO would be inefficient for write\n"
    "\t\t * but it would be not bad as starter.\n"
    "\t\t */\n"
    "\t\terr = submit_bio_wait(&bio);\n"
    "\t\tif (err) {\n"
    "\t\t\tzram_slot_lock(zram, index);\n"
    "\t\t\tzram_clear_flag(zram, index, ZRAM_UNDER_WB);\n"
    "\t\t\tzram_clear_flag(zram, index, ZRAM_IDLE);\n"
    "\t\t\tzram_slot_unlock(zram, index);\n"
    "\t\t\t/*\n"
    "\t\t\t * Return last IO error unless every IO were\n"
    "\t\t\t * not suceeded.\n"
    "\t\t\t */\n"
    "\t\t\tret = err;\n"
    "\t\t\tcontinue;\n"
    "\t\t}\n"
    "\n"
    "\t\tatomic64_inc(&zram->stats.bd_writes);\n"
    "\t\t/*\n"
    "\t\t * We released zram_slot_lock so need to check if the slot was\n"
    "\t\t * changed. If there is freeing for the slot, we can catch it\n"
    "\t\t * easily by zram_allocated.\n"
    "\t\t * A subtle case is the slot is freed/reallocated/marked as\n"
    "\t\t * ZRAM_IDLE again. To close the race, idle_store doesn't\n"
    "\t\t * mark ZRAM_IDLE once it found the slot was ZRAM_UNDER_WB.\n"
    "\t\t * Thus, we could close the race by checking ZRAM_IDLE bit.\n"
    "\t\t */\n"
    "\t\tzram_slot_lock(zram, index);\n"
    "\t\tif (!zram_allocated(zram, index) ||\n"
    "\t\t\t  !zram_test_flag(zram, index, ZRAM_IDLE)) {\n"
    "\t\t\tzram_clear_flag(zram, index, ZRAM_UNDER_WB);\n"
    "\t\t\tzram_clear_flag(zram, index, ZRAM_IDLE);\n"
    "\t\t\tgoto next;\n"
    "\t\t}\n"
    "\n"
    "\t\tzram_free_page(zram, index);\n"
    "\t\tzram_clear_flag(zram, index, ZRAM_UNDER_WB);\n"
    "\t\tzram_set_flag(zram, index, ZRAM_WB);\n"
    "\t\tzram_set_element(zram, index, blk_idx);\n"
    "\t\tblk_idx = 0;\n"
    "\t\tatomic64_inc(&zram->stats.pages_stored);\n"
    "\t\tspin_lock(&zram->wb_limit_lock);\n"
    "\t\tif (zram->wb_limit_enable && zram->bd_wb_limit > 0)\n"
    "\t\t\tzram->bd_wb_limit -=  1UL << (PAGE_SHIFT - 12);\n"
    "\t\tspin_unlock(&zram->wb_limit_lock);\n"
)

_A_LOOP_NEW = (
    "\tfor (; nr_pages != 0; index++, nr_pages--) {\n"
    "\t\tstruct bio_vec bvec;\n"
    "\n"
    "\t\tspin_lock(&zram->wb_limit_lock);\n"
    "\t\tif (zram->wb_limit_enable && !zram->bd_wb_limit) {\n"
    "\t\t\tspin_unlock(&zram->wb_limit_lock);\n"
    "\t\t\tret = -EIO;\n"
    "\t\t\tbreak;\n"
    "\t\t}\n"
    "\t\tspin_unlock(&zram->wb_limit_lock);\n"
    "\n"
    "\t\t/*\n"
    "\t\t * ABK stable_515_backport: f405066a1f0d.  Take an idle request,\n"
    "\t\t * completing in-flight ones if the pool is exhausted.  New bios are\n"
    "\t\t * submitted as soon as an older one completes, which is what turns\n"
    "\t\t * the one-page-at-a-time sweep into a pipeline bounded by\n"
    "\t\t * wb_batch_size.\n"
    "\t\t */\n"
    "\t\twhile (!req) {\n"
    "\t\t\treq = zram_select_idle_req(wb_ctl);\n"
    "\t\t\tif (req)\n"
    "\t\t\t\tbreak;\n"
    "\n"
    "\t\t\twait_event(wb_ctl->done_wait,\n"
    "\t\t\t\t   !list_empty(&wb_ctl->done_reqs));\n"
    "\n"
    "\t\t\terr = zram_complete_done_reqs(zram, wb_ctl);\n"
    "\t\t\t/*\n"
    "\t\t\t * BIO errors are not fatal, we continue and simply attempt\n"
    "\t\t\t * to writeback the remaining objects (pages).  Signal\n"
    "\t\t\t * user-space with the most recent BIO error.\n"
    "\t\t\t */\n"
    "\t\t\tif (err)\n"
    "\t\t\t\tret = err;\n"
    "\t\t}\n"
    "\n"
    "\t\tif (!blk_idx) {\n"
    "\t\t\tblk_idx = alloc_block_bdev(zram);\n"
    "\t\t\tif (!blk_idx) {\n"
    "\t\t\t\tret = -ENOSPC;\n"
    "\t\t\t\tbreak;\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\n"
    "\t\tzram_slot_lock(zram, index);\n"
    "\t\tif (!zram_allocated(zram, index))\n"
    "\t\t\tgoto next;\n"
    "\n"
    "\t\tif (zram_test_flag(zram, index, ZRAM_WB) ||\n"
    "\t\t\t\tzram_test_flag(zram, index, ZRAM_SAME) ||\n"
    "\t\t\t\tzram_test_flag(zram, index, ZRAM_UNDER_WB))\n"
    "\t\t\tgoto next;\n"
    "\n"
    "\t\tif (mode == IDLE_WRITEBACK &&\n"
    "\t\t\t  !zram_test_flag(zram, index, ZRAM_IDLE))\n"
    "\t\t\tgoto next;\n"
    "\t\tif (mode == HUGE_WRITEBACK &&\n"
    "\t\t\t  !zram_test_flag(zram, index, ZRAM_HUGE))\n"
    "\t\t\tgoto next;\n"
    "\t\t/*\n"
    "\t\t * Clearing ZRAM_UNDER_WB is duty of caller.\n"
    "\t\t * IOW, zram_free_page never clear it.\n"
    "\t\t */\n"
    "\t\tzram_set_flag(zram, index, ZRAM_UNDER_WB);\n"
    "\t\t/* Need for hugepage writeback racing */\n"
    "\t\tzram_set_flag(zram, index, ZRAM_IDLE);\n"
    "\n"
    "\t\tif (zram->wb_compressed) {\n"
    "\t\t\t/*\n"
    "\t\t\t * ABK stable_515_backport: d38fab605c66.  Store the raw\n"
    "\t\t\t * zspool object: no decompression on the writeback path.\n"
    "\t\t\t * Mapping happens under the slot lock, or a concurrent\n"
    "\t\t\t * slot_free() could hand the handle back to zsmalloc\n"
    "\t\t\t * between the unlock and the map.\n"
    "\t\t\t */\n"
    "\t\t\terr = zram_read_from_zspool_raw(zram, req->page, index);\n"
    "\t\t\tzram_slot_unlock(zram, index);\n"
    "\t\t\tif (err) {\n"
    "\t\t\t\tzram_slot_lock(zram, index);\n"
    "\t\t\t\tzram_clear_flag(zram, index, ZRAM_UNDER_WB);\n"
    "\t\t\t\tzram_clear_flag(zram, index, ZRAM_IDLE);\n"
    "\t\t\t\tzram_slot_unlock(zram, index);\n"
    "\t\t\t\tcontinue;\n"
    "\t\t\t}\n"
    "\t\t} else {\n"
    "\t\t\tzram_slot_unlock(zram, index);\n"
    "\n"
    "\t\t\tbvec.bv_page = req->page;\n"
    "\t\t\tbvec.bv_len = PAGE_SIZE;\n"
    "\t\t\tbvec.bv_offset = 0;\n"
    "\t\t\tif (zram_bvec_read(zram, &bvec, index, 0, NULL)) {\n"
    "\t\t\t\tzram_slot_lock(zram, index);\n"
    "\t\t\t\tzram_clear_flag(zram, index, ZRAM_UNDER_WB);\n"
    "\t\t\t\tzram_clear_flag(zram, index, ZRAM_IDLE);\n"
    "\t\t\t\tzram_slot_unlock(zram, index);\n"
    "\t\t\t\tcontinue;\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\n"
    "\t\treq->index = index;\n"
    "\t\treq->blk_idx = blk_idx;\n"
    "\t\tbio_init(&req->bio, &req->bio_vec, 1);\n"
    "\t\tbio_set_dev(&req->bio, zram->bdev);\n"
    "\t\treq->bio.bi_iter.bi_sector = req->blk_idx * (PAGE_SIZE >> 9);\n"
    "\t\treq->bio.bi_opf = REQ_OP_WRITE;\n"
    "\t\treq->bio.bi_end_io = zram_writeback_endio;\n"
    "\t\tbio_add_page(&req->bio, req->page, PAGE_SIZE, 0);\n"
    "\n"
    "\t\tzram_submit_wb_request(zram, wb_ctl, req);\n"
    "\t\tblk_idx = 0;\n"
    "\t\treq = NULL;\n"
    "\t\tcond_resched();\n"
    "\t\tcontinue;\n"
)

# -- zram_drv.c writeback_store(): drain and release.  The "if (blk_idx)" line
#    belongs to Batch 14's _B_LOOP_TAIL_NEW and stays untouched: this step only
#    supplies its body, then the unsubmitted request and the whole pool. -------
# The old block deliberately starts at the pristine free_block_bdev() line:
# Batch 14's _B_LOOP_TAIL_NEW ends at "if (blk_idx)", so that call is still
# the if's body and must be *replaced* (to carry the leak-fix comment), not
# re-emitted after it.  Re-emitting it produced a duplicated release plus a
# -Werror=misleading-indentation build failure -- the second C-level bug of
# this batch that every text-only audit passed.
_A_TAIL_OLD = (
    "\t\tfree_block_bdev(zram, blk_idx);\n"
    "\t__free_page(page);\n"
    "release_init_lock:\n"
)

_A_TAIL_NEW = (
    "\t\t/*\n"
    "\t\t * ABK stable_515_backport: 3e8d8eb8d7f5.  A reservation the sweep\n"
    "\t\t * did not hand to a request (ENOSPC, the writeback limit, or the\n"
    "\t\t * last iteration bailing out) has to be released here: a leaked\n"
    "\t\t * index stays busy forever and costs writeback capacity.\n"
    "\t\t */\n"
    "\t\tfree_block_bdev(zram, blk_idx);\n"
    "\n"
    "\t/* A request that was selected but never submitted owns no block. */\n"
    "\tif (req)\n"
    "\t\trelease_wb_req(req);\n"
    "\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: f405066a1f0d.  Every submitted bio must have\n"
    "\t * completed before init_lock is dropped.  zram_reset_device() and\n"
    "\t * zram_remove() only take the write lock and then free the table, so an\n"
    "\t * in-flight bio that outlived this function would run against freed\n"
    "\t * memory.\n"
    "\t */\n"
    "\twhile (atomic_read(&wb_ctl->num_inflight) > 0) {\n"
    "\t\twait_event(wb_ctl->done_wait,\n"
    "\t\t\t   !list_empty(&wb_ctl->done_reqs));\n"
    "\t\terr = zram_complete_done_reqs(zram, wb_ctl);\n"
    "\t\tif (err)\n"
    "\t\t\tret = err;\n"
    "\t}\n"
    "\n"
    "\trelease_wb_ctl(wb_ctl);\n"
    "release_init_lock:\n"
)

# ---------------------------------------------------------------------------
# Group 2: zram_wb_batch_size -- the e828cccb72ed sysfs surface.
# ---------------------------------------------------------------------------

_B_HANDLERS_OLD = (
    "static void reset_bdev(struct zram *zram)\n"
)

_B_HANDLERS_NEW = (
    "/*\n"
    " * ABK stable_515_backport: e828cccb72ed.  Upper bound for\n"
    " * writeback_batch_size.  Upstream stores whatever the user writes, but\n"
    " * init_wb_ctl() allocates one request and one page per unit under\n"
    " * GFP_KERNEL, so an unbounded u32 is a userspace-triggerable allocation\n"
    " * loop.  256 requests are ~1.1 MiB at 4 KiB pages, far more queue depth\n"
    " * than a backing device needs.\n"
    " */\n"
    "#define ZRAM_WB_BATCH_SIZE_MAX\t256\n"
    "\n"
    "static ssize_t writeback_batch_size_store(struct device *dev,\n"
    "\t\t\t\t\t  struct device_attribute *attr,\n"
    "\t\t\t\t\t  const char *buf, size_t len)\n"
    "{\n"
    "\tstruct zram *zram = dev_to_zram(dev);\n"
    "\tu32 val;\n"
    "\n"
    "\tif (kstrtouint(buf, 10, &val))\n"
    "\t\treturn -EINVAL;\n"
    "\n"
    "\tif (!val)\n"
    "\t\treturn -EINVAL;\n"
    "\n"
    "\tif (val > ZRAM_WB_BATCH_SIZE_MAX)\n"
    "\t\tval = ZRAM_WB_BATCH_SIZE_MAX;\n"
    "\n"
    "\tdown_write(&zram->init_lock);\n"
    "\tzram->wb_batch_size = val;\n"
    "\tup_write(&zram->init_lock);\n"
    "\n"
    "\treturn len;\n"
    "}\n"
    "\n"
    "static ssize_t writeback_batch_size_show(struct device *dev,\n"
    "\t\t\t\t\t struct device_attribute *attr,\n"
    "\t\t\t\t\t char *buf)\n"
    "{\n"
    "\tstruct zram *zram = dev_to_zram(dev);\n"
    "\tu32 val;\n"
    "\n"
    "\tdown_read(&zram->init_lock);\n"
    "\tval = zram->wb_batch_size;\n"
    "\tup_read(&zram->init_lock);\n"
    "\n"
    "\treturn scnprintf(buf, PAGE_SIZE, \"%u\\n\", val);\n"
    "}\n"
    "\n"
    "static void reset_bdev(struct zram *zram)\n"
)

_B_ATTRDECL_OLD = (
    "static DEVICE_ATTR_RW(writeback_limit_enable);\n"
    "#endif\n"
)

_B_ATTRDECL_NEW = (
    "static DEVICE_ATTR_RW(writeback_limit_enable);\n"
    "static DEVICE_ATTR_RW(writeback_batch_size);\n"
    "#endif\n"
)

_B_ATTRS_OLD = (
    "\t&dev_attr_writeback_limit_enable.attr,\n"
    "#endif\n"
)

_B_ATTRS_NEW = (
    "\t&dev_attr_writeback_limit_enable.attr,\n"
    "\t&dev_attr_writeback_batch_size.attr,\n"
    "#endif\n"
)

# ---------------------------------------------------------------------------
# Group 3: zram_compressed_writeback -- the d38fab605c66 read half, the
# compressed_writeback attribute (4c1d61389e8e, renamed by ba4c3698e696).
# ---------------------------------------------------------------------------

_C_READ_OLD = (
    "static int zram_bvec_read(struct zram *zram, struct bio_vec *bvec,\n"
    "\t\t\t\tu32 index, int offset, struct bio *bio)\n"
    "{\n"
    "\tint ret;\n"
    "\tstruct page *page;\n"
)

# The whole block below is compressed-writeback code: it reads zram->bdev and
# zram->wb_compressed, and struct zram only declares those under
# CONFIG_ZRAM_WRITEBACK (zram_drv.h).  ABK's dispatch does not have to enable the
# symbol -- the ROM tier does, and the option is documented as optional -- so the
# block carries the same guard the upstream writeback code does.  Without it the
# module builds a kernel that fails with "no member named 'bdev' in 'struct
# zram'" the moment the config is off (the smoke build of 2026-09-14 hit exactly
# that in CI, see CHANGELOG.md#batch-23).
_C_READ_NEW = (
    "#ifdef CONFIG_ZRAM_WRITEBACK\n"
    "/*\n"
    " * ABK stable_515_backport: d38fab605c66.  Compressed writeback leaves the\n"
    " * zspool object as-is on the backing device, so reading such a slot back has\n"
    " * to decompress what the bio delivered (\"decompression on demand\").  zram\n"
    " * decompression is sleepable while an async read completes in IRQ context,\n"
    " * so the decompression is deferred to a preemptible workqueue; the\n"
    " * synchronous case (partial IO) runs it inline in process context.\n"
    " *\n"
    " * Only the async path needs a request: the synchronous one (partial IO, or a\n"
    " * reader with no parent bio) runs a plain submit_bio_wait() in process\n"
    " * context, where decompression may sleep.  Upstream carries a blk_idx/error\n"
    " * pair here for its worker-based sync path; neither is read in this shape,\n"
    " * so neither is declared.\n"
    " */\n"
    "struct abk_zram_rb_req {\n"
    "\tstruct work_struct work;\n"
    "\tstruct zram *zram;\n"
    "\tstruct page *page;\n"
    "\t/* The read bio for the backing device */\n"
    "\tstruct bio *bio;\n"
    "\tu32 index;\n"
    "\t/* The original bio to complete (async read) */\n"
    "\tstruct bio *parent;\n"
    "};\n"
    "\n"
    "static int abk_zram_decompress_bdev_page(struct zram *zram, struct page *page,\n"
    "\t\t\t\t\t u32 index)\n"
    "{\n"
    "\tstruct zcomp_strm *zstrm;\n"
    "\tunsigned int size;\n"
    "\tu32 prio;\n"
    "\tvoid *src, *dst;\n"
    "\tint ret;\n"
    "\n"
    "\tzram_slot_lock(zram, index);\n"
    "\t/* The slot was unlocked while the page was read, so re-check it */\n"
    "\tif (!zram_test_flag(zram, index, ZRAM_WB)) {\n"
    "\t\tzram_slot_unlock(zram, index);\n"
    "\t\t/* We read some stale data, zero it out */\n"
    "\t\tzero_user(page, 0, PAGE_SIZE);\n"
    "\t\treturn -EIO;\n"
    "\t}\n"
    "\n"
    "\tif (zram_test_flag(zram, index, ZRAM_HUGE)) {\n"
    "\t\t/* ZRAM_HUGE slots are stored raw, nothing to decompress */\n"
    "\t\tzram_slot_unlock(zram, index);\n"
    "\t\treturn 0;\n"
    "\t}\n"
    "\n"
    "\tsize = zram_get_obj_size(zram, index);\n"
    "\tprio = zram_get_priority(zram, index);\n"
    "\n"
    "\t/* 5.15: zcomp_stream_put() takes the comp, not the stream */\n"
    "\tzstrm = zcomp_stream_get(zram->comps[prio]);\n"
    "\tsrc = kmap_atomic(page);\n"
    "\tret = zcomp_decompress(zstrm, src, size, zstrm->buffer);\n"
    "\tkunmap_atomic(src);\n"
    "\tif (!ret) {\n"
    "\t\tdst = kmap_atomic(page);\n"
    "\t\tmemcpy(dst, zstrm->buffer, PAGE_SIZE);\n"
    "\t\tkunmap_atomic(dst);\n"
    "\t}\n"
    "\tzcomp_stream_put(zram->comps[prio]);\n"
    "\tzram_slot_unlock(zram, index);\n"
    "\n"
    "\treturn ret;\n"
    "}\n"
    "\n"
    "static void abk_zram_deferred_decompress(struct work_struct *w)\n"
    "{\n"
    "\tstruct abk_zram_rb_req *req =\n"
    "\t\tcontainer_of(w, struct abk_zram_rb_req, work);\n"
    "\tint ret;\n"
    "\n"
    "\tret = abk_zram_decompress_bdev_page(req->zram, req->page, req->index);\n"
    "\tif (ret)\n"
    "\t\treq->parent->bi_status = BLK_STS_IOERR;\n"
    "\n"
    "\t/* Decrement parent's ->remaining */\n"
    "\tbio_endio(req->parent);\n"
    "\tbio_put(req->bio);\n"
    "\tkfree(req);\n"
    "}\n"
    "\n"
    "static void abk_zram_read_endio(struct bio *bio)\n"
    "{\n"
    "\tstruct abk_zram_rb_req *req = bio->bi_private;\n"
    "\n"
    "\tif (bio->bi_status) {\n"
    "\t\treq->parent->bi_status = bio->bi_status;\n"
    "\t\tbio_endio(req->parent);\n"
    "\t\tbio_put(bio);\n"
    "\t\tkfree(req);\n"
    "\t\treturn;\n"
    "\t}\n"
    "\n"
    "\t/*\n"
    "\t * zram decompression is sleepable, so defer it to a preemptible\n"
    "\t * context.\n"
    "\t */\n"
    "\tINIT_WORK(&req->work, abk_zram_deferred_decompress);\n"
    "\tqueue_work(system_highpri_wq, &req->work);\n"
    "}\n"
    "\n"
    "static int abk_zram_read_bdev_page_sync(struct zram *zram, struct page *page,\n"
    "\t\t\t\t\tunsigned long blk_idx)\n"
    "{\n"
    "\tstruct bio bio;\n"
    "\tstruct bio_vec bvec;\n"
    "\n"
    "\tbvec.bv_page = page;\n"
    "\tbvec.bv_len = PAGE_SIZE;\n"
    "\tbvec.bv_offset = 0;\n"
    "\n"
    "\tbio_init(&bio, &bvec, 1);\n"
    "\tbio_set_dev(&bio, zram->bdev);\n"
    "\tbio.bi_iter.bi_sector = blk_idx * (PAGE_SIZE >> 9);\n"
    "\tbio.bi_opf = REQ_OP_READ;\n"
    "\tbio_add_page(&bio, page, PAGE_SIZE, 0);\n"
    "\n"
    "\treturn submit_bio_wait(&bio);\n"
    "}\n"
    "\n"
    "static int abk_zram_read_compressed_bdev(struct zram *zram, struct page *page,\n"
    "\t\t\t\t\t u32 index, unsigned long blk_idx,\n"
    "\t\t\t\t\t struct bio *parent, bool sync)\n"
    "{\n"
    "\tstruct abk_zram_rb_req *req;\n"
    "\tstruct bio *bio;\n"
    "\tint ret;\n"
    "\n"
    "\tatomic64_inc(&zram->stats.bd_reads);\n"
    "\n"
    "\tif (sync || !parent) {\n"
    "\t\tret = abk_zram_read_bdev_page_sync(zram, page, blk_idx);\n"
    "\t\tif (ret)\n"
    "\t\t\treturn ret;\n"
    "\t\treturn abk_zram_decompress_bdev_page(zram, page, index);\n"
    "\t}\n"
    "\n"
    "\treq = kmalloc(sizeof(*req), GFP_NOIO);\n"
    "\tif (!req)\n"
    "\t\treturn -ENOMEM;\n"
    "\n"
    "\tbio = bio_alloc(GFP_NOIO, 1);\n"
    "\tif (!bio) {\n"
    "\t\tkfree(req);\n"
    "\t\treturn -ENOMEM;\n"
    "\t}\n"
    "\n"
    "\treq->zram = zram;\n"
    "\treq->index = index;\n"
    "\treq->page = page;\n"
    "\treq->bio = bio;\n"
    "\treq->parent = parent;\n"
    "\n"
    "\tbio_set_dev(bio, zram->bdev);\n"
    "\tbio->bi_iter.bi_sector = blk_idx * (PAGE_SIZE >> 9);\n"
    "\tbio->bi_opf = parent->bi_opf;\n"
    "\tbio->bi_private = req;\n"
    "\tbio->bi_end_io = abk_zram_read_endio;\n"
    "\t__bio_add_page(bio, page, PAGE_SIZE, 0);\n"
    "\n"
    "\tbio_inc_remaining(parent);\n"
    "\tsubmit_bio(bio);\n"
    "\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
    "/*\n"
    " * Dispatch wrapper for the two __zram_bvec_read() call sites.  With\n"
    " * compressed writeback enabled a ZRAM_WB slot holds compressed data, so it\n"
    " * has to be decompressed rather than read as a raw page.\n"
    " */\n"
    "static int abk_zram_bvec_read(struct zram *zram, struct page *page, u32 index,\n"
    "\t\t\t      struct bio *bio, bool partial_io)\n"
    "{\n"
    "\tunsigned long blk_idx;\n"
    "\tbool huge;\n"
    "\n"
    "\tif (!zram->wb_compressed)\n"
    "\t\treturn __zram_bvec_read(zram, page, index, bio, partial_io);\n"
    "\n"
    "\tzram_slot_lock(zram, index);\n"
    "\tif (!zram_test_flag(zram, index, ZRAM_WB)) {\n"
    "\t\tzram_slot_unlock(zram, index);\n"
    "\t\treturn __zram_bvec_read(zram, page, index, bio, partial_io);\n"
    "\t}\n"
    "\n"
    "\tblk_idx = zram_get_element(zram, index);\n"
    "\thuge = zram_test_flag(zram, index, ZRAM_HUGE);\n"
    "\tzram_slot_unlock(zram, index);\n"
    "\n"
    "\t/* ZRAM_HUGE slots are stored raw even under compressed writeback */\n"
    "\tif (huge)\n"
    "\t\treturn __zram_bvec_read(zram, page, index, bio, partial_io);\n"
    "\n"
    "\treturn abk_zram_read_compressed_bdev(zram, page, index, blk_idx, bio,\n"
    "\t\t\t\t\t     partial_io);\n"
    "}\n"
    "#endif /* CONFIG_ZRAM_WRITEBACK */\n"
    "\n"
    "static int zram_bvec_read(struct zram *zram, struct bio_vec *bvec,\n"
    "\t\t\t\tu32 index, int offset, struct bio *bio)\n"
    "{\n"
    "\tint ret;\n"
    "\tstruct page *page;\n"
)

_C_CALL1_OLD = (
    "\tret = __zram_bvec_read(zram, page, index, bio, is_partial_io(bvec));\n"
)

# The redirects have to follow the same guard as the code they call: with
# CONFIG_ZRAM_WRITEBACK off there are no compressed WB slots, so the plain
# __zram_bvec_read() of the pristine tree is the right call (and the only one
# that exists).
_C_CALL1_NEW = (
    "\t/* ABK stable_515_backport: d38fab605c66 -- compressed WB slots */\n"
    "#ifdef CONFIG_ZRAM_WRITEBACK\n"
    "\tret = abk_zram_bvec_read(zram, page, index, bio, is_partial_io(bvec));\n"
    "#else\n"
    "\tret = __zram_bvec_read(zram, page, index, bio, is_partial_io(bvec));\n"
    "#endif\n"
)

_C_CALL2_OLD = (
    "\t\tret = __zram_bvec_read(zram, page, index, bio, true);\n"
)

_C_CALL2_NEW = (
    "\t\t/*\n"
    "\t\t * ABK stable_515_backport: d38fab605c66.  A partial write has to\n"
    "\t\t * read the old contents first, and a compressed writeback slot\n"
    "\t\t * cannot be read raw -- that would splice compressed bytes into\n"
    "\t\t * the page being written.\n"
    "\t\t */\n"
    "#ifdef CONFIG_ZRAM_WRITEBACK\n"
    "\t\tret = abk_zram_bvec_read(zram, page, index, bio, true);\n"
    "#else\n"
    "\t\tret = __zram_bvec_read(zram, page, index, bio, true);\n"
    "#endif\n"
)

_C_ATTR_OLD = (
    "#ifdef CONFIG_ZRAM_WRITEBACK\n"
    "static DEVICE_ATTR_RW(backing_dev);\n"
)

_C_ATTR_NEW = (
    "#ifdef CONFIG_ZRAM_WRITEBACK\n"
    "static ssize_t compressed_writeback_store(struct device *dev,\n"
    "\t\t\t\t\t  struct device_attribute *attr,\n"
    "\t\t\t\t\t  const char *buf, size_t len)\n"
    "{\n"
    "\tstruct zram *zram = dev_to_zram(dev);\n"
    "\tbool val;\n"
    "\n"
    "\tif (kstrtobool(buf, &val))\n"
    "\t\treturn -EINVAL;\n"
    "\n"
    "\tdown_write(&zram->init_lock);\n"
    "\tif (init_done(zram)) {\n"
    "\t\tup_write(&zram->init_lock);\n"
    "\t\treturn -EBUSY;\n"
    "\t}\n"
    "\n"
    "\tzram->wb_compressed = val;\n"
    "\tup_write(&zram->init_lock);\n"
    "\n"
    "\treturn len;\n"
    "}\n"
    "\n"
    "static ssize_t compressed_writeback_show(struct device *dev,\n"
    "\t\t\t\t\t struct device_attribute *attr,\n"
    "\t\t\t\t\t char *buf)\n"
    "{\n"
    "\tstruct zram *zram = dev_to_zram(dev);\n"
    "\tbool val;\n"
    "\n"
    "\tdown_read(&zram->init_lock);\n"
    "\tval = zram->wb_compressed;\n"
    "\tup_read(&zram->init_lock);\n"
    "\n"
    "\treturn scnprintf(buf, PAGE_SIZE, \"%d\\n\", val);\n"
    "}\n"
    "\n"
    "static DEVICE_ATTR_RW(compressed_writeback);\n"
    "static DEVICE_ATTR_RW(backing_dev);\n"
)

_C_ATTRS_OLD = (
    "\t&dev_attr_backing_dev.attr,\n"
)

_C_ATTRS_NEW = (
    "\t&dev_attr_backing_dev.attr,\n"
    "\t&dev_attr_compressed_writeback.attr,\n"
)


def build_batching_steps():
    """Group 1: f405066a1f0d plus its two fixes.

    All eight steps are required, so a missing anchor writes nothing: the pool
    creation, the sweep body, the completion helper and the drain are one
    logical change and half of it does not compile.
    """
    return [
        (ZRAM_C, _A_INC_OLD, _A_INC_NEW, T),
        (ZRAM_H, _A_FIELDS_OLD, _A_FIELDS_NEW, T),
        (ZRAM_C, _A_HELPERS_OLD, _A_HELPERS_NEW, T),
        (ZRAM_C, _A_DEFAULT_OLD, _A_DEFAULT_NEW, T),
        (ZRAM_C, _A_DECL_OLD, _A_DECL_NEW, T),
        (ZRAM_C, _A_ALLOC_OLD, _A_ALLOC_NEW, T),
        (ZRAM_C, _A_LOOP_OLD, _A_LOOP_NEW, T),
        (ZRAM_C, _A_TAIL_OLD, _A_TAIL_NEW, T),
    ]


def build_batch_size_steps():
    """Group 2: the e828cccb72ed sysfs surface."""
    return [
        (ZRAM_C, _B_HANDLERS_OLD, _B_HANDLERS_NEW, T),
        (ZRAM_C, _B_ATTRDECL_OLD, _B_ATTRDECL_NEW, T),
        (ZRAM_C, _B_ATTRS_OLD, _B_ATTRS_NEW, T),
    ]


def build_compressed_steps():
    """Group 3: the d38fab605c66 read-back half.

    The two call-site redirects are one logical change with the dispatcher they
    call; the attribute steps are additive and use anchors disjoint from the
    batch-size group's, so the two groups cannot break each other's replacement
    blocks.
    """
    return [
        (ZRAM_C, _C_READ_OLD, _C_READ_NEW, T),
        (ZRAM_C, _C_CALL1_OLD, _C_CALL1_NEW, T),
        (ZRAM_C, _C_CALL2_OLD, _C_CALL2_NEW, T),
        (ZRAM_C, _C_ATTR_OLD, _C_ATTR_NEW, T),
        (ZRAM_C, _C_ATTRS_OLD, _C_ATTRS_NEW, T),
    ]


def _batching_apply(ctx):
    try:
        text = ctx.read(ZRAM_C)
    except FileNotFoundError:
        return "blocked_by_shape", f"{ZRAM_C}: file absent"
    for probe, why in (
        ("static ssize_t writeback_store(struct device *dev,",
         "writeback_store()"),
        ("#define PAGE_WB_SIG \"page_index=\"", "the writeback sweep"),
        ("static void free_block_bdev(struct zram *zram, unsigned long blk_idx)",
         "free_block_bdev()"),
    ):
        if probe not in text:
            return "blocked_by_shape", f"{why} not found in zram_drv.c"
    status, _results, detail = apply_steps(ctx, build_batching_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _batch_size_apply(ctx):
    try:
        text = ctx.read(ZRAM_C)
    except FileNotFoundError:
        return "blocked_by_shape", f"{ZRAM_C}: file absent"
    for probe, why in (
        ("static void reset_bdev(struct zram *zram)", "reset_bdev()"),
        ("static DEVICE_ATTR_RW(writeback_limit_enable);", "the writeback attrs"),
    ):
        if probe not in text:
            return "blocked_by_shape", f"{why} not found in zram_drv.c"
    status, _results, detail = apply_steps(ctx, build_batch_size_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def _compressed_apply(ctx):
    try:
        text = ctx.read(ZRAM_C)
        header = ctx.read(ZRAM_H)
    except FileNotFoundError:
        return "blocked_by_shape", f"{ZRAM_C}/{ZRAM_H}: file absent"
    # The read-back half is meaningless without the write half: without
    # zram_writeback_batching's flag field the dispatcher cannot compile, so
    # degrade instead of writing a tree that does not build.
    if "bool wb_compressed;" not in header:
        return "blocked_by_shape", ("zram->wb_compressed is absent: "
                                    "zram_writeback_batching must apply first")
    for probe, why in (
        ("static int zram_bvec_read(struct zram *zram, struct bio_vec *bvec,",
         "zram_bvec_read()"),
    ):
        if probe not in text:
            return "blocked_by_shape", f"{why} not found in zram_drv.c"
    # The redirect step replaces this call with abk_zram_bvec_read(), so the
    # probe has to accept either shape: an early return on the pristine form
    # alone would make this group report blocked_by_shape on every second pass
    # (step_audit trap 4).
    if ("\t\tret = __zram_bvec_read(zram, page, index, bio, true);\n" not in text
            and "\t\tret = abk_zram_bvec_read(zram, page, index, bio, true);\n"
            not in text):
        return "blocked_by_shape", ("the partial-write read-modify-write path "
                                    "not found in zram_drv.c")
    status, _results, detail = apply_steps(ctx, build_compressed_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the three PatchGroup records, in registration order."""
    return [
        PatchGroup(
            "zram_writeback_batching",
            "keep several writeback bios in flight (wb_batch_size, default 32) "
            "instead of one submit_bio_wait() per page, with the upstream "
            "wb_ctl UAF (kfree_rcu + rcu_read_lock in the endio) and blk_idx "
            "leak fixes built in from the first line (f405066a1f0d, "
            "re-anchored to the 5.15 ZRAM_UNDER_WB protocol; also carries the "
            "write half of compressed writeback)",
            [
                "f405066a1f0d (v6.19, series v6)",
                "bf62f69574b1 (v7.3-rc, Cc: stable; Fixes: f405066a1f0d)",
                "3e8d8eb8d7f5 (v7.3; Fixes: f405066a1f0d)",
                "e828cccb72ed (v6.19) -- field + 32 default (sysfs is the next group)",
                "d38fab605c66 (v7.0) -- write half only; read half is the third group",
                "3bf1c285dc40 (v7.3; Fixes: d38fab605c66) -- trailing-byte zeroing",
            ],
            [ZRAM_C, ZRAM_H],
            _batching_apply,
        ),
        PatchGroup(
            "zram_wb_batch_size",
            "expose the in-flight writeback batch size as a per-device attribute "
            "(default 32, !0 rejected, clamped to a bounded pool)",
            [
                "e828cccb72ed (v6.19, series v6)",
            ],
            [ZRAM_C],
            _batch_size_apply,
        ),
        PatchGroup(
            "zram_compressed_writeback",
            "store writeback pages as raw zspool objects and decompress them on "
            "demand on the read path (deferred to a preemptible workqueue for "
            "async reads), plus the compressed_writeback attribute",
            [
                "d38fab605c66 (v7.0) -- read-back half",
                "4c1d61389e8e (v7.0) -- writeback_compressed attribute",
                "ba4c3698e696 (Fixes: 4c1d61389e8e) -- renamed to compressed_writeback",
            ],
            [ZRAM_C],
            _compressed_apply,
        ),
    ]
