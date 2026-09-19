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
    QAbstractNativeEventFilter, QEvent, QPointF, QRectF, QSize, QThread, QTimer, Qt, pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QGridLayout, QHBoxLayout, QMenu, QPushButton, QWidget,
)

from spritesheet.settings import last_directory, set_last_directory
from .models import Action
from .widgets import RULER_SIZE, HintToast, _Ruler, make_tool_icon, paint_checkerboard


SUPPORTED_IMAGE_EXT = {".png", ".webp", ".jpeg", ".jpg", ".gif", ".bmp"}
MIN_ZOOM = 0.05
MAX_ZOOM = 20

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
        # 无动作时的提示气泡（"先添加动作"），画在画布底部
        self._toast = HintToast(self)

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
            target = QRectF(offset.x(), offset.y(),
                            self._viewer._pixmap.width() * zoom,
                            self._viewer._pixmap.height() * zoom)
            p.drawPixmap(target, self._viewer._pixmap, QRectF(self._viewer._pixmap.rect()))

            # 绘制元素辅助线（普通灰色虚线，当前动作选中变当前色，多动作色块依次排）
            if self._viewer._frames:
                current_action = self._viewer.current_action
                # 为每个动作预计算 frame_numbers / composite_representatives /
                # 复用计数（元素出现的所有帧号）。显式帧布局（复用/导入）逐帧编号，
                # 同一元素可对应多个帧号。
                _act_info = {}
                for _act in self._viewer.actions:
                    _fn = {}
                    _cr = {}
                    _fns = {}
                    _layouts = _act.frame_layouts
                    if _layouts is not None:
                        for _no, _layout in enumerate(_layouts, 1):
                            _members = [_i for _i, _, _ in _layout]
                            if len(_members) >= 2:
                                _rep = _members[0]
                                for _m in _members:
                                    _cr[_m] = _rep
                            for _m in _members:
                                _fn.setdefault(_m, _no)
                                _fns.setdefault(_m, []).append(_no)
                    else:
                        _frame_no = 1
                        _emitted_groups = set()
                        for _source_idx in _act.selected_indices:
                            _group = next(
                                (tuple(_g) for _g in _act.composite_groups
                                 if _source_idx in _g),
                                None,
                            )
                            if _group is not None:
                                if _group in _emitted_groups:
                                    continue
                                _emitted_groups.add(_group)
                                for _member in _group:
                                    _fn[_member] = _frame_no
                                    _fns.setdefault(_member, []).append(_frame_no)
                                if len(_group) >= 2:
                                    _rep = _group[0]
                                    for _member in _group:
                                        _cr[_member] = _rep
                            else:
                                _fn[_source_idx] = _frame_no
                                _fns.setdefault(_source_idx, []).append(_frame_no)
                            _frame_no += 1
                    _act_info[_act] = (_fn, _cr, _fns)
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
                                (_, _act_cr, _) = _act_info.get(act, ({}, {}, {}))
                                if i in _act_cr:
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
                        # （步进 sq+2），而一个动作可能占**多格**——该元素被复用
                        # 一次就多一个帧号。所以下一个动作必须从上一个动作画完
                        # 之后的游标位置开始，不能按动作序号预留固定格位；否则
                        # 复用帧的色块会被下一个动作的色块盖住（一格内挤两个序号）。
                        sq = 16
                        sy = rect.top()
                        cursor_x = rect.left()
                        p.setPen(QColor("#FFFFFF"))
                        p.setFont(QFont("Arial", 9, QFont.Weight.Bold))
                        for act in selected_actions:
                            # 属于多元素合成组的元素不单独画色块，由合成粗框统一绘制
                            (_, _act_cr, _) = _act_info.get(act, ({}, {}, {}))
                            if i in _act_cr:
                                continue
                            # 帧号序列：首帧号 + 每个复用帧号各一个同样的小方块，
                            # 与前一块间隔 2px（步进 sq+2）。
                            (_act_fn, _, _act_fns) = _act_info.get(act, ({}, {}, {}))
                            nos = _act_fns.get(i) or [act.selected_indices.index(i) + 1]
                            for no in nos:
                                p.fillRect(QRectF(cursor_x, sy, sq, sq), QColor(act.color))
                                p.drawText(QRectF(cursor_x, sy, sq, sq),
                                           Qt.AlignmentFlag.AlignCenter, str(no))
                                cursor_x += sq + 2

                # 合成帧粗框：所有动作的合成组都绘制（当前动作粗实线，非当前动作虚线）。
                # 前置遍历：按「未加偏移的框几何」把合成框分组并分配色块档位 slot——
                # 两个动作合成同一组元素时框的位置完全相同，若把帧号都画在框左上角
                # 就会互相遮盖，因此同框组内第 k 个动作的色块顺延 k*(sq+2)。
                _composite_draw = []
                _slots: dict = {}
                for _act in self._viewer.actions:
                    for group in _act.composite_groups:
                        if len(group) < 2:
                            continue
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
                        # 同一几何位置归为一组（整数量化，避免浮点误差把同框拆开）
                        key = (round(base_rect.x()), round(base_rect.y()),
                               round(base_rect.width()), round(base_rect.height()))
                        slot = len(_slots.setdefault(key, []))
                        _slots[key].append(_act)
                        _composite_draw.append((_act, group, pen, draw_rect, slot))

                for _act, group, pen, draw_rect, slot in _composite_draw:
                    p.setPen(pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(draw_rect)

                    # 帧号：自合成框左上角起依次画色块（同框多动作时顺延，避免遮盖）
                    (_act_fn, _, _) = _act_info.get(_act, ({}, {}, {}))
                    _frame_no = _act_fn.get(group[0])
                    if _frame_no is not None:
                        sq = 16
                        bx = draw_rect.left() + slot * (sq + 2)
                        p.fillRect(QRectF(bx, draw_rect.top(), sq, sq),
                                   QColor(_act.color))
                        p.setPen(QColor("#FFFFFF"))
                        p.setFont(QFont("Arial", 9, QFont.Weight.Bold))
                        p.drawText(
                            QRectF(bx, draw_rect.top(), sq, sq),
                            Qt.AlignmentFlag.AlignCenter,
                            str(_frame_no),
                        )

            # 绘制框选矩形
            if self._selection_rect is not None:
                pen = QPen(QColor("#E91E63"), 1.0, Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.setBrush(QColor(233, 30, 99, 30))
                p.drawRect(self._selection_rect)
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

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self._panning = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
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

    def mousePressEvent(self, event):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        # 在原图上按下（含空白、含空格平移）：通知主窗口收起右侧变换手柄
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton,
                              Qt.MouseButton.MiddleButton):
            self._viewer.canvas_pressed.emit()
        if self._panning and event.button() == Qt.MouseButton.LeftButton:
            self._pan_start = event.position() - self._viewer._offset
            return
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
        if self._panning and self._pan_start is not None:
            self._viewer._offset = event.position() - self._pan_start
            self._viewer._update_rulers()
            self.update()
            return
        if self._drag_start is not None:
            self._selection_rect = QRectF(self._drag_start, event.position()).normalized()
            self.update()

    def mouseReleaseEvent(self, event):
        if self._panning:
            self._pan_start = None
            return
        if self._drag_start is not None and self._selection_rect is not None:
            rect = self._selection_rect
            self._drag_start = None
            self._selection_rect = None
            if rect.width() < 5 and rect.height() < 5:
                # Ctrl+点击（或合成模式）将源元素加入同一张合成帧；
                # Alt+点击（或复用模式）把已选元素所属的帧再追加一帧（复用）。
                idx = self._viewer._frame_at_point(event.position())
                if self._viewer.current_action and idx >= 0:
                    ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                    alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
                    if ctrl or self._viewer.compose_mode:
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
                        for idx in indices:
                            self._viewer.add_to_composite(idx)
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
                        self._viewer.current_action.selected_indices = indices
                        self._viewer.current_action.composite_groups = []
                    self._viewer._normalize_composite_groups()
                    if indices:
                        self.selection_changed.emit()
            self.update()

    def contextMenuEvent(self, event):
        """右键菜单统一兜底：菜单构建 / 弹出 / 执行的任何 Python 异常都只记录、
        不闪退整个程序（原生层崩溃由 main.py 的 faulthandler 写入 crash.log）。"""
        try:
            self._context_menu_impl(event)
        except Exception:
            sys.stderr.write(traceback.format_exc())

    def _context_menu_impl(self, event):
        """右键菜单：根据选中数量启用/禁用项。"""
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
        hit_pos = QPointF(event.pos())
        hit_index = self._viewer._frame_at_point(hit_pos) if action else -1
        hit_selected = bool(hit_index >= 0 and action
                            and hit_index in set(action.selected_indices))

        act_actual = menu.addAction("实际像素 (100%)")
        menu.addSeparator()
        act_merge = menu.addAction("合并为一帧（拼成同一帧）")
        act_merge_one = menu.addAction("合并为一整块（碎块捏成一个元素）")
        act_ignore = menu.addAction("忽略选中区域")
        menu.addSeparator()
        act_invert = menu.addAction("反选")
        act_select_all = menu.addAction("全选")
        act_clear = menu.addAction("清空选择")
        act_remove = menu.addAction("取消选中")
        menu.addSeparator()
        act_export = menu.addAction("导出选中为单独 PNG…")

        # 禁用不适用的项
        act_merge.setEnabled(n_sel >= 2)
        act_merge_one.setEnabled(n_sel >= 2)
        act_ignore.setEnabled(n_sel >= 1)
        act_clear.setEnabled(n_sel >= 1)
        act_remove.setEnabled(hit_selected)
        act_export.setEnabled(n_frames >= 1)

        chosen = menu.exec(event.globalPos())
        if chosen is act_actual:
            self._viewer.zoom_actual_pixel()
        elif chosen is act_merge:
            self._viewer.menu_merge_composite()
        elif chosen is act_merge_one:
            self._viewer.menu_merge_single_block()
        elif chosen is act_ignore:
            self._viewer.menu_ignore_selected_regions()
        elif chosen is act_invert:
            self._viewer.menu_invert_selection()
        elif chosen is act_select_all:
            self._viewer.menu_select_all()
        elif chosen is act_clear:
            self._viewer.menu_clear_selection()
        elif chosen is act_remove:
            self._viewer.menu_remove_selected_elements(hit_index)
        elif chosen is act_export:
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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # 状态
        self._pixmap: QPixmap | None = None
        self._source_images: list[QPixmap] = []
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
        self._frames = []
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
        gap = 16
        combined = QPixmap(max(old_pixmap.width(), pm.width()), old_pixmap.height() + gap + pm.height())
        combined.fill(Qt.GlobalColor.transparent)
        painter = QPainter(combined)
        painter.drawPixmap(0, 0, old_pixmap)
        y_offset = old_pixmap.height() + gap
        painter.drawPixmap(0, y_offset, pm)
        painter.end()

        self._pixmap = combined
        self._source_images.append(pm)
        # 旧元素可立即继续显示；新增图片的元素由后台线程补充。
        self._frames = old_frames
        self._start_frame_detection(pm.toImage(), base_frames=old_frames, y_offset=y_offset)
        self.fit_view()
        self.canvas.update()
        self._update_rulers()
        QTimer.singleShot(0, self.fit_view)
        return True

    def _start_frame_detection(
        self, image: QImage, base_frames: list[tuple[int, int, int, int]], y_offset: int
    ) -> None:
        """异步检测元素，避免大图 flood fill 阻塞 UI 线程。"""
        self._detect_generation += 1
        generation = self._detect_generation
        from spritesheet.settings import detect_min_area
        thread = _FrameDetectThread(image.copy(), min_area=detect_min_area(), parent=self)
        self._detect_thread = thread
        self._detect_threads.add(thread)

        def apply_result(frames: list[tuple[int, int, int, int]]) -> None:
            if generation != self._detect_generation:
                return
            shifted = [(x, y + y_offset, w, h) for x, y, w, h in frames]
            if not base_frames:
                # 全新检测（元素索引全部重建）：旧的忽略标记无法可靠对应，清空
                self._ignored_frames.clear()
            self._frames = list(base_frames) + shifted
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
            action.composite_groups.append(self._active_composite_group)
        if idx not in self._active_composite_group:
            self._active_composite_group.append(idx)

    def _normalize_composite_groups(self) -> None:
        if not self.current_action:
            return
        selected = set(self.current_action.selected_indices)
        groups = []
        for group in self.current_action.composite_groups:
            clean = [idx for idx in group if idx in selected]
            if clean:
                groups.append(clean)
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
        action.composite_groups = []
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
        ignored = set(action.selected_indices)
        self._ignored_frames.update(ignored)
        for act in self._all_actions():
            act.selected_indices = [i for i in act.selected_indices if i not in ignored]
            act.composite_groups = [
                [i for i in g if i not in ignored] for g in act.composite_groups
            ]
            act.composite_groups = [g for g in act.composite_groups if g]
            if act.frame_layouts:
                # 布局成员（元素索引, rx, ry）：含被忽略元素的布局成员直接移除
                act.frame_layouts = [
                    [m for m in layout if m[0] not in ignored]
                    for layout in act.frame_layouts
                ]
                act.frame_layouts = [l for l in act.frame_layouts if l] or None
            self.crop_frames_for_action(act)
        self.frames_updated.emit()
        self.action_performed_full.emit("忽略选中区域")

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
        if not self._alt_reusing:
            return
        self._alt_reusing = False
        self.reuse_mode = self._manual_reuse
        if self._toolbar is not None:
            self._toolbar.btn_reuse.setChecked(self.reuse_mode)

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
            group = group_by_idx.get(idx, [idx])
            valid = [i for i in group if 0 <= i < len(self._frames)]
            if not valid:
                continue
            if len(valid) == 1:
                layouts.append([(valid[0], 0, 0)])
            else:
                lx0 = min(self._logical_xy(i)[0] for i in valid)
                ly0 = min(self._logical_xy(i)[1] for i in valid)
                layouts.append([
                    (i, self._logical_xy(i)[0] - lx0, self._logical_xy(i)[1] - ly0)
                    for i in valid
                ])
            emitted.update(valid)
        return layouts

    def _merge_added_into_layouts(self, action: Action, existing_ids: set[int]) -> None:
        """选中新增了元素时，把新元素（及其合成组）并入显式布局，保留既有复用帧。

        只处理"纯新增"（existing_ids 是删除校验后的布局内元素集）；
        新元素按其合成组整体追加为一帧，避免与已入布局的元素重复。
        """
        group_by_idx = {i: g for g in action.composite_groups for i in g}
        in_layout = set(existing_ids)
        before = len(action.frame_layouts)
        for idx in action.selected_indices:
            if idx in in_layout:
                continue
            group = group_by_idx.get(idx, [idx])
            valid = [i for i in group if 0 <= i < len(self._frames)]
            if not valid or any(i in in_layout for i in valid):
                continue
            if len(valid) == 1:
                action.frame_layouts.append([(valid[0], 0, 0)])
            else:
                lx0 = min(self._logical_xy(i)[0] for i in valid)
                ly0 = min(self._logical_xy(i)[1] for i in valid)
                action.frame_layouts.append([
                    (i, self._logical_xy(i)[0] - lx0, self._logical_xy(i)[1] - ly0)
                    for i in valid
                ])
            in_layout.update(valid)
        # 新增元素帧属于基线（不可被 Alt 取消），基线上移
        action.frame_layouts_base = getattr(action, "frame_layouts_base", 0) + (
            len(action.frame_layouts) - before
        )

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
                # 显式取消了元素选择 -> 布局不再适用，回退到常规生成
                action.frame_layouts = None
                action.frame_layouts_base = 0
            elif added:
                # 仅新增了元素 -> 把它并入布局（保留已有复用帧，不丢用户工作）
                self._merge_added_into_layouts(action, layout_ids)
            if action.frame_layouts:
                frames = []
                rects = []
                parts_all = []
                for layout in action.frame_layouts:
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
            # 用户改动了选中元素 -> 导入布局不再适用，回退到常规生成
            action.frame_layouts = None
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
