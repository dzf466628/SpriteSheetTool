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

from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QColor, QPainter, QPixmap
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


def drag_quad(app, mode: str, kind: str, index: int, dx: float, dy: float):
    """进入 mode，从手柄 index 拖 (dx,dy) 像素，返回结果归一化 quad。"""
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

    captured: list = []
    canvas.transform_quadded.connect(captured.append)
    canvas._update_drag(screen + QPointF(dx, dy))
    canvas._end_drag()
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
        check(f"  拖动角两个轴都动了", abs(dx) > 1e-4 and abs(dy) > 1e-4,
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
    print("=== 3. 斜切：拖边 = 两端一起平移（平行四边形），另外两角不动 ===")
    for edge in range(4):
        horizontal = (min(edge, (edge + 1) % 4), max(edge, (edge + 1) % 4)) in HORIZONTAL_EDGES
        d = 20.0 if horizontal else 20.0
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
    print("=== 4. 扭曲：只有被拖的角动 ===")
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
    if FAILURES:
        print("TRANSFORM MODES FAILED：", *FAILURES, sep="\n  - ")
        return 1
    print("TRANSFORM MODES OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
