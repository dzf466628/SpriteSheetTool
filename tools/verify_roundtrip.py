# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Export -> import round-trip audit for every JSON format.

Builds a deliberately feature-rich document (reuse frames, composite frames,
rotation/mirror transforms, padding, ground line, move action, custom fps /
loop / anchor / color), exports it with the real export dialog, imports the
result back into a fresh window, and compares:

  * metadata   : name / fps / loop / anchor / padding / ground / type / move / color
  * frame pixels: the baked per-frame pixmaps
  * canvas frames: each frame rendered into its action canvas — this is what
    actually catches alignment (jitter) regressions

Differences are classified as either an inherent format limitation (the format
cannot express that field) or a real defect.

Usage: .venv\\Scripts\\python.exe tools\\verify_roundtrip.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spritesheet.ui.export_dialog import ExportDialog
from spritesheet.ui.exporters import FMT_NATIVE, FMT_TP_HASH, FMT_TP_ARRAY, FMT_ASEPRITE_ARRAY
from spritesheet.ui.models import Action
from spritesheet.ui.transform import _alpha_bounds, action_frame_pixmap, compute_preview_layout

# 每个第三方格式「本身表达不了」的字段（这些丢失是可接受的，不算缺陷）
INHERENT_LOSSES = {
    FMT_NATIVE: set(),
    FMT_TP_HASH: {"fps", "loop", "anchor", "padding", "ground", "type", "move", "color"},
    FMT_TP_ARRAY: {"fps", "loop", "anchor", "padding", "ground", "type", "move", "color"},
    FMT_ASEPRITE_ARRAY: {"anchor", "padding", "ground", "type", "move", "color",
                         "loop_once"},
}

RESULTS = []   # (fmt, field_key, label, ok, detail)


def record(fmt, key, label, ok, detail=""):
    kind = "OK" if ok else ("固有" if key in INHERENT_LOSSES.get(fmt, set()) else "缺陷")
    RESULTS.append((fmt, key, label, ok, detail, kind))
    mark = {"OK": "OK  ", "固有": "--  ", "缺陷": "DIFF"}[kind]
    print(f"    [{mark}] {label:<32} {detail}")


def img_bytes(img):
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    bits = img.constBits()
    bits.setsize(img.sizeInBytes())
    return img.width(), img.height(), bytes(bits)


def pm_bytes(pm):
    return img_bytes(pm.toImage())


def add_action(win, name, color):
    """用真实代码路径新建动作（含侧边栏卡牌）。"""
    act = Action(name, color)
    win.actions.append(act)
    win._append_action_card(act)
    return act


def build_document(win):
    """In-memory document: 3 elements, 2 actions, reuse + composite + transforms."""
    src = QPixmap(200, 100)
    src.fill(Qt.GlobalColor.transparent)
    p = QPainter(src)
    p.fillRect(QRect(10, 10, 40, 30), QColor("#C62828"))
    p.fillRect(QRect(60, 10, 40, 30), QColor("#2E7D32"))
    p.fillRect(QRect(110, 10, 40, 30), QColor("#1565C0"))
    p.end()

    viewer = win.image_viewer
    viewer._detect_generation += 1
    viewer._pixmap = src
    viewer._source_images = [src]
    viewer._frames = [(10, 10, 40, 30), (60, 10, 40, 30), (110, 10, 40, 30)]
    viewer._frame_logical = {}

    win.actions.clear()
    win.action_list.clear()

    # 动作 1：两个元素 + 复用第 1 帧 + 旋转/镜像 + 逐帧微调
    a1 = add_action(win, "跑步", "#E3351C")
    a1.selected_indices = [0, 1]
    viewer.set_current_action(a1)
    viewer.crop_frames_for_action(a1)
    viewer.reuse_frame_at(0)                      # 复用 -> 第 1 帧再出现一次
    a1.fps = 24
    a1.loop_mode = "pingpong"
    a1.padding = 6
    a1.ground_enabled = True
    a1.ground_offset = 3
    a1.anchor = (0.25, 0.75)
    a1.frame_rotations[1] = 30
    a1.frame_mirror_h.add(0)
    a1.frame_offsets[1] = (4, -2)                 # 手动微调，必须能往返
    a1.invalidate_transforms()

    # 动作 2：元素 1+2 合成为一帧，移动类型
    a2 = add_action(win, "跳跃", "#1C98E3")
    a2.selected_indices = [1, 2]
    a2.composite_groups = [[1, 2]]
    viewer.set_current_action(a2)
    viewer.crop_frames_for_action(a2)
    a2.fps = 12
    a2.loop_mode = "once"
    a2.action_type = "move"
    a2.move_direction = "left"
    a2.move_speed = 42

    viewer.set_actions(win.actions)
    viewer.set_current_action(a1)
    win.preview.set_actions(win.actions)
    win.preview.set_source_pixmap(src)
    win.preview.set_action(a1)
    return [a1, a2]


def frame_content(act, i):
    """帧的**实际像素内容**（去掉透明边），与帧容器大小无关。

    TexturePacker/Aseprite 会把 spriteSourceSize 的边距包进帧图里，帧容器尺寸
    因此与原始不同 —— 那是格式表达差异，内容本身必须逐像素一致。
    """
    pm = action_frame_pixmap(act, i)
    b = _alpha_bounds(pm)
    if b is None:
        return (0, 0, b"")
    crop = pm.copy(b)
    return (b.width(), b.height(), pm_bytes(crop)[2])


def canvas_align(act):
    """每帧内容在其画布中的落点（相对第 1 帧的位移）—— 帧间相对位置错位会在此暴露。"""
    if not act.frames:
        return []
    _, _, positions, _ = compute_preview_layout(act, 0)
    out = []
    base = None
    for i in range(len(act.frames)):
        tx, ty, _, _ = positions[i]
        b = _alpha_bounds(action_frame_pixmap(act, i))
        if b is None:
            out.append(None)
            continue
        cx, cy = tx + b.x(), ty + b.y()
        if base is None:
            base = (cx, cy)
        out.append((int(cx - base[0]), int(cy - base[1])))
    return out


def snapshot(actions):
    snap = []
    for act in actions:
        snap.append({
            "name": act.name,
            "color": act.color,
            "n_frames": len(act.frames),
            "fps": act.fps,
            "loop": act.loop_mode,
            "anchor": tuple(round(float(v), 4) for v in act.anchor),
            "padding": act.padding,
            "ground": (bool(act.ground_enabled), int(act.ground_offset)),
            "type": act.action_type,
            "move": (act.move_direction, act.move_speed) if act.action_type == "move" else None,
            "frames": [frame_content(act, i) for i in range(len(act.frames))],
            "canvas": canvas_align(act),
        })
    return snap


def compare(fmt, before, after):
    if len(before) != len(after):
        record(fmt, "n_actions", "动作数量", False, f"{len(before)} -> {len(after)}")
        return
    for i, (b, a) in enumerate(zip(before, after)):
        tag = f"[{i}] {b['name']}"
        # Aseprite 的 direction 只有 forward/reverse/pingpong，表达不了 once
        loop_key = "loop"
        if fmt == FMT_ASEPRITE_ARRAY and b["loop"] == "once" and a["loop"] == "loop":
            loop_key = "loop_once"
        for key, label, want, got in (
            ("name", "名称", b["name"], a["name"]),
            ("color", "颜色", b["color"], a["color"]),
            ("n_frames", "帧数", b["n_frames"], a["n_frames"]),
            ("fps", "fps", b["fps"], a["fps"]),
            (loop_key, "loop", b["loop"], a["loop"]),
            ("anchor", "anchor", b["anchor"], a["anchor"]),
            ("padding", "padding", b["padding"], a["padding"]),
            ("ground", "ground", b["ground"], a["ground"]),
            ("type", "动作类型", b["type"], a["type"]),
            ("move", "移动参数", b["move"], a["move"]),
        ):
            record(fmt, key, f"{tag} {label}", want == got, f"{want} -> {got}")

        same_frames = (len(a["frames"]) == len(b["frames"]) and
                       all(x == y for x, y in zip(b["frames"], a["frames"])))
        record(fmt, "frames", f"{tag} 帧内容像素", same_frames,
               "逐像素一致" if same_frames else _size_detail(b["frames"], a["frames"]))

        same_canvas = (len(a["canvas"]) == len(b["canvas"]) and
                       all(x == y for x, y in zip(b["canvas"], a["canvas"])))
        record(fmt, "canvas", f"{tag} 帧间相对位移", same_canvas,
               "一致" if same_canvas else f"{b['canvas']} -> {a['canvas']}")


def _size_detail(want, got):
    parts = []
    for j in range(min(len(want), len(got))):
        if want[j] != got[j]:
            parts.append(f"帧{j}: {want[j][0]}x{want[j][1]} vs {got[j][0]}x{got[j][1]}")
    return f"{len(want)} 帧 -> {len(got)} 帧" + ("；" + "; ".join(parts) if parts else "")


def silence_dialogs():
    """无头运行：把模态弹窗替换成直接返回默认值，否则会永久阻塞。"""
    from PyQt6.QtWidgets import QMessageBox, QFileDialog
    std = QMessageBox.StandardButton
    QMessageBox.information = staticmethod(lambda *a, **k: std.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: std.Ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: std.Ok)
    QMessageBox.question = staticmethod(lambda *a, **k: std.Yes)
    QMessageBox.exec = lambda self, *a, **k: 0
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")


def roundtrip(app, fmt, root):
    from spritesheet.ui.main_window import MainWindow
    win = MainWindow()
    actions = build_document(win)
    before = snapshot(actions)

    out = root / fmt
    out.mkdir(parents=True, exist_ok=True)
    dlg = ExportDialog(win.actions, win.image_viewer._pixmap,
                       project_provider=win._project_manifest)
    for fid, cb in dlg._json_fmt_checks.items():
        cb.setChecked(fid == fmt)
    dlg.cb_gif.setChecked(False)
    dlg.cb_project.setChecked(False)
    dlg._export_to_directory(str(out))
    win.close()

    jsons = sorted(out.glob("*.json"))
    if not jsons:
        record(fmt, "export", "导出 JSON", False, "没有生成 JSON")
        return
    json_path = jsons[0]
    print(f"    （导出文件: {json_path.name}）")

    win2 = MainWindow()
    win2._import_project(str(json_path))
    after = snapshot(win2.actions)
    compare(fmt, before, after)
    win2.close()


def main() -> int:
    app = QApplication(sys.argv)
    silence_dialogs()
    root = Path(tempfile.mkdtemp(prefix="sstool_roundtrip_"))
    print(f"临时目录: {root}\n", flush=True)

    for fmt in (FMT_NATIVE, FMT_TP_HASH, FMT_TP_ARRAY, FMT_ASEPRITE_ARRAY):
        print(f"== {fmt} ==", flush=True)
        try:
            roundtrip(app, fmt, root)
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            record(fmt, "crash", "往返异常", False, f"{type(exc).__name__}: {exc}")
        print(flush=True)

    defects = [r for r in RESULTS if not r[3] and r[5] == "缺陷"]
    inherent = [r for r in RESULTS if not r[3] and r[5] == "固有"]
    print("=" * 66)
    print(f"共 {len(RESULTS)} 项检查：缺陷 {len(defects)} 项，格式固有丢失 {len(inherent)} 项")
    for fmt in (FMT_NATIVE, FMT_TP_HASH, FMT_TP_ARRAY, FMT_ASEPRITE_ARRAY):
        d = sorted({r[1] for r in defects if r[0] == fmt})
        i = sorted({r[1] for r in inherent if r[0] == fmt})
        print(f"  {fmt:<16} 缺陷={d or '无'}  固有丢失={i or '无'}")
    return 1 if defects else 0


if __name__ == "__main__":
    sys.exit(main())
