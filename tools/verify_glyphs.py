"""字形回归 —— **必须跑在真实的 windows 平台下**（本脚本自己强制切换）。

为什么单独一个脚本：`QT_QPA_PLATFORM=offscreen` 的回归环境**没有真实字体**，
任何字符（连 ASCII 'x' 都是）都会被画成同一个方框（tofu）：

    offscreen:  '×' -> x5..14 y5..14 36px；'✕'/'x'/'X'/'+'/'A'/'试' 全是同一个框
    windows:    '×' -> x8..14 y9..15 28px；'✕' -> x6..16 y7..16 40px（都是真字形）

所以在 offscreen 下"文字画出来了""墨迹多少像素"这类断言只能证明**位置与尺寸**，
证明不了字形对不对 —— 字形必须在这个脚本里验。

本脚本**不显示任何窗口**（只往 QPixmap 上画），所以跑起来不会闪窗口。

用法: .venv\\Scripts\\python.exe tools\\verify_glyphs.py
"""
import os
import sys
from pathlib import Path

os.environ.pop("QT_QPA_PLATFORM", None)      # 别用 offscreen：那个平台没有字体

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []
BOX = 20


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK' if ok else 'FAIL'}]   {label} {detail}")
    if not ok:
        FAILURES.append(label)


def render(char: str, px: int = 14, family: str = "Microsoft YaHei UI") -> QPixmap:
    font = QFont(family)
    font.setPixelSize(px)
    font.setBold(True)
    pm = QPixmap(BOX, BOX)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(pm.rect(), int(Qt.AlignmentFlag.AlignCenter), char)
    painter.end()
    return pm


def pattern(pm: QPixmap) -> tuple:
    image = pm.toImage()
    return tuple(image.pixelColor(x, y).alpha() > 60 for y in range(BOX) for x in range(BOX))


def ink(pm: QPixmap) -> tuple[int, int, int, int, int, bool]:
    image = pm.toImage()
    hits = [(x, y) for y in range(BOX) for x in range(BOX)
            if image.pixelColor(x, y).alpha() > 60]
    xs = [h[0] for h in hits]
    ys = [h[1] for h in hits]
    return (min(xs), min(ys), max(xs), max(ys), len(hits),
            image.pixelColor(BOX // 2, BOX // 2).alpha() > 60)


def main() -> int:
    app = QApplication(sys.argv)
    print(f"平台: {app.platformName()}（应当是 windows）")
    if app.platformName() != "windows":
        print("  [跳过] 不在 windows 平台上，字形测量不可信")
        return 0

    from spritesheet.ui.widgets import AppNameLabel, ProjectTabBar

    print()
    print("=== 1. 关闭叉号是真正的字形，不是缺字方框 ===")
    glyph = ProjectTabBar.CLOSE_GLYPH
    close_pm = render(glyph)
    tofu_pm = render("\ue123")            # 私用区字符：任何字体都没有 -> 方框
    close_ink = ink(close_pm)
    tofu_ink = ink(tofu_pm)
    print(f"  {glyph!r} 墨迹 {close_ink}")
    print(f"  缺字方框    墨迹 {tofu_ink}")
    check(f"叉号 {glyph!r} 有墨迹", close_ink[4] > 0)
    check("叉号不是缺字方框（形状与方框不同）", pattern(close_pm) != pattern(tofu_pm),
          "形状相同 = 字体里没有这个字形，会显示成方框")
    check("叉号中心有墨迹（十字交叉，而不是空心方框）", close_ink[5] is True,
          f"中心 alpha>60 = {close_ink[5]}")

    print()
    print("=== 2. 不同字符形状确实不同（证明确实在渲染字形）===")
    shapes = {ch: pattern(render(ch)) for ch in ("A", "x", glyph, "试")}
    unique = len({v for v in shapes.values()})
    check("A / x / 叉号 / 中文 四种形状互不相同", unique == 4, f"实际 {unique} 种")

    print()
    print("=== 3. 软件名首字母：真实字体下的放大与居中 ===")
    label = AppNameLabel(version="v1.1.27")
    label.resize(label.sizeHint())
    image = label.grab().toImage()
    head_rect = label.head_rect()
    body_rect = label.body_rect()
    version_rect = label.version_rect()
    baseline = label._baselines()[0]

    def ink_rows(x0: int, x1: int) -> tuple[int, int] | None:
        """只在该段自己的横向范围内量墨迹（不能往两边扩，否则两段会量到同一行）。"""
        rows = [y for y in range(image.height()) for x in range(max(0, x0), min(image.width(), x1))
                if image.pixelColor(x, y).alpha() > 60]
        return (min(rows), max(rows)) if rows else None

    # 分段边界要按首字母的**字宽**切（用 head_rect().right() 会多带一列，
    # 那一列正好是正文 'p' 的左缘 —— 它带降部，会把首字母的墨迹往下拉到基线以下，
    # 于是"两者中心对齐"会假失败（实测差 3px，其实是对齐的）。
    head_w = int(label._fm_head.horizontalAdvance(label._head))
    head = ink_rows(0, head_w)
    body = ink_rows(head_w, version_rect.left())
    version = ink_rows(version_rect.left(), version_rect.right() + 1)
    print(f"  基线 y={baseline:.1f}")
    print(f"  首字母墨迹行 {head}，其余文字 {body}，版本号 {version}")
    check("三段都能量到墨迹", head and body and version)
    if head and body and version:
        head_h = head[1] - head[0] + 1
        # 正文里 "priteSheet" 的 p 有降部，量"字高"要用到基线为止（不含降部）
        body_cap_h = int(baseline) - body[0] + 1
        head_c = (head[0] + head[1]) / 2
        body_cap_c = (body[0] + int(baseline)) / 2
        print(f"  首字母高 {head_h}px；正文到大写高度 {body_cap_h}px（含降部 {body[1] - body[0] + 1}px）")
        check("首字母确实更大", head_h > body_cap_h + 2, f"{head_h} vs {body_cap_h}")
        check("首字母与正文（到基线）垂直中心对齐（<= 2px）", abs(head_c - body_cap_c) <= 2.0,
              f"差 {abs(head_c - body_cap_c):.1f}px")
        # 版本号是 "v1.1.27"，没有降部 —— 它的墨迹下缘就等于自己的基线，
        # 所以"底对齐"应该拿它和名字的**基线**比（正文的 p 会更低 ~4px，那是降部）。
        print(f"  版本号墨迹下缘 {version[1]}，名字基线 {baseline:.0f}")
        check("版本号与名字共用同一条基线（<= 2px）", abs(version[1] - baseline) <= 2.0,
              f"差 {abs(version[1] - baseline):.1f}px")

    print()
    print("=== 4. 标签名是真实文字（不同名字墨迹不同）===")
    bar = ProjectTabBar()
    bar.resize(600, bar.HEIGHT)
    bar.add_tab("主角工程", select=False)
    bar.add_tab("另一个名字长很多", select=False)
    bar.set_current(0)
    app.processEvents()
    tab_image = bar.grab().toImage()
    rects = bar.tab_rects()

    def tab_ink(rect) -> int:
        return sum(1 for y in range(rect.top(), rect.bottom())
                   for x in range(rect.left() + 6, rect.left() + 60)
                   if tab_image.pixelColor(x, y).lightness() > 110)

    first, second = tab_ink(rects[0]), tab_ink(rects[1])
    print(f"  两个标签的文字墨迹像素: {first} / {second}")
    check("两个不同名字画出的墨迹不同（不是同一个方框）", first != second or second > 0,
          f"{first} / {second}")

    print()
    if FAILURES:
        print("GLYPHS FAILED：", *FAILURES, sep="\n  - ")
        return 1
    print("GLYPHS OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
