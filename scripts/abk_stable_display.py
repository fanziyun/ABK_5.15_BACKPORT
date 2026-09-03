"""Child ``stable_display_fix``: display-bringup revert for android13-5.15.

Removes the upstream ``drm: Add valid clones check`` graft (5.15.185, from the
Concurrent Writeback series) from ``drivers/gpu/drm/drm_atomic_helper.c``.

That check validates that every encoder in a CRTC's ``encoder_mask`` has a
``possible_clones`` mask covering the whole ``encoder_mask``, and returns
``-EINVAL`` when it does not.  The vendor ``msm_drm`` display module (built
against the pre-5.15.185 KMI) drives the dual-pipe split display and
concurrent writeback with an encoder mask that does not satisfy the new
check, so on 5.15.185+ (2025-07 / 2025-09 / 2025-12 monthly branches and the
``android13-5.15-lts`` branch) every atomic commit fails with ``-EINVAL``,
the compositor retries forever, and the panel stays black while touch,
fingerprint and the rest of the system keep working.

On baselines that never carried the check (5.15.167 / 5.15.178 / 5.15.180)
the group reports ``already_present``: removing the check restores exactly
the pre-5.15.185 upstream shape, so the tree is already in the target form
and nothing is written.

The revert is pure deletion: the two ``replace_once`` steps carry enough
surrounding context in both old and new blocks that the removal cannot
short-circuit on a pre-existing replacement (the ``replace_once`` idempotency
pre-check) and cannot collide with the other ``drm_atomic_add_affected_planes``
call in this file.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from abk_backport_engine import (  # noqa: E402
    PatchGroup,
    apply_steps,
    make_context,
    parse_args,
    run_child,
)

T = True  # required step


# ---------------------------------------------------------------------------
# drm: Add valid clones check (5.15.185) — removal restores the pre-185 shape.
# ---------------------------------------------------------------------------

# The function sits between mode_valid_path() and the drm_atomic_helper_
# check_modeset() doc comment.  The old block carries the unique tail of the
# preceding function (the mode_status != MODE_OK bail-out) plus the head of
# the following doc comment so the replacement (the pre-185 shape) is both
# unique and non-empty.  The preceding anchor must extend past the bare
# "return 0; }" tail: the function's own body ends with the same tail, so a
# replacement anchored only on that tail would be a suffix of the old block,
# trip replace_once's idempotency pre-check and never apply.  A pure empty
# replacement has the same problem.
_VC_FN_OLD = (
    "\t\tif (mode_status != MODE_OK)\n"
    "\t\t\treturn -EINVAL;\n"
    "\t}\n"
    "\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
    "static int drm_atomic_check_valid_clones(struct drm_atomic_state *state,\n"
    "\t\t\t\t\t struct drm_crtc *crtc)\n"
    "{\n"
    "\tstruct drm_encoder *drm_enc;\n"
    "\tstruct drm_crtc_state *crtc_state = drm_atomic_get_new_crtc_state(state,\n"
    "\t\t\t\t\t\t\t\t\t  crtc);\n"
    "\n"
    "\tdrm_for_each_encoder_mask(drm_enc, crtc->dev, crtc_state->encoder_mask) {\n"
    "\t\tif (!drm_enc->possible_clones) {\n"
    "\t\t\tDRM_DEBUG(\"enc%d possible_clones is 0\\n\", drm_enc->base.id);\n"
    "\t\t\tcontinue;\n"
    "\t\t}\n"
    "\n"
    "\t\tif ((crtc_state->encoder_mask & drm_enc->possible_clones) !=\n"
    "\t\t    crtc_state->encoder_mask) {\n"
    "\t\t\tDRM_DEBUG(\"crtc%d failed valid clone check for mask 0x%x\\n\",\n"
    "\t\t\t\t  crtc->base.id, crtc_state->encoder_mask);\n"
    "\t\t\treturn -EINVAL;\n"
    "\t\t}\n"
    "\t}\n"
    "\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
    "/**\n"
    " * drm_atomic_helper_check_modeset - validate state object for modeset changes\n"
)

_VC_FN_NEW = (
    "\t\tif (mode_status != MODE_OK)\n"
    "\t\t\treturn -EINVAL;\n"
    "\t}\n"
    "\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
    "/**\n"
    " * drm_atomic_helper_check_modeset - validate state object for modeset changes\n"
)

# The call site inside drm_atomic_helper_check_modeset().  The old block
# carries the unique drm_atomic_check_valid_clones() call; the new block is
# anchored by the following "Iterate over all connectors again" comment so it
# cannot collide with the other drm_atomic_add_affected_planes() call in
# drm_atomic_helper_disable_all().
_VC_CALL_OLD = (
    "\t\tret = drm_atomic_add_affected_planes(state, crtc);\n"
    "\t\tif (ret != 0)\n"
    "\t\t\treturn ret;\n"
    "\n"
    "\t\tret = drm_atomic_check_valid_clones(state, crtc);\n"
    "\t\tif (ret != 0)\n"
    "\t\t\treturn ret;\n"
    "\t}\n"
    "\n"
    "\t/*\n"
    "\t * Iterate over all connectors again, to make sure atomic_check()\n"
)

_VC_CALL_NEW = (
    "\t\tret = drm_atomic_add_affected_planes(state, crtc);\n"
    "\t\tif (ret != 0)\n"
    "\t\t\treturn ret;\n"
    "\t}\n"
    "\n"
    "\t/*\n"
    "\t * Iterate over all connectors again, to make sure atomic_check()\n"
)


def _drm_valid_clones_revert_apply(ctx):
    steps = [
        ("drivers/gpu/drm/drm_atomic_helper.c", _VC_FN_OLD, _VC_FN_NEW, T),
        ("drivers/gpu/drm/drm_atomic_helper.c", _VC_CALL_OLD, _VC_CALL_NEW, T),
    ]
    status, _results, detail = apply_steps(ctx, steps)
    if status is None:
        return "blocked_by_shape", detail
    return status, detail


PATCH_GROUPS = [
    PatchGroup(
        "drm_valid_clones_revert",
        "remove the 5.15.185 drm valid-clones encoder check so vendor msm_drm atomic commits stop failing with -EINVAL (fixes the black screen on 5.15.185+/lts)",
        ["revert of upstream 5.15.185 \"drm: Add valid clones check\" (Concurrent Writeback series)"],
        ["drivers/gpu/drm/drm_atomic_helper.c"],
        _drm_valid_clones_revert_apply,
    ),
]


def main():
    args = parse_args("stable_display_fix: remove the 5.15.185 drm valid-clones check")
    ctx = make_context(args)
    enabled = ctx.family == "android13-5.15" or args.allow_unsupported
    if not enabled:
        print("[ABK stable_515_backport] unsupported family "
              f"{ctx.family}; every group reports report_only and nothing is "
              "written (pass --allow-unsupported to override)")
    run_child("stable_display_fix", PATCH_GROUPS, ctx, args, enabled=enabled)


if __name__ == "__main__":
    main()
