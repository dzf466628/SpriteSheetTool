"""探针：导入的真进度条（A 单文件分块读 / B2 多文件与文件夹按"第 n/N 个"追加）。

用法：offscreen 下跑，直接调真实入口（`_load_image_with_progress` / `_import_image_paths`），
量出「读到了没、追加了几张原图、大文件时进度条真不真（max=文件字节数）」，不靠肉眼。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QElapsedTimer, Qt                          # noqa: E402
from PyQt6.QtGui import QColor, QPainter, QPixmap                    # noqa: E402
from PyQt6.QtWidgets import QApplication                            # noqa: E402

from settings_guard import protect_settings                         # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv)


def wait(ms):
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        QApplication.processEvents()


def make_png(path: Path, w=60, h=40, color="#1565C0"):
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.fillRect(5, 5, w - 10, h - 10, QColor(color))
    p.end()
    assert pm.save(str(path), "PNG")
    return path


def main():
    restore = protect_settings()
    from spritesheet.ui.main_window import MainWindow
    win = MainWindow()
    win.resize(1200, 800)
    win.show()
    wait(200)

    tmp = Path(tempfile.mkdtemp(prefix="sst_import_"))
    # A：单文件（小图不弹条，但必须能读出正确像素）
    one = make_png(tmp / "one.png", 60, 40, "#C62828")
    pm = win._load_image_with_progress(str(one))
    print("A 单文件读出：", not pm.isNull(), pm.width(), "x", pm.height(),
          "（应为 60x40）")

    # B2：多文件 → 依次追加（第一张建工程，其余追加）
    others = [make_png(tmp / f"f{i}.png", 40 + i, 30 + i, "#2E7D32") for i in (1, 2, 3)]
    win.image_viewer.set_pixmap(QPixmap())        # 先清成空工程
    wait(100)
    print("导入前原图张数：", len(win.image_viewer._source_images))
    win._import_image_paths([str(p) for p in others])
    wait(300)
    print("B2 导入后原图张数：", len(win.image_viewer._source_images), "（应为 3）")
    print("状态栏：", win.lbl_status.text())

    # B2：拖文件夹（一层，按名字排序）+ 混进一个不支持的文件
    folder = tmp / "folder"
    folder.mkdir()
    for i in (1, 2):
        make_png(folder / f"a{i}.png", 30, 30, "#F9A825")
    (folder / "note.txt").write_text("ignore me", encoding="utf-8")
    files, skipped = win._expand_import_paths([str(folder)])
    print("文件夹展开：", [Path(f).name for f in files], "跳过：", skipped, "（应为 a1/a2 + 跳 1）")

    # A 的真进度：造一个 >8MB 的文件（有效 PNG + 追加填充，解码器读到 IEND 就停）
    big = tmp / "big.png"
    holder = QPixmap(2000, 1400)
    holder.fill(QColor("#3949AB"))
    assert holder.save(str(big), "PNG")
    with open(big, "ab") as fh:
        fh.write(b"\0" * (9 << 20))
    size = big.stat().st_size
    pm2 = win._load_image_with_progress(str(big))
    prog = getattr(win, "_import_progress", None)
    print(f"大文件：{size // 1024} KB，读出 {pm2.width()}x{pm2.height()}（应为 2000x1400）")
    print("读取阶段进度条最大值 == 文件字节数（真进度，不是匀速假条）：",
          prog is not None and prog.maximum() == max(1, size),
          f"max={None if prog is None else prog.maximum()} size={size}")

    win.close()
    wait(200)
    restore()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())