"""Performance benchmark for SpriteSheetTool hot paths (A/B where possible).

Reports the optimized implementations, and for the checkerboard does a true
same-process A/B against the original per-cell fillRect loop.

Usage: .venv\\Scripts\\python.exe bench.py
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from spritesheet.ui.transform import _alpha_bounds, frame_base_size
from spritesheet.ui.image_viewer import _detect_frames_image
from spritesheet.ui.widgets import paint_checkerboard
from spritesheet.ui.models import Action

# 优化前实测基线（本次优化开始时的数据，用于对照）
BEFORE = {
    "alpha_256": 10.38, "alpha_512": 55.54, "alpha_1024": 243.57,
    "fbs_x1": 83.78, "fbs_x20": 1700.81,
    "check_12": 43.75, "check_6": 183.87, "check_4": 369.00,
    "detect_2048": 1692.31,
}


def timeit(label, fn, repeat=3, before=None):
    best = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        dt = (time.perf_counter() - t0) * 1000
        best = dt if best is None else min(best, dt)
    if before:
        print(f"  {label:<44} {best:9.2f} ms   (优化前 {before:8.2f} ms, 提速 {before / max(best, 1e-6):6.1f}x)")
    else:
        print(f"  {label:<44} {best:9.2f} ms")
    return best


def make_frame(w, h):
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.fillRect(QRect(w // 4, h // 4, w // 2, h // 2), QColor("#E91E63"))
    p.end()
    return pm


def main() -> int:
    # 必须保留实例引用，否则临时对象被 GC 回收后进程崩溃（坑1/坑29：pyflakes F841 为误报）。
    app = QApplication(sys.argv)  # noqa: F841
    print("=== 1. _alpha_bounds（每次变换都会调用）===")
    for size, key in ((256, "alpha_256"), (512, "alpha_512"), (1024, "alpha_1024")):
        pm = make_frame(size, size)
        timeit(f"{size}x{size}", lambda pm=pm: _alpha_bounds(pm), before=BEFORE[key])

    print("\n=== 2. frame_base_size（变换拖动时每次 mouseMove 都调用）===")
    act = Action("bench")
    act.frames = [make_frame(512, 512)]
    act.frame_rotations[0] = 30
    act.invalidate_transforms()
    timeit("x1 (首次，含缓存填充)", lambda: frame_base_size(act, 0), before=BEFORE["fbs_x1"])
    timeit("x20（一次拖动的连续 mouseMove）",
           lambda: [frame_base_size(act, 0) for _ in range(20)], before=BEFORE["fbs_x20"])

    print("\n=== 3. 棋盘格背景 1000x800（同进程 A/B）===")
    canvas = QPixmap(1000, 800)

    def old_loop(cell):
        p = QPainter(canvas)
        for y in range(0, 800, cell):
            for x in range(0, 1000, cell):
                c = "#2a2a2a" if (x // cell + y // cell) % 2 == 0 else "#1e1e1e"
                p.fillRect(x, y, cell, cell, QColor(c))
        p.end()

    def new_tiled(cell):
        p = QPainter(canvas)
        paint_checkerboard(p, canvas.rect(), cell)
        p.end()

    for cell, key in ((12, "check_12"), (6, "check_6"), (4, "check_4")):
        timeit(f"cell={cell}（新：缓存平铺）", lambda cell=cell: new_tiled(cell), before=BEFORE[key])
        timeit(f"cell={cell}（旧：逐格 fillRect，仅对照）", lambda cell=cell: old_loop(cell), repeat=1)

    print("\n=== 4. 元素检测 flood fill ===")
    big = ROOT / "mage_sprite_keyed.png"
    if big.exists():
        img = QImage(str(big))
        timeit(f"{img.width()}x{img.height()} mage_sprite_keyed",
               lambda: _detect_frames_image(img), repeat=2, before=BEFORE["detect_2048"])
    for name in ("sprite_demo_keyed.png", "003-妙蛙花.png", "007-杰尼龟.png", "033-尼多利诺.png"):
        p = ROOT / name
        if p.exists():
            img = QImage(str(p))
            timeit(f"{name} ({img.width()}x{img.height()})",
                   lambda img=img: _detect_frames_image(img), repeat=3)
    print("\n注：元素检测在后台线程执行（不阻塞 UI）；剩余开销为逐像素邻域判定的固有成本。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
