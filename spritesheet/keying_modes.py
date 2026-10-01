"""统一的抠图模式适配层。

传统色键和卡通 profile 都在本地运行，不需要外部模型文件。
"""
from __future__ import annotations

from dataclasses import dataclass
from PIL import Image

from . import keying


@dataclass(frozen=True, slots=True)
class ModeProfile:
    id: str
    label: str
    kind: str
    description: str


MODE_PROFILES: tuple[ModeProfile, ...] = (
    ModeProfile(
        "traditional",
        "基础色键 · 手动调节",
        "traditional",
        "零新增运行时依赖，适合纯色/渐变精灵图。",
    ),
    ModeProfile(
        "cartoon",
        "卡通背景 · 自动 profile",
        "cartoon",
        "保护黑线和主体颜色，适合 AI 生成的卡通关键帧。",
    ),
)

_PROFILE_BY_ID = {profile.id: profile for profile in MODE_PROFILES}


def _first_color(bg_colors) -> tuple[int, int, int]:
    if not bg_colors:
        return 0, 0, 0
    if isinstance(bg_colors[0], int):
        return tuple(int(value) for value in bg_colors[:3])
    return tuple(int(value) for value in bg_colors[0][:3])


def get_profile(mode_id: str) -> ModeProfile:
    return _PROFILE_BY_ID.get(str(mode_id), _PROFILE_BY_ID["traditional"])


def key_image(
    image: Image.Image,
    bg_colors,
    *,
    mode_id: str = "traditional",
    cartoon_strength: str = "balanced",
    cartoon_edge_type: str = "auto",
    **params,
) -> tuple[Image.Image, dict]:
    """按模式抠图，返回 ``(RGBA 图, 统计)``。"""
    profile = get_profile(mode_id)
    if profile.kind == "traditional":
        result, stats = keying.key_image(image, bg_colors, **params)
        stats["mode_id"] = profile.id
        return result, stats

    if profile.kind == "cartoon":
        try:
            from . import cartoon_keying
        except ImportError as exc:
            raise RuntimeError("卡通模式需要 numpy，请安装 requirements.txt") from exc
        result, stats = cartoon_keying.key_image(
            image,
            bg_colors,
            strength=str(cartoon_strength),
            edge_type=str(cartoon_edge_type),
            edge_only=bool(params.get("edge_only", True)),
            brightness_tolerance=int(params.get("brightness_tolerance", 35)),
        )
        stats["mode_id"] = profile.id
        stats.setdefault("bg_color", _first_color(bg_colors))
        return result, stats

    raise ValueError(f"未知抠图模式：{profile.id}")


__all__ = [
    "MODE_PROFILES",
    "ModeProfile",
    "get_profile",
    "key_image",
]
