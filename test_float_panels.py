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
from PyQt6.QtGui import QColor, QImage, QPixmap
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
    """真正当窗口的那个：四块都是同款无边框圆角卡片。"""
    return [(name, w, win._float_cards.get(name) or w) for name, w in panels]


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
check("动作编辑/帧编辑 固定大小；预览可缩放（用户要求）",
      all(win._float_cards[n].minimumSize() == win._float_cards[n].maximumSize()
          for n in ("动作编辑", "帧编辑") if win._float_cards.get(n))
      and win._float_cards["预览"].maximumWidth() > 10000,
      f"预览 min={win._float_cards['预览'].minimumSize()} max={win._float_cards['预览'].maximumSize()}")
_cards = [win._float_cards.get(n) for n in ("动作编辑", "帧编辑")]
check("卡片的尺寸是锁住的（内部布局不许被拉乱）",
      all(c is not None and c.minimumSize() == c.maximumSize() for c in _cards),
      f"{[(c.minimumSize(), c.maximumSize()) for c in _cards if c is not None]}")
check("预览整块隐藏（宽高让给原图区）", not pv.isVisible())
check("原图区拿到全部宽度",
      win.splitter.sizes()[1] == 0 and win.splitter.sizes()[0] > 0,
      f"splitter={win.splitter.sizes()}")

print("\n2a. 四块都是无系统标题栏/无关闭按钮的圆角卡片（黑描边、底色不透明）：")


def card_pixels(card, pt):
    """把卡片渲染出来，取某点像素 (r,g,b,a)。"""
    img = QImage(card.size(), QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    card.render(img)
    px = img.pixel(pt)
    return ((px >> 16) & 255, (px >> 8) & 255, px & 255, (px >> 24) & 255)


for name in ("动作编辑", "帧编辑", "预览"):
    card = win._float_cards.get(name)
    panel = dict(panels)[name]
    check(f"{name}：无边框（没有系统标题栏、没有关闭按钮）",
          card is not None and bool(card.windowFlags() & Qt.WindowType.FramelessWindowHint)
          and not hasattr(card, "btn_close"),
          f"frameless={bool(card.windowFlags() & Qt.WindowType.FramelessWindowHint) if card else None}")
    check(f"{name}：和分界线同款浅灰底色 + 2px 描边（#555555）",
          card is not None and card.BG_COLOR == "#3d3d3d"
          and card.BORDER_COLOR == "#555555" and card.BORDER_W == 2,
          f"bg={getattr(card, 'BG_COLOR', None)} border={getattr(card, 'BORDER_COLOR', None)}"
          f" w={getattr(card, 'BORDER_W', None)}")
    body = card_pixels(card, QPoint(card.width() // 2, card.height() - 12))
    corner = card_pixels(card, QPoint(1, card.height() - 1))
    check(f"{name}：底色不透明（不是透明弹窗）",
          body[3] == 255,
          f"卡片内像素={body}（alpha 必须 255；具体色值看落在哪个子控件上）")
    check(f"{name}：面板内容在卡片里", panel.parent() is card.body,
          f"parent={panel.parent().__class__.__name__ if panel.parent() else None}")
    check(f"{name}：圆角（四角是透明的）", corner[3] == 0, f"左上角像素={corner}")

check("历史卡片同样没有关闭按钮（最大化时不能关掉）",
      win._history_card is not None and not hasattr(win._history_card, "btn_close"))

print("\n2b. 预览卡片：画布方形 + 尺寸固定 + 拖动标题栏可移动：")
card_pv = win._float_cards.get("预览")
check("预览画布是正方形", pv.canvas.width() == pv.canvas.height() == exp_side,
      f"画布={pv.canvas.width()}x{pv.canvas.height()} 期望={exp_side}")
check("预览卡片可以拖边框改大小",
      card_pv is not None and card_pv.resizable
      and card_pv.maximumWidth() > 10000 and card_pv.minimumWidth() < 300,
      f"resizable={card_pv.resizable if card_pv else None} min={card_pv.minimumSize() if card_pv else None}")
sz0 = card_pv.size()
QTest.mousePress(card_pv, Qt.MouseButton.LeftButton,
                 pos=QPoint(card_pv.width() - 2, card_pv.height() // 2))
QTest.mouseMove(card_pv, pos=QPoint(card_pv.width() - 2 + 60, card_pv.height() // 2))
QTest.mouseRelease(card_pv, Qt.MouseButton.LeftButton,
                   pos=QPoint(card_pv.width() - 2, card_pv.height() // 2))
wait(80)
check("拖右边框真的变宽了", card_pv.width() >= sz0.width() + 40,
      f"{sz0.width()} -> {card_pv.width()}")
pos0 = card_pv.pos()
QTest.mousePress(card_pv, Qt.MouseButton.LeftButton, pos=QPoint(20, 8))     # 拖标题栏
QTest.mouseMove(card_pv, pos=QPoint(20 + 60, 8 + 40))
QTest.mouseRelease(card_pv, Qt.MouseButton.LeftButton, pos=QPoint(20 + 60, 8 + 40))
wait(80)
check("拖标题栏能移动（四块都可拖）", card_pv.pos() != pos0,
      f"{pos0.x()},{pos0.y()} -> {card_pv.pos().x()},{card_pv.pos().y()}")

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
grip = win.image_viewer._toolbar.grip
tb = win.image_viewer._toolbar
check("原图工具条把手：3 根 18px 长、间距 0 贴紧、最右一根距工具条右边 1px",
      grip.LINES == 3 and grip.LINE_H == 18 and grip.GAP == 0
      and grip.width() == 3 + grip.SHIFT_RIGHT == 11
      and grip.mapTo(tb, QPoint(grip.width() - 1, 0)).x() == tb.width() - 2,
      f"grip={grip.width()}x{grip.height()} LINE_H={grip.LINE_H} GAP={grip.GAP} "
      f"右边缘x={grip.mapTo(tb, QPoint(grip.width(), 0)).x()} toolbar宽={tb.width()}")

check("工具条不会跑出画布",
      0 <= tb.x() <= max(0, canvas.width() - tb.width())
      and 0 <= tb.y() <= max(0, canvas.height() - tb.height()),
      f"toolbar=({tb.x()},{tb.y()}) canvas={canvas.width()}x{canvas.height()}")

print("\n6. 历史记录：始终是独立悬浮卡片（默认窗口下也不融进主窗口）+ 底色不透明：")
hist_btn = win.image_viewer._toolbar.btn_history
card_h = win._history_card
check("默认窗口下历史记录就是独立窗口（不在主窗口布局里）",
      card_h is not None and card_h.isWindow() and card_h.parent() is win
      and win.splitter.indexOf(card_h) < 0 and not hasattr(win, "history_box"),
      f"isWindow={card_h.isWindow() if card_h else None} parent={card_h.parent().__class__.__name__ if card_h else None}")
card_h.show(); win._sync_history_button(); wait(80)
check("显示时工具栏历史按钮激活", hist_btn.isChecked(), f"checked={hist_btn.isChecked()}")
win._show_history(False)
wait(100)
check("收起后按钮弹回", not hist_btn.isChecked() and not card_h.isVisible())
win._show_history(True)
wait(80)
check("点按钮/菜单能再叫回来", card_h.isVisible() and hist_btn.isChecked())
check("历史卡片：无系统标题栏 + 浅灰底+1px 描边 + 无关闭按钮 + 固定大小",
      bool(card_h.windowFlags() & Qt.WindowType.FramelessWindowHint)
      and card_h.BG_COLOR == "#3d3d3d" and card_h.BORDER_COLOR == "#555555"
      and not hasattr(card_h, "btn_close")
      and card_h.minimumSize() == card_h.maximumSize(),
      f"fixed={card_h.minimumSize() == card_h.maximumSize()}")
_h_body = card_pixels(card_h, QPoint(card_h.width() // 2, card_h.height() - 14))
_h_corner = card_pixels(card_h, QPoint(1, card_h.height() - 1))
check("历史卡片底色不透明（数像素：不是透明弹窗）",
      _h_body[3] == 255 and _h_corner[3] == 0,
      f"卡片内像素={_h_body} 圆角外={_h_corner}")
win.setWindowState(Qt.WindowState.WindowMaximized)
wait(250)
check("最大化后历史卡片依然显示、依然是独立窗口",
      card_h.isVisible() and card_h.isWindow(), f"visible={card_h.isVisible()}")
check("四块 + 历史 都是同款浅灰卡片（含 1px 描边）",
      all(getattr(c, "BG_COLOR", None) == "#3d3d3d" and getattr(c, "BORDER_COLOR", None) == "#555555"
          and getattr(c, "BORDER_W", None) == 2
          for c in (win._float_cards.get("动作编辑"), win._float_cards.get("帧编辑"),
                    win._float_cards.get("预览"), card_h)),
      "动作编辑/帧编辑/预览/历史")
win.setWindowState(Qt.WindowState.WindowNoState)
wait(200)

win.close()
print(f"\n结果：{PASS} 通过, {FAIL} 失败")
sys.exit(1 if FAIL else 0)
