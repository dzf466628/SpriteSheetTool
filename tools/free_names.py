# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""List free names (referenced but not locally bound) for given top-level blocks.

This computes exactly which imports a newly extracted module needs, instead of
guessing. Builtins are filtered out.

Usage: python tools/free_names.py <file.py> <qualname> [<qualname> ...]
"""
from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from block_tool import _qualname_map  # noqa: E402


def bound_names(node: ast.AST) -> set[str]:
    """Every identifier bound somewhere inside the block (approximate but safe)."""
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
            args = getattr(n, "args", None)
            if args is not None:
                for a in (list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)):
                    names.add(a.arg)
                if args.vararg:
                    names.add(args.vararg.arg)
                if args.kwarg:
                    names.add(args.kwarg.arg)
        elif isinstance(n, ast.Lambda):
            args = n.args
            for a in (list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)):
                names.add(a.arg)
            if args.vararg:
                names.add(args.vararg.arg)
            if args.kwarg:
                names.add(args.kwarg.arg)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            names.update(n.names)
    return names


def loaded_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            cur = n
            while isinstance(cur, ast.Attribute):
                cur = cur.value
            if isinstance(cur, ast.Name):
                names.add(cur.id)
    return names


def main() -> int:
    path = sys.argv[1]
    targets = sys.argv[2:]
    text = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(text)
    m = _qualname_map(tree)
    bi = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__"}

    all_free: set[str] = set()
    for name in targets:
        node = m.get(name)
        if node is None:
            print(f"NOT FOUND: {name}", file=sys.stderr)
            return 2
        free = loaded_names(node) - bound_names(node) - bi
        all_free |= free
        print(f"{name}: {', '.join(sorted(free))}")
    print()
    print("UNION:", " ".join(sorted(all_free)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
