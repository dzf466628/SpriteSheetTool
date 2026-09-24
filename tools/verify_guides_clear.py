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
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication

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
    check("选中的标线画成主题色 #E91E63", "#E91E63" in row, f"row={sorted(set(row))}")
    viewer.select_guide(None)
    wait(80)
    row2 = guide_row()
    check("没选中时画成青色 #00FFFF（一眼看出选中的是哪条）",
          "#00FFFF" in row2, f"row={sorted(set(row2))}")

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

    print("--- 10. 收尾 ---")
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