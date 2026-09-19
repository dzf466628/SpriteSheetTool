# -*- coding: utf-8 -*-
"""离屏渲染真实 MainWindow，为介绍视频截取真实界面截图。不弹窗、不控制屏幕。"""
import os, sys, time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from PyQt6.QtCore import QTimer, QEventLoop, QSize
from PyQt6.QtGui import QPixmap, QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "video" / "shots"
OUT.mkdir(parents=True, exist_ok=True)

from spritesheet.ui.main_window import MainWindow
from spritesheet.ui.theme import APP_QSS


def wait_ms(app, ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def save(widget, name):
    pm = widget.grab()
    p = OUT / name
    pm.save(str(p), "PNG")
    print(f"saved {name}  {pm.width()}x{pm.height()}")


app = QApplication(sys.argv)
app.setStyle("Fusion")
# offscreen 下 Qt 找不到系统字体，显式加载微软雅黑，否则中文全是方块
for _f in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhui.ttc",
           r"C:\Windows\Fonts\simhei.ttf"):
    if Path(_f).exists():
        _fid = QFontDatabase.addApplicationFont(_f)
        if _fid >= 0:
            print("loaded font:", Path(_f).name, QFontDatabase.applicationFontFamilies(_fid))
_fams = QFontDatabase.families()
_cn_family = "Microsoft YaHei UI" if "Microsoft YaHei UI" in _fams else \
    ("Microsoft YaHei" if "Microsoft YaHei" in _fams else "SimHei")
print("using family:", _cn_family, "| available:", len(_fams))
app.setFont(QFont(_cn_family, 9))
app.setStyleSheet(APP_QSS)

win = MainWindow()
win._forcing_size = True          # 绕过默认 1000x1000 尺寸强制（仅截图用，不改源码）
win.setMinimumSize(1, 1)
win.resize(1600, 900)
win.setGeometry(0, 0, 1600, 900)
win.show()
app.processEvents()
wait_ms(app, 300)
win.resize(1600, 900)
app.processEvents()
wait_ms(app, 300)
print("window size:", win.width(), "x", win.height())

# 加载示例精灵图
pm = QPixmap(str(ROOT / "mage_sprite_keyed.png"))
print("demo image:", pm.width(), "x", pm.height())

detected = {"n": None}
win.image_viewer.frames_updated.connect(lambda: detected.update(n=len(win.image_viewer._frames)))
win._on_image_loaded(pm)
deadline = time.perf_counter() + 60
while detected["n"] is None and time.perf_counter() < deadline:
    wait_ms(app, 100)
print("detected frames:", detected["n"])
wait_ms(app, 800)

# 截图1：刚加载识别完的主界面
save(win, "01_main_detected.png")

# 创建动作 + 选 8 帧
win._create_action()
action = win.actions[-1]
frames = win.image_viewer._frames
n = min(8, len(frames))
action.selected_indices = list(range(n))
win.image_viewer.set_current_action(action)
win.image_viewer.crop_frames_for_action(action)
win.preview.set_action(action)
win.preview.set_actions(win.actions)
wait_ms(app, 600)

# 截图2：建好动作后的主界面
save(win, "02_main_action.png")

# 截图3：多动作列表（再建两个动作，选不同帧）
win._create_action()
a2 = win.actions[-1]
frames = win.image_viewer._frames
if len(frames) > 12:
    a2.selected_indices = list(range(8, min(14, len(frames))))
    win.image_viewer.set_current_action(a2)
    win.image_viewer.crop_frames_for_action(a2)
win._create_action()
a3 = win.actions[-1]
if len(frames) > 16:
    a3.selected_indices = list(range(12, min(17, len(frames))))
    win.image_viewer.set_current_action(a3)
    win.image_viewer.crop_frames_for_action(a3)
win.preview.set_actions(win.actions)
wait_ms(app, 500)
save(win, "03_multi_actions.png")

# 截图4：导出对话框（真实）
from spritesheet.ui.export_dialog import ExportDialog
dlg = ExportDialog(win.actions, win.image_viewer._pixmap,
                   project_provider=win._project_manifest,
                   project_name=win._project_name)
dlg.cb_gif.setChecked(True)
dlg.resize(960, 640)
dlg.show()
wait_ms(app, 500)
save(dlg, "04_export_dialog.png")
dlg.close()

print("DONE")
win.close()
