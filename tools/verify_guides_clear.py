#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""回归测试：右键「清空选中区域」+ 标线（从标尺拖出来的参考线）。

钉死的约定：
- 右键菜单里「清空选中区域」在**「忽略选中区域」上方**（用户指定），选中 >= 1 个元素时可用；
- 清空 = 把选中元素那几块像素**真的擦成透明**（合成图 + 每张原图都要擦），并且顺手把这些
  元素标成「已忽略」（内容都没了，不该再当元素用）；`_frames` 本身不动（索引是全局的）；
- 清空只动选中的那几个元素：没选中的一个像素都不许变；
- 清空 = 一条全局撤销，Ctrl+Z 一次把像素和状态全退回（快照存的是 QPixmap 引用，
  所以擦必须**换新对象**，绝不能就地改原件）；
- 标线：上标尺往下拖 = 水平标线（按 y），左标尺往右拖 = 竖直标线（按 x）；
  松手在画布外（含拖回标尺）= 删掉这条线；画布上点中它就能拖、Del 能删；
- 标线只是**参考线**：不改 `_pixmap`、不改元素、不进导出，且**跟着标签页走**；
- 有工具开着（画笔/橡皮/吸管/原图选择）时画布不接标线这一下点击 —— 不能抢用户干活。

全程 offscreen；QApplication 必须挂模块级。

Usage: .venv\\Scripts\\python.exe tools\\verify_guides_clear.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QColor, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication

from spritesheet import settings
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


def sheet() -> Image.Image:
    """透明底 + 三个方块：自动检测会认出 3 个元素（位置好算，便于逐像素断言）。

    底色必须是**透明**的：不透明的白底会把三个方块连成一整块，
    检测出来就只有一个元素（第一次写这个 fixture 时就是这么翻车的）。
    """
    img = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for x in (20, 90, 160):
        draw.rectangle((x, 30, x + 20, 60), fill=(200, 40, 40, 255))
    return img


def ring_sheet() -> Image.Image:
    """一个空心大框（元素 1，它的外接框**套住**里面），框里再放一个小方块（元素 2）。

    专门用来验"清空选中区域只能擦选中的那个"：大框被选中要擦时，
    框里那块没被选中的小元素必须一个像素都不动（挖洞保下来）。
    """
    img = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    band = 8
    draw.rectangle((10, 10, 100, 100), outline=(60, 160, 220, 255), width=band)
    draw.rectangle((45, 45, 65, 65), fill=(220, 60, 60, 255))
    return img


def block_sheet() -> Image.Image:
    """只有一个实心方块的图：用来验"擦空之后虚线框消失"。"""
    img = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle((50, 50, 110, 110), fill=(200, 40, 40, 255))
    return img


def gray_box_pixels(viewer, canvas, index: int) -> int:
    """数一数这个元素的框线上有多少个"中灰"像素（#808080 那圈虚线）。

    棋盘格底是 #2a2a2a / #1e1e1e、界面底 #262626，离中灰都很远，
    所以"贴近 (128,128,128)"基本只可能是元素框本身（含多动作的彩色框不在这个范围）。
    """
    fx, fy, fw, fh = viewer._frames[index]
    zoom = viewer._zoom or 1.0
    image = canvas.grab().toImage()
    left = int(viewer._offset.x() + fx * zoom)
    top = int(viewer._offset.y() + fy * zoom)
    right = int(viewer._offset.x() + (fx + fw) * zoom)
    bottom = int(viewer._offset.y() + (fy + fh) * zoom)
    count = 0
    for x in range(max(0, left + 1), min(image.width() - 1, right)):
        for y in (top, bottom):
            if 0 <= y < image.height():
                count += _is_mid_gray(QColor(image.pixelColor(x, y)))
    for y in range(max(0, top + 1), min(image.height() - 1, bottom)):
        for x in (left, right):
            if 0 <= x < image.width():
                count += _is_mid_gray(QColor(image.pixelColor(x, y)))
    return count


def _is_mid_gray(color: QColor) -> bool:
    return (abs(color.red() - 128) <= 20 and abs(color.green() - 128) <= 20
            and abs(color.blue() - 128) <= 20)


def _around(grab, center: QPointF, size: int = 26) -> list[QColor]:
    image = grab.toImage()
    cx, cy = int(center.x()), int(center.y())
    colors = []
    for x in range(max(0, cx - size), min(image.width(), cx + size)):
        for y in range(max(0, cy - size), min(image.height(), cy + size)):
            colors.append(QColor(image.pixelColor(x, y)))
    return colors


def dark_pixels_around(grab, center: QPointF) -> int:
    """提示小药丸的深色底（#181818 一带）。"""
    return sum(1 for c in _around(grab, center)
               if c.red() < 90 and c.green() < 90 and c.blue() < 90)


def light_pixels_around(grab, center: QPointF) -> int:
    """提示里那行浅色小字（#D8D8D8 一带）。"""
    return sum(1 for c in _around(grab, center)
               if c.red() > 170 and c.green() > 170 and c.blue() > 170)


def mouse(kind, point: QPointF, button=Qt.MouseButton.LeftButton,
          buttons=Qt.MouseButton.NoButton) -> QMouseEvent:
    return QMouseEvent(kind, point, point, point, button, buttons,
                       Qt.KeyboardModifier.NoModifier)


def canvas_point(viewer, ix: float, iy: float) -> QPointF:
    zoom = viewer._zoom or 1.0
    return QPointF(viewer._offset.x() + ix * zoom, viewer._offset.y() + iy * zoom)


def drag_guide_from_ruler(win, orientation: str, from_point: QPoint, to_point: QPointF,
                          release_inside: bool = True) -> None:
    """在标尺上按下 → 拖到画布上的某点 → 松手。

    事件里的坐标必须是**接收控件自己的局部坐标**（真实事件就是这样的，Qt 也是这么送的）：
    所以"画布局部坐标"要先换算成"标尺局部坐标"再塞进事件里，
    否则 `mapToGlobal` 会再叠一次控件偏移、落点就偏了（第一版就是这么错的）。
    """
    viewer = win.image_viewer
    ruler = viewer.h_ruler if orientation == "horizontal" else viewer.v_ruler
    # 画布局部坐标 → 全局 → 标尺局部。**刻意不用 ruler.mapFrom(canvas, pos)**：
    # 实测在这套控件层级下它算出来的偏移是错的（画布局部 (270,324) 被算成标尺局部
    # (54,215)，正确值是 (270,338)），显式走一遍 mapToGlobal/mapFromGlobal 才准。
    target_local = QPointF(ruler.mapFromGlobal(
        viewer.canvas.mapToGlobal(QPointF(to_point).toPoint())))
    ruler.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, QPointF(from_point)))
    ruler.mouseMoveEvent(mouse(QEvent.Type.MouseMove, target_local,
                               buttons=Qt.MouseButton.LeftButton))
    # release_inside=False 模拟"拖回标尺上"：松手点落回标尺自己身上
    end = target_local if release_inside else QPointF(from_point)
    ruler.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, end))


def main() -> int:
    win = MainWindow()
    win.resize(1280, 860)
    win.show()
    wait(250)
    viewer = win.image_viewer
    canvas = viewer.canvas

    win._on_image_loaded(pil_to_pixmap(sheet()))
    wait(600)
    check("(准备) 自动认出 3 个元素", len(viewer._frames) == 3, f"frames={viewer._frames}")
    win._on_add_action_clicked()
    wait(250)
    action = win.actions[0] if win.actions else None
    check("(准备) 动作建出来了", action is not None)
    if action is None:
        return 1

    print("--- 1. 右键菜单：清空选中区域在忽略选中区域**上方** ---")
    action.selected_indices = []
    menu, items = canvas.build_context_menu()
    labels = [a.text() for a in menu.actions() if not a.isSeparator()]
    check("菜单里有「清空选中区域」", "清空选中区域" in labels, f"labels={labels}")
    check("它就排在「忽略选中区域」正上方",
          labels.index("清空选中区域") + 1 == labels.index("忽略选中区域"),
          f"labels={labels}")
    check("没选中元素时两项都禁用",
          not items["clear_area"].isEnabled() and not items["ignore"].isEnabled())
    action.selected_indices = [0]
    menu2, items2 = canvas.build_context_menu()
    check("选中 1 个元素后两项都可用",
          items2["clear_area"].isEnabled() and items2["ignore"].isEnabled())

    print("--- 2. 清空选中区域：真把像素擦成透明（合成图 + 原图）---")
    fx, fy, fw, fh = viewer._frames[0]
    box_x, box_y = fx + fw // 2, fy + fh // 2          # 第 1 个元素内部（红方块）
    other_x = viewer._frames[1][0] + 10                 # 第 2 个元素内部（不该被动）
    check("(准备) 清空前第 1 块是不透明红",
          pixel(viewer._pixmap, box_x, box_y).alpha() == 255,
          f"got={pixel(viewer._pixmap, box_x, box_y).name()}")
    viewer.menu_clear_selected_regions()
    wait(250)
    check("合成图上第 1 块的像素被擦成透明",
          pixel(viewer._pixmap, box_x, box_y).alpha() == 0,
          f"got={pixel(viewer._pixmap, box_x, box_y).name()}/a{pixel(viewer._pixmap, box_x, box_y).alpha()}")
    check("每张原图上同样被擦掉（不然一重拼又回来）",
          pixel(viewer._source_images[0], box_x, box_y).alpha() == 0,
          f"got=a{pixel(viewer._source_images[0], box_x, box_y).alpha()}")
    check("没选中的第 2 块一个像素没动",
          pixel(viewer._pixmap, other_x, box_y).alpha() == 255,
          f"got=a{pixel(viewer._pixmap, other_x, box_y).alpha()}")
    check("擦掉的元素同时被标成「已忽略」", 0 in viewer._ignored_frames,
          f"ignored={sorted(viewer._ignored_frames)}")
    check("擦掉后它不再挂在动作的选中集里", 0 not in action.selected_indices,
          f"selected={action.selected_indices}")
    check("_frames 不动（索引是全局的，删条目会让后面的元素全部重排）",
          len(viewer._frames) == 3, f"frames={len(viewer._frames)}")

    print("--- 3. 一条撤销能把像素和状态整个退回 ---")
    win._undo_stack.undo()
    wait(350)
    check("撤销后第 1 块的像素回来了",
          pixel(viewer._pixmap, box_x, box_y).alpha() == 255,
          f"got=a{pixel(viewer._pixmap, box_x, box_y).alpha()}")
    check("撤销后原图也回到有内容",
          pixel(viewer._source_images[0], box_x, box_y).alpha() == 255,
          f"got=a{pixel(viewer._source_images[0], box_x, box_y).alpha()}")
    check("撤销后不再算「已忽略」", 0 not in viewer._ignored_frames,
          f"ignored={sorted(viewer._ignored_frames)}")

    print("--- 4. 标尺拖出标线：上标尺 → 水平线，左标尺 → 竖直线 ---")
    check("(准备) 一开始没有标线", viewer.guides() == [])
    drag_guide_from_ruler(win, "horizontal", QPoint(120, 8),
                          canvas_point(viewer, 100, 45))
    wait(120)
    check("上标尺拖出一条标线", len(viewer.guides()) == 1, f"guides={viewer.guides()}")
    guide = viewer.guides()[0] if viewer.guides() else None
    check("它是**水平**标线（按 y 定位）", guide is not None and guide[0] == "h",
          f"guide={guide}")
    check("落点 y 对得上（拖到图片 y=45）", guide is not None and abs(guide[1] - 45) <= 1,
          f"guide={guide}")
    check("拖出来的那条就是选中的那条", viewer.selected_guide() is guide)

    v_target = canvas_point(viewer, 100, 45)
    drag_guide_from_ruler(win, "vertical", QPoint(8, 300), v_target)
    wait(120)
    check("左标尺拖出第二条标线", len(viewer.guides()) == 2, f"guides={viewer.guides()}")
    check("它是**竖直**标线（按 x 定位）",
          viewer.guides()[1][0] == "v" and abs(viewer.guides()[1][1] - 100) <= 1,
          f"guide={viewer.guides()[1]}")

    print("--- 5. 拖回标尺上 = 删掉这条线 ---")
    count_before = len(viewer.guides())
    drag_guide_from_ruler(win, "horizontal", QPoint(300, 8), QPoint(320, 8),
                          release_inside=False)
    wait(120)
    check("在标尺上松手没有留下标线", len(viewer.guides()) == count_before,
          f"guides={viewer.guides()}")
    # 把已有的那条水平标线拖出画布（往上拖到画布外的标尺那一侧）：应当被删掉。
    # 注意取点时 x 要避开竖直标线（竖线在 x=100）：否则 1px 判定会先抓到竖线。
    h_guide = [g for g in viewer.guides() if g[0] == "h"][0]
    canvas.mousePressEvent(mouse(QEvent.Type.MouseButtonPress,
                                 canvas_point(viewer, 60, h_guide[1])))
    check("抓住的是那条水平标线（不是旁边的竖线）", viewer.selected_guide() is h_guide,
          f"selected={viewer.selected_guide()}")
    canvas.mouseMoveEvent(mouse(QEvent.Type.MouseMove, QPointF(60, -8),
                                buttons=Qt.MouseButton.LeftButton))
    canvas.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, QPointF(60, -8)))
    wait(120)
    check("把已有的标线拖到画布外松手 → 这条线被删掉",
          h_guide not in viewer.guides(), f"guides={viewer.guides()}")

    print("--- 6. 画布上：点中 → 拖 → Del 删除 ---")
    v_guide = [g for g in viewer.guides() if g[0] == "v"][0]
    canvas.mousePressEvent(mouse(QEvent.Type.MouseButtonPress,
                                 canvas_point(viewer, v_guide[1], 45)))
    check("点中竖直标线 = 选中它", viewer.selected_guide() is v_guide)
    canvas.mouseMoveEvent(mouse(QEvent.Type.MouseMove, canvas_point(viewer, 150, 45),
                                buttons=Qt.MouseButton.LeftButton))
    wait(80)
    canvas.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease,
                                   canvas_point(viewer, 150, 45)))
    wait(80)
    check("拖到了新的 x=150", abs(v_guide[1] - 150) <= 1, f"guide={v_guide}")
    canvas.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete,
                                   Qt.KeyboardModifier.NoModifier))
    wait(80)
    check("按 Del 删掉了选中的标线", v_guide not in viewer.guides(),
          f"guides={viewer.guides()}")
    check("删掉后没有残留的选中项", viewer.selected_guide() is None)

    print("--- 7. 标线只是参考线：不动图、不进导出、跟着标签页 ---")
    pixmap_before = viewer._pixmap
    frames_before = list(viewer._frames)
    drag_guide_from_ruler(win, "horizontal", QPoint(150, 8),
                          canvas_point(viewer, 60, 20))
    wait(120)
    check("加标线不动合成图对象", viewer._pixmap is pixmap_before)
    check("加标线不动元素列表", viewer._frames == frames_before)
    check("标线不进已忽略那套状态（它不是元素）", viewer._ignored_frames == set())
    zoom_now = viewer._zoom or 1.0
    guide_y = round(viewer._offset.y() + viewer.guides()[0][1] * zoom_now)

    def guide_row() -> list[str]:
        """标线所在那一横排的颜色（只扫图片横向范围内）。"""
        grabbed = canvas.grab().toImage()
        return [QColor(grabbed.pixelColor(x, guide_y)).name().upper()
                for x in range(max(0, int(viewer._offset.x())),
                               min(grabbed.width(),
                                   int(viewer._offset.x() + 200 * zoom_now)))]

    row = guide_row()
    # 期望颜色**从设置里读**，不写死默认值：颜色现在可以在「软件设置」里自己调
    # （绿标线、橙选中都是合法配置），写死默认色的断言会在用户改过颜色之后无故变红 ——
    # 这里要验的是"选中的那条用了设置里的'选中色'、其余用了设置里的'普通色'"这条接线。
    want_sel = settings.guide_selected_color().name().upper()
    want_norm = settings.guide_color().name().upper()
    check(f"选中的标线画成设置里的选中色 {want_sel}", want_sel in row, f"row={sorted(set(row))}")
    viewer.select_guide(None)
    wait(80)
    row2 = guide_row()
    check(f"没选中时画成设置里的普通色 {want_norm}（一眼看出选中的是哪条）",
          want_norm in row2, f"row={sorted(set(row2))}")

    win._new_tab(switch=True)          # 新标签页：标线各是各的
    wait(250)
    check("切到新标签页：标线是空的", viewer.guides() == [], f"guides={viewer.guides()}")
    win._switch_tab(0)                 # 切回原标签页：标线该跟着标签页各是各的
    wait(300)
    check("切回原标签页：标线回来了", len(viewer.guides()) == 1,
          f"guides={viewer.guides()}")

    print("--- 8. 有工具开着时，画布不接标线那一下（不抢用户干活）---")
    check("没工具时标线可编辑", viewer.can_edit_guides())
    win._set_active_tool("brush")
    wait(80)
    check("画笔开着时标线不可编辑", not viewer.can_edit_guides())
    guide = viewer.guides()[0]
    hit = canvas_point(viewer, 60, guide[1])
    before_guide_pos = list(guide)
    canvas.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, hit))
    canvas.mouseMoveEvent(mouse(QEvent.Type.MouseMove, QPointF(hit.x(), hit.y() + 40),
                                buttons=Qt.MouseButton.LeftButton))
    canvas.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease,
                                   QPointF(hit.x(), hit.y() + 40)))
    wait(150)
    check("画笔开着时点标线 = 涂了一笔（标线没被拖走）",
          guide == before_guide_pos, f"guide={guide}")
    win._set_active_tool(None)
    wait(80)

    print("--- 9. 没图时标尺拖不出标线（不能留一条量不了任何东西的线）---")
    win._new_tab(switch=True)
    wait(300)
    check("(准备) 新标签页没有图", viewer._pixmap is None)
    drag_guide_from_ruler(win, "horizontal", QPoint(150, 8), QPointF(150, 200))
    wait(120)
    check("没图时拖不出标线", viewer.guides() == [], f"guides={viewer.guides()}")

    print("--- 10. 清空只擦「选中的」：框里没被选中的小元素不能被连坐 ---")
    win._new_tab(switch=True)
    wait(300)
    win._on_image_loaded(pil_to_pixmap(ring_sheet()))
    wait(600)
    check("(准备) 认出 2 个元素：外框 + 框里的小块", len(viewer._frames) == 2,
          f"frames={viewer._frames}")
    if len(viewer._frames) != 2:
        return 1
    # 按面积分清谁套谁（不依赖检测顺序）
    outer_index = max(range(2), key=lambda i: viewer._frames[i][2] * viewer._frames[i][3])
    inner_index = 1 - outer_index
    outer = QRect(*[int(v) for v in viewer._frames[outer_index]])
    inner = QRect(*[int(v) for v in viewer._frames[inner_index]])
    check("(准备) 外框那个元素的框真的套住了里面那个", outer.contains(inner),
          f"outer={outer} inner={inner}")
    win._on_add_action_clicked()
    wait(250)
    action = win.actions[0]
    action.selected_indices = [outer_index]
    stroke = (outer.left() + 3, outer.top() + 3)          # 外框笔画上
    inside = (inner.center().x(), inner.center().y())     # 框里那个小元素身上
    check("(准备) 外框笔画是不透明的",
          pixel(viewer._pixmap, *stroke).alpha() == 255)
    check("(准备) 框里那块小元素也是不透明的",
          pixel(viewer._pixmap, *inside).alpha() == 255)
    viewer.menu_clear_selected_regions()
    wait(250)
    check("选中的外框被擦掉了", pixel(viewer._pixmap, *stroke).alpha() == 0,
          f"got=a{pixel(viewer._pixmap, *stroke).alpha()}")
    check("**没选中**、只是正好落在它框里的小元素一个像素没动",
          pixel(viewer._pixmap, *inside).alpha() == 255,
          f"got=a{pixel(viewer._pixmap, *inside).alpha()}")
    check("小元素也没被算成已忽略", inner_index not in viewer._ignored_frames,
          f"ignored={sorted(viewer._ignored_frames)}")
    check("原图上小元素同样没被动",
          pixel(viewer._source_images[0], *inside).alpha() == 255,
          f"got=a{pixel(viewer._source_images[0], *inside).alpha()}")
    check("被清空的那个元素：框不再画（哪怕框里还留着邻居的像素）",
          viewer.frame_box_hidden(outer_index))
    win._undo_stack.undo()
    wait(350)
    check("撤销后外框笔画回来了", pixel(viewer._pixmap, *stroke).alpha() == 255,
          f"got=a{pixel(viewer._pixmap, *stroke).alpha()}")
    check("撤销后那个元素又开始画框了", not viewer.frame_box_hidden(outer_index))

    print("--- 11. 内容被擦空就不画虚线框（橡皮擦路径）---")
    win._new_tab(switch=True)
    wait(300)
    win._on_image_loaded(pil_to_pixmap(block_sheet()))
    wait(600)
    check("(准备) 只认出 1 个元素", len(viewer._frames) == 1,
          f"frames={viewer._frames}")
    fx, fy, fw, fh = viewer._frames[0]
    check("(准备) 擦之前是画着框的", gray_box_pixels(viewer, canvas, 0) > 4,
          f"gray={gray_box_pixels(viewer, canvas, 0)}")
    win._set_active_tool("eraser")
    viewer.set_brush_size(160)                     # 一笔盖住整个方块
    wait(80)
    start = canvas_point(viewer, fx + 2, fy + 2)
    canvas.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, start))
    canvas.mouseMoveEvent(mouse(QEvent.Type.MouseMove,
                                canvas_point(viewer, fx + fw - 2, fy + fh - 2),
                                buttons=Qt.MouseButton.LeftButton))
    canvas.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease,
                                   canvas_point(viewer, fx + fw - 2, fy + fh - 2)))
    wait(250)
    win._set_active_tool(None)
    wait(120)
    check("橡皮擦把这块擦空了", 0 in viewer.empty_frames(),
          f"empty={sorted(viewer.empty_frames())}")
    check("擦空之后虚线框不再出现", viewer.frame_box_hidden(0))
    check("画布上真的没有那个灰框了（逐像素扫框线）",
          gray_box_pixels(viewer, canvas, 0) == 0,
          f"gray={gray_box_pixels(viewer, canvas, 0)}")
    check("擦出来的地方是透明的（不是涂白）",
          pixel(viewer._pixmap, fx + fw // 2, fy + fh // 2).alpha() == 0,
          f"got={pixel(viewer._pixmap, fx + fw // 2, fy + fh // 2).name()}")
    win._undo_stack.undo()                          # 撤销这一笔：内容回来，框也得回来
    wait(350)
    check("撤销那一笔之后虚线框又回来了（证明是「看内容」而不是一律不画）",
          gray_box_pixels(viewer, canvas, 0) > 4,
          f"gray={gray_box_pixels(viewer, canvas, 0)}")

    print("--- 12. 「已忽略」小字提示（点到 / 框到时出现在区域正中间）---")
    win._new_tab(switch=True)
    wait(300)
    win._on_image_loaded(pil_to_pixmap(sheet()))
    wait(600)
    win._on_add_action_clicked()
    wait(250)
    action = win.actions[0]
    action.selected_indices = [0]
    viewer.menu_ignore_selected_regions()
    wait(250)
    check("(准备) 0 号元素已被忽略", 0 in viewer._ignored_frames,
          f"ignored={sorted(viewer._ignored_frames)}")
    check("(准备) 一开始没有小字提示", viewer.ignored_hint_frames() == set())
    gx, gy, gw, gh = viewer._frames[0]
    center = canvas_point(viewer, gx + gw / 2, gy + gh / 2)
    before_center = pixel(viewer.canvas.grab(), center.x(), center.y())
    check("(准备) 忽略不动像素：中间还是原来那块颜色", before_center.alpha() == 255,
          f"got={before_center.name()}")
    canvas.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, center))
    wait(150)
    check("点一下被忽略的这块：它出现「已忽略」提示",
          viewer.ignored_hint_frames() == {0}, f"hint={viewer.ignored_hint_frames()}")
    dark = dark_pixels_around(viewer.canvas.grab(), center)
    check("画布正中间真的画出了深色提示底", dark > 20, f"dark={dark}")
    light = light_pixels_around(viewer.canvas.grab(), center)
    check("提示里有浅色小字（字真的画出来了）", light > 8, f"light={light}")
    # 点别的地方（没被忽略的元素）→ 提示收掉
    ox, oy, ow, oh = viewer._frames[1]
    other = canvas_point(viewer, ox + ow / 2, oy + oh / 2)
    canvas.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, center))
    canvas.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, other))
    wait(150)
    check("点到没被忽略的元素：提示收掉", viewer.ignored_hint_frames() == set(),
          f"hint={viewer.ignored_hint_frames()}")
    check("提示收掉后那块又露出原图内容",
          pixel(viewer.canvas.grab(), center.x(), center.y()) == before_center)
    # 框选：从图外起手，框过被忽略的那块 → 也显示提示
    # 终点要真的**框进去**（落到元素中间），不能正好停在元素角上：
    # 选框换算成图坐标会四舍五入，停在角上可能只差 1px 就判成"没框到"
    canvas.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, other))
    board = QPointF(viewer.canvas.width() - 8, viewer.canvas.height() - 8)
    canvas.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, board))
    canvas.mouseMoveEvent(mouse(QEvent.Type.MouseMove, center,
                                buttons=Qt.MouseButton.LeftButton))
    wait(150)
    check("框选过程中框到它就显示提示",
          viewer.ignored_hint_frames() == {0}, f"hint={viewer.ignored_hint_frames()}")
    canvas.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, center))
    wait(150)
    check("松手后提示还在（这一框的结果看得见）",
          viewer.ignored_hint_frames() == {0}, f"hint={viewer.ignored_hint_frames()}")
    canvas.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                   Qt.KeyboardModifier.NoModifier))
    wait(120)
    check("Esc 收掉提示", viewer.ignored_hint_frames() == set(),
          f"hint={viewer.ignored_hint_frames()}")

    print("--- 13. 收尾 ---")
    win.close()
    wait(400)
    print()
    print("=" * 52)
    print(f"PASSED {passed}  |  FAILED {len(FAILURES)}")
    if FAILURES:
        print("Failures:")
        for name in FAILURES:
            print(f"  - {name}")
    else:
        print("GUIDES / CLEAR REGIONS OK")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())