"""软件设置回归检查。

覆盖五件事：
  1. 「工程文件显示缩略图」默认是**勾选**的；
  2. 关掉之后保存的工程里**没有** thumbnail 区段（资源管理器回退到文件类型图标）；
  3. 打开之后又有了缩略图，且是 256×256、描边三层更粗、红色是品牌粉 #FB5582；
  4. 设置弹窗确实拿着一个可用的「检查更新」入口；
  5. 辅助线颜色（普通标线 / 选中标线 / 像素网格）与像素网格显示门槛：
     默认值 + 读写往返 + 颜色无效值回退默认 + 「恢复默认颜色」+ 弹窗控件即时写设置。

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
sys.path.insert(0, str(Path(__file__).resolve().parent))   # 同目录的 settings_guard

from settings_guard import protect_settings   # noqa: E402  （要先把 ROOT 塞进 sys.path）

FAILURES = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [OK]   {label}")
    else:
        FAILURES.append(label)
        print(f"  [FAIL] {label}   {detail}")


def main() -> int:
    # 必须留住引用：写成 QApplication(sys.argv) 不赋值的话，对象会被 GC 掉，
    # 后面的 Qt 调用直接崩（这个脚本上真的踩过，表现为只打印两行就退出）。
    app = QApplication(sys.argv)
    app.setApplicationName("SpriteSheetTool verify_settings")

    from spritesheet import settings as settings_mod
    # 这套回归会把用户设置整段改掉（为了验默认值还会删键）：先存档，收尾原样写回
    restore_settings = protect_settings()
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

    print()
    print("=== 5. 辅助线颜色 / 像素网格门槛 ===")
    from PyQt6.QtGui import QColor

    store = QSettings(settings_mod.ORG_NAME, settings_mod.APP_NAME)
    # 先把这几项清掉，确认"没设置过"时的出厂值
    for key in (settings_mod.KEY_GUIDE_COLOR, settings_mod.KEY_GUIDE_SELECTED_COLOR,
                settings_mod.KEY_PIXEL_GRID_COLOR, settings_mod.KEY_PIXEL_GRID_ZOOM):
        store.remove(key)
    check("普通标线默认青色", settings_mod.guide_color().name().upper() == "#00FFFF",
          f"实际 {settings_mod.guide_color().name()}")
    check("选中标线默认主题色",
          settings_mod.guide_selected_color().name().upper() == "#E91E63",
          f"实际 {settings_mod.guide_selected_color().name()}")
    check("像素网格默认半透灰（alpha 128，不是全不透明）",
          settings_mod.pixel_grid_color().name().upper() == "#808080"
          and settings_mod.pixel_grid_color().alpha() == 128,
          f"实际 {settings_mod.pixel_grid_color().name(QColor.NameFormat.HexArgb)}")
    check("网格门槛默认 400%", settings_mod.pixel_grid_zoom() == 400,
          f"实际 {settings_mod.pixel_grid_zoom()}")

    settings_mod.set_guide_color(QColor("#123456"))
    settings_mod.set_guide_selected_color(QColor("#ABCDEF"))
    settings_mod.set_pixel_grid_color(QColor(10, 20, 30, 40))
    settings_mod.set_pixel_grid_zoom(650)
    check("普通标线颜色写得进读得出",
          settings_mod.guide_color().name().upper() == "#123456",
          f"实际 {settings_mod.guide_color().name()}")
    check("选中标线颜色写得进读得出",
          settings_mod.guide_selected_color().name().upper() == "#ABCDEF")
    check("网格颜色连透明度一起存下来（alpha 40）",
          settings_mod.pixel_grid_color().alpha() == 40
          and settings_mod.pixel_grid_color().name().upper() == "#0A141E",
          f"实际 {settings_mod.pixel_grid_color().name(QColor.NameFormat.HexArgb)}")
    check("网格门槛写得进读得出", settings_mod.pixel_grid_zoom() == 650)
    store.setValue(settings_mod.KEY_PIXEL_GRID_ZOOM, 99999)
    check("门槛超范围会被夹住（100..3200）", settings_mod.pixel_grid_zoom() == 3200,
          f"实际 {settings_mod.pixel_grid_zoom()}")
    store.setValue(settings_mod.KEY_GUIDE_COLOR, "不是个颜色")
    check("颜色值是垃圾时回退默认色（绝不让绘制崩）",
          settings_mod.guide_color().name().upper() == "#00FFFF",
          f"实际 {settings_mod.guide_color().name()}")

    settings_mod.reset_view_colors()
    check("恢复默认颜色：三个色一起回出厂",
          settings_mod.guide_color().name().upper() == "#00FFFF"
          and settings_mod.guide_selected_color().name().upper() == "#E91E63"
          and settings_mod.pixel_grid_color().name(QColor.NameFormat.HexArgb).upper()
          == "#80808080",
          f"实际 {settings_mod.guide_color().name()}/"
          f"{settings_mod.pixel_grid_color().name(QColor.NameFormat.HexArgb)}")

    # 弹窗里的控件：改了要即时写设置，并且要通知主窗口重绘
    signals = []
    dialog2 = SettingsDialog(win, check_update=lambda: None)
    dialog2.view_colors_changed.connect(lambda: signals.append(1))
    check("弹窗摸得到三个颜色按钮 + 门槛输入框",
          hasattr(dialog2, "btn_guide_color") and hasattr(dialog2, "btn_guide_selected_color")
          and hasattr(dialog2, "btn_grid_color") and hasattr(dialog2, "spin_grid_zoom"))
    check("颜色按钮显示的是当前设置色",
          dialog2.btn_guide_color.color().name().upper() == "#00FFFF",
          f"实际 {dialog2.btn_guide_color.color().name()}")
    dialog2.spin_grid_zoom.setValue(250)
    check("门槛框改动即时写设置", settings_mod.pixel_grid_zoom() == 250,
          f"实际 {settings_mod.pixel_grid_zoom()}")
    check("门槛框改动会通知主窗口（不然画布不重绘）", signals == [1], f"实际 {signals}")
    # 模拟取色盘选了一个颜色（offscreen 下弹不出真的 QColorDialog，
    # 直接发按钮的"选好了"信号，验的是信号接线本身）
    from PyQt6.QtGui import QColor as _QC

    dialog2.btn_grid_color.set_color(_QC("#FF00FF"))
    dialog2.btn_grid_color.color_changed.emit(_QC("#FF00FF"))
    check("颜色按钮选色后即时写设置（信号接线没接错）",
          settings_mod.pixel_grid_color().name().upper() == "#FF00FF")
    check("选色也会通知主窗口", len(signals) == 2, f"实际 {signals}")
    dialog2.btn_reset_colors.click()
    check("弹窗里的「恢复默认颜色」按钮生效（含按钮上的色块）",
          settings_mod.pixel_grid_color().name(QColor.NameFormat.HexArgb).upper()
          == "#80808080"
          and dialog2.btn_grid_color.color().name(QColor.NameFormat.HexArgb).upper()
          == "#80808080")
    dialog2.close()
    # 收尾：把用户原来的设置**原样写回**（不是"恢复出厂"）。
    # 原来这里写的是 reset_view_colors()：那等于"用户一旦自己调过标线色，跑一次回归就被抹掉"，
    # 而这几个颜色恰恰是新加的、鼓励用户自己调的功能。用存档保证跑完就是用户跑之前的样子；
    # 中途崩了也能靠存档在下次启动时救回来（见 tools/settings_guard.py）。
    restore_settings()

    # 收尾：MainWindow 关掉才会走 closeEvent → ImageViewer.shutdown()。
    # 不关就走，元素检测线程会在解释器退出时与窗口析构撞车（0xC0000005）。
    win.close()

    print()
    print(f"SETTINGS FAILED {len(FAILURES)}" if FAILURES else "SETTINGS OK")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
