"""Where the tree-level audits get the files this module *creates*.

Both audits build their fixture by copying every file a PatchGroup declares out
of the fetched reference tree.  The sched_ext payload files do not exist in any
upstream branch -- abk_stable_backport_overlay_sched_ext() materialises them from
files/ during a real graft -- so a group that targets one of them
(batch57_perf_sched_ext_adapt edits kernel/sched/ext.c) would otherwise fail the
audit with "source tree is missing <rel>", which reads like a broken reference
tree instead of the legitimate created-file case it is.

resolve() therefore falls back to the archived payload for exactly these paths,
which is what the overlay would have put in the tree.  Anything else is still a
hard failure.
"""

from __future__ import annotations

from pathlib import Path

# The four paths abk_stable_backport_overlay_sched_ext() creates.  Keep in sync
# with the loop in scripts/stable_backport.sh and with files/README.md; the three
# upstream files are additionally sha256-pinned in tests/stable_5_15_test.py.
PAYLOAD_FILES = (
    "include/linux/sched/ext.h",
    "kernel/sched/ext.h",
    "kernel/sched/ext.c",
    "kernel/sched/sched_ext_glue.c",
)


def resolve(source, rel, module_dir):
    """Return the file the fixture should copy for *rel*, or None."""
    src = Path(source) / rel
    if src.is_file():
        return src
    if rel in PAYLOAD_FILES:
        payload = Path(module_dir) / "files" / rel
        if payload.is_file():
            return payload
    return None
