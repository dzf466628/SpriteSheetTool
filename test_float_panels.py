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

from PyQt6.QtCore import QEventLoop, QTimer, Qt
from PyQt6.QtGui import QPixmap
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
check("尺寸与默认窗口完全一致（★大小不变）", floated == docked, f"默认={docked} 悬浮={floated}")
check("内部结构比例不变（画布/控制条/缩略图/面板）", inner() == docked_inner,
      f"默认={docked_inner} 悬浮={inner()}")
check("预览整块隐藏（宽高让给原图区）", not pv.isVisible())
check("原图区拿到全部宽度",
      win.splitter.sizes()[1] == 0 and win.splitter.sizes()[0] > 0,
      f"splitter={win.splitter.sizes()}")

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
    ok = ok and sizes() == docked and all(w.isWindow() for _n, w in panels)
    win.setWindowState(Qt.WindowState.WindowNoState)
    wait(250)
    ok = ok and sizes() == docked and all(not w.isWindow() for _n, w in panels)
check("两轮来回尺寸都保持", ok, f"最后={sizes()}")

win.close()
print(f"\n结果：{PASS} 通过, {FAIL} 失败")
sys.exit(1 if FAIL else 0)
