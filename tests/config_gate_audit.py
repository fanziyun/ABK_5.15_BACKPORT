#!/usr/bin/env python3
"""Config-gate audit: no ABK-added code that the built .config compiles out.

step_audit.py proves the anchors land; implementation_audit.py proves the
content is real; smoke.sh proves the child path end to end.  All three run
without a kernel build, so none of them can see the failure this one exists
for: code this module *adds* behind a CONFIG gate that the build turns **off**.
The group reports ``applied``, the tree looks patched, and the compiler deletes
the payload.

That is not hypothetical.  Batch 8 added the RCU ``offload_all`` machinery
behind ``CONFIG_RCU_NOCB_CPU_DEFAULT_ALL`` and no tier ever enabled the symbol,
so ``if (offload_all)`` was provably false on every baseline while the group
reported applied (found by the Batch 16 emptiness audit; the module tier
enables it now).  ``scripts/abk_stable_core.py`` grew ``_INTRODUCED_KCONFIG``
from that finding, but that table only covers symbols this module *introduces*
-- symbols it merely *depends on* were invisible to it.

Attribution is exact rather than heuristic.  Every file this module writes has
a pristine ``<file>.abk-orig`` snapshot next to it (the same snapshots
``scripts/abk_rollback.sh`` restores from), so "did we add this line?" is a diff
against the baseline, not a guess about nearby markers.  Without that, an
upstream ``IS_ENABLED(CONFIG_PREEMPT_RT)`` two lines above one of our edits
reads as our gate -- and the Batch 16 RCU gate, whose marker sits *above* the
``#if``, reads as upstream.  Both mistakes were observed while writing this.

Two classes are checked:

* A -- a gate line this module added (``#ifdef CONFIG_X``, ``#if defined(...)``,
  ``IS_ENABLED(CONFIG_X)``) whose symbol the build has off.
* B -- an added code line sitting inside an *upstream* gate whose symbol the
  build has off (adding live code to a block that is compiled out).

Either way the symbol must be on, or the darkness must be recorded in
``DARK_GATES`` below with a reason.  A gate whose symbol a tier of this module
claims to enable but whose .config says ``not set`` is a hard failure on its
own: that is the signature of an unmet Kconfig dependency, the one way a tier
can silently fail to take effect.

Usage:
  python3 tests/config_gate_audit.py <grafted-common-dir> --config <path>

``--config`` may be omitted when the tree sits in the standard Android layout
(``<workspace>/kernel/common`` with ``<workspace>/kernel/out/<target>/common``)
or when ``ABK_CONFIG`` names it.  Without a .config there is nothing to compare
against, so the audit refuses to run rather than print a green that means
nothing -- and it refuses a tree with no ``.abk-orig`` snapshots for the same
reason: an unpatched tree passing this check says nothing at all.
"""

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

# Dark gates this audit accepts.  Every entry needs a reason, because an
# unrecorded dark gate is exactly the Batch 8 RCU bug.  Keep it small.
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
}


def config_state(path: Path) -> tuple[set[str], set[str], set[str]]:
    """Return (on, off, known) symbol sets for a .config."""
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
    """1-based line numbers in ``patched`` that the graft inserted or rewrote."""
    added: set[int] = set()
    matcher = difflib.SequenceMatcher(a=original, b=patched, autojunk=False)
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ("insert", "replace"):
            added.update(range(j1 + 1, j2 + 1))
    return added


def guarded_span(lines: list[str], start: int) -> int:
    """Index of the ``#endif`` closing the gate that opens at ``start``."""
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
    """Which tier of this module claims to enable ``symbol``, if any."""
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
    """Classify every gate on module-added code.

    Returns (class_a, class_b, files_checked); each hit is
    (rel, line, symbol, kind).
    """
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


def run(common: Path, config: Path) -> list[str]:
    on, _off, known = config_state(config)
    class_a, class_b, snapshots = scan(common)
    print(f"tree       : {common}")
    print(f"config     : {config}")
    print(f"abk-orig   : {snapshots} snapshot(s)")
    print(f"class A    : {len(class_a)} added gate line(s)")
    print(f"class B    : {len(class_b)} upstream gate(s) containing added code\n")

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

    if dark:
        print("Deliberately dark (recorded, not failures):")
        for label, rel, line, symbol in dark:
            print(f"  [{label}] {symbol}  {rel}:{line}")
            print(f"        {DARK_GATES[symbol]}")
        print()
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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
