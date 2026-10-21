#!/usr/bin/env python3

from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_DIR / "scripts"))

import abk_stable_core  # noqa: E402

GATE_RE = re.compile(
    r"^\s*#\s*if(?:def)?\s+(?:defined\s*\(\s*)?(CONFIG_[A-Za-z0-9_]+)"
)
IS_ENABLED_RE = re.compile(r"IS_ENABLED\(\s*(CONFIG_[A-Za-z0-9_]+)\s*\)")

SCHED_FEAT_RE = re.compile(
    r"^\s*SCHED_FEAT\(\s*([A-Za-z0-9_]+)\s*,\s*(true|false)\s*\)"
)
SCHED_FEAT_USE_RE = re.compile(r"sched_feat\(\s*([A-Za-z0-9_]+)\s*\)")

DARK_GATES = {
    "CONFIG_ZRAM_WRITEBACK": (
        "ROM-integration tier, opt-in by decision (Batch 14): the target ROM "
        "disables zram bring-up outright, so default-on would buy nothing there "
        "while handing a flash-wear capability to every other ROM"
    ),
    "CONFIG_NO_HZ_FULL": (
        "class B only: the added line inside the guard is a refinement that "
        "only applies when NO_HZ_FULL is on (that mask owns offloading).  With "
        "it off there is no competing mask, so skipping the line is correct by "
        "construction -- the DEFAULT_ALL path it protects is outside the guard"
    ),
    "CONFIG_NUMA": (
        "class B only: the added declaration sits inside the baseline's "
        "existing #ifdef CONFIG_NUMA / #else pair, which already provides the "
        "#define fallback the code needs; the graft is portable either way"
    ),
    "CONFIG_CFS_BANDWIDTH": (
        "class B only: the added pick-logic/placement helpers sit inside the "
        "baseline's CFS-bandwidth region but are not bandwidth code; the fair.c "
        "half is reached through the scheduler call sites, not this guard"
    ),
    "CONFIG_ZRAM_MEMORY_TRACKING": (
        "class B only: the 5.15 baseline decides zram_accessed() with an "
        "#ifdef CONFIG_ZRAM_MEMORY_TRACKING / #else pair and the module rewrote "
        "both variants in place, so the ac_time write survives in the #else one "
        "that the GKI default actually compiles (verified at "
        "zram_drv.c:1115-1121 with CONFIG_ZRAM_MEMORY_TRACKING unset and "
        "CONFIG_ZRAM_TRACK_ENTRY_ACTIME=y).  The module deliberately decouples "
        "the two symbols: its Kconfig step makes ZRAM_MEMORY_TRACKING select "
        "ZRAM_TRACK_ENTRY_ACTIME so age tracking no longer requires debugfs"
    ),
    "CONFIG_HUGETLB_PAGE": (
        "class B only (Batch 70 smaps_migration_guard): the guarded call site "
        "is smaps_hugetlb_range(), which the baseline itself wraps in "
        "#ifdef CONFIG_HUGETLB_PAGE / #else #define smaps_hugetlb_range NULL "
        "-- so with the symbol off the added line leaves the build together "
        "with the whole function, leaving no dead payload and no unmet "
        "dependency: the helper is still called by four other smaps / "
        "pagemap callers, three of them ungated (smaps_pte_entry, "
        "smaps_pmd_entry, pte_to_pagemap_entry) and the fourth (pagemap_pmd_range) under "
        "CONFIG_ARCH_ENABLE_THP_MIGRATION, which the GKI config does set).  A "
        "hugetlb-enabled build compiles the guard and gets the same fix"
    ),
    "CONFIG_HIGHMEM": (
        "negated guard (Batch 39 pagealloc_batch_clear): the added line is "
        "inside ``if (!IS_ENABLED(CONFIG_HIGHMEM))`` -- the batch memset is "
        "precisely the HIGHMEM=n path arm64 always compiles (HIGHMEM does not "
        "exist on arm64), while the HIGHMEM=y branch keeps the pristine "
        "per-page loop byte for byte.  The audit's IS_ENABLED scan cannot see "
        "the ``!`` polarity; both branches compile, and the one that runs is "
        "the added one"
    ),
}


def config_state(path: Path) -> tuple[set[str], set[str], set[str]]:
    on, off, known = set(), set(), set()
    for line in path.read_text(errors="replace").splitlines():
        m = re.match(r"(CONFIG_[A-Za-z0-9_]+)=(.*)", line)
        if m:
            known.add(m.group(1))
            (on if m.group(2).strip() not in ("n", "") else off).add(m.group(1))
            continue
        m = re.match(r"# (CONFIG_[A-Za-z0-9_]+) is not set", line)
        if m:
            known.add(m.group(1))
            off.add(m.group(1))
    return on, off, known


def added_lines(original: list[str], patched: list[str]) -> set[int]:
    added: set[int] = set()
    matcher = difflib.SequenceMatcher(a=original, b=patched, autojunk=False)
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ("insert", "replace"):
            added.update(range(j1 + 1, j2 + 1))
    return added


def guarded_span(lines: list[str], start: int) -> int:
    depth = 0
    for i in range(start, len(lines)):
        stripped = lines[i].lstrip()
        if re.match(r"#\s*if", stripped):
            depth += 1
        elif re.match(r"#\s*endif", stripped):
            depth -= 1
            if depth == 0:
                return i
    return len(lines) - 1


def find_config(common: Path, explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    env = os.environ.get("ABK_CONFIG", "").strip()
    if env:
        return Path(env)
    candidates = [common / ".config"]
    candidates += sorted((common.parent / "out").glob("*/common/.config"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def tier_claims(symbol: str) -> str | None:
    short = symbol[len("CONFIG_"):]
    for attr, tier in (
        ("_MODULE_CONFIGS", "module"),
        ("_ALIGN_CONFIGS", "6.6-align"),
        ("_ROM_CONFIGS", "rom"),
    ):
        for entry in getattr(abk_stable_core, attr):
            if entry[0] == short:
                return tier
    return None


def scan(common: Path) -> tuple[list, list, int]:
    class_a, class_b = [], []
    snapshots = sorted(common.rglob("*.abk-orig"))
    for snapshot in snapshots:
        target = Path(str(snapshot)[:-len(".abk-orig")])
        if not target.is_file():
            continue
        original = snapshot.read_text(errors="replace").splitlines()
        patched = target.read_text(errors="replace").splitlines()
        added = added_lines(original, patched)
        if not added:
            continue
        rel = str(target.relative_to(common))
        for i, line in enumerate(patched):
            lineno = i + 1
            gate = GATE_RE.match(line)
            if gate:
                symbol = gate.group(1)
                if lineno in added:
                    class_a.append((rel, lineno, symbol, "preprocessor"))
                elif any(n in added for n in range(lineno + 1,
                                                   guarded_span(patched, i) + 1)):
                    class_b.append((rel, lineno, symbol, "preprocessor"))
                continue
            used = IS_ENABLED_RE.search(line)
            if used and lineno in added:
                class_a.append((rel, lineno, used.group(1), "IS_ENABLED"))
    return class_a, class_b, len(snapshots)


def scan_sched_feats(common: Path) -> tuple[list, list]:
    enabled, disabled = [], []
    for snapshot in sorted(common.rglob("*.abk-orig")):
        target = Path(str(snapshot)[:-len(".abk-orig")])
        if not target.is_file():
            continue
        original = snapshot.read_text(errors="replace").splitlines()
        patched = target.read_text(errors="replace").splitlines()
        added = added_lines(original, patched)
        if not added:
            continue
        rel = str(target.relative_to(common))
        for i, line in enumerate(patched):
            lineno = i + 1
            if lineno not in added:
                continue
            m = SCHED_FEAT_RE.match(line)
            if not m:
                continue
            name, default = m.group(1), m.group(2)
            (enabled if default == "true" else disabled).append(
                (rel, lineno, name, default))
    return enabled, disabled


def _sched_feat_has_reader(common: Path, name: str, defined_in: str,
                           defined_at: int) -> bool:
    for snapshot in sorted(common.rglob("*.abk-orig")):
        target = Path(str(snapshot)[:-len(".abk-orig")])
        if not target.is_file():
            continue
        original = snapshot.read_text(errors="replace").splitlines()
        patched = target.read_text(errors="replace").splitlines()
        added = added_lines(original, patched)
        if not added:
            continue
        rel = str(target.relative_to(common))
        for i, line in enumerate(patched):
            lineno = i + 1
            if rel == defined_in and lineno == defined_at:
                continue
            if lineno not in added:
                continue
            for use in SCHED_FEAT_USE_RE.finditer(line):
                if use.group(1) == name:
                    return True
    return False


def run(common: Path, config: Path) -> list[str]:
    on, _off, known = config_state(config)
    class_a, class_b, snapshots = scan(common)
    feats_on, feats_off = scan_sched_feats(common)
    print(f"tree       : {common}")
    print(f"config     : {config}")
    print(f"abk-orig   : {snapshots} snapshot(s)")
    print(f"class A    : {len(class_a)} added gate line(s)")
    print(f"class B    : {len(class_b)} upstream gate(s) containing added code")
    print(f"sched_feat : {len(feats_on)} added switch(es) default-on, "
          f"{len(feats_off)} default-off\n")

    problems, dark = [], []
    for label, hits in (("A", class_a), ("B", class_b)):
        for rel, line, symbol, kind in hits:
            if symbol in on:
                continue
            if symbol in DARK_GATES:
                dark.append((label, rel, line, symbol))
                continue
            state = "not set" if symbol in known or symbol in _off else "absent"
            tier = tier_claims(symbol)
            if tier:
                problems.append(
                    f"[{label}] {rel}:{line}: {symbol} is on ABK-added code and "
                    f"the {tier} tier claims to enable it, but the .config has "
                    f"it {state} -- an unmet Kconfig dependency silently "
                    f"disables the tier (the Batch 8 RCU failure mode)"
                )
            else:
                problems.append(
                    f"[{label}] {rel}:{line}: {symbol} ({kind}) is {state} in "
                    f"the .config, so the ABK-added code it guards never "
                    f"compiles, and neither a tier nor DARK_GATES accounts for "
                    f"it"
                )

    for rel, line, name, _default in feats_off:
        if name in DARK_GATES:
            dark.append(("SF", rel, line, name))
            continue
        problems.append(
            f"[SF] {rel}:{line}: SCHED_FEAT({name}, false) is an ABK-added "
            f"switch that is off by construction and no DARK_GATES entry "
            f"records why"
        )
    for rel, line, name, _default in feats_on:
        if not _sched_feat_has_reader(common, name, rel, line):
            problems.append(
                f"[SF] {rel}:{line}: SCHED_FEAT({name}, true) is on, but no "
                f"module-added line calls sched_feat({name}) -- a switch with "
                f"no reader controls nothing (Batch 28 shipped exactly this)"
            )

    if dark:
        print("Deliberately dark (recorded, not failures):")
        for label, rel, line, symbol in dark:
            print(f"  [{label}] {symbol}  {rel}:{line}")
            print(f"        {DARK_GATES[symbol]}")
        print()
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit graft CONFIG gates against a build's .config")
    parser.add_argument("common_dir",
                        help="the grafted <KERNEL_ROOT>/common tree")
    parser.add_argument("--config", default=None,
                        help="the .config the build produced for that tree")
    args = parser.parse_args()

    common = Path(args.common_dir)
    if not common.is_dir():
        raise SystemExit(f"AUDIT FAIL: not a directory: {common}")
    if not list(common.rglob("*.abk-orig")):
        raise SystemExit(
            "AUDIT FAIL: no .abk-orig snapshots under this tree, so nothing was "
            "grafted; this audit only means something against a patched tree"
        )

    config = find_config(common, args.config)
    if config is None or not config.is_file():
        raise SystemExit(
            "AUDIT FAIL: no .config found; this audit compares grafted code "
            "against the config the build produced, so pass --config or set "
            "ABK_CONFIG (a green without one would mean nothing)"
        )

    problems = run(common, config)
    if problems:
        print("AUDIT FAIL:")
        for problem in problems:
            print(f"  - {problem}")
        if any("tier claims to enable" in p for p in problems):
            print("\n  note: if the tier change is newer than the .config, "
                  "rebuild before believing this; otherwise the symbol's "
                  "Kconfig dependency is unmet.")
        sys.exit(1)
    print("CONFIG GATE AUDIT OK")


if __name__ == "__main__":
    main()
