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
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from spritesheet.settings import set_show_project_thumbnail, show_project_thumbnail


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

        title2 = QLabel("更新")
        title2.setStyleSheet("color: #F06292; font-weight: bold; font-size: 13px;")
        layout.addWidget(title2)

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

    def _on_check_update(self) -> None:
        if self._check_update is not None:
            self._check_update()

    def thumbnail_enabled(self) -> bool:
        return self.cb_thumbnail.isChecked()
