"""从一张源 PNG 生成 Windows 多尺寸 .ico。

两种模式：

**方形模式（默认）** 把源图按长边适配到方形画布并居中（不拉伸、不裁切），输出全部标准
方形尺寸 16/24/32/48/64/128/256。源图长宽比不是 1:1 时，方形两侧会是透明 —— 这是
`SpriteSheetTool.ico`（软件本体图标）用的模式。

**原比例模式（--natural）** 不套方形画布：先把源图按 alpha 裁掉透明边，再按**高度**
缩放到 16/24/32/48/64/128/256，宽度按原图比例算，于是每一档都是原图的等比缩小，
画布上不会多出透明边框。`SSTool.ico`（工程文件图标）用这一种：它的原图是竖幅的
（185×256），套进方形画布两侧会空出一大块，看起来就像「一个方块里飘着一张画」。
判定依据（tools/inspect_icon.py 可复现）：方形模式每档实心内容只占画布宽 72%，
原比例模式每档都是 100%。

为什么不能只给一档尺寸：Windows 按**槽位**尺寸挑最接近的一档，只有一档时其它尺寸全靠
位图拉伸，越小越糊。所以每档都按原图重新缩放（LANCZOS），而不是从大图逐级缩。

Usage:
    python tools/make_icons.py <源图> <输出.ico> [--tile 颜色] [--inset 0.86] [--natural]
    python tools/make_icons.py <现有.ico> <输出.png> --extract
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

CANONICAL = (16, 24, 32, 48, 64, 128, 256)
MASTER = 256


def load_master(path: Path) -> Image.Image:
    """读源图；.ico 取最大的那一帧。"""
    image = Image.open(path)
    if getattr(image, "format", "") == "ICO":
        sizes = image.info.get("sizes") or [(image.width, image.height)]
        image.size = max(sizes, key=lambda s: s[0] * s[1])
    return image


def crop_to_content(image: Image.Image) -> Image.Image:
    """按 alpha 裁掉四周透明边，得到「原图」本身。"""
    rgba = image.convert("RGBA")
    box = rgba.getchannel("A").getbbox()
    return rgba.crop(box) if box else rgba


def build_square_master(src: Image.Image, tile: str | None, inset: float) -> Image.Image:
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
        pos = ((MASTER - fitted.width) // 2, (MASTER - fitted.height) // 2)
    canvas.alpha_composite(fitted, dest=pos)
    return canvas


def natural_frames(src: Image.Image) -> list[Image.Image]:
    """按高度分档、按原图比例算宽度，逐档独立缩放。"""
    art = crop_to_content(src)
    frames = []
    for height in CANONICAL:
        width = max(1, round(art.width * height / art.height))
        frames.append(art.resize((width, height), Image.Resampling.LANCZOS))
    return frames


def save_square(master: Image.Image, out_path: Path) -> None:
    master.save(out_path, format="ICO", sizes=[(s, s) for s in CANONICAL])


def save_natural(frames: list[Image.Image], out_path: Path) -> None:
    # 逐帧显式缩放后交给 Pillow；它发现「已有该尺寸的图」就直接用，不会再缩一次。
    biggest = max(frames, key=lambda f: (f.width * f.height))
    biggest.save(out_path, format="ICO", sizes=[f.size for f in frames], append_images=frames)


def main() -> int:
    argv = sys.argv[1:]
    positional = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    extract = "--extract" in flags
    natural = "--natural" in flags
    if len(positional) < 2:
        print(__doc__)
        return 2
    src_path, out_path = Path(positional[0]), Path(positional[1])
    tile = None
    inset = 0.86
    for i, arg in enumerate(argv):
        if arg == "--tile" and i + 1 < len(argv):
            tile = argv[i + 1]
        if arg == "--inset" and i + 1 < len(argv):
            inset = float(argv[i + 1])

    src = load_master(src_path)

    if extract:
        art = crop_to_content(src)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        art.save(out_path, format="PNG")
        print(f"已取出原图 {out_path}")
        print(f"  {src_path.name} 最大帧 {src.width}x{src.height} -> 裁掉透明边 {art.width}x{art.height}")
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if natural:
        frames = natural_frames(src)
        save_natural(frames, out_path)
        art = crop_to_content(src)
        print(f"已生成 {out_path}（原比例模式）")
        print(f"  原图 {art.width}x{art.height}  -> 按高度分档，宽度按比例")
        for frame in frames:
            print(f"    {frame.width}x{frame.height}")
        return 0

    master = build_square_master(src, tile, inset)
    save_square(master, out_path)
    print(f"已生成 {out_path}")
    print(f"  源图 {src.width}x{src.height}"
          + (f"  -> 圆角底 {tile}，内容占 {inset:.0%}" if tile else "  -> 透明底，按长边适配"))
    print(f"  标准方形尺寸: {', '.join(str(s) for s in CANONICAL)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
