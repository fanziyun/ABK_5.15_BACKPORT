"""Batch 74: read fault-around gated on the faulting task's uid.

``do_read_fault()`` on this tree already consults the vendor hook chain before
it calls ``->map_pages()``:

    trace_android_vh_should_fault_around(vmf, &should_around);
    if (vma->vm_ops->map_pages && fault_around_bytes >> PAGE_SHIFT > 1 &&
        should_around) { ... }

The hook alone cannot express a *uid* policy: it is a per-fault callback with
no policy state of its own, so the tree's only fault-around suppression is
whatever a registered callback decides per fault.  This group adds the policy
state and the decision point -- one call after the hook -- so a build can say
"fault-around everywhere except these uids" (or "for system uids only") without
touching the vendor hook, and can change its mind at runtime through sysfs.

Why fault-around is the thing to keep on: on a file-backed fault it maps a run
of up to ``fault_around_bytes`` (default 64 KiB) around the faulting address
with one ``->map_pages()`` call, replacing up to 16 separate faults with one.
For an app that faults through a hot file region (dex, oat, resources) that is
a large win; for an app whose access pattern is genuinely random it is wasted
work (pages mapped and then evicted untouched), and for system processes
booting up it competes with the page-cache work that actually matters.  Hence
the default is performance-first and permissive: fault-around on for the whole
app uid range, off only for the system range, exclusion list empty.

Judgement order in ``abk_fa_adjust()`` (it can only tighten, never re-enable,
so a vendor callback that already said "no" keeps priority):

  1. ``fault_around_enable`` off            -> leave the hook's answer alone.
  2. ``uid < 10000`` and ``fault_around_sys_uid``  -> suppress.
  3. otherwise ``!fault_around_app_only`` and the uid's bitmap bit set
                                             -> suppress.

``fault_around_app_only`` reads literally in that order, and the name is the
part that misleads: **1 skips gate 3 entirely**, so the exclusion list is not
consulted at all and the ``uid < 10000`` rule is the whole policy.  It is not
"only the listed apps get fault-around".  0 (default) is the
performance-first direction -- fault-around for every uid, minus the listed
ones.

Knobs are module parameters, so they land under
``/sys/module/memory/parameters/`` (a built-in object's sysfs module name is
its file basename -- the convention this module already relies on for
``page_alloc`` and ``readahead``):

  * ``fault_around_enable`` (bool, default 1)   master switch
  * ``fault_around_sys_uid`` (bool, default 1)  suppress for uid < 10000
  * ``fault_around_app_only`` (bool, default 0) see above
  * ``fault_around_app`` (custom set/get, 0644) exclusion list:
      - write ``0`` clears it,
      - write a uid in ``[10000, 99999]`` adds that uid,
      - any other value is ``-EINVAL`` (a uid outside the app range cannot be
        represented, and silently aliasing it onto a bit would be worse),
      - read prints the first 64 uids, comma separated, newline terminated.

Storage: one bit per uid in ``[10000, 99999]`` -- 90000 bits,
``DIV_ROUND_UP(90000, BITS_PER_LONG)`` unsigned longs = 11.25 KiB of ``.bss``
on a 64-bit build.  Index is ``uid - 10000``, so *every* access range-checks
the uid first.  That is a correctness requirement, not tidiness: ``from_kuid()``
returns ``(uid_t)-1`` for a task outside the initial user namespace, and a
lockless ``test_bit()`` with that as an index would read 17 KiB past the array.

The uid itself is read with ``current_uid()`` (i.e. ``from_kuid(&init_user_ns,
current_cred()->uid)``), never with a byte offset into ``struct cred``: on 5.15
the uid sits at a different offset than on the tree this policy was modelled
after (``cred+8`` is the *gid* there), so an offset read compiles, runs, and
enforces the wrong half of the credential pair.

Locking: writers (the two list operations) hold ``abk_fa_app_lock``; the fault
path is lockless (``READ_ONCE`` on the bools, plain ``test_bit`` on the
bitmap).  A fault racing a list update therefore sees the old answer for at
most that one fault -- the tolerance a hint policy wants.  No allocation, no
sleep, no refcount on the fault path.

Anchors (both unique in the pristine 5.15.220 ``mm/memory.c``; the group refuses
an unknown shape instead of half-patching it):

  * the head of ``do_read_fault()`` -- the policy block is inserted immediately
    above it, so the statics precede their only caller with no forward
    declaration (and no ``__read_mostly``-ordering surprise);
  * the ``trace_android_vh_should_fault_around()`` call site plus the ``if`` it
    feeds -- the call is inserted between them.

``apply_steps`` is transactional and the group is idempotent.  The second pass
probes this group's own payload (``abk_fa_adjust()`` / the bitmap declaration)
and the marker separately: payload + marker means ``already_present``, payload
without the marker is a *refusal* (``blocked_by_shape``) rather than a re-apply
-- step 1's replacement keeps the ``do_read_fault()`` head it is anchored on as
its tail, so re-running it on a tree whose marker was reflowed would insert a
second copy of every static and fail the compile with a redefinition.  That is
Batch 70's smaps-guard contract, applied here for the same reason.

The group owns no text another group edits and edits no text another group
owns: the only other group that reads this call site (``bg_readahead_tune`` /
``bg_launch_prefetch``) probes it and rewrites ``mm/filemap.c``, and its
callback's decision is consumed by the hook call this group inserts *after* --
so registration order does not matter.
"""

from __future__ import annotations

import re

from abk_backport_engine import apply_steps

MEMORY_C = "mm/memory.c"

T = True

# Pins for the unit test, the audits and the smoke run.  Keep them exact: they
# are the only text-level evidence that the policy is compiled in, not merely
# claimed.
MARKER = "sailboat_fault_around_app_policy"
HOOK_CALL = "trace_android_vh_should_fault_around(vmf, &should_around);"
CALL_SITE = "abk_fa_adjust(&should_around);"
ADJUST_DEF = "static void abk_fa_adjust(bool *should_around)"
UID_READ = "from_kuid(&init_user_ns, current_uid())"
BITMAP_DEF = ("static unsigned long abk_fa_app_bitmap"
              "[DIV_ROUND_UP(90000, BITS_PER_LONG)];")
LOCK_DEF = "static DEFINE_MUTEX(abk_fa_app_lock);"
PARAM_ENABLE = "module_param(fault_around_enable, bool, 0644);"
PARAM_SYS_UID = "module_param(fault_around_sys_uid, bool, 0644);"
PARAM_APP_ONLY = "module_param(fault_around_app_only, bool, 0644);"
PARAM_APP = ("module_param_cb(fault_around_app, &abk_fa_app_param_ops, "
             "NULL, 0644);")
READ_BOUND = "ABK_FA_APP_LIST_MAX"
CLEAR_CALL = "bitmap_zero(abk_fa_app_bitmap, 90000);"
# The decision, in the order a reviewer should read it.
GATE_ENABLE = "if (!READ_ONCE(fault_around_enable))"
GATE_SYS_UID = "if (READ_ONCE(fault_around_sys_uid))"
GATE_APP_ONLY = "if (!READ_ONCE(fault_around_app_only) &&"


def _tabs(text):
    """Re-indent a 4-space-drafted kernel C block with real tabs."""
    text = re.sub(r"(?m)^    +", lambda m: "\t" * (len(m.group(0)) // 4), text)
    return text.rstrip("\n") + "\n"


# The policy block, inserted above do_read_fault().  Drafted with 4-space
# indentation and re-tabbed by _tabs().
_POLICY_DRAFT = r"""
/*
 * sailboat_fault_around_app_policy: per-uid read fault-around policy.
 *
 * do_read_fault() asks the vendor hook chain first and this runs after it.
 * It can only tighten the answer -- never re-enable fault-around -- so a
 * vendor callback that already suppressed the read-around keeps priority.
 * The decision, in order:
 *
 *   1. fault_around_enable off: leave the hook's answer alone.
 *   2. uid below 10000 and fault_around_sys_uid: suppress (system range).
 *   3. otherwise, fault_around_app_only off and the uid's bit set in
 *      fault_around_app: suppress.
 *
 * Knobs live under /sys/module/memory/parameters/ (a built-in object's
 * module name is its basename):
 *
 *   fault_around_enable    master switch, default on.
 *   fault_around_sys_uid   suppress for uid < 10000, default on.
 *   fault_around_app_only  0 (default) = a listed uid loses fault-around and
 *                          every other uid keeps the hook's answer, which is
 *                          the performance-first direction; 1 = the exclusion
 *                          list is not consulted at all, so the uid < 10000
 *                          rule is the whole policy (literal reading of the
 *                          decision order above; it is NOT "only the listed
 *                          uids get fault-around").
 *   fault_around_app       exclusion list: write one uid in [10000, 99999]
 *                          to add it, write 0 to clear; reading prints the
 *                          first 64 listed uids, comma separated.
 *
 * The bitmap holds one bit per uid in [10000, 99999] -- 90000 bits, 11.25
 * KiB of .bss -- indexed by (uid - 10000), so every access range-checks the
 * uid first: from_kuid() returns (uid_t)-1 for a task outside the initial
 * user namespace and that value must never become a bitmap index.
 *
 * Writers serialise on abk_fa_app_lock; the fault path is lockless
 * (READ_ONCE on the bools, plain test_bit on the bitmap), so a fault racing
 * a list update sees the old answer for at most that one fault.  No
 * allocation, no sleep, no refcount on the fault path.
 */
#define ABK_FA_APP_UID_BASE 10000
#define ABK_FA_APP_UID_LAST 99999
#define ABK_FA_APP_LIST_MAX 64

static bool fault_around_enable __read_mostly = true;
static bool fault_around_sys_uid __read_mostly = true;
static bool fault_around_app_only __read_mostly;

/* One bit per app uid, indexed by uid - ABK_FA_APP_UID_BASE. */
static unsigned long abk_fa_app_bitmap[DIV_ROUND_UP(90000, BITS_PER_LONG)];

static DEFINE_MUTEX(abk_fa_app_lock);

static uid_t abk_fa_current_uid(void)
{
    return from_kuid(&init_user_ns, current_uid());
}

static int abk_fa_app_set(const char *val, const struct kernel_param *kp)
{
    unsigned int uid;
    int ret;

    ret = kstrtouint(val, 10, &uid);
    if (ret)
        return ret;
    if (uid && (uid < ABK_FA_APP_UID_BASE || uid > ABK_FA_APP_UID_LAST))
        return -EINVAL;

    mutex_lock(&abk_fa_app_lock);
    if (!uid)
        bitmap_zero(abk_fa_app_bitmap, 90000);
    else
        set_bit(uid - ABK_FA_APP_UID_BASE, abk_fa_app_bitmap);
    mutex_unlock(&abk_fa_app_lock);

    return 0;
}

static int abk_fa_app_get(char *buffer, const struct kernel_param *kp)
{
    unsigned int uid;
    int len = 0;
    int nr = 0;

    mutex_lock(&abk_fa_app_lock);
    for (uid = ABK_FA_APP_UID_BASE;
         uid <= ABK_FA_APP_UID_LAST && nr < ABK_FA_APP_LIST_MAX; uid++) {
        if (!test_bit(uid - ABK_FA_APP_UID_BASE, abk_fa_app_bitmap))
            continue;
        if (len > PAGE_SIZE - 16)
            break;
        len += scnprintf(buffer + len, PAGE_SIZE - len,
                 nr ? ",%u" : "%u", uid);
        nr++;
    }
    mutex_unlock(&abk_fa_app_lock);

    len += scnprintf(buffer + len, PAGE_SIZE - len, "\n");
    return len;
}

static const struct kernel_param_ops abk_fa_app_param_ops = {
    .set = abk_fa_app_set,
    .get = abk_fa_app_get,
};

module_param(fault_around_enable, bool, 0644);
MODULE_PARM_DESC(fault_around_enable,
    "read fault-around uid policy on (default 1)");
module_param(fault_around_sys_uid, bool, 0644);
MODULE_PARM_DESC(fault_around_sys_uid,
    "suppress fault-around for system uids below 10000 (default 1)");
module_param(fault_around_app_only, bool, 0644);
MODULE_PARM_DESC(fault_around_app_only,
    "1: the fault_around_app list is not consulted at all; 0: a listed uid loses fault-around (default 0)");
module_param_cb(fault_around_app, &abk_fa_app_param_ops, NULL, 0644);
MODULE_PARM_DESC(fault_around_app,
    "uid excluded from fault-around, [10000, 99999]; write 0 to clear, read the first 64");

static void abk_fa_adjust(bool *should_around)
{
    uid_t uid;

    if (!*should_around)
        return;
    if (!READ_ONCE(fault_around_enable))
        return;

    uid = abk_fa_current_uid();
    if (uid < ABK_FA_APP_UID_BASE) {
        if (READ_ONCE(fault_around_sys_uid))
            *should_around = false;
        return;
    }
    if (uid > ABK_FA_APP_UID_LAST)
        return;
    if (!READ_ONCE(fault_around_app_only) &&
        test_bit(uid - ABK_FA_APP_UID_BASE, abk_fa_app_bitmap))
        *should_around = false;
}
"""

_POLICY_BLOCK = _tabs(_POLICY_DRAFT).lstrip("\n")

# 1) Insert the policy block immediately above do_read_fault(), so the statics
# precede their only caller.  The anchor is the function head through its local
# declarations, which is unique in the file; the replacement is the block plus
# that anchor, so the second pass short-circuits on the block (idempotency) and
# the first pass cannot match it (the block is new text).
_DEF_OLD = (
    "static vm_fault_t do_read_fault(struct vm_fault *vmf)\n"
    "{\n"
    "\tstruct vm_area_struct *vma = vmf->vma;\n"
    "\tvm_fault_t ret = 0;\n"
    "\tbool should_around = true;\n"
)
_DEF_NEW = _POLICY_BLOCK + "\n" + _DEF_OLD

# 2) The decision point: right after the vendor hook, before the `if` that
# consumes should_around.  Kept as a two-line anchor (hook call + the if it
# feeds) so it cannot match the do_read_fault() head or an unrelated hook call.
_CALL_OLD = (
    "\ttrace_android_vh_should_fault_around(vmf, &should_around);\n"
    "\tif (vma->vm_ops->map_pages && fault_around_bytes >> PAGE_SHIFT > 1 && should_around) {\n"
)
_CALL_NEW = (
    "\ttrace_android_vh_should_fault_around(vmf, &should_around);\n"
    "\t/* sailboat_fault_around_app_policy: tighten the hook's answer by uid. */\n"
    "\tabk_fa_adjust(&should_around);\n"
    "\tif (vma->vm_ops->map_pages && fault_around_bytes >> PAGE_SHIFT > 1 && should_around) {\n"
)

# Shape probes: the definition anchor and the hook call site must both exist.
DO_READ_FAULT_PROBE = "static vm_fault_t do_read_fault(struct vm_fault *vmf)"


def build_steps():
    """Two required steps: the policy block above do_read_fault(), then the
    call that applies it after the vendor hook."""
    return [
        (MEMORY_C, _DEF_OLD, _DEF_NEW, T),
        (MEMORY_C, _CALL_OLD, _CALL_NEW, T),
    ]


def _fault_around_app_policy_apply(ctx):
    try:
        text = ctx.read(MEMORY_C)
    except OSError:
        return "blocked_by_shape", MEMORY_C + ": file absent"
    # The marker is part of the match, not decoration (AGENTS.md's idempotency
    # rule), and here the failure mode is concrete rather than cosmetic: step 1
    # builds its replacement as "policy block + the do_read_fault() head", so
    # the head keeps matching even after the block is in place.  A tree whose
    # marker comment was reflowed (marker gone, payload still there) would
    # therefore match step 1's `old` *again* and get a second copy of every
    # static in the block -- `static bool fault_around_enable`, the bitmap, the
    # mutex, `abk_fa_adjust()` -- i.e. a redefinition, a compile error on a tree
    # that was previously fine.
    #
    # So probe this group's own payload and *refuse*: do not fall through into
    # apply_steps, which is exactly what would insert the duplicate.  Same
    # contract as Batch 70's smaps guard (batch70_core_smaps_migration_guard.py).
    if ADJUST_DEF in text or BITMAP_DEF in text:
        if MARKER in text:
            return "already_present", "the fault-around uid policy is already applied"
        return (
            "blocked_by_shape",
            MEMORY_C + ": the policy block is present but its " + MARKER
            + " marker is not; refusing rather than inserting a second copy",
        )
    for needle in (DO_READ_FAULT_PROBE, HOOK_CALL):
        if needle not in text:
            return "blocked_by_shape", (
                "%s: %s not found; this tree does not have the "
                "android_vh_should_fault_around call site the policy hangs "
                "off" % (MEMORY_C, needle.split("(")[0]))
    status, _results, detail = apply_steps(ctx, build_steps())
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


def build_groups(PatchGroup):
    """Return the single Batch-74 fault-around uid-policy PatchGroup record."""
    return [
        PatchGroup(
            "fault_around_app_policy",
            "do_read_fault() gains a per-uid read fault-around policy after "
            "the android_vh_should_fault_around hook: a 90000-bit app-uid "
            "bitmap (11.25 KiB .bss, lockless test_bit on the fault path, "
            "mutex on the list writers) plus four /sys/module/memory/"
            "parameters knobs, defaulting to performance-first -- "
            "fault-around on for the whole app uid range (10000-99999), "
            "suppressed only for the system range, exclusion list empty",
            [
                "no upstream commit: project policy over the tree's existing "
                "android_vh_should_fault_around call site in mm/memory.c",
            ],
            [MEMORY_C],
            _fault_around_app_policy_apply,
        ),
    ]
