#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动作编辑(整体变换) 的坐标口径回归：必须**逐像素等于播放/帧编辑**。

背景（用户两次报的是同一个问题）：
  · "动作编辑的时候 是不是用的原图的坐标 导致坐标不统一 有上有下"
  · "调整变化的时候 其他帧的位置会有变化又回去的闪"
原理：动作编辑 == 单帧模式全选所有帧。所以
  ① 每一帧在整体模式里画的位置 == 把那一帧当当前帧(播放/帧编辑)时的位置（含 frame_offsets）；
  ② 拖动中看到的东西 == 松手后看到的东西（否则就是"位置变了又回去"的闪）。

写法：真控件 + 真鼠标事件 + 真渲染。每帧一个**唯一颜色横条**当探针，四帧横条在 y 上
互不重叠 → 既能被单独量到、也不会被别的帧盖住。
"""
from __future__ import annotations

import os
import sys
import math

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

app = QApplication([])

from spritesheet.ui.models import Action  # noqa: E402
from spritesheet.ui.preview import ActionPreviewWidget, PreviewCanvas  # noqa: E402
from spritesheet.ui.transform import (  # noqa: E402
    DEFAULT_QUAD, DEFAULT_ROTATION_PIVOT,
)

PASS = 0
FAIL = 0
W, H = 120, 100
N = 4
BAR = [(0, 255, 0), (0, 128, 255), (255, 255, 0), (255, 0, 255)]
BAR_Y = [30, 50, 70, 90]
BAR_W = [100, 92, 84, 76]
OFFSETS = [(0, 0), (0, 20), (0, 40), (0, 60)]


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name} {detail}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def frame_pix(i):
    pm = QPixmap(W, H)
    pm.fill(QColor(0, 0, 0, 0))
    q = QPainter(pm)
    q.fillRect(10, BAR_Y[i], BAR_W[i], 12, QColor(*BAR[i], 255))
    q.end()
    return pm


def build():
    wd = ActionPreviewWidget()
    wd.resize(1100, 820)
    a = Action("t")
    a.frames = [frame_pix(i) for i in range(N)]
    a.padding = 14
    a.bg_mode = "black"          # 半透明帧(0.4)混合底色可预测
    a.frame_opacity = 1.0
    a.frame_offsets = {i: OFFSETS[i] for i in range(N)}
    wd.set_actions([a])
    wd.set_action(a)
    c = wd.canvas
    c.setMinimumWidth(760)
    c.setMinimumHeight(560)
    c.setMaximumHeight(560)
    app.processEvents()
    c._fit = False
    c._zoom = 1.0
    c._offset = QPointF(60, 60)
    c._anchor_spec = None
    c._view_shift = QPointF(0, 0)
    return wd, a, c


# ---------------- 渲染与测量 ----------------

_handles_state = {"hidden": False}
_orig_draw_handles = PreviewCanvas._draw_transform_handles


def _draw_handles_maybe_hidden(self, p):
    if _handles_state["hidden"]:
        return
    return _orig_draw_handles(self, p)


PreviewCanvas._draw_transform_handles = _draw_handles_maybe_hidden


def render(c):
    img = QImage(c.size(), QImage.Format.Format_RGB32)
    img.fill(QColor("#141414"))
    c.render(img)
    return img


def pump(c):
    """画两遍：第一遍让 _sync_view_shift 生效，第二遍才是稳定状态。"""
    render(c)
    return render(c)


def img_diff(a, b):
    """两幅图的差异像素比例。"""
    w, h = a.width(), a.height()
    n = 0
    for y in range(h):
        for x in range(w):
            if a.pixel(x, y) != b.pixel(x, y):
                n += 1
    return n / float(w * h)


def bar_palette():
    pal = {}
    for i in range(N):
        pal[("full", i)] = BAR[i]
        pal[("dim", i)] = tuple(int(round(v * 0.4)) for v in BAR[i])
    return pal


PAL = bar_palette()
INV = {v: k for k, v in PAL.items()}


def bars_screen(img, tol=30.0):
    """各帧横条质心（屏幕坐标）：主帧满不透明色，其余 0.4 混合色。"""
    acc = {col: [0.0, 0.0, 0] for col in PAL.values()}
    for y in range(img.height()):
        for x in range(img.width()):
            px = img.pixel(x, y)
            r, g, b = (px >> 16) & 255, (px >> 8) & 255, px & 255
            best, bd = None, tol
            for col in PAL.values():
                d = math.dist((r, g, b), col)
                if d < bd:
                    best, bd = col, d
            if best is not None:
                acc[best][0] += x
                acc[best][1] += y
                acc[best][2] += 1
    out = {}
    for col, (sx, sy, n) in acc.items():
        if n:
            _kind, i = INV[col]
            if i in out and out[i][2] >= n:
                continue
            out[i] = (sx / n, sy / n, n)
    return out


def to_logical(c, pt):
    return ((pt[0] - c._offset.x() - c._view_shift.x()) / c._zoom,
            (pt[1] - c._offset.y() - c._view_shift.y()) / c._zoom)


is_cross = lambda r, g, b: r >= 254 and 18 <= g <= 30 and 62 <= b <= 75


def cross_screen(c):
    img = render(c)
    xs, ys = [], []
    for y in range(img.height()):
        for x in range(img.width()):
            px = img.pixel(x, y)
            if is_cross((px >> 16) & 255, (px >> 8) & 255, px & 255):
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0


def reset_view(c):
    c._fit = False
    c._zoom = 1.0
    c._offset = QPointF(60, 60)
    c._anchor_spec = None
    c._view_shift = QPointF(0, 0)


def enter_all(wd, c, mode):
    """进入动作编辑(整体)变换。

    必须先显式退出：`set_transform_mode` 里"再点同一个模式 = 退出"，
    否则第二次 enter 会把模式关掉，测试就变成"什么都没拖"的空跑（踩过）。
    """
    c.set_transform_mode(None)
    wd.adjust_panel.enter_transform_mode_all(mode)


def clear_transforms(a):
    a.frame_quads.clear()
    a.frame_rotations.clear()
    a.frame_rotation_pivots.clear()
    a.invalidate_transforms()


# ================= 1. 整体模式落位 == 播放/单帧落位 =================
print("1. 动作编辑(整体变换)各帧落位 vs 播放(单帧)落位：")
wd, a, c = build()
single = {}
for i in range(N):
    wd.adjust_panel._exit_transform_mode()
    c.set_transform_targets([])
    c.set_frame(i)
    app.processEvents()
    reset_view(c)
    _handles_state["hidden"] = True
    img = render(c)
    _handles_state["hidden"] = False
    single[i] = {k: to_logical(c, v) for k, v in bars_screen(img).items()}

c.set_frame(0)
enter_all(wd, c, "scale")
app.processEvents()
reset_view(c)
_handles_state["hidden"] = True
whole_img = render(c)
_handles_state["hidden"] = False
whole = {k: to_logical(c, v) for k, v in bars_screen(whole_img).items()}

print(f"  {'帧':<4}{'播放(单帧)y':>13}{'整体模式y':>12}{'差':>7}"
      f"{'播放x':>10}{'整体x':>10}{'差':>7}")
worst = 0.0
for i in range(N):
    s = single[i].get(i)
    w_ = whole.get(i)
    if s is None or w_ is None:
        check(f"帧{i} 两种口径都能量到", False, f"single={s} whole={w_}")
        worst = 99.0
        continue
    dy = w_[1] - s[1]
    dx = w_[0] - s[0]
    worst = max(worst, abs(dx), abs(dy))
    print(f"  {i:<4}{s[1]:>13.2f}{w_[1]:>12.2f}{dy:>7.2f}{s[0]:>10.2f}{w_[0]:>10.2f}{dx:>7.2f}")
check("四帧落位与播放一致（<=1.0px）", worst <= 1.0, f"worst={worst:.2f}px")

ys = [whole[i][1] for i in range(N) if i in whole]
gaps = [ys[k + 1] - ys[k] for k in range(len(ys) - 1)]
# 相邻帧间距 = frame_offsets 的 20px + 原图内横条本身的 20px（BAR_Y 逐帧 +20）
check("保留 frame_offsets 的纵向间距(20+20=40px)",
      gaps and max(abs(g - 40.0) for g in gaps) <= 1.0,
      f"gaps={[round(g, 2) for g in gaps]}")

# ================= 2. 红点位置：整体模式 == 单帧模式 =================
print("\n2. 整体模式下红点的位置 == 单帧模式同一帧红点的位置：")
for pivot in ((0.5, 0.5), (0.25, 0.8), (0.5, 0.0)):
    clear_transforms(a)
    a.frame_rotation_pivots[0] = pivot
    a.invalidate_transforms()
    c.set_frame(0)
    enter_all(wd, c, "rotate")
    app.processEvents()
    reset_view(c)
    pump(c)
    reset_view(c)
    whole_cross = cross_screen(c)
    # 单帧模式：同一帧、同一 pivot
    c.set_transform_mode(None)
    c.set_transform_mode("rotate", "test")
    c.set_transform_targets([0])
    app.processEvents()
    reset_view(c)
    pump(c)
    reset_view(c)
    single_cross = cross_screen(c)
    d = 99.0 if (whole_cross is None or single_cross is None) else \
        math.dist(whole_cross, single_cross)
    check(f"pivot={pivot} 红点一致(<=1.0px)", d <= 1.0,
          f"整体={whole_cross} 单帧={single_cross} 差={d:.2f}px")
clear_transforms(a)

# ================= 3. 拖动中 == 松手后（不能"变了又回去"）=================
print("\n3. 整体变换：拖动中看到的画面 == 松手后看到的画面（整幅像素比对）：")


def begin_handle_drag(c, kind, index, dx, dy):
    pts = c._handles_canvas(c._current_mo())
    start = c._logical_to_screen(pts[index])
    c._begin_drag(kind, index, start)
    c._update_drag(QPointF(start.x() + dx, start.y() + dy))
    return start


def check_release(label, setup):
    """setup 里做拖动，然后比对"拖动中" vs "松手后"的整幅画面与各帧位置。"""
    clear_transforms(a)
    c.set_frame(0)
    setup()
    app.processEvents()
    _handles_state["hidden"] = True
    during = pump(c)
    during_bars = bars_screen(during)
    _handles_state["hidden"] = False
    c._end_drag()
    app.processEvents()
    _handles_state["hidden"] = True
    after = pump(c)
    after_bars = bars_screen(after)
    _handles_state["hidden"] = False
    frac = img_diff(during, after)
    d = 0.0
    for i in during_bars:
        if i in after_bars:
            d = max(d, abs(during_bars[i][0] - after_bars[i][0]),
                    abs(during_bars[i][1] - after_bars[i][1]))
    check(f"{label} 拖动中==松手后", frac <= 0.002 and d <= 1.5,
          f"差异像素={frac * 100:.3f}%  各帧最大位移={d:.2f}px")


check_release("缩放-锚点(角0)", lambda: (
    enter_all(wd, c, "scale"),
    begin_handle_drag(c, "corner", 0, 26, 20)))
check_release("缩放-对角(角2)", lambda: (
    enter_all(wd, c, "scale"),
    begin_handle_drag(c, "corner", 2, -22, -18)))
check_release("斜切(边1)", lambda: (
    enter_all(wd, c, "skew"),
    begin_handle_drag(c, "edge", 1, 0, 24)))


def begin_rotate(c, deg):
    piv = c._whole_anchor_canvas() or QPointF(0, 0)
    piv_s = c._logical_to_screen(piv)
    # 从红点正上方 160px 处按下，转到 deg 度
    r = 160.0
    start = QPointF(piv_s.x() + r * math.sin(math.radians(0)),
                    piv_s.y() - r * math.cos(math.radians(0)))
    c._begin_drag("rotate", -1, start)
    c._update_drag(QPointF(piv_s.x() + r * math.sin(math.radians(deg)),
                           piv_s.y() - r * math.cos(math.radians(deg))))


check_release("旋转(18°)", lambda: (
    enter_all(wd, c, "rotate"),
    begin_rotate(c, 18)))
check_release("旋转(75°)", lambda: (
    enter_all(wd, c, "rotate"),
    begin_rotate(c, 75)))

# ================= 3b. 拖动中手柄必须跟手 =================
print("\n3b. 整体变换拖动中，手柄还在鼠标底下（不能因为实时布局而脱手）：")
for label, kind, index, dx, dy in (
        ("缩放(角0)", "corner", 0, 26, 20),
        ("缩放(角2)", "corner", 2, -22, -18),
        ("斜切(边1)", "edge", 1, 0, 24),
):
    clear_transforms(a)
    c.set_frame(0)
    enter_all(wd, c, "scale" if kind == "corner" else "skew")
    reset_view(c)
    pts = c._handles_canvas(c._current_mo())
    start = c._logical_to_screen(pts[index])
    c._begin_drag(kind, index, start)
    # 真鼠标是一小步一小步走的：分 8 步，每步之后都重新读手柄位置
    steps = 8
    worst = 0.0
    for s in range(1, steps + 1):
        target = QPointF(start.x() + dx * s / steps, start.y() + dy * s / steps)
        c._update_drag(target)
        pump(c)                  # 让视图锚定生效（和真实交互一样）
        if kind == "edge":
            pts2 = c._handles_canvas(c._current_mo())
            cur = (c._logical_to_screen(pts2[index])
                   + c._logical_to_screen(pts2[(index + 1) % 4])) / 2.0
        else:
            cur = c._logical_to_screen(c._handles_canvas(c._current_mo())[index])
        lag = math.dist((cur.x(), cur.y()), (target.x(), target.y()))
        if s in (1, steps):
            print(f"      {label} 第{s}步: 鼠标=({target.x():.1f},{target.y():.1f}) "
                  f"手柄=({cur.x():.1f},{cur.y():.1f}) 滞后={lag:.2f}px")
        worst = max(worst, lag)
    check(f"{label} 手柄跟手(<=3px)", worst <= 3.0, f"全程最大滞后={worst:.2f}px")
    c._end_drag()

# ================= 4. 改红点：只动红点、不动帧 =================
print("\n4. 拖红点：只有红点动，各帧纹丝不动（编辑锚点不影响已做的变换）：")
clear_transforms(a)
c.set_frame(0)
enter_all(wd, c, "rotate")
app.processEvents()
_handles_state["hidden"] = True
before = bars_screen(pump(c))
_handles_state["hidden"] = False
cross0 = cross_screen(c)
start_s = QPointF(cross0[0], cross0[1])
c._begin_drag("pivot", -1, start_s)
c._update_drag(QPointF(start_s.x() + 18, start_s.y() - 14))
_handles_state["hidden"] = True
after = bars_screen(pump(c))
_handles_state["hidden"] = False
cross1 = cross_screen(c)
c._end_drag()
if before and after and cross0 and cross1:
    d = max(max(abs(before[i][0] - after[i][0]), abs(before[i][1] - after[i][1]))
            for i in before if i in after)
    move = math.dist(cross0, cross1)
    check("拖红点后各帧不动(<=1.0px)", d <= 1.0,
          f"帧最大位移={d:.2f}px  红点位移={move:.2f}px")
    check("红点确实跟着鼠标走了(>=10px)", move >= 10.0, f"{move:.2f}px")
else:
    check("拖红点前后都能量到", False, f"before={before} after={after} cross={cross0},{cross1}")

# ================= 5. 改红点不影响"已经做过的旋转"的画面位置 =================
print("\n5. 已旋转的帧，改红点后画面不动（锚点只影响后续操作）：")
clear_transforms(a)
a.frame_rotations[0] = 30
a.invalidate_transforms()
c.set_frame(0)
enter_all(wd, c, "rotate")
app.processEvents()
_handles_state["hidden"] = True
before = bars_screen(pump(c))
_handles_state["hidden"] = False
a.frame_rotation_pivots[0] = (0.2, 0.9)
a.invalidate_transforms()
_handles_state["hidden"] = True
after = bars_screen(pump(c))
_handles_state["hidden"] = False
if before and after:
    d = max(max(abs(before[i][0] - after[i][0]), abs(before[i][1] - after[i][1]))
            for i in before if i in after)
    check("改红点后各帧位置不动(<=1.5px)", d <= 1.5, f"帧最大位移={d:.2f}px")
else:
    check("改红点前后都能量到", False, f"before={before} after={after}")

# ================= 6. 真实素材（近乎不透明的大块帧）重跑一遍 =================
# 前面的测试用"小横条 + 大片透明"把裁剪口径的变化放大到极致；真实 spritesheet 的帧
# 基本铺满画布，裁剪口径几乎不变。这一节用近乎铺满的色块再验一遍（跟手 + 不闪）。
print("\n6. 真实素材（近乎不透明的大块帧）：")


def block_pix(i):
    """第 i 帧：一块铺满画布(留小边)的纯色块，尺寸逐帧递减（真实素材的差异）。"""
    bw, bh = 120 - 8 * i, 100 - 6 * i
    pm = QPixmap(120, 100)
    pm.fill(QColor(0, 0, 0, 0))
    q = QPainter(pm)
    q.fillRect(0, 0, bw, bh, QColor(*BAR[i], 255))
    q.end()
    return pm


def build_real():
    wd2 = ActionPreviewWidget()
    wd2.resize(1100, 900)
    a2 = Action("real")
    a2.frames = [block_pix(i) for i in range(N)]
    a2.padding = 14
    a2.bg_mode = "black"
    a2.frame_opacity = 1.0
    # 帧偏移拉开，保证四帧互不遮挡（能被单独量到）
    a2.frame_offsets = {i: (0, 110 * i) for i in range(N)}
    wd2.set_actions([a2])
    wd2.set_action(a2)
    c2 = wd2.canvas
    c2.setMinimumWidth(760)
    c2.setMinimumHeight(560)
    c2.setMaximumHeight(560)
    app.processEvents()
    c2._fit = False
    c2._zoom = 1.0
    c2._offset = QPointF(60, 60)
    c2._anchor_spec = None
    c2._view_shift = QPointF(0, 0)
    return wd2, a2, c2


wd, a, c = build_real()
c.set_frame(0)
enter_all(wd, c, "scale")
reset_view(c)
_handles_state["hidden"] = True
whole_real = bars_screen(render(c))
_handles_state["hidden"] = False
singles = {}
for i in range(N):
    c.set_transform_mode(None)
    c.set_transform_targets([])
    c.set_frame(i)
    app.processEvents()
    reset_view(c)
    _handles_state["hidden"] = True
    img = render(c)
    _handles_state["hidden"] = False
    got = bars_screen(img)
    singles[i] = got.get(i)
worst = 0.0
for i in range(N):
    s, w_ = singles.get(i), whole_real.get(i)
    if s is None or w_ is None:
        worst = 99.0
        continue
    worst = max(worst, abs(w_[0] - s[0]), abs(w_[1] - s[1]))
check("真实素材：整体模式落位 == 播放落位(<=1.0px)", worst <= 1.0, f"worst={worst:.2f}px")

for label, kind, index, dx, dy in (
        ("缩放(角0)", "corner", 0, 26, 20),
        ("斜切(边1)", "edge", 1, 0, 24),
):
    clear_transforms(a)
    c.set_frame(0)
    enter_all(wd, c, "scale" if kind == "corner" else "skew")
    reset_view(c)
    pts = c._handles_canvas(c._current_mo())
    start = c._logical_to_screen(pts[index])
    c._begin_drag(kind, index, start)
    worst_lag = 0.0
    for s in range(1, 9):
        target = QPointF(start.x() + dx * s / 8, start.y() + dy * s / 8)
        c._update_drag(target)
        pump(c)
        if kind == "edge":
            pts2 = c._handles_canvas(c._current_mo())
            cur = (c._logical_to_screen(pts2[index])
                   + c._logical_to_screen(pts2[(index + 1) % 4])) / 2.0
        else:
            cur = c._logical_to_screen(c._handles_canvas(c._current_mo())[index])
        worst_lag = max(worst_lag, math.dist((cur.x(), cur.y()), (target.x(), target.y())))
    check(f"真实素材 {label} 手柄跟手(<=2px)", worst_lag <= 2.0,
          f"全程最大滞后={worst_lag:.2f}px")
    _handles_state["hidden"] = True
    during = pump(c)
    _handles_state["hidden"] = False
    c._end_drag()
    app.processEvents()
    _handles_state["hidden"] = True
    after = pump(c)
    _handles_state["hidden"] = False
    frac = img_diff(during, after)
    check(f"真实素材 {label} 拖动中==松手后", frac <= 0.002, f"差异像素={frac * 100:.3f}%")

# ================= 7. 单帧(帧编辑)路径的手柄也要跟手 =================
print("\n7. 单帧模式(帧编辑)拖角：手柄跟手 + 松手不跳（真实素材）：")
wd, a, c = build_real()
clear_transforms(a)
c.set_frame(0)
c.set_transform_mode(None)
c.set_transform_mode("scale", "test")
c.set_transform_targets([0])
app.processEvents()
reset_view(c)
pts = c._handles_canvas(c._current_mo())
start = c._logical_to_screen(pts[0])
c._begin_drag("corner", 0, start)
worst_lag = 0.0
for s in range(1, 9):
    target = QPointF(start.x() + 26 * s / 8, start.y() + 20 * s / 8)
    c._update_drag(target)
    pump(c)
    cur = c._logical_to_screen(c._handles_canvas(c._current_mo())[0])
    worst_lag = max(worst_lag, math.dist((cur.x(), cur.y()), (target.x(), target.y())))
check("单帧 手柄跟手(<=2px)", worst_lag <= 2.0, f"全程最大滞后={worst_lag:.2f}px")
_handles_state["hidden"] = True
during = pump(c)
_handles_state["hidden"] = False
c._end_drag()
app.processEvents()
_handles_state["hidden"] = True
after = pump(c)
_handles_state["hidden"] = False
frac = img_diff(during, after)
check("单帧 拖动中==松手后", frac <= 0.002, f"差异像素={frac * 100:.3f}%")

print(f"\n结果：{PASS} 通过, {FAIL} 失败")
sys.exit(1 if FAIL else 0)
