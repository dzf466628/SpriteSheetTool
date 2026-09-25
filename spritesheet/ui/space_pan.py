#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""空格 = 按住拖动（**应用级**）。

用户要求：「鼠标放在预览的窗口 按空格可以随意拖动」。只在画布自己的 keyPressEvent 里认空格
不够用 —— 空格是发给**有焦点**的控件的，鼠标悬停时画布没焦点，键根本到不了它（而且 Qt 里
空格会去「按」当前有焦点的按钮）。所以这里装一个应用级事件过滤器：按住空格就把"正在拖"这个
状态喂给鼠标底下那些预览画布，松开再收回；空格被吃掉，不会跑去点按钮。

画布不用改：`PreviewCanvas` / 抽帧窗口的预览都有 `_panning` / `_pan_start` 这套状态与
左键拖动逻辑，喂状态就行。
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QAbstractSpinBox, QApplication, QLineEdit, QTextEdit


class SpacePanFilter(QObject):
    """应用级空格监听：targets_provider() 返回"要喂状态的预览控件"（可以随时变）。"""

    def __init__(self, targets_provider, parent=None) -> None:
        super().__init__(parent)
        self.targets_provider = targets_provider

    def targets(self):
        try:
            return [w for w in (self.targets_provider() or ()) if w is not None]
        except Exception:  # noqa: BLE001 — 取目标失败不能影响键盘事件
            return []

    def eventFilter(self, obj, event):  # noqa: N802 (Qt 命名)
        etype = event.type()
        if etype in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) \
                and event.key() == Qt.Key.Key_Space:
            if event.isAutoRepeat():
                return True                      # 一直按住：重复事件吞掉，状态别抖
            focus = QApplication.focusWidget()
            if isinstance(focus, (QLineEdit, QTextEdit, QAbstractSpinBox)):
                return False                     # 正在输入框里打字：空格照旧给它
            held = etype == QEvent.Type.KeyPress
            for target in self.targets():
                try:
                    target.setMouseTracking(True)   # 悬停也要能换光标
                    target._panning = held
                    if not held:
                        target._pan_start = None
                    target.setCursor(Qt.CursorShape.OpenHandCursor if held
                                     else Qt.CursorShape.ArrowCursor)
                except Exception:  # noqa: BLE001 — 某个控件喂不动不能拖累别的
                    continue
            # **绝不能吃掉空格**：原图那边（image_viewer）的「空格拖动 / 空格临时让位工具」
            # 本来就是靠这个键自己处理的，一吃就把人家掐了（用户发现的回归）。
            # 抽帧窗口/预览那边"空格会去点焦点按钮"的毛病，用按钮 NoFocus 解决，不靠吞键。
            return False
        return super().eventFilter(obj, event)
