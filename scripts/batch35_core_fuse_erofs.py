"""Batch 35: FUSE write-path prefaulting, and why the erofs half of this batch
does not land.

Batch theme was "filesystems -- FUSE and erofs": one FUSE commit plus the
erofs file-backed-mount pair.  Only the FUSE commit is portable onto
android13-5.15; the erofs pair is recorded as excluded in ``plan.md`` with the
evidence below, and nothing erofs-related is registered here.

The landed group
----------------

Source: mainline ``faa794dd2e17`` ("fuse: Move prefaulting out of hot write
path", Dave Hansen, v6.16, Signed-off-by Miklos Szeredi).  One file,
``fs/fuse/file.c``, two hunks.

``fuse_fill_write_pages()`` prefaults the write source buffer at the head of
its retry loop, so *every* write(2) into a FUSE file touches userspace twice:
once explicitly through ``fault_in_iov_iter_readable()`` and once implicitly
through the copy.

The commit's justification is that generic_perform_write() stopped doing that:
from v6.11 it faults only after a no-progress copy, under the comment
"'folio' is now unlocked and faults on it can be handled.  Ensure forward
progress by trying to fault it in now."  State that precisely, because it is
**not** true of 5.15's generic_perform_write(), which still faults at the loop
head to bring the user page in before ``write_begin()`` takes the page.  What
makes the fuse form safe on 5.15 is where the retry fault lands, not what
generic_perform_write() does here: it runs *after* ``unlock_page()`` /
``put_page()``, so no fault is taken under the page lock, and
``copy_page_from_iter_atomic()`` never faults in the first place.  The change
is a fast-path heuristic, not a correctness requirement, and it is upstream's
reasoning -- this module claims no independent measurement of it.

This is the batch's cheapest item and it is self-contained: no other group in
this module writes into ``fs/fuse/file.c``, the file carries no ABK marker on
any baseline, and the two anchors are byte-identical on all four tracked
baselines (167/178/194/lts-.216; the three places the file differs across
them at all -- ``fuse_dax_break_layouts()``'s third argument, a ``->len``
clamp, and one ``outarg.size`` check -- are all far from these hunks).

5.15 shape, not the source shape
-------------------------------

The upstream hunk is written against the 6.x folio form of the function:

    folio = __filemap_get_folio(mapping, index, FGP_WRITEBEGIN, ...);
    ...
    tmp = copy_folio_from_iter_atomic(folio, offset, bytes, ii);
    folio_unlock(folio);
    if (!tmp) {
        folio_put(folio);
        ...prefault...
        goto again;
    }

5.15 still has the page form -- ``grab_cache_page_write_begin()``,
``copy_page_from_iter_atomic()``, ``unlock_page()``/``put_page()`` -- and sets
``err = -ENOMEM`` before the page grab rather than deriving it from the grab's
error, so the removed hunk is not even in the same place textually.  The
resulting pair is therefore a 5.15-shape rewrite of the same two edits, not a
copy:

* the ``again:`` label previously opened with ``err = -EFAULT; if
  (fault_in_iov_iter_readable(...)) break;``; that pair is deleted, leaving
  ``again:`` followed directly by the ``err = -ENOMEM;`` / page-grab that was
  already there;
* the ``if (!tmp)`` branch keeps ``unlock_page()``/``put_page()`` and then
  runs the prefault before ``goto again``.

``err`` semantics are preserved exactly.  On the no-progress path the old code
retried into a loop head that set ``err = -EFAULT`` before faulting; the new
code sets it in the branch, so a failed fault still returns ``-EFAULT`` from
``return count > 0 ? count : err`` while a successful one still retries.  The
delta is confined to the faulting case, which now allocates and frees one page
before discovering the bad pointer -- the trade the upstream commit makes.

No ABK marker: this is an upstream-shape rewrite, so a future baseline that
carries ``faa794dd2e17`` itself (the folio form, i.e. a 6.x tree) must be left
byte-identical and report ``already_present``.  A marker on these lines would
defeat that short-circuit, which is why ``tests/implementation_audit.py`` pins
the absence per-file rather than pinning a marker's presence.

The erofs half, excluded
------------------------

``fb176750266a`` ("erofs: add file-backed mount support", v6.12) and
``6422cde1b0d5`` ("erofs: use buffered I/O for file-backed mounts by
default", v6.13) are **not** a bounded graft onto android13-5.15.  Measured
prerequisites, grepped over the ``fs/erofs/`` of **all four** baselines
(167/178/194/lts-.216; ``internal.h`` is 15955 bytes and byte-identical on
every one -- the trees are not otherwise identical, but nothing that
matters here has moved):

    erofs_is_fscache_mode        erofs_bread            erofs_buf
    erofs_read_metabuf           devs->flatdev          s_fscache
    packed_inode                 erofs_pos              erofs_fill_from_devinfo
    super_set_sysfs_name_generic
    fs/erofs/fileio.c            (in no Makefile either)

every one of them: **0 occurrences on all four**.  ACK's 5.15 erofs is the
iomap-era tree:
metadata comes from ``erofs_get_meta_page()``, which reads
``sb->s_bdev->bd_inode->i_mapping`` directly (so there is no indirection to
point at a file's mapping), data comes from ``erofs_iomap_begin()`` handing
``iomap->bdev = mdev.m_bdev`` to iomap (so a bdev-less mapping has nothing to
submit through), and ``struct erofs_device_info`` holds a ``struct
block_device *bdev`` rather than a ``struct file *bdev_file``.

The two named commits are only the mount plumbing and the default-behaviour
switch of that feature; the data path they switch on lives in commits the
batch list does not name (``ce63cb62d794`` and ``283213718f5d``, which *create*
``fs/erofs/fileio.c`` and are what ``6422cde1b0d5`` patches).  Landing the
named pair alone would produce a mount that succeeds and then returns
``-EOPNOTSUPP`` for every inode's data -- upstream's own interim state, marked
``XXX: data I/Os will be implemented in the following patches``.  That is a
stub, not a graft, and it is why the exclusion is the whole feature rather
than the two commits.

Reaching a working file-backed mount on this baseline means first backporting
what it stands on: the ``erofs_buf``/``erofs_bread`` metabuf layer (upstream has
had it since ~5.19; 5.15 does not, and the patch *context* of every hunk above
is written against it) and then ``fileio.c`` itself.  Do not misread the shape:
file-backed mount adds a *parallel* ``erofs_fileio_aops`` and a bdev-less
submission path, it does not replace iomap -- upstream's bdev path still reads
through ``erofs_read_folio()`` -> ``iomap_read_folio(..., &erofs_iomap_ops)`` at
v6.12 and still does at v6.16.  The fscache/ondemand mode is *not* a
prerequisite either: upstream treats the two as mutually exclusive
(``erofs_is_fscache_mode()`` returns false once ``erofs_is_fileio_mode()`` is
true), and backporting it would be a separate choice.  Two layers plus a new
I/O path is a subsystem rewrite, not a batch.  Recorded in ``plan.md``'s
exclusion record with this evidence.

Two smaller exclusions
----------------------

``770c8d55c428`` ("lib/iov_iter: fix to increase non slab folio refcount")
is not applicable: it fixes a regression from ``b9c0e49abfca`` ("mm: decline
to manipulate the refcount on a slab page"), and 5.15's ``lib/iov_iter.c``
contains no ``page_folio()`` and no ``folio_test_slab()`` at all -- the bug it
patches does not exist here.

FUSE passthrough (mainline v6.9) is deliberately not ported: android13-5.15
already ships its own implementation on a different ioctl pair
(``_IOW(229,126)`` against upstream's ``_IOW(229,1)``/``(229,2)``), the two do
not conflict but MediaProvider's FuseDaemon was never migrated to the upstream
API, so porting it would add an unreachable second implementation.  Recorded
in ``plan.md`` so it is not re-proposed.

Graft boundary: no other group writes into ``fs/fuse/file.c``, so there is
nothing to order against and no shape probe is needed.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

FUSE_FILE_C = "fs/fuse/file.c"

T = True

# ---------------------------------------------------------------------------
# Step 1: the retry label stops prefaulting unconditionally.
# ---------------------------------------------------------------------------

_PREFAULT_HEAD_OLD = (
    " again:\n"
    "\t\terr = -EFAULT;\n"
    "\t\tif (fault_in_iov_iter_readable(ii, bytes))\n"
    "\t\t\tbreak;\n"
    "\n"
    "\t\terr = -ENOMEM;\n"
    "\t\tpage = grab_cache_page_write_begin(mapping, index, 0);\n"
)

_PREFAULT_HEAD_NEW = (
    " again:\n"
    "\t\terr = -ENOMEM;\n"
    "\t\tpage = grab_cache_page_write_begin(mapping, index, 0);\n"
)

# ---------------------------------------------------------------------------
# Step 2: ... and prefaults exactly where the copy made no progress.
# ---------------------------------------------------------------------------

_PREFAULT_RETRY_OLD = (
    "\t\tif (!tmp) {\n"
    "\t\t\tunlock_page(page);\n"
    "\t\t\tput_page(page);\n"
    "\t\t\tgoto again;\n"
    "\t\t}\n"
)

_PREFAULT_RETRY_NEW = (
    "\t\tif (!tmp) {\n"
    "\t\t\tunlock_page(page);\n"
    "\t\t\tput_page(page);\n"
    "\n"
    "\t\t\t/*\n"
    "\t\t\t * Ensure forward progress by faulting in\n"
    "\t\t\t * while not holding the page lock:\n"
    "\t\t\t */\n"
    "\t\t\tif (fault_in_iov_iter_readable(ii, bytes)) {\n"
    "\t\t\t\terr = -EFAULT;\n"
    "\t\t\t\tbreak;\n"
    "\t\t\t}\n"
    "\n"
    "\t\t\tgoto again;\n"
    "\t\t}\n"
)

# Symbols the unit test and the audits probe for.  The prefault moved, so both
# halves are load-bearing: RETRY_PREFAULT is the new call site, LOOP_HEAD_PREFAULT
# is the text that must be gone (a port that only added the new one would double
# the fault-in work rather than remove it).
RETRY_PREFAULT = (
    "\t\t\tif (fault_in_iov_iter_readable(ii, bytes)) {\n"
    "\t\t\t\terr = -EFAULT;\n"
    "\t\t\t\tbreak;\n"
    "\t\t\t}"
)
LOOP_HEAD_PREFAULT = (
    " again:\n"
    "\t\terr = -EFAULT;\n"
    "\t\tif (fault_in_iov_iter_readable(ii, bytes))\n"
    "\t\t\tbreak;\n"
)


def build_steps():
    """Two required steps: drop the loop-head prefault, add the retry one.

    Both required.  They are the two halves of one edit, and each half alone
    compiles and is correct -- dropping only the first would remove the
    forward-progress guarantee, adding only the second would fault twice.
    ``apply_steps`` is transactional, so a required miss writes nothing.
    """
    return [
        (FUSE_FILE_C, _PREFAULT_HEAD_OLD, _PREFAULT_HEAD_NEW, T),
        (FUSE_FILE_C, _PREFAULT_RETRY_OLD, _PREFAULT_RETRY_NEW, T),
    ]


def _fuse_prefault_out_of_write_path_apply(ctx):
    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the single Batch-34 PatchGroup record."""
    return [
        PatchGroup(
            "fuse_prefault_out_of_write_path",
            "prefault the FUSE write source buffer only where the copy made no "
            "progress, instead of on every retry of the fill loop -- one fewer "
            "userspace access per write(2) through a FUSE daemon",
            [
                "faa794dd2e17 (mainline v6.16, 'fuse: Move prefaulting out of "
                "hot write path')",
            ],
            [FUSE_FILE_C],
            _fuse_prefault_out_of_write_path_apply,
        ),
    ]
