# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""抠除背景的界面回归：竖条按钮 -> 参数浮层 -> 实时预览 -> 原图分块选择 -> 应用/撤销。

为什么单独一个脚本：算法层有 ``verify_keying.py``（纯 Pillow，32 项），这里管的是**接线**——
按钮通不通、预览有没有副作用、分块选得对不对、"只抠这张"会不会误伤别的原图。
这些用像素数字判定，不靠肉眼；全程 offscreen 可跑（不需要真实窗口）。

判定用的素材是自己造的：多张**底色各不相同**的原图（蓝 / 洋红 / 绿，各带一个不同颜色的
角色），这样"抠了哪张、漏了哪张"完全能用 alpha 判定。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from spritesheet.ui.key_panel import pil_to_pixmap
from spritesheet.ui.main_window import MainWindow

# QApplication 必须挂在模块级：如果放在 main() 里，函数返回时它先被回收，
# 解释器收尾阶段 Qt 对象的析构顺序就乱了，真机下会以 0xC0000409 fail-fast 收场
# （offscreen 看不出来，因为不建原生窗口资源）。
_APP = QApplication.instance() or QApplication(sys.argv)

BG = [(40, 120, 200), (200, 40, 200), (60, 200, 90)]        # 三张原图各自的底色
SPRITE = [(200, 40, 40), (240, 230, 60), (230, 120, 40)]    # 三个角色各自的颜色
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

    def wait(ms: int = 1200) -> None:
        """等防抖 + 预览算完（FULL_DELAY=350ms，多等一点无所谓）。"""
        for _ in range(max(1, ms // 50)):
            app.processEvents()
            app.thread().msleep(50)

    sources = [make_source(i) for i in range(3)]
    win._on_image_loaded(pil_to_pixmap(sources[0]))
    for pm_src in sources[1:]:
        win._on_image_loaded(pil_to_pixmap(pm_src))
    wait(300)
    baked = viewer._pixmap

    print("---- 1. 原图分块位置 ----")
    rects = viewer.source_rects()
    check("分块矩形就是拼装算法里那三块",
          rects == [(0, 0, W, H), (0, H + GAP, W, H), (0, 2 * (H + GAP), W, H)], f"实际 {rects}")
    same = True
    for index, (bx, by, _bw, _bh) in enumerate(rects):
        for (sx, sy) in ((3, 3), (30, 25), (55, 45)):
            if px(baked, bx + sx, by + sy) != sources[index].getpixel((sx, sy)):
                same = False
    check("每块的内容与源图逐像素一致", same)
    check("块之间的间隙是透明的", px(baked, 30, H + 8)[3] == 0 and px(baked, 30, 2 * H + GAP + 8)[3] == 0)

    print("---- 2. 点坐标 -> 第几张原图 ----")
    zoom, off = viewer._zoom, viewer._offset

    def canvas_point(image_x: float, image_y: float) -> QPointF:
        return QPointF(off.x() + image_x * zoom, off.y() + image_y * zoom)

    for index in range(3):
        got = viewer.source_index_at(canvas_point(30, rects[index][1] + 25))
        check(f"点在块{index}上判定正确", got == index, f"判定={got}")
    check("点在块外判定为 -1", viewer.source_index_at(canvas_point(W + 20, 5)) == -1)

    print("---- 3. 竖条按钮 -> 面板 ----")
    viewer._v_toolbar.btn_key.setChecked(True)
    wait()
    panel = win._key_panel
    check("按钮点开后浮层出现", panel.isVisible())
    check("面板贴在竖条右边", panel.x() > viewer._v_toolbar.x() + viewer._v_toolbar.width())
    check("多张原图时才显示作用范围", panel.scope_row.isVisible())
    check("默认「只抠选中的」且自动选中第 1 张",
          panel.btn_this.isChecked() and panel._scope == "single"
          and viewer.selected_sources() == [0])
    check("面板开着时画布显示分块边框", viewer.source_borders_visible)
    # 版面（用户要求"重新排版 + 那个信息窗口可以不要 + 按钮要加悬浮动效"）
    check("统计信息那行已经删掉（用户说那个信息窗口不要）", not hasattr(panel, "lbl_stats"))
    check("提示行固定两行高（避免按钮来回跳）",
          panel.lbl_hint.minimumHeight() == panel.lbl_hint.maximumHeight()
          and panel.lbl_hint.height() >= panel.lbl_hint.fontMetrics().lineSpacing() * 2,
          f"高={panel.lbl_hint.height()} 最小={panel.lbl_hint.minimumHeight()}")
    _h0 = panel.height()
    panel.lbl_hint.setText("警告一")
    app.processEvents()
    _h1 = panel.height()
    panel.lbl_hint.setText("警告一　警告二　警告三")
    app.processEvents()
    check("提示一行 / 两行 / 清空，面板高度都不变（按钮不再来回跳）",
          panel.height() == _h0 == _h1, f"{_h0} -> {_h1} -> {panel.height()}")
    panel.lbl_hint.setText("")
    _btns = (panel.btn_pick, panel.btn_guess, panel.btn_cancel, panel.btn_apply,
             panel.btn_this, panel.btn_all)
    check("面板每个按钮都带悬浮反馈（:hover 规则）",
          all(":hover" in b.styleSheet() for b in _btns),
          "缺 hover：" + ",".join(b.text() for b in _btns if ":hover" not in b.styleSheet()))
    check("收尾两个按钮等宽、高度一致",
          panel.btn_cancel.minimumHeight() == panel.btn_apply.minimumHeight() == 24)
    check("分块图裁得对", panel._block_pils[0].tobytes() == sources[0].tobytes())
    check("自动猜色猜的是第 1 块的底色", panel._bg_color == BG[0], f"实际 {panel._bg_color}")

    before_no_mode = canvas.grab().toImage()
    # 隔离"面板那一路"：两个开关都关掉取基线，再只把面板打开 —— 否则工具本身没动作时
    # 是**默认开着**的（用户要求），两边的框一样，差异点会是 0，测不出东西
    _keep_tool, _keep_panel = viewer._source_tool, viewer._source_panel
    viewer._source_tool, viewer._source_panel = False, False
    canvas.update()
    for _ in range(4):
        app.processEvents()
    without = canvas.grab().toImage()
    viewer._source_panel = True
    canvas.update()
    for _ in range(4):
        app.processEvents()
    with_mode = canvas.grab().toImage()
    viewer._source_tool, viewer._source_panel = _keep_tool, _keep_panel
    canvas.update()
    diff = sum(1 for y in range(0, min(without.height(), with_mode.height()), 3)
               for x in range(0, min(without.width(), with_mode.width()), 3)
               if without.pixel(x, y) != with_mode.pixel(x, y))
    check("分块虚框确实画在画布上", diff > 0 and before_no_mode is not None, f"差异点={diff}")

    print("---- 4. 只抠选中的：不能误伤别的原图 ----")
    local = canvas_point(30, rects[1][1] + 25)
    canvas.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, local, local, Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    canvas.mouseReleaseEvent(QMouseEvent(
        QEvent.Type.MouseButtonRelease, local, local, Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    wait(900)
    check("选中的块变成第 2 张", viewer.selected_sources() == [1],
          f"选中={viewer.selected_sources()}")
    check("猜色跟着换成第 2 张的底色", panel._bg_color == BG[1], f"实际 {panel._bg_color}")
    check("点块没被当成框选", canvas._drag_start is None)
    preview = viewer._preview_pixmap
    check("预览已生成", preview is not None)
    check("第 2 张底色被清掉", px(preview, 5, rects[1][1] + 5)[3] == 0)
    check("第 1 张底色一个像素没动", px(preview, 5, rects[0][1] + 5) == BG[0] + (255,))
    check("第 3 张底色也没动", px(preview, 5, rects[2][1] + 5) == BG[2] + (255,))
    check("三个角色都还在",
          all(px(preview, 30, rects[i][1] + 25)[:3] == SPRITE[i]
              and px(preview, 30, rects[i][1] + 25)[3] == 255 for i in range(3)))
    check("预览期间原图对象身份没变（预览零副作用）", viewer._pixmap is baked)

    print("---- 5. 取消：一切归零 ----")
    panel._on_cancel()
    wait(300)
    check("取消后预览被清掉、浮层隐藏",
          viewer._preview_pixmap is None and not panel.isVisible())
    check("取消后按钮弹起", not viewer._v_toolbar.btn_key.isChecked())
    check("取消后抠图那一路的钩子全清掉（取色目标/分块显示标记）",
          viewer._source_panel is False and viewer._pick_target is None)
    check("取消后选中项保留（选中是选择工具的状态，面板只是借用）",
          viewer.selected_sources() == [1], f"选中={viewer.selected_sources()}")
    check("取消后原图身份没变", viewer._pixmap is baked)

    print("---- 6. 抠全部：每张各自猜色 ----")
    viewer._v_toolbar.btn_key.setChecked(True)
    wait()
    panel.btn_all.setChecked(True)
    wait(900)
    check("作用范围变成全部", panel._scope == "all")
    check("抠全部时取色控件被禁用（不留点了没反应的按钮）",
          not panel.btn_pick.isEnabled() and not panel.btn_guess.isEnabled()
          and not panel.swatch.isEnabled())
    preview = viewer._preview_pixmap
    check("三张底色全被清掉", all(px(preview, 5, rects[i][1] + 5)[3] == 0 for i in range(3)))
    check("三个角色全在", all(px(preview, 30, rects[i][1] + 25)[:3] == SPRITE[i] for i in range(3)))
    check("间隙仍是透明的", px(preview, 30, H + 8)[3] == 0)

    print("---- 7. 应用 + 撤销 ----")
    panel._on_apply()
    wait(600)
    check("应用后原图换成抠好的图", viewer._pixmap is not baked)
    check("应用后三张底色全透明",
          all(px(viewer._pixmap, 5, rects[i][1] + 5)[3] == 0 for i in range(3)))
    check("应用后抠图那一路的钩子全清掉",
          viewer._source_panel is False and viewer._pick_target is None)
    check("应用后没留下预览", viewer._preview_pixmap is None)
    index_before = win._undo_stack.index()
    win._do_undo()
    wait(400)
    check("撤销回到原图", viewer._pixmap is baked)
    check("撤销后全局栈 index 归零", win._undo_stack.index() == index_before - 1,
          f"index={win._undo_stack.index()}")

    print("---- 8. 没有图片时的兜底 ----")
    win2 = MainWindow()
    win2.show()
    for _ in range(6):
        app.processEvents()
    win2.image_viewer._v_toolbar.btn_key.setChecked(True)
    for _ in range(6):
        app.processEvents()
    check("没图时按钮自动弹起", not win2.image_viewer._v_toolbar.btn_key.isChecked())
    check("没图时状态栏给提示", "图片" in win2.lbl_status.text(),
          f"状态栏='{win2.lbl_status.text()}'")

    win2.close()
    win.close()
    print(f"\nKEY PANEL VERIFY {'OK' if not failed else 'FAILED'}  "
          f"{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())