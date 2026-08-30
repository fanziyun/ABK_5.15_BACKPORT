"""Graft engine for the abk_5_15_backport module_set.

A *patch group* is one registered backport unit (usually one upstream 5.15.y
commit or a tightly coupled series).  Each group declares the tree shapes it
accepts and performs bounded ``replace_once``/``ensure_after`` edits through
:class:`GraftContext`.  Groups never expand beyond their declared regions:
whenever an anchor is absent the group degrades to a reported status instead
of half-patching the tree.

Status vocabulary:
    applied                  rewritten now (or would-write under dry-run)
    partial                  some hunks applied, some degraded (see detail)
    already_present          graft content already in the tree
    skip_suite_processed     another graft module already rewrote this region
    skip_f2fs_rolled_back    a storage-rollback module changed the shape
    report_only              deliberately recorded, no edit performed
    blocked_by_missing_anchor  anchor absent from this tree
    blocked_by_shape         tree shape not covered by any accepted variant
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import abk_common as common  # noqa: E402


class GraftContext:
    def __init__(self, common_dir, sub_level, family, dry_run=False):
        self.common_dir = Path(common_dir)
        self.sub_level = sub_level
        self.family = family
        self.dry_run = dry_run
        self._texts = {}
        self._dirty = set()
        self._shape_cache = {}

    # -- file access -----------------------------------------------------
    def path(self, rel):
        return self.common_dir / rel

    def read(self, rel):
        if rel not in self._texts:
            self._texts[rel] = common.read_text(self.path(rel))
        return self._texts[rel]

    def write(self, rel, text):
        self._texts[rel] = text
        if not self.dry_run:
            common.write_text(self.path(rel), text)
        self._dirty.add(rel)

    def pending_writes(self):
        return sorted(self._dirty)

    def snapshot_original(self, rel):
        """Record the pristine bytes of ``rel`` before any group edits it.

        Normally :func:`abk_common.write_text` snapshots implicitly; this
        exists for groups that want the backup taken even under dry-run.
        """
        backup = Path(str(self.path(rel)) + common.BACKUP_SUFFIX)
        if not backup.exists() and not self.dry_run:
            backup.write_bytes(self.path(rel).read_bytes())

    # -- compatibility shape probes --------------------------------------
    def suite_touched(self, rel):
        """True when ABK_ABI_PATCH_SUITE markers appear in ``rel``."""
        text = self.read(rel)
        return common.has_any(
            text,
            [common.SUITE_FEATURE_MARKER, common.SUITE_SECURITY_MARKER, common.SUITE_FD_HELPER],
        )

    def suite_fdtable_fallback(self):
        """True when the suite rewrote alloc_fdtable() into its own fallback."""
        text = self.read("fs/file.c")
        return common.SUITE_FD_FALLBACK_ALIGN in text or common.SUITE_FD_HELPER in text

    def block_rolled_back(self):
        """True when the F2FS suite's block rollback already ran."""
        if "block" not in self._shape_cache:
            text = self.read("block/blk-mq.c")
            self._shape_cache["block"] = common.F2FS_BLOCK_MONTHLY_SENTINEL not in text
        return self._shape_cache["block"]

    def fdtable_upstream_shape(self):
        """True only for the genuine upstream 5.15.191 conventions.

        The suite's fallback rewrite shares the ``slots_wanted`` signature, so
        the ALIGN line / suite helper are treated as disqualifying markers.
        """
        text = self.read("fs/file.c")
        if "alloc_fdtable(unsigned int slots_wanted)" not in text:
            return False
        if common.SUITE_FD_FALLBACK_ALIGN in text or common.SUITE_FD_HELPER in text:
            return False
        return "roundup_pow_of_two(slots_wanted)" in text


class PatchGroup:
    def __init__(self, key, summary, commits, files, apply_fn, hard=False):
        self.key = key
        self.summary = summary
        self.commits = commits
        self.files = files
        self.apply_fn = apply_fn
        self.hard = hard

    def run(self, ctx):
        try:
            status, detail = self.apply_fn(ctx)
        except SystemExit as exc:  # groups may abort with a precise reason
            if self.hard:
                raise
            status, detail = "blocked_by_shape", f"aborted: {exc}"
        except (OSError, ValueError) as exc:
            if self.hard:
                raise
            status, detail = "blocked_by_shape", f"error: {exc}"
        return {
            "key": self.key,
            "summary": self.summary,
            "commits": self.commits,
            "files": self.files,
            "status": status,
            "detail": detail,
        }


def run_child(child_name, groups, ctx, args):
    """Run every registered group and write the child report pair."""
    results = []
    for group in groups:
        before = set(ctx.pending_writes())
        results.append(group.run(ctx))
        after = set(ctx.pending_writes())
        # Safety net: a degraded group must never have touched the tree.
        if results[-1]["status"] not in ("applied", "partial", "already_present"):
            leaked = after - before
            if leaked:
                raise SystemExit(
                    f"group {group.key} degraded to {results[-1]['status']} "
                    f"but already wrote {sorted(leaked)}; refusing to continue"
                )

    applied = [r["key"] for r in results if r["status"] in ("applied", "partial", "already_present")]
    summary = {}
    for r in results:
        summary[r["status"]] = summary.get(r["status"], 0) + 1

    report = {
        "module": common.MODULE_ID,
        "child": child_name,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "family": ctx.family,
        "sub_level": ctx.sub_level,
        "dry_run": ctx.dry_run,
        "shapes": {
            "fdtable_upstream_shape": ctx.fdtable_upstream_shape(),
            "suite_fdtable_fallback": ctx.suite_fdtable_fallback(),
            "block_rolled_back": ctx.block_rolled_back(),
        },
        "pending_writes": ctx.pending_writes(),
        "applied_groups": applied,
        "status_summary": summary,
        "groups": results,
    }
    _write_report(ctx, child_name, report)
    _log_summary(child_name, report)
    return report


def _write_report(ctx, child_name, report):
    report_dir = Path(ctx.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    for suffix, writer in (
        (".json", lambda f: json.dump(report, f, indent=2)),
        (".md", lambda f: f.write(_render_markdown(report))),
    ):
        target = report_dir / f"{child_name}_report{suffix}"
        backup = Path(str(target) + common.BACKUP_SUFFIX)
        if target.exists() and not backup.exists():
            backup.write_bytes(target.read_bytes())
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            writer(handle)


def _render_markdown(report):
    lines = [
        f"# {report['child']} report",
        "",
        f"- generated: {report['generated_at_utc']}",
        f"- family: {report['family']} (SUBLEVEL {report['sub_level']})",
        f"- dry-run: {report['dry_run']}",
        f"- shapes: {json.dumps(report['shapes'])}",
        f"- status summary: {json.dumps(report['status_summary'])}",
        "",
        "| group | status | commits | detail |",
        "|---|---|---|---|",
    ]
    for group in report["groups"]:
        commits = "<br>".join(group["commits"])
        detail = group["detail"].replace("\n", " ")
        lines.append(f"| {group['key']} | {group['status']} | {commits} | {detail} |")
    lines.append("")
    return "\n".join(lines)


def _log_summary(child_name, report):
    for group in report["groups"]:
        print(f"[ABK stable_515_backport] {child_name}/{group['key']}: {group['status']}")
    print(f"[ABK stable_515_backport] {child_name}: {json.dumps(report['status_summary'])}")


def parse_args(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--common-dir", required=True)
    parser.add_argument("--defconfig", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--sub-level", required=True)
    parser.add_argument("--family", required=True)
    parser.add_argument("--dry-run", action="store_true",
                        help="compute statuses without writing any kernel file")
    return parser.parse_args()


def make_context(args):
    ctx = GraftContext(args.common_dir, args.sub_level, args.family, dry_run=args.dry_run)
    ctx.report_dir = args.report_dir
    return ctx


def apply_steps(ctx, steps):
    """Transactional anchor applier shared by every child.

    ``steps`` is a list of ``(rel, old, new, required)`` tuples evaluated in
    order against in-memory texts.  A required step that finds no anchor
    aborts the whole group without writing anything, so chained hunks stay
    build-safe (e.g. a rename plus all of its users).

    Returns (status, results, detail) with status one of
    "applied"/"partial"/"already_present" or None when a required anchor was
    missing (caller decides how to degrade).
    """
    pending = {}
    results = []
    for rel, old, new, required in steps:
        text = pending.get(rel, ctx.read(rel))
        text, status = common.replace_once(text, old, new)
        results.append((rel, status))
        if status == "missing_anchor":
            if required:
                detail = "; ".join(f"{r}:{s}" for r, s in results if s != "already_present")
                return None, results, f"required anchor missing ({detail})"
            continue
        pending[rel] = text
    wrote = []
    for rel, text in pending.items():
        if text != ctx.read(rel):
            ctx.write(rel, text)
            wrote.append(rel)
    applied = sum(1 for _r, s in results if s == "applied")
    present = sum(1 for _r, s in results if s == "already_present")
    if applied == 0 and present == len(results):
        return "already_present", results, "all anchors already in the upstream form"
    detail = f"{applied} hunk(s) applied, {present} already present"
    missing_optional = [(r, s) for r, s in results if s == "missing_anchor"]
    if missing_optional:
        detail += "; degraded: " + ", ".join(f"{r}:{s}" for r, s in missing_optional)
        return "partial", results, detail
    return "applied", results, detail
