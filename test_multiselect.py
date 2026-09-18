"""多选帧变换/位移 专项验证（Ctrl 多选后变换与位移是否作用于所有选中帧）。

用法：.venv\\Scripts\\python.exe test_multiselect.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer, QEventLoop
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from spritesheet.ui.main_window import MainWindow

FAILURES: list[str] = []
passed = 0


def check(name: str, cond, detail: str = "") -> None:
    global passed
    if cond:
        passed += 1
        print(f"  [OK]   {name}")
    else:
        FAILURES.append(name)
        print(f"  [FAIL] {name}  {detail}")


def wait_ms(app: QApplication, ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    win = MainWindow()
    win.show()
    wait_ms(app, 300)

    # ---- 加载 demo 图并创建动作 ----
    img = ROOT / "mage_sprite_keyed.png"
    pm = QPixmap(str(img))
    win._on_image_loaded(pm)
    detected = {"n": None}
    win.image_viewer.frames_updated.connect(lambda: detected.update(n=len(win.image_viewer._frames)))
    deadline = 0
    import time
    t0 = time.perf_counter()
    while detected["n"] is None and time.perf_counter() - t0 < 60:
        wait_ms(app, 100)
    win._create_action()
    action = win.actions[-1]
    frames = win.image_viewer._frames
    n = min(8, len(frames))
    action.selected_indices = list(range(n))
    win.image_viewer.set_current_action(action)
    win.image_viewer.crop_frames_for_action(action)
    win.preview.set_action(action)
    win.preview.set_actions(win.actions)
    wait_ms(app, 300)
    print(f"动作帧数: {len(action.frames)}")

    ap = win.preview.adjust_panel
    thumbs = win.preview.thumb_list
    print(f"缩略图数量: {thumbs.count()}, 当前主帧: {win.preview._frame_index}")

    # ---- 模拟 Ctrl 多选缩略图 0,1,2（主帧保持 0，1/2 作为叠加）----
    for i in (0, 1, 2):
        thumbs.item(i).setSelected(True)
    wait_ms(app, 50)  # 让 _apply_selection 的 singleShot 跑完
    rows = win.preview._selected_rows()
    print(f"选中行: {rows}")
    check("缩略图多选生效", rows == [0, 1, 2], f"rows={rows}")

    # ---- 1. 旋转 ----
    action.frame_rotations.clear()
    ap._apply_rotation(45)
    rot = sorted(action.frame_rotations.keys())
    check("旋转应用到全部选中帧", rot == [0, 1, 2], f"rot={rot}")

    # ---- 2. 自由变换 quad ----
    action.frame_quads.clear()
    action.frame_transform_modes.clear()
    q = (0.1, 0.0, 0.9, 0.0, 1.0, 1.0, 0.0, 1.0)
    # 进入缩放模式，模拟画布拖角点
    ap._on_mode_button("scale", True)
    ap._apply_quad(q)
    quads = sorted(action.frame_quads.keys())
    check("缩放 quad 应用到全部选中帧", quads == [0, 1, 2], f"quads={quads}")
    ap._on_mode_button("scale", False)

    # ---- 3. 镜像 ----
    action.frame_mirror_h.clear()
    ap.btn_mirror_h.setChecked(True)   # 模拟用户点击按钮（_toggle_mirror 读勾选状态）
    ap._toggle_mirror("h")
    mh = sorted(action.frame_mirror_h)
    check("水平镜像应用到全部选中帧", mh == [0, 1, 2], f"mirror_h={mh}")
    ap.btn_mirror_h.setChecked(False)  # 取消
    ap._toggle_mirror("h")

    # ---- 4. 位移：滑块/输入框（绝对设置）----
    action.frame_offsets.clear()
    ap._set_current_offset(10, 0)
    off = {k: v for k, v in action.frame_offsets.items()}
    check("位移(滑块)应用到全部选中帧", sorted(off.keys()) == [0, 1, 2],
          f"offsets={off}  期望 {{0,1,2}}")

    # ---- 5. 位移：方向按钮（相对微调 1px）----
    action.frame_offsets.clear()
    ap._adjust_frame(1, 0)
    off2 = {k: v for k, v in action.frame_offsets.items()}
    check("位移(方向按钮)应用到全部选中帧", sorted(off2.keys()) == [0, 1, 2],
          f"offsets={off2}  期望 {{0,1,2}}")

    # ---- 6. 位移：重置 ----
    action.frame_offsets = {0: (5, 5), 1: (5, 5), 2: (5, 5)}
    ap._reset_frame_offset()
    check("位移(重置)清空全部选中帧", action.frame_offsets == {},
          f"offsets={action.frame_offsets}")

    # ---- 7. 画布拖动当前帧（参照帧模式）----
    action.frame_offsets = {0: (0, 0), 1: (0, 0), 2: (0, 0)}
    action.ref_frame = 0
    canvas = win.preview.canvas
    canvas._frame_drag_active = True
    canvas._frame_drag_last = None
    # 直接走 mouseMoveEvent 的增量分支：先构造一次按下再移动
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtCore import QEvent
    # 模拟点击当前帧开始拖动
    canvas.begin_layout_freeze()
    canvas._frame_drag_active = True
    canvas._frame_drag_last = QPointF(100, 100)
    ev = QMouseEvent(QEvent.Type.MouseMove, QPointF(108, 100), QPointF(108, 100),
                     QPointF(108, 100), __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.MouseButton.NoButton,
                     __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.MouseButton.NoButton,
                     __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.KeyboardModifier.NoModifier)
    canvas.mouseMoveEvent(ev)
    canvas._frame_drag_active = False
    canvas.end_layout_freeze()
    off3 = {k: v for k, v in action.frame_offsets.items() if v != (0, 0)}
    check("画布拖动位移应用到全部选中帧", sorted(off3.keys()) == [0, 1, 2],
          f"offsets={off3}  期望 {{0,1,2}}")

    win.close()
    print("=" * 50)
    print(f"PASSED {passed}  |  FAILED {len(FAILURES)}")
    if FAILURES:
        print("Failures:", *FAILURES, sep="\n  - ")
        return 1
    print("MULTISELECT TEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
