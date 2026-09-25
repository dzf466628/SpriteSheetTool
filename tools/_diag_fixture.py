"""临时探针：整帧原图导进来之后，工具到底把帧切成了什么（用来把回归夹具搭对）。"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt6.QtCore import QElapsedTimer, Qt                        # noqa: E402
from PyQt6.QtGui import QColor, QPainter, QPixmap                 # noqa: E402
from PyQt6.QtWidgets import QApplication                          # noqa: E402

from settings_guard import protect_settings                       # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv)
CANVAS = (100, 80)


def wait(ms):
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        QApplication.processEvents()


def block(x, y, w, h, arms=0):
    pm = QPixmap(*CANVAS)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.fillRect(x, y, w, h, QColor("#1565C0"))
    if arms:
        p.fillRect(x + w, y + 8, arms, 5, QColor("#1565C0"))
    p.end()
    return pm


def main():
    restore = protect_settings()
    from spritesheet.settings import set_detect_min_area
    set_detect_min_area(4)
    from spritesheet.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1280, 800)
    win.show()
    wait(200)
    viewer = win.image_viewer
    sources = [block(20, 40, 20, 30), block(30, 40, 20, 30), block(40, 40, 20, 30, 18)]
    viewer.import_frames(sources)
    wait(400)
    print("原图数量 =", len(viewer._source_images),
          "尺寸 =", [(pm.width(), pm.height()) for pm in viewer._source_images])
    print("原图落点 =", viewer._source_offsets)
    print("原图矩形 =", viewer.source_rects())
    print("检测到的元素 =", viewer._frames)
    print("合成大图尺寸 =", (viewer._pixmap.width(), viewer._pixmap.height())
          if viewer._pixmap else None)
    action = win._create_animation_action("探针", [], 0, len(viewer._frames))
    win._install_animation_action(action)
    wait(300)
    print("选中元素 =", action.selected_indices)
    print("帧矩形 =", action.frame_rects)
    print("帧图像尺寸 =", [(pm.width(), pm.height()) for pm in action.frames])
    print("帧部件 =", action.frame_parts)
    print("_auto_align_reason =", repr(win._auto_align_reason(action)))
    print("_source_anchor_data =", win._source_anchor_data(action))
    win.close()
    win.deleteLater()
    wait(100)
    restore()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())