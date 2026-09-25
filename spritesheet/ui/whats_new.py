"""「本次更新」窗口：**更新完第一次打开软件时显示一次**（用户要求：只显示一次）。

口径
----
* 只在"当前版本号 != 上次看过的版本号"时弹一次；点「知道了」后记下版本号，之后不再弹。
* 记忆写在 `%APPDATA%\\SpriteSheetTool\\whats_new.json` —— **不动用户的任何设置项**
  （这个项目里用户设置是要保护的，不能拿它当我们的记事本）。
* 一段简短的、用户视角的说明。**不写统计口径那类小字**（用户明确要求去掉）。
* 任何异常都静默：这个窗口只是"打招呼"，绝不能因为它出不来就连累启动。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from spritesheet import __version__

# 用户视角的要点（跟 README「版本历史」那行是同一批事，这里只留最关键的几条）
UPDATES = [
    ("抽帧（全新）", "「帧工具」第三颗按钮开独立窗口：四档互斥按钮 抽帧3/5｜1/2｜1/4｜强力去重，"
                     "只把**相邻且同色**的帧当一串、每串留最标准那一帧；点即抽、实时看到结果，"
                     "Ctrl+Z 可退，「取消」整轮撤回。"),
    ("相似帧", "从占位变成实装：把全图元素里长得像的那些标成彩块，和抽帧共用同一套相似度。"),
    ("真进度条", "抽帧 / 相似帧 / 自动对齐 都改成按 n/N 显示的真进度，不是转圈、也不是匀速假条。"),
    ("导入更省心", "单个大文件按**真实字节数**报进度；多个文件或拖文件夹时按「第 n/N 个」"
                   "依次追加成一张张原图（顺带修掉拖文件夹没反应的毛病）。"),
    ("修掉两个真 bug", "抽帧后后面的元素会位移、换档位越抽越少 —— 成因和证据都记在 NEXT.md。"),
    ("退出更干净", "关掉抽帧 / 导出窗口不再刷 GetDC failed 与 External WM_DESTROY。"),
]




def _state_path() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / "SpriteSheetTool" / "whats_new.json"


def seen_version() -> str:
    """上次看过的版本号；读不到就返回空串（当成"没见过"）。"""
    try:
        path = _state_path()
        if path.is_file():
            return str(json.loads(path.read_text(encoding="utf-8")).get("version") or "")
    except Exception:  # noqa: BLE001
        pass
    return ""


def mark_seen(version: str = "") -> None:
    """记下"这一版已经看过了"。失败静默（大不了下次再弹一次）。"""
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": version or __version__}), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


class WhatsNewDialog(QDialog):
    """本次更新说明：标题 + 要点列表 + 统计口径一行 + 「知道了」。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("本次更新")
        self.setModal(True)
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)

        title = QLabel(f"更新完成 —— SpriteSheetTool v{__version__}")
        title.setStyleSheet("color: #ffffff; font-size: 17px; font-weight: bold;")
        layout.addWidget(title)

        sub = QLabel("这一版新加了这些（点「知道了」后不再提示）：")
        sub.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        layout.addWidget(sub)

        for name, desc in UPDATES:
            item = QLabel(f"<b style='color:#E91E63;'>{name}</b> —— "
                          f"<span style='color:#d8d8d8;'>{desc.replace('**', '')}</span>")
            item.setStyleSheet("font-size: 11px;")
            item.setWordWrap(True)
            layout.addWidget(item)

        layout.addSpacing(4)
        row = QHBoxLayout()
        row.addStretch()
        ok = QPushButton("知道了")
        ok.setFixedWidth(96)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        layout.addLayout(row)


def maybe_show(parent=None) -> bool:
    """该弹就弹一次（并记下版本），不该弹就直接返回 False。全程异常静默。

    返回 True 表示"这次真的弹了"（探针用得上）。
    """
    try:
        if seen_version() == __version__:
            return False
        dialog = WhatsNewDialog(parent)
        dialog.exec()
        mark_seen(__version__)
        return True
    except Exception:  # noqa: BLE001
        return False