"""动画调节面板里「变换」一栏的布局回归。

用户要求：
  1. 变换按钮**两个一行**（原来是五个按钮各占一行，很占高度）；
  2. 「当前变换」摘要**单独一行**（原来和两个镜像按钮挤同一行，被压得看不清）；
  3. 面板变窄时**换行**而不是把按钮压扁（原来按钮 setMinimumWidth(112) 且 Expanding，
     窄面板会把侧边栏/预览面板顶开）。

用法: .venv\\Scripts\\python.exe tools\\verify_adjust_panel.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK' if ok else 'FAIL'}]   {label} {detail}")
    if not ok:
        FAILURES.append(label)


def rows_of(grid) -> list[list[str]]:
    """按当前列数把按钮分行（回归里用来看"每行几个"）。"""
    columns = max(1, grid.columns())
    rows: list[list[str]] = []
    for index, button in enumerate(grid._buttons):
        row = index // columns
        while len(rows) <= row:
            rows.append([])
        rows[row].append(button.text())
    return rows


def main() -> int:
    app = QApplication(sys.argv)
    from spritesheet.ui.theme import APP_QSS

    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)

    from spritesheet.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1400, 1000)
    win.show()
    app.processEvents()

    panel = win.preview.adjust_panel
    grid = panel._transform_grid

    print("=== 1. 默认宽度下每行两个 ===")
    print(f"  面板宽 {panel.width()}，变换网格宽 {grid.width()}，列数 {grid.columns()}")
    rows = rows_of(grid)
    for index, names in enumerate(rows):
        print(f"    第 {index + 1} 行: {names}")
    check("默认是每行 2 个", grid.columns() == 2, f"实际 {grid.columns()}")
    check("按钮总数是 7（5 个变换模式 + 2 个镜像）", len(grid._buttons) == 7,
          f"实际 {len(grid._buttons)}")
    check("行数 = ceil(7/2) = 4", len(rows) == 4, f"实际 {len(rows)}")
    check("每行不超过 2 个", all(len(names) <= 2 for names in rows))
    check("前两行是 旋转/缩放 与 斜切/透视",
          rows[0] == ["旋转", "缩放"] and rows[1] == ["斜切", "透视"], f"实际 {rows[:2]}")

    print()
    print("=== 2. 「当前变换」单独一行 ===")
    summary = panel.lbl_transform_summary
    grid_bottom = grid.y() + grid.height()
    print(f"  网格 y={grid.y()} 高 {grid.height()}（底部 {grid_bottom}）；摘要 y={summary.y()}")
    check("摘要在网格下方（不与按钮同行）", summary.y() >= grid_bottom - 2,
          f"摘要 y={summary.y()}，网格底部 {grid_bottom}")
    check("摘要宽度与网格一致（占满一行）", abs(summary.width() - grid.width()) <= 2,
          f"摘要 {summary.width()} vs 网格 {grid.width()}")
    check("摘要不再与镜像按钮同宽挤压", summary.width() > grid.width() // 2,
          f"摘要 {summary.width()}")

    print()
    print("=== 3. 面板变窄时换行而不是压扁 ===")
    for width in (420, 340, 300, 220, 170):
        win.preview.setFixedWidth(width)
        app.processEvents()
        print(f"  预览面板 {width}px -> 网格 {grid.width()}px，列数 {grid.columns()}")
    win.preview.setFixedWidth(170)
    app.processEvents()
    check("窄面板下退化成一行一个", grid.columns() == 1, f"实际 {grid.columns()}")
    check("按钮宽度不会被压到最小宽度以下",
          all(b.width() >= 80 for b in grid._buttons),
          f"最窄 {min(b.width() for b in grid._buttons)}")
    win.preview.setFixedWidth(520)
    app.processEvents()
    check("面板恢复宽度后回到一行两个", grid.columns() == 2, f"实际 {grid.columns()}")

    win.close()
    print()
    if FAILURES:
        print("ADJUST PANEL FAILED：", *FAILURES, sep="\n  - ")
        return 1
    print("ADJUST PANEL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
