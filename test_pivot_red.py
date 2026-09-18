#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证：pivot 红色锚点在非 rotate 模式下也可命中/拖动。"""
from __future__ import annotations
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap, QColor, QPainter
from PyQt6.QtCore import QPointF, QRectF

from spritesheet.ui.models import Action
from spritesheet.ui.preview import PreviewCanvas

app = QApplication([])

def make_pm(w, h):
    pm = QPixmap(w, h); pm.fill(QColor(0,0,0,0))
    qp = QPainter(pm); qp.fillRect(QRectF(2,2,w-4,h-4), QColor(255,80,80,255)); qp.end()
    return pm

a = Action("t"); a.frames = [make_pm(100,80), make_pm(120,90)]; a.padding = 10
c = PreviewCanvas(); c.set_action(a); c.set_frame(0)
c._apply_fit()

ok = True
for mode in ("rotate", "scale", "skew", "perspective", "distort"):
    c.set_transform_mode(mode, owner="test"); c.set_transform_targets([0])
    c._apply_fit()
    pivot = c._rotation_pivot_canvas()
    ps = c._logical_to_screen(pivot)
    kind, idx = c._hit_test(ps)
    status = "OK" if kind == "pivot" else "FAIL"
    if kind != "pivot": ok = False
    print(f"  [{status}] mode={mode:12s} hit_test at pivot -> kind={kind}")

# 整体(GIF 级)模式的 pivot 同样可命中、可拖（用户要求：红点在任何变换模式都能自由移动）
c.set_transform_mode("scale", owner="test"); c.set_transform_targets([0,1])
c._apply_fit()
pivot = c._rotation_pivot_canvas(); ps = c._logical_to_screen(pivot)
kind, idx = c._hit_test(ps)
status = "OK" if kind == "pivot" else "FAIL"
if kind != "pivot": ok = False
print(f"  [{status}] 整体模式 pivot 应可命中（可自由移动） -> kind={kind}")

# 整体模式：红点外侧 30px 必须还给"框内拖动=整体平移"，不能被红点吃掉
far = ps + QPointF(0, 30)
kind_far, _ = c._hit_test(far)
status = "OK" if kind_far != "pivot" else "FAIL"
if kind_far == "pivot": ok = False
print(f"  [{status}] 整体模式 红点外侧仍可整体平移 -> kind={kind_far}")

# pivot 位置必须在手柄中心（无变换时）
c.set_transform_mode("scale", owner="test"); c.set_transform_targets([0])
c._apply_fit()
handles = c._handles_canvas(c._current_mo())
gx = sum(p.x() for p in handles)/4; gy = sum(p.y() for p in handles)/4
p = c._rotation_pivot_canvas()
err = max(abs(p.x()-gx), abs(p.y()-gy))
print(f"  [{'OK' if err<0.5 else 'FAIL'}] 单帧无变换 pivot 距手柄中心 = {err:.2f}px")
if err >= 0.5: ok = False

print("ALL OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
