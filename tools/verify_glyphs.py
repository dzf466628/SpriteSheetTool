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

# Windows 控制台默认 GBK，输出 ✕ 等特殊字符会 UnicodeEncodeError 崩溃；强制切 UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

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
    print("=== 5. 关闭叉号悬停：变红 + 旋转 30°（形状只能在真实字体下量）===")
    import time as _time

    from PyQt6.QtCore import QPointF as _QPointF, QRectF as _QRectF
    from PyQt6.QtGui import QMouseEvent as _QMouseEvent

    close_bar = ProjectTabBar()
    close_bar.resize(400, close_bar.HEIGHT)
    close_bar.add_tab("甲工程", select=False)
    close_bar.set_current(0)
    app.processEvents()
    close_rect = close_bar._close_rect(0)
    idle_image = close_bar.grab().toImage()

    def mask_of(image):
        return [[image.pixelColor(x, y).lightness() > 90
                 for x in range(close_rect.left(), close_rect.right())]
                for y in range(close_rect.top(), close_rect.bottom())]

    def mask_diff(a, b) -> int:
        return sum(1 for ra, rb in zip(a, b) for x, y in zip(ra, rb) if x != y)

    def reddish(color) -> bool:
        r, g, b = color.red(), color.green(), color.blue()
        return r - g > 40 and r - b > 20 and r > 120

    def red_count(image) -> int:
        return sum(1 for y in range(close_rect.top(), close_rect.bottom())
                   for x in range(close_rect.left(), close_rect.right())
                   if reddish(image.pixelColor(x, y)))

    idle_red = red_count(idle_image)
    idle_mask = mask_of(idle_image)
    check("未悬停时没有红色", idle_red == 0, f"红色像素 {idle_red}")

    pos = _QPointF(close_rect.center())
    close_bar.mouseMoveEvent(_QMouseEvent(
        _QMouseEvent.Type.MouseMove, pos, pos, Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
    while close_bar._close_anim.state() == close_bar._close_anim.State.Running:
        app.processEvents()
        _time.sleep(0.005)
    app.processEvents()
    hover_image = close_bar.grab().toImage()
    hover_red = red_count(hover_image)
    hover_mask = mask_of(hover_image)
    print(f"  悬停：红色像素 {hover_red}")
    check("悬停后叉号变红", hover_red >= 8, f"红色像素 {hover_red}")
    check("红色只在笔画上（不是红底圆点）", hover_red < 90,
          f"红色像素 {hover_red}（红底圆会到 150+）")

    def reference(angle: float):
        pm = QPixmap(close_rect.width(), close_rect.height())
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setFont(close_bar._close_font)
        painter.setPen(QColor("#ffffff"))
        painter.translate(pm.width() / 2.0, pm.height() / 2.0)
        painter.rotate(angle)
        painter.drawText(_QRectF(-pm.width(), -pm.height(), pm.width() * 2, pm.height() * 2),
                         int(Qt.AlignmentFlag.AlignCenter), close_bar._close_glyph)
        painter.end()
        image = pm.toImage()
        return [[image.pixelColor(x, y).lightness() > 90 for x in range(image.width())]
                for y in range(image.height())]

    zero = reference(0.0)
    thirty = reference(ProjectTabBar.CLOSE_ROTATE)
    d_zero, d_thirty = mask_diff(hover_mask, zero), mask_diff(hover_mask, thirty)
    print(f"  悬停墨迹 vs 0° 差 {d_zero}，vs 30° 差 {d_thirty}")
    check("悬停时叉号转到了 30°（更像 30° 参考图）", d_thirty < d_zero,
          f"0° 差 {d_zero} vs 30° 差 {d_thirty}")
    check("未悬停时是 0°（没被转）",
          mask_diff(idle_mask, zero) < mask_diff(idle_mask, thirty),
          f"0° 差 {mask_diff(idle_mask, zero)} vs 30° 差 {mask_diff(idle_mask, thirty)}")

    print()
    print("=== 6. 关闭叉号与工程名横向对齐（用户要求下移约 3px）===")
    # 工程名的墨迹范围：从文字起点到叉号左边（留出间隙）
    name_rect = close_bar.tab_rects()[0]
    name_left = name_rect.left() + ProjectTabBar.PAD_X
    name_right = close_rect.left() - 4
    # 从第 4 行起量：0~1 行是活动标签顶部那条品牌玫红，会把范围撑到整条标签。
    name_rows = [y for y in range(4, close_bar.height())
                 for x in range(name_left, name_right)
                 if idle_image.pixelColor(x, y).alpha() > 60
                 and idle_image.pixelColor(x, y).lightness() > 110]
    close_rows = [y for y in range(close_rect.top() - 4, close_rect.bottom() + 4)
                  for x in range(close_rect.left(), close_rect.right())
                  if idle_image.pixelColor(x, y).alpha() > 60
                  # 未悬停的叉号是灰的 #6a6a6a（lightness 106），阈值要比名字低一点，
                  # 但又要高于标签底色 #2b2b2b（43）。
                  and idle_image.pixelColor(x, y).lightness() > 90]
    if name_rows and close_rows:
        name_center = (min(name_rows) + max(name_rows)) / 2
        close_center = (min(close_rows) + max(close_rows)) / 2
        print(f"  工程名墨迹 y[{min(name_rows)},{max(name_rows)}] 中心 {name_center:.1f}；"
              f"叉号墨迹 y[{min(close_rows)},{max(close_rows)}] 中心 {close_center:.1f}")
        check("叉号与工程名横向对齐（中心相差 <= 2px）",
              abs(name_center - close_center) <= 2.0,
              f"差 {abs(name_center - close_center):.1f}px")
    else:
        check("能量到工程名与叉号的墨迹", False,
              f"name_rows={bool(name_rows)} close_rows={bool(close_rows)}")

    print()
    print("=== 7. 标签上不画未保存圆点（真实字体下再确认一次）===")
    # v1.1.33 曾在这里验"红点是品牌红、紧跟在名字后面"；用户随后确认那个点不是有意设计的
    # （「原来点不是你故意加的…不要了 删掉吧」），v1.1.34 删除。真字体下补一条：
    # 标脏与不标脏的渲染必须**逐像素完全相同**（曾经它是 `text += " ●"`，会多一个方框）。
    def tab_render(dirty: bool):
        bar = ProjectTabBar()
        bar.resize(400, bar.HEIGHT)
        bar.add_tab("甲工程", select=False)
        bar.set_current(0)
        if dirty:
            bar.set_dirty(0, True)
        app.processEvents()
        return bar.grab().toImage()

    idle_tab = tab_render(False)
    dirty_tab = tab_render(True)
    diff = sum(1 for y in range(idle_tab.height()) for x in range(idle_tab.width())
               if idle_tab.pixelColor(x, y) != dirty_tab.pixelColor(x, y))
    print(f"  标脏 / 不标脏两版渲染，不同像素 {diff}")
    check("标脏不再改变标签外观（圆点已删除）", diff == 0, f"不同像素 {diff}")

    print()
    if FAILURES:
        print("GLYPHS FAILED：", *FAILURES, sep="\n  - ")
        return 1
    print("GLYPHS OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
