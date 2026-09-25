"""临时探针：为什么「自动对齐」还会飘（尾巴把中线搞偏）。

合成素材（身体**固定不动**，只有尾巴长度在变 —— 这就是"尾巴甩"的情形）：
    帧画布 140x90，身体 = 40x60 的块固定在 (45,10)，尾巴 = 6px 高的细条从身体左侧伸出去。
    tail_len = 0 / 16 / 40（尾巴越长，外框左边界越靠左）

理想情况：对齐后身体一个像素都不该动（漂移 = 0）。

对比四种口径：
    A 现在用的：外框底部中心（并集框底部中心）
    B 质量重心：alpha 加权质心
    C 投影互相关：列/行 alpha 投影与"内容最多的那一帧"互相关（峰落在身体上）
    D 核心外框：按 alpha 质量掐掉两端离群像素（2%~98%）后的外框底部中心
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtGui import QColor, QPainter, QPixmap                # noqa: E402
from PyQt6.QtWidgets import QApplication                         # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv)

BODY = (45, 10, 40, 60)          # 身体：固定不动
TAIL_Y = 34                      # 尾巴贴在这个高度（细条，6px 高）
CANVAS = (140, 90)


def make_frame(tail_len: int, tail_down: int = 0) -> QPixmap:
    """身体固定 + 尾巴长度可变（tail_down>0 时再挂一条往下垂的尾巴）。"""
    pm = QPixmap(*CANVAS)
    pm.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pm)
    painter.fillRect(*BODY, QColor("#FF6B6B"))                  # 身体
    if tail_len:
        painter.fillRect(BODY[0] - tail_len, TAIL_Y, tail_len, 6, QColor("#4D96FF"))
    if tail_down:
        painter.fillRect(BODY[0] + 30, BODY[1] + BODY[3], 6, tail_down, QColor("#4D96FF"))
    painter.end()
    return pm


def alpha_map(pm: QPixmap):
    """(w, h, 每列 alpha 和, 每行 alpha 和, 总质量, x 质心, y 质心)。"""
    img = pm.toImage().convertToFormat(pm.toImage().Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    bits = img.constBits()
    bits.setsize(img.sizeInBytes())
    data = bytes(bits)
    stride = img.bytesPerLine()
    cols = [0] * w
    rows = [0] * h
    mx = my = mass = 0
    for y in range(h):
        row = data[y * stride:y * stride + w * 4]
        a = row[3::4]
        s = 0
        for x, v in enumerate(a):
            if v:
                cols[x] += v
                s += v
                mx += x * v
                my += y * v
                mass += v
        rows[y] = s
    return w, h, cols, rows, mass, (mx / mass if mass else 0.0), (my / mass if mass else 0.0)


def bbox(pm: QPixmap):
    img = pm.toImage().convertToFormat(pm.toImage().Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    bits = img.constBits()
    bits.setsize(img.sizeInBytes())
    data = bytes(bits)
    stride = img.bytesPerLine()
    left, top, right, bottom = w, h, -1, -1
    for y in range(h):
        row = data[y * stride:y * stride + w * 4][3::4]
        nz = [x for x, v in enumerate(row) if v]
        if not nz:
            continue
        top = min(top, y)
        bottom = y
        left = min(left, nz[0])
        right = max(right, nz[-1])
    return left, top, right, bottom


def core_box(pm: QPixmap, lo: float = 0.02, hi: float = 0.98):
    """按 alpha 质量掐掉两端离群像素后的"核心外框"。"""
    w, h, cols, rows, mass, _cx, _cy = alpha_map(pm)
    def _cut(profile, total):
        low, high = total * lo, total * hi
        acc = 0
        first = last = 0
        for i, v in enumerate(profile):
            acc += v
            if acc >= low and not first:
                first = i
            if acc <= high:
                last = i
        return first, last
    x0, x1 = _cut(cols, mass)
    y0, y1 = _cut(rows, mass)
    return x0, y0, x1, y1


def correlate(ref_prof, ref_len, prof, length, span: int):
    """一维投影互相关：返回让 prof 对上 ref_prof 的位移（取重叠区点积最大）。"""
    best, best_score = 0, None
    for shift in range(-span, span + 1):
        score = 0
        for i in range(length):
            j = i + shift
            if 0 <= j < ref_len:
                score += prof[i] * ref_prof[j]
        if best_score is None or score > best_score:
            best_score, best = score, shift
    return best


def body_position(frame_pm: QPixmap, dx: int, dy: int):
    """把这一帧按 (dx,dy) 摆好以后，身体左上角落在哪（在帧坐标系里）。"""
    return BODY[0] + dx, BODY[1] + dy


def main() -> int:
    print("=== A 现在用的口径：外框底部中心（并集框底部中心）===")
    for tag, tails in (("尾巴往侧面甩", [(0, 0), (16, 0), (40, 0)]),
                       ("尾巴往下垂", [(0, 0), (0, 20), (0, 40)])):
        pms = [make_frame(t, d) for t, d in tails]
        boxes = [bbox(pm) for pm in pms]
        if "侧面" in tag:
            left = min(b[0] for b in boxes)
            right = max(b[2] for b in boxes)
            ref = ((left + right) / 2.0, max(b[3] for b in boxes))
            deltas = [(ref[0] - (b[0] + b[2]) / 2.0, ref[1] - b[3]) for b in boxes]
        else:
            left = min(b[0] for b in boxes)
            right = max(b[2] for b in boxes)
            ref = ((left + right) / 2.0, max(b[3] for b in boxes))
            deltas = [(ref[0] - (b[0] + b[2]) / 2.0, ref[1] - b[3]) for b in boxes]
        pos = [body_position(pm, int(round(d[0])), int(round(d[1])))
               for pm, d in zip(pms, deltas)]
        sx = max(p[0] for p in pos) - min(p[0] for p in pos)
        sy = max(p[1] for p in pos) - min(p[1] for p in pos)
        print(f"  {tag}: 偏移={[(int(round(d[0])), int(round(d[1]))) for d in deltas]} "
              f"→ 身体漂移 X={sx}px Y={sy}px")

    print("=== B 质量重心（alpha 加权质心）===")
    for tag, tails in (("尾巴往侧面甩", [(0, 0), (16, 0), (40, 0)]),
                       ("尾巴往下垂", [(0, 0), (0, 20), (0, 40)])):
        pms = [make_frame(t, d) for t, d in tails]
        stats = [alpha_map(pm) for pm in pms]
        mass = sum(s[4] for s in stats)
        ref_x = sum(s[5] * s[4] for s in stats) / mass
        ref_y = sum(s[6] * s[4] for s in stats) / mass
        deltas = [(ref_x - s[5], ref_y - s[6]) for s in stats]
        pos = [body_position(pm, int(round(d[0])), int(round(d[1])))
               for pm, d in zip(pms, deltas)]
        sx = max(p[0] for p in pos) - min(p[0] for p in pos)
        sy = max(p[1] for p in pos) - min(p[1] for p in pos)
        print(f"  {tag}: 偏移={[(int(round(d[0])), int(round(d[1]))) for d in deltas]} "
              f"→ 身体漂移 X={sx}px Y={sy}px")

    print("=== C 投影互相关（参考帧 = 内容最多的那一帧）===")
    for tag, tails in (("尾巴往侧面甩", [(0, 0), (16, 0), (40, 0)]),
                       ("尾巴往下垂", [(0, 0), (0, 20), (0, 40)])):
        pms = [make_frame(t, d) for t, d in tails]
        maps = [alpha_map(pm) for pm in pms]
        ref_i = max(range(len(maps)), key=lambda i: maps[i][4])   # 质量最大 = 最完整的一帧
        ref = maps[ref_i]
        deltas = []
        for m in maps:
            sx = correlate(ref[2], ref[0], m[2], m[0], 60)
            sy = correlate(ref[3], ref[1], m[3], m[1], 40)
            deltas.append((sx, sy))
        pos = [body_position(pm, d[0], d[1]) for pm, d in zip(pms, deltas)]
        sx = max(p[0] for p in pos) - min(p[0] for p in pos)
        sy = max(p[1] for p in pos) - min(p[1] for p in pos)
        print(f"  {tag}: 偏移={deltas} → 身体漂移 X={sx}px Y={sy}px")

    print("=== D 核心外框（掐掉两端 2% 质量）===")
    for tag, tails in (("尾巴往侧面甩", [(0, 0), (16, 0), (40, 0)]),
                       ("尾巴往下垂", [(0, 0), (0, 20), (0, 40)])):
        pms = [make_frame(t, d) for t, d in tails]
        boxes = [core_box(pm) for pm in pms]
        left = min(b[0] for b in boxes)
        right = max(b[2] for b in boxes)
        ref = ((left + right) / 2.0, max(b[3] for b in boxes))
        deltas = [(ref[0] - (b[0] + b[2]) / 2.0, ref[1] - b[3]) for b in boxes]
        pos = [body_position(pm, int(round(d[0])), int(round(d[1])))
               for pm, d in zip(pms, deltas)]
        sx = max(p[0] for p in pos) - min(p[0] for p in pos)
        sy = max(p[1] for p in pos) - min(p[1] for p in pos)
        print(f"  {tag}: 偏移={[(int(round(d[0])), int(round(d[1]))) for d in deltas]} "
              f"→ 身体漂移 X={sx}px Y={sy}px")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())