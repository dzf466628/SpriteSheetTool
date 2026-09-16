"""Quick static scan: unused imports + dead top-level defs (AST-based, no deps)."""
import ast
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")

for path in sorted(root.rglob("*.py")):
    if ".venv" in path.parts or "__pycache__" in path.parts or "backup" in path.parts:
        continue
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        print(f"{path}: SYNTAX ERROR {exc}")
        continue

    imported: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                name = a.asname or a.name.split(".")[0]
                imported[name] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "*":
                    continue
                imported[a.asname or a.name] = node.lineno

    # collect all Name/Attribute root identifiers actually used outside import stmts
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            cur = node
            while isinstance(cur, ast.Attribute):
                cur = cur.value
            if isinstance(cur, ast.Name):
                used.add(cur.id)

    unused = {n: ln for n, ln in imported.items() if n not in used}

    # top-level function/class defined but never referenced
    defined: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined[node.name] = node.lineno
    # count references (Name occurrences) minus the definition itself
    ref_counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            ref_counts[node.id] = ref_counts.get(node.id, 0) + 1
    dead = {n: ln for n, ln in defined.items()
            if ref_counts.get(n, 0) == 0 and not n.startswith("__")}

    if unused or dead:
        print(f"\n== {path} ==")
        for n, ln in sorted(unused.items(), key=lambda kv: kv[1]):
            print(f"  unused import   L{ln}: {n}")
        for n, ln in sorted(dead.items(), key=lambda kv: kv[1]):
            print(f"  dead def        L{ln}: {n}")
