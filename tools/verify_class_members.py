# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compare each member of a class between two files and name the differing ones.

Usage: python tools/verify_class_members.py <fileA> <fileB> <ClassName>
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from block_tool import _qualname_map, _strip_dump  # noqa: E402


def main() -> int:
    fa, fb, cls = sys.argv[1], sys.argv[2], sys.argv[3]
    ma = _qualname_map(ast.parse(Path(fa).read_text(encoding="utf-8")))
    mb = _qualname_map(ast.parse(Path(fb).read_text(encoding="utf-8")))
    members = sorted({k.split(".", 1)[1] for k in ma if k.startswith(cls + ".")} |
                     {k.split(".", 1)[1] for k in mb if k.startswith(cls + ".")})
    diff = []
    for name in members:
        qa, qb = f"{cls}.{name}", f"{cls}.{name}"
        a, b = ma.get(qa), mb.get(qb)
        if a is None or b is None:
            diff.append((name, "MISSING in " + (fa if a is None else fb)))
            continue
        if _strip_dump(a) != _strip_dump(b):
            diff.append((name, "DIFFERENT"))
    same = len(members) - len(diff)
    print(f"{cls}: {same}/{len(members)} members identical")
    for name, why in diff:
        print(f"  {why:<24} {name}")
    return 0 if not diff else 1


if __name__ == "__main__":
    sys.exit(main())
