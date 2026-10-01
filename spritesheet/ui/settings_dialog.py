# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""软件设置弹窗：把「检查更新」从文件菜单收进来，并放软件级开关。

目前内容：
  - 「工程文件显示缩略图」复选框（默认勾选）
      勾选 → 保存的 .sstool 内嵌缩略图，资源管理器直接显示画面；
      不勾 → 不写缩略图区段，资源管理器显示工程文件图标（SSTool.ico）。
  - 「检查更新」按钮（原来在 文件 菜单里，现已移到这里）
  - 「关闭」

开关是**即时生效**的（改了立刻写 QSettings），不设「确定 / 应用」——
只有一个开关时，多一步确认只会让人怀疑到底存没存。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QCheckBox, QColorDialog, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout,
)

from spritesheet.settings import (
    DEFAULT_PIXEL_GRID_ZOOM, DEFAULT_GUIDE_COLOR, DEFAULT_GUIDE_SELECTED_COLOR,
    DEFAULT_PIXEL_GRID_COLOR,
    detect_min_area, guide_color, guide_selected_color, pixel_grid_color,
    pixel_grid_zoom, reset_view_colors, set_detect_min_area, set_guide_color,
    set_guide_selected_color, set_pixel_grid_color, set_pixel_grid_zoom,
    set_show_project_thumbnail, show_project_thumbnail,
    set_undo_limit, undo_limit,
    set_similar_tolerance, similar_tolerance,
)
from spritesheet.ui.widgets import CompactSpinBox


class _ColorButton(QPushButton):
    """一个显示当前颜色的小方块按钮，点开取色盘（支持透明度）。

    为什么不用现成的 ColorSwatchButton：那是 PS 那种"前景/背景双色块"，
    这里是"每个设置项一个色块"，语义不同，硬套会让控件里多出一堆用不到的交互。
    """

    color_changed = pyqtSignal(QColor)

    def __init__(self, color: QColor, label: str, parent=None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self._label = label
        self.setFixedSize(58, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._pick)

    def color(self) -> QColor:
        return QColor(self._color)

    def set_color(self, color: QColor) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        # 棋盘格打底：半透明颜色才看得出透了多少
        p.fillRect(self.rect(), QColor("#2b2b2b"))
        for y in range(0, self.height(), 6):
            for x in range(0, self.width(), 6):
                if (x // 6 + y // 6) % 2:
                    p.fillRect(x, y, 6, 6, QColor("#3a3a3a"))
        p.fillRect(self.rect().adjusted(1, 1, -1, -1), self._color)
        p.setPen(QColor("#666"))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()

    def _pick(self) -> None:
        picked = QColorDialog.getColor(
            self._color, self, f"选择{self._label}颜色",
            QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if picked.isValid():
            self.set_color(picked)
            self.color_changed.emit(picked)


class SettingsDialog(QDialog):
    """软件设置：开关即时保存；检查更新转发给主窗口。"""

    # 辅助线颜色 / 网格门槛改了 → 主窗口收到就重绘画布（不用重启）
    view_colors_changed = pyqtSignal()

    def __init__(self, parent=None, check_update=None, note: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("软件设置")
        self.setModal(True)
        self.setMinimumWidth(420)
        self._check_update = check_update
        self._build_ui(note)

    def _build_ui(self, note: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)

        title = QLabel("文件缩略图")
        title.setStyleSheet("color: #F06292; font-weight: bold; font-size: 13px;")
        layout.addWidget(title)

        self.cb_thumbnail = QCheckBox("工程文件显示缩略图")
        self.cb_thumbnail.setChecked(show_project_thumbnail())
        self.cb_thumbnail.toggled.connect(self._on_thumbnail_toggled)
        layout.addWidget(self.cb_thumbnail)

        hint = QLabel(
            note or "勾选：工程文件里保存一张画面缩略图，资源管理器直接显示它。\n"
                    "不勾选：不保存缩略图，资源管理器改为显示工程文件图标。\n"
                    "改动即时生效，对下次保存的工程文件起作用。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        hint.setContentsMargins(22, 0, 0, 0)
        layout.addWidget(hint)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #3d3d3d;")
        layout.addWidget(line)

        title2 = QLabel("自动识别")
        title2.setStyleSheet("color: #F06292; font-weight: bold; font-size: 13px;")
        layout.addWidget(title2)

        row_min = QHBoxLayout()
        row_min.addWidget(QLabel("去除最长边不超过"))
        self.spin_min_area = CompactSpinBox()
        self.spin_min_area.setRange(0, 256)
        self.spin_min_area.setSingleStep(1)
        self.spin_min_area.setValue(detect_min_area())
        self.spin_min_area.setFixedWidth(80)
        self.spin_min_area.setFixedHeight(24)
        self.spin_min_area.valueChanged.connect(self._on_min_area_changed)
        row_min.addWidget(self.spin_min_area)
        row_min.addWidget(QLabel("像素的碎片"))
        # 红色像素块可视化：按阈值大小画红方块
        self.lbl_min_preview = QLabel()
        self.lbl_min_preview.setFixedSize(40, 40)
        self.lbl_min_preview.setStyleSheet("background: #222; border: 1px solid #555;")
        row_min.addWidget(self.lbl_min_preview)
        row_min.addStretch()
        layout.addLayout(row_min)
        self._update_min_preview(self.spin_min_area.value())

        hint_min = QLabel(
            "自动识别图块时，最长边不超过此值的连通区域会被当作噪点忽略。\n"
            "比如设 8：8×8 及以下的小碎片直接去掉。0 = 不过滤。\n"
            "改完对下次打开图片/重新识别生效。"
        )
        hint_min.setWordWrap(True)
        hint_min.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        hint_min.setContentsMargins(22, 0, 0, 0)
        layout.addWidget(hint_min)

        title_sim = QLabel("相似帧")
        title_sim.setStyleSheet("color: #F06292; font-weight: bold; font-size: 13px;")
        layout.addWidget(title_sim)

        row_sim = QHBoxLayout()
        row_sim.addWidget(QLabel("判定容差"))
        self.spin_similar = CompactSpinBox()
        self.spin_similar.setRange(0, 100)
        self.spin_similar.setSingleStep(1)
        self.spin_similar.setValue(similar_tolerance())
        self.spin_similar.setFixedWidth(80)
        self.spin_similar.setFixedHeight(24)
        self.spin_similar.valueChanged.connect(self._on_similar_changed)
        row_sim.addWidget(self.spin_similar)
        row_sim.addWidget(QLabel("%"))
        row_sim.addStretch()
        layout.addLayout(row_sim)

        hint_sim = QLabel(
            "「帧工具 · 相似帧」按这个容差给相似的帧标同一种颜色。\n"
            "0 = 一模一样才算相似（默认；只差一个抗锯齿像素都不算）。\n"
            "1 起步 = 允许形状/颜色有一点差别，数字越大越宽松（建议从 1~5 试着调）。\n"
            "判定看的是「形状重合度 + 有内容处的平均色差」，透明背景不参与。\n"
            "改完把开关按钮重新按一次生效。"
        )
        hint_sim.setWordWrap(True)
        hint_sim.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        hint_sim.setContentsMargins(22, 0, 0, 0)
        layout.addWidget(hint_sim)

        title_hist = QLabel("历史记录")
        title_hist.setStyleSheet("color: #F06292; font-weight: bold; font-size: 13px;")
        layout.addWidget(title_hist)

        row_hist = QHBoxLayout()
        row_hist.addWidget(QLabel("保留最近"))
        self.spin_undo_limit = CompactSpinBox()
        self.spin_undo_limit.setRange(0, 500)
        self.spin_undo_limit.setSingleStep(1)
        self.spin_undo_limit.setValue(undo_limit())
        self.spin_undo_limit.setFixedWidth(80)
        self.spin_undo_limit.setFixedHeight(24)
        self.spin_undo_limit.valueChanged.connect(self._on_undo_limit_changed)
        row_hist.addWidget(self.spin_undo_limit)
        row_hist.addWidget(QLabel("步操作（0-500，默认100）"))
        row_hist.addStretch()
        layout.addLayout(row_hist)

        hint_hist = QLabel(
            "步数太多会占内存、操作多了可能卡。改完重启生效。"
        )
        hint_hist.setWordWrap(True)
        hint_hist.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        hint_hist.setContentsMargins(22, 0, 0, 0)
        layout.addWidget(hint_hist)

        line3 = QFrame()
        line3.setFrameShape(QFrame.Shape.HLine)
        line3.setStyleSheet("color: #3d3d3d;")
        layout.addWidget(line3)

        title_view = QLabel("视图辅助线（标线 / 像素网格）")
        title_view.setStyleSheet("color: #F06292; font-weight: bold; font-size: 13px;")
        layout.addWidget(title_view)

        self.btn_guide_color = _ColorButton(guide_color(), "普通标线")
        self.btn_guide_selected_color = _ColorButton(guide_selected_color(), "选中标线")
        self.btn_grid_color = _ColorButton(pixel_grid_color(), "像素网格")
        self.btn_guide_color.color_changed.connect(
            lambda c: (set_guide_color(c), self.view_colors_changed.emit()))
        self.btn_guide_selected_color.color_changed.connect(
            lambda c: (set_guide_selected_color(c), self.view_colors_changed.emit()))
        self.btn_grid_color.color_changed.connect(
            lambda c: (set_pixel_grid_color(c), self.view_colors_changed.emit()))

        for label, btn in (("普通标线", self.btn_guide_color),
                           ("选中的那条标线", self.btn_guide_selected_color),
                           ("完美像素的每像素网格", self.btn_grid_color)):
            row_color = QHBoxLayout()
            row_color.addWidget(QLabel(label))
            row_color.addWidget(btn)
            row_color.addStretch()
            layout.addLayout(row_color)

        row_grid = QHBoxLayout()
        row_grid.addWidget(QLabel("像素网格显示门槛：放大到"))
        self.spin_grid_zoom = CompactSpinBox()
        self.spin_grid_zoom.setRange(100, 3200)
        self.spin_grid_zoom.setSingleStep(100)
        self.spin_grid_zoom.setValue(pixel_grid_zoom())
        self.spin_grid_zoom.setSuffix(" %")
        self.spin_grid_zoom.setFixedWidth(96)
        self.spin_grid_zoom.setFixedHeight(24)
        self.spin_grid_zoom.valueChanged.connect(
            lambda value: (set_pixel_grid_zoom(value), self.view_colors_changed.emit()))
        row_grid.addWidget(self.spin_grid_zoom)
        row_grid.addWidget(QLabel("以上才显示"))
        row_grid.addStretch()
        layout.addLayout(row_grid)

        btn_row_reset = QHBoxLayout()
        self.btn_reset_colors = QPushButton("恢复默认颜色")
        self.btn_reset_colors.setFixedHeight(26)
        self.btn_reset_colors.clicked.connect(self._on_reset_colors)
        btn_row_reset.addWidget(self.btn_reset_colors)
        btn_row_reset.addStretch()
        layout.addLayout(btn_row_reset)

        hint_view = QLabel(
            "标线：从标尺拖出来的参考线（青色是普通、主题色是当前选中的那条）。\n"
            f"每像素网格：默认半透灰 #{DEFAULT_PIXEL_GRID_COLOR[3:]}。"
            f"为什么默认要 {DEFAULT_PIXEL_GRID_ZOOM}% 才显示 —— 网格线间距就是放大倍数，"
            "100% 时线宽和间距都是 1 像素，整张图会被线盖住。\n"
            "网格颜色可以带透明度（取色盘下面那根滑块）。改完立刻生效。"
        )
        hint_view.setWordWrap(True)
        hint_view.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        hint_view.setContentsMargins(22, 0, 0, 0)
        layout.addWidget(hint_view)

        line2 = QFrame()
        line2.setFrameShape(QFrame.Shape.HLine)
        line2.setStyleSheet("color: #3d3d3d;")
        layout.addWidget(line2)

        title3 = QLabel("更新")
        title3.setStyleSheet("color: #F06292; font-weight: bold; font-size: 13px;")
        layout.addWidget(title3)

        row = QHBoxLayout()
        self.btn_update = QPushButton("检查更新")
        self.btn_update.setFixedHeight(28)
        self.btn_update.clicked.connect(self._on_check_update)
        row.addWidget(self.btn_update)
        lbl_update = QLabel("连接源站检查是否有新版本")
        lbl_update.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        row.addWidget(lbl_update)
        row.addStretch()
        layout.addLayout(row)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.setFixedSize(80, 30)
        btn_close.setStyleSheet(
            "QPushButton { background: #E91E63; color: white; border: none; border-radius: 4px; }"
            "QPushButton:hover { background: #F06292; }"
        )
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    def _on_thumbnail_toggled(self, checked: bool) -> None:
        set_show_project_thumbnail(checked)

    def _on_min_area_changed(self, value: int) -> None:
        set_detect_min_area(value)
        self._update_min_preview(value)

    def _on_similar_changed(self, value: int) -> None:
        set_similar_tolerance(value)

    def _update_min_preview(self, value: int) -> None:
        """在 40x40 预览框里画红色方块，大小 = min(value, 32)。"""
        from PyQt6.QtGui import QPixmap, QPainter, QColor
        pm = QPixmap(40, 40)
        pm.fill(QColor("#222222"))
        p = QPainter(pm)
        sz = min(value, 32)
        if sz > 0:
            p.fillRect((40 - sz) // 2, (40 - sz) // 2, sz, sz, QColor("#E91E63"))
        p.end()
        self.lbl_min_preview.setPixmap(pm)

    def _on_undo_limit_changed(self, value: int) -> None:
        set_undo_limit(value)

    def _on_reset_colors(self) -> None:
        reset_view_colors()
        self.btn_guide_color.set_color(QColor(DEFAULT_GUIDE_COLOR))
        self.btn_guide_selected_color.set_color(QColor(DEFAULT_GUIDE_SELECTED_COLOR))
        self.btn_grid_color.set_color(QColor(DEFAULT_PIXEL_GRID_COLOR))
        self.view_colors_changed.emit()

    def _on_check_update(self) -> None:
        if self._check_update is not None:
            self._check_update()

    def thumbnail_enabled(self) -> bool:
        return self.cb_thumbnail.isChecked()
