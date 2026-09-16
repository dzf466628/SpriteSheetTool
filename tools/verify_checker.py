"""Pixel-exact verification of the checkerboard tiling optimization.

Renders the checkerboard with the ORIGINAL per-cell fillRect loop and with the
new cached-tile ``paint_checkerboard``, then compares the two QImages byte for
byte across many sizes and cell widths (including non-multiple sizes, which is
where tiling phase bugs would show up).

Usage: .venv\\Scripts\\python.exe tools\\verify_checker.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spritesheet.ui.widgets import paint_checkerboard

A, B = "#2a2a2a", "#1e1e1e"


def render_loop(w, h, cell):
    """Original implementation: per-cell fillRect (reference)."""
    pm = QPixmap(w, h)
    pm.fill(QColor("#000000"))
    p = QPainter(pm)
    for y in range(0, h, cell):
        for x in range(0, w, cell):
            c = A if (x // cell + y // cell) % 2 == 0 else B
            p.fillRect(x, y, cell, cell, QColor(c))
    p.end()
    return pm.toImage().convertToFormat(QImage.Format.Format_RGBA8888)


def render_tiled(w, h, cell):
    """New implementation: cached 2x2 tile + one drawTiledPixmap."""
    pm = QPixmap(w, h)
    pm.fill(QColor("#000000"))
    p = QPainter(pm)
    paint_checkerboard(p, pm.rect(), cell, A, B)
    p.end()
    return pm.toImage().convertToFormat(QImage.Format.Format_RGBA8888)


def img_bytes(img):
    bits = img.constBits()
    bits.setsize(img.sizeInBytes())
    return bytes(bits)


def main() -> int:
    app = QApplication(sys.argv)
    cases = []
    for w, h in ((1000, 800), (999, 797), (64, 64), (61, 63), (13, 7), (1, 1),
                 (512, 300), (17, 1024)):
        for cell in (4, 6, 10, 12, 20, 32):
            cases.append((w, h, cell))

    bad = 0
    for w, h, cell in cases:
        ref = img_bytes(render_loop(w, h, cell))
        got = img_bytes(render_tiled(w, h, cell))
        if ref != got:
            bad += 1
            diff = sum(1 for a, b in zip(ref, got) if a != b)
            print(f"  DIFF {w}x{h} cell={cell}: {diff} 字节不同")
    print(f"棋盘格逐像素对比：{len(cases)} 个用例，差异 {bad} 个")
    print("PIXEL-EXACT" if bad == 0 else "MISMATCH")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
