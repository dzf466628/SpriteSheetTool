#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""通用小部件与图标/像素工具（从 main_window.py 拆出，纯搬移，逻辑未改动）。

包含：
- ``make_tool_icon`` / ``make_logo_pixmap``：SVG 图标渲染与软件 Logo；
- ``_Ruler`` / ``RULER_SIZE``：PS 风格标尺；
- ``ColorPickerDialog`` / ``ActionCardWidget`` / ``ActionItemDelegate``：动作卡牌一族；
- ``CompactComboBox`` / ``CompactSpinBox`` / ``EditableNumberLabel`` / ``SwitchWidget``：
  深色紧凑控件（覆盖系统原生白底箭头/上下按钮）；
- ``ActionThumbPlayer``：动作缩略循环播放器；
- ``_SLIDER_QSS`` / ``_GROUP_QSS``：面板级 QSS 常量。

依赖方向：models ← transform ← widgets（本模块不依赖 main_window，避免循环 import）。
"""
from __future__ import annotations

import math
from pathlib import Path

from PyQt6.QtCore import QPointF, QRect, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSizePolicy, QSpinBox, QStyledItemDelegate, QStyle, QWidget,
)

from .models import MACARON_COLORS
from .transform import action_frame_pixmap


RULER_SIZE = 14


_APP_ROOT = Path(__file__).resolve().parent.parent.parent


_ICONS_DIR = _APP_ROOT / "icons"


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
