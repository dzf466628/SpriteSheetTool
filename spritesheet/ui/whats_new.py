# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

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
    ("MCP 打包稳定性修复", "客户端结束连接或提交空笔刷路径时不再导致程序弹出未处理异常窗口；错误会正常返回给 MCP 客户端。"),
    ("笔刷参数校验补全", "动图文档的 stroke.segs 现在会检查坐标数量和数字有效性，避免无效操作写入工程。"),
    ("MCP 操作引导补全", "创建工程、创建动作、添加帧的顺序和内部字段边界现在会直接告诉 AI 客户端，避免自行拼工程清单。"),
    ("MCP 版本与工程打开修复", "MCP 握手现在正确显示 SpriteSheetTool 版本；修复 MCP 创建的工程打开时报错、动作不显示的问题。"),
    ("MCP 工具扩展到 30 个", "可以创建工程、编辑动作和帧、管理源图、处理动图文档，并导出 PNG、GIF、逐帧 PNG、" 
                              "本工具 JSON、TexturePacker 和 Aseprite 格式。"),
    ("抠图保留两种模式", "保留传统色键和卡通背景两种抠图模式；桌面端与 MCP headless 都能使用，动图文档会重放操作链。"),
    ("Windows 打包启动修复", "修复新版本直接启动时 Qt DLL 加载失败，以及 MCP 无界面模式仍提前加载 Qt 的问题。"),
    ("多部件帧导入更完整", "导入合成帧时保留所有部件、位置和逐帧偏移，重排、复制和导出后的画面保持一致。"),
    ("缩略图条焕新", "预览缩略图滚动条加高、鼠标放上去有悬浮动画；底部黑色控制条压到 15 像素高，"
                     "省出来的空间全部给缩略图滑块。"),
    ("缩略图右键删帧", "预览和抽帧窗口里都能右键缩略图选「删除此帧」：删完后面的帧自动补位、"
                       "画布落点同步重排、Ctrl+Z 一步退回；抽帧窗口里「取消」整轮还原、「确定」即定案。"),
    ("MCP 工具大扩编", "给 AI 客户端（MCP 协议）加了一整套工具：工程总览图、动作全部帧、健康检查、"
                      "相似帧分组、逐帧运动轨迹；外加写回编辑 —— 改帧率/循环/锚点/重命名、删动作、"
                      "删帧，每次写回前自动备份原文件。"),
    ("MCP 设置可见", "「MCP 设置…」菜单里列出已注册的全部工具（名称 + 用途）。"),
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
