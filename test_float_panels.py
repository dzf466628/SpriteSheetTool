#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""最大化 → 三块区域脱出成独立悬浮窗，**尺寸/比例与默认窗口完全一致**的回归。

用户需求（原话）：窗口只有"默认大小"和"最大化"两种；默认大小时布局不变；
最大化后 ①动作编辑 ②帧编辑 ③预览窗口(画布+进度条+缩略图) 变成三个独立悬浮窗，
**大小不变、比例不变**。

用真控件 + 真窗口状态变化（setWindowState）验证，并把三块的尺寸和内部子控件
尺寸都量出来对比。
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from pathlib import Path

from PyQt6.QtCore import QEventLoop, QPoint, QTimer, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
ROOT = Path(__file__).resolve().parent

from spritesheet.ui.main_window import MainWindow  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name} {detail}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def wait(ms):
    loop = QEventLoop(); QTimer.singleShot(ms, loop.quit); loop.exec()


win = MainWindow()
win.show()
wait(200)
pm = QPixmap(str(ROOT / "mage_sprite_keyed.png"))
win._on_image_loaded(pm)
done = {"n": None}
win.image_viewer.frames_updated.connect(lambda: done.update(n=len(win.image_viewer._frames)))
t0 = time.perf_counter()
while done["n"] is None and time.perf_counter() - t0 < 30:
    wait(100)
win._create_action()
act = win.actions[-1]
act.selected_indices = list(range(min(4, done["n"])))
win.image_viewer.set_current_action(act)
win.image_viewer.crop_frames_for_action(act)
win.preview.set_action(act)
win.preview.set_actions(win.actions)
wait(600)          # 等去抖记下"默认尺寸"

pv = win.preview
panels = (("动作编辑", win.anim_bar), ("预览", pv.preview_box), ("帧编辑", pv.edit_box))


def sizes():
    return {name: (w.width(), w.height()) for name, w in panels}


def inner():
    """预览块内部的相对结构（画布/控制条/缩略图 + 帧编辑块内部）。"""
    return {
        "画布": (pv.canvas.width(), pv.canvas.height()),
        "控制条": (pv.ctrl_widget.width(), pv.ctrl_widget.height()),
        "缩略图": (pv.thumb_list.width(), pv.thumb_list.height()),
        "面板": (pv.adjust_panel.width(), pv.adjust_panel.height()),
        "导出按钮": (pv.btn_export.width(), pv.btn_export.height()),
    }


docked = sizes()
docked_inner = inner()
print("1. 默认窗口：三块都在原布局里、尺寸已记录：")
check("三块都是子控件（不是独立窗口）", all(not w.isWindow() for _n, w in panels),
      f"{[(n, w.isWindow()) for n, w in panels]}")
check("显示了默认尺寸（去抖已生效）",
      set(win._panel_sizes_normal) == {"动作编辑", "预览", "帧编辑"},
      f"{ {k: (v.width(), v.height()) for k, v in win._panel_sizes_normal.items()} }")
check("预览块 = 画布+控制条+缩略图 都在里面",
      pv.canvas.parent() is pv.preview_box and pv.ctrl_widget.parent() is pv.preview_box
      and pv.thumb_list.parent() is pv.preview_box)

print("\n2. 最大化（真窗口状态变化）→ 三块脱出成独立悬浮窗，尺寸不变：")
win.setWindowState(Qt.WindowState.WindowMaximized)
wait(300)
floated = sizes()
check("三个都变成独立窗口", all(w.isWindow() for _n, w in panels) and win._panels_floating,
      f"{[(n, w.isWindow()) for n, w in panels]}")
# 预览窗例外：按用户要求"默认画布方形"，并再缩 30%
exp_side = max(200, int(round(docked["预览"][0] * 0.7)))
check("预览窗默认=画布方形(缩30%)",
      floated["预览"] == (exp_side, exp_side + 92)
      and pv.canvas.width() == pv.canvas.height() == exp_side,
      f"窗口={floated['预览']} 画布={pv.canvas.width()}x{pv.canvas.height()} 期望={exp_side}")
check("另两块尺寸与默认窗口完全一致（★大小不变）",
      floated["动作编辑"] == docked["动作编辑"] and floated["帧编辑"] == docked["帧编辑"],
      f"默认={docked} 悬浮={floated}")
check("四块悬浮窗都是归属主窗口的 Tool 窗口（永远在主页面之前、不被遮挡）",
      all(bool(w.windowFlags() & Qt.WindowType.Tool) and w.parent() is win for _n, w in panels)
      and win._history_dock.isFloating(),
      f"{[(n, bool(w.windowFlags() & Qt.WindowType.Tool), w.parent() is win) for n, w in panels]} "
      f"历史记录 floating={win._history_dock.isFloating()}")
check("预览窗可拖边框改大小（没被锁死）",
      pv.preview_box.maximumWidth() > 10000 and pv.preview_box.minimumWidth() < 300,
      f"min={pv.preview_box.minimumSize()} max={pv.preview_box.maximumSize()}")
check("另两块锁住尺寸（内部布局不许被拉乱）",
      win.anim_bar.minimumSize() == win.anim_bar.maximumSize()
      and pv.edit_box.minimumSize() == pv.edit_box.maximumSize(),
      f"动作编辑 min={win.anim_bar.minimumSize()} max={win.anim_bar.maximumSize()}")
check("预览整块隐藏（宽高让给原图区）", not pv.isVisible())
check("原图区拿到全部宽度",
      win.splitter.sizes()[1] == 0 and win.splitter.sizes()[0] > 0,
      f"splitter={win.splitter.sizes()}")

print("\n2b. 悬浮的预览窗随便拖大小：")
pv.preview_box.move(pv.preview_box.x() + 40, pv.preview_box.y() + 30)
pv.preview_box.resize(420, 300)
wait(200)
check("拖边框改大小后画布跟着变（不崩、不缩错）",
      pv.canvas.width() == 420 and pv.canvas.height() == 300 - 32 - 52 - 8,
      f"窗口={pv.preview_box.width()}x{pv.preview_box.height()} "
      f"画布={pv.canvas.width()}x{pv.canvas.height()}")
pv.preview_box.resize(*floated["预览"])     # 拖回默认方形，后面按默认值还原
wait(120)

print("\n2c. 贴屏幕边缘（磁吸）：")
area = app.primaryScreen().availableGeometry()
w0 = win.anim_bar
w0.move(area.left() + 5, area.top() + 5)
win._snap_floating_to_edge(w0)              # 等价于贴边轮询定时器做的动作
check(f"靠近左/上边缘自动贴边 (left={area.left()}, top={area.top()})",
      w0.x() == area.left() and w0.y() == area.top(),
      f"实际=({w0.x()},{w0.y()})")
w0.move(area.left() + 3, area.top() + 3)    # 贴边后再往外拖（下一次轮询）
win._snap_floating_to_edge(w0)
check("贴边后能拉出来（不会被粘住）", w0.x() > area.left() and w0.y() > area.top(),
      f"实际=({w0.x()},{w0.y()})")
w0.move(area.left() + 120, area.top() + 140)
win._snap_floating_to_edge(w0)
check("离边缘远时不动它", w0.x() == area.left() + 120, f"实际=({w0.x()},{w0.y()})")

print("\n3. 还原窗口 → 三块塞回原布局，比例复位：")
win.setWindowState(Qt.WindowState.WindowNoState)
wait(400)
back = sizes()
check("三块又都是子控件", all(not w.isWindow() for _n, w in panels))
check("尺寸回到默认值", back == docked, f"{back}")
check("内部结构复位", inner() == docked_inner, f"{inner()}")
check("预览块里面还是原来那三样（画布/控制条/缩略图）",
      pv.preview_splitter.count() == 2 and pv.canvas.parent() is pv.preview_box
      and pv.ctrl_widget.parent() is pv.preview_box and pv.thumb_list.parent() is pv.preview_box,
      f"splitter={pv.preview_splitter.count()} sizes={pv.preview_splitter.sizes()}")
check("左列顺序仍是 原图区 → 动作编辑",
      [win.left_layout.itemAt(i).widget().__class__.__name__
       for i in range(win.left_layout.count())] == ["ImageViewer", "AnimSettingsBar"],
      f"{[win.left_layout.itemAt(i).widget().__class__.__name__ for i in range(win.left_layout.count())]}")

print("\n4. 可以反复最大化/还原：")
ok = True
for _ in range(2):
    win.setWindowState(Qt.WindowState.WindowMaximized)
    wait(150)
    s = sizes()
    ok = ok and s["动作编辑"] == docked["动作编辑"] and s["帧编辑"] == docked["帧编辑"] \
        and s["预览"] == (exp_side, exp_side + 92) \
        and all(w.isWindow() for _n, w in panels)
    win.setWindowState(Qt.WindowState.WindowNoState)
    wait(250)
    ok = ok and sizes() == docked and all(not w.isWindow() for _n, w in panels)
check("两轮来回尺寸都保持", ok, f"最后={sizes()}")

print("\n5. 原图区悬浮工具条可以自己拖（用户要挪到悬浮预览窗左边）：")
tb = win.image_viewer._toolbar
canvas = win.image_viewer.canvas
start = tb.pos()
QTest.mousePress(tb, Qt.MouseButton.LeftButton, pos=QPoint(3, 3))
QTest.mouseMove(tb, pos=QPoint(3 - 90, 3 + 60))
QTest.mouseRelease(tb, Qt.MouseButton.LeftButton, pos=QPoint(3 - 90, 3 + 60))
wait(80)
check("拖动空白处工具条会移动", tb.pos() != start,
      f"{start.x()},{start.y()} -> {tb.pos().x()},{tb.pos().y()}")
check("工具条被标记为用户挪过", tb.moved_by_user())
moved = tb.pos()
canvas.resize(canvas.width() - 30, canvas.height() - 20)
wait(80)
check("画布缩放后不再被强行搬回右上角", tb.pos() == moved,
      f"{moved.x()},{moved.y()} -> {tb.pos().x()},{tb.pos().y()}")
check("工具条不会跑出画布",
      0 <= tb.x() <= max(0, canvas.width() - tb.width())
      and 0 <= tb.y() <= max(0, canvas.height() - tb.height()),
      f"toolbar=({tb.x()},{tb.y()}) canvas={canvas.width()}x{canvas.height()}")

win.close()
print(f"\n结果：{PASS} 通过, {FAIL} 失败")
sys.exit(1 if FAIL else 0)
