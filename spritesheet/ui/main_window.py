"""主窗口：系统标题栏 + 菜单栏 + Logo 名称栏 + 左侧边栏 + 原图区(带标尺) + 右侧空白 + 状态栏。

原图区内部：左上角方块 + 顶部水平标尺 + 左侧垂直标尺 + 画布区（浮动工具条）。
"""
from __future__ import annotations

import math
import json
import os
import re
import struct
import hashlib
from pathlib import Path

from PyQt6.QtCore import (
    QAbstractNativeEventFilter, QByteArray, QBuffer, QEvent, QPoint, QPointF, QRect, QRectF, Qt,
    QElapsedTimer, QTimer, QSize, QThread, QIODevice, pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QBrush, QColor, QFont, QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPolygonF,
    QPixmap, QTransform, QUndoCommand, QUndoStack,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication, QAbstractItemView, QAbstractSpinBox, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListView, QMessageBox, QListWidget, QListWidgetItem, QMainWindow, QPushButton, QRadioButton,
    QScrollArea, QSizePolicy, QSlider, QSpinBox, QSplitter, QStyledItemDelegate, QStyle, QProgressBar,
    QProgressDialog, QVBoxLayout, QWidget,
)

from spritesheet import __app_name__, __version__
from .models import Action, MACARON_COLORS
from .transform import (
    DEFAULT_QUAD, DEFAULT_ROTATION_PIVOT, _alpha_bounds, _normalized_rotation_pivot,
    _rotation_transform, action_anchor_pixels, action_frame_pixmap, action_frame_size,
    compute_preview_layout, frame_base_size, transform_frame_pixmap,
)

from spritesheet.ui.exporters import (
    FMT_NATIVE,
    FORMAT_CHOICES, FORMAT_SUFFIX, build_payload, write_payload,
)
from spritesheet.updater import UPDATE_URL, UpdateCheckThread, UpdateDownloadThread
from spritesheet.project_file import read_project, write_project

SUPPORTED_IMAGE_EXT = {".png", ".webp", ".jpeg", ".jpg", ".gif", ".bmp"}
MIN_ZOOM = 0.05
MAX_ZOOM = 20
RULER_SIZE = 14


def make_logo_pixmap(size: int) -> QPixmap:
    """生成软件 Logo 图标（玫红圆角方块 + 白色胶片齿孔）。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#E91E63"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(0, 0, size, size, 5, 5)
    p.setBrush(QColor(255, 255, 255, 235))
    s = size // 5
    gap = s // 2 + 1
    p.drawRect(gap, gap, s, s)
    p.drawRect(gap * 2 + s, gap, s, s)
    p.drawRect(gap, gap * 2 + s, s, s)
    p.drawRect(gap * 2 + s, gap * 2 + s, s, s)
    p.end()
    return pm


_APP_ROOT = Path(__file__).resolve().parent.parent.parent
_ICONS_DIR = _APP_ROOT / "icons"


def make_tool_icon(kind: str, size: int = 18) -> QIcon:
    """从 icons/ 目录加载 SVG 图标，支持 bg_black/bg_white 染色。"""
    path = _ICONS_DIR / f"{kind}.svg"
    if not path.exists():
        # fallback: 简单绘制
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setPen(QPen(QColor("#d8d8d8"), 1.5))
        p.drawRect(2, 2, size - 4, size - 4)
        p.end()
        return QIcon(pm)
    renderer = QSvgRenderer(str(path))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    renderer.render(p)
    p.end()
    # bg_black / bg_white 需要染色
    if kind in ("bg_black", "bg_white"):
        color = QColor("#000000") if kind == "bg_black" else QColor("#FFFFFF")
        p3 = QPainter(pm)
        p3.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        p3.fillRect(pm.rect(), color)
        p3.end()
    return QIcon(pm)


def _tick_interval(zoom: float) -> int:
    """根据缩放选择图片像素刻度间距（1,2,5,10,20,50...）。"""
    target_view_px = 60
    raw = target_view_px / max(zoom, 0.001)
    for s in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000):
        if s >= raw:
            return s
    return 5000


def _detect_frames_image(img: QImage, alpha_threshold: int = 10) -> list[tuple[int, int, int, int]]:
    """在工作线程中检测 QImage 的连通区域。"""
    if img is None or img.isNull():
        return []
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = img.width(), img.height()
    # 一次性复制连续像素，避免逐行创建 bytes 和重复 scanLine 调用。
    ptr = img.bits()
    ptr.setsize(w * h * 4)
    data = bytes(ptr)
    visited = bytearray(w * h)
    frames = []
    for sy in range(h):
        row_base = sy * w
        for sx in range(w):
            start = row_base + sx
            if visited[start] or data[start * 4 + 3] < alpha_threshold:
                visited[start] = 1
                continue
            stack = [start]
            visited[start] = 1
            min_x = max_x = sx
            min_y = max_y = sy
            while stack:
                pos = stack.pop()
                cx, cy = pos % w, pos // w
                if cx < min_x: min_x = cx
                if cx > max_x: max_x = cx
                if cy < min_y: min_y = cy
                if cy > max_y: max_y = cy
                if cx > 0:
                    ni = pos - 1
                    if not visited[ni] and data[ni * 4 + 3] >= alpha_threshold:
                        visited[ni] = 1; stack.append(ni)
                if cx + 1 < w:
                    ni = pos + 1
                    if not visited[ni] and data[ni * 4 + 3] >= alpha_threshold:
                        visited[ni] = 1; stack.append(ni)
                if cy > 0:
                    ni = pos - w
                    if not visited[ni] and data[ni * 4 + 3] >= alpha_threshold:
                        visited[ni] = 1; stack.append(ni)
                if cy + 1 < h:
                    ni = pos + w
                    if not visited[ni] and data[ni * 4 + 3] >= alpha_threshold:
                        visited[ni] = 1; stack.append(ni)
            frames.append((min_x, min_y, max_x - min_x + 1, max_y - min_y + 1))
    return frames


def detect_frames(pixmap: QPixmap, alpha_threshold: int = 10) -> list[tuple[int, int, int, int]]:
    """检测精灵图中每个独立元素的 bounding box。

    通过 alpha 通道做 4 连通 flood fill，每个非透明连通区域为一个元素。
    返回 [(x, y, w, h), ...] 列表。
    """
    if pixmap is None or pixmap.isNull():
        return []

    return _detect_frames_image(pixmap.toImage(), alpha_threshold)


class _FrameDetectThread(QThread):
    """后台检测线程；只传递 QImage，避免在线程间操作 QPixmap。"""

    detected = pyqtSignal(list)

    def __init__(self, image: QImage, parent=None) -> None:
        super().__init__(parent)
        self._image = image

    def run(self) -> None:
        self.detected.emit(_detect_frames_image(self._image))




class ColorPickerDialog(QDialog):
    """24 色高区分度深色配色选择弹窗。"""

    def __init__(self, current_color: str = "#E91E63", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择颜色")
        self.setModal(True)
        self._selected_color: str = current_color
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QGridLayout(self)
        layout.setSpacing(4)
        for i, color in enumerate(MACARON_COLORS):
            btn = QPushButton()
            btn.setFixedSize(28, 28)
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {color}; border: 2px solid #333; border-radius: 4px; }}"
                f"QPushButton:hover {{ border-color: #E91E63; }}"
            )
            btn.clicked.connect(lambda checked, c=color: self._on_pick(c))
            layout.addWidget(btn, i // 6, i % 6)

    def _on_pick(self, color: str) -> None:
        self._selected_color = color
        self.accept()

    def get_color(self) -> str:
        return self._selected_color


class ActionCardWidget(QWidget):
    """动作卡牌：可编辑名称 + 颜色圆点（点击弹出 24 色马卡龙弹窗）。"""

    color_changed = pyqtSignal(str)
    name_changed = pyqtSignal(str)
    delete_requested = pyqtSignal()
    clicked = pyqtSignal()

    def __init__(self, name: str, color: str, parent=None) -> None:
        super().__init__(parent)
        self._name = name
        self._color = color
        self._editing = False
        # 以本控件为 parent 的单次定时器：控件销毁时自动取消，
        # 避免卡片被销毁后仍回调 end_rename 导致崩溃。
        self._rename_timer = QTimer(self)
        self._rename_timer.setSingleShot(True)
        self._rename_timer.timeout.connect(self.end_rename)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        self.lbl_name = QLabel(name)
        self.lbl_name.setStyleSheet("color: #e8e8e8; font-size: 12px; background: transparent; border: none;")
        self.lbl_name.installEventFilter(self)
        layout.addWidget(self.lbl_name, 1)

        self.btn_delete = QPushButton()
        self.btn_delete.setFixedSize(18, 18)
        self.btn_delete.setIcon(make_tool_icon("delete", 14))
        self.btn_delete.setIconSize(QSize(14, 14))
        self.btn_delete.setToolTip("删除动作")
        self.btn_delete.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 3px; }"
            "QPushButton:hover { background: rgba(244, 67, 54, 0.35); }"
        )
        self.btn_delete.clicked.connect(self.delete_requested)
        layout.addWidget(self.btn_delete)

        self.btn_color = QPushButton()
        self.btn_color.setFixedSize(16, 16)
        self.btn_color.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_color.setToolTip("点击修改颜色")
        self.btn_color.clicked.connect(self._pick_color)
        layout.addWidget(self.btn_color)

        self.name_edit = QLineEdit(name)
        self.name_edit.setStyleSheet(
            "QLineEdit { background: #2b2b2b; border: 1px solid #E91E63; border-radius: 3px; color: #fff; font-size: 12px; padding: 1px 4px; }"
        )
        self.name_edit.hide()
        self.name_edit.editingFinished.connect(self._on_rename_done)
        layout.addWidget(self.name_edit, 1)

        self._apply_color_style()

    def eventFilter(self, obj, event):
        if obj is self.lbl_name and event.type() == event.Type.MouseButtonDblClick:
            self.start_rename()
            return True
        if obj is self.lbl_name and event.type() == event.Type.MouseButtonRelease:
            if not self._editing:
                self.clicked.emit()
            return True
        return super().eventFilter(obj, event)

    def start_rename(self) -> None:
        """双击后进入重命名状态：隐藏 label，显示 edit。"""
        self._editing = True
        self.lbl_name.hide()
        self.name_edit.setText(self._name)
        self.name_edit.show()
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def mouseDoubleClickEvent(self, event):
        self.start_rename()

    def _on_rename_done(self) -> None:
        """name_edit 失去焦点时自动结束编辑。"""
        if not self._editing:
            return
        new_name = self.name_edit.text().strip()
        if new_name:
            self._name = new_name
            self.lbl_name.setText(new_name)
            self.name_changed.emit(new_name)
        # 延迟到事件循环结束后执行，避免与 editingFinished 冲突。
        # 必须用「以本控件为 parent 的定时器」：若期间卡片被销毁
        # （例如导入工程会清空动作列表），定时器会随控件一并销毁，
        # 不会回调到已释放的对象（否则会造成 access violation 崩溃）。
        self._rename_timer.start(0)

    def end_rename(self) -> None:
        """如果正在编辑，结束编辑并保存。"""
        if not self._editing:
            return
        self._editing = False
        self.name_edit.hide()
        self.lbl_name.show()

    def _apply_color_style(self) -> None:
        self.btn_color.setStyleSheet(
            f"QPushButton {{ background-color: {self._color}; border: none; border-radius: 8px; }}"
            f"QPushButton:hover {{ border: 1px solid #fff; }}"
        )

    def _pick_color(self) -> None:
        dlg = ColorPickerDialog(self._color, self)
        if dlg.exec():
            self._color = dlg.get_color()
            self._apply_color_style()
            self.color_changed.emit(self._color)

    def get_name(self) -> str:
        return self._name

    def get_color(self) -> str:
        return self._color

    def set_selected(self, selected: bool) -> None:
        if selected:
            self.setStyleSheet("background-color: rgba(233, 30, 99, 0.15); border: 1px solid #E91E63; border-radius: 4px;")
        else:
            self.setStyleSheet("background-color: transparent; border: 1px solid transparent; border-radius: 4px;")


class ActionItemDelegate(QStyledItemDelegate):
    """侧边栏动作项代理：按动作颜色绘制背景、左侧色条和文字。"""

    def paint(self, painter, option, index):
        action = index.data(Qt.ItemDataRole.UserRole)
        if action is None:
            super().paint(painter, option, index)
            return
        painter.save()
        # 背景：选中 > hover > 普通，透明度渐变
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor(233, 30, 99, 40))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, QColor(233, 30, 99, 20))
        else:
            painter.fillRect(option.rect, QColor(30, 30, 30, 0))
        # 左侧颜色条（标签色）
        painter.fillRect(option.rect.left(), option.rect.top(), 3, option.rect.height(), QColor(action.color))
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(0, 34)


class _Ruler(QWidget):
    """PS 风格标尺基类。"""

    def __init__(self, viewer, orientation: str = "horizontal", parent=None) -> None:
        super().__init__(parent)
        self._viewer = viewer
        self._orientation = orientation
        if orientation == "horizontal":
            self.setFixedHeight(RULER_SIZE)
        else:
            self.setFixedWidth(RULER_SIZE)
        self.setStyleSheet("background-color: #1a1a1a; border: none;")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        # 无图片时用画布尺寸作为虚拟文档尺寸，刻度从0开始（PS 行为）
        if self._viewer._pixmap and not self._viewer._pixmap.isNull():
            doc_w = self._viewer._pixmap.width()
            doc_h = self._viewer._pixmap.height()
        else:
            doc_w = self.width()
            doc_h = self.height()
        zoom = max(0.001, self._viewer._zoom)
        offset = self._viewer._offset
        tick = _tick_interval(zoom)

        if self._orientation == "horizontal":
            p.setPen(QPen(QColor("#666"), 0.5))
            p.setFont(QFont("Arial", 7))
            first = max(0, math.floor((-offset.x()) / zoom / tick) - 1)
            last = min(math.ceil(doc_w / tick), math.ceil((self.width() - offset.x()) / zoom / tick) + 1)
            for index in range(first, last + 1):
                doc_x = index * tick
                px = offset.x() + doc_x * zoom
                if 0 <= doc_x <= doc_w and -1 <= px <= self.width() + 1:
                    p.drawLine(round(px), self.height() - 4, round(px), self.height())
                    if index % 5 == 0:
                        p.drawText(round(px) + 2, 10, str(doc_x))
        else:
            p.setPen(QPen(QColor("#666"), 0.5))
            p.setFont(QFont("Arial", 7))
            first = max(0, math.floor((-offset.y()) / zoom / tick) - 1)
            last = min(math.ceil(doc_h / tick), math.ceil((self.height() - offset.y()) / zoom / tick) + 1)
            for index in range(first, last + 1):
                doc_y = index * tick
                py = offset.y() + doc_y * zoom
                if 0 <= doc_y <= doc_h and -1 <= py <= self.height() + 1:
                    p.drawLine(self.width() - 4, round(py), self.width(), round(py))
                    if index % 5 == 0:
                        # 文字旋转 -90° 竖排，避免窄标尺中的标签重叠。
                        p.save()
                        p.translate(9, py + 4)
                        p.rotate(-90)
                        p.drawText(0, 0, str(doc_y))
                        p.restore()
        p.end()


# 画布区（绘制图片 + 鼠标交互）
class _Canvas(QWidget):
    """原图画布：背景 + 图片绘制，滚轮缩放，空格拖动，点击/拖放添加。"""

    image_loaded = pyqtSignal(QPixmap)
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

    def resizeEvent(self, event):
        toolbar = getattr(self._viewer, "_toolbar", None)
        if toolbar is not None:
            toolbar.move(self.width() - toolbar.width() - 6, 6)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # 背景
        if self._viewer._bg_mode == "checker":
            cell = 12
            for y in range(0, self.height(), cell):
                for x in range(0, self.width(), cell):
                    if (x // cell + y // cell) % 2 == 0:
                        p.fillRect(x, y, cell, cell, QColor("#2a2a2a"))
                    else:
                        p.fillRect(x, y, cell, cell, QColor("#1e1e1e"))
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
                frame_numbers = _act_info.get(current_action, ({}, {}, {}))[0] if current_action else {}
                composite_representatives = _act_info.get(current_action, ({}, {}, {}))[1] if current_action else {}
                reuse_counts = _act_info.get(current_action, ({}, {}, {}))[2] if current_action else {}
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
                        for idx, act in enumerate(selected_actions):
                            # 属于多元素合成组的元素不单独画色块，由合成粗框统一绘制
                            (_, _act_cr, _) = _act_info.get(act, ({}, {}, {}))
                            if i in _act_cr:
                                continue
                            sq = 16
                            sy = rect.top()
                            base_x = rect.left() + idx * (sq + 2)
                            # 帧号序列：首帧号 + 每个复用帧号各一个同样的小方块，
                            # 与前一块间隔 2px（步进 sq+2）。
                            (_act_fn, _, _act_fns) = _act_info.get(act, ({}, {}, {}))
                            nos = _act_fns.get(i) or [act.selected_indices.index(i) + 1]
                            p.setPen(QColor("#FFFFFF"))
                            p.setFont(QFont("Arial", 9, QFont.Weight.Bold))
                            for k, no in enumerate(nos):
                                sx = base_x + k * (sq + 2)
                                p.fillRect(QRectF(sx, sy, sq, sq), QColor(act.color))
                                p.drawText(QRectF(sx, sy, sq, sq), Qt.AlignmentFlag.AlignCenter, str(no))

                # 合成帧粗框：所有动作的合成组都绘制（当前动作粗实线，非当前动作虚线）
                for _act in self._viewer.actions:
                    _is_current = (_act is current_action)
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
                        group_rect = QRectF(
                            offset.x() + gx0 * zoom,
                            offset.y() + gy0 * zoom,
                            (gx1 - gx0) * zoom,
                            (gy1 - gy0) * zoom,
                        )
                        if _is_current:
                            pen = QPen(QColor(_act.color), 1.5, Qt.PenStyle.SolidLine)
                        else:
                            # 非当前动作：虚线，轻微偏移避免与当前动作框完全重叠
                            _act_idx = self._viewer.actions.index(_act)
                            _off = _act_idx * 2
                            pen = QPen(QColor(_act.color), 1.0, Qt.PenStyle.DashLine)
                            group_rect = group_rect.adjusted(_off, _off, _off, _off)
                        p.setPen(pen)
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.drawRect(group_rect)

                        # 帧号：在合成框左上角统一画一个色块
                        (_act_fn, _, _) = _act_info.get(_act, ({}, {}, {}))
                        _frame_no = _act_fn.get(group[0])
                        if _frame_no is not None:
                            sq = 16
                            p.fillRect(QRectF(group_rect.left(), group_rect.top(), sq, sq),
                                       QColor(_act.color))
                            p.setPen(QColor("#FFFFFF"))
                            p.setFont(QFont("Arial", 9, QFont.Weight.Bold))
                            p.drawText(
                                QRectF(group_rect.left(), group_rect.top(), sq, sq),
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
        if self._panning and event.button() == Qt.MouseButton.LeftButton:
            self._pan_start = event.position() - self._viewer._offset
            return
        if event.button() == Qt.MouseButton.LeftButton and self._viewer._pixmap:
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
                # 框选：默认替换，Ctrl 框选追加并合成为一帧；结果按阅读顺序排列。
                indices = self._viewer._frames_in_view_rect(rect)
                if self._viewer.current_action:
                    if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                        # Ctrl+框选：激活合成模式，将框选的所有元素合成为一帧
                        self._viewer.begin_ctrl_composition()
                        for idx in indices:
                            self._viewer.add_to_composite(idx)
                    elif indices:
                        self._viewer.current_action.selected_indices = indices
                        self._viewer.current_action.composite_groups = []
                    self._viewer._normalize_composite_groups()
                    if indices:
                        self.selection_changed.emit()
            self.update()

    def _open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "添加图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)"
        )
        if path:
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
                if suffix == ".sstool":
                    # Forward to MainWindow after the native drop event ends;
                    # opening a project directly from this child widget used
                    # to call a non-existent method and terminate Qt.
                    viewer = self._viewer
                    QTimer.singleShot(0, lambda p=path, v=viewer: v.project_dropped.emit(p))
                    event.acceptProposedAction()
                    return
                if suffix == ".json":
                    # JSON 要交给顶层 ImageViewer 转发（_Canvas 自身没有 json_dropped 信号，
                    # 直接访问会抛 AttributeError，而 PyQt 会让它 abort —— 也就是闪退）。
                    viewer = self._viewer
                    QTimer.singleShot(0, lambda p=path, v=viewer: v.json_dropped.emit(p))
                    event.acceptProposedAction()
                    return
                if suffix in SUPPORTED_IMAGE_EXT:
                    QTimer.singleShot(0, lambda p=path: self.image_loaded.emit(QPixmap(p)))
                    event.acceptProposedAction()
                    return
        except Exception:  # noqa: BLE001
            pass

    def _has_image(self) -> bool:
        return self._viewer._pixmap is not None and not self._viewer._pixmap.isNull()


class FloatingToolBar(QWidget):
    """画布右上角浮动工具条：放大/缩小/透明背景/黑背景/白背景。"""

    zoom_in_requested = pyqtSignal()
    zoom_out_requested = pyqtSignal()
    bg_changed = pyqtSignal(str)
    compose_toggled = pyqtSignal(bool)
    reuse_toggled = pyqtSignal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # 宽度要容纳：合成(80) + 复用(80) + 放大/缩小(24×2) + 三种背景(24×3) + 边距/间距
        self.setFixedSize(300, 32)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._bg_mode = "checker"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
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

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(30, 30, 30, 220))
        p.setPen(QPen(QColor("#444"), 1))
        p.drawRoundedRect(self.rect(), 6, 6)
        p.end()

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




def _rect_from_dict(d: dict) -> tuple[int, int, int, int]:
    """把 JSON 里的 rect 字典转成 (x, y, w, h) 元组。"""
    return (int(d.get("x", 0)), int(d.get("y", 0)),
            int(d.get("width", d.get("w", 0))), int(d.get("height", d.get("h", 0))))


def _pixmap_bytes(pm: QPixmap | None) -> bytes:
    if pm is None or pm.isNull():
        return b""
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pm.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def _pixmap_from_bytes(data: bytes) -> QPixmap:
    pm = QPixmap()
    if data:
        pm.loadFromData(data, "PNG")
    return pm


class PreviewFloatingBar(QWidget):
    """播放器右上角浮动工具条：循环模式 / 锚点准星 / 背景切换（仿原图工具条）。"""

    loop_changed = pyqtSignal(str)
    anchor_toggled = pyqtSignal(bool)
    bg_changed = pyqtSignal(str)
    zoom_in_requested = pyqtSignal()
    zoom_out_requested = pyqtSignal()

    LOOP_ICONS = {
        Action.LOOP: ("loop", "循环播放"),
        Action.ONCE: ("once", "单次播放"),
        Action.PINGPONG: ("pingpong", "往返播放"),
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(232, 32)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._loop_mode = Action.LOOP
        self._bg_mode = "checker"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        self.btn_zoom_in = self._make_button("zoom_in", "放大")
        self.btn_zoom_in.clicked.connect(self.zoom_in_requested)
        layout.addWidget(self.btn_zoom_in)

        self.btn_zoom_out = self._make_button("zoom_out", "缩小")
        self.btn_zoom_out.clicked.connect(self.zoom_out_requested)
        layout.addWidget(self.btn_zoom_out)

        self.btn_loop = self._make_button("loop", "循环模式：循环播放（点击切换）")
        self.btn_loop.clicked.connect(self._cycle_loop)
        layout.addWidget(self.btn_loop)

        self.btn_anchor = self._make_button("anchor", "锚点准星", checkable=True)
        self.btn_anchor.clicked.connect(lambda: self.anchor_toggled.emit(self.btn_anchor.isChecked()))
        layout.addWidget(self.btn_anchor)

        # 背景切换
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

    def _cycle_loop(self):
        order = [Action.LOOP, Action.ONCE, Action.PINGPONG]
        idx = order.index(self._loop_mode)
        self._loop_mode = order[(idx + 1) % len(order)]
        icon_kind, tip = self.LOOP_ICONS[self._loop_mode]
        self.btn_loop.setIcon(make_tool_icon(icon_kind, 16))
        self.btn_loop.setToolTip(f"循环模式：{tip}（点击切换）")
        self.loop_changed.emit(self._loop_mode)

    def sync_action(self, action: Action):
        """从动作同步按钮状态（不触发信号）。"""
        self._loop_mode = action.loop_mode
        icon_kind, tip = self.LOOP_ICONS[action.loop_mode]
        self.btn_loop.setIcon(make_tool_icon(icon_kind, 16))
        self.btn_loop.setToolTip(f"循环模式：{tip}（点击切换）")
        self.btn_anchor.setChecked(action.show_anchor)
        self._bg_mode = action.bg_mode
        if action.bg_mode == "checker":
            self.btn_bg_checker.setChecked(True)
        elif action.bg_mode == "black":
            self.btn_bg_black.setChecked(True)
        else:
            self.btn_bg_white.setChecked(True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(30, 30, 30, 220))
        p.setPen(QPen(QColor("#444"), 1))
        p.drawRoundedRect(self.rect(), 6, 6)
        p.end()

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
        return btn


class PreviewCanvas(QWidget):
    """动作预览画布：支持滚轮缩放、空格拖动，移动模式下背景滚动。"""

    MIN_ZOOM = 0.1
    MAX_ZOOM = 8.0

    # ---- 自由变换交互信号 ----
    # 进入/退出变换模式（'' 表示退出）；由帧设置按钮联动
    transform_mode_changed = pyqtSignal(str)
    # 请求把当前帧旋转设为 angle；由帧设置面板传播（数据复用）
    transform_rotated = pyqtSignal(int)
    # 请求把当前帧 quad 设为归一化 8 元组（或 None 清除）
    transform_quadded = pyqtSignal(object)
    # 请求把当前帧旋转中心设为归一化 (x, y)（或 None 清除）
    transform_pivot_moved = pyqtSignal(object)
    # 一次鼠标拖动的边界，用于把连续 mouseMove 合并为一个撤销步骤。
    transform_edit_started = pyqtSignal()
    transform_edit_finished = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(200)
        self.setStyleSheet("background-color: #141414; border: 1px solid #4a4a4a; border-radius: 4px;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._action: Action | None = None
        self._frame_index: int = 0
        # 缩放与拖动
        self._zoom = 1.0
        self._offset = QPointF(0, 0)
        self._fit = True  # 初始自适应
        self._panning = False
        self._pan_start = None
        self._toolbar = None
        # 移动模式背景滚动
        self._bg_scroll = 0.0
        self._bg_texture: QPixmap | None = None
        self._bg_texture_key: tuple[str, int] | None = None
        self._scroll_clock = QElapsedTimer()
        self._playing = False
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._scroll_timer.timeout.connect(self._scroll_background)
        # ---- 自由变换交互状态 ----
        self._transform_mode: str | None = None  # None/rotate/scale/skew/perspective/distort
        self._drag_kind: str | None = None       # None/corner/edge/rotate/pivot
        self._drag_index: int = -1
        self._drag_layout: tuple | None = None   # 拖动期间锁定的布局（避免帧跳动）
        self._drag_mo = QPointF(0, 0)            # 拖动开始时 quad 包围盒最小角（快照）
        self._drag_wrot: int = 0
        self._drag_hrot: int = 0
        self._drag_orig: tuple | None = None     # 拖动开始时 quad 快照（归一化 8 元组）
        self._drag_orig_c: list[QPointF] = []    # 拖动开始时 4 角画布逻辑坐标
        self._drag_center = QPointF(0, 0)        # 拖动开始时手柄中心（画布逻辑）
        self._drag_rot_start = 0                 # 拖动开始时旋转角度
        self._drag_rot_mouse_start = 0.0
        self._drag_pivot_canvas = QPointF(0, 0)
        # Hover feedback deliberately uses a tiny pulse rather than a layout
        # animation, so the control frame never changes size while painting.
        self._hover_kind: str | None = None
        self._hover_index: int = -1
        self._hover_phase = 0.0
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(45)
        self._hover_timer.timeout.connect(self._advance_handle_hover)
        self._hover_timer.start()

    def set_toolbar(self, toolbar):
        self._toolbar = toolbar
        self._reposition_toolbar()

    def resizeEvent(self, event):
        if self._fit:
            self._apply_fit()
        self._reposition_toolbar()

    def _reposition_toolbar(self):
        if self._toolbar:
            self._toolbar.move(self.width() - self._toolbar.width() - 6, 6)

    def set_action(self, action: Action | None):
        self._action = action
        self._frame_index = 0
        self._hover_kind, self._hover_index = None, -1
        self._direction = 1
        self._bg_scroll = 0.0
        self._playing = False
        self._fit = True
        self._apply_fit()
        self._update_scroll_timer()
        if action is None:
            self.setMinimumHeight(100)
            self.setMaximumHeight(16777215)
        self.update()

    def set_frame(self, idx: int):
        if self._action and 0 <= idx < len(self._action.frames):
            self._frame_index = idx
            self._hover_kind, self._hover_index = None, -1
            self.update()

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        if playing:
            self._scroll_clock.restart()
        else:
            self._scroll_clock.invalidate()
        self._update_scroll_timer()

    def _update_scroll_timer(self):
        if self._playing and self._action and self._action.action_type == "move":
            self._scroll_timer.start(8)
        else:
            self._scroll_timer.stop()
            if not self._action or self._action.action_type != "move":
                self._bg_scroll = 0.0

    def _scroll_background(self):
        if self._playing and self._action and self._action.action_type == "move":
            elapsed_ms = self._scroll_clock.restart() if self._scroll_clock.isValid() else 16
            speed = self._action.move_speed * 9.0 * elapsed_ms / 1000.0
            # Keep the logical offset unbounded so elapsed-time updates never
            # jump when crossing an arbitrary wrap boundary.  The painter
            # applies the direction and texture-period modulo only when rendering.
            self._bg_scroll += speed
            self.update()

    def _draw_moving_background(self, painter: QPainter, rect: QRectF, scale: float) -> None:
        """绘制随动作方向和速度平移的平铺背景纹理。

        使用一张可缓存的 2x2 纹理交给 Qt 平铺，避免每个定时器 tick
        都逐格填充整个预览区，低缩放时尤其能减少卡顿。
        """
        if not self._action:
            return

        if self._action.bg_mode == "checker":
            colors = (QColor("#2a2a2a"), QColor("#1e1e1e"))
        elif self._action.bg_mode == "black":
            colors = (QColor("#0a0a0a"), QColor("#303030"))
        else:
            colors = (QColor("#ffffff"), QColor("#cfcfcf"))

        scale = max(0.1, scale)
        tile_px = max(2, int(round(48.0 * scale)))
        texture_key = (self._action.bg_mode, tile_px)
        if self._bg_texture is None or self._bg_texture_key != texture_key:
            texture = QPixmap(tile_px * 2, tile_px * 2)
            texture.fill(colors[0])
            tp = QPainter(texture)
            tp.fillRect(0, 0, tile_px, tile_px, colors[0])
            tp.fillRect(tile_px, tile_px, tile_px, tile_px, colors[0])
            tp.fillRect(tile_px, 0, tile_px, tile_px, colors[1])
            tp.fillRect(0, tile_px, tile_px, tile_px, colors[1])
            tp.end()
            self._bg_texture = texture
            self._bg_texture_key = texture_key

        tile = float(tile_px)
        # Use the actual rasterized texture period.  This keeps non-integer
        # zoom levels from accumulating a fractional-period seam at wrap.
        phase = (self._bg_scroll * scale) % (tile * 2.0)
        direction = self._action.move_direction
        shift_x = 0.0
        shift_y = 0.0
        # The preview represents the camera/background response, so it moves
        # opposite to the action direction: down -> background up, left ->
        # background right, and so on.
        if direction == "left":
            shift_x = phase
        elif direction == "right":
            shift_x = -phase
        elif direction == "up":
            shift_y = phase
        elif direction == "down":
            shift_y = -phase
        # QPainter's tile source offset moves the visible texture in the
        # opposite direction, so negate the camera shift here.
        painter.drawTiledPixmap(rect, self._bg_texture, QPointF(-shift_x, -shift_y))

    def _apply_fit(self):
        if not self._action or not self._action.frames:
            return
        canvas_w, canvas_h, _, _ = compute_preview_layout(self._action, self._frame_index)
        if canvas_w <= 0 or canvas_h <= 0:
            return
        zw = self.width() / canvas_w
        zh = self.height() / canvas_h
        self._zoom = min(zw, zh) * 0.9
        self._offset = QPointF(
            (self.width() - canvas_w * self._zoom) / 2,
            (self.height() - canvas_h * self._zoom) / 2,
        )

    def zoom_in(self):
        self._zoom_to(self._zoom * 1.2)

    def zoom_out(self):
        self._zoom_to(self._zoom / 1.2)

    def _zoom_to(self, new_zoom):
        self._zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, new_zoom))
        self._fit = False
        self.update()

    def wheelEvent(self, event):
        if not self._action or not self._action.frames:
            return
        delta = event.angleDelta().y()
        factor = 1.1 if delta > 0 else 1 / 1.1
        new_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self._zoom * factor))
        mouse_pos = event.position()
        old_zoom = self._zoom
        self._offset = QPointF(
            mouse_pos.x() - (mouse_pos.x() - self._offset.x()) * (new_zoom / old_zoom),
            mouse_pos.y() - (mouse_pos.y() - self._offset.y()) * (new_zoom / old_zoom),
        )
        self._zoom = new_zoom
        self._fit = False
        self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self._panning = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif (event.key() == Qt.Key.Key_T
              and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            # PS-style shortcut: Ctrl+T enters the default free-transform mode.
            self.set_transform_mode("scale")
            event.accept()
        elif event.key() == Qt.Key.Key_Escape and self._transform_mode:
            self.set_transform_mode(None)
            event.accept()

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)

    # ==================== 自由变换交互 ====================

    def set_transform_mode(self, mode: str | None) -> None:
        """进入/退出变换模式（'rotate'/'scale'/'skew'/'perspective'/'distort'/''/None）。"""
        mode = mode or None
        if mode is not None and mode == self._transform_mode:
            mode = None  # 再点同按钮 = 退出
        self._transform_mode = mode
        self._drag_kind = None
        self._drag_layout = None
        self._hover_kind, self._hover_index = None, -1
        self.setCursor(Qt.CursorShape.CrossCursor if self._transform_mode else Qt.CursorShape.ArrowCursor)
        self.update()
        self.transform_mode_changed.emit(self._transform_mode or "")

    def transform_mode(self) -> str | None:
        return self._transform_mode

    def _current_layout(self):
        if not self._action or not self._action.frames:
            return None
        return compute_preview_layout(self._action, self._frame_index)

    def _quad_dst(self, quad):
        """归一化 quad → (相对旋转后帧原点的 4 角像素点, 基准宽, 基准高)。"""
        w, h = frame_base_size(self._action, self._frame_index)
        return ([QPointF(quad[0] * w, quad[1] * h), QPointF(quad[2] * w, quad[3] * h),
                 QPointF(quad[4] * w, quad[5] * h), QPointF(quad[6] * w, quad[7] * h)], w, h)

    def _rotation_pivot_output(self) -> QPointF:
        """旋转中心在旋转后基础图像中的坐标（未应用 quad）。"""
        if not self._action or not self._action.frames:
            return QPointF(0, 0)
        src = self._action.frames[self._frame_index]
        # Mirroring keeps the raster size; the pivot is defined in that space.
        src_w, src_h = src.width(), src.height()
        pivot = self._action.frame_rotation_pivots.get(
            self._frame_index, DEFAULT_ROTATION_PIVOT
        )
        matrix, pivot_px, min_pt = _rotation_transform(
            src_w, src_h, self._action.frame_rotations.get(self._frame_index, 0), pivot
        )
        mapped = matrix.map(pivot_px)
        rotated = src
        if self._frame_index in self._action.frame_mirror_h:
            rotated = rotated.transformed(QTransform().scale(-1.0, 1.0), Qt.TransformationMode.FastTransformation)
        if self._frame_index in self._action.frame_mirror_v:
            rotated = rotated.transformed(QTransform().scale(1.0, -1.0), Qt.TransformationMode.FastTransformation)
        if self._action.frame_rotations.get(self._frame_index, 0) % 360:
            rotated = rotated.transformed(matrix, Qt.TransformationMode.FastTransformation)
        content = _alpha_bounds(rotated)
        trim_x = content.x() if content else 0
        trim_y = content.y() if content else 0
        return QPointF(mapped.x() - min_pt.x() - trim_x,
                       mapped.y() - min_pt.y() - trim_y)

    def _rotation_pivot_canvas(self, layout=None, mo=None) -> QPointF:
        if not self._action or not self._action.frames:
            return QPointF(0, 0)
        layout = layout or self._current_layout()
        if layout is None:
            return QPointF(0, 0)
        _, _, positions, _ = layout
        tx, ty, _, _ = positions[self._frame_index]
        quad = self._action.frame_quads.get(self._frame_index, DEFAULT_QUAD)
        dst, w, h = self._quad_dst(quad)
        mo = mo if mo is not None else QPointF(min(p.x() for p in dst), min(p.y() for p in dst))
        point = self._rotation_pivot_output()
        if tuple(quad) != DEFAULT_QUAD:
            src_poly = QPolygonF([QPointF(0, 0), QPointF(w, 0), QPointF(w, h), QPointF(0, h)])
            dst_poly = QPolygonF(dst)
            matrix = QTransform()
            if QTransform.quadToQuad(src_poly, dst_poly, matrix):
                point = matrix.map(point)
        return QPointF(tx + point.x() - mo.x(), ty + point.y() - mo.y())

    def _current_mo(self) -> QPointF:
        """当前 quad 四角的最小角（pixmap 内容贴左上的平移量）。"""
        quad = self._action.frame_quads.get(self._frame_index, DEFAULT_QUAD)
        dst, _, _ = self._quad_dst(quad)
        return QPointF(min(p.x() for p in dst), min(p.y() for p in dst))

    def _handles_canvas(self, mo: QPointF, layout=None, anchor_override=None) -> list[QPointF]:
        """quad 四角 → 画布逻辑坐标（layout 传 None 用当前布局，或传拖动锁定的快照）。"""
        if layout is None:
            layout = self._current_layout()
        if layout is None:
            return []
        _, _, positions, _ = layout
        tx, ty, _, _ = positions[self._frame_index]
        quad = self._action.frame_quads.get(self._frame_index, DEFAULT_QUAD)
        dst, _, _ = self._quad_dst(quad)
        points = [QPointF(tx + p.x() - mo.x(), ty + p.y() - mo.y()) for p in dst]
        if anchor_override is not None:
            current = self._rotation_pivot_canvas(layout, mo)
            dx = anchor_override.x() - current.x()
            dy = anchor_override.y() - current.y()
            points = [QPointF(p.x() + dx, p.y() + dy) for p in points]
        return points

    def _logical_to_screen(self, pt: QPointF) -> QPointF:
        return QPointF(self._offset.x() + pt.x() * self._zoom,
                       self._offset.y() + pt.y() * self._zoom)

    def _screen_to_logical(self, pt: QPointF) -> QPointF:
        return QPointF((pt.x() - self._offset.x()) / self._zoom,
                       (pt.y() - self._offset.y()) / self._zoom)

    def _rotate_handle_screen(self, canvas_pts, pivot_canvas=None) -> QPointF | None:
        """旋转手柄屏幕坐标，位于以旋转中心为圆心的圆环顶部。"""
        if not canvas_pts:
            return None
        center = pivot_canvas or QPointF(
            sum(p.x() for p in canvas_pts) / 4.0,
            sum(p.y() for p in canvas_pts) / 4.0,
        )
        center_s = self._logical_to_screen(center)
        radius = max(math.hypot((p.x() - center.x()) * self._zoom,
                                (p.y() - center.y()) * self._zoom) for p in canvas_pts) + 28.0
        return QPointF(center_s.x(), center_s.y() - max(28.0, radius))

    def _rotation_circle(self, canvas_pts, pivot_canvas=None) -> tuple[QPointF, float, QPointF] | None:
        if not canvas_pts:
            return None
        center = pivot_canvas or QPointF(
            sum(p.x() for p in canvas_pts) / 4.0,
            sum(p.y() for p in canvas_pts) / 4.0,
        )
        center_s = self._logical_to_screen(center)
        radius = max(math.hypot((p.x() - center.x()) * self._zoom,
                                (p.y() - center.y()) * self._zoom) for p in canvas_pts) + 28.0
        radius = max(28.0, radius)
        return center_s, radius, QPointF(center_s.x(), center_s.y() - radius)

    def _hit_test(self, screen_pos):
        """返回 (kind, index)，只让当前模式的玫红控件可命中。"""
        if not self._transform_mode or not self._action or not self._action.frames:
            return None, -1
        layout = self._drag_layout if self._drag_kind else None
        mo = self._drag_mo if self._drag_kind else self._current_mo()
        pts = self._handles_canvas(mo, layout=layout)
        if not pts:
            return None, -1
        screens = [self._logical_to_screen(p) for p in pts]
        mode = self._transform_mode
        if mode == "rotate":
            pivot = self._rotation_pivot_canvas(layout, mo)
            pivot_s = self._logical_to_screen(pivot)
            if (pivot_s - screen_pos).manhattanLength() < 15:
                return "pivot", -1
            rot = self._rotate_handle_screen(pts, pivot)
            if rot is not None and (rot - screen_pos).manhattanLength() < 15:
                return "rotate", -1
            return None, -1
        if mode in ("scale", "perspective", "distort"):
            for i, s in enumerate(screens):
                if (s - screen_pos).manhattanLength() < 15:
                    return "corner", i
        elif mode == "skew":
            for i in range(4):
                mid = (screens[i] + screens[(i + 1) % 4]) / 2.0
                if (mid - screen_pos).manhattanLength() < 15:
                    return "edge", i
        return None, -1

    def _begin_drag(self, kind: str, index: int, screen_pos: QPointF) -> None:
        self.transform_edit_started.emit()
        self._drag_kind = kind
        self._drag_index = index
        self._drag_layout = self._current_layout()
        quad = self._action.frame_quads.get(self._frame_index, DEFAULT_QUAD)
        self._drag_orig = tuple(quad)
        dst, w, h = self._quad_dst(quad)
        self._drag_wrot, self._drag_hrot = w, h
        mo = QPointF(min(p.x() for p in dst), min(p.y() for p in dst))
        self._drag_mo = mo
        _, _, positions, _ = self._drag_layout
        tx, ty, _, _ = positions[self._frame_index]
        self._drag_orig_c = [QPointF(tx + p.x() - mo.x(), ty + p.y() - mo.y()) for p in dst]
        self._drag_center = QPointF(
            sum(p.x() for p in self._drag_orig_c) / 4.0,
            sum(p.y() for p in self._drag_orig_c) / 4.0,
        )
        self._drag_rot_start = self._action.frame_rotations.get(self._frame_index, 0)
        self._drag_pivot_canvas = self._rotation_pivot_canvas(self._drag_layout, self._drag_mo)
        if kind == "rotate":
            start = self._screen_to_logical(screen_pos)
            self._drag_rot_mouse_start = math.degrees(math.atan2(
                start.y() - self._drag_pivot_canvas.y(),
                start.x() - self._drag_pivot_canvas.x(),
            ))

    def _emit_pivot_from_canvas(self, canvas_point: QPointF) -> None:
        """Convert a displayed pivot point back to pre-rotation normalized space."""
        if not self._action or self._drag_layout is None:
            return
        _, _, positions, _ = self._drag_layout
        tx, ty, _, _ = positions[self._frame_index]
        quad = self._action.frame_quads.get(self._frame_index, DEFAULT_QUAD)
        dst, w, h = self._quad_dst(quad)
        mo = self._drag_mo
        point = QPointF(canvas_point.x() - tx + mo.x(), canvas_point.y() - ty + mo.y())
        if tuple(quad) != DEFAULT_QUAD:
            src_poly = QPolygonF([QPointF(0, 0), QPointF(w, 0), QPointF(w, h), QPointF(0, h)])
            matrix = QTransform()
            if not QTransform.quadToQuad(src_poly, QPolygonF(dst), matrix) or not matrix.isInvertible():
                return
            point = matrix.inverted()[0].map(point)
        src = self._action.frames[self._frame_index]
        angle = self._action.frame_rotations.get(self._frame_index, 0)
        current = self._action.frame_rotation_pivots.get(self._frame_index, DEFAULT_ROTATION_PIVOT)
        matrix, _, min_pt = _rotation_transform(src.width(), src.height(), angle, current)
        rotated = src
        if self._frame_index in self._action.frame_mirror_h:
            rotated = rotated.transformed(QTransform().scale(-1.0, 1.0), Qt.TransformationMode.FastTransformation)
        if self._frame_index in self._action.frame_mirror_v:
            rotated = rotated.transformed(QTransform().scale(1.0, -1.0), Qt.TransformationMode.FastTransformation)
        if angle % 360:
            rotated = rotated.transformed(matrix, Qt.TransformationMode.FastTransformation)
        content = _alpha_bounds(rotated)
        trim_x = content.x() if content else 0
        trim_y = content.y() if content else 0
        point = QPointF(point.x() + min_pt.x() + trim_x,
                        point.y() + min_pt.y() + trim_y)
        if matrix.isInvertible():
            point = matrix.inverted()[0].map(point)
        pivot = (max(-2.0, min(3.0, point.x() / max(1, src.width()))),
                 max(-2.0, min(3.0, point.y() / max(1, src.height()))))
        self.transform_pivot_moved.emit(tuple(round(v, 6) for v in pivot))

    def _emit_quad_from_canvas(self, canvas_pts) -> None:
        """把 4 个画布逻辑角点换算成归一化 quad 并发出信号。"""
        w, h = self._drag_wrot, self._drag_hrot
        mo = self._drag_mo
        _, _, positions, _ = self._drag_layout
        tx, ty, _, _ = positions[self._frame_index]
        quad = []
        for p in canvas_pts:
            dstx = p.x() - tx + mo.x()
            dsty = p.y() - ty + mo.y()
            quad.append(dstx / w if w else 0.0)
            quad.append(dsty / h if h else 0.0)
        self.transform_quadded.emit(tuple(round(v, 6) for v in quad))

    def _update_drag(self, screen_pos: QPointF) -> None:
        mode = self._transform_mode
        if mode == "rotate":
            cur = self._screen_to_logical(screen_pos)
            if self._drag_kind == "pivot":
                self._drag_pivot_canvas = cur
                self._emit_pivot_from_canvas(cur)
                return
            dx = cur.x() - self._drag_pivot_canvas.x()
            dy = cur.y() - self._drag_pivot_canvas.y()
            if dx or dy:
                current_mouse_angle = math.degrees(math.atan2(dy, dx))
                delta = (current_mouse_angle - self._drag_rot_mouse_start + 180.0) % 360.0 - 180.0
                self.transform_rotated.emit((self._drag_rot_start + int(round(delta))) % 360)
            return
        cur = self._screen_to_logical(screen_pos)
        kind = self._drag_kind
        orig_c = self._drag_orig_c
        if kind == "corner":
            k = self._drag_index
            if mode == "distort":
                new = [QPointF(p.x(), p.y()) for p in orig_c]
                new[k] = cur
            elif mode == "perspective":
                new = [QPointF(p.x(), p.y()) for p in orig_c]
                dx = cur.x() - orig_c[k].x()
                dy = cur.y() - orig_c[k].y()
                # 相邻两角联动：边 (k,k+1) 水平、边 (k-1,k) 垂直（对边保持不动 → 透视感）
                new[k].setX(orig_c[k].x() + dx)
                new[(k + 1) % 4].setX(orig_c[(k + 1) % 4].x() + dx)
                new[k].setY(orig_c[k].y() + dy)
                new[(k - 1) % 4].setY(orig_c[(k - 1) % 4].y() + dy)
            elif mode == "scale":
                anchor = orig_c[(k + 2) % 4]
                den_x = orig_c[k].x() - anchor.x()
                den_y = orig_c[k].y() - anchor.y()
                if abs(den_x) < 1e-6 or abs(den_y) < 1e-6:
                    return
                rx = (cur.x() - anchor.x()) / den_x
                ry = (cur.y() - anchor.y()) / den_y
                new = [QPointF(anchor.x() + (p.x() - anchor.x()) * rx,
                               anchor.y() + (p.y() - anchor.y()) * ry) for p in orig_c]
            else:
                return
            self._emit_quad_from_canvas(new)
        elif kind == "edge" and mode == "skew":
            k = self._drag_index
            new = [QPointF(p.x(), p.y()) for p in orig_c]
            if k in (0, 2):  # 顶/底边：水平斜切
                dx = cur.x() - (orig_c[k].x() + orig_c[(k + 1) % 4].x()) / 2.0
                new[k].setX(orig_c[k].x() + dx)
                new[(k + 1) % 4].setX(orig_c[(k + 1) % 4].x() + dx)
            else:            # 左/右边：垂直斜切
                dy = cur.y() - (orig_c[k].y() + orig_c[(k + 1) % 4].y()) / 2.0
                new[k].setY(orig_c[k].y() + dy)
                new[(k + 1) % 4].setY(orig_c[(k + 1) % 4].y() + dy)
            self._emit_quad_from_canvas(new)

    def _end_drag(self) -> None:
        had_drag = self._drag_kind is not None
        self._drag_kind = None
        self._drag_layout = None
        if had_drag:
            self.transform_edit_finished.emit()
        self.update()

    def _advance_handle_hover(self) -> None:
        if self._hover_kind is not None and self._transform_mode:
            self._hover_phase = (self._hover_phase + 0.32) % (math.pi * 2.0)
            self.update()

    def _update_hover(self, screen_pos: QPointF) -> None:
        kind, index = self._hit_test(screen_pos)
        if (kind, index) != (self._hover_kind, self._hover_index):
            self._hover_kind, self._hover_index = kind, index
            self._hover_phase = 0.0
            self.update()

    def leaveEvent(self, event):
        self._hover_kind, self._hover_index = None, -1
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if self._panning and event.button() == Qt.MouseButton.LeftButton:
            self._pan_start = event.position() - self._offset
            return
        if self._transform_mode and event.button() == Qt.MouseButton.LeftButton:
            kind, idx = self._hit_test(event.position())
            if kind is None:
                self.set_transform_mode(None)  # 点空白退出
            else:
                self._begin_drag(kind, idx, event.position())

    def mouseMoveEvent(self, event):
        if self._panning and self._pan_start is not None:
            self._offset = event.position() - self._pan_start
            self.update()
            return
        if self._drag_kind and self._transform_mode:
            self._update_drag(event.position())
        elif self._transform_mode:
            self._update_hover(event.position())

    def mouseReleaseEvent(self, event):
        if self._panning:
            self._pan_start = None
        if self._drag_kind:
            self._end_drag()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 整widget填深色
        p.fillRect(self.rect(), QColor("#141414"))

        if not self._action or not self._action.frames:
            p.setPen(QColor("#666"))
            p.setFont(QFont("Microsoft YaHei", 11))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "选择动作以预览")
            p.end()
            return

        canvas_w, canvas_h, positions, (pad, max_w, max_h) = compute_preview_layout(
            self._action, self._frame_index
        )
        # 缩放居中（支持用户缩放和拖动）
        scale = self._zoom
        cx = self._offset.x()
        cy = self._offset.y()
        canvas_rect = QRectF(cx, cy, canvas_w * scale, canvas_h * scale)

        # 背景填满整个播放窗口，内部画布只负责边框、帧和辅助线。
        background_rect = self.rect()
        background_scale = max(0.1, self._zoom)
        if self._action.action_type == "move":
            self._draw_moving_background(p, QRectF(background_rect), background_scale)
        elif self._action.bg_mode == "checker":
            cell = max(4, int(12 * background_scale))
            for y in range(0, background_rect.height(), cell):
                for x in range(0, background_rect.width(), cell):
                    if ((x // cell) + (y // cell)) % 2 == 0:
                        p.fillRect(x, y, cell, cell, QColor("#2a2a2a"))
                    else:
                        p.fillRect(x, y, cell, cell, QColor("#1e1e1e"))
        elif self._action.bg_mode == "black":
            p.fillRect(background_rect, QColor("#000000"))
        else:
            p.fillRect(background_rect, QColor("#FFFFFF"))

        # 统一画布（安全区）边框：灰色虚线
        pen = QPen(QColor("#888"), 1.0, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawRect(canvas_rect)

        # 参照帧（洋葱皮，半透明）
        if 0 <= self._action.ref_frame < len(self._action.frames) and self._action.ref_frame != self._frame_index:
            ref_tx, ref_ty, ref_w, ref_h = positions[self._action.ref_frame]
            ref_rect = QRectF(cx + ref_tx * scale, cy + ref_ty * scale, ref_w * scale, ref_h * scale)
            p.setOpacity(self._action.ref_opacity)
            ref_pm = action_frame_pixmap(self._action, self._action.ref_frame)
            p.drawPixmap(ref_rect, ref_pm, QRectF(ref_pm.rect()))
            p.setOpacity(1.0)

        # 当前帧（始终显示一帧，居中显示）
        if self._transform_mode and self._drag_layout is not None:
            # 拖动期间锁定布局位置，并用 quad 包围盒最小角变化补偿，避免帧跳动
            _, _, pos_drag, _ = self._drag_layout
            tx, ty, _, _ = pos_drag[self._frame_index]
            mo0 = self._drag_mo
            mo_new = self._current_mo()
            offx = mo_new.x() - mo0.x()
            offy = mo_new.y() - mo0.y()
            fw, fh = action_frame_size(self._action, self._frame_index)
            frame_rect = QRectF(cx + (tx + offx) * scale, cy + (ty + offy) * scale, fw * scale, fh * scale)
        else:
            tx, ty, fw, fh = positions[self._frame_index]
            frame_rect = QRectF(cx + tx * scale, cy + ty * scale, fw * scale, fh * scale)
        p.setOpacity(self._action.frame_opacity)
        frame_pm = action_frame_pixmap(self._action, self._frame_index)
        p.drawPixmap(frame_rect, frame_pm, QRectF(frame_pm.rect()))
        p.setOpacity(1.0)

        # 模拟地面线：默认在内容区最底部（不考虑四周扩展），向上偏移为正
        if self._action.ground_enabled:
            gy = cy + (pad + max_h - self._action.ground_offset) * scale
            p.setPen(QPen(QColor("#E91E63"), 1.5, Qt.PenStyle.DashLine))
            p.drawLine(int(canvas_rect.left()), int(gy), int(canvas_rect.right()), int(gy))

        # 锚点准星
        if self._action.show_anchor:
            anchor_x, anchor_y = action_anchor_pixels(self._action, canvas_w, canvas_h)
            ax = cx + anchor_x * scale
            ay = cy + anchor_y * scale
            p.setPen(QPen(QColor("#4FC3F7"), 1.5))
            p.drawLine(int(ax - 8), int(ay), int(ax + 8), int(ay))
            p.drawLine(int(ax), int(ay - 8), int(ax), int(ay + 8))
            p.drawEllipse(QPointF(ax, ay), 4, 4)

        # 自由变换手柄（PS 式控制框：四角/边点/旋转手柄）
        if self._transform_mode:
            self._draw_transform_handles(p)

        p.end()

    def _draw_transform_handles(self, p: QPainter) -> None:
        pts = self._handles_canvas(
            self._drag_mo if self._drag_kind else self._current_mo(),
            layout=self._drag_layout if self._drag_kind else None,
            anchor_override=self._drag_pivot_canvas if self._drag_kind and self._transform_mode == "rotate" else None,
        )
        if not pts:
            return
        screens = [self._logical_to_screen(pt) for pt in pts]
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        mode = self._transform_mode
        # 四边形边框保持低对比，避免把不可控点误认为可拖拽控件。
        p.setPen(QPen(QColor(120, 190, 205, 190), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        path.moveTo(screens[0])
        for s in screens[1:]:
            path.lineTo(s)
        path.closeSubpath()
        p.drawPath(path)
        def draw_square(center: QPointF, controllable: bool, kind: str, index: int, size: float) -> None:
            hovered = self._hover_kind == kind and self._hover_index == index
            pulse = 1.0 + (0.16 * (0.5 + 0.5 * math.sin(self._hover_phase)) if hovered else 0.0)
            half = size * pulse / 2.0
            if controllable:
                p.setPen(QPen(QColor("#FF80AB" if hovered else "#E91E63"), 1.2))
                p.setBrush(QColor("#FF4081" if hovered else "#E91E63"))
            else:
                # 空心/透明中心：明确表示该点在当前模式下不可控。
                p.setPen(QPen(QColor(233, 30, 99, 115), 1.0))
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(center.x() - half, center.y() - half, half * 2, half * 2))
            if hovered and controllable:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(255, 128, 171, 170), 1.0))
                p.drawEllipse(center, half + 4.0, half + 4.0)

        corner_control = mode in ("scale", "perspective", "distort")
        edge_control = mode == "skew"
        for i, s in enumerate(screens):
            draw_square(s, corner_control, "corner", i, 9.0)
        for i in range(4):
            mid = (screens[i] + screens[(i + 1) % 4]) / 2.0
            draw_square(mid, edge_control, "edge", i, 8.0)

        if mode == "rotate":
            pivot_canvas = self._rotation_pivot_canvas(
                self._drag_layout if self._drag_kind else None,
                self._drag_mo if self._drag_kind else None,
            )
            if self._drag_kind == "pivot":
                pivot_canvas = self._drag_pivot_canvas
            circle = self._rotation_circle(pts, pivot_canvas)
            if circle is not None:
                center_s, radius, rot = circle
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(233, 30, 99, 210), 1.5))
                p.drawEllipse(center_s, radius, radius)
                p.setPen(QPen(QColor(233, 30, 99, 155), 1.0, Qt.PenStyle.DashLine))
                p.drawLine(center_s, rot)
                # 独立旋转中心：圆形十字，和动作锚点使用不同颜色。
                pivot_hover = self._hover_kind == "pivot"
                pulse = 1.0 + (0.18 * (0.5 + 0.5 * math.sin(self._hover_phase)) if pivot_hover else 0.0)
                p.setPen(QPen(QColor("#FF80AB" if pivot_hover else "#E91E63"), 1.6))
                p.setBrush(QColor("#FF4081" if pivot_hover else "#E91E63"))
                p.drawEllipse(center_s, 5.0 * pulse, 5.0 * pulse)
                p.setPen(QPen(QColor("#FFB0C8" if pivot_hover else "#F06292"), 1.2))
                p.drawLine(center_s + QPointF(-9, 0), center_s + QPointF(9, 0))
                p.drawLine(center_s + QPointF(0, -9), center_s + QPointF(0, 9))
                handle_hover = self._hover_kind == "rotate"
                handle_r = 6.0 * (1.16 if handle_hover else 1.0)
                p.setBrush(QColor("#FF4081" if handle_hover else "#E91E63"))
                p.setPen(QPen(QColor("#FFB0C8" if handle_hover else "#F06292"), 1.2))
                p.drawEllipse(rot, handle_r, handle_r)


_SLIDER_QSS = (
    # 控件本体透明：否则会带上全局 QWidget 的底色，与所在面板不一致
    "QSlider { background: transparent; border: none; }"
    # 水平：细轨道 + 玫红圆形手柄（保持原有外观，勿加 border，否则会渲染成方形）
    "QSlider::groove:horizontal { height: 2px; background: #3d3d3d; border-radius: 1px; }"
    "QSlider::handle:horizontal { width: 8px; height: 8px; margin: -3px 0;"
    " background: #E91E63; border-radius: 4px; }"
    "QSlider::handle:horizontal:hover { background: #F06292; }"
    # 垂直：与水平完全同款（圆点手柄，尺寸一致）
    "QSlider::groove:vertical { width: 2px; background: #3d3d3d; border-radius: 1px; }"
    "QSlider::handle:vertical { width: 8px; height: 8px; margin: 0 -3px;"
    " background: #E91E63; border-radius: 4px; }"
    "QSlider::handle:vertical:hover { background: #F06292; }"
)

_GROUP_QSS = (
    "QGroupBox { background-color: #1f1f1f; border: 1px solid #333333; border-radius: 5px; "
    "margin-top: 10px; padding: 8px 6px 6px 6px; font-size: 11px; color: #cfcfcf;}"
    "QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 8px; "
    "padding: 0 4px; color: #F06292;}"
    "QLabel { color: #bdbdbd; font-size: 11px; border: none; background: transparent; }"
    "QPushButton { background-color: #2b2b2b; border: 1px solid #3d3d3d; border-radius: 3px; "
    "color: #e8e8e8; padding: 3px 8px; }"
    "QPushButton:hover { background-color: #383838; border-color: #F06292; }"
    "QPushButton:checked { background-color: #E91E63; border-color: #E91E63; color: #ffffff; }"
    "QSpinBox, QComboBox { background-color: #2b2b2b; border: 1px solid #3d3d3d; border-radius: 3px; "
    "color: #e8e8e8; font-size: 11px; padding: 1px 4px; }"
    "QSpinBox::up-button, QSpinBox::down-button { width: 16px; background-color: #353535; border: none; }"
    "QSpinBox::up-button:hover, QSpinBox::down-button:hover { background-color: #E91E63; }"
    "QSpinBox::up-arrow { image: none; border-left: 4px solid transparent; border-right: 4px solid transparent; border-bottom: 5px solid #e8e8e8; }"
    "QSpinBox::down-arrow { image: none; border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 5px solid #e8e8e8; }"
    "QComboBox::drop-down { width: 20px; background-color: #353535; border: none; }"
    "QComboBox::drop-down:hover { background-color: #E91E63; }"
    "QComboBox::down-arrow { image: none; border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 5px solid #e8e8e8; }"
    "QComboBox QAbstractItemView { background-color: #2b2b2b; color: #e8e8e8; border: 1px solid #E91E63; "
    "selection-background-color: #E91E63; selection-color: #ffffff; outline: none; }"
    "QCheckBox { color: #bdbdbd; font-size: 11px; spacing: 5px; }"
    "QCheckBox::indicator { width: 13px; height: 13px; border-radius: 3px; border: 1px solid #3d3d3d; background: #2b2b2b; }"
    "QCheckBox::indicator:checked { background: #E91E63; border-color: #E91E63; }"
)


class CompactComboBox(QComboBox):
    """深色下拉框：覆盖系统原生白色箭头区域。"""

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        arrow_rect = QRect(self.width() - 20, 1, 19, max(1, self.height() - 2))
        painter.fillRect(arrow_rect, QColor("#353535"))
        painter.setPen(QPen(QColor("#4a4a4a"), 1))
        painter.drawLine(arrow_rect.left(), arrow_rect.top(), arrow_rect.left(), arrow_rect.bottom())
        cx = arrow_rect.center().x()
        cy = arrow_rect.center().y()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#d7dbe0"))
        painter.drawPolygon(QPolygonF([
            QPointF(cx - 4, cy - 2),
            QPointF(cx + 4, cy - 2),
            QPointF(cx, cy + 3),
        ]))
        painter.end()


class EditableNumberLabel(QLineEdit):
    """只显示数字的文本；双击后可直接输入，回车或失焦确认。

    外观完全像静态文字（无边框），因此面板看起来干净；
    需要修改时双击即可编辑，不需要常驻的上下调节箭头。
    """

    value_edited = pyqtSignal(int)

    def __init__(self, value: int = 0, parent=None) -> None:
        super().__init__(parent)
        self._value = int(value)
        self.setReadOnly(True)
        self.setFrame(False)
        self.setFixedWidth(52)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setToolTip("双击可直接输入数值")
        self.setStyleSheet(
            "QLineEdit { background: transparent; border: 1px solid transparent; border-radius: 3px;"
            " color: #e8e8e8; font-size: 11px; padding: 1px 2px; }"
            "QLineEdit:hover { border-color: #3d3d3d; }"
            "QLineEdit[editing=\"true\"] { background: #2b2b2b; border-color: #E91E63; }"
        )
        self.setText(str(self._value))
        self.editingFinished.connect(self._commit)

    def value(self) -> int:
        return self._value

    def set_value(self, value: int) -> None:
        """外部同步数值（编辑中不覆盖用户输入）。"""
        self._value = int(value)
        if self.isReadOnly():
            self.setText(str(self._value))

    def begin_edit(self) -> None:
        if not self.isReadOnly():
            return
        self.setReadOnly(False)
        self.setProperty("editing", "true")
        self.style().unpolish(self)
        self.style().polish(self)
        self.selectAll()
        self.setFocus(Qt.FocusReason.MouseFocusReason)

    def mouseDoubleClickEvent(self, event):
        self.begin_edit()
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._commit()
            self.clearFocus()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.setText(str(self._value))
            self.setReadOnly(True)
            self._unset_editing()
            self.clearFocus()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        if not self.isReadOnly():
            self._commit()
        super().focusOutEvent(event)

    def _unset_editing(self) -> None:
        self.setProperty("editing", "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def _commit(self) -> None:
        if self.isReadOnly():
            return
        text = self.text().strip()
        try:
            value = int(text)
        except ValueError:
            value = self._value          # 非法输入：还原原值
        self._value = value
        self.setText(str(value))
        self.setReadOnly(True)
        self._unset_editing()
        self.value_edited.emit(value)


class CompactSpinBox(QSpinBox):
    """深色数字框：覆盖系统原生白色上下按钮区域。"""

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        button_rect = QRect(self.width() - 18, 1, 17, max(1, self.height() - 2))
        painter.fillRect(button_rect, QColor("#353535"))
        painter.setPen(QPen(QColor("#4a4a4a"), 1))
        painter.drawLine(button_rect.left(), button_rect.top(), button_rect.left(), button_rect.bottom())
        cx = button_rect.center().x()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#d7dbe0"))
        painter.drawPolygon(QPolygonF([
            QPointF(cx - 4, self.height() * 0.34),
            QPointF(cx + 4, self.height() * 0.34),
            QPointF(cx, self.height() * 0.18),
        ]))
        painter.drawPolygon(QPolygonF([
            QPointF(cx - 4, self.height() * 0.66),
            QPointF(cx + 4, self.height() * 0.66),
            QPointF(cx, self.height() * 0.82),
        ]))
        painter.end()


class AnimAdjustPanel(QWidget):
    """动画调节组：统一画布/帧速度、对齐与地面、当前帧微调与参照帧、动作类型。"""

    changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._action: Action | None = None
        self._actions: list = []
        self._transform_changed = False
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet(_GROUP_QSS)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ---- 动作类型 ----
        g_type = QGroupBox("动作类型")
        type_layout = QVBoxLayout(g_type)
        type_layout.setContentsMargins(6, 12, 6, 6)
        type_layout.setSpacing(4)
        self.btn_type_static = QPushButton("静止")
        self.btn_type_static.setCheckable(True)
        self.btn_type_static.setChecked(True)
        self.btn_type_move = QPushButton("移动")
        self.btn_type_move.setCheckable(True)
        self._type_group = QButtonGroup(self)
        self._type_group.addButton(self.btn_type_static)
        self._type_group.addButton(self.btn_type_move)
        self.btn_type_static.clicked.connect(self._on_type_changed)
        self.btn_type_move.clicked.connect(self._on_type_changed)
        type_buttons_row = QHBoxLayout()
        type_buttons_row.setContentsMargins(0, 0, 0, 0)
        type_buttons_row.setSpacing(4)
        type_buttons_row.addWidget(self.btn_type_static)
        type_buttons_row.addWidget(self.btn_type_move)
        type_layout.addLayout(type_buttons_row)

        # 移动方向
        self.dir_widget = QWidget()
        dir_layout = QHBoxLayout(self.dir_widget)
        dir_layout.setContentsMargins(0, 0, 0, 0)
        dir_layout.setSpacing(2)
        dir_layout.addWidget(QLabel("方向"))
        self._dir_group = QButtonGroup(self)
        self.btn_dir_up = self._small_btn("", "向上移动", "arrow_up")
        self.btn_dir_down = self._small_btn("", "向下移动", "arrow_down")
        self.btn_dir_left = self._small_btn("", "向左移动", "arrow_left")
        self.btn_dir_right = self._small_btn("", "向右移动", "arrow_right")
        for btn in (self.btn_dir_up, self.btn_dir_down, self.btn_dir_left, self.btn_dir_right):
            btn.setCheckable(True)
            self._dir_group.addButton(btn)
            dir_layout.addWidget(btn)
        self.btn_dir_right.setChecked(True)
        self.btn_dir_up.clicked.connect(lambda: self._set_direction("up"))
        self.btn_dir_down.clicked.connect(lambda: self._set_direction("down"))
        self.btn_dir_left.clicked.connect(lambda: self._set_direction("left"))
        self.btn_dir_right.clicked.connect(lambda: self._set_direction("right"))
        type_layout.addWidget(self.dir_widget)

        # 移动速度
        self.speed_widget = QWidget()
        speed_layout = QHBoxLayout(self.speed_widget)
        speed_layout.setContentsMargins(0, 0, 0, 0)
        speed_layout.setSpacing(4)
        self.lbl_speed = QLabel("速度")
        self.slider_speed = QSlider(Qt.Orientation.Horizontal)
        self.slider_speed.setRange(1, 100)
        self.slider_speed.setValue(10)
        self.slider_speed.setStyleSheet(_SLIDER_QSS)
        self.slider_speed.valueChanged.connect(self._on_speed_changed)
        self.lbl_speed_value = QLabel("10")
        self.lbl_speed_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_speed_value.setMinimumWidth(28)
        self.slider_speed.valueChanged.connect(lambda value: self.lbl_speed_value.setText(str(value)))
        speed_layout.addWidget(self.lbl_speed)
        speed_layout.addWidget(self.slider_speed, 1)
        speed_layout.addWidget(self.lbl_speed_value)
        type_layout.addWidget(self.speed_widget)

        self.dir_widget.hide()
        self.speed_widget.hide()
        layout.addWidget(g_type)

        # ---- 帧设置 ----
        g1 = QGroupBox("帧设置")
        f1 = QFormLayout(g1)
        f1.setContentsMargins(6, 12, 6, 6)
        f1.setSpacing(4)
        self.spin_padding = CompactSpinBox()
        self.spin_padding.setRange(0, 200)
        self.spin_padding.setValue(0)
        self.spin_padding.valueChanged.connect(self._on_padding_changed)
        f1.addRow("四周扩展", self.spin_padding)

        fps_row = QHBoxLayout()
        fps_row.setContentsMargins(0, 0, 0, 0)
        fps_row.setSpacing(6)
        self.slider_fps = QSlider(Qt.Orientation.Horizontal)
        self.slider_fps.setRange(1, 60)
        self.slider_fps.setValue(10)
        self.slider_fps.setStyleSheet(_SLIDER_QSS)
        self.slider_fps.valueChanged.connect(self._on_fps_changed)
        self.lbl_fps = QLabel("10")
        self.lbl_fps.setFixedWidth(24)
        self.slider_fps.valueChanged.connect(lambda v: self.lbl_fps.setText(str(v)))
        fps_row.addWidget(self.slider_fps, 1)
        fps_row.addWidget(self.lbl_fps)
        fps_w = QWidget()
        fps_w.setLayout(fps_row)
        f1.addRow("帧速度", fps_w)

        self.lbl_canvas = QLabel("-")
        self.lbl_canvas.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_canvas.setMinimumWidth(72)
        self.chk_reuse = QCheckBox("数据复用")
        self.chk_reuse.setChecked(True)
        self.chk_reuse.stateChanged.connect(self._on_reuse_toggled)
        # 用显式透明容器：QFormLayout.addRow(布局) 会自动包一层 QWidget，
        # 那层容器会带上全局 QWidget 的底色，与面板背景不一致。
        canvas_wrap = QWidget()
        canvas_wrap.setStyleSheet("background: transparent;")
        canvas_row = QHBoxLayout(canvas_wrap)
        canvas_row.setContentsMargins(0, 0, 0, 0)
        canvas_row.setSpacing(4)
        canvas_row.addWidget(self.chk_reuse)
        canvas_row.addStretch(1)
        canvas_row.addWidget(QLabel("画布大小"))
        canvas_row.addWidget(self.lbl_canvas)
        f1.addRow(canvas_wrap)

        # ---- 自由变换（每帧独立；勾选"数据复用"时整组帧一起改）----
        # 变换模式按钮：点击后直接在预览上拖手柄调整（PS 式自由变换）
        trans_wrap = QWidget()
        trans_wrap.setStyleSheet("background: transparent;")
        trans_v = QVBoxLayout(trans_wrap)
        trans_v.setContentsMargins(0, 0, 0, 0)
        trans_v.setSpacing(5)
        transform_grid = QGridLayout()
        transform_grid.setContentsMargins(0, 0, 0, 0)
        transform_grid.setHorizontalSpacing(5)
        transform_grid.setVerticalSpacing(5)
        self.btn_mode_rotate = self._small_btn("旋转", "旋转：点击后在预览上拖圆形旋转手柄或旋转中心", "rotate")
        self.btn_mode_scale = self._small_btn("缩放", "缩放：点击后在预览上拖角点（对角固定）", "scale")
        self.btn_mode_skew = self._small_btn("斜切", "斜切：点击后在预览上拖边点", "skew")
        self.btn_mode_perspective = self._small_btn("透视", "透视：点击后在预览上拖角点（对边联动）", "perspective")
        self.btn_mode_distort = self._small_btn("扭曲", "扭曲：点击后在预览上拖角点（单角自由）", "distort")
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        mode_buttons = (self.btn_mode_rotate, self.btn_mode_scale, self.btn_mode_skew,
                        self.btn_mode_perspective, self.btn_mode_distort)
        for row, b in enumerate(mode_buttons):
            b.setCheckable(True)
            b.setFixedHeight(30)
            b.setMinimumWidth(112)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._mode_group.addButton(b)
            transform_grid.addWidget(b, row, 0)
        self.btn_mode_rotate.toggled.connect(lambda on: self._on_mode_button("rotate", on))
        self.btn_mode_scale.toggled.connect(lambda on: self._on_mode_button("scale", on))
        self.btn_mode_skew.toggled.connect(lambda on: self._on_mode_button("skew", on))
        self.btn_mode_perspective.toggled.connect(lambda on: self._on_mode_button("perspective", on))
        self.btn_mode_distort.toggled.connect(lambda on: self._on_mode_button("distort", on))
        trans_v.addLayout(transform_grid)
        # 镜像按钮 + 只读变换摘要（旋转角度只能由预览圆形手柄修改）
        self.btn_mirror_h = self._small_btn("水平镜像", "水平镜像（左右翻转）", "mirror_h")
        self.btn_mirror_v = self._small_btn("垂直镜像", "垂直镜像（上下翻转）", "mirror_v")
        self.btn_mirror_h.setFixedHeight(30)
        self.btn_mirror_v.setFixedHeight(30)
        self.btn_mirror_h.setCheckable(True)
        self.btn_mirror_v.setCheckable(True)
        self.btn_mirror_h.clicked.connect(lambda: self._toggle_mirror("h"))
        self.btn_mirror_v.clicked.connect(lambda: self._toggle_mirror("v"))
        self.lbl_transform_summary = QLabel("当前变换：无变换")
        self.lbl_transform_summary.setMinimumHeight(28)
        self.lbl_transform_summary.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_transform_summary.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.lbl_transform_summary.setStyleSheet(
            "QLabel { color: #bdbdbd; background: #252525; border: 1px solid #3d3d3d; "
            "border-radius: 3px; padding: 0 7px; }"
        )
        self.lbl_transform_summary.setToolTip("当前帧的全部变换状态；旋转角度只读")
        mirror_row = QHBoxLayout()
        mirror_row.setContentsMargins(0, 0, 0, 0)
        mirror_row.setSpacing(5)
        mirror_row.addWidget(self.btn_mirror_h, 1)
        mirror_row.addWidget(self.btn_mirror_v, 1)
        mirror_row.addWidget(self.lbl_transform_summary, 2)
        transform_grid.addLayout(mirror_row, len(mode_buttons), 0)
        transform_grid.setColumnStretch(0, 1)
        f1.addRow("变换", trans_wrap)

        layout.addWidget(g1)

        # ---- 地面与锚点 ----
        g2 = QGroupBox("地面与锚点")
        g2_layout = QVBoxLayout(g2)
        g2_layout.setContentsMargins(6, 12, 6, 6)
        g2_layout.setSpacing(4)
        self.chk_ground = QCheckBox("显示地面线")
        self.chk_ground.setChecked(False)
        self.chk_ground.stateChanged.connect(self._on_ground_toggled)

        ground_row = QHBoxLayout()
        ground_row.setSpacing(4)
        ground_row.addWidget(self.chk_ground)
        ground_row.addWidget(QLabel("微调"))
        self.btn_ground_up = self._small_btn("", "地面线上移1px", "arrow_up")
        self.btn_ground_down = self._small_btn("", "地面线下移1px", "arrow_down")
        self.lbl_ground_offset = QLabel("0")
        self.lbl_ground_offset.setMinimumWidth(28)
        self.btn_ground_up.clicked.connect(lambda: self._adjust_ground(1))
        self.btn_ground_down.clicked.connect(lambda: self._adjust_ground(-1))
        ground_row.addWidget(self.btn_ground_up)
        ground_row.addWidget(self.btn_ground_down)
        ground_row.addWidget(self.lbl_ground_offset)
        ground_row.addStretch()
        g2_layout.addLayout(ground_row)
        layout.addWidget(g2)

        # ---- 当前帧微调 / 参照 ----
        g3 = QGroupBox("当前帧微调 / 参照")
        g3_layout = QVBoxLayout(g3)
        g3_layout.setContentsMargins(6, 12, 6, 6)
        g3_layout.setSpacing(4)

        offset_header = QHBoxLayout()
        offset_header.setSpacing(4)
        offset_header.addWidget(QLabel("帧偏移"))
        # 只显示数字，双击可直接输入（无上下调节箭头）
        self.spin_off_x = EditableNumberLabel(0)
        self.spin_off_x.setToolTip("当前帧水平偏移：双击可直接输入")
        self.spin_off_x.value_edited.connect(self._on_offset_label_changed)
        self.spin_off_y = EditableNumberLabel(0)
        self.spin_off_y.setToolTip("当前帧垂直偏移：双击可直接输入")
        self.spin_off_y.value_edited.connect(self._on_offset_label_changed)
        offset_header.addWidget(self.spin_off_x)
        offset_header.addWidget(self.spin_off_y)
        offset_header.addStretch()
        g3_layout.addLayout(offset_header)

        self.btn_up = self._small_btn("", "当前帧上移1px", "arrow_up")
        self.btn_down = self._small_btn("", "当前帧下移1px", "arrow_down")
        self.btn_left = self._small_btn("", "当前帧左移1px", "arrow_left")
        self.btn_right = self._small_btn("", "当前帧右移1px", "arrow_right")
        self.btn_up.clicked.connect(lambda: self._adjust_frame(0, -1))
        self.btn_down.clicked.connect(lambda: self._adjust_frame(0, 1))
        self.btn_left.clicked.connect(lambda: self._adjust_frame(-1, 0))
        self.btn_right.clicked.connect(lambda: self._adjust_frame(1, 0))
        self.btn_reset = self._small_btn("", "重置当前帧偏移", "reset")
        self.btn_reset.clicked.connect(self._reset_frame_offset)

        # 水平滑块（下方）与垂直滑块（右侧）：用于大幅调节偏移
        self.slider_off_h = QSlider(Qt.Orientation.Horizontal)
        self.slider_off_h.setRange(-200, 200)
        self.slider_off_h.setValue(0)
        self.slider_off_h.setFixedHeight(20)
        self.slider_off_h.setStyleSheet(_SLIDER_QSS)
        self.slider_off_h.setToolTip("大幅调节水平偏移（-200 ~ +200）")
        self.slider_off_h.valueChanged.connect(self._on_offset_slider_h)
        self.slider_off_v = QSlider(Qt.Orientation.Vertical)
        self.slider_off_v.setRange(-200, 200)
        self.slider_off_v.setValue(0)
        self.slider_off_v.setFixedWidth(20)
        self.slider_off_v.setStyleSheet(_SLIDER_QSS)
        self.slider_off_v.setToolTip("大幅调节垂直偏移（-200 ~ +200，向上为负）")
        self.slider_off_v.valueChanged.connect(self._on_offset_slider_v)

        offset_grid = QGridLayout()
        offset_grid.setContentsMargins(0, 0, 0, 0)
        offset_grid.setSpacing(2)
        offset_grid.addWidget(self.btn_up, 0, 1)
        offset_grid.addWidget(self.btn_left, 1, 0)
        offset_grid.addWidget(self.btn_reset, 1, 1)
        offset_grid.addWidget(self.btn_right, 1, 2)
        offset_grid.addWidget(self.btn_down, 2, 1)
        # 右侧：垂直滑块（跨 3 行）——调 Y
        offset_grid.addWidget(self.slider_off_v, 0, 3, 3, 1)
        # 下方：水平滑块（跨 3 列）——调 X
        offset_grid.addWidget(self.slider_off_h, 3, 0, 1, 3)
        offset_grid.setColumnStretch(0, 1)
        offset_grid.setColumnStretch(2, 1)
        g3_layout.addLayout(offset_grid)

        ref_row = QFormLayout()
        ref_row.setContentsMargins(0, 4, 0, 0)
        ref_row.setSpacing(4)
        self.combo_ref = CompactComboBox()
        self.combo_ref.currentIndexChanged.connect(self._on_ref_changed)
        ref_row.addRow("参照帧", self.combo_ref)

        # 同样用透明容器，避免 QFormLayout 包装层带来色差
        op_wrap = QWidget()
        op_wrap.setStyleSheet("background: transparent;")
        op_row = QHBoxLayout(op_wrap)
        op_row.setContentsMargins(0, 0, 0, 0)
        op_row.setSpacing(4)
        self.slider_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_opacity.setRange(10, 100)
        self.slider_opacity.setValue(100)
        self.slider_opacity.setStyleSheet(_SLIDER_QSS)
        self.slider_opacity.valueChanged.connect(self._on_opacity_changed)
        op_row.addWidget(self.slider_opacity, 1)
        ref_row.addRow("当前帧透明度", op_wrap)
        g3_layout.addLayout(ref_row)
        layout.addWidget(g3)

        layout.addStretch()

    def _small_btn(self, text: str, tip: str, icon_kind: str | None = None) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedSize(24, 24)
        btn.setToolTip(tip)
        if icon_kind:
            btn.setIcon(make_tool_icon(icon_kind, 14))
            btn.setIconSize(QSize(14, 14))
        btn.setStyleSheet(
            "QPushButton { background: #2b2b2b; border: 1px solid #3d3d3d; border-radius: 3px; color: #e8e8e8; font-size: 11px; }"
            "QPushButton:hover { background: #3a3a3a; border-color: #E91E63; }"
            "QPushButton:checked { background: #E91E63; border-color: #E91E63; }"
        )
        return btn

    def set_action(self, action: Action | None):
        self._action = action
        if action is None:
            self.lbl_canvas.setText("-")
            self._sync_offset_widgets(0, 0)
            self.lbl_ground_offset.setText("0")
            self.combo_ref.clear()
            self._sync_transform_widgets()
            return
        self.spin_padding.blockSignals(True)
        self.spin_padding.setValue(action.padding)
        self.spin_padding.blockSignals(False)
        self.slider_fps.blockSignals(True)
        self.slider_fps.setValue(action.fps)
        self.lbl_fps.setText(str(action.fps))
        self.slider_fps.blockSignals(False)
        self.chk_ground.blockSignals(True)
        self.chk_ground.setChecked(action.ground_enabled)
        self.chk_ground.blockSignals(False)
        self.chk_reuse.blockSignals(True)
        self.chk_reuse.setChecked(action.reuse_frame_settings)
        self.chk_reuse.blockSignals(False)
        self.lbl_ground_offset.setText(str(action.ground_offset))
        # 动作类型
        if action.action_type == "move":
            self.btn_type_move.setChecked(True)
            self.dir_widget.show()
            self.speed_widget.show()
        else:
            self.btn_type_static.setChecked(True)
            self.dir_widget.hide()
            self.speed_widget.hide()
        # 方向
        dir_map = {"up": self.btn_dir_up, "down": self.btn_dir_down,
                   "left": self.btn_dir_left, "right": self.btn_dir_right}
        dir_map.get(action.move_direction, self.btn_dir_right).setChecked(True)
        # 速度
        self.slider_speed.blockSignals(True)
        self.slider_speed.setValue(action.move_speed)
        self.slider_speed.blockSignals(False)
        self.lbl_speed_value.setText(str(action.move_speed))
        # 参照帧下拉
        self.combo_ref.blockSignals(True)
        self.combo_ref.clear()
        self.combo_ref.addItem("无", -1)
        for i in range(len(action.frames)):
            self.combo_ref.addItem(f"帧{i + 1}", i)
        idx = self.combo_ref.findData(action.ref_frame)
        if idx >= 0:
            self.combo_ref.setCurrentIndex(idx)
        self.combo_ref.blockSignals(False)
        # 透明度
        self.slider_opacity.blockSignals(True)
        self.slider_opacity.setValue(int(action.frame_opacity * 100))
        self.slider_opacity.blockSignals(False)
        self._sync_transform_widgets()
        self._refresh_canvas_label()

    def _refresh_canvas_label(self):
        if self._action and self._action.frames:
            canvas_w, canvas_h, _, _ = compute_preview_layout(self._action, 0)
            self.lbl_canvas.setText(f"{canvas_w} × {canvas_h}")
            idx = getattr(self, "_current_frame", 0)
            dx, dy = self._action.frame_offsets.get(idx, (0, 0))
            self._sync_offset_widgets(dx, dy)
        else:
            self.lbl_canvas.setText("-")
            self._sync_offset_widgets(0, 0)

    def _sync_offset_widgets(self, dx: int, dy: int) -> None:
        """把当前帧偏移同步到数字文本与滑块（阻塞信号，避免递归触发）。"""
        self.spin_off_x.set_value(dx)
        self.spin_off_y.set_value(dy)
        for w, v in ((self.slider_off_h, dx), (self.slider_off_v, dy)):
            w.blockSignals(True)
            # 超出滑块范围时滑块钳制显示，文本始终显示真值
            w.setValue(max(w.minimum(), min(w.maximum(), v)))
            w.blockSignals(False)

    def _set_current_offset(self, dx: int | None = None, dy: int | None = None) -> None:
        """设置当前帧偏移的某个分量并刷新界面。"""
        if not self._action or not self._action.frames:
            return
        idx = getattr(self, "_current_frame", 0)
        if not (0 <= idx < len(self._action.frames)):
            return
        cur = self._action.frame_offsets.get(idx, (0, 0))
        nx = cur[0] if dx is None else int(dx)
        ny = cur[1] if dy is None else int(dy)
        if (nx, ny) == cur:
            return
        if nx == 0 and ny == 0:
            self._action.frame_offsets.pop(idx, None)
        else:
            self._action.frame_offsets[idx] = (nx, ny)
        self._sync_offset_widgets(nx, ny)
        self._refresh_canvas_label()   # 边缘并集随微调实时变化
        self.changed.emit()

    def _on_offset_label_changed(self, _value: int) -> None:
        self._set_current_offset(self.spin_off_x.value(), self.spin_off_y.value())

    def _on_offset_slider_h(self, value: int) -> None:
        self._set_current_offset(dx=value)

    def _on_offset_slider_v(self, value: int) -> None:
        self._set_current_offset(dy=value)

    def _on_type_changed(self):
        if not self._action:
            return
        if self.btn_type_move.isChecked():
            self._action.action_type = "move"
            self.dir_widget.show()
            self.speed_widget.show()
        else:
            self._action.action_type = "static"
            self.dir_widget.hide()
            self.speed_widget.hide()
        self.changed.emit()

    def _set_direction(self, direction: str):
        if self._action:
            self._action.move_direction = direction
            self.changed.emit()

    def _on_speed_changed(self, val: int):
        if self._action:
            self._action.move_speed = val
            self.changed.emit()

    def _on_padding_changed(self, val: int):
        if self._action:
            self._action.padding = val
            self._refresh_canvas_label()
            self.changed.emit()

    def _on_fps_changed(self, val: int):
        if self._action:
            self._action.fps = val
            self.changed.emit()

    def _on_reuse_toggled(self, state):
        if self._action:
            self._action.reuse_frame_settings = state == 2
            self.changed.emit()

    def _on_ground_toggled(self, state):
        if self._action:
            self._action.ground_enabled = state == 2
            self.changed.emit()

    def _adjust_ground(self, delta: int):
        """地面线上下调整：delta>0 上移，delta<0 下移。"""
        if self._action:
            self._action.ground_offset += delta
            self.lbl_ground_offset.setText(str(self._action.ground_offset))
            self.changed.emit()

    def _adjust_frame(self, dx: int, dy: int):
        """方向按钮：当前帧偏移微调 1px。"""
        if not self._action or not self._action.frames:
            return
        idx = getattr(self, "_current_frame", 0)
        if 0 <= idx < len(self._action.frames):
            cur = self._action.frame_offsets.get(idx, (0, 0))
            self._set_current_offset(cur[0] + dx, cur[1] + dy)

    def _reset_frame_offset(self):
        if not self._action:
            return
        idx = getattr(self, "_current_frame", 0)
        if idx in self._action.frame_offsets:
            del self._action.frame_offsets[idx]
        self._sync_offset_widgets(0, 0)
        self._refresh_canvas_label()
        self.changed.emit()

    def set_current_frame(self, idx: int):
        self._current_frame = idx
        self._sync_transform_widgets()

    def set_actions(self, actions: list):
        """提供动作列表引用，用于"数据复用"跨动作传播旋转/镜像。"""
        self._actions = actions

    def _sync_transform_widgets(self) -> None:
        """同步镜像按钮和只读变换摘要（阻塞信号避免递归）。"""
        if not self._action or not self._action.frames:
            self.lbl_transform_summary.setText("当前变换：无变换")
            for b in (self.btn_mirror_h, self.btn_mirror_v):
                b.blockSignals(True)
                b.setChecked(False)
                b.blockSignals(False)
            return
        idx = getattr(self, "_current_frame", 0)
        self.btn_mirror_h.blockSignals(True)
        self.btn_mirror_h.setChecked(idx in self._action.frame_mirror_h)
        self.btn_mirror_h.blockSignals(False)
        self.btn_mirror_v.blockSignals(True)
        self.btn_mirror_v.setChecked(idx in self._action.frame_mirror_v)
        self.btn_mirror_v.blockSignals(False)
        items = [f"旋转 {self._action.frame_rotations.get(idx, 0)}°"]
        modes = self._action.frame_transform_modes.get(idx, set())
        labels = {"scale": "缩放", "skew": "斜切", "perspective": "透视", "distort": "扭曲"}
        for key in ("scale", "skew", "perspective", "distort"):
            if key in modes:
                items.append(labels[key])
        if self._action.frame_quads.get(idx, DEFAULT_QUAD) != DEFAULT_QUAD and not modes:
            items.append("自由变换")
        if idx in self._action.frame_mirror_h:
            items.append("水平镜像")
        if idx in self._action.frame_mirror_v:
            items.append("垂直镜像")
        self.lbl_transform_summary.setText("当前变换：" + " · ".join(items))

    def _transform_targets(self) -> list:
        """返回 [(action, 帧索引迭代器)]。

        勾选"数据复用"时覆盖本动作所有帧，并同步到其他勾选了复用帧设置的动作；
        未勾选时只改当前帧。当前动作始终包含（即使未注册进动作列表）。
        """
        if not self._action:
            return []
        if getattr(self._action, "reuse_frame_settings", False):
            targets = []
            seen: set[int] = set()
            for act in getattr(self, "_actions", ()) or ():
                if act is not None and act.reuse_frame_settings:
                    targets.append((act, range(len(act.frames))))
                    seen.add(id(act))
            if id(self._action) not in seen:
                targets.append((self._action, range(len(self._action.frames))))
            return targets
        return [(self._action, [getattr(self, "_current_frame", 0)])]

    # ---- 自由变换：模式按钮 + 预览手柄回调 ----
    def set_canvas(self, canvas) -> None:
        self.canvas = canvas

    def _on_mode_button(self, mode: str, on: bool) -> None:
        canvas = getattr(self, "canvas", None)
        if canvas is None:
            return
        if on:
            canvas.set_transform_mode(mode)
        elif canvas.transform_mode() == mode:
            canvas.set_transform_mode(None)

    def _on_canvas_mode_changed(self, mode: str) -> None:
        """预览画布侧的退出（点空白/Esc/再点同按钮）→ 同步按钮选中态。"""
        btn = {"rotate": self.btn_mode_rotate, "scale": self.btn_mode_scale,
               "skew": self.btn_mode_skew, "perspective": self.btn_mode_perspective,
               "distort": self.btn_mode_distort}.get(mode)
        buttons = (self.btn_mode_rotate, self.btn_mode_scale, self.btn_mode_skew,
                   self.btn_mode_perspective, self.btn_mode_distort)
        # QButtonGroup 的 exclusive 模式下，显式暂时关闭互斥才能可靠地
        # 清除最后一个 checked 按钮（尤其是点击画布空白处退出时）。
        self._mode_group.setExclusive(False)
        for candidate in buttons:
            candidate.blockSignals(True)
            candidate.setChecked(candidate is btn)
            candidate.blockSignals(False)
        self._mode_group.setExclusive(True)

    def _apply_rotation(self, angle: int) -> None:
        """旋转角度变更（仅来自预览圆形手柄）→ 传播到目标帧。"""
        if not self._action or not self._action.frames:
            return
        angle = int(angle) % 360
        for act, indices in self._transform_targets():
            for i in indices:
                if angle == 0:
                    act.frame_rotations.pop(i, None)
                else:
                    act.frame_rotations[i] = angle
            act.invalidate_transforms()
        self._transform_changed = True
        self.changed.emit()
        self._sync_transform_widgets()

    def _apply_pivot(self, pivot) -> None:
        """旋转中心变更，只写入旋转参数，不触碰动作锚点。"""
        if not self._action or not self._action.frames:
            return
        pivot = _normalized_rotation_pivot(pivot)
        for act, indices in self._transform_targets():
            for i in indices:
                if pivot == DEFAULT_ROTATION_PIVOT:
                    act.frame_rotation_pivots.pop(i, None)
                else:
                    act.frame_rotation_pivots[i] = pivot
            act.invalidate_transforms()
        self._transform_changed = True
        self.changed.emit()

    def _apply_quad(self, quad) -> None:
        """自由四边形变更（预览拖角/边点）→ 传播到目标帧（数据复用联动）。"""
        if not self._action or not self._action.frames:
            return
        quad = tuple(quad)
        mode = getattr(getattr(self, "canvas", None), "transform_mode", lambda: None)() or ""
        for act, indices in self._transform_targets():
            for i in indices:
                if quad == DEFAULT_QUAD:
                    act.frame_quads.pop(i, None)
                    if mode:
                        act.frame_transform_modes.pop(i, None)
                else:
                    act.frame_quads[i] = quad
                    if mode:
                        act.frame_transform_modes.setdefault(i, set()).add(mode)
            act.invalidate_transforms()
        self._transform_changed = True
        self.changed.emit()
        self._sync_transform_widgets()

    def _toggle_mirror(self, axis: str) -> None:
        if not self._action or not self._action.frames:
            return
        checked = (self.btn_mirror_h if axis == "h" else self.btn_mirror_v).isChecked()
        for act, indices in self._transform_targets():
            target = act.frame_mirror_h if axis == "h" else act.frame_mirror_v
            for i in indices:
                if checked:
                    target.add(i)
                else:
                    target.discard(i)
            act.invalidate_transforms()
        self._transform_changed = True
        self.changed.emit()

        self._refresh_canvas_label()

    def _on_ref_changed(self, idx: int):
        if self._action and idx >= 0:
            self._action.ref_frame = self.combo_ref.itemData(idx)
            self.changed.emit()

    def _on_opacity_changed(self, val: int):
        """透明度滑块调整当前帧透明度（非参照帧）。"""
        if self._action:
            self._action.frame_opacity = val / 100.0
            self.changed.emit()


class SwitchWidget(QWidget):
    """苹果风格胶囊开关。"""

    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(42, 24)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._checked = checked
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def setChecked(self, checked: bool) -> None:
        if self._checked != checked:
            self._checked = checked
            self.update()
            self.toggled.emit(checked)

    def isChecked(self) -> bool:
        return self._checked

    def sizeHint(self):
        return QSize(42, 24)

    def minimumSizeHint(self):
        return QSize(42, 24)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
            event.accept()
        else:
            super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        if self.width() <= 0 or self.height() <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QColor("#E91E63") if self._checked else QColor("#4a4a4a")
        p.setBrush(QBrush(track))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, 42, 24, 12, 12)
        knob_x = 20 if self._checked else 2
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(knob_x, 2, 20, 20)
        p.end()


class ActionThumbPlayer(QWidget):
    """动作小预览窗：按该动作 fps 循环播放帧，棋盘格背景显示透明区域。
    窗口尺寸可大于内部帧显示尺寸；移动类型动作的背景会随方向滚动。"""

    FRAME_DISPLAY_SIZE = 48  # 内部动画帧显示尺寸，不随窗口放大

    def __init__(self, action, size: int = 72, parent=None) -> None:
        super().__init__(parent)
        self._action = action
        self._size = size
        self.setFixedSize(size, size)
        self._frame_idx = 0
        self._bg_x = 0.0
        self._bg_y = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._next_frame)
        self._restart_timer()

    def _restart_timer(self) -> None:
        fps = max(1, int(getattr(self._action, "fps", 10) or 10))
        self._timer.start(int(1000 / fps))

    def _next_frame(self) -> None:
        frames = self._action.frames
        if frames:
            self._frame_idx = (self._frame_idx + 1) % len(frames)
        # 移动类型：棋盘格背景随方向滚动
        if getattr(self._action, "action_type", "static") == "move":
            speed = max(1, int(getattr(self._action, "move_speed", 10) or 10))
            step = speed / 5.0
            direction = getattr(self._action, "move_direction", "right")
            cell2 = 12  # 两个棋盘格单元，循环偏移范围
            # 相对运动：角色移动方向与背景滚动方向相反
            if direction == "right":
                self._bg_x = (self._bg_x - step) % cell2
            elif direction == "left":
                self._bg_x = (self._bg_x + step) % cell2
            elif direction == "down":
                self._bg_y = (self._bg_y - step) % cell2
            elif direction == "up":
                self._bg_y = (self._bg_y + step) % cell2
        self.update()

    def stop(self) -> None:
        self._timer.stop()

    def start(self) -> None:
        """恢复该动作缩略图播放。"""
        self._restart_timer()

    def paintEvent(self, event) -> None:
        if self.width() <= 0 or self.height() <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # 棋盘格背景（移动模式下带滚动偏移）
        cell = 6
        p.save()
        p.translate(self._bg_x, self._bg_y)
        for y in range(-cell, self._size + cell, cell):
            for x in range(-cell, self._size + cell, cell):
                c = "#2a2a2a" if (x // cell + y // cell) % 2 == 0 else "#1e1e1e"
                p.fillRect(x, y, cell, cell, QColor(c))
        p.restore()
        # 当前帧：内部显示尺寸保持 FRAME_DISPLAY_SIZE，不随窗口放大，居中
        frames = self._action.frames
        if frames:
            idx = self._frame_idx % len(frames)
            pm = action_frame_pixmap(self._action, idx)
            if pm and not pm.isNull():
                ds = self.FRAME_DISPLAY_SIZE
                scaled = pm.scaled(
                    ds, ds,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                p.drawPixmap(
                    (self._size - scaled.width()) // 2,
                    (self._size - scaled.height()) // 2,
                    scaled,
                )
        else:
            p.setPen(QColor("#666"))
            p.setFont(QFont("Arial", 8))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "空")
        p.end()


class PreviewArea(QWidget):
    """导出弹窗中间预览区：棋盘格背景 + 居中缩放显示一张预览图。
    支持滚轮缩放、鼠标拖动平移、双击重置。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self.setMinimumSize(220, 220)
        self.setStyleSheet("background: #141414; border: 1px solid #4a4a4a; border-radius: 4px;")
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        # 用户缩放（1.0 = 适应窗口）与平移偏移
        self._zoom = 1.0
        self._pan_x = 0
        self._pan_y = 0
        self._dragging = False
        self._last_pos = None

    def set_preview(self, pm: QPixmap | None) -> None:
        self._pixmap = pm
        self._zoom = 1.0
        self._pan_x = 0
        self._pan_y = 0
        self.update()

    def reset_view(self) -> None:
        self._zoom = 1.0
        self._pan_x = 0
        self._pan_y = 0
        self.update()

    def _fit_rect(self):
        """返回当前 pixmap 在控件中的绘制矩形 (x, y, w, h)。"""
        pm = self._pixmap
        if pm is None or pm.isNull():
            return None
        base = min(self.width() / pm.width(), self.height() / pm.height())
        s = base * self._zoom
        dw, dh = pm.width() * s, pm.height() * s
        x = (self.width() - dw) / 2 + self._pan_x
        y = (self.height() - dh) / 2 + self._pan_y
        return x, y, dw, dh

    def wheelEvent(self, event) -> None:
        if self._pixmap is None or self._pixmap.isNull():
            return
        old_pan_x, old_pan_y = self._pan_x, self._pan_y
        old_rect = self._fit_rect()
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self._zoom = max(0.1, min(20.0, self._zoom * factor))
        # 以鼠标位置为缩放中心，保持光标下的图像点不动
        new_rect = self._fit_rect()
        if old_rect and new_rect:
            mx, my = event.position().x(), event.position().y()
            ox, oy, ow, oh = old_rect
            nx, ny, nw, nh = new_rect
            if ow > 0 and oh > 0:
                rx = (mx - ox) / ow
                ry = (my - oy) / oh
                self._pan_x = (mx - nx) - rx * nw + old_pan_x
                self._pan_y = (my - ny) - ry * nh + old_pan_y
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_pos = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and self._last_pos is not None:
            cur = event.position().toPoint()
            self._pan_x += cur.x() - self._last_pos.x()
            self._pan_y += cur.y() - self._last_pos.y()
            self._last_pos = cur
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._last_pos = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseDoubleClickEvent(self, event) -> None:
        self.reset_view()

    def paintEvent(self, event) -> None:
        if self.width() <= 0 or self.height() <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # 棋盘格背景
        cell = 10
        for y in range(0, self.height(), cell):
            for x in range(0, self.width(), cell):
                c = "#2a2a2a" if (x // cell + y // cell) % 2 == 0 else "#1e1e1e"
                p.fillRect(x, y, cell, cell, QColor(c))
        rect = self._fit_rect()
        if rect is not None:
            x, y, dw, dh = rect
            p.drawPixmap(int(x), int(y), int(dw), int(dh), self._pixmap)
        else:
            p.setPen(QColor("#666"))
            p.setFont(QFont("Microsoft YaHei", 10))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "（无预览）")
        p.end()


def _safe_export_name(name: str, used: set[str] | None = None) -> str:
    """将动作名转换为安全文件名，并处理同名动作。"""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (name or "animation")).strip(" .")
    value = value or "animation"
    if used is None:
        return value
    base = value
    index = 2
    while value in used:
        value = f"{base}_{index}"
        index += 1
    used.add(value)
    return value


def _pixmap_to_pil(pixmap: QPixmap):
    """将 QPixmap 转成 Pillow RGBA 图像，保持原始像素尺寸。"""
    from PIL import Image

    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = image.bits()
    ptr.setsize(image.sizeInBytes())
    return Image.frombytes("RGBA", (image.width(), image.height()), bytes(ptr))


class ExportDialog(QDialog):
    """导出弹窗：左侧动作选择（循环小预览+胶囊开关），中间预览，右侧导出选项。"""

    def __init__(self, actions: list, source_pixmap: QPixmap | None = None,
                 project_provider=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("导出")
        self.resize(900, 600)
        # 分组框只保留标题文字，彻底隐藏边框/背景/标题描边
        self.setStyleSheet(
            "QGroupBox { background: transparent; border: none; border-radius: 0px;"
            " margin-top: 14px; padding-top: 0px; font-weight: bold;"
            " color: #F06292; font-size: 12px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 2px; top: 2px;"
            " padding: 0px; background: transparent; border: none; }"
        )
        self.actions = actions
        self._source_pixmap = source_pixmap
        # The provider returns the complete document, so exporting a project
        # never depends on which actions are selected in this dialog.
        self._project_provider = project_provider
        self._switches: dict = {}
        self._players: list[ActionThumbPlayer] = []
        self._players_by_action: dict = {}
        self._build_ui()
        self._update_preview()

    def _build_ui(self) -> None:
        main = QVBoxLayout(self)
        main.setContentsMargins(12, 12, 12, 12)
        main.setSpacing(10)

        content = QHBoxLayout()
        content.setSpacing(10)

        # ---- 左侧：动作列表（循环小预览 + 名称 + 开关）----
        left_box = QGroupBox("选择导出动作")
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(8, 12, 8, 8)
        left_layout.setSpacing(4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content_widget = QWidget()
        v = QVBoxLayout(content_widget)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        if not self.actions:
            empty = QLabel("（暂无动作）")
            empty.setStyleSheet("color: #666; font-size: 12px;")
            v.addWidget(empty)
        else:
            for act in self.actions:
                # ---- 动作卡牌（单行紧凑布局）----
                card = QFrame()
                card.setStyleSheet(
                    "QFrame { background: #232323; border: 1px solid #3a3a3a; border-radius: 6px; }"
                )
                card_layout = QGridLayout(card)
                card_layout.setContentsMargins(8, 5, 8, 5)
                card_layout.setHorizontalSpacing(8)
                card_layout.setVerticalSpacing(0)
                card_layout.setColumnMinimumWidth(0, 52)
                card_layout.setColumnMinimumWidth(2, 42)

                # 预览窗（左）
                player = ActionThumbPlayer(act, 52)
                self._players.append(player)
                self._players_by_action[act] = player
                card_layout.addWidget(player, 0, 0, 2, 1, Qt.AlignmentFlag.AlignCenter)

                # 动作名 + 信息（中，垂直）
                info_col = QVBoxLayout()
                info_col.setSpacing(1)
                info_col.setContentsMargins(0, 0, 0, 0)
                label = QLabel(act.name)
                label.setStyleSheet(
                    "QLabel { color: #e8e8e8; font-size: 12px; font-weight: bold;"
                    " background: transparent; border: none; }"
                )
                info_col.addWidget(label)
                info_label = QLabel(self._action_info_text(act))
                info_label.setStyleSheet(
                    "QLabel { color: #999;"
                    " background: transparent; border: none; }"
                )
                info_label.setWordWrap(True)
                info_col.addWidget(info_label)
                info_col.addStretch()
                card_layout.addLayout(info_col, 0, 1, 2, 1)

                # 开关（右）
                sw = SwitchWidget(checked=True)
                sw.toggled.connect(
                    lambda checked, action=act: self._on_action_toggle(action, checked)
                )
                self._switches[act] = sw
                card_layout.addWidget(sw, 0, 2, 2, 1, Qt.AlignmentFlag.AlignCenter)

                v.addWidget(card)
        v.addStretch()
        scroll.setWidget(content_widget)
        left_layout.addWidget(scroll)
        content.addWidget(left_box, 23)

        # ---- 中间：预览区 ----
        mid_box = QGroupBox("预览")
        mid_layout = QVBoxLayout(mid_box)
        mid_layout.setContentsMargins(8, 12, 8, 8)
        self.preview_area = PreviewArea()
        mid_layout.addWidget(self.preview_area, 1)
        content.addWidget(mid_box, 30)

        # ---- 右侧：导出选项 ----
        right_box = QGroupBox("导出选项")
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(12, 12, 12, 8)
        right_layout.setSpacing(8)

        lbl_mode = QLabel("导出方式")
        lbl_mode.setStyleSheet("color: #F06292; font-weight: bold;")
        right_layout.addWidget(lbl_mode)
        self.rb_original = QRadioButton("原图导出")
        self.rb_repack = QRadioButton("重构导出")
        self.rb_repack.setChecked(True)  # 默认重构导出
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.rb_original)
        self._mode_group.addButton(self.rb_repack)
        self.rb_original.toggled.connect(lambda: self._update_preview())
        right_layout.addWidget(self.rb_original)
        right_layout.addWidget(self.rb_repack)

        right_layout.addSpacing(8)
        lbl_extra = QLabel("附加选项")
        lbl_extra.setStyleSheet("color: #F06292; font-weight: bold;")
        right_layout.addWidget(lbl_extra)
        self.cb_json = QCheckBox("导出 JSON")
        self.cb_json.setChecked(True)  # 默认打开导出 JSON
        self.cb_gif = QCheckBox("导出 GIF（预览）")
        self.cb_separate = QCheckBox("分别导出")
        self.cb_project = QCheckBox("导出 SSTOOL 工程")
        self.cb_project.setChecked(False)
        self.cb_project.setToolTip("导出一个包含全部原图和编辑状态的单文件工程")
        self.cb_separate.toggled.connect(self._on_separate_toggled)
        right_layout.addWidget(self.cb_json)
        right_layout.addWidget(self.cb_gif)
        right_layout.addWidget(self.cb_separate)
        right_layout.addWidget(self.cb_project)

        # JSON 格式：独立分组框 + 竖排（可多选，同时导出多种格式）
        json_fmt_group = QGroupBox("JSON 格式（可多选）")
        json_fmt_group.setStyleSheet(
            "QGroupBox { background: transparent; border: 1px solid #333; border-radius: 6px;"
            " margin-top: 10px; padding-top: 4px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; top: 0px; padding: 0 4px;"
            " color: #F06292; font-weight: bold; }"
        )
        json_fmt_layout = QVBoxLayout(json_fmt_group)
        json_fmt_layout.setContentsMargins(10, 10, 8, 8)
        json_fmt_layout.setSpacing(4)
        self._json_fmt_checks: dict = {}
        for fmt_id, fmt_label in FORMAT_CHOICES:
            cb = QCheckBox(fmt_label)
            cb.setChecked(fmt_id == FMT_NATIVE)  # 默认只勾本工具格式
            cb.setStyleSheet("color: #ccc;")  # 字号与上方复选框一致（继承默认字体）
            self._json_fmt_checks[fmt_id] = cb
            json_fmt_layout.addWidget(cb)
        self.cb_json.toggled.connect(json_fmt_group.setEnabled)
        self.cb_json.toggled.connect(lambda _=False: self._update_preview())
        for _cb in self._json_fmt_checks.values():
            _cb.toggled.connect(lambda _=False: self._update_preview())
        json_fmt_group.setEnabled(self.cb_json.isChecked())
        right_layout.addWidget(json_fmt_group)

        right_layout.addSpacing(8)
        lbl_info_title = QLabel("图像信息")
        lbl_info_title.setStyleSheet("color: #F06292; font-weight: bold;")
        right_layout.addWidget(lbl_info_title)
        self.lbl_preview_info = QLabel("")
        self.lbl_preview_info.setWordWrap(True)
        self.lbl_preview_info.setStyleSheet(
            "QLabel { color: #aaa; background: #232323; "
            "border: 1px solid #3a3a3a; border-radius: 4px; padding: 6px; }"
        )
        right_layout.addWidget(self.lbl_preview_info)

        right_layout.addStretch()
        content.addWidget(right_box, 20)

        main.addLayout(content, 1)

        # ---- 底部按钮 ----
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("取消")
        btn_cancel.setFixedSize(80, 30)
        btn_cancel.clicked.connect(self.reject)
        btn_export = QPushButton("导出")
        btn_export.setFixedSize(80, 30)
        btn_export.setStyleSheet(
            "QPushButton { background: #E91E63; color: white; border: none; border-radius: 4px; }"
            "QPushButton:hover { background: #F06292; }"
        )
        btn_export.clicked.connect(self._perform_export)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_export)
        main.addLayout(btn_row)

    # ---- 预览 ----
    def _on_action_toggle(self, action, checked: bool) -> None:
        """动作取消导出时立即停止缩略图定时器，避免后台无效播放。"""
        player = self._players_by_action.get(action)
        if player is not None:
            if checked:
                player.start()
            else:
                player.stop()
        self._update_preview()

    def _on_separate_toggled(self, checked: bool) -> None:
        """拆分导出必须使用重构帧，确保每个动作都有独立自洽的图集。"""
        self.rb_original.setEnabled(not checked)
        if checked and self.rb_original.isChecked():
            self.rb_repack.setChecked(True)
        # 分别导出时预览展示的是第 1 个动作的图集，需要同步刷新说明
        self._update_preview()

    @staticmethod
    def _action_info_text(act) -> str:
        """动作信息摘要：帧数 · 帧尺寸 · 帧率 · 循环模式 · 动作类型。"""
        n_frames = len(act.frames)
        if getattr(act, "frame_rects", None):
            max_w = max(r[2] for r in act.frame_rects)
            max_h = max(r[3] for r in act.frame_rects)
            size_text = f"{max_w}×{max_h}"
        else:
            size_text = "—"
        fps = int(getattr(act, "fps", 10) or 10)
        loop_map = {Action.LOOP: "循环", Action.ONCE: "单次", Action.PINGPONG: "往返"}
        loop_text = loop_map.get(getattr(act, "loop_mode", Action.LOOP), "循环")
        atype = "移动" if getattr(act, "action_type", "static") == "move" else "静止"
        return f"{n_frames}帧·{size_text}·{fps}FPS·{loop_text}·{atype}"

    def _build_repack_pixmap(self) -> QPixmap | None:
        """使用与导出完全相同的参数生成预览，确保预览与实际 PNG 一致。

        合成帧一律按整块打包（与导出一致，图集可能略大但画面必然相同）；
        分别导出时每个动作单独一张图集 -> 预览展示第 1 个动作的那张。
        """
        selected = self.selected_actions()
        if self.cb_separate.isChecked():
            selected = selected[:1]
        atlas, _ = self._build_packed_atlas(selected)
        return atlas

    @staticmethod
    def _final_frames(action: Action) -> list[QPixmap]:
        """生成动作最终帧：统一画布、padding 和单帧偏移均在此应用。"""
        if not action.frames:
            return []
        canvas_w, canvas_h, positions, _ = compute_preview_layout(action, 0)
        if canvas_w <= 0 or canvas_h <= 0:
            return []
        result = []
        for index, frame in enumerate(action.frames):
            if frame.isNull():
                continue
            x, y, _, _ = positions[index]
            canvas = QPixmap(canvas_w, canvas_h)
            canvas.fill(Qt.GlobalColor.transparent)
            painter = QPainter(canvas)
            painter.drawPixmap(x, y, action_frame_pixmap(action, index))
            painter.end()
            result.append(canvas)
        return result

    @staticmethod
    def _pack_frames(frames: list[QPixmap], gap: int = 1) -> tuple[QPixmap | None, list[tuple[int, int, int, int]]]:
        """用紧凑 shelf packing 排列帧，保持帧像素大小不变。"""
        if not frames:
            return None, []
        total_area = sum(frame.width() * frame.height() for frame in frames)
        max_width = max(frame.width() for frame in frames)
        sum_width = sum(frame.width() for frame in frames) + gap * (len(frames) - 1)
        target_width = max(max_width, int(math.ceil(math.sqrt(max(1, total_area)))))
        # 用固定数量的候选宽度覆盖从最窄到横向排列，避免大图时采样范围失控。
        candidates = {max_width, target_width, sum_width}
        span = max(0, sum_width - max_width)
        sample_count = min(64, max(12, len(frames) * 4))
        if span:
            for step in range(sample_count + 1):
                ratio = step / sample_count
                candidates.add(max_width + round(span * ratio))
        candidates = sorted(max(max_width, min(sum_width, width)) for width in candidates)
        candidates = list(dict.fromkeys(candidates))

        layouts = []
        ordered = sorted(range(len(frames)), key=lambda i: (frames[i].height(), frames[i].width()), reverse=True)
        for width in candidates:
            rows: list[list[int]] = []
            row_widths: list[int] = []
            row_heights: list[int] = []
            for index in ordered:
                frame = frames[index]
                placed = False
                for row, used_width in enumerate(row_widths):
                    needed = frame.width() + (gap if rows[row] else 0)
                    if used_width + needed <= width:
                        rows[row].append(index)
                        row_widths[row] += needed
                        row_heights[row] = max(row_heights[row], frame.height())
                        placed = True
                        break
                if not placed:
                    rows.append([index])
                    row_widths.append(frame.width())
                    row_heights.append(frame.height())
            used_width = max(row_widths, default=0)
            height = sum(row_heights) + gap * max(0, len(rows) - 1)
            area = used_width * height
            aspect_error = abs(math.log(max(1, used_width) / max(1, height)))
            layouts.append((area, aspect_error, used_width, rows, row_widths, row_heights, height))

        # 先保持面积接近最优，再在 5% 面积容差内优先选择接近正方形的布局。
        min_area = min(layout[0] for layout in layouts)
        eligible = [layout for layout in layouts if layout[0] <= min_area * 1.05]
        best = min(eligible, key=lambda layout: (layout[1], layout[0], abs(layout[2] - layout[6])))

        _, _, width, rows, row_widths, row_heights, height = best
        positions = [None] * len(frames)
        y = 0
        for row, indices in enumerate(rows):
            x = 0
            for index in indices:
                frame = frames[index]
                positions[index] = (x, y, frame.width(), frame.height())
                x += frame.width() + gap
            y += row_heights[row] + gap
        canvas = QPixmap(max(1, width), max(1, height))
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        for index, frame in enumerate(frames):
            x, y, _, _ = positions[index]
            painter.drawPixmap(x, y, frame)
        painter.end()
        return canvas, positions

    @staticmethod
    def _dedupe_frames(frames: list[QPixmap]) -> tuple[list[QPixmap], list[list[int]]]:
        """按像素内容去重最终帧。

        多个动作/帧若内容完全相同（同一元素被多个动作复用、或动作内重复帧），
        只保留一份用于打包，返回 (唯一帧列表, 索引分组)：
        - unique: 去重后的帧列表（每份只出现一次）
        - groups: 与原 frames 一一对应，groups[i] 是该帧在 unique 中的下标

        去重对象是已烘焙合成/居中/padding/单帧偏移的最终画布帧：
        只有像素逐字节完全一致的帧才会合并；hash 仅作快速索引，
        命中后会用原始像素字节再次确认，避免任何碰撞导致误合并。
        """
        import hashlib as _hl

        unique: list[QPixmap] = []
        unique_bytes: list[bytes] = []
        index_map: dict[str, list[int]] = {}  # hash -> 该 hash 下的所有 unique 下标

        def content_bytes(pm: QPixmap) -> bytes:
            img = pm.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            buf = img.constBits()
            buf.setsize(img.sizeInBytes())
            return bytes(buf)

        groups: list[list[int]] = []
        for pm in frames:
            raw = content_bytes(pm)
            key = _hl.sha1(raw).hexdigest()
            matched = None
            for candidate in index_map.get(key, ()):
                if unique_bytes[candidate] == raw:
                    matched = candidate
                    break
            if matched is not None:
                groups.append([matched])
                continue
            # 未命中，或 hash 命中但字节不一致（理论碰撞）→ 新增一份
            idx = len(unique)
            index_map.setdefault(key, []).append(idx)
            unique.append(pm)
            unique_bytes.append(raw)
            groups.append([idx])
        return unique, groups

    def _build_packed_atlas(self, actions: list[Action]):
        """重构打包：普通帧以「元素图块」为单位打包并内容去重；合成帧直接打包整块。

        图集内容分两类：
        - 普通帧：登记为去重后的**元素图块**（原图中被检测出的元素原像素），
          同一元素被多个动作/帧复用时图集里只存一份；
        - 合成帧：直接打包拼接好的**完整整块**（一帧一块，不拆部件、不参与图块复用）。

        合成帧不复用的原因：parts 拆装链路复杂，容易在导出/导入往返中引入
        部件丢失或位置错位；整块导出后，第三方格式与本工具格式的表现完全一致，
        图集略大，但导出安全性与画面正确性最高（以导出安全为基准）。
        """
        src = self._source_pixmap
        tile_pixmaps: list[QPixmap] = []      # 待打包的图块/整块
        tile_index: dict[str, int] = {}       # 内容哈希 -> 图块下标（快速索引）
        tile_bytes: list[bytes] = []

        def content_of(pm: QPixmap) -> bytes:
            img = pm.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            buf = img.constBits()
            buf.setsize(img.sizeInBytes())
            return bytes(buf)

        def register(pm: QPixmap) -> int:
            """登记一个图块，返回其在唯一图块表里的下标（内容相同则复用）。"""
            raw = content_of(pm)
            key = hashlib.sha1(raw).hexdigest()
            for cand in tile_index.get(key, ()):   # hash 命中后逐字节复核
                if tile_bytes[cand] == raw:
                    return cand
            idx = len(tile_pixmaps)
            tile_index.setdefault(key, []).append(idx)
            tile_pixmaps.append(pm)
            tile_bytes.append(raw)
            return idx

        frames_info = []  # 每帧: (action, index, 帧在画布中的(x,y), (cw,ch), parts, frame)
        for action in actions:
            if not action.frames:
                continue
            cw, ch, positions, _ = compute_preview_layout(action, 0)
            for index, frame in enumerate(action.frames):
                if frame.isNull():
                    continue
                fx, fy, _, _ = positions[index]
                raw_parts = action.frame_parts[index] if index < len(action.frame_parts) else []
                rot = action.frame_rotations.get(index, 0)
                mh = index in action.frame_mirror_h
                mv = index in action.frame_mirror_v
                quad = action.frame_quads.get(index)
                pivot = action.frame_rotation_pivots.get(index, DEFAULT_ROTATION_PIVOT)
                parts: list[tuple[int, int, int, int, int]] = []   # (tile_id, rel_x, rel_y, origin_x, origin_y)
                if len(raw_parts) > 1:
                    # 合成帧：整块直接入表（不复用、不拆部件），一帧一块。
                    tile_idx = len(tile_pixmaps)
                    tile_pixmaps.append(transform_frame_pixmap(frame, rot, mh, mv, quad, pivot))
                    parts.append((tile_idx, 0, 0, 0, 0))
                elif raw_parts:
                    # 普通帧（单部件）：以元素图块登记，内容去重复用。
                    part = raw_parts[0]
                    # 新结构：(裁剪矩形, 帧内x, 帧内y, 逻辑x, 逻辑y)
                    if len(part) == 5:
                        srect, rx, ry, ox, oy = part
                    else:  # 兼容旧结构
                        srect, rx, ry = part
                        ox, oy = srect[0], srect[1]
                    parts.append((register(transform_frame_pixmap(src.copy(*srect), rot, mh, mv, quad, pivot)), rx, ry, ox, oy))
                else:
                    rect = action.frame_rects[index] if index < len(action.frame_rects) else None
                    if rect:
                        parts.append((register(transform_frame_pixmap(src.copy(*rect), rot, mh, mv, quad, pivot)), 0, 0, rect[0], rect[1]))
                frames_info.append((action, index, (fx, fy), (cw, ch), parts, frame))

        atlas, positions = self._pack_frames(tile_pixmaps)

        records = {}
        for action, index, (fx, fy), (cw, ch), parts, frame in frames_info:
            parts_out = []
            for tid, rx, ry, ox, oy in parts:
                tx, ty, tw, th = positions[tid]
                parts_out.append({
                    "rect": (tx, ty, tw, th),   # 图集中的位置（裁剪用）
                    "pos": (rx, ry),            # 帧内相对位置
                    "origin": (ox, oy),         # 元素的逻辑坐标（元素层关系）
                })
            first = parts_out[0]["rect"] if parts_out else (0, 0, 0, 0)
            records[(action, index)] = {
                "parts": parts_out,
                "rect": first,
                "composite_rect": None,         # 兼容旧记录读取；合成帧现已是整块，不再单独打包
                "offset": (fx, fy),
                "source": action_frame_size(action, index),
                "canvas": (cw, ch),
                "trimmed": False,
            }
        return atlas, records

    def _build_original_atlas(self, actions: list[Action]):
        """原图模式直接输出源图，并使用原图中的元素矩形。

        存在旋转/镜像帧时，源图无法直接表达变换：把变换帧烘焙成图块
        **追加到原图下方**（未变换帧仍引用原图区域），保证 JSON 引用
        与实际画面一致（GIF 始终走 _final_frames，天然带变换）。
        """
        if self._source_pixmap is None or self._source_pixmap.isNull():
            return None, {}
        src = self._source_pixmap

        def frame_has_transform(action: Action, index: int) -> bool:
            return bool(action.frame_rotations.get(index, 0)
                        or index in action.frame_mirror_h
                        or index in action.frame_mirror_v
                        or action.frame_quads.get(index) not in (None, DEFAULT_QUAD))

        need_bake = [
            (action, index, rect)
            for action in actions
            for index, rect in enumerate(action.frame_rects)
            if 0 <= index < len(action.frames) and frame_has_transform(action, index)
        ]
        records = {}

        if not need_bake:
            for action in actions:
                for index, rect in enumerate(action.frame_rects):
                    x, y, width, height = rect
                    records[(action, index)] = {
                        # 原图模式下帧直接引用原图区域，等价于「单部件、部件即在原图中的位置」
                        "parts": [{"rect": (x, y, width, height), "pos": (0, 0)}],
                        "rect": (x, y, width, height),
                        "composite_rect": (x, y, width, height),
                        "offset": (0, 0),
                        "source": (width, height),
                        "canvas": (width, height),
                        "trimmed": False,
                    }
            return src, records

        # 有变换帧：原图下方追加变换块（横向居中，逐行排布）
        baked = [(action, index, action_frame_pixmap(action, index)) for action, index, _ in need_bake]
        baked_w = max((pm.width() for _, _, pm in baked), default=1)
        baked_h = sum(pm.height() for _, _, pm in baked) + max(0, len(baked) - 1) + 2
        canvas_w = max(src.width(), baked_w + 2)
        canvas = QPixmap(canvas_w, src.height() + baked_h)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.drawPixmap(0, 0, src)
        cur_y = src.height() + 2
        bake_rects: dict[tuple, tuple[int, int, int, int]] = {}
        for action, index, pm in baked:
            bx = max(1, (canvas_w - pm.width()) // 2)
            painter.drawPixmap(bx, cur_y, pm)
            bake_rects[(action, index)] = (bx, cur_y, pm.width(), pm.height())
            cur_y += pm.height() + 1
        painter.end()

        for action in actions:
            for index, rect in enumerate(action.frame_rects):
                if 0 <= index < len(action.frames) and frame_has_transform(action, index):
                    bx, by, bw, bh = bake_rects[(action, index)]
                    records[(action, index)] = {
                        "parts": [{"rect": (bx, by, bw, bh), "pos": (0, 0)}],
                        "rect": (bx, by, bw, bh),
                        "composite_rect": (bx, by, bw, bh),
                        "offset": (0, 0),
                        "source": (bw, bh),
                        "canvas": (bw, bh),
                        "trimmed": False,
                    }
                else:
                    x, y, width, height = rect
                    records[(action, index)] = {
                        "parts": [{"rect": (x, y, width, height), "pos": (0, 0)}],
                        "rect": (x, y, width, height),
                        "composite_rect": (x, y, width, height),
                        "offset": (0, 0),
                        "source": (width, height),
                        "canvas": (width, height),
                        "trimmed": False,
                    }
        return canvas, records

    def _update_preview(self) -> None:
        if self.rb_original.isChecked():
            pm, _ = self._build_original_atlas(self.selected_actions())
            mode_name = "原图"
        else:
            pm = self._build_repack_pixmap()
            mode_name = "重构图"
        self.preview_area.set_preview(pm)
        # 信息栏要说明"这张预览对应导出里的哪张图"，避免与实际产出不符
        note = ""
        if mode_name == "重构图":
            if self.cb_separate.isChecked():
                n = len(self.selected_actions())
                note = f"　·　分别导出：共 {max(1, n)} 张，此处为第 1 张"
            else:
                note = "　·　合成帧按整块打包（图集略大，导出更安全）"
        # 更新预览图尺寸信息
        if pm is not None and not pm.isNull():
            pixels = pm.width() * pm.height()
            if pixels >= 1_000_000:
                px_text = f"{pixels / 1_000_000:.1f}MP"
            elif pixels >= 1_000:
                px_text = f"{pixels / 1_000:.0f}K"
            else:
                px_text = f"{pixels}"
            self.lbl_preview_info.setText(
                f"{mode_name}：{pm.width()}×{pm.height()} px · {px_text}{note}"
            )
        else:
            self.lbl_preview_info.setText(f"{mode_name}：（无内容）")

    @staticmethod
    def _animation_json(action: Action, atlas_name: str, gif_name: str | None, records: dict) -> dict:
        frames = []
        for index, frame in enumerate(action.frames):
            record = records.get((action, index))
            if record is None:
                continue
            frame_ox, frame_oy = record["offset"]
            sw, sh = record["source"]
            parts = record.get("parts") or []
            parts_json = []
            for part in parts:
                px, py, pw, ph = part["rect"]
                rx, ry = part["pos"]
                po_x, po_y = part.get("origin", (0, 0))
                parts_json.append({
                    "rect": {"x": px, "y": py, "width": pw, "height": ph},
                    "pos": {"x": rx, "y": ry},
                    "origin": {"x": po_x, "y": po_y},
                })
            x, y, width, height = record["rect"]
            frames.append({
                "index": index,
                # parts：该帧由哪些图块组成（普通帧 1 个，合成帧多个）。
                # 渲染时把每个图块的 rect 裁出、贴到「帧在画布中的 offset + 该部件的 pos」。
                "parts": parts_json,
                "rect": {"x": x, "y": y, "width": width, "height": height},
                "source_size": {"width": sw, "height": sh},
                "offset": {"x": frame_ox, "y": frame_oy},
                "trimmed": bool(record.get("trimmed", False)),
                "duration_ms": round(1000 / max(1, action.fps)),
            })
        canvas_w, canvas_h, _, _ = compute_preview_layout(action, 0) if action.frames else (0, 0, [], (0, 0, 0))
        anchor_x, anchor_y = action_anchor_pixels(action, canvas_w, canvas_h)
        result = {
            "name": action.name,
            "type": action.action_type,
            "fps": action.fps,
            "loop": action.loop_mode,
            "canvas": {"width": canvas_w, "height": canvas_h, "padding": action.padding},
            "anchor": {
                "x": anchor_x,
                "y": anchor_y,
                "unit": "px",
                "space": "action_canvas",
                "normalized": {"x": action.anchor[0], "y": action.anchor[1]},
            },
            "ground": {"enabled": action.ground_enabled, "offset": action.ground_offset},
            "move": {
                "direction": action.move_direction,
                "speed": action.move_speed,
            } if action.action_type == "move" else None,
            "atlas": atlas_name,
            "frames": frames,
        }
        if gif_name:
            result["gif"] = gif_name
        return result

    @staticmethod
    def _write_gif(path: Path, action: Action, frames: list[QPixmap]) -> None:
        from PIL import Image

        if not frames:
            return
        images = [_pixmap_to_pil(frame) for frame in frames]
        if action.loop_mode == Action.PINGPONG and len(images) > 1:
            images = images + images[-2:0:-1]
        duration = max(1, round(1000 / max(1, action.fps)))
        loop = 0 if action.loop_mode == Action.LOOP else 1
        images[0].save(
            str(path),
            format="GIF",
            save_all=True,
            append_images=images[1:],
            duration=duration,
            loop=loop,
            disposal=2,
            optimize=False,
        )

    def _write_json(self, path: Path, atlas_name: str, atlas: QPixmap, animations: list[dict]) -> None:
        # 汇总「元素表」：按 (图集位置, 逻辑坐标) 去重，给每个元素分配稳定 id，
        # 并让 frames[].parts 通过 element 字段引用它。
        # elements 让消费方（尤其是 AI）能看出图集里哪些图块属于同一元素、
        # 以及它们在原始素材中的相对位置；导入本软件时也靠它还原元素坐标系。
        elements: list[dict] = []
        element_ids: dict[tuple, int] = {}
        for anim in animations:
            for fr in anim.get("frames", []):
                for part in fr.get("parts", []):
                    r = part["rect"]
                    o = part.get("origin", {"x": 0, "y": 0})
                    key = (r["x"], r["y"], r["width"], r["height"], o["x"], o["y"])
                    eid = element_ids.get(key)
                    if eid is None:
                        eid = len(elements)
                        element_ids[key] = eid
                        elements.append({
                            "id": eid,
                            "rect": dict(r),
                            "origin": {"x": o["x"], "y": o["y"]},
                        })
                    part["element"] = eid
        payload = {
            "format": "spritesheet-animation",
            "version": "1.1",
            "generator": {"name": __app_name__, "version": __version__},
            "atlas": {"image": atlas_name, "width": atlas.width(), "height": atlas.height()},
            "elements": elements,
            "animations": animations,
            "usage": {
                "summary": "animations 是动作数组（不是字典）。每个动作有一个逻辑画布 canvas；每一帧由若干「图块部件」组成（frames[].parts）：把每个部件的 rect 从 atlas.image 裁出，贴到画布坐标 (帧.offset + 部件.pos)，就得到该帧的完整画面。普通帧只有 1 个部件，合成帧有多个（例如纵向拼接的多个元素）。按 fps（或 duration_ms）播放，用 anchor 定位角色原点。",
                "animations_is_array": "animations 是数组：请按索引或遍历 name 字段定位动作，不要写成 animations['name']。",
                "coordinate_system": "两套坐标不要混用：parts[].rect 的 x/y 是图集坐标（从图集左上角算）；frames[].offset 与 parts[].pos 是逻辑画布坐标（从画布左上角算）。图块的最终落点 = offset + pos。",
                "canvas": "canvas 是动作的逻辑画布尺寸（width×height）。它只用于对齐：把所有图块按 offset+pos 贴到这张画布上，使各帧元素位置稳定（播放不抖动）。图集里的图块本身是元素原始像素，不含为对齐而填充的透明边。",
                "parts": "frames[].parts 是该帧的组成部件数组。每个部件 = 一个图块：rect 指明它在图集中的位置，pos 指明它相对帧左上角的偏移。同一图块会被多个帧/多个动作复用（合成帧也复用元素图块），所以图集里不会有重复内容。",
                "source_size": "source_size 是该帧的整体尺寸（合成帧为多个部件的外接尺寸），仅作参考；渲染以 parts 为准。",
                "anchor": "anchor 是动作逻辑画布内的锚点像素坐标（取值范围 0..canvas 对应轴），用于定位角色原点；不是图集坐标。",
                "trimmed": "固定为 false：图块就是元素原始像素，图集里没有为对齐而填充的透明边。",
                "loop_values": {"loop": "循环", "once": "播放一次", "pingpong": "往返播放"},
                "fields": {
                    "animations[]": {
                        "name": "动作名（用于显示/查找，不一定唯一，按 name 查时取第一个匹配即可）",
                        "type": "动作类型：static=静止，move=移动（配合 move 字段描述移动语义）",
                        "fps": "播放帧率（帧/秒）；与 frames[].duration_ms 等价（duration_ms = round(1000/fps)）",
                        "loop": "循环模式：loop/once/pingpong，见 usage.loop_values",
                        "canvas": {"width": "逻辑画布宽（像素），用于对齐帧", "height": "逻辑画布高（像素）", "padding": "画布四周扩展的安全边距（像素）"},
                        "anchor": {
                            "x": "锚点 X 像素坐标（0..canvas.width）",
                            "y": "锚点 Y 像素坐标（0..canvas.height）",
                            "unit": "固定为 px",
                            "space": "固定为 action_canvas：坐标属于该动作自己的画布，不是图集坐标",
                            "normalized": {"x": "锚点归一化 X（0~1，0=画布左缘，1=右缘）", "y": "锚点归一化 Y（0~1，0=画布上缘，1=下缘）"},
                        },
                        "ground": {
                            "enabled": "是否启用模拟地面线",
                            "offset": "地面线相对自动位置（主体最底部）的向上偏移像素；仅 ground.enabled=true 时有意义",
                        },
                        "move": "type=move 时是对象（含 direction/speed）；type=static 时为 null，忽略即可。",
                        "move.direction": "移动方向：up/down/left/right（仅 type=move 时存在）",
                        "move.speed": "移动速度（1~100，相对值，单位由使用方定义；仅 type=move 时存在）",
                        "atlas": "该动作的帧所在图集文件名（= 顶层 atlas.image）",
                        "frames[]": {
                            "index": "帧序号（0 起，连续）",
                            "parts[]": {
                                "rect": {"x": "图块在图集中的 X", "y": "图块在图集中的 Y", "width": "图块宽（= 元素原始宽）", "height": "图块高（= 元素原始高）"},
                                "pos": {"x": "该图块相对帧左上角的水平偏移", "y": "该图块相对帧左上角的垂直偏移"},
                            },
                            "rect": "便捷字段：单部件帧即该图块位置；多部件帧时请改用 parts。",
                            "source_size": {"width": "该帧整体宽（合成帧为外接宽）", "height": "该帧整体高"},
                            "offset": {"x": "该帧在逻辑画布中的左上角 X", "y": "该帧在逻辑画布中的左上角 Y"},
                            "trimmed": "固定为 false：图块未做透明边裁剪",
                            "duration_ms": "该帧显示时长（毫秒），与 fps 一致",
                        },
                    },
                    "atlas": {"image": "图集图片文件名（与 JSON 同目录）", "width": "图集宽（像素）", "height": "图集高（像素）"},
                },
                "steps": [
                    "读取 atlas.image 图片。",
                    "遍历 animations（数组），对每个动作 anim：",
                    "  建一张 anim.canvas.width × anim.canvas.height 的透明画布（逻辑画布）。",
                    "  对第 i 帧：遍历 anim.frames[i].parts，按 part.rect 从图集裁出图块，",
                    "  贴到画布坐标 (frame.offset.x + part.pos.x, frame.offset.y + part.pos.y)。",
                    "  这张画布就是该帧的完整画面；按 anim.fps（或 frames[i].duration_ms）显示，循环行为见 loop。",
                    "  用 anim.anchor 定位角色原点（锚点在逻辑画布坐标系内）。",
                    "  type=move 时，move.direction / move.speed 描述角色移动动画的走向与相对速度（可作播放方向/速率提示）。",
                ],
                "example": {
                    "language": "python",
                    "code": (
                        "import json\n"
                        "from PIL import Image\n"
                        "data = json.load(open('spritesheet.json', encoding='utf-8'))\n"
                        "atlas = Image.open(data['atlas']['image'])\n"
                        "for anim in data['animations']:  # animations 是数组\n"
                        "    cw = anim['canvas']['width']\n"
                        "    ch = anim['canvas']['height']\n"
                        "    frames = []\n"
                        "    for f in anim['frames']:\n"
                        "        canvas = Image.new('RGBA', (cw, ch), (0, 0, 0, 0))\n"
                        "        fo = f['offset']\n"
                        "        for part in f['parts']:   # 普通帧 1 个部件，合成帧多个\n"
                        "            r, pos = part['rect'], part['pos']\n"
                        "            tile = atlas.crop((r['x'], r['y'],\n"
                        "                               r['x'] + r['width'],\n"
                        "                               r['y'] + r['height']))\n"
                        "            canvas.paste(tile, (fo['x'] + pos['x'], fo['y'] + pos['y']))\n"
                        "        frames.append(canvas)\n"
                        "    # frames[i] 即第 i 帧完整画面；用 anim['anchor'] 定位角色原点\n"
                        "    # 播放间隔 = f['duration_ms'] 毫秒，循环行为见 anim['loop']"
                    ),
                },
            },
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _json_formats(self) -> list[str]:
        """返回勾选的 JSON 格式标识列表（按 FORMAT_CHOICES 顺序）。"""
        return [fmt for fmt, _ in FORMAT_CHOICES if self._json_fmt_checks.get(fmt, None) is not None
                and self._json_fmt_checks[fmt].isChecked()]

    def _write_animation_jsons(
        self,
        base_name: str,
        atlas_name: str,
        atlas: QPixmap,
        actions: list,
        records: dict,
        gif_names: dict | None = None,
    ) -> list[Path]:
        """为每个勾选的 JSON 格式写出一个文件，返回生成的文件列表。

        base_name 是不含扩展名的目标路径（如 {folder}/spritesheet 或 {folder}/{name}），
        各格式自动追加后缀：本工具格式 -> .json，其余 -> .{fmt}.json。
        """
        gif_names = gif_names or {}
        generated: list[Path] = []
        for fmt in self._json_formats():
            suffix = FORMAT_SUFFIX.get(fmt, "")
            path = Path(f"{base_name}{suffix}.json")
            if fmt == FMT_NATIVE:
                animations = [
                    self._animation_json(action, atlas_name, gif_names.get(action), records)
                    for action in actions
                ]
                self._write_json(path, atlas_name, atlas, animations)
            else:
                # 第三方格式：动作名去重后作为帧名前缀（与 PNG/GIF 文件名规则一致）
                prefixes: dict = {}
                used_names: set[str] = set()
                for action in actions:
                    prefixes[action] = _safe_export_name(action.name, used_names)
                payload = build_payload(
                    fmt,
                    actions,
                    records,
                    atlas_name,
                    atlas.width(),
                    atlas.height(),
                    f"{__app_name__} {__version__}",
                    prefixes,
                )
                write_payload(path, payload)
            generated.append(path)
        return generated

    def _export_to_directory(self, directory: str, progress=None) -> list[Path]:
        """执行导出；progress 为可选回调 progress(value:int, total:int, label:str) -> bool，
        返回 False 表示用户取消。"""
        selected = self.selected_actions()
        if not selected and not (self.cb_project.isChecked() and self._project_provider):
            raise ValueError("请至少选择一个导出动作")
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        generated: list[Path] = []
        used_names: set[str] = set()
        json_formats = self._json_formats() if self.cb_json.isChecked() else []

        def report(done: int, total: int, label: str) -> bool:
            if progress is not None and not progress(done, total, label):
                raise ValueError("导出已取消")
            return True

        def export_project(done: int, total: int) -> int:
            if not self.cb_project.isChecked():
                return done
            if self._project_provider is None:
                raise ValueError("无法导出 SSTOOL：当前窗口未提供工程数据")
            manifest, sections = self._project_provider()
            project_path = root / "project.sstool"
            write_project(project_path, manifest, sections)
            generated.append(project_path)
            done += 1
            report(done, total, f"[{done}/{total}] SSTOOL 工程")
            return done

        if self.cb_separate.isChecked():
            per_action = 1 + (1 if self.cb_gif.isChecked() else 0) + len(json_formats)
            total = len(selected) * per_action + (1 if self.cb_project.isChecked() else 0)
            done = 0
            for action in selected:
                name = _safe_export_name(action.name, used_names)
                folder = root / name
                folder.mkdir(parents=True, exist_ok=True)
                frames = self._final_frames(action)
                # 图集按元素图块打包（普通帧去重复用；合成帧直接打包整块，不丢部件）
                atlas, full_records = self._build_packed_atlas([action])
                if atlas is None:
                    continue
                atlas_name = f"{name}.png"
                atlas_path = folder / atlas_name
                if not atlas.save(str(atlas_path), "PNG"):
                    raise IOError(f"无法写入 {atlas_path}")
                generated.append(atlas_path)
                done += 1
                report(done, total, f"[{done}/{total}] {name} · 图集 PNG")
                gif_name = f"{name}.gif" if self.cb_gif.isChecked() else None
                if gif_name:
                    gif_path = folder / gif_name
                    self._write_gif(gif_path, action, frames)
                    generated.append(gif_path)
                    done += 1
                    report(done, total, f"[{done}/{total}] {name} · GIF")
                if json_formats:
                    for json_path in self._write_animation_jsons(folder / name, atlas_name, atlas, [action], full_records, {action: gif_name}):
                        generated.append(json_path)
                        done += 1
                        report(done, total, f"[{done}/{total}] {name} · {json_path.name}")
            export_project(done, total)
            return generated

        # The project checkbox can be used on its own to export the complete
        # document even when no action cards are selected.
        if not selected:
            export_project(0, 1)
            return generated

        if self.rb_original.isChecked():
            atlas, records = self._build_original_atlas(selected)
        else:
            # 重构打包：普通帧去重复用元素图块；合成帧直接打包整块，
            # 因此所有 JSON 格式（含 TP/Aseprite）的合成帧都是一帧一块，画面一致。
            atlas, records = self._build_packed_atlas(selected)
        if atlas is None:
            raise ValueError("所选动作没有可导出的帧")

        gif_names: dict[Action, str] = {}
        gif_count = len(selected) if self.cb_gif.isChecked() else 0
        total = 2 + gif_count + len(json_formats) + (1 if self.cb_project.isChecked() else 0)
        done = 0
        report(done, total, "正在打包图集 …")
        atlas_path = root / "spritesheet.png"
        if not atlas.save(str(atlas_path), "PNG"):
            raise IOError(f"无法写入 {atlas_path}")
        generated.append(atlas_path)
        done += 1
        report(done, total, "已写入 spritesheet.png")
        if self.cb_gif.isChecked():
            gif_used: set[str] = set()
            for action in selected:
                gif_name = f"{_safe_export_name(action.name, gif_used)}.gif"
                gif_names[action] = gif_name
                gif_path = root / gif_name
                self._write_gif(gif_path, action, self._final_frames(action))
                generated.append(gif_path)
                done += 1
                report(done, total, f"[{done}/{total}] GIF · {gif_name}")
        if json_formats:
            for json_path in self._write_animation_jsons(root / "spritesheet", "spritesheet.png", atlas, selected, records, gif_names):
                generated.append(json_path)
                done += 1
                report(done, total, f"[{done}/{total}] JSON · {json_path.name}")
        export_project(done, total)
        return generated

    def _perform_export(self) -> None:
        if self.cb_gif.isChecked():
            try:
                from PIL import Image  # noqa: F401
            except ImportError:
                QMessageBox.critical(self, "无法导出", "GIF 导出需要 Pillow，请先安装 requirements.txt 中的依赖。")
                return
        directory = QFileDialog.getExistingDirectory(self, "选择导出文件夹")
        if not directory:
            return
        dlg = QProgressDialog("正在导出 …", "取消", 0, 100, self)
        dlg.setWindowTitle("导出进度")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(True)
        dlg.setAutoReset(True)
        dlg.show()

        def on_progress(value: int, total: int, label: str) -> bool:
            dlg.setLabelText(label)
            dlg.setMaximum(max(total, 1))
            dlg.setValue(value)
            QApplication.processEvents()
            return not dlg.wasCanceled()

        try:
            generated = self._export_to_directory(directory, progress=on_progress)
        except (ValueError, IOError, OSError) as exc:
            dlg.close()
            if str(exc) != "导出已取消":
                QMessageBox.warning(self, "导出失败", str(exc))
            return
        dlg.close()
        QMessageBox.information(self, "导出完成", f"已生成 {len(generated)} 个文件。\n{directory}")
        self.accept()

    def _stop_players(self) -> None:
        for player in self._players:
            player.stop()

    def done(self, result: int) -> None:
        """所有关闭路径的底层入口，确保动画定时器一定停止。"""
        self._stop_players()
        super().done(result)

    def closeEvent(self, event) -> None:
        self._stop_players()
        super().closeEvent(event)

    def reject(self) -> None:
        self._stop_players()
        super().reject()

    def accept(self) -> None:
        self._stop_players()
        super().accept()

    def selected_actions(self) -> list:
        return [act for act, sw in self._switches.items() if sw.isChecked()]

    def export_mode(self) -> str:
        return "original" if self.rb_original.isChecked() else "repack"

    def export_json(self) -> bool:
        return self.cb_json.isChecked()

    def export_gif(self) -> bool:
        return self.cb_gif.isChecked()

    def export_separate(self) -> bool:
        return self.cb_separate.isChecked()


class ActionPreviewWidget(QWidget):
    """动作预览窗口：预览画布 + 浮动工具条 + 播放控制 + 帧缩略图 + 动画调节组。"""

    frame_changed = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(460)
        self._action: Action | None = None
        self._actions: list[Action] = []
        self._source_pixmap: QPixmap | None = None
        self._frame_index = 0
        self._playing = False
        self._direction = 1  # 往返播放方向
        self._timer = QTimer(self)
        self._timer.setInterval(100)  # 默认 10 fps
        self._timer.timeout.connect(self._tick)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 预览区（画布 + 浮动工具条）
        self.canvas = PreviewCanvas(self)
        self.toolbar = PreviewFloatingBar(self.canvas)
        self.canvas.set_toolbar(self.toolbar)
        self.toolbar.zoom_in_requested.connect(self.canvas.zoom_in)
        self.toolbar.zoom_out_requested.connect(self.canvas.zoom_out)
        self.toolbar.loop_changed.connect(self._on_loop_changed)
        self.toolbar.anchor_toggled.connect(self._on_anchor_toggled)
        self.toolbar.bg_changed.connect(self._on_bg_changed)

        # 预览画布与下方控制区使用垂直分栏，用户可拖动横向分隔线调整高度。
        lower = QWidget()
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(4)
        self.preview_splitter = QSplitter(Qt.Orientation.Vertical)
        self.preview_splitter.setHandleWidth(6)
        self.preview_splitter.setStyleSheet(
            "QSplitter::handle { background: #3d3d3d; }"
            "QSplitter::handle:hover { background: #E91E63; }"
        )
        self.preview_splitter.addWidget(self.canvas)
        self.preview_splitter.addWidget(lower)
        self.preview_splitter.setStretchFactor(0, 1)
        self.preview_splitter.setStretchFactor(1, 0)
        self.preview_splitter.setSizes([360, 420])
        layout.addWidget(self.preview_splitter, 1)

        # 控制栏：播放/暂停 + 进度条 + 帧序号（带背景）
        ctrl_widget = QWidget()
        ctrl_widget.setStyleSheet("background-color: #1a1a1a; border: none; border-radius: 4px;")
        ctrl = QHBoxLayout(ctrl_widget)
        ctrl.setContentsMargins(6, 4, 6, 4)
        ctrl.setSpacing(6)

        self.btn_play = QPushButton()
        self.btn_play.setFixedSize(24, 24)
        self.btn_play.setIcon(make_tool_icon("play", 16))
        self.btn_play.setIconSize(QSize(16, 16))
        self.btn_play.setToolTip("播放/暂停")
        self.btn_play.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 3px; }"
            "QPushButton:hover { background: rgba(233,30,99,0.3); }"
        )
        self.btn_play.clicked.connect(self.toggle_play)
        ctrl.addWidget(self.btn_play)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setValue(0)
        self.slider.setFixedHeight(10)
        self.slider.setStyleSheet(
            "QSlider::groove:horizontal { height: 1px; background: #3d3d3d; border-radius: 1px;}"
            "QSlider::handle:horizontal { width: 6px; height: 6px; margin: -2px 0; background: #E91E63; border-radius: 3px;}"
            "QSlider::handle:horizontal:hover { background: #F06292; }"
        )
        self.slider.valueChanged.connect(self._on_slider_changed)
        ctrl.addWidget(self.slider, 1)

        self.lbl_frame = QLabel("0/0")
        self.lbl_frame.setStyleSheet("color: #bdbdbd; font-size: 11px; background: transparent; border: none;")
        self.lbl_frame.setMinimumWidth(40)
        ctrl.addWidget(self.lbl_frame)

        lower_layout.addWidget(ctrl_widget)

        # 帧缩略图
        self.thumb_list = QListWidget()
        self.thumb_list.setFixedHeight(56)
        self.thumb_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.thumb_list.setIconSize(QSize(28, 28))
        self.thumb_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.thumb_list.setMovement(QListWidget.Movement.Static)
        self.thumb_list.setFlow(QListWidget.Flow.LeftToRight)
        self.thumb_list.setWrapping(False)
        self.thumb_list.setSpacing(2)
        self.thumb_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.thumb_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.thumb_list.setStyleSheet(
            "QListWidget { background: #1a1a1a; border: none; border-radius: 4px; padding: 2px; }"
            "QListWidget::item { width: 40px; height: 36px; margin: 2px; border: 1px solid #555555; border-radius: 3px; background: transparent; }"
            "QListWidget::item:selected { background: rgba(233,30,99,0.25); border: 1px solid #E91E63; }"
        )
        self.thumb_list.currentRowChanged.connect(self._on_thumb_changed)
        lower_layout.addWidget(self.thumb_list)

        # 动画调节组（可滚动）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.adjust_panel = AnimAdjustPanel()
        self.adjust_panel.set_canvas(self.canvas)
        # 预览画布 ↔ 帧设置面板：变换模式按钮、拖转/拖四边形互相同步
        self.canvas.transform_mode_changed.connect(self.adjust_panel._on_canvas_mode_changed)
        self.canvas.transform_rotated.connect(self.adjust_panel._apply_rotation)
        self.canvas.transform_quadded.connect(self.adjust_panel._apply_quad)
        self.canvas.transform_pivot_moved.connect(self.adjust_panel._apply_pivot)
        self.adjust_panel.changed.connect(self._on_adjust_changed)
        scroll.setWidget(self.adjust_panel)
        lower_layout.addWidget(scroll, 1)

        # 原图信息栏
        self.lbl_source_info = QLabel("未加载原图")
        self.lbl_source_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_source_info.setStyleSheet(
            "QLabel { color: #888; font-size: 10px; padding: 3px 4px; "
            "background: #1a1a1a; border: 1px solid #333; border-radius: 3px; }"
        )
        lower_layout.addWidget(self.lbl_source_info)

        # 导出按钮
        self.btn_export = QPushButton("导出")
        self.btn_export.setFixedHeight(36)
        self.btn_export.setStyleSheet(
            "QPushButton { background: #E91E63; color: white; border: none; border-radius: 4px; font-size: 13px; }"
            "QPushButton:hover { background: #F06292; }"
        )
        self.btn_export.clicked.connect(self._on_export_clicked)
        lower_layout.addWidget(self.btn_export)

        self._square_preview = True
        QTimer.singleShot(0, self._apply_default_square)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._action is None:
            QTimer.singleShot(0, self._apply_default_square)

    def _apply_default_square(self):
        if self._action is not None:
            return
        # 底部预留：原图信息栏 + 导出按钮 + spacing，避免挤压底部控件
        bottom_reserve = 90
        side = max(160, min(self.width() - 8, self.height() - bottom_reserve))
        self.canvas.setMinimumHeight(side)
        self.canvas.setMaximumHeight(side)

    # ---- 对外接口 ----
    def set_actions(self, actions: list[Action]) -> None:
        """提供动作列表引用，用于帧设置的数据复用。"""
        self._actions = actions
        panel = getattr(self, "adjust_panel", None)
        if panel is not None:
            panel.set_actions(actions)

    def set_source_pixmap(self, pm: QPixmap | None) -> None:
        """提供原始大图引用，供导出弹窗的原图预览使用，并更新原图信息栏。"""
        self._source_pixmap = pm
        if not hasattr(self, "lbl_source_info"):
            return
        if pm is not None and not pm.isNull():
            w, h = pm.width(), pm.height()
            pixels = w * h
            if pixels >= 1_000_000:
                pixel_text = f"{pixels / 1_000_000:.1f}MP"
            elif pixels >= 1_000:
                pixel_text = f"{pixels / 1_000:.0f}K"
            else:
                pixel_text = f"{pixels}"
            mem_bytes = pixels * 4  # RGBA 估算
            if mem_bytes >= 1024 * 1024:
                mem_text = f"{mem_bytes / (1024 * 1024):.1f}MB"
            else:
                mem_text = f"{mem_bytes / 1024:.0f}KB"
            self.lbl_source_info.setText(
                f"原图：{w}×{h} px · {pixel_text} · 约{mem_text}"
            )
        else:
            self.lbl_source_info.setText("未加载原图")

    def set_action(self, action: Action | None):
        # Switching actions must not leave the previous playback timer running.
        self._timer.stop()
        self._action = action
        self._frame_index = 0
        self._playing = False
        self.btn_play.setIcon(make_tool_icon("play", 16))
        self.canvas.set_action(action)
        if action is None:
            QTimer.singleShot(0, self._apply_default_square)
        else:
            # 进入动作预览后由垂直分栏控制高度，不沿用无动作状态的方形限制。
            self.canvas.setMinimumHeight(100)
            self.canvas.setMaximumHeight(16777215)
        self.adjust_panel.set_action(action)
        self.adjust_panel.set_current_frame(0)
        if action and action.frames:
            self._timer.setInterval(int(1000 / max(1, action.fps)))
            self.slider.setRange(0, len(action.frames) - 1)
            self.slider.setValue(0)
            self._update_frame_label()
            self._populate_thumbs()
            self.toolbar.sync_action(action)
        else:
            self.slider.setRange(0, 0)
            self.lbl_frame.setText("0/0")
            self.thumb_list.clear()

    def _populate_thumbs(self):
        self.thumb_list.clear()
        if not self._action:
            return
        for i in range(len(self._action.frames)):
            item = QListWidgetItem()
            pm = action_frame_pixmap(self._action, i)
            thumb = QPixmap(28, 28)
            thumb.fill(Qt.GlobalColor.transparent)
            painter = QPainter(thumb)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            scaled = pm.scaled(
                24, 24,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(
                (28 - scaled.width()) // 2,
                (28 - scaled.height()) // 2,
                scaled,
            )
            painter.end()
            item.setIcon(QIcon(thumb))
            item.setText(str(i + 1))
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
            self.thumb_list.addItem(item)
        self.thumb_list.setCurrentRow(0)

    def _refresh_thumbs(self):
        """重建缩略图但保持当前选中帧（旋转/镜像变化后同步）。"""
        if not self._action:
            return
        row = self.thumb_list.currentRow()
        self._populate_thumbs()
        if row >= 0:
            self.thumb_list.setCurrentRow(min(row, len(self._action.frames) - 1))

    def _update_frame_label(self):
        if self._action and self._action.frames:
            self.lbl_frame.setText(f"{self._frame_index + 1}/{len(self._action.frames)}")
        else:
            self.lbl_frame.setText("0/0")

    def toggle_play(self):
        if not self._action or not self._action.frames:
            return
        self._playing = not self._playing
        self.canvas.set_playing(self._playing)
        if self._playing:
            self.btn_play.setIcon(make_tool_icon("pause", 16))
            self._timer.start()
        else:
            self.btn_play.setIcon(make_tool_icon("play", 16))
            self._timer.stop()

    def _tick(self):
        if not self._action or not self._action.frames:
            return
        n = len(self._action.frames)
        if self._action.loop_mode == Action.LOOP:
            self._frame_index = (self._frame_index + 1) % n
        elif self._action.loop_mode == Action.ONCE:
            if self._frame_index < n - 1:
                self._frame_index += 1
            else:
                self.toggle_play()
                return
        elif self._action.loop_mode == Action.PINGPONG:
            if n == 1:
                self._frame_index = 0
                self._direction = 1
                self._sync_frame()
                return
            self._frame_index += self._direction
            if self._frame_index >= n:
                self._frame_index = n - 2
                self._direction = -1
            elif self._frame_index < 0:
                self._frame_index = 1
                self._direction = 1
        self._sync_frame()

    def _sync_frame(self):
        self.slider.blockSignals(True)
        self.slider.setValue(self._frame_index)
        self.slider.blockSignals(False)
        self.thumb_list.blockSignals(True)
        self.thumb_list.setCurrentRow(self._frame_index)
        self.thumb_list.blockSignals(False)
        self.canvas.set_frame(self._frame_index)
        self.adjust_panel.set_current_frame(self._frame_index)
        self._update_frame_label()
        self.frame_changed.emit(self._frame_index)

    def _on_slider_changed(self, val: int):
        self._frame_index = val
        self._sync_frame()

    def _on_thumb_changed(self, row: int):
        if row >= 0:
            self._frame_index = row
            self._sync_frame()

    # ---- 浮动工具条槽 ----
    def _on_loop_changed(self, mode: str):
        if self._action:
            self._action.loop_mode = mode

    def _on_anchor_toggled(self, checked: bool):
        if self._action:
            self._action.show_anchor = checked
            self.canvas.update()

    def _on_bg_changed(self, mode: str):
        if self._action:
            self._action.bg_mode = mode
            self.canvas.update()

    def _on_adjust_changed(self):
        if self._action:
            # 勾选“数据复用”的动作共享帧速度与四周扩展；未勾选动作
            # 保留自己的设置。当前动作是本次编辑的唯一数据源。
            if self._action.reuse_frame_settings:
                for action in self._actions:
                    if action is not self._action and action.reuse_frame_settings:
                        action.padding = self._action.padding
                        action.fps = self._action.fps
            self._timer.setInterval(int(1000 / max(1, self._action.fps)))
            self.canvas._update_scroll_timer()
            if self.canvas._fit and not self.canvas._drag_kind:
                # 拖动变换手柄期间保持视图缩放稳定，避免每帧重新自适应导致跳动
                self.canvas._apply_fit()
            self.canvas.update()
            self.adjust_panel._refresh_canvas_label()
            # 旋转/镜像变化后同步帧缩略图
            if getattr(self.adjust_panel, "_transform_changed", False):
                self.adjust_panel._transform_changed = False
                self._refresh_thumbs()

    def _on_export_clicked(self) -> None:
        dlg = ExportDialog(
            self._actions,
            self._source_pixmap,
            # 工程数据提供者由 MainWindow 注入（self._project_manifest 本类没有定义）
            project_provider=getattr(self, "_project_manifest", None),
            parent=self,
        )
        dlg.exec()


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
    selection_changed = pyqtSignal()
    frames_updated = pyqtSignal()
    json_dropped = pyqtSignal(str)   # 拖入 JSON 工程时转发给主窗口处理
    project_dropped = pyqtSignal(str)  # 拖入 SSTOOL 工程时转发给主窗口处理

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
        thread = _FrameDetectThread(image.copy(), self)
        self._detect_thread = thread
        self._detect_threads.add(thread)

        def apply_result(frames: list[tuple[int, int, int, int]]) -> None:
            if generation != self._detect_generation:
                return
            shifted = [(x, y + y_offset, w, h) for x, y, w, h in frames]
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

    # ---- 帧复用（按钮模式 + Alt+点击 临时模式）----

    def set_reuse_mode(self, enabled: bool) -> None:
        """切换复用按钮的保持模式；按钮/Alt 任一激活时都处于复用模式。"""
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

    def _on_bg_changed(self, mode: str):
        self._bg_mode = mode
        self.canvas.update()

    def _on_image_loaded(self, pm: QPixmap):
        # 由主窗口决定是首次载入还是追加，避免覆盖已有动作。
        self.image_loaded.emit(pm)

    def refresh(self):
        """刷新画布和标尺。"""
        self.canvas.update()
        self._update_rulers()


# 主窗口
class UpdateDownloadDialog(QDialog):
    """自动更新下载对话框（深色风格 + 实时进度 + 取消）。"""

    download_ok = pyqtSignal(str)  # 安装包本地路径

    def __init__(self, version: str, url: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("正在更新")
        self.setModal(True)
        self.setFixedWidth(420)
        self._url = url
        self._thread: UpdateDownloadThread | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel(f"正在下载 v{version} ...")
        title.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        self.lbl_status = QLabel("连接服务器 ...")
        self.lbl_status.setStyleSheet("color: #9a9a9a; font-size: 12px;")
        layout.addWidget(self.lbl_status)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setStyleSheet(
            "QProgressBar { background: #2b2b2b; border: 1px solid #3d3d3d; border-radius: 4px;"
            " height: 14px; text-align: center; color: #ffffff; font-size: 10px; }"
            "QProgressBar::chunk { background: #E91E63; border-radius: 3px; }"
        )
        layout.addWidget(self.progress)

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self._on_cancel)
        layout.addWidget(self.btn_cancel, 0, Qt.AlignmentFlag.AlignRight)

        self._start()

    def _start(self):
        import tempfile
        target_dir = os.path.join(tempfile.gettempdir(), "SpriteSheetToolUpdate")
        self._thread = UpdateDownloadThread(self._url, target_dir, self)
        self._thread.progress.connect(self._on_progress)
        self._thread.download_finished.connect(self._on_finished)
        self._thread.download_failed.connect(self._on_failed)
        self._thread.finished.connect(self._on_thread_done)
        self._thread.start()

    def _on_progress(self, done: int, total: int):
        if total > 0:
            percent = int(done * 100 / total)
            self.progress.setValue(percent)
            self.lbl_status.setText(
                f"已下载 {done / 1024 / 1024:.1f} MB / {total / 1024 / 1024:.1f} MB"
            )
        else:
            self.lbl_status.setText(f"已下载 {done / 1024 / 1024:.1f} MB")

    def _on_finished(self, path: str):
        self.lbl_status.setText("下载完成，即将安装 ...")
        self.progress.setValue(100)
        self.btn_cancel.setEnabled(False)
        QTimer.singleShot(300, lambda: (self.accept(), self.download_ok.emit(path)))

    def _on_failed(self, reason: str):
        QMessageBox.warning(self, "下载失败", f"下载更新失败：\n{reason}")
        self.reject()

    def _on_cancel(self):
        if self._thread is not None:
            self._thread.cancel()
        self.reject()

    def _on_thread_done(self):
        self._thread = None


class _TransformHistoryCommand(QUndoCommand):
    """Undo command for per-frame free-transform state."""

    def __init__(self, owner, before, after) -> None:
        super().__init__("变换")
        self._owner = owner
        self.before = before
        self.after = after
        self._initial_redo = True

    def redo(self) -> None:
        if self._initial_redo:
            self._initial_redo = False
            return
        self._owner._restore_transform_state(self.after)

    def undo(self) -> None:
        self._owner._restore_transform_state(self.before)


class MainWindow(QMainWindow):
    """主窗口：系统标题栏 + 菜单栏 + Logo名称栏 + 左侧边栏 + 原图区 + 右侧预览 + 状态栏。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{__app_name__} v{__version__}")
        # Match the supplied reference layout.  Keep this as both the default
        # and the minimum so the adjustment panel is never vertically clipped.
        self.setMinimumSize(1000, 1000)
        self.resize(1000, 1000)
        self.setAcceptDrops(True)   # 支持拖入图片或 JSON 工程
        self.actions: list[Action] = []
        self._undo_stack = QUndoStack(self)
        self._transform_history_state = None
        self._transform_history_pending = None
        self._restoring_transform_history = False
        self._project_path: Path | None = None
        self._project_dirty = False
        self._build_ui()
        self._build_menu()
        self._transform_history_state = self._capture_transform_state()

    def _capture_transform_state(self):
        """Capture only frame transform fields, keeping undo focused and cheap."""
        state = []
        for action in self.actions:
            state.append((
                id(action), action,
                tuple(sorted((int(k), int(v)) for k, v in action.frame_rotations.items())),
                tuple(sorted(int(k) for k in action.frame_mirror_h)),
                tuple(sorted(int(k) for k in action.frame_mirror_v)),
                tuple(sorted((int(k), tuple(float(x) for x in v))
                             for k, v in action.frame_rotation_pivots.items())),
                tuple(sorted((int(k), tuple(float(x) for x in v))
                             for k, v in action.frame_quads.items())),
                tuple(sorted((int(k), tuple(sorted(str(x) for x in v)))
                             for k, v in action.frame_transform_modes.items())),
            ))
        return tuple(state)

    def _restore_transform_state(self, state) -> None:
        if self._restoring_transform_history:
            return
        self._restoring_transform_history = True
        try:
            restored_ids = set()
            for _, action, rotations, mirror_h, mirror_v, pivots, quads, modes in state:
                if action not in self.actions:
                    continue
                restored_ids.add(id(action))
                action.frame_rotations = dict(rotations)
                action.frame_mirror_h = set(mirror_h)
                action.frame_mirror_v = set(mirror_v)
                action.frame_rotation_pivots = dict(pivots)
                action.frame_quads = dict(quads)
                action.frame_transform_modes = {k: set(v) for k, v in modes}
                action.invalidate_transforms()
            # A snapshot may predate a newly created action. Undoing to it
            # must also clear transforms on actions absent from that snapshot.
            for action in self.actions:
                if id(action) in restored_ids:
                    continue
                action.frame_rotations.clear()
                action.frame_mirror_h.clear()
                action.frame_mirror_v.clear()
                action.frame_rotation_pivots.clear()
                action.frame_quads.clear()
                action.frame_transform_modes.clear()
                action.invalidate_transforms()
            self._transform_history_state = self._capture_transform_state()
            if getattr(self, "preview", None) is not None:
                self.preview.adjust_panel._sync_transform_widgets()
                self.preview.adjust_panel._refresh_canvas_label()
                self.preview.canvas.update()
                self.preview._refresh_thumbs()
                self.preview.adjust_panel._transform_changed = False
        finally:
            self._restoring_transform_history = False

    def _begin_transform_history(self) -> None:
        if not self._restoring_transform_history and self._transform_history_pending is None:
            self._transform_history_pending = self._capture_transform_state()

    def _finish_transform_history(self) -> None:
        if self._transform_history_pending is None:
            return
        before = self._transform_history_pending
        self._transform_history_pending = None
        after = self._capture_transform_state()
        if before != after and not self._restoring_transform_history:
            self._undo_stack.push(_TransformHistoryCommand(self, before, after))
        self._transform_history_state = after

    def _on_transform_history_changed(self) -> None:
        if self._restoring_transform_history:
            return
        current = self._capture_transform_state()
        if self._transform_history_pending is not None:
            # Continuous drag is committed once on mouse release.
            return
        before = self._transform_history_state
        if before is not None and before != current:
            self._undo_stack.push(_TransformHistoryCommand(self, before, current))
        self._transform_history_state = current

    def _reset_transform_history(self) -> None:
        self._undo_stack.clear()
        self._transform_history_pending = None
        self._transform_history_state = self._capture_transform_state()

    @staticmethod
    def _tuple2(value, default=(0, 0)) -> tuple:
        value = value if isinstance(value, (list, tuple)) else default
        return tuple(value[:2]) if len(value) >= 2 else tuple(default)

    def _project_manifest(self) -> tuple[dict, dict[str, bytes]]:
        """Collect the complete in-memory document into manifest + PNG sections."""
        sections: dict[str, bytes] = {}
        viewer = self.image_viewer

        def add_pixmap(name: str, pm: QPixmap | None) -> str | None:
            payload = _pixmap_bytes(pm)
            if not payload:
                return None
            sections[name] = payload
            return name

        combined_id = add_pixmap("images/combined.png", viewer._pixmap)
        source_ids = []
        for index, pm in enumerate(viewer._source_images):
            name = add_pixmap(f"images/source_{index:04d}.png", pm)
            if name:
                source_ids.append(name)

        actions = []
        for ai, action in enumerate(self.actions):
            frame_ids = []
            for fi, frame in enumerate(action.frames):
                name = add_pixmap(f"actions/{ai:04d}/frame_{fi:04d}.png", frame)
                frame_ids.append(name)
            parts = []
            for frame_parts in action.frame_parts:
                parts.append([
                    [list(rect), int(rx), int(ry), int(ox), int(oy)]
                    for rect, rx, ry, ox, oy in frame_parts
                ])
            actions.append({
                "name": action.name, "color": action.color,
                "selected_indices": list(action.selected_indices),
                "composite_groups": [list(group) for group in action.composite_groups],
                "frame_layouts": None if action.frame_layouts is None else [
                    [[int(i), int(x), int(y)] for i, x, y in layout]
                    for layout in action.frame_layouts
                ],
                "frame_layouts_base": action.frame_layouts_base,
                "frame_rects": [list(rect) for rect in action.frame_rects],
                "frame_parts": parts, "frame_sections": frame_ids,
                "fps": action.fps, "loop_mode": action.loop_mode,
                "padding": action.padding, "ground_enabled": action.ground_enabled,
                "ground_offset": action.ground_offset,
                "reuse_frame_settings": action.reuse_frame_settings,
                "show_anchor": action.show_anchor, "anchor": list(action.anchor),
                "frame_offsets": {str(k): list(v) for k, v in action.frame_offsets.items()},
                "frame_rotations": {str(k): v for k, v in action.frame_rotations.items()},
                "frame_mirror_h": sorted(action.frame_mirror_h),
                "frame_mirror_v": sorted(action.frame_mirror_v),
                "frame_rotation_pivots": {str(k): list(v) for k, v in action.frame_rotation_pivots.items()},
                "frame_quads": {str(k): list(v) for k, v in action.frame_quads.items()},
                "frame_transform_modes": {str(k): sorted(v) for k, v in action.frame_transform_modes.items()},
                "ref_frame": action.ref_frame, "ref_opacity": action.ref_opacity,
                "frame_opacity": action.frame_opacity, "bg_mode": action.bg_mode,
                "action_type": action.action_type, "move_direction": action.move_direction,
                "move_speed": action.move_speed,
            })

        thumb = None
        current_action_index = self.actions.index(viewer.current_action) if viewer.current_action in self.actions else -1
        if viewer.current_action and viewer.current_action.frames:
            index = min(self.preview._frame_index, len(viewer.current_action.frames) - 1)
            thumb = add_pixmap("thumbnail.png", action_frame_pixmap(viewer.current_action, index))
        if thumb is None:
            thumb = add_pixmap("thumbnail.png", viewer._pixmap)
        # Keep a small project-file badge inside the embedded thumbnail.  This
        # is useful to thumbnail-capable file browsers without changing the
        # application's own SpriteSheetTool icon.
        if thumb and "thumbnail.png" in sections:
            badge = QPixmap(str(_APP_ROOT / "工程图标.png"))
            preview_pm = _pixmap_from_bytes(sections["thumbnail.png"])
            if not badge.isNull() and not preview_pm.isNull():
                side = max(24, min(preview_pm.width(), preview_pm.height()) // 5)
                badge = badge.scaled(
                    side, side,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                painter = QPainter(preview_pm)
                painter.drawPixmap(
                    preview_pm.width() - badge.width() - 4,
                    preview_pm.height() - badge.height() - 4,
                    badge,
                )
                painter.end()
                sections["thumbnail.png"] = _pixmap_bytes(preview_pm)
        manifest = {
            "format": "spritesheet-tool-project", "version": 1,
            "app_version": __version__, "combined_image": combined_id,
            "source_images": source_ids, "thumbnail": thumb,
            "viewer": {
                "frames": [list(x) for x in viewer._frames],
                "frame_logical": {str(k): list(v) for k, v in viewer._frame_logical.items()},
                "zoom": viewer._zoom, "offset": [viewer._offset.x(), viewer._offset.y()],
                "bg_mode": viewer._bg_mode, "compose_mode": viewer.compose_mode,
                "manual_compose": viewer._manual_compose,
            },
            "preview": {
                "action_index": current_action_index,
                "frame_index": self.preview._frame_index,
                "canvas_zoom": self.preview.canvas._zoom,
                "canvas_offset": [self.preview.canvas._offset.x(), self.preview.canvas._offset.y()],
                "fit": self.preview.canvas._fit,
            },
            "actions": actions,
        }
        return manifest, sections

    def _set_project_title(self) -> None:
        name = self._project_path.name if self._project_path else "未命名工程"
        marker = " *" if self._project_dirty else ""
        self.setWindowTitle(f"{name}{marker} - {__app_name__} v{__version__}")

    def _mark_project_dirty(self) -> None:
        self._project_dirty = True
        self._set_project_title()

    def _save_project(self, path: str | Path | None = None) -> bool:
        target = Path(path) if path else self._project_path
        if target is None:
            return self._save_project_as()
        try:
            manifest, sections = self._project_manifest()
            write_project(target, manifest, sections)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存工程失败", str(exc))
            return False
        self._project_path = target
        self._project_dirty = False
        self._set_project_title()
        self.lbl_status.setText(f"工程已保存：{target.name}")
        return True

    def _save_project_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 SSTOOL 工程", str(self._project_path or "工程.sstool"),
            "SSTOOL 工程 (*.sstool)"
        )
        if not path:
            return False
        if not path.lower().endswith(".sstool"):
            path += ".sstool"
        return self._save_project(path)

    def _new_project(self) -> None:
        self._end_all_renames()
        self.actions.clear()
        self.action_list.clear()
        viewer = self.image_viewer
        viewer._detect_generation += 1
        viewer._pixmap = None
        viewer._source_images = []
        viewer._frames = []
        viewer._frame_logical = {}
        viewer.set_current_action(None)
        self.preview.set_action(None)
        self.preview.set_source_pixmap(None)
        self._project_path = None
        self._project_dirty = False
        self._reset_transform_history()
        self._set_project_title()
        self.lbl_status.setText("已新建空白工程")

    def _open_project_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开 SSTOOL 工程", "", "SSTOOL 工程 (*.sstool)")
        if path:
            self._open_project(path)

    def _open_project(self, path: str) -> bool:
        try:
            manifest, sections = read_project(path)
            self._apply_sstool_project(manifest, sections)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "打开工程失败", str(exc))
            return False
        self._project_path = Path(path)
        self._project_dirty = False
        self._set_project_title()
        self.lbl_status.setText(f"工程已打开：{self._project_path.name}")
        return True

    def _safe_open_project(self, path: str) -> None:
        """Safe slot for delayed drag/drop project loading."""
        try:
            self._open_project(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "打开工程失败", str(exc))

    def _apply_sstool_project(self, manifest: dict, sections: dict[str, bytes]) -> None:
        if manifest.get("format") != "spritesheet-tool-project":
            raise ValueError("不是有效的 SSTOOL 工程清单")
        viewer_state = manifest.get("viewer") or {}
        viewer = self.image_viewer
        viewer._detect_generation += 1
        combined = _pixmap_from_bytes(sections.get(manifest.get("combined_image", ""), b""))
        if combined.isNull():
            raise ValueError("工程缺少有效的原图数据")
        viewer._pixmap = combined
        viewer._source_images = [
            _pixmap_from_bytes(sections[name]) for name in manifest.get("source_images", []) if name in sections
        ]
        # Older projects may only contain the merged image.  Keep cropping
        # and re-export functional by treating that image as the sole source.
        if not viewer._source_images:
            viewer._source_images = [combined]
        viewer._frames = [tuple(map(int, x)) for x in viewer_state.get("frames", [])]
        viewer._frame_logical = {int(k): tuple(map(int, v)) for k, v in (viewer_state.get("frame_logical") or {}).items()}
        viewer._zoom = float(viewer_state.get("zoom", 1.0))
        viewer._offset = QPointF(*self._tuple2(viewer_state.get("offset"), (0, 0)))
        viewer._bg_mode = str(viewer_state.get("bg_mode", "checker"))
        viewer.compose_mode = bool(viewer_state.get("compose_mode", False))
        viewer._manual_compose = bool(viewer_state.get("manual_compose", False))

        self._end_all_renames()
        self.actions.clear()
        self.action_list.clear()
        for raw in manifest.get("actions", []):
            action = Action(str(raw.get("name", "未命名动作")), str(raw.get("color", "#E91E63")))
            for attr in ("fps", "loop_mode", "padding", "ground_enabled", "ground_offset",
                         "reuse_frame_settings", "show_anchor", "ref_frame", "ref_opacity",
                         "frame_opacity", "bg_mode", "action_type", "move_direction", "move_speed"):
                if attr in raw:
                    setattr(action, attr, raw[attr])
            action.selected_indices = [int(x) for x in raw.get("selected_indices", [])]
            action.composite_groups = [[int(x) for x in g] for g in raw.get("composite_groups", [])]
            layouts = raw.get("frame_layouts")
            action.frame_layouts = None if layouts is None else [
                [(int(i), int(x), int(y)) for i, x, y in layout] for layout in layouts
            ]
            action.frame_layouts_base = int(raw.get("frame_layouts_base", 0))
            action.frame_rects = [tuple(map(int, x)) for x in raw.get("frame_rects", [])]
            action.frame_parts = []
            for frame_parts in raw.get("frame_parts", []):
                parsed_parts = []
                for part in frame_parts:
                    if len(part) >= 5:
                        parsed_parts.append(
                            (tuple(map(int, part[0])), int(part[1]), int(part[2]), int(part[3]), int(part[4]))
                        )
                    elif len(part) >= 3:
                        # Projects written before logical origins were added.
                        rect = tuple(map(int, part[0]))
                        parsed_parts.append((rect, int(part[1]), int(part[2]), rect[0], rect[1]))
                action.frame_parts.append(parsed_parts)
            for fi, name in enumerate(raw.get("frame_sections", [])):
                frame = _pixmap_from_bytes(sections.get(name, b""))
                if frame.isNull():
                    raise ValueError(f"动作 {action.name} 缺少帧图像")
                action.frames.append(frame)
            action.anchor = tuple(float(x) for x in self._tuple2(raw.get("anchor"), (0.5, 0.5)))
            action.frame_offsets = {int(k): tuple(map(int, v)) for k, v in (raw.get("frame_offsets") or {}).items()}
            action.frame_rotations = {int(k): int(v) for k, v in (raw.get("frame_rotations") or {}).items()}
            action.frame_mirror_h = set(map(int, raw.get("frame_mirror_h", [])))
            action.frame_mirror_v = set(map(int, raw.get("frame_mirror_v", [])))
            action.frame_rotation_pivots = {int(k): tuple(map(float, v)) for k, v in (raw.get("frame_rotation_pivots") or {}).items()}
            action.frame_quads = {int(k): tuple(map(float, v)) for k, v in (raw.get("frame_quads") or {}).items()}
            action.frame_transform_modes = {int(k): set(v) for k, v in (raw.get("frame_transform_modes") or {}).items()}
            action.invalidate_transforms()
            self.actions.append(action)
            self._append_action_card(action)

        viewer.set_actions(self.actions)
        viewer.set_current_action(None)
        preview_state = manifest.get("preview") or {}
        action_index = int(preview_state.get("action_index", -1))
        action = self.actions[action_index] if 0 <= action_index < len(self.actions) else None
        if action is not None:
            viewer.set_current_action(action)
            if 0 <= action_index < self.action_list.count():
                self.action_list.blockSignals(True)
                self.action_list.setCurrentRow(action_index)
                self.action_list.blockSignals(False)
        self.preview.set_actions(self.actions)
        self.preview.set_source_pixmap(viewer._pixmap)
        self.preview.set_action(action)
        frame_index = int(preview_state.get("frame_index", 0))
        if action and action.frames:
            self.preview._frame_index = max(0, min(frame_index, len(action.frames) - 1))
            self.preview._sync_frame()
        self.preview.canvas._zoom = float(preview_state.get("canvas_zoom", self.preview.canvas._zoom))
        self.preview.canvas._offset = QPointF(*self._tuple2(preview_state.get("canvas_offset"), (0, 0)))
        self.preview.canvas._fit = bool(preview_state.get("fit", True))
        viewer.canvas.update()
        viewer._update_rulers()
        self._reset_transform_history()
        self._project_path = None
        self._project_dirty = False

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Logo 名称栏
        logo_bar = QWidget()
        logo_bar.setFixedHeight(64)
        logo_bar.setStyleSheet("background-color: #1a1a1a; border-bottom: 1px solid #333;")
        logo_layout = QHBoxLayout(logo_bar)
        logo_layout.setContentsMargins(8, 5, 8, 5)
        logo_layout.setSpacing(8)
        logo_label = QLabel()
        logo_label.setFixedSize(54, 54)
        software_icon = QIcon(str(_APP_ROOT / "SpriteSheetTool.ico"))
        real_logo = software_icon.pixmap(50, 50)
        if not real_logo.isNull():
            logo_label.setPixmap(real_logo)
        else:
            real_logo = QPixmap(str(_APP_ROOT / "SpriteSheetTool.png"))
            if not real_logo.isNull():
                logo_label.setPixmap(real_logo.scaled(
                    50,
                    50,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
            else:
                logo_label.setPixmap(make_logo_pixmap(50))
        # logo 白色描边（加粗）
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_label.setStyleSheet("border: 2px solid #ffffff; border-radius: 10px;")
        logo_layout.addWidget(logo_label)
        # 软件名 + 版本号：同一行，版本号在名字右边；整行底部与 logo 下边缘齐平
        name_row = QWidget()
        name_row.setFixedHeight(54)
        row = QHBoxLayout(name_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        name_label = QLabel(
            "<span style='color:#ffffff;'>SpriteSheet</span>"
            "<span style='color:#E91E63;'>Tool</span>"
        )
        name_label.setStyleSheet("background: transparent; border: none; font-size: 22px; font-weight: bold;")
        version_label = QLabel(f"v{__version__}")
        version_label.setStyleSheet("background: transparent; border: none; color: #9a9a9a; font-size: 11px;")
        row.addWidget(name_label, 0, Qt.AlignmentFlag.AlignBottom)
        row.addWidget(version_label, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch()
        logo_layout.addWidget(name_row)
        logo_layout.addStretch()
        main_layout.addWidget(logo_bar)

        # 内容区：侧边栏 | QSplitter(原图区 | 右侧预览)
        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)

        # 左侧边栏
        sidebar = QWidget()
        sidebar.setFixedWidth(200)
        sidebar.setStyleSheet("background-color: #1a1a1a; border-right: 1px solid #333;")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(4, 4, 4, 4)
        sidebar_layout.setSpacing(4)

        self.btn_add_action = QPushButton("+ 添加图片")
        self.btn_add_action.setFixedHeight(32)
        self.btn_add_action.setStyleSheet(
            "QPushButton { background: #E91E63; color: white; border: none; border-radius: 4px; font-size: 12px; }"
            "QPushButton:hover { background: #F06292; }"
        )
        self.btn_add_action.clicked.connect(self._on_add_action_clicked)
        sidebar_layout.addWidget(self.btn_add_action)

        self.action_list = QListWidget()
        self.action_list.setItemDelegate(ActionItemDelegate(self.action_list))
        self.action_list.setStyleSheet(
            "QListWidget { background: #141414; border: none; border-radius: 4px; }"
            "QListWidget::item { height: 34px; padding: 0 4px; }"
        )
        self.action_list.currentRowChanged.connect(self._on_action_selected)
        sidebar_layout.addWidget(self.action_list, 1)

        content.addWidget(sidebar)

        # 右侧：QSplitter(原图区 | 预览区)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(4)
        self.splitter.setStyleSheet("QSplitter::handle { background: #333; } QSplitter::handle:hover { background: #E91E63; }")

        self.image_viewer = ImageViewer()
        self.image_viewer.image_loaded.connect(self._on_image_loaded)
        self.image_viewer.json_dropped.connect(self._import_project)
        self.image_viewer.project_dropped.connect(self._safe_open_project)
        self.image_viewer.selection_changed.connect(self._on_selection_changed)
        self.image_viewer.selection_changed.connect(self._mark_project_dirty)
        self.image_viewer.frames_updated.connect(self._on_frames_updated)
        self.splitter.addWidget(self.image_viewer)

        self.preview = ActionPreviewWidget()
        self.preview.set_actions(self.actions)
        # 导出工程（.sstool）需要完整文档数据；provider 由主窗口注入
        self.preview._project_manifest = self._project_manifest
        self.preview.adjust_panel.changed.connect(self._on_transform_history_changed)
        self.preview.adjust_panel.changed.connect(self._mark_project_dirty)
        self.preview.canvas.transform_edit_started.connect(self._begin_transform_history)
        self.preview.canvas.transform_edit_finished.connect(self._finish_transform_history)
        self.preview.toolbar.loop_changed.connect(lambda _mode: self._mark_project_dirty())
        self.preview.toolbar.anchor_toggled.connect(lambda _checked: self._mark_project_dirty())
        self.preview.toolbar.bg_changed.connect(lambda _mode: self._mark_project_dirty())
        self.image_viewer._toolbar.bg_changed.connect(lambda _mode: self._mark_project_dirty())
        self.image_viewer._toolbar.compose_toggled.connect(lambda _checked: self._mark_project_dirty())
        self.splitter.addWidget(self.preview)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([600, 320])

        content.addWidget(self.splitter, 1)
        main_layout.addLayout(content, 1)

        # 状态栏
        self.lbl_status = QLabel(f"{__app_name__} v{__version__}  |  重构版")
        self.lbl_status.setStyleSheet("color: #888; font-size: 11px; padding: 2px 8px; background: #1a1a1a; border-top: 1px solid #333;")
        main_layout.addWidget(self.lbl_status)

    def _build_menu(self) -> None:
        menubar = self.menuBar()
        menubar.setStyleSheet("QMenuBar { background: #1a1a1a; color: #ddd; font-size: 12px; } QMenuBar::item:selected { background: #E91E63; }")
        edit_menu = menubar.addMenu("编辑")
        act_undo = QAction("撤销", self)
        act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        act_undo.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        act_undo.triggered.connect(self._undo_stack.undo)
        edit_menu.addAction(act_undo)
        file_menu = menubar.addMenu("文件")
        act_new = QAction("新建工程", self)
        act_new.triggered.connect(self._new_project)
        file_menu.addAction(act_new)
        act_open_project = QAction("打开工程…", self)
        act_open_project.setShortcut(QKeySequence.StandardKey.Open)
        act_open_project.triggered.connect(self._open_project_dialog)
        file_menu.addAction(act_open_project)
        act_save = QAction("保存工程", self)
        act_save.setShortcut(QKeySequence.StandardKey.Save)
        act_save.triggered.connect(lambda: self._save_project())
        file_menu.addAction(act_save)
        act_save_as = QAction("工程另存为…", self)
        act_save_as.triggered.connect(self._save_project_as)
        file_menu.addAction(act_save_as)
        file_menu.addSeparator()
        act_open = QAction("添加图片", self)
        act_open.triggered.connect(self._open_image)
        file_menu.addAction(act_open)
        act_import = QAction("导入 JSON 工程…", self)
        act_import.setShortcut("Ctrl+I")
        act_import.triggered.connect(self._import_json_dialog)
        file_menu.addAction(act_import)
        file_menu.addSeparator()
        act_check_update = QAction("检查更新", self)
        act_check_update.triggered.connect(self._check_update_manual)
        file_menu.addAction(act_check_update)
        file_menu.addSeparator()
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        # 启动后延迟自动检查一次更新（不阻塞界面；失败时静默）
        self._update_thread: UpdateCheckThread | None = None
        self._update_checked = False
        QTimer.singleShot(2500, self._check_update_auto)

    def _check_update_auto(self):
        """启动自动检查：仅检查一次，无更新/失败都静默，不打扰用户。"""
        if self._update_checked:
            return
        self._start_update_check(notify_no_update=False)

    def _check_update_manual(self):
        """菜单手动检查：始终弹窗反馈结果。"""
        self._start_update_check(notify_no_update=True)

    def _start_update_check(self, notify_no_update: bool):
        if getattr(self, "_update_thread", None) is not None and self._update_thread.isRunning():
            return  # 上一次检查还没结束
        self._update_checked = True
        self._notify_no_update = notify_no_update
        self._update_thread = UpdateCheckThread(__version__, UPDATE_URL, self)
        self._update_thread.result_ready.connect(self._on_update_result)
        self._update_thread.finished.connect(self._on_update_thread_finished)
        self._update_thread.start()

    def _on_update_thread_finished(self):
        self._update_thread = None

    def _on_update_result(self, info: dict | None):
        if not info:
            if self._notify_no_update:
                QMessageBox.information(self, "检查更新", f"当前已是最新版本 v{__version__}")
            return
        status = info.get("status", "")
        if status == "update":
            self._show_update_dialog(info)
        elif self._notify_no_update:
            if status == "latest":
                QMessageBox.information(
                    self, "检查更新",
                    f"当前已是最新版本 v{__version__}"
                    f"（服务器最新 v{info.get('version', '')}）",
                )
            elif status == "no_installer":
                QMessageBox.warning(
                    self, "检查更新",
                    "服务器目录里没有找到安装包。\n\n"
                    "请先把 SpriteSheetTool_v.X.Y.Z.exe 上传到：\n"
                    f"{info.get('url', '')}\n\n"
                    "（服务器需开启目录索引）",
                )
            elif status == "error":
                QMessageBox.warning(
                    self, "检查更新",
                    f"检查更新失败：\n{info.get('reason', '网络异常')}",
                )

    def _show_update_dialog(self, info: dict):
        version = info.get("version", "")
        url = info.get("url", "")
        notes = info.get("notes", "")
        msg = f"发现新版本 <b>v{version}</b>（当前 v{__version__}）"
        if notes:
            msg += f"<br><br>更新说明：<br>{notes}"
        box = QMessageBox(self)
        box.setWindowTitle("发现新版本")
        box.setIcon(QMessageBox.Icon.Information)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(msg)
        if url:
            download_btn = box.addButton("立即更新", QMessageBox.ButtonRole.AcceptRole)
        else:
            download_btn = None
        box.addButton("稍后再说", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if download_btn is not None and box.clickedButton() is download_btn and url:
            self._download_and_install_update(version, url)

    def _download_and_install_update(self, version: str, url: str):
        """弹深色下载窗口，下载完成后运行安装包并退出本程序。"""
        dialog = UpdateDownloadDialog(version, url, self)
        dialog.download_ok.connect(self._launch_installer)
        dialog.exec()

    def _launch_installer(self, installer_path: str):
        """静默安装更新，然后关闭本程序；安装器装完会自动拉起新版本。"""
        import subprocess
        try:
            # /SILENT：显示进度条但不询问；/NORESTART 由 [Run] 段负责启动新版本
            subprocess.Popen(
                [installer_path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        except OSError as exc:
            QMessageBox.warning(self, "启动安装程序失败", f"无法启动安装包：\n{exc}")
            return
        # 稍等片刻让安装器进程真正起来（它会自动关闭并重启本程序）
        QTimer.singleShot(800, self.close)

    def _open_image(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "添加图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)"
        )
        for path in paths:
            pm = QPixmap(path)
            if not pm.isNull():
                self._on_image_loaded(pm)

    # ---- 导入 JSON 工程 ----

    def _import_json_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 JSON 工程", "", "精灵图 JSON (*.json)"
        )
        if path:
            self._import_project(path)

    def _import_project(self, path_str: str) -> None:
        """导入 JSON（自动识别格式），配套图片由 JSON 引用的文件名在同目录查找。"""
        from spritesheet.importer import ImportError_, load_project

        try:
            project = load_project(path_str)
        except ImportError_ as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - 兜底，避免导入异常打断程序
            QMessageBox.warning(self, "导入失败", f"解析 {path_str} 时出错：{exc}")
            return

        if self.actions:
            answer = QMessageBox.question(
                self, "导入工程",
                f"导入将<b>替换当前工程</b>（现有 {len(self.actions)} 个动作）。<br><br>"
                f"识别格式：{project.format_label}<br>"
                f"配套图片：{project.atlas_path.name}<br>"
                f"动作数量：{len(project.actions)} 个<br><br>是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        pm = QPixmap(str(project.atlas_path))
        if pm.isNull():
            QMessageBox.warning(self, "导入失败", f"无法读取配套图片：\n{project.atlas_path}")
            return

        try:
            self._apply_imported_project(project, pm)
        except Exception as exc:  # noqa: BLE001 - 导入失败不应导致程序退出
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, "导入失败", f"应用工程时出错：{exc}")

    def _end_all_renames(self) -> None:
        """结束所有卡片的编辑状态与待执行的延迟回调（销毁卡片前必须先做）。"""
        for i in range(self.action_list.count()):
            item = self.action_list.item(i)
            card = self.action_list.itemWidget(item) if item is not None else None
            if isinstance(card, ActionCardWidget):
                try:
                    card._rename_timer.stop()   # 取消待触发的重命名收尾
                    card.end_rename()
                except (RuntimeError, AttributeError):
                    pass

    def _apply_imported_project(self, project, pm: QPixmap) -> None:
        """把解析好的工程套用到界面：图集当原图、图块当元素、动作重建。

        若 JSON 带 elements（本工具格式），用其中的 origin 还原每个元素的**逻辑坐标**，
        于是合成帧的拼接间距与导出前完全一致 —— 图集块数、画面、合成组结构都不变。
        若缺少 elements（第三方格式），退化为把合成帧按 parts 拼成整块。
        """
        viewer = self.image_viewer
        atlas = pm

        tiles: list[tuple[int, int, int, int]] = []
        tile_index: dict[tuple[int, int, int, int], int] = {}

        def add_tile(rect) -> int:
            if rect not in tile_index:
                tile_index[rect] = len(tiles)
                tiles.append(rect)
            return tile_index[rect]

        # 1) 单部件帧的图块（直接来自图集）
        for act in project.actions:
            for fr in act.frames:
                if len(fr.parts) == 1:
                    add_tile(fr.parts[0].rect)

        # 2) 收集所有帧用到的图块（含合成帧的各个部件）——
        #    合成帧不再拼成整块，而是保留「每帧由哪些图块、贴在什么位置」的布局，
        #    这样既不会丢帧（同一元素可为多个帧服务），导出时也能复用元素图块。
        for act in project.actions:
            for fr in act.frames:
                for p in fr.parts:
                    add_tile(p.rect)

        # 3) 载入原图（其触发的异步自动检测随后会被作废）
        viewer.set_pixmap(atlas)
        viewer._detect_generation += 1
        viewer._frames = tiles

        # 4) 还原元素逻辑坐标（来自 JSON 的 elements；用于普通帧的对齐参考）
        logical: dict[int, tuple[int, int]] = {}
        for el in project.elements:
            rect = _rect_from_dict(el.get("rect") or {})
            o = el.get("origin") or {}
            if rect in tile_index:
                logical[tile_index[rect]] = (int(o.get("x", 0)), int(o.get("y", 0)))
        viewer._frame_logical = logical

        viewer.canvas.update()
        viewer._update_rulers()

        # 5) 重建动作
        # 先结束所有卡片的编辑并取消其待执行回调，再清空列表，
        # 否则卡片销毁后仍有 0ms 定时器回调 -> 访问已释放对象导致崩溃。
        self._end_all_renames()
        self.actions.clear()
        self.action_list.clear()
        viewer.set_current_action(None)
        self.preview.set_action(None)

        total_frames = 0
        for ai, imported in enumerate(project.actions):
            color = MACARON_COLORS[ai % len(MACARON_COLORS)]
            action = Action(imported.name, color)
            action.fps = imported.fps
            action.loop_mode = imported.loop
            action.anchor = imported.anchor
            layouts: list[list[tuple[int, int, int]]] = []
            for fr in imported.frames:
                layout = [(tile_index[p.rect], p.pos[0], p.pos[1])
                          for p in fr.parts if p.rect in tile_index]
                if layout:
                    layouts.append(layout)
            if layouts:
                action.frame_layouts = layouts
                # 导入的帧全部视为基线（非用户复用），Alt 点击不会误取消
                action.frame_layouts_base = len(layouts)
                # selected_indices 覆盖布局中用到的全部元素（用于界面高亮，
                # 也让「布局是否仍适用」的判定成立）
                ids: list[int] = []
                for layout in layouts:
                    for tile_id, _rx, _ry in layout:
                        if tile_id not in ids:
                            ids.append(tile_id)
                action.selected_indices = ids
            viewer.crop_frames_for_action(action)
            total_frames += len(action.frames)
            self.actions.append(action)
            self._append_action_card(action)

        viewer.set_actions(self.actions)
        self.preview.set_source_pixmap(viewer._pixmap)
        self.btn_add_action.setText("+ 添加动作")
        if self.actions:
            self.action_list.setCurrentRow(0)
        self._reset_transform_history()

        msg = (f"已导入 {project.format_label}：{len(self.actions)} 个动作、"
               f"{total_frames} 帧、{len(tiles)} 个图块")
        self.lbl_status.setText(msg)
        notices = list(project.warnings)
        if notices:
            QMessageBox.information(
                self, "导入完成",
                f"{msg}\n\n提示：\n" + "\n".join(f"· {n}" for n in notices),
            )

    # ---- 拖拽：图片 / JSON ----

    def dragEnterEvent(self, event):
        try:
            if event.mimeData().hasUrls():
                for url in event.mimeData().urls():
                    if Path(url.toLocalFile()).suffix.lower() in (SUPPORTED_IMAGE_EXT | {".json", ".sstool"}):
                        event.acceptProposedAction()
                        return
        except Exception:  # noqa: BLE001 - 拖放回调异常会让 PyQt abort（闪退）
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
                if suffix == ".sstool":
                    QTimer.singleShot(0, lambda p=path: self._safe_open_project(p))
                    event.acceptProposedAction()
                    return
                if suffix == ".json":
                    # 拖放回调期间不做重活：延迟处理，避免 OLE 拖放未结束就弹出模态框/重建界面
                    QTimer.singleShot(0, lambda p=path: self._import_project(p))
                    event.acceptProposedAction()
                    return
                if suffix in SUPPORTED_IMAGE_EXT:
                    QTimer.singleShot(0, lambda p=path: self._on_image_loaded(QPixmap(p)))
                    event.acceptProposedAction()
                    return
        except Exception:  # noqa: BLE001
            pass

    def _on_image_loaded(self, pm: QPixmap):
        """首次载入或追加原图；追加时保留已有动作和帧排序。"""
        has_image = self.image_viewer._pixmap is not None and not self.image_viewer._pixmap.isNull()
        if not has_image:
            self.image_viewer.set_pixmap(pm)
            self.actions.clear()
            self.action_list.clear()
            self.image_viewer.set_actions(self.actions)
            self.image_viewer.set_current_action(None)
            self.preview.set_action(None)
        else:
            if not self.image_viewer.append_pixmap(pm):
                return
            # 新图追加到帧列表末尾，原有 selected_indices 无需改动；
            # 重新裁剪所有动作以刷新当前源图的帧缓存。
            for action in self.actions:
                self.image_viewer.crop_frames_for_action(action)
            if self.image_viewer.current_action:
                self.preview.set_action(self.image_viewer.current_action)
        # 同步最新原图给预览组件，供导出弹窗的原图预览使用
        self.preview.set_source_pixmap(self.image_viewer._pixmap)
        self._reset_transform_history()
        self.btn_add_action.setText("+ 添加动作")
        source_count = len(self.image_viewer._source_images)
        verb = "已加载" if source_count == 1 else "已添加"
        self.lbl_status.setText(
            f"{verb}第 {source_count} 张原图：{pm.width()}×{pm.height()}，"
            f"当前共检测到 {len(self.image_viewer._frames)} 个元素"
        )
        self._mark_project_dirty()

    def _on_add_action_clicked(self):
        """根据是否已加载图片切换按钮文字。"""
        if self.image_viewer._pixmap is None or self.image_viewer._pixmap.isNull():
            self._open_image()
        else:
            self._create_action()

    def _append_action_card(self, action: Action) -> ActionCardWidget:
        """把动作加入列表并创建卡片（供新建动作与导入复用）。"""
        item = QListWidgetItem()
        self.action_list.addItem(item)
        card = ActionCardWidget(action.name, action.color)
        card.color_changed.connect(lambda c, a=action: self._on_action_color_changed(a, c))
        card.name_changed.connect(lambda n, a=action: self._on_action_name_changed(a, n))
        card.delete_requested.connect(lambda a=action: self._delete_action(a))
        card.clicked.connect(lambda a=action: self._select_action_by_obj(a))
        item.setSizeHint(QSize(0, 34))
        self.action_list.setItemWidget(item, card)
        item.setData(Qt.ItemDataRole.UserRole, action)
        return card

    def _create_action(self):
        """无图片时打开文件对话框；有图片时直接创建动作卡牌。"""
        # 选一个未被使用的颜色
        used_colors = {a.color for a in self.actions}
        color = "#E91E63"
        for c in MACARON_COLORS:
            if c not in used_colors:
                color = c
                break
        name = f"动作{len(self.actions) + 1}"
        action = Action(name, color)
        self.actions.append(action)
        self._reset_transform_history()
        self._mark_project_dirty()
        self.image_viewer.set_actions(self.actions)

        card = self._append_action_card(action)

        # 切换动作前结束所有卡牌的编辑状态
        for i in range(self.action_list.count()):
            w = self.action_list.itemWidget(self.action_list.item(i))
            if isinstance(w, ActionCardWidget):
                w.end_rename()

        self.action_list.setCurrentRow(self.action_list.count() - 1)
        # 新建动作直接进入重命名状态
        card.start_rename()
        self.lbl_status.setText(f"已添加动作「{name}」，共 {len(action.frames)} 帧")

    def _on_action_color_changed(self, action: Action, color: str):
        action.color = color
        if self.image_viewer.current_action is action:
            self.image_viewer.action_color = color
        self.image_viewer.refresh()
        self._mark_project_dirty()

    def _on_action_name_changed(self, action: Action, name: str):
        action.name = name
        self._mark_project_dirty()

    def _delete_action(self, action: Action):
        """删除动作卡片并将选择切换到相邻动作。"""
        if action not in self.actions:
            return
        row = next((i for i in range(self.action_list.count())
                    if self.action_list.item(i).data(Qt.ItemDataRole.UserRole) is action), -1)
        if row < 0:
            return
        current_row = self.action_list.currentRow()
        was_current = self.image_viewer.current_action is action
        item = self.action_list.takeItem(row)
        card = self.action_list.itemWidget(item) if item else None
        if card is not None:
            card.deleteLater()
        del item
        self.actions.remove(action)
        self._reset_transform_history()
        self._mark_project_dirty()
        self.image_viewer.set_actions(self.actions)

        if self.actions:
            if current_row > row:
                target_row = current_row - 1
            elif current_row == row:
                target_row = min(row, self.action_list.count() - 1)
            else:
                target_row = current_row
            if target_row >= 0:
                self.action_list.setCurrentRow(target_row)
        elif was_current:
            self.image_viewer.set_current_action(None)
            self.preview.set_action(None)
            self.lbl_status.setText("已删除动作，当前没有动作")

    def _select_action_by_obj(self, action: Action):
        for i in range(self.action_list.count()):
            item = self.action_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) is action:
                self.action_list.setCurrentRow(i)
                break

    def _on_action_selected(self, row: int):
        if row < 0 or row >= len(self.actions):
            return
        action = self.actions[row]
        self.image_viewer.set_current_action(action)
        # 裁剪帧
        self.image_viewer.crop_frames_for_action(action)
        self.preview.set_action(action)
        self.lbl_status.setText(f"当前动作：{action.name}，{len(action.frames)} 帧")
        self._mark_project_dirty()

    def _on_selection_changed(self):
        """根据昐否已加载图片切换按钮文字。"""
        if self.image_viewer.current_action:
            self.image_viewer.crop_frames_for_action(self.image_viewer.current_action)
            self.preview.set_action(self.image_viewer.current_action)
            self._reset_transform_history()

    def _on_frames_updated(self):
        """后台检测完成后刷新动作帧和界面摘要。"""
        for action in self.actions:
            self.image_viewer.crop_frames_for_action(action)
        if self.image_viewer.current_action:
            self.preview.set_action(self.image_viewer.current_action)
        if self.image_viewer._pixmap and not self.image_viewer._pixmap.isNull():
            self.lbl_status.setText(
                f"已完成元素检测：当前共检测到 {len(self.image_viewer._frames)} 个元素"
            )
        self._reset_transform_history()
        self._mark_project_dirty()
