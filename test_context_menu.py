# -*- coding: utf-8 -*-
"""右键菜单健壮性回归：任何状态下在元素区右键都不得闪退；
「取消选中」端到端（经菜单构建→exec→分发）只移除鼠标所指单个元素。

用法: .venv\\Scripts\\python.exe test_context_menu.py
"""
import os, sys, time
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8"); sys.stderr.reconfigure(encoding="utf-8")
import faulthandler
faulthandler.enable()
from PyQt6.QtCore import QTimer, QEventLoop, QPoint, Qt
from PyQt6.QtGui import QPixmap, QContextMenuEvent
from PyQt6.QtWidgets import QApplication, QMenu

ROOT = Path(__file__).resolve().parent; sys.path.insert(0, str(ROOT))
from spritesheet.ui.main_window import MainWindow

FAILURES = []; passed = 0
def check(name, cond, extra=""):
    global passed
    if cond: passed += 1; print(f"  [OK]   {name}")
    else: FAILURES.append(name); print(f"  [FAIL] {name} {extra}")

app = QApplication(sys.argv)
win = MainWindow(); win.show()
def wait(ms):
    loop = QEventLoop(); QTimer.singleShot(ms, loop.quit); loop.exec()

pm = QPixmap(str(ROOT / "mage_sprite_keyed.png"))
win._on_image_loaded(pm)
done = {"n": None}
win.image_viewer.frames_updated.connect(lambda: done.update(n=len(win.image_viewer._frames)))
t0 = time.perf_counter()
while done["n"] is None and time.perf_counter() - t0 < 30: wait(100)
win._create_action()
action = win.actions[-1]
n = done["n"]
win.image_viewer.set_current_action(action)
win.image_viewer.crop_frames_for_action(action)
win.preview.set_action(action); win.preview.set_actions(win.actions)
wait(200)

viewer = win.image_viewer
canvas = viewer.canvas

def frame_point(i):
    fx, fy, fw, fh = viewer._frames[i]
    off = viewer._offset; z = viewer._zoom
    return QPoint(int(off.x() + (fx + fw / 2) * z), int(off.y() + (fy + fh / 2) * z))

def send_context(pos):
    ev = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos, pos)
    canvas.contextMenuEvent(ev)

# 菜单不弹窗：默认什么都不选（仅验证构建/执行链路不抛异常）
QMenu.exec = lambda self, *a, **k: None

print("=== 各状态右键均不崩 ===")
action.selected_indices = [0, 1, 2]
for name, pos in (("命中选中帧", frame_point(0)), ("空白", QPoint(3, 3))):
    try:
        send_context(pos); check(f"右键-{name} 不抛异常", True)
    except Exception as e:
        check(f"右键-{name} 不抛异常", False, repr(e))

viewer.current_action = None
try:
    send_context(frame_point(0)); check("无动作时右键不崩", True)
except Exception as e:
    check("无动作时右键不崩", False, repr(e))
viewer.set_current_action(action)

# 整体（动作编辑）变换激活后再回元素区右键也不崩
action.selected_indices = [0, 1, 2]
win.anim_bar._all_mode_buttons["scale"].click(); wait(40)
try:
    send_context(frame_point(0)); check("整体变换激活态右键不崩", True)
except Exception as e:
    check("整体变换激活态右键不崩", False, repr(e))
win.preview.canvas.set_transform_mode(None)

print("=== 经菜单端到端：取消选中只移除鼠标所指单个 ===")
def pick_remove(self, *a, **k):
    for act in self.actions():
        if act.text() == "取消选中":
            return act
    return None
QMenu.exec = pick_remove
action.selected_indices = [0, 1, 2]
send_context(frame_point(0))
check("仅移除所指帧 0", action.selected_indices == [1, 2], f"{action.selected_indices}")
# 右键空白（未命中任何帧）：取消选中项应禁用，选择不变
action.selected_indices = [0, 1, 2]
send_context(QPoint(2, 2))
check("空白处右键不误删", action.selected_indices == [0, 1, 2], f"{action.selected_indices}")

print("=" * 50)
print(f"PASSED {passed}  |  FAILED {len(FAILURES)}")
win.close(); app.processEvents()
if FAILURES:
    print("FAILURES:", FAILURES); sys.exit(1)
print("CONTEXT MENU TEST OK")
sys.exit(0)
