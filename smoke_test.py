"""Offscreen smoke test for SpriteSheetTool.

Runs the real MainWindow headless and exercises the full pipeline:
load image -> detect elements -> create action -> select frames ->
transform -> export PNG/GIF/JSON/.sstool -> reopen project round-trip.

Usage:  .venv\\Scripts\\python.exe smoke_test.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer, QEventLoop
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from spritesheet.ui.main_window import MainWindow
from spritesheet.project_file import read_project
from spritesheet import __version__

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


def wait_ms(app: QApplication, ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    print(f"SpriteSheetTool v{__version__} smoke test")
    win = MainWindow()
    win.show()
    wait_ms(app, 400)

    # ---- 1. Load image ----
    img = ROOT / "mage_sprite_keyed.png"
    pm = QPixmap(str(img))
    check("load demo image", not pm.isNull(), f"path={img}")
    print(f"  image size: {pm.width()}x{pm.height()}")

    # Element detection runs on a background QThread -> wait for the signal
    # instead of sleeping a fixed amount (large images take seconds).
    import time
    detected = {"n": None}
    win.image_viewer.frames_updated.connect(lambda: detected.update(n=len(win.image_viewer._frames)))
    t0 = time.perf_counter()
    win._on_image_loaded(pm)
    deadline = time.perf_counter() + 60
    while detected["n"] is None and time.perf_counter() < deadline:
        wait_ms(app, 100)
    elapsed = time.perf_counter() - t0
    print(f"  detection took {elapsed:.2f}s for {pm.width()*pm.height()} px")
    check("element detection ran", bool(detected["n"]), f"detected={detected['n']}")
    check("source pixmap set", win.image_viewer._pixmap is not None)

    # ---- 2. Create an action and select elements ----
    win._create_action()
    action = win.actions[-1]
    check("action created", action is not None and action.name.startswith("动作"))
    frames = win.image_viewer._frames
    n = min(8, len(frames))
    action.selected_indices = list(range(n))
    win.image_viewer.set_current_action(action)
    win.image_viewer.crop_frames_for_action(action)
    check("frames cropped", len(action.frames) == n, f"frames={len(action.frames)}")
    win.preview.set_action(action)
    win.preview.set_actions(win.actions)
    wait_ms(app, 300)

    # ---- 3. Frame transforms ----
    action.frame_rotations[1] = 45
    action.frame_mirror_h.add(2)
    action.frame_quads[3] = (0.0, 0.0, 0.8, 0.0, 1.0, 1.0, 0.0, 1.0)
    action.invalidate_transforms()
    from spritesheet.ui.transform import action_frame_pixmap, compute_preview_layout
    pm1 = action_frame_pixmap(action, 1)
    check("rotate transform renders", not pm1.isNull())
    cw, ch, pos, _ = compute_preview_layout(action, 0)
    check("preview layout computed", cw > 0 and ch > 0, f"canvas={cw}x{ch}")

    # ---- 4. Export pipeline (PNG + GIF + JSON + .sstool) ----
    out = Path(tempfile.mkdtemp(prefix="sstool_smoke_"))
    print(f"  export dir: {out}")

    def progress(value, total, label):
        return True

    from spritesheet.ui.exporters import FMT_TP_HASH
    atlas, records = win.preview._export_probe() if hasattr(win.preview, "_export_probe") else (None, None)
    dlg = None
    try:
        from spritesheet.ui.export_dialog import ExportDialog
        dlg = ExportDialog(win.actions, win.image_viewer._pixmap,
                           project_provider=win._project_manifest,
                           project_name=win._project_name)
    except Exception as exc:  # noqa: BLE001
        check("ExportDialog constructs", False, f"{exc}")
        dlg = None
    check("ExportDialog constructs", dlg is not None)

    # Use the dialog's internal exporter (same code path the button uses).
    if dlg is not None:
        dlg.cb_gif.setChecked(True)          # exercise GIF path (Pillow)
        dlg.cb_project.setChecked(True)      # exercise .sstool path
        dlg._json_fmt_checks[FMT_TP_HASH].setChecked(True)   # exercise 3rd-party JSON
        try:
            generated = dlg._export_to_directory(str(out), progress=progress)
            names = sorted(p.name for p in generated)
            print(f"  generated: {names}")
            # 命名规则：产物以工程名开头；超过两个文件就装进以工程名命名的文件夹
            base = dlg.base_name()
            root = dlg.target_root(out)
            check(f"export produced {base}.png", (root / f"{base}.png").exists())
            check("export produced JSON", any(p.suffix == ".json" for p in generated))
            check(f"export produced {base}.tp_hash.json",
                  (root / f"{base}.tp_hash.json").exists())
            check("export produced GIF", any(p.suffix == ".gif" for p in generated))
            check(f"export produced {base}.sstool", (root / f"{base}.sstool").exists())
            check("多文件时输出在工程名文件夹里", root != out and root.is_dir(),
                  f"root={root}, out={out}")
        except Exception as exc:  # noqa: BLE001
            check("export runs", False, f"{type(exc).__name__}: {exc}")

    # ---- 5. .sstool round-trip via MainWindow ----
    proj_path = out / "roundtrip.sstool"
    try:
        ok = win._save_project(proj_path)
        check("save project", ok and proj_path.exists())
        manifest, sections = read_project(proj_path)
        check("reopen project file", manifest.get("format") == "spritesheet-tool-project",
              f"actions={len(manifest.get('actions', []))}")
        ok2 = win._open_project(str(proj_path))
        check("apply project to window", ok2 and len(win.actions) == len(manifest.get("actions", [])))
    except Exception as exc:  # noqa: BLE001
        check("project round-trip", False, f"{type(exc).__name__}: {exc}")

    # ---- 6. Undo stack sanity ----
    check("undo stack exists", win._undo_stack is not None)
    win.close()

    print("=" * 50)
    print(f"PASSED {passed}  |  FAILED {len(FAILURES)}")
    if FAILURES:
        print("Failures:", *FAILURES, sep="\n  - ")
        return 1
    print("SMOKE TEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
