"""Quick static scan: unused imports + suspected dead top-level defs (AST-based, no deps).

**诊断工具，不是回归**：不参与整套专项回归，退出码恒为 0，输出只供人看。

判定口径（2026-09-23 收紧 —— 此前误报很多，见 README「诊断与一次性工具」）：
  * 读文件用 `utf-8-sig`：带 UTF-8 BOM 的源文件以前会在这里报 SYNTAX ERROR
    （其实就是 BOM 被当成 `U+FEFF` 非法字符）；
  * `from __future__ import ...` 不算「未使用导入」—— 它本来就不会被当名字引用，
    以前每个文件都会误报一行 `unused import: annotations`；
  * 顶层 def/class 只有在**整棵扫描树**里都没有被引用时才算「疑似死代码」。
    以前只看定义所在文件，于是 `read_project` / `SingleInstance` / `load_project`
    这类「定义在一个模块、被别的模块调用」的公开 API 被整片误报成 dead def。

用法: .venv\\Scripts\\python.exe tools\\static_scan.py [扫描根目录，默认 .]
"""
import ast
import sys
from pathlib import Path


def scan(path: Path) -> tuple[dict[str, int], set[str], dict[str, int]]:
    """解析一个文件，返回 (导入名→行号, 被引用的名字, 顶层定义名→行号)。"""
    src = path.read_text(encoding="utf-8-sig", errors="replace")
    tree = ast.parse(src)

    imported: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported[a.asname or a.name.split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            for a in node.names:
                if a.name != "*":
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

    defined: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined[node.name] = node.lineno
    return imported, used, defined


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    files = [p for p in sorted(root.rglob("*.py"))
             if not ({".venv", "__pycache__", "backup"} & set(p.parts))]

    parsed: dict[Path, tuple] = {}
    refs_global: dict[str, int] = {}
    errors: list[str] = []
    for path in files:
        try:
            imported, used, defined = scan(path)
        except SyntaxError as exc:
            errors.append(f"{path}: SYNTAX ERROR {exc}")
            continue
        parsed[path] = (imported, used, defined)
        for name in used:
            refs_global[name] = refs_global.get(name, 0) + 1

    flagged = 0
    for path, (imported, used, defined) in parsed.items():
        unused = {n: ln for n, ln in imported.items() if n not in used}
        # 本文件引用过就是活的；否则再看整棵树里有没有别处引用
        dead = {n: ln for n, ln in defined.items()
                if not n.startswith("__") and n not in used
                and refs_global.get(n, 0) == 0}
        if unused or dead:
            flagged += 1
            print(f"\n== {path} ==")
            for n, ln in sorted(unused.items(), key=lambda kv: kv[1]):
                print(f"  unused import   L{ln}: {n}")
            for n, ln in sorted(dead.items(), key=lambda kv: kv[1]):
                print(f"  dead def        L{ln}: {n}")

    print()
    for line in errors:
        print(line)
    print(f"扫描 {len(parsed)} 个文件，{flagged} 个有提示"
          + (f"，{len(errors)} 个解析失败" if errors else "，无解析失败"))
    return 0


if __name__ == "__main__":
    sys.exit(main())