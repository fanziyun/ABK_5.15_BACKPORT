from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import abk_common as common  # noqa: E402


class GraftContext:
    def __init__(self, common_dir, sub_level, family, defconfig=None, dry_run=False):
        self.common_dir = Path(common_dir)
        self.sub_level = sub_level
        self.family = family
        self.dry_run = dry_run
        self.defconfig = Path(defconfig) if defconfig else None
        self._texts = {}
        self._dirty = set()
        self._shape_cache = {}
        self._defconfig_rel = self._resolve_defconfig()

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

    def _resolve_defconfig(self):
        # Outside common_dir is refused: rollback cannot restore it.
        if self.defconfig is None:
            return None
        try:
            return str(
                self.defconfig.resolve().relative_to(self.common_dir.resolve())
            )
        except (ValueError, OSError):
            return None

    def defconfig_rel(self):
        return self._defconfig_rel

    def enable_configs(self, configs):
        # Three shapes: already set (no-op), set to another value or "is not
        # set" (rewrite in place), absent (append under one marker).
        rel = self.defconfig_rel()
        if rel is None:
            reason = ("no defconfig passed" if self.defconfig is None
                      else "defconfig outside KERNEL_ROOT")
            return "report_only", reason

        text = self.read(rel)
        joiner = "\r\n" if "\r\n" in text else "\n"
        marker = "# " + common.MODULE_MARKER + " config_enablement"
        appended = rewritten = 0

        for name, value in configs:
            target = f"CONFIG_{name}={value}"
            lines = text.split(joiner)
            if target in lines:
                continue
            for index, line in enumerate(lines):
                if line == f"# CONFIG_{name} is not set" or (
                        line.startswith(f"CONFIG_{name}=") and line != target):
                    lines[index] = target
                    rewritten += 1
                    break
            else:
                if marker not in lines:
                    lines.extend(["", marker])
                lines.append(target)
                appended += 1
            text = joiner.join(lines)

        if not appended and not rewritten:
            return ("already_present",
                    f"{len(configs)} symbol(s) already set in {rel}")
        if not text.endswith(joiner):
            text += joiner
        self.write(rel, text)
        return ("applied",
                f"{appended} appended, {rewritten} rewritten in {rel}")

    def defconfig_drop_cmdline_token(self, token):
        # CONFIG_CMDLINE is a string, not CONFIG_x=y; a missing line is an
        # anchor miss, never a silent success.
        rel = self.defconfig_rel()
        if rel is None:
            reason = ("no defconfig passed" if self.defconfig is None
                      else "defconfig outside KERNEL_ROOT")
            return "report_only", reason

        text = self.read(rel)
        joiner = "\r\n" if "\r\n" in text else "\n"
        head, tail = 'CONFIG_CMDLINE="', '"'
        seen = dropped = False
        lines = []
        for line in text.split(joiner):
            if line.startswith(head) and line.endswith(tail):
                seen = True
                tokens = line[len(head):-len(tail)].split()
                if token in tokens:
                    tokens = [t for t in tokens if t != token]
                    line = head + " ".join(tokens) + tail
                    dropped = True
            lines.append(line)
        if not seen:
            return ("blocked_by_missing_anchor",
                    f"no CONFIG_CMDLINE line in {rel}")
        if not dropped:
            return ("already_present",
                    f"{token} already absent from CONFIG_CMDLINE in {rel}")
        self.write(rel, joiner.join(lines))
        return "applied", f"dropped {token} from CONFIG_CMDLINE in {rel}"

    def suite_touched(self, rel):
        text = self.read(rel)
        return common.has_any(
            text,
            [common.SUITE_FEATURE_MARKER, common.SUITE_SECURITY_MARKER, common.SUITE_FD_HELPER],
        )

    def suite_fdtable_fallback(self):
        text = self.read("fs/file.c")
        return common.SUITE_FD_FALLBACK_ALIGN in text or common.SUITE_FD_HELPER in text

    def block_rolled_back(self):
        if "block" not in self._shape_cache:
            text = self.read("block/blk-mq.c")
            self._shape_cache["block"] = common.F2FS_BLOCK_MONTHLY_SENTINEL not in text
        return self._shape_cache["block"]

    def fdtable_upstream_shape(self):
        # The suite's fallback shares the slots_wanted sizing, so its ALIGN line
        # disqualifies; a leftover suite helper alone does not.
        text = self.read("fs/file.c")
        if "alloc_fdtable(unsigned int slots_wanted)" not in text:
            return False
        if common.SUITE_FD_FALLBACK_ALIGN in text:
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
        except SystemExit as exc:
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


def shape_report(ctx):
    # block_rolled_back is informational and gates nothing.
    return {
        "fdtable_upstream_shape": ctx.fdtable_upstream_shape(),
        "suite_fdtable_fallback": ctx.suite_fdtable_fallback(),
        "block_rolled_back": ctx.block_rolled_back(),
    }


def run_child(child_name, groups, ctx, args, enabled=True):
    results = []
    for group in groups:
        if not enabled:
            results.append({
                "key": group.key,
                "summary": group.summary,
                "commits": group.commits,
                "files": group.files,
                "status": "report_only",
                "detail": f"family {ctx.family} is not android13-5.15; "
                          "nothing read or written",
            })
            continue
        before = {rel: ctx.read(rel) for rel in ctx.pending_writes()}
        results.append(group.run(ctx))
        changed = [rel for rel in ctx.pending_writes()
                   if ctx.read(rel) != before.get(rel, None)]
        status = results[-1]["status"]
        # A degraded group must not have written; an applied one must have.
        if status not in ("applied", "partial", "already_present"):
            if changed:
                raise SystemExit(
                    f"group {group.key} degraded to {results[-1]['status']} "
                    f"but already wrote {sorted(changed)}; refusing to continue"
                )
        elif status in ("applied", "partial") and not changed:
            raise SystemExit(
                f"group {group.key} reported {status} without writing any "
                "file; refusing to report a no-op as an edit"
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
        "defconfig": str(ctx.defconfig) if ctx.defconfig else None,
        "defconfig_writable": ctx.defconfig_rel() is not None,
        "shapes": shape_report(ctx) if enabled else {},
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
    parser.add_argument("--allow-unsupported", action="store_true",
                        help="apply groups even when --family is not "
                             "android13-5.15 (default: report_only only)")
    return parser.parse_args()


def make_context(args):
    ctx = GraftContext(args.common_dir, args.sub_level, args.family,
                       defconfig=args.defconfig, dry_run=args.dry_run)
    ctx.report_dir = args.report_dir
    return ctx


def apply_steps(ctx, steps):
    # Required anchors are transactional: one miss writes nothing at all.
    pending = {}
    results = []
    for rel, old, new, required in steps:
        try:
            text = pending.get(rel, ctx.read(rel))
        except FileNotFoundError:
            if required:
                detail = f"{rel}: file absent"
                return None, results, detail
            results.append((rel, "missing_anchor"))
            continue
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
    if applied == 0 and present == 0:
        # No match at all is blocked_by_missing_anchor, not already_present.
        missing = "; ".join(f"{r}:{s}" for r, s in results)
        return ("blocked_by_missing_anchor", results,
                "no step anchor matched (" + (missing or "empty step list") + ")")
    if applied == 0 and present == len(results):
        return "already_present", results, "all anchors already in the upstream form"
    detail = f"{applied} hunk(s) applied, {present} already present"
    missing_optional = [(r, s) for r, s in results if s == "missing_anchor"]
    if missing_optional:
        detail += "; degraded: " + ", ".join(f"{r}:{s}" for r, s in missing_optional)
        return "partial", results, detail
    return "applied", results, detail
