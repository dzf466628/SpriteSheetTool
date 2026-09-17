"""逐帧时长（frame_durations）专项验证。

覆盖：
  1. Action.frame_duration_ms 默认值（跟随 fps）与逐帧覆盖；
  2. 自格式 JSON：frames[].duration_ms 逐帧写出；
  3. Aseprite JSON：frames[].duration 逐帧写出；
  4. GIF：Pillow 读回每帧时长（GIF 单位 1/100 秒，取 10ms 整数倍用例）；
  5. .sstool 工程：frame_durations 字段写入清单并可读回；
  6. 自格式 JSON 导入：逐帧时长保留（与默认相同的帧不落字段）。

Usage: .venv\\Scripts\\python.exe tools\\verify_frame_duration.py
"""
import os
import sys
import tempfile
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []
passed = 0


def check(name: str, cond, detail: str = "") -> None:
    global passed
    if cond:
        passed += 1
        print(f"  [OK]   {name}")
    else:
        FAILURES.append(name)
        print(f"  [FAIL] {name} {detail}")


def silence_dialogs():
    from PyQt6.QtWidgets import QMessageBox, QFileDialog
    std = QMessageBox.StandardButton
    QMessageBox.information = staticmethod(lambda *a, **k: std.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: std.Ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: std.Ok)
    QMessageBox.question = staticmethod(lambda *a, **k: std.Yes)
    QMessageBox.exec = lambda self, *a, **k: 0
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")


def build_window():
    """3 个色块元素 -> 1 个动作 3 帧，fps=10（默认 100ms）。"""
    from spritesheet.ui.main_window import MainWindow
    from spritesheet.ui.models import Action
    win = MainWindow()
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
    act = Action("时长测试", "#E3351C")
    win.actions.append(act)
    win._append_action_card(act)
    act.selected_indices = [0, 1, 2]
    viewer.set_current_action(act)
    viewer.crop_frames_for_action(act)
    act.fps = 10                      # 默认 100ms
    act.frame_durations = {1: 300, 2: 50}  # 帧0 默认100 / 帧1 300 / 帧2 50
    act.invalidate_transforms()
    viewer.set_actions(win.actions)
    win.preview.set_actions(win.actions)
    win.preview.set_source_pixmap(src)
    win.preview.set_action(act)
    return win, act


def main() -> int:
    app = QApplication(sys.argv)  # noqa: F841  必须保留实例引用（坑1）
    silence_dialogs()
    out = Path(tempfile.mkdtemp(prefix="sstool_duration_"))
    print(f"临时目录: {out}")

    # ---- 1. 模型默认值 / 覆盖 ----
    from spritesheet.ui.models import Action
    a = Action("x")
    a.fps = 20
    check("默认时长 = round(1000/fps)", a.frame_duration_ms(0) == 50,
          f"got {a.frame_duration_ms(0)}")
    a.frame_durations[3] = 77
    check("覆盖帧时长优先", a.frame_duration_ms(3) == 77)
    check("未覆盖帧仍跟随 fps", a.frame_duration_ms(0) == 50)
    a.frame_durations[0] = 0
    check("异常值钳制到 >=1ms", a.frame_duration_ms(0) == 1)

    win, act = build_window()
    check("构造 3 帧", len(act.frames) == 3, f"got {len(act.frames)}")
    check("帧时长 100/300/50",
          [act.frame_duration_ms(i) for i in range(3)] == [100, 300, 50],
          str([act.frame_duration_ms(i) for i in range(3)]))

    # ---- 2/3/4. 导出 native + aseprite + GIF ----
    from spritesheet.ui.export_dialog import ExportDialog
    from spritesheet.ui.exporters import FMT_NATIVE, FMT_ASEPRITE_ARRAY
    dlg = ExportDialog(win.actions, win.image_viewer._pixmap,
                       project_provider=win._project_manifest)
    for fid, cb in dlg._json_fmt_checks.items():
        cb.setChecked(fid in (FMT_NATIVE, FMT_ASEPRITE_ARRAY))
    dlg.cb_gif.setChecked(True)
    dlg.cb_project.setChecked(False)
    dlg._export_to_directory(str(out), progress=lambda v, t, l: True)

    native = [p for p in out.rglob("*.json") if not p.name.endswith(".aseprite.json")
              and not p.name.endswith(".tp_hash.json") and not p.name.endswith(".tp_array.json")][0]
    ase = next(out.rglob("*.aseprite.json"))
    gif = next(out.rglob("*.gif"))

    data = json.loads(native.read_text(encoding="utf-8"))
    native_durs = [f["duration_ms"] for f in data["animations"][0]["frames"]]
    check("自格式 JSON 逐帧 duration_ms", native_durs == [100, 300, 50], str(native_durs))

    adata = json.loads(ase.read_text(encoding="utf-8"))
    ase_durs = [f["duration"] for f in adata["frames"]]
    check("Aseprite JSON 逐帧 duration", ase_durs == [100, 300, 50], str(ase_durs))

    from PIL import Image
    im = Image.open(gif)
    gif_durs = []
    for i in range(im.n_frames):
        im.seek(i)
        gif_durs.append(im.info.get("duration"))
    check("GIF 逐帧时长", gif_durs[:3] == [100, 300, 50], str(gif_durs))

    # ---- 5. .sstool 工程清单含 frame_durations ----
    from spritesheet.project_file import read_project
    sstool = out / "dur.sstool"
    ok = win._save_project(sstool)
    check("保存 .sstool", ok and sstool.exists())
    manifest, _sections = read_project(sstool)
    raw_durs = manifest["actions"][0].get("frame_durations")
    check("sstool 写入 frame_durations", raw_durs == {"1": 300, "2": 50}, str(raw_durs))
    win2 = type(win)()
    check("sstool 打开成功", win2._open_project(str(sstool)))
    opened = win2.actions[0]
    check("sstool 读回逐帧时长",
          opened.frame_durations == {1: 300, 2: 50}
          and [opened.frame_duration_ms(i) for i in range(3)] == [100, 300, 50],
          str(opened.frame_durations))
    win2.close()

    # ---- 6. 自格式 JSON 导入保留逐帧时长 ----
    win3 = type(win)()
    win3._import_project(str(native))
    imported = win3.actions[0]
    check("JSON 导入逐帧时长",
          imported.frame_durations == {1: 300, 2: 50},
          str(imported.frame_durations))
    win3.close()
    win.close()

    print("=" * 50)
    print(f"PASSED {passed}  |  FAILED {len(FAILURES)}")
    if FAILURES:
        print("Failures:", *FAILURES, sep="\n  - ")
        return 1
    print("FRAME DURATION VERIFY OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
