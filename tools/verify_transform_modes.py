"""自由变换各模式的**行为**回归（不是看画面，是看角点数值）。

四角顺序（与 DEFAULT_QUAD 一致）：0=左上 1=右上 2=右下 3=左下；
源矩形里 (0,1)(2,3) 是水平边，(1,2)(3,0) 是垂直边。

透视的正确行为（**梯形**，与斜切/扭曲都不同）：拖某个角 →
  · 该角跟着鼠标走（两个轴都动）；
  · **与它同一条水平边的邻角**只动 X，且朝**相反**方向（位移 = -dx）；
  · **与它同一条垂直边的邻角**只动 Y，且朝**相反**方向（位移 = -dy）；
  · 对角不动；两条邻边一起收放 → 对边不再平行 = 梯形。

历史 bug（本次修复，两处）：
  ① 邻角写死成 (k+1)→X、(k-1)→Y，只对 k=0/2 成立；k=1（右上）/k=3（左下）会把
     两条邻边搞反，两个邻角沿错轴移动；
  ② 更根本的：邻角原本与拖动**同向**位移。几何上同向位移恒等于「两对对边都保持
     平行」= 平行四边形 = 仿射变换 —— 看着就是斜切/平移，压根不是透视。
     改成反向（对称收放）后才有对边不平行，也就是真正的梯形。

用法: .venv\\Scripts\\python.exe tools\\verify_transform_modes.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []
CORNER_NAMES = ("左上", "右上", "右下", "左下")
HORIZONTAL_EDGES = {(0, 1), (2, 3)}
EPS = 1e-6


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK' if ok else 'FAIL'}]   {label} {detail}")
    if not ok:
        FAILURES.append(label)


def make_action():
    from spritesheet.ui.models import Action

    pm = QPixmap(100, 60)
    pm.fill(QColor("#C62828"))
    action = Action("t", "#C62828")
    action.frames.append(pm)
    action.frame_rects.append((0, 0, 100, 60))
    action.frame_parts.append([((0, 0, 100, 60), 0, 0, 0, 0)])
    return action


def drag_quad(app, mode: str, kind: str, index: int, dx: float, dy: float,
              shift: bool = False, canvas_out: list | None = None):
    """进入 mode，从手柄 index 拖 (dx,dy) 像素，返回结果归一化 quad。

    shift=True 时带上 ShiftModifier（模拟按住 Shift 拖动）。
    canvas_out 非空时把画布对象塞进去，便于继续拖（测滞回）。
    """
    from spritesheet.ui.preview import PreviewCanvas

    canvas = PreviewCanvas()
    action = make_action()
    canvas.set_action(action)
    canvas.resize(800, 600)
    canvas.set_transform_mode(mode)
    app.processEvents()

    pts = canvas._handles_canvas(canvas._current_mo())
    start = pts[index] if kind == "corner" else (pts[index] + pts[(index + 1) % 4]) / 2.0
    screen = canvas._logical_to_screen(start)
    canvas._begin_drag(kind, index, screen)
    if canvas_out is not None:
        canvas_out.append((canvas, screen))

    captured: list = []
    canvas.transform_quadded.connect(captured.append)
    mods = Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier
    canvas._update_drag(screen + QPointF(dx, dy), mods)
    if canvas_out is None:
        canvas._end_drag()      # 要接着拖的场景（canvas_out）不能结束拖拽
    return captured[-1] if captured else None


def continue_drag(ctx, dx: float, dy: float, shift: bool = True):
    """在同一次拖拽里继续移动（测锁轴滞回）。

    ctx = (canvas, 拖拽起点屏幕坐标)，dx/dy 是**累计**位移（不是增量）。
    注意不能依赖 canvas._last_drag_screen —— 那个只在真实 mouseMoveEvent 里赋值，
    测试直接调 _update_drag 时它是 None。
    """
    canvas, screen = ctx
    captured: list = []
    canvas.transform_quadded.connect(captured.append)
    mods = Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier
    canvas._update_drag(screen + QPointF(dx, dy), mods)
    return captured[-1] if captured else None


def corners_of(quad):
    return [QPointF(quad[i * 2], quad[i * 2 + 1]) for i in range(4)]


def near(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def main() -> int:
    app = QApplication(sys.argv)

    print("=== 1. 透视：四个角的联动都必须按「边的方向」===")
    dx_px, dy_px = 20.0, 6.0
    for k in range(4):
        quad = drag_quad(app, "perspective", "corner", k, dx_px, dy_px)
        if quad is None:
            check(f"拖{CORNER_NAMES[k]}产生了变化", False, "没有发出 quad")
            continue
        base = [QPointF(0, 0), QPointF(1, 0), QPointF(1, 1), QPointF(0, 1)]
        got = corners_of(quad)
        nxt, prv = (k + 1) % 4, (k - 1) % 4
        if (min(k, nxt), max(k, nxt)) in HORIZONTAL_EDGES:
            h_neighbor, v_neighbor = nxt, prv
        else:
            h_neighbor, v_neighbor = prv, nxt
        opposite = (k + 2) % 4
        dx = got[k].x() - base[k].x()
        dy = got[k].y() - base[k].y()
        print(f"  拖 {CORNER_NAMES[k]}：水平邻角={CORNER_NAMES[h_neighbor]} "
              f"垂直邻角={CORNER_NAMES[v_neighbor]} 对角={CORNER_NAMES[opposite]}")
        check("  拖动角两个轴都动了", abs(dx) > 1e-4 and abs(dy) > 1e-4,
              f"dx={dx:.4f} dy={dy:.4f}")
        check(f"  对角（{CORNER_NAMES[opposite]}）不动",
              near(got[opposite].x(), base[opposite].x(), 1e-5)
              and near(got[opposite].y(), base[opposite].y(), 1e-5),
              f"实际 {got[opposite].x():.4f},{got[opposite].y():.4f}")
        check(f"  水平邻角（{CORNER_NAMES[h_neighbor]}）只动 X，且方向相反（= -dx）",
              near(got[h_neighbor].y(), base[h_neighbor].y(), 1e-5)
              and near(got[h_neighbor].x() - base[h_neighbor].x(), -dx, 1e-4),
              f"实际 dx={got[h_neighbor].x() - base[h_neighbor].x():.4f} "
              f"dy={got[h_neighbor].y() - base[h_neighbor].y():.4f}")
        check(f"  垂直邻角（{CORNER_NAMES[v_neighbor]}）只动 Y，且方向相反（= -dy）",
              near(got[v_neighbor].x(), base[v_neighbor].x(), 1e-5)
              and near(got[v_neighbor].y() - base[v_neighbor].y(), -dy, 1e-4),
              f"实际 dx={got[v_neighbor].x() - base[v_neighbor].x():.4f} "
              f"dy={got[v_neighbor].y() - base[v_neighbor].y():.4f}")

    print()
    print("=== 2. 透视的结果确实是梯形（有一对边不平行），不是平行四边形 ===")
    for k in range(4):
        quad = drag_quad(app, "perspective", "corner", k, dx_px, dy_px)
        got = corners_of(quad)
        # 四条边两两求斜率，看是否存在"同向边不平行"（梯形特征）
        def slope(a, b):
            return (b.y() - a.y()) / (b.x() - a.x()) if abs(b.x() - a.x()) > 1e-9 else None

        top, right = slope(got[0], got[1]), slope(got[1], got[2])
        bottom, left = slope(got[2], got[3]), slope(got[3], got[0])
        trapezoid = (
            (top is not None and bottom is not None and abs(top - bottom) > 1e-4)
            or (left is not None and right is not None and abs(left - right) > 1e-4)
        )
        check(f"拖 {CORNER_NAMES[k]} 之后是梯形（不是平行四边形）", trapezoid,
              f"上{top} 下{bottom} 左{left} 右{right}")

    print()
    print("=== 3. 变换按钮排布：8 个 = 4 行 × 2（含斜切与清空变换）===")
    from spritesheet.ui.preview import ActionPreviewWidget, PreviewCanvas

    widget = ActionPreviewWidget()
    action = make_action()
    widget.set_actions([action])
    widget.set_action(action)
    widget.resize(420, 800)
    app.processEvents()
    panel = widget.adjust_panel
    grid = panel._transform_grid
    labels = [b.text() for b in grid._buttons]
    print(f"  变换按钮 {len(labels)} 个：{labels}")
    check("  正好 8 个（5 模式 + 清空变换 + 2 镜像）", len(labels) == 8, f"实际 {len(labels)}")
    check("  排成 4 行 × 2（末行不空）",
          grid.columns() == 2 and len(labels) == 8, f"列数 {grid.columns()}")
    check("  斜切在（用户要求保留）", "斜切" in labels, f"实际 {labels}")
    check("  清空变换在网格里且不可勾选",
          "清空变换" in labels and not panel.btn_clear_transform.isCheckable())
    check("  两个镜像在最后一行", labels[-2:] == ["水平镜像", "垂直镜像"],
          f"实际 {labels[-2:]}")
    check("  摘要单独一行、占满网格宽度",
          abs(panel.lbl_transform_summary.width() - grid.width()) <= 2,
          f"{panel.lbl_transform_summary.width()} vs {grid.width()}")

    canvas = PreviewCanvas()
    canvas.set_action(make_action())
    canvas.resize(800, 600)
    app.processEvents()
    canvas.set_transform_mode("skew")
    app.processEvents()
    pts = canvas._handles_canvas(canvas._current_mo())
    edge_hits = [canvas._hit_test(canvas._logical_to_screen(
        (pts[i] + pts[(i + 1) % 4]) / 2.0))[0] for i in range(4)]
    check("  斜切下四条边的中点都可拖", edge_hits == ["edge"] * 4, f"实际 {edge_hits}")
    canvas.set_transform_mode("perspective")
    app.processEvents()
    pts = canvas._handles_canvas(canvas._current_mo())
    corner_hits = [canvas._hit_test(canvas._logical_to_screen(p))[0] for p in pts]
    check("  透视下四个角可拖", corner_hits == ["corner"] * 4, f"实际 {corner_hits}")

    print()
    print("=== 4. 斜切：拖边 = 两端一起平移（平行四边形），另外两角不动 ===")
    for edge in range(4):
        horizontal = (min(edge, (edge + 1) % 4), max(edge, (edge + 1) % 4)) in HORIZONTAL_EDGES
        d = 20.0
        quad = drag_quad(app, "skew", "edge", edge, d if horizontal else 0.0,
                         0.0 if horizontal else d)
        got = corners_of(quad)
        base = [QPointF(0, 0), QPointF(1, 0), QPointF(1, 1), QPointF(0, 1)]
        a, b = edge, (edge + 1) % 4
        moved_a = got[a] - base[a]
        moved_b = got[b] - base[b]
        others_ok = all(
            near(got[i].x(), base[i].x(), 1e-5) and near(got[i].y(), base[i].y(), 1e-5)
            for i in (set(range(4)) - {a, b}))
        print(f"  拖边 {a}-{b}（{'水平' if horizontal else '垂直'}）："
              f"两端位移 {moved_a.x():.4f},{moved_a.y():.4f} / {moved_b.x():.4f},{moved_b.y():.4f}")
        check(f"  边 {a}-{b} 两端位移相同（平行四边形）",
              near(moved_a.x(), moved_b.x(), 1e-4) and near(moved_a.y(), moved_b.y(), 1e-4))
        check(f"  边 {a}-{b} 另外两角不动", others_ok)

    print()
    print("=== 5. 斜切 + Shift = 居中剪切（对边反向等量，中心不动）===")
    for edge in (0, 1):
        horizontal = edge == 0
        quad = drag_quad(app, "skew", "edge", edge, 20.0 if horizontal else 0.0,
                         0.0 if horizontal else 20.0, shift=True)
        got = corners_of(quad)
        base = [QPointF(0, 0), QPointF(1, 0), QPointF(1, 1), QPointF(0, 1)]
        a, b = edge, (edge + 1) % 4
        other_a = (edge + 2) % 4
        moved = (got[a] - base[a]).x() if horizontal else (got[a] - base[a]).y()
        opposite = ((got[other_a] - base[other_a]).x() if horizontal
                    else (got[other_a] - base[other_a]).y())
        same_edge_b = ((got[b] - base[b]).x() if horizontal else (got[b] - base[b]).y())
        print(f"  拖边 {a}-{b} + Shift：本边 {moved:+.4f}（另一端 {same_edge_b:+.4f}），"
              f"对边 {opposite:+.4f}")
        check(f"  边 {a}-{b} + Shift：对边朝相反方向等量移动（居中，图形不整体跑）",
              near(moved, -opposite, 1e-4) and abs(moved) > 1e-4,
              f"{moved:.4f} vs {-opposite:.4f}")

    print()
    print("=== 6. 扭曲：只有被拖的角动 ===")
    for k in range(4):
        quad = drag_quad(app, "distort", "corner", k, 20.0, 6.0)
        got = corners_of(quad)
        base = [QPointF(0, 0), QPointF(1, 0), QPointF(1, 1), QPointF(0, 1)]
        others_ok = all(
            near(got[i].x(), base[i].x(), 1e-5) and near(got[i].y(), base[i].y(), 1e-5)
            for i in range(4) if i != k)
        moved = abs(got[k].x() - base[k].x()) > 1e-4 and abs(got[k].y() - base[k].y()) > 1e-4
        check(f"  拖 {CORNER_NAMES[k]}：只有它动", moved and others_ok)

    print()
    print("=== 7. Shift 锁轴（透视 / 扭曲）===")
    for mode in ("perspective", "distort"):
        # 偏水平的拖动 -> 只保留 X
        quad = drag_quad(app, mode, "corner", 0, 30.0, 6.0, shift=True)
        got = corners_of(quad)
        base = [QPointF(0, 0), QPointF(1, 0), QPointF(1, 1), QPointF(0, 1)]
        print(f"  {mode} 偏水平拖(30,6)+Shift：角位移 "
              f"dx={got[0].x() - base[0].x():.4f} dy={got[0].y() - base[0].y():.4f}")
        check(f"  {mode}：偏水平时只横向动（dy = 0）",
              abs(got[0].y() - base[0].y()) < 1e-6 and abs(got[0].x() - base[0].x()) > 1e-4)
        # 偏竖直的拖动 -> 只保留 Y
        quad = drag_quad(app, mode, "corner", 0, 6.0, 30.0, shift=True)
        got = corners_of(quad)
        print(f"  {mode} 偏竖直拖(6,30)+Shift：角位移 "
              f"dx={got[0].x() - base[0].x():.4f} dy={got[0].y() - base[0].y():.4f}")
        check(f"  {mode}：偏竖直时只纵向动（dx = 0）",
              abs(got[0].x() - base[0].x()) < 1e-6 and abs(got[0].y() - base[0].y()) > 1e-4)

    print()
    print("=== 8. Shift 锁轴的滞回（连续判断，换轴要超出 4px）===")
    holder: list = []
    quad = drag_quad(app, "distort", "corner", 0, 30.0, 6.0, shift=True, canvas_out=holder)
    ctx = holder[0]
    canvas = ctx[0]
    # 画布有缩放，屏幕位移和逻辑位移不是 1:1 —— 先量出比例，再按**逻辑像素**造位移，
    # 否则滞回的 4px 阈值会被缩放悄悄放大/缩小（第一版就是这么算错的）。
    probe_a = canvas._screen_to_logical(QPointF(0.0, 0.0))
    probe_b = canvas._screen_to_logical(QPointF(100.0, 100.0))
    per_screen = abs((probe_b.x() - probe_a.x()) / 100.0)

    def screen_for(logical: float) -> float:
        return logical / per_screen

    check("  一开始锁在 X", canvas._axis_lock == "x", f"实际 {canvas._axis_lock}")
    base = [QPointF(0, 0), QPointF(1, 0), QPointF(1, 1), QPointF(0, 1)]
    # 逻辑累计位移 (10, 13)：13 > 10 但没超出 10+4 -> 仍锁 X
    quad = continue_drag(ctx, screen_for(10.0), screen_for(13.0))
    got = corners_of(quad)
    check("  另一轴超出但没到 4px 滞回量时仍锁 X（不抖）",
          canvas._axis_lock == "x" and abs(got[0].y() - base[0].y()) < 1e-6,
          f"轴={canvas._axis_lock} dy={got[0].y() - base[0].y():.4f}")
    # 逻辑累计位移 (10, 20)：20 > 10+4 -> 换到 Y
    quad = continue_drag(ctx, screen_for(10.0), screen_for(20.0))
    got = corners_of(quad)
    check("  另一轴超出滞回量后换到 Y",
          canvas._axis_lock == "y" and abs(got[0].x() - base[0].x()) < 1e-6,
          f"轴={canvas._axis_lock} dx={got[0].x() - base[0].x():.4f}")

    print()
    print("=== 9. Shift 等比（缩放）===")
    base = [QPointF(0, 0), QPointF(1, 0), QPointF(1, 1), QPointF(0, 1)]

    def size_of(quad):
        got = corners_of(quad)
        return (got[1] - got[0]).manhattanLength(), (got[3] - got[0]).manhattanLength()

    quad_free = drag_quad(app, "scale", "corner", 0, 60.0, 0.0, shift=False)
    quad_shift = drag_quad(app, "scale", "corner", 0, 60.0, 0.0, shift=True)
    free_ratio = size_of(quad_free)
    shift_ratio = size_of(quad_shift)
    print(f"  不按 Shift：{free_ratio[0]:.4f} x {free_ratio[1]:.4f}（比例 {free_ratio[0] / free_ratio[1]:.4f}）")
    print(f"  按住 Shift：{shift_ratio[0]:.4f} x {shift_ratio[1]:.4f}（比例 {shift_ratio[0] / shift_ratio[1]:.4f}）")
    check("  不按 Shift 是自由缩放（宽高比变了）",
          abs(free_ratio[0] / free_ratio[1] - 1.0) > 0.10,
          f"比例 {free_ratio[0] / free_ratio[1]:.4f}")
    check("  按住 Shift 是等比（宽高比保持 1:1）",
          abs(shift_ratio[0] / shift_ratio[1] - 1.0) < 0.01,
          f"比例 {shift_ratio[0] / shift_ratio[1]:.4f}")

    print()
    print("=== 10. Shift 旋转 15° 吸附 ===")
    from spritesheet.ui.preview import PreviewCanvas

    for shift in (False, True):
        canvas = PreviewCanvas()
        action = make_action()
        canvas.set_action(action)
        canvas.resize(800, 600)
        canvas.set_transform_mode("rotate")
        app.processEvents()
        pivot = canvas._rotation_pivot_canvas()
        pivot_screen = canvas._logical_to_screen(pivot)
        start = QPointF(pivot_screen.x() + 90.0, pivot_screen.y())
        canvas._begin_drag("rotate", -1, start)
        angles: list = []
        canvas.transform_rotated.connect(angles.append)
        mods = Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier
        # 绕中心转到约 38°（终点算出来给它），看吸附前后
        import math as _math
        rad = _math.radians(38.0)
        end = QPointF(pivot_screen.x() + 90.0 * _math.cos(rad),
                      pivot_screen.y() + 90.0 * _math.sin(rad))
        canvas._update_drag(end, mods)
        angle = angles[-1] if angles else None
        print(f"  {'按 Shift' if shift else '不按 Shift'}：得到 {angle}°")
        if shift:
            check("  按 Shift 时角度是 15 的整数倍", angle is not None and angle % 15 == 0,
                  f"实际 {angle}")
            check("  吸附到 45°（38° 最近的 15 档是 45）", angle == 45, f"实际 {angle}")
        else:
            check("  不按 Shift 时不吸附（不是 15 的整数倍）",
                  angle is not None and angle % 15 != 0, f"实际 {angle}")

    print()
    print("=== 11. 清空变换按钮 ===")
    from spritesheet.ui.preview import ActionPreviewWidget

    widget = ActionPreviewWidget()
    action = make_action()
    widget.set_actions([action])
    widget.set_action(action)
    widget.resize(420, 800)
    app.processEvents()
    panel = widget.adjust_panel
    # 先造一堆变换
    action.frame_rotations[0] = 30
    action.frame_mirror_h.add(0)
    action.frame_mirror_v.add(0)
    action.frame_quads[0] = (0.05, 0.05, 0.95, 0.02, 0.98, 0.95, 0.02, 0.98)
    action.frame_rotation_pivots[0] = (0.25, 0.75)
    action.frame_transform_modes[0] = {"perspective"}
    action.frame_offsets[0] = (7, -3)
    action.invalidate_transforms()
    panel._sync_transform_widgets()
    print(f"  清空前：{panel.lbl_transform_summary.text()}")
    check("  清空前摘要里有内容", "无变换" not in panel.lbl_transform_summary.text())
    panel.btn_clear_transform.click()
    app.processEvents()
    summary = panel.lbl_transform_summary.text()
    print(f"  清空后：{summary}")
    check("  旋转被清掉", 0 not in action.frame_rotations, f"{action.frame_rotations}")
    check("  水平镜像被清掉", 0 not in action.frame_mirror_h)
    check("  垂直镜像被清掉", 0 not in action.frame_mirror_v)
    check("  透视四边形被清掉", 0 not in action.frame_quads)
    check("  旋转中心被清掉", 0 not in action.frame_rotation_pivots)
    check("  回调记录的模式被清掉", 0 not in action.frame_transform_modes)
    check("  摘要回到「无变换」", "无变换" in summary, summary)
    check("  帧偏移**不动**（它有自己的重置）", action.frame_offsets.get(0) == (7, -3),
          f"{action.frame_offsets}")
    check("  模式按钮都回到未选中",
          not any(b.isChecked() for b in (panel.btn_mode_rotate, panel.btn_mode_scale,
                                          panel.btn_mode_perspective, panel.btn_mode_distort,
                                          panel.btn_mirror_h, panel.btn_mirror_v)))

    print()
    if FAILURES:
        print("TRANSFORM MODES FAILED：", *FAILURES, sep="\n  - ")
        return 1
    print("TRANSFORM MODES OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
