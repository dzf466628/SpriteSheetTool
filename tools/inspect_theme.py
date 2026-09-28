# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""诊断工具：整窗渲染，找出有没有「成条描不出来的浅色区域」。

**这是诊断工具，不是断言工具**（正常退出码 0）：它把发现的东西列出来给人看，
适合在做界面调整后随手跑一次。

⚠️ 必须**套上主题**再量（脚本自己会调 `setStyle("Fusion")` + `APP_QSS`）。
第一版忘了套，于是所有"靠全局 QSS 铺底"的地方都露了窗口默认底色（浅灰 #EFEFEF），
报出一堆"白条"，看上去像严重缺陷，其实全是**测试环境**的问题 —— 程序里根本不出现。
教训：量界面之前，先确认被测环境的样式和真实运行一致。

顺带澄清一个 Qt 细节（当初就是它把人带偏的）：重写了 `paintEvent` 的 QWidget 子类
确实不会自动绘制样式表背景，但**露出来的是父控件的底色**，不是窗口默认色。
套上深色 QSS 之后父底也是深的，所以不会出现"白条"。实测最小复现：

    父 = 普通 QWidget（拿到 QSS 深色底）+ 子 = 重写 paintEvent 不铺底
      -> 子控件区域仍是 #232323（父底透出来）
    父 = 同样重写 paintEvent 也不铺底
      -> 仍是 #232323（更上一层会画）

判据：找**连续**的浅色长条（>= 40px）。文字/滑块本身也是浅色，但都是零散短笔画，
用"一行里浅色像素数"会把它们全算进来（实测深色界面一行也能有 300 个浅色像素）。

用法: .venv\\Scripts\\python.exe tools\\inspect_theme.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LIGHT = 180            # 亮度超过这个值算"浅色"（正常深色底在 0x1a~0x35）
RUN = 40               # 连续这么长的浅色才算"成条"


def main() -> int:
    app = QApplication(sys.argv)
    from spritesheet.ui.theme import APP_QSS

    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)

    from spritesheet.ui.main_window import MainWindow
    from spritesheet.ui.models import Action

    win = MainWindow()
    win.resize(1000, 1000)
    sheet = QPixmap(240, 80)
    sheet.fill(Qt.GlobalColor.transparent)
    painter = QPainter(sheet)
    for i in range(3):
        painter.fillRect(QRect(i * 80 + 6, 6, 68, 68), QColor("#C62828"))
    painter.end()
    win._on_image_loaded(sheet)
    win.image_viewer._frames = [(6, 6, 68, 68), (86, 6, 68, 68), (166, 6, 68, 68)]
    win.image_viewer._frame_logical = {}
    action = Action("动作1", "#C62828")
    action.selected_indices = [0]
    win.actions.append(action)
    win._append_action_card(action)
    win.image_viewer.set_actions(win.actions)
    win.image_viewer.crop_frames_for_action(action)
    win.image_viewer.set_current_action(action)
    win.preview.set_source_pixmap(win.image_viewer._pixmap)
    win.preview.set_action(action)
    win.show()
    app.processEvents()

    image = win.grab().toImage()
    width, height = image.width(), image.height()
    print(f"整窗渲染 {width}x{height}（已套 Fusion + APP_QSS）")

    def is_light(x, y):
        color = image.pixelColor(x, y)
        return min(color.red(), color.green(), color.blue()) > LIGHT

    def long_runs(pixels):
        out, run, start = [], 0, 0
        for index, light in enumerate(pixels):
            if light:
                if run == 0:
                    start = index
                run += 1
            else:
                if run >= RUN:
                    out.append((start, start + run - 1, run))
                run = 0
        if run >= RUN:
            out.append((start, start + run - 1, run))
        return out

    found = 0
    for axis, size, other in (("y", height, width), ("x", width, height)):
        rows = []
        for i in range(size):
            pixels = ([is_light(x, i) for x in range(width)] if axis == "y"
                      else [is_light(i, y) for y in range(height)])
            runs = long_runs(pixels)
            if runs:
                rows.append((i, max(runs, key=lambda r: r[2]), other))
        if not rows:
            print(f"  [OK]   没有成条的浅色区域（按 {axis} 扫描）")
            continue
        found += len(rows)
        for i, run, _ in rows[:20]:
            print(f"  [注意] {axis}={i} 处有连续浅色 {run[2]}px（{i} 上 {run[0]}..{run[1]}）")
        if len(rows) > 20:
            print(f"  ... 以及另外 {len(rows) - 20} 处")

    print()
    print("几处界面部件当前的颜色：")
    for label, widget in (("工程标签栏", win.tabs),
                          ("水平标尺", win.image_viewer.h_ruler),
                          ("垂直标尺", win.image_viewer.v_ruler),
                          ("预览面板", win.preview),
                          ("画布", win.image_viewer.canvas)):
        x = widget.mapTo(win, widget.rect().topLeft()).x() + widget.width() // 2
        y = widget.mapTo(win, widget.rect().topLeft()).y() + 3
        color = image.pixelColor(max(0, min(width - 1, x)), max(0, min(height - 1, y)))
        print(f"  {label:10s} #{color.red():02X}{color.green():02X}{color.blue():02X}")

    win.close()
    print()
    print(f"共发现 {found} 处成条浅色区域（0 = 干净）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
