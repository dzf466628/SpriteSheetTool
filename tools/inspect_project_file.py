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
        _report_badge(im)
    except Exception as exc:  # noqa: BLE001
        print(f"  缩略图解码失败: {exc}")
    win.close()
    return 0


def _report_badge(im) -> None:
    """量化角标：取角标图的**最高频不透明色**当特征色，在缩略图里定位它的包围盒。

    注意别用「平均色」—— 角标通常由多色组成，平均值会偏灰，容易和精灵画面里的
    相近颜色混淆，得出离谱的包围盒（实测过一次 88% 短边的假结果）。
    """
    import collections

    from PIL import Image
    badge_path = ROOT / "工程图标.png"
    if not badge_path.exists():
        return
    badge = Image.open(badge_path).convert("RGBA")
    counter = collections.Counter(
        p[:3] for p in badge.getdata() if p[3] > 240
    )
    if not counter:
        return
    main, count = counter.most_common(1)[0]
    rgba = im.convert("RGBA")
    W, H = rgba.size
    xs, ys = [], []
    for y in range(H):
        for x in range(W):
            r, g, b, a = rgba.getpixel((x, y))
            if a > 200 and abs(r - main[0]) < 20 and abs(g - main[1]) < 20 and abs(b - main[2]) < 20:
                xs.append(x)
                ys.append(y)
    if not xs:
        print(f"  角标       : 未找到（特征色 {main}，角标中占 {count} 像素）")
        return
    bx0, bx1, by0, by1 = min(xs), max(xs), min(ys), max(ys)
    bw, bh = bx1 - bx0 + 1, by1 - by0 + 1
    print(f"  角标特征色 : RGB{main}（角标内 {count}/{sum(counter.values())} 像素）")
    print(f"  角标范围   : x {bx0}-{bx1}, y {by0}-{by1}  ({bw}x{bh})")
    print(f"  角标占短边 : {bw / min(W, H):.0%}   距右 {W - 1 - bx1}px 距下 {H - 1 - by1}px")
    right_bottom = bx0 > W * 0.55 and by0 > H * 0.55
    print(f"  位于右下角 : {right_bottom}")


if __name__ == "__main__":
    sys.exit(main())
