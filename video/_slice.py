# -*- coding: utf-8 -*-
"""把蓝色史莱姆精灵图按 4x4 切成单帧，供视频角色动画使用。"""
import sys
from pathlib import Path
from PIL import Image

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
src = ROOT / "sprite_demo_keyed.png"
outdir = ROOT / "video" / "assets" / "slime"
outdir.mkdir(parents=True, exist_ok=True)

im = Image.open(src).convert("RGBA")
W, H = im.size
cols = rows = 4
cw, ch = W // cols, H // rows
print(f"source {W}x{H}, cell {cw}x{ch}")

# 等分切 16 帧
for r in range(rows):
    for c in range(cols):
        i = r * cols + c
        box = (c * cw, r * ch, (c + 1) * cw, (r + 1) * ch)
        cell = im.crop(box)
        cell.save(outdir / f"frame_{i:02d}.png")

# 另外复制整图供 background-position 方案用
import shutil
shutil.copy(src, ROOT / "video" / "assets" / "slime_sheet.png")
print("done, frames ->", outdir)
