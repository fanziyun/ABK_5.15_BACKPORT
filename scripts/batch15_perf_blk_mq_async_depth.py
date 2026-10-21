from __future__ import annotations

from abk_backport_engine import apply_steps

BLKDEV_H = "include/linux/blkdev.h"
BLK_CORE_C = "block/blk-core.c"
BLK_MQ_C = "block/blk-mq.c"
BLK_MQ_SCHED_C = "block/blk-mq-sched.c"
BLK_SYSFS_C = "block/blk-sysfs.c"
ELEVATOR_C = "block/elevator.c"
MQ_DEADLINE_C = "block/mq-deadline.c"
BFQ_C = "block/bfq-iosched.c"
KYBER_C = "block/kyber-iosched.c"

T = True


_A_KABI = (
    "\tANDROID_KABI_RESERVE(1);\n"
    "\tANDROID_KABI_RESERVE(2);\n"
    "\tANDROID_KABI_RESERVE(3);\n"
    "\tANDROID_KABI_RESERVE(4);\n"
    "\n"
    "\tANDROID_OEM_DATA(1);\n"
    "};\n"
)

_A_KABI_NEW = (
    "\t/* ABK stable_515_backport: blk_mq_async_depth -- queue-level async\n"
    "\t * tag depth.  Slot 1 is the slot ABK_ABI_PATCH_SUITE also claims; the\n"
    "\t * suite must not be co-injected with this module. */\n"
    "\tANDROID_KABI_USE(1, unsigned int\t\tasync_depth);\n"
    "\tANDROID_KABI_RESERVE(2);\n"
    "\tANDROID_KABI_RESERVE(3);\n"
    "\tANDROID_KABI_RESERVE(4);\n"
    "\n"
    "\tANDROID_OEM_DATA(1);\n"
    "};\n"
)


_B_DEFAULT = (
    "\tq->nr_requests = BLKDEV_MAX_RQ;\n"
    "\n"
    "\treturn q;\n"
)

_B_DEFAULT_NEW = (
    "\tq->nr_requests = BLKDEV_MAX_RQ;\n"
    "\t/* ABK stable_515_backport: blk_mq_async_depth -- an MQ queue starts at\n"
    "\t * its request depth; blk_mq_init_sched() narrows it per elevator. */\n"
    "\tq->async_depth = BLKDEV_MAX_RQ;\n"
    "\n"
    "\treturn q;\n"
)


_C_HELPER = (
    "/*\n"
    " * ABK stable_515_backport: blk_mq_async_depth -- the single limit_depth\n"
    " * entry point.  Flush, passthrough and reserved requests bypass the limit\n"
    " * (they go straight to the dispatch list, so limiting them is useless);\n"
    " * everything else is handed to the elevator, whose op derives a per-hctx\n"
    " * shallow depth from q->async_depth.\n"
    " */\n"
    "static void blk_mq_limit_depth(unsigned int opf, struct blk_mq_alloc_data *data)\n"
    "{\n"
    "\tstruct elevator_queue *e = data->q->elevator;\n"
    "\n"
    "\tif (!e || !e->type->ops.limit_depth)\n"
    "\t\treturn;\n"
    "\tif (op_is_flush(opf) || blk_op_is_passthrough(opf) ||\n"
    "\t    (data->flags & BLK_MQ_REQ_RESERVED))\n"
    "\t\treturn;\n"
    "\n"
    "\te->type->ops.limit_depth(opf, data);\n"
    "}\n"
    "\n"
)

_C_ALLOC_HEAD = (
    "static struct request *__blk_mq_alloc_request(struct blk_mq_alloc_data *data)\n"
    "{\n"
    "\tstruct request_queue *q = data->q;\n"
)

_C_ALLOC_HEAD_NEW = (
    _C_HELPER
    + "static struct request *__blk_mq_alloc_request(struct blk_mq_alloc_data *data)\n"
    "{\n"
    "\tvoid (*limit_depth)(unsigned int, struct blk_mq_alloc_data *) = NULL;\n"
    "\tstruct request_queue *q = data->q;\n"
)

_C_LIMIT = (
    "\tif (e) {\n"
    "\t\t/*\n"
    "\t\t * Flush/passthrough requests are special and go directly to the\n"
    "\t\t * dispatch list. Don't include reserved tags in the\n"
    "\t\t * limiting, as it isn't useful.\n"
    "\t\t */\n"
    "\t\tif (!op_is_flush(data->cmd_flags) &&\n"
    "\t\t    !blk_op_is_passthrough(data->cmd_flags) &&\n"
    "\t\t    e->type->ops.limit_depth &&\n"
    "\t\t    !(data->flags & BLK_MQ_REQ_RESERVED))\n"
    "\t\t\te->type->ops.limit_depth(data->cmd_flags, data);\n"
    "\t}\n"
    "\n"
    "retry:\n"
    "\tdata->ctx = blk_mq_get_ctx(q);\n"
    "\tdata->hctx = blk_mq_map_queue(q, data->cmd_flags, data->ctx);\n"
    "\tif (!e)\n"
    "\t\tblk_mq_tag_busy(data->hctx);\n"
)

_C_LIMIT_NEW = (
    "\t/*\n"
    "\t * ABK stable_515_backport: blk_mq_async_depth -- record the elevator's\n"
    "\t * limit_depth() op and run it below, once data->hctx is mapped: the\n"
    "\t * shallow depth it sets is a per-hardware-queue word depth, so it can\n"
    "\t * only be derived from the hctx.  Flush/passthrough/reserved are\n"
    "\t * filtered inside blk_mq_limit_depth().\n"
    "\t */\n"
    "\tif (e)\n"
    "\t\tlimit_depth = blk_mq_limit_depth;\n"
    "\n"
    "retry:\n"
    "\tdata->ctx = blk_mq_get_ctx(q);\n"
    "\tdata->hctx = blk_mq_map_queue(q, data->cmd_flags, data->ctx);\n"
    "\tif (!e)\n"
    "\t\tblk_mq_tag_busy(data->hctx);\n"
)

_C_GET_TAG = "\ttag = blk_mq_get_tag(data);\n"

_C_GET_TAG_NEW = (
    "\t/*\n"
    "\t * ABK stable_515_backport: blk_mq_async_depth -- data->hctx is known\n"
    "\t * here, so the elevator's limit_depth() can set data->shallow_depth,\n"
    "\t * which blk_mq_get_tag() consumes.\n"
    "\t */\n"
    "\tif (limit_depth)\n"
    "\t\tlimit_depth(data->cmd_flags, data);\n"
    "\n"
    "\ttag = blk_mq_get_tag(data);\n"
)

_C_QUEUE_INIT = (
    "\tq->nr_requests = set->queue_depth;\n"
    "\n"
    "\t/*\n"
    "\t * Default to classic polling\n"
    "\t */\n"
)

_C_QUEUE_INIT_NEW = (
    "\tq->nr_requests = set->queue_depth;\n"
    "\t/* ABK stable_515_backport: blk_mq_async_depth -- default the async\n"
    "\t * depth to the tag set's depth (blk_mq_init_sched narrows it). */\n"
    "\tq->async_depth = set->queue_depth;\n"
    "\n"
    "\t/*\n"
    "\t * Default to classic polling\n"
    "\t */\n"
)

_C_RESIZE = (
    "\tif (!ret) {\n"
    "\t\tq->nr_requests = nr;\n"
    "\t\tif (q->elevator && blk_mq_is_sbitmap_shared(set->flags))\n"
)

_C_RESIZE_NEW = (
    "\tif (!ret) {\n"
    "\t\tunsigned long new_async_depth;\n"
    "\n"
    "\t\t/*\n"
    "\t\t * ABK stable_515_backport: blk_mq_async_depth -- keep the async\n"
    "\t\t * depth relative to the request depth across a resize, so\n"
    "\t\t * `echo N > queue/nr_requests` narrows (or widens) the async budget\n"
    "\t\t * with it instead of silently turning a relative knob absolute.\n"
    "\t\t */\n"
    "\t\tnew_async_depth = (unsigned long)q->async_depth * nr / q->nr_requests;\n"
    "\t\tif (!new_async_depth)\n"
    "\t\t\tnew_async_depth = 1;\n"
    "\t\tq->async_depth = min_t(unsigned long, new_async_depth, UINT_MAX);\n"
    "\t\tq->nr_requests = nr;\n"
    "\t\tif (q->elevator && blk_mq_is_sbitmap_shared(set->flags))\n"
)


_D_SCHED_NONE = (
    "\tif (!e) {\n"
    "\t\tq->elevator = NULL;\n"
    "\t\tq->nr_requests = q->tag_set->queue_depth;\n"
    "\t\treturn 0;\n"
    "\t}\n"
)

_D_SCHED_NONE_NEW = (
    "\tif (!e) {\n"
    "\t\tq->elevator = NULL;\n"
    "\t\tq->nr_requests = q->tag_set->queue_depth;\n"
    "\t\t/* ABK stable_515_backport: blk_mq_async_depth -- switching to no\n"
    "\t\t * scheduler drops the async budget back to the tag set depth. */\n"
    "\t\tq->async_depth = q->tag_set->queue_depth;\n"
    "\t\treturn 0;\n"
    "\t}\n"
)

_D_SCHED_DEFAULT = (
    "\tq->nr_requests = 2 * min_t(unsigned int, q->tag_set->queue_depth,\n"
    "\t\t\t\t   BLKDEV_MAX_RQ);\n"
)

_D_SCHED_DEFAULT_NEW = (
    "\tq->nr_requests = 2 * min_t(unsigned int, q->tag_set->queue_depth,\n"
    "\t\t\t\t   BLKDEV_MAX_RQ);\n"
    "\t/* ABK stable_515_backport: blk_mq_async_depth -- a fresh elevator\n"
    "\t * starts unrestricted; its ->init_sched() may reserve a share. */\n"
    "\tq->async_depth = q->nr_requests;\n"
)


_E_SYSFS_ANCHOR = (
    "static ssize_t\n"
    "queue_ra_store(struct request_queue *q, const char *page, size_t count)\n"
    "{\n"
)

_E_SYSFS_FUNCS_NEW = (
    "/* ABK stable_515_backport: blk_mq_async_depth -- queue/async_depth. */\n"
    "static ssize_t queue_async_depth_show(struct request_queue *q, char *page)\n"
    "{\n"
    "\treturn queue_var_show(q->async_depth, page);\n"
    "}\n"
    "\n"
    "static ssize_t\n"
    "queue_async_depth_store(struct request_queue *q, const char *page, size_t count)\n"
    "{\n"
    "\tunsigned long nr;\n"
    "\tint ret;\n"
    "\n"
    "\tif (!queue_is_mq(q))\n"
    "\t\treturn -EINVAL;\n"
    "\n"
    "\tret = queue_var_store(&nr, page, count);\n"
    "\tif (ret < 0)\n"
    "\t\treturn ret;\n"
    "\tif (nr == 0)\n"
    "\t\treturn -EINVAL;\n"
    "\tif (!q->elevator)\n"
    "\t\treturn -EINVAL;\n"
    "\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: blk_mq_async_depth -- clamp to the queue's\n"
    "\t * own request depth, then let every scheduler recompute the shallow\n"
    "\t * depth it derives from it.\n"
    "\t */\n"
    "\tq->async_depth = min_t(unsigned long, q->nr_requests, nr);\n"
    "\tif (q->elevator->type->ops.depth_updated) {\n"
    "\t\tstruct blk_mq_hw_ctx *hctx;\n"
    "\t\tunsigned int i;\n"
    "\n"
    "\t\tqueue_for_each_hw_ctx(q, hctx, i) {\n"
    "\t\t\tif (hctx->sched_tags)\n"
    "\t\t\t\tq->elevator->type->ops.depth_updated(hctx);\n"
    "\t\t}\n"
    "\t}\n"
    "\treturn ret;\n"
    "}\n"
    "\n"
    "static ssize_t\n"
    "queue_ra_store(struct request_queue *q, const char *page, size_t count)\n"
    "{\n"
)

_E_SYSFS_ENTRY = (
    'QUEUE_RW_ENTRY(queue_requests, "nr_requests");\n'
    'QUEUE_RW_ENTRY(queue_ra, "read_ahead_kb");\n'
)

_E_SYSFS_ENTRY_NEW = (
    'QUEUE_RW_ENTRY(queue_requests, "nr_requests");\n'
    '/* ABK stable_515_backport: blk_mq_async_depth -- queue/async_depth. */\n'
    'QUEUE_RW_ENTRY(queue_async_depth, "async_depth");\n'
    'QUEUE_RW_ENTRY(queue_ra, "read_ahead_kb");\n'
)

_E_SYSFS_ATTR = (
    "\t&queue_requests_entry.attr,\n"
    "\t&queue_ra_entry.attr,\n"
)

_E_SYSFS_ATTR_NEW = (
    "\t&queue_requests_entry.attr,\n"
    "\t/* ABK stable_515_backport: blk_mq_async_depth -- queue/async_depth. */\n"
    "\t&queue_async_depth_entry.attr,\n"
    "\t&queue_ra_entry.attr,\n"
)


_F_ELEVATOR = (
    "\tret = blk_mq_init_sched(q, new_e);\n"
    "\tif (ret)\n"
    "\t\tgoto out;\n"
)

_F_ELEVATOR_NEW = (
    "\tret = blk_mq_init_sched(q, new_e);\n"
    "\tif (ret)\n"
    "\t\tgoto out;\n"
    "\t/* ABK stable_515_backport: blk_mq_async_depth -- switching to no\n"
    "\t * scheduler drops the async budget back to the tag set depth. */\n"
    "\tif (!new_e)\n"
    "\t\tq->async_depth = q->tag_set->queue_depth;\n"
)


_G_DD_HELPER = (
    "/*\n"
    " * Called by __blk_mq_alloc_request(). The shallow_depth value set by this\n"
    " * function is used by __blk_mq_get_tag().\n"
    " */\n"
    "static void dd_limit_depth(unsigned int op, struct blk_mq_alloc_data *data)\n"
)

_G_DD_HELPER_NEW = (
    "/*\n"
    " * ABK stable_515_backport: blk_mq_async_depth.  'depth' is a number in the\n"
    " * range 1..INT_MAX representing a number of requests. Scale it with a\n"
    " * factor (1 << bt->sb.shift) / q->nr_requests since 1..(1 << bt->sb.shift)\n"
    " * is the range expected by sbitmap_get_shallow().  Values larger than\n"
    " * q->nr_requests have the same effect as q->nr_requests.\n"
    " */\n"
    "static int dd_to_word_depth(struct blk_mq_hw_ctx *hctx, unsigned int qdepth)\n"
    "{\n"
    "\tstruct sbitmap_queue *bt = hctx->sched_tags->bitmap_tags;\n"
    "\tconst unsigned int nrr = hctx->queue->nr_requests;\n"
    "\n"
    "\treturn ((qdepth << bt->sb.shift) + nrr - 1) / nrr;\n"
    "}\n"
    "\n"
    "/*\n"
    " * Called by __blk_mq_alloc_request(). The shallow_depth value set by this\n"
    " * function is used by __blk_mq_get_tag().\n"
    " */\n"
    "static void dd_limit_depth(unsigned int op, struct blk_mq_alloc_data *data)\n"
)

_G_DD_SHALLOW = "\tdata->shallow_depth = dd->async_depth;\n"

_G_DD_SHALLOW_NEW = "\tdata->shallow_depth = dd_to_word_depth(data->hctx, dd->async_depth);\n"

_G_DD_DEPTH = (
    "\tstruct request_queue *q = hctx->queue;\n"
    "\tstruct deadline_data *dd = q->elevator->elevator_data;\n"
    "\tstruct blk_mq_tags *tags = hctx->sched_tags;\n"
    "\tunsigned int shift = tags->bitmap_tags->sb.shift;\n"
    "\n"
    "\tdd->async_depth = max(1U, 3 * (1U << shift)  / 4);\n"
    "\n"
    "\tsbitmap_queue_min_shallow_depth(tags->bitmap_tags, dd->async_depth);\n"
)

_G_DD_DEPTH_NEW = (
    "\tstruct request_queue *q = hctx->queue;\n"
    "\tstruct deadline_data *dd = q->elevator->elevator_data;\n"
    "\tstruct blk_mq_tags *tags = hctx->sched_tags;\n"
    "\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: blk_mq_async_depth -- dd->async_depth is now\n"
    "\t * the queue-level async depth (a request count); dd_limit_depth()\n"
    "\t * converts it per hctx.  The minimum shallow depth therefore drops to\n"
    "\t * one word bit, which is the smallest depth the conversion can produce.\n"
    "\t */\n"
    "\tdd->async_depth = q->async_depth;\n"
    "\n"
    "\tsbitmap_queue_min_shallow_depth(tags->bitmap_tags, 1);\n"
)

_G_DD_INIT = (
    "\tq->elevator = eq;\n"
    "\treturn 0;\n"
)

_G_DD_INIT_NEW = (
    "\tq->elevator = eq;\n"
    "\t/* ABK stable_515_backport: blk_mq_async_depth -- a freshly installed\n"
    "\t * mq-deadline is unrestricted; dd_depth_updated() reads this back. */\n"
    "\tq->async_depth = q->nr_requests;\n"
    "\treturn 0;\n"
)


_H_BFQ_DECL = "\tunsigned int i, j, min_shallow = UINT_MAX;\n"

_H_BFQ_DECL_NEW = (
    "\tunsigned int i, j, min_shallow = UINT_MAX;\n"
    "\t/* ABK stable_515_backport: blk_mq_async_depth -- per-word async budget,\n"
    "\t * derived from the queue-level async depth below. */\n"
    "\tunsigned int depth;\n"
)

_H_BFQ_WORD00 = "\tbfqd->word_depths[0][0] = max((1U << bt->sb.shift) >> 1, 1U);\n"

_H_BFQ_WORD00_NEW = (
    "\t/*\n"
    "\t * ABK stable_515_backport: blk_mq_async_depth.  q->async_depth is a\n"
    "\t * request count clamped to q->nr_requests, while word_depths[] are\n"
    "\t * per-word bit caps: sbitmap_get_shallow() caps each word at\n"
    "\t * min(word->depth, shallow_depth).  Scale into the 1..(1 << shift)\n"
    "\t * window the allocator expects -- the same conversion mq-deadline's\n"
    "\t * dd_to_word_depth() performs -- then apply bfq's documented ratios.\n"
    "\t */\n"
    "\tdepth = ((bfqd->queue->async_depth << bt->sb.shift) +\n"
    "\t\t bfqd->queue->nr_requests - 1) / bfqd->queue->nr_requests;\n"
    "\tbfqd->word_depths[0][0] = max(depth >> 1, 1U);\n"
)

_H_BFQ_WORD01 = "\tbfqd->word_depths[0][1] = max(((1U << bt->sb.shift) * 3) >> 2, 1U);\n"

_H_BFQ_WORD01_NEW = "\tbfqd->word_depths[0][1] = max((depth * 3) >> 2, 1U);\n"

_H_BFQ_WORD10 = "\tbfqd->word_depths[1][0] = max(((1U << bt->sb.shift) * 3) >> 4, 1U);\n"

_H_BFQ_WORD10_NEW = "\tbfqd->word_depths[1][0] = max((depth * 3) >> 4, 1U);\n"

_H_BFQ_WORD11 = "\tbfqd->word_depths[1][1] = max(((1U << bt->sb.shift) * 6) >> 4, 1U);\n"

_H_BFQ_WORD11_NEW = "\tbfqd->word_depths[1][1] = max((depth * 6) >> 4, 1U);\n"

_H_BFQ_INIT = "\tbfqd->queue = q;\n"

_H_BFQ_INIT_NEW = (
    "\tbfqd->queue = q;\n"
    "\t/* ABK stable_515_backport: blk_mq_async_depth -- bfq's async budget is\n"
    "\t * 75% of the queue depth (the remaining 25% stays for synchronous\n"
    "\t * requests); bfq_update_depths() turns it into its four word depths. */\n"
    "\tq->async_depth = (q->nr_requests * 3) >> 2;\n"
)


_I_KYBER_DEPTH = (
    "\tstruct kyber_queue_data *kqd = hctx->queue->elevator->elevator_data;\n"
    "\tstruct blk_mq_tags *tags = hctx->sched_tags;\n"
    "\tunsigned int shift = tags->bitmap_tags->sb.shift;\n"
    "\n"
    "\tkqd->async_depth = (1U << shift) * KYBER_ASYNC_PERCENT / 100U;\n"
    "\n"
    "\tsbitmap_queue_min_shallow_depth(tags->bitmap_tags, kqd->async_depth);\n"
)

_I_KYBER_DEPTH_NEW = (
    "\tstruct request_queue *q = hctx->queue;\n"
    "\tstruct kyber_queue_data *kqd = q->elevator->elevator_data;\n"
    "\tstruct blk_mq_tags *tags = hctx->sched_tags;\n"
    "\tunsigned int shift = tags->bitmap_tags->sb.shift;\n"
    "\n"
    "\t/*\n"
    "\t * ABK stable_515_backport: blk_mq_async_depth.  q->async_depth is a\n"
    "\t * request count while data->shallow_depth (fed from kqd->async_depth\n"
    "\t * by kyber_limit_depth()) is a per-word bit cap, so scale it into the\n"
    "\t * 1..(1 << shift) window sbitmap_get_shallow() expects -- the same\n"
    "\t * conversion mq-deadline's dd_to_word_depth() performs.  With the\n"
    "\t * default (KYBER_ASYNC_PERCENT % of q->nr_requests) this is identical\n"
    "\t * to the 5.15 formula it replaces.\n"
    "\t */\n"
    "\tkqd->async_depth = ((q->async_depth << shift) + q->nr_requests - 1) /\n"
    "\t\t\t   q->nr_requests;\n"
    "\n"
    "\tsbitmap_queue_min_shallow_depth(tags->bitmap_tags, kqd->async_depth);\n"
)

_I_KYBER_INIT = (
    "\teq->elevator_data = kqd;\n"
    "\tq->elevator = eq;\n"
    "\n"
    "\treturn 0;\n"
    "}\n"
)

_I_KYBER_INIT_NEW = (
    "\teq->elevator_data = kqd;\n"
    "\tq->elevator = eq;\n"
    "\t/* ABK stable_515_backport: blk_mq_async_depth -- kyber reserves a\n"
    "\t * KYBER_ASYNC_PERCENT share of the queue depth for async requests. */\n"
    "\tq->async_depth = q->nr_requests * KYBER_ASYNC_PERCENT / 100;\n"
    "\n"
    "\treturn 0;\n"
    "}\n"
)


def build_blk_mq_async_depth_steps():
    return [
        (BLKDEV_H, _A_KABI, _A_KABI_NEW, T),
        (BLK_CORE_C, _B_DEFAULT, _B_DEFAULT_NEW, T),
        (BLK_MQ_C, _C_ALLOC_HEAD, _C_ALLOC_HEAD_NEW, T),
        (BLK_MQ_C, _C_LIMIT, _C_LIMIT_NEW, T),
        (BLK_MQ_C, _C_GET_TAG, _C_GET_TAG_NEW, T),
        (BLK_MQ_C, _C_QUEUE_INIT, _C_QUEUE_INIT_NEW, T),
        (BLK_MQ_C, _C_RESIZE, _C_RESIZE_NEW, T),
        (BLK_MQ_SCHED_C, _D_SCHED_NONE, _D_SCHED_NONE_NEW, T),
        (BLK_MQ_SCHED_C, _D_SCHED_DEFAULT, _D_SCHED_DEFAULT_NEW, T),
        (BLK_SYSFS_C, _E_SYSFS_ANCHOR, _E_SYSFS_FUNCS_NEW, T),
        (BLK_SYSFS_C, _E_SYSFS_ENTRY, _E_SYSFS_ENTRY_NEW, T),
        (BLK_SYSFS_C, _E_SYSFS_ATTR, _E_SYSFS_ATTR_NEW, T),
        (ELEVATOR_C, _F_ELEVATOR, _F_ELEVATOR_NEW, T),
        (MQ_DEADLINE_C, _G_DD_HELPER, _G_DD_HELPER_NEW, T),
        (MQ_DEADLINE_C, _G_DD_SHALLOW, _G_DD_SHALLOW_NEW, T),
        (MQ_DEADLINE_C, _G_DD_DEPTH, _G_DD_DEPTH_NEW, T),
        (MQ_DEADLINE_C, _G_DD_INIT, _G_DD_INIT_NEW, T),
        (BFQ_C, _H_BFQ_DECL, _H_BFQ_DECL_NEW, T),
        (BFQ_C, _H_BFQ_WORD00, _H_BFQ_WORD00_NEW, T),
        (BFQ_C, _H_BFQ_WORD01, _H_BFQ_WORD01_NEW, T),
        (BFQ_C, _H_BFQ_WORD10, _H_BFQ_WORD10_NEW, T),
        (BFQ_C, _H_BFQ_WORD11, _H_BFQ_WORD11_NEW, T),
        (BFQ_C, _H_BFQ_INIT, _H_BFQ_INIT_NEW, T),
        (KYBER_C, _I_KYBER_DEPTH, _I_KYBER_DEPTH_NEW, T),
        (KYBER_C, _I_KYBER_INIT, _I_KYBER_INIT_NEW, T),
    ]


def _blk_mq_async_depth_apply(ctx):
    probes = (
        (BLKDEV_H, "struct request_queue {", "struct request_queue"),
        (BLKDEV_H, "\tANDROID_KABI_RESERVE(4);", "request_queue KABI reserve run"),
        (BLK_CORE_C, "q->nr_requests = BLKDEV_MAX_RQ;", "blk_alloc_queue() default"),
        (BLK_MQ_C, "static struct request *__blk_mq_alloc_request(struct blk_mq_alloc_data *data)",
         "__blk_mq_alloc_request()"),
        (BLK_MQ_C, "int blk_mq_update_nr_requests(struct request_queue *q, unsigned int nr)",
         "blk_mq_update_nr_requests()"),
        (BLK_MQ_SCHED_C, "int blk_mq_init_sched(struct request_queue *q, struct elevator_type *e)",
         "blk_mq_init_sched()"),
        (BLK_SYSFS_C, 'QUEUE_RW_ENTRY(queue_requests, "nr_requests");',
         "queue/nr_requests attribute"),
        (BLK_SYSFS_C,
         "queue_ra_store(struct request_queue *q, const char *page, size_t count)",
         "queue_ra_store()"),
        (ELEVATOR_C, "int elevator_switch_mq(struct request_queue *q,", "elevator_switch_mq()"),
        (MQ_DEADLINE_C, "static void dd_limit_depth(unsigned int op, struct blk_mq_alloc_data *data)",
         "dd_limit_depth()"),
        (MQ_DEADLINE_C, "static void dd_depth_updated(struct blk_mq_hw_ctx *hctx)",
         "dd_depth_updated()"),
        (BFQ_C, "static unsigned int bfq_update_depths(struct bfq_data *bfqd,",
         "bfq_update_depths()"),
        (KYBER_C, "static void kyber_depth_updated(struct blk_mq_hw_ctx *hctx)",
         "kyber_depth_updated()"),
        (KYBER_C, "eq->elevator_data = kqd;", "kyber_init_sched() tail"),
    )
    for rel, probe, why in probes:
        try:
            text = ctx.read(rel)
        except FileNotFoundError:
            return "blocked_by_shape", f"{rel}: file absent"
        if probe not in text:
            return "blocked_by_shape", f"{why} not found in {rel}"
    status, _results, detail = apply_steps(ctx, build_blk_mq_async_depth_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    return [
        PatchGroup(
            "blk_mq_async_depth",
            "queue-level blk-mq async tag depth: blk_mq_limit_depth() runs the "
            "elevator's limit_depth() after the hctx mapping, "
            "request_queue.async_depth lives in KABI slot 1, queue/async_depth "
            "reads/writes it (clamped to nr_requests), blk_mq_update_nr_requests() "
            "keeps it proportional, and mq-deadline/bfq/kyber consume it "
            "(absorbed from ABK_ABI_PATCH_SUITE blk_mq_async_depth; see the "
            "module docstring for the two 5.15 unit conversions)",
            [
                "ABK_ABI_PATCH_SUITE scripts/abk_feature_porting.py "
                "patch_blk_mq_async_depth() / collect_blk_async_depth_status(), "
                "group blk_mq_async_depth (feature source 7.0.12); absorbed per "
                "docs/survey_suite_absorption.md",
                "read-only lineage check: the mq-deadline half "
                "(dd_to_word_depth() + sbitmap_queue_min_shallow_depth(..., 1)) "
                "is byte-identical to the Android 6.1 tree (common-6.1-src, "
                "SUBLEVEL 176) modulo the 5.15 pointer form of bitmap_tags; the "
                "queue-level q->async_depth member is the suite's extension "
                "(absent from common-6.1-src 6.1.176 and common-6.6-src 6.6.142)",
            ],
            [
                BLKDEV_H,
                BLK_CORE_C,
                BLK_MQ_C,
                BLK_MQ_SCHED_C,
                BLK_SYSFS_C,
                ELEVATOR_C,
                MQ_DEADLINE_C,
                BFQ_C,
                KYBER_C,
            ],
            _blk_mq_async_depth_apply,
        ),
    ]
