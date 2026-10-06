"""Batch 55 (sched_ext S2b-1): the build wiring for the Batch-54 SCX payload.

Seven groups that turn the payload from inert text into a compiled part of the
kernel.  Nothing here is a policy decision about scheduling: the groups add the
Kconfig symbol, the object rule and the glue compilation unit, give the engine
the per-task and per-CPU state it dereferences, and put ext_sched_class into the
linker's class array.  The functional hooks (fork, tick, pick, setscheduler,
idle, debugfs) are a separate batch, so a task still cannot enter SCX on this
batch -- SCHED_EXT exists but valid_policy() does not accept it yet.

Measured against the android13-5.15-lts reference tree (5.15.220):

* sched_ext_kconfig -- config SCHED_CLASS_EXT on kernel/Kconfig.preempt with the
  6.6 dependency "depends on BPF_SYSCALL && BPF_JIT" (the trampoline and the
  kfunc allow-list are Batch 51/52/53's, and a struct_ops program needs both).
  The symbol is what decides whether the 143 KB engine is in the build.
* sched_ext_uapi -- #define SCHED_EXT 7 in include/uapi/linux/sched.h, the value
  the engine compares p->policy against.
* sched_ext_task_slot -- include/linux/sched/ext.h included from
  include/linux/sched.h (the 6.6 tree includes it at the same point), and the
  per-task "struct sched_ext_entity *scx" claimed in the task_struct KABI
  reserve slot 7.  Slot 8 is already this module's (the Batch 16 kstack_offset),
  so this group anchors on the post-randomize_kstack_pertask run and is
  registered after it; the earlier group's own kstack_offset); shape probe keeps
  the pair idempotent (group_recipe trap 5 -- Batch 55 also repaired that probe,
  which spelled the member without its closing parenthesis and so never fired).
* sched_ext_rq_state -- struct scx_rq (plus enum scx_rq_flags) and the struct rq
  pointer to it, sched_class_above() (5.15 walks the class array with class--
  from __end_sched_classes - 1, so "above" is the greater address -- the 6.6
  spelling has the opposite walk and the opposite polarity), and
  #include "ext.h" at the end of kernel/sched/sched.h, which is the only place
  the two headers may meet: kernel/sched/ext.h has no include guard and cannot
  be included twice.
* sched_ext_class_order -- *(__ext_sched_class) in SCHED_DATA.  5.15 orders the
  section idle-first and walks it downwards, so the entry goes between
  __idle_sched_class and __fair_sched_class and the resulting order is stop, dl,
  rt, fair, ext, idle -- which is also what keeps the two "&class + 1"
  assertions in sched_init() true.
* sched_ext_build -- obj-$(CONFIG_SCHED_CLASS_EXT) += sched_ext_glue.o in
  kernel/sched/Makefile.  Not ext.o: ext.c has no include block of its own and
  is compiled on the 6.6 tree by textual inclusion from
  kernel/sched/build_policy.c; 5.15 has no build_policy.c (its policy files are
  separate objects) and OPPO's cannot be shipped verbatim either (it also
  includes idle.c/rt.c/cpudeadline.c/pelt.c/cputime.c/deadline.c, whose
  duplicate symbols would collide).  The glue file is a module-authored fourth
  payload, created by abk_stable_backport_overlay_sched_ext().
* sched_ext_init -- init_sched_ext_class() from sched_init() and the class-order
  assertions extended for the new rung.  The call is what allocates rq->scx per
  CPU: with the class in the array, the existing for_each_class() loop reaches
  pick_next_task_scx() even while SCX is disabled, and its first_local_task()
  dereferences rq->scx.

Deliberately not in this batch: valid_policy() / fair_policy() /
normal_policy(), so sched_setscheduler(SCHED_EXT) is still rejected and no task
can be put on the class; the fork hooks (scx_pre_fork/scx_fork/scx_cancel_fork/
scx_post_fork), the tick watchdog and pick-notify hooks, the setscheduler gate,
scx_update_idle() and the debugfs dump.  They are the next batch, and until they
land the class is registered but unreachable -- which is exactly the property
this half is proven against.
"""

from __future__ import annotations

from abk_backport_engine import apply_steps

KCONFIG = "kernel/Kconfig.preempt"
UAPI_SCHED_H = "include/uapi/linux/sched.h"
SCHED_H = "include/linux/sched.h"
KSCHED_H = "kernel/sched/sched.h"
VMLINUX_LDS_H = "include/asm-generic/vmlinux.lds.h"
KSCHED_MAKEFILE = "kernel/sched/Makefile"
CORE_C = "kernel/sched/core.c"


# ---------------------------------------------------------------------------
# kernel/Kconfig.preempt: the new symbol.
# ---------------------------------------------------------------------------

_KCONFIG_OLD = (
    "\t  SCHED_CORE is default disabled. When it is enabled and unused,\n"
    "\t  which is the likely usage by Linux distributions, there should\n"
    "\t  be no measurable impact on performance.\n"
)

_KCONFIG_NEW = _KCONFIG_OLD + (
    "\n"
    "# sailboat_sched_ext: the sched_ext (SCX) scheduler class.  The payload is\n"
    "# three files created by this module plus kernel/sched/sched_ext_glue.c (the\n"
    "# compilation unit that supplies the include block ext.c does not carry), so\n"
    "# this symbol is what decides whether the 143 KB engine is in the build at\n"
    "# all.  The dependency is the 6.6 tree's: a SCX scheduler is a BPF struct_ops\n"
    "# program, which needs the verifier (BPF_SYSCALL) and the arm64 JIT's kfunc\n"
    "# and trampoline support (BPF_JIT) landed by Batch 51/52/53.\n"
    "config SCHED_CLASS_EXT\n"
    "\tbool \"Extensible Scheduling Class\"\n"
    "\tdepends on BPF_SYSCALL && BPF_JIT\n"
    "\thelp\n"
    "\t  This option enables a new scheduler class, sched_ext (SCX), whose\n"
    "\t  scheduling policy is a BPF struct_ops program instead of C.  A loaded\n"
    "\t  SCX scheduler owns only the tasks explicitly marked SCHED_EXT unless\n"
    "\t  it asks for the whole machine; everything else keeps running on the\n"
    "\t  built-in classes.\n"
    "\n"
    "\t  Say N unless you intend to load a BPF scheduler.\n"
)


def _sched_ext_kconfig_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (KCONFIG, _KCONFIG_OLD, _KCONFIG_NEW, True),
    ])
    return status, detail


# ---------------------------------------------------------------------------
# include/uapi/linux/sched.h: the policy id.
# ---------------------------------------------------------------------------

_UAPI_OLD = (
    "#define SCHED_IDLE\t\t5\n"
    "#define SCHED_DEADLINE\t\t6\n"
)

_UAPI_NEW = (
    "#define SCHED_IDLE\t\t5\n"
    "#define SCHED_DEADLINE\t\t6\n"
    "/* sailboat_sched_ext: SCX's policy id.  A task carries it until the SCX\n"
    " * scheduler's init() callback has run, so the engine compares p->policy\n"
    " * against it.  Same value as the 6.6 tree; SCHED_EXT is not accepted by\n"
    " * sched_setscheduler() until the functional wiring batch lands. */\n"
    "#define SCHED_EXT\t\t7\n"
)


def _sched_ext_uapi_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (UAPI_SCHED_H, _UAPI_OLD, _UAPI_NEW, True),
    ])
    return status, detail


# ---------------------------------------------------------------------------
# include/linux/sched.h: the header include and the KABI slot (7).
# ---------------------------------------------------------------------------

_SCHED_H_INCLUDE_OLD = (
    "struct task_delay_info;\n"
    "struct task_group;\n"
)

_SCHED_H_INCLUDE_NEW = (
    "struct task_delay_info;\n"
    "struct task_group;\n"
    "\n"
    "/*\n"
    " * sailboat_sched_ext: struct sched_ext_entity (a task's SCX state) and\n"
    " * struct sched_ext_ops.  The 6.6 tree includes the header from the same\n"
    " * spot; only struct task_struct has to be visible here, and asm/current.h\n"
    " * has already declared it.  Included unconditionally so that the task_struct\n"
    " * KABI slot below can name the type on a build with the class compiled out.\n"
    " */\n"
    "#include <linux/sched/ext.h>\n"
)

# The Batch 16 kstack group claims task_struct slot 8 and leaves the free run
# 2..7; SCX takes 7.  Anchoring on the post-kstack text is what makes the two
# coexist, and the earlier group's own kstack_offset; probe is what keeps it
# idempotent once this group rewrites the run it appended.
_SCHED_H_SLOT_OLD = (
    "\tANDROID_KABI_RESERVE(6);\n"
    "\tANDROID_KABI_RESERVE(7);\n"
    "\t/* ABK stable_515_backport: per-task kstack randomization offset (5.15.210) mapped onto the KABI reserve slot. */\n"
    "\tANDROID_KABI_USE(8, u32\t\t\tkstack_offset);\n"
)

_SCHED_H_SLOT_NEW = (
    "\tANDROID_KABI_RESERVE(6);\n"
    "\t/* sailboat_sched_ext: per-task SCX state pointer, one pointer in one KABI\n"
    "\t * reserve slot.  struct sched_ext_entity is far larger than a slot, so the\n"
    "\t * relation has to be indirect. */\n"
    "\tANDROID_KABI_USE(7, struct sched_ext_entity *scx);\n"
    "\t/* ABK stable_515_backport: per-task kstack randomization offset (5.15.210) mapped onto the KABI reserve slot. */\n"
    "\tANDROID_KABI_USE(8, u32\t\t\tkstack_offset);\n"
)


def _sched_ext_task_slot_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (SCHED_H, _SCHED_H_INCLUDE_OLD, _SCHED_H_INCLUDE_NEW, True),
        (SCHED_H, _SCHED_H_SLOT_OLD, _SCHED_H_SLOT_NEW, True),
    ])
    return status, detail


# ---------------------------------------------------------------------------
# kernel/sched/sched.h: struct scx_rq, rq->scx, sched_class_above, ext.h.
# ---------------------------------------------------------------------------

_KSCHED_H_RQCOMMENT = (
    "/*\n"
    " * This is the main, per-CPU runqueue data structure.\n"
)

_KSCHED_H_SCXRQ_NEW = (
    "#ifdef CONFIG_SCHED_CLASS_EXT\n"
    "/* sailboat_sched_ext: SCX's per-CPU state; the flags are protected by the\n"
    " * rq lock.  Fields are exactly the ones ext.c dereferences through rq->scx\n"
    " * (local_dsq, watchdog_list, ops_qseq, extra_enq_flags, nr_running, flags,\n"
    " * cpu_released, cpus_to_kick/preempt/wait, pnt_seq, kick_cpus_irq_work, rq). */\n"
    "enum scx_rq_flags {\n"
    "\tSCX_RQ_CAN_STOP_TICK\t= 1 << 0,\n"
    "};\n"
    "\n"
    "struct scx_rq {\n"
    "\tstruct scx_dispatch_q\tlocal_dsq;\n"
    "\tstruct list_head\twatchdog_list;\n"
    "\tu64\t\t\tops_qseq;\n"
    "\tu64\t\t\textra_enq_flags;\t/* see move_task_to_local_dsq() */\n"
    "\tu32\t\t\tnr_running;\n"
    "\tu32\t\t\tflags;\n"
    "\tbool\t\t\tcpu_released;\n"
    "\tcpumask_var_t\t\tcpus_to_kick;\n"
    "\tcpumask_var_t\t\tcpus_to_preempt;\n"
    "\tcpumask_var_t\t\tcpus_to_wait;\n"
    "\tu64\t\t\tpnt_seq;\n"
    "\tstruct irq_work\t\tkick_cpus_irq_work;\n"
    "\tstruct rq\t\t*rq;\n"
    "};\n"
    "#endif /* CONFIG_SCHED_CLASS_EXT */\n"
    "\n"
) + _KSCHED_H_RQCOMMENT

_KSCHED_H_RQ_OLD = (
    "\tANDROID_OEM_DATA_ARRAY(1, 16);\n"
    "\n"
    "\tANDROID_KABI_RESERVE(1);\n"
)

_KSCHED_H_RQ_NEW = (
    "\tANDROID_OEM_DATA_ARRAY(1, 16);\n"
    "\n"
    "#ifdef CONFIG_SCHED_CLASS_EXT\n"
    "\t/* sailboat_sched_ext: this CPU's SCX state, allocated in\n"
    "\t * init_sched_ext_class().  One pointer in rq's own KABI reserve slot 1. */\n"
    "\tANDROID_KABI_USE(1, struct scx_rq *scx);\n"
    "#else\n"
    "\tANDROID_KABI_RESERVE(1);\n"
    "#endif\n"
)

_KSCHED_H_FOREACH_OLD = (
    "#define for_each_class(class) \\\n"
    "\tfor_class_range(class, sched_class_highest, sched_class_lowest)\n"
)

_KSCHED_H_FOREACH_NEW = _KSCHED_H_FOREACH_OLD + (
    "\n"
    "/*\n"
    " * sailboat_sched_ext: ext.c asks whether one class outranks another.  5.15\n"
    " * lays the class array out idle-first and walks it downwards with class--\n"
    " * (sched_class_highest is __end_sched_classes - 1), so a class is above\n"
    " * another when its address is greater; the 6.6 tree spells the same polarity\n"
    " * over its reversed layout and an ascending walk.\n"
    " */\n"
    "#define sched_class_above(_a, _b)\t((_a) > (_b))\n"
)

_KSCHED_H_TAIL_OLD = "#endif /* CONFIG_RT_SOFTINT_OPTIMIZATION */\n"

_KSCHED_H_TAIL_NEW = (
    "#endif /* CONFIG_RT_SOFTINT_OPTIMIZATION */\n"
    "\n"
    "/*\n"
    " * sailboat_sched_ext: the SCX hook prototypes and the inline class-skip\n"
    " * helpers.  This has to be the last include in the file (the 6.6 tree has it\n"
    " * at the same place): the header needs struct rq, struct task_group and\n"
    " * fair_sched_class, and it has no include guard, so it may be included\n"
    " * exactly once.  struct file_operations and struct bpf_verifier_ops are\n"
    " * declared for the two extern objects it names; a redundant declaration of an\n"
    " * already-complete type is legal.\n"
    " */\n"
    "struct file_operations;\n"
    "struct bpf_verifier_ops;\n"
    "\n"
    "#include \"ext.h\"\n"
)


def _sched_ext_rq_state_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (KSCHED_H, _KSCHED_H_RQCOMMENT, _KSCHED_H_SCXRQ_NEW, True),
        (KSCHED_H, _KSCHED_H_RQ_OLD, _KSCHED_H_RQ_NEW, True),
        (KSCHED_H, _KSCHED_H_FOREACH_OLD, _KSCHED_H_FOREACH_NEW, True),
        (KSCHED_H, _KSCHED_H_TAIL_OLD, _KSCHED_H_TAIL_NEW, True),
    ])
    return status, detail


# ---------------------------------------------------------------------------
# include/asm-generic/vmlinux.lds.h: the class-array slot.
# ---------------------------------------------------------------------------

_LDS_OLD = (
    "\t*(__idle_sched_class)\t\t\t\\\n"
    "\t*(__fair_sched_class)\t\t\t\\\n"
)

_LDS_NEW = (
    "\t*(__idle_sched_class)\t\t\t\\\n"
    "\t*(__ext_sched_class)\t\t\t\\\n"
    "\t*(__fair_sched_class)\t\t\t\\\n"
)


def _sched_ext_class_order_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (VMLINUX_LDS_H, _LDS_OLD, _LDS_NEW, True),
    ])
    return status, detail


# ---------------------------------------------------------------------------
# kernel/sched/Makefile: the object rule.
# ---------------------------------------------------------------------------

_MAKEFILE_OLD = "obj-$(CONFIG_ANDROID_VENDOR_HOOKS) += vendor_hooks.o\n"

_MAKEFILE_NEW = _MAKEFILE_OLD + (
    "\n"
    "# sailboat_sched_ext: the SCX engine is built through a module-authored glue\n"
    "# translation unit, not as ext.o directly -- ext.c carries no include block of\n"
    "# its own and is meant to be textually included from kernel/sched/\n"
    "# build_policy.c, which 5.15 does not have (its policy files are separate\n"
    "# objects).  sched_ext_glue.c supplies the headers and includes ext.c; the\n"
    "# file is created by the module's sched_ext overlay.\n"
    "obj-$(CONFIG_SCHED_CLASS_EXT) += sched_ext_glue.o\n"
)


def _sched_ext_build_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (KSCHED_MAKEFILE, _MAKEFILE_OLD, _MAKEFILE_NEW, True),
    ])
    return status, detail


# ---------------------------------------------------------------------------
# kernel/sched/core.c: class order and init_sched_ext_class().
# ---------------------------------------------------------------------------

_CORE_BUGON_OLD = (
    "\t/* Make sure the linker didn't screw up */\n"
    "\tBUG_ON(&idle_sched_class + 1 != &fair_sched_class ||\n"
    "\t       &fair_sched_class + 1 != &rt_sched_class ||\n"
    "\t       &rt_sched_class + 1   != &dl_sched_class);\n"
)

_CORE_BUGON_NEW = (
    "\t/* Make sure the linker didn't screw up */\n"
    "#ifdef CONFIG_SCHED_CLASS_EXT\n"
    "\t/* sailboat_sched_ext: SCHED_DATA puts __ext_sched_class between the\n"
    "\t * idle and fair classes, so the rung order is idle, ext, fair, rt, dl,\n"
    "\t * stop. */\n"
    "\tBUG_ON(&idle_sched_class + 1 != &ext_sched_class ||\n"
    "\t       &ext_sched_class + 1 != &fair_sched_class);\n"
    "#endif\n"
    "\tBUG_ON(&fair_sched_class + 1 != &rt_sched_class ||\n"
    "\t       &rt_sched_class + 1   != &dl_sched_class);\n"
)

_CORE_INIT_OLD = (
    "\tinit_uclamp();\n"
    "\n"
    "\tscheduler_running = 1;\n"
)

_CORE_INIT_NEW = (
    "\tinit_uclamp();\n"
    "\n"
    "#ifdef CONFIG_SCHED_CLASS_EXT\n"
    "\t/*\n"
    "\t * sailboat_sched_ext: allocate each CPU's rq->scx.  Required even while\n"
    "\t * SCX is disabled: the class is in the SCHED_DATA array, so the existing\n"
    "\t * for_each_class() walk in __pick_next_task() reaches pick_next_task_scx()\n"
    "\t * and its first_local_task() dereferences rq->scx.  Safe before the\n"
    "\t * functional hooks land: no task can be put on the class yet, so every\n"
    "\t * pick returns NULL and the idle class still wins.\n"
    "\t */\n"
    "\tinit_sched_ext_class();\n"
    "#endif\n"
    "\n"
    "\tscheduler_running = 1;\n"
)


def _sched_ext_init_apply(ctx):
    status, _results, detail = apply_steps(ctx, [
        (CORE_C, _CORE_BUGON_OLD, _CORE_BUGON_NEW, True),
        (CORE_C, _CORE_INIT_OLD, _CORE_INIT_NEW, True),
    ])
    return status, detail


def build_groups(PatchGroup):
    """Return the seven PatchGroup records, in registration order."""
    return [
        PatchGroup(
            "sched_ext_kconfig",
            "the CONFIG_SCHED_CLASS_EXT symbol (kernel/Kconfig.preempt), with "
            "the 6.6 dependency on BPF_SYSCALL && BPF_JIT.  Bool, no default, so "
            "the module tier is what turns it on: without a tier entry the "
            "three-file payload and its glue translation unit would never "
            "compile, which is the Batch 8 RCU failure mode one config layer "
            "down.  tests/stable_5_15_test.py asserts the tier table and "
            "_INTRODUCED_KCONFIG agree.",
            ["no upstream commit: the symbol is the 6.6 tree's, and this "
             "module's tier enables it"],
            [KCONFIG],
            _sched_ext_kconfig_apply,
        ),
        PatchGroup(
            "sched_ext_uapi",
            "SCHED_EXT (policy id 7) in include/uapi/linux/sched.h, the value "
            "kernel/sched/ext.c compares p->policy against.  The id is defined "
            "but not accepted by sched_setscheduler() until the functional "
            "wiring batch extends valid_policy()/normal_policy(); until then a "
            "task cannot enter the class, which is what makes the class-"
            "registered-but-unreachable state of this batch safe.",
            ["no upstream commit: the id is the 6.6 tree's"],
            [UAPI_SCHED_H],
            _sched_ext_uapi_apply,
        ),
        PatchGroup(
            "sched_ext_task_slot",
            "the per-task SCX state: include/linux/sched/ext.h included from "
            "include/linux/sched.h at the position the 6.6 tree uses, and "
            "struct sched_ext_entity *scx claimed in task_struct's KABI reserve "
            "slot 7.  struct sched_ext_entity is much larger than one slot, so "
            "the task relation has to be a pointer; slot 8 is already this "
            "module's kstack_offset, so the group anchors on the "
            "post-randomize_kstack_pertask run and is registered after it "
            "(group_recipe trap 5 -- the earlier group's kstack_offset; probe "
            "keeps the rewrite idempotent).",
            ["no upstream commit: the KABI slot is this module's, the struct is "
             "the Batch-54 payload header"],
            [SCHED_H],
            _sched_ext_task_slot_apply,
        ),
        PatchGroup(
            "sched_ext_rq_state",
            "the state kernel/sched/ext.c dereferences: struct scx_rq with "
            "exactly the fields it uses, the rq pointer to it in rq's KABI "
            "reserve slot 1, sched_class_above() adapted to 5.15's idle-first "
            "class array (the 6.6 spelling assumes a reversed array and an "
            "ascending walk), and #include \"ext.h\" at the end of "
            "kernel/sched/sched.h -- the only position where the two headers "
            "may meet, because kernel/sched/ext.h has no include guard and "
            "needs struct rq, struct task_group and fair_sched_class in scope.  "
            "struct file_operations and struct bpf_verifier_ops are declared "
            "for the two extern objects ext.h names.",
            ["no upstream commit: the struct shape is OPPO's 6.6 kernel/sched/"
             "sched.h, the macro adaptation is measured on this tree"],
            [KSCHED_H],
            _sched_ext_rq_state_apply,
        ),
        PatchGroup(
            "sched_ext_class_order",
            "the SCHED_DATA slot that puts ext_sched_class in the linker's "
            "class array.  It goes between __idle_sched_class and "
            "__fair_sched_class: 5.15 orders the sections idle-first and walks "
            "them downwards from __end_sched_classes - 1, so the traversal "
            "becomes stop, dl, rt, fair, ext, idle, which is also the order the "
            "two &class + 1 assertions in sched_init() then check.  Without "
            "this entry the class object lands in an orphan section and "
            "for_each_class() never sees it.",
            ["no upstream commit: this is 5.15's linker-script convention"],
            [VMLINUX_LDS_H],
            _sched_ext_class_order_apply,
        ),
        PatchGroup(
            "sched_ext_build",
            "obj-$(CONFIG_SCHED_CLASS_EXT) += sched_ext_glue.o.  The engine is "
            "NOT built as ext.o: ext.c has no include block and is compiled on "
            "the 6.6 tree by textual inclusion from kernel/sched/build_policy.c, "
            "which 5.15 does not have; OPPO's cannot be shipped either, because "
            "it also includes idle.c/rt.c/cpudeadline.c/pelt.c/cputime.c/"
            "deadline.c whose objects 5.15 already builds.  The glue file is "
            "module-authored and created by abk_stable_backport_overlay_sched_"
            "ext(); this Makefile line is also what the overlay's wired probe "
            "looks for before it writes the empty .abk-orig diff base.",
            ["no upstream commit: this is 5.15's build convention"],
            [KSCHED_MAKEFILE],
            _sched_ext_build_apply,
        ),
        PatchGroup(
            "sched_ext_init",
            "init_sched_ext_class() called from sched_init(), and the "
            "&class + 1 assertions extended for the new rung.  With the class "
            "in the array the existing for_each_class() walk reaches "
            "pick_next_task_scx() even while SCX is disabled, and "
            "first_local_task() dereferences rq->scx -- so the call is what "
            "keeps a registered-but-disabled class from faulting.  It is called "
            "where the 6.6 tree calls it, just before scheduler_running = 1.",
            ["no upstream commit: the call site is the 6.6 tree's"],
            [CORE_C],
            _sched_ext_init_apply,
        ),
    ]
