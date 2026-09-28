# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Equivalence tests for the performance optimizations.

Every optimized hot path is checked against a reference implementation of the
ORIGINAL algorithm, on real assets plus adversarial synthetic images, so the
speedups are proven behavior-preserving.

Usage: .venv\\Scripts\\python.exe tools\\verify_perf_equiv.py
"""
import os
import random
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spritesheet.ui import transform as T
from spritesheet.ui.image_viewer import _detect_frames_image
from spritesheet.ui.models import Action

FAIL = []


def check(name, ok, detail=""):
    print(f"  [{'OK' if ok else 'FAIL'}]   {name} {detail}")
    if not ok:
        FAIL.append(name)


# ---------------- reference: ORIGINAL per-pixel _alpha_bounds ----------------
def alpha_bounds_reference(pm):
    if not pm or pm.isNull():
        return None
    image = pm.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    bits = image.constBits()
    bits.setsize(image.sizeInBytes())
    data = bytes(bits)
    stride = image.bytesPerLine()
    left, top = image.width(), image.height()
    right = bottom = -1
    for y in range(image.height()):
        row = y * stride
        for x in range(image.width()):
            if data[row + x * 4 + 3] > 0:
                left = min(left, x)
                top = min(top, y)
                right = max(right, x)
                bottom = max(bottom, y)
    if right < left or bottom < top:
        return None
    return QRect(left, top, right - left + 1, bottom - top + 1)


# ---------------- reference: ORIGINAL flood fill ----------------
def detect_reference(img, alpha_threshold=10):
    if img is None or img.isNull():
        return []
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = img.width(), img.height()
    ptr = img.bits()
    ptr.setsize(w * h * 4)
    data = bytes(ptr)
    visited = bytearray(w * h)
    frames = []
    for sy in range(h):
        row_base = sy * w
        for sx in range(w):
            start = row_base + sx
            if visited[start] or data[start * 4 + 3] < alpha_threshold:
                visited[start] = 1
                continue
            stack = [start]
            visited[start] = 1
            min_x = max_x = sx
            min_y = max_y = sy
            while stack:
                pos = stack.pop()
                cx, cy = pos % w, pos // w
                if cx < min_x: min_x = cx
                if cx > max_x: max_x = cx
                if cy < min_y: min_y = cy
                if cy > max_y: max_y = cy
                if cx > 0:
                    ni = pos - 1
                    if not visited[ni] and data[ni * 4 + 3] >= alpha_threshold:
                        visited[ni] = 1; stack.append(ni)
                if cx + 1 < w:
                    ni = pos + 1
                    if not visited[ni] and data[ni * 4 + 3] >= alpha_threshold:
                        visited[ni] = 1; stack.append(ni)
                if cy > 0:
                    ni = pos - w
                    if not visited[ni] and data[ni * 4 + 3] >= alpha_threshold:
                        visited[ni] = 1; stack.append(ni)
                if cy + 1 < h:
                    ni = pos + w
                    if not visited[ni] and data[ni * 4 + 3] >= alpha_threshold:
                        visited[ni] = 1; stack.append(ni)
            frames.append((min_x, min_y, max_x - min_x + 1, max_y - min_y + 1))
    return frames


# ---------------- synthetic images ----------------
def synth_cases():
    rng = random.Random(20260916)
    cases = []

    def solid(w, h, color):
        pm = QPixmap(w, h)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.fillRect(0, 0, w, h, color)
        p.end()
        return pm

    cases.append(("fully transparent 64x64", solid(64, 64, Qt.GlobalColor.transparent)))
    cases.append(("fully opaque 64x64", solid(64, 64, QColor(255, 0, 0, 255))))
    # alpha=1 semi-transparent: must count as content (alpha > 0)
    cases.append(("alpha=1 32x32", solid(32, 32, QColor(0, 0, 0, 1))))
    cases.append(("alpha=0 fill + 1px corner", None))
    for w, h in ((1, 1), (3, 7), (17, 5), (33, 66), (100, 61), (257, 3)):
        pm = QPixmap(w, h)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        for _ in range(max(1, (w * h) // 40)):
            x, y = rng.randrange(w), rng.randrange(h)
            pw, ph = rng.randrange(1, max(2, w // 3)), rng.randrange(1, max(2, h // 3))
            a = rng.choice([1, 5, 9, 10, 11, 128, 255])
            p.fillRect(QRect(x, y, pw, ph), QColor(rng.randrange(256), rng.randrange(256),
                                                   rng.randrange(256), a))
        p.end()
        cases.append((f"random {w}x{h}", pm))
    return cases


# ---------------- adversarial detection cases ----------------
def adversarial_cases():
    """边界用例：对角接触不得连通、阈值边界、细线、棋盘格等。

    '#'=不透明(255)  '.'=恰好等于阈值(10)  '~'=低于阈值(9)
    """
    rng = random.Random(4242)
    out = []

    def from_mask(name, mask):
        h = len(mask)
        w = len(mask[0]) if h else 0
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        for y, row in enumerate(mask):
            for x, ch in enumerate(row):
                if ch == "#":
                    img.setPixelColor(x, y, QColor(255, 0, 0, 255))
                elif ch == ".":
                    img.setPixelColor(x, y, QColor(255, 0, 0, 10))
                elif ch == "~":
                    img.setPixelColor(x, y, QColor(255, 0, 0, 9))
        out.append((name, img))

    from_mask("对角接触（不得连通，应为 3 个）", ["#~~", "~#~", "~~#"])
    from_mask("单像素散点（应为 4 个）", ["#~#", "~~~", "#~#"])
    from_mask("细十字（应为 1 个）", ["~~#~~", "~~#~~", "#####", "~~#~~", "~~#~~"])
    from_mask("阈值边界（. 恰好达标 / ~ 不达标，应为 2 个）", ["#.~", "..~", "~~."])
    from_mask("全满（应为 1 个）", ["###", "###", "###"])
    from_mask("回字形含洞（应为 1 个）", ["####", "#~~#", "#~~#", "####"])
    from_mask("棋盘格（4 连通应为 13 个）",
              ["#~#~#", "~#~#~", "#~#~#", "~#~#~", "#~#~#"])
    from_mask("楼梯形阶梯（4 连通应为 1 个）", ["##~~", "~##~", "~~##"])
    for w, h in ((37, 23), (64, 40)):
        from_mask(f"随机细碎 {w}x{h}",
                  ["".join(rng.choice("#~") for _ in range(w)) for _ in range(h)])
    return out


def main():
    app = QApplication(sys.argv)
    import time

    print("=== 1. _alpha_bounds 等价性（对照原逐像素实现）===")
    cases = synth_cases()
    # corner-only case built explicitly (1 non-transparent pixel)
    pm = QPixmap(20, 20)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.fillRect(19, 0, 1, 1, QColor(255, 255, 255, 255))
    p.end()
    cases.append(("single pixel at (19,0)", pm))
    for name in ("mage_sprite_keyed.png", "sprite_demo_keyed.png",
                 "003-妙蛙花.png", "007-杰尼龟.png", "033-尼多利诺.png"):
        f = ROOT / name
        if f.exists():
            cases.append((name, QPixmap(str(f))))

    bad = 0
    for name, pm in cases:
        ref = alpha_bounds_reference(pm)
        got = T._alpha_bounds(pm)
        if ref != got:
            bad += 1
            print(f"    DIFF {name}: ref={ref} got={got}")
    check(f"_alpha_bounds 与参照完全一致（{len(cases)} 个用例）", bad == 0, f"差异 {bad} 个")

    print("\n=== 2. frame_base_size 缓存等价性 ===")
    act = Action("t")
    act.frames = [QPixmap(str(ROOT / "003-妙蛙花.png")), QPixmap(str(ROOT / "007-杰尼龟.png"))]
    act.frame_rotations = {0: 32, 1: 90}
    act.frame_mirror_h = {1}
    act.frame_rotation_pivots = {0: (0.3, 0.7)}
    act.invalidate_transforms()
    ok = True
    for i in (0, 1):
        uncached = T.transform_frame_pixmap(
            act.frames[i], act.frame_rotations.get(i, 0), i in act.frame_mirror_h,
            i in act.frame_mirror_v, None, act.frame_rotation_pivots.get(i, T.DEFAULT_ROTATION_PIVOT),
        )
        want = (uncached.width(), uncached.height())
        got1 = T.frame_base_size(act, i)
        got2 = T.frame_base_size(act, i)
        if not (want == got1 == got2):
            ok = False
            print(f"    DIFF frame {i}: want={want} got1={got1} got2={got2}")
    # cache must be dropped when transforms are invalidated
    act.frame_rotations[0] = 0
    act.invalidate_transforms()
    fresh = T.frame_base_size(act, 0)
    want_fresh = T.transform_frame_pixmap(act.frames[0], 0, False, False, None,
                                         T.DEFAULT_ROTATION_PIVOT)
    if fresh != (want_fresh.width(), want_fresh.height()):
        ok = False
        print(f"    DIFF after invalidate: got={fresh}")
    check("frame_base_size 与原结果一致，且失效后重算", ok)

    print("\n=== 3. 元素检测等价性（对照原 flood fill）===")
    det_cases = [("mage_sprite_keyed.png", ROOT / "mage_sprite_keyed.png"),
                 ("sprite_demo_keyed.png", ROOT / "sprite_demo_keyed.png"),
                 ("003-妙蛙花.png", ROOT / "003-妙蛙花.png"),
                 ("007-杰尼龟.png", ROOT / "007-杰尼龟.png"),
                 ("033-尼多利诺.png", ROOT / "033-尼多利诺.png")]
    for w, h in ((1, 1), (5, 4), (31, 17), (64, 64)):
        pm = QPixmap(w, h)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        rng = random.Random(w * 100 + h)
        for _ in range(5):
            p.fillRect(QRect(rng.randrange(w), rng.randrange(h), 3, 2), QColor(255, 0, 0, 255))
        p.end()
        det_cases.append((f"synthetic {w}x{h}", pm))
    det_cases.extend(adversarial_cases())

    bad = 0
    for name, src in det_cases:
        if isinstance(src, QPixmap):
            img = src.toImage()
        elif isinstance(src, QImage):
            img = src          # 注意：不能走 QImage(str(src))，那会被当成路径
        else:
            img = QImage(str(src))
        t0 = time.perf_counter()
        ref = detect_reference(img)
        t_ref = time.perf_counter() - t0
        t0 = time.perf_counter()
        got = _detect_frames_image(img)
        t_new = time.perf_counter() - t0
        same = ref == got
        if not same:
            bad += 1
        speed = (t_ref / t_new) if t_new > 0 else 0
        print(f"    {name:<26} 元素={len(ref):<4} 一致={same}  "
              f"旧={t_ref*1000:8.1f}ms 新={t_new*1000:8.1f}ms  提速={speed:5.1f}x")
    check(f"元素检测与参照完全一致（{len(det_cases)} 个用例）", bad == 0, f"差异 {bad} 个")

    print()
    if FAIL:
        print("等价性验证失败：", *FAIL, sep="\n  - ")
        return 1
    print("全部等价性验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
