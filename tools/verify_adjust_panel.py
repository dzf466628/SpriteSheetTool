# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

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

from PyQt6.QtCore import QEvent, QPointF
from PyQt6.QtGui import QEnterEvent
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

    print()
    print("=== 4. 「清空变换」是红色的（与其他按钮反着，证明它是动作不是模式）===")
    clear = panel.btn_clear_transform
    sheet = clear.styleSheet()
    print(f"  按钮宽 {clear.width()} 高 {clear.height()}")
    check("平时就是红字红边（color / border 都是品牌红）",
          "color: #E91E63" in sheet and "border: 1px solid #E91E63" in sheet)
    check("悬停时反相成红底（background: #E91E63）",
          ":hover" in sheet and "background: #E91E63" in sheet)
    icon_image = clear.icon().pixmap(14, 14).toImage()
    red_pixels = 0
    for y in range(icon_image.height()):
        for x in range(icon_image.width()):
            c = icon_image.pixelColor(x, y)
            if c.alpha() > 60 and c.red() - c.green() > 40 and c.red() - c.blue() > 20:
                red_pixels += 1
    print(f"  图标里的红色像素 {red_pixels}")
    check("按钮图标也是红色的", red_pixels >= 3, f"红色像素 {red_pixels}")
    check("按钮够宽放得下「清空变换」四个字", clear.width() >= 72, f"宽 {clear.width()}")

    print()
    print("=== 5. 8 个变换按钮一样长（清空变换不许比别人长） ===")
    widths = [b.width() for b in grid._buttons]
    names = [(b.text(), b.width()) for b in grid._buttons]
    print(f"  各按钮宽度：{names}")
    # 改之前 QGridLayout 会把第 2 列吃满剩余空间：338px 的网格里第 0 列 80px、
    # 第 1 列 253px —— "清空变换"正好在第 1 列，看着比邻居长一倍多（用户报的）。
    check("所有变换按钮等宽", len(set(widths)) == 1, f"实际 {sorted(set(widths))}")
    check("清空变换与邻居等宽", clear.width() == grid._buttons[0].width(),
          f"{clear.width()} vs {grid._buttons[0].width()}")
    for width in (420, 300, 260):
        win.preview.setFixedWidth(width)
        app.processEvents()
        row = [b.width() for b in grid._buttons]
        print(f"  面板 {width}px -> 网格 {grid.width()}px，列数 {grid.columns()}，宽度 {sorted(set(row))}")
        check(f"面板 {width}px 时仍然等宽", len(set(row)) == 1, f"实际 {sorted(set(row))}")
    win.preview.setFixedWidth(420)
    app.processEvents()

    print()
    print("=== 6. 清空变换悬停时图标转白（QSS 改不了 pixmap，靠换图标） ===")
    check("按钮是会换图标的类型", hasattr(clear, "set_state_icons"), type(clear).__name__)
    normal_icon = clear.icon().pixmap(14, 14).toImage()
    normal_red = sum(1 for y in range(normal_icon.height()) for x in range(normal_icon.width())
                     if normal_icon.pixelColor(x, y).red() - normal_icon.pixelColor(x, y).green() > 40
                     and normal_icon.pixelColor(x, y).alpha() > 60)
    print(f"  悬停前的红色像素 {normal_red}，图标状态 {getattr(clear, 'icon_state', lambda: '?')()}")
    enter = QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1))
    app.sendEvent(clear, enter)
    app.processEvents()
    hover_icon = clear.icon().pixmap(14, 14).toImage()
    white = 0
    red_now = 0
    for y in range(hover_icon.height()):
        for x in range(hover_icon.width()):
            c = hover_icon.pixelColor(x, y)
            if c.alpha() > 60:
                if c.red() > 220 and c.green() > 220 and c.blue() > 220:
                    white += 1
                if c.red() - c.green() > 40:
                    red_now += 1
    print(f"  悬停后：白色像素 {white}，红色像素 {red_now}，状态 {clear.icon_state()}")
    check("悬停时切到悬停图标", clear.icon_state() == "hover", f"实际 {clear.icon_state()}")
    check("悬停图标是白色的", white >= 3 and red_now == 0,
          f"白 {white} 红 {red_now}")
    app.sendEvent(clear, QEvent(QEvent.Type.Leave))
    app.processEvents()
    check("移开后恢复红色图标", clear.icon_state() == "normal", f"实际 {clear.icon_state()}")

    win.close()
    # Qt 收尾：headless 下直接让解释器回收原生对象/后台线程会随机 0xC0000005。
    # 与 smoke_test.py 同一套收尾（先跑一轮事件，再 deleteLater，再 quit）。
    for _ in range(3):
        app.processEvents()
    win.deleteLater()
    app.processEvents()
    app.quit()
    app.processEvents()
    del win
    print()
    if FAILURES:
        print("ADJUST PANEL FAILED：", *FAILURES, sep="\n  - ")
        return 1
    print("ADJUST PANEL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
