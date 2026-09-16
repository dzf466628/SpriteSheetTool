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
    check("按钮总数是 8（5 模式 + 清空变换 + 2 镜像）",
          len(grid._buttons) == 8, f"实际 {len(grid._buttons)}")
    check("行数 = 8 / 2 = 4（末行不空）", len(rows) == 4, f"实际 {len(rows)}")
    check("每行都是 2 个", all(len(names) == 2 for names in rows), f"实际 {[len(r) for r in rows]}")
    check("前两行是 旋转/缩放 与 斜切/透视",
          rows[0] == ["旋转", "缩放"] and rows[1] == ["斜切", "透视"], f"实际 {rows[:2]}")
    check("第三行是 扭曲/清空变换", rows[2] == ["扭曲", "清空变换"], f"实际 {rows[2]}")
    check("第四行是两个镜像", rows[3] == ["水平镜像", "垂直镜像"], f"实际 {rows[3]}")

    print()
    print("=== 2. 「当前变换」摘要单独一行、占满宽度 ===")
    summary = panel.lbl_transform_summary
    clear_btn = panel.btn_clear_transform
    grid_bottom = grid.y() + grid.height()
    print(f"  网格 y={grid.y()} 高 {grid.height()}（底部 {grid_bottom}）；"
          f"摘要 y={summary.y()} 宽 {summary.width()}；清空按钮 y={clear_btn.y()} 宽 {clear_btn.width()}")
    check("摘要在网格下方（不与按钮同行）", summary.y() >= grid_bottom - 2,
          f"摘要 y={summary.y()}，网格底部 {grid_bottom}")
    check("摘要宽度与网格一致（独占一行）",
          abs(summary.width() - grid.width()) <= 2,
          f"摘要 {summary.width()} vs 网格 {grid.width()}")
    check("清空变换在网格里（不再和摘要挤同一行）",
          clear_btn in grid._buttons, f"网格按钮 {[b.text() for b in grid._buttons]}")

    print()
    print("=== 3. 窄了要换行而不是压扁 ===")
    for width in (420, 340, 300, 240, 200, 170):
        win.preview.setFixedWidth(width)
        app.processEvents()
        print(f"  预览面板 {width}px -> 网格 {grid.width()}px，列数 {grid.columns()}，"
              f"最窄按钮 {min(b.width() for b in grid._buttons)}")
    check("无论多窄，按钮都不会被压到最小宽度以下",
          all(b.width() >= 80 for b in grid._buttons),
          f"最窄 {min(b.width() for b in grid._buttons)}")

    # 面板本身有最小宽度（摘要行 + 按钮），挤不到换行阈值；所以换行逻辑单独用
    # 一个不在布局里的网格控件来验（它能被自由 resize）。
    from PyQt6.QtWidgets import QPushButton

    from spritesheet.ui.widgets import ResponsiveButtonGrid

    standalone = ResponsiveButtonGrid([QPushButton(f"b{i}") for i in range(6)],
                                      max_columns=2, min_button=84)
    # 必须 show()：隐藏的控件收不到 resizeEvent，列数就不会重算（实测一直停在 2）
    standalone.show()
    app.processEvents()
    # 用 setFixedWidth 强制宽度：普通 resize 会被控件自身最小宽度挡住（实测 150 被顶回 200），
    # 那样测不到窄宽度下的换行分支。
    standalone.setFixedWidth(320)
    app.processEvents()
    print(f"  独立网格 320px(实宽 {standalone.width()}) -> 列数 {standalone.columns()}")
    check("  够宽时每行 2 个", standalone.columns() == 2, f"实际 {standalone.columns()}")
    standalone.setFixedWidth(150)
    app.processEvents()
    print(f"  独立网格 150px(实宽 {standalone.width()}) -> 列数 {standalone.columns()}")
    check("  太窄时退化成一行 1 个（换行而不是压扁）", standalone.columns() == 1,
          f"实际 {standalone.columns()}")
    standalone.setFixedWidth(320)
    app.processEvents()
    check("  恢复宽度后回到每行 2 个", standalone.columns() == 2, f"实际 {standalone.columns()}")

    # 给面板最少受力的宽度，免得影响后面的检查
    win.preview.setFixedWidth(420)
    app.processEvents()

    win.close()
    print()
    if FAILURES:
        print("ADJUST PANEL FAILED：", *FAILURES, sep="\n  - ")
        return 1
    print("ADJUST PANEL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
