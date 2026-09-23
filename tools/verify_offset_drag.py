"""「当前帧微调」拖动行为的回归：**拖的时候帧真的动，松手才重算画布区域**。

用户原话：
  「左右移动好难受啊 还是真的在移动吧 松手以后再计算区域
    不然拖动滑块不动只有区域动 感觉很难受」

改之前的行为：偏移一变就立刻重算并集 → 画布（安全区）跟着长大/移动，而画布是
**居中**画的，于是"帧看起来钉在原地、只有区域在动"。改之后：按下时冻结画布尺寸与
各帧位置，把"相对按下时刻的偏移增量"加到当前帧上（帧真的位移），松开再按新偏移
重算并集并刷新画布。

> 注：原来这里还断言过面板上的「画布大小」标签（`panel.lbl_canvas`）。该标签在
> 帧编辑面板重做时已被移除，`_refresh_canvas_label()` 现在只同步偏移控件，
> 所以标签类断言已删除 —— "松手才重算" 这件事由 `canvas._paint_layout()`
> 的尺寸断言直接证明（更靠近真实行为，不依赖某个界面标签是否存在）。

这个回归用**真信号 + 真像素**验证：
  * 真信号：QTest 在真滑块上按下/移动/松开（走 sliderPressed/sliderValueChanged/
    sliderReleased，不是直接调内部方法）；
  * 真像素：把画布渲染成图，量当前帧（纯色块）的包围盒，看它是不是真的位移了，
    以及是否真的画到了旧画布区域之外。

用法: .venv\\Scripts\\python.exe tools\\verify_offset_drag.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    # detail 只在失败时打：它写的是失败诊断，通过时打出来会读成反话
    print(f"  [{'OK' if ok else 'FAIL'}]   {label}" + ("" if ok else f" —— {detail}"))
    if not ok:
        FAILURES.append(label)


FRAME_W = 32


def pink_bbox(canvas):
    """渲染画布，量"当前帧"（纯玫红块）的包围盒；没有就返回 None。"""
    image = canvas.grab().toImage()
    left, top, right, bottom = None, None, None, None
    for y in range(image.height()):
        for x in range(image.width()):
            c = image.pixelColor(x, y)
            # 玫红 #E91E63：红高、绿很低、蓝中等
            if c.red() > 180 and c.green() < 90 and 60 < c.blue() < 160:
                left = x if left is None else min(left, x)
                right = x if right is None else max(right, x)
                top = y if top is None else min(top, y)
                bottom = y if bottom is None else max(bottom, y)
    if left is None:
        return None
    return (left, top, right, bottom)


def build():
    """独立的 面板 + 画布（不牵 MainWindow：这里只关心偏移拖动本身）。"""
    from spritesheet.ui.models import Action
    import spritesheet.ui.preview as P

    app = QApplication.instance() or QApplication(sys.argv)
    panel = P.AnimAdjustPanel()
    canvas = P.PreviewCanvas()
    panel.set_canvas(canvas)

    action = Action(name="idle")
    frame0 = QPixmap(FRAME_W, FRAME_W)
    frame0.fill(QColor("#E91E63"))
    frame1 = QPixmap(FRAME_W, FRAME_W)
    frame1.fill(QColor("#1E88E5"))
    action.frames = [frame0, frame1]
    panel.set_actions([action])
    panel.set_action(action)
    canvas.set_action(action)

    panel.resize(420, 1400)
    panel.show()
    canvas.resize(420, 300)
    canvas.show()
    # 关掉自适应并固定缩放/位移：像素坐标才能直接算
    canvas._fit = False
    canvas._zoom = 1.0
    canvas._offset = P.QPointF(20, 20)
    canvas.update()
    for _ in range(3):
        app.processEvents()
    return app, panel, canvas, action


def main() -> int:
    app, panel, canvas, action = build()
    slider = panel.slider_off_h

    print("=== 0. 起始状态 ===")
    frozen_w, frozen_h, _, _ = canvas._paint_layout()
    bbox0 = pink_bbox(canvas)
    print(f"  画布区域 {frozen_w}×{frozen_h}，当前帧包围盒 {bbox0}")
    check("起始画布区域 = 帧尺寸", (frozen_w, frozen_h) == (FRAME_W, FRAME_W),
          f"实际 {frozen_w}×{frozen_h}")
    check("起始就能看到当前帧", bbox0 is not None, "没找到玫红像素")

    print()
    print("=== 1. 真按下滑块 → 冻结画布区域 ===")
    centre = QPoint(slider.width() // 2, slider.height() // 2)
    QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=centre)
    app.processEvents()
    check("按下即冻结（sliderPressed 已接线）", canvas.layout_frozen(),
          f"layout_frozen()={canvas.layout_frozen()}")

    print()
    print("=== 2. 拖动中：帧真的位移、画布区域**不动** ===")
    QTest.mouseMove(slider, QPoint(centre.x() + 40, centre.y()))
    app.processEvents()
    dx, dy = action.frame_offsets.get(0, (0, 0))
    drag_w, drag_h, positions, _ = canvas._paint_layout()
    bbox1 = pink_bbox(canvas)
    print(f"  偏移 = ({dx},{dy})，绘制用画布区域 {drag_w}×{drag_h}，包围盒 {bbox1}")
    check("拖动确实改了偏移（滑块 → 数据）", dx != 0, f"偏移 {dx}")
    check("拖动期间画布区域尺寸冻结不变", (drag_w, drag_h) == (frozen_w, frozen_h),
          f"{drag_w}×{drag_h} vs 冻结 {frozen_w}×{frozen_h}")
    check("拖动期间当前帧**真的右移**了（像素证据）",
          bbox1 is not None and bbox0 is not None and bbox1[0] - bbox0[0] == dx,
          f"包围盒左边界 {bbox0[0]} → {bbox1[0]}（偏移 {dx}）")
    check("帧已经画出旧画布区域之外（真的是帧在动，不是区域在动）",
          bbox1 is not None and bbox1[2] > 20 + frozen_w,
          f"帧右边界 {bbox1[2] if bbox1 else None} > 画布右边界 {20 + frozen_w}")

    print()
    print("=== 3. 松手：重算区域 ===")
    QTest.mouseRelease(slider, Qt.MouseButton.LeftButton,
                       pos=QPoint(centre.x() + 40, centre.y()))
    app.processEvents()
    after_w, after_h, _, _ = canvas._paint_layout()
    print(f"  画布区域 {after_w}×{after_h}")
    check("松手解除冻结", not canvas.layout_frozen(), "还是冻结状态")
    check("松手后区域按新偏移重算（变宽）", after_w > frozen_w,
          f"{frozen_w} → {after_w}")
    check("松手后区域宽度 = 帧宽 + 偏移", after_w == FRAME_W + dx,
          f"实际 {after_w}，期望 {FRAME_W + dx}")

    print()
    print("=== 4. 方向按钮：按住冻结、连点累计、松开重算 ===")
    panel._reset_frame_offset()
    app.processEvents()
    base_w = canvas._paint_layout()[0]
    button = panel.btn_right
    print(f"  自动重复：{button.autoRepeat()}，延迟 {button.autoRepeatDelay()}ms，"
          f"间隔 {button.autoRepeatInterval()}ms")
    check("方向按钮开了自动重复（按住能连续移动）", button.autoRepeat())
    QTest.mousePress(button, Qt.MouseButton.LeftButton,
                     pos=QPoint(button.width() // 2, button.height() // 2))
    app.processEvents()
    check("按下方向按钮也冻结区域", canvas.layout_frozen(),
          f"layout_frozen()={canvas.layout_frozen()}")
    panel._adjust_frame(1, 0)
    panel._adjust_frame(1, 0)
    app.processEvents()
    mid_w = canvas._paint_layout()[0]
    print(f"  连点 3 次后偏移 {action.frame_offsets.get(0)}，区域仍 {mid_w}×?")
    check("连点期间区域不变", mid_w == base_w, f"{base_w} → {mid_w}")
    QTest.mouseRelease(button, Qt.MouseButton.LeftButton,
                       pos=QPoint(button.width() // 2, button.height() // 2))
    app.processEvents()
    end_w = canvas._paint_layout()[0]
    print(f"  松开后偏移 {action.frame_offsets.get(0)}，区域 {base_w} → {end_w}")
    check("松开后按累计偏移重算", end_w == base_w + 3, f"{base_w} → {end_w}")

    print()
    print("=== 5. 非拖动路径仍然是实时刷新（不能一起被冻住）===")
    panel._reset_frame_offset()
    app.processEvents()
    panel.spin_off_x.set_value(12)
    panel._on_offset_label_changed(12)
    app.processEvents()
    live_w = canvas._paint_layout()[0]
    print(f"  数字框输入 12 → 区域 {live_w}")
    check("数字框改动立刻重算区域（没有冻结）", live_w == FRAME_W + 12,
          f"实际 {live_w}")

    canvas.close()
    panel.close()
    print()
    if FAILURES:
        print("OFFSET DRAG FAILED：", *FAILURES, sep="\n  - ")
        return 1
    print("OFFSET DRAG OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
