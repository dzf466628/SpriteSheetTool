#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""回归测试：「完美像素」（像素画视图）+「收边」0.5 档 + 辅助线颜色走设置。

钉死的约定：
- 工具条上「完美像素」按钮在**白色背景右边**，是开关（每个标签页各一份）；
- 进模式：100% 实际像素 + **居中** + 偏移取整（半个像素的偏移会让像素宽窄不匀）；
- 模式内放大/缩小**只走整数倍**（1×/2×/3×…），最小 1×；渲染**关掉平滑**（最近邻）；
- 模式内**不画**元素虚线框、帧号色块、青色标线、「已忽略」小字（100% 下这些会糊住画面）；
- 每像素网格：1px 线，颜色/透明度与"放大到多少倍才显示"都来自「软件设置」
  （默认 400%，因为 100% 时线宽=线间距=1px，整张图会被线盖住）；
- 画笔在模式内改**方头**：单击 1px 笔 = 恰好 1 个像素，5px 笔 = 恰好 5×5 方块（圆头不是）；
- 「收边」共 13 档（0.5 一步，范围不变 -2..4）；整数档与老实现逐像素一致，
  半档是相邻整数档之间的 alpha 插值（边缘半透明）。

全程 offscreen；QApplication 必须挂模块级。
跑之前会把用户当前的辅助线/阈值设置**存档到磁盘**、跑完原样写回（见 settings_guard.py：
中途崩了也能在下次启动时救回来，不能让测试把用户配置永久改成测试色）。

Usage: .venv\\Scripts\\python.exe tools\\verify_pixel_perfect.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # 同目录的 settings_guard

from PIL import Image, ImageDraw
from PyQt6.QtCore import QEvent, QPointF, QRect, Qt
from PyQt6.QtGui import QColor, QMouseEvent
from PyQt6.QtWidgets import QApplication

from settings_guard import protect_settings
from spritesheet import keying, settings
from spritesheet.ui.key_panel import pil_to_pixmap
from spritesheet.ui.main_window import MainWindow

_APP = QApplication.instance() or QApplication(sys.argv)

FAILURES: list[str] = []
passed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed
    if ok:
        passed += 1
        print(f"  [OK]   {name}")
    else:
        FAILURES.append(name)
        print(f"  [FAIL] {name} {detail}")


def wait(ms: int = 120) -> None:
    for _ in range(max(1, ms // 40)):
        _APP.processEvents()
        _APP.thread().msleep(40)


def pixel(pm, x: int, y: int) -> QColor:
    return QColor(pm.toImage().pixelColor(int(x), int(y)))


def mouse(kind, point: QPointF, button=Qt.MouseButton.LeftButton,
          buttons=Qt.MouseButton.NoButton) -> QMouseEvent:
    return QMouseEvent(kind, point, point, point, button, buttons,
                       Qt.KeyboardModifier.NoModifier)


def click(win, point: QPointF) -> None:
    """在画布上真的"点一下"（按下 → 松开，没有中间移动）。"""
    canvas = win.image_viewer.canvas
    canvas.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, point))
    canvas.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, point))


def image_to_canvas(viewer, ix: float, iy: float) -> QPointF:
    zoom = viewer._zoom or 1.0
    return QPointF(viewer._offset.x() + ix * zoom, viewer._offset.y() + iy * zoom)


def changed_pixels(before, after) -> list[tuple[int, int]]:
    """两张原图之间**被改过的像素坐标**（逐像素比 RGB，忽略 alpha 之外的差异）。"""
    a, b = before.toImage(), after.toImage()
    out = []
    for y in range(min(a.height(), b.height())):
        for x in range(min(a.width(), b.width())):
            if a.pixel(x, y) != b.pixel(x, y):
                out.append((x, y))
    return out


def sheet() -> Image.Image:
    """透明底 + 两个分离的方块：自动识别会认出 2 个元素。

    底色必须**透明**：不透明底会把两个方块连成一整块（抠图/检测那两套脚本都踩过）。
    """
    img = Image.new("RGBA", (100, 70), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((10, 10, 29, 29), fill=(220, 40, 40, 255))
    draw.rectangle((60, 40, 89, 59), fill=(40, 60, 220, 255))
    return img


def count_where(grab, predicate, skip: list | None = None) -> int:
    """数一数画布抓图里满足条件的像素。

    ``skip`` 是一串要**排除**的矩形（画布坐标）：``QWidget.grab()`` 会把子控件一起
    渲染进来，右上角那条浮动工具条（含"完美像素"按钮自身的按下底色）就在里面 ——
    不排除的话"元素虚线框的灰色像素"会把工具条图标也数上（第一版就是这么误报的）。
    """
    image = grab.toImage()
    total = 0
    for y in range(image.height()):
        for x in range(image.width()):
            if skip and any(r.contains(x, y) for r in skip):
                continue
            if predicate(QColor(image.pixelColor(x, y))):
                total += 1
    return total


def count_mid_gray(grab, skip: list | None = None) -> int:
    """元素辅助线那圈灰色虚线（#808080）。棋盘格/界面底离中灰都很远。"""
    return count_where(grab, lambda c: abs(c.red() - 128) <= 20
                       and abs(c.green() - 128) <= 20 and abs(c.blue() - 128) <= 20, skip)


def count_accent(grab, skip: list | None = None) -> int:
    """主题色 #E91E63 一带的像素（帧号色块 / 当前动作的实线框）。"""
    return count_where(grab, lambda c: c.red() > 180 and c.green() < 90
                       and 70 < c.blue() < 170, skip)


def count_exact(grab, rgb: tuple[int, int, int], skip: list | None = None) -> int:
    return count_where(grab, lambda c: (c.red(), c.green(), c.blue()) == rgb, skip)


def count_partial_red(grab, skip: list | None = None) -> int:
    """「暗红」像素：红块（#DC2828）与黑底之间那层插值出来的中间色。

    判据要**卡在"比纯红暗、又明显偏红"这一段**：
      - 只写 "r >= 60" 会把红块自己（220,40,40）也数进去（第二版就是这么误报 1600 的）；
      - 只写 "r 在 20..180、g/b < 40" 又会把成片的 #1a1a1a 深灰数进来（第一版误报 14000+）。
    所以三条一起要：偏红、比纯红暗、不是灰（红明显压过绿蓝）。
    """
    return count_where(grab, lambda c: 60 <= c.red() <= 200
                       and c.red() - c.green() >= 50 and c.red() - c.blue() >= 50, skip)


def floating_rects(viewer, canvas) -> list:
    """两条浮动工具条占的画布矩形（抓图时排除用）。"""
    rects = []
    for bar in (viewer._toolbar, viewer._v_toolbar):
        if bar is None:
            continue
        top_left = bar.mapTo(canvas, bar.rect().topLeft())
        rects.append(QRect(top_left, bar.size()))
    return rects


def shrink_alpha(img: Image.Image, value: float) -> bytes:
    out, _stats = keying.key_image(img, [(255, 255, 255)], shrink=value)
    return out.getchannel("A").tobytes()


def main() -> int:
    # 用户当前的辅助线/阈值设置：存档 → 跑完原样写回（测试不能改用户的软件设置）。
    # 用 settings_guard 落盘而不是记在内存里：跑到一半崩了（offscreen 收尾偶尔会崩）
    # 内存那份就没了，用户设置会永久留成测试色 —— 这个坑真踩过。
    restore_settings = protect_settings()
    # 识别阈值也先归零：这个值用户可能自己调大过（调大就会把测试用的小方块当噪点滤掉，
    # 元素数量对不上，后面所有按元素算的断言全塌），跑完由 restore_settings 原样写回。
    settings.set_detect_min_area(0)

    win = MainWindow()
    win.resize(1280, 860)
    win.show()
    wait(250)
    viewer = win.image_viewer
    canvas = viewer.canvas
    toolbar = viewer._toolbar

    win._on_image_loaded(pil_to_pixmap(sheet()))
    wait(600)
    check("(准备) 自动认出 2 个元素", len(viewer._frames) == 2, f"frames={viewer._frames}")
    win._on_add_action_clicked()
    wait(250)
    action = win.actions[0] if win.actions else None
    check("(准备) 动作建出来了", action is not None)

    print("--- 1. 「收边」13 档：0.5 一步，整数档和以前逐像素一致 ---")
    spec = next((s for s in keying.PARAM_SPEC if s["key"] == "shrink"), None)
    check("收边参数是小数档位", spec is not None and spec["kind"] == "float", f"spec={spec}")
    check("档位步长 0.5、范围仍是 -2..4",
          spec is not None and float(spec["step"]) == 0.5
          and float(spec["min"]) == -2 and float(spec["max"]) == 4, f"spec={spec}")
    detents = int(round((float(spec["max"]) - float(spec["min"])) / float(spec["step"]))) + 1
    check("一共 13 档（以前 7 档）", detents == 13, f"档位={detents}")

    probe = Image.new("RGBA", (40, 40), (255, 255, 255, 255))
    ImageDraw.Draw(probe).rectangle((10, 10, 29, 29), fill=(200, 40, 40, 255))
    check("整数档 1.0 与 1 一致（老行为没变）",
          shrink_alpha(probe, 1.0) == shrink_alpha(probe, 1))
    check("0.5 档和 0 档不同（半档真的生效）",
          shrink_alpha(probe, 0.5) != shrink_alpha(probe, 0))
    check("1.5 档夹在 1 和 2 之间（不等于任一端）",
          shrink_alpha(probe, 1.5) not in (shrink_alpha(probe, 1), shrink_alpha(probe, 2)))
    whole_hist = keying.key_image(probe, [(255, 255, 255)], shrink=1)[0].getchannel("A").histogram()
    half_hist = keying.key_image(probe, [(255, 255, 255)], shrink=1.5)[0].getchannel("A").histogram()
    check("整数档只有 0/255（硬边，和以前一样）",
          all(c == 0 for v, c in enumerate(whole_hist) if 0 < v < 255),
          f"hist={ {v: c for v, c in enumerate(whole_hist) if c} }")
    check("半档多出一圈半透明（这就是「更细」的表现）",
          any(c for v, c in enumerate(half_hist) if 0 < v < 255),
          f"hist={ {v: c for v, c in enumerate(half_hist) if c} }")
    check("负半档同样生效",
          shrink_alpha(probe, -0.5) != shrink_alpha(probe, 0))

    print("--- 2. 收边滑块：0.5 一档，标签带小数 ---")
    viewer._v_toolbar.btn_key.setChecked(True)
    wait(200)
    panel = win._key_panel
    slider = panel._sliders.get("shrink")
    check("滑块当「档位序号」用：0..12", slider is not None and slider.maximum() == 12,
          f"slider={slider and (slider.minimum(), slider.maximum())}")
    slider.setValue(7)
    wait(120)
    check("第 7 格 = 1.5（-2 + 7×0.5，不是 3）", float(panel._params["shrink"]) == 1.5,
          f"value={panel._params['shrink']}")
    check("标签显示 1.5（不能显示成 1 或 2）",
          panel._values["shrink"].text() == "1.5", f"label={panel._values['shrink'].text()}")
    slider.setValue(3)
    wait(80)
    check("第 3 格 = -0.5（负半档也能选）", float(panel._params["shrink"]) == -0.5,
          f"value={panel._params['shrink']}")
    slider.setValue(0)
    wait(80)
    check("第 0 格 = -2（下限）", float(panel._params["shrink"]) == -2.0,
          f"value={panel._params['shrink']}")
    check("负数标签也带小数位", panel._values["shrink"].text() == "-2.0",
          f"label={panel._values['shrink'].text()}")
    slider.setValue(8)
    wait(80)
    check("第 8 格 = 2.0（整数档照旧）", float(panel._params["shrink"]) == 2.0,
          f"value={panel._params['shrink']}")
    viewer._v_toolbar.btn_key.setChecked(False)
    wait(150)

    print("--- 3. 完美像素按钮：位置 + 进模式的视图 ---")
    # 布局顺序：白色背景 → 完美像素（用户指定「在白色背景右边」）
    order = [toolbar.btn_zoom_in, toolbar.btn_zoom_out, toolbar.btn_bg_checker,
             toolbar.btn_bg_black, toolbar.btn_bg_white, toolbar.btn_pixel_perfect]
    layout = toolbar.layout()
    check("按钮在工具条布局里，且紧挨白色背景右边",
          layout.indexOf(toolbar.btn_pixel_perfect) == layout.indexOf(toolbar.btn_bg_white) + 1,
          f"索引={[layout.indexOf(w) for w in order]}")
    check("它是可勾选的开关", toolbar.btn_pixel_perfect.isCheckable())
    check("(准备) 一开始不在完美像素模式", viewer.pixel_perfect is False)

    viewer._zoom, viewer._offset = 0.5, QPointF(13, 17)
    # 用 click() 而不是 setChecked()：这个按钮接的是 clicked（"用户点了一下"），
    # 程序化 setChecked 不发 clicked，等于没点 —— 第一版就是这么假通过的。
    toolbar.btn_pixel_perfect.click()
    wait(150)
    check("点开后进入完美像素模式", viewer.pixel_perfect is True)
    check("缩放自动变成 100%（实际像素）", abs(viewer._zoom - 1.0) < 1e-9,
          f"zoom={viewer._zoom}")
    expect_x = round((canvas.width() - viewer._pixmap.width()) / 2)
    expect_y = round((canvas.height() - viewer._pixmap.height()) / 2)
    check("图片居中（偏移 = 画布与图片差的一半）",
          abs(viewer._offset.x() - expect_x) <= 1 and abs(viewer._offset.y() - expect_y) <= 1,
          f"offset={viewer._offset.x()},{viewer._offset.y()} 期望={expect_x},{expect_y}")
    check("偏移是整数（半个像素会让像素宽窄不匀）",
          viewer._offset.x() == int(viewer._offset.x())
          and viewer._offset.y() == int(viewer._offset.y()),
          f"offset={viewer._offset}")

    print("--- 4. 模式内缩放只走整数倍 ---")
    viewer.zoom_in()
    wait(80)
    check("放大 1 次 → 2×（不是 1.2×）", abs(viewer._zoom - 2.0) < 1e-9, f"zoom={viewer._zoom}")
    viewer.zoom_in()
    wait(80)
    check("再放大 → 3×", abs(viewer._zoom - 3.0) < 1e-9, f"zoom={viewer._zoom}")
    viewer.zoom_out()
    wait(80)
    check("缩小 1 次 → 2×", abs(viewer._zoom - 2.0) < 1e-9, f"zoom={viewer._zoom}")
    viewer.zoom_out()
    viewer.zoom_out()
    wait(80)
    check("缩到 100% 就不动了（不会低于实际像素）", abs(viewer._zoom - 1.0) < 1e-9,
          f"zoom={viewer._zoom}")
    check("滚轮缩放后偏移仍是整数", viewer._offset.x() == int(viewer._offset.x()))

    print("--- 5. 最近邻渲染：像素画必须是硬边 ---")
    viewer._on_bg_changed("black")
    skip = floating_rects(viewer, canvas)
    viewer._zoom = 2.0
    viewer.snap_offset()
    canvas.update()
    wait(150)
    sharp = count_partial_red(canvas.grab(), skip)
    red_2x = count_exact(canvas.grab(), (220, 40, 40), skip)
    check("完美像素模式下没有过渡像素（最近邻）", sharp == 0, f"过渡像素={sharp}")
    check("放大 2× 后红块像素数正好 4 倍（20×20 → 40×40，多一个都说明有插值）",
          red_2x == 1600, f"红像素={red_2x}")
    check("蓝块同样正好 4 倍（30×20 → 60×40）",
          count_exact(canvas.grab(), (40, 60, 220), skip) == 2400)
    viewer.pixel_perfect = False
    viewer.snap_offset()
    canvas.update()
    wait(150)
    smooth = count_partial_red(canvas.grab(), skip)
    check("关掉模式后同样的视图有过渡像素（证明上面那条不是空断言）",
          smooth > 0, f"过渡像素={smooth}")
    viewer.pixel_perfect = True
    viewer._sync_pixel_perfect_button()
    canvas.update()
    wait(120)

    print("--- 6. 模式内不画元素虚线框 / 帧号色块 / 标线 ---")
    viewer.zoom_actual_pixel()
    wait(150)
    action.selected_indices = [0]
    viewer.set_actions(win.actions)
    viewer.set_current_action(action)
    gray_pp = count_mid_gray(canvas.grab(), skip)
    viewer.pixel_perfect = False
    viewer._sync_pixel_perfect_button()
    canvas.update()
    wait(180)
    gray_on = count_mid_gray(canvas.grab(), skip)
    check("普通视图里能看到元素虚线框（灰色像素 > 0）", gray_on > 0, f"灰={gray_on}")
    check("完美像素模式里一个框都不画", gray_pp == 0, f"灰={gray_pp}")
    accent_rgb = tuple(QColor(action.color).getRgb()[:3])
    accent_off = count_exact(canvas.grab(), accent_rgb, skip)
    viewer.pixel_perfect = True
    viewer._sync_pixel_perfect_button()
    canvas.update()
    wait(180)
    accent_pp = count_exact(canvas.grab(), accent_rgb, skip)
    check("普通视图里帧号色块/当前动作色框在（用这个动作自己的颜色数）",
          accent_off > 0, f"色={accent_rgb} 像素={accent_off}")
    check("完美像素模式里帧号色块也不画", accent_pp == 0, f"像素={accent_pp}")

    settings.set_guide_color(QColor("#00FF00"))
    settings.set_guide_selected_color(QColor("#FF8800"))
    viewer.reload_view_colors()
    viewer.set_guides([["h", 45], ["v", 30]])
    canvas.update()
    wait(150)
    green_pp = count_exact(canvas.grab(), (0, 255, 0), skip)
    check("完美像素模式里标线也不画", green_pp == 0, f"绿像素={green_pp}")
    viewer.pixel_perfect = False
    viewer._sync_pixel_perfect_button()
    canvas.update()
    wait(150)
    green_normal = count_exact(canvas.grab(), (0, 255, 0), skip)
    check("普通视图里标线画出来了（证明上一条不是空断言）", green_normal > 0,
          f"绿像素={green_normal}")
    check("标线颜色确实用了设置里的绿（不再写死青色）",
          count_exact(canvas.grab(), (0, 255, 255), skip) == 0)
    viewer.pixel_perfect = True
    viewer._sync_pixel_perfect_button()

    print("--- 7. 每像素网格：颜色与门槛都来自软件设置 ---")
    settings.set_pixel_grid_color(QColor("#FF00FF"))
    settings.set_pixel_grid_zoom(400)
    viewer.reload_view_colors()
    viewer._zoom = 2.0
    viewer.snap_offset()
    canvas.update()
    wait(150)
    check("放大 2× 还没到门槛（400%）→ 不画网格",
          count_exact(canvas.grab(), (255, 0, 255), skip) == 0)
    viewer._zoom = 4.0
    viewer.snap_offset()
    canvas.update()
    wait(150)
    mag = count_exact(canvas.grab(), (255, 0, 255), skip)
    check("放大到 4× 出现网格线（用的是设置里的颜色）", mag > 0, f"品红像素={mag}")
    settings.set_pixel_grid_zoom(800)
    viewer.reload_view_colors()
    viewer._zoom = 4.0
    canvas.update()
    wait(150)
    check("把门槛改成 800% 后 4× 又不画了（门槛真的读设置）",
          count_exact(canvas.grab(), (255, 0, 255), skip) == 0)
    settings.set_pixel_grid_color(QColor(128, 128, 128, 0))
    viewer.reload_view_colors()
    canvas.update()
    wait(120)
    check("网格颜色全透明 = 不要网格（alpha 0 直接跳过绘制）",
          viewer._grid_color.alpha() == 0)

    print("--- 8. 方块笔：单击 1px 笔 = 恰好 1 个像素 ---")
    settings.set_pixel_grid_zoom(400)
    settings.set_pixel_grid_color(QColor("#80808080"))
    viewer.reload_view_colors()
    viewer.pixel_perfect = True
    viewer._sync_pixel_perfect_button()
    viewer.zoom_actual_pixel()
    viewer._v_toolbar.btn_brush.setChecked(True)
    wait(150)
    check("(准备) 画笔工具开着", viewer.paint_tool == "brush")
    viewer.set_paint_color(QColor("#00FF00"))
    viewer.set_brush_size(1)
    viewer.sync_paint_colors()
    wait(80)
    before = viewer._source_images[0]
    click(win, image_to_canvas(viewer, 20, 20))
    wait(200)
    changed = changed_pixels(before, viewer._source_images[0])
    check("1px 方头笔点一下**恰好**改 1 个像素", len(changed) == 1, f"改了={changed}")
    check("落点就是鼠标所在那个像素 (20,20)", changed == [(20, 20)], f"改了={changed}")
    check("落的是前景色", pixel(viewer._source_images[0], 20, 20).name().upper() == "#00FF00",
          f"got={pixel(viewer._source_images[0], 20, 20).name()}")

    print("--- 9. 方块笔：3px 笔 = 3×3 方块，5px 笔 = 25 个像素 ---")
    viewer.set_brush_size(3)
    wait(80)
    before = viewer._source_images[0]
    click(win, image_to_canvas(viewer, 25, 15))
    wait(200)
    changed = changed_pixels(before, viewer._source_images[0])
    check("3px 笔点一下改 9 个像素（3×3 方块）", len(changed) == 9, f"改了={len(changed)}")
    xs = sorted({x for x, _ in changed})
    ys = sorted({y for _, y in changed})
    check("这 9 个像素拼成一个整方块（不是圆点）",
          xs == [24, 25, 26] and ys == [14, 15, 16], f"x={xs} y={ys}")
    viewer.set_brush_size(5)
    wait(80)
    before = viewer._source_images[0]
    click(win, image_to_canvas(viewer, 50, 20))
    wait(200)
    square5 = len(changed_pixels(before, viewer._source_images[0]))
    check("5px 方头笔 = 25 个像素", square5 == 25, f"改了={square5}")
    viewer.pixel_perfect = False
    viewer._sync_pixel_perfect_button()
    wait(120)
    before = viewer._source_images[0]
    click(win, image_to_canvas(viewer, 50, 45))
    wait(250)
    round5 = len(changed_pixels(before, viewer._source_images[0]))
    # 圆头笔的落点是**跟着鼠标的连续坐标**（不像像素模式那样吸到像素格），
    # 所以这个直径 5 的圆点覆盖 6×6 里的一部分：既不是 0，也**不是 25 的方块**。
    check("普通模式同一支笔是**圆**的：不是一个 5×5 方块（证明形状真的换了）",
          round5 != 25 and 0 < round5 <= 36, f"改了={round5}（方块是 25）")
    check("单击也要落笔（以前零长度线段什么都不画，用户「点一下没反应」）",
          round5 > 0, f"改了={round5}")
    viewer.pixel_perfect = True
    viewer._sync_pixel_perfect_button()

    print("--- 10. 退出模式：回到进来之前那份视图 ---")
    viewer._v_toolbar.btn_brush.setChecked(False)
    wait(120)
    viewer.pixel_perfect = False
    viewer._sync_pixel_perfect_button()
    viewer._zoom, viewer._offset = 0.5, QPointF(13, 17)
    toolbar.btn_pixel_perfect.click()
    wait(150)
    check("(准备) 又进了模式，缩放 100%", abs(viewer._zoom - 1.0) < 1e-9)
    toolbar.btn_pixel_perfect.click()
    wait(150)
    check("退出后缩放回到 0.5", abs(viewer._zoom - 0.5) < 1e-9, f"zoom={viewer._zoom}")
    check("退出后偏移也回到原来的 (13,17)",
          abs(viewer._offset.x() - 13) <= 1 and abs(viewer._offset.y() - 17) <= 1,
          f"offset={viewer._offset.x()},{viewer._offset.y()}")
    check("退出后按钮是弹起的", toolbar.btn_pixel_perfect.isChecked() is False)

    print("--- 11. 每个标签页各一份（跟标线同一口径） ---")
    toolbar.btn_pixel_perfect.click()
    wait(150)
    check("(准备) 标签页 1 进了模式", viewer.pixel_perfect is True)
    win._new_tab()
    wait(300)
    check("新标签页是普通视图（不会跟着上一个标签页一起切）",
          viewer.pixel_perfect is False, f"pp={viewer.pixel_perfect}")
    check("新标签页按钮也是弹起的", toolbar.btn_pixel_perfect.isChecked() is False)
    win._switch_tab(0)
    wait(400)
    check("切回标签页 1 恢复到完美像素模式", viewer.pixel_perfect is True,
          f"pp={viewer.pixel_perfect}")
    check("切回后按钮同步按下（显示状态不能和实际状态脱节）",
          toolbar.btn_pixel_perfect.isChecked() is True)
    toolbar.btn_pixel_perfect.click()
    wait(120)

    print("--- 13. 退出完美像素：重贴紧老外框 + 追加新画的元素（老元素绝不重排） ---")
    # 用户口径："关闭完美像素按钮以后要重新检测场上的素材"、后来又补了一句
    # "新加入的元素要有（灰色框）啊，以前的灰色框可以不动" —— 所以：
    #   老外框只贴紧、下标一个不动；没碰到任何老外框的连通域**追加在末尾**；
    #   新元素要过软件设置里的"自动识别阈值"，免得点错一个像素也变成一个元素。
    # 阈值调成 4：注意检测器的口径是"**长边**要 > 阈值"（`_detect_frames_image` 里
    # `max(w, h) > min_area`），不是面积 —— 5px 的那块（长边 5）过得了，
    # 单个像素（长边 1）过不了。而且这个值必须**小于**夹具方块的边长 20，
    # 不然 A（20×20）自己就会被"识别阈值"挡掉（第一版设成 20 就踩了这个）。
    settings.set_detect_min_area(4)
    # 开个**新标签页**再载一张干净的原图验这一节：前面第 8/9 节在空白处涂过像素，
    # 现在那些也会被认成新元素（功能是对的），但"开头两个元素"这条前提就不成立了。
    # 注意不能用 `_on_image_loaded` 直接重载 —— 已经有一张图时它是**追加**原图
    # （会把两张图拼在一起，元素变成 4 个），必须从空白页开始。
    win._new_tab()
    wait(300)
    win._on_image_loaded(pil_to_pixmap(sheet()))
    wait(500)
    toolbar.btn_pixel_perfect.click()          # 先确保在模式里
    wait(200)
    viewer.zoom_actual_pixel()
    viewer._v_toolbar.btn_brush.setChecked(True)
    viewer.set_paint_color(QColor("#00FF00"))
    viewer.set_brush_size(1)
    viewer.sync_paint_colors()
    wait(120)
    frames_before = list(viewer._frames)
    check("(准备) 两个元素：A=(10,10,20,20) B=(60,40,30,20)",
          frames_before == [(10, 10, 20, 20), (60, 40, 30, 20)], f"frames={frames_before}")

    # 1) 贴着 A 的右边补一个像素（x=30 与 A 的 x=29 相邻，属于同一块像素）
    click(win, image_to_canvas(viewer, 30, 10))
    wait(200)
    # 2) 在空白处画一个**太小**的孤立像素（谁也不挨着，而且过不了 20px 的阈值）
    click(win, image_to_canvas(viewer, 45, 60))
    wait(200)
    # 3) 在空白处画一块**够大**的新元素：5px 笔 = 25 个像素，位置离 A / B 都远
    viewer.set_brush_size(5)
    wait(80)
    click(win, image_to_canvas(viewer, 70, 5))
    wait(200)
    check("三处像素都涂上去了（否则下面是空断言）",
          pixel(viewer._source_images[0], 30, 10).name().upper() == "#00FF00"
          and pixel(viewer._source_images[0], 45, 60).name().upper() == "#00FF00"
          and pixel(viewer._source_images[0], 70, 3).name().upper() == "#00FF00",
          f"a={pixel(viewer._source_images[0], 30, 10).name()} "
          f"b={pixel(viewer._source_images[0], 45, 60).name()} "
          f"c={pixel(viewer._source_images[0], 70, 3).name()}")

    undo_before = win._undo_stack.count()
    toolbar.btn_pixel_perfect.click()          # ← 用户点按钮退出
    wait(500)
    frames_after = list(viewer._frames)
    check("A 的外框跟着像素长大了（右边多了一个像素 → 宽 20→21）",
          frames_after[0] == (10, 10, 21, 20), f"frames={frames_after}")
    check("B 的外框原样不动", frames_after[1] == (60, 40, 30, 20), f"frames={frames_after}")
    check("新画的那块**追加成了第 3 个元素**，框就是它自己 (68,3,5,5)",
          len(frames_after) == 3 and frames_after[2] == (68, 3, 5, 5),
          f"frames={frames_after}")
    check("老元素的**下标一个没动**（前两个格子还是 A、B —— 帧号/合成组靠的就是这个）",
          frames_after[0][:2] == (10, 10) and frames_after[1][:2] == (60, 40),
          f"frames={frames_after}")
    check("空白处那个**单个像素**太小，没算成元素（阈值：长边 > 4 px）",
          not any(x <= 45 < x + w and y <= 60 < y + h for x, y, w, h in frames_after),
          f"frames={frames_after}")
    check("重贴紧只记了**一条**撤销", win._undo_stack.count() == undo_before + 1,
          f"before={undo_before} after={win._undo_stack.count()}")
    check("状态栏说了两件事：重贴紧 + 新认出，且说清了「太小没算」",
          "重贴紧" in win.lbl_status.text() and "新认出 1 个元素" in win.lbl_status.text()
          and "太小" in win.lbl_status.text(), f"status={win.lbl_status.text()}")

    win._do_undo()
    wait(400)
    check("Ctrl+Z 能把外框退回重贴紧之前（新元素也一起退掉）",
          list(viewer._frames) == frames_before, f"frames={list(viewer._frames)}")
    win._do_redo()
    wait(400)
    check("重做又回到重贴紧之后", list(viewer._frames) == frames_after,
          f"frames={list(viewer._frames)}")

    print("--- 14. 擦掉像素 / 涂在内部：不删元素、不白占撤销 ---")
    toolbar.btn_pixel_perfect.click()          # 再进模式，用橡皮擦掉第 13 步补的那个像素
    wait(200)
    viewer.zoom_actual_pixel()
    viewer._v_toolbar.btn_eraser.setChecked(True)
    viewer.set_brush_size(1)
    wait(120)
    check("(准备) 橡皮开着、1px", viewer.paint_tool == "eraser" and viewer.brush_size == 1,
          f"tool={viewer.paint_tool} size={viewer.brush_size}")
    # 擦之前 A 必须还是**长大过**的那个框：不先钉住这一条，下面"擦完又缩回去"就会在
    # "框从头到尾没变过"的情况下也通过（假绿）
    check("(准备) 擦之前 A 仍是长过的 (10,10,21,20)",
          list(viewer._frames)[0] == (10, 10, 21, 20), f"frames={list(viewer._frames)}")
    click(win, image_to_canvas(viewer, 30, 10))     # 正好擦掉让 A 变宽的那个像素
    wait(200)
    check("那个像素真的被擦成透明了",
          pixel(viewer._source_images[0], 30, 10).alpha() == 0,
          f"alpha={pixel(viewer._source_images[0], 30, 10).alpha()}")
    toolbar.btn_pixel_perfect.click()               # 退出 → 外框应该收回去
    wait(500)
    shrunk = list(viewer._frames)
    check("擦掉内容后外框收紧了（A 的宽 21→20，回到没涂之前）",
          shrunk[0] == (10, 10, 20, 20), f"frames={shrunk}")
    check("被擦的元素**还在**、第 13 步追加的新元素也还在（不删元素：删了后面所有下标都会错位）",
          len(shrunk) == 3 and shrunk[1] == (60, 40, 30, 20) and shrunk[2] == (68, 3, 5, 5),
          f"frames={shrunk}")

    toolbar.btn_pixel_perfect.click()
    wait(200)
    viewer.zoom_actual_pixel()
    viewer._v_toolbar.btn_brush.setChecked(True)
    viewer.set_brush_size(1)
    viewer.set_paint_color(QColor("#0000FF"))
    viewer.sync_paint_colors()
    wait(120)
    click(win, image_to_canvas(viewer, 15, 20))     # 涂在 A 的**内部**：外框不该变
    wait(200)
    frames_now = list(viewer._frames)
    undo_now = win._undo_stack.count()
    toolbar.btn_pixel_perfect.click()
    wait(500)
    check("涂在元素内部：外框一个都没变",
          list(viewer._frames) == frames_now, f"frames={list(viewer._frames)}")
    check("没变化就不留一条空撤销（历史上不该多出「重贴紧」这个动作）",
          win._undo_stack.count() == undo_now,
          f"before={undo_now} after={win._undo_stack.count()}")
    check("但会给一句提示（不能静悄悄什么都不做）", canvas._toast.active() is True,
          f"toast={canvas._toast.text()!r}")

    print("--- 15. 新元素的阈值口径，必须和「识别」那套一模一样 ---")
    # 检测器（_detect_frames_image）的口径是 `max(w, h) > min_area` —— 比**长边**、而且是
    # **严格大于**。重贴紧认新元素必须照抄这套，不然同一个"阈值 20"在两处含义不同：
    # 识别时不算、退出完美像素又算了（我第一版这里写成了"面积 >= 阈值"，正好错在这）。
    settings.set_detect_min_area(20)
    toolbar.btn_pixel_perfect.click()
    wait(200)
    viewer.zoom_actual_pixel()
    viewer._v_toolbar.btn_brush.setChecked(True)
    viewer.set_paint_color(QColor("#FF00FF"))
    viewer.set_brush_size(20)
    viewer.sync_paint_colors()
    wait(120)
    click(win, image_to_canvas(viewer, 20, 60))     # 20×20：长边 == 阈值 → 不算
    wait(200)
    toolbar.btn_pixel_perfect.click()
    wait(500)
    check("20×20 那块（长边正好等于阈值 20）**不算**新元素",
          not any(x <= 20 < x + w and y <= 60 < y + h for x, y, w, h in viewer._frames),
          f"frames={list(viewer._frames)}")

    toolbar.btn_pixel_perfect.click()
    wait(200)
    viewer.zoom_actual_pixel()
    viewer._v_toolbar.btn_brush.setChecked(True)
    viewer.set_brush_size(21)
    viewer.sync_paint_colors()
    wait(120)
    click(win, image_to_canvas(viewer, 45, 30))     # 21×21：长边 > 阈值 → 算
    wait(200)
    toolbar.btn_pixel_perfect.click()
    wait(500)
    check("21×21 那块（长边比阈值大 1）**算**新元素，追加在最后",
          list(viewer._frames)[-1] == (35, 20, 21, 21), f"frames={list(viewer._frames)}")

    print("--- 16. 切标签页时「在途」的重贴紧，不许落到新文档上 ---")
    # 重贴紧是后台线程算的，而它现在会**追加新元素**：如果上一份画面的连通域落到
    # 换过图的新文档上，新文档就会凭空多出一堆"新元素"（切标签页正好会撞上）。
    # 这条不需要真的抢到那个时间窗：即使线程先跑完了，断言也照样成立（老文档自己改自己）。
    toolbar.btn_pixel_perfect.click()
    wait(200)
    viewer.zoom_actual_pixel()
    viewer.refit_frames()                       # 起一次重贴紧（线程在飞）
    win._new_tab()                              # 立刻切走
    wait(150)
    # 阈值调回 4：不然夹具里 20×20 的 A 会被"长边要 > 20"这条挡掉，只剩 B 一个元素
    settings.set_detect_min_area(4)
    win._on_image_loaded(pil_to_pixmap(sheet()))
    wait(700)                                   # 等那趟在途的重贴紧跑完
    check("新标签页只按自己的图识别元素（没被上一份画面的重贴紧塞进外来元素）",
          list(viewer._frames) == [(10, 10, 20, 20), (60, 40, 30, 20)],
          f"frames={list(viewer._frames)}")

    print("--- 17. 设置里「恢复默认颜色」能一键回到出厂色 ---")
    settings.reset_view_colors()
    check("普通标线回到青色", settings.guide_color().name().upper() == "#00FFFF",
          f"got={settings.guide_color().name()}")
    check("选中标线回到主题色", settings.guide_selected_color().name().upper() == "#E91E63",
          f"got={settings.guide_selected_color().name()}")
    check("像素网格回到半透灰（alpha 不是 255）",
          settings.pixel_grid_color().name().upper() == "#808080"
          and settings.pixel_grid_color().alpha() == 128,
          f"got={settings.pixel_grid_color().name(QColor.NameFormat.HexArgb)}")
    check("门槛默认 400%", settings.pixel_grid_zoom() == 400,
          f"got={settings.pixel_grid_zoom()}")

    print("--- 18. 收尾 ---")
    viewer.set_guides([])
    restore_settings()                 # 原样写回用户的设置（幂等，atexit 也会兜一次）
    viewer.reload_view_colors()
    win.close()

    # Qt 收尾：headless 下让解释器直接回收原生对象/后台线程会随机 0xC0000005
    # （检测线程、QPixmap 缓存等）。顺序与 smoke_test.py 一致：先跑几轮事件，
    # 再 deleteLater 让 Qt 真正析构，最后 quit 并再跑一轮事件。
    for _ in range(3):
        _APP.processEvents()
    win.deleteLater()
    _APP.processEvents()
    _APP.quit()
    _APP.processEvents()
    del win
    wait(400)
    print()
    print("=" * 52)
    print(f"PASSED {passed}  |  FAILED {len(FAILURES)}")
    if FAILURES:
        print("Failures:")
        for name in FAILURES:
            print(f"  - {name}")
    else:
        print("PIXEL PERFECT / SHRINK STEPS OK")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())