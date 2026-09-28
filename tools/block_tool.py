# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""AST block tool for mechanical refactoring.

Locates top-level / class-level definitions by qualified name and can list,
cut, or extract them with byte-exact source text, plus verify that a moved
block is AST-identical between two files (pure-move proof).

Usage:
    python tools/block_tool.py list    <file>
    python tools/block_tool.py span    <file> <qualname>
    python tools/block_tool.py cut     <file> <qualname> [<qualname> ...]
    python tools/block_tool.py extract <file> <qualname> [<qualname> ...]
    python tools/block_tool.py verify  <fileA> <qualnameA> <fileB> <qualnameB>
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


def _qualname_map(tree: ast.Module) -> dict[str, ast.AST]:
    """Map dotted qualname -> node for module/class level defs and assigns."""
    out: dict[str, ast.AST] = {}

    def walk(body, prefix=""):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + node.name
                out[name] = node
                if isinstance(node, ast.ClassDef):
                    walk(node.body, name + ".")
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        out[prefix + t.id] = node
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                out[prefix + node.target.id] = node

    walk(tree.body)
    return out


def _node_span(node: ast.AST, lines: list[str]) -> tuple[int, int]:
    """1-based (start, end) line span, including decorators and leading comments."""
    start = node.lineno
    for dec in getattr(node, "decorator_list", []) or []:
        start = min(start, dec.lineno)
    # pull in a contiguous comment block directly above the definition
    i = start - 1  # index of line above (0-based)
    while i - 1 >= 0:
        prev = lines[i - 1].strip()
        if prev.startswith("#"):
            start = i
            i -= 1
        else:
            break
    end = node.end_lineno
    return start, end


def _load(path: str):
    text = Path(path).read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    return text, lines, tree


def cmd_list(path: str) -> None:
    _, lines, tree = _load(path)
    for name, node in _qualname_map(tree).items():
        s, e = _node_span(node, lines)
        kind = type(node).__name__
        print(f"{s:>6}-{e:<6} {e - s + 1:>5} lines  {kind:<16} {name}")


def cmd_span(path: str, name: str) -> None:
    _, lines, tree = _load(path)
    node = _qualname_map(tree).get(name)
    if node is None:
        print(f"NOT FOUND: {name}", file=sys.stderr)
        sys.exit(1)
    s, e = _node_span(node, lines)
    print(f"{s} {e}")


def cmd_cut(path: str, names: list[str]) -> None:
    text, lines, tree = _load(path)
    spans = []
    m = _qualname_map(tree)
    for name in names:
        node = m.get(name)
        if node is None:
            print(f"NOT FOUND: {name}", file=sys.stderr)
            sys.exit(1)
        spans.append(_node_span(node, lines))
    # merge overlapping spans
    spans.sort()
    merged = [list(spans[0])]
    for s, e in spans[1:]:
        if s <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    # delete from the bottom up, also swallowing trailing blank lines (max 2)
    for s, e in reversed(merged):
        end = e
        while end < len(lines) and lines[end].strip() == "" and (end - e) < 2:
            end += 1
        del lines[s - 1:end]
    Path(path).write_text("".join(lines), encoding="utf-8")
    print(f"cut {len(merged)} block(s) from {path}")


def cmd_extract(path: str, names: list[str], out: str | None = None) -> None:
    _, lines, tree = _load(path)
    m = _qualname_map(tree)
    chunks = []
    for name in names:
        node = m.get(name)
        if node is None:
            print(f"NOT FOUND: {name}", file=sys.stderr)
            sys.exit(1)
        s, e = _node_span(node, lines)
        chunks.append("".join(lines[s - 1:e]).rstrip("\n"))
    body = "\n\n\n".join(chunks) + "\n"
    if out:
        # Write directly with UTF-8: piping through PowerShell mangles
        # non-ASCII text because Python encodes stdout with the console locale.
        Path(out).write_text(body, encoding="utf-8")
        print(f"extracted {len(names)} block(s) -> {out}")
    else:
        sys.stdout.write(body)


def _strip_dump(node: ast.AST) -> str:
    """AST dump with line/col info removed -> structural comparison."""
    for n in ast.walk(node):
        for attr in ("lineno", "col_offset", "end_lineno", "end_col_offset"):
            if hasattr(n, attr):
                setattr(n, attr, None)
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def cmd_verify(fileA: str, nameA: str, fileB: str, nameB: str) -> None:
    _, _, treeA = _load(fileA)
    _, _, treeB = _load(fileB)
    a = _qualname_map(treeA).get(nameA)
    b = _qualname_map(treeB).get(nameB)
    if a is None:
        print(f"NOT FOUND in {fileA}: {nameA}", file=sys.stderr)
        sys.exit(2)
    if b is None:
        print(f"NOT FOUND in {fileB}: {nameB}", file=sys.stderr)
        sys.exit(2)
    if _strip_dump(a) == _strip_dump(b):
        print(f"IDENTICAL  {nameA}")
    else:
        print(f"DIFFERENT  {nameA}  ({fileA}) vs {nameB} ({fileB})")
        sys.exit(1)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    cmd, path = sys.argv[1], sys.argv[2]
    if cmd == "list":
        cmd_list(path)
    elif cmd == "span":
        cmd_span(path, sys.argv[3])
    elif cmd == "cut":
        cmd_cut(path, sys.argv[3:])
    elif cmd == "extract":
        cmd_extract(path, sys.argv[3:])
    elif cmd == "extract-to":
        # extract-to <src> <dst> <qualname...>
        cmd_extract(path, sys.argv[4:], out=sys.argv[3])
    elif cmd == "verify":
        cmd_verify(path, sys.argv[3], sys.argv[4], sys.argv[5])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
