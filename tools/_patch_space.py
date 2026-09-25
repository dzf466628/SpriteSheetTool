"""一次性补丁②：空格拖动改成**应用级**，并建 space_pan.py。

为什么推倒重来：用户实测「鼠标放在预览窗口上按空格还是不能拖动，指针都没变」——
因为空格是发给**有焦点**的控件的，鼠标只是悬停时画布根本没焦点（而且按钮会把空格吃掉）。
所以在应用级事件过滤器里统一收空格，再广播"空格是否按住"给预览画布。
画布本身不用改：它们都有 `_panning` / `_pan_start` 这套状态，这里直接喂状态即可。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "spritesheet" / "ui"

SPACE_PAN = '''#!/usr/bin/env python
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
        if etype in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) \\
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
                    target.setCursor(Qt.CursorShape.ClosedHandCursor if held
                                     else Qt.CursorShape.OpenHandCursor)
                except Exception:  # noqa: BLE001 — 某个控件喂不动不能拖累别的
                    continue
            return True                          # 吃掉空格：别让它去「按」焦点按钮
        return super().eventFilter(obj, event)
'''

PREVIEW_CHANGES = [
    ("preview.py", "缩略图函数支持放大（抽帧窗口用 64×64，帧条仍是 36）",
     [('def build_frame_thumb(action, index: int, color=None) -> QPixmap:',
       'def build_frame_thumb(action, index: int, color=None, size: int = 36) -> QPixmap:'),
      ('    pm = action_frame_pixmap(action, index)\n    thumb = QPixmap(36, 36)',
       '    pm = action_frame_pixmap(action, index)\n'
       '    thumb = QPixmap(size, size)      # size 可放大：抽帧窗口要 64×64 更好认'),
      ('        scaled = pm.scaled(30, 30, Qt.AspectRatioMode.KeepAspectRatio,\n'
       '                           Qt.TransformationMode.SmoothTransformation)\n'
       '        painter.drawPixmap((36 - scaled.width()) // 2, (36 - scaled.height()) // 2, scaled)',
       '        inner = int(size * 30 / 36)      # 内部留白按比例走，放大后一样好看\n'
       '        scaled = pm.scaled(inner, inner, Qt.AspectRatioMode.KeepAspectRatio,\n'
       '                           Qt.TransformationMode.SmoothTransformation)\n'
       '        painter.drawPixmap((size - scaled.width()) // 2,\n'
       '                           (size - scaled.height()) // 2, scaled)'),
      ('    painter.drawText(QRect(18, 20, 14, 12),\n'
       '                     Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, str(index + 1))',
       '    painter.drawText(QRect(size // 2, int(size * 20 / 36), size // 2 - 4, int(size * 12 / 36)),\n'
       '                     Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, str(index + 1))'),
      ('        painter.drawRoundedRect(QRect(24, 3, 9, 9), 2, 2)',
       '        side = max(8, int(size * 9 / 36))     # 相似帧彩块：右上角，跟着放大\n'
       '        painter.drawRoundedRect(\n'
       '            QRect(size - side - 3, 3, side, side), 2, 2)')]),
]

MAIN_CHANGES = [
    ("main_window.py", "装应用级空格过滤器",
     ["        self.preview.adjust_panel.extract_requested.connect(self._on_extract)",
      "        self.preview.adjust_panel.extract_requested.connect(self._on_extract)\n"
      "        # 空格 = 按住拖动（应用级过滤）：鼠标悬停在预览上按空格就能拖，不用先点画布拿焦点\n"
      "        from PyQt6.QtWidgets import QApplication\n"
      "        from spritesheet.ui.space_pan import SpacePanFilter\n"
      "        self._space_pan = SpacePanFilter(self._space_pan_targets, self)\n"
      "        QApplication.instance().installEventFilter(self._space_pan)"]),
    ("main_window.py", "空格喂给哪些预览",
     ["    def _similar_enable_state(self):",
      "    def _space_pan_targets(self):\n"
      "        \"\"\"空格要喂状态的预览控件：右侧预览画布 + 抽帧窗口的预览（开着才有）。\"\"\"\n"
      "        targets = [getattr(self.preview, \"canvas\", None)]\n"
      "        dialog = getattr(self, \"_extract_dialog\", None)\n"
      "        if dialog is not None:\n"
      "            targets.append(getattr(dialog, \"preview\", None))\n"
      "        return targets\n\n"
      "    def _similar_enable_state(self):"]),
]


def apply(path: Path, changes) -> None:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    crlf = "\r\n" in text
    for label, pair in changes:
        pairs = pair if isinstance(pair, list) else [pair]
        for old, new in pairs:
            if crlf:
                old = old.replace("\r\n", "\n").replace("\n", "\r\n")
                new = new.replace("\r\n", "\n").replace("\n", "\r\n")
            count = text.count(old)
            if count != 1:
                raise SystemExit(f"[BAD] {path.name} / {label}: 锚点出现 {count} 次（要求 1 次）")
            text = text.replace(old, new)
    path.write_bytes(text.encode("utf-8"))
    print(f"[OK] {path.name}: {len(changes)} 处改完")


def main() -> int:
    (UI / "space_pan.py").write_bytes(SPACE_PAN.encode("utf-8"))
    print("[OK] 新建 space_pan.py")
    apply(UI / "preview.py", PREVIEW_CHANGES)
    apply(UI / "main_window.py", MAIN_CHANGES)
    return 0


if __name__ == "__main__":
    sys.exit(main())