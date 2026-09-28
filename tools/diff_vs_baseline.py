# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Report per-block differences between the post-cleanup baseline and the
current modules, so every difference can be attributed to a known change.

Usage: python tools/diff_vs_baseline.py <baseline.py>
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from block_tool import _qualname_map, _strip_dump  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

MOVED = {
    "spritesheet/ui/widgets.py": [
        "RULER_SIZE", "_APP_ROOT", "_ICONS_DIR", "make_logo_pixmap", "make_tool_icon",
        "_tick_interval", "ColorPickerDialog", "ActionCardWidget", "ActionItemDelegate",
        "_Ruler", "_SLIDER_QSS", "_GROUP_QSS", "CompactComboBox", "EditableNumberLabel",
        "CompactSpinBox", "SwitchWidget", "ActionThumbPlayer",
    ],
    "spritesheet/ui/export_dialog.py": [
        "PreviewArea", "_safe_export_name", "_pixmap_to_pil", "ExportDialog",
    ],
    "spritesheet/ui/image_viewer.py": [
        "_detect_frames_image", "_FrameDetectThread", "_Canvas", "FloatingToolBar",
        "_AltNativeFilter", "ImageViewer", "SUPPORTED_IMAGE_EXT", "MIN_ZOOM", "MAX_ZOOM",
    ],
    "spritesheet/ui/preview.py": [
        "PreviewFloatingBar", "PreviewCanvas", "AnimAdjustPanel", "ActionPreviewWidget",
    ],
    "spritesheet/project_file.py": [
        "_rect_from_dict", "_pixmap_bytes", "_pixmap_from_bytes",
    ],
    "spritesheet/ui/main_window.py": [
        "UpdateDownloadDialog", "_TransformHistoryCommand", "MainWindow",
    ],
}


def main() -> int:
    base_path = sys.argv[1]
    base = _qualname_map(ast.parse(Path(base_path).read_text(encoding="utf-8")))
    total = 0
    changed = 0
    for rel, names in MOVED.items():
        path = ROOT / rel
        cur = _qualname_map(ast.parse(path.read_text(encoding="utf-8")))
        print(f"\n== {rel} ==")
        for name in names:
            total += 1
            a, b = base.get(name), cur.get(name)
            if a is None or b is None:
                print(f"  [MISSING] {name}")
                changed += 1
                continue
            # compare the whole block, but for classes also list differing members
            if _strip_dump(a) == _strip_dump(b):
                print(f"  [same]    {name}")
                continue
            changed += 1
            # narrow down which members differ
            if isinstance(a, ast.ClassDef):
                members = sorted({k.split(".", 1)[1] for k in base if k.startswith(name + ".")} |
                                 {k.split(".", 1)[1] for k in cur if k.startswith(name + ".")})
                diffs = []
                for m in members:
                    ma, mb = base.get(f"{name}.{m}"), cur.get(f"{name}.{m}")
                    if ma is None:
                        diffs.append(f"{m}(仅基线有)")
                    elif mb is None:
                        diffs.append(f"{m}(仅当前有)")
                    elif _strip_dump(ma) != _strip_dump(mb):
                        diffs.append(m)
                print(f"  [CHANGED] {name}  -> 成员差异: {diffs}")
            else:
                print(f"  [CHANGED] {name}")
    print(f"\n共 {total} 个块，其中 {changed} 个与基线不同")
    return 0


if __name__ == "__main__":
    sys.exit(main())
