"""Apply the checkerboard tiling optimization to the three expensive paint sites.

Replaces per-cell fillRect loops with a single cached-tile fillRect/drawTiledPixmap
via widgets.paint_checkerboard. Fails loudly if any expected snippet is not found
exactly once, so a partial/incorrect patch cannot pass silently.

Usage: python tools/patch_checker.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PATCHES = [
    # ---- _Canvas.paintEvent (原图区画布) ----
    (
        ROOT / "spritesheet/ui/image_viewer.py",
        "from .widgets import RULER_SIZE, _Ruler, make_tool_icon",
        "from .widgets import RULER_SIZE, _Ruler, make_tool_icon, paint_checkerboard",
        None,
    ),
    (
        ROOT / "spritesheet/ui/image_viewer.py",
        """        if self._viewer._bg_mode == "checker":
            cell = 12
            for y in range(0, self.height(), cell):
                for x in range(0, self.width(), cell):
                    if (x // cell + y // cell) % 2 == 0:
                        p.fillRect(x, y, cell, cell, QColor("#2a2a2a"))
                    else:
                        p.fillRect(x, y, cell, cell, QColor("#1e1e1e"))
""",
        """        if self._viewer._bg_mode == "checker":
            # 一次平铺（原逐格 fillRect 在大画布上每次重绘要数千次调用）
            paint_checkerboard(p, self.rect(), 12)
""",
        None,
    ),
    # ---- PreviewCanvas.paintEvent (预览画布，高频重绘) ----
    (
        ROOT / "spritesheet/ui/preview.py",
        """from .widgets import (
    CompactComboBox, CompactSpinBox, EditableNumberLabel, _GROUP_QSS, _SLIDER_QSS,
    make_tool_icon,
)""",
        """from .widgets import (
    CompactComboBox, CompactSpinBox, EditableNumberLabel, _GROUP_QSS, _SLIDER_QSS,
    make_tool_icon, paint_checkerboard,
)""",
        None,
    ),
    (
        ROOT / "spritesheet/ui/preview.py",
        """        elif self._action.bg_mode == "checker":
            cell = max(4, int(12 * background_scale))
            for y in range(0, background_rect.height(), cell):
                for x in range(0, background_rect.width(), cell):
                    if ((x // cell) + (y // cell)) % 2 == 0:
                        p.fillRect(x, y, cell, cell, QColor("#2a2a2a"))
                    else:
                        p.fillRect(x, y, cell, cell, QColor("#1e1e1e"))
""",
        """        elif self._action.bg_mode == "checker":
            # 一次平铺：原来 cell 最小 4，1000x800 下每帧最多约 5 万次 fillRect
            paint_checkerboard(p, background_rect, max(4, int(12 * background_scale)))
""",
        None,
    ),
    # ---- PreviewArea.paintEvent (导出弹窗预览) ----
    (
        ROOT / "spritesheet/ui/export_dialog.py",
        "from .widgets import ActionThumbPlayer, SwitchWidget",
        "from .widgets import ActionThumbPlayer, SwitchWidget, paint_checkerboard",
        None,
    ),
    (
        ROOT / "spritesheet/ui/export_dialog.py",
        """        # 棋盘格背景
        cell = 10
        for y in range(0, self.height(), cell):
            for x in range(0, self.width(), cell):
                c = "#2a2a2a" if (x // cell + y // cell) % 2 == 0 else "#1e1e1e"
                p.fillRect(x, y, cell, cell, QColor(c))
""",
        """        # 棋盘格背景（一次平铺，替代逐格 fillRect）
        paint_checkerboard(p, self.rect(), 10)
""",
        None,
    ),
]


def main() -> int:
    failed = False
    for path, old, new, _ in PATCHES:
        text = path.read_text(encoding="utf-8")
        count = text.count(old)
        if count != 1:
            print(f"FAIL  {path.name}: 期望命中 1 次，实际 {count} 次")
            failed = True
            continue
        path.write_text(text.replace(old, new), encoding="utf-8")
        print(f"OK    {path.name}: 已替换")
    if failed:
        print("补丁未全部应用，未做任何进一步修改")
        return 1
    print("checkerboard 补丁全部应用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
