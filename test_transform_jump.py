#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""回归：变换拖动中 -> 松手后几何一致（不闪跳），红色锚点拖动精确且不带动手柄框。"""
from __future__ import annotations
import os, sys, math
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap, QColor, QPainter
from PyQt6.QtCore import QRectF, QPointF

from spritesheet.ui.models import Action
from spritesheet.ui.preview import PreviewCanvas
from spritesheet.ui.transform import DEFAULT_QUAD, DEFAULT_ROTATION_PIVOT

app = QApplication([])
PASS = 0; FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  [OK]   {name} {detail}")
    else: FAIL += 1; print(f"  [FAIL] {name} {detail}")

def make_pm(w, h, inset=4):
    pm = QPixmap(w, h); pm.fill(QColor(0, 0, 0, 0))
    qp = QPainter(pm)
    qp.fillRect(QRectF(inset, inset, w-2*inset, h-2*inset), QColor(90, 160, 255, 255))
    qp.fillRect(QRectF(inset, inset, 18, 18), QColor(255, 220, 60, 255))
    qp.end(); return pm

def wire(c, a):
    def on_rot(ang):
        i = c._frame_index; ang = int(ang) % 360
        (a.frame_rotations.__setitem__(i, ang) if ang else a.frame_rotations.pop(i, None))
        a.invalidate_transforms()
    def on_quad(q):
        i = c._frame_index
        (a.frame_quads.__setitem__(i, tuple(q)) if tuple(q) != DEFAULT_QUAD
         else a.frame_quads.pop(i, None))
        a.invalidate_transforms()
    def on_pivot(pv):
        i = c._frame_index
        (a.frame_rotation_pivots.__setitem__(i, tuple(pv)) if tuple(pv) != DEFAULT_ROTATION_PIVOT
         else a.frame_rotation_pivots.pop(i, None))
        a.invalidate_transforms()
    c.transform_rotated.connect(on_rot)
    c.transform_quadded.connect(on_quad)
    c.transform_pivot_moved.connect(on_pivot)

def new_canvas(frames=1, mode="rotate"):
    a = Action("t"); a.frames = [make_pm(120, 90) for _ in range(frames)]; a.padding = 14
    c = PreviewCanvas(); c.resize(440, 440); c.set_action(a); c.set_frame(0)
    c.set_transform_mode(mode, owner="test"); c.set_transform_targets(list(range(frames)))
    wire(c, a); c._apply_fit()
    return a, c

def released_rect(c):
    _, _, positions, _ = c._current_layout()
    tx, ty, fw, fh = positions[c._frame_index]
    return QRectF(tx, ty, fw, fh)

def center(r): return QPointF(r.x()+r.width()/2, r.y()+r.height()/2)

def jump(a, b):
    ca, cb = center(a), center(b)
    return max(abs(ca.x()-cb.x()), abs(ca.y()-cb.y()),
               abs(a.width()-b.width()), abs(a.height()-b.height()))

# ---- 1. 单帧旋转：多角度拖动中 == 松手 ----
print("单帧旋转拖动/松手一致性：")
for target in (30, 45, 90, 137, 210, 300):
    a, c = new_canvas(1, "rotate")
    pv = c._rotation_pivot_canvas()
    h = c._handles_canvas(c._current_mo())
    _, radius, handle = c._rotation_circle(h, pv)
    c._begin_drag("rotate", -1, c._logical_to_screen(handle))
    r = math.radians(target)
    tgt = QPointF(pv.x()+radius*math.sin(r), pv.y()-radius*math.cos(r))
    c._update_drag(c._logical_to_screen(tgt))
    drag = c._dragging_frame_rect(0, 0, 1.0)
    c._end_drag()
    rel = released_rect(c)
    j = jump(drag, rel)
    check(f"旋转~{a.frame_rotations.get(0):3d} 跳变<2px", j < 2.0, f"jump={j:.2f}px")

# ---- 2. 单帧缩放：拖动中 == 松手 ----
print("单帧缩放拖动/松手一致性：")
a, c = new_canvas(1, "scale")
h = c._handles_canvas(c._current_mo())
c._begin_drag("corner", 2, c._logical_to_screen(h[2]))
c._update_drag(c._logical_to_screen(QPointF(h[2].x()+30, h[2].y()+30)))
drag = c._dragging_frame_rect(0, 0, 1.0)
c._end_drag()
rel = released_rect(c)
j = jump(drag, rel)
check("缩放 跳变<2px", j < 2.0,
      f"drag={drag.width():.0f}x{drag.height():.0f} rel={rel.width():.0f}x{rel.height():.0f} jump={j:.2f}px")

# ---- 3. pivot 拖动精确，且手柄框/图像不跟随移动 ----
print("红色锚点拖动：")
a, c = new_canvas(1, "rotate")
before = [(p.x(), p.y()) for p in c._handles_canvas(c._current_mo())]
pv0 = c._rotation_pivot_canvas()
c._begin_drag("pivot", -1, c._logical_to_screen(pv0))
c._update_drag(c._logical_to_screen(pv0 + QPointF(15, 0)))
pv1 = c._rotation_pivot_canvas(c._drag_layout, c._drag_mo)
moved = abs((pv1.x()-pv0.x()) - 15) < 1.0
check("锚点跟手（拖15px）", moved, f"实际={pv1.x()-pv0.x():.2f}px")
# 拖动中手柄框四角必须原地不动
during = [(p.x(), p.y()) for p in c._handles_canvas(c._drag_mo, layout=c._drag_layout)]
maxd = max(math.hypot(d[0]-b[0], d[1]-b[1]) for d, b in zip(during, before))
check("拖锚点时手柄框不移动", maxd < 0.5, f"max_shift={maxd:.2f}px")
c._end_drag()

# ---- 4. 旋转拖动中：手柄框中心 与 帧中心 对齐（锚点不歪）----
print("旋转中手柄框与帧中心对齐：")
for target in (45, 120):
    a, c = new_canvas(1, "rotate")
    pv = c._rotation_pivot_canvas()
    h = c._handles_canvas(c._current_mo())
    _, radius, handle = c._rotation_circle(h, pv)
    c._begin_drag("rotate", -1, c._logical_to_screen(handle))
    r = math.radians(target)
    tgt = QPointF(pv.x()+radius*math.sin(r), pv.y()-radius*math.cos(r))
    c._update_drag(c._logical_to_screen(tgt))
    drag = c._dragging_frame_rect(0, 0, 1.0)
    hh = c._handles_canvas(c._drag_mo, layout=c._drag_layout)
    hc = QPointF(sum(p.x() for p in hh)/4, sum(p.y() for p in hh)/4)
    fc = center(drag)
    err = math.hypot(hc.x()-fc.x(), hc.y()-fc.y())
    check(f"旋转~{a.frame_rotations.get(0):3d} 框心=帧心", err < 2.0, f"err={err:.2f}px")
    c._end_drag()

print(f"\n结果: {PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
