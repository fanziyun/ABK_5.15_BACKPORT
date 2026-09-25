#!/usr/bin/env python3
"""Pack a KernelSU module from ``ksu/<id>/`` into a flashable zip.

The default module source is ``ksu/abk_runtime_tunables/``; this script turns it
into ``build/ksu/abk_runtime_tunables.zip``, which the KernelSU manager can flash
and ``ksud module install`` can install over adb.  Pass ``--module`` for the
second module (``ksu/sailboat_addon_2``).  The AK3 bundler renames each zip to
the ``id=`` inside its module.prop, because the installer derives the install
directory from that basename -- so module 1 keeps its original id (an installed
copy must upgrade in place rather than be orphaned) and module 2, being new, is
free to take the sailboat_addon_2 id.

Three details matter:

* Entries sit at the zip root, because KernelSU extracts them straight into
  ``/data/adb/modules/<id>``.  No ``META-INF/`` is shipped: ``ksud module
  install`` has its own unpacker (it even skips ``META-INF/*``).
* ``embed.conf`` maps repository files -- currently
  ``tools/zram_recompress_trigger.sh`` -- into the module, so the device-facing
  tool stays a single implementation instead of a copy that drifts.
* The output is deterministic (fixed timestamps, sorted entries, ``.sh`` at
  0755), so two builds of the same tree are byte-identical and CI can compare
  them.

Run: python3 scripts/build_ksu_module.py
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
MODULE_SOURCE = REPOSITORY / "ksu/abk_runtime_tunables"
DEFAULT_OUTPUT = REPOSITORY / "build/ksu/abk_runtime_tunables.zip"
EMBED_MANIFEST = "embed.conf"

EXECUTABLE_MODE = 0o755
REGULAR_MODE = 0o644
FIXED_TIME = (1980, 1, 1, 0, 0, 0)


class BuildError(SystemExit):
    """A condition the caller must fix; the CLI reports it and exits non-zero."""


def read_module_id(source: Path = MODULE_SOURCE) -> str:
    """The ``id=`` of module.prop, which must also be the module directory name."""
    prop = source / "module.prop"
    if not prop.is_file():
        raise BuildError(f"no module.prop in {source}")
    for line in prop.read_text(encoding="utf-8").splitlines():
        if line.startswith("id="):
            return line[3:].strip()
    raise BuildError(f"no id= in {prop}")


def read_embed_manifest(source: Path = MODULE_SOURCE) -> list[tuple[Path, str]]:
    """Parse ``<repo-relative source>=<path inside the module>`` lines.

    Paths are resolved and checked: a manifest must not reach outside the
    repository, and must not escape the module directory either.
    """
    manifest = source / EMBED_MANIFEST
    if not manifest.is_file():
        return []

    pairs: list[tuple[Path, str]] = []
    for lineno, raw in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise BuildError(f"{manifest}:{lineno}: no '=' in {line!r}")
        source_text, _, target_text = line.partition("=")
        source_text = source_text.strip()
        target_text = target_text.strip().replace("\\", "/")
        if not source_text or not target_text:
            raise BuildError(f"{manifest}:{lineno}: empty side in {line!r}")
        if target_text.startswith("/") or ".." in target_text.split("/"):
            raise BuildError(f"{manifest}:{lineno}: refusing in-zip path {target_text!r}")
        embed_source = (REPOSITORY / source_text).resolve()
        if not embed_source.is_relative_to(REPOSITORY):
            raise BuildError(f"{manifest}:{lineno}: {source_text!r} is outside the repository")
        if not embed_source.is_file():
            raise BuildError(f"{manifest}:{lineno}: {source_text!r} does not exist")
        pairs.append((embed_source, target_text))
    return pairs


def module_entries(source: Path = MODULE_SOURCE) -> list[tuple[str, bytes, int]]:
    """(entry name, bytes, mode) for the module tree plus everything embedded."""
    if not (source / "module.prop").is_file():
        raise BuildError(f"{source} does not look like a KernelSU module")

    entries: list[tuple[str, bytes, int]] = []
    seen: set[str] = set()

    for path in sorted((p for p in source.rglob("*") if p.is_file()), key=lambda p: p.as_posix()):
        name = path.relative_to(source).as_posix()
        seen.add(name)
        mode = EXECUTABLE_MODE if name.endswith(".sh") else REGULAR_MODE
        entries.append((name, path.read_bytes(), mode))

    for embed_source, target in read_embed_manifest(source):
        if target in seen:
            raise BuildError(f"embedded entry {target!r} collides with a module file")
        seen.add(target)
        mode = EXECUTABLE_MODE if target.endswith(".sh") else REGULAR_MODE
        entries.append((target, embed_source.read_bytes(), mode))

    entries.sort(key=lambda item: item[0])
    return entries


def build(source: Path = MODULE_SOURCE, output: Path = DEFAULT_OUTPUT) -> Path:
    module_id = read_module_id(source)
    if source.name != module_id:
        raise BuildError(
            f"{source.name} != module.prop id {module_id!r}: KernelSU names the "
            f"module directory after the zip, so the two must match"
        )

    entries = module_entries(source)
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.create_system = 3  # unix, so external_attr carries the mode
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)

    if output.stem != module_id:
        print(
            f"warning: {output.name} does not match id={module_id}; KernelSU names "
            f"the module directory after the zip",
            file=sys.stderr,
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--module", type=Path, default=MODULE_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--list", action="store_true", help="print the entries and exit")
    args = parser.parse_args()

    if args.list:
        for name, data, mode in module_entries(args.module):
            print(f"{oct(mode)}  {len(data):>8}  {name}")
        return 0

    output = build(args.module, args.output)
    with zipfile.ZipFile(output) as archive:
        count = len(archive.namelist())
    print(f"{output} ({count} entries, {output.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
