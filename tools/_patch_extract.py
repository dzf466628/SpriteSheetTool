"""一次性补丁：把「抽帧窗口」和「空格拖动」接上（逐条断言锚点唯一，全成功才落盘）。

为什么用脚本改而不是一条条手改：这轮要动 4 处小地方（提缩略图构造函数 / 工具按钮不抢空格 /
预览画布拿焦点 / 待定按钮改抽帧并接窗口），一条条改容易漏；这里每条都先断言"锚点唯一"，
任何一条对不上就整批不写盘。换行符按原文件（preview.py/main_window.py 是 CRLF）保持一致。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "spritesheet" / "ui"

THUMB_FUNC = '''def build_frame_thumb(action, index: int, color=None) -> QPixmap:
    """一帧的缩略图（36x36）：图像居中 + **右下角帧序号** + **右上角相似帧彩块**。

    帧缩略图和抽帧窗口**共用这一份** —— 两处必须长得一样（用户要求抽帧窗口的缩略图
    也要显示帧号和相似帧彩块）。色块画右上角，右下角留给帧序号。
    """
    pm = action_frame_pixmap(action, index)
    thumb = QPixmap(36, 36)
    thumb.fill(Qt.GlobalColor.transparent)
    painter = QPainter(thumb)
    if pm is not None and not pm.isNull():
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        scaled = pm.scaled(30, 30, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap((36 - scaled.width()) // 2, (36 - scaled.height()) // 2, scaled)
    painter.setPen(QColor("#E0E0E0"))
    painter.setFont(QFont("Microsoft YaHei UI", 7, QFont.Weight.Bold))
    painter.drawText(QRect(18, 20, 14, 12),
                     Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, str(index + 1))
    if color is not None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRect(24, 3, 9, 9), 2, 2)
    painter.end()
    return thumb


'''

OLD_THUMB_BLOCK = """            item = QListWidgetItem()
            pm = action_frame_pixmap(self._action, i)"""

NEW_THUMB_BLOCK = """            item = QListWidgetItem()
            _mark = None
            if self.similar_color_provider is not None:
                try:
                    _mark = self.similar_color_provider(self._action, i)
                except Exception:  # noqa: BLE001 — 标色失败绝不能影响缩略图重建
                    _mark = None
            item.setIcon(QIcon(build_frame_thumb(self._action, i, _mark)))"""

OLD_PENDING = """        # 第三颗：用户说"右边再加一个待定按钮"—— 还没定要做什么，所以**禁用 + 写明**，
        # 不做"点了没反应"的按钮（跟当初「相似帧」占位时一个规矩）。
        self.btn_pending = self._tool_btn(
            "待定", "待定：这颗按钮还没定要做什么 —— 先占个位，不点也点不动。\\n"
                    "想好了告诉我，我再接上。", enabled=False)
        tools_row.addWidget(self.btn_pending)"""

NEW_EXTRACT = """        # 第三颗「抽帧」：点开抽帧窗口（500x600：预览 + 进度条 + 帧缩略图 + 4 个抽帧选项）
        self.btn_extract = self._tool_btn(
            "抽帧", "抽帧：打开抽帧窗口（500×600）—— 上面预览当前动作，中间进度条 + 帧缩略图"
                    "（带帧序号和相似帧彩块），下面 4 个抽帧选项。\\n"
                    "这一版先把位置摆好，4 个选项还没实装。")
        tools_row.addWidget(self.btn_extract)"""

CHANGES = [
    # (文件, 说明, [(old, new), ...])
    ("preview.py", "提出共用的缩略图构造函数",
     [("class AnimAdjustPanel(QWidget):", THUMB_FUNC + "class AnimAdjustPanel(QWidget):")]),
    ("preview.py", "缩略图改用共用函数（帧号+右上角彩块一模一样）",
     [(OLD_THUMB_BLOCK, NEW_THUMB_BLOCK)]),
    ("preview.py", "工具按钮不抢空格键（Qt 里空格 = 按一下焦点按钮）",
     [("    def _tool_btn(self, text: str, tip: str, enabled: bool = True) -> QPushButton:",
       "    def _tool_btn(self, text: str, tip: str, enabled: bool = True) -> QPushButton:\n"
       "        # 按钮一律不抢键盘焦点：否则点过按钮之后空格会去「按」它，预览就没法空格拖动了\n"
       "        # （Qt 默认行为）。空格的活儿留给画布。")]),
    ("preview.py", "预览画布拿 StrongFocus（空格才到得了画布）",
     [("        self._panning = False\n        self._pan_start = None",
       "        self._panning = False\n        self._pan_start = None\n"
       "        # 必须能收键盘：不然空格键根本到不了这里（用户反馈「按住空格拖不动」多是这个）\n"
       "        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)")]),
    ("preview.py", "待定按钮改成抽帧按钮并加信号",
     [(OLD_PENDING, NEW_EXTRACT),
      ("    similar_toggled = pyqtSignal(bool)  # 「相似帧」开关：True = 按下开始标色，False = 松开清掉",
       "    similar_toggled = pyqtSignal(bool)  # 「相似帧」开关：True = 按下开始标色，False = 松开清掉\n"
       "    extract_requested = pyqtSignal()     # 「抽帧」：由主窗口开抽帧窗口")]),
    ("main_window.py", "接上抽帧按钮 → 开窗口",
     [("        self.preview.adjust_panel.similar_enable_provider = self._similar_enable_state",
       "        self.preview.adjust_panel.similar_enable_provider = self._similar_enable_state\n"
       "        self.preview.adjust_panel.extract_requested.connect(self._on_extract)\n"
       "        # 换动作/换帧时，抽帧窗口开着就跟着刷\n"
       "        self.preview.action_changed.connect(lambda _a: self._refresh_extract_dialog())")]),
    ("main_window.py", "加 _on_extract / _refresh_extract_dialog",
     [("    def _similar_enable_state(self):",
       "    def _on_extract(self) -> None:\n"
       "        \"\"\"「抽帧」按钮：开抽帧窗口（500×600，预览 + 进度条 + 缩略图 + 4 个抽帧选项）。\n\n"
       "        窗口只开一个、反复点就复用（跟预览一样，不叠一堆窗口）。非模态：开着窗口\n"
       "        照样能操作主界面。选项按钮这版只占位、禁用并写明。\n"
       "        \"\"\"\n"
       "        from spritesheet.ui.extract_dialog import ExtractDialog\n"
       "        dialog = getattr(self, \"_extract_dialog\", None)\n"
       "        if dialog is None:\n"
       "            dialog = ExtractDialog(parent=self)\n"
       "            self._extract_dialog = dialog     # 留着引用：绝不能让它被回收\n"
       "        self._refresh_extract_dialog()\n"
       "        dialog.show()\n"
       "        dialog.raise_()\n"
       "        dialog.activateWindow()\n\n"
       "    def _refresh_extract_dialog(self) -> None:\n"
       "        \"\"\"抽帧窗口开着的时候，把颜色回调和当前动作推过去（关着就什么都不做）。\"\"\"\n"
       "        dialog = getattr(self, \"_extract_dialog\", None)\n"
       "        if dialog is None or not dialog.isVisible():\n"
       "            return\n"
       "        dialog.color_provider = self._similar_color_for_frame\n"
       "        dialog.set_action(self.image_viewer.current_action)\n\n"
       "    def _similar_enable_state(self):")]),
]

TEST_CHANGES = [
    ("verify_auto_align.py", "第三颗按钮现在是「抽帧」（待定已实装）",
     [('    check("第三颗「待定」占位但禁用、写明还没定（不留点了没反应的按钮）",\n'
       '          not panel.btn_pending.isEnabled() and "还没定" in panel.btn_pending.toolTip(),\n'
       '          panel.btn_pending.toolTip())',
       '    check("第三颗是「抽帧」且已实装（点了会开抽帧窗口）",\n'
       '          panel.btn_extract.isEnabled() and "抽帧窗口" in panel.btn_extract.toolTip(),\n'
       '          panel.btn_extract.toolTip())')]),
]


def apply(path: Path, changes) -> None:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    crlf = "\r\n" in text
    for _file, label, pairs in changes:
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
    apply(UI / "preview.py", [c for c in CHANGES if c[0] == "preview.py"])
    apply(UI / "main_window.py", [c for c in CHANGES if c[0] == "main_window.py"])
    apply(ROOT / "tools" / "verify_auto_align.py", TEST_CHANGES)
    return 0


if __name__ == "__main__":
    sys.exit(main())