"""Shared low-level helpers for the abk_5_15_backport child scripts.

Conventions:
- Every write snapshots the original file to ``<file>.abk-orig`` exactly once;
  ``scripts/abk_rollback.sh`` restores from those snapshots.
- All graft content carries a distinctive ``ABK stable_515_backport`` marker
  that doubles as the idempotency anchor and keeps this module's edits
  distinguishable from any other module's edits in the same tree.

EOL handling: checkouts of the same tree can surface LF on Linux CI or CRLF
on Windows.  Block matching tries both line-ending forms and the replacement
is emitted with whichever form matched, so edits stay byte-faithful to their
surrounding region.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

BACKUP_SUFFIX = ".abk-orig"

MODULE_ID = "abk_5_15_backport"
MODULE_MARKER = "ABK stable_515_backport:"

# Sibling-module shape markers used by the compatibility probes.
SUITE_FEATURE_MARKER = "/* ABK feature_porting:"
SUITE_SECURITY_MARKER = "/* ABK security_update_backport:"
SUITE_FD_FALLBACK_ALIGN = "nr = ALIGN(slots_wanted, BITS_PER_LONG)"
SUITE_FD_HELPER = "abk_fdtable_slots_wanted"
# Present in the monthly block/blk-mq.c; the ABK_F2FS_FIX_MODULE block
# rollback (git apply --reverse of android13-5.15-*-*.patch) removes it.
F2FS_BLOCK_MONTHLY_SENTINEL = "delayed_work_pending(&hctx->run_work)"


def read_text(path):
    """Return the decoded file contents verbatim (EOLs preserved)."""
    return Path(path).read_bytes().decode("utf-8")


def write_text(path, text, eol=None, backup=True):
    """Write ``text`` verbatim (callers keep the file's own EOL style).

    Snapshots the original bytes to ``<path>.abk-orig`` on first write so the
    rollback script can restore the pre-module state.
    """
    target = Path(path)
    if backup and not target.exists():
        raise FileNotFoundError(path)
    if backup:
        backup_path = Path(str(target) + BACKUP_SUFFIX)
        if not backup_path.exists():
            backup_path.write_bytes(target.read_bytes())
    target.write_bytes(text.encode("utf-8"))


def count_needles(text, needles):
    return {needle: text.count(needle) for needle in needles}


def _eol_variants(block):
    """Yield (variant, joiner) pairs for CRLF-tolerant block matching."""
    lf = block.replace("\r\n", "\n")
    yield lf, "\n"
    yield lf.replace("\n", "\r\n"), "\r\n"


def replace_once(text, old, new):
    """Idempotent-friendly single replacement, tolerant of mixed EOL files.

    The search block is tried in its LF form and in its CRLF form; the
    replacement is emitted with whichever line ending matched so edits stay
    byte-faithful to their surrounding region.

    Returns (text, status) with status one of:
    - "applied": ``old`` found and replaced.
    - "already_present": ``new`` already in the text (no change).
    - "missing_anchor": neither found.
    """
    new_lf = new.replace("\r\n", "\n")
    if new_lf in text or new_lf.replace("\n", "\r\n") in text:
        return text, "already_present"
    for old_variant, joiner in _eol_variants(old):
        if old_variant in text:
            new_variant = new_lf.replace("\n", joiner)
            return text.replace(old_variant, new_variant, 1), "applied"
    return text, "missing_anchor"


def replace_once_any(text, variants_old, new):
    """Apply :func:`replace_once` with the first matching old-block variant."""
    for old in variants_old:
        text, status = replace_once(text, old, new)
        if status != "missing_anchor":
            return text, status
    return text, "missing_anchor"


def ensure_after(text, anchor, snippet):
    """Insert ``snippet`` directly after the ``anchor`` line (once).

    Returns (text, status): "applied", "already_present", or "missing_anchor".
    """
    if snippet in text:
        return text, "already_present"
    if anchor not in text:
        return text, "missing_anchor"
    replacement = anchor + "\n" + snippet
    return text.replace(anchor, replacement, 1), "applied"


def has_any(text, needles):
    return any(needle in text for needle in needles)
