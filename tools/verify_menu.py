"""Menu structure regression check.

Asserts the requested menu layout:
  - 文件 sits on the LEFT, 编辑 on the RIGHT
  - 文件 order: 添加图片 / 导入 JSON 工程… / — / 新建工程 / 打开工程… / 保存工程
                / 工程另存为… / — / 退出
  - 编辑 order: 撤销 / — / 软件设置…
  - 检查更新 is NOT in 文件 any more: it lives inside 编辑 → 软件设置…
  - 新建工程 is back, and it opens a NEW TAB (see tools/verify_tabs.py)

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
    "添加图片", "导入 JSON 工程…", "---", "新建工程", "打开工程…", "保存工程",
    "工程另存为…", "---", "退出",
]
EXPECTED_EDIT_ITEMS = ["撤销", "---", "软件设置…"]


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

    edit_menu = menubar.actions()[1].menu()
    edit_items = []
    for act in edit_menu.actions():
        edit_items.append("---" if act.isSeparator() else act.text())
    print(f"  编辑菜单顺序: {edit_items}")
    if edit_items != EXPECTED_EDIT_ITEMS:
        ok = False
        print(f"  [FAIL] 期望 {EXPECTED_EDIT_ITEMS}")
    else:
        print("  [OK]   编辑菜单含「软件设置…」")

    if any("新建工程" in t for t in items):
        print("  [OK]   文件菜单里有「新建工程」（Ctrl+N，新开标签页）")
    else:
        ok = False
        print("  [FAIL] 文件菜单里没有「新建工程」")

    if any("检查更新" in t for t in items):
        ok = False
        print("  [FAIL] 文件菜单里仍有「检查更新」（应已移进软件设置）")
    else:
        print("  [OK]   检查更新已从文件菜单移走")

    # 必须关窗口：closeEvent 里才会走 ImageViewer.shutdown()。
    # 不关就走，元素检测线程会与窗口析构撞车（0xC0000005，实测连续跑套件时必现）。
    win.close()

    print()
    print("MENU OK" if ok else "MENU MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
