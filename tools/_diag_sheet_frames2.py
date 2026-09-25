"""临时探针（第三版）：用真实网格量图集里的位置漂移。

上一版错在 pitch 估成 21（我把不同行的 x0 混在一起取差值众数了）。这版：
  * 只认"主体帧"（面积 >= 20000），小碎块单独统计（可能是特效/残渣）
  * pitch = 主体帧 x0 去重后的差值众数（本图 = 481，两轴都是）
  * 格子坐标 = (x0 // pitch, y0 // pitch) 排序 —— 这才是真正的帧序
  * 「格内身体位置」= (x0 + 重心x) mod pitch —— 引擎/播放看到的就是这个位置
  * 对照：现在这样（各帧居中）vs 互相关对齐（以中位参考帧为准）
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from PIL import Image

DEFAULT = r"E:\开发源文件\SpriteSheetTool\SpriteSheetTool\9月25日(1)_keyed_spritesheet_0.png"
RAMP = " .:-=+*#%@"
MIN_ALPHA = 8
BODY_AREA = 20000


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


class Frame:
    __slots__ = ("box", "w", "h", "mass", "cx", "cy", "px", "py")

    def __init__(self, box, w, h, mass, cx, cy, px, py):
        self.box, self.w, self.h, self.mass = box, w, h, mass
        self.cx, self.cy, self.px, self.py = cx, cy, px, py


def make_frame(alpha, W, x0, y0, x1, y1):
    w, h = x1 - x0, y1 - y0
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
        return None
    cx = sum(i * v for i, v in enumerate(px)) / mass
    cy = sum(i * v for i, v in enumerate(py)) / mass
    return Frame((x0, y0, x1, y1), w, h, mass, cx, cy, px, py)


def correlate(ref, rlen, prof, span):
    best, best_score = 0, None
    for shift in range(-span, span + 1):
        score = 0.0
        for i, v in enumerate(prof):
            j = i + shift
            if 0 <= j < rlen:
                score += v * ref[j]
        if best_score is None or score > best_score:
            best_score, best = score, shift
    return best


def spread(vals):
    return max(vals) - min(vals)


def art(img, box, cols=44):
    crop = img.crop(box).getchannel("A")
    rows = max(6, min(24, int(cols * crop.height / max(1, crop.width) / 2)))
    d = list(crop.resize((cols, rows), Image.BILINEAR).getdata())
    return ["".join(RAMP[min(9, d[y * cols + x] * 10 // 256)] for x in range(cols))
            for y in range(rows)]


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT)
    img = Image.open(path).convert("RGBA")
    W, H = img.size
    alpha = img.getchannel("A").point(lambda v: v if v >= MIN_ALPHA else 0).tobytes()
    row_sums = [sum(alpha[y * W:(y + 1) * W]) for y in range(H)]

    all_frames = []
    for y0, y1 in runs([s > 0 for s in row_sums], gap=2):
        block = alpha[y0 * W:y1 * W]
        col_sums = [sum(block[x::W]) for x in range(W)]
        for x0, x1 in runs([s > 0 for s in col_sums], gap=2):
            if (x1 - x0) * (y1 - y0) < 400:
                continue
            f = make_frame(alpha, W, x0, y0, x1, y1)
            if f:
                all_frames.append(f)

    body = [f for f in all_frames if f.w * f.h >= BODY_AREA]
    small = [f for f in all_frames if f.w * f.h < BODY_AREA]
    print(f"文件 {path.name}  {W}x{H}")
    print(f"拆出 {len(all_frames)} 块：主体帧 {len(body)} 个（面积>={BODY_AREA}），"
          f"小碎块 {len(small)} 个")
    if small:
        sizes = Counter(f"{f.w}x{f.h}" for f in small)
        print(f"  小碎块尺寸：{sizes.most_common(6)}")

    xs = sorted({f.box[0] for f in body})
    ys = sorted({f.box[1] for f in body})
    cand_x = Counter(b - a for a, b in zip(xs, xs[1:]) if b - a > 50)
    cand_y = Counter(b - a for a, b in zip(ys, ys[1:]) if b - a > 50)
    px = cand_x.most_common(1)[0][0] if cand_x else 481
    py = cand_y.most_common(1)[0][0] if cand_y else 481
    print(f"网格 pitch = {px} x {py}（x0 去重差值众数：{cand_x.most_common(3)}）")

    body.sort(key=lambda f: (f.box[1] // py, f.box[0] // px))
    print(f"\n按格子序（{len(body)} 帧），帧尺寸："
          f"宽 {min(f.w for f in body)}–{max(f.w for f in body)}，"
          f"高 {min(f.h for f in body)}–{max(f.h for f in body)}")

    # 格内身体位置 = 引擎看到的位置
    pos = [((f.box[0] + f.cx) % px, (f.box[1] + f.cy) % py) for f in body]
    bxs = [p[0] for p in pos]
    bys = [p[1] for p in pos]
    print(f"\n=== 现在这版（各帧外框居中）在引擎里的位置 ===")
    print(f"  格内身体 x: {min(bxs):.1f}–{max(bxs):.1f}  跨度 {spread(bxs):.1f}px  ← 这就是飘的幅度")
    print(f"  格内身体 y: {min(bys):.1f}–{max(bys):.1f}  跨度 {spread(bys):.1f}px")

    # 相邻帧真实运动（重合位移）
    span = 60
    motion = [(0, 0)]
    for i in range(1, len(body)):
        a, b = body[i - 1], body[i]
        motion.append((correlate(a.px, a.w, b.px, span), correlate(a.py, a.h, b.py, min(span, 40))))
    mx = [abs(m[0]) for m in motion]
    my = [abs(m[1]) for m in motion]
    print(f"\n=== 相邻帧真实运动（互相关位移）===")
    print(f"  |dx| 最大 {max(mx)}px，|dy| 最大 {max(my)}px，"
          f"位移<=2px 的帧占 {sum(1 for a, b in zip(mx, my) if a <= 2 and b <= 2)}/{len(body)}")

    # 互相关对齐到"中位尺寸参考帧"
    order = sorted(range(len(body)), key=lambda i: body[i].w * body[i].h)
    ref_i = order[len(order) // 2]
    ref = body[ref_i]
    dxs = [correlate(ref.px, ref.w, f.px, span) for f in body]
    dys = [correlate(ref.py, ref.h, f.py, min(span, 40)) for f in body]
    fixed_x = [p[0] + d for p, d in zip(pos, dxs)]
    fixed_y = [p[1] + d for p, d in zip(pos, dys)]
    print(f"\n=== 互相关对齐（参考帧 = 尺寸中位的那一帧 #{ref_i} {ref.w}x{ref.h}）===")
    print(f"  格内身体 x: {min(fixed_x):.1f}–{max(fixed_x):.1f}  跨度 {spread(fixed_x):.1f}px")
    print(f"  格内身体 y: {min(fixed_y):.1f}–{max(fixed_y):.1f}  跨度 {spread(fixed_y):.1f}px")

    # 横切面：宽度变化 vs 需要修正多少
    print(f"\n=== 逐帧（每 6 帧一行）：宽度 / 格内x / 修正量dx ===")
    for i in range(0, len(body), 6):
        f = body[i]
        print(f"  #{i:>3} {f.w:>3}x{f.h:<3} 格内x={pos[i][0]:>6.1f} 修正dx={dxs[i]:>+4d} "
              f"格内y={pos[i][1]:>6.1f} 修正dy={dys[i]:>+4d}")

    widest = max(range(len(body)), key=lambda i: body[i].w)
    narrow = min(range(len(body)), key=lambda i: body[i].w)
    for i in (widest, narrow):
        print(f"\n--- 最{'宽' if i == widest else '窄'}的一帧 #{i} {body[i].w}x{body[i].h} ---")
        for line in art(img, body[i].box):
            print("    |" + line + "|")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())