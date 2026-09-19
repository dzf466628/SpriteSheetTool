#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""变换交互回归（像素级 + 真控件端到端）。

为什么这么写：上一版回归只比较代码内部记账（_dragging_frame_rect vs 布局），
两边同源，所以"拖动中"和"松手后"一起错的时候照样全绿，实际拖起来还在跳。
这一版一律**真渲染 + 数像素**：把 PreviewCanvas / ActionPreviewWidget 画到
QImage 上，找内容包围盒和红色锚点十字，用它们的屏幕位置做断言。

覆盖：
  1. 锚点显示精度：红十字符号位置 == 源图旋转中心像素在画面上的位置
  2. 旋转拖动：红点在屏幕上钉死不动（图像绕它转，不再"爬"），松手不跳
  3. 缩放拖动：四个角都拖，拖动中 == 松手后（此前 3/4 的角会瞬移 25px）
  4. 红点在**所有**变换模式下都能拖，且只动红点（图像/手柄框不动）
  5. 整体(多帧)变换：共同中心钉住、松手不跳
  6. 参照帧偏移拖动仍然跟手（视图锚定不能把它抵消）
  7. 真控件端到端：点右侧"旋转"按钮 → 真鼠标事件拖动 → 像素级断言
"""
from __future__ import annotations

import os
import sys
import math
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFontMetrics, QImage, QMouseEvent, QPainter, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from spritesheet.ui.models import Action
from spritesheet.ui.preview import ActionPreviewWidget, PreviewCanvas
from spritesheet.ui.transform import (
    DEFAULT_QUAD, DEFAULT_ROTATION_PIVOT, action_frame_size,
)

app = QApplication([])

PASS = 0
FAIL = 0
W, H = 120, 90


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name} {detail}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


# ---------------- 素材与接线 ----------------

def make_pm(w=W, h=H, inset=0, marks=()):
    """蓝块 + 品红标记；标记**中心精确等于** (mx,my)（4x4 块跨 [mx-2,mx+2)），
    这样它才能当"锚点所在点"的精确探针 —— 5x5 块的中心会偏 0.5px，
    被非等比缩放放大后能差好几个像素，把测试自己带偏。"""
    pm = QPixmap(w, h)
    pm.fill(QColor(0, 0, 0, 0))
    qp = QPainter(pm)
    qp.fillRect(QRectF(inset, inset, w - 2 * inset, h - 2 * inset), QColor(60, 120, 220, 255))
    for mx, my in marks:
        qp.fillRect(QRectF(mx - 2, my - 2, 4, 4), QColor(255, 0, 255, 255))
    qp.end()
    return pm


def wire(c, a):
    """按真实主窗口的接线（数据落回 Action + changed 重排视图）。"""
    def on_rot(ang):
        i = c._frame_index
        ang = int(ang) % 360
        (a.frame_rotations.__setitem__(i, ang) if ang else a.frame_rotations.pop(i, None))
        a.invalidate_transforms()
        on_changed()

    def on_quad(q):
        i = c._frame_index
        (a.frame_quads.__setitem__(i, tuple(q)) if tuple(q) != DEFAULT_QUAD
         else a.frame_quads.pop(i, None))
        a.invalidate_transforms()
        on_changed()

    def on_pivot(pv):
        i = c._frame_index
        (a.frame_rotation_pivots.__setitem__(i, tuple(pv)) if tuple(pv) != DEFAULT_ROTATION_PIVOT
         else a.frame_rotation_pivots.pop(i, None))
        a.invalidate_transforms()
        on_changed()

    def on_changed():
        # ActionPreviewWidget._on_adjust_changed 的关键副作用：_fit 时重排视图
        if c._fit and not c._drag_kind and not c.view_anchored():
            c._apply_fit()

    c.transform_rotated.connect(on_rot)
    c.transform_quadded.connect(on_quad)
    c.transform_pivot_moved.connect(on_pivot)
    c.transform_edit_finished.connect(on_changed)


def new_canvas(frames=1, mode="rotate", pm=None, fit=False):
    a = Action("t")
    a.frames = [pm or make_pm() for _ in range(frames)]
    a.padding = 14
    a.bg_mode = "white"
    c = PreviewCanvas()
    c.resize(460, 460)
    c.set_action(a)
    c.set_frame(0)
    if not fit:
        c._fit = False
        c._zoom = 1.0
        c._offset = QPointF(40, 40)
    c.set_transform_mode(mode, owner="test")
    c.set_transform_targets(list(range(frames)))
    wire(c, a)
    return a, c


# ---------------- 数像素 ----------------

_handles_state = {"hidden": False}
_orig_draw_handles = PreviewCanvas._draw_transform_handles


def _draw_handles_maybe_hidden(self, p):
    if _handles_state["hidden"]:
        return
    return _orig_draw_handles(self, p)


PreviewCanvas._draw_transform_handles = _draw_handles_maybe_hidden


def mask_bbox(img, pred):
    w, h = img.width(), img.height()
    xs, ys, sx, sy = [], [], 0.0, 0.0
    for y in range(h):
        for x in range(w):
            px = img.pixel(x, y)
            r, g, b = (px >> 16) & 255, (px >> 8) & 255, px & 255
            if pred(r, g, b):
                sx += x
                sy += y
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    # cx/cy = 质心（实心大块稳）；bx/by = 包围盒中心（十字这种带半透明描边的控件稳）
    return {"n": len(xs), "x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys),
            "cx": sx / len(xs), "cy": sy / len(ys),
            "bx": (min(xs) + max(xs)) / 2.0, "by": (min(ys) + max(ys)) / 2.0}


is_content = lambda r, g, b: abs(r - 60) < 60 and abs(g - 120) < 60 and b > 150
is_marker = lambda r, g, b: r > 200 and g < 60 and b > 200
# 红点必须只取"实心圆内部"的纯色像素：半透明十字线在不同底色上混出的像素
# 时多时少，会把包围盒中心带偏 1~3px（那是量测噪声，不是锚点在动）。
is_cross = lambda r, g, b: r >= 254 and 18 <= g <= 30 and 62 <= b <= 75


def render(c):
    img = QImage(c.size(), QImage.Format.Format_RGB32)
    img.fill(QColor("#141414"))
    c.render(img)
    return img


def measure(c, hide_handles=False):
    _handles_state["hidden"] = hide_handles
    img = render(c)
    _handles_state["hidden"] = False
    return mask_bbox(img, is_content), mask_bbox(img, is_marker), mask_bbox(img, is_cross)


def fmt(b):
    return "None" if b is None else f"({b['cx']:.1f},{b['cy']:.1f})"


def diff(a, b):
    """两个包围盒的"位置+尺寸"最大偏差（像素）。"""
    if a is None or b is None:
        return 99.0
    return max(abs(a["cx"] - b["cx"]), abs(a["cy"] - b["cy"]),
               abs((a["x1"] - a["x0"]) - (b["x1"] - b["x0"])),
               abs((a["y1"] - a["y0"]) - (b["y1"] - b["y0"])))


def rotate_ref(c):
    """(旋转中心屏幕点, 手柄屏幕半径)：单帧=红点，整体模式=目标帧共同中心。"""
    if c._whole_active():
        fr = c._whole_frame()
        cc = QPointF(fr[4], fr[5])
        pts = c._whole_handles()
    else:
        cc = c._rotation_pivot_canvas()
        pts = c._handles_canvas(c._current_mo())
    r = max(math.hypot(p.x() - cc.x(), p.y() - cc.y()) for p in pts) + 28.0 / c._zoom
    return c._logical_to_screen(cc), r * c._zoom


def rotate_drag_begin(c):
    """按真鼠标来模拟：起点/终点都在**屏幕坐标**里算（角度按屏幕量）。"""
    centre_s, r_s = rotate_ref(c)
    c._begin_drag("rotate", -1, QPointF(centre_s.x(), centre_s.y() - r_s))
    c._rp = (centre_s, r_s)


def rotate_drag_to(c, deg):
    centre_s, r_s = c._rp
    c._update_drag(QPointF(centre_s.x() + r_s * math.sin(math.radians(deg)),
                           centre_s.y() - r_s * math.cos(math.radians(deg))))


# ================= 1. 锚点显示精度 =================
print("1. 红十字符号位置 vs 源图旋转中心像素在画面上的位置：")
for name, (mx, my), pivot in (
    ("中心锚点", (60, 45), (0.5, 0.5)),
    ("偏心锚点(脚底)", (60, 85), (0.5, 85 / H)),
    ("偏心锚点(左手)", (12, 45), (12 / W, 0.5)),
):
    worst = 0.0
    for ang in (0, 30, 67, 155, 250):
        a, c = new_canvas(1, "rotate", pm=make_pm(marks=[(mx, my)]))
        a.frame_rotation_pivots[0] = pivot
        if ang:
            a.frame_rotations[0] = ang
        a.invalidate_transforms()
        _, marker, _ = measure(c, hide_handles=True)   # 十字会盖住标记，先只画帧
        _, _, cross = measure(c)                       # 再单独量十字真正画在哪
        if marker is None or cross is None:
            worst = 99.0
            break
        worst = max(worst, math.hypot(marker["bx"] - cross["bx"], marker["by"] - cross["by"]))
    check(f"{name} 全角度偏差<1.5px", worst < 1.5, f"worst={worst:.2f}px")


# ================= 2. 旋转拖动：目标钉住、松手不跳 =================
print("2. 旋转拖动：红点钉在屏幕上、图像绕它转、松手不跳：")
for frames in (1, 3):
    a, c = new_canvas(frames, "rotate")
    rotate_drag_begin(c)
    seen = []
    for deg in (0, 12, 25, 45, 70, 90):
        if deg:
            rotate_drag_to(c, deg)
        m = measure(c)
        # 单帧有红点十字可量；整体模式没有红点，改量内容包围盒中心
        seen.append((m[0], m[2] if m[2] is not None else m[0]))
    during = measure(c)
    c._end_drag()
    after = measure(c)
    pivot_drift = max(max(abs(x[1]["bx"] - seen[0][1]["bx"]), abs(x[1]["by"] - seen[0][1]["by"]))
                      for x in seen)
    check(f"{frames}帧 拖动中不动点不移动(<1.5px)", pivot_drift < 1.5, f"drift={pivot_drift:.2f}px")
    check(f"{frames}帧 松手不跳(<1.5px)", diff(during[0], after[0]) < 1.5,
          f"{fmt(during[0])} -> {fmt(after[0])}")
    check(f"{frames}帧 旋转确实生效(包围盒变了)",
          abs((seen[0][0]["x1"] - seen[0][0]["x0"]) - (seen[-1][0]["x1"] - seen[-1][0]["x0"])) > 10,
          f"w {seen[0][0]['x1']-seen[0][0]['x0']} -> {seen[-1][0]['x1']-seen[-1][0]['x0']}")


# ================= 3. 缩放拖动：四个角都不能跳 =================
print("3. 缩放拖动：四个角分别拖，拖动中 == 松手后：")
for k in range(4):
    a, c = new_canvas(1, "scale")
    h = c._handles_canvas(c._current_mo())
    c._begin_drag("corner", k, c._logical_to_screen(h[k]))
    c._update_drag(c._logical_to_screen(QPointF(h[k].x() + 25, h[k].y() + 25)))
    during = measure(c)[0]
    c._end_drag()
    after = measure(c)[0]
    check(f"corner{k} 松手不跳(<1.5px)", diff(during, after) < 1.5,
          f"{fmt(during)} -> {fmt(after)}")


# ================= 4. 红点在各模式下可拖且只动红点 =================
print("4. 红点（旋转中心）在所有变换模式下都能拖、且只动红点：")
for mode in ("rotate", "scale", "skew", "perspective", "distort"):
    a, c = new_canvas(1, mode)
    if mode == "rotate":
        a.frame_rotations[0] = 25       # 有角度时 pivot 才真的影响画面
        a.invalidate_transforms()
    before_img = measure(c)[0]
    before_box = [(p.x(), p.y()) for p in c._handles_canvas(c._current_mo())]
    s0 = c._logical_to_screen(c._rotation_pivot_canvas())
    kind, _ = c._hit_test(s0)
    check(f"{mode:12s} 点得到红点", kind == "pivot", f"kind={kind}")
    c._begin_drag("pivot", -1, s0)
    c._update_drag(s0 + QPointF(22, 10))
    moved = measure(c)[2]
    c._end_drag()
    after_img = measure(c)[0]
    after_box = [(p.x(), p.y()) for p in c._handles_canvas(c._current_mo())]
    follow = math.hypot(moved["bx"] - s0.x() - 22, moved["by"] - s0.y() - 10) if moved else 99
    img_shift = math.hypot(after_img["cx"] - before_img["cx"], after_img["cy"] - before_img["cy"])
    box_shift = max(math.hypot(x - u, y - v) for (x, y), (u, v) in zip(after_box, before_box))
    check(f"{mode:12s} 红点跟手(<2px)", follow < 2.0, f"err={follow:.2f}px")
    check(f"{mode:12s} 只动红点：图像/框不动(<2px)", img_shift < 2.0 and box_shift < 2.0,
          f"img={img_shift:.2f} box={box_shift:.2f}")


# ================= 5. 参照帧偏移拖动仍然跟手 =================
print("5. 参照帧偏移拖动：帧跟着手走（视图锚定不能抵消）：")
a, c = new_canvas(1, "rotate")
a.ref_frame = 0
a.frame_rotations[0] = 40
a.invalidate_transforms()
c.set_transform_mode(None)
before = measure(c)[0]
c.begin_layout_freeze()
c._frame_drag_active = True
c._frame_drag_last = QPointF(100, 100)


class _Ev:
    def position(self):
        return QPointF(140, 130)


c.mouseMoveEvent(_Ev())
c.end_layout_freeze()
after = measure(c)[0]
check("偏移拖动 40px 生效", abs(after["cx"] - before["cx"] - 40) < 2.0,
      f"dx={after['cx'] - before['cx']:.2f}")


# ================= 6. 真控件端到端（按钮 + 真鼠标事件） =================
print("6. 真控件端到端：点「旋转」按钮 → 真鼠标拖动 → 松手：")
wd = ActionPreviewWidget()
wd.resize(1100, 900)
act = Action("e2e")
act.frames = [make_pm(marks=[(60, 45)])]
act.padding = 14
act.bg_mode = "white"
wd.set_actions([act])
wd.set_action(act)
canvas = wd.canvas
canvas.setMinimumHeight(560)
canvas.setMaximumHeight(560)
canvas.setMinimumWidth(640)
app.processEvents()
canvas._apply_fit()            # 画布拿到真实尺寸后重新自适应（真窗口里由 resizeEvent 触发）
wd.adjust_panel.btn_mode_rotate.setChecked(True)
check("按钮进入旋转模式", canvas.transform_mode() == "rotate", f"mode={canvas.transform_mode()}")

centre_s, r_s = rotate_ref(canvas)


def send(kind, pos, buttons=Qt.MouseButton.LeftButton):
    ev = QMouseEvent(kind, pos, canvas.mapToGlobal(pos.toPoint()).toPointF(),
                     Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(canvas, ev)


send(QEvent.Type.MouseButtonPress, QPointF(centre_s.x(), centre_s.y() - r_s))
check("真鼠标按在旋转手柄上进入拖动", canvas._drag_kind == "rotate", f"kind={canvas._drag_kind}")
shots = []
expected = []
for deg in (0, 20, 45, 75):
    tgt = QPointF(centre_s.x() + r_s * math.sin(math.radians(deg)),
                  centre_s.y() - r_s * math.cos(math.radians(deg)))
    send(QEvent.Type.MouseMove, tgt)
    shots.append(measure(canvas))
    # 红点的量测说明：高缩放下红点是固定 10px、且带半透明十字线，边缘像素会跟
    # 底下旋转中的图像混色，像素质心会飘 1~3px（是量测噪声）。所以这里用更硬的
    # 判据：① 内容不许动（用户看到的"目标不动"）；② 红点该在的位置必须恒定。
    expected.append(canvas._logical_to_screen(canvas._rotation_pivot_canvas()))
during = shots[-1]
content_drift = max(max(abs(s[0]["cx"] - shots[0][0]["cx"]), abs(s[0]["cy"] - shots[0][0]["cy"]))
                    for s in shots)
exp_drift = max(max(abs(p.x() - expected[0].x()), abs(p.y() - expected[0].y()))
                for p in expected)
send(QEvent.Type.MouseButtonRelease, tgt, Qt.MouseButton.NoButton)
after = measure(canvas)
check("端到端：拖动中图像不动(<1.5px)", content_drift < 1.5, f"drift={content_drift:.2f}px")
check("端到端：红点位置恒定(<0.6px)", exp_drift < 0.6, f"drift={exp_drift:.2f}px")
check("端到端：松手不跳(<2px)", diff(during[0], after[0]) < 2.0,
      f"{fmt(during[0])} -> {fmt(after[0])}")
check("端到端：图像确实转了", abs((during[0]["x1"] - during[0]["x0"])
                              - (shots[0][0]["x1"] - shots[0][0]["x0"])) > 5,
      f"w {shots[0][0]['x1']-shots[0][0]['x0']} -> {during[0]['x1']-during[0]['x0']}")
check("端到端：旋转角写回数据", int(act.frame_rotations.get(0, 0)) > 0,
      f"angle={act.frame_rotations.get(0)}")

# 端到端：缩放四角
wd.adjust_panel.btn_mode_scale.setChecked(True)
check("按钮切到缩放模式", canvas.transform_mode() == "scale", f"mode={canvas.transform_mode()}")
for k in range(4):
    ht = canvas._handles_canvas(canvas._current_mo())
    start = canvas._logical_to_screen(ht[k])
    send(QEvent.Type.MouseButtonPress, start)
    send(QEvent.Type.MouseMove, start + QPointF(20, 20))
    mid = measure(canvas)
    send(QEvent.Type.MouseButtonRelease, start + QPointF(20, 20), Qt.MouseButton.NoButton)
    rel = measure(canvas)
    check(f"端到端 corner{k} 松手不跳(<2px)", diff(mid[0], rel[0]) < 2.0,
          f"{fmt(mid[0])} -> {fmt(rel[0])}")

# ================= 7. 红点在所有模式都看得见（含整体 GIF 级） =================
print("7. 红色旋转中心：单帧/整体、各变换模式都必须画出来：")
for frames in (1, 3):
    for mode in ("rotate", "scale", "perspective"):
        a, c = new_canvas(frames, mode)
        _, _, cross = measure(c)
        exp = c._logical_to_screen(c._rotation_pivot_canvas())
        ok = cross is not None and math.hypot(cross["bx"] - exp.x(), cross["by"] - exp.y()) < 2.0
        check(f"{frames}帧 {mode:11s} 红点可见", ok,
              f"画在={fmt(cross)} 应为=({exp.x():.1f},{exp.y():.1f})")


# ================= 8. 拖出窗口很远：不再卡死 =================
print("8. 变换鼠标划出窗口很远：帧尺寸与耗时必须被夹住：")
for frames in (1, 18):
    a, c = new_canvas(frames, "scale")
    ht = c._whole_handles() if frames > 1 else c._handles_canvas(c._current_mo())
    c._begin_drag("corner", 2, c._logical_to_screen(ht[2]))
    c._update_drag(c._logical_to_screen(QPointF(ht[2].x() + 10000, ht[2].y() + 10000)))
    w0, h0 = action_frame_size(a, 0)
    t0 = time.perf_counter()
    render(c)
    dt = time.perf_counter() - t0
    check(f"{frames}帧 拖到 10000px 外：单帧 {w0}x{h0} ≤32MP", w0 * h0 <= 33_000_000,
          f"{w0 * h0 / 1e6:.1f}MP")
    check(f"{frames}帧 拖到 10000px 外：一帧绘制 {dt * 1000:.0f}ms 不卡死", dt < 1.5,
          f"{dt * 1000:.0f}ms")


# ================= 9. 整体模式拖红点：端到端 =================
print("9. 整体(GIF 级)拖红点：真鼠标事件 → 各帧 pivot 一致、红点跟手：")
wd = ActionPreviewWidget()
wd.resize(1000, 800)
whole = Action("whole")
whole.frames = [make_pm() for _ in range(3)]
whole.padding = 14
whole.bg_mode = "white"
wd.set_actions([whole])
wd.set_action(whole)
canvas2 = wd.canvas
canvas2.setMinimumWidth(640)
canvas2.setMinimumHeight(520)
canvas2.setMaximumHeight(520)
app.processEvents()
canvas2._apply_fit()
wd.adjust_panel.enter_transform_mode_all("rotate")
check("进入整体旋转模式", canvas2.transform_mode() == "rotate" and canvas2._whole_active(),
      f"mode={canvas2.transform_mode()} whole={canvas2._whole_active()}")
p0 = canvas2._logical_to_screen(canvas2._rotation_pivot_canvas())


def send2(kind, pos, buttons=Qt.MouseButton.LeftButton):
    ev = QMouseEvent(kind, pos, canvas2.mapToGlobal(pos.toPoint()).toPointF(),
                     Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(canvas2, ev)


send2(QEvent.Type.MouseButtonPress, p0)
check("按下红点进入 pivot 拖动（不是整体平移）", canvas2._drag_kind == "pivot",
      f"kind={canvas2._drag_kind}")
target = p0 + QPointF(0, 70)
send2(QEvent.Type.MouseMove, target)
_, _, cross2 = measure(canvas2)
send2(QEvent.Type.MouseButtonRelease, target, Qt.MouseButton.NoButton)
pivs = [whole.frame_rotation_pivots.get(i) for i in range(3)]
check("整体拖红点：三帧都写入了 pivot", all(p is not None for p in pivs), f"{pivs}")
check("整体拖红点：三帧 pivot 一致（同一视觉点）",
      len({tuple(round(v, 3) for v in p) for p in pivs if p}) == 1, f"{pivs}")
check("整体拖红点：红点跟到鼠标附近(<4px)",
      cross2 is not None and math.hypot(cross2["bx"] - target.x(), cross2["by"] - target.y()) < 4.0,
      f"红点={fmt(cross2)} 鼠标=({target.x():.1f},{target.y():.1f})")
check("整体拖红点：红点确实离开了内容中心", pivs and pivs[0][1] > 0.55, f"{pivs[0] if pivs else None}")


# ================= 10. 面板读数与动画设置行距 =================
print("10. 「当前变换」角度位数不抖 + 速度滑块紧贴数值：")
lbl = wd.adjust_panel.lbl_transform_summary
widths = set()
for ang in (5, 45, 180, 359):
    whole.frame_rotations[0] = ang
    wd.adjust_panel._sync_transform_widgets()
    widths.add(lbl.sizeHint().width())
check("5°~359° 标签宽度恒定", len(widths) == 1, f"宽度={sorted(widths)}")

from spritesheet.ui.anim_settings import AnimSettingsBar
bar = AnimSettingsBar()
bar.resize(900, 200)
bar.show()
bar.set_action(Action("t"))
app.processEvents()
sg, vg = bar.slider_speed.geometry(), bar.lbl_speed_value.geometry()
gap = vg.x() - (sg.x() + sg.width())
check("速度滑块右缘与数值间距 ≤6px", gap <= 6, f"gap={gap}px")
# 数值必须左对齐（数字紧贴滑块）且放得下 3 位数：原来写死 20px + 右对齐，
# 连 "10"(22px) 都会被截掉一块，还会在滑块与数字之间留出空隙。
fm = QFontMetrics(bar.lbl_speed_value.font())
check("速度数值左对齐（紧贴滑块）",
      bool(bar.lbl_speed_value.alignment() & Qt.AlignmentFlag.AlignLeft),
      f"alignment={int(bar.lbl_speed_value.alignment())}")
widths, clipped = set(), False
for v in ("1", "10", "100"):
    bar.lbl_speed_value.setText(v)
    app.processEvents()
    widths.add(bar.lbl_speed_value.width())
    if fm.horizontalAdvance(v) > bar.lbl_speed_value.width():
        clipped = True
check("速度数值 1/10/100 都不被截断", not clipped,
      f"标签宽={sorted(widths)} 需要宽={fm.horizontalAdvance('100')}")
check("速度数值标签宽度不随位数变化（滑块不忽长忽短）", len(widths) == 1, f"宽度={sorted(widths)}")
check("地面线偏移数值同样左对齐",
      bool(bar.lbl_ground_offset.alignment() & Qt.AlignmentFlag.AlignLeft) and
      bar.lbl_ground_offset.maximumWidth() > 25,
      f"alignment={int(bar.lbl_ground_offset.alignment())} max={bar.lbl_ground_offset.maximumWidth()}")

# ================= 11. 变换必须真的绕红色锚点 =================
# 做法：在源图"锚点像素"处画一个品红标记；变换前后这个标记的屏幕位置必须
# 基本不动（它是旋转/缩放的不动点），否则就是"没按锚点来"。
print("11. 旋转/缩放必须以红色锚点为不动点（标记点不动）：")


def anchor_case(nframes, mode, act):
    """返回 (标记点位移, 红点位移)。act(c, a) 里执行一次真实拖动。"""
    wd = ActionPreviewWidget()
    wd.resize(1000, 800)
    a = Action("anchor")
    a.frames = [make_pm(marks=[(60, 78)]) for _ in range(nframes)]
    a.padding = 14
    a.bg_mode = "white"
    for i in range(nframes):
        a.frame_rotation_pivots[i] = (60 / W, 78 / H)      # 锚点在脚底
    wd.set_actions([a])
    wd.set_action(a)
    c = wd.canvas
    c.setMinimumWidth(640)
    c.setMinimumHeight(520)
    c.setMaximumHeight(520)
    app.processEvents()
    c._apply_fit()
    wire(c, a)
    if nframes > 1:
        wd.adjust_panel.enter_transform_mode_all(mode)
    else:
        wd.adjust_panel._on_mode_button(mode, True)
    assert c.transform_mode() == mode, f"没进变换模式: {c.transform_mode()}"
    m0, x0 = measure(c)[1], measure(c)[2]
    act(c, a)
    m1, x1 = measure(c)[1], measure(c)[2]
    if m0 is None or m1 is None or x0 is None or x1 is None:
        return None, None
    return (math.hypot(m1["bx"] - m0["bx"], m1["by"] - m0["by"]),
            math.hypot(x1["bx"] - x0["bx"], x1["by"] - x0["by"]))


def drag_rotate_at(c, deg):
    cs, rs = rotate_ref(c)
    c._begin_drag("rotate", -1, QPointF(cs.x(), cs.y() - rs))
    c._update_drag(QPointF(cs.x() + rs * math.sin(math.radians(deg)),
                           cs.y() - rs * math.cos(math.radians(deg))))


def drag_corner_at(c, k, dx, dy):
    ht = c._whole_handles() if c._whole_active() else c._handles_canvas(c._current_mo())
    s = c._logical_to_screen(ht[k])
    c._begin_drag("corner", k, s)
    c._update_drag(s + QPointF(dx * c._zoom, dy * c._zoom))


for frames, tag in ((1, "单帧"), (3, "整体3帧")):
    md, xd = anchor_case(frames, "rotate", lambda c, a: drag_rotate_at(c, 45))
    check(f"{tag} 旋转 45°：锚点处的内容不动(<2px)", md is not None and md < 2.0,
          f"标记位移={None if md is None else round(md, 2)}px")
    md, xd = anchor_case(frames, "scale", lambda c, a: drag_corner_at(c, 2, 40, 30))
    check(f"{tag} 缩放拖角：锚点处的内容不动(<2px)", md is not None and md < 2.0,
          f"标记位移={None if md is None else round(md, 2)}px")

# Alt = 老行为（对角固定）：锚点会跟着动，这里只确认这条路还在、可用
def _alt_case(c, a):
    ht = c._handles_canvas(c._current_mo())
    s = c._logical_to_screen(ht[2])
    c._scale_uses_pivot = lambda kind: False     # 等价于按住 Alt（无头环境读不到修饰键）
    c._begin_drag("corner", 2, s)
    c._update_drag(s + QPointF(40 * c._zoom, 30 * c._zoom))


md_alt, _ = anchor_case(1, "scale", _alt_case)
check("Alt(对角固定)路径仍可用（锚点会动）", md_alt is not None and md_alt > 3.0,
      f"标记位移={None if md_alt is None else round(md_alt, 2)}px（预期明显移动）")

# ================= 12. 动作编辑(全选所有帧)：帧不许相对红点漂 =================
# 做法：每帧都在"锚点像素"处画一颗品红标记。整体变换时所有帧的锚点必须重合，
# 于是标记会叠成一小块、正好压在红十字上；只要有帧摆错，这一块就会偏离/拉长。
# （老代码：拖动中用未裁剪图、落位用裁剪口径，实测拖动中偏出 12~14px、松手又回 0。）
print("12. 动作编辑(全选所有帧)拖动：每帧锚点必须压在红点上：")


def whole_frames_with_marker(n=6, anchor_px=(60, 40)):
    def mk():
        pm = QPixmap(W, H)
        pm.fill(QColor(0, 0, 0, 0))
        q = QPainter(pm)
        q.fillRect(QRectF(0, 0, W, H), QColor(128, 128, 128, 255))
        q.fillRect(QRectF(anchor_px[0] - 2, anchor_px[1] - 2, 4, 4), QColor(255, 0, 255, 255))
        q.end()
        return pm

    wd = ActionPreviewWidget()
    wd.resize(1000, 800)
    a = Action("whole")
    a.frames = [mk() for _ in range(n)]
    a.padding = 14
    a.bg_mode = "white"
    for i in range(n):
        a.frame_rotation_pivots[i] = (anchor_px[0] / W, anchor_px[1] / H)
    wd.set_actions([a])
    wd.set_action(a)
    c = wd.canvas
    c.setMinimumWidth(640)
    c.setMinimumHeight(520)
    c.setMaximumHeight(520)
    app.processEvents()
    c._apply_fit()
    wd.adjust_panel.enter_transform_mode_all("rotate")
    return wd, a, c


def find_blob(c, pred, window=None):
    img = render(c)
    xs, ys = [], []
    x0, y0, x1, y1 = 0, 0, img.width(), img.height()
    if window:
        cx, cy, rr = window
        x0, y0, x1, y1 = int(cx - rr), int(cy - rr), int(cx + rr), int(cy + rr)
    for y in range(max(0, y0), min(img.height(), y1)):
        for x in range(max(0, x0), min(img.width(), x1)):
            p = img.pixel(x, y)
            r, g, b = (p >> 16) & 255, (p >> 8) & 255, p & 255
            if pred(r, g, b):
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0,
            max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


# 品红标记（主帧纯色、其余 0.4 混色都算）；红十字是 (255,23,68)，b 很低不会混进来
blob_marker = lambda r, g, b: r > 150 and g < 110 and b > 150 and abs(r - b) < 40


def anchor_err(c):
    cross = find_blob(c, is_cross)
    if not cross:
        return None
    mk = find_blob(c, blob_marker, window=(cross[0], cross[1], 70))
    if not mk:
        return None
    return math.hypot(mk[0] - cross[0], mk[1] - cross[1])


wd_w, act_w, cw = whole_frames_with_marker()
cs_w, rs_w = rotate_ref(cw)
cw._begin_drag("rotate", -1, QPointF(cs_w.x(), cs_w.y() - rs_w))
errs = []
for deg in (0, 15, 30, 45, 60):
    cw._update_drag(QPointF(cs_w.x() + rs_w * math.sin(math.radians(deg)),
                            cs_w.y() - rs_w * math.cos(math.radians(deg))))
    errs.append(anchor_err(cw))
during_err = anchor_err(cw)
cw._end_drag()
after_err = anchor_err(cw)
worst = max([e for e in errs if e is not None] or [99])
check("整体拖动中：各帧锚点都压在红点上(<1.5px)", worst < 1.5, f"worst={worst:.2f}px")
check("整体拖动中 ↔ 松手后一致(<1.5px)",
      during_err is not None and after_err is not None and abs(during_err - after_err) < 1.5,
      f"拖动中={None if during_err is None else round(during_err, 2)} "
      f"松手后={None if after_err is None else round(after_err, 2)}")

# ================= 13. 播放与变换互斥 =================
print("13. 播放不再拆掉正在进行的变换：")


def play_case():
    wd = ActionPreviewWidget()
    wd.resize(1000, 800)
    a = Action("play")
    a.frames = [make_pm() for _ in range(4)]
    a.padding = 14
    a.bg_mode = "white"
    wd.set_actions([a])
    wd.set_action(a)
    c = wd.canvas
    c.setMinimumWidth(640)
    c.setMinimumHeight(520)
    c.setMaximumHeight(520)
    app.processEvents()
    c._apply_fit()
    return wd, a, c


wd_p, act_p, cp = play_case()
wd_p.toggle_play()
cp.set_transform_mode("rotate", owner="test")
check("播放中进变换模式会自动暂停播放",
      cp.transform_mode() == "rotate" and not wd_p._playing,
      f"mode={cp.transform_mode()} playing={wd_p._playing}")
cs_p, rs_p = rotate_ref(cp)
cp._begin_drag("rotate", -1, QPointF(cs_p.x(), cs_p.y() - rs_p))
angle_p = None
for k in range(3):
    cp._update_drag(QPointF(cs_p.x() + 20 * (k + 1), cs_p.y() - rs_p))
    wd_p._playing = True          # 万一又播起来
    wd_p._tick()
    angle_p = act_p.frame_rotations.get(cp._frame_index)
check("播放 tick 不再打断拖动（模式/拖动/角度都在）",
      cp.transform_mode() == "rotate" and cp._drag_kind == "rotate" and bool(angle_p),
      f"mode={cp.transform_mode()} drag={cp._drag_kind} 角度={angle_p}")
check("播放 tick 会暂停播放", not wd_p._playing, f"playing={wd_p._playing}")

# ================= 14. 点框内不退出，点框外空白才退出 =================
# 用户实测：旋转模式下点四个空心角点会把手柄弹起（以前没点到可拖控件就直接退出）。
print("14. 点框内/点手柄不退出变换，点框外空白才退出：")
a, c = new_canvas(1, "rotate")
ht = c._handles_canvas(c._current_mo())
QTest.mouseClick(c, Qt.MouseButton.LeftButton,
                 pos=c._logical_to_screen(ht[0]).toPoint())
check("旋转模式点角点手柄不退出", c.transform_mode() == "rotate",
      f"实际: {c.transform_mode()!r}")
mid = (ht[0] + ht[2]) / 2.0
QTest.mouseClick(c, Qt.MouseButton.LeftButton,
                 pos=c._logical_to_screen(mid + QPointF(30, 0)).toPoint())
check("点框内空白不退出", c.transform_mode() == "rotate",
      f"实际: {c.transform_mode()!r}")
QTest.mouseClick(c, Qt.MouseButton.LeftButton, pos=QPoint(3, 3))
check("点框外空白退出", c.transform_mode() is None,
      f"实际: {c.transform_mode()!r}")

print(f"\n结果: {PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
