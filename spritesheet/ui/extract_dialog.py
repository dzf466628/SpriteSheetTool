#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抽帧窗口（初始 500×600，可缩放）：动画预览（悬浮播放工具条）+ 缩略图 + 抽帧选项。

用户口径（原话，按时间顺序）：
  * 「待定按钮改为 抽帧按钮 点击抽帧按钮 新弹出一个 抽帧窗口 500*600大小」
  * 「上面是预览 下面是进度条帧缩略图 再下面是抽帧按钮选项 先放4个 按钮先不用实装」
  * 「窗口里的预览就和预览窗口一样 显示当前动作的预览 下面是缩略图 要显示 帧号还有 相似彩块」
  * 「我说进度条是 播放暂停 拖动的进度条 你看外面的预览界面」「缩略图要大一些」
  * 「预览动作B XXX帧放到 缩略图下面」「播放暂停不是符号」
  * 「抽帧窗口也要可以放大缩小 空格拖动」
  * 「循环按钮 和 帧率调节块 像悬浮工具栏一样 放到抽帧窗口的预览框里（棋盘格上面）」
  * 「没按空格就不要出手的指针」「循环按钮不要标题 按钮加符号就可以」
  * 「循环模式有好几种啊 点击切换」「帧率最快到60」
  * 「抽帧选项1改为 相似度的调整框（不改动设置）每次调节都对当前抽帧动画的相似度进行调节」

要点：
  * 预览按 `compute_preview_layout` 的落点摆**当前帧**（跟右侧预览/导出同一份公式），
    背景用共用的 `paint_checkerboard`（跟外面预览一致的棋盘格）。
  * **循环 + 帧率悬浮在棋盘格上**；循环按外面设置面板那套三档切换（循环/单次/往返，图标同 LOOP_ICONS），
    帧率 1~60；这两个只影响**这个窗口**的播放，不改动作。
  * 选项1 是**本窗口专属的相似度调节框**：改它只重算这个窗口缩略图上的相似彩块，
    **不写设置**（全局设置和右侧帧条都不受影响）。
  * 缩略图共用 `preview.build_frame_thumb`（帧号 + 右上角相似彩块，放大到 64）。
  * 鼠标平时箭头，按住空格才是手；空格状态由 space_pan.py 应用级过滤器喂（它不吞键）。
"""
from __future__ import annotations

import sys

from PyQt6.QtCore import QPointF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QIcon, QPainter
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QDialog, QFrame, QHBoxLayout,
                             QLabel, QListWidget, QListWidgetItem, QProgressDialog,
                             QPushButton, QSizeGrip, QSlider, QVBoxLayout, QWidget)

from .models import Action
from .preview import build_frame_thumb
from .similar import group_color, group_elements
from .transform import action_frame_pixmap, compute_preview_layout
from .widgets import CompactSpinBox, make_tool_icon

try:                                      # 只读全局设置当初始值，**绝不写回**
    from ..settings import similar_tolerance
except Exception:                          # noqa: BLE001 — 读不到就按 0 起，不能挡着窗口开
    def similar_tolerance() -> int:
        return 0

try:                                      # 循环三档的图标/提示跟设置面板共用一份
    from .anim_settings import LOOP_ICONS
except Exception:                          # noqa: BLE001
    LOOP_ICONS = {Action.LOOP: ("loop", "循环播放"), Action.ONCE: ("once", "单次播放"),
                  Action.PINGPONG: ("pingpong", "往返播放")}

THUMB_SIZE = 64          # 用户要求"缩略图要大一些"（右侧预览那边是 36）
THUMB_GRID = 78
DEFAULT_FPS = 12
MAX_FPS = 100            # 用户要求「帧率最高改到100」
FLOAT_MARGIN = 8         # 悬浮工具条离预览框左上角的距离
LOOP_ORDER = (Action.LOOP, Action.ONCE, Action.PINGPONG)


def _paint_checkerboard(p: QPainter, rect) -> None:
    """棋盘格底：优先用共用那份（跟外面预览窗口一致），取不到就自己画一份。"""
    try:
        from .widgets import paint_checkerboard
        paint_checkerboard(p, rect, 12)
        return
    except Exception:  # noqa: BLE001 — 取不到共用实现也不能让预览画不出来
        pass
    cell = 12
    p.fillRect(rect, QColor("#2b2b2b"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#383838"))
    for row in range(rect.height() // cell + 1):
        for col in range(rect.width() // cell + 1):
            if (row + col) % 2 == 0:
                p.drawRect(rect.x() + col * cell, rect.y() + row * cell, cell, cell)


class ExtractPreview(QWidget):
    """动画预览：按落点公式摆**当前帧**（跟右侧预览/导出同一份公式）+ 棋盘格底。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.action = None
        self.frame_index = 0
        self.offset = QPointF(0, 0)
        self._panning = False          # 供 space_pan.SpacePanFilter 喂状态
        self._pan_start = None
        self.float_bar = None          # 由抽帧窗口塞进来（循环 + 帧率）
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumHeight(180)
        self.setStyleSheet("border: 1px solid #333333; border-radius: 4px;")
        self.setCursor(Qt.CursorShape.ArrowCursor)   # 平时就是箭头：没按空格不出手

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        super().resizeEvent(event)
        if self.float_bar is not None:
            self.float_bar.move(FLOAT_MARGIN, FLOAT_MARGIN)
            self.float_bar.raise_()

    def set_action(self, action) -> None:
        self.action = action
        self.update()

    def set_frame(self, index: int) -> None:
        self.frame_index = max(0, int(index))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        super().paintEvent(event)
        p = QPainter(self)
        _paint_checkerboard(p, self.rect())      # 棋盘格底（跟外面预览一致）
        action = self.action
        if action is None or not len(action.frames):
            p.end()
            return
        index = min(max(self.frame_index, 0), len(action.frames) - 1)
        canvas_w, canvas_h, positions = compute_preview_layout(action)[:3]
        if canvas_w <= 0 or canvas_h <= 0:
            p.end()
            return
        # 落点是元组，必须**按下标取** —— 直接拆成两个会 ValueError，而 paintEvent 里抛异常
        # 会把整个进程打死（用户实测「抽帧窗口不显示」就是这个：QBackingStore::endPaint 刷屏）
        spots = [(spot[0], spot[1]) for spot in positions]
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        zoom = min((self.width() - 16) / canvas_w, (self.height() - 16) / canvas_h)
        zoom = max(0.05, min(zoom, 8.0))
        base_x = (self.width() - canvas_w * zoom) / 2 + self.offset.x()
        base_y = (self.height() - canvas_h * zoom) / 2 + self.offset.y()
        # **只画当前帧**：用户明确「显示的是所有的帧叠加啊！」—— 叠着就看不出动画了
        px, py = spots[index]
        pm = action_frame_pixmap(action, index)
        if pm is not None and not pm.isNull():
            p.drawPixmap(
                QPointF(base_x + px * zoom, base_y + py * zoom),
                pm.scaled(max(1, int(pm.width() * zoom)), max(1, int(pm.height() * zoom)),
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation))
        p.end()

    # ---- 空格拖动（状态由 space_pan 喂；这里只管鼠标与光标）----
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._panning and event.button() == Qt.MouseButton.LeftButton:
            self._pan_start = event.position() - self.offset
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._panning and self._pan_start is not None:
            self.offset = event.position() - self._pan_start
            self.update()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._pan_start = None
        if self._panning:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)


class ExtractDialog(QDialog):
    """抽帧窗口：动画预览（悬浮播放工具条）+ 大缩略图 + 相似度框 + 占位选项。"""

    OPTION_TITLES = ("抽帧选项 2", "抽帧选项 3", "抽帧选项 4")

    def __init__(self, action=None, color_provider=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("抽帧")
        self.resize(390, 600)                # 用户要求默认宽度 390（高度仍 600），**可以放大缩小**
        self.setMinimumSize(380, 440)
        self.setSizeGripEnabled(False)       # 自带的在右下角，按用户要求改成左下角（见 resizeEvent）
        self._grip = None
        self._closing = False
        self.setStyleSheet("QDialog { background: #1a1a1a; }")
        self.color_provider = color_provider
        self._action = None
        self._frame = 0
        self._fps = DEFAULT_FPS
        self._loop_mode = Action.LOOP
        self._direction = 1
        # 本窗口专属的相似度标记（**不写设置**）：值一改就按新容差重算
        self._local_marks: dict[int, QColor] = {}
        self._marks_gid: dict[int, int] = {}      # 相似组号（算"多少块"用）
        self._blocks = 0                          # 当前有多少块（= 强力去重后的帧数）
        self._pending_reapply = False             # 刚调过相似度 → 重算完要按选中档位再抽一次
        self._tolerance = int(similar_tolerance())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.title = QLabel("预览")
        self.title.setStyleSheet("color: #F06292; font-weight: bold; font-size: 12px;")

        self.preview = ExtractPreview(self)
        layout.addWidget(self.preview, 1)
        self._build_float_bar()              # 循环 + 帧率：悬浮压在棋盘格上

        # ---- 播放控制条：播放/暂停（符号）+ 可拖动的进度条 + 帧号 ----
        row_play = QHBoxLayout()
        row_play.setSpacing(6)
        self.btn_play = QPushButton()
        self.btn_play.setIcon(make_tool_icon("play", 16))
        self.btn_play.setFixedSize(30, 24)
        self.btn_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_play.setToolTip("播放/暂停当前动作（只影响这个窗口的预览）")
        self.btn_play.clicked.connect(self._toggle_play)
        row_play.addWidget(self.btn_play)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.valueChanged.connect(self._on_slider)
        row_play.addWidget(self.slider, 1)
        self.lbl_frame = QLabel("1 / 1")
        self.lbl_frame.setFixedWidth(64)
        self.lbl_frame.setStyleSheet("color: #DDD; font-size: 11px;")
        row_play.addWidget(self.lbl_frame)
        layout.addLayout(row_play)

        self.thumb_list = QListWidget()
        self.thumb_list.setFixedHeight(THUMB_GRID + 8)
        self.thumb_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.thumb_list.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self.thumb_list.setResizeMode(QListWidget.ResizeMode.Fixed)
        self.thumb_list.setMovement(QListWidget.Movement.Static)
        self.thumb_list.setFlow(QListWidget.Flow.LeftToRight)
        self.thumb_list.setWrapping(False)
        self.thumb_list.setSpacing(0)
        self.thumb_list.setGridSize(QSize(THUMB_GRID, THUMB_GRID))
        self.thumb_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.thumb_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.thumb_list.setStyleSheet(
            "QListWidget { background: #1f1f1f; border: 1px solid #333; border-radius: 4px;"
            " padding: 4px; }"
            "QListWidget::item { border: 1px solid #555555; border-radius: 3px;"
            " background: transparent; }"
            "QListWidget::item:selected { background: rgba(233,30,99,0.25);"
            " border: 1px solid #E91E63; }")
        self.thumb_list.currentRowChanged.connect(self._on_thumb_row)
        layout.addWidget(self.thumb_list)

        layout.addWidget(self.title)      # 「预览 · 动作名（N 帧）」在缩略图下面（用户指定位置）
        self.hint = QLabel("按住空格 + 左键拖动预览；拖进度条逐帧看；棋盘格左上角调循环/帧率")
        self.hint.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        layout.addWidget(self.hint)

        # ---- 选项行：第一格是「相似度」调节框，其余先占位 ----
        row = QHBoxLayout()
        row.setSpacing(6)
        lbl_sim = QLabel("相似度")
        lbl_sim.setStyleSheet("color: #DDD; font-size: 11px; background-color: #1a1a1a;")
        row.addWidget(lbl_sim)
        self.spin_similar = CompactSpinBox()
        self.spin_similar.setRange(0, 100)
        self.spin_similar.setValue(self._tolerance)
        self.spin_similar.setFixedWidth(56)
        # 要能直接键盘敲数字（用户实测敲不进去）—— 所以给正常焦点；
        # 空格不会跑到这儿来捣乱：space_pan 过滤器对输入框是放行的，不吞键
        self.spin_similar.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # 用户口径：改完相似度「回车就开始」—— 敲回车（或离开输入框）立刻算，
        # 不等那 2 秒；不改动的话 2 秒后自动开始。
        self.spin_similar.editingFinished.connect(self._recalc_now)
        self.spin_similar.setToolTip(
            "相似度容差：0 = 只认一模一样。\n"
            "改这里**只重算这个窗口缩略图上的相似彩块**，不动设置、不影响主界面。")
        self.spin_similar.valueChanged.connect(self._on_tolerance)
        row.addWidget(self.spin_similar)
        row.addStretch(1)   # 相似度在左、抽帧按钮全部靠右（用户要求「抽帧按钮右对齐」）
        # 4 颗**互斥**抽帧按钮：按"抽掉多少"由轻到重，强力去重放最右（用户指定顺序）；
        # 文字带"抽帧"前缀（用户要求不要只写分数），选中后高亮，且不能同时选
        self._decimate_group = QButtonGroup(self)
        self._decimate_group.setExclusive(True)
        self.decimate_buttons: list[QPushButton] = []
        for text, mode, tip in (("抽帧3/5", "keep_3_5", "一串相邻相似帧只留 3/5"),
                                ("抽帧1/2", "keep_1_2", "一串相邻相似帧只留一半"),
                                ("抽帧1/4", "keep_1_4", "一串相邻相似帧只留四分之一"),
                                ("抽帧强力去重", "dedupe", "一串相邻相似帧只留 1 帧")):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setProperty("mode", mode)     # 调完相似度要按"当前选中档位"重抽
            btn.setFixedHeight(26)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # 别抢空格键
            btn.setToolTip(f"{tip}\n点一下立刻抽帧（实时显示）。撤回方式：在窗口里点「取消」"
                           "（普通工程也可以按 Ctrl+Z）。只动当前动作，不改设置。")
            btn.setStyleSheet(
                "QPushButton { background: #2a2a2a; color: #CCC; border: 1px solid #444;"
                " border-radius: 3px; font-size: 11px; padding: 0 6px; }"
                "QPushButton:checked { background: #E91E63; color: #FFFFFF;"
                " border: 1px solid #FF6090; font-weight: bold; }")
            btn.clicked.connect(lambda _c, m=mode, b=btn: self._apply_decimate(b, m))
            self._decimate_group.addButton(btn)
            row.addWidget(btn)
            self.decimate_buttons.append(btn)   # 把多余空白收在这一行末尾：否则会被"相似度"标签吃掉，看着右边空一大截
        layout.addLayout(row)

        # ---- 页脚：一条分界线 + 「确定」（用户要求去掉取消按钮、往下一些、加分隔线）----
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet("background: #3a3a3a; border: none;")
        layout.addWidget(line)
        layout.addSpacing(4)
        row_footer = QHBoxLayout()
        row_footer.addStretch(1)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setFixedSize(70, 26)
        self.btn_cancel.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # 别抢空格键
        self.btn_cancel.setToolTip("把这轮抽帧整个撤回并关闭（等同按 Esc）")
        self.btn_cancel.clicked.connect(self._on_cancel)
        row_footer.addWidget(self.btn_cancel)
        self.btn_ok = QPushButton("确定")
        self.btn_ok.setFixedSize(70, 26)
        self.btn_ok.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_ok.setToolTip("保留这个窗口里的调节并关闭（抽帧结果已经实时落到当前动作，"
                               "Ctrl+Z 可撤回）")
        self.btn_ok.clicked.connect(self.accept)
        row_footer.addWidget(self.btn_ok)
        layout.addLayout(row_footer)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        # 改相似度要重算（几百帧会比较慢），攒一下再算，别每敲一下就卡一次
        self._recalc = QTimer(self)
        self._recalc.setSingleShot(True)
        self._recalc.timeout.connect(self._recalc_marks)
        self.set_action(action)

    def _build_float_bar(self) -> None:
        """悬浮播放工具条：压在预览的棋盘格上（循环切换按钮 + 竖着的帧率条）。

        用户要求「像悬浮工具栏一样 放到抽帧窗口的预览框里（棋盘格上面）」「循环按钮不要标题
        就像外面下方的循环按钮一样 按钮加符号就可以」「循环模式有好几种啊 点击切换」——
        所以是纯符号的**切换**按钮，三档图标跟设置面板共用 LOOP_ICONS。
        """
        bar = QWidget(self.preview)
        bar.setObjectName("extractFloatBar")
        bar.setStyleSheet(
            "#extractFloatBar { background: rgba(20,20,20,0.78); border: 1px solid #3a3a3a;"
            " border-radius: 6px; }")
        col = QVBoxLayout(bar)
        col.setContentsMargins(4, 4, 4, 4)
        col.setSpacing(4)
        self.btn_loop = QPushButton()
        self.btn_loop.setCheckable(False)        # 改成"点击切换档位"（三档），不是开关
        self.btn_loop.setFixedSize(26, 24)
        self.btn_loop.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # 别抢空格键
        self.btn_loop.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self.btn_loop.clicked.connect(self._cycle_loop)
        col.addWidget(self.btn_loop)
        self.speed = QSlider(Qt.Orientation.Vertical)   # 竖着的帧率速度条
        self.speed.setRange(1, MAX_FPS)                 # 用户要求：最快到 60
        self.speed.setValue(DEFAULT_FPS)
        self.speed.setFixedWidth(22)
        self.speed.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.speed.setToolTip(f"播放帧率 1~{MAX_FPS}（只影响这个窗口的预览，不改动作本身）")
        self.speed.valueChanged.connect(self._on_speed)
        col.addWidget(self.speed, 1)
        self.lbl_speed = QLabel(str(DEFAULT_FPS))
        self.lbl_speed.setStyleSheet("color: #DDD; font-size: 10px; border: none;")
        self.lbl_speed.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(self.lbl_speed)
        bar.setFixedWidth(36)
        bar.setFixedHeight(140)
        bar.move(FLOAT_MARGIN, FLOAT_MARGIN)
        bar.show()
        self.float_bar = bar
        self.preview.float_bar = bar
        self._sync_loop_button()

    # ---- 循环档位（只影响本窗口播放）----
    def _sync_loop_button(self) -> None:
        icon_kind, tip = LOOP_ICONS.get(self._loop_mode, ("loop", "循环播放"))
        self.btn_loop.setIcon(make_tool_icon(icon_kind, 16))
        self.btn_loop.setToolTip(f"循环模式：{tip}（点击切换，只影响这个窗口的预览）")

    def _cycle_loop(self) -> None:
        idx = LOOP_ORDER.index(self._loop_mode) if self._loop_mode in LOOP_ORDER else 0
        self._loop_mode = LOOP_ORDER[(idx + 1) % len(LOOP_ORDER)]
        self._direction = 1
        self._sync_loop_button()

    # ---- 播放控制（全部只影响本窗口预览）----
    def _frame_ms(self) -> int:
        return max(16, int(1000 / max(1, self._fps)))

    def _on_speed(self, value: int) -> None:
        self._fps = max(1, int(value))
        self.lbl_speed.setText(str(self._fps))
        if self._timer.isActive():               # 正在播放：立刻按新帧率走
            self._timer.start(self._frame_ms())

    def _toggle_play(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            self.btn_play.setIcon(make_tool_icon("play", 16))
        else:
            self._timer.start(self._frame_ms())
            self.btn_play.setIcon(make_tool_icon("pause", 16))

    def _stop_play(self) -> None:
        self._timer.stop()
        self.btn_play.setIcon(make_tool_icon("play", 16))

    def _tick(self) -> None:
        total = len(self._action.frames) if self._action is not None else 0
        if total < 2:
            return
        if self._loop_mode == Action.PINGPONG:
            nxt = self._frame + self._direction
            if nxt >= total:
                self._direction = -1
                nxt = max(0, total - 2)
            elif nxt < 0:
                self._direction = 1
                nxt = min(total - 1, 1)
        else:
            nxt = self._frame + 1
            if nxt >= total:
                if self._loop_mode == Action.ONCE:   # 单次：停在最后一帧
                    self._set_frame(total - 1)
                    self._stop_play()
                    return
                nxt = 0
        self._set_frame(nxt)

    def _on_slider(self, value: int) -> None:
        if value != self._frame:
            self._set_frame(value)

    def _on_thumb_row(self, row: int) -> None:
        if row >= 0 and row != self._frame:
            self._set_frame(row)

    def _set_frame(self, index: int) -> None:
        self._frame = index
        self.slider.blockSignals(True)
        self.slider.setValue(index)
        self.slider.blockSignals(False)
        self.thumb_list.blockSignals(True)
        self.thumb_list.setCurrentRow(index)
        self.thumb_list.blockSignals(False)
        self.preview.set_frame(index)
        total = len(self._action.frames) if self._action is not None else 0
        self.lbl_frame.setText(f"{index + 1} / {max(1, total)}")

    # ---- 相似度（只影响本窗口的彩块，**不写设置**）----
    def _recalc_now(self) -> None:
        """回车（或离开输入框）：**立刻**按当前相似度重算，不等那 2 秒的攒批。

        是否"重算完还要按当前档位再抽一次"由 `_on_tolerance` 设的那个标记决定，
        所以只是点一下输入框又移开焦点（值没变）不会白抽一次。
        """
        self._recalc.stop()
        self._recalc_marks()

    def _on_tolerance(self, value: int) -> None:
        """相似度一变：**从当前动作的所有帧重新算一遍**（用户口径：相当于重新计算）。

        已经选了抽帧档位的话，重算完还要按这一档重新抽一次（基准就是现在的帧表）。
        """
        self._tolerance = int(value)
        self._pending_reapply = True
        self._recalc.start(2000)         # 用户口径：不动之后等 2 秒再算（回车可立即执行）

    def hideEvent(self, event):
        """窗口一藏起来（确定 / 取消 / Esc / 外部关闭 / 程序退出）就把**所有定时器停掉**。

        这是本工具踩过的老坑：带着还在跑的 QTimer 被拆窗口，会变成
        `QWindowsBackingStore::flush: GetDC failed` + `External WM_DESTROY` 刷屏，
        严重时退出卡住。播放 timer 和相似度攒批 timer 都必须在这里停表。
        """
        try:
            self._timer.stop()
            self._recalc.stop()
            self._pending_reapply = False
        except Exception:  # noqa: BLE001
            pass
        QDialog.hideEvent(self, event)

    def reject(self) -> None:  # noqa: N802 (Qt 命名)
        """Esc / 右上角关闭 = 和原来那颗「取消」一样：把这轮抽帧撤回再关。

        取消按钮已按用户要求去掉，但"关窗口就撤回"的口径保持不变 —— 免得 Esc 关掉之后
        留下一份谁也没认领的抽帧结果。
        """
        if not self._closing:
            self._closing = True
            try:
                self._on_cancel()
            finally:
                self._closing = False
            return
        QDialog.reject(self)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """把缩放抓柄摆到**左下角**（用户要求：右下角那个方块放到左边）。"""
        super().resizeEvent(event)
        if self._grip is None:
            self._grip = QSizeGrip(self)
            self._grip.setFixedSize(14, 14)
            self._grip.setToolTip("拖这里可以调整窗口大小")
        self._grip.move(2, max(0, self.height() - self._grip.height() - 2))
        self._grip.raise_()

    def _main_window(self):
        """抽帧要落到当前动作、还要走撤销快照，所以真正干活的是主窗口（父窗口）。"""
        win = self.parent()
        return win if hasattr(win, "_on_extract_decimate") else None

    def _apply_decimate(self, btn, mode: str, from_current: bool = False) -> None:
        """点档位：立刻抽帧 → 立刻刷新这个窗口的预览和缩略图（实时显示）。

        帧多的时候给一个转圈进度框（用户说"如果慢可以做进度条"）。抽帧本身由主窗口做，
        它负责推撤销快照；这里只负责把结果画出来。
        """
        win = self._main_window()
        if win is None:
            btn.setChecked(False)
            print("[extract] 找不到主窗口，抽帧没法应用", file=sys.stderr)
            return
        action = self._action
        total = len(action.frames) if action is not None else 0
        busy = None
        if total >= 40:                      # 帧多才弹进度（少帧一瞬间就完，弹窗反而碍事）
            busy = QProgressDialog("正在抽帧……", "", 0, max(1, total * 2), self)
            busy.setWindowTitle("抽帧")
            busy.setCancelButton(None)       # **真进度条**（用户要求），不给取消
            busy.setMinimumDuration(0)
            busy.setValue(0)
            busy.show()
            QApplication.processEvents()

        def _tick(done, all_units):
            # 抽帧窗口是应用级模态 ⇒ 用户点不到别处，这里 processEvents 刷进度是安全的
            if busy is None:
                return
            busy.setMaximum(max(1, int(all_units)))
            busy.setValue(min(int(done), int(all_units)))
            busy.setLabelText(f"正在抽帧…… {min(int(done), int(all_units))}/{int(all_units)}")
            QApplication.processEvents()

        try:
            count, message = win._on_extract_decimate(mode, self._tolerance,
                                                      from_current=from_current,
                                                      progress=_tick)
        finally:
            if busy is not None:
                busy.close()
                self._busy = busy           # 留引用：带着它被回收会出问题
        self.set_action(self._action)        # 实时显示抽帧后的帧表和彩块
        self.hint.setText(message or f"已抽帧，现在 {count} 帧")
        if count <= 0:
            btn.setChecked(False)

    def _on_cancel(self) -> None:
        """取消：把这一轮抽帧整个撤回去（撤回由主窗口做：它有原始状态和帧重建通道），
        再把这个窗口里的调节恢复默认（循环档/帧率/相似度）。外面本来就只被"抽帧"动过，
        而抽帧正是这里要撤回的那件事。
        """
        win = self._main_window()
        # 先把还没到点的重算/重抽掐掉：否则取消之后 300ms 那把刀还会落下来 ——
        # 用户实测「取消以后 显示正在抽帧 然后就改变原图的帧选择了」就是这个
        self._recalc.stop()
        self._pending_reapply = False
        # 抽帧已经撤回了，档位高亮也要清掉 —— 否则下次开窗口会看到"选中了某档"却没抽帧，
        # 等于骗人（对话框是复用实例，勾选状态会跨次留存）
        for _btn in self.decimate_buttons:
            _btn.setChecked(False)
        self._blocks = 0
        if win is not None and getattr(win, "_extract_origin", None) is not None:
            try:
                win._cancel_extract_state()
            except Exception as e:  # noqa: BLE001 — 撤回失败也要能关窗口
                print(f"[extract] 撤回抽帧失败: {e!r}", file=sys.stderr)
        self._timer.stop()
        self.btn_play.setIcon(make_tool_icon("play", 16))
        self._loop_mode = Action.LOOP
        self._direction = 1
        self._sync_loop_button()
        self.speed.setValue(DEFAULT_FPS)
        self.spin_similar.setValue(int(similar_tolerance()))
        self._set_frame(0)
        self._recalc.stop()
        self._pending_reapply = False
        # **取消 = 直接抛弃**（用户口径）。窗口马上就被关了，这里**绝不再刷新它**：
        # 刷新会重新算一遍相似度、并按帧数重建全部缩略图（几百帧就是好几秒）——
        # 那正是"点取消卡住"的来源：窗口无响应 → Windows 从外部销毁它 →
        # 刷屏 "External WM_DESTROY received for ..."。撤回本身由主窗口完成（轻量）。
        QDialog.reject(self)                  # 显式走 QDialog 的 reject：不要再触发本类的 reject

    def _mark_for(self, index: int):
        return self._local_marks.get(index)

    def _recalc_marks(self) -> None:
        """按当前容差重算相似组 → 只刷新这个窗口的缩略图彩块（设置与主界面都不动）。"""
        action = self._action
        self._local_marks = {}
        if action is None or len(action.frames) < 2:
            self._refresh_thumbs()
            return
        from spritesheet.ui.similar import split_runs  # noqa: PLC0415 — 只为算"多少块"
        pixmaps = [action_frame_pixmap(action, i) for i in range(len(action.frames))]
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            marks = group_elements(pixmaps, tolerance=self._tolerance)
            self._marks_gid = dict(marks)
            self._local_marks = {i: group_color(g) for i, g in marks.items()}
        except Exception as e:  # noqa: BLE001 — 算不出来就别标色，窗口不能因此报错
            print(f"[extract] 相似度重算失败: {e!r}", file=sys.stderr)
            self._marks_gid = {}
            self._local_marks = {}
        finally:
            QApplication.restoreOverrideCursor()
        # 「多少块」：相邻同组的算一块、没配对的每帧自己一块 —— 等于强力去重后的帧数
        total = len(action.frames)
        runs = split_runs(self._marks_gid, total)
        blocks = len(runs) + (total - sum(len(r) for r in runs))
        self._blocks = blocks
        self._refresh_thumbs()
        if self._pending_reapply:
            self._pending_reapply = False
            checked = next((b for b in self.decimate_buttons if b.isChecked()), None)
            if checked is not None and self.isVisible():
                # 重算后按当前档位再抽一次。基准仍然是**这个动作的所有帧**（主窗口会先还原
                # 到原始帧表），所以这里不用 from_current —— 用户要求：换档/重算都从所有帧来
                self._apply_decimate(checked, checked.property("mode"))

    # ---- 主窗口推内容进来 ----
    def set_action(self, action) -> None:
        """换动作：预览 + 进度条 + 缩略图一起刷（缩略图带帧序号和相似帧彩块）。

        包在 try 里：这个方法会被主窗口从 **Qt 槽**里调用，槽里抛异常会直接打死进程
        （0xC0000409 那一类，看不到 traceback）。出错就打一行日志，窗口照样开着。
        """
        try:
            self._set_action_inner(action)
        except Exception as e:  # noqa: BLE001
            print(f"[extract] 刷新抽帧窗口内容失败: {e!r}", file=sys.stderr)

    def _set_action_inner(self, action) -> None:
        if action is not self._action:
            self._action = action
            self._frame = 0
            self._direction = 1
            self.preview.offset = QPointF(0, 0)
            self._local_marks = {}
        self.preview.set_action(action)
        self._set_frame(min(self._frame, max(0, len(action.frames) - 1))
                        if action is not None and len(action.frames) else 0)
        self._recalc_marks()             # 换动作要按当前容差重新标一遍

    def _refresh_thumbs(self) -> None:
        """重建缩略图（帧号 + 右上角相似彩块），保留当前帧。"""
        action = self._action
        keep = self._frame
        self.thumb_list.blockSignals(True)
        self.thumb_list.clear()
        total = len(action.frames) if action is not None else 0
        if not total:
            self.title.setText("预览（还没有动作）")
            self.slider.setRange(0, 0)
            self.lbl_frame.setText("1 / 1")
            self.thumb_list.blockSignals(False)
            return
        self.title.setText(f"预览 · {getattr(action, 'name', '')}"
                           f"（{total} 帧 · {self._blocks} 块）")
        self.slider.setRange(0, total - 1)
        for i in range(total):
            color = self._mark_for(i)
            if color is None and callable(self.color_provider) and self._tolerance == int(similar_tolerance()):
                color = self.color_provider(action, i)   # 容差没改时沿用主界面那份标记
            item = QListWidgetItem()
            item.setIcon(QIcon(build_frame_thumb(action, i, color, THUMB_SIZE)))
            item.setSizeHint(QSize(THUMB_GRID, THUMB_GRID))
            self.thumb_list.addItem(item)
        self.thumb_list.blockSignals(False)
        self._set_frame(min(keep, total - 1))