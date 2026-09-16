"""软件设置回归检查。

覆盖四件事：
  1. 「工程文件显示缩略图」默认是**勾选**的；
  2. 关掉之后保存的工程里**没有** thumbnail 区段（资源管理器回退到文件类型图标）；
  3. 打开之后又有了缩略图，且是 256×256、描边三层更粗、红色是品牌粉 #FB5582；
  4. 设置弹窗确实拿着一个可用的「检查更新」入口。

用法: .venv\\Scripts\\python.exe tools\\verify_settings.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [OK]   {label}")
    else:
        FAILURES.append(label)
        print(f"  [FAIL] {label}   {detail}")


def main() -> int:
    app = QApplication(sys.argv)

    from spritesheet import settings as settings_mod
    from spritesheet.project_file import read_project, write_project
    from spritesheet.ui.main_window import _THUMB_STROKE, MainWindow
    from spritesheet.ui.settings_dialog import SettingsDialog

    print("=== 1. 默认值 ===")
    # 清掉可能存在的旧值，确认「没设置过」时是开
    QSettings(settings_mod.ORG_NAME, settings_mod.APP_NAME).remove(
        settings_mod.KEY_SHOW_THUMBNAIL)
    check("读取不到的设置 -> 默认显示缩略图", settings_mod.show_project_thumbnail() is True)

    win = MainWindow()
    # 没有素材时本来就生成不出缩略图（内容为空），先装一张示例图 —— 否则
    # 「关掉开关后没有缩略图」这类断言会因为没内容而假通过。
    from PyQt6.QtGui import QPixmap

    sample = next((p for p in sorted(ROOT.glob("*.png"))
                   if p.name not in ("工程图标.png",)), None)
    if sample is not None:
        win._on_image_loaded(QPixmap(str(sample)))
        print(f"  已载入示例素材 {sample.name}")
    manifest, sections = win._project_manifest()
    check("默认保存带 thumbnail 区段", "thumbnail.png" in sections)
    check("manifest.thumbnail 指向该区段", manifest.get("thumbnail") == "thumbnail.png")

    print()
    print("=== 2. 关闭后 ===")
    settings_mod.set_show_project_thumbnail(False)
    check("关闭后读取为 False", settings_mod.show_project_thumbnail() is False)
    manifest_off, sections_off = win._project_manifest()
    check("关闭后不再有 thumbnail 区段", "thumbnail.png" not in sections_off,
          f"实际区段: {sorted(sections_off)[:4]}")
    check("关闭后 manifest.thumbnail 为 None", manifest_off.get("thumbnail") is None)

    # 真的落盘一次，确认写出来的工程文件里也没有缩略图
    tmp = Path(tempfile.mkdtemp(prefix="sstool_settings_"))
    off_path = tmp / "off.sstool"
    write_project(str(off_path), manifest_off, sections_off)
    _m, secs = read_project(str(off_path))
    check("落盘后工程文件内无缩略图", "thumbnail.png" not in secs)

    print()
    print("=== 3. 重新打开后 ===")
    settings_mod.set_show_project_thumbnail(True)
    manifest_on, sections_on = win._project_manifest()
    check("打开后又有 thumbnail 区段", "thumbnail.png" in sections_on)

    from PyQt6.QtGui import QImage

    image = QImage.fromData(sections_on["thumbnail.png"])
    check("缩略图是 256x256", (image.width(), image.height()) == (256, 256),
          f"实际 {image.width()}x{image.height()}")

    stroke_total = sum(w for _c, w in _THUMB_STROKE)
    check("描边更粗（合计 >= 12px，此前是 7px）", stroke_total >= 12, f"实际 {stroke_total}px")
    reds = [c for c, _w in _THUMB_STROKE]
    check("描边用品牌粉 #FB5582", "#FB5582" in reds, f"实际 {reds}")

    # 逐行验证描边层：黑 -> 白 -> 品牌粉
    def rgb(x, y):
        color = image.pixelColor(x, y)
        return (color.red(), color.green(), color.blue())

    cx = 128

    def run_from(y0: int, pred) -> int:
        """从 y0 起连续满足 pred 的像素数（逐层往下量描边）。"""
        n = 0
        while y0 + n < 24 and pred(rgb(cx, y0 + n)):
            n += 1
        return n

    # 逐层量，不能用「有几行偏黑」——棋盘格的深格 #1E1E1E 也是暗色，会混进来。
    black_n = run_from(0, lambda c: c == (0, 0, 0))
    white_n = run_from(black_n, lambda c: c == (255, 255, 255))
    pink_n = run_from(black_n + white_n, lambda c: c == (251, 85, 130))
    print(f"  描边实测（由上往下）: 黑 {black_n}px / 白 {white_n}px / 粉 {pink_n}px")
    check("顶部黑描边 4px", black_n == 4, f"实际 {black_n}")
    check("黑之内白描边 4px", white_n == 4, f"实际 {white_n}")
    check("白之内品牌粉描边 6px", pink_n == 6, f"实际 {pink_n}")

    print()
    print("=== 4. 设置弹窗 ===")
    calls = []
    dialog = SettingsDialog(win, check_update=lambda: calls.append(1))
    check("弹窗复选框默认勾选", dialog.thumbnail_enabled() is True)
    dialog.btn_update.click()
    check("弹窗里的检查更新按钮能触发回调", calls == [1], f"实际 {calls}")
    dialog.cb_thumbnail.setChecked(False)
    check("弹窗取消勾选即时写设置", settings_mod.show_project_thumbnail() is False)
    dialog.cb_thumbnail.setChecked(True)
    check("弹窗重新勾选即时写设置", settings_mod.show_project_thumbnail() is True)
    dialog.close()

    # 收尾：MainWindow 关掉才会走 closeEvent → ImageViewer.shutdown()。
    # 不关就走，元素检测线程会在解释器退出时与窗口析构撞车（0xC0000005）。
    win.close()

    print()
    print(f"SETTINGS FAILED {len(FAILURES)}" if FAILURES else "SETTINGS OK")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
