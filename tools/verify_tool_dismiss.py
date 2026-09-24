"""回归：「按住空格/其他键要关（让位）画笔」+ Esc 关工具 + 点画布外自动收工具。

用户口径（原话）："按住空格键或者其他按键要关闭画笔工具"。补充问的口径是：
  1. 按住期间画笔**临时让位**、松开就回来（不是永久关掉）—— 像 PS 按住空格变手形；
  2. 「其他按键」= Esc 也顺手关掉当前工具 + 点画布以外的界面（竖条空白处/菜单栏/动作列表…）
     自动收工具；**Ctrl / Alt 也让位**（用户明确勾了这个，我原本不建议）；
  3. Esc 的优先级：先取消「选中的东西」（框选/标线/已忽略提示），都取消完了才收工具。

顺带把两个真问题一起钉住：
  - **空格会被聚焦的按钮当成「点击自己」吃掉**（QPushButton 的默认行为），
    所以平移状态改成由 app 级过滤器先截下来（焦点在哪个按钮上都管用）；
  - 让位期间**不画笔刷圈**：圈还在就等于告诉用户「这一下会画」，而其实这一下是平移/合成/复用。

全程 offscreen，不读也不改用户的软件设置。
Usage: .venv\\Scripts\\python.exe -X utf8 tools\\verify_tool_dismiss.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from PyQt6.QtCore import QElapsedTimer, QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QLineEdit

from spritesheet.ui.key_panel import pil_to_pixmap
from spritesheet.ui.main_window import MainWindow

# QApplication **必须挂在模块级**（不是 main() 里）：这是本项目记过的坑 ——
# 放到函数里、或者干脆不建，真机上收尾会 0xC0000409；而不建 QApplication 就建 QWidget
# 会直接 abort，**连一句 print 都留不下来**（我这次就先踩了：跑出来 0 行输出 + 退出码
# -1073740791，查了半天）。挂在模块级 = 解释器还活着的时候它就活着。
_APP = QApplication.instance() or QApplication(sys.argv)

PASSED = 0
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [OK]   {label}")
    else:
        FAILURES.append(label)
        print(f"  [FAIL] {label}   {detail}")


def wait(ms: int = 120) -> None:
    timer = QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        QApplication.processEvents()


def solid(color, size=(60, 40)) -> Image.Image:
    return Image.new("RGBA", size, color + (255,))


def pixel(pm, x: int, y: int) -> QColor:
    return QColor(pm.toImage().pixelColor(x, y))


def image_to_canvas(viewer, ix: float, iy: float) -> QPointF:
    zoom = viewer._zoom or 1.0
    return QPointF(viewer._offset.x() + ix * zoom, viewer._offset.y() + iy * zoom)


def press_on(widget, pos: QPoint | None = None,
             mods=Qt.KeyboardModifier.NoModifier) -> None:
    """把一次鼠标左键按下**投递给指定控件**（走 app 级过滤器，与真实点击同一条路）。

    刻意不用 canvas.mousePressEvent(...) 直接调：那样会绕开事件过滤器，
    而「点画布外收工具」这件事恰恰是在过滤器里判的。
    """
    pos = pos if pos is not None else QPoint(4, 4)
    event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(pos),
                        QPointF(widget.mapToGlobal(pos)),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, mods)
    QApplication.sendEvent(widget, event)


def release_on(widget, pos: QPoint | None = None) -> None:
    """补一个松开：只按下不松开会在画布上留下一条「还按着」的笔画状态。"""
    pos = pos if pos is not None else QPoint(4, 4)
    event = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(pos),
                        QPointF(widget.mapToGlobal(pos)),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def key_to(widget, key, kind=QEvent.Type.KeyPress, text: str = "") -> None:
    """把按键投递给指定控件（同样走 app 级过滤器）。"""
    QApplication.sendEvent(widget, QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier, text))


def drag(win, points, mods=Qt.KeyboardModifier.NoModifier) -> None:
    """画布上按住 → 拖过若干点 → 松开（真实 mousePress/Move/Release）。"""
    canvas = win.image_viewer.canvas
    start = points[0]
    canvas.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, start, start, start,
                                       Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, mods))
    for point in points[1:]:
        canvas.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, point, point, point,
                                          Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, mods))
    last = points[-1]
    canvas.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, last, last, last,
                                         Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, mods))


def bright_pixels(pm, cx: int, cy: int, radius: int = 22) -> int:
    """笔刷圈是「黑圈 + 白圈」：数一数附近够亮的像素就知道圈画没画。"""
    img = pm.toImage()
    n = 0
    for y in range(max(0, cy - radius), min(img.height(), cy + radius + 1)):
        for x in range(max(0, cx - radius), min(img.width(), cx + radius + 1)):
            c = QColor(img.pixelColor(x, y))
            if c.red() > 200 and c.green() > 200 and c.blue() > 200:
                n += 1
    return n


def main() -> int:
    win = MainWindow()
    win.resize(1200, 800)
    win.show()
    wait(200)
    viewer = win.image_viewer
    canvas = viewer.canvas
    bar = viewer._v_toolbar

    print("--- 准备：一张纯色原图 + 画笔 ---")
    win._on_image_loaded(pil_to_pixmap(solid((40, 120, 200))))
    wait(400)
    check("原图载入", viewer._pixmap is not None and not viewer._pixmap.isNull())
    check("认出 1 个元素", len(viewer._frames) == 1, f"frames={viewer._frames}")
    viewer.set_paint_color(QColor("#FF0000"))
    viewer.set_brush_size(6)
    viewer.sync_paint_colors()
    bar.btn_brush.setChecked(True)
    wait(80)
    check("画笔开着", viewer.paint_tool == "brush" and viewer.painting is True)

    print("--- 1. 让位判断：空格 / Ctrl / Alt 期间画笔不接活 ---")
    check("没按任何键时不让位", viewer.paint_yielded() is False)
    canvas.set_pan_mode(True)
    check("按住空格（平移中）→ 让位", viewer.paint_yielded() is True)
    canvas.set_pan_mode(False)
    check("松开空格 → 回到画笔（临时让位，不是关掉）",
          viewer.paint_yielded() is False and viewer.paint_tool == "brush")
    check("Ctrl 按着 → 让位（用户勾选的：Ctrl/Alt 也让位）",
          viewer.paint_yielded(Qt.KeyboardModifier.ControlModifier) is True)
    check("Alt 按着 → 让位", viewer.paint_yielded(Qt.KeyboardModifier.AltModifier) is True)
    check("Shift 按着**不**让位（Shift 没有别的画布手势）",
          viewer.paint_yielded(Qt.KeyboardModifier.ShiftModifier) is False)

    print("--- 2. 让位是真的不落笔（不是只改了个标志） ---")
    # 注意：按住空格那次拖动会**真的挪画布**，所以每次拖动前都要按当前缩放/偏移重新算
    # 画布坐标（拿老坐标去拖会落到图外，断言就变成"因为没点到所以没涂"的空断言）
    canvas.set_pan_mode(True)
    drag(win, [image_to_canvas(viewer, 10, 20), image_to_canvas(viewer, 50, 20)])
    wait(80)
    check("按住空格拖动：原图没被涂（这一下是挪画布）",
          pixel(viewer._source_images[0], 30, 20).name().upper() != "#FF0000",
          f"got={pixel(viewer._source_images[0], 30, 20).name()}")
    canvas.set_pan_mode(False)
    drag(win, [image_to_canvas(viewer, 10, 20), image_to_canvas(viewer, 50, 20)],
         Qt.KeyboardModifier.ControlModifier)
    wait(80)
    check("Ctrl+拖动：原图没被涂（Ctrl 那一下归合成帧）",
          pixel(viewer._source_images[0], 30, 20).name().upper() != "#FF0000",
          f"got={pixel(viewer._source_images[0], 30, 20).name()}")
    drag(win, [image_to_canvas(viewer, 10, 20), image_to_canvas(viewer, 50, 20)],
         Qt.KeyboardModifier.AltModifier)
    wait(80)
    check("Alt+拖动：原图没被涂（Alt 那一下归复用帧）",
          pixel(viewer._source_images[0], 30, 20).name().upper() != "#FF0000",
          f"got={pixel(viewer._source_images[0], 30, 20).name()}")
    drag(win, [image_to_canvas(viewer, 10, 20), image_to_canvas(viewer, 50, 20)])
    wait(120)
    check("松开按键后画笔照常落色（临时让位，说明画笔真的回来了）",
          pixel(viewer._source_images[0], 30, 20).name().upper() == "#FF0000",
          f"got={pixel(viewer._source_images[0], 30, 20).name()}")

    print("--- 3. 让位期间不画笔刷圈（圈还在=骗用户「这一下会画」） ---")
    # 取画布正中：上面拖动挪过画布，按图片坐标算的点可能已经跑到画布外了
    center = canvas.rect().center().toPointF()
    viewer._brush_pos = center
    canvas.update()
    wait(60)
    normal_ring = bright_pixels(canvas.grab(), int(center.x()), int(center.y()))
    canvas.set_pan_mode(True)
    canvas.update()
    wait(60)
    yielded_ring = bright_pixels(canvas.grab(), int(center.x()), int(center.y()))
    canvas.set_pan_mode(False)
    check("画笔圈平时画得出来（否则下面那条是空断言）", normal_ring > 0, f"n={normal_ring}")
    check("按住空格时圈收掉", yielded_ring == 0, f"n={yielded_ring}")
    viewer._brush_pos = None

    print("--- 4. 空格不会被聚焦的按钮吃掉 ---")
    bar.btn_brush.setFocus()
    wait(40)
    checked_before = bar.btn_brush.isChecked()
    key_to(bar.btn_brush, Qt.Key.Key_Space, QEvent.Type.KeyPress)
    check("焦点在画笔按钮上按空格：平移状态照样开起来（按钮抢不走）",
          canvas.is_panning() is True)
    check("这一下空格没被当成「点击按钮」（画笔还亮着、没被按掉）",
          bar.btn_brush.isChecked() == checked_before and viewer.paint_tool == "brush")
    key_to(bar.btn_brush, Qt.Key.Key_Space, QEvent.Type.KeyRelease)
    check("松开空格：平移状态关掉", canvas.is_panning() is False)

    print("--- 5. 在能打字的地方空格留给打字 ---")
    edit = QLineEdit(win)
    edit.show()
    edit.setFocus()
    key_to(edit, Qt.Key.Key_Space, QEvent.Type.KeyPress, " ")
    check("输入框里按空格：不进平移状态", canvas.is_panning() is False)
    check("空格真的打进了输入框（没被过滤器吞掉）", edit.text() == " ",
          f"text={edit.text()!r}")
    edit.deleteLater()

    print("--- 6. Esc 收工具，但排在「取消选中」后面 ---")
    wait(60)
    check("画笔还开着（准备按 Esc）", viewer.paint_tool == "brush")
    key_to(canvas, Qt.Key.Key_Escape)
    wait(80)
    check("Esc 把工具收掉了", viewer.paint_tool is None)
    check("竖条上的按钮也跟着弹起（按钮和工具不能分裂）",
          bar.btn_brush.isChecked() is False)
    check("状态栏说了发生了什么", "收起" in win.lbl_status.text(),
          f"status={win.lbl_status.text()}")
    check("工具选项条跟着收掉", viewer._paint_bar.isVisible() is False)

    bar.btn_source.setChecked(True)
    wait(80)
    viewer.set_selected_sources([0])
    wait(40)
    check("原图选择开着且有选中（准备验优先级）",
          viewer._source_tool is True and viewer.selected_sources() == [0])
    key_to(canvas, Qt.Key.Key_Escape)
    wait(60)
    check("第一次 Esc 只取消选中，工具还开着",
          viewer.selected_sources() == [] and viewer._source_tool is True)
    key_to(canvas, Qt.Key.Key_Escape)
    wait(60)
    check("第二次 Esc 才把工具收掉",
          viewer._source_tool is False and bar.btn_source.isChecked() is False)

    bar.btn_key.setChecked(True)
    wait(200)
    check("抠除背景面板开着（准备按 Esc）", bar.btn_key.isChecked() is True)
    key_to(canvas, Qt.Key.Key_Escape)
    wait(150)
    check("Esc 也收「抠除背景」：按钮弹起", bar.btn_key.isChecked() is False)

    status_before = win.lbl_status.text()
    key_to(canvas, Qt.Key.Key_Escape)
    wait(60)
    check("手上没工具时 Esc 什么也不做（不报错、不改状态栏）",
          win.lbl_status.text() == status_before and viewer.paint_tool is None,
          f"status={win.lbl_status.text()}")

    print("--- 7. 点画布外自动收工具；画笔这条工作流里的控件不收 ---")
    bar.btn_brush.setChecked(True)
    wait(80)
    press_on(win.anim_bar)
    wait(60)
    check("点「动画设置」横条 → 收掉画笔", viewer.paint_tool is None)

    bar.btn_brush.setChecked(True)
    wait(60)
    press_on(win.preview)
    wait(60)
    check("点右侧预览 → 收掉画笔", viewer.paint_tool is None)

    bar.btn_brush.setChecked(True)
    wait(60)
    press_on(viewer._paint_bar)
    wait(60)
    check("点画笔工具选项条（粗细/颜色）→ **不收**（改个笔宽不能把画笔弄丢）",
          viewer.paint_tool == "brush")

    bar.btn_brush.setChecked(True)
    wait(60)
    press_on(viewer._toolbar)
    wait(60)
    check("点右上角浮动工具条（缩放/背景）→ 不收", viewer.paint_tool == "brush")

    bar.btn_brush.setChecked(True)
    wait(60)
    press_on(canvas, QPoint(30, 30))
    release_on(canvas, QPoint(30, 30))
    wait(60)
    check("点画布本身 → 当然不收", viewer.paint_tool == "brush")

    bar.btn_brush.setChecked(True)
    wait(60)
    press_on(bar)                      # 竖条的空白处（不是按钮）
    wait(60)
    check("点左侧竖条的空白处 → 收掉画笔", viewer.paint_tool is None)

    bar.btn_brush.setChecked(True)
    wait(60)
    press_on(bar.btn_eraser)           # 竖条上的另一个工具按钮（按下 + 松开才算点了一下）
    release_on(bar.btn_eraser)
    wait(100)
    check("点竖条上的橡皮按钮 → 正常切工具（不是「收掉」）",
          viewer.paint_tool == "eraser" and bar.btn_eraser.isChecked() is True)

    print("--- 8. 吸管也遵守让位（Ctrl+点击不该变成取色） ---")
    bar.btn_pick.setChecked(True)
    wait(80)
    check("吸管开着", viewer.paint_tool == "pick")
    color_before = QColor(viewer.paint_color)
    drag(win, [image_to_canvas(viewer, 30, 20)], Qt.KeyboardModifier.ControlModifier)
    wait(80)
    check("Ctrl+点吸管：没取色（Ctrl 那一下归合成帧）",
          QColor(viewer.paint_color).name().upper() == color_before.name().upper(),
          f"before={color_before.name()} after={QColor(viewer.paint_color).name()}")
    # 取**(5,5)**这个点的色：它是没被涂过的原色，取了必然和前景色不同
    # （如果去取 (30,20)，那里第 2 步已经被涂成前景色了，取了"看起来没变"，断言就是假的）
    drag(win, [image_to_canvas(viewer, 5, 5)])
    wait(80)
    check("松开 Ctrl 后吸管正常取到色（让位是临时的）",
          QColor(viewer.paint_color).name().upper() == "#2878C8",
          f"after={QColor(viewer.paint_color).name()}")

    print()
    win.close()
    wait(300)
    print("=" * 60)
    print(f"PASSED {PASSED}  |  FAILED {len(FAILURES)}")
    if FAILURES:
        print("Failures:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("TOOL DISMISS OK")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())