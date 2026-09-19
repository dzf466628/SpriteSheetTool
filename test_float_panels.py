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
# 三块内容控件（尺寸以它们为准）
panels = (("动作编辑", win.anim_bar), ("预览", pv.preview_box), ("帧编辑", pv.edit_box))


def holders():
    """真正当窗口的那个：动作编辑/帧编辑 = 无边框圆角卡片，预览 = preview_box 本身。"""
    out = []
    for name, w in panels:
        card = None if w is pv.preview_box else win._float_cards.get(name)
        out.append((name, w, card if card is not None else w))
    return out


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
check("三块都成了独立窗口", all(h.isWindow() for _n, _w, h in holders()) and win._panels_floating,
      f"{[(n, h.isWindow()) for n, _w, h in holders()]}")
# 预览窗例外：按用户要求"默认画布方形"，并再缩 30%
exp_side = max(200, int(round(docked["预览"][0] * 0.7)))
check("预览窗默认=画布方形(缩30%)",
      floated["预览"] == (exp_side, exp_side + 92)
      and pv.canvas.width() == pv.canvas.height() == exp_side,
      f"窗口={floated['预览']} 画布={pv.canvas.width()}x{pv.canvas.height()} 期望={exp_side}")
check("另两块尺寸与默认窗口完全一致（★大小不变）",
      floated["动作编辑"] == docked["动作编辑"] and floated["帧编辑"] == docked["帧编辑"],
      f"默认={docked} 悬浮={floated}")
check("悬浮窗都是归属主窗口的 Tool 窗口（永远在主页面之前、不被遮挡）",
      all(bool(h.windowFlags() & Qt.WindowType.Tool) and h.parent() is win
          for _n, _w, h in holders())
      and win._history_card is not None and win._history_card.isVisible(),
      f"{[(n, bool(h.windowFlags() & Qt.WindowType.Tool), h.parent() is win) for n, _w, h in holders()]} "
      f"历史记录卡片可见={win._history_card is not None and win._history_card.isVisible()}")
check("预览窗可拖边框改大小（没被锁死）",
      pv.preview_box.maximumWidth() > 10000 and pv.preview_box.minimumWidth() < 300,
      f"min={pv.preview_box.minimumSize()} max={pv.preview_box.maximumSize()}")
_cards = [win._float_cards.get(n) for n in ("动作编辑", "帧编辑")]
check("另两块的卡片锁住尺寸（内部布局不许被拉乱）",
      all(c is not None and c.minimumSize() == c.maximumSize() for c in _cards),
      f"{[(c.minimumSize(), c.maximumSize()) for c in _cards if c is not None]}")
check("预览整块隐藏（宽高让给原图区）", not pv.isVisible())
check("原图区拿到全部宽度",
      win.splitter.sizes()[1] == 0 and win.splitter.sizes()[0] > 0,
      f"splitter={win.splitter.sizes()}")

print("\n2a. 动作编辑/帧编辑 用无系统标题栏的圆角卡片 + 自带 ✕：")
for name in ("动作编辑", "帧编辑"):
    card = win._float_cards.get(name)
    panel = dict(panels)[name]
    check(f"{name}：无边框（没有系统标题栏）",
          card is not None and bool(card.windowFlags() & Qt.WindowType.FramelessWindowHint),
          f"flags={card.windowFlags() if card else None}")
    check(f"{name}：自带标题栏 + 关闭按钮在右边",
          card is not None and card.title_bar.isVisible() and card.btn_close.isVisible()
          and card.btn_close.mapTo(card, QPoint(0, 0)).x() > card.width() // 2,
          f"✕位置={card.btn_close.mapTo(card, QPoint(0, 0)).x() if card else None} 宽={card.width() if card else None}")
    check(f"{name}：面板内容在卡片里（内容屏幕位置和默认窗口一致）",
          panel.parent() is card.body,
          f"parent={panel.parent().__class__.__name__ if panel.parent() else None}")
    check(f"{name}：卡片是圆角（有 border-radius 样式）",
          card is not None and "border-radius" in card.styleSheet())

card_anim = win._float_cards.get("动作编辑")
card_anim.btn_close.click()
wait(120)
check("点 ✕ 会把这块收起来（不是关掉程序）",
      not card_anim.isVisible() and win._panels_floating, f"visible={card_anim.isVisible()}")
win.setWindowState(Qt.WindowState.WindowNoState)
wait(200)
win.setWindowState(Qt.WindowState.WindowMaximized)
wait(250)
check("重新最大化后它会回来", card_anim.isVisible(), f"visible={card_anim.isVisible()}")

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

print("\n3. 还原窗口 → 三块塞回原布局，比例复位：")
win.setWindowState(Qt.WindowState.WindowNoState)
wait(400)
back = sizes()
# 期望值取"默认尺寸记录"（面板自己记的默认尺寸），而不是启动时的快照 ——
# 中间显示过历史记录右列，主分隔线比例可能整体漂几像素
expect = {n: (v.width(), v.height()) for n, v in win._panel_sizes_normal.items()}
check("三块又都是子控件", all(not w.isWindow() for _n, w in panels))
check("尺寸回到默认值", all(back[n] == expect[n] for n in back if n in expect),
      f"{back} vs {expect}")
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
    before_round = sizes()          # 每一轮都拿当前停靠尺寸当基准
    win.setWindowState(Qt.WindowState.WindowMaximized)
    wait(150)
    s = sizes()
    ok = ok and s["动作编辑"] == before_round["动作编辑"] and s["帧编辑"] == before_round["帧编辑"] \
        and s["预览"] == (exp_side, exp_side + 92) \
        and all(h.isWindow() for _n, _w, h in holders())
    win.setWindowState(Qt.WindowState.WindowNoState)
    wait(250)
    ok = ok and sizes() == before_round and all(not w.isWindow() for _n, w in panels)
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

print("\n6. 历史记录：默认在主页面右列，最大化时是同样的圆角卡片，工具栏按钮联动：")
hist_btn = win.image_viewer._toolbar.btn_history
# 默认窗口：显示时是主窗口右列（不是浮在外面的 QDockWidget，那种会被"吸"进主窗口）
win._show_history(True)
wait(150)
check("默认窗口下历史记录在主窗口里面（splitter 的一列）",
      win.history_box.isVisible() and not win.history_box.isWindow()
      and win.splitter.indexOf(win.history_box) >= 0,
      f"visible={win.history_box.isVisible()} window={win.history_box.isWindow()}")
check("显示时工具栏历史按钮激活", hist_btn.isChecked(), f"checked={hist_btn.isChecked()}")
win._show_history(False)
wait(120)
check("收起后按钮弹回", not hist_btn.isChecked() and not win.history_box.isVisible())
win.setWindowState(Qt.WindowState.WindowMaximized)
wait(300)
card_h = win._history_card
check("最大化后历史记录是独立卡片窗口", card_h is not None and card_h.isWindow()
      and card_h.isVisible(), f"card={card_h}")
check("历史卡片同样无系统标题栏 + 圆角 + 1px 灰描边 + 自带 ✕",
      card_h is not None and bool(card_h.windowFlags() & Qt.WindowType.FramelessWindowHint)
      and "border-radius: 10px" in card_h.styleSheet()
      and "border: 1px solid" in card_h.styleSheet()
      and card_h.btn_close.isVisible(),
      f"style={'border-radius' in card_h.styleSheet() if card_h else None}")
check("工具栏历史按钮激活（最大化时也显示着）", hist_btn.isChecked() and win._history_shown())
win._show_history(False)
wait(120)
check("卡片能收起来，按钮弹回", not card_h.isVisible() and not hist_btn.isChecked())
hist_btn.click()
wait(120)
check("点按钮又能叫回来", card_h.isVisible() and hist_btn.isChecked())
check("四个窗口都是 1 像素灰描边",
      all("border: 1px solid" in (c.styleSheet() if c is not None else "")
          for c in (win._float_cards.get("动作编辑"), win._float_cards.get("帧编辑"), card_h))
      and "border: 1px solid" in pv.preview_box.styleSheet(),
      "动作编辑/帧编辑/历史/预览")
win.setWindowState(Qt.WindowState.WindowNoState)
wait(200)

win.close()
print(f"\n结果：{PASS} 通过, {FAIL} 失败")
sys.exit(1 if FAIL else 0)
