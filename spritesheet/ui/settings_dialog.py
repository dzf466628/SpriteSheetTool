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

from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout,
)

from spritesheet.settings import (
    detect_min_area, set_detect_min_area,
    set_show_project_thumbnail, show_project_thumbnail,
    set_undo_limit, undo_limit,
)


class SettingsDialog(QDialog):
    """软件设置：开关即时保存；检查更新转发给主窗口。"""

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
        self.spin_min_area = QSpinBox()
        self.spin_min_area.setRange(0, 256)
        self.spin_min_area.setSingleStep(1)
        self.spin_min_area.setValue(detect_min_area())
        self.spin_min_area.setFixedWidth(80)
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

        title_hist = QLabel("历史记录")
        title_hist.setStyleSheet("color: #F06292; font-weight: bold; font-size: 13px;")
        layout.addWidget(title_hist)

        row_hist = QHBoxLayout()
        row_hist.addWidget(QLabel("保留最近"))
        self.spin_undo_limit = QSpinBox()
        self.spin_undo_limit.setRange(0, 500)
        self.spin_undo_limit.setSingleStep(1)
        self.spin_undo_limit.setValue(undo_limit())
        self.spin_undo_limit.setFixedWidth(80)
        self.spin_undo_limit.valueChanged.connect(self._on_undo_limit_changed)
        row_hist.addWidget(self.spin_undo_limit)
        row_hist.addWidget(QLabel("步操作（0-30，默认10）"))
        row_hist.addStretch()
        layout.addLayout(row_hist)

        hint_hist = QLabel(
            "步数太多会占内存、操作多了可能卡。改完重启生效。"
        )
        hint_hist.setWordWrap(True)
        hint_hist.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        hint_hist.setContentsMargins(22, 0, 0, 0)
        layout.addWidget(hint_hist)

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

    def _on_check_update(self) -> None:
        if self._check_update is not None:
            self._check_update()

    def thumbnail_enabled(self) -> bool:
        return self.cb_thumbnail.isChecked()
