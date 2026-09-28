# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""从一张源 PNG 生成 Windows 多尺寸 .ico。

**每一档都必须是方形画布**，把源图按长边适配居中（不拉伸、不裁切），输出全部标准尺寸
16/24/32/48/64/128/256。源图不是 1:1 时，方形两侧/上下会是透明 —— 这不是浪费，
而是**唯一正确的做法**，原因见下。

⚠️ **不要做「原比例帧」（非方形帧）**。曾经试过：源图 185×256（竖幅），按高度分档输出
`12x16 / 17x24 / 23x32 …`，想着"文件里不存方框、内容占满整帧"。结果**资源管理器把图标
显示成被压扁的**：Explorer 的图标视图走的是**系统图标列表**（SHGetFileInfo /
SHImageList），列表项是**方形位图**，非方形帧会被**拉伸填满方形槽位** —— 实测 32px 档
里 23×32 的帧被拉成 32×32，横向拉宽 1.39 倍，人物就变扁了。

实测数据（`tools/verify_icon_imagelist.py` 走真实 shell 路径，可复现）：

| 图标文件 | 16px 档粉色框 | 32px 档粉色框 | 结论 |
| --- | --- | --- | --- |
| 方形帧（正确） | 12×16（0.750） | 24×32（0.750） | 比例正确 |
| 非方形帧（错误） | 16×16（**1.000**） | 32×32（**1.000**） | 被拉伸压扁 |

注意 `IShellItemImageFactory`（取缩略图用的接口）**会**保持比例，所以拿它验证会得出
「没变化」的错误结论 —— 验证图标必须走 `SHGetFileInfo`。同理，方形画布并非"图更小"：
图按高度铺满整档（256 档里就是 256 高），只是宽度按原比例（72%），这是不拉伸的前提下的
最大尺寸。

为什么每档都要给：Windows 按**槽位**尺寸挑最接近的一档，只有一档时其它尺寸全靠位图拉伸，
越小越糊。所以每档都按原图重新缩放（LANCZOS），而不是从大图逐级缩。

Usage:
    python tools/make_icons.py <源图> <输出.ico> [--tile 颜色] [--inset 0.86]
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
    """按高度分档、按原图比例算宽度，逐档独立缩放。

    ⚠️ **不要用它生成 .ico**：非方形帧会被资源管理器拉伸填满方形槽位（见模块 docstring
    的实测表）。保留这个函数只是为了导出「非方形素材」的等比缩放序列，例如做对比图。
    """
    art = crop_to_content(src)
    frames = []
    for height in CANONICAL:
        width = max(1, round(art.width * height / art.height))
        frames.append(art.resize((width, height), Image.Resampling.LANCZOS))
    return frames


def save_square(master: Image.Image, out_path: Path) -> None:
    master.save(out_path, format="ICO", sizes=[(s, s) for s in CANONICAL])


def _resize_sequence(src: Image.Image, out_dir: Path, prefix: str) -> None:
    """把等比缩放序列导出成 PNG（非方形，仅用于对比/预览，不要拿去当图标）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    art = crop_to_content(src)
    for frame in natural_frames(src):
        path = out_dir / f"{prefix}_{frame.height}.png"
        frame.save(path, format="PNG")
        print(f"    {path.name}  {frame.width}x{frame.height}")
    print(f"  注意：这些是非方形图，不要直接当 .ico 用（会被资源管理器拉伸）。原图 {art.width}x{art.height}")


def main() -> int:
    argv = sys.argv[1:]
    positional = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    extract = "--extract" in flags
    frames_out = "--frames" in flags
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

    if frames_out:
        print(f"导出等比缩放序列到 {out_path}/（非方形，仅供对比）")
        _resize_sequence(src, out_path, src_path.stem)
        return 0

    master = build_square_master(src, tile, inset)
    save_square(master, out_path)
    print(f"已生成 {out_path}")
    print(f"  源图 {src.width}x{src.height}"
          + (f"  -> 圆角底 {tile}，内容占 {inset:.0%}" if tile else "  -> 透明底，按长边适配"))
    print(f"  标准方形尺寸: {', '.join(str(s) for s in CANONICAL)}")
    if max(src.width, src.height) != min(src.width, src.height):
        print("  提醒：源图不是 1:1，画布两侧/上下会透明 —— 这是正确的，"
              "不要改成非方形帧（会被资源管理器拉伸压扁）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
