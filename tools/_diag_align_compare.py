# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""临时探针（第四版）：四种对齐口径放在真实素材上比一比。

判定标准（不循环论证）：把各帧按某种口径摆到同一张逻辑画布上，然后**两两算相邻帧的
互相关位移** —— 位移应该等于"真实运动"。这张图集实测真实运动 ≈ 0（原地动画），
所以**残留位移越小 = 越不飘**。另外再报一个直观的数：质量重心跨帧的跨度。

口径：
  A 各帧外框居中           （工具现在默认的摆法）
  B 质量重心对齐           （对齐 alpha 加权质心）
  C 互相关对齐到中位参考帧 （我建议的）
  D 外框底部中心对齐       （我上一版实现的那套）
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

DEFAULT = r"E:\开发源文件\SpriteSheetTool\SpriteSheetTool\9月25日(1)_keyed_spritesheet_0.png"
MIN_ALPHA = 8
BODY_AREA = 20000
MARGIN = 120
JUDGE_STEP = 6
JUDGE_SPAN_X = 60
JUDGE_SPAN_Y = 30


def runs(flags, gap=2):
    out, i, n = [], 0, len(flags)
    while i < n:
        if not flags[i]:
            i += 1
            continue
        start = i
        while i < n:
            if flags[i]:
                i += 1
                continue
            j = i
            while j < n and not flags[j]:
                j += 1
            if j - i <= gap and j < n:
                i = j
                continue
            break
        out.append((start, i))
    return out


class F:
    __slots__ = ("box", "w", "h", "mass", "cx", "cy", "px", "py")

    def __init__(self, box, w, h, mass, cx, cy, px, py):
        self.box, self.w, self.h, self.mass = box, w, h, mass
        self.cx, self.cy, self.px, self.py = cx, cy, px, py


def load_frames(path):
    img = Image.open(path).convert("RGBA")
    W, H = img.size
    alpha = img.getchannel("A").point(lambda v: v if v >= MIN_ALPHA else 0).tobytes()
    row_sums = [sum(alpha[y * W:(y + 1) * W]) for y in range(H)]
    out = []
    for y0, y1 in runs([s > 0 for s in row_sums], gap=2):
        block = alpha[y0 * W:y1 * W]
        col_sums = [sum(block[x::W]) for x in range(W)]
        for x0, x1 in runs([s > 0 for s in col_sums], gap=2):
            w, h = x1 - x0, y1 - y0
            if w * h < BODY_AREA:
                continue
            px, py, mass = [0] * w, [0] * h, 0
            for y in range(y0, y1):
                row = alpha[y * W + x0:y * W + x1]
                s = 0
                for i, v in enumerate(row):
                    if v:
                        px[i] += v
                        s += v
                py[y - y0] = s
                mass += s
            if not mass:
                continue
            out.append(F((x0, y0, x1, y1), w, h, mass,
                         sum(i * v for i, v in enumerate(px)) / mass,
                         sum(i * v for i, v in enumerate(py)) / mass, px, py))
    # 按格子序（沿用 x0//481 的思路不靠谱，这里按"读入顺序"= 行带内从左到右，够用）
    return out


def place(prof, pos, size):
    canvas = [0] * size
    for i, v in enumerate(prof):
        j = pos + i
        if 0 <= j < size:
            canvas[j] = v
    return canvas


def correlate(a, b, span):
    """b 对上 a 要移动多少（点积最大）。"""
    best, best_score = 0, None
    for shift in range(-span, span + 1):
        score = 0.0
        for i, v in enumerate(b):
            if not v:
                continue
            j = i + shift
            if 0 <= j < len(a):
                score += v * a[j]
        if best_score is None or score > best_score:
            best_score, best = score, shift
    return best


def evaluate(name, frames, ox, oy, max_w, max_h, span_x, span_y):
    """按给定偏移摆好，量残留位移 + 重心跨度。"""
    size_x = max_w + 2 * MARGIN + JUDGE_SPAN_X * 2
    size_y = max_h + 2 * MARGIN + JUDGE_SPAN_Y * 2
    cx_list, cy_list, dxs, dys = [], [], [], []
    prev = None
    for k, f in enumerate(frames):
        pos_x = MARGIN + JUDGE_SPAN_X + (max_w - f.w) // 2 + ox[k]
        pos_y = MARGIN + JUDGE_SPAN_Y + (max_h - f.h) // 2 + oy[k]
        cx_list.append(pos_x + f.cx)
        cy_list.append(pos_y + f.cy)
        if k % JUDGE_STEP == 0:
            cur = (place(f.px, pos_x, size_x), place(f.py, pos_y, size_y))
            if prev is not None and k > 0:
                dxs.append(abs(correlate(prev[0], cur[0], JUDGE_SPAN_X)))
                dys.append(abs(correlate(prev[1], cur[1], JUDGE_SPAN_Y)))
            prev = cur
    sx = max(cx_list) - min(cx_list)
    sy = max(cy_list) - min(cy_list)
    mx = max(dxs) if dxs else 0
    my = max(dys) if dys else 0
    avx = sum(dxs) / len(dxs) if dxs else 0
    avy = sum(dys) / len(dys) if dys else 0
    print(f"{name:<22} 重心跨度 X={sx:>5.1f} Y={sy:>5.1f} | "
          f"残留位移 max X={mx:>4d} Y={my:>4d}  平均 X={avx:>4.1f} Y={avy:>4.1f}")
    return avx + avy


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT)
    frames = load_frames(path)
    n = len(frames)
    max_w = max(f.w for f in frames)
    max_h = max(f.h for f in frames)
    print(f"{path.name}: {n} 帧（面积>={BODY_AREA}）")
    print(f"帧尺寸 宽 {min(f.w for f in frames)}–{max_w}，高 {min(f.h for f in frames)}–{max_h}")
    span_x, span_y = 120, 40

    # 口径 C 需要：与"尺寸中位的那一帧"互相关求修正量（直接在帧自身坐标里算）
    order = sorted(range(n), key=lambda i: frames[i].w * frames[i].h)
    ref_i = order[n // 2]
    ref = frames[ref_i]
    cx_fix = [correlate(ref.px, f.px, span_x) for f in frames]
    cy_fix = [correlate(ref.py, f.py, span_y) for f in frames]

    def centered(f):
        """工具默认落点：居中。"""
        return (max_w - f.w) // 2, (max_h - f.h) // 2

    refx, refy = centered(ref)
    # 口径 B：把各帧质量重心拉到"参考帧重心所在的那个位置"
    tx = refx + ref.cx
    ty = refy + ref.cy
    dx_b, dy_b = [], []
    for f in frames:
        fx, fy = centered(f)
        dx_b.append(int(round(tx - (fx + f.cx))))
        dy_b.append(int(round(ty - (fy + f.cy))))
    # 口径 C：落点_i = 落点_ref + 互相关位移  → off = 落点_ref + dx - 居中落点
    dx_c = [int(refx + d - centered(f)[0]) for f, d in zip(frames, cx_fix)]
    dy_c = [int(refy + d - centered(f)[1]) for f, d in zip(frames, cy_fix)]
    # 口径 D：外框底部中心对齐到参考帧的框底部中心（全部相对量，不用图集坐标）
    tdx = refx + ref.w / 2
    tdy = refy + ref.h
    dx_d, dy_d = [], []
    for f in frames:
        fx, fy = centered(f)
        dx_d.append(int(round(tdx - (fx + f.w / 2))))
        dy_d.append(int(round(tdy - (fy + f.h))))

    print(f"\n{'口径':<22} {'直观漂移（重心跨度）':<28} {'判定：残留的帧间位移'}")
    print("-" * 96)
    scores = {}
    scores["A"] = evaluate("A 各帧外框居中", frames, [0] * n, [0] * n, max_w, max_h, span_x, span_y)
    scores["B"] = evaluate("B 质量重心对齐", frames, dx_b, dy_b, max_w, max_h, span_x, span_y)
    scores["C"] = evaluate("C 互相关→中位参考帧", frames, dx_c, dy_c, max_w, max_h, span_x, span_y)
    scores["D"] = evaluate("D 外框底部中心（上一版）", frames, dx_d, dy_d, max_w, max_h, span_x, span_y)

    best = min(scores, key=lambda k: scores[k])
    print(f"\n残留最小的是 {best}（平均残留 {scores[best]:.1f}px）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())