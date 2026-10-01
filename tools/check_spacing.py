# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Report PEP8 blank-line violations at module and class level.

Cutting blocks with block_tool.py can consume the blank separator lines that
surrounded a definition; this finds the leftovers so they can be restored.
Cosmetic only -- it never changes behavior (the AST is unaffected).

Usage: python tools/check_spacing.py <file.py> [...]
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


def _check_scope(body, lines, scope: str, want: int | None, report: list[str]) -> None:
    for node in body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = node.lineno
        for dec in getattr(node, "decorator_list", []) or []:
            start = min(start, dec.lineno)
        # count blank lines above, skipping an interleaved comment block
        # (a "# ---- section ----" comment directly above a def is normal style)
        i = start - 2  # 0-based index of the line above
        while i >= 0 and lines[i].strip().startswith("#"):
            i -= 1
        blanks = 0
        while i >= 0 and lines[i].strip() == "":
            blanks += 1
            i -= 1
        if i < 0:
            continue  # at the top of the file: no requirement
        needed = want
        if needed is None:
            continue
        if blanks < needed:
            report.append(
                f"L{start}: {scope}{node.name} has {blanks} blank line(s) above, "
                f"expected >= {needed}"
            )
        if isinstance(node, ast.ClassDef):
            _check_scope(node.body, lines, f"{scope}{node.name}.", 1, report)


def main() -> int:
    problems = 0
    for path_str in sys.argv[1:]:
        path = Path(path_str)
        lines = path.read_text(encoding="utf-8").splitlines()
        tree = ast.parse("\n".join(lines))
        report: list[str] = []
        _check_scope(tree.body, lines, "", 2, report)
        if report:
            problems += len(report)
            print(f"== {path} ==")
            for item in report:
                print(f"  {item}")
    if not problems:
        print("spacing OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
