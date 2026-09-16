"""Profile the hot paths that are still slow after the first optimization pass.

Previous pass fixed _alpha_bounds / frame_base_size / checkerboard / detection.
This measures what is *now* on top, on realistic (heavy) documents:

  1. 原图区 _Canvas.paintEvent —— 每次重绘都要为「所有动作 × 所有帧」重算帧号信息
  2. 预览画布 PreviewCanvas.paintEvent
  3. 图集打包 _pack_frames（候选宽度 × shelf packing）
  4. 完整导出管线（PNG + JSON + GIF）
  5. 元素检测（已知剩余项，作为对照）

Usage: .venv\\Scripts\\python.exe tools\\profile_hotpaths.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF, QRect, Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spritesheet.ui.image_viewer import _detect_frames_image, ImageViewer
from spritesheet.ui.models import Action


def timeit(label, fn, repeat=3):
    best = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        dt = (time.perf_counter() - t0) * 1000
        best = dt if best is None else min(best, dt)
    print(f"  {label:<52} {best:9.2f} ms")
    return best


def make_sheet(elements: int, size: int = 40, per_row: int = 20):
    cols = per_row
    rows = (elements + cols - 1) // cols
    pm = QPixmap(cols * size, rows * size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    rects = []
    for i in range(elements):
        x, y = (i % cols) * size, (i // cols) * size
        p.fillRect(QRect(x + 2, y + 2, size - 4, size - 4), QColor("#C62828"))
        rects.append((x + 2, y + 2, size - 4, size - 4))
    p.end()
    return pm, rects


def build_scene(elements=200, actions=12, frames_per_action=8):
    """构造一个偏重的工程：多动作、多帧、含复用与合成。"""
    viewer = ImageViewer()
    pm, rects = make_sheet(elements)
    viewer._pixmap = pm
    viewer._frames = rects
    viewer._frame_logical = {}
    viewer._bg_mode = "checker"

    acts = []
    for a in range(actions):
        act = Action(f"动作{a + 1}", "#E3351C")
        base = (a * 7) % max(1, elements - frames_per_action)
        act.selected_indices = list(range(base, base + frames_per_action))
        acts.append(act)
    viewer.actions = acts
    viewer.set_current_action(acts[0])
    for act in acts:
        viewer.crop_frames_for_action(act)
    # 制造复用帧（frame_layouts 路径）
    for act in acts[:6]:
        viewer.set_current_action(act)
        viewer.reuse_frame_at(act.selected_indices[0])
    viewer.set_current_action(acts[0])
    return viewer, acts


def main() -> int:
    app = QApplication(sys.argv)
    print("=== 0. 构造场景 ===")
    viewer, acts = build_scene()
    total_frames = sum(len(a.frames) for a in acts)
    print(f"  元素 {len(viewer._frames)} 个 · 动作 {len(acts)} 个 · 帧合计 {total_frames} 个")

    print("\n=== 1. 原图区画布重绘（选择/缩放/拖框时都会触发）===")
    viewer.canvas.resize(1200, 900)
    viewer._zoom = 0.5
    viewer._offset = QPointF(0, 0)
    timeit("_Canvas.paintEvent （全部动作参与绘制）", lambda: viewer.canvas.grab())

    print("\n=== 2. 复算帧号信息（paintEvent 内部的开销）===")

    def legacy_act_info():
        """复刻 paintEvent 里那段：为所有动作 × 所有帧重算 {元素: 帧号}。"""
        out = {}
        for act in viewer.actions:
            fn, cr, fns = {}, {}, {}
            layouts = act.frame_layouts
            if layouts is not None:
                for no, layout in enumerate(layouts, 1):
                    members = [i for i, _, _ in layout]
                    if len(members) >= 2:
                        rep = members[0]
                        for m in members:
                            cr[m] = rep
                    for m in members:
                        fn.setdefault(m, no)
                        fns.setdefault(m, []).append(no)
            else:
                frame_no = 1
                emitted = set()
                for src in act.selected_indices:
                    group = next((tuple(g) for g in act.composite_groups if src in g), None)
                    if group is not None:
                        if group in emitted:
                            continue
                        emitted.add(group)
                        for m in group:
                            fn[m] = frame_no
                            fns.setdefault(m, []).append(frame_no)
                        if len(group) >= 2:
                            for m in group:
                                cr[m] = group[0]
                    else:
                        fn[src] = frame_no
                        fns.setdefault(src, []).append(frame_no)
                    frame_no += 1
            out[act] = (fn, cr, fns)
        return out

    timeit("帧号信息重算（每次重绘都做一遍）", legacy_act_info, repeat=5)

    print("\n=== 3. 预览画布重绘 ===")
    from spritesheet.ui.preview import ActionPreviewWidget
    prev = ActionPreviewWidget()
    prev.set_actions(acts)
    prev.set_source_pixmap(viewer._pixmap)
    prev.set_action(acts[0])
    prev.canvas.resize(700, 500)
    timeit("PreviewCanvas.paintEvent", lambda: prev.canvas.grab())

    print("\n=== 4. 图集打包 ===")
    from spritesheet.ui.export_dialog import ExportDialog
    dlg = ExportDialog(acts, viewer._pixmap)
    dlg._source_pixmap = viewer._pixmap
    timeit("_build_packed_atlas（全部动作）",
           lambda: dlg._build_packed_atlas(acts), repeat=2)

    print("\n=== 5. 完整导出管线（PNG + JSON + GIF + 工程）===")
    out = Path(tempfile.mkdtemp(prefix="sstool_profile_"))
    dlg.cb_gif.setChecked(True)
    dlg.cb_project.setChecked(False)
    t0 = time.perf_counter()
    dlg._export_to_directory(str(out))
    print(f"  {'导出（含 GIF）':<52} {(time.perf_counter() - t0) * 1000:9.2f} ms")

    print("\n=== 6. 元素检测（上一轮的已知剩余项，对照）===")
    big = ROOT / "mage_sprite_keyed.png"
    if big.exists():
        from PyQt6.QtGui import QImage
        img = QImage(str(big))
        timeit(f"_detect_frames_image {img.width()}x{img.height()}",
               lambda: _detect_frames_image(img), repeat=2)
    _, rects = make_sheet(200)
    small = make_sheet(200)[0].toImage()
    timeit(f"_detect_frames_image {small.width()}x{small.height()}（重场景那张）",
           lambda: _detect_frames_image(small), repeat=3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
