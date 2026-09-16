"""存一个真实工程，检查 .sstool 的文件布局与缩略图。

Usage: .venv\\Scripts\\python.exe tools\\inspect_project_file.py [输出路径]
"""
import io
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spritesheet.project_file import MAGIC, PNG_SIGNATURE, png_image_end
from spritesheet.ui.main_window import MainWindow


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win._on_image_loaded(QPixmap(str(ROOT / "mage_sprite_keyed.png")))
    wait(1200)                      # 等元素检测
    win._create_action()
    action = win.actions[-1]
    action.selected_indices = list(range(6))
    win.image_viewer.set_current_action(action)
    win.image_viewer.crop_frames_for_action(action)
    win.preview.set_action(action)
    win.preview.set_actions(win.actions)

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path(tempfile.mkdtemp(prefix="sstool_thumb_")) / "工程.sstool")
    win._save_project(out)

    data = out.read_bytes()
    end = png_image_end(data) or 0
    print(f"  文件路径   : {out}")
    print(f"  文件大小   : {len(data):,} bytes")
    print(f"  以 PNG 开头: {data.startswith(PNG_SIGNATURE)}")
    print(f"  缩略图长度 : {end:,} bytes")
    print(f"  其后是容器 : {data[end:end + len(MAGIC)] == MAGIC}")
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data[:end]))
        print(f"  缩略图尺寸 : {im.size[0]}x{im.size[1]} 模式={im.mode}")
    except Exception as exc:  # noqa: BLE001
        print(f"  缩略图解码失败: {exc}")
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
