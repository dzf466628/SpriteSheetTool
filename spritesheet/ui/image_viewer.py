#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""原图查看器：元素检测 + 标尺画布 + 浮动工具条（从 main_window.py 拆出，纯搬移，逻辑未改动）。

包含：
- ``SUPPORTED_IMAGE_EXT`` / ``MIN_ZOOM`` / ``MAX_ZOOM``：查看器级常量；
- ``_detect_frames_image`` / ``_FrameDetectThread``：alpha 连通域元素检测（后台线程）；
- ``_Canvas``：原图画布（棋盘格背景、滚轮缩放、空格拖动、点选/框选、拖放载入）；
- ``FloatingToolBar``：画布右上角浮动工具条（合成 / 复用 / 缩放 / 三种背景）；
- ``_AltNativeFilter``：Windows 原生 Alt 系统键监听（顶层类，不并入 ImageViewer）；
- ``ImageViewer``：顶部水平标尺 + 左侧垂直标尺 + 画布 + 工具条，并承载帧复用
  （frame_layouts）与元素逻辑坐标（合成帧拼接间距）等核心交互。

依赖方向：models ← transform ← widgets ← image_viewer（本模块不依赖 main_window）。
"""
from __future__ import annotations

import struct
import sys
import threading
import traceback
from pathlib import Path

from PyQt6.QtCore import (
    QAbstractNativeEventFilter, QEvent, QPointF, QRect, QRectF, QSize, QThread, QTimer, Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QMenu,
    QPushButton, QVBoxLayout, QWidget,
)

from spritesheet.settings import last_directory, set_last_directory
from .models import Action
from .widgets import (
    RULER_SIZE, ColorSwatchButton, CompactSpinBox, HintToast, _Ruler, make_tool_icon,
    paint_checkerboard,
)


SUPPORTED_IMAGE_EXT = {".png", ".webp", ".jpeg", ".jpg", ".gif", ".bmp"}
MIN_ZOOM = 0.05
MAX_ZOOM = 20
# 帧号色块：边长 16，与下一块间隔 2px（步进 18）。抽成常量是因为"落点会不会撞"
# 也要用同一套步进量来判断（见 paintEvent 里合成框的 slot 分组）。
BADGE_SQ = 16
BADGE_STEP = BADGE_SQ + 2

# 无动作时点画布/控件的统一提示（与预览面板同一句，避免文案漂移）
NO_ACTION_HINT = "请先添加动作：点左侧「+ 添加动作」"


def _detect_frames_image(img: QImage, alpha_threshold: int = 10,
                         min_area: int = 0,
                         cancel=None) -> list[tuple[int, int, int, int]]:
    """在工作线程中检测 QImage 的连通区域（行程 + 并查集）。

    结果与最初的逐像素 4 连通 flood fill **完全等价**：同一批连通域、同样的包围盒、
    同样的发现顺序（自上行下行、同行自左向右）。区别只在实现：

    1. alpha 通道一次切出，再用 ``bytes.translate`` 把「是否达标」压成 0/1 掩码
       （全图一次 C 层调用）；
    2. 逐行用 ``split(b"\\x00")`` 切出**连续不透明行程**，Python 只遍历行程，
       不再遍历像素；
    3. 相邻两行的行程若列区间有重叠（共享至少一列 = 上下相邻 = 4 连通）就合并，
       用并查集维护连通域。

    复杂度从 O(w*h) 次 Python 迭代降到 O(行程数) 次；2048x2048 实测约 1.4s → 数十毫秒。
    """
    if img is None or img.isNull():
        return []
    threshold = int(alpha_threshold)
    if threshold > 255:
        return []  # alpha 上限 255，不可能有像素达标（与原实现等价）
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return []
    # 一次性复制连续像素，避免逐行创建 bytes 和重复 scanLine 调用。
    ptr = img.bits()
    ptr.setsize(w * h * 4)
    data = bytes(ptr)
    stride = img.bytesPerLine()
    if stride == w * 4:
        alpha = data[3::4]           # 快路径：每像素 4 字节且行已 4 字节对齐
    else:                            # 兜底：逐行取 alpha，丢掉行尾 padding
        alpha = b"".join(
            data[y * stride + 3:y * stride + w * 4:4] for y in range(h)
        )

    # 达标 → 1，不达标 → 0；threshold <= 0 时全部为 1（与 alpha >= threshold 等价）
    table = bytes(1 if v >= threshold else 0 for v in range(256))
    mask = alpha.translate(table)

    # ---- 1) 逐行切出行程 (y, x0, x1)，均为闭区间 ----
    runs: list[tuple[int, int, int]] = []
    for y in range(h):
        if cancel is not None and cancel():
            return []                    # 所属窗口已关闭：尽快退出，避免继续访问
        row = mask[y * w:(y + 1) * w]
        if b"\x01" not in row:           # 整行无有效像素（C 层一次判定）
            continue
        pos = 0
        for seg in row.split(b"\x00"):
            length = len(seg)
            if length:
                runs.append((y, pos, pos + length - 1))
            pos += length + 1            # +1 跳过被 split 吃掉的这个分隔字节
    if not runs:
        return []

    # ---- 2) 相邻行行程两两合并（并查集 + 双指针）----
    parent = list(range(len(runs)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]   # 路径折半
            a = parent[a]
        return a

    by_row: dict[int, list[int]] = {}
    for idx, (y, _x0, _x1) in enumerate(runs):
        by_row.setdefault(y, []).append(idx)

    for idx, (y, x0, x1) in enumerate(runs):
        prev = by_row.get(y - 1)
        if not prev:
            continue
        j = 0
        while j < len(prev) and runs[prev[j]][2] < x0:
            j += 1                       # 跳过在上行中、结束于本行程左侧的行程
        k = j
        while k < len(prev) and runs[prev[k]][1] <= x1:
            ra, rb = find(idx), find(prev[k])
            if ra != rb:
                parent[ra] = rb
            k += 1

    # ---- 3) 汇总包围盒 + 复现发现顺序 ----
    boxes: dict[int, list[int]] = {}     # root -> [left, top, right, bottom]
    first: dict[int, tuple[int, int]] = {}   # root -> 发现点（最上行、该行最左列）
    for idx, (y, x0, x1) in enumerate(runs):
        root = find(idx)
        box = boxes.get(root)
        if box is None:
            boxes[root] = [x0, y, x1, y]
            first[root] = (y, x0)
        else:
            if x0 < box[0]:
                box[0] = x0
            if x1 > box[2]:
                box[2] = x1
            if y > box[3]:
                box[3] = y

    found = [(first[root][0], first[root][1],
              box[0], box[1], box[2] - box[0] + 1, box[3] - box[1] + 1)
             for root, box in boxes.items()]
    found.sort(key=lambda f: (f[0], f[1]))   # 与逐像素扫描的发现顺序一致
    result = [(left, top, width, height) for _, _, left, top, width, height in found]
    if min_area > 0:
        result = [r for r in result if max(r[2], r[3]) > min_area]
    return result


class _FrameDetectThread(QThread):
    """后台检测线程；只传递 QImage，避免在线程间操作 QPixmap。

    支持协作式取消：窗口关闭时调用 ``cancel()``，线程会在下一行边界退出，
    调用方 ``wait()`` 等它结束后再销毁窗口，避免「线程还在跑、宿主已析构」的原生崩溃。
    """

    detected = pyqtSignal(list)

    def __init__(self, image: QImage, min_area: int = 0, parent=None) -> None:
        super().__init__(parent)
        self._image = image
        self._min_area = min_area
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self) -> None:
        frames = _detect_frames_image(self._image, min_area=self._min_area, cancel=self._cancelled.is_set)
        if self._cancelled.is_set():
            return                       # 已取消：不再向可能正在析构的对象发信号
        self.detected.emit(frames)


# 画布区（绘制图片 + 鼠标交互）
class _Canvas(QWidget):
    """原图画布：背景 + 图片绘制，滚轮缩放，空格拖动，点击/拖放添加。"""

    image_loaded = pyqtSignal(QPixmap)
    json_selected = pyqtSignal(str)
    selection_changed = pyqtSignal()

    # 判定"点击"还是"拖动"的阈值（画布像素）：小于它算点击（只选中），大于它才算拖动
    SRC_DRAG_MIN = 4

    def __init__(self, viewer, parent=None) -> None:
        super().__init__(parent)
        self._viewer = viewer
        self.setMinimumSize(400, 300)
        self.setStyleSheet("background-color: #141414; border: none;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self._drag_start = None
        self._panning = False
        self._pan_start = None
        self._selection_rect = None
        # 原图选择/拖动的手势状态：起点、是否已超过阈值、是否正在拖动移位、上一次拖动点
        self._src_press = None
        self._src_moved = False
        self._src_moving = False
        self._src_applied = False      # 这一把拖动里有没有真的挪动过（夹住时为 False）
        self._src_last = None
        # 无动作时的提示气泡（"先添加动作"），画在画布底部
        self._toast = HintToast(self)
        # 悬停到标线上时换的光标（None = 没用标线光标）。缓存一份，避免每个
        # 鼠标事件都重设光标（Qt 每次 setCursor 都会走一遍控件光标栈）。
        self._guide_cursor = None
        # 右键落点（画布坐标）：build_context_menu 与"取消选中"都读它
        self._context_pos = QPointF()

    def hint_no_action(self) -> None:
        self._toast.show(NO_ACTION_HINT)

    def resizeEvent(self, event):
        toolbar = getattr(self._viewer, "_toolbar", None)
        if toolbar is not None:
            if toolbar.moved_by_user():
                # 用户自己拖过：只保证不跑出画布，不再强行搬回右上角
                toolbar.move(max(0, min(toolbar.x(), self.width() - toolbar.width())),
                             max(0, min(toolbar.y(), self.height() - toolbar.height())))
            else:
                # 工具条向右加宽 3px 后保持左缘不动（右边距 6 → 3），把手才会真正右移
                toolbar.move(self.width() - toolbar.width() - 3, 6)
        badge = getattr(self._viewer, "_json_badge", None)
        if badge is not None:
            badge.move(6, 6)
        # 左侧竖条工具条：默认贴左边缘（JSON 徽标下方），用户拖过则只保证不跑出画布
        v_bar = getattr(self._viewer, "_v_toolbar", None)
        if v_bar is not None:
            if v_bar.moved_by_user():
                v_bar.move(max(0, min(v_bar.x(), self.width() - v_bar.width())),
                           max(0, min(v_bar.y(), self.height() - v_bar.height())))
            else:
                v_bar.move(VerticalToolBar.HOME_X, VerticalToolBar.HOME_Y)
        # 「工具选项」条：跟着画布宽度走（会自己躲开两条浮动工具条，见 _place_paint_bar）
        self._viewer._place_paint_bar()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # 背景
        if self._viewer._bg_mode == "checker":
            # 一次平铺（原逐格 fillRect 在大画布上每次重绘要数千次调用）
            paint_checkerboard(p, self.rect(), 12)
        elif self._viewer._bg_mode == "black":
            p.fillRect(self.rect(), QColor("#000000"))
        else:
            p.fillRect(self.rect(), QColor("#FFFFFF"))

        if self._viewer._pixmap and not self._viewer._pixmap.isNull():
            zoom = self._viewer._zoom
            offset = self._viewer._offset
            live = self._viewer._paint_display
            if live is not None:
                # 正在画一笔：直接画这份 QImage 工作图（画笔/橡皮都落在 QImage 上 ——
                # 见 ImageViewer._paint_copy_of 里关于 QPixmap 丢 alpha 的说明）
                p.drawImage(QRectF(offset.x(), offset.y(), live.width() * zoom,
                                   live.height() * zoom),
                            live, QRectF(live.rect()))
            else:
                # 抠图预览：有预览图就画预览图（尺寸与原图相同）。只在这里换，不动 _pixmap，
                # 所以撤销栈、帧、动作在预览期间完全不受影响。
                shown = self._viewer._preview_pixmap or self._viewer._pixmap
                target = QRectF(offset.x(), offset.y(),
                                shown.width() * zoom,
                                shown.height() * zoom)
                p.drawPixmap(target, shown, QRectF(shown.rect()))

            # 原图分块框（「原图选择」工具或抠图面板开着时显示）：没选中的**实线**暗灰、
            # 选中的主题色实线。刻意不用虚线 —— 元素辅助线本来就是灰色虚线，再来一套
            # 长得一样的，用户会误以为"多出来一堆错开的虚线框"。save/restore 包住，
            # 免得影响后面的元素辅助线。
            if self._viewer.source_borders_visible or self._viewer._band_rect is not None:
                _rects = (self._viewer.source_rects()
                          if self._viewer.source_borders_visible else [])
                _selected = set(self._viewer.selected_sources())
                p.save()
                # 描边统一 1px 且**四边一样粗**：原来选中是 2px、未选中 1px，粗细不一致；
                # 而且 2px 画在小数坐标上时四边被反锯齿啃得深浅不一（看着像"上边粗下边细"）。
                # 所以关掉反锯齿 + 把矩形对齐到整数**设备**像素再画，只靠颜色区分选中。
                p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                p.setBrush(Qt.BrushStyle.NoBrush)
                for _i, (_sx, _sy, _sw, _sh) in enumerate(_rects):
                    _x = round(offset.x() + _sx * zoom)
                    _y = round(offset.y() + _sy * zoom)
                    _r = QRect(_x, _y,
                               max(1, round(_sw * zoom)), max(1, round(_sh * zoom)))
                    if _i in _selected:
                        # 选中只靠 1px 主题色描边区分，**不加半透明填充** ——
                        # 加过 12% 粉色填充，用户反馈"很影响观察"（抠图预览时整块被染粉，
                        # 根本看不出边缘抠得对不对）。粗细一致也靠这里保证：所有块同 1px。
                        p.setPen(QPen(QColor("#F06292"), 1))
                    else:
                        p.setPen(QPen(QColor("#666666"), 1))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(_r)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                # 框选框：半透明主题色填充 + 细虚线边框（拖动中一直跟着鼠标）。
                # 单独判 _band_rect：单张原图时不画分块框，但框选的手势还在，框得画出来。
                if self._viewer._band_rect is not None:
                    p.setPen(QPen(QColor("#F06292"), 1, Qt.PenStyle.DashLine))
                    p.setBrush(QColor(240, 98, 146, 40))
                    p.drawRect(self._viewer._band_rect)
                p.restore()

            # 绘制元素辅助线（普通灰色虚线，当前动作选中变当前色，多动作色块依次排）
            if self._viewer._frames:
                current_action = self._viewer.current_action
                # 为每个动作预计算 frame_numbers / composite_representatives /
                # 复用计数（元素出现的所有帧号）。显式帧布局（复用 / Alt+Ctrl 轮 / 导入）
                # 逐帧编号，同一元素可对应多个帧号 —— 见 ImageViewer._action_frame_badges。
                _act_info = {}
                for _act in self._viewer.actions:
                    _act_info[_act] = self._viewer._action_frame_badges(_act)
                _empty_info = ({}, {}, [], False)
                for i, (fx, fy, fw, fh) in enumerate(self._viewer._frames):
                    rect = QRectF(offset.x() + fx * zoom, offset.y() + fy * zoom,
                                  fw * zoom, fh * zoom)
                    # 收集所有选中该元素的动作（按动作列表顺序）
                    selected_actions = []
                    for act in self._viewer.actions:
                        if i in act.selected_indices:
                            selected_actions.append(act)
                    if not selected_actions:
                        # 无任何动作选中：灰色虚线
                        pen = QPen(QColor("#808080"), 1.0)
                        pen.setStyle(Qt.PenStyle.DashLine)
                        p.setPen(pen)
                        p.drawRect(rect)
                    else:
                        # 非当前动作的边框先画（轻微偏移避免完全重叠）
                        for idx, act in enumerate(selected_actions):
                            if act is not current_action:
                                # 属于多元素合成组的元素不画单独框，由合成粗框统一绘制
                                if i in (_act_info.get(act) or _empty_info)[0]:
                                    continue
                                off = idx * 2
                                pen = QPen(QColor(act.color), 1.0)
                                pen.setStyle(Qt.PenStyle.DashLine)
                                p.setPen(pen)
                                p.drawRect(rect.adjusted(off, off, off, off))
                        # 当前动作的边框最后画（最上方，更粗 + 轻微半透明填充，不遮挡图）
                        if current_action and current_action in selected_actions:
                            pen = QPen(QColor(current_action.color), 1.5)
                            p.setPen(pen)
                            p.drawRect(rect)
                            p.setOpacity(0.12)
                            p.fillRect(rect, QColor(current_action.color))
                            p.setOpacity(1.0)
                    # 合成组的帧号在整体边框上统一绘制；源元素仍保留
                    # 各自的细边框，但不再重复显示同一个帧号。
                    if selected_actions:
                        # 帧号色块沿元素左边缘从左到右连续排布：每个色块占一格
                        # （步进 BADGE_STEP），而一个动作可能占**多格**——该元素被复用
                        # 一次就多一个帧号。所以下一个动作必须从上一个动作画完
                        # 之后的游标位置开始，不能按动作序号预留固定格位；否则
                        # 复用帧的色块会被下一个动作的色块盖住（一格内挤两个序号）。
                        sy = rect.top()
                        cursor_x = rect.left()
                        p.setPen(QColor("#FFFFFF"))
                        p.setFont(QFont("Arial", 9, QFont.Weight.Bold))
                        for act in selected_actions:
                            _info = _act_info.get(act) or _empty_info
                            # 属于多元素合成组的元素不单独画色块，由合成粗框统一绘制
                            if i in _info[0]:
                                continue
                            # 帧号序列：首帧号 + 每个复用帧号各一个同样的小方块，
                            # 与前一块间隔 2px（步进 BADGE_STEP）。
                            nos = _info[1].get(i) or [act.selected_indices.index(i) + 1]
                            for no in nos:
                                p.fillRect(QRectF(cursor_x, sy, BADGE_SQ, BADGE_SQ),
                                           QColor(act.color))
                                p.drawText(QRectF(cursor_x, sy, BADGE_SQ, BADGE_SQ),
                                           Qt.AlignmentFlag.AlignCenter, str(no))
                                cursor_x += BADGE_STEP

                # 合成帧粗框：所有动作的合成组都绘制（当前动作粗实线，非当前动作虚线）。
                # 前置遍历：按「未加偏移的框几何」把合成框分组并分配色块档位 slot——
                # 两个动作合成同一组元素时框的位置完全相同，若把帧号都画在框左上角
                # 就会互相遮盖，因此同框组内第 k 个动作的色块顺延 k*(sq+2)。
                _composite_draw = []
                _slots: dict = {}
                for _act in self._viewer.actions:
                    _info = _act_info.get(_act) or _empty_info
                    if _info[3]:
                        # 显式布局（Alt+Ctrl 轮 / 复用 / 导入）：元素可以跨多帧，
                        # 粗框就按布局**逐帧**画，号是这一帧自己的号
                        _boxes = [(no, members) for no, members in _info[2] if len(members) >= 2]
                    else:
                        # 没有布局时按 composite_groups 画（一个元素只属于一个组）；
                        # 号从"逐帧表"里按成员反查，查不到就不画号
                        _no_of = {members: no for no, members in _info[2]}
                        _boxes = [(_no_of.get(tuple(group)), tuple(group))
                                  for group in _act.composite_groups if len(group) >= 2]
                    for _no, group in _boxes:
                        valid = [self._viewer._frames[i] for i in group if 0 <= i < len(self._viewer._frames)]
                        if not valid:
                            continue
                        gx0 = min(x for x, y, w, h in valid)
                        gy0 = min(y for x, y, w, h in valid)
                        gx1 = max(x + w for x, y, w, h in valid)
                        gy1 = max(y + h for x, y, w, h in valid)
                        base_rect = QRectF(
                            offset.x() + gx0 * zoom,
                            offset.y() + gy0 * zoom,
                            (gx1 - gx0) * zoom,
                            (gy1 - gy0) * zoom,
                        )
                        if _act is current_action:
                            pen = QPen(QColor(_act.color), 1.5, Qt.PenStyle.SolidLine)
                            draw_rect = base_rect
                        else:
                            # 非当前动作：虚线，轻微偏移避免与当前动作框完全重叠
                            _off = self._viewer.actions.index(_act) * 2
                            pen = QPen(QColor(_act.color), 1.0, Qt.PenStyle.DashLine)
                            draw_rect = base_rect.adjusted(_off, _off, _off, _off)
                        # 帧号色块的落点就是框左上角；两个框的左上角**挨得太近**就会互相盖住，
                        # 所以同格内第 k 个动作/帧的色块顺延 k*BADGE_STEP：
                        #  - 多个动作合成同一组元素：框位置完全相同（非当前动作只差 2px 偏移）；
                        #  - Alt+Ctrl 三轮攒出来的帧：都以元素1 为左上角，精确坐标**完全一样** ——
                        #    三个帧号原来就叠成一坨（用户报"帧号还是不对"）。
                        # 按 BADGE_STEP 网格量化而不是比精确坐标：2px 偏移的框若按精确坐标
                        # 算作"不同位置"，两个色块照样重叠（16px 宽只错开 2px = 盖住 14px）。
                        key = (round(draw_rect.left() / BADGE_STEP),
                               round(draw_rect.top() / BADGE_STEP))
                        slot = len(_slots.setdefault(key, []))
                        _slots[key].append(_act)
                        _composite_draw.append((_act, group, pen, draw_rect, slot, _no))

                for _act, group, pen, draw_rect, slot, _no in _composite_draw:
                    p.setPen(pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(draw_rect)

                    # 帧号：自合成框左上角起依次画色块（同格多动作/多帧时顺延，避免遮盖）。
                    # 号取**这一帧自己的帧号**：以前取 group[0] 的"首帧号"，元素跨多帧时
                    # 几个框会一起显示第一帧的号。
                    if _no is not None:
                        bx = draw_rect.left() + slot * BADGE_STEP
                        p.fillRect(QRectF(bx, draw_rect.top(), BADGE_SQ, BADGE_SQ),
                                   QColor(_act.color))
                        p.setPen(QColor("#FFFFFF"))
                        p.setFont(QFont("Arial", 9, QFont.Weight.Bold))
                        p.drawText(
                            QRectF(bx, draw_rect.top(), BADGE_SQ, BADGE_SQ),
                            Qt.AlignmentFlag.AlignCenter,
                            str(_no),
                        )

            # 画笔/橡皮的笔刷圈（PS 那种跟着鼠标的圆）：看得出当前多粗、落点在哪。
            # 画黑白双圈：单色圈在浅色图上会看不见。
            if self._viewer.painting and self._viewer._brush_pos is not None:
                radius = max(1.0, self._viewer.brush_size * self._viewer._zoom / 2.0)
                center = self._viewer._brush_pos
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(0, 0, 0, 160), 1))
                p.drawEllipse(center, radius + 1, radius + 1)
                p.setPen(QPen(QColor(255, 255, 255, 220), 1))
                p.drawEllipse(center, radius, radius)

            # 绘制框选矩形
            if self._selection_rect is not None:
                pen = QPen(QColor("#E91E63"), 1.0, Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.setBrush(QColor(233, 30, 99, 30))
                p.drawRect(self._selection_rect)

            # 标线（从标尺拖出来的参考线）：画在**图片范围**上（PS 也是铺满文档）。
            # 选中的那条用主题色，其余青色 —— 一眼能看出现在选中的是哪一条。
            # 画在最后 = 压在所有元素框之上（参考线本来就是为了对齐看的，不能被挡住）。
            if self._viewer._guides:
                selected = self._viewer.selected_guide()
                zoom = self._viewer._zoom
                offset = self._viewer._offset
                doc_w = self._viewer._pixmap.width()
                doc_h = self._viewer._pixmap.height()
                # 1px 的线必须关反锯齿 + 取整，否则会糊成 2px 的灰线（分块框踩过这个坑）
                p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                for guide in self._viewer._guides:
                    p.setPen(QPen(QColor("#E91E63") if guide is selected
                                  else QColor("#00FFFF"), 1))
                    if guide[0] == "h":
                        line_y = round(offset.y() + guide[1] * zoom)
                        p.drawLine(round(offset.x()), line_y,
                                   round(offset.x() + doc_w * zoom), line_y)
                    else:
                        line_x = round(offset.x() + guide[1] * zoom)
                        p.drawLine(line_x, round(offset.y()),
                                   line_x, round(offset.y() + doc_h * zoom))
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        else:
            # 无图片提示
            p.setPen(QColor("#666"))
            p.setFont(QFont("Microsoft YaHei", 12))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "拖入图片\n或点击添加")
        self._toast.paint(p, self.rect())
        p.end()

    def wheelEvent(self, event):
        if self._viewer._pixmap is None or self._viewer._pixmap.isNull():
            return
        delta = event.angleDelta().y()
        factor = 1.1 if delta > 0 else 1 / 1.1
        new_zoom = max(MIN_ZOOM, min(MAX_ZOOM, self._viewer._zoom * factor))
        # 以鼠标位置为中心缩放
        mouse_pos = event.position()
        old_zoom = self._viewer._zoom
        self._viewer._offset = QPointF(
            mouse_pos.x() - (mouse_pos.x() - self._viewer._offset.x()) * (new_zoom / old_zoom),
            mouse_pos.y() - (mouse_pos.y() - self._viewer._offset.y()) * (new_zoom / old_zoom),
        )
        self._viewer._zoom = new_zoom
        self._viewer._update_rulers()
        self.update()

    def leaveEvent(self, event):
        # 鼠标离开画布：笔刷圈要收掉，否则它停在边缘像个"卡住的圈"
        if self._viewer._brush_pos is not None:
            self._viewer._brush_pos = None
            self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self._panning = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif event.key() == Qt.Key.Key_Escape and self._viewer.source_interaction:
            # 原图选择状态下的 Esc = 取消选择（工具本身还开着，跟画图软件一致）
            self._viewer.set_selected_sources([])
            self._viewer._band_start = None
            self._viewer._band_rect = None
            self.update()
            event.accept()
            return
        elif event.key() == Qt.Key.Key_Escape and self._viewer.selected_guide() is not None:
            # 标线选中状态下 Esc = 取消选中（线还在，只是不再选中它）
            self._viewer.select_guide(None)
            event.accept()
            return
        elif event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) \
                and self._viewer.selected_guide() is not None:
            # 选中标线 + Del = 删掉它（用户要的两种删法之一；另一种是拖回标尺上）
            self._viewer.remove_guide(self._viewer.selected_guide())
            event.accept()
            return
        elif event.key() == Qt.Key.Key_Control:
            self._viewer.begin_ctrl_composition()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif event.key() == Qt.Key.Key_Control:
            self._viewer.end_ctrl_composition()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        """双击棋盘格（图外）= 添加图片，走主窗口同一个入口。

        刻意不看「原图选择」工具开没开：棋盘格本来就没有别的用途，双击它加图是顺手操作。
        图上双击则不抢（现在没有含义，留着以后用）。
        """
        if event.button() == Qt.MouseButton.LeftButton:
            viewer = self._viewer
            if not viewer.source_rects() or viewer.source_index_at(event.position()) < 0:
                viewer.add_image_requested.emit()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------ 原图选择 / 拖动
    def _src_cursor(self, over_block: bool = False, band: bool = False, moving: bool = False):
        """PS「移动工具 + 自动选择」那套光标手感。

        图上 = 可移动（四向箭头），棋盘格 = 普通箭头，拖框时 = 十字。
        """
        if moving or over_block:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif band:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def _src_begin(self, event) -> None:
        """按下：从图上起手 = 选择/拖动，从棋盘格（图外）起手 = 框选。"""
        viewer = self._viewer
        pos = event.position()
        index = viewer.source_index_at(pos)
        additive = bool(event.modifiers() & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))
        selected = index >= 0 and index in viewer.selected_sources()
        if index >= 0 and len(viewer.source_rects()) == 1:
            # 只有一张原图时它天然就是"选中项"：没有别的可拖，不必先点一下再拖
            selected = True
        self._src_press = (pos, index, selected, additive)
        self._src_moved = False
        self._src_moving = False
        self._src_applied = False
        self._src_last = pos if index >= 0 else None
        if index < 0:
            viewer._band_start = pos
            viewer._band_rect = QRectF(pos, pos)
            viewer._band_add = additive
        self.update()

    def _src_drag(self, event) -> None:
        """拖动中：越过阈值后，按下时已选中的那一组整体移位；棋盘格起手的走框选。"""
        viewer = self._viewer
        pos = event.position()
        start, index, selected, _additive = self._src_press
        if not self._src_moved:
            if (pos - start).manhattanLength() < self.SRC_DRAG_MIN:
                return
            self._src_moved = True
            # 看的是**按下那一刻**的选中态：没被选中的图拖动=不移动（用户定的"先选中再说"），
            # 所以不能在按下之后才把它选上来再拖
            if index >= 0 and selected and viewer._source_tool:
                self._src_moving = True
                # 归属一次定死：拖动中几何一直在变，靠"当前重叠"重判会把邻居的元素框带走
                viewer.begin_source_move(viewer.selected_sources())
                viewer.source_move_started.emit()
        if self._src_moving:
            last = self._src_last or pos
            zoom = viewer._zoom or 1.0
            if viewer.move_sources(viewer.selected_sources(),
                                   round((pos.x() - last.x()) / zoom),
                                   round((pos.y() - last.y()) / zoom)):
                # 真动了才推进基准点：被夹住时基准点不动，往回拖立刻就能接着走
                self._src_last = pos
                self._src_applied = True
            self._src_cursor(moving=True)
        elif viewer._band_start is not None:
            viewer._band_rect = QRectF(viewer._band_start, pos).normalized()
            self._src_cursor(band=True)
        self.update()

    def _src_end(self, event) -> None:
        """松开：没拖开 = 点击选择；拖开了 = 拖动移位 / 框选。"""
        viewer = self._viewer
        press = self._src_press
        self._src_press = None
        moved, moving = self._src_moved, self._src_moving
        applied = self._src_applied
        self._src_moved = False
        self._src_moving = False
        self._src_applied = False
        self._src_last = None
        if press is None:
            return
        start, index, _selected, additive = press
        if index < 0:
            # 棋盘格起手：拖开了 = 框选那几张，没拖开 = 取消选择
            rect = QRectF(viewer._band_start or start, event.position()).normalized()
            viewer._band_start = None
            viewer._band_rect = None
            viewer._band_add = False
            if moved:
                hits = viewer.source_indices_in_rect(rect)
                viewer.set_selected_sources(
                    set(viewer.selected_sources()) | set(hits) if additive else hits)
            elif not additive:
                viewer.set_selected_sources([])
        elif moving:
            viewer.end_source_move()
            viewer.source_move_finished.emit(applied)
        elif additive:
            viewer.toggle_source_selection(index, True)
        else:
            viewer.set_selected_sources([index])
        self._src_cursor(viewer._source_tool and viewer.source_index_at(event.position()) >= 0)
        self.update()

    def mousePressEvent(self, event):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        # 空格平移**永远优先**：用户按住空格就是想挪画布，任何模式都不该抢这一下
        # （原图选择模式特别需要：挪完再挑图、再拖）
        if self._panning and event.button() == Qt.MouseButton.LeftButton:
            self._pan_start = event.position() - self._viewer._offset
            self._viewer.canvas_pressed.emit()
            event.accept()
            return
        # 标线（从标尺拖出来的参考线）：接在空格平移**后面**、所有工具**前面**，
        # 但只在"没有工具开着"时才接（can_edit_guides）—— 画笔/橡皮/吸管/原图选择
        # 开着时用户是在干活，不能让 1px 的参考线截胡这一下。
        if event.button() == Qt.MouseButton.LeftButton and self._viewer.can_edit_guides():
            guide = self._viewer.guide_at(event.position())
            if guide is not None:
                self._viewer.begin_guide_move(guide)
                self._viewer.canvas_pressed.emit()
                event.accept()
                return
            if self._viewer.selected_guide() is not None:
                self._viewer.select_guide(None)      # 点别处 = 取消标线选中
        # 吸管模式（抠图面板开着时）：这一下只用来取色，绝不能走进框选/加图逻辑。
        # 坐标换算与 _frame_at_point 用的是同一套：图坐标 = (画布坐标 - 偏移) / 缩放。
        pick = getattr(self._viewer, "_pick_target", None)
        if pick is not None and event.button() == Qt.MouseButton.LeftButton:
            zoom = self._viewer._zoom or 1.0
            pos = event.position()
            pick.sample_at(int((pos.x() - self._viewer._offset.x()) / zoom),
                           int((pos.y() - self._viewer._offset.y()) / zoom))
            event.accept()
            return
        # 吸管工具（左侧竖条，与抠图面板那个吸管是两回事：那个只给抠图取背景色）：
        # 取色写进前景色，取完**保持吸管模式**（PS 也是这样，方便连着取几个色）
        if self._viewer.paint_tool == "pick" and event.button() == Qt.MouseButton.LeftButton:
            self._viewer.canvas_pressed.emit()   # 点了原图 → 收起变换手柄（与点别处一致）
            self._viewer.pick_color_at(event.position())
            event.accept()
            return
        # 画笔 / 橡皮：按住左键涂抹。
        # 只接"没有 Ctrl / Alt"的那一下 —— Ctrl+点击=合成帧、Alt+点击=复用帧是既有的
        # 全局手势，绘画工具开着也不该把它们抢掉（用户刚验收过那套逻辑）。
        if (self._viewer.painting and event.button() == Qt.MouseButton.LeftButton
                and not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                              | Qt.KeyboardModifier.AltModifier))):
            if self._viewer.begin_paint_stroke(event.position()):
                self._viewer.canvas_pressed.emit()
                event.accept()
                return
        # 原图选择 / 拖动（「原图选择」工具或抠图面板开着时）：
        # 从图上起手 = 选择/拖动，从棋盘格（图外）起手 = 框选。
        # 判定的关键是"松手前有没有真的拖开"：没拖开=点击选择，拖开了才按拖动/框选算。
        if self._viewer.source_interaction and event.button() == Qt.MouseButton.LeftButton:
            self._src_begin(event)
            event.accept()
            return
        # 在原图上按下（含空白、含空格平移）：通知主窗口收起右侧变换手柄
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton,
                              Qt.MouseButton.MiddleButton):
            self._viewer.canvas_pressed.emit()
        if event.button() == Qt.MouseButton.LeftButton and self._viewer._pixmap:
            if not self._viewer.current_action:
                # 有图但还没有动作：框选出来的帧没有地方存（selected_indices 属于动作），
                # 点了只会"完全没反应"。用户要求这种时候直接提示先添加动作。
                self.hint_no_action()
                return
            # 记录框选起点
            self._drag_start = event.position()
            self._selection_rect = QRectF(event.position(), event.position())
            self.update()
        elif event.button() == Qt.MouseButton.LeftButton:
            self._open_file_dialog()

    def mouseMoveEvent(self, event):
        # 空格平移优先（与按下时的判定一致），否则"空格拖着拖着变成拖图/框选"了
        if self._panning and self._pan_start is not None:
            self._viewer._offset = event.position() - self._pan_start
            self._viewer._update_rulers()
            self.update()
            return
        if self._viewer._paint_active:
            # 正在画：这一段画完就重绘，别走下面的框选/悬停逻辑
            self._viewer.paint_stroke_to(event.position())
            return
        if self._viewer._guide_drag_active:
            # 正在拖标线：只改它的位置（松手才决定留还是删）
            self._viewer.drag_guide_to_canvas(event.position())
            return
        if self._viewer.painting:
            # 没按下时只更新"笔刷圈"的位置（PS 那种跟着鼠标的圆圈，看得出笔有多粗）
            self._viewer._brush_pos = event.position()
            self.update()
        if self._src_press is not None:
            self._src_drag(event)
            return
        if self._viewer._source_tool:
            # 悬停反馈：图上是"可移动"光标、棋盘格上回到普通箭头（PS 移动工具那套手感）
            self._src_cursor(self._viewer.source_index_at(event.position()) >= 0)
        if self._drag_start is not None:
            self._selection_rect = QRectF(self._drag_start, event.position()).normalized()
            self.update()
            return
        # 悬停反馈：光标移到标线上时换成"可以上下/左右拖"的光标（PS 手感）。
        # 放在最后：前面那些分支（框选、原图拖动）自己的光标优先。
        if self._viewer.can_edit_guides():
            guide = self._viewer.guide_at(event.position())
            want = (Qt.CursorShape.SizeVerCursor if guide[0] == "h"
                    else Qt.CursorShape.SizeHorCursor) if guide is not None else None
            if want != self._guide_cursor:
                self._guide_cursor = want
                if want is None:
                    self.unsetCursor()
                else:
                    self.setCursor(want)
        elif self._guide_cursor is not None:
            self._guide_cursor = None
            self.unsetCursor()

    def mouseReleaseEvent(self, event):
        if self._panning:
            self._pan_start = None
            return
        if self._viewer._guide_drag_active:
            # 松手：还在画布里 = 落在这里；拖回标尺/拖出画布 = 删掉（PS 一样）
            # （QRect.contains 只认 QPoint/QRect，QPointF 会直接抛 TypeError）
            self._viewer.finish_guide_drag(inside=self.rect().contains(event.position().toPoint()))
            event.accept()
            return
        if self._viewer._paint_active:
            # 松开 = 一笔结束：重裁帧/刷预览/记撤销都在主窗口的信号处理里
            self._viewer.end_paint_stroke()
            event.accept()
            return
        if self._src_press is not None:
            self._src_end(event)
            event.accept()
            return
        if self._drag_start is not None and self._selection_rect is not None:
            rect = self._selection_rect
            self._drag_start = None
            self._selection_rect = None
            if rect.width() < 5 and rect.height() < 5:
                # 单击按修饰键分三种（**顺序不能反**：Alt 一旦排在合成模式后面，
                # 手动点亮「Ctrl+合成」按钮时 Alt+点击就会被合成抢走，见下）：
                # Alt+Ctrl = 一轮一张新帧；Ctrl（或手动合成模式）= 合成一帧；
                # Alt（或复用按钮模式）= 复用/取消复用；无修饰 = 普通选择。
                idx = self._viewer._frame_at_point(event.position())
                if self._viewer.current_action and idx >= 0:
                    ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                    alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
                    if ctrl and alt:
                        # 每轮 Alt+Ctrl 自成一帧：起手先封掉上一轮（第一帧不再被动）
                        self._viewer.begin_ctrl_composition()
                        self._viewer.seal_alt_ctrl_round()
                        # 判定看**当前这一帧的成员**（不是全局选中集）：跨轮带过来的选中项
                        # 不在这一帧里，所以会被**加进这一帧** —— 它在旧帧里存在时，这一步的
                        # 效果就是"复用进这一帧"；只有本来就属于这一帧的，再点才是取消。
                        _active = self._viewer._active_composite_group
                        if _active is not None and idx in _active:
                            # 按住期间"选中的就是这一帧"：再点已选中的 = 从这一帧移出
                            # （用户明确要求）。所以这条组合键用普通点击的 toggle 规则，
                            # 而不是 add_to_composite 的"只加不减"。
                            self._viewer.current_action.selected_indices.remove(idx)
                            if idx in _active:
                                _active.remove(idx)
                            self._viewer._normalize_composite_groups()
                        else:
                            self._viewer.add_to_composite(idx)
                    elif ctrl or (self._viewer.compose_mode and not alt):
                        # 单 Ctrl（或手动点亮的合成模式）：只加不减地把元素合成为一帧。
                        # 这里排除 alt：手动合成模式常驻时，单独 Alt+点击仍必须是复用。
                        if ctrl:
                            self._viewer.begin_ctrl_composition()
                        self._viewer.add_to_composite(idx)
                    elif alt or self._viewer.reuse_mode:
                        if idx in self._viewer.current_action.selected_indices:
                            if alt:
                                # Alt：复用/取消复用 toggle（已复用则取消最近一次）。
                                # 点击时同步高亮按钮（原生 Alt 监听失败时的兜底）；
                                # 若 Alt 仍被原生监听持有，则保持到 Alt 松开，避免破坏持续高亮。
                                was_alt_down = self._viewer._alt_reusing
                                self._viewer.begin_alt_reuse()
                                self._viewer.toggle_reuse_at(idx)
                                if not was_alt_down:
                                    self._viewer.end_alt_reuse()
                            else:
                                # 复用按钮模式：持续追加一帧
                                self._viewer.reuse_frame_at(idx)
                        else:
                            # 复用作用于"已选"元素：未选元素按普通选择处理
                            self._viewer.current_action.selected_indices.append(idx)
                            self._viewer._normalize_composite_groups()
                    else:
                        if idx in self._viewer.current_action.selected_indices:
                            self._viewer.current_action.selected_indices.remove(idx)
                        else:
                            self._viewer.current_action.selected_indices.append(idx)
                        self._viewer._normalize_composite_groups()
                    self.selection_changed.emit()
            else:
                # 框选：默认替换，Ctrl 框选追加并合成为一帧；Alt 框选追加复用。
                indices = self._viewer._frames_in_view_rect(rect)
                if self._viewer.current_action:
                    if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                        # Ctrl+框选：激活合成模式，将框选的所有元素合成为一帧
                        self._viewer.begin_ctrl_composition()
                        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
                            # Alt+Ctrl+框选同样"一轮一帧"：先封上一轮，别再碰上一帧
                            self._viewer.seal_alt_ctrl_round()
                        for idx in indices:
                            self._viewer.add_to_composite(idx)
                        # 同上：不在这里追加复用（会复制到"上一帧"上）
                    elif event.modifiers() & Qt.KeyboardModifier.AltModifier or self._viewer.reuse_mode:
                        # Alt+框选：已选元素追加复用帧，未选元素正常加入选择，不清空已有
                        action = self._viewer.current_action
                        for idx in indices:
                            if idx in action.selected_indices:
                                self._viewer.reuse_frame_at(idx)
                            else:
                                action.selected_indices.append(idx)
                        self._viewer._normalize_composite_groups()
                        self._viewer.crop_frames_for_action(action)
                    elif indices:
                        # 无修饰框选 = 整份替换选择：帧按新选择**重新生成**
                        # （显式清掉 frame_layouts，与清空选择/反选同一个口径；
                        # 不清的话旧复用帧会被"保留 + 裁剪"，和"替换"的意图不符）
                        self._viewer.current_action.selected_indices = indices
                        self._viewer.current_action.composite_groups = []
                        self._viewer.current_action.frame_layouts = None
                        self._viewer.current_action.frame_layouts_base = 0
                    self._viewer._normalize_composite_groups()
                    if indices:
                        self.selection_changed.emit()
            self.update()

    def contextMenuEvent(self, event):
        """右键菜单统一兜底：菜单构建 / 弹出 / 执行的任何 Python 异常都只记录、
        不闪退整个程序（原生层崩溃由 main.py 的 faulthandler 写入 crash.log）。"""
        # 右键落点记下来：建菜单和"取消选中"都要用它（建菜单那步已经拆出去了，
        # 不能再靠 event 传下去）
        self._context_pos = QPointF(event.pos())
        try:
            self._context_menu_impl(event)
        except Exception:
            sys.stderr.write(traceback.format_exc())

    def _context_menu_impl(self, event):
        """右键菜单：根据选中数量启用/禁用项。"""
        menu, items = self.build_context_menu()
        chosen = menu.exec(event.globalPos())
        if chosen is None:
            return
        self._run_context_action(chosen, items)

    def build_context_menu(self):
        """建右键菜单，返回 (菜单, 动作项字典)。

        拆成两步是为了**能在回归里检查菜单**：`exec` 一弹就是模态对话框，
        测试点不了也没法读菜单项顺序；这里只建不弹。
        动作项字典把「哪个菜单项对应哪个方法」显式写出来，比一长串 is 判断好读。
        """
        action = self._viewer.current_action
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #2b2b2b; color: #e8e8e8; border: 1px solid #555; }"
            "QMenu::item:selected { background: #E91E63; }"
            "QMenu::item:disabled { color: #666; }"
        )
        n_sel = len(action.selected_indices) if action else 0
        n_frames = len(action.frames) if action else 0
        # 右键所指的那个元素：「取消选中」只取消它，保留其他选中
        hit_index = self._viewer._frame_at_point(self._context_pos) if action else -1
        hit_selected = bool(hit_index >= 0 and action
                            and hit_index in set(action.selected_indices))

        items = {}
        items["actual"] = menu.addAction("实际像素 (100%)")
        menu.addSeparator()
        items["merge"] = menu.addAction("合并为一帧（拼成同一帧）")
        items["merge_one"] = menu.addAction("合并为一整块（碎块捏成一个元素）")
        # 「清空选中区域」放在「忽略选中区域」**上方**（用户指定）：两个都是"这块不要了"，
        # 按从重到轻排 —— 先"把内容也擦掉"，再"只标记忽略"。
        items["clear_area"] = menu.addAction("清空选中区域")
        items["ignore"] = menu.addAction("忽略选中区域")
        menu.addSeparator()
        items["invert"] = menu.addAction("反选")
        items["select_all"] = menu.addAction("全选")
        items["clear"] = menu.addAction("清空选择")
        items["remove"] = menu.addAction("取消选中")
        menu.addSeparator()
        items["export"] = menu.addAction("导出选中为单独 PNG…")

        # 禁用不适用的项
        items["merge"].setEnabled(n_sel >= 2)
        items["merge_one"].setEnabled(n_sel >= 2)
        items["clear_area"].setEnabled(n_sel >= 1)
        items["ignore"].setEnabled(n_sel >= 1)
        items["clear"].setEnabled(n_sel >= 1)
        items["remove"].setEnabled(hit_selected)
        items["export"].setEnabled(n_frames >= 1)
        return menu, items

    def _run_context_action(self, chosen, items) -> None:
        if chosen is items["actual"]:
            self._viewer.zoom_actual_pixel()
        elif chosen is items["merge"]:
            self._viewer.menu_merge_composite()
        elif chosen is items["merge_one"]:
            self._viewer.menu_merge_single_block()
        elif chosen is items["clear_area"]:
            self._viewer.menu_clear_selected_regions()
        elif chosen is items["ignore"]:
            self._viewer.menu_ignore_selected_regions()
        elif chosen is items["invert"]:
            self._viewer.menu_invert_selection()
        elif chosen is items["select_all"]:
            self._viewer.menu_select_all()
        elif chosen is items["clear"]:
            self._viewer.menu_clear_selection()
        elif chosen is items["remove"]:
            self._viewer.menu_remove_selected_elements(
                self._viewer._frame_at_point(self._context_pos))
        elif chosen is items["export"]:
            self._viewer.menu_export_selected_png()

    def _open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "添加图片", last_directory(),
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)"
        )
        if path:
            set_last_directory(path)
            # 自动检测同目录同名 JSON，有则导入工程（JSON 连带加载图片）
            json_candidate = Path(path).with_suffix(".json")
            if json_candidate.is_file():
                self.json_selected.emit(str(json_candidate))
                return
            pm = QPixmap(path)
            if not pm.isNull():
                self.image_loaded.emit(pm)

    def dragEnterEvent(self, event):
        try:
            if event.mimeData().hasUrls():
                for url in event.mimeData().urls():
                    if Path(url.toLocalFile()).suffix.lower() in (SUPPORTED_IMAGE_EXT | {".json", ".sstool"}):
                        event.acceptProposedAction()
                        return
        except Exception:  # noqa: BLE001 - 拖放回调里的异常绝不能逃逸（PyQt 会 abort）
            pass

    def dragMoveEvent(self, event):
        try:
            if event.mimeData().hasUrls():
                event.acceptProposedAction()
        except Exception:  # noqa: BLE001
            pass

    def dropEvent(self, event):
        try:
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                suffix = Path(path).suffix.lower()
                viewer = self._viewer
                if suffix == ".sstool":
                    QTimer.singleShot(0, lambda p=path, v=viewer: v.project_dropped.emit(p))
                elif suffix == ".json":
                    # 画布上拖入 JSON：直接加入当前工程（不问）
                    QTimer.singleShot(0, lambda p=path, v=viewer: v.json_dropped_current.emit(p))
                elif suffix in SUPPORTED_IMAGE_EXT:
                    # 画布上拖入图片：走智能路径（自动找同名 JSON）
                    QTimer.singleShot(0, lambda p=path, v=viewer: v.image_path_dropped.emit(p))
                event.acceptProposedAction()
                return
        except Exception:  # noqa: BLE001
            pass
    def _has_image(self) -> bool:
        return self._viewer._pixmap is not None and not self._viewer._pixmap.isNull()


class _DragGrip(QWidget):
    """工具条右侧的拖动手柄：3 根**细短竖线**，一眼看出这里可以拖。"""

    LINES = 3
    LINE_H = 18          # 竖线长度（用户再次要求"再长 4 像素"：14 → 18）
    GAP = 1              # 三根线间距 1px（0 会糊成一条，用户要求向左分散）
    SHIFT_RIGHT = 7      # 左侧留白 7px：三根线在 12px 控件里右对齐，最右一根距工具条右边 3px（左移2px）

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(self.LINES + (self.LINES - 1) * self.GAP + self.SHIFT_RIGHT, 20)
        self.setToolTip("按住这里拖动工具条")
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setPen(QPen(QColor("#a0a0a0"), 1.0))     # 1 像素细线
        y0 = max(0, (self.height() - self.LINE_H) // 2)
        # 右对齐画：最右一根距控件右边 3px（本轮整体左移 2 像素）
        for i in range(self.LINES):
            x = self.width() - 3 - (self.LINES - 1 - i) * (1 + self.GAP)
            p.drawLine(x, y0, x, y0 + self.LINE_H)
        p.end()


class _DragGripH(_DragGrip):
    """竖条工具条用的拖动把手：把「三根短竖线」转 90° 变成「三根短横线」。

    纯旋转版本 —— 控件 20×12（原版 12×20），线长 18、间距 1、边缘留白都与原版一一对应，
    所以观感完全一致。原版 ``_DragGrip`` 一字未改，右上角那条横工具条不受影响。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # 转置后的尺寸：宽度 = 原版高度（线长 18 + 上下各 1），高度 = 原版宽度
        self.setFixedSize(self.LINE_H + 2, self.LINES + (self.LINES - 1) * self.GAP + self.SHIFT_RIGHT)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setPen(QPen(QColor("#a0a0a0"), 1.0))     # 1 像素细线
        x0 = max(0, (self.width() - self.LINE_H) // 2)
        # 与原版同一套排布（只是把 x/y 对调）：靠下对齐，最下一根距控件下边 3px
        for i in range(self.LINES):
            y = self.height() - 3 - (self.LINES - 1 - i) * (1 + self.GAP)
            p.drawLine(x0, y, x0 + self.LINE_H, y)
        p.end()


class FloatingToolBar(QWidget):
    """画布右上角浮动工具条：放大/缩小/透明背景/黑背景/白背景。"""

    zoom_in_requested = pyqtSignal()
    zoom_out_requested = pyqtSignal()
    bg_changed = pyqtSignal(str)
    compose_toggled = pyqtSignal(bool)
    reuse_toggled = pyqtSignal(bool)
    history_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # 宽度要容纳：合成(80) + 复用(80) + 放大/缩小(24×2) + 三种背景(24×3) + 历史(24) + 边距/间距
        # 右侧留一条"3 根细短竖线"的拖动把手（证明这里可以拖）
        self.GRIP_W = 12
        self.setFixedSize(335, 32)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._bg_mode = "checker"
        # 工具条可以自己拖（空白处按下拖动）：用户要求"原图的悬浮工具也能拖动，
        # 自己挪到悬浮预览窗口左边、不被遮挡"。按钮在子控件上，按下不会走到这里。
        self._drag_offset = None
        self._moved_by_user = False
        self.setToolTip("拖动空白处可移动工具条")
        self.setCursor(Qt.CursorShape.SizeAllCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 1, 2)      # 右边距收窄 → 把手更靠右
        layout.setSpacing(2)

        # 合成帧按钮：宽按钮 + 图标 + Ctrl+文字 + 描边
        self.btn_compose = QPushButton()
        self.btn_compose.setFixedSize(80, 24)
        self.btn_compose.setIcon(make_tool_icon("compose_red", 14))
        self.btn_compose.setIconSize(QSize(14, 14))
        self.btn_compose.setText("Ctrl+合成")
        self.btn_compose.setToolTip("合成帧（可用 Ctrl+点击/框选）")
        self.btn_compose.setCheckable(True)
        self.btn_compose.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #555; border-radius: 3px; "
            "color: #e8e8e8; font-size: 11px; padding: 0 4px; }"
            "QPushButton:hover { border-color: #E91E63; color: #fff; background: rgba(233,30,99,0.15); }"
            "QPushButton:checked { background: rgba(233,30,99,0.4); border-color: #E91E63; color: #fff; }"
        )
        self.btn_compose.toggled.connect(lambda checked: self.btn_compose.setIcon(
            make_tool_icon("compose", 14) if checked else make_tool_icon("compose_red", 14)
        ))
        self.btn_compose.clicked.connect(self.compose_toggled)
        layout.addWidget(self.btn_compose)

        self.btn_reuse = QPushButton()
        self.btn_reuse.setFixedSize(80, 24)
        self.btn_reuse.setIcon(make_tool_icon("reuse_red", 14))  # 平时红色（与合成按钮一致）
        self.btn_reuse.setIconSize(QSize(14, 14))
        self.btn_reuse.setText("Alt+复用")
        self.btn_reuse.setToolTip("复用当前选择的帧（可用 Alt+点击；Alt 按下时按钮高亮）")
        self.btn_reuse.setCheckable(True)
        # 选中（Alt 按下 / 按钮保持模式）时图标变白，松开恢复红色
        self.btn_reuse.toggled.connect(lambda checked: self.btn_reuse.setIcon(
            make_tool_icon("reuse", 14) if checked else make_tool_icon("reuse_red", 14)
        ))
        self.btn_reuse.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #555; border-radius: 3px; "
            "color: #e8e8e8; font-size: 11px; padding: 0 4px; }"
            "QPushButton:hover { border-color: #E91E63; color: #fff; background: rgba(233,30,99,0.15); }"
            "QPushButton:checked { background: rgba(233,30,99,0.4); border-color: #E91E63; color: #fff; }"
        )
        self.btn_reuse.clicked.connect(self.reuse_toggled)
        layout.addWidget(self.btn_reuse)

        self.btn_zoom_in = self._make_button("zoom_in", "放大")
        self.btn_zoom_in.clicked.connect(self.zoom_in_requested)
        layout.addWidget(self.btn_zoom_in)

        self.btn_zoom_out = self._make_button("zoom_out", "缩小")
        self.btn_zoom_out.clicked.connect(self.zoom_out_requested)
        layout.addWidget(self.btn_zoom_out)

        # 背景切换（互斥）：透明 / 黑 / 白
        self._bg_group = QButtonGroup(self)
        self.btn_bg_checker = self._make_button("bg_checker", "透明背景", checkable=True)
        self.btn_bg_black = self._make_button("bg_black", "黑色背景", checkable=True)
        self.btn_bg_white = self._make_button("bg_white", "白色背景", checkable=True)
        self._bg_group.addButton(self.btn_bg_checker)
        self._bg_group.addButton(self.btn_bg_black)
        self._bg_group.addButton(self.btn_bg_white)
        self.btn_bg_checker.setChecked(True)
        self.btn_bg_checker.clicked.connect(lambda: self.bg_changed.emit("checker"))
        self.btn_bg_black.clicked.connect(lambda: self.bg_changed.emit("black"))
        self.btn_bg_white.clicked.connect(lambda: self.bg_changed.emit("white"))
        layout.addWidget(self.btn_bg_checker)
        layout.addWidget(self.btn_bg_black)
        layout.addWidget(self.btn_bg_white)

        # 历史记录面板开关（显示/隐藏右侧浮动历史窗口）
        self.btn_history = self._make_button("history", "历史记录")
        # 可勾选：历史窗口显示着的时候保持"激活"外观（主窗口负责同步状态）
        self.btn_history.setCheckable(True)
        self.btn_history.clicked.connect(self.history_requested)
        layout.addWidget(self.btn_history)

        # 拖动把手：3 根细短竖线，贴到最右边（stretch 把可能的空隙吃掉）
        self.grip = _DragGrip()
        layout.addStretch(1)
        layout.addWidget(self.grip)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(30, 30, 30, 220))
        p.setPen(QPen(QColor("#444"), 1))
        p.drawRoundedRect(self.rect(), 6, 6)
        p.end()

    def set_history_active(self, active: bool) -> None:
        """历史记录窗口显示时，工具栏里的"历史"按钮保持激活外观。"""
        if self.btn_history.isChecked() != bool(active):
            self.btn_history.blockSignals(True)
            self.btn_history.setChecked(bool(active))
            self.btn_history.blockSignals(False)

    def _make_button(self, icon_kind: str, tip: str, checkable: bool = False) -> QPushButton:
        btn = QPushButton()
        btn.setFixedSize(24, 24)
        btn.setIcon(make_tool_icon(icon_kind, 16))
        btn.setIconSize(QSize(16, 16))
        btn.setToolTip(tip)
        btn.setCheckable(checkable)
        btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 3px; }"
            "QPushButton:hover { background: rgba(233,30,99,0.3); }"
            "QPushButton:checked { background: rgba(233,30,99,0.5); }"
        )
        if icon_kind == "compose_red":
            btn.toggled.connect(lambda checked, b=btn: b.setIcon(
                make_tool_icon("compose", 16) if checked else make_tool_icon("compose_red", 16)
            ))
        return btn

    # ---- 拖动工具条（空白处按下拖）----
    def moved_by_user(self) -> bool:
        """用户是否自己挪过位置（挪过之后就不再被强行搬回右上角）。"""
        return self._moved_by_user

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.position().toPoint()
            self.raise_()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is None:
            super().mouseMoveEvent(event)
            return
        parent = self.parentWidget()
        want = self.mapToParent(event.position().toPoint() - self._drag_offset)
        if parent is not None:
            # 只限制在画布范围内，别被拖到看不见的地方
            want.setX(max(0, min(parent.width() - self.width(), want.x())))
            want.setY(max(0, min(parent.height() - self.height(), want.y())))
        self.move(want)
        self._moved_by_user = True
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class VerticalToolBar(QWidget):
    """画布左侧浮动**竖条**工具条：外观与右上角 FloatingToolBar 完全同款，只是竖着排。

    - 尺寸/配色/圆角/悬停反馈与右上角一致（按钮 24×24、图标 16、卡片 QColor(30,30,30,220)
      + 1px #444 + 圆角 6）；
    - 底部是「3 根细短横线」的拖动把手（``_DragGripH``，由右上角那根竖线版转 90° 而来）；
    - 按住空白处可拖动，用户拖过之后就不再被搬回默认位置（与右上角同一套规则）；
    - 高度按当前可见按钮数自适应（``_sync_height``），以后往里加按钮不用改常量。

    默认位置在画布左边缘、左上角 JSON 徽标（y=6，高 24）下方，见 ``HOME_Y``。
    """

    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()
    key_requested = pyqtSignal()
    source_tool_requested = pyqtSignal(bool)   # 「原图选择」开关（带勾选状态）
    # 画笔 / 橡皮 / 吸管三个工具：统一从这一条信号出（"brush"/"eraser"/"pick"），
    # 主窗口按"单工具模式"统一处理互斥，避免三个按钮各接一套 handler 后互相打架
    paint_tool_requested = pyqtSignal(str, bool)
    color_requested = pyqtSignal(str)          # "fg" / "bg"：色块被点了，要开调色板

    BTN = 24            # 与右上角工具条同一尺寸
    W = BTN + 12        # 左右各留 6px → 36px 宽
    MARGIN_TOP = 4
    MARGIN_BOTTOM = 2
    SPACING = 2
    HOME_X = 6
    # 左上角 JSON 徽标占 y=6..30，竖条从 38 起，留 8px 间隙不打架
    HOME_Y = 38

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedWidth(self.W)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._drag_offset = None
        self._moved_by_user = False
        self.setToolTip("拖动空白处可移动工具条")
        self.setCursor(Qt.CursorShape.SizeAllCursor)

        side = (self.W - self.BTN) // 2
        layout = QVBoxLayout(self)
        layout.setContentsMargins(side, self.MARGIN_TOP, side, self.MARGIN_BOTTOM)
        layout.setSpacing(self.SPACING)

        # 「原图选择」：点选/框选要处理哪张原图，选中持续高亮（拖动改位置是下一步）。
        # 放在抠图按钮**上面**：先选图再抠图，顺序跟实际操作一致。
        self.btn_source = self._make_button("select_source",
                                            "原图选择 / 移动（点选 · 框选 · 拖动）",
                                            checkable=True)
        self.btn_source.toggled.connect(self.source_tool_requested)
        layout.addWidget(self.btn_source)

        # 主工具：抠除背景。点开参数浮层（预览不动原图，点"应用"才落地）。
        # 之所以现在才加这个按钮：功能没落地之前放上去就是个"点了没反应"的死按钮。
        self.btn_key = self._make_button("remove_bg", "抠除背景", checkable=True)
        self.btn_key.toggled.connect(self.key_requested)
        layout.addWidget(self.btn_key)

        # 绘制三件套（PS 单工具模式，互斥）：画笔 B / 橡皮擦 E / 吸管 I。
        # 图标名与快捷键都对齐 PS，用户不必重新学。
        self.btn_brush = self._make_button("brush", "画笔（B）：涂抹前景色", checkable=True)
        self.btn_brush.toggled.connect(
            lambda on: self.paint_tool_requested.emit("brush", on))
        layout.addWidget(self.btn_brush)

        self.btn_eraser = self._make_button("eraser", "橡皮擦（E）：擦成透明", checkable=True)
        self.btn_eraser.toggled.connect(
            lambda on: self.paint_tool_requested.emit("eraser", on))
        layout.addWidget(self.btn_eraser)

        self.btn_pick = self._make_button("eyedropper",
                                          "吸管（I）：取图上的颜色当前景色", checkable=True)
        self.btn_pick.toggled.connect(
            lambda on: self.paint_tool_requested.emit("pick", on))
        layout.addWidget(self.btn_pick)

        # 「颜色选择」：PS 式前景/背景双色块。它不是"模式"（不参与互斥），
        # 点是开调色板 —— 所以用 _make_button 的样式但自己画内容。
        self.btn_color = ColorSwatchButton(self.BTN)
        self.btn_color.setStyleSheet(
            "QPushButton { background: transparent; border: none; }")
        self.btn_color.foreground_clicked.connect(lambda: self.color_requested.emit("fg"))
        self.btn_color.background_clicked.connect(lambda: self.color_requested.emit("bg"))
        layout.addWidget(self.btn_color)

        self.btn_undo = self._make_button("undo", "撤销（Ctrl+Z）")
        self.btn_undo.clicked.connect(self.undo_requested)
        layout.addWidget(self.btn_undo)

        self.btn_redo = self._make_button("redo", "重做（Ctrl+Y）")
        self.btn_redo.clicked.connect(self.redo_requested)
        layout.addWidget(self.btn_redo)

        # 拖动把手：三根细短**横**线，贴到最下面（stretch 把可能的空隙吃掉）。
        # 竖条里用横向把手才像"抓这里拖"（用户看了竖线版后要求转 90°）
        layout.addStretch(1)
        self.grip = _DragGripH()
        layout.addWidget(self.grip, 0, Qt.AlignmentFlag.AlignHCenter)

        self._sync_height()

    def set_paint_colors(self, fg: QColor, bg: QColor) -> None:
        """把当前前景/背景色刷到色块上（颜色状态在查看器手里，这里只显示）。"""
        self.btn_color.set_colors(fg, bg)

    def set_active_tool(self, name: str | None) -> None:
        """按名字把工具按钮的勾选状态同步过去（不触发信号，避免回调打回环）。"""
        buttons = {
            "source": self.btn_source, "key": self.btn_key, "brush": self.btn_brush,
            "eraser": self.btn_eraser, "pick": self.btn_pick,
        }
        for key, button in buttons.items():
            want = key == name
            if button.isChecked() != want:
                button.blockSignals(True)
                button.setChecked(want)
                button.blockSignals(False)

    def set_paint_checked(self, name: str | None) -> None:
        """只同步画笔/橡皮/吸管三个按钮的勾选态。

        刻意**不动**「原图选择 / 抠除背景」两个按钮：查看器刷自身界面时若顺手把整组
        都按"当前画笔"重设，会把用户刚点亮的原图选择又弹掉（互斥的最终态由主窗口的
        ``_set_active_tool`` 一处决定）。
        """
        name = name if name in ("brush", "eraser", "pick") else None
        for key, button in (("brush", self.btn_brush), ("eraser", self.btn_eraser),
                            ("pick", self.btn_pick)):
            want = key == name
            if button.isChecked() != want:
                button.blockSignals(True)
                button.setChecked(want)
                button.blockSignals(False)

    def tool_buttons(self) -> list[tuple[str, QPushButton]]:
        """(工具名, 按钮) 列表，按界面上从上到下的顺序 —— 互斥处理都按这一份来。"""
        return [
            ("source", self.btn_source), ("key", self.btn_key), ("brush", self.btn_brush),
            ("eraser", self.btn_eraser), ("pick", self.btn_pick),
        ]

    def _sync_height(self) -> None:
        """按当前可见子控件自适应高度：以后加按钮不用手改常量。"""
        layout = self.layout()
        margins = layout.contentsMargins()
        height = margins.top() + margins.bottom()
        count = 0
        for i in range(layout.count()):
            widget = layout.itemAt(i).widget()
            if widget is not None and not widget.isHidden():
                height += widget.height()
                count += 1
        height += max(0, count - 1) * layout.spacing()
        self.setFixedHeight(height)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(30, 30, 30, 220))
        p.setPen(QPen(QColor("#444"), 1))
        p.drawRoundedRect(self.rect(), 6, 6)
        p.end()

    def _make_button(self, icon_kind: str, tip: str, checkable: bool = False) -> QPushButton:
        """与 FloatingToolBar._make_button 同一套样式（两边各自持有，互不影响）。"""
        btn = QPushButton()
        btn.setFixedSize(self.BTN, self.BTN)
        btn.setIcon(make_tool_icon(icon_kind, 16))
        btn.setIconSize(QSize(16, 16))
        btn.setToolTip(tip)
        btn.setCheckable(checkable)
        btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 3px; }"
            "QPushButton:hover { background: rgba(233,30,99,0.3); }"
            "QPushButton:checked { background: rgba(233,30,99,0.5); }"
        )
        return btn

    # ---- 拖动工具条（空白处按下拖）----
    def moved_by_user(self) -> bool:
        """用户是否自己挪过位置（挪过之后就不再被强行搬回左边）。"""
        return self._moved_by_user

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.position().toPoint()
            self.raise_()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is None:
            super().mouseMoveEvent(event)
            return
        parent = self.parentWidget()
        want = self.mapToParent(event.position().toPoint() - self._drag_offset)
        if parent is not None:
            # 只限制在画布范围内，别被拖到看不见的地方
            want.setX(max(0, min(parent.width() - self.width(), want.x())))
            want.setY(max(0, min(parent.height() - self.height(), want.y())))
        self.move(want)
        self._moved_by_user = True
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class JsonBadge(QWidget):
    """画布左上角浮动徽标：当前工程有关联 JSON 时显示红色 JSON ✓。

    与右上角 FloatingToolBar 同高度（y=6，按钮 24px），水平对齐。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(64, 24)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._path = ""

    def set_path(self, path: str) -> None:
        self._path = str(path or "")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(30, 30, 30, 220))
        p.setPen(QPen(QColor("#E91E63"), 1))
        p.drawRoundedRect(self.rect(), 6, 6)
        p.setPen(QPen(QColor("#E91E63")))
        f = QFont("Microsoft YaHei UI", 8, QFont.Weight.Bold)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "JSON ✓")
        p.end()


# 原图查看器（标尺 + 画布容器）
class PaintOptionsBar(QWidget):
    """画布顶部居中的「工具选项」条：画笔/橡皮的**粗细**与当前**颜色**。

    为什么单独做一条而不是塞进右上角那条工具条：那条是 ``setFixedSize`` 定死的，
    往里面加控件就得连带改它的定位/拖动逻辑；而且画笔参数是"跟着工具出现、关掉工具就消失"
    的临时面板，独立一条互不影响。只在画笔/橡皮激活时显示（吸管没有参数）。
    """

    size_changed = pyqtSignal(int)
    color_requested = pyqtSignal(str)          # "fg"，与左侧色块同一条通路

    H = 30

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(self.H)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(6)

        self.lbl_size = QLabel("粗细")
        self.lbl_size.setStyleSheet("color: #d7dbe0; font-size: 11px;")
        layout.addWidget(self.lbl_size)

        # 1~300px：像素画要能细到 1，甩几笔大色块也要够粗（300 够用了）
        self.spin_size = CompactSpinBox()
        self.spin_size.setRange(1, 300)
        self.spin_size.setValue(8)
        self.spin_size.setSuffix(" px")
        self.spin_size.setFixedWidth(78)
        self.spin_size.setStyleSheet(
            "QSpinBox { background: #2b2b2b; color: #e8e8e8; border: 1px solid #555;"
            " border-radius: 3px; padding-left: 4px; font-size: 11px; }")
        self.spin_size.valueChanged.connect(self.size_changed)
        layout.addWidget(self.spin_size)

        self.btn_color = ColorSwatchButton(22)
        self.btn_color.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self.btn_color.foreground_clicked.connect(lambda: self.color_requested.emit("fg"))
        self.btn_color.background_clicked.connect(lambda: self.color_requested.emit("bg"))
        layout.addWidget(self.btn_color)

        self.lbl_tip = QLabel("[ ] 调粗细")
        self.lbl_tip.setStyleSheet("color: #8a8f96; font-size: 11px;")
        layout.addWidget(self.lbl_tip)
        self.adjustSize()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(30, 30, 30, 220))
        p.setPen(QPen(QColor("#444"), 1))
        p.drawRoundedRect(self.rect(), 6, 6)
        p.end()

    # ---- 外部同步（真状态在查看器里，这里只显示）----

    def set_brush_size(self, size: int) -> None:
        if self.spin_size.value() != int(size):
            self.spin_size.blockSignals(True)
            self.spin_size.setValue(int(size))
            self.spin_size.blockSignals(False)

    def set_colors(self, fg: QColor, bg: QColor) -> None:
        self.btn_color.set_colors(fg, bg)

    def set_tool(self, name: str) -> None:
        """按当前工具调整：橡皮擦没有颜色（擦成透明），把色块和提示藏起来。"""
        is_brush = name == "brush"
        self.btn_color.setVisible(is_brush)
        self.lbl_tip.setText("拖动涂抹 · [ ] 调粗细" if is_brush else "拖动擦除 · [ ] 调粗细")
        self.adjustSize()


class _AltNativeFilter(QAbstractNativeEventFilter):
    """Windows 平台：捕获 WM_SYSKEYDOWN/WM_SYSKEYUP（Alt 系统键）。

    带菜单栏的窗口会让纯 Alt 键的 QKeyEvent 被菜单栏拦截（installEventFilter
    收不到），所以必须走原生消息过滤器，让「按下 Alt 时复用按钮高亮变红」
    在任何窗口状态下都生效。
    """

    # WM_SYSKEYDOWN / WM_SYSKEYUP
    _WM_SYSKEYDOWN = 0x0104
    _WM_SYSKEYUP = 0x0105
    # VK_MENU / VK_LMENU / VK_RMENU（Alt 本身，排除 Alt+F4 / Alt+Tab 等组合）
    _ALT_VKS = (0x12, 0xA4, 0xA5)

    def __init__(self, viewer) -> None:
        super().__init__()
        self._viewer = viewer

    def nativeEventFilter(self, eventType, message):
        try:
            if eventType in ("windows_generic_MSG", b"windows_generic_MSG"):
                raw = message.asarray(64) if message else b""
                ptr = struct.calcsize("P")
                if len(raw) < 16:
                    return False, 0
                if ptr == 8:  # x64: hwnd(8) message(4) pad(4) wParam(8)
                    msg_id = struct.unpack_from("=I", raw, 8)[0]
                    wparam = struct.unpack_from("=Q", raw, 16)[0]
                else:         # x86: hwnd(4) message(4) wParam(4)
                    msg_id = struct.unpack_from("=I", raw, 4)[0]
                    wparam = struct.unpack_from("=I", raw, 8)[0]
                if msg_id in (self._WM_SYSKEYDOWN, self._WM_SYSKEYUP) and wparam in self._ALT_VKS:
                    if msg_id == self._WM_SYSKEYDOWN:
                        self._viewer.begin_alt_reuse()
                    else:
                        self._viewer.end_alt_reuse()
        except Exception:  # noqa: BLE001 - 原生消息解析失败时静默，不影响主流程
            pass
        return False, 0


class ImageViewer(QWidget):
    """原图查看器：顶部水平标尺 + 左侧垂直标尺 + 画布 + 浮动工具条。"""

    image_loaded = pyqtSignal(QPixmap)
    json_selected = pyqtSignal(str)
    selection_changed = pyqtSignal()
    frames_updated = pyqtSignal()
    # 在原图上按下鼠标（选元素 / 框选 / 点空白 / 空格平移都算）：主窗口据此收起
    # 右侧「帧编辑」的变换手柄 —— 点了别处，变换按钮就该弹起。
    canvas_pressed = pyqtSignal()
    action_performed = pyqtSignal(str)  # 右键菜单操作完成，带记录文字
    # 影响所有动作的右键操作（如合并为一整块会重映射全部动作的元素索引），
    # 需要全量快照才能正确 undo。
    action_performed_full = pyqtSignal(str)
    # 全量操作前先发这个信号：main_window 在此时抓全量 before（数据还没改）。
    about_to_perform_full = pyqtSignal()
    json_dropped = pyqtSignal(str)   # 拖入 JSON 工程时转发给主窗口处理
    project_dropped = pyqtSignal(str)  # 拖入 SSTOOL 工程时转发给主窗口处理
    json_dropped_current = pyqtSignal(str)  # 画布上拖入 JSON：直接加入当前工程
    image_path_dropped = pyqtSignal(str)  # 画布上拖入图片路径（用于自动找同名 JSON）
    # 左侧竖条工具条上的撤销/重做：转发给主窗口的 _do_undo / _do_redo
    # （撤销作用在哪个栈由主窗口 _active_stack() 决定，这里不重复判断）
    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()
    key_requested = pyqtSignal()          # 「抠除背景」：主窗口据此打开参数浮层
    source_tool_requested = pyqtSignal(bool)   # 「原图选择」开关；主窗口先校验再真正开
    source_selection_changed = pyqtSignal()    # 选中项变了（主窗口刷新状态栏提示）
    # 拖动原图：开始（主窗口抓 before 快照）/ 结束（主窗口收尾：重裁帧、记撤销）。
    # 结束时带一个 bool：这一把到底有没有真的动过（被画布边界夹住时没动，
    # 主窗口据此不要说"已移动"，也别白做一次重裁）
    source_move_started = pyqtSignal()
    source_move_finished = pyqtSignal(bool)
    # 双击棋盘格 = 添加图片（走主窗口同一个"添加图片"入口）
    add_image_requested = pyqtSignal()
    # 画笔/橡皮：一笔开始（主窗口此刻抓全量 before 快照）与结束（主窗口收尾：
    # 重裁帧、刷预览、记一条全局撤销）。带的是工具名，用于历史记录里的文字。
    paint_stroke_started = pyqtSignal(str)
    paint_stroke_finished = pyqtSignal(str)
    # 吸管取到颜色（主窗口写进前景色 + 状态栏反馈）
    color_picked = pyqtSignal(QColor)
    # 画笔/橡皮参数变了（主窗口同步状态栏/工具选项条）
    paint_tool_changed = pyqtSignal(str)
    # 竖条上的画笔/橡皮/吸管按钮被点了（名字；"" = 关掉）：主窗口按单工具模式统一处理
    paint_tool_requested = pyqtSignal(str)
    # 「颜色选择」色块被点了（"fg"/"bg"）：主窗口弹调色板 —— 对话框属于窗口层，
    # 查看器只负责把"用户想改颜色"这件事报上去
    color_requested = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # 状态
        self._pixmap: QPixmap | None = None
        # 抠图预览：只给画布画，不替换 _pixmap —— 这样撤销栈/帧/动作全程不动，
        # "取消"就是把这里清空，零副作用（快照存的是 _pixmap 的引用，不能乱换）
        self._preview_pixmap: QPixmap | None = None
        # 吸管模式：非 None 时画布点击会先交给它取色（key_panel.KeyPanel.sample_at）
        self._pick_target = None
        # 原图分块（多张原图叠成一张大图时，每张原图占的那一块）：
        # 位置**不额外存一份状态**，按 append_pixmap 的同一套算法现算 —— 见 source_rects()。
        # _source_tool：左侧「原图选择」工具开着；_source_panel：抠图面板开着 ——
        # 两者任一为真就显示分块边框（面板开着时也能直接点画布换张，和工具行为一致）。
        self._source_tool = False
        self._source_panel = False
        # 画笔 / 橡皮 / 吸管（PS 单工具模式，三者与原图选择、抠图互斥，由主窗口统一管）。
        # 颜色与粗细放这里而不是工具栏里：工具栏只管显示，改一次要能被画布立刻用上。
        self.paint_tool: str | None = None       # None / "brush" / "eraser" / "pick"
        self.brush_size = 8                      # 画笔粗细（**图片像素**，不是屏幕像素）
        self.paint_color = QColor("#FFFFFF")     # 前景色（画笔落色）
        self.paint_bg_color = QColor("#000000")  # 背景色（PS 那对色块的后块）
        # 一笔的中间状态。两份工作图都是 **QImage**（ARGB32_Premultiplied）：QPixmap 在
        # 全不透明时会被 Qt 存成没有 alpha 通道的 RGB32，橡皮擦的"擦成透明"在上面无效。
        # 拖动期间只改工作图、**不碰 _source_images / _pixmap** —— 撤销快照里存的正是
        # 那两份的引用（见 main_window._capture_snapshot），碰了就把"涂之前"一起改掉了。
        self._paint_copies: dict[int, QImage] = {}   # 这一笔改过的原图工作图
        self._paint_display: QImage | None = None    # 这一笔的合成工作图（画布直接画它）
        self._paint_last: QPointF | None = None   # 上一段终点（图片坐标）
        self._paint_active = False
        self._paint_tool_now = ""                 # 正在画的那一笔的工具名
        self._brush_pos: QPointF | None = None    # 鼠标位置（画布坐标），画笔刷圈用
        # 标线（从标尺拖出来的参考线）：只当"看图和量尺寸"的参考，**不参与导出/识别**。
        # 每条是 ["h"|"v", 图片坐标]，用**列表对象本身**当选中的身份标识（删掉一条不会让
        # 别的标线"串位"）。_guide_drag_active：正在拖（决定松手时是留下还是删掉）。
        self._guides: list[list] = []
        self._sel_guide: list | None = None
        self._guide_drag_active = False
        self._selected_sources: set[int] = set()
        self._band_start = None          # 框选起点（画布坐标）
        self._band_rect = None           # 框选矩形
        self._band_add = False           # 框选时是否按住 Ctrl/Shift（追加）
        self._source_images: list[QPixmap] = []
        # 每张原图在合成大图里的左上角位置 —— 拖动移位改的就是这个。
        # 位置**必须存下来**：允许拖动之后，"重放拼装算法现算"就无从表达了
        # （老工程/异常状态没有这个字段时，才回退到 _replay_source_offsets）。
        self._source_offsets: list[tuple[int, int]] = []
        self._zoom = 1.0
        self._offset = QPointF(0, 0)
        self._bg_mode = "checker"
        self._frames: list[tuple[int, int, int, int]] = []  # 自动检测的元素辅助线
        # 元素的「逻辑坐标」：仅用于计算元素之间的相对位置（合成帧拼接间距）。
        # 为空时逻辑坐标 = _frames 的左上角（正常手工流程）；
        # 从 JSON 导入时，图块被重新打包过，这里会填入 JSON 记录的元素原始坐标，
        # 从而让合成帧的拼接间距与导出前完全一致。
        self._frame_logical: dict[int, tuple[int, int]] = {}
        # 被「忽略选中区域」标记的元素索引集合：不参与自动识别为可选区域
        # （点击/框选/全选/反选全部排除），绘制时仍显示灰色虚线标识存在。
        self._ignored_frames: set[int] = set()
        # 选中与框选（选中状态存储在当前 Action.selected_indices）
        self.current_action: Action | None = None
        self.compose_mode = False
        self.reuse_mode = False  # 复用模式：开启后点击已选元素即追加一帧（Alt+点击为临时复用）
        self._manual_reuse = False  # 复用按钮保持的模式（Alt 临时按下不算）
        self._alt_reusing = False   # Alt 键是否正在按下（临时复用）
        self._manual_compose = False
        self._ctrl_composing = False
        self._active_composite_group: list[int] | None = None
        self.actions: list[Action] = []  # 所有动作列表引用（用于多动作色块绘制）
        self.action_color = "#E91E63"  # 当前动作颜色，选中辅助线用此色
        self._detect_thread: _FrameDetectThread | None = None
        self._detect_threads: set[_FrameDetectThread] = set()
        self._detect_generation = 0
        # 后台元素检测是否在跑 + 这段时间里发生的拖动（结果落地时要补上，见
        # _apply_pending_detect_shifts：不补的话新元素会落在拖动前的位置上）
        self._detect_inflight = False
        self._pending_detect_shifts: list = []
        # 正在被检测的那张原图的下标（-1 = 整张画布重检）：检测期间拖动时，只有拖的是
        # 这张图才需要给结果补位移
        self._detect_owner_index = -1
        # 拖动开始时定下的"搬哪些帧"（见 begin_source_move）
        self._move_frame_owner: set | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        # 布局：[左上角方块 | 水平标尺] / [垂直标尺 | 画布]
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)

        corner = QWidget()
        corner.setFixedSize(RULER_SIZE, RULER_SIZE)
        corner.setStyleSheet("background-color: #1a1a1a; border: none;")
        grid.addWidget(corner, 0, 0)

        self.h_ruler = _Ruler(self, "horizontal")
        grid.addWidget(self.h_ruler, 0, 1)

        self.v_ruler = _Ruler(self, "vertical")
        grid.addWidget(self.v_ruler, 1, 0)

        self.canvas = _Canvas(self)
        self.canvas.image_loaded.connect(self._on_image_loaded)
        self.canvas.json_selected.connect(self._on_json_selected)
        self.canvas.selection_changed.connect(self.selection_changed)
        grid.addWidget(self.canvas, 1, 1)
        grid.setRowStretch(1, 1)
        grid.setColumnStretch(1, 1)

        # 浮动工具条（父对象为画布）
        self._toolbar: FloatingToolBar | None = None
        self._toolbar = FloatingToolBar(self.canvas)
        self._toolbar.move(self.canvas.width() - self._toolbar.width() - 6, 6)
        self._toolbar.zoom_in_requested.connect(self.zoom_in)
        self._toolbar.zoom_out_requested.connect(self.zoom_out)
        self._toolbar.bg_changed.connect(self._on_bg_changed)
        self._toolbar.compose_toggled.connect(self.set_compose_mode)
        self._toolbar.reuse_toggled.connect(self.set_reuse_mode)

        # 左侧浮动竖条工具条（与右上角同款外观，竖着排；位置由画布 resizeEvent 维持）
        self._v_toolbar = VerticalToolBar(self.canvas)
        self._v_toolbar.move(VerticalToolBar.HOME_X, VerticalToolBar.HOME_Y)
        self._v_toolbar.undo_requested.connect(self.undo_requested)
        self._v_toolbar.redo_requested.connect(self.redo_requested)
        self._v_toolbar.key_requested.connect(self.key_requested)
        self._v_toolbar.source_tool_requested.connect(self.source_tool_requested)
        self._v_toolbar.paint_tool_requested.connect(self._on_paint_tool_button)
        self._v_toolbar.color_requested.connect(self.color_requested)
        self._v_toolbar.raise_()

        # 画笔/橡皮的「工具选项」条（粗细 + 颜色）：默认藏着，画笔/橡皮激活时才出现。
        # 位置由画布 resizeEvent 维持在顶部居中。
        self._paint_bar = PaintOptionsBar(self.canvas)
        self._paint_bar.hide()
        self._paint_bar.size_changed.connect(self.set_brush_size)
        self._paint_bar.color_requested.connect(self.color_requested)
        self._paint_bar.set_brush_size(self.brush_size)
        self._paint_bar.set_colors(self.paint_color, self.paint_bg_color)
        self._paint_bar.raise_()
        # 画笔工具按钮按下时，先把"这一个按钮"的勾选状态同步给主窗口的互斥逻辑：
        # 主窗口再回叫 set_paint_tool → _sync_paint_ui，把界面刷成最终态。
        self.paint_tool_changed.connect(self._sync_paint_ui)

        # 左上角 JSON 徽标（有关联 JSON 时显示）
        self._json_badge = JsonBadge(self.canvas)
        self._json_badge.move(6, 6)
        self._json_badge.hide()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            # 原生 Alt 键监听（Windows 系统键会绕过 installEventFilter）
            self._alt_native_filter = _AltNativeFilter(self)
            try:
                app.installNativeEventFilter(self._alt_native_filter)
            except Exception:  # noqa: BLE001 - 平台不支持时退回 eventFilter 路径
                self._alt_native_filter = None
        # 默认选中透明背景
        self._bg_mode = "checker"

    def _update_rulers(self):
        self.h_ruler.update()
        self.v_ruler.update()

    def set_json_badge(self, visible: bool, path: str = "") -> None:
        """显示/隐藏左上角 JSON 徽标（当前工程有关联 JSON 时）。"""
        badge = getattr(self, "_json_badge", None)
        if badge is None:
            return
        if visible:
            badge.set_path(path)
            badge.move(6, 6)
            badge.show()
            badge.raise_()
        else:
            badge.hide()

    # ---- 对外接口 ----
    def set_pixmap(self, pm: QPixmap):
        if pm is None or pm.isNull():
            return
        self._pixmap = pm
        self._source_images = [pm]
        self._source_offsets = [(0, 0)]
        self._frames = []
        # 换了原图，原来的选中索引可能指向另一张图了 —— 直接清空，别留脏状态
        self._selected_sources.clear()
        self._start_frame_detection(pm.toImage(), base_frames=[], y_offset=0)
        self.fit_view()
        self.canvas.update()
        self._update_rulers()
        # 布局完成后再适配一次，确保居中
        QTimer.singleShot(0, self.fit_view)

    def append_pixmap(self, pm: QPixmap) -> bool:
        """将新的原图追加到当前原图下方，并保留已有帧索引与动作。"""
        if pm is None or pm.isNull():
            return False
        if self._pixmap is None or self._pixmap.isNull():
            self.set_pixmap(pm)
            return True

        old_pixmap = self._pixmap
        old_frames = list(self._frames)
        # 间隙常量只留一份：分块位置是**按这个算法现算**的（source_rects 重放拼装），
        # 这里要是写死另一个数字，虚框就会跟实际内容错位
        gap = self.SOURCE_GAP
        combined = QPixmap(max(old_pixmap.width(), pm.width()), old_pixmap.height() + gap + pm.height())
        combined.fill(Qt.GlobalColor.transparent)
        painter = QPainter(combined)
        painter.drawPixmap(0, 0, old_pixmap)
        y_offset = old_pixmap.height() + gap
        painter.drawPixmap(0, y_offset, pm)
        painter.end()

        self._pixmap = combined
        self._source_images.append(pm)
        self._source_offsets.append((0, y_offset))
        # 旧元素可立即继续显示；新增图片的元素由后台线程补充。
        self._frames = old_frames
        self._start_frame_detection(pm.toImage(), base_frames=old_frames, y_offset=y_offset,
                                   owner_index=len(self._source_images) - 1)
        self.fit_view()
        self.canvas.update()
        self._update_rulers()
        QTimer.singleShot(0, self.fit_view)
        return True

    def _start_frame_detection(
        self, image: QImage, base_frames: list[tuple[int, int, int, int]], y_offset: int,
        owner_index: int = -1,
    ) -> None:
        """异步检测元素，避免大图 flood fill 阻塞 UI 线程。

        ``owner_index`` = 这次检测的是第几张原图（-1 = 整张画布重检，如抠图后重检）：
        检测期间用户拖了那张图时，结果要按它的位移补正（见 _apply_pending_detect_shifts）。
        """
        self._detect_generation += 1
        generation = self._detect_generation
        # 新一轮检测的基准就是**当前**的帧列表，所以之前攒下的位移补偿清空
        self._pending_detect_shifts = []
        self._detect_inflight = True
        self._detect_owner_index = owner_index
        from spritesheet.settings import detect_min_area
        thread = _FrameDetectThread(image.copy(), min_area=detect_min_area(), parent=self)
        self._detect_thread = thread
        self._detect_threads.add(thread)

        def apply_result(frames: list[tuple[int, int, int, int]]) -> None:
            if generation != self._detect_generation:
                return
            shifted = [(x, y + y_offset, w, h) for x, y, w, h in frames]
            shifted = self._apply_pending_detect_shifts(shifted)
            self._detect_inflight = False
            if not base_frames:
                # 全新检测（元素索引全部重建）：旧的忽略标记无法可靠对应，清空
                self._ignored_frames.clear()
            # 追加到**当前**帧列表，而不是检测开始时那份：开始之后用户在拖动中挪走的帧
            # 已经在那个列表里被就地改过了，用旧的那份会把它们打回原位
            self._frames = list(self._frames) + shifted
            self.canvas.update()
            self._update_rulers()
            self.frames_updated.emit()

        thread.detected.connect(apply_result)
        thread.finished.connect(lambda t=thread: self._detect_threads.discard(t))
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def fit_view(self):
        """居中并自适应画布窗口（0.9 比例）。"""
        if not self._pixmap or self._pixmap.isNull():
            return
        cw, ch = max(1, self.canvas.width()), max(1, self.canvas.height())
        zw = cw / self._pixmap.width()
        zh = ch / self._pixmap.height()
        self._zoom = min(zw, zh) * 0.9
        self._offset = QPointF(
            (cw - self._pixmap.width() * self._zoom) / 2,
            (ch - self._pixmap.height() * self._zoom) / 2,
        )
        self.canvas.update()
        self._update_rulers()

    def set_current_action(self, action: Action | None):
        """切换当前动作，选中状态从动作中恢复。"""
        self.current_action = action
        if action:
            self.action_color = action.color
        self.canvas.update()

    def set_compose_mode(self, enabled: bool) -> None:
        if enabled and not self.current_action:
            # 没有动作 → 合成出来的帧没有地方存。按钮弹回 + 提示原因，
            # 否则用户看到按钮亮着、点帧却毫无反应。
            self.canvas.hint_no_action()
            if self._toolbar is not None and self._toolbar.btn_compose.isChecked():
                self._toolbar.btn_compose.setChecked(False)
            return
        self._manual_compose = enabled
        # Ctrl 临时激活时，合成按钮必须保持高亮；按钮点击只决定
        # Ctrl 松开后的手动模式是否继续保留。
        self.compose_mode = True if self._ctrl_composing else enabled
        self._active_composite_group = None
        self.canvas.setCursor(
            Qt.CursorShape.CrossCursor if self.compose_mode else Qt.CursorShape.ArrowCursor
        )
        if self._toolbar is not None:
            checked = True if self._ctrl_composing else enabled
            if self._toolbar.btn_compose.isChecked() != checked:
                self._toolbar.btn_compose.setChecked(checked)

    def begin_ctrl_composition(self) -> None:
        if self._ctrl_composing:
            self.compose_mode = True
            self.canvas.setCursor(Qt.CursorShape.CrossCursor)
            if self._toolbar is not None and not self._toolbar.btn_compose.isChecked():
                self._toolbar.btn_compose.setChecked(True)
            return
        self._ctrl_composing = True
        self._active_composite_group = None
        self.compose_mode = True
        self.canvas.setCursor(Qt.CursorShape.CrossCursor)
        if self._toolbar is not None:
            self._toolbar.btn_compose.setChecked(True)

    def end_ctrl_composition(self) -> None:
        # Ctrl+Alt 那一轮的边界到此为止 —— 一次"按住"只开一张新帧，松开就收尾
        # （两个键任一松开都算，Alt 那边见 end_alt_reuse）。
        # 只被组合键路径读，单 Ctrl 的行为一字未改。
        self._end_alt_ctrl_round()
        if not self._ctrl_composing:
            return
        self._ctrl_composing = False
        self._active_composite_group = None
        # A manually enabled compose mode remains active after Ctrl is released.
        self.compose_mode = self._manual_compose
        self.canvas.setCursor(Qt.CursorShape.CrossCursor if self.compose_mode else Qt.CursorShape.ArrowCursor)
        if self._toolbar is not None:
            self._toolbar.btn_compose.setChecked(self.compose_mode)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Control:
            self.begin_ctrl_composition()
        elif event.type() == QEvent.Type.KeyRelease and event.key() == Qt.Key.Key_Control:
            self.end_ctrl_composition()
        elif event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Alt:
            self.begin_alt_reuse()
        elif event.type() == QEvent.Type.KeyRelease and event.key() == Qt.Key.Key_Alt:
            self.end_alt_reuse()
        elif event.type() in (
            QEvent.Type.ApplicationDeactivate,
            QEvent.Type.WindowDeactivate,
        ):
            # 窗口切走时系统可能不会再发 Ctrl/Alt 的 KeyRelease，避免状态和
            # 工具按钮一直卡在按下状态。
            self.end_ctrl_composition()
            self.end_alt_reuse()
        return super().eventFilter(watched, event)

    def add_to_composite(self, idx: int) -> None:
        if not self.current_action or idx < 0 or idx >= len(self._frames):
            return
        action = self.current_action
        if idx not in action.selected_indices:
            action.selected_indices.append(idx)
        # Ctrl-click groups selected elements into the current composite frame.
        if self._active_composite_group is None:
            self._active_composite_group = []
        if not any(self._active_composite_group is g for g in action.composite_groups):
            # 兜底：当前组被裁剪空/摘掉过就重新挂回列表尾部，别往孤儿列表里追加
            action.composite_groups.append(self._active_composite_group)
        if idx not in self._active_composite_group:
            self._active_composite_group.append(idx)

    # Ctrl+Alt 一轮是否已经开着（一次**按住** = 一张新帧）。只服务组合键路径
    _alt_ctrl_round = False

    def seal_alt_ctrl_round(self) -> None:
        """Ctrl+Alt：**一次按住 = 一张新帧**（不是一次点击一张）。

        用户口径："只要按下这两个键，再点其他的，所有的都是新的一帧的元素，直到松开"。
        所以轮的边界是**按键**，不是点击：
        - 同一次按住里连点好几个元素 → 全都进这一张新帧（已经开着就直接 return）；
        - 松开（`eventFilter` 会走到 `end_ctrl_composition()` / `end_alt_reuse()`，
          两个键任一松开都算）后再按住 → 才另起一张。

        封的是"上一轮那一组"：把 `_active_composite_group` 摘掉，组本身仍留在
        `action.composite_groups` 里，所以上一帧内容一个字节都不会变。

        起手还要把**本轮之前已有的帧**落成显式布局（`_materialize_layouts_before_round`）：
        只摘 `_active_composite_group` 是不够的 —— 本轮的组一旦进了 `composite_groups`，
        生成帧用的 `group_by_idx`（一个元素只属于一个组）就会让后一轮把前一轮吞掉，
        帧数永远只剩 1（用户报的"帧序号都是第一帧"，只查 `composite_groups` 看不出来）。

        上一版按"每次点击都封一轮"做，成了**一点一帧**，用户反馈还是不对，这里改成按键边界。
        只服务 Alt+Ctrl，单 Ctrl / 单 Alt 的逻辑一字未改。
        """
        if self._alt_ctrl_round:
            return
        self._materialize_layouts_before_round()
        self._active_composite_group = None
        self._alt_ctrl_round = True

    def _materialize_layouts_before_round(self) -> None:
        """Alt+Ctrl 起手：把当前已经排好的帧落成显式布局（已经是布局就不动）。

        `_layouts_from_selection` 用的是 `group_by_idx`（一元素一组），所以必须在本轮的组
        进 `composite_groups` **之前**落 —— 落晚了前几轮就已经被吞掉了。
        """
        action = self.current_action
        if action is None or not self._frames or action.frame_layouts:
            return
        action.frame_layouts = self._layouts_from_selection(action) or None
        action.frame_layouts_base = len(action.frame_layouts or [])

    def _end_alt_ctrl_round(self) -> None:
        """Alt+Ctrl 一轮收尾：本轮这一组**定格成一帧**（不额外追加）。

        轮内那一帧是"临时帧"（`crop_frames_for_action` 现算着显示，按住期间实时可见），
        松手才真正落进 `action.frame_layouts`；轮内点几个元素都只落这一帧。
        不落盘的话，"一轮里点两个新元素"会被逐元素并入逻辑拆成两帧。
        """
        if not self._alt_ctrl_round:
            return
        self._alt_ctrl_round = False
        group = self._active_composite_group
        self._active_composite_group = None
        action = self.current_action
        if action is None or not group:
            return
        layout = self._frame_layout_for_group(group)
        if not layout:
            return
        if action.frame_layouts is None:
            action.frame_layouts = []
        action.frame_layouts.append(layout)
        # 轮帧属于基线（不可被单 Alt 点击"取消复用"），基线上移 —— 与"新增元素帧"同一口径。
        # 不这么做的话，Alt+点击会把刚攒出来的轮帧当复用帧删掉；元素只剩这一帧时
        # 连选中项一起被清空（整份帧消失），而用户的意图是"Alt = 复用一帧"。
        action.frame_layouts_base = len(action.frame_layouts)

    def _normalize_composite_groups(self) -> None:
        if not self.current_action:
            return
        selected = set(self.current_action.selected_indices)
        groups = []
        for group in self.current_action.composite_groups:
            # **原地**裁剪：`_active_composite_group` 指向的就是这个列表对象本身，
            # 换成新列表会让它变成"不在册的孤儿"——之后 add_to_composite 会往孤儿里追加，
            # 元素进了选中却不进任何组，本该同帧的元素被拆成两帧。
            group[:] = [idx for idx in group if idx in selected]
            if group:
                groups.append(group)
            elif group is self._active_composite_group:
                # 本轮组被裁剪空了：当前组跟着清掉，下一个点进来的元素另起一组
                self._active_composite_group = None
        self.current_action.composite_groups = groups

    # ---- 右键菜单：合并 ----

    def menu_merge_composite(self) -> None:
        """合并为一帧：把当前选中的多个元素组成一个合成组（拼在同一帧里）。"""
        action = self.current_action
        if action is None or len(action.selected_indices) < 2:
            return
        group = list(action.selected_indices)
        # 去掉和已有组完全重复的
        if group not in action.composite_groups:
            action.composite_groups.append(group)
        self._normalize_composite_groups()
        self.crop_frames_for_action(action)
        # 先记录历史（action_performed 抓 before/after），再刷 frames_updated ——
        # frames_updated 会刷新快照基线，放后面才能让 _on_action_performed 抓到正确的 before。
        self.action_performed.emit("合并为一帧")
        self.frames_updated.emit()

    def menu_merge_single_block(self) -> None:
        """合并为一整块：把选中的多个碎块按整体包围盒捏成一个新元素，原碎块删除。"""
        action = self.current_action
        if action is None or len(action.selected_indices) < 2:
            return
        # 数据改动前先发信号，让 main_window 抓全量 before
        self.about_to_perform_full.emit()
        sel = sorted(action.selected_indices)
        # 计算整体包围盒
        x0 = min(self._frames[i][0] for i in sel)
        y0 = min(self._frames[i][1] for i in sel)
        x1 = max(self._frames[i][0] + self._frames[i][2] for i in sel)
        y1 = max(self._frames[i][1] + self._frames[i][3] for i in sel)
        new_rect = (x0, y0, x1 - x0, y1 - y0)
        # 被删除的旧元素索引
        removed = set(sel)
        # 索引重映射：旧索引 -> 新索引
        old_logical = self._frame_logical
        new_frames = []
        self._frame_logical = {}
        mapping = {}
        ni = 0
        for old_i in range(len(self._frames)):
            if old_i in removed:
                continue
            mapping[old_i] = ni
            new_frames.append(self._frames[old_i])
            if old_i in old_logical:
                self._frame_logical[ni] = old_logical[old_i]
            ni += 1
        new_index = ni  # 新元素在重建后的索引
        new_frames.append(new_rect)
        self._frames = new_frames
        # 被忽略标记跟着索引重映射（忽略元素不可选，不会成为合并对象）
        self._ignored_frames = {mapping[i] for i in self._ignored_frames if i in mapping}
        # 所有动作：重映射 selected_indices / composite_groups / frame_layouts
        for act in self._all_actions():
            act.selected_indices = [mapping[i] for i in act.selected_indices if i in mapping]
            act.composite_groups = [
                [mapping[i] for i in g if i in mapping] for g in act.composite_groups
            ]
            act.composite_groups = [g for g in act.composite_groups if g]
            if act.frame_layouts:
                new_layouts = []
                for layout in act.frame_layouts:
                    nl = [(mapping[i], rx, ry) for i, rx, ry in layout if i in mapping]
                    if nl:
                        new_layouts.append(nl)
                act.frame_layouts = new_layouts or None
        # 当前动作选中新元素
        action.selected_indices = [new_index]
        action.composite_groups = []
        action.frame_layouts = None
        action.frame_layouts_base = 0
        self.crop_frames_for_action(action)
        # 这个操作重映射了所有动作的元素索引，必须全量快照；
        # 用 action_performed_full 走全量记录槽（不再单独 emit frames_updated / selection_changed，
        # 避免它们的 debounce/基线刷新副作用干扰本次落账）。
        self.action_performed_full.emit("合并为一整块")

    def _all_actions(self) -> list:
        """返回所有动作（当前 viewer 所属的工程动作列表）。"""
        return getattr(self, "actions", [])

    # ---- 右键菜单：批量 / 选择 / 导出 ----

    def menu_rotate_all_frames(self, angle: int) -> None:
        """把当前动作所有帧旋转 angle 度。"""
        action = self.current_action
        if action is None:
            return
        n = len(action.frames)
        for i in range(n):
            action.frame_rotations[i] = (action.frame_rotations.get(i, 0) + angle) % 360
        action.invalidate_transforms()
        self.frames_updated.emit()
        self.action_performed.emit("全部帧旋转90°")

    def menu_mirror_all_frames(self) -> None:
        """把当前动作所有帧水平镜像切换。"""
        action = self.current_action
        if action is None:
            return
        for i in range(len(action.frames)):
            if i in action.frame_mirror_h:
                action.frame_mirror_h.discard(i)
            else:
                action.frame_mirror_h.add(i)
        action.invalidate_transforms()
        self.frames_updated.emit()
        self.action_performed.emit("全部帧水平镜像")

    def menu_invert_selection(self) -> None:
        """反选：选中当前没选的元素。"""
        action = self.current_action
        if action is None:
            return
        sel = set(action.selected_indices)
        action.selected_indices = [
            i for i in range(len(self._frames))
            if i not in sel and i not in self._ignored_frames
        ]
        # 反选 = 按新选择**重新生成**帧（与清空选择同一口径）：
        # 不清掉 frame_layouts 的话，旧复用帧会被"保留 + 裁剪"下来，和反选的意图不符
        action.composite_groups = []
        action.frame_layouts = None
        action.frame_layouts_base = 0
        self.crop_frames_for_action(action)
        self.frames_updated.emit()
        self.action_performed.emit("反选")

    def menu_select_all(self) -> None:
        """全选所有元素。"""
        action = self.current_action
        if action is None:
            return
        action.selected_indices = [
            i for i in range(len(self._frames)) if i not in self._ignored_frames
        ]
        action.composite_groups = []
        self.crop_frames_for_action(action)
        self.frames_updated.emit()
        self.action_performed.emit("全选")

    def menu_clear_selection(self) -> None:
        """清空当前动作选择。"""
        action = self.current_action
        if action is None:
            return
        action.selected_indices = []
        action.composite_groups = []
        action.frame_layouts = None
        action.frame_layouts_base = 0
        self.crop_frames_for_action(action)
        self.frames_updated.emit()
        self.action_performed.emit("清空选择")

    def menu_remove_selected_elements(self, only_index: int | None = None) -> None:
        """取消选中：只把右键所指的那个元素移出当前动作（不从工程删除，以后还能选回）。

        只取消鼠标当前对着的单个元素，保留其他选中；与「清空选择」区分。
        """
        action = self.current_action
        if action is None or not action.selected_indices:
            return
        if only_index is None or only_index not in action.selected_indices:
            return
        ignored = {only_index}
        action.selected_indices = [i for i in action.selected_indices if i not in ignored]
        action.composite_groups = [
            [i for i in g if i not in ignored] for g in action.composite_groups
        ]
        action.composite_groups = [g for g in action.composite_groups if g]
        self.crop_frames_for_action(action)
        self.frames_updated.emit()
        self.action_performed.emit("取消选中")

    def menu_ignore_selected_regions(self) -> None:
        """忽略选中区域：把选中元素标记为忽略，不再自动识别为可选区域。

        被忽略的元素仍显示灰色虚线（标识存在），但点击 / 框选 / 全选 / 反选
        都选不中它；操作影响所有动作，走全量快照进历史记录，可撤销恢复。
        """
        action = self.current_action
        if action is None or not action.selected_indices:
            return
        # 数据改动前先发信号，让 main_window 抓全量 before
        self.about_to_perform_full.emit()
        self._detach_elements({int(i) for i in action.selected_indices}, mark_ignored=True)
        self.frames_updated.emit()
        self.action_performed_full.emit("忽略选中区域")

    def menu_clear_selected_regions(self) -> None:
        """清空选中区域：把选中元素那几块**像素擦成透明**（等于拿橡皮擦掉），
        并且顺手把它们标成「已忽略」—— 内容都擦没了，就不该再当元素用了。

        与「忽略选中区域」的区别：那个只标记、像素一个不动；这个真把内容抹掉。
        走全量快照（像素也变，快照里带着 pixmap / source_images），Ctrl+Z 一次全回来。
        """
        action = self.current_action
        if action is None or not action.selected_indices or self._pixmap is None:
            return
        indices = sorted({int(i) for i in action.selected_indices
                          if 0 <= int(i) < len(self._frames)})
        if not indices:
            return
        # 先抓 before（含旧 pixmap / source_images 的引用），再换图 —— 顺序反了撤销回不去
        self.about_to_perform_full.emit()
        self.erase_regions([self._frames[i] for i in indices])
        self._detach_elements(set(indices), mark_ignored=True)
        self.frames_updated.emit()
        self.action_performed_full.emit("清空选中区域")

    def erase_regions(self, rects) -> None:
        """把若干矩形（图片坐标）擦成透明：合成图和每张原图都要改。

        **必须换新对象、绝不能就地 QPainter 改原件**：快照存的是 QPixmap 引用
        （隐式共享），就地改会把 before 快照一起改掉，撤销就回不去了 ——
        「抠除背景」和「画笔落地」都踩过同一个坑，这里照同一套办法做。
        """
        if self._pixmap is None or self._pixmap.isNull():
            return
        boxes = [QRect(int(x), int(y), max(0, int(w)), max(0, int(h)))
                 for (x, y, w, h) in rects]
        boxes = [box for box in boxes if not box.isEmpty()]
        if not boxes:
            return
        self._pixmap = QPixmap.fromImage(self._erased_image(self._pixmap, boxes))
        # 每张原图：把落在它自己那块里的部分一起擦掉（原图列表是按块存的，
        # 坐标要平移到"这一张自己的坐标系"）
        for index, (bx, by, bw, bh) in enumerate(self.source_rects()):
            if index >= len(self._source_images):
                break
            block = QRect(bx, by, bw, bh)
            local = []
            for box in boxes:
                inter = box.intersected(block)
                if inter.isEmpty():
                    continue
                local.append(QRect(inter.x() - bx, inter.y() - by,
                                   inter.width(), inter.height()))
            if local:
                self._source_images[index] = QPixmap.fromImage(
                    self._erased_image(self._source_images[index], local))
        self.canvas.update()

    @staticmethod
    def _erased_image(pixmap: QPixmap, boxes: list[QRect]) -> QImage:
        """把 boxes 擦成透明后返回**新的** QImage（原 pixmap 一个像素都不动）。

        走 QImage(ARGB32_Premultiplied) 而不是直接画 QPixmap：QPixmap 在全不透明时
        会被 Qt 存成没有 alpha 通道的 RGB32，"擦成透明"在上面对付不了（橡皮擦踩过）。
        """
        image = ImageViewer._to_paint_image(pixmap)
        painter = QPainter(image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        for box in boxes:
            painter.fillRect(box, QColor(0, 0, 0, 0))
        painter.end()
        return image

    def _detach_elements(self, indices: set[int], mark_ignored: bool) -> None:
        """把若干元素从所有动作里摘掉（选中项 / 合成组 / 布局成员），必要时记进「已忽略」。

        抽成一个方法是因为「忽略选中区域」和「清空选中区域」收尾完全一样：
        元素不再参与任何动作，但 ``_frames`` 本身不动 —— 索引是全局的，
        删条目会让后面所有元素的编号重排（连带撤销、布局、合成组全要重映射），不值得。
        """
        if mark_ignored:
            self._ignored_frames.update(indices)
        for act in self._all_actions():
            act.selected_indices = [i for i in act.selected_indices if i not in indices]
            act.composite_groups = [
                [i for i in g if i not in indices] for g in act.composite_groups
            ]
            act.composite_groups = [g for g in act.composite_groups if g]
            if act.frame_layouts:
                # 布局成员（元素索引, rx, ry）：含被摘掉元素的布局成员直接移除
                act.frame_layouts = [
                    [m for m in layout if m[0] not in indices]
                    for layout in act.frame_layouts
                ]
                act.frame_layouts = [l for l in act.frame_layouts if l] or None
            self.crop_frames_for_action(act)

    def menu_export_selected_png(self) -> None:
        """把当前动作每帧导出为单独 PNG。"""
        from PyQt6.QtWidgets import QFileDialog
        action = self.current_action
        if action is None or not action.frames:
            return
        folder = QFileDialog.getExistingDirectory(self, "导出选中帧 PNG 到文件夹")
        if not folder:
            return
        base = "frame"
        for i, pm in enumerate(action.frames):
            pm.save(str(Path(folder) / f"{base}_{i:03d}.png"), "PNG")

    # ---- 帧复用（按钮模式 + Alt+点击 临时模式）----

    def set_reuse_mode(self, enabled: bool) -> None:
        """切换复用按钮的保持模式；按钮/Alt 任一激活时都处于复用模式。"""
        if enabled and not self.current_action:
            # 同上：没有动作时复用出来的帧没有地方存，提示并让按钮弹回。
            self.canvas.hint_no_action()
            if self._toolbar is not None and self._toolbar.btn_reuse.isChecked():
                self._toolbar.btn_reuse.setChecked(False)
            return
        self._manual_reuse = bool(enabled)
        self.reuse_mode = self._manual_reuse or self._alt_reusing
        if self._toolbar is not None and self._toolbar.btn_reuse.isChecked() != self.reuse_mode:
            self._toolbar.btn_reuse.setChecked(self.reuse_mode)

    def begin_alt_reuse(self) -> None:
        """Alt 按下：临时进入复用模式，复用按钮高亮（图标变红）。"""
        if self._alt_reusing:
            self.reuse_mode = True
            if self._toolbar is not None and not self._toolbar.btn_reuse.isChecked():
                self._toolbar.btn_reuse.setChecked(True)
            return
        self._alt_reusing = True
        self.reuse_mode = True
        if self._toolbar is not None:
            self._toolbar.btn_reuse.setChecked(True)

    def end_alt_reuse(self) -> None:
        """Alt 释放：退出临时复用，回到按钮保持模式的状态。"""
        # Alt 也是 Alt+Ctrl 那一轮的边界：按住 Ctrl 不动、只把 Alt 松开再按，
        # 同样要另起一轮（用户口径"松开以后，再按就是下一帧"）。
        self._end_alt_ctrl_round()
        if not self._alt_reusing:
            return
        self._alt_reusing = False
        self.reuse_mode = self._manual_reuse
        if self._toolbar is not None:
            self._toolbar.btn_reuse.setChecked(self.reuse_mode)

    def _frame_layout_for_group(self, group) -> list[tuple[int, int, int]]:
        """把一组元素转成一条帧布局：[(元素索引, 帧内相对 x, 帧内相对 y), ...]。

        组内相对坐标用**逻辑坐标**差算（与常规生成、JSON 导入完全一致），
        所以单元素组必然得到 [(idx, 0, 0)]，多元素组就是它们拼在一起的相对位置。
        """
        valid = [i for i in group if 0 <= i < len(self._frames)]
        if not valid:
            return []
        lx0 = min(self._logical_xy(i)[0] for i in valid)
        ly0 = min(self._logical_xy(i)[1] for i in valid)
        return [
            (i, self._logical_xy(i)[0] - lx0, self._logical_xy(i)[1] - ly0)
            for i in valid
        ]

    def _action_frame_badges(self, act) -> tuple[dict, dict, list, bool]:
        """算一个动作的帧号标注：(合成代表, 出现帧号表, 逐帧表, 是否按布局)。

        - `fns`：元素出现的**所有**帧号（复用一次就多一个，元素左上角逐个画色块）；
        - `cr`：多元素帧里代表整个框的元素（这些成员不单独画色块，由粗框统一画帧号）；
        - `frames`：逐帧的 [(帧号, 成员...)]，合成粗框和它的帧号**按帧自己的号画**；
        - `from_layouts`：帧号来源是不是显式布局（Alt+Ctrl 轮 / 复用 / 导入）。
          True 时粗框必须按布局逐帧画：按 `composite_groups` 画（一个元素只属于一个组）
          会跟真实帧错位，几个框会一起显示第一帧的号（用户报"帧号还是不对"）。

        Alt+Ctrl 轮按住期间，本轮那一帧还没落盘，这里补在最后 —— 否则它上面的元素
        会退回"选中序号"当帧号，显示出来也是错的。
        """
        cr: dict[int, int] = {}
        fns: dict[int, list[int]] = {}
        frames: list[tuple[int, tuple[int, ...]]] = []
        rows: list[list[int]] = []
        if act.frame_layouts is not None:
            rows = [[i for i, _, _ in layout] for layout in act.frame_layouts]
        else:
            emitted = set()
            for idx in act.selected_indices:
                group = next((tuple(g) for g in act.composite_groups if idx in g), None)
                if group is not None:
                    if group in emitted:
                        continue
                    emitted.add(group)
                    rows.append(list(group))
                else:
                    rows.append([idx])
        if act is self.current_action and self._alt_ctrl_round and self._active_composite_group:
            rows.append([i for i in self._active_composite_group if 0 <= i < len(self._frames)])
        for no, members in enumerate(rows, 1):
            members = [m for m in members if 0 <= m < len(self._frames)]
            if not members:
                continue
            frames.append((no, tuple(members)))
            if len(members) >= 2:
                rep = members[0]
                for m in members:
                    cr[m] = rep
            for m in members:
                fns.setdefault(m, []).append(no)
        return cr, fns, frames, act.frame_layouts is not None

    def _layouts_from_selection(self, action: Action) -> list[list[tuple[int, int, int]]]:
        """把当前选中状态（selected_indices + composite_groups）转成显式帧布局基线。

        每帧 = [(元素索引, 帧内相对 x, 帧内相对 y), ...]；合成组展开为一帧、
        组内相对坐标用逻辑坐标差计算（与常规生成完全一致）。
        """
        layouts: list[list[tuple[int, int, int]]] = []
        group_by_idx = {i: g for g in action.composite_groups for i in g}
        emitted = set()
        for idx in action.selected_indices:
            if idx in emitted:
                continue
            layout = self._frame_layout_for_group(group_by_idx.get(idx, [idx]))
            if not layout:
                continue
            layouts.append(layout)
            emitted.update(i for i, _, _ in layout)
        return layouts

    def _merge_added_into_layouts(self, action: Action, existing_ids: set[int],
                                  skip: set[int] | None = None) -> None:
        """选中新增了元素时，把新元素（及其合成组）并入显式布局，保留既有复用帧。

        只处理"纯新增"（existing_ids 是删除校验后的布局内元素集）；
        新元素按其合成组整体追加为一帧，避免与已入布局的元素重复。

        `skip` = Alt+Ctrl **本轮组**里的元素：它们由"一轮一帧"统一落帧
        （`_end_alt_ctrl_round`），这里必须跳过 —— 否则一轮里点两个新元素会被
        逐元素并成两帧（一轮本该只出一帧）。
        """
        skip = skip or set()
        group_by_idx = {i: g for g in action.composite_groups for i in g}
        in_layout = set(existing_ids)
        before = len(action.frame_layouts)
        for idx in action.selected_indices:
            if idx in in_layout or idx in skip:
                continue
            group = group_by_idx.get(idx, [idx])
            if any(i in skip for i in group):
                continue      # 本轮组：交给轮收尾落帧
            layout = self._frame_layout_for_group(group)
            valid = [i for i, _, _ in layout]
            if not valid or any(i in in_layout for i in valid):
                continue
            action.frame_layouts.append(layout)
            in_layout.update(valid)
        # 新增元素帧属于基线（不可被 Alt 取消），基线上移
        action.frame_layouts_base = getattr(action, "frame_layouts_base", 0) + (
            len(action.frame_layouts) - before
        )

    def _drop_from_layouts(self, action: Action, selected: set[int]) -> None:
        """把"取消选中"的元素从显式布局里**摘掉**，帧本身保留（含基线帧计数修正）。

        以前这里是整份 `frame_layouts = None` 回退常规生成：等于用户多点了/点错一下，
        先前用 Alt 复用出来的帧、Alt+Ctrl 攒出来的多帧会被一次性删光（用户报过丢帧）。
        元素消失了、帧还在 —— 与「忽略选中区域」的处理口径一致。
        """
        base = getattr(action, "frame_layouts_base", 0)
        kept: list[list[tuple[int, int, int]]] = []
        new_base = 0
        for li, layout in enumerate(action.frame_layouts or []):
            layout[:] = [item for item in layout if item[0] in selected]
            if layout:
                kept.append(layout)
                if li < base:
                    new_base += 1
        action.frame_layouts = kept or None
        action.frame_layouts_base = min(new_base, len(kept))

    def toggle_reuse_at(self, idx: int) -> None:
        """Alt+点击：若元素已有复用帧则取消最近一次复用，否则执行复用（toggle）。"""
        if not self.current_action or not self._frames or not (0 <= idx < len(self._frames)):
            return
        action = self.current_action
        layouts = action.frame_layouts
        if layouts is not None:
            base = getattr(action, "frame_layouts_base", 0)
            # 该元素出现的所有帧位
            occ = [li for li, layout in enumerate(layouts)
                   if any(m == idx for m, _, _ in layout)]
            # 只取消「复用区」（>= base）里的帧：基线帧（常规/导入/新增选择）
            # 是元素的原始帧，不可取消。复用区帧本来就是额外追加的，直接移除
            # 最近一次（撤销该次复用）。
            for li in reversed(occ):
                if li < base:
                    break
                del action.frame_layouts[li]
                # 清理只属于被取消复用帧的纯复用元素，避免 crop 的"新增"判定
                # 把刚取消的帧重新并入。
                remaining_ids = {
                    m for layout in action.frame_layouts for m, _, _ in layout
                }
                action.selected_indices = [
                    i for i in action.selected_indices if i in remaining_ids
                ]
                self.crop_frames_for_action(action)
                self.frames_updated.emit()
                self.selection_changed.emit()
                return
        # 没有可取消的复用 -> 执行复用
        self.reuse_frame_at(idx)

    def reuse_frame_at(self, idx: int) -> None:
        """复用 idx 元素当前所属的帧：把它再追加为一帧（同一元素可出现在多帧）。

        通过显式帧布局 frame_layouts 表达，与 JSON 导入的"同一元素多帧"统一，
        导出/导入往返不丢复用关系。原元素/原合成组保持不动。
        """
        if not self.current_action or not self._frames or not (0 <= idx < len(self._frames)):
            return
        action = self.current_action
        group = next((g for g in action.composite_groups if idx in g), None)
        valid = [i for i in (group if group is not None else [idx]) if 0 <= i < len(self._frames)]
        if not valid:
            return
        if action.frame_layouts is None:
            action.frame_layouts = self._layouts_from_selection(action)
            action.frame_layouts_base = len(action.frame_layouts)
        # 复用元素纳入主选区：布局里出现但未选中的元素（纯复用）不会被
        # crop 的"取消选择"判定误回退。先建基线再加，避免基线多出一帧。
        for m in valid:
            if m not in action.selected_indices:
                action.selected_indices.append(m)
        if len(valid) == 1:
            layout = [(valid[0], 0, 0)]
        else:
            lx0 = min(self._logical_xy(i)[0] for i in valid)
            ly0 = min(self._logical_xy(i)[1] for i in valid)
            layout = [
                (i, self._logical_xy(i)[0] - lx0, self._logical_xy(i)[1] - ly0)
                for i in valid
            ]
        action.frame_layouts.append(layout)
        self.crop_frames_for_action(action)
        self.frames_updated.emit()
        self.selection_changed.emit()

    def _logical_xy(self, index: int) -> tuple[int, int]:
        """元素的逻辑坐标：用于计算元素间相对位置；缺省时等于其在图中的位置。"""
        if index in self._frame_logical:
            return self._frame_logical[index]
        frame = self._frames[index]
        return (frame[0], frame[1])

    def crop_frames_for_action(self, action: Action):
        """根据当前动作选中的元素索引，从原图区裁剪为帧。帧序即选中序号。

        裁剪始终使用元素在「当前载入的图」里的实际位置；
        而合成帧内各元素的相对位置使用元素的**逻辑坐标**计算，
        这样从 JSON 导入的工程也能还原出与导出前一致的拼接间距。
        """
        if not self._pixmap or not action.selected_indices:
            action.frames = []
            action.frame_rects = []
            action.frame_parts = []
            action.frame_offsets.clear()
            action.frame_rotations.clear()
            action.frame_mirror_h.clear()
            action.frame_mirror_v.clear()
            action.frame_rotation_pivots.clear()
            action.frame_quads.clear()
            action.frame_transform_modes.clear()
            action.invalidate_transforms()
            action.ref_frame = -1
            return
        # 显式帧布局（从 JSON 导入 / 复用帧）：逐帧按记录的部件与位置生成，
        # 支持同一元素出现在多个合成帧中（避免丢帧），且导出可复用元素图块。
        if action.frame_layouts:
            selected = set(action.selected_indices)
            layout_ids = {i for layout in action.frame_layouts for i, _, _ in layout}
            removed = layout_ids - selected
            added = selected - layout_ids
            if removed:
                # 显式取消了元素选择：只把这些元素从各帧里**摘掉**，帧本身保留
                # （整份丢弃 = 一次误点就把复用帧 / Alt+Ctrl 攒的多帧全删，见 _drop_from_layouts）
                self._drop_from_layouts(action, selected)
                layout_ids &= selected
                added = selected - layout_ids
            if added and action.frame_layouts:
                # 新增元素 -> 并入布局（保留已有帧，不丢用户工作）。
                # Alt+Ctrl 本轮组例外：它由"一轮一帧"统一落帧（见 _end_alt_ctrl_round），
                # 否则一轮里点两个新元素会被逐元素并成两帧。
                skip = set(self._active_composite_group or ()) if self._alt_ctrl_round else set()
                self._merge_added_into_layouts(action, layout_ids, skip)
            if action.frame_layouts:
                layouts = list(action.frame_layouts)
                if self._alt_ctrl_round and self._active_composite_group:
                    # 本轮的帧还没落盘（松手才落）：现算一帧接在最后，
                    # 按住 Alt+Ctrl 期间也能实时看到"这一帧"长什么样。
                    live = self._frame_layout_for_group(self._active_composite_group)
                    if live:
                        layouts.append(live)
                frames = []
                rects = []
                parts_all = []
                for layout in layouts:
                    valid = [(i, rx, ry) for i, rx, ry in layout if 0 <= i < len(self._frames)]
                    if not valid:
                        continue
                    if len(valid) == 1:
                        idx, rx, ry = valid[0]
                        sx, sy, sw, sh = self._frames[idx]
                        # Keep a non-zero imported part position (for example
                        # TexturePacker's spriteSourceSize offset).  The
                        # transparent canvas is part of the frame layout and
                        # must not be collapsed to the source tile's origin.
                        frame_w = max(1, rx + sw)
                        frame_h = max(1, ry + sh)
                        frame = QPixmap(frame_w, frame_h)
                        frame.fill(Qt.GlobalColor.transparent)
                        painter = QPainter(frame)
                        painter.drawPixmap(rx, ry, self._pixmap.copy(sx, sy, sw, sh))
                        painter.end()
                        frames.append(frame)
                        # 用原图中的真实位置作包围盒，原图模式导出才不会裁错像素
                        rects.append((sx, sy, sw, sh))
                        parts_all.append([((sx, sy, sw, sh), rx, ry, sx, sy)])
                        continue
                    w = max(rx + self._frames[i][2] for i, rx, ry in valid)
                    h = max(ry + self._frames[i][3] for i, rx, ry in valid)
                    canvas = QPixmap(max(1, w), max(1, h))
                    canvas.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(canvas)
                    parts = []
                    for idx, rx, ry in valid:
                        sx, sy, sw, sh = self._frames[idx]
                        painter.drawPixmap(rx, ry, self._pixmap.copy(sx, sy, sw, sh))
                        parts.append(((sx, sy, sw, sh), rx, ry, sx, sy))
                    painter.end()
                    frames.append(canvas)
                    rects.append((0, 0, max(1, w), max(1, h)))
                    parts_all.append(parts)
                action.frames = frames
                action.frame_rects = rects
                action.frame_parts = parts_all
                valid_idx = set(range(len(frames)))
                action.frame_offsets = {k: v for k, v in action.frame_offsets.items() if k in valid_idx}
                action.frame_rotations = {k: v for k, v in action.frame_rotations.items() if k in valid_idx}
                action.frame_mirror_h &= valid_idx
                action.frame_mirror_v &= valid_idx
                action.frame_rotation_pivots = {k: v for k, v in action.frame_rotation_pivots.items() if k in valid_idx}
                action.frame_quads = {k: v for k, v in action.frame_quads.items() if k in valid_idx}
                action.frame_transform_modes = {k: v for k, v in action.frame_transform_modes.items() if k in valid_idx}
                action.invalidate_transforms()
                if action.ref_frame >= len(frames):
                    action.ref_frame = -1
                return
            # 布局被裁到一帧不剩（元素全被取消选中）-> 回到常规生成
            action.frame_layouts = None
            action.frame_layouts_base = 0
        frames = []
        rects = []
        parts_all: list[list[tuple[tuple[int, int, int, int], int, int, int, int]]] = []
        group_by_idx = {idx: group for group in action.composite_groups for idx in group}
        emitted = set()
        for idx in action.selected_indices:
            if idx in emitted:
                continue
            group = group_by_idx.get(idx, [idx])
            valid = [i for i in group if 0 <= i < len(self._frames)]
            if not valid:
                continue
            parts: list[tuple[tuple[int, int, int, int], int, int, int, int]] = []
            if len(valid) == 1:
                # 单元素帧：帧就是该元素本身
                sx, sy, sw, sh = self._frames[valid[0]]
                lx, ly = self._logical_xy(valid[0])
                frame = self._pixmap.copy(sx, sy, sw, sh)
                parts.append(((sx, sy, sw, sh), 0, 0, lx, ly))
                rects.append((sx, sy, sw, sh))
            else:
                # 合成帧：拼接间距按逻辑坐标算，像素按实际位置裁
                lx0 = min(self._logical_xy(i)[0] for i in valid)
                ly0 = min(self._logical_xy(i)[1] for i in valid)
                lx1 = max(self._logical_xy(i)[0] + self._frames[i][2] for i in valid)
                ly1 = max(self._logical_xy(i)[1] + self._frames[i][3] for i in valid)
                cw, ch = max(1, lx1 - lx0), max(1, ly1 - ly0)
                frame = QPixmap(cw, ch)
                frame.fill(Qt.GlobalColor.transparent)
                painter = QPainter(frame)
                for source_idx in valid:
                    sx, sy, sw, sh = self._frames[source_idx]
                    lx, ly = self._logical_xy(source_idx)
                    rel_x, rel_y = lx - lx0, ly - ly0
                    painter.drawPixmap(rel_x, rel_y, self._pixmap.copy(sx, sy, sw, sh))
                    parts.append(((sx, sy, sw, sh), rel_x, rel_y, lx, ly))
                painter.end()
                rects.append((lx0, ly0, cw, ch))
            frames.append(frame)
            parts_all.append(parts)
            emitted.update(valid)
        action.frames = frames
        action.frame_rects = rects
        action.frame_parts = parts_all
        # 清理失效的单帧偏移、旋转/镜像与参照帧
        valid = set(range(len(frames)))
        action.frame_offsets = {k: v for k, v in action.frame_offsets.items() if k in valid}
        action.frame_rotations = {k: v for k, v in action.frame_rotations.items() if k in valid}
        action.frame_mirror_h &= valid
        action.frame_mirror_v &= valid
        action.frame_rotation_pivots = {k: v for k, v in action.frame_rotation_pivots.items() if k in valid}
        action.frame_quads = {k: v for k, v in action.frame_quads.items() if k in valid}
        action.frame_transform_modes = {k: v for k, v in action.frame_transform_modes.items() if k in valid}
        action.invalidate_transforms()
        if action.ref_frame >= len(frames):
            action.ref_frame = -1

    def set_actions(self, actions: list[Action]):
        """设置动作列表引用（用于多动作色块绘制）。"""
        self.actions = actions

    def _sort_reading_order(self, indices: list[int]) -> list[int]:
        """按阅读顺序排序：从上到下分行，每行内从左到右。"""
        if not indices:
            return indices
        # 按中心y排序后分行
        centers = sorted(
            [(self._frames[i][0] + self._frames[i][2] / 2,
              self._frames[i][1] + self._frames[i][3] / 2, i)
             for i in indices],
            key=lambda c: c[1]
        )
        # 行高阈值：平均高度的0.8倍；以每行第一个元素的中心y为行基准
        avg_h = sum(self._frames[i][3] for i in indices) / len(indices)
        rows = []
        current_row = [centers[0]]
        row_y = centers[0][1]
        for c in centers[1:]:
            if c[1] - row_y < avg_h * 0.8:
                current_row.append(c)
            else:
                rows.append(current_row)
                current_row = [c]
                row_y = c[1]
        rows.append(current_row)
        # 每行按x排序
        result = []
        for row in rows:
            row.sort(key=lambda c: c[0])
            result.extend([c[2] for c in row])
        return result

    # ---- 坐标与命中 ----
    def _image_top_left(self) -> QPointF:
        """图片左上角在画布视图中的坐标。"""
        return self._offset

    # 多张原图叠成一张大图时的间距（必须与 append_pixmap 里的 gap 是同一个数）
    SOURCE_GAP = 16
    # 拖动移位时画布能长到多大：同宽的图横向本来没余量，允许长大才有"随意移动"；
    # 再大也没意义（还容易把内存吃满），到顶就夹住
    SOURCE_MOVE_MAX = 8192

    def source_rects(self) -> list[tuple[int, int, int, int]]:
        """每张原图在合成大图里的矩形 (x, y, w, h)。

        位置取自 ``_source_offsets``（**存下来的状态**，拖动移位会改它）。只有状态对不上
        （老工程没存偏移、或合成图与源图不一致）时才回退到重放拼装算法
        （``_replay_source_offsets``）—— 宁可不给选，也不能给出错的块。
        """
        if not self._source_images:
            return []
        if len(self._source_offsets) != len(self._source_images):
            self._source_offsets = self._replay_source_offsets()
        return [(x, y, pm.width(), pm.height())
                for (x, y), pm in zip(self._source_offsets, self._source_images)]

    def _replay_source_offsets(self) -> list[tuple[int, int]]:
        """重放 append_pixmap 的拼装算法：第 i 张在 y = 前面高度和 + i×gap。

        这是**兼容用**的兜底路径（老工程里没有偏移字段）。算出来跟当前合成图尺寸对不上，
        就退化成"整张算一块"（返回单个 (0,0)，``source_rects`` 按 zip 截断后正好如此）。
        """
        offsets = []
        y = 0
        for index, pm in enumerate(self._source_images):
            if index:
                y += self.SOURCE_GAP
            offsets.append((0, y))
            y += pm.height()
        if self._pixmap is not None and not self._pixmap.isNull():
            if y != self._pixmap.height() or any(pm.width() > self._pixmap.width()
                                                 for pm in self._source_images):
                return [(0, 0)]
        return offsets

    def move_sources(self, indices, dx: int, dy: int) -> bool:
        """把选中的原图整体移动 (dx, dy) 像素：里面的帧跟着一起走，画布按需长大。

        为什么允许画布长大：多张**同宽**的图摞在一起时，画布宽度就等于图宽，横向一点余量
        都没有，用户拖一下就会说"不能随意移动"。长大的只是**右下边界**，左上角原点不动，
        所以帧坐标基准 / 导出 / 工程格式都不受影响（这正是当初"画布固定"想保住的东西）。
        往左上仍以 (0,0) 为界：要越过原点就得把全部坐标整体重算一遍，不值得。
        返回是否真的动了 —— 被夹住一点没动时返回 False，调用方据此不收尾（快照自己会判等，
        不会白记一条撤销）。
        """
        rects = self.source_rects()
        moved = [int(i) for i in indices if 0 <= int(i) < len(rects)]
        if not moved or self._pixmap is None or self._pixmap.isNull():
            return False
        width, height = self._pixmap.width(), self._pixmap.height()
        # 夹取按**整组**算：组里最靠边的块也得守规矩，所以取各块允许范围的交集
        low_x = max(-rects[i][0] for i in moved)
        high_x = min(self.SOURCE_MOVE_MAX - (rects[i][0] + rects[i][2]) for i in moved)
        low_y = max(-rects[i][1] for i in moved)
        high_y = min(self.SOURCE_MOVE_MAX - (rects[i][1] + rects[i][3]) for i in moved)
        dx = max(low_x, min(high_x, int(dx)))
        dy = max(low_y, min(high_y, int(dy)))
        if dx == 0 and dy == 0:
            return False
        old_rects = [(i, rects[i]) for i in moved]
        if self._detect_inflight and (self._detect_owner_index < 0
                                      or self._detect_owner_index in moved):
            # 后台检测还在跑，而且被拖的正是它正在检测的那张图：它算的是拖动前的位置，
            # 这次位移记下来，结果回来时补给那批新元素（否则它们就"留在原处、跟内容错开"）
            self._pending_detect_shifts.append((old_rects, dx, dy))
        for index in moved:
            x, y = self._source_offsets[index]
            self._source_offsets[index] = (x + dx, y + dy)
        self._shift_frames_with(old_rects, dx, dy, owner=self._move_frame_owner)
        # 有块越过右下边界就把画布长到刚好装下。只长不缩：拖动中画布忽大忽小很晃眼，
        # 留白本身也无害（导出用的是帧，不是这张工作图）
        need_w, need_h = width, height
        for x, y, w, h in self.source_rects():
            need_w, need_h = max(need_w, x + w), max(need_h, y + h)
        self._recompose_sources(QSize(need_w, need_h))
        self.canvas.update()
        self._update_rulers()
        return True

    def _shift_rects(self, rects, old_rects, dx: int, dy: int) -> list:
        """把"落在被拖动块里"的矩形整体平移 (dx, dy)，返回新列表（不改原列表）。

        判定见 ``_belongs_to_moved``：只按**中心点**判会在框跨越块边界、或检测出的框比
        原图还大时漏掉，那个框就留在原处、跟内容错开。
        """
        if not rects or (dx == 0 and dy == 0):
            return [tuple(r) for r in rects]
        out = []
        for rect in rects:
            if self._belongs_to_moved(rect, old_rects):
                out.append((rect[0] + dx, rect[1] + dy, rect[2], rect[3]))
            else:
                out.append(tuple(rect))
        return out

    @staticmethod
    def _belongs_to_moved(rect, old_rects) -> bool:
        """这个框算不算"就在被拖动的那张原图里"（按中心点判，边界算在内）。

        用中心点而不是"碰没碰到"：元素永远属于它中心所在的那张图。边界取闭区间，
        是为了兜住缩放取整导致中心正好压在块边界上的情况。
        """
        fx, fy, fw, fh = rect
        cx, cy = fx + fw / 2.0, fy + fh / 2.0
        for _index, (bx, by, bw, bh) in old_rects:
            if bx <= cx <= bx + bw and by <= cy <= by + bh:
                return True
        return False

    def _apply_pending_detect_shifts(self, rects: list) -> list:
        """检测结果落地前，补上"检测期间发生过的拖动"。

        后台检测算的是**开始那一刻**的位置（y_offset 也是那时算的）；如果用户在检测跑完
        之前就拖走了那张图，这批新元素会落在拖动前的位置上 —— 画出来正是用户看到的
        "留在原处、跟内容错开的虚线框"。这里把那些位移按顺序补回去。
        """
        pending = self._pending_detect_shifts
        if pending:
            self._pending_detect_shifts = []
            whole_canvas = self._detect_owner_index < 0
            for old_rects, mdx, mdy in pending:
                if whole_canvas:
                    # 整张画布重检：结果里混着所有图的元素，只能按几何判（旧矩形 = 拖动前的位置）
                    rects = self._shift_rects(rects, old_rects, mdx, mdy)
                else:
                    # 这批元素**全来自正在被检测的那张图**，被拖的也是它 → 整批一起搬。
                    # 不按几何判：几何判会在"扫过邻居"时误伤邻居的元素（用户报过这个）
                    rects = [(x + mdx, y + mdy, w, h) for x, y, w, h in rects]
        return rects

    def begin_source_move(self, indices) -> None:
        """拖动开始：把"哪些帧属于这几张图"一次定死。

        为什么必须定死：拖动中几何一直在变，如果每次都按"当前几何重叠"重新判，被拖的图
        **扫过邻居时邻居的元素框会被一起带走** —— 用户看到的就是"拖动时路过另一张图，
        就带走了另一张图那么大的一圈灰色虚线"（邻居整张图算一个元素时，那圈正好和图一样大）。
        归属只在拖动开始时算一次，之后整场就只搬这一批。
        """
        rects = self.source_rects()
        pairs = [(int(i), rects[int(i)]) for i in indices if 0 <= int(i) < len(rects)]
        if not pairs:
            self._move_frame_owner = None
            return
        self._move_frame_owner = {
            pos for pos, frame in enumerate(self._frames)
            if self._belongs_to_moved(frame, pairs)
        }

    def end_source_move(self) -> None:
        """拖动结束：清掉定死的归属，下次拖动重新算。"""
        self._move_frame_owner = None

    def _shift_frames_with(self, old_rects: list, dx: int, dy: int,
                           owner: set | None = None) -> None:
        """把落在被移动块里的帧按同一位移搬走（内容与框始终粘在一起）。

        ``owner`` 就是"拖动开始定下的那一批帧索引"（``begin_source_move``）；不给时按当前
        几何判一次（脚本/测试直接调 API 的情况）。这里只改几何；每个动作缓存的帧图/变换
        由主窗口在**拖动结束**时统一重算 —— 拖动中每次鼠标移动都重裁一遍会明显卡顿。

        注意是**原地改元素**而不是换一个新列表：检测结果落地时要在这个列表上追加，
        换新列表会切断那份引用（老代码踩过这个）。
        """
        if not self._frames or (dx == 0 and dy == 0):
            return
        if owner is None:
            owner = {pos for pos, frame in enumerate(self._frames)
                     if self._belongs_to_moved(frame, old_rects)}
        for pos in owner:
            if not 0 <= pos < len(self._frames):
                continue
            frame = self._frames[pos]
            self._frames[pos] = type(frame)((frame[0] + dx, frame[1] + dy, frame[2], frame[3]))

    def _recompose_sources(self, size: QSize | None = None) -> None:
        """按当前偏移把各张原图重新拼成合成大图。

        ``size`` 给了就按它新建（拖到右下边界外时画布要长大），不给就沿用当前尺寸。
        """
        if self._pixmap is None or self._pixmap.isNull() or not self._source_images:
            return
        target = size if size is not None and size.isValid() else self._pixmap.size()
        combined = QPixmap(target)
        combined.fill(Qt.GlobalColor.transparent)
        painter = QPainter(combined)
        for (x, y), pm in zip(self._source_offsets, self._source_images):
            painter.drawPixmap(int(x), int(y), pm)
        painter.end()
        self._pixmap = combined

    def source_index_at(self, pos: QPointF) -> int:
        """画布坐标点落在第几张原图上；不在任何一块里返回 -1。"""
        if not self._pixmap:
            return -1
        ix = (pos.x() - self._offset.x()) / (self._zoom or 1.0)
        iy = (pos.y() - self._offset.y()) / (self._zoom or 1.0)
        for index, (sx, sy, sw, sh) in enumerate(self.source_rects()):
            if sx <= ix < sx + sw and sy <= iy < sy + sh:
                return index
        return -1

    def source_indices_in_rect(self, rect: QRectF) -> list[int]:
        """画布矩形碰到的所有原图（框选用）。"""
        if not self._pixmap:
            return []
        # 严格水平/垂直的拖动会得到"零宽或零高"的矩形，而 QRectF.intersects 对**空矩形
        # 恒为 False** —— 用户明明拖了一整条，却一个都选不中。给这种退化情形补半像素。
        if rect.width() < 0.5 or rect.height() < 0.5:
            rect = rect.adjusted(-0.5, -0.5, 0.5, 0.5)
        zoom = self._zoom or 1.0
        hits = []
        for index, (sx, sy, sw, sh) in enumerate(self.source_rects()):
            block = QRectF(self._offset.x() + sx * zoom, self._offset.y() + sy * zoom,
                           sw * zoom, sh * zoom)
            if block.intersects(rect):
                hits.append(index)
        return hits

    # ---- 原图选择（左侧「原图选择」工具 + 抠图面板共用的同一份选中状态）----

    @property
    def source_interaction(self) -> bool:
        """原图选择/拖动的手势要不要接管鼠标。

        工具开着就接管（**只有一张也接管** —— 单张时"选哪张"没意义，但"拖动移位"有意义，
        用户明确要求这个按钮不依赖有没有动作、也不依赖几张图）；抠图面板开着时只在多张时
        接管（一张没什么可挑的）。
        """
        rects = self.source_rects()
        if not rects:
            return False
        return bool(self._source_tool) or (bool(self._source_panel) and len(rects) > 1)

    @property
    def source_borders_visible(self) -> bool:
        """要不要画原图分块框。

        只有**多张**时才画：单张图没有"哪一张"可区分，画一圈框就是纯噪音 ——
        用户会把它看成"多出来的一圈（灰）虚线框"，而且它沿图片外框画，图片有透明边距时
        看着还和内容错开。
        """
        return self.source_interaction and len(self.source_rects()) > 1

    def selected_sources(self) -> list[int]:
        """当前选中的原图索引（升序）。"""
        return sorted(self._selected_sources)

    def set_selected_sources(self, indices, notify: bool = True) -> None:
        """整批设置选中项（会自动丢掉越界索引，避免状态变脏后点出界）。"""
        count = len(self.source_rects())
        self._selected_sources = {int(i) for i in indices if 0 <= int(i) < count}
        self.canvas.update()
        if notify:
            self.source_selection_changed.emit()

    def toggle_source_selection(self, index: int, additive: bool = False) -> None:
        """点选一张原图：additive（Ctrl/Shift）时切换该张的选中态，否则独占选中它。"""
        count = len(self.source_rects())
        if not (0 <= index < count):
            return
        if additive:
            if index in self._selected_sources:
                self._selected_sources.discard(index)
            else:
                self._selected_sources.add(index)
        else:
            self._selected_sources = {index}
        self.canvas.update()
        self.source_selection_changed.emit()

    def selected_source_blocks(self) -> list[tuple]:
        """选中的 (索引, 矩形) 列表，按索引升序。"""
        rects = self.source_rects()
        return [(i, rects[i]) for i in self.selected_sources() if i < len(rects)]

    def set_source_tool(self, on: bool) -> None:
        """开关「原图选择」工具。关掉时清掉框选过程状态（选中结果保留，方便接着抠图）。"""
        self._source_tool = bool(on)
        if not on:
            self._band_start = None
            self._band_rect = None
            self.canvas.unsetCursor()
        else:
            self.canvas.setCursor(Qt.CursorShape.CrossCursor)
            self.canvas.setFocus(Qt.FocusReason.OtherFocusReason)
            if not self._selected_sources and len(self.source_rects()) == 1:
                # 只有一张原图：它天然就是选中项。不补这一下的话，"拖动移位"会因为
                # 选中集合为空而拖不动（用户明确要求单张也能拖）
                self.set_selected_sources([0])
        self.canvas.update()

    # ------------------------------------------------------------- 画笔 / 橡皮 / 吸管

    @property
    def paint_mode(self) -> str | None:
        """当前是不是"绘制"模式（画笔/橡皮）。吸管只取色，不算绘制。"""
        return self.paint_tool if self.paint_tool in ("brush", "eraser", "pick") else None

    @property
    def painting(self) -> bool:
        """画笔/橡皮（不含吸管）—— 决定鼠标拖动是涂抹还是选元素。"""
        return self.paint_tool in ("brush", "eraser")

    def _place_paint_bar(self) -> None:
        """摆「工具选项」条：优先画布顶部居中，位置不够就躲开两条浮动工具条。

        为什么不能只写死"顶部居中"：默认窗口是 1000×1000，去掉左列之后画布只剩四百来像素，
        而右上角的浮动工具条自己就占 335px —— 居中的参数条会**压在它上面**。
        规则：左边的竖条要躲（x 至少推到它右边），前面叠上右上角横条时就让到它下面一行。
        """
        bar = getattr(self, "_paint_bar", None)
        canvas = self.canvas
        if bar is None or canvas is None or not bar.isVisible():
            return
        margin = 6
        width = bar.width()
        left_limit = margin
        v_bar = getattr(self, "_v_toolbar", None)
        if v_bar is not None and v_bar.isVisible():
            left_limit = v_bar.x() + v_bar.width() + margin
        x = max(left_limit, (canvas.width() - width) // 2)
        if x + width > canvas.width() - margin:
            x = max(left_limit, canvas.width() - width - margin)
        y = margin
        top_bar = getattr(self, "_toolbar", None)
        if top_bar is not None and top_bar.isVisible():
            mine = QRect(x, y, width, bar.height())
            theirs = QRect(top_bar.x(), top_bar.y(), top_bar.width(), top_bar.height())
            if mine.intersects(theirs):
                y = theirs.bottom() + margin
        bar.move(x, y)

    def _on_paint_tool_button(self, name: str, on: bool) -> None:
        """竖条上画笔/橡皮/吸管按钮被点了：转成"设哪个工具"（None = 都关）。

        互斥不在查看器里做：主窗口要顺带处理"关掉抠图面板""弹起原图选择"这些
        跨模块的收尾（见 MainWindow._set_active_tool），统一由它定夺。
        """
        self.paint_tool_requested.emit(name if on else "")

    def _sync_paint_ui(self) -> None:
        """把画笔工具状态刷到界面上：工具选项条显隐/内容、色块颜色。"""
        name = self.paint_tool or ""
        bar = getattr(self, "_paint_bar", None)
        if bar is not None:
            show = name in ("brush", "eraser")
            if show:
                bar.set_tool(name)
                bar.set_brush_size(self.brush_size)
                bar.set_colors(self.paint_color, self.paint_bg_color)
                if not bar.isVisible():
                    bar.show()
                self._place_paint_bar()
                bar.raise_()
            else:
                bar.hide()
        bar_v = getattr(self, "_v_toolbar", None)
        if bar_v is not None:
            bar_v.set_paint_colors(self.paint_color, self.paint_bg_color)
            bar_v.set_paint_checked(name or None)

    def sync_paint_colors(self) -> None:
        """前景/背景色改过之后，把两处显示刷一遍（颜色真值在查看器里）。"""
        self._paint_bar.set_colors(self.paint_color, self.paint_bg_color)
        self._v_toolbar.set_paint_colors(self.paint_color, self.paint_bg_color)

    def set_paint_tool(self, name: str | None) -> None:
        """开关画笔 / 橡皮 / 吸管（None 或 "" = 都关掉）。

        位置参数用字符串而不是枚举：工具栏按钮、快捷键都用同一个名字传下来，
        主窗口的互斥逻辑也只认这一个名字，不必三处各写一套映射。
        """
        name = name if name in ("brush", "eraser", "pick") else None
        if self._paint_active:
            self.end_paint_stroke()          # 画到一半切工具：先把这一笔收尾
        self._paint_copies.clear()
        self._paint_display = None
        self._paint_last = None
        self.paint_tool = name
        self._brush_pos = None
        if name:
            self.canvas.setCursor(Qt.CursorShape.CrossCursor)
            self.canvas.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.canvas.unsetCursor()
        self.canvas.update()
        self.paint_tool_changed.emit(name or "")

    def set_brush_size(self, size: int) -> None:
        self.brush_size = max(1, min(300, int(size)))
        self.canvas.update()

    def set_paint_color(self, color: QColor, background: bool = False) -> None:
        if background:
            self.paint_bg_color = QColor(color)
        else:
            self.paint_color = QColor(color)

    def _image_pos(self, pos: QPointF) -> QPointF:
        """画布坐标 → 图片坐标（与 _frame_at_point / source_index_at 同一套换算）。"""
        zoom = self._zoom or 1.0
        return QPointF((pos.x() - self._offset.x()) / zoom,
                       (pos.y() - self._offset.y()) / zoom)

    # ---- 标线（从标尺拖出来的参考线，PS 那套手感）----

    def guides(self) -> list[list]:
        """当前文档的标线（每条 ["h"|"v", 图片坐标]）。只给画布/标尺画，不参与导出。"""
        return self._guides

    def selected_guide(self) -> list | None:
        return self._sel_guide

    def select_guide(self, guide: list | None) -> None:
        if self._sel_guide is guide:
            return
        self._sel_guide = guide
        self.canvas.update()

    def can_edit_guides(self) -> bool:
        """画布上能不能点选/拖动标线：**没有工具开着**时才接这一下。

        画笔 / 橡皮 / 吸管 / 原图选择（或抠图面板的取色）开着的时候，用户是在干活 ——
        不能让一条 1px 的参考线半路截胡这一下点击（PS 里画笔状态也是点不中参考线的）。
        """
        return (self.paint_tool is None and not self._source_tool
                and self._pick_target is None
                and self._pixmap is not None and not self._pixmap.isNull())

    def guide_at(self, pos: QPointF) -> list | None:
        """画布坐标 pos 附近（±4 屏幕像素）的标线；命中多个取最近的。"""
        zoom = max(0.001, self._zoom)
        best, best_dist = None, 5.0
        for guide in self._guides:
            screen = (self._offset.y() + guide[1] * zoom if guide[0] == "h"
                      else self._offset.x() + guide[1] * zoom)
            dist = abs(screen - (pos.y() if guide[0] == "h" else pos.x()))
            if dist <= best_dist:
                best, best_dist = guide, dist
        return best

    def begin_guide_drag(self, orientation: str, global_pos) -> bool:
        """从**标尺**上按下：立刻建一条标线并跟着鼠标走（PS 就是这么起手的）。

        上标尺往下拖 → **水平**标线（用 y 定位）；左标尺往右拖 → 竖直标线（用 x 定位）。
        没图时不建（标线没有可以量的东西）。
        """
        if self._pixmap is None or self._pixmap.isNull():
            return False
        guide = ["h" if orientation == "horizontal" else "v", 0.0]
        self._guides.append(guide)
        self._sel_guide = guide
        self._guide_drag_active = True
        self.drag_guide_to_global(global_pos)
        return True

    def begin_guide_move(self, guide: list) -> None:
        """在**画布**上按住一条已有标线：选中它并开始拖。"""
        self._sel_guide = guide
        self._guide_drag_active = True
        self.canvas.update()

    def drag_guide_to_canvas(self, pos: QPointF) -> None:
        guide = self._sel_guide
        if guide is None:
            return
        zoom = max(0.001, self._zoom)
        guide[1] = ((pos.y() - self._offset.y()) / zoom if guide[0] == "h"
                    else (pos.x() - self._offset.x()) / zoom)
        self.canvas.update()
        self._update_rulers()      # 标尺上那个"线在哪"的小标记跟着走

    def drag_guide_to_global(self, global_pos) -> None:
        """标尺那边报上来的是全局坐标（鼠标已经在画布上了，坐标要按画布换算）。"""
        self.drag_guide_to_canvas(QPointF(self.canvas.mapFromGlobal(global_pos)))

    def finish_guide_drag(self, global_pos=None, inside: bool | None = None) -> None:
        """松手：落在画布外（含"拖回标尺上"）= 删掉这条标线，落在画布里就定在这里。

        `inside` 由画布自己传（它知道鼠标在不在自己身上）；标尺那边传 global_pos 现算。
        """
        if not self._guide_drag_active:
            return
        self._guide_drag_active = False
        guide = self._sel_guide
        if guide is None:
            return
        if inside is None and global_pos is not None:
            self.drag_guide_to_global(global_pos)
            inside = self.canvas.rect().contains(self.canvas.mapFromGlobal(global_pos))
        if inside is False:
            self.remove_guide(guide)
            return
        self._clamp_guide(guide)

    def _clamp_guide(self, guide: list) -> None:
        """落定：夹进图片范围并取整（像素画工具不需要 0.37px 这种参考线）。"""
        limit = (self._pixmap.height() if guide[0] == "h" else self._pixmap.width())
        guide[1] = float(max(0, min(int(limit), int(round(guide[1])))))
        self.canvas.update()
        self._update_rulers()

    def remove_guide(self, guide: list) -> None:
        if guide in self._guides:
            self._guides.remove(guide)
        if self._sel_guide is guide:
            self._sel_guide = None
        self.canvas.update()
        self._update_rulers()

    def set_guides(self, guides) -> None:
        """整批换标线（切标签页时把这份文档的装回来）。选中项按对象身份记，必须清掉。"""
        self._guides = [["h" if g[0] == "h" else "v", float(g[1])]
                        for g in (guides or []) if len(g) >= 2]
        self._sel_guide = None
        self._guide_drag_active = False
        self.canvas.update()
        self._update_rulers()

    # ---- 一笔的生命周期：按下 → 拖动 → 松开 ----

    def begin_paint_stroke(self, pos: QPointF) -> bool:
        """按下左键：开一笔。返回是否真的开了（没图 / 不是绘制模式时为 False）。

        "抓 before 快照"不在这一步做，而是主窗口收到 ``paint_stroke_started`` 时做 ——
        撤销栈属于主窗口，查看器不碰。
        """
        tool = self.paint_tool
        if tool not in ("brush", "eraser"):
            return False
        if self._pixmap is None or self._pixmap.isNull() or not self._source_images:
            return False
        self._paint_active = True
        self._paint_tool_now = tool
        self._paint_copies.clear()
        self._paint_last = self._image_pos(pos)
        self.paint_stroke_started.emit(tool)
        # 画布上要能立刻看到笔画：合成图也换成一份**可写的工作图**再抹。
        # 用 QImage 而不是 QPixmap 的原因见 _paint_copy_of 的注释（QPixmap 会丢 alpha）。
        self._paint_display = self._to_paint_image(self._pixmap)
        self._stroke_segment(self._paint_last, self._paint_last)
        return True

    def paint_stroke_to(self, pos: QPointF) -> None:
        """拖到新位置：把上一段到这一段画出来。"""
        if not self._paint_active:
            return
        now = self._image_pos(pos)
        last = self._paint_last or now
        self._paint_last = now
        self._brush_pos = pos
        self._stroke_segment(last, now)

    def end_paint_stroke(self) -> None:
        """松开左键：把这一笔落地（重裁帧/刷预览/记撤销由主窗口在信号里做）。"""
        if not self._paint_active:
            return
        tool = self._paint_tool_now
        self._paint_active = False
        self._paint_tool_now = ""
        self._paint_last = None
        # 落地：以**这一笔的合成工作图**为准当新合成图。
        # 为什么不是"按原图重拼一遍"（原来就是这么写的，是个坑）：抠除背景只替换了
        # `_pixmap`（合成大图），`_source_images` 里还是没抠过的那些图 —— 一重拼，
        # 用户刚抠掉的白底就**原样回来了**（用户报的"用了画笔/橡皮，抠图状态又返回"）。
        # 工作图本来就是从 `_pixmap` 拷出来的，抠图结果在里面；笔画又是按原图块裁剪画的
        # （见 _stroke_segment 的 clipRect），不会溢出，所以不需要重拼。
        if self._paint_display is not None:
            self._pixmap = QPixmap.fromImage(self._paint_display)
        # 这一笔画过的每张原图，用"画完的 QImage"换掉列表里的那份 QPixmap。
        # 拖动期间只改工作副本不动列表 —— 列表里那份是撤销快照存着的"涂之前"。
        for index, image in self._paint_copies.items():
            if 0 <= index < len(self._source_images):
                self._source_images[index] = QPixmap.fromImage(image)
        self._paint_copies.clear()
        self._paint_display = None
        self.canvas.update()
        self.paint_stroke_finished.emit(tool)

    def _stroke_segment(self, start: QPointF, end: QPointF) -> None:
        """把一段直线同时画进"这一笔的合成工作图"和"碰到的每张原图工作图"。

        两处都要画：合成图那张是为了**当场看见**（不然要等松手），原图那份是为了**落地**
        （导出、重裁帧都读原图）。两份都是 QImage（见 _paint_copy_of）。
        """
        tool = self._paint_tool_now
        radius = max(1.0, self.brush_size / 2.0)
        rects = self.source_rects()
        hits = [i for i, (sx, sy, sw, sh) in enumerate(rects)
                if self._segment_hits(start, end, sx, sy, sw, sh, radius)]
        if not hits:
            return
        if self._paint_display is not None:
            painter = self._begin_painter(self._paint_display)
            if painter is not None:
                self._set_paint_pen(painter, tool)
                for i in hits:
                    sx, sy, sw, sh = rects[i]
                    painter.save()
                    painter.setClipRect(QRectF(sx, sy, sw, sh))  # 别溢到原图外的空格子里
                    painter.drawLine(start, end)
                    painter.restore()
                painter.end()
        for i in hits:
            sx, sy, sw, sh = rects[i]
            image = self._paint_copy_of(i)
            if image is None:
                continue
            p2 = self._begin_painter(image)
            if p2 is None:
                continue
            self._set_paint_pen(p2, tool)
            p2.drawLine(QPointF(start.x() - sx, start.y() - sy),
                        QPointF(end.x() - sx, end.y() - sy))
            p2.end()
        self.canvas.update()

    @staticmethod
    def _to_paint_image(pm: QPixmap) -> QImage:
        """QPixmap → 可写工作图（带 alpha 的 ARGB32_Premultiplied）。

        **必须带 alpha**：QPixmap 在全是不透明像素时会被 Qt 存成 RGB32（没有 alpha 通道，
        实测 Windows/offscreen 都一样），在这种图上用 Clear 合成模式"擦"不会擦成透明，
        而是把 RGB 抹成黑色 —— 症状就是"橡皮擦擦出黑块"。QImage 上 Clear 正常。
        """
        return pm.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    def _paint_copy_of(self, index: int) -> QImage | None:
        """第 index 张原图在这一笔里的工作副本（QImage，首次用到时创建并缓存）。

        用**深拷贝**而不是共享：撤销快照里存着 ``_source_images`` 里那份 QPixmap 的引用，
        只要拖动期间不替换列表项、也不原地改它，"撤销回涂之前"就一定成立
        （不依赖 Qt 隐式共享的 detach 时机）。
        """
        if index in self._paint_copies:
            return self._paint_copies[index]
        if not (0 <= index < len(self._source_images)):
            return None
        image = self._to_paint_image(self._source_images[index])
        self._paint_copies[index] = image
        return image

    @staticmethod
    def _begin_painter(image):
        if image is None or image.isNull():
            return None
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        return painter

    def _set_paint_pen(self, painter: QPainter, tool: str) -> None:
        """笔刷：圆头圆角（PS 默认的硬边圆刷），橡皮擦走 Clear 擦成透明。"""
        if tool == "eraser":
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            color = QColor(0, 0, 0, 0)
        else:
            color = QColor(self.paint_color)
        pen = QPen(color, float(self.brush_size))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    @staticmethod
    def _segment_hits(a: QPointF, b: QPointF, sx: int, sy: int, sw: int, sh: int,
                      radius: float) -> bool:
        """这一段（连同笔刷半径）有没有碰到这块原图。"""
        rect = QRectF(a, b).normalized().adjusted(-radius, -radius, radius, radius)
        return rect.intersects(QRectF(sx, sy, sw, sh))

    # ---- 吸管 ----

    def pick_color_at(self, pos: QPointF) -> QColor | None:
        """取图上这一点的颜色（取的是**合成图**：看到什么取什么）。

        取到的颜色把 alpha 归一到 255 —— 透明像素的 RGB 是无意义的残留值，
        直接拿去当画笔颜色会"涂了个看不见"，用户会以为画笔坏了。
        """
        if self._pixmap is None or self._pixmap.isNull():
            return None
        point = self._image_pos(pos)
        x, y = int(point.x()), int(point.y())
        if not (0 <= x < self._pixmap.width() and 0 <= y < self._pixmap.height()):
            return None
        color = QColor(self._pixmap.toImage().pixelColor(x, y))
        color.setAlpha(255)
        self.color_picked.emit(color)
        return color

    def _frame_at_point(self, pos: QPointF) -> int:
        """返回图片坐标点所在的 frame 索引，没有返回-1。"""
        if not self._pixmap:
            return -1
        for i, (fx, fy, fw, fh) in enumerate(self._frames):
            if i in self._ignored_frames:
                continue  # 被忽略的区域不可点选
            rect = QRectF(self._offset.x() + fx * self._zoom,
                          self._offset.y() + fy * self._zoom,
                          fw * self._zoom, fh * self._zoom)
            if rect.contains(pos):
                return i
        return -1

    def _frames_in_view_rect(self, rect: QRectF) -> list[int]:
        """返回与视图矩形相交的所有 frame 索引。"""
        result = []
        for i, (fx, fy, fw, fh) in enumerate(self._frames):
            if i in self._ignored_frames:
                continue  # 被忽略的区域不参与框选
            frame_rect = QRectF(self._offset.x() + fx * self._zoom,
                                 self._offset.y() + fy * self._zoom,
                                 fw * self._zoom, fh * self._zoom)
            if rect.intersects(frame_rect):
                result.append(i)
        return self._sort_reading_order(result)

    def zoom_in(self):
        self._zoom = min(MAX_ZOOM, self._zoom * 1.2)
        self.canvas.update()
        self._update_rulers()

    def zoom_out(self):
        self._zoom = max(MIN_ZOOM, self._zoom / 1.2)
        self.canvas.update()
        self._update_rulers()

    def zoom_actual_pixel(self):
        """实际像素 100%。"""
        self._zoom = 1.0
        self.canvas.update()
        self._update_rulers()

    def _on_bg_changed(self, mode: str):
        self._bg_mode = mode
        self.canvas.update()

    def _on_image_loaded(self, pm: QPixmap):
        # 由主窗口决定是首次载入还是追加，避免覆盖已有动作。
        self.image_loaded.emit(pm)

    def _on_json_selected(self, path: str):
        # 导入图片时自动检测到同目录同名 JSON，转发给主窗口导入工程。
        self.json_selected.emit(path)

    def refresh(self):
        """刷新画布和标尺。"""
        self.canvas.update()
        self._update_rulers()

    def shutdown(self) -> None:
        """窗口关闭/程序退出前收尾：停检测线程 + 卸下全局事件过滤器。

        这两件事不做会触发**原生崩溃**（access violation），且表现为随机闪退：

        1. 元素检测在线程里跑，线程的 parent 是本控件；若宿主先被析构，线程还在
           访问 ``self._image`` 并 emit 信号给已释放对象。这里先置取消标志（线程会在
           下一行边界退出），再 ``wait()`` 等它结束。
        2. ``_build_ui`` 里把自身装成了 app 级事件过滤器（还有原生 Alt 过滤器）；
           控件析构后过滤器没卸，下个事件投递就会访问已释放内存。

        幂等：重复调用无副作用。
        """
        self._detect_generation += 1            # 已有结果一律作废
        threads = list(self._detect_threads)
        for thread in threads:
            try:
                thread.cancel()
            except (RuntimeError, AttributeError):   # 已被销毁
                pass
        for thread in threads:
            try:
                thread.wait(5000)               # 检测已支持逐行取消，正常很快返回
            except RuntimeError:
                pass
        self._detect_threads.clear()
        self._detect_thread = None

        app = QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except Exception:  # noqa: BLE001
                pass
            native = getattr(self, "_alt_native_filter", None)
            if native is not None:
                try:
                    app.removeNativeEventFilter(native)
                except Exception:  # noqa: BLE001
                    pass
                self._alt_native_filter = None
