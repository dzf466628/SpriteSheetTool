# -*- coding: utf-8 -*-
"""整体(GIF 级)变换回归：
- 动作编辑(all)缩放：所有帧同一中心对称 quad、变换后各帧中心对齐（重叠）、不弹起、松手保留
- 动作编辑(all)旋转：所有帧同角度、旋转中心统一为内容中心
- 帧编辑多选缩放：仅选中帧整体变形、中心对称；未选中帧不动
- 单帧缩放：保持对角锚定（原行为，quad 不要求中心对称）
- 历史记录入账
用法: .venv\\Scripts\\python.exe test_whole_transform.py
"""
import os, sys, time
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8"); sys.stderr.reconfigure(encoding="utf-8")
import faulthandler
faulthandler.enable()
from PyQt6.QtCore import QTimer, QEventLoop, QPoint, QPointF, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent; sys.path.insert(0, str(ROOT))
from spritesheet.ui.main_window import MainWindow
from spritesheet.ui.transform import compute_preview_layout

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
n = min(4, done["n"])
action.selected_indices = list(range(n))
win.image_viewer.set_current_action(action)
win.image_viewer.crop_frames_for_action(action)
win.preview.set_action(action); win.preview.set_actions(win.actions)
wait(200)

canvas = win.preview.canvas
panel = win.preview.adjust_panel
ab = win.anim_bar._all_mode_buttons

def centers():
    cw, ch, pos, _ = compute_preview_layout(action, canvas._frame_index)
    return [round(pos[i][0] + pos[i][2] / 2.0, 1) for i in range(len(action.frames))]

def drag_corner(activate, dx=-46, dy=-30):
    activate(); wait(30)
    pts = canvas._handles_canvas(canvas._current_mo())
    p0 = canvas._logical_to_screen(pts[0]).toPoint()
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=p0)
    pmid = p0 + QPoint(dx // 2, dy // 2)
    QTest.mouseMove(canvas, pos=pmid)
    p1 = p0 + QPoint(dx, dy)
    QTest.mouseMove(canvas, pos=p1)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=p1)
    wait(30)

def drag_rotate(activate, deg_screen=40):
    activate(); wait(30)
    pts = canvas._handles_canvas(canvas._current_mo())
    pivot = canvas._rotation_pivot_canvas()
    h = canvas._rotate_handle_screen(pts, pivot).toPoint()
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=h)
    QTest.mouseMove(canvas, pos=h + QPoint(deg_screen, 0))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=h + QPoint(deg_screen, 0))
    wait(30)

def is_centered(quad, tol=0.02):
    """中心对称：左上+右下 ≈ (1,1)，右上+左下 ≈ (1,1)。"""
    return (abs(quad[0] + quad[4] - 1.0) < tol and abs(quad[1] + quad[5] - 1.0) < tol
            and abs(quad[2] + quad[6] - 1.0) < tol and abs(quad[3] + quad[7] - 1.0) < tol)

def quads_approx_equal(qs, tol=0.01):
    """各帧 quad 近似相同（不同 base 宽高经 quadToQuad 外推会有 <1e-3 数值误差）。"""
    q0 = qs[0]
    if q0 is None:
        return False
    return all(q is not None and all(abs(a - b) <= tol for a, b in zip(q0, q)) for q in qs)

def reset_all_transforms():
    """测试夹具：直接清掉所有帧的全部变换，不依赖面板当前作用域（避免跨场景污染）。"""
    panel._exit_transform_mode()
    for i in range(len(action.frames)):
        action.frame_rotations.pop(i, None)
        action.frame_rotation_pivots.pop(i, None)
        action.frame_quads.pop(i, None)
        action.frame_transform_modes.pop(i, None)
        action.frame_mirror_h.discard(i)
        action.frame_mirror_v.discard(i)
    action.invalidate_transforms()
    wait(20)

print("=== all 缩放：整体围绕中心、各帧重叠 ===")
stack_before = len(action.undo_stack)
drag_corner(lambda: ab["scale"].click())
check("all 缩放后所有帧都有 quad", all(action.frame_quads.get(i) is not None for i in range(n)))
q0 = action.frame_quads.get(0)
check("各帧 quad 近似相同", quads_approx_equal([action.frame_quads.get(i) for i in range(n)]),
      f"{[action.frame_quads.get(i) for i in range(n)]}")
check("quad 围绕中心对称", q0 is not None and is_centered(q0), f"q0={q0}")
cs = centers()
check("变换后各帧中心对齐（重叠）", max(cs) - min(cs) <= 1.0, f"centers={cs}")
check("历史记录入账", len(action.undo_stack) == stack_before + 1,
      f"{len(action.undo_stack)} vs {stack_before}")
# 全量重置再测旋转
reset_all_transforms()

print("=== all 旋转：同角度、中心 pivot ===")
drag_rotate(lambda: ab["rotate"].click())
rots = {action.frame_rotations.get(i, 0) for i in range(n)}
check("所有帧旋转角度相同", len(rots) == 1 and list(rots)[0] != 0, f"rots={rots}")
check("所有帧 pivot 为默认中心", all(i not in action.frame_rotation_pivots for i in range(n)))
check("旋转后各帧中心对齐", (lambda c: max(c) - min(c) <= 1.0)(centers()), f"centers={centers()}")
reset_all_transforms()

print("=== frame 多选缩放：仅选中帧整体变形 ===")
win.preview.thumb_list.clearSelection()
wait(20)
win.preview.thumb_list.item(0).setSelected(True)
win.preview.thumb_list.item(1).setSelected(True)
win.preview.thumb_list.item(2).setSelected(True)
wait(50)
sel = panel._transform_targets()[0][1]
check("目标帧为选中的 0/1/2", sel == [0, 1, 2], f"{sel}")
drag_corner(lambda: panel._on_mode_button("scale", True))
q0 = action.frame_quads.get(0)
check("选中帧都有 quad 且近似相同",
      q0 is not None and quads_approx_equal([action.frame_quads.get(i) for i in (0, 1, 2)]))
check("quad 围绕中心对称", is_centered(q0), f"q0={q0}")
check("未选中帧 3 无 quad", action.frame_quads.get(3) is None)
reset_all_transforms()

print("=== 单帧缩放：默认以红色锚点为不动点；Alt = 对角固定（原行为）===")
win.preview.thumb_list.clearSelection()
wait(30)
drag_corner(lambda: panel._on_mode_button("scale", True), dx=-40, dy=-24)
q = action.frame_quads.get(0)
check("单帧 quad 已写入", q is not None, f"{q}")
# 锚点默认在正中心 -> 缩放围绕中心，四角等量内收/外扩（Aseprite / PS 参考点行为）
check("单帧缩放围绕红色锚点（中心对称）", q is not None and is_centered(q), f"q={q}")
reset_all_transforms()

# 按住 Alt（无头环境直接关掉开关）= 老手感：对角固定
canvas._scale_uses_pivot = lambda kind: False
drag_corner(lambda: panel._on_mode_button("scale", True), dx=-40, dy=-24)
q_alt = action.frame_quads.get(0)
check("Alt 时保留对角锚定（右下近似固定）",
      q_alt is not None and abs(q_alt[4] - 1.0) < 0.02 and abs(q_alt[5] - 1.0) < 0.02,
      f"q={q_alt}")
del canvas._scale_uses_pivot
reset_all_transforms()

print("=== 拖动稳定性：all 缩放拖动中不弹起 ===")
ab["scale"].click(); wait(20)
pts = canvas._handles_canvas(canvas._current_mo())
p0 = canvas._logical_to_screen(pts[0]).toPoint()
# 计数缩略图重建（性能回归：拖动中不得重建，松手补刷一次）
pv = win.preview
_calls = {"n": 0}
_orig_populate = pv._populate_thumbs
def _counting_populate(*a, **k):
    _calls["n"] += 1
    return _orig_populate(*a, **k)
pv._populate_thumbs = _counting_populate
QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=p0)
QTest.mouseMove(canvas, pos=p0 + QPoint(-20, -12))
check("拖动中模式保持", canvas.transform_mode() == "scale" and canvas._drag_kind == "corner")
# 落位口径：整体模式拖动中每一帧都按**实时布局**画（== 松手后/播放的落位），
# 所以拖动中看到的相对位置就是导出/播放里的相对位置。
# （旧实现是"拖动中冻结布局快照 + 松手重排"，那正是"其它帧位置变了又回去"的闪。）
_live = compute_preview_layout(action, canvas._frame_index)
_paint = canvas._paint_layout()
check("拖动中按实时布局落位（与松手后同口径）",
      canvas._drag_layout is not None and _paint is not None
      and [[round(v, 3) for v in _paint[2][i]] for i in range(len(_paint[2]))]
      == [[round(v, 3) for v in _live[2][i]] for i in range(len(_live[2]))],
      f"paint={_paint[2] if _paint else None} live={_live[2]}")
QTest.mouseMove(canvas, pos=p0 + QPoint(-30, -18))
check("拖动中不重建缩略图", _calls["n"] == 0, f"rebuild={_calls['n']}")
check("拖动中画布尺寸标签挂起", getattr(pv, "_label_dirty", False) is True)
QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=p0 + QPoint(-30, -18))
check("松手后模式保持", canvas.transform_mode() == "scale")
wait(80)
check("松手后补刷一次缩略图", _calls["n"] == 1, f"rebuild={_calls['n']}")
check("松手后画布尺寸标签已补刷", getattr(pv, "_label_dirty", False) is False)
pv._populate_thumbs = _orig_populate
reset_all_transforms()

print("=== all 整体平移：所有帧一起移动、拖动/松手一致、入账 ===")
action.frame_offsets.clear(); wait(20)
ab["scale"].click(); wait(30)
pts = canvas._whole_handles()
ccx = sum(p.x() for p in pts) / 4
ccy = sum(p.y() for p in pts) / 4
center = canvas._logical_to_screen(QPointF(ccx, ccy)).toPoint()
# 注意：整体框正中现在压着红色旋转中心（10px 内是"拖红点"），所以整体平移
# 从中心偏下 40px 起拖 —— 框内其它位置仍然是整体平移。
grab = center + QPoint(0, 40)
stack_before = len(action.undo_stack)
QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=grab)
QTest.mouseMove(canvas, pos=grab + QPoint(30, 20))
_, _, pos_drag, _ = canvas._paint_layout()
drag_centers = [pos_drag[i][0] + pos_drag[i][2] / 2 for i in range(n)]
offs_drag = {i: action.frame_offsets.get(i, (0, 0)) for i in range(n)}
QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=grab + QPoint(30, 20))
wait(50)
_, _, pos_rel, _ = canvas._paint_layout()
rel_centers = [pos_rel[i][0] + pos_rel[i][2] / 2 for i in range(n)]
vals = set(offs_drag.values())
check("所有帧偏移同步且非零", len(vals) == 1 and next(iter(vals)) != (0, 0), f"{offs_drag}")
check("拖动中各帧重叠", max(drag_centers) - min(drag_centers) <= 1.0, f"{drag_centers}")
check("拖动与松手位置一致（不回弹）", all(abs(a - b) < 0.6 for a, b in zip(drag_centers, rel_centers)),
      f"{drag_centers} vs {rel_centers}")
check("松手偏移保留", all(action.frame_offsets.get(i) == next(iter(vals)) for i in range(n)))
wait(500)  # 等偏移快照 debounce 入账
check("平移历史入账", len(action.undo_stack) == stack_before + 1,
      f"{len(action.undo_stack)} vs {stack_before}")
action.frame_offsets.clear()
reset_all_transforms()

print("=" * 50)
print(f"PASSED {passed}  |  FAILED {len(FAILURES)}")
# 先关闭窗口并排空事件（停掉播放/缩略图定时器），避免解释器退出时 Qt 析构 access violation
win.close()
app.processEvents()
if FAILURES:
    print("FAILURES:", FAILURES); sys.exit(1)
print("WHOLE TRANSFORM TEST OK")
sys.exit(0)
