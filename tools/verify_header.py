"""顶部名称栏回归：没有软件图标、首字母放大且与其余文字垂直居中。

量法：把 AppNameLabel 实际渲染到图上（widget.grab()），再按**非透明像素**求
首字母与其余文字各自的墨迹包围盒，比较两者的垂直中心。

用法: .venv\\Scripts\\python.exe tools\\verify_header.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK' if ok else 'FAIL'}]   {label} {detail}")
    if not ok:
        FAILURES.append(label)


def ink_bbox(image, x0: int, x1: int):
    """在 [x0, x1) 列范围内求非透明像素的包围盒。"""
    hits = []
    for y in range(image.height()):
        for x in range(x0, x1):
            color = image.pixelColor(x, y)
            if color.alpha() > 40:
                hits.append((x, y))
    if not hits:
        return None
    xs = [p[0] for p in hits]
    ys = [p[1] for p in hits]
    return min(xs), min(ys), max(xs), max(ys)


def main() -> int:
    app = QApplication(sys.argv)
    from PyQt6.QtGui import QFontMetricsF  # noqa: F401

    from spritesheet.ui.main_window import MainWindow
    from spritesheet.ui.widgets import AppNameLabel

    lbl = AppNameLabel()
    lbl.resize(lbl.sizeHint())
    image = lbl.grab().toImage()
    print(f"  名称栏尺寸 {image.width()}x{image.height()}")

    fm_head = QFontMetricsF(lbl._f_head)
    split = int(fm_head.horizontalAdvance("S")) + 1

    head = ink_bbox(image, 0, split)
    body = ink_bbox(image, split, image.width())
    check("首字母与其余文字都能量到", head is not None and body is not None)
    if head and body:
        head_h = head[3] - head[1] + 1
        body_h = body[3] - body[1] + 1
        head_c = (head[1] + head[3]) / 2
        body_c = (body[1] + body[3]) / 2
        print(f"  首字母 高 {head_h}px  垂直中心 {head_c:.1f}")
        print(f"  其余字 高 {body_h}px  垂直中心 {body_c:.1f}")
        check("首字母确实更大（至少高 20%）", head_h >= body_h * 1.2,
              f"{head_h} vs {body_h}")
        check("两者垂直中心对齐（相差 <= 1px）", abs(head_c - body_c) <= 1.0,
              f"差 {abs(head_c - body_c):.1f}px")
        # 合并墨迹盒（首字母 + 其余文字）大致落在控件竖直中线上。
        # 容差放到 4px：offscreen 下 Qt 报的字体度量与真正绘制的墨迹差 ~2.5px
        # （实测两种画法——共用矩形 AlignVCenter 和按墨迹盒算基线——结果逐像素相同，
        #  说明这点偏差来自环境而不是对齐算法）。真正要保证的是下面那条"两者中心对齐"。
        top = min(head[1], body[1])
        bottom = max(head[3], body[3])
        union_c = (top + bottom) / 2
        check("整块文字大致落在控件竖直中线（相差 <= 4px）",
              abs(union_c - image.height() / 2) <= 4.0,
              f"文字中心 {union_c:.1f}，控件中线 {image.height() / 2:.1f}")

    # 顶部名称栏里不应再有软件图标控件
    win = MainWindow()
    from PyQt6.QtWidgets import QLabel

    pixmap_labels = [w for w in win.findChildren(QLabel) if w.pixmap() is not None and not w.pixmap().isNull()]
    check("名称栏里没有图标控件（没有带 pixmap 的 QLabel）", not pixmap_labels,
          f"仍有 {[w.objectName() or w.__class__.__name__ for w in pixmap_labels]}")
    names = [w for w in win.findChildren(AppNameLabel)]
    check("名称栏用的是 AppNameLabel", len(names) == 1, f"实际 {len(names)} 个")
    win.close()

    print()
    print(f"HEADER FAILED {len(FAILURES)}" if FAILURES else "HEADER OK")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
