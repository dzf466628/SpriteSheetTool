"""临时探针（第二版）：量真实图集的每一帧，找出"紧切撑大框导致的位置跳"。

为什么这么写：
  * 图集 8178x8178，逐像素跑 Python 太慢 —— 全部用 bytes 步长切片求和（C 层，一次遍历）。
  * alpha>=8 才算内容：这张图的 α=1..7 有 2.2 万微弱像素，会把网格切成一堆 1x1 碎片。
  * 图集是按 pitch 排的格子：每格 = 一个动作画布，格内位置就是工具摆出来的位置。
    所以"格内身体位置" = (外框左上 + 质量重心) mod pitch —— 这才是真正落到磁盘上的位置。
  * 判定"这几帧飘了"的签名：**框变大了、身体没动** ——
    格内身体位置跳了一大截，而跟上一帧算出的重合位移（= 真实运动）却很小。
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from PIL import Image

DEFAULT = r"E:\开发源文件\SpriteSheetTool\SpriteSheetTool\9月25日(1)_keyed_spritesheet_0.png"
RAMP = " .:-=+*#%@"
MIN_ALPHA = 8
MIN_AREA = 400


def runs(flags: list[bool], gap: int = 2) -> list[tuple[int, int]]:
    """把 True 的连续段找出来，顺便把 <=gap 的空隙并进去（抗噪）。"""
    out: list[tuple[int, int]] = []
    i, n = 0, len(flags)
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


class Frame:
    __slots__ = ("box", "w", "h", "mass", "cx", "cy", "by", "px", "py")

    def __init__(self, box, w, h, mass, cx, cy, by, px, py):
        self.box, self.w, self.h, self.mass = box, w, h, mass
        self.cx, self.cy, self.by = cx, cy, by
        self.px, self.py = px, py            # 逐列 / 逐行 alpha 投影


def make_frame(alpha: bytes, W: int, x0: int, y0: int, x1: int, y1: int) -> Frame | None:
    w, h = x1 - x0, y1 - y0
    if w * h < MIN_AREA:
        return None
    px = [0] * w
    py = [0] * h
    mass = 0
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
        return None
    cx = sum(i * v for i, v in enumerate(px)) / mass
    cy = sum(i * v for i, v in enumerate(py)) / mass
    need, acc, yb = mass * 0.15, 0, h - 1
    for i in range(h - 1, -1, -1):
        acc += py[i]
        yb = i
        if acc >= need:
            break
    band = py[yb:]
    bm = sum(band) or 1
    by = sum((yb + i) * v for i, v in enumerate(band)) / bm
    return Frame((x0, y0, x1, y1), w, h, mass, cx, cy, by, px, py)


def correlate(ref: list[int], rlen: int, prof: list[int], span: int) -> tuple[int, float]:
    """prof 对上 ref 要移动多少；同时给出归一化后的吻合度（用来判断"是不是同一套动作"）。"""
    best, best_score, total = 0, -1.0, sum(prof) or 1
    for shift in range(-span, span + 1):
        score = 0
        for i, v in enumerate(prof):
            j = i + shift
            if 0 <= j < rlen:
                score += v * ref[j]
        score /= total
        if score > best_score:
            best_score, best = score, shift
    return best, best_score


def spread(vals: list[float]) -> float:
    return max(vals) - min(vals)


def ascii_art(img: Image.Image, box, cols: int = 44) -> list[str]:
    x0, y0, x1, y1 = box
    crop = img.crop((x0, y0, x1, y1)).getchannel("A")
    rows = max(6, min(26, int(cols * crop.height / max(1, crop.width) / 2)))
    small = crop.resize((cols, rows), Image.BILINEAR)
    data = list(small.getdata())
    return ["".join(RAMP[min(9, data[y * cols + x] * 10 // 256)] for x in range(cols))
            for y in range(rows)]


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT)
    img = Image.open(path).convert("RGBA")
    W, H = img.size
    print(f"文件 {path.name}   {W}x{H}")
    alpha = img.getchannel("A").point(lambda v: v if v >= MIN_ALPHA else 0).tobytes()

    row_sums = [sum(alpha[y * W:(y + 1) * W]) for y in range(H)]
    ybands = runs([s > 0 for s in row_sums], gap=2)
    print(f"行带 {len(ybands)} 条（按整行空/非空切，空隙<=2 并起来抗噪）")

    frames: list[Frame] = []
    for y0, y1 in ybands:
        block = alpha[y0 * W:y1 * W]
        col_sums = [sum(block[x::W]) for x in range(W)]
        for x0, x1 in runs([s > 0 for s in col_sums], gap=2):
            f = make_frame(alpha, W, x0, y0, x1, y1)
            if f is not None:
                frames.append(f)
    print(f"有效帧 {len(frames)} 个（面积 >= {MIN_AREA}）")

    xs = sorted({f.box[0] for f in frames})
    ys = sorted({f.box[1] for f in frames})
    pxs = Counter(b - a for a, b in zip(xs, xs[1:]) if b - a > 20)
    pys = Counter(b - a for a, b in zip(ys, ys[1:]) if b - a > 20)
    pitch_x = pxs.most_common(1)[0][0] if pxs else 0
    pitch_y = pys.most_common(1)[0][0] if pys else 0
    print(f"网格 pitch = {pitch_x} x {pitch_y}   （格子数约 "
          f"{W // max(1, pitch_x)} x {H // max(1, pitch_y)} = {W // max(1, pitch_x) * (H // max(1, pitch_y))}）")
    widths = [f.w for f in frames]
    heights = [f.h for f in frames]
    print(f"帧尺寸 {min(widths)}x{min(heights)} … {max(widths)}x{max(heights)}"
          f"   （不一样 = 紧切过）")

    # ---- 相邻帧重合位移（真实运动）+ 格内身体位置（磁盘上的实际摆放）----
    rows_out = []
    prev: Frame | None = None
    for idx, f in enumerate(frames):
        bx = (f.box[0] + f.cx) % pitch_x if pitch_x else f.box[0] + f.cx
        by = (f.box[1] + f.cy) % pitch_y if pitch_y else f.box[1] + f.cy
        if prev is not None:
            dx, sx = correlate(prev.px, prev.w, f.px, max(16, pitch_x // 6))
            dy, sy = correlate(prev.py, prev.h, f.py, max(16, pitch_y // 6))
        else:
            dx = dy = 0
            sx = sy = 0.0
        rows_out.append([idx, f, bx, by, dx, dy, sx, sy])
        prev = f

    print("\n=== 逐帧（前 40 帧）：格内身体位置 + 与上一帧的重合位移 ===")
    print(f"{'#':>3} {'宽x高':>9} {'格内重心x':>9} {'格内重心y':>9} "
          f"{'Δ格内x':>7} {'Δ格内y':>7} {'重合dx':>7} {'重合dy':>7} {'吻合度':>7}")
    prev_bx = prev_by = None
    for row in rows_out[:40]:
        idx, f, bx, by, dx, dy, sx, sy = row
        dbx = "" if prev_bx is None else f"{bx - prev_bx:+7.1f}"
        dby = "" if prev_by is None else f"{by - prev_by:+7.1f}"
        print(f"{idx:>3} {f'{f.w}x{f.h}':>9} {bx:>9.1f} {by:>9.1f} {dbx:>7} {dby:>7} "
              f"{dx:>+7d} {dy:>+7d} {sx:>7.2f}")
        prev_bx, prev_by = bx, by

    # ---- 诊断：框跳了、身体没动 = 紧切撑大框导致的位置跳 ----
    print("\n=== 诊断：格内身体位置跳了，但真实运动很小 —— 就是「紧切把框撑大」的那几帧 ===")
    sus = []
    for k in range(1, len(rows_out)):
        _, f, bx, by, dx, dy, sx, sy = rows_out[k]
        _, pf, pbx, pby, *_ = rows_out[k - 1]
        jump = max(abs(bx - pbx), abs(by - pby))
        motion = max(abs(dx), abs(dy))
        grew = f.w * f.h - pf.w * pf.h
        if jump > 3 and motion < jump * 0.5:
            sus.append((k, f.w, f.h, pf.w, pf.h, bx - pbx, by - pby, dx, dy, grew, sx))
    if not sus:
        print("  没有发现这种帧")
    for k, w, h, pw, ph, jx, jy, dx, dy, grew, sx in sus:
        print(f"  第 {k} 帧: {pw}x{ph} → {w}x{h}（面积 {grew:+d}）"
              f"  格内身体跳 {jx:+.1f},{jy:+.1f}px  真实运动 {dx:+d},{dy:+d}px  吻合度 {sx:.2f}")
    print(f"  合计 {len(sus)} / {len(frames)} 帧")

    # ---- 全图集的漂移幅度 ----
    bxs = [r[2] for r in rows_out]
    bys = [r[3] for r in rows_out]
    print(f"\n全图集：格内身体 x 变化范围 {min(bxs):.0f}–{max(bxs):.0f}（跨度 {spread(bxs):.0f}px），"
          f"y 变化范围 {min(bys):.0f}–{max(bys):.0f}（跨度 {spread(bys):.0f}px）")

    # ---- 看几张轮廓：最大的那帧 / 最小的那帧 ----
    big = max(range(len(frames)), key=lambda i: frames[i].w * frames[i].h)
    small = min(range(len(frames)), key=lambda i: frames[i].w * frames[i].h)
    for i in (big, small):
        f = frames[i]
        print(f"\n--- 第 {i} 帧 {f.w}x{f.h} 轮廓（面积{'最大' if i == big else '最小'}）---")
        for line in ascii_art(img, f.box):
            print("    |" + line + "|")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())