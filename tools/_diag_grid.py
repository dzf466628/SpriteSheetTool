# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""临时探针：这张图集到底是不是「横竖均分」，一格多大。

思路（不循环论证）：均分的话，各帧外框的中心在 X 上只会落在固定的几列、Y 上落在固定的几行，
列与列之间、行与行之间是等距的。所以把中心值去重、看间距就完了。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _diag_align_compare import load_frames                    # noqa: E402

DEFAULT = r"E:\开发源文件\SpriteSheetTool\SpriteSheetTool\9月25日(1)_keyed_spritesheet_0.png"


def uniq(values, tol=6):
    """按 tol 归并的"去重"（相邻很近的算同一个）。"""
    out = []
    for v in sorted(values):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(g) / len(g) for g in out], [len(g) for g in out]


def diffs(vals):
    return [round(b - a, 1) for a, b in zip(vals, vals[1:])]


def main():
    from PIL import Image
    frames = load_frames(DEFAULT)
    W, H = Image.open(DEFAULT).size
    print(f"图集 {W}x{H}，检出内容块 {len(frames)} 个")
    cxs = [f.box[0] + f.w / 2.0 for f in frames]
    cys = [f.box[1] + f.h / 2.0 for f in frames]
    print(f"外框尺寸范围：宽 {min(f.w for f in frames)}~{max(f.w for f in frames)}，"
          f"高 {min(f.h for f in frames)}~{max(f.h for f in frames)}")

    ux, nx = uniq(cxs)
    uy, ny = uniq(cys)
    print(f"\nX 中心去重后 {len(ux)} 列（每列帧数 {sorted(set(nx))}）")
    print(f"  列距：{diffs(ux)}")
    print(f"Y 中心去重后 {len(uy)} 行（每行帧数 {sorted(set(ny))}）")
    print(f"  行距：{diffs(uy)}")

    if len(ux) > 1:
        pitch_x = sum(diffs(ux)) / len(diffs(ux))
        print(f"\n列数 {len(ux)} × 平均列距 {pitch_x:.1f} = {len(ux) * pitch_x:.0f}"
              f"（图宽 {W}）")
    if len(uy) > 1:
        pitch_y = sum(diffs(uy)) / len(diffs(uy))
        print(f"行数 {len(uy)} × 平均行距 {pitch_y:.1f} = {len(uy) * pitch_y:.0f}"
              f"（图高 {H}）")
    print(f"列数×行数 = {len(ux) * len(uy)}（内容块 {len(frames)} 个）")

    # 每格内容在格内的位置：以"格左上角"为零点量一遍，看是不是都在格子里、偏多少
    if len(ux) > 1 and len(uy) > 1:
        px = sum(diffs(ux)) / len(diffs(ux))
        py = sum(diffs(uy)) / len(diffs(uy))
        offs = []
        for f in frames:
            col = min(range(len(ux)), key=lambda i: abs(cxs[frames.index(f)] - ux[i]))
            row = min(range(len(uy)), key=lambda i: abs(cys[frames.index(f)] - uy[i]))
            offs.append((col, row, f.box[0] - col * px, f.box[1] - row * py))
        inx = [o[2] for o in offs]
        iny = [o[3] for o in offs]
        print(f"\n内容在格内的落点：x {min(inx):.0f}~{max(inx):.0f}（跨度 {max(inx)-min(inx):.0f}）"
              f"，y {min(iny):.0f}~{max(iny):.0f}（跨度 {max(iny)-min(iny):.0f}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())