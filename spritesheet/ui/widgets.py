#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""通用小部件与图标/像素工具（从 main_window.py 拆出，纯搬移，逻辑未改动）。

包含：
- ``make_tool_icon``：SVG 图标渲染；
- ``AppNameLabel``：软件名标签（首字母放大 + 与其余文字垂直居中）；
- ``ProjectTabBar``：窗口底部的工程标签栏（切换 / 重命名 / 新建 / 关闭）；
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

from PyQt6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontMetricsF, QIcon, QPainter, QPen, QPixmap, QPolygonF,
)
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


def make_tool_icon(kind: str, size: int = 18) -> QIcon:
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


class AppNameLabel(QWidget):
    """软件名标签：首字母放大，并与其余文字**垂直居中**。

    为什么不用 QLabel 富文本（`<span style="font-size:30px">S</span>`）：
    Qt 的富文本按**基线**对齐，放大首字母后它会整块往上冒 —— 上缘高出很多、
    下缘却和普通文字齐平，看起来是"吊在上面"而不是"上下居中"。
    这里按墨迹盒（capHeight）自己算基线，像素级可控，也便于回归断言。

    单词拆成三段着色：首字母 + 剩余部分 + 尾段（尾段用品牌玫红）。
    """

    BASE_PX = 22          # 其余文字字号
    HEAD_PX = 30          # 首字母字号（更大）
    COLOR_BODY = "#ffffff"
    COLOR_TAIL = "#E91E63"

    def __init__(self, head: str = "S", rest: str = "priteSheet", tail: str = "Tool",
                 parent=None) -> None:
        super().__init__(parent)
        self._head, self._rest, self._tail = head, rest, tail
        family = "Microsoft YaHei UI"
        self._f_base = QFont(family)
        self._f_base.setPixelSize(self.BASE_PX)
        self._f_base.setBold(True)
        self._f_head = QFont(family)
        self._f_head.setPixelSize(self.HEAD_PX)
        self._f_head.setBold(True)
        self._fm_base = QFontMetricsF(self._f_base)
        self._fm_head = QFontMetricsF(self._f_head)
        self._width = (self._fm_head.horizontalAdvance(head)
                       + self._fm_base.horizontalAdvance(rest)
                       + self._fm_base.horizontalAdvance(tail))
        self.setFixedSize(int(math.ceil(self._width)) + 2, 54)
        self.setStyleSheet("background: transparent;")

    def sizeHint(self) -> QSize:
        return QSize(int(math.ceil(self._width)) + 2, 54)

    def head_rect(self) -> QRect:
        """首字母的墨迹矩形（回归用：断言它与其余文字上下居中）。"""
        return self._head_ink_rect().toAlignedRect()

    def body_rect(self) -> QRect:
        """其余文字的墨迹矩形（回归用）。"""
        return self._body_ink_rect().toAlignedRect()

    # ---- 内部几何：全部按**墨迹盒**（tightBoundingRect）算，不用 ascent/descent ----
    def _ink_rel(self, fm: QFontMetricsF, text: str) -> tuple[float, float]:
        """相对基线的墨迹上下边界（相对基线，向上为负）。"""
        rect = fm.tightBoundingRect(text)
        return rect.top(), rect.bottom()

    def _baselines(self) -> tuple[float, float]:
        """返回 (正文基线 y, 首字母基线 y)。

        两步：
          ① 先让**两块墨迹的垂直中心对齐**（这样首字母是"上下居中"而不是被基线吊上去）；
          ② 再把合并后的整块墨迹放到控件竖直中线上。
        用墨迹盒而不是 capHeight：渲染出来的实际墨迹比 capHeight 高一点点
        （抗锯齿外扩），只用 capHeight 算会整体偏高几个像素。
        """
        body_top, body_bot = self._ink_rel(self._fm_base, self._rest + self._tail)
        head_top, head_bot = self._ink_rel(self._fm_head, self._head)
        # 首字母基线相对正文基线的偏移：让两块墨迹中心重合
        delta = ((body_top + body_bot) / 2.0) - ((head_top + head_bot) / 2.0)
        top = min(body_top, delta + head_top)
        bottom = max(body_bot, delta + head_bot)
        base_y = self.height() / 2.0 - (top + bottom) / 2.0
        return base_y, base_y + delta

    def head_rect(self) -> QRect:
        """首字母的墨迹矩形（回归用：断言它与其余文字上下居中）。"""
        return self._head_ink_rect().toAlignedRect()

    def body_rect(self) -> QRect:
        """其余文字的墨迹矩形（回归用）。"""
        return self._body_ink_rect().toAlignedRect()

    def _head_ink_rect(self) -> QRectF:
        _base_y, head_y = self._baselines()
        top, bottom = self._ink_rel(self._fm_head, self._head)
        return QRectF(0.0, head_y + top,
                      self._fm_head.horizontalAdvance(self._head), bottom - top)

    def _body_ink_rect(self) -> QRectF:
        base_y, _head_y = self._baselines()
        top, bottom = self._ink_rel(self._fm_base, self._rest + self._tail)
        return QRectF(self._fm_head.horizontalAdvance(self._head), base_y + top,
                      self._fm_base.horizontalAdvance(self._rest + self._tail), bottom - top)

    def paintEvent(self, event) -> None:  # noqa: ARG002 - Qt 接口
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        base_y, head_y = self._baselines()
        x = 0.0

        painter.setFont(self._f_head)
        painter.setPen(QPen(QColor(self.COLOR_BODY)))
        painter.drawText(QPointF(x, head_y), self._head)
        x += self._fm_head.horizontalAdvance(self._head)

        painter.setFont(self._f_base)
        painter.drawText(QPointF(x, base_y), self._rest)
        x += self._fm_base.horizontalAdvance(self._rest)

        painter.setPen(QPen(QColor(self.COLOR_TAIL)))
        painter.drawText(QPointF(x, base_y), self._tail)
        painter.end()


class ProjectTabBar(QWidget):
    """窗口底部的工程标签栏（矮条，类似 PS 的文档标签）。

    交互：
      * 单击标签      -> ``tab_selected``
      * 双击标签      -> 就地重命名（内嵌 QLineEdit，回车提交 / Esc 取消）
      * 双击空白处    -> ``new_tab_requested``（新建标签页）
      * 点标签上的 ×  -> ``tab_close_requested``
      * 点末尾的 +    -> ``new_tab_requested``

    自己画而不是用 QTabBar：QTabBar 的最小高度跟着样式走（系统样式下三十多像素），
    这里要的是"矮"一条；而且需要标签上的脏标记与就地改名。
    """

    HEIGHT = 26
    PAD_X = 10            # 标签内左右留白
    CLOSE_W = 14          # 关闭按钮宽度
    GAP = 2               # 标签间距
    PLUS_W = 26

    tab_selected = pyqtSignal(int)
    tab_close_requested = pyqtSignal(int)
    tab_renamed = pyqtSignal(int, str)
    new_tab_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setMouseTracking(True)
        self.setStyleSheet("background: #1a1a1a; border-top: 1px solid #333;")
        self.setToolTip("单击切换工程 · 双击标签重命名 · 双击空白处新建工程")
        self._tabs: list[dict] = []       # {name, dirty, tooltip}
        self._current = -1
        self._hover = -1
        self._hover_close = -1
        self._editor: QLineEdit | None = None
        self._editing_index = -1

    # ---- 数据接口 ----
    def count(self) -> int:
        return len(self._tabs)

    def current_index(self) -> int:
        return self._current

    def name_at(self, index: int) -> str:
        return self._tabs[index]["name"]

    def is_dirty(self, index: int) -> bool:
        return bool(self._tabs[index]["dirty"])

    def add_tab(self, name: str, dirty: bool = False, tooltip: str = "",
                select: bool = True) -> int:
        self._tabs.append({"name": name or "未命名工程", "dirty": dirty, "tooltip": tooltip})
        index = len(self._tabs) - 1
        if select or self._current < 0:
            self._current = index
        self.update()
        return index

    def remove_tab(self, index: int) -> None:
        if not (0 <= index < len(self._tabs)):
            return
        self._tabs.pop(index)
        if self._current >= len(self._tabs):
            self._current = len(self._tabs) - 1
        elif index < self._current:
            self._current -= 1
        self.update()

    def set_current(self, index: int) -> None:
        if 0 <= index < len(self._tabs) and index != self._current:
            self._current = index
            self.update()

    def set_name(self, index: int, name: str) -> None:
        if 0 <= index < len(self._tabs):
            self._tabs[index]["name"] = name or "未命名工程"
            self.update()

    def set_dirty(self, index: int, dirty: bool) -> None:
        if 0 <= index < len(self._tabs):
            self._tabs[index]["dirty"] = bool(dirty)
            self.update()

    def set_tooltip(self, index: int, tooltip: str) -> None:
        if 0 <= index < len(self._tabs):
            self._tabs[index]["tooltip"] = tooltip

    # ---- 几何 ----
    def _fm(self):
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(12)
        return QFontMetricsF(font)

    def tab_rects(self) -> list[QRect]:
        fm = self._fm()
        rects = []
        x = 4.0
        for tab in self._tabs:
            width = fm.horizontalAdvance(tab["name"]) + self.PAD_X * 2 + self.CLOSE_W
            rects.append(QRect(int(x), 0, int(max(48.0, width)), self.height()))
            x += max(48.0, width) + self.GAP
        return rects

    def plus_rect(self) -> QRect:
        rects = self.tab_rects()
        x = (rects[-1].right() + self.GAP + 2) if rects else 4
        return QRect(int(x), 0, self.PLUS_W, self.height())

    def _close_rect(self, index: int) -> QRect:
        rect = self.tab_rects()[index]
        return QRect(rect.right() - self.CLOSE_W - 2, rect.top() + 5,
                     self.CLOSE_W - 2, rect.height() - 10)

    def _index_at(self, pos) -> int:
        for index, rect in enumerate(self.tab_rects()):
            if rect.contains(pos):
                return index
        return -1

    # ---- 绘制 ----
    def paintEvent(self, event) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        fm = self._fm()
        painter.setFont(QFont("Microsoft YaHei UI", -1, QFont.Weight.Normal, False))
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(12)
        painter.setFont(font)

        for index, rect in enumerate(self.tab_rects()):
            active = index == self._current
            if active:
                fill = QColor("#2b2b2b")
            elif index == self._hover:
                fill = QColor("#232323")
            else:
                fill = QColor("#1a1a1a")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(rect.adjusted(0, 1, 0, -1), 4, 4)
            if active:
                # 活动标签：顶部一条品牌玫红，压在内容区那条线上
                painter.setBrush(QColor("#E91E63"))
                painter.drawRect(rect.left() + 4, rect.top(), rect.width() - 8, 2)

            text = self._tabs[index]["name"]
            if self._tabs[index]["dirty"]:
                text += " ●"
            painter.setPen(QPen(QColor("#e8e8e8" if active else "#9a9a9a")))
            text_rect = rect.adjusted(self.PAD_X, 0, -self.CLOSE_W, 0)
            painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter |
                                            Qt.AlignmentFlag.AlignLeft), text)

            # 关闭按钮：悬停才点亮，避免整条太花
            close = self._close_rect(index)
            painter.setPen(QPen(QColor("#e8e8e8" if self._hover_close == index else "#6a6a6a")))
            painter.drawLine(close.left() + 3, close.center().y() - 3,
                             close.right() - 3, close.center().y() + 3)
            painter.drawLine(close.right() - 3, close.center().y() - 3,
                             close.left() + 3, close.center().y() + 3)

        plus = self.plus_rect()
        painter.setPen(QPen(QColor("#9a9a9a")))
        cx, cy = plus.center().x(), plus.center().y()
        painter.drawLine(cx - 5, cy, cx + 5, cy)
        painter.drawLine(cx, cy - 5, cx, cy + 5)

        painter.end()

    # ---- 鼠标 ----
    def mousePressEvent(self, event) -> None:
        pos = event.position().toPoint()
        index = self._index_at(pos)
        if event.button() == Qt.MouseButton.LeftButton:
            if index >= 0:
                if self._close_rect(index).contains(pos):
                    self.tab_close_requested.emit(index)
                    return
                if index != self._current:
                    self._current = index
                    self.update()
                    self.tab_selected.emit(index)
            elif self.plus_rect().contains(pos):
                self.new_tab_requested.emit()
        elif event.button() == Qt.MouseButton.MiddleButton and index >= 0:
            self.tab_close_requested.emit(index)

    def mouseDoubleClickEvent(self, event) -> None:
        pos = event.position().toPoint()
        index = self._index_at(pos)
        if index >= 0:
            self.start_rename(index)
        else:
            # 双击空白处 = 新建工程（PS 里双击标签栏空白也一样）
            self.new_tab_requested.emit()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        index = self._index_at(pos)
        close = index if (index >= 0 and self._close_rect(index).contains(pos)) else -1
        if (index, close) != (self._hover, self._hover_close):
            self._hover, self._hover_close = index, close
            self.update()
        tip = self._tabs[index]["tooltip"] if index >= 0 else ""
        self.setToolTip(tip or "单击切换工程 · 双击标签重命名 · 双击空白处新建工程")

    def leaveEvent(self, event) -> None:  # noqa: ARG002
        self._hover = self._hover_close = -1
        self.update()

    # ---- 就地重命名 ----
    def start_rename(self, index: int) -> None:
        if not (0 <= index < len(self._tabs)):
            return
        if self._editor is None:
            self._editor = QLineEdit(self)
            self._editor.setStyleSheet(
                "QLineEdit { background: #141414; color: #ffffff; border: 1px solid #E91E63;"
                " border-radius: 3px; padding: 0 4px; font-size: 12px; }"
            )
            self._editor.returnPressed.connect(self._commit_rename)
            self._editor.editingFinished.connect(self._commit_rename)
            self._editor.installEventFilter(self)
        rect = self.tab_rects()[index]
        self._editing_index = index
        self._editor.setText(self._tabs[index]["name"])
        self._editor.setGeometry(rect.left() + 2, rect.top() + 3,
                                 max(60, rect.width() - 6), rect.height() - 6)
        self._editor.show()
        self._editor.setFocus()
        self._editor.selectAll()

    def _commit_rename(self) -> None:
        if self._editor is None or self._editing_index < 0:
            return
        index = self._editing_index
        text = self._editor.text().strip()
        self._editor.hide()
        self._editing_index = -1
        if text and 0 <= index < len(self._tabs) and text != self._tabs[index]["name"]:
            self._tabs[index]["name"] = text
            self.update()
            self.tab_renamed.emit(index, text)

    def cancel_rename(self) -> None:
        if self._editor is not None:
            self._editor.hide()
        self._editing_index = -1

    def is_renaming(self) -> bool:
        return self._editing_index >= 0

    def eventFilter(self, obj, event):
        # Esc 取消改名（QLineEdit 自己不吃 Esc）
        if (obj is self._editor and event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Escape):
            self.cancel_rename()
            return True
        return super().eventFilter(obj, event)


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


# ---------------- 棋盘格背景（缓存图块 + 一次平铺） ----------------

_CHECKER_TILE_CACHE: dict = {}


def checker_tile(cell: int, color_a: str = "#2a2a2a", color_b: str = "#1e1e1e") -> QPixmap:
    """返回 2x2 格的棋盘格图块（按 (格宽, 两色) 缓存），供 ``paint_checkerboard`` 平铺。

    图案相位与逐格实现一致：格 (0,0)=color_a、(1,0)=(0,1)=color_b、(1,1)=color_a。
    """
    key = (cell, color_a, color_b)
    tile = _CHECKER_TILE_CACHE.get(key)
    if tile is None:
        tile = QPixmap(cell * 2, cell * 2)
        tile.fill(QColor(color_a))
        p = QPainter(tile)
        p.fillRect(cell, 0, cell, cell, QColor(color_b))
        p.fillRect(0, cell, cell, cell, QColor(color_b))
        p.end()
        _CHECKER_TILE_CACHE[key] = tile
    return tile


def paint_checkerboard(painter: QPainter, rect: QRect, cell: int,
                       color_a: str = "#2a2a2a", color_b: str = "#1e1e1e") -> None:
    """在 ``rect`` 内铺满棋盘格背景（矩形左上角为图案原点）。

    性能：原实现逐格 ``fillRect``——1000x800、cell=4 时每帧约 5 万次调用
    （实测 370ms 以上），而预览画布在播放 / 拖动时会高频重绘。改用一张缓存
    图块 + 一次 ``drawTiledPixmap``；图案与逐格实现逐像素一致（已用渲染对比验证）。
    """
    if rect.width() <= 0 or rect.height() <= 0:
        return
    cell = max(1, int(cell))
    painter.drawTiledPixmap(rect, checker_tile(cell, color_a, color_b))
