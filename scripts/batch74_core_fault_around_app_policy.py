from __future__ import annotations

import re

from abk_backport_engine import apply_steps

MEMORY_C = "mm/memory.c"

T = True

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
GATE_ENABLE = "if (!READ_ONCE(fault_around_enable))"
GATE_SYS_UID = "if (READ_ONCE(fault_around_sys_uid))"
GATE_APP_ONLY = "if (!READ_ONCE(fault_around_app_only) &&"


def _tabs(text):
    text = re.sub(r"(?m)^    +", lambda m: "\t" * (len(m.group(0)) // 4), text)
    return text.rstrip("\n") + "\n"


_POLICY_DRAFT = '\n/* sailboat_fault_around_app_policy */\n#define ABK_FA_APP_UID_BASE 10000\n#define ABK_FA_APP_UID_LAST 99999\n#define ABK_FA_APP_LIST_MAX 64\n\nstatic bool fault_around_enable __read_mostly = true;\nstatic bool fault_around_sys_uid __read_mostly = true;\nstatic bool fault_around_app_only __read_mostly;\n\n\nstatic unsigned long abk_fa_app_bitmap[DIV_ROUND_UP(90000, BITS_PER_LONG)];\n\nstatic DEFINE_MUTEX(abk_fa_app_lock);\n\nstatic uid_t abk_fa_current_uid(void)\n{\n    return from_kuid(&init_user_ns, current_uid());\n}\n\nstatic int abk_fa_app_set(const char *val, const struct kernel_param *kp)\n{\n    unsigned int uid;\n    int ret;\n\n    ret = kstrtouint(val, 10, &uid);\n    if (ret)\n        return ret;\n    if (uid && (uid < ABK_FA_APP_UID_BASE || uid > ABK_FA_APP_UID_LAST))\n        return -EINVAL;\n\n    mutex_lock(&abk_fa_app_lock);\n    if (!uid)\n        bitmap_zero(abk_fa_app_bitmap, 90000);\n    else\n        set_bit(uid - ABK_FA_APP_UID_BASE, abk_fa_app_bitmap);\n    mutex_unlock(&abk_fa_app_lock);\n\n    return 0;\n}\n\nstatic int abk_fa_app_get(char *buffer, const struct kernel_param *kp)\n{\n    unsigned int uid;\n    int len = 0;\n    int nr = 0;\n\n    mutex_lock(&abk_fa_app_lock);\n    for (uid = ABK_FA_APP_UID_BASE;\n         uid <= ABK_FA_APP_UID_LAST && nr < ABK_FA_APP_LIST_MAX; uid++) {\n        if (!test_bit(uid - ABK_FA_APP_UID_BASE, abk_fa_app_bitmap))\n            continue;\n        if (len > PAGE_SIZE - 16)\n            break;\n        len += scnprintf(buffer + len, PAGE_SIZE - len,\n                 nr ? ",%u" : "%u", uid);\n        nr++;\n    }\n    mutex_unlock(&abk_fa_app_lock);\n\n    len += scnprintf(buffer + len, PAGE_SIZE - len, "\\n");\n    return len;\n}\n\nstatic const struct kernel_param_ops abk_fa_app_param_ops = {\n    .set = abk_fa_app_set,\n    .get = abk_fa_app_get,\n};\n\nmodule_param(fault_around_enable, bool, 0644);\nMODULE_PARM_DESC(fault_around_enable,\n    "read fault-around uid policy on (default 1)");\nmodule_param(fault_around_sys_uid, bool, 0644);\nMODULE_PARM_DESC(fault_around_sys_uid,\n    "suppress fault-around for system uids below 10000 (default 1)");\nmodule_param(fault_around_app_only, bool, 0644);\nMODULE_PARM_DESC(fault_around_app_only,\n    "1: the fault_around_app list is not consulted at all; 0: a listed uid loses fault-around (default 0)");\nmodule_param_cb(fault_around_app, &abk_fa_app_param_ops, NULL, 0644);\nMODULE_PARM_DESC(fault_around_app,\n    "uid excluded from fault-around, [10000, 99999]; write 0 to clear, read the first 64");\n\nstatic void abk_fa_adjust(bool *should_around)\n{\n    uid_t uid;\n\n    if (!*should_around)\n        return;\n    if (!READ_ONCE(fault_around_enable))\n        return;\n\n    uid = abk_fa_current_uid();\n    if (uid < ABK_FA_APP_UID_BASE) {\n        if (READ_ONCE(fault_around_sys_uid))\n            *should_around = false;\n        return;\n    }\n    if (uid > ABK_FA_APP_UID_LAST)\n        return;\n    if (!READ_ONCE(fault_around_app_only) &&\n        test_bit(uid - ABK_FA_APP_UID_BASE, abk_fa_app_bitmap))\n        *should_around = false;\n}\n'

_POLICY_BLOCK = _tabs(_POLICY_DRAFT).lstrip("\n")

_DEF_OLD = (
    "static vm_fault_t do_read_fault(struct vm_fault *vmf)\n"
    "{\n"
    "\tstruct vm_area_struct *vma = vmf->vma;\n"
    "\tvm_fault_t ret = 0;\n"
    "\tbool should_around = true;\n"
)
_DEF_NEW = _POLICY_BLOCK + "\n" + _DEF_OLD

_CALL_OLD = (
    "\ttrace_android_vh_should_fault_around(vmf, &should_around);\n"
    "\tif (vma->vm_ops->map_pages && fault_around_bytes >> PAGE_SHIFT > 1 && should_around) {\n"
)
_CALL_NEW = (
    "\ttrace_android_vh_should_fault_around(vmf, &should_around);\n"
    "\t/* sailboat_fault_around_app_policy */\n"
    "\tabk_fa_adjust(&should_around);\n"
    "\tif (vma->vm_ops->map_pages && fault_around_bytes >> PAGE_SHIFT > 1 && should_around) {\n"
)

DO_READ_FAULT_PROBE = "static vm_fault_t do_read_fault(struct vm_fault *vmf)"


def build_steps():
    return [
        (MEMORY_C, _DEF_OLD, _DEF_NEW, T),
        (MEMORY_C, _CALL_OLD, _CALL_NEW, T),
    ]


def _fault_around_app_policy_apply(ctx):
    try:
        text = ctx.read(MEMORY_C)
    except OSError:
        return "blocked_by_shape", MEMORY_C + ": file absent"
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
