"""Dev-time helper: dump .patch hunks as LF old/new block pairs.

Lives under research/ and is NOT part of the shipped module.  Used to lift
hunk content into the anchor groups of abk_stable_core.py / abk_stable_perf.py.
"""

import sys
from pathlib import Path


def parse_patch(path):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    hunks = []
    file_path = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            parts = line.split()
            file_path = parts[2][2:] if parts[2].startswith("a/") else parts[2]
        elif line.startswith("@@"):
            header = line
            old, new, old_lines, new_lines = [], [], [], []
            i += 1
            while i < len(lines) and not lines[i].startswith(("diff --git", "@@")):
                raw = lines[i]
                if raw.startswith("+"):
                    new_lines.append(raw[1:])
                elif raw.startswith("-"):
                    old_lines.append(raw[1:])
                elif raw.startswith(" "):
                    old_lines.append(raw[1:])
                    new_lines.append(raw[1:])
                i += 1
            hunks.append((file_path, header, "\n".join(old_lines), "\n".join(new_lines)))
            continue
        i += 1
    return hunks


def main():
    patch_dir = Path(sys.argv[1])
    for patch in sorted(patch_dir.glob("*.patch")):
        print(f"\n{'=' * 20} {patch.stem} {'=' * 20}")
        for file_path, header, old, new in parse_patch(patch):
            print(f"\n--- {file_path} {header}")
            print("OLD >>>")
            print(old)
            print("<<< NEW")
            print(new)
            print(">>>")


if __name__ == "__main__":
    main()
