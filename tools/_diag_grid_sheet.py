# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""临时探针：把真图集拖进软件 → 点自动对齐 → 看均分格那条路对不对。

判定标准（不循环论证）：图集本身就是"真值" —— 对齐之后，每帧内容在逻辑画布上的相对位置
必须等于它在**原图集格子里**的相对位置。所以直接量：对齐后的落点 − 格子里的落点，应该全是
同一个常数（整体平移无所谓）；跨帧不一致就是没对齐。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt6.QtCore import QElapsedTimer                            # noqa: E402
from PyQt6.QtGui import QPixmap                                   # noqa: E402
from PyQt6.QtWidgets import QApplication                          # noqa: E402

from settings_guard import protect_settings                       # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv)
SHEET = ROOT / "9月25日(1)_keyed_spritesheet_0.png"


def wait(ms):
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        QApplication.processEvents()


def main():
    restore = protect_settings()
    from spritesheet.settings import set_detect_min_area
    set_detect_min_area(30)            # 用户自己的设置
    from spritesheet.ui.main_window import MainWindow
    from spritesheet.ui.transform import (cell_rect, compute_preview_layout,
                                          detect_sheet_grid, grid_shape)

    pm = QPixmap(str(SHEET))
    print(f"图集 {pm.width()}x{pm.height()}")
    win = MainWindow()
    win.resize(1280, 800)
    win.show()
    wait(200)
    viewer = win.image_viewer
    viewer.set_pixmap(pm)              # 用户拖单张图集的入口：走自动检测，出那些灰色紧切框
    wait(8000)
    print(f"检测到的元素 {len(viewer._frames)} 个（我早先单独量到 289 个身体 + 16 个小碎块）")
    if not viewer._frames:
        print("检测不到元素，后面没法验")
        return 1
    action = win._create_animation_action("真图集", [], 0, len(viewer._frames))
    win._install_animation_action(action)
    wait(2000)
    n = len(action.frames)
    print(f"动作帧数 {n}")

    rects = action.frame_rects
    index_of = detect_sheet_grid(rects, pm.width(), pm.height())
    print("均分判定 =", grid_shape(index_of) if index_of else None)
    if not index_of:
        print("!! 均分判不出来")
        return 1
    cols, rows = grid_shape(index_of)

    before = compute_preview_layout(action)[2]
    spread_before = (max(p[0] for p in before) - min(p[0] for p in before),
                     max(p[1] for p in before) - min(p[1] for p in before))

    win._on_auto_align()
    wait(2000)
    print("状态栏：", win.lbl_status.text()[:120])
    after = compute_preview_layout(action)[2]

    truth = []
    for i in range(n):
        cell = cell_rect(index_of, i, pm.width(), pm.height(), cols, rows)
        truth.append((rects[i][0] - cell[0], rects[i][1] - cell[1]))
    kx = [p[0] - t[0] for p, t in zip(after, truth)]
    ky = [p[1] - t[1] for p, t in zip(after, truth)]
    print(f"误差（对齐后落点 − 格子真值）：x {min(kx)}~{max(kx)}（差 {max(kx)-min(kx)}）"
          f"，y {min(ky)}~{max(ky)}（差 {max(ky)-min(ky)}）")
    print("（这个差必须是同一个常数 = 整体平移；跨帧不一致就是没对齐）")
    print(f"对齐前后落点跨度：{spread_before} → "
          f"({max(p[0] for p in after)-min(p[0] for p in after)}, "
          f"{max(p[1] for p in after)-min(p[1] for p in after)})")
    win.close()
    wait(100)
    del win, action, pm
    restore()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())