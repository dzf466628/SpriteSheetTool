"""回归 KeyFrame 抠图模式在 SpriteSheetTool 中的统一适配。"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from spritesheet import keying_modes  # noqa: E402


def make_image() -> Image.Image:
    image = Image.new("RGB", (128, 128), (8, 220, 10))
    ImageDraw.Draw(image).ellipse((35, 20, 95, 110), fill=(220, 70, 40))
    return image


def check_mode(mode_id: str) -> None:
    source = make_image()
    result, stats = keying_modes.key_image(
        source,
        (8, 220, 10),
        mode_id=mode_id,
        tolerance=28,
        soft=40,
        edge_only=True,
        shrink=0,
        despill=30,
        brightness_tolerance=35,
    )
    assert result.mode == "RGBA"
    assert stats["mode_id"] == mode_id
    assert result.getpixel((0, 0))[3] == 0
    assert result.getpixel((64, 64))[3] == 255
    assert result.getpixel((64, 64))[:3] == source.getpixel((64, 64))


def main() -> int:
    for profile in keying_modes.MODE_PROFILES:
        check_mode(profile.id)
        print(f"PASS {profile.id}")
    print("KEYFRAME MODES OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
