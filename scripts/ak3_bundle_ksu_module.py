#!/usr/bin/env python3

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPOSITORY = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from build_ksu_module import MODULE_SOURCE, MODULE_SOURCES  # noqa: E402
from build_ksu_module import build as build_module  # noqa: E402
from build_ksu_module import read_module_id  # noqa: E402

ANY_KERNEL_SCRIPT = "anykernel.sh"
BUNDLE_DIR = "abk-ksu-modules"
BEGIN_MARKER = "### >>> ABK 5.15 Runtime Tunables KernelSU module installer"
END_MARKER = "### <<< ABK 5.15 Runtime Tunables KernelSU module installer"

REGULAR_MODE = 0o644
EXECUTABLE_MODE = 0o755

INSTALLER_BLOCK = f"""\
{BEGIN_MARKER} (managed by ABK_5.15_BACKPORT) >>>
## Installs the KernelSU companions shipped in {BUNDLE_DIR}/, so that
## flashing this AnyKernel3 zip also installs the policy that fixes the zram
## algorithms, drives the recompression sweeps, holds the cpufreq governor and
## arms the smart-freq band.  Additive and non-fatal: the boot image is already
## written when this runs, so a failure only prints a warning.
if ! command -v ui_print >/dev/null 2>&1; then
  ui_print() {{ echo "$@"; }}
fi

abk_tu_install_ksu_modules() {{
  ABK_TU_HOME="${{AKHOME:-$PWD}}"
  ABK_TU_SRC="$ABK_TU_HOME/{BUNDLE_DIR}"
  [ -d "$ABK_TU_SRC" ] || return 0
  ABK_TU_UNZIP="unzip"
  [ -x "$ABK_TU_HOME/tools/busybox" ] && ABK_TU_UNZIP="$ABK_TU_HOME/tools/busybox unzip"
  for ABK_TU_ZIP in "$ABK_TU_SRC"/*.zip; do
    [ -f "$ABK_TU_ZIP" ] || continue
    ABK_TU_ID="$(basename "$ABK_TU_ZIP" .zip)"
    ABK_TU_VIA=""
    for ABK_TU_KSUD in /data/adb/ksud /data/adb/ksu/bin/ksud /data/adb/ap/bin/apd; do
      [ -x "$ABK_TU_KSUD" ] || continue
      if "$ABK_TU_KSUD" module install "$ABK_TU_ZIP" >/dev/null 2>&1; then
        ABK_TU_VIA="$(basename "$ABK_TU_KSUD")"
        break
      fi
    done
    if [ -z "$ABK_TU_VIA" ] && command -v magisk >/dev/null 2>&1; then
      magisk --install-module "$ABK_TU_ZIP" >/dev/null 2>&1 && ABK_TU_VIA="magisk"
    fi
    if [ -z "$ABK_TU_VIA" ] && [ -d /data/adb ]; then
      ABK_TU_TARGET="/data/adb/modules/$ABK_TU_ID"
      rm -rf "$ABK_TU_TARGET"
      mkdir -p "$ABK_TU_TARGET" 2>/dev/null
      if $ABK_TU_UNZIP -o "$ABK_TU_ZIP" -d "$ABK_TU_TARGET" >/dev/null 2>&1; then
        chmod 0755 "$ABK_TU_TARGET"/*.sh 2>/dev/null
        [ -d "$ABK_TU_TARGET/bin" ] && chmod 0755 "$ABK_TU_TARGET"/bin/*.sh 2>/dev/null
        ABK_TU_VIA="unpacked to /data/adb/modules"
      fi
    fi
    if [ -n "$ABK_TU_VIA" ]; then
      ui_print "  -> ABK KernelSU module: $ABK_TU_ID installed via $ABK_TU_VIA"
    else
      ui_print "  -> ABK KernelSU module: WARNING could not install $ABK_TU_ID; flash that module zip manually"
    fi
  done
}}

abk_tu_install_ksu_modules || ui_print "  -> ABK KernelSU module: install step skipped"
unset ABK_TU_HOME ABK_TU_SRC ABK_TU_UNZIP ABK_TU_ZIP ABK_TU_ID ABK_TU_VIA \\
  ABK_TU_KSUD ABK_TU_TARGET 2>/dev/null
{END_MARKER} <<<
"""

_BLOCK_PATTERN = re.compile(
    re.escape(BEGIN_MARKER) + r".*?" + re.escape(END_MARKER) + r" <<<",
    re.DOTALL,
)


class InjectionError(SystemExit):
    pass


def module_id(source: Path = MODULE_SOURCE) -> str:
    return read_module_id(source)


def module_zip_id(module_zip: Path) -> str:
    with zipfile.ZipFile(module_zip) as archive:
        if "module.prop" not in archive.namelist():
            raise InjectionError(f"{module_zip} has no module.prop")
        for line in archive.read("module.prop").decode("utf-8").splitlines():
            if line.startswith("id="):
                return line[3:].strip()
    raise InjectionError(f"no id= in {module_zip}:module.prop")


def build_module_zip(source: Path = MODULE_SOURCE, output: Path | None = None) -> Path:
    if output is None:
        output = Path(tempfile.mkdtemp(prefix="abk-tunables-ksu-")) / f"{module_id(source)}.zip"
    return build_module(source, output)


def build_module_zips(sources: tuple[Path, ...] = MODULE_SOURCES) -> list[Path]:
    return [build_module_zip(source) for source in sources]


def expected_module_ids() -> tuple[str, ...]:
    return tuple(read_module_id(source) for source in MODULE_SOURCES)


def resolve_module_zips(args: argparse.Namespace) -> list[Path]:
    if args.module_zip:
        return list(args.module_zip)
    sources = tuple(args.source) if args.source else MODULE_SOURCES
    return build_module_zips(sources)


def inject_script(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if _BLOCK_PATTERN.search(text):
        return _BLOCK_PATTERN.sub(lambda _: INSTALLER_BLOCK.rstrip("\n"), text)
    separator = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    return f"{text}{separator}{INSTALLER_BLOCK}"


def discover_ak3_dir(hint: str | None = None, start: Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if hint:
        candidates.append(Path(hint))
    for value in (os.environ.get("ANYKERNEL3"), os.environ.get("GITHUB_WORKSPACE")):
        if not value:
            continue
        root = Path(value)
        candidates.extend([root, root / "AnyKernel3", root / "oneplus_workspace/AnyKernel3"])

    workspace = os.environ.get("GITHUB_WORKSPACE")
    if workspace and Path(workspace).is_dir():
        root = Path(workspace)
        for pattern in ("*/anykernel.sh", "*/*/anykernel.sh"):
            candidates.extend(path.parent for path in sorted(root.glob(pattern)))

    origin = (start or Path.cwd()).resolve()
    for parent in [origin, *origin.parents[:2]]:
        candidates.append(parent)
        candidates.extend(path.parent for path in sorted(parent.glob("*/anykernel.sh")))

    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:  # pragma: no cover - unreadable candidate
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / ANY_KERNEL_SCRIPT).is_file() and (resolved / "tools").is_dir():
            return resolved
    return None


def inject_dir_modules(ak3_dir: Path, module_zips: list[Path]) -> tuple[Path, list[Path]]:
    script = ak3_dir / ANY_KERNEL_SCRIPT
    if not script.is_file():
        raise InjectionError(f"no {ANY_KERNEL_SCRIPT} in {ak3_dir}")

    payloads: list[tuple[str, bytes]] = []
    for module_zip in module_zips:
        if not module_zip.is_file():
            raise InjectionError(f"module zip not found: {module_zip}")
        payloads.append((module_zip_id(module_zip), module_zip.read_bytes()))

    bundle = ak3_dir / BUNDLE_DIR
    bundle.mkdir(parents=True, exist_ok=True)
    targets: list[Path] = []
    for module_name, data in payloads:
        target = bundle / f"{module_name}.zip"
        target.write_bytes(data)
        targets.append(target)

    script.write_bytes(inject_script(script.read_text(encoding="utf-8")).encode("utf-8"))
    script.chmod(script.stat().st_mode | 0o111)
    return script, targets


def inject_dir(ak3_dir: Path, module_zip: Path) -> tuple[Path, Path]:
    script, targets = inject_dir_modules(ak3_dir, [module_zip])
    return script, targets[0]


def _zip_info(name: str, mode: int, date_time: tuple[int, int, int, int, int, int]) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=date_time)
    info.create_system = 3
    info.external_attr = mode << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def inject_zip_modules(
    source: Path, output: Path, module_zips: list[Path]
) -> tuple[Path, list[str]]:
    if not source.is_file():
        raise InjectionError(f"ak3 zip not found: {source}")

    modules: dict[str, bytes] = {}
    for module_zip in module_zips:
        if not module_zip.is_file():
            raise InjectionError(f"module zip not found: {module_zip}")
        modules[f"{BUNDLE_DIR}/{module_zip_id(module_zip)}.zip"] = module_zip.read_bytes()

    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if ANY_KERNEL_SCRIPT not in names:
            raise InjectionError(f"{source} has no {ANY_KERNEL_SCRIPT} at the zip root")
        patched = inject_script(archive.read(ANY_KERNEL_SCRIPT).decode("utf-8")).encode("utf-8")
        script_mode = (archive.getinfo(ANY_KERNEL_SCRIPT).external_attr >> 16) & 0o777

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(output, "w") as target:
        for info in archive.infolist():
            if info.filename == ANY_KERNEL_SCRIPT:
                data = patched
            elif info.filename in modules:
                data = modules[info.filename]
            else:
                data = archive.read(info.filename)
            if info.is_dir():
                new_info = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                new_info.create_system = info.create_system
                new_info.external_attr = info.external_attr
                new_info.compress_type = zipfile.ZIP_STORED
            else:
                new_info = _zip_info(
                    info.filename,
                    (info.external_attr >> 16) & 0o777 or script_mode,
                    info.date_time,
                )
                new_info.create_system = info.create_system
            target.writestr(new_info, data)
        for module_name, module_bytes in modules.items():
            if module_name not in names:
                target.writestr(
                    _zip_info(module_name, REGULAR_MODE, (1980, 1, 1, 0, 0, 0)), module_bytes
                )
    return output, list(modules)


def inject_zip(source: Path, output: Path, module_zip: Path) -> tuple[Path, str]:
    patched, entries = inject_zip_modules(source, output, [module_zip])
    return patched, entries[0]


def _check_anykernel_text(text: str, problems: list[str]) -> None:
    if text.count(BEGIN_MARKER) != 1 or text.count(END_MARKER) != 1:
        problems.append("anykernel.sh must contain exactly one installer block")
        return
    block = _BLOCK_PATTERN.search(text)
    assert block is not None
    body = block.group(0)
    for needle in ("module install", "magisk --install-module", "/data/adb/modules/"):
        if needle not in body:
            problems.append(f"installer block is missing {needle!r}")
    if "\nset -e" in body:
        problems.append("installer block must not enable set -e")
    if re.search(r"^\s*exit\s+[1-9]", body, re.MULTILINE):
        problems.append("installer block must not abort the flash")


def _check_module_zip(data: bytes, expected_name: str, problems: list[str]) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if "module.prop" not in names:
                problems.append("bundled module zip has no module.prop")
                return
            props = {}
            for line in archive.read("module.prop").decode("utf-8").splitlines():
                if "=" in line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    props[key.strip()] = value.strip()
            if props.get("id") != expected_name:
                problems.append(
                    f"bundled module id {props.get('id')!r} does not match entry name {expected_name!r}"
                )
            for info in archive.infolist():
                if info.filename.endswith(".sh"):
                    mode = (info.external_attr >> 16) & 0o777
                    if mode != EXECUTABLE_MODE:
                        problems.append(
                            f"{info.filename} must be 0755 in the module zip, got {oct(mode)}"
                        )
    except zipfile.BadZipFile:
        problems.append("bundled module zip is not a readable zip")


def _expected_module_problems(
    pairs: list[tuple[str, bytes]], require: tuple[str, ...]
) -> list[str]:
    present = {Path(name).stem for name, _data in pairs}
    return [
        f"the bundle is missing the companion module {module!r}"
        for module in require
        if module not in present
    ]


def _check_bundle_files(
    pairs: list[tuple[str, bytes]], script_text: str, require: tuple[str, ...] = ()
) -> list[str]:
    problems: list[str] = []
    _check_anykernel_text(script_text, problems)
    modules = [
        (name, data)
        for name, data in pairs
        if name.startswith(f"{BUNDLE_DIR}/") and not name.endswith("/")
    ]
    if not modules:
        problems.append(f"no module zip under {BUNDLE_DIR}/")
    for name, data in modules:
        _check_module_zip(data, Path(name).stem, problems)
    problems.extend(_expected_module_problems(pairs, require))
    return problems


def verify_dir(ak3_dir: Path, require: tuple[str, ...] = ()) -> list[str]:
    script = ak3_dir / ANY_KERNEL_SCRIPT
    if not script.is_file():
        return [f"no {ANY_KERNEL_SCRIPT} in {ak3_dir}"]
    bundle = ak3_dir / BUNDLE_DIR
    pairs = [(f"{BUNDLE_DIR}/{path.name}", path.read_bytes()) for path in sorted(bundle.glob("*.zip"))]
    return _check_bundle_files(pairs, script.read_text(encoding="utf-8"), require)


def verify_zip(source: Path, require: tuple[str, ...] = ()) -> list[str]:
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if ANY_KERNEL_SCRIPT not in names:
            return [f"{source} has no {ANY_KERNEL_SCRIPT} at the zip root"]
        pairs = [
            (name, archive.read(name))
            for name in names
            if name.startswith(f"{BUNDLE_DIR}/") and not name.endswith("/")
        ]
        script_text = archive.read(ANY_KERNEL_SCRIPT).decode("utf-8")
    return _check_bundle_files(pairs, script_text, require)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bundle the KernelSU runtime companion into the AnyKernel3 zip")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_targets(target: argparse.ArgumentParser) -> None:
        target.add_argument(
            "--ak3-dir",
            default=None,
            help="extracted AnyKernel3 tree, or 'auto' to discover it (default: auto)",
        )
        target.add_argument("--zip", type=Path, default=None, help="finished AnyKernel3 zip")

    inject = subparsers.add_parser("inject", help="add the module and the installer block")
    add_targets(inject)
    inject.add_argument("--output", type=Path, default=None, help="output zip (required with --zip)")
    inject.add_argument(
        "--source",
        type=Path,
        action="append",
        default=None,
        help="KernelSU module source dir (repeatable; default: every shipped companion)",
    )
    inject.add_argument(
        "--module-zip",
        type=Path,
        action="append",
        default=None,
        help="prebuilt module zip to embed (repeatable)",
    )
    inject.add_argument(
        "--strict",
        action="store_true",
        help="fail when no AnyKernel3 tree can be found instead of warning",
    )

    verify = subparsers.add_parser("verify", help="check that a bundle is complete and runnable")
    add_targets(verify)

    args = parser.parse_args(argv)

    module_zips: list[Path] = []
    if args.command == "inject":
        module_zips = resolve_module_zips(args)
    require = expected_module_ids()

    if args.zip is not None:
        if args.command == "inject":
            if args.output is None:
                raise InjectionError("--output is required with --zip")
            output, entries = inject_zip_modules(args.zip, args.output, module_zips)
            print(f"injected {', '.join(entries)} into {output}")
            problems = verify_zip(output, require=require)
        else:
            problems = verify_zip(args.zip, require=require)
    else:
        hint = None if args.ak3_dir in (None, "auto") else args.ak3_dir
        ak3_dir = discover_ak3_dir(hint)
        if ak3_dir is None:
            message = "no extracted AnyKernel3 tree found (looked for anykernel.sh + tools/)"
            if getattr(args, "strict", False):
                raise InjectionError(message)
            print(f"warning: {message}; nothing to bundle", file=sys.stderr)
            return 0
        if args.command == "inject":
            script, targets = inject_dir_modules(ak3_dir, module_zips)
            entries = ", ".join(f"{BUNDLE_DIR}/{target.name}" for target in targets)
            print(f"injected {entries} and the installer block into {script}")
            problems = verify_dir(ak3_dir, require=require)
        else:
            problems = verify_dir(ak3_dir, require=require)

    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    print(f"ok: {', '.join(require)} are bundled and their installer block is present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
