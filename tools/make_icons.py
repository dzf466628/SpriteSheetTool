"""从一张源 PNG 生成 Windows 标准方形多尺寸 .ico。

为什么需要它：`SSTool.ico` 原来的尺寸是 12x16 / 17x24 / 23x32 / 35x48 / 46x64 /
93x128 / 185x256 —— 全是**非方形**（当初按高度缩放一张竖幅源图得到）。Windows 选图标
是按标准方形尺寸（16/24/32/48/64/128/256）匹配的，没有匹配项时图标会被画得比同槽位小，
表现为「工程文件图标比别的图标小一圈」。

本工具的做法：把源图按**长边**适配到方形画布并居中（不拉伸、不裁切），再输出全部标准
方形尺寸。源图长宽比不是 1:1 时，方形两侧会是透明 —— 若想让它铺满，见 --tile。

Usage:
    python tools/make_icons.py <源图.png> <输出.ico> [--tile 颜色] [--inset 0.86]

    --tile 颜色   在图标底层铺一个该颜色的圆角方块（图标铺满槽位，像常规 App 图标）
    --inset       tile 模式下内容占方块的比例（默认 0.86）
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

CANONICAL = (16, 24, 32, 48, 64, 128, 256)
MASTER = 256


def build_master(src: Image.Image, tile: str | None, inset: float) -> Image.Image:
    """把源图按长边适配到 MASTER×MASTER 的方形画布并居中。"""
    src = src.convert("RGBA")
    box = MASTER if tile is None else int(MASTER * inset)
    long_side = max(src.width, src.height)
    scale = box / long_side
    fitted = src.resize(
        (max(1, round(src.width * scale)), max(1, round(src.height * scale))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    pos = ((MASTER - fitted.width) // 2, (MASTER - fitted.height) // 2)
    if tile is not None:
        # 圆角品牌底：让图标铺满槽位（视觉上与其他图标同大）
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle((0, 0, MASTER - 1, MASTER - 1),
                               radius=int(MASTER * 0.18), fill=tile)
        # 内容再向内收一点，避免贴边
        inset_pad = (MASTER - fitted.width) // 2, (MASTER - fitted.height) // 2
        pos = inset_pad
    canvas.alpha_composite(fitted, dest=pos)
    return canvas


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        print(__doc__)
        return 2
    src_path, out_path = Path(args[0]), Path(args[1])
    tile = None
    inset = 0.86
    for i, a in enumerate(sys.argv):
        if a == "--tile" and i + 1 < len(sys.argv):
            tile = sys.argv[i + 1]
        if a == "--inset" and i + 1 < len(sys.argv):
            inset = float(sys.argv[i + 1])

    src = Image.open(src_path)
    master = build_master(src, tile, inset)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    master.save(out_path, format="ICO", sizes=[(s, s) for s in CANONICAL])
    print(f"已生成 {out_path}")
    print(f"  源图 {src.width}x{src.height}"
          + (f"  -> 圆角底 {tile}，内容占 {inset:.0%}" if tile else "  -> 透明底，按长边适配"))
    print(f"  标准方形尺寸: {', '.join(str(s) for s in CANONICAL)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
