"""Regression test: composite-frame badges of different actions must not overlap.

Scenario: two actions composite the SAME pair of elements, so their composite
boxes sit at the same place (non-current actions are only staggered by 2px per
action index).  Before the fix both frame-number badges were drawn at the box's
top-left corner and covered each other; now they must be laid out side by side.

Usage: .venv\\Scripts\\python.exe tools\\verify_composite_badges.py
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

COLOR_A = "#E3351C"
COLOR_B = "#1C98E3"
COLOR_C = "#1CE335"                            # 第三个动作（验证顺延是通用的）
COLORS = [COLOR_A, COLOR_B, COLOR_C]
ELEMS = [(10, 10, 60, 40), (80, 10, 60, 40)]   # 合成组的两个元素
SQ = 16
STEP = SQ + 2


def build_scene(n_actions):
    """n 个动作全部合成同一组元素（框位置完全相同）。"""
    viewer = ImageViewer()
    pm = QPixmap(400, 300)
    pm.fill(QColor("#404040"))
    viewer._pixmap = pm
    viewer._frames = list(ELEMS)
    viewer._frame_logical = {}
    viewer._bg_mode = "checker"

    frame = QPixmap(140, 40)
    frame.fill(QColor("#606060"))

    acts = []
    for i in range(n_actions):
        act = Action(f"动作{i + 1}", COLORS[i % len(COLORS)])
        act.frames = [frame]
        act.selected_indices = [0, 1]
        act.composite_groups = [[0, 1]]     # 同一组元素
        acts.append(act)

    viewer.actions = acts
    viewer.current_action = acts[0]
    viewer._zoom = 1.0
    viewer._offset = QPointF(0, 0)
    return viewer


def dominant_color(img, x, y, w, h):
    counts = {}
    for py in range(int(y), int(y + h)):
        for px in range(int(x), int(x + w)):
            name = QColor(img.pixel(px, py)).name().upper()
            counts[name] = counts.get(name, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0]


def run_case(app, n_actions):
    """断言 n 个动作的色块依次占第 1..n 格且互不重叠。"""
    viewer = build_scene(n_actions)
    viewer.canvas.resize(400, 300)
    img = viewer.canvas.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    box = (ELEMS[0][0], ELEMS[0][1])   # 第 1 格锚点（当前动作无 2px 偏移）

    print(f"--- {n_actions} 个动作合成同一组元素 ---")
    ok = True
    slots = []
    for i in range(n_actions):
        want = COLORS[i % len(COLORS)]
        # 每个后续动作的框带 2px 偏移，色块再顺延 i 格
        x = box[0] + (i * 2 if i else 0) + i * STEP
        y = box[1] + (i * 2 if i else 0)
        got = dominant_color(img, x, y, SQ, SQ)
        good = got == want.upper()
        ok = ok and good
        slots.append((x, x + SQ))
        print(f"  [{'OK' if good else 'FAIL'}] 第 {i + 1} 格 ({int(x)},{int(y)}) 期望 {want} 实际 {got}")

    # 任意两格不得重叠
    overlap = any(slots[i][1] > slots[i + 1][0] for i in range(len(slots) - 1))
    print(f"  [{'OK' if not overlap else 'FAIL'}] {n_actions} 格互不重叠  {slots}")
    ok = ok and not overlap
    return ok


def main() -> int:
    app = QApplication(sys.argv)
    ok = run_case(app, 2) and run_case(app, 3)
    print()
    print("COMPOSITE BADGE LAYOUT OK" if ok else "COMPOSITE BADGE OVERLAP DETECTED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
