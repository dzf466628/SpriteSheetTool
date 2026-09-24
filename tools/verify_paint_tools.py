#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""回归测试：左侧原图工具栏的画笔 / 橡皮擦 / 吸管 / 颜色选择（PS 同款四件套）。

钉死的约定：
- 单工具模式：画笔/橡皮/吸管/原图选择/抠图 同一时刻只有一个亮着（选了画笔要自动弹掉原图选择）；
- 画笔：在图上按住左键拖 = 沿路径涂前景色，颜色/粗细立刻生效；
- 橡皮擦：把像素**擦成透明**（不是涂白 —— 精灵图要的是透明）；
- 吸管：取图上那一点的颜色写进前景色（取完保持吸管模式，方便连着取）；
- 颜色选择：前景/背景两个色块分别可点，D 恢复黑白、X 前后景互换（PS 同款）；
- 一笔 = 一条全局撤销：Ctrl+Z 要能整笔退回涂之前（这是最容易写坏的一处：
  笔画必须落在原图的**深拷贝**上，直接改原对象会把撤销快照里那份"之前"一起改掉）；
- 画笔开着时 Ctrl+点击 / Alt+点击 仍归合成/复用（用户刚验收过那套逻辑，不能被抢）。

全程 offscreen；QApplication 必须挂模块级（放函数里真机收尾会 0xC0000409，
见 skills/dudu-ai-dev-kit/references/pitfalls.md）。

Usage: .venv\\Scripts\\python.exe tools\\verify_paint_tools.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw
from PyQt6.QtCore import QEvent, QPointF, QRect, Qt
from PyQt6.QtGui import QColor, QMouseEvent
from PyQt6.QtWidgets import QApplication

from spritesheet.ui.key_panel import pil_to_pixmap
from spritesheet.ui.main_window import MainWindow
from spritesheet.ui.widgets import make_tool_icon

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


def solid(color, size=(60, 40)) -> Image.Image:
    return Image.new("RGBA", size, color + (255,))


def pixel(pm, x: int, y: int) -> QColor:
    return QColor(pm.toImage().pixelColor(x, y))


def drag(win, points, mods=Qt.KeyboardModifier.NoModifier) -> None:
    """在画布上按下 → 拖过若干点 → 松开（走真实的 mousePress/Move/Release）。"""
    canvas = win.image_viewer.canvas
    start = points[0]
    press = QMouseEvent(QEvent.Type.MouseButtonPress, start, start, start,
                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, mods)
    canvas.mousePressEvent(press)
    for point in points[1:]:
        move = QMouseEvent(QEvent.Type.MouseMove, point, point, point,
                           Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, mods)
        canvas.mouseMoveEvent(move)
    last = points[-1]
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, last, last, last,
                          Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, mods)
    canvas.mouseReleaseEvent(release)


def image_to_canvas(viewer, ix: float, iy: float) -> QPointF:
    zoom = viewer._zoom or 1.0
    return QPointF(viewer._offset.x() + ix * zoom, viewer._offset.y() + iy * zoom)


def main() -> int:
    win = MainWindow()
    win.resize(1200, 800)
    win.show()
    wait(200)
    viewer = win.image_viewer
    bar = viewer._v_toolbar

    print("--- 准备：一张纯色原图 ---")
    win._on_image_loaded(pil_to_pixmap(solid((40, 120, 200))))
    wait(300)
    check("原图载入", viewer._pixmap is not None and not viewer._pixmap.isNull())

    print("--- 1. 单工具模式（互斥）：选画笔要弹掉原图选择 ---")
    bar.btn_source.setChecked(True)
    wait(60)
    check("原图选择先亮起来", viewer._source_tool is True)
    bar.btn_brush.setChecked(True)
    wait(60)
    check("画笔亮着", bar.btn_brush.isChecked() and viewer.painting is True)
    check("原图选择已自动弹掉（不串台）", bar.btn_source.isChecked() is False
          and viewer._source_tool is False)
    check("工具选项条出现了（粗细 + 颜色）", viewer._paint_bar.isVisible())
    check("状态栏说了怎么用", "画笔" in win.lbl_status.text(), f"status={win.lbl_status.text()}")

    print("--- 2. 画笔：拖一笔要沿路径落色，且粗细/颜色立刻生效 ---")
    viewer.set_paint_color(QColor("#FF0000"))
    viewer.set_brush_size(10)
    viewer.sync_paint_colors()
    src_before = viewer._source_images[0]
    a = image_to_canvas(viewer, 15, 20)
    b = image_to_canvas(viewer, 45, 20)
    drag(win, [a, b])
    wait(150)
    after = viewer._source_images[0]
    check("原图换成了新的对象（不原地改原对象）", after is not src_before)
    check("起点被涂成前景色", pixel(after, 15, 20).name().upper() == "#FF0000",
          f"got={pixel(after, 15, 20).name()}")
    check("终点也被涂到（真的是沿路径画）", pixel(after, 45, 20).name().upper() == "#FF0000",
          f"got={pixel(after, 45, 20).name()}")
    check("笔宽生效：路径上下 4px 内仍有色",
          pixel(after, 30, 24).name().upper() == "#FF0000",
          f"got={pixel(after, 30, 24).name()}")
    check("离路径够远的地方没被涂（12px 外）",
          pixel(after, 30, 33).name().upper() != "#FF0000",
          f"got={pixel(after, 30, 33).name()}")
    check("合成图上也看得到（不用等松手）",
          pixel(viewer._pixmap, 30, 20).name().upper() == "#FF0000",
          f"got={pixel(viewer._pixmap, 30, 20).name()}")
    check("变动记进了全局撤销（Ctrl+Z 能撤）", win._undo_stack.count() >= 1,
          f"count={win._undo_stack.count()}")

    print("--- 3. 一笔 = 一条撤销：Ctrl+Z 要整笔退回涂之前 ---")
    win._do_undo()
    wait(250)
    back = viewer._source_images[0]
    check("撤销后回到原图（起点恢复原色）",
          pixel(back, 15, 20).name().upper() == "#2878C8",
          f"got={pixel(back, 15, 20).name()}")
    check("撤销后终点也恢复了", pixel(back, 45, 20).name().upper() == "#2878C8",
          f"got={pixel(back, 45, 20).name()}")
    win._do_redo()
    wait(250)
    again = viewer._source_images[0]
    check("重做把这一笔又画回来了", pixel(again, 15, 20).name().upper() == "#FF0000",
          f"got={pixel(again, 15, 20).name()}")

    print("--- 4. 橡皮擦：擦成透明（不是涂白）---")
    bar.btn_eraser.setChecked(True)
    wait(60)
    check("橡皮擦亮着", bar.btn_eraser.isChecked() and viewer.paint_tool == "eraser")
    check("画笔按钮已弹掉", bar.btn_brush.isChecked() is False)
    check("工具选项条还在（橡皮也有粗细）", viewer._paint_bar.isVisible())
    viewer.set_brush_size(12)
    drag(win, [image_to_canvas(viewer, 15, 20), image_to_canvas(viewer, 30, 20)])
    wait(150)
    erased = viewer._source_images[0]
    check("擦过的地方 alpha = 0（真透明）", pixel(erased, 20, 20).alpha() == 0,
          f"alpha={pixel(erased, 20, 20).alpha()}")
    check("擦的是像素内容、不是涂成白色",
          pixel(erased, 20, 20).name().upper() != "#FFFFFF",
          f"got={pixel(erased, 20, 20).name()}")
    check("没擦到的地方还在", pixel(erased, 45, 20).alpha() == 255,
          f"alpha={pixel(erased, 45, 20).alpha()}")

    print("--- 5. 吸管：取色写进前景色，且保持吸管模式 ---")
    bar.btn_pick.setChecked(True)
    wait(60)
    check("吸管亮着", bar.btn_pick.isChecked() and viewer.paint_tool == "pick")
    check("橡皮按钮已弹掉", bar.btn_eraser.isChecked() is False)
    check("吸管没有参数条（不用显粗细）", not viewer._paint_bar.isVisible())
    # 取"没被涂过"的那块颜色（原色 #2878C8）
    drag(win, [image_to_canvas(viewer, 50, 30)], Qt.KeyboardModifier.NoModifier)
    wait(120)
    check("前景色 = 点到的颜色", viewer.paint_color.name().upper() == "#2878C8",
          f"got={viewer.paint_color.name()}")
    check("取完仍是吸管模式（方便连着取）", viewer.paint_tool == "pick")

    print("--- 6. 颜色选择：D 恢复黑白、X 前后景互换（PS 同款）---")
    win._reset_paint_colors()
    check("D → 前景黑", viewer.paint_color.name().upper() == "#000000",
          f"got={viewer.paint_color.name()}")
    check("D → 背景白", viewer.paint_bg_color.name().upper() == "#FFFFFF",
          f"got={viewer.paint_bg_color.name()}")
    win._swap_paint_colors()
    check("X → 互换后前景白", viewer.paint_color.name().upper() == "#FFFFFF",
          f"got={viewer.paint_color.name()}")
    check("X → 互换后背景黑", viewer.paint_bg_color.name().upper() == "#000000",
          f"got={viewer.paint_bg_color.name()}")
    check("色块控件上的颜色跟着变",
          bar.btn_color.colors()[0].name().upper() == "#FFFFFF"
          and bar.btn_color.colors()[1].name().upper() == "#000000",
          f"swatch={bar.btn_color.colors()}")

    print("--- 7. 快捷键：B / E / I / [ ] ---")
    win._shortcut_tool("brush")
    wait(60)
    check("B → 画笔", viewer.paint_tool == "brush" and bar.btn_brush.isChecked())
    win._shortcut_tool("eraser")
    wait(60)
    check("E → 橡皮擦", viewer.paint_tool == "eraser" and bar.btn_eraser.isChecked())
    win._shortcut_tool("pick")
    wait(60)
    check("I → 吸管", viewer.paint_tool == "pick" and bar.btn_pick.isChecked())
    viewer.set_brush_size(20)
    win._nudge_brush_size(-1)
    check("[ → 变细（20 → 15）", viewer.brush_size == 15, f"size={viewer.brush_size}")
    win._nudge_brush_size(1)
    check("] → 变粗（15 → 20）", viewer.brush_size == 20, f"size={viewer.brush_size}")
    win._shortcut_tool("pick")             # 同一个键再按一次 = 关掉
    wait(60)
    check("再按 I → 工具关掉", viewer.paint_tool is None and not bar.btn_pick.isChecked())

    print("--- 8. 画笔开着时 Ctrl/Alt+点击 仍归合成/复用（不抢既有手势）---")
    win._on_add_action_clicked()
    wait(250)
    action = win.actions[0] if win.actions else None
    check("动作建出来了", action is not None)
    if action is not None:
        frames = list(action.frame_parts)
        win._set_active_tool("brush")
        wait(60)
        # 原图上点一下元素：Ctrl+点击 应该是"合成一帧"（不是涂一笔）
        src_before = viewer._source_images[0]
        viewer._frames = [(0, 0, 60, 40)]
        viewer._frame_logical = {}
        center = image_to_canvas(viewer, 30, 20)
        drag(win, [center], Qt.KeyboardModifier.ControlModifier)
        wait(200)
        check("Ctrl+点击 没被画笔抢走（原图没被涂改）",
              viewer._source_images[0] is src_before
              or pixel(viewer._source_images[0], 30, 20).name().upper() != viewer.paint_color.name().upper(),
              f"paint={viewer.paint_color.name()} got={pixel(viewer._source_images[0], 30, 20).name()}")
        check("Ctrl+点击 正常合成了帧（frame_parts 有内容）",
              len(action.frame_parts) > len(frames), f"frames={len(action.frame_parts)}")

    print("--- 9. 没图时点工具：弹回去 + 说明原因（不做死按钮）---")
    # 直接清空画布状态而不是走 _new_project()：后者会开新工程页（那套标签页逻辑
    # 不归本脚本管），这里要验的只是"没图时工具点不下去"这一条守卫
    viewer._pixmap = None
    viewer._source_images = []
    viewer._source_offsets = []
    viewer.canvas.update()
    wait(60)
    win._set_active_tool(None)      # 先把工具关掉，否则按钮本来就亮着，点它等于没点
    wait(60)
    bar.btn_brush.setChecked(True)
    wait(60)
    check("没图时画笔弹回去", bar.btn_brush.isChecked() is False)
    check("状态栏说明了原因", "先添加图片" in win.lbl_status.text(),
          f"status={win.lbl_status.text()}")

    print("--- 10. 竖条按钮：三个新工具在列、可勾选、图标不是空图 ---")
    names = [key for key, _ in bar.tool_buttons()]
    check("工具顺序：原图选择 → 抠图 → 画笔 → 橡皮 → 吸管",
          names == ["source", "key", "brush", "eraser", "pick"], f"names={names}")
    check("三个新按钮都是可勾选（有开关态）",
          all(b.isCheckable() for b in (bar.btn_brush, bar.btn_eraser, bar.btn_pick)))
    icons_dir = Path(__file__).resolve().parents[1] / "icons"
    for kind in ("brush", "eraser", "eyedropper"):
        check(f"icons/{kind}.svg 存在（缺了就退化成占位方框）",
              (icons_dir / f"{kind}.svg").exists())
    for kind, button in (("brush", bar.btn_brush), ("eraser", bar.btn_eraser),
                         ("eyedropper", bar.btn_pick)):
        icon = make_tool_icon(kind, 16, "#FFFFFF")
        pm = icon.pixmap(16, 16)
        image = pm.toImage()
        ink = sum(1 for y in range(image.height()) for x in range(image.width())
                  if image.pixelColor(x, y).alpha() > 30)
        # 有墨但不糊成一坨：判"图标真的画出来了"，而不是全透明/整块实心
        check(f"{kind} 图标有笔画（墨点 {ink}/256）", 8 <= ink <= 200, f"ink={ink}")

    print("--- 11. 色块控件：前块画前景色、露出的一角画背景色 ---")
    swatch = bar.btn_color
    swatch.set_colors(QColor("#E91E63"), QColor("#1E88E5"))
    wait(60)
    shot = swatch.grab().toImage()
    side = max(8, round(swatch.width() * 0.62))
    front_px = QColor(shot.pixelColor(3, 3))                     # 前块（左上）
    back_px = QColor(shot.pixelColor(swatch.width() - 3, swatch.width() - 3))  # 后块露出的右下角
    check("前块 = 前景色", front_px.name().upper() == "#e91e63".upper(),
          f"got={front_px.name()}")
    check("右下角 = 背景色（两色块都看得见）", back_px.name().upper() == "#1e88e5".upper(),
          f"got={back_px.name()} side={side}")

    print("--- 12. 参数条与状态同步：粗细控件、显隐 ---")
    viewer.set_brush_size(42)
    viewer._paint_bar.set_brush_size(42)
    check("粗细控件跟着变", viewer._paint_bar.spin_size.value() == 42,
          f"value={viewer._paint_bar.spin_size.value()}")
    win._nudge_brush_size(1)
    check("] 之后粗细控件也同步（不是只有内部变量变）",
          viewer._paint_bar.spin_size.value() == viewer.brush_size == 47,
          f"spin={viewer._paint_bar.spin_size.value()} size={viewer.brush_size}")

    print("--- 13. 笔刷圈：鼠标悬停时画出笔有多粗的圆圈 ---")
    # 第 9 节把图清掉了（画布上什么都没有时不画笔刷圈，那是从 _pixmap 那一大块里画的），
    # 这里先补一张图回来
    win._on_image_loaded(pil_to_pixmap(solid((40, 120, 200))))
    wait(250)
    win._set_active_tool("brush")
    wait(60)
    viewer.set_brush_size(20)
    center = image_to_canvas(viewer, 30, 20)
    viewer._brush_pos = center
    viewer.canvas.repaint()
    canvas_shot = viewer.canvas.grab().toImage()
    cx, cy = int(center.x()), int(center.y())
    # 圈的半径 = 粗细 × 缩放 ÷ 2（笔粗是**图片像素**，屏幕上要乘缩放 —— 缩放不是 1，
    # 画布默认按"适应窗口"自动缩放，所以这里必须按当前 zoom 算）
    radius = int(round(viewer.brush_size * (viewer._zoom or 1.0) / 2.0))

    def ring_hits(image) -> int:
        found = 0
        for dx, dy in ((radius, 0), (-radius, 0), (0, radius), (0, -radius)):
            # 圆是 1px 抗锯齿描边，采样点可能正好落在半格上：取该点周围 3×3 的最亮值
            best = max(QColor(image.pixelColor(cx + dx + ox, cy + dy + oy)).lightness()
                       for ox in (-1, 0, 1) for oy in (-1, 0, 1))
            if best > 150:
                found += 1
        return found

    check(f"悬停处能看到白色笔刷圈（半径 {radius}px，4 个方向至少 3 个命中）",
          ring_hits(canvas_shot) >= 3, f"ring_hits={ring_hits(canvas_shot)}")
    viewer._brush_pos = None
    viewer.canvas.repaint()
    after_shot = viewer.canvas.grab().toImage()
    check("鼠标移开（_brush_pos 清空）后圈就没了", ring_hits(after_shot) == 0,
          f"ring_hits={ring_hits(after_shot)}")

    print("--- 14. 切到空白标签页：工具要弹回去（不然点画布会弹出添加图片的文件框）---")
    win._set_active_tool("brush")
    wait(60)
    check("切之前的准备工作：画笔亮着且有图",
          bar.btn_brush.isChecked() and viewer.painting and viewer._pixmap is not None)
    win._new_tab(switch=True)          # 空白文档：_restore_document 会把 _pixmap 装成 None
    wait(250)
    check("切到空白页后画布确实空了", viewer._pixmap is None)
    check("画笔自动弹回去（工具状态跟着文档校验）",
          bar.btn_brush.isChecked() is False and viewer.paint_tool is None,
          f"checked={bar.btn_brush.isChecked()} tool={viewer.paint_tool}")
    check("工具选项条也收起来了", not viewer._paint_bar.isVisible())

    print("--- 15. 与抠除背景按钮互斥（只验入口接线；抠图算法本身不在此重测）---")
    win._switch_tab(0)                 # 切回那张有图的文档
    wait(300)
    check("回到有图的文档", viewer._pixmap is not None and not viewer._pixmap.isNull())
    win._set_active_tool("brush")      # 先点亮画笔
    wait(60)
    bar.btn_key.setChecked(True)       # 再点抠图：应当抢走"当前工具"的位置
    wait(300)
    panel = getattr(win, "_key_panel", None)
    check("点抠图：参数浮层开了", panel is not None and panel.isVisible())
    check("画笔被弹掉（两个工具不共存）",
          viewer.paint_tool is None and bar.btn_brush.isChecked() is False,
          f"tool={viewer.paint_tool} brush_checked={bar.btn_brush.isChecked()}")
    check("画笔参数条也收起来了", not viewer._paint_bar.isVisible())
    bar.btn_key.setChecked(False)      # 再点一下 = 关掉
    wait(300)
    check("再点一下：浮层关了、按钮弹回",
          panel is not None and not panel.isVisible() and bar.btn_key.isChecked() is False)
    check("关掉后回到没有工具的状态（状态栏清空）", win.lbl_status.text() == "",
          f"status={win.lbl_status.text()}")

    print("--- 16. 参数条摆位：窄画布下不许压在右上角浮动工具条上 ---")
    win.resize(1000, 1000)         # 默认（最小）窗口：去掉左列后画布只剩四百来像素
    wait(300)
    win._set_active_tool("brush")
    wait(250)
    canvas = viewer.canvas
    bar_rect = QRect(viewer._paint_bar.pos(), viewer._paint_bar.size())
    top_bar = viewer._toolbar
    top_rect = QRect(top_bar.pos(), top_bar.size())
    v_bar = viewer._v_toolbar
    v_rect = QRect(v_bar.pos(), v_bar.size())
    check(f"参数条与右上浮动条不重叠（画布 {canvas.width()}px 宽）",
          not bar_rect.intersects(top_rect),
          f"bar={bar_rect.getRect()} top={top_rect.getRect()} canvas_w={canvas.width()}")
    horizontally_same_band = bar_rect.right() > top_rect.left()
    check("横向本来就撞上时，参数条让到了浮动条下面一行（不是硬挤）",
          (not horizontally_same_band) or bar_rect.top() > top_rect.bottom(),
          f"bar_top={bar_rect.top()} top_bottom={top_rect.bottom()} 同带={horizontally_same_band}")
    check("参数条不压在左侧竖条上", bar_rect.left() >= v_rect.right() + 1,
          f"bar_left={bar_rect.left()} v_right={v_rect.right()}")
    check("参数条整条都在画布内", bar_rect.right() <= canvas.width()
          and bar_rect.bottom() <= canvas.height(),
          f"bar={bar_rect.getRect()} canvas={canvas.width()}x{canvas.height()}")

    print("--- 17. 抠图结果不许被画笔/橡皮还回来（用户报的 bug）---")
    # 开一个干净文档再载图：这样图上就这一张、坐标可预测（往已有文档里再载一张会
    # 变成"拼接"到下面，按图片坐标算的落笔点就对不上了）
    win._new_tab(switch=True)
    wait(300)
    white_bg = Image.new("RGBA", (120, 90), (255, 255, 255, 255))
    ImageDraw.Draw(white_bg).rectangle((30, 25, 90, 65), fill=(30, 90, 200, 255))
    win._on_image_loaded(pil_to_pixmap(white_bg))
    wait(400)
    check("(准备) 只有一张原图", len(viewer._source_images) == 1,
          f"n={len(viewer._source_images)}")
    check("(准备) 底色是不透明白", pixel(viewer._pixmap, 5, 5).alpha() == 255)
    check("(准备) 蓝方块在图片坐标 (60,45) 上",
          pixel(viewer._pixmap, 60, 45).name().upper() == "#1E5AC8".upper(),
          f"got={pixel(viewer._pixmap, 60, 45).name()}")
    win._set_active_tool("key")            # 打开抠图
    wait(700)
    panel = win._key_panel
    check("抠图面板开着", panel is not None and panel.isVisible())
    panel._on_apply()                      # 走真实的「应用」通路
    wait(700)
    bg = pixel(viewer._pixmap, 5, 5)
    check("抠图应用后：合成图底色透明", bg.alpha() == 0, f"got={bg.name()}/a{bg.alpha()}")
    src_bg = pixel(viewer._source_images[0], 5, 5)
    check("抠图应用后：每张原图也同步抠过了（不同步的话一重拼就还回来）",
          src_bg.alpha() == 0, f"got={src_bg.name()}/a{src_bg.alpha()}")

    win._set_active_tool("brush")
    wait(150)
    viewer.set_paint_color(QColor("#FF0000"))
    viewer.set_brush_size(6)
    drag(win, [image_to_canvas(viewer, 60, 45), image_to_canvas(viewer, 78, 45)])
    wait(400)
    after = pixel(viewer._pixmap, 5, 5)
    check("画完一笔：抠掉的底色没被还回来", after.alpha() == 0,
          f"got={after.name()}/a{after.alpha()}")
    check("画的那一笔在（红色）",
          pixel(viewer._pixmap, 60, 45).name().upper() == "#FF0000",
          f"got={pixel(viewer._pixmap, 60, 45).name()}")

    viewer._recompose_sources()            # "拖动原图/画布长大"走的就是这条重拼
    wait(150)
    again = pixel(viewer._pixmap, 5, 5)
    check("按原图重拼之后底色仍是透明（拖动原图也不会把抠图还回来）",
          again.alpha() == 0, f"got={again.name()}/a{again.alpha()}")

    win._undo_stack.undo()                 # 撤这一笔（全局栈顶）
    wait(350)
    check("撤销这一笔：底色依旧透明（撤的是笔画，不是抠图）",
          pixel(viewer._pixmap, 5, 5).alpha() == 0,
          f"got={pixel(viewer._pixmap, 5, 5).name()}")
    win._undo_stack.undo()                 # 再撤一步：抠图本身
    wait(350)
    check("再撤一步：底色回来了（抠图仍然可以整个撤销）",
          pixel(viewer._pixmap, 5, 5).alpha() == 255,
          f"got={pixel(viewer._pixmap, 5, 5).name()}/a{pixel(viewer._pixmap, 5, 5).alpha()}")

    print()
    print("=" * 52)
    print(f"PASSED {passed}  |  FAILED {len(FAILURES)}")
    # 收尾等一会儿：元素检测是后台线程（_FrameDetectThread），脚本退出时它还在跑的话
    # 解释器关闭阶段会直接 0xC0000409（症状是"全部 PASS 但退出码不是 0"）。
    # 这是本项目的老坑，见 skills/dudu-ai-dev-kit/references/pitfalls.md。
    wait(600)
    # 显式收尾：趁 QApplication 还活着，先把窗口和它挂着的浮动控件（抠图浮层、参数条、
    # 竖条/横条）析构掉。不这么做的话，关掉抠图浮层的用例偶尔会在解释器关闭阶段
    # 变成 0xC0000005（症状同样是"全部 PASS 但退出码看着像失败"，实测 3 次里中 2 次）。
    win.close()
    panel = getattr(win, "_key_panel", None)
    if panel is not None:
        panel.setParent(None)
    win.image_viewer._paint_bar.setParent(None)
    win.setParent(None)
    win.deleteLater()
    for _ in range(8):
        _APP.processEvents()
    if FAILURES:
        print("Failures:", *FAILURES, sep="\n  - ")
        return 1
    print("PAINT TOOLS OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())