"""Batch 71: kcompressd drain takes the page lock without stalling.

Field evidence: md_UFS_QC_PHY.BIN.txt:23403 (second crash fragment)
``task:kcompressd0 state:D ... __lock_page <- abk_kcompressd_do_swapout+0x120``
plus 1690x ``Write-error on swap-device (254:0:...)`` across the burst window,
with kcompressd0 at 12-36% kernel CPU (10 PowerDet hits). The drain thread's
blocking ``lock_page(page)`` in ``abk_kcompressd_do_swapout()`` waits on a page
whose holder never releases it -- a fault mapping it back in, a later reclaim
pass, or a failed write stuck in ``PageWriteback``/``PageDirty`` -- so the
whole per-node FIFO pins behind one entry while kswapd keeps queueing: swap
pressure piles up exactly when the device can least afford it.

What this group does: second-pass rewrite of the text Batch 41 generates --
the ``lock_page(page)`` at the head of ``abk_kcompressd_do_swapout()`` becomes
a ``trylock_page(page)`` with a skip-and-drop-reference fallback. A page that is
currently locked is left for its current owner (fault, reclaim, or an
in-flight writeback whose completion unlocks it); the queued reference is
dropped and the drain moves to the next entry instead of sleeping the only
thread that can make progress. Steady state is unchanged: an unlocked page
takes the lock exactly as before and flows into the same frontswap /
__swap_writepage body.

Why trylock (not lock, not skip-silently):

* ``lock_page()`` is the stall in the fragment. Upstream 0.5 has no lock at
  all here (latent double-unlock, Batch 41 docstring); Batch 41 added the lock
  to restore the ->writepage contract. trylock keeps the contract (the write
  still runs under PG_locked) without the wait.
* Dropping the reference on the contended path is required, not optional:
  every queued entry holds the ``get_page()`` Batch 41 took at enqueue time,
  so returning without ``put_page()`` leaks the page and its swap slot. The
  skip also bumps ``abk_kcompressd_swapped`` -- a skipped page has left the
  FIFO, and Batch 41 reports ``enqueued - swapped`` as the live queue depth,
  so a dequeue that did not count would inflate that depth by every skipped
  page forever.
* ``pr_warn_once`` (never WARN-family: a WARN is an oops, and panic_on_oops
  turns it into a panic) keeps the skip visible in dmesg without spamming the
  reclaim hot path.

Scope notes:

* Second-pass group over Batch 41's generated text (trap 5): registered after
  ``vm_kcompressd_swapout`` and refuses (``blocked_by_shape``) when that
  group's engine is absent, so an unknown tree is never half-patched. The
  ``sailboat_`` marker rides in the new comment, so the cost is explicit: a
  baseline that ever grows an equivalent upstream guard still reports
  ``applied`` on the second pass rather than ``already_present``.
* Hook/plain variance does not touch this anchor: the lock site sits above
  the ``@@ABK_KCOMPRESS_SWAPOUT_BODY@@`` join point, byte-identical in both
  engine variants.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

PAGE_IO = "mm/page_io.c"

T = True

# The lock site Batch 41 generates at the head of abk_kcompressd_do_swapout(),
# above the body join point -- byte-identical in the hook and plain variants.
_TRYLOCK_OLD = (
    "\t * freed underneath us no matter who else locked it.\n"
    "\t */\n"
    "\tlock_page(page);\n"
)

_TRYLOCK_NEW = (
    "\t * freed underneath us no matter who else locked it.\n"
    "\t */\n"
    "\t/*\n"
    "\t * sailboat_kcompressd_trylock: take the page lock without stalling the\n"
    "\t * drain. Field fragment md_UFS_QC_PHY.BIN.txt:23403 shows kcompressd0\n"
    "\t * in D state under __lock_page inside abk_kcompressd_do_swapout+0x120,\n"
    "\t * the whole FIFO pinned behind one contended page while swap writes\n"
    "\t * fail around it. trylock keeps the ->writepage contract (the write\n"
    "\t * still runs under PG_locked) and skips the entry when its owner -- a\n"
    "\t * fault, a later reclaim pass, or an in-flight writeback -- still holds\n"
    "\t * it; the queued reference is dropped so neither the page nor its swap\n"
    "\t * slot leaks. pr_warn_once, never WARN-family (a WARN is an oops).\n"
    "\t */\n"
    "\tif (!trylock_page(page)) {\n"
    "\t\tpr_warn_once(\"kcompressd: skipping a locked page, its owner makes progress instead\\n\");\n"
    "\t\t/*\n"
    "\t\t * This page has left the FIFO, so it counts against the queue\n"
    "\t\t * exactly like a written one: abk_kcompressd_swapped is what\n"
    "\t\t * Batch 41 subtracts from abk_kcompressd_enqueued to report the\n"
    "\t\t * live queue depth, and a dequeue that did not bump it would\n"
    "\t\t * inflate that depth by every skipped page forever.\n"
    "\t\t */\n"
    "\t\tatomic_long_inc(&abk_kcompressd_swapped);\n"
    "\t\tput_page(page);\n"
    "\t\treturn;\n"
    "\t}\n"
)

# Pins for the audits and the unit test.
TRYLOCK_MARKER = "sailboat_kcompressd_trylock"
TRYLOCK_CALL = "if (!trylock_page(page)) {"
DO_SWAPOUT_PROBE = "abk_kcompressd_do_swapout"


def build_steps():
    """One required step: blocking lock becomes a trylock with fallback."""
    return [
        (PAGE_IO, _TRYLOCK_OLD, _TRYLOCK_NEW, T),
    ]


def _kcompressd_trylock_apply(ctx):
    try:
        text = ctx.read(PAGE_IO)
    except FileNotFoundError:
        return "blocked_by_shape", PAGE_IO + ": file absent"
    if TRYLOCK_MARKER in text:
        return "already_present", "the kcompressd trylock guard is already applied"
    if DO_SWAPOUT_PROBE not in text:
        return "blocked_by_shape", ("abk_kcompressd_do_swapout() not found: "
                                     "vm_kcompressd_swapout must apply first")
    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the single Batch-71 kcompressd trylock PatchGroup record."""
    return [
        PatchGroup(
            "kcompressd_trylock_guard",
            "abk_kcompressd_do_swapout() takes a queued page with trylock_page() "
            "instead of lock_page(), so one contended page is skipped -- "
            "reference dropped -- instead of stalling the whole drain FIFO in "
            "D state while swap writes fail around it",
            [
                "field stall md_UFS_QC_PHY.BIN.txt:23403 "
                "(kcompressd0 D under __lock_page in abk_kcompressd_do_swapout)",
            ],
            [PAGE_IO],
            _kcompressd_trylock_apply,
        ),
    ]
