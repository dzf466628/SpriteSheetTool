"""「加了图片但还没有动作」时点图片/控件的提示回归。

用户原话：
  「添加图片后 如果没有动作 点击图片或者控件要提示先添加动作」

改之前：有图、没动作时，画布点击、变换模式按钮、合成/复用按钮**全部静默 return**
（数据层确实没地方落：selected_indices / frame_offsets 都挂在动作上），用户看到的
就是"点了毫无反应"。改之后：这些入口都会在自己身上弹出提示气泡。

这个回归走**真入口**（真 MainWindow + QTest 真点击），并顺带验证：
  * 提示文案一致；
  * 提示是**画出来的**（像素有变化），不是只改了个状态；
  * 无动作时变换模式不会被"点亮"（按钮弹回），避免出现"按钮亮着但没手柄"；
  * 加完动作后不再提示，点击恢复正常。

用法: .venv\\Scripts\\python.exe tools\\verify_no_action_hint.py
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


def bottom_pixels(widget) -> list[tuple[int, int, int]]:
    """取控件底部一条（提示气泡所在区域）的像素，用来证明"确实画出来了"。"""
    image = widget.grab().toImage()
    band = max(0, image.height() - 60)
    return [image.pixelColor(x, y).getRgb()[:3]
            for y in range(band, image.height(), 3)
            for x in range(0, image.width(), 3)]


def click_center(widget) -> None:
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton,
                     pos=QPoint(widget.width() // 2, widget.height() // 2))


def main() -> int:
    app = QApplication(sys.argv)
    from spritesheet.ui.theme import APP_QSS

    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)

    from spritesheet.ui.image_viewer import NO_ACTION_HINT as IV_HINT
    from spritesheet.ui.main_window import MainWindow
    from spritesheet.ui.preview import NO_ACTION_HINT as PV_HINT

    win = MainWindow()
    win.resize(1400, 1000)
    win.show()
    app.processEvents()

    canvas = win.image_viewer.canvas
    preview_canvas = win.preview.canvas
    panel = win.preview.adjust_panel

    # 真入口：走 image_loaded 信号的处理函数（等价于用户拖入/选择了图片）
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    from PyQt6.QtGui import QPainter

    painter = QPainter(pixmap)
    painter.fillRect(8, 8, 24, 24, QColor("#E91E63"))
    painter.fillRect(36, 36, 20, 20, QColor("#1E88E5"))
    painter.end()
    win._on_image_loaded(pixmap)
    for _ in range(4):
        app.processEvents()

    print("=== 0. 前置状态：有图、没有动作 ===")
    print(f"  原图 {win.image_viewer._pixmap.width()}×{win.image_viewer._pixmap.height()}，"
          f"检测到元素 {len(win.image_viewer._frames)} 个，动作数 {len(win.actions)}")
    check("图片已载入", win.image_viewer._pixmap is not None)
    check("当前没有动作", not win.actions and win.image_viewer.current_action is None)
    check("按钮文案已变成「+ 添加动作」", win.btn_add_action.text() == "+ 添加动作",
          f"实际「{win.btn_add_action.text()}」")

    print()
    print("=== 1. 点原图画布 → 提示 ===")
    check("初始没有提示", not canvas._toast.active(), canvas._toast.text())
    before = bottom_pixels(canvas)
    click_center(canvas)
    app.processEvents()
    after = bottom_pixels(canvas)
    print(f"  提示文案「{canvas._toast.text()}」")
    check("弹出了提示", canvas._toast.active(), "没弹出")
    check("文案与预览面板一致（不漂移）", canvas._toast.text() == IV_HINT == PV_HINT,
          f"{IV_HINT!r} / {PV_HINT!r}")
    check("提示是真的画出来了（底部像素有变化）", before != after,
          f"{sum(1 for a, b in zip(before, after) if a != b)} 个采样点不同")

    print()
    print("=== 2. 点预览画布 → 提示 ===")
    preview_canvas._toast.clear()
    app.processEvents()
    click_center(preview_canvas)
    app.processEvents()
    print(f"  提示文案「{preview_canvas._toast.text()}」")
    check("预览画布也提示", preview_canvas._toast.active(), "没弹出")

    print()
    print("=== 3. 点变换按钮（控件）→ 提示 + 不允许点亮模式 ===")
    preview_canvas._toast.clear()
    app.processEvents()
    rotate = panel.btn_mode_rotate
    before = bottom_pixels(preview_canvas)
    click_center(rotate)
    app.processEvents()
    print(f"  按钮选中={rotate.isChecked()}，画布模式={preview_canvas.transform_mode()}，"
          f"提示「{preview_canvas._toast.text()}」")
    check("点了变换按钮会提示", preview_canvas._toast.active(), "没弹出")
    check("无动作时按钮不保持选中（弹回）", not rotate.isChecked(), "按钮亮着")
    check("无动作时不进入变换模式（不会出现按钮亮着却没手柄）",
          preview_canvas.transform_mode() is None, f"模式 {preview_canvas.transform_mode()}")
    check("提示画在预览画布上（像素有变化）", before != bottom_pixels(preview_canvas))

    print()
    print("=== 4. 点方向按钮 / 镜像 / 清空变换 → 同样提示 ===")
    for widget, name in ((panel.btn_up, "↑"), (panel.btn_mirror_h, "水平镜像"),
                         (panel.btn_clear_transform, "清空变换")):
        preview_canvas._toast.clear()
        app.processEvents()
        click_center(widget)
        app.processEvents()
        print(f"  {name}: 提示={preview_canvas._toast.active()}「{preview_canvas._toast.text()}」")
        check(f"点「{name}」会提示", preview_canvas._toast.active(), "没弹出")
        check(f"点「{name}」不会留下脏数据",
              not (win.actions and getattr(win.actions[0], "frame_offsets", {})),
              "产生了帧偏移")

    print()
    print("=== 5. 点原图工具条的「Ctrl+合成」→ 提示 + 按钮不保持 ===")
    canvas._toast.clear()
    app.processEvents()
    compose = win.image_viewer._toolbar.btn_compose
    QTest.mouseClick(compose, Qt.MouseButton.LeftButton,
                     pos=QPoint(compose.width() // 2, compose.height() // 2))
    app.processEvents()
    print(f"  按钮选中={compose.isChecked()}，提示「{canvas._toast.text()}」")
    check("点合成按钮会提示", canvas._toast.active(), "没弹出")
    check("无动作时合成按钮不保持选中", not compose.isChecked(), "按钮亮着")
    check("无动作时不进入合成模式", not win.image_viewer.compose_mode, "进入了合成模式")

    print()
    print("=== 6. 添加动作之后：不再提示，交互恢复正常 ===")
    canvas._toast.clear()
    preview_canvas._toast.clear()
    win._create_action()
    for _ in range(4):
        app.processEvents()
    print(f"  动作数 {len(win.actions)}，当前动作 {getattr(win.image_viewer.current_action, 'name', None)}")
    check("动作已创建", len(win.actions) == 1)
    click_center(canvas)
    app.processEvents()
    check("有动作时点画布不再提示", not canvas._toast.active(),
          f"提示「{canvas._toast.text()}」")
    click_center(rotate)
    app.processEvents()
    print(f"  变换按钮选中={rotate.isChecked()}，画布模式={preview_canvas.transform_mode()}")
    check("有动作时变换按钮正常工作（可进入模式）", rotate.isChecked(), "按钮没选中")
    check("有动作时点控件不再提示", not preview_canvas._toast.active(),
          f"提示「{preview_canvas._toast.text()}」")
    rotate.setChecked(False)
    app.processEvents()

    print()
    print("=== 7. 提示会自动消失（不是常驻） ===")
    from spritesheet.ui.widgets import HintToast

    probe = HintToast(canvas, duration_ms=80)
    probe.show("测试")
    app.processEvents()
    check("刚显示时是激活的", probe.active())
    QTest.qWait(200)
    app.processEvents()
    check("超时后自动清空", not probe.active(), f"仍为「{probe.text()}」")

    win.close()
    print()
    if FAILURES:
        print("NO-ACTION HINT FAILED：", *FAILURES, sep="\n  - ")
        return 1
    print("NO-ACTION HINT OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
