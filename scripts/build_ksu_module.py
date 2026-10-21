#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
MODULE_SOURCE = REPOSITORY / "ksu/abk_runtime_tunables"
ADDON2_SOURCE = REPOSITORY / "ksu/sailboat_addon_2"
MODULE_SOURCES = (MODULE_SOURCE, ADDON2_SOURCE)
BUILD_DIR = REPOSITORY / "build/ksu"
DEFAULT_OUTPUT = BUILD_DIR / "abk_runtime_tunables.zip"
EMBED_MANIFEST = "embed.conf"

EXECUTABLE_MODE = 0o755
REGULAR_MODE = 0o644
FIXED_TIME = (1980, 1, 1, 0, 0, 0)


class BuildError(SystemExit):
    pass


def read_module_id(source: Path = MODULE_SOURCE) -> str:
    prop = source / "module.prop"
    if not prop.is_file():
        raise BuildError(f"no module.prop in {source}")
    for line in prop.read_text(encoding="utf-8").splitlines():
        if line.startswith("id="):
            return line[3:].strip()
    raise BuildError(f"no id= in {prop}")


def default_output(source: Path = MODULE_SOURCE) -> Path:
    return BUILD_DIR / f"{read_module_id(source)}.zip"


def read_embed_manifest(source: Path = MODULE_SOURCE) -> list[tuple[Path, str]]:
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
            info.create_system = 3
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
    parser = argparse.ArgumentParser(description="Pack the KernelSU runtime companions")
    parser.add_argument("--module", type=Path, default=MODULE_SOURCE)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--list", action="store_true", help="print the entries and exit")
    parser.add_argument("--all", action="store_true", help="pack every shipped companion")
    args = parser.parse_args()

    sources = MODULE_SOURCES if args.all else (args.module,)
    if args.list:
        for source in sources:
            if args.all:
                print(f"# {source.name}")
            for name, data, mode in module_entries(source):
                print(f"{oct(mode)}  {len(data):>8}  {name}")
        return 0

    if args.output is not None and len(sources) != 1:
        raise BuildError("--output needs a single --module, not --all")
    for source in sources:
        output = build(source, args.output or default_output(source))
        with zipfile.ZipFile(output) as archive:
            count = len(archive.namelist())
        print(f"{output} ({count} entries, {output.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
