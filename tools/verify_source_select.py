"""「原图选择」工具回归：点选 / Ctrl 追加 / 框选 / Esc / 高亮 / 与抠图面板打通。

为什么单独一个脚本：这个工具的价值全在"点哪选哪、选了几张、选中的到底被处理了没有"，
这些用像素与状态判定最靠谱（肉眼看不出一张图是"选中了但没生效"还是"根本没选中"）。
全程 offscreen 可跑。

素材与 ``verify_key_panel.py`` 同一套自造图：三张底色各异（蓝 / 洋红 / 绿）的原图，
上下叠成一张大图，彼此之间有 16px 透明间隙 —— 间隙是天然的"点空白"测试点。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication

from spritesheet.ui.key_panel import pil_to_pixmap
from spritesheet.ui.main_window import MainWindow

# QApplication 挂模块级：放函数里真机收尾会 0xC0000409（offscreen 看不出来）
_APP = QApplication.instance() or QApplication(sys.argv)

BG = [(40, 120, 200), (200, 40, 200), (60, 200, 90)]
SPRITE = [(200, 40, 40), (240, 230, 60), (230, 120, 40)]
W, H, GAP = 60, 50, 16


def make_source(index: int) -> Image.Image:
    """一张原图：纯色底 + 中间一个角色。"""
    img = Image.new("RGBA", (W, H), BG[index] + (255,))
    for y in range(15, 36):
        for x in range(20, 41):
            img.putpixel((x, y), SPRITE[index] + (255,))
    return img


def main() -> int:
    app = _APP
    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  PASS  {name} {detail}")
        else:
            failed += 1
            print(f"  FAIL  {name} {detail}")

    def px(pixmap, x: int, y: int) -> tuple:
        c = pixmap.toImage().pixelColor(x, y)
        return (c.red(), c.green(), c.blue(), c.alpha())

    win = MainWindow()
    win.resize(1400, 900)
    win.show()
    for _ in range(6):
        app.processEvents()
    viewer = win.image_viewer
    canvas = viewer.canvas

    def wait(ms: int = 300) -> None:
        for _ in range(max(1, ms // 50)):
            app.processEvents()
            app.thread().msleep(50)

    def mouse(kind, point, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        canvas.mousePressEvent(QMouseEvent(
            QEvent.Type.MouseButtonPress, point, point, Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton, modifiers))
        if kind == "click":
            canvas.mouseReleaseEvent(QMouseEvent(
                QEvent.Type.MouseButtonRelease, point, point, Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton, modifiers))

    def band(from_image, to_image, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        start, end = canvas_point(*from_image), canvas_point(*to_image)
        canvas.mousePressEvent(QMouseEvent(
            QEvent.Type.MouseButtonPress, start, start, Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton, modifiers))
        mid = QPointF((start.x() + end.x()) / 2, (start.y() + end.y()) / 2)
        canvas.mouseMoveEvent(QMouseEvent(
            QEvent.Type.MouseMove, mid, mid, Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, modifiers))
        canvas.mouseReleaseEvent(QMouseEvent(
            QEvent.Type.MouseButtonRelease, end, end, Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton, modifiers))

    print("---- 0. 没有图片 / 只有一张时：按钮不许变成死按钮 ----")
    button = viewer._v_toolbar.btn_source
    check("竖条上有「原图选择」按钮且可勾选", button.isVisible() and button.isCheckable())
    button.setChecked(True)
    wait(150)
    check("没图时按钮自动弹起", not button.isChecked())
    check("没图时说明原因", "先添加图片" in win.lbl_status.text(), f"状态栏={win.lbl_status.text()!r}")

    print("---- 准备：三张底色各异的原图 ----")
    sources = [make_source(i) for i in range(3)]
    win._on_image_loaded(pil_to_pixmap(sources[0]))
    wait(400)
    button.setChecked(True)
    wait(150)
    check("只有一张原图也能开工具（用户要求：不依赖动作、也不依赖张数）",
          button.isChecked() and viewer._source_tool is True)
    check("只有一张时也显示边框（拖它有意义）", viewer.source_borders_visible)
    check("此时确实还没有任何动作（所以这条限制真的撤掉了）", win.actions == [])
    check("单张时它自动成为选中项", viewer.selected_sources() == [0],
          f"选中={viewer.selected_sources()}")
    button.setChecked(False)
    viewer.set_selected_sources([])     # 后面要测"初始没有选中项"，这里清干净
    for pm_src in sources[1:]:
        win._on_image_loaded(pil_to_pixmap(pm_src))
    wait(900)               # 等元素检测跑完：它会刷状态栏，别和后面的提示断言打架
    baked = viewer._pixmap
    rects = viewer.source_rects()
    check("三块位置是拼装算法里那三块",
          rects == [(0, 0, W, H), (0, H + GAP, W, H), (0, 2 * (H + GAP), W, H)], f"实际 {rects}")

    def canvas_point(image_x: float, image_y: float) -> QPointF:
        # 每次读**实时**偏移/缩放：视图在途中变过（fit_view）的话，缓存的旧值会让
        # 测试点跟实际画的位置对不上，测出来的失败是假的
        return QPointF(viewer._offset.x() + image_x * viewer._zoom,
                       viewer._offset.y() + image_y * viewer._zoom)

    print("---- 1. 开工具：边框出现、状态栏有提示 ----")
    button.setChecked(True)
    wait(150)
    check("工具模式已开", viewer._source_tool is True)
    check("分块边框可见", viewer.source_borders_visible)
    check("状态栏给了用法", "原图选择" in win.lbl_status.text(), f"状态栏={win.lbl_status.text()!r}")

    canvas.update()
    for _ in range(4):
        app.processEvents()
    with_borders = canvas.grab().toImage()
    viewer._source_tool = False
    canvas.update()
    for _ in range(4):
        app.processEvents()
    without_borders = canvas.grab().toImage()
    viewer._source_tool = True
    canvas.update()
    diff = sum(1 for y in range(0, min(with_borders.height(), without_borders.height()), 3)
               for x in range(0, min(with_borders.width(), without_borders.width()), 3)
               if with_borders.pixel(x, y) != without_borders.pixel(x, y))
    check("边框真的画在画布上（不是只有状态变了）", diff > 0, f"差异点={diff}")

    print("---- 2. 点选某一张 ----")
    check("初始没有选中项", viewer.selected_sources() == [], f"选中={viewer.selected_sources()}")
    mouse("click", canvas_point(30, rects[1][1] + 25))
    wait(150)
    check("点第 2 块 -> 只选中它", viewer.selected_sources() == [1],
          f"选中={viewer.selected_sources()}")
    check("状态栏说清选了第几张", "第 2 张" in win.lbl_status.text(), f"状态栏={win.lbl_status.text()!r}")
    check("点块没有被当成元素框选", canvas._drag_start is None)

    mouse("click", canvas_point(30, rects[2][1] + 25))
    wait(150)
    check("点第 3 块 -> 换成只选它（独占）", viewer.selected_sources() == [2],
          f"选中={viewer.selected_sources()}")

    print("---- 3. Ctrl 点选 = 追加 ----")
    mouse("click", canvas_point(30, rects[0][1] + 25), Qt.KeyboardModifier.ControlModifier)
    wait(150)
    check("Ctrl 点第 1 块 -> 两张都选中",
          viewer.selected_sources() == [0, 2], f"选中={viewer.selected_sources()}")
    check("状态栏列出张数", "2 张原图" in win.lbl_status.text(), f"状态栏={win.lbl_status.text()!r}")
    mouse("click", canvas_point(30, rects[0][1] + 25), Qt.KeyboardModifier.ControlModifier)
    wait(150)
    check("Ctrl 再点一次 -> 取消那一张", viewer.selected_sources() == [2],
          f"选中={viewer.selected_sources()}")

    print("---- 4. 点空白（块之间的间隙）= 取消选择 ----")
    mouse("click", canvas_point(30, H + 8))
    wait(150)
    check("点间隙 -> 选中清空", viewer.selected_sources() == [], f"选中={viewer.selected_sources()}")
    check("状态栏说明已取消", "取消选择" in win.lbl_status.text(), f"状态栏={win.lbl_status.text()!r}")

    print("---- 5. 框选（必须从棋盘格起手）----")
    # 起手点必须落在棋盘格上：图右侧(x>W)或两块之间的 16px 间隙里
    band((30, H + 8), (30, rects[1][1] + 45))          # 起点在间隙里，零宽
    wait(150)
    check("从棋盘格纵向拖一条（零宽矩形）选得中第 2 块 -> [1]",
          viewer.selected_sources() == [1], f"选中={viewer.selected_sources()}")
    band((W + 10, rects[2][1] + 25), (10, rects[2][1] + 25))   # 起点在图右侧，零高
    wait(150)
    check("从棋盘格横向拖一条（零高矩形）选得中第 3 块 -> [2]",
          viewer.selected_sources() == [2], f"选中={viewer.selected_sources()}")
    band((W + 10, 5), (10, rects[2][1] + 45))
    wait(150)
    check("框住全部 -> 三块都选中", viewer.selected_sources() == [0, 1, 2],
          f"选中={viewer.selected_sources()}")
    mouse("click", canvas_point(30, rects[2][1] + 25))
    check("先只选第 3 块", viewer.selected_sources() == [2],
          f"选中={viewer.selected_sources()}")
    band((W + 10, 5), (10, rects[1][1] + 45), Qt.KeyboardModifier.ControlModifier)
    wait(150)
    check("Ctrl 框选 = 在原选中项上追加",
          viewer.selected_sources() == [0, 1, 2], f"选中={viewer.selected_sources()}")

    print("---- 5b. 从图上起手不是框选（用户定的：棋盘格才框选）----")
    mouse("click", canvas_point(30, rects[0][1] + 25))
    check("先只选第 1 块", viewer.selected_sources() == [0],
          f"选中={viewer.selected_sources()}")
    before_offsets = list(viewer._source_offsets)
    canvas.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, canvas_point(30, 5), canvas_point(30, 5),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    canvas.mouseMoveEvent(QMouseEvent(
        QEvent.Type.MouseMove, canvas_point(30, rects[2][1] + 45),
        canvas_point(30, rects[2][1] + 45), Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    canvas.mouseReleaseEvent(QMouseEvent(
        QEvent.Type.MouseButtonRelease, canvas_point(30, rects[2][1] + 45),
        canvas_point(30, rects[2][1] + 45), Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    wait(300)
    check("图上起手拖开不会框选别的图", viewer.selected_sources() == [0],
          f"选中={viewer.selected_sources()}")
    check("但会把按下时已选中的那张拖走（这是移动，不是框选）",
          viewer._source_offsets != before_offsets,
          f"偏移={viewer._source_offsets}")
    win._do_undo()          # 撤销这次移动，别把后面的几何带歪
    wait(400)
    check("撤销后位置回到原样", viewer._source_offsets == before_offsets,
          f"偏移={viewer._source_offsets}")

    print("---- 6. 拖动中框选框确实画出来了 ----")
    start, end = canvas_point(W + 10, 5), canvas_point(30, rects[1][1] + 40)
    canvas.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, start, start, Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    canvas.mouseMoveEvent(QMouseEvent(
        QEvent.Type.MouseMove, end, end, Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    for _ in range(4):
        app.processEvents()
    check("拖动中记着框选矩形", viewer._band_rect is not None)
    band_image = canvas.grab().toImage()
    canvas.mouseReleaseEvent(QMouseEvent(
        QEvent.Type.MouseButtonRelease, end, end, Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    canvas.update()
    for _ in range(4):
        app.processEvents()
    after_image = canvas.grab().toImage()
    band_diff = sum(1 for y in range(0, min(band_image.height(), after_image.height()), 3)
                    for x in range(0, min(band_image.width(), after_image.width()), 3)
                    if band_image.pixel(x, y) != after_image.pixel(x, y))
    check("框选框在画布上有实际像素（松手后消失）", band_diff > 0, f"差异点={band_diff}")
    check("松手后框选矩形清掉", viewer._band_rect is None)

    print("---- 7. Esc 取消选择 ----")
    mouse("click", canvas_point(30, rects[0][1] + 25))
    check("先选中一块", viewer.selected_sources() == [0])
    canvas.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                   Qt.KeyboardModifier.NoModifier))
    wait(150)
    check("Esc 清空选中", viewer.selected_sources() == [], f"选中={viewer.selected_sources()}")
    check("Esc 之后工具仍然开着（跟画图软件一致）", viewer._source_tool is True)

    print("---- 8. 关工具：边框收起，但选中保留 ----")
    mouse("click", canvas_point(30, rects[1][1] + 25))
    button.setChecked(False)
    wait(200)
    check("工具已关", viewer._source_tool is False)
    check("边框收起", not viewer.source_borders_visible)
    check("选中项保留（关工具不等于取消选择）", viewer.selected_sources() == [1],
          f"选中={viewer.selected_sources()}")

    print("---- 9. 与抠图面板打通：只抠选中的那些 ----")
    viewer._v_toolbar.btn_key.setChecked(True)
    wait(1200)
    panel = win._key_panel
    check("面板出现", panel.isVisible())
    check("面板读的就是画布上的选中项", panel._target_indices() == [1],
          f"目标={panel._target_indices()}")
    check("面板开着时边框也显示（面板借用了同一份状态）", viewer.source_borders_visible)
    preview = viewer._preview_pixmap
    check("预览已生成", preview is not None)
    check("第 2 张底色被清掉", px(preview, 5, rects[1][1] + 5)[3] == 0)
    check("第 1 张底色一个像素没动", px(preview, 5, rects[0][1] + 5) == BG[0] + (255,))
    check("第 3 张底色也没动", px(preview, 5, rects[2][1] + 5) == BG[2] + (255,))
    check("取样色就是第 2 张的底色", panel._bg_color == BG[1], f"实际 {panel._bg_color}")

    print("---- 10. 面板开着时框选多张 -> 每张各自猜色 ----")
    band((W + 10, 5), (10, rects[2][1] + 45))
    wait(1200)
    check("目标变成三块", panel._target_indices() == [0, 1, 2], f"目标={panel._target_indices()}")
    check("多块时取色控件被禁用（不留点了没反应的按钮）",
          not panel.btn_pick.isEnabled() and not panel.btn_guess.isEnabled()
          and not panel.swatch.isEnabled())
    preview = viewer._preview_pixmap
    check("三张底色全被清掉（各自猜色）",
          all(px(preview, 5, rects[i][1] + 5)[3] == 0 for i in range(3)))
    check("三个角色全在",
          all(px(preview, 30, rects[i][1] + 25)[:3] == SPRITE[i] for i in range(3)))
    check("预览期间原图身份没变", viewer._pixmap is baked)

    print("---- 11. 只选一块时取色控件恢复可用 ----")
    mouse("click", canvas_point(30, rects[1][1] + 25))
    wait(1200)
    check("目标收成一块", panel._target_indices() == [1], f"目标={panel._target_indices()}")
    check("取色控件恢复可用", panel.btn_pick.isEnabled() and panel.btn_guess.isEnabled())
    check("取样色跟着换成第 2 张", panel._bg_color == BG[1], f"实际 {panel._bg_color}")

    print("---- 12. 关掉面板：选中保留 ----")
    panel._on_cancel()
    wait(300)
    check("面板收起", not panel.isVisible())
    check("预览清掉", viewer._preview_pixmap is None)
    check("选中项保留", viewer.selected_sources() == [1], f"选中={viewer.selected_sources()}")

    win.close()
    print(f"\nSOURCE SELECT VERIFY {'OK' if not failed else 'FAILED'}  {passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())