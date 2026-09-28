# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""当前帧微调（逐帧偏移）回归。

用户报的两句话：
  「应该是调整单独帧，而且不能影响其他帧（一个帧一个偏移度）」
  「左右滑块好像不是居中的」

查下来数据层本来就是逐帧的（`action.frame_offsets[idx]`），坏的是**显示**：
`set_current_frame()` 只同步了变换控件，**没同步偏移控件** —— 切到没有偏移的帧时，
滑块和数字框还停在上一个帧的值上。于是看起来"偏移是所有帧共用的"、"滑块不居中"。
实测：第 0 帧设 (30,-12) 后切到第 1 帧，滑块仍显示 (30,-12)。

另外：两个偏移滑块是**以 0 为中心**的（-200~+200），原来用普通滑块样式，
QSlider 的 sub-page 会"从最左填到当前值"，值为 0 时左半边全亮 —— 看着像只填了一半。
手柄本身其实是正中的（实测偏差 0.5px），所以改成双向样式（不填 sub-page）。

用法: .venv\\Scripts\\python.exe tools\\verify_frame_offset.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK' if ok else 'FAIL'}]   {label} {detail}")
    if not ok:
        FAILURES.append(label)


def make_action(frames: int = 3):
    from spritesheet.ui.models import Action

    action = Action("t", "#C62828")
    for i in range(frames):
        pm = QPixmap(60, 60)
        pm.fill(QColor("#C62828" if i == 0 else "#1565C0"))
        action.frames.append(pm)
        action.frame_rects.append((0, 0, 60, 60))
        action.frame_parts.append([((0, 0, 60, 60), 0, 0, 0, 0)])
    return action


def main() -> int:
    app = QApplication(sys.argv)
    from spritesheet.ui.theme import APP_QSS

    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)

    from spritesheet.ui.preview import ActionPreviewWidget

    widget = ActionPreviewWidget()
    action = make_action()
    widget.set_actions([action])
    widget.set_action(action)
    widget.resize(420, 900)
    widget.show()
    app.processEvents()
    panel = widget.adjust_panel

    print("=== 1. 切帧时偏移控件要跟着换（这就是看起来像共用的原因）===")
    panel.set_current_frame(0)
    panel._set_current_offset(30, -12)
    app.processEvents()
    check("第 0 帧的偏移写进了数据", action.frame_offsets.get(0) == (30, -12),
          f"{action.frame_offsets}")
    panel.set_current_frame(1)
    app.processEvents()
    print(f"  切到第 1 帧：滑块=({panel.slider_off_h.value()}, {panel.slider_off_v.value()}) "
          f"数字框=({panel.spin_off_x.value()}, {panel.spin_off_y.value()})")
    check("第 0 帧的偏移没被复制到第 1 帧", 1 not in action.frame_offsets,
          f"{action.frame_offsets}")
    check("切到没偏移的帧后，滑块回到 0（居中）",
          panel.slider_off_h.value() == 0 and panel.slider_off_v.value() == 0,
          f"({panel.slider_off_h.value()}, {panel.slider_off_v.value()})")
    check("数字框也回到 0",
          panel.spin_off_x.value() == 0 and panel.spin_off_y.value() == 0,
          f"({panel.spin_off_x.value()}, {panel.spin_off_y.value()})")
    panel.set_current_frame(0)
    app.processEvents()
    check("切回第 0 帧，控件显示回它自己的偏移",
          panel.slider_off_h.value() == 30 and panel.slider_off_v.value() == -12,
          f"({panel.slider_off_h.value()}, {panel.slider_off_v.value()})")

    print()
    print("=== 2. 拖滑块只改当前帧 ===")
    panel.set_current_frame(1)
    panel.slider_off_h.setValue(45)
    panel.slider_off_v.setValue(8)
    app.processEvents()
    print(f"  frame_offsets={action.frame_offsets}")
    check("第 1 帧改成了 (45,8)", action.frame_offsets.get(1) == (45, 8))
    check("第 0 帧原样不动（(30,-12)）", action.frame_offsets.get(0) == (30, -12),
          f"{action.frame_offsets.get(0)}")
    check("第 2 帧没被牵连", 2 not in action.frame_offsets, f"{action.frame_offsets}")

    print()
    print("=== 3. 方向按钮 / 重置也只作用于当前帧 ===")
    panel.set_current_frame(2)
    panel._adjust_frame(1, 0)
    panel._adjust_frame(0, -1)
    app.processEvents()
    check("方向按钮改的是第 2 帧", action.frame_offsets.get(2) == (1, -1),
          f"{action.frame_offsets.get(2)}")
    check("第 0、1 帧不受影响",
          action.frame_offsets.get(0) == (30, -12) and action.frame_offsets.get(1) == (45, 8),
          f"{action.frame_offsets}")
    panel._reset_frame_offset()
    app.processEvents()
    check("重置只清掉第 2 帧", 2 not in action.frame_offsets, f"{action.frame_offsets}")
    check("重置后控件回到 0",
          panel.slider_off_h.value() == 0 and panel.slider_off_v.value() == 0)
    check("重置不影响其他帧",
          action.frame_offsets.get(0) == (30, -12) and action.frame_offsets.get(1) == (45, 8),
          f"{action.frame_offsets}")

    print()
    print("=== 4. 滑块手柄在值为 0 时确实居中（逐像素量）===")
    slider = panel.slider_off_h
    slider.setValue(0)
    app.processEvents()
    image = slider.grab().toImage()
    width, height = image.width(), image.height()

    def is_accent(x, y) -> bool:
        c = image.pixelColor(x, y)
        return (abs(c.red() - 0xE9) < 60 and abs(c.green() - 0x1E) < 80
                and abs(c.blue() - 0x63) < 80)

    columns = {x: [y for y in range(height) if is_accent(x, y)] for x in range(width)}
    accent_cols = [x for x, ys in columns.items() if ys]
    thickest = max(len(ys) for ys in columns.values())
    handle_cols = [x for x, ys in columns.items() if len(ys) == thickest]
    handle_center = (min(handle_cols) + max(handle_cols)) / 2.0
    print(f"  滑块宽 {width}：手柄中心 {handle_center:.1f}，中线 {width / 2:.1f}，"
          f"偏差 {handle_center - width / 2:+.1f}px")
    check("值为 0 时手柄在中线上（偏差 <= 2px）",
          abs(handle_center - width / 2) <= 2.0,
          f"{handle_center - width / 2:+.1f}px")
    check("强调色只出现在手柄上（不再是从最左填到中间）",
          (max(accent_cols) - min(accent_cols) + 1) <= 12,
          f"强调色覆盖 {min(accent_cols)}..{max(accent_cols)}"
          f"（宽 {max(accent_cols) - min(accent_cols) + 1}px，手柄只有 8px）")

    print()
    print("=== 5. 偏移会推出画布时，其他帧在画布内的位置不被改写 ===")
    from spritesheet.ui.transform import compute_preview_layout

    for offset in ((0, 0), (-30, 0), (30, 12)):
        action.frame_offsets = {0: offset}
        _w, _h, positions, _ = compute_preview_layout(action, 0)
        others = positions[1:]
        check(f"  第 0 帧偏移 {offset}：第 1、2 帧相对位置一致（都在同一处）",
              others[0] == others[1], f"{positions}")

    widget.close()
    print()
    if FAILURES:
        print("FRAME OFFSET FAILED：", *FAILURES, sep="\n  - ")
        return 1
    print("FRAME OFFSET OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
