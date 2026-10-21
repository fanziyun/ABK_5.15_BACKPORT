from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

BACKUP_SUFFIX = ".abk-orig"

MODULE_ID = "abk_5_15_backport"
MODULE_MARKER = "ABK stable_515_backport:"

SUITE_FEATURE_MARKER = "/* ABK feature_porting:"
SUITE_SECURITY_MARKER = "/* ABK security_update_backport:"
SUITE_FD_FALLBACK_ALIGN = "nr = ALIGN(slots_wanted, BITS_PER_LONG)"
SUITE_FD_HELPER = "abk_fdtable_slots_wanted"
F2FS_BLOCK_MONTHLY_SENTINEL = "delayed_work_pending(&hctx->run_work)"


def read_text(path):
    return Path(path).read_bytes().decode("utf-8")


def write_text(path, text, eol=None, backup=True):
    target = Path(path)
    if backup and not target.exists():
        raise FileNotFoundError(path)
    if backup:
        # Snapshot once; rollback restores from <file>.abk-orig.
        backup_path = Path(str(target) + BACKUP_SUFFIX)
        if not backup_path.exists():
            backup_path.write_bytes(target.read_bytes())
    target.write_bytes(text.encode("utf-8"))


def _eol_variants(block):
    # A tree may be CRLF; try both forms.
    lf = block.replace("\r\n", "\n")
    yield lf, "\n"
    yield lf.replace("\n", "\r\n"), "\r\n"


def replace_once(text, old, new):
    # The new block is checked first: a second pass must report already_present,
    # so a `new` that already exists in the pristine tree never lands.
    new_lf = new.replace("\r\n", "\n")
    if new_lf in text or new_lf.replace("\n", "\r\n") in text:
        return text, "already_present"
    for old_variant, joiner in _eol_variants(old):
        if old_variant in text:
            new_variant = new_lf.replace("\n", joiner)
            return text.replace(old_variant, new_variant, 1), "applied"
    return text, "missing_anchor"


def ensure_after(text, anchor, snippet):
    if snippet in text:
        return text, "already_present"
    if anchor not in text:
        return text, "missing_anchor"
    replacement = anchor + "\n" + snippet
    return text.replace(anchor, replacement, 1), "applied"


def has_any(text, needles):
    return any(needle in text for needle in needles)
