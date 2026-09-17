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

from PyQt6.QtCore import (
    QEasingCurve, QEvent, QPointF, QRect, QRectF, QSize, QTimer, Qt, QVariantAnimation,
    pyqtSignal,
)
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


def make_tool_icon(kind: str, size: int = 18, color: str | None = None) -> QIcon:
    """从 icons/ 目录加载 SVG 图标。

    染色：``color`` 指定时整枚图标染成该色（SourceIn）；不指定时
    bg_black / bg_white 这两个按名字染黑/白（历史行为，别动）。
    """
    path = _ICONS_DIR / f"{kind}.svg"
    if not path.exists():
        # fallback: 简单绘制
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setPen(QPen(QColor(color or "#d8d8d8"), 1.5))
        p.drawRect(2, 2, size - 4, size - 4)
        p.end()
        return _icon_with_disabled(pm)
    renderer = QSvgRenderer(str(path))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    renderer.render(p)
    p.end()
    tint = color
    if tint is None and kind in ("bg_black", "bg_white"):
        tint = "#000000" if kind == "bg_black" else "#FFFFFF"
    if tint:
        p3 = QPainter(pm)
        p3.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        p3.fillRect(pm.rect(), QColor(tint))
        p3.end()
    return _icon_with_disabled(pm)


def _icon_with_disabled(pm: QPixmap) -> QIcon:
    """给图标附加禁用态（灰色）版本，保证按钮置灰时图标清晰可见为灰。"""
    icon = QIcon(pm)
    gray = QPixmap(pm.size())
    gray.fill(Qt.GlobalColor.transparent)
    gp = QPainter(gray)
    gp.drawPixmap(0, 0, pm)
    gp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    gp.fillRect(gray.rect(), QColor(120, 120, 120))
    gp.end()
    icon.addPixmap(gray, QIcon.Mode.Disabled, QIcon.State.Off)
    icon.addPixmap(gray, QIcon.Mode.Disabled, QIcon.State.On)
    return icon


def _blend(start: QColor, end: QColor, t: float) -> QColor:
    """按 t（0~1）在两色之间插值（用于悬停时灰→红的过渡）。"""
    t = max(0.0, min(1.0, float(t)))
    return QColor(
        round(start.red() + (end.red() - start.red()) * t),
        round(start.green() + (end.green() - start.green()) * t),
        round(start.blue() + (end.blue() - start.blue()) * t),
    )


class AppNameLabel(QWidget):
    """软件名标签：首字母放大，并与其余文字**垂直居中**；版本号跟在名字后面，
    与名字**底对齐**（共用同一条基线）。

    为什么不用 QLabel 富文本（`<span style="font-size:30px">S</span>`）：
    Qt 的富文本按**基线**对齐，放大首字母后它会整块往上冒 —— 上缘高出很多、
    下缘却和普通文字齐平，看起来是"吊在上面"而不是"上下居中"。
    这里按墨迹盒（capHeight）自己算基线，像素级可控，也便于回归断言。

    版本号也画在这里（而不是单独一个 QLabel）：两个控件分别用 AlignBottom 摆，
    底边差多少取决于各自的行高与内边距，调不准；画在同一个基线上下对齐是天然的。

    单词拆成四段：首字母 + 剩余部分 + 尾段（品牌玫红）+ 版本号（灰色小字）。
    """

    BASE_PX = 22          # 其余文字字号
    HEAD_PX = 30          # 首字母字号（更大）
    VERSION_PX = 11       # 版本号字号
    VERSION_GAP = 8       # 版本号与名字的间距
    COLOR_BODY = "#ffffff"
    COLOR_TAIL = "#E91E63"
    COLOR_VERSION = "#9a9a9a"

    def __init__(self, head: str = "S", rest: str = "priteSheet", tail: str = "Tool",
                 version: str = "", parent=None) -> None:
        super().__init__(parent)
        self._head, self._rest, self._tail = head, rest, tail
        self._version = version
        family = "Microsoft YaHei UI"
        self._f_base = QFont(family)
        self._f_base.setPixelSize(self.BASE_PX)
        self._f_base.setBold(True)
        self._f_head = QFont(family)
        self._f_head.setPixelSize(self.HEAD_PX)
        self._f_head.setBold(True)
        self._f_version = QFont(family)
        self._f_version.setPixelSize(self.VERSION_PX)
        self._fm_base = QFontMetricsF(self._f_base)
        self._fm_head = QFontMetricsF(self._f_head)
        self._fm_version = QFontMetricsF(self._f_version)
        self._width = (self._fm_head.horizontalAdvance(head)
                       + self._fm_base.horizontalAdvance(rest)
                       + self._fm_base.horizontalAdvance(tail))
        if version:
            self._width += self.VERSION_GAP + self._fm_version.horizontalAdvance(version)
        self.setFixedSize(int(math.ceil(self._width)) + 2, 54)
        self.setStyleSheet("background: transparent;")

    def sizeHint(self) -> QSize:
        return QSize(int(math.ceil(self._width)) + 2, 54)

    def version_rect(self) -> QRect:
        """版本号的墨迹矩形（回归用：断言与名字底对齐）。"""
        return self._version_ink_rect().toAlignedRect()

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

    @staticmethod
    def _cap_height(fm: QFontMetricsF) -> float:
        """大写字母高度：拿 "H" 的墨迹高当基准。

        为什么不用 ``fm.capHeight()``：offscreen 下它和字号一样大（度量退化）；
        为什么要专门找一个**没有降部**的字符：正文 "priteSheetTool" 里的 p 会往下伸，
        直接量它的墨迹盒会把降部也算进去，首字母就会被"居中"到偏低的位置
        （真实字体下实测偏低 3px —— 眼神看就是 S 站得偏下）。
        """
        rect = fm.tightBoundingRect("H")
        return rect.bottom() - rect.top()

    def _baselines(self) -> tuple[float, float]:
        """返回 (正文基线 y, 首字母基线 y)。

        两步：
          ① 首字母的墨迹盒中心对齐**正文的大写带中心**（大写顶 → 基线），降部不参与；
          ② 再把两块（正文大写带 + 首字母盒）合起来放到控件竖直中线上。
        """
        cap_base = self._cap_height(self._fm_base)
        cap_head = self._cap_height(self._fm_head)
        delta = (cap_head - cap_base) / 2.0        # 首字母基线相对正文基线的下移量
        top = min(-cap_base, delta - cap_head)
        bottom = max(0.0, delta)
        base_y = self.height() / 2.0 - (top + bottom) / 2.0
        return base_y, base_y + delta


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

    def _version_ink_rect(self) -> QRectF:
        base_y, _head_y = self._baselines()
        top, bottom = self._ink_rel(self._fm_version, self._version)
        x = (self._fm_head.horizontalAdvance(self._head)
             + self._fm_base.horizontalAdvance(self._rest + self._tail) + self.VERSION_GAP)
        return QRectF(x, base_y + top,
                      self._fm_version.horizontalAdvance(self._version), bottom - top)

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
        x += self._fm_base.horizontalAdvance(self._tail)

        if self._version:
            # 版本号与名字共用同一条基线 —— 这就是"底对齐"
            painter.setFont(self._f_version)
            painter.setPen(QPen(QColor(self.COLOR_VERSION)))
            painter.drawText(QPointF(x + self.VERSION_GAP, base_y), self._version)
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
    这里要的是"矮"一条；而且需要就地改名与自绘的关闭叉号。
    （未保存圆点曾经也画在这里，v1.1.34 按用户要求删掉了。）
    """

    HEIGHT = 26
    PAD_X = 14            # 标签内左右留白
    NAME_GAP = 26         # 文件名与关闭叉号之间留出的空档（原来只有 14，太挤）
    CLOSE_W = 16          # 关闭按钮宽度
    CLOSE_R = 7           # 关闭按钮悬停时红色圆形的半径
    GAP = 4               # 标签间距
    PLUS_W = 26
    TEXT_DROP = 2         # 文件名整体下移的像素数
    # 关闭叉号再往下压一点，与工程名的文字横向对齐（用户要求 ~3px）
    CLOSE_DROP = 3
    MIN_W = 96            # 标签最小宽度（比原来宽，名字短的也不会挤成一小块）
    BG = "#1a1a1a"
    LINE = "#333333"
    CLOSE_RED = "#E91E63"        # 关闭按钮悬停时的红色（软件强调色）
    CLOSE_IDLE = "#6a6a6a"       # 未悬停时的叉号颜色
    CLOSE_HOVER_MS = 130         # 悬停动画时长
    CLOSE_ROTATE = 30.0          # 悬停时叉号旋转的角度（用户要求：转 30° 变红，不要红底）
    CLOSE_GLYPH = "✕"            # 叉号字符（U+2715；想用更细的 × 改成 "×"）

    tab_selected = pyqtSignal(int)
    tab_close_requested = pyqtSignal(int)
    tab_renamed = pyqtSignal(int, str)
    new_tab_requested = pyqtSignal()
    tab_drop_requested = pyqtSignal(str)  # 拖入标签栏：新建工程并打开该文件

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setStyleSheet("background: #1a1a1a; border-top: 1px solid #333;")
        self.setToolTip("单击切换工程 · 双击标签重命名 · 双击空白处新建工程")
        self._tabs: list[dict] = []       # {name, dirty, tooltip}
        self._current = -1
        self._hover = -1
        self._hover_close = -1
        self._editor: QLineEdit | None = None
        self._editing_index = -1
        # 关闭按钮的悬停动画进度（0=没悬停，1=完全悬停）。同一时刻只会有一个叉号被悬停，
        # 所以一个值够用；从叉号 A 移到叉号 B 时起点就是 1，不会闪一下。
        self._close_progress = 0.0
        self._close_anim = QVariantAnimation(self)
        self._close_anim.setDuration(self.CLOSE_HOVER_MS)
        self._close_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._close_anim.valueChanged.connect(self._on_close_anim)
        self._close_font = QFont("Microsoft YaHei UI")
        self._close_font.setPixelSize(13)
        self._close_font.setBold(True)
        # 叉号用真正的符号字形画（原来是用两条斜线拼的，看着不像正常叉号）。
        # 用 ✕(U+2715)：在真实 Windows 字体下渲染成 11x10 的粗叉、居中好看。
        # 想换成 ×(U+00D7) 就改 CLOSE_GLYPH 一处（更小更细）。
        #
        # ⚠️ 字形不能用 QFontMetricsF.inFont() 判断是否存在，也别在 offscreen 下量：
        #    - inFont 实测对 ASCII 'x' 都返回 False；
        #    - offscreen 平台**没有真实字体**，任何字符都画成同一个方框（tofu）。
        #    字形是否正常由 tools/verify_glyphs.py（跑在真实 windows 平台）验证。
        self._close_glyph = self.CLOSE_GLYPH

    def dragEnterEvent(self, event):
        try:
            if event.mimeData().hasUrls():
                event.acceptProposedAction()
        except Exception:  # noqa: BLE001
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
                if suffix in (".json", ".sstool", ".png", ".webp", ".jpg", ".jpeg", ".bmp", ".gif"):
                    self.tab_drop_requested.emit(path)
                    event.acceptProposedAction()
                    return
        except Exception:  # noqa: BLE001
            pass

    def _on_close_anim(self, value) -> None:
        self._close_progress = float(value)
        self.update()

    def _animate_close(self, target: float) -> None:
        self._close_anim.stop()
        self._close_anim.setStartValue(self._close_progress)
        self._close_anim.setEndValue(target)
        self._close_anim.start()

    # ---- 数据接口 ----
    def count(self) -> int:
        return len(self._tabs)

    def current_index(self) -> int:
        return self._current

    def name_at(self, index: int) -> str:
        return self._tabs[index]["name"]

    def is_dirty(self, index: int) -> bool:
        """该标签对应的文档有没有未保存的改动。

        注：这个标记**不再画在标签上**（曾经是名字后面的一个小圆点，v1.1.34 删除）。
        标签栏只保存状态供主窗口/回归读取；未保存提示在窗口标题的 " *" 上。
        """
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
            # 宽度 = 左右留白 + 名字 + 名字与叉号之间的空档 + 叉号
            width = fm.horizontalAdvance(tab["name"]) + self.PAD_X * 2 + self.NAME_GAP + self.CLOSE_W
            width = max(float(self.MIN_W), width)
            rects.append(QRect(int(x), 0, int(width), self.height()))
            x += width + self.GAP
        return rects

    def plus_rect(self) -> QRect:
        rects = self.tab_rects()
        x = (rects[-1].right() + self.GAP + 2) if rects else 4
        return QRect(int(x), 0, self.PLUS_W, self.height())

    def _close_rect(self, index: int) -> QRect:
        rect = self.tab_rects()[index]
        return QRect(rect.right() - self.CLOSE_W - 2, rect.top() + 5 + self.CLOSE_DROP,
                     self.CLOSE_W - 2, rect.height() - 10)

    def _index_at(self, pos) -> int:
        for index, rect in enumerate(self.tab_rects()):
            if rect.contains(pos):
                return index
        return -1

    # ---- 绘制 ----
    def paintEvent(self, event) -> None:  # noqa: ARG002
        painter = QPainter(self)
        # 自己铺底色，让标签栏真正用它自己声明的 #1a1a1a。
        # 重写了 paintEvent 的 QWidget 子类不会自动画样式表背景；不铺的话露出来的是
        # 父控件的底色（#232323），标签栏就和它自己的声明不一致了。
        # 注：这不是"白条"那类问题 —— 套上全局 QSS 后露出的本来就是深色父底，
        # 详见 tools/inspect_theme.py 的说明。
        painter.fillRect(self.rect(), QColor(self.BG))
        # 上边缘分隔线先画，标签随后盖上去 —— 活动标签那条品牌玫红要能压住它
        painter.setPen(QPen(QColor(self.LINE)))
        painter.drawLine(0, 0, self.width(), 0)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
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
            # 直角矩形（用户要求：两边不要圆角），上边留 1px 给分隔线
            painter.drawRect(rect.adjusted(0, 1, 0, -1))
            if active:
                # 活动标签：顶部一条品牌玫红，压在内容区那条线上
                painter.setBrush(QColor("#E91E63"))
                painter.drawRect(rect.left() + 4, rect.top(), rect.width() - 8, 2)

            text = self._tabs[index]["name"]
            painter.setPen(QPen(QColor("#e8e8e8" if active else "#9a9a9a")))
            # 文字整体下移 TEXT_DROP：竖直居中时贴着上面那条玫红/分隔线太挤。
            # 上下边都要 +TEXT_DROP（只加 top 的话是把矩形压扁、文字只挪半格）。
            text_rect = rect.adjusted(self.PAD_X, self.TEXT_DROP, -self.NAME_GAP, self.TEXT_DROP)
            painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter |
                                            Qt.AlignmentFlag.AlignLeft), text)

            # 标签上**不再画**未保存圆点。
            # 那个点最早是 `text += " ●"` 拼在名字后面当文字画的（跟着文字色，
            # 活动标签上就是白的），v1.1.33 曾改成单独绘制、活动标签用品牌红；
            # 用户随后确认这个点本来就不是有意设计的（"原来点不是你故意加的…不要了 删掉吧"），
            # 于是整个删掉。未保存状态仍然可见：窗口标题带 " *"（见 _set_project_title），
            # 关闭标签/退出时也仍会问要不要保存。

            # 关闭按钮：正常的叉号字形；悬停时**转 30° 并变红**（不带红底）
            close = self._close_rect(index)
            hovered = index == self._hover_close
            progress = self._close_progress if hovered else 0.0
            cx, cy = close.center().x(), close.center().y()
            angle = self.CLOSE_ROTATE * progress
            painter.setFont(self._close_font)
            painter.setPen(QPen(_blend(QColor(self.CLOSE_IDLE), QColor(self.CLOSE_RED), progress)))
            painter.save()
            painter.translate(cx, cy)
            if angle:
                painter.rotate(angle)
            # 以中心为原点画字形，这样旋转是绕叉号自己转
            painter.drawText(QRectF(-close.width(), -close.height(),
                                    close.width() * 2, close.height() * 2),
                             int(Qt.AlignmentFlag.AlignCenter), self._close_glyph)
            painter.restore()

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
                # ⚠️ 这里**不能**自己改 self._current 再发信号。
                # 曾经这么写过：MainWindow._switch_tab 开头是
                #   if index == self.tabs.current_index(): return
                # 于是"当前标签"已经被本函数改掉了，判断必然成立、直接返回，
                # 结果就是**点标签只换了高亮、工程根本没切**（用户实际踩到的 bug）。
                # 现在谁负责切换谁才改 _current（主窗口切完调 set_current）。
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
            if close != self._hover_close:
                # 悬停动画：进叉号 -> 渐显到 1；离开 -> 渐隐到 0
                self._animate_close(1.0 if close >= 0 else 0.0)
            self._hover, self._hover_close = index, close
            self.setCursor(Qt.CursorShape.PointingHandCursor if (index >= 0 or self.plus_rect().contains(pos))
                           else Qt.CursorShape.ArrowCursor)
            self.update()
        tip = self._tabs[index]["tooltip"] if index >= 0 else ""
        self.setToolTip(tip or "单击切换工程 · 双击标签重命名 · 双击空白处新建工程")

    def leaveEvent(self, event) -> None:  # noqa: ARG002
        self._hover = -1
        if self._hover_close >= 0:
            self._hover_close = -1
            self._animate_close(0.0)
        self.setCursor(Qt.CursorShape.ArrowCursor)
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


class ResponsiveButtonGrid(QWidget):
    """按钮网格：**优先每行 2 个**，宽度不够就自动变成每行 1 个（换行）。

    为什么不用 QGridLayout 固定两列：面板宽度是可拖动的，固定两列在窄面板里会把按钮压扁
    （用户反馈"不要挤压侧边栏"）。Qt 没有内置的自动换行布局，所以这里在 resizeEvent 里
    按可用宽度算列数再重排。

    用法：``ResponsiveButtonGrid([btn1, btn2, ...], min_button=92)``
    """

    def __init__(self, buttons: list[QWidget], max_columns: int = 2,
                 min_button: int = 92, spacing: int = 5, parent=None) -> None:
        super().__init__(parent)
        self._buttons = list(buttons)
        self._max_columns = max(1, max_columns)
        self._min_button = min_button
        self._columns = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(spacing)
        self._grid.setVerticalSpacing(spacing)
        self.setStyleSheet("background: transparent;")
        self._relayout()

    def columns(self) -> int:
        """当前实际的列数（回归用）。"""
        return self._columns

    def _wanted_columns(self) -> int:
        margins = self._grid.contentsMargins()
        available = max(0, self.width() - margins.left() - margins.right())
        spacing = self._grid.horizontalSpacing()
        for columns in range(self._max_columns, 0, -1):
            needed = columns * self._min_button + (columns - 1) * spacing
            if available >= needed:
                return columns
        return 1

    def _relayout(self) -> None:
        columns = self._wanted_columns()
        self._apply_cell_width(columns)
        if columns == self._columns:
            return
        self._columns = columns
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        for index, button in enumerate(self._buttons):
            row, col = divmod(index, columns)
            button.setParent(self)
            self._grid.addWidget(button, row, col)
        for col in range(self._max_columns):
            self._grid.setColumnStretch(col, 1 if col < columns else 0)
        self.updateGeometry()

    def _cell_width(self, columns: int) -> int:
        margins = self._grid.contentsMargins()
        available = max(0, self.width() - margins.left() - margins.right())
        spacing = self._grid.horizontalSpacing()
        return max(self._min_button, (available - (columns - 1) * spacing) // columns)

    def _apply_cell_width(self, columns: int) -> None:
        """把所有按钮钉成**同一个宽度**（= 单元格宽度）。

        为什么必须显式钉死：QGridLayout 在"最小宽度都一样、列拉伸也一样"时并不会
        均分多余空间。实测（面板 338px、8 个按钮、2 列，所有按钮 min=80/hint=80/
        Expanding）：第 0 列 80px、第 1 列直接把剩下的 253px 全吃掉了 —— 于是
        "清空变换"（恰好在第 1 列）看起来比邻居长一倍多，用户报的就是这个。
        只压 ``maximumWidth``：按钮必然等宽（布局有多余空间就把它们撑到上限）。

        这里**不能**顺手把 minimumWidth 也设成 cell：那样网格的最小尺寸会随面板
        变宽而变大（Qt 的最小尺寸棘轮），splitter 就再也拖不窄了。
        """
        cell = self._cell_width(columns)
        for button in self._buttons:
            if button.minimumWidth() > cell:
                button.setMinimumWidth(self._min_button)
            button.setMaximumWidth(cell)

    def resizeEvent(self, event) -> None:  # noqa: ARG002
        self._relayout()


class HoverIconButton(QPushButton):
    """悬停（或选中）时切换图标的按钮。

    为什么需要它：QSS 能改文字色和背景，但**改不了 pixmap 图标的颜色**。
    "清空变换"平时是红图标红字，悬停时底色变玫红、文字转白 —— 红图标压在玫红底上
    几乎看不见，用户要求悬停图标也变白，只能自己换 QIcon。

    ``set_state_icons(普通, 悬停, 选中)``：选中态不给就用悬停那张（两者底色都是玫红）。
    """

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self._icon_normal = None
        self._icon_hover = None
        self._icon_checked = None
        self._hovered = False
        self.toggled.connect(lambda _checked: self._sync_icon())

    def set_state_icons(self, normal, hover, checked=None) -> None:
        self._icon_normal = normal
        self._icon_hover = hover
        self._icon_checked = hover if checked is None else checked
        self._sync_icon()

    def icon_state(self) -> str:
        """当前生效的是哪张图标（回归用）：normal / hover / checked。"""
        if self._hovered:
            return "hover"
        if self.isChecked():
            return "checked"
        return "normal"

    def _sync_icon(self) -> None:
        if self._icon_normal is None:
            return
        icon = {"normal": self._icon_normal,
                "hover": self._icon_hover,
                "checked": self._icon_checked}[self.icon_state()]
        if icon is not None:
            self.setIcon(icon)

    def enterEvent(self, event):  # noqa: ARG002 - Qt 接口
        self._hovered = True
        self._sync_icon()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: ARG002 - Qt 接口
        self._hovered = False
        self._sync_icon()
        super().leaveEvent(event)


class HintToast:
    """画在控件上的临时提示气泡（默认 2.6 秒后自己消失）。

    为什么不是 QMessageBox：这里要表达的是"点了没反应，原因是还没有动作"——
    是**解释**不是**决策**，弹窗会打断操作、还得再点一次"确定"。画在用户刚点的
    那块画布上，既在视线里又不挡路。使用方在 paintEvent 末尾调 ``paint()``，
    并在交互入口调 ``show()``。
    """

    BG = QColor(28, 28, 28, 238)
    BORDER = QColor("#E91E63")
    TEXT = QColor("#f2f2f2")
    PAD_X = 12
    HEIGHT = 28
    MARGIN_BOTTOM = 16

    def __init__(self, widget: QWidget, duration_ms: int = 2600) -> None:
        self._widget = widget
        self._duration_ms = int(duration_ms)
        self._text = ""
        self._timer = QTimer(widget)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.clear)

    # ---- 状态 ----
    def show(self, text: str) -> None:
        """显示提示；重复调用会刷新文字并重新计时。"""
        self._text = str(text)
        self._timer.start(self._duration_ms)
        self._widget.update()

    def clear(self) -> None:
        if not self._text:
            return
        self._text = ""
        self._timer.stop()
        self._widget.update()

    def active(self) -> bool:
        return bool(self._text)

    def text(self) -> str:
        return self._text

    def _font(self) -> QFont:
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(12)
        return font

    # ---- 绘制 ----
    def paint(self, painter: QPainter, rect: QRect | None = None) -> None:
        """在 rect（默认整个控件）底部居中画气泡；没有提示时什么都不做。"""
        if not self._text:
            return
        rect = self._widget.rect() if rect is None else rect
        font = self._font()
        fm = QFontMetricsF(font)
        width = fm.horizontalAdvance(self._text) + self.PAD_X * 2
        box = QRectF(rect.center().x() - width / 2.0,
                     rect.bottom() - self.HEIGHT - self.MARGIN_BOTTOM,
                     width, self.HEIGHT)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(self.BORDER, 1.0))
        painter.setBrush(self.BG)
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(self.TEXT)
        painter.setFont(font)
        painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), self._text)
        painter.restore()


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
        # 注：这里不铺底色。实测（tools/inspect_theme.py + 一个最小复现）：
        # 套上全局 QSS 后，重写了 paintEvent 的子控件即使什么都不铺，露出来的也是
        # **父控件的底色**（深色），不是窗口默认的浅灰。之前看到"标尺是浅灰条"是
        # 测试脚本没套 QSS 造成的假象，不是真实缺陷。
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
    # 禁用态：轨道与手柄整体置灰（伪状态须写在子控件之后才生效）
    "QSlider::groove:horizontal:disabled { background: #2a2a2a; }"
    "QSlider::handle:horizontal:disabled { background: #555555; }"
    "QSlider::groove:vertical:disabled { background: #2a2a2a; }"
    "QSlider::handle:vertical:disabled { background: #555555; }"
)

# 双向滑块的样式：和上面同款，但**不填 sub-page**。
# 为什么：QSlider 的 sub-page 是"从最左端填到当前值"的进度条，用在 0~N 的滑块上很对
# （fps / 透明度就用那个），但偏移这类**以 0 为中心**的滑块上，值为 0 时会左半边全亮、
# 右半边全暗 —— 看着像"只填了一半"而不是"在中间"（用户报的"左右滑块好像不是居中的"
# 就是这个观感；实测手柄本身其实正好在中线，偏差 0.5px）。
# 去掉填充后，"是不是居中"完全由手柄位置表达，没有歧义。
_BIPOLAR_SLIDER_QSS = (
    "QSlider { background: transparent; border: none; }"
    "QSlider::groove:horizontal { height: 2px; background: #3d3d3d; border-radius: 1px; }"
    "QSlider::sub-page:horizontal { background: transparent; }"
    "QSlider::add-page:horizontal { background: transparent; }"
    "QSlider::handle:horizontal { width: 8px; height: 8px; margin: -3px 0;"
    " background: #E91E63; border-radius: 4px; }"
    "QSlider::handle:horizontal:hover { background: #F06292; }"
    "QSlider::groove:vertical { width: 2px; background: #3d3d3d; border-radius: 1px; }"
    "QSlider::sub-page:vertical { background: transparent; }"
    "QSlider::add-page:vertical { background: transparent; }"
    "QSlider::handle:vertical { width: 8px; height: 8px; margin: 0 -3px;"
    " background: #E91E63; border-radius: 4px; }"
    "QSlider::handle:vertical:hover { background: #F06292; }"
    # 禁用态：轨道与手柄整体置灰（伪状态须写在子控件之后才生效）
    "QSlider::groove:horizontal:disabled { background: #2a2a2a; }"
    "QSlider::handle:horizontal:disabled { background: #555555; }"
    "QSlider::groove:vertical:disabled { background: #2a2a2a; }"
    "QSlider::handle:vertical:disabled { background: #555555; }"
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
