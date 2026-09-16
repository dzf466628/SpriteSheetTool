"""Menu structure regression check.

Asserts the requested menu layout:
  - 文件 sits on the LEFT, 编辑 on the RIGHT
  - 文件 order: 添加图片 / 导入 JSON 工程… / — / 打开工程… / 保存工程 / 工程另存为…
                / — / 检查更新 / — / 退出
  - 新建工程 no longer appears in the menu bar

Usage: .venv\\Scripts\\python.exe tools\\verify_menu.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spritesheet.ui.main_window import MainWindow

EXPECTED_MENUS = ["文件", "编辑"]
EXPECTED_FILE_ITEMS = [
    "添加图片", "导入 JSON 工程…", "---", "打开工程…", "保存工程", "工程另存为…",
    "---", "检查更新", "---", "退出",
]


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    menubar = win.menuBar()

    top = [a.text() for a in menubar.actions()]
    print(f"  顶层菜单顺序: {top}")

    ok = True
    if top != EXPECTED_MENUS:
        ok = False
        print(f"  [FAIL] 期望 {EXPECTED_MENUS}")
    else:
        print("  [OK]   文件在左、编辑在右")

    file_menu = menubar.actions()[0].menu()
    items = []
    for act in file_menu.actions():
        items.append("---" if act.isSeparator() else act.text())
    print(f"  文件菜单顺序: {items}")
    if items != EXPECTED_FILE_ITEMS:
        ok = False
        print(f"  [FAIL] 期望 {EXPECTED_FILE_ITEMS}")
    else:
        print("  [OK]   文件菜单顺序与分组符合预期")

    if any("新建工程" in t for t in items):
        ok = False
        print("  [FAIL] 文件菜单里仍有「新建工程」")
    else:
        print("  [OK]   文件菜单已无「新建工程」")

    print()
    print("MENU OK" if ok else "MENU MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
