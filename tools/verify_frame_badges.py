# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression test: frame-number badges of different actions must not overlap.

The bug: badge x-positions were computed as `rect.left() + action_index * (sq+2)`,
which reserves exactly ONE slot per action.  An action whose element is reused
draws one badge per frame number, so it occupies several slots — and the next
action's first badge landed on top of the previous action's last badge (two
numbers crammed into one square).

Scenario: element #0 selected by action A (reused once -> frame numbers 1 and 2)
and action B (frame number 1).  Expected: three side-by-side badges, A, A, B.

Usage: .venv\\Scripts\\python.exe tools\\verify_frame_badges.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spritesheet.ui.image_viewer import ImageViewer
from spritesheet.ui.models import Action

COLOR_A = "#E3351C"   # 动作 A 的颜色
COLOR_B = "#1C98E3"   # 动作 B 的颜色
ELEM = (10, 10, 100, 60)   # 元素在原图中的包围盒
SQ = 16
STEP = SQ + 2


def build_scene(app):
    viewer = ImageViewer()
    pm = QPixmap(400, 300)
    pm.fill(QColor("#404040"))          # 非动作色的底色，便于判定"没画到"
    viewer._pixmap = pm
    viewer._frames = [ELEM]
    viewer._frame_logical = {}
    viewer._bg_mode = "checker"

    frame = QPixmap(ELEM[2], ELEM[3])
    frame.fill(QColor("#606060"))

    # 动作 A：元素 0 出现在第 1、2 帧（=复用了一次）-> 两个帧号色块
    act_a = Action("A", COLOR_A)
    act_a.frames = [frame, frame]
    act_a.selected_indices = [0]
    act_a.frame_layouts = [[(0, 0, 0)], [(0, 0, 0)]]
    act_a.frame_layouts_base = 1

    # 动作 B：元素 0 只出现在第 1 帧 -> 一个帧号色块
    act_b = Action("B", COLOR_B)
    act_b.frames = [frame]
    act_b.selected_indices = [0]
    act_b.frame_layouts = [[(0, 0, 0)]]
    act_b.frame_layouts_base = 1

    viewer.actions = [act_a, act_b]
    viewer.current_action = act_a
    viewer._zoom = 1.0
    viewer._offset = QPointF(0, 0)
    return viewer


def dominant_color(img, x, y, w, h):
    """区域内出现次数最多的颜色（避开居中的白色数字字形干扰）。"""
    counts = {}
    for py in range(int(y), int(y + h)):
        for px in range(int(x), int(x + w)):
            name = QColor(img.pixel(px, py)).name().upper()
            counts[name] = counts.get(name, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def main() -> int:
    app = QApplication(sys.argv)
    viewer = build_scene(app)
    viewer.canvas.resize(400, 300)
    # 采样前把两条**浮动**工具条收起来：它们是半透明覆盖层、还能被用户拖走，
    # 正好压在帧号色块上就会把颜色染淡（工具条加了「完美像素」按钮变宽之后，
    # 这个 400 宽的小画布上就压住了第 2、3 格）。浮层不影响元素怎么画。
    bars = [b for b in (viewer._toolbar, viewer._v_toolbar) if b is not None]
    for bar in bars:
        bar.hide()
    img = viewer.canvas.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)

    left, top = ELEM[0], ELEM[1]
    probes = [
        ("第 1 格（动作A 帧号1）", 0, COLOR_A),
        ("第 2 格（动作A 帧号2）", 1, COLOR_A),
        ("第 3 格（动作B 帧号1）", 2, COLOR_B),
    ]
    ok = True
    for label, slot, want in probes:
        x = left + slot * STEP
        got = dominant_color(img, x, top, SQ, SQ)
        good = got == want.upper()
        ok = ok and good
        print(f"  [{'OK' if good else 'FAIL'}] {label:<24} x={int(x):<4} 期望 {want} 实际 {got}")

    # 第 2、3 格之间必须留有 2px 间隙（不是被同一块色块连续覆盖）
    gap_x = left + STEP + SQ
    gap = dominant_color(img, gap_x, top, 2, SQ)
    gap_ok = gap not in (COLOR_A.upper(), COLOR_B.upper())
    ok = ok and gap_ok
    print(f"  [{'OK' if gap_ok else 'FAIL'}] 第 2、3 格之间 2px 间隙        "
          f"x={int(gap_x):<4} 实际 {gap}（应非动作色）")

    print()
    print("BADGE LAYOUT OK" if ok else "BADGE OVERLAP DETECTED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
