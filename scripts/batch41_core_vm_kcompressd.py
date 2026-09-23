# -*- coding: utf-8 -*-
"""Batch 41: kcompressd -- kswapd's swap-out compression moved off its thread.

Kcompressd-Unofficial 0.5 (Masahito Suzuki <firelzrd@gmail.com>, forked from
MediaTek's Kcompressd by Qun-Wei Lin) is distributed as patches for three
kernels only: 6.12.44, 6.18 and 7.1-rc1.  There is no 5.15 patch and there
cannot be one in that shape, because the carrier the upstream chose is a
struct pglist_data field run and android13-5.15's GKI ABI pins that struct
completely.  So this batch is a re-carriage rather than a backport, and it
lands as one new PatchGroup, ``vm_kcompressd_swapout``, with the whole payload
in mm/page_io.c.

The feature.  kswapd spends its reclaim scan inside swap_writepage() waiting
for zram to compress every anonymous page it writes out.  The patch moves that
wait onto a per-node kcompressd%d kthread: swap_writepage() queues the page
and returns, kcompressd does the compression and the writeback.  This is *not*
what Batch 10-1 does.  That one offloads the second compression of an already
resident page (``zram_async_recompress`` -- an already-used-memory saving);
this offloads the first compression of a page being reclaimed (a
reclaim-latency / allocation-stall saving).  Neither covers the other's case,
and plan.md records "kcompressd was superseded by Batch 10-1" as the wrong call
it was.

Graft-boundary contract.  Everything lands on pristine text in a file no other
group writes -- mm/page_io.c appears in zero groups' file lists -- so no later
group can edit this group's blocks and no trap-5 ordering is involved:

  * the include block, right after the pristine ``sched/task.h`` include;
  * the offload call inside swap_writepage(), anchored on the
    ``ret = __swap_writepage(...)`` / ``out:`` / ``return ret;`` tail, which is
    byte-identical on all four baselines -- unlike the frontswap_store() block
    above it, which android13-5.15-lts grew a vendor hook in;
  * the engine, appended after the file's last function, so everything it
    calls is already declared and no forward declaration has to be invented.

Only mm/page_io.c is touched: no struct pglist_data field, no mm/vmscan.c, no
include/linux/swap.h extern, no Kconfig edit and no new Kconfig symbol, so
nothing lands in _MODULE_CONFIGS or _INTRODUCED_KCONFIG either.

The four deviations from upstream, each forced rather than chosen, are spelled
out in the C banner this module emits: private per-node state instead of
pglist_data members; boot-time node set-up from an initcall instead of
kswapd_run() (with the hotplug half picked up by a memory notifier, see below);
a file-static knob registered with register_sysctl() instead of an entry in
kernel/sysctl.c's vm_table[]; and the page ABI plus the three-argument
__swap_writepage().  Three further corrections are the ones worth knowing:

  * abk_kcompressd_do_swapout() takes the page lock.  Upstream does not, and
    the reason is a fact about 5.15 that also holds on 6.12:
    shrink_page_list()'s cannot_free branch falls into keep_locked:, which
    *unlocks* the page and puts it back on the LRU, so by the time kcompressd
    runs nobody holds PG_locked -- while __swap_writepage() is a ->writepage
    implementation and every one of its exits unlocks.  Upstream 0.5 carries
    that latent double-unlock; it survives only because unlock_page()'s
    VM_BUG_ON_PAGE(!PageLocked(page)) needs CONFIG_DEBUG_VM to be visible.
  * mem_cgroup_zswap_writeback_enabled() does not exist here, so that gate is
    replaced by this module's own Batch-38 policy, memcg_is_dying()
    (include/linux/memcontrol.h, already reachable through the swap.h include
    mm/page_io.c has), which is both a stricter and a better-motivated refusal
    than the upstream one.
  * zswap_is_enabled() does not exist here either -- 5.15 predates the
    zswap_enabled() rename -- so the "synchronously efficient swap device"
    gate tests frontswap_enabled(), the 5.15 spelling of the same question.
    On a zram-only Android with no zswap, SWP_SYNCHRONOUS_IO is what opens the
    gate, and it is set: mm/swapfile.c:3252 tests p->bdev->bd_disk->fops->
    rw_page and zram implements .rw_page.  Without that the patch would be
    inert on every device this module targets.

The one per-baseline text variant is AOSP's
android_vh_shrink_page_lock_owner_clear() vendor hook, which the lts branch
grew at the swap_writepage() unlock points together with its DECLARE_HOOK in
include/trace/hooks/vmscan.h.  It is absent from 167/178/194 entirely, so
there is nothing to compile against and no CONFIG gate can express it: the
shape is probed in abk_stable_core._vm_kcompressd_swapout_apply and the engine
text follows.  A baseline that matches neither shape reports
blocked_by_shape and writes nothing, because an offload that dropped a
vendor-hook contract the tree does honour is worse than no offload.
"""

import re

__all__ = ["build_steps", "DECL_ANCHOR", "HOOK_PROBE", "PLAIN_PROBE", "HOOK_BODY",
           "PLAIN_BODY", "probe_swapout_shape", "swapout_body", "T"]

T = True

PAGE_IO = "mm/page_io.c"


def _tabs(text):
    """Convert leading 4-space indentation (readable source) to kernel tabs."""
    text = re.sub(r"(?m)^    +",
                  lambda m: "\t" * (len(m.group(0)) // 4), text)
    return text.rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# 1) mm/page_io.c: the includes the engine needs, on pristine include text.
#
# The pristine list is mm.h, kernel_stat.h, gfp.h, pagemap.h, swap.h, bio.h,
# swapops.h, buffer_head.h, writeback.h, frontswap.h, blkdev.h, psi.h, uio.h
# and sched/task.h.  `#include <linux/sched/task.h>` is its own line and
# occurs once, so replace_once cannot take the anchor twice.  Five of these
# arrive transitively today (mm.h pulls mmzone.h, which pulls nodemask.h and
# numa.h for MAX_NUMNODES; sysctl.h pulls wait.h); they are named anyway,
# because what the engine uses should not be an accident of the include
# closure.
#
# <linux/memory.h> is the one that is genuinely new: it carries
# register_memory_notifier() (the stubbed version when
# !CONFIG_MEMORY_HOTPLUG_SPARSE, in either case safe to call), struct
# memory_notify, the MEM_ONLINE/MEM_OFFLINE actions, NOTIFY_OK and struct
# notifier_block.  Nothing heavier is needed: the node-lifecycle code touches
# no part of <linux/memory_hotplug.h> that is gated behind
# CONFIG_MEMORY_HOTPLUG.
# ---------------------------------------------------------------------------

_C_INC_OLD = (
    "#include <linux/sched/task.h>\n"
    "\n"
)

_C_INC_NEW = (
    "#include <linux/sched/task.h>\n"
    "#include <linux/kfifo.h>\n"
    "#include <linux/kthread.h>\n"
    "#include <linux/memory.h>\n"
    "#include <linux/nodemask.h>\n"
    "#include <linux/sysctl.h>\n"
    "#include <linux/wait.h>\n"
    "\n"
)

# ---------------------------------------------------------------------------
# 2) mm/page_io.c: the offload call, inside swap_writepage().
#
# Placement is upstream's: after frontswap_store() has failed -- so zswap has
# already declined the page and cannot be bypassed -- and before
# __swap_writepage().  try_to_free_swap() and arch_prepare_to_swap() stay
# above it, so the swap-slot bookkeeping and the AOP_WRITEPAGE_ACTIVATE return
# are unchanged, and the writeback accounting stays inside __swap_writepage().
#
# The anchor is the function's tail rather than the frontswap_store() block,
# because that block is the one place in this function the four baselines
# disagree: android13-5.15-lts grew a shrink_page_lock_owner_clear() vendor hook
# into it (and a `#undef CREATE_TRACE_POINTS` +
# `#include <trace/hooks/vmscan.h>` pair after the include list).  The tail is
# byte-identical on all four, so one step covers every baseline.  The
# `ret = __swap_writepage(...)` line is unique in the file too -- the only
# other occurrence of __swap_writepage is its definition, which is not
# preceded by `ret =` -- so replace_once cannot match it twice.
# ---------------------------------------------------------------------------

_C_OFFLOAD_OLD = (
    "\tret = __swap_writepage(page, wbc, end_swap_bio_write);\n"
    "out:\n"
    "\treturn ret;\n"
    "}\n"
)

_C_OFFLOAD_NEW = (
    "\t/* ABK stable_515_backport: Batch 41 -- hand this page's\n"
    "\t * compression to the node's kcompressd thread. */\n"
    "\tif (abk_kcompressd_store(page))\n"
    "\t\treturn 0;\n"
    "\n"
) + _C_OFFLOAD_OLD

# ---------------------------------------------------------------------------
# 2b) mm/page_io.c: the forward declaration swap_writepage() needs.
#
# The engine is appended at the end of the file, i.e. *after* swap_writepage(),
# so the call above precedes the definition by roughly 500 lines.  Upstream never
# hits this -- its patch inserts do_swapout()/kcompressd_store() in a hunk
# *above* the swap_writepage() hunk -- but appending means C requires a
# prototype.  Without it: "implicit declaration of function
# 'abk_kcompressd_store'" and then "static declaration follows non-static
# declaration", both -Werror.  That is what the ABK CI compile gate found on the
# first run of this batch, and no text audit can see it: step_audit checks
# comment/brace/#ifdef balance and idempotency, implementation_audit checks
# content -- neither runs a compiler (AGENTS.md trap 6).
#
# Anchored on the function's signature, which is unique in the file.  `struct
# page` is only forward-declared this early, which is fine for a prototype, and
# bool comes from <linux/types.h> via mm.h.
# ---------------------------------------------------------------------------

# The same anchor spelled out on its own so the unit test can compare against it:
# an edit to _C_DECL_OLD that stops being "the doc comment + the signature"
# would otherwise silently produce the comment-splitting shape.
DECL_ANCHOR = (
    "/*\n"
    " * We may have stale swap cache pages in memory: notice\n"
    " * them here and get rid of the unnecessary final write.\n"
    " */\n"
    "int swap_writepage(struct page *page, struct writeback_control *wbc)\n"
    "{\n"
)

_C_DECL_OLD = (
    "/*\n"
    " * We may have stale swap cache pages in memory: notice\n"
    " * them here and get rid of the unnecessary final write.\n"
    " */\n"
    "int swap_writepage(struct page *page, struct writeback_control *wbc)\n"
    "{\n"
)

_C_DECL_NEW = (
    "/* ABK stable_515_backport: Batch 41 -- defined below, with the engine. */\n"
    "static bool abk_kcompressd_store(struct page *page);\n"
    "\n"
    "/*\n"
    " * We may have stale swap cache pages in memory: notice\n"
    " * them here and get rid of the unnecessary final write.\n"
    " */\n"
    "int swap_writepage(struct page *page, struct writeback_control *wbc)\n"
    "{\n"
)

# ---------------------------------------------------------------------------
# 3) mm/page_io.c: the engine, appended after the file's last function.
#
# swap_set_page_dirty() is the tail of mm/page_io.c on every baseline and is
# byte-identical there, so the anchor needs no shape handling at all.  It
# carries the whole function so the appended text is guaranteed fresh (trap 1)
# and so the second pass finds the replacement already present.
#
# The text below is the 167/178/194 variant, with no vendor hook.  216 calls
# android_vh_shrink_page_lock_owner_clear() here, and that call inside the
# replicated frontswap branch is the only per-baseline variance in the whole
# payload -- a text variant, not a CONFIG gate, because the hook is absent from
# 167/178/194 outright (its DECLARE_HOOK is not in include/trace/hooks/vmscan.h
# there), so there is nothing to compile against and nothing to gate on.
# ---------------------------------------------------------------------------

# The two shapes of swap_writepage()'s frontswap branch, exactly as they appear in
# the tree -- the shape probe, and the only place in this group's payload where
# the baselines differ.  Each is unique in the file (it only occurs in
# swap_writepage()).
#
# android13-5.15-lts (216): AOSP's android_vh_shrink_page_lock_owner_clear()
# call.  It is there because the DECLARE_HOOK in include/trace/hooks/vmscan.h
# arrived in the same change, which is also why the hook variant needs no extra
# include step -- that branch's page_io.c already carries `#undef
# CREATE_TRACE_POINTS` + `#include <trace/hooks/vmscan.h>` after the pristine
# include list, and the engine sits in that same translation unit.
HOOK_PROBE = (
    '\tif (frontswap_store(page) == 0) {\n'
    '\t\tset_page_writeback(page);\n'
    '\t\ttrace_android_vh_shrink_page_lock_owner_clear(page);\n'
    '\t\tunlock_page(page);\n'
    '\t\tend_page_writeback(page);\n'
    '\t\tgoto out;\n'
    '\t}\n'
)

# 167 / 178 / 194: no vendor hook in the tree at all, so the replicated branch
# carries none -- a call with no DECLARE_HOOK behind it would not compile there.
PLAIN_PROBE = (
    '\tif (frontswap_store(page) == 0) {\n'
    '\t\tset_page_writeback(page);\n'
    '\t\tunlock_page(page);\n'
    '\t\tend_page_writeback(page);\n'
    '\t\tgoto out;\n'
    '\t}\n'
)

# The emitted do_swapout() body.  Upstream writes it as an if/else, so this does
# too -- and unlike the probe it drops the pristine `goto out;`, because the
# __swap_writepage() half and the frontswap half then share the counter and the
# put_page() rather than each exiting on its own.
HOOK_BODY = (
    '\n'
    '\tif (frontswap_store(page) == 0) {\n'
    '\t\tset_page_writeback(page);\n'
    '\t\ttrace_android_vh_shrink_page_lock_owner_clear(page);\n'
    '\t\tunlock_page(page);\n'
    '\t\tend_page_writeback(page);\n'
    '\t} else {\n'
    '\t\t/* Implies unlock_page(page). */\n'
    '\t\t__swap_writepage(page, &wbc, end_swap_bio_write);\n'
    '\t}\n'
    '\n'
    '\tatomic_long_inc(&abk_kcompressd_swapped);\n'
    '\t/* Drop the reference abk_kcompressd_enqueue() took for this page. */\n'
    '\tput_page(page);\n'
    '\n'
)

# 167/178/194: the same text minus the hook line.
PLAIN_BODY = (
    '\n'
    '\tif (frontswap_store(page) == 0) {\n'
    '\t\tset_page_writeback(page);\n'
    '\t\tunlock_page(page);\n'
    '\t\tend_page_writeback(page);\n'
    '\t} else {\n'
    '\t\t/* Implies unlock_page(page). */\n'
    '\t\t__swap_writepage(page, &wbc, end_swap_bio_write);\n'
    '\t}\n'
    '\n'
    '\tatomic_long_inc(&abk_kcompressd_swapped);\n'
    '\t/* Drop the reference abk_kcompressd_enqueue() took for this page. */\n'
    '\tput_page(page);\n'
    '\n'
)


_ENGINE = _tabs(r"""
/* ========================================================================
 * ABK stable_515_backport: Batch 41 -- kcompressd swap-out offload.
 *
 * Kcompressd-Unofficial 0.5 (Masahito Suzuki, forked from MediaTek's
 * Kcompressd by Qun-Wei Lin) moves kswapd's compress-and-swap-out work out
 * of swap_writepage() and onto a per-node kcompressd kthread, so reclaim
 * latency stops including the compression of every anonymous page kswapd
 * writes.  Four things about this tree keep it from looking like the patch:
 *
 *   1. pglist_data is off limits.  Upstream adds wait_queue_head_t
 *      kcompressd_wait, struct task_struct *kcompressd, struct kfifo
 *      kcompress_fifo and spinlock_t kcompress_fifo_lock to struct
 *      pglist_data.  android/abi_gki_aarch64.xml pins that struct at 56320
 *      bits across 22 members with a layout offset on each of them, and
 *      there is no ANDROID_KABI_RESERVE run to spend -- the four spare slots
 *      are in struct zone, and pglist_data's one free-looking member,
 *      ANDROID_OEM_DATA(1), is a single u64 behind
 *      CONFIG_ANDROID_VENDOR_OEM_DATA and belongs to the OEM anyway.  So the
 *      per-node state is a private static array indexed by page_to_nid(),
 *      the way Batch 10-1 carries per-device state in a private map rather
 *      than growing struct zram.
 *   2. mm/vmscan.c is not touched.  Upstream creates the thread in
 *      kswapd_run() and tears it down in kswapd_stop(); this tree's
 *      kswapd_run() is void, has no __meminit and no pgdat_kswapd_lock(),
 *      and its trace_android_vh_kswapd_per_node() can skip the body before
 *      the thread would have been created.  One late_initcall over the
 *      online nodes replaces both, and never runs twice.
 *   3. There is no global to declare.  include/linux/swap.h gains no extern
 *      (it already carries four groups' text, and a new global would add a
 *      symbol to vmlinux); the knob is file-static here and is registered
 *      with register_sysctl() instead of as an entry in kernel/sysctl.c's
 *      vm_table[].
 *   4. 5.15 is page-based, not folio-based; its __swap_writepage() takes
 *      three arguments and its own bio end-io function; zswap is reached
 *      through frontswap_store() (already what swap_writepage() calls here)
 *      rather than a direct zswap_store(); and
 *      mem_cgroup_zswap_writeback_enabled() does not exist.
 *
 * Kcompressd-Unofficial 0.5 by Masahito Suzuki (forked from Kcompressd by
 * Qun-Wei Lin from MediaTek).
 * ===================================================================== */

/*
 * Soft limit behind /proc/sys/vm/kcompressd.  Zero switches the offload off
 * and the tree behaves exactly like an unpatched kernel; a non-zero value is
 * NOT the queue's capacity -- the ring is always ABK_KCOMPRESS_FIFO_SIZE
 * entries, and this is how many queued pages the head-drain strategy starts
 * at.  Same range (0..256) and same default (24) as upstream.
 */
#define ABK_KCOMPRESS_FIFO_SIZE 256

static int abk_kcompressd_threshold = 24;

/*
 * Read-only counters, /proc/sys/vm/kcompressd_{enqueued,swapped,sync}.
 * Upstream ships none, but Batch 8 showed what an offload that reports
 * applied without doing anything looks like, so the three numbers that tell
 * the two apart are observable: enqueued - swapped is the live queue depth,
 * and sync is the volume the caller wrote itself.  Atomic because they are
 * incremented from more than one node's context; atomic_long_t's value is its
 * first member, so .data can point straight at the variable.
 */
static atomic_long_t abk_kcompressd_enqueued;
static atomic_long_t abk_kcompressd_swapped;
static atomic_long_t abk_kcompressd_sync;

/*
 * Per-node state: upstream's four pglist_data members.  Zero-initialised BSS
 * of MAX_NUMNODES entries -- arm64's NODES_SHIFT is 4 when CONFIG_NUMA is on
 * and MAX_NUMNODES collapses to 1 when it is off, so the whole array is a
 * couple of kilobytes either way.  Every field starts out "no thread, no
 * queue", which is exactly the degraded state, so teardown has nothing to
 * intersect with and there is no partial-init shape to recover from.
 */
struct abk_kcompressd_node {
	wait_queue_head_t	wait;
	struct task_struct	*task;
	struct kfifo		fifo;
	spinlock_t		lock;
};

static struct abk_kcompressd_node abk_kcompressd_nodes[MAX_NUMNODES];

/*
 * abk_kcompressd_do_swapout() - write one page out to swap.
 * @page: the page abk_kcompressd_enqueue() queued, or the head it drained.
 *
 * A line-for-line copy of the tail of swap_writepage(): the same
 * writeback_control pageout() builds, frontswap_store() first -- zswap is a
 * frontswap backend here, so this is upstream's zswap_store() branch -- and
 * __swap_writepage() otherwise.  Only the page ABI and the three-argument
 * __swap_writepage() differ from upstream.
 *
 * The vendor hook below replicates the one swap_writepage() calls at the same
 * point; the __swap_writepage() half needs no replica, because the hooks that
 * path carries (mm/page_io.c:266 and :308, and block/bdev.c:380 inside
 * bdev_write_page()) are inside the callee and travel with the call.
 */
static void abk_kcompressd_do_swapout(struct page *page)
{
	struct writeback_control wbc = {
		.sync_mode	= WB_SYNC_NONE,
		.nr_to_write	= SWAP_CLUSTER_MAX,
		.range_start	= 0,
		.range_end	= LLONG_MAX,
		.for_reclaim	= 1,
	};

	/*
	 * try_to_free_swap() (mm/swapfile.c) tests page_swapped() and knows
	 * nothing about this queue, so a path that frees the swap slot while
	 * the page sits in the FIFO would have this write land in a slot
	 * another page may since have taken.  Write nothing instead: the page
	 * keeps its data and no other page's swap-out is corrupted.  No path
	 * that reaches here has been shown to do this -- see the risk note in
	 * the Batch 41 report.
	 *
	 * pr_warn_once(), deliberately not a check from the WARN family: this is
	 * a check for a condition we believe unreachable, and the *action* on
	 * reaching it (skip the write) is already the safe one.  A WARN-family
	 * check is an oops, and a device booted with
	 * panic_on_oops/panic_on_warn would turn our own "impossible" guard into
	 * a panic -- strictly worse than the condition it guards.  A dmesg line
	 * is enough to find out that it happened, and the once form means it
	 * cannot become a hot-path cost either.
	 */
	if (unlikely(!PageSwapCache(page))) {
		pr_warn_once("kcompressd: dropping a queued page whose swap slot was freed (swap entry %#lx)\n",
			     (unsigned long)page_private(page));
		put_page(page);
		return;
	}

	/*
	 * Not in upstream, and not optional here.  An offloaded page cannot be
	 * freed on the pass that queued it: __remove_mapping()'s
	 * page_ref_freeze(page, 1 + compound_nr(page)) wants the refcount to
	 * match exactly, and abk_kcompressd_enqueue()'s get_page() makes it one
	 * too many.  That is deliberate -- without the reference,
	 * __delete_from_swap_cache() would hand the slot back while this page
	 * still has a write pending -- but it means shrink_page_list() takes
	 * cannot_free: and falls into keep_locked: (mm/vmscan.c:1874), which
	 * *releases the page lock* and puts the page back on the LRU.  So by
	 * the time this runs nobody holds PG_locked, while __swap_writepage()
	 * is a ->writepage implementation whose every exit unlocks the page,
	 * and unlock_page() has a VM_BUG_ON_PAGE(!PageLocked(page)) that needs
	 * CONFIG_DEBUG_VM to be visible.  Taking the lock restores the contract
	 * the synchronous path relies on.  6.12's keep_locked:
	 * (mm/vmscan.c:1531) unlocks the same way, so upstream 0.5 carries this
	 * latent double-unlock and this graft does not.
	 *
	 * The page cannot have been freed under us: this path owns a reference.
	 * It can be locked by whoever took it after keep_locked: let go -- a
	 * fault mapping it back in, or a later reclaim pass -- and lock_page()
	 * waits for them, which is what the synchronous path does too.
	 */
	lock_page(page);

@@ABK_KCOMPRESS_SWAPOUT_BODY@@
}

/*
 * abk_kcompressd_enqueue() - queue one page for the node's kcompressd.
 * @kcd: the node's state, already validated by the caller.
 * @page: the page to queue; the caller holds the page's lock and the LRU's
 * reference.
 *
 * Returns true when the page is in the queue, false when it is not and the
 * caller has to write it itself.
 *
 * Queue discipline, and the part that makes this version worth porting.
 * MediaTek's original refused the *new* page when the queue was full and
 * left the queued ones waiting, which reversed completion order against
 * submission order and could starve the oldest pages indefinitely.
 * Unofficial 0.5 does the opposite: at the soft limit it dequeues the oldest
 * page first, writes that one out synchronously on the caller's thread, and
 * only then makes room for the new one.  Completion order then matches
 * submission order, and the queue pushes back towards the synchronous path
 * instead of growing -- once it is full, every further page costs one older
 * page's write, so an overloaded node degrades towards first-come-first-
 * served rather than towards a delay.
 */
static bool abk_kcompressd_enqueue(struct abk_kcompressd_node *kcd,
				   struct page *page)
{
	struct page *head = NULL;
	unsigned long flags;
	unsigned int ret;

	/*
	 * One lock for the ring and for nothing else, and the node's lifetime is
	 * decided under it: the kcd->task read in abk_kcompressd_store() is a
	 * plain unlocked load, so a reader that saw a thread could still be
	 * about to call in here while abk_kcompressd_del_node() frees the ring
	 * underneath it.  Taking the lock for the write makes the two mutually
	 * exclusive instead of merely racy -- after this, any enqueue that
	 * arrives finds kcd->task == NULL here and bails.
	 *
	 * kfifo_out_locked() in the thread takes the same lock, so every ring
	 * operation is serialized against both sides.  The wait queue predicate
	 * reads kfifo_is_empty() unlocked, which is safe the usual wait_event()
	 * way: the sleeper enqueues itself on the queue before it tests the
	 * condition, so a wake_up_interruptible() landing in between cannot be
	 * missed.
	 */
	spin_lock_irqsave(&kcd->lock, flags);
	if (unlikely(!kcd->task)) {
		/* The node was off-lined; nothing may enter a freed ring. */
		spin_unlock_irqrestore(&kcd->lock, flags);
		return false;
	}
	if (kfifo_len(&kcd->fifo) >=
			(unsigned int)abk_kcompressd_threshold * sizeof(page) &&
			unlikely(!kfifo_out(&kcd->fifo, &head, sizeof(page)))) {
		/* Cannot make room; refuse rather than lose the oldest page. */
		spin_unlock_irqrestore(&kcd->lock, flags);
		return false;
	}
	/*
	 * Load-bearing, not an optimisation -- see the keep_locked: note in
	 * abk_kcompressd_do_swapout().  It is what keeps the swap slot alive
	 * (__delete_from_swap_cache()/put_swap_page() only run once the
	 * refcount matches, and this reference makes it not match) and what
	 * keeps the page from being freed out from under the write.
	 */
	get_page(page);
	ret = kfifo_in(&kcd->fifo, &page, sizeof(page));
	if (likely(ret)) {
		atomic_long_inc(&abk_kcompressd_enqueued);
		wake_up_interruptible(&kcd->wait);
	} else {
		/* Enqueue failed; undo the reference rather than leak it. */
		put_page(page);
	}
	spin_unlock_irqrestore(&kcd->lock, flags);

	/*
	 * Written out here, on the caller's thread, before its own page
	 * returns -- the head-drain cost is paid inside the offload, so an
	 * overloaded node slows the reclaim that overloaded it.
	 */
	if (head)
		abk_kcompressd_do_swapout(head);

	return ret != 0;
}

/*
 * abk_kcompressd_store() - off-load one page's compression to kcompressd.
 * @page: the page swap_writepage() is about to write synchronously.
 *
 * True means queued, and swap_writepage() returns 0 (the writeback is
 * kcompressd's job now).  False means the caller writes the page itself,
 * exactly as it would have without this module.
 */
static bool abk_kcompressd_store(struct page *page)
{
	struct abk_kcompressd_node *kcd = &abk_kcompressd_nodes[page_to_nid(page)];
	bool offloaded;

	/*
	 * A node that never got a thread, or a knob at zero, is not a fallback
	 * -- the tree is simply behaving like an unpatched kernel, and counting
	 * these would only turn the counter into a second PSWPOUT.  Not
	 * counted.
	 */
	if (!abk_kcompressd_threshold || unlikely(!kcd->task))
		return false;

	/* Everything below is a refused offload, and is counted. */

	/*
	 * Only kswapd.  This is the one gate that must not be relaxed, and the
	 * reason is not a preference: direct reclaim would take the cannot_free
	 * path above for *every* page, __remove_mapping() would return 0,
	 * nr_reclaimed would never be incremented (it only accumulates at
	 * free_it:), shrink_page_list() would return 0,
	 * do_try_to_free_pages() would never reach sc->nr_to_reclaim, and
	 * __alloc_pages_slowpath() would escalate -- to the OOM killer, on a
	 * system with plenty to reclaim.  kswapd instead re-scans and
	 * converges, because the next pass finds the page freeable once
	 * kcompressd has dropped its reference.
	 */
	if (!current_is_kswapd())
		goto sync;

	/*
	 * ...and not kcompressd itself.  current_is_kswapd() tests
	 * current->flags & PF_KSWAPD, and this module's own drain thread sets
	 * PF_KSWAPD (see the thread below), so the gate above admits kcompressd
	 * as well as kswapd.  Nothing in 5.15's offload path re-enters
	 * swap_writepage() today -- frontswap_store() -> zswap and
	 * __swap_writepage() -> bdev_write_page() -> zram_rw_page() ->
	 * zcomp_compress() all bottom out without it -- so this is a guard
	 * against a future path, not a fix for a live bug.
	 *
	 * What it would be if it fired: the enqueue puts the page back in the
	 * FIFO this very thread is draining, the drain loop in
	 * kfifo_out_locked() never empties, so it neither sleeps nor exits, and
	 * every round pins one more page -- a livelock that ends in the OOM
	 * killer, not a panic.  Comparing the task struct directly (rather than
	 * a per-cpu marker) is also migration-safe: a kthread that migrates
	 * between draining pages takes the guard with it.
	 */
	if (unlikely(kcd->task == current))
		goto sync;

	/* Only anonymous pages: a shared page's writeback is not ours to
	 * defer. */
	if (!PageAnon(page))
		goto sync;

	/*
	 * The swap device must be efficiently written synchronously.  On this
	 * branch, this is what keeps the patch from being inert: zram sets
	 * SWP_SYNCHRONOUS_IO because mm/swapfile.c:3252 tests
	 * p->bdev->bd_disk->fops->rw_page and zram implements .rw_page, so a
	 * zram-only Android with no zswap opens the gate right here.  (5.15 has
	 * no zswap_is_enabled(), the 6.12 name for the same question; an
	 * enabled frontswap backend opens the gate instead.)  A slow or
	 * asynchronous device -- network swap, a slow SSD -- falls out: moving
	 * the submission to another thread only moves the wait.
	 */
	if (!frontswap_enabled() &&
	    !data_race(page_swap_info(page)->flags & SWP_SYNCHRONOUS_IO))
		goto sync;

	/*
	 * This module's own reclaim policy from Batch 38 (memcg_dying_bailout):
	 * no reclaim into a memcg cgroup_rmdir() is tearing down.  It is both
	 * stricter and better motivated than the upstream
	 * mem_cgroup_zswap_writeback_enabled() gate it replaces -- that symbol
	 * does not exist on 5.15 -- and it costs nothing new: page_memcg() and
	 * memcg_is_dying() are already reachable through the swap.h include
	 * this file has, memcontrol.h included by it.
	 *
	 * Last, and deliberately: page_memcg() is the only helper in this
	 * function with a precondition on the page's shape (its
	 * VM_BUG_ON_PGFLAGS(PageTail(page), page) is CONFIG_DEBUG_VM_PGFLAGS
	 * only, so it would not panic -- it would read the tail page's
	 * memcg_data as an slub objcg).  Every gate above is shape-agnostic, so
	 * running them first means this helper only ever sees a page that
	 * survived them.  Cosmetic today -- swap_writepage() is only ever called
	 * on a compound head page, so a tail cannot arrive at all.
	 */
	if (memcg_is_dying(page_memcg(page)))
		goto sync;

	offloaded = abk_kcompressd_enqueue(kcd, page);
	if (offloaded)
		return true;
sync:
	atomic_long_inc(&abk_kcompressd_sync);
	return false;
}

/*
 * abk_kcompressd() - the per-node drain thread.
 *
 * PF_MEMALLOC: compressing allocates memory of its own (the crypto/scomp
 * streams, a bounce page), and being reclaimable from inside the compressor
 * would have it wait on itself.  PF_KSWAPD: upstream's own comment says this
 * stops i915_gem_shrinker_scan() from crashing the system; the side effect is
 * that every shrinker then treats kcompressd the way it treats kswapd.
 *
 * No throttling, no batching, no cgroup accounting -- a straight FIFO drain,
 * like upstream.
 */
static int abk_kcompressd(void *p)
{
    int nid = (int)(long)p;
    struct abk_kcompressd_node *kcd = &abk_kcompressd_nodes[nid];
    struct page *page;

    current->flags |= PF_MEMALLOC | PF_KSWAPD;

    while (!kthread_should_stop()) {
        /*
			* kthread_should_stop() is part of the predicate, not just of the
			* loop condition, because abk_kcompressd_del_node() calls
			* kthread_stop(): kthread_stop() sets the flag and waits for this
			* thread to exit, and nothing else will wake it once the FIFO is
			* empty.  Upstream 0.5's predicate is !kfifo_is_empty() only, so
			* kswapd_stop() on a memory-offline node would block on
			* kthread_stop() forever with mem_hotplug_lock held in write mode --
			* a sysfs write that never returns, and every other hotplug operation
			* deadlocked behind it.  (5.15's own kswapd gets away with a
			* stop-unaware predicate only because wakeup_kswapd() fires on every
			* allocation while it sleeps; our thread has no such traffic, so it
			* has to be explicit.)
         */
        wait_event_interruptible(kcd->wait,
                     !kfifo_is_empty(&kcd->fifo) ||
                     kthread_should_stop());

        /*
			* Drain unconditionally, including on the last round.  A queued page
			* holds a reference and a live swap slot, and the node being torn down
			* is exactly the node those pages belong to: dropping them instead of
			* writing them would leave a swap-cache page whose data was never
			* written but whose clean bit is already clear, and no code path
			* re-dirties such a page on our behalf.
         */
        while (kfifo_out_locked(&kcd->fifo, &page, sizeof(page),
                    &kcd->lock))
            abk_kcompressd_do_swapout(page);
    }
    return 0;
}

/*
 * The knob plus the three counters.  register_sysctl("vm", ...) rather than
 * an entry in kernel/sysctl.c's vm_table[]: that is not a file this module
 * otherwise touches.  register_sysctl() sits behind CONFIG_SYSCTL, which
 * fs/proc/Kconfig's PROC_SYSCTL selects and which in turn depends only on the
 * always-on default of PROC_FS; the !CONFIG_SYSCTL fallback is the same inline
 * stub, so a tree without /proc/sys keeps the compiled-in threshold instead
 * of failing to build.  proc_doulongvec_minmax() dereferences extra1/extra2
 * only under a NULL check, so the read-only counters leave them at zero.
 */
static int abk_kcompressd_sysctl_limit = ABK_KCOMPRESS_FIFO_SIZE;

static struct ctl_table abk_kcompressd_sysctl_table[] = {
	{
		.procname	= "kcompressd",
		.data		= &abk_kcompressd_threshold,
		.maxlen		= sizeof(int),
		.mode		= 0644,
		.proc_handler	= proc_dointvec_minmax,
		.extra1		= SYSCTL_ZERO,
		.extra2		= &abk_kcompressd_sysctl_limit,
	},
	{
		.procname	= "kcompressd_enqueued",
		.data		= &abk_kcompressd_enqueued,
		.maxlen		= sizeof(unsigned long),
		.mode		= 0444,
		.proc_handler	= proc_doulongvec_minmax,
	},
	{
		.procname	= "kcompressd_swapped",
		.data		= &abk_kcompressd_swapped,
		.maxlen		= sizeof(unsigned long),
		.mode		= 0444,
		.proc_handler	= proc_doulongvec_minmax,
	},
	{
		.procname	= "kcompressd_sync",
		.data		= &abk_kcompressd_sync,
		.maxlen		= sizeof(unsigned long),
		.mode		= 0444,
		.proc_handler	= proc_doulongvec_minmax,
	},
	{ }
};

/*
 * abk_kcompressd_add_node() - bring the node's kcompressd up.
 * @nid: node to serve.
 *
 * Both the boot-time initcall and the memory-hotplug notifier land here, so
 * "how a node gets a thread" has exactly one definition.  Idempotent: the task
 * pointer is the whole "already up" marker, and a node this cannot serve keeps
 * its entry NULL, which abk_kcompressd_store() reads as "write synchronously".
 */
static void abk_kcompressd_add_node(int nid)
{
    struct abk_kcompressd_node *kcd = &abk_kcompressd_nodes[nid];
    struct task_struct *task;
    int ret;

    /*
        * The waitqueue head is zero-initialised in BSS and never "un-made" by
        * abk_kcompressd_del_node(), so re-initialising it here is safe (nobody
        * can be asleep on it: the only waiter is this node's own drain thread,
        * which either does not exist yet or has already exited) and makes
        * "ready for a thread" explicit.  The spinlock needs no such treatment --
        * zero is a valid unlocked arch_spinlock_t and del_node() never
        * un-initialises it -- so there is deliberately no spin_lock_init() here.
     */
    if (!node_online(nid))
        return;
    init_waitqueue_head(&kcd->wait);

    spin_lock_irq(&kcd->lock);
    if (kcd->task) {
        spin_unlock_irq(&kcd->lock);
        return;                         /* already served */
    }
    spin_unlock_irq(&kcd->lock);

    ret = kfifo_alloc(&kcd->fifo,
              ABK_KCOMPRESS_FIFO_SIZE * sizeof(struct page *),
              GFP_KERNEL);
    if (ret) {
        pr_err("kcompressd: node %d fifo allocation failed (%d), node stays synchronous\n",
               nid, ret);
        return;
    }

    /*
        * Re-check after the (sleeping) allocation.  Two concurrent bring-ups
        * would otherwise both get past the check above and the loser would
        * overwrite kcd->task, leaking its thread while its ring gets freed under
        * it.  Memory notifications take mem_hotplug_lock in write mode and the
        * boot-time loop runs before the registration, so this is belt-and-braces
        * rather than a live race -- but it costs one lock round trip.
     */
    spin_lock_irq(&kcd->lock);
    if (kcd->task) {
        spin_unlock_irq(&kcd->lock);
        kfifo_free(&kcd->fifo);
        return;
    }
    spin_unlock_irq(&kcd->lock);

    task = kthread_create_on_node(abk_kcompressd, (void *)(long)nid, nid,
                      "kcompressd%d", nid);
    if (IS_ERR(task)) {
        pr_err("kcompressd: node %d failed to start (%ld), node stays synchronous\n",
               nid, PTR_ERR(task));
        kfifo_free(&kcd->fifo);
        return;
    }
    kcd->task = task;
    wake_up_process(task);
}

/*
 * abk_kcompressd_del_node() - tear the node's kcompressd down.
 * @nid: node that just lost its last memory.
 *
 * The mirror of upstream's kswapd_stop() half, which this module needs because
 * its thread is not created by kswapd_run()/kswapd_stop().  Without it every
 * memory-hotplug removal leaks one kthread, one ~2 KiB ring and the task
 * struct for that node.
 *
 * Ordering is the whole problem, so it is spelled out step by step.  Upstream
 * calls kswapd_stop(node) in offline_pages() *before* memory_notify(MEM_OFFLINE),
 * so by the time this runs kswapd for the node is already gone and the node is
 * already out of N_MEMORY (node_states_clear_node()).  That is the only point
 * at which both of the following hold: no new work can be routed to the node,
 * and the pages still queued are the node's own, still-isolated-in-zone range
 * which remove_pfn_range_from_zone() has not touched yet.  Doing this any
 * earlier -- MEM_GOING_OFFLINE, where the range is being migrated -- would
 * mean draining pages that the offline path still holds isolated.
 *
 * The three steps are also the reason this cannot deadlock against a
 * concurrent reclaim: NULL the task pointer *under the ring lock*, so a
 * reclaim page already past the unlocked task read either bails at the new
 * check inside abk_kcompressd_enqueue() or finished before we took the lock.
 */
static void abk_kcompressd_del_node(int nid)
{
	struct abk_kcompressd_node *kcd = &abk_kcompressd_nodes[nid];
	struct task_struct *task;
	struct page *page;
	unsigned long flags;

	spin_lock_irqsave(&kcd->lock, flags);
	task = kcd->task;
	kcd->task = NULL;
	spin_unlock_irqrestore(&kcd->lock, flags);
	if (!task)
		return;

	/*
	 * kthread_stop() only *sets* the stop flag and waits for the thread; it
	 * does not wake it, and this thread's predicate has to include
	 * kthread_should_stop() to notice.  Waiting first rather than last also
	 * means the ring cannot still be in use when we drain and free it.
	 */
	wake_up_interruptible(&kcd->wait);
	kthread_stop(task);

	/*
	 * Anything enqueued between the thread's last drain and its exit lands
	 * here.  The ring must be empty before it is freed: every entry holds a
	 * page reference and a live swap slot, and those pages belong to the
	 * node being removed, so dropping rather than writing them would leave
	 * swap-cache pages whose data was never written and whose clean bit is
	 * already clear.
	 */
	while (kfifo_out(&kcd->fifo, &page, sizeof(page)))
		abk_kcompressd_do_swapout(page);

	kfifo_free(&kcd->fifo);
}

/*
 * abk_kcompressd_memory_notifier() - node bring-up and teardown.
 *
 * Registers on the memory notifier chain, which is a *blocking* chain
 * (drivers/base/memory.c: BLOCKING_NOTIFIER_HEAD(memory_chain)), so both
 * kthread_create_on_node() and kthread_stop() are allowed to sleep here.  It is
 * also a real function only under CONFIG_MEMORY_HOTPLUG_SPARSE -- the
 * !CONFIG_MEMORY_HOTPLUG_SPARSE definition in include/linux/memory.h returns 0
 * and does not touch the chain -- so a build without hotplug simply never
 * calls back and keeps the boot-time node set.  gki_defconfig has
 * CONFIG_MEMORY_HOTPLUG=y and arm64 selects SPARSEMEM, so on this target the
 * registration is live.
 *
 * MEM_ONLINE: pfn_to_nid(arg->start_pfn) rather than arg->status_change_nid,
 * because online_pages() calls kswapd_run(nid) for *every* block added to a
 * node, not only the first one, and a notifier that ignored the second and
 * later blocks would leave a node whose first block arrived before this module
 * loaded without a thread.  add_node() is idempotent, so covering both costs
 * nothing.
 *
 * MEM_OFFLINE: gated on arg->status_change_nid >= 0 for the same reason
 * offline_pages() gates kswapd_stop() on it -- that is the condition under
 * which the node actually became memoryless.
 */
static int abk_kcompressd_memory_notifier(struct notifier_block *nb,
					  unsigned long action, void *v)
{
	struct memory_notify *arg = v;
	int nid;

	switch (action) {
	case MEM_ONLINE:
		nid = pfn_to_nid(arg->start_pfn);
		if (nid >= 0 && nid < MAX_NUMNODES)
			abk_kcompressd_add_node(nid);
		break;
	case MEM_OFFLINE:
		if (arg->status_change_nid < 0)
			break;
		nid = arg->status_change_nid;
		if (nid >= 0 && nid < MAX_NUMNODES)
			abk_kcompressd_del_node(nid);
		break;
	}
	return NOTIFY_OK;
}

static struct notifier_block abk_kcompressd_memory_nb = {
	.notifier_call = abk_kcompressd_memory_notifier,
};

static int __init abk_kcompressd_init(void)
{
	struct ctl_table_header *header;
	int nid;

	/*
	 * Fail closed.  /proc/sys/vm/kcompressd is the *only* way to switch
	 * this off (vm.kcompressd=0) or to watch it (the three counters), and
	 * register_sysctl() returns NULL in its !CONFIG_SYSCTL inline-stub
	 * form.  Running the offload with no knob and no counters would leave an
	 * operator with no handle on it, so a tree without /proc/sys gets no
	 * threads instead -- the same end state as upstream, whose whole
	 * vm_table[] entry (and vm_kcompressd) is inside kernel/sysctl.c and
	 * simply does not exist there.
	 *
	 * kthreadd is PID 2, long before any initcall, and /proc/sys exists from
	 * do_pre_smp_initcalls(), so both the register_sysctl() below and the
	 * kthread_create_on_node() under it work at this level.
	 */
	header = register_sysctl("vm", abk_kcompressd_sysctl_table);
	if (!header) {
		pr_err("kcompressd: cannot register /proc/sys/vm/kcompressd, offload disabled\n");
		return 0;
	}

	for (nid = 0; nid < MAX_NUMNODES; nid++)
		if (node_online(nid))
			abk_kcompressd_add_node(nid);

	/*
	 * Node lifecycle after this is the notifier's job: MEM_ONLINE brings a
	 * hot-plugged node's thread up, MEM_OFFLINE tears it down again.  Both
	 * are no-ops (or unreachable) on a build without
	 * CONFIG_MEMORY_HOTPLUG_SPARSE, in which case the loop above covers
	 * everything the system will ever have.
	 */
	register_memory_notifier(&abk_kcompressd_memory_nb);

	pr_info("kcompressd: swap-out compression offload ready, "
		"vm.kcompressd soft limit %d of a %d-entry queue\n",
		abk_kcompressd_threshold, ABK_KCOMPRESS_FIFO_SIZE);
	return 0;
}
late_initcall(abk_kcompressd_init);
""")

_SWAPOUT_BODY_MARKER = "@@ABK_KCOMPRESS_SWAPOUT_BODY@@"

_ENGINE_ANCHOR_OLD = (
    "int swap_set_page_dirty(struct page *page)\n"
    "{\n"
    "\tstruct swap_info_struct *sis = page_swap_info(page);\n"
    "\n"
    "\tif (data_race(sis->flags & SWP_FS_OPS)) {\n"
    "\t\tstruct address_space *mapping = sis->swap_file->f_mapping;\n"
    "\n"
    "\t\tVM_BUG_ON_PAGE(!PageSwapCache(page), page);\n"
    "\t\treturn mapping->a_ops->set_page_dirty(page);\n"
    "\t} else {\n"
    "\t\treturn __set_page_dirty_no_writeback(page);\n"
    "\t}\n"
    "}\n"
)


def probe_swapout_shape(ctx):
    """Return ``"hook"``/``"plain"`` for the tree, or None on an unknown shape.

    Discriminates the two shapes of swap_writepage()'s frontswap branch, which
    is the only place in this group's payload where the baselines differ: the
    216 lts branch grew AOSP's android_vh_shrink_page_lock_owner_clear() call
    into it, and neither the call nor its DECLARE_HOOK exists on 167/178/194.
    A tree matching neither shape stops the group instead of letting it guess.
    """
    try:
        text = ctx.read(PAGE_IO)
    except OSError:
        return None
    if HOOK_PROBE in text:
        return "hook"
    if PLAIN_PROBE in text:
        return "plain"
    return None


def swapout_body(ctx):
    """The engine's per-baseline do_swapout() body, or None on an unknown shape."""
    shape = probe_swapout_shape(ctx)
    if shape is None:
        return None
    return HOOK_BODY if shape == "hook" else PLAIN_BODY


def build_steps(swapout_body=None):
    """The three mm/page_io.c steps, in order.

    ``swapout_body`` defaults to the no-vendor-hook variant so the step list can
    be inspected without a tree; abk_stable_core passes the probed one.
    """
    if swapout_body is None:
        swapout_body = PLAIN_BODY
    engine = _ENGINE.replace(_SWAPOUT_BODY_MARKER,
                             swapout_body.rstrip("\n")) + "\n"
    return [
        (PAGE_IO, _C_INC_OLD, _C_INC_NEW, T),
        (PAGE_IO, _C_OFFLOAD_OLD, _C_OFFLOAD_NEW, T),
        (PAGE_IO, _C_DECL_OLD, _C_DECL_NEW, T),
        (PAGE_IO, _ENGINE_ANCHOR_OLD, _ENGINE_ANCHOR_OLD + engine, T),
    ]
