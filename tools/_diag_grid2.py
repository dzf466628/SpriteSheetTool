"""临时探针（第二版）：怎么把这张精灵图"横竖均分"定出来，以及均分后各帧的真值偏移。

第一版结论：Y 方向完美均分（17 行 × 481.06 = 8178）；X 方向内容中心是散的（角色横向会动），
所以不能拿"中心去重"定列。这一版试两种能定列的办法，并把均分结果跟内容块对齐验证。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _diag_align_compare import load_frames                    # noqa: E402

DEFAULT = r"E:\开发源文件\SpriteSheetTool\SpriteSheetTool\9月25日(1)_keyed_spritesheet_0.png"


def main():
    from PIL import Image
    img = Image.open(DEFAULT).convert("RGBA")
    W, H = img.size
    frames = load_frames(DEFAULT)
    print(f"图集 {W}x{H}，内容块 {len(frames)} 个")

    # --- 定行：内容块的 y 中心按 481 归并，看每行几个 ----------------------
    rows = {}
    for f in frames:
        rows.setdefault(round((f.box[1] + f.h / 2.0) / (H / 17)), []).append(f)
    print(f"按 H/17 = {H/17:.2f} 归行：{len(rows)} 行，每行帧数 {sorted(set(len(v) for v in rows.values()))}")
    n_rows = len(rows)

    # --- 定列办法一：每行内按 x 排序，名次就是列号（格子是把图横着切 17 份）----
    pitch_x = W / n_rows
    col_of = {}
    for key, group in rows.items():
        for rank, f in enumerate(sorted(group, key=lambda f: f.box[0] + f.w / 2.0)):
            col_of[id(f)] = rank
    inside = []
    dxs, dys = [], []
    for f in frames:
        col, row = col_of[id(f)], f.box[1] // pitch_x      # 行号只用来验算
        dx = f.box[0] - col * pitch_x
        dy = f.box[1] - row * pitch_x if row == row else 0
        dy = f.box[1] - round((f.box[1] + f.h / 2.0) / pitch_x - 0.5) * pitch_x
        dxs.append(dx)
        dys.append(dy)
        inside.append(0 <= dx and dx + f.w <= pitch_x + 0.5)
    print(f"\n办法一（行内按 x 排名当列号）：{sum(inside)}/{len(frames)} 个内容完整落在自己那一格里")
    print(f"  格内 x 落点 {min(dxs):.0f}~{max(dxs):.0f}（跨度 {max(dxs)-min(dxs):.0f}，"
          f"格宽 {pitch_x:.1f}）→ 这是横向真实位移")
    print(f"  格内 y 落点 {min(dys):.0f}~{max(dys):.0f}（跨度 {max(dys)-min(dys):.0f}）→ 纵向真实位移")

    # --- 定列办法二：不靠排名，纯粹看"哪些列是空的"（格与格之间的空隙是周期的）---
    small = img.crop((0, 0, W, H))
    alpha = small.getchannel("A")
    step_rows = 4
    col_sum = [0] * W
    for y in range(0, H, step_rows):
        line = alpha.crop((0, y, W, y + 1)).tobytes()
        for x, v in enumerate(line):
            if v:
                col_sum[x] += v
    empty = [x for x, v in enumerate(col_sum) if v == 0]
    # 把连续的空列并成一段段空隙，取每段的中心
    gaps, cur = [], []
    for x in empty:
        if cur and x - cur[-1] > 1:
            gaps.append(sum(cur) / len(cur))
            cur = []
        cur.append(x)
    if cur:
        gaps.append(sum(cur) / len(cur))
    print(f"\n办法二（找整列空白的空隙）：空隙 {len(gaps)} 段")
    if len(gaps) > 2:
        gap_d = [round(b - a, 1) for a, b in zip(gaps, gaps[1:])]
        print(f"  空隙中心间距：{gap_d}")
        avg = sum(gap_d) / len(gap_d)
        print(f"  平均间距 {avg:.1f} → 列数 {W / avg:.2f}")

    # --- 按均分算出的"真值偏移"：格子底部中心 − 紧切框中心 ----------------
    print("\n按「格底中心 − 紧切框中心」算前 5 帧的偏移（这就是引擎要的那个数）：")
    for f in frames[:5]:
        col = col_of[id(f)]
        row = round((f.box[1] + f.h / 2.0) / pitch_x - 0.5)
        cell_cx = (col + 0.5) * pitch_x
        cell_bottom = (row + 1) * pitch_x
        crop_cx = f.box[0] + f.w / 2.0
        crop_cy = f.box[1] + f.h / 2.0
        print(f"  帧{col_of[id(f)]}/行{row}: 偏移 = ({cell_cx - crop_cx:7.1f}, "
              f"{cell_bottom - crop_cy:7.1f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())