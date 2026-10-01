# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fast, deterministic keying for cartoons on a solid-colour background.

This engine is deliberately different from portrait matting models: it treats the
image as three confidence regions (background, narrow antialiased edge, foreground)
and only changes alpha.  RGB bytes are copied from the source image unchanged.

The public entry points are :func:`estimate_background_profile`,
:func:`compute_alpha`, and :func:`key_image`.  A ``BackgroundProfile`` can be
estimated once from several sampled video frames and reused for every frame.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np
from PIL import Image

from . import keying

RGBColor = tuple[int, int, int]

STRENGTHS = ("detail", "balanced", "clean")
EDGE_TYPES = ("auto", "hard", "antialias", "soft")
DEFAULT_BRIGHTNESS_TOLERANCE = 35


@dataclass(frozen=True)
class BackgroundProfile:
    """Reusable description of a solid-colour background.

    ``colors`` contains up to ``max_colors`` representative samples.  ``noise``
    is the robust residual around those samples, ``tolerance`` is the suggested
    certain-background threshold, and ``confidence`` is the fraction of border
    samples explained by the colour cluster (0..1).
    """

    colors: tuple[RGBColor, ...]
    noise: float
    tolerance: int
    confidence: float
    sample_count: int = 0


def _as_rgba_array(image: Image.Image) -> np.ndarray:
    if not isinstance(image, Image.Image):
        raise TypeError("image must be a PIL.Image.Image")
    return np.asarray(image.convert("RGBA"), dtype=np.uint8)


def _normalize_colors(bg_colors) -> tuple[RGBColor, ...]:
    if bg_colors is None:
        return ()
    if isinstance(bg_colors, np.ndarray):
        bg_colors = bg_colors.tolist()
    if len(bg_colors) == 0:
        return ()
    if isinstance(bg_colors[0], (int, np.integer)):
        bg_colors = [bg_colors]

    normalized = []
    for color in bg_colors:
        if len(color) < 3:
            raise ValueError("each background colour needs three RGB channels")
        normalized.append(tuple(max(0, min(255, int(v))) for v in color[:3]))
    return tuple(dict.fromkeys(normalized))


def _iter_images(image_or_images) -> list[Image.Image]:
    if isinstance(image_or_images, Image.Image):
        return [image_or_images]
    if isinstance(image_or_images, Iterable):
        images = list(image_or_images)
        if images and all(isinstance(image, Image.Image) for image in images):
            return images
    raise TypeError("image_or_images must be a PIL image or a non-empty iterable of PIL images")


def _border_pixels(image: Image.Image, border: int, max_samples: int = 80_000) -> np.ndarray:
    rgba = _as_rgba_array(image)
    height, width = rgba.shape[:2]
    thickness = max(1, min(int(border), height, width))
    mask = np.zeros((height, width), dtype=bool)
    mask[:thickness, :] = True
    mask[-thickness:, :] = True
    mask[:, :thickness] = True
    mask[:, -thickness:] = True
    pixels = rgba[mask]
    pixels = pixels[pixels[:, 3] >= 128, :3]
    if len(pixels) > max_samples:
        # Deterministic even sampling avoids making preview/export profiles differ.
        indices = np.linspace(0, len(pixels) - 1, max_samples, dtype=np.int64)
        pixels = pixels[indices]
    return pixels


def _bucket_means(pixels: np.ndarray, quantum: int = 8):
    quantized = pixels.astype(np.int16) // quantum
    codes = (quantized[:, 0] * 1024 + quantized[:, 1] * 32 + quantized[:, 2]).astype(np.int32)
    unique, inverse, counts = np.unique(codes, return_inverse=True, return_counts=True)
    order = np.argsort(counts)[::-1]
    means = []
    for bucket_index in order[: min(96, len(order))]:
        members = pixels[inverse == bucket_index]
        mean = tuple(int(round(float(v))) for v in members.mean(axis=0))
        means.append((mean, int(counts[bucket_index])))
    return means


def _same_background_family(color: RGBColor, primary: RGBColor) -> bool:
    a = np.asarray(color, dtype=np.float32)
    b = np.asarray(primary, dtype=np.float32)
    direct = float(np.max(np.abs(a - b)))
    if direct <= 32.0:
        return True

    # A shaded saturated screen may change brightness while preserving chroma.
    a_range = float(a.max() - a.min())
    b_range = float(b.max() - b.min())
    if a_range < 48.0 or b_range < 48.0 or int(a.argmax()) != int(b.argmax()):
        return False
    a_chroma = a / max(1.0, float(a.sum()))
    b_chroma = b / max(1.0, float(b.sum()))
    return float(np.max(np.abs(a_chroma - b_chroma))) <= 0.055


def _screen_channel(color: RGBColor) -> int | None:
    """Return the dominant channel for a usable coloured screen.

    The cartoon workflow mainly sees green and blue screens whose brightness can
    vary a lot because of gradients, shadows, compression, or generated artwork.
    Neutral backgrounds keep using ordinary RGB distance.
    """
    values = np.asarray(color, dtype=np.int16)
    order = np.argsort(values)
    dominant = int(order[-1])
    second = int(order[-2])
    if int(values[dominant]) < 48:
        return None
    if int(values[dominant] - values[second]) < 12:
        return None
    if int(values.max() - values.min()) < 24:
        return None
    return dominant


def _screen_chroma_compatible(rgb: np.ndarray, color: RGBColor) -> np.ndarray:
    """Return pixels that still have the selected screen's dominant channel.

    Muted green-screen captures can be numerically close to grey or grey-purple
    artwork in ordinary RGB distance.  Brightness may change freely, but a real
    green/blue screen pixel must retain at least a small dominance in the screen
    channel.  This keeps neutral cartoon parts from being treated as dark screen
    shading.
    """
    channel = _screen_channel(color)
    if channel is None:
        return np.ones(rgb.shape[:-1], dtype=bool)

    values = rgb.astype(np.int16, copy=False)
    reference = np.asarray(color, dtype=np.int16)
    other = [index for index in range(3) if index != channel]
    dominant = values[..., channel].astype(np.int32)
    strongest_other = np.maximum(
        values[..., other[0]].astype(np.int32),
        values[..., other[1]].astype(np.int32),
    )
    reference_excess = int(reference[channel]) - max(
        int(reference[other[0]]), int(reference[other[1]])
    )
    minimum_excess = max(2, int(round(reference_excess * 0.08)))
    return dominant - strongest_other >= minimum_excess


def screen_family_mask(rgb: np.ndarray, bg_colors) -> np.ndarray:
    """Return pixels compatible with any coloured-screen sample.

    Neutral backgrounds have no meaningful dominant channel, so their mask stays
    unrestricted.  The helper is also used by model fusion to stop a rough colour
    key from erasing obvious non-screen foreground recovered by an AI matte.
    """
    colors = _normalize_colors(bg_colors)
    coloured = [color for color in colors if _screen_channel(color) is not None]
    if not coloured:
        return np.ones(rgb.shape[:-1], dtype=bool)
    compatible = np.zeros(rgb.shape[:-1], dtype=bool)
    for color in coloured:
        compatible |= _screen_chroma_compatible(rgb, color)
    return compatible


def _distance_to_color(
    rgb: np.ndarray,
    color: RGBColor,
    brightness_tolerance: int = DEFAULT_BRIGHTNESS_TOLERANCE,
) -> np.ndarray:
    """Distance to one background colour, tolerant of screen brightness.

    For a coloured screen, compare the non-dominant channels with values
    predicted from the dominant channel.  A darker patch of the same green/blue
    therefore stays close, while neutral hair, clothes, and outlines remain far
    away.  Very dark near-neutral pixels use direct RGB distance so black line art
    is not mistaken for a screen shadow.
    """
    values = rgb.astype(np.int16, copy=False)
    reference = np.asarray(color, dtype=np.int16)
    direct = np.max(np.abs(values - reference), axis=-1)
    channel = _screen_channel(color)
    if channel is None:
        return direct

    dominant = values[..., channel].astype(np.int32)
    reference_dominant = max(1, int(reference[channel]))
    residual = np.zeros(values.shape[:-1], dtype=np.int32)
    other_values = []
    for index in range(3):
        if index == channel:
            continue
        predicted = (dominant * int(reference[index]) + reference_dominant // 2) // reference_dominant
        delta = np.abs(values[..., index].astype(np.int32) - predicted)
        np.maximum(residual, delta, out=residual)
        other_values.append(values[..., index].astype(np.int32))

    strongest_other = np.maximum(other_values[0], other_values[1])
    dominance = dominant - strongest_other
    dark_neutral = (dominant < 80) & (dominance * 16 < dominant)
    # This is a literal dominant-channel difference (0..255), not an abstract
    # strength percentage.  Screen hue/chroma and brightness must both match.
    maximum_shift = int(np.clip(brightness_tolerance, 0, 255))
    brightness_shift = np.abs(dominant - reference_dominant)
    screen_like = (
        _screen_chroma_compatible(values, color)
        & (brightness_shift <= maximum_shift)
    )
    screen_distance = np.where(dark_neutral | ~screen_like, 255, residual)
    direct_distance = np.where(screen_like, direct, 255)
    return np.minimum(direct_distance.astype(np.int32), screen_distance).astype(np.int16)


def _nearest_distance(
    rgb: np.ndarray,
    colors: Sequence[RGBColor],
    brightness_tolerance: int = DEFAULT_BRIGHTNESS_TOLERANCE,
) -> np.ndarray:
    """Distance to the nearest representative background colour."""
    result = np.full(rgb.shape[:-1], 255, dtype=np.int16)
    for color in colors:
        np.minimum(
            result,
            _distance_to_color(rgb, color, brightness_tolerance),
            out=result,
        )
    return result


def estimate_background_profile(
    image_or_images,
    *,
    border: int = 4,
    max_colors: int = 4,
) -> BackgroundProfile:
    """Estimate a compact solid-background colour cluster from image borders.

    ``image_or_images`` may be one PIL image or sampled video frames.  Foreground
    objects touching a border are resisted by selecting the dominant quantized
    colour family rather than averaging all border pixels.
    """
    if max_colors < 1:
        raise ValueError("max_colors must be at least one")

    samples = [_border_pixels(image, border) for image in _iter_images(image_or_images)]
    samples = [sample for sample in samples if len(sample)]
    if not samples:
        return BackgroundProfile(((0, 0, 0),), 0.0, 4, 0.0, 0)
    pixels = np.concatenate(samples, axis=0)
    if len(pixels) > 200_000:
        indices = np.linspace(0, len(pixels) - 1, 200_000, dtype=np.int64)
        pixels = pixels[indices]

    buckets = _bucket_means(pixels)
    primary, primary_count = buckets[0]
    selected: list[RGBColor] = []
    screen_channel = _screen_channel(primary)
    if screen_channel is not None:
        # Strict brightness matching needs real samples across the screen's
        # observed brightness range.  Split same-hue border pixels into ordered
        # brightness bands instead of pretending one green/blue sample covers
        # every shadow and highlight.
        compatible = _screen_chroma_compatible(pixels, primary)
        pixel_values = pixels.astype(np.float32)
        pixel_chroma = pixel_values / np.maximum(
            pixel_values.sum(axis=1, keepdims=True), 1.0
        )
        primary_values = np.asarray(primary, dtype=np.float32)
        primary_chroma = primary_values / max(float(primary_values.sum()), 1.0)
        # A tiny green lead is not enough: grey clothing/tails touching the border
        # can have G exceed R/B by a few values.  Require the RGB proportions to
        # remain close to the dominant screen cluster before using a pixel as an
        # automatic brightness sample.
        chroma_match = np.max(
            np.abs(pixel_chroma - primary_chroma[None, :]), axis=1
        ) <= 0.055
        family = pixels[compatible & chroma_match]
        if len(family):
            levels = family[:, screen_channel].astype(np.int16)
            targets = np.linspace(
                int(levels.min()), int(levels.max()), min(max_colors, len(family))
            )
            sample_size = min(len(family), max(32, len(family) // 100))
            for target in targets:
                nearest = np.argpartition(
                    np.abs(levels.astype(np.float32) - float(target)),
                    sample_size - 1,
                )[:sample_size]
                band = family[nearest]
                color = tuple(
                    int(round(float(value))) for value in np.median(band, axis=0)
                )
                if all(
                    max(abs(color[i] - old[i]) for i in range(3)) >= 5
                    for old in selected
                ):
                    selected.append(color)
    else:
        minimum_count = max(2, int(primary_count * 0.015))
        for color, count in buckets:
            if count < minimum_count or not _same_background_family(color, primary):
                continue
            if all(
                max(abs(color[i] - old[i]) for i in range(3)) >= 5
                for old in selected
            ):
                selected.append(color)
            if len(selected) >= max_colors:
                break
    if not selected:
        selected = [primary]

    distances = _nearest_distance(pixels, selected)
    # Ignore border foreground while measuring screen noise.  The gate stays tight
    # enough that a touching cartoon character cannot inflate the threshold.
    plausible = distances <= 32
    inlier_distances = distances[plausible]
    if len(inlier_distances) == 0:
        inlier_distances = distances[np.argsort(distances)[: max(1, len(distances) // 2)]]
    noise = float(np.percentile(inlier_distances, 95))
    tolerance = int(np.clip(np.ceil(noise) + 3, 3, 18))
    explained = float(np.mean(distances <= max(12, tolerance + 5)))
    compactness = 1.0 - min(noise / 48.0, 0.65)
    confidence = float(np.clip(explained * compactness, 0.0, 1.0))
    return BackgroundProfile(tuple(selected), noise, tolerance, confidence, int(len(pixels)))


def _profile_for_colors(image: Image.Image, colors: tuple[RGBColor, ...]) -> BackgroundProfile:
    pixels = _border_pixels(image, border=4)
    if len(pixels) == 0:
        return BackgroundProfile(colors, 0.0, 4, 0.0, 0)
    distances = _nearest_distance(pixels, colors)
    plausible = distances <= 40
    inliers = distances[plausible]
    if len(inliers) == 0:
        noise = 0.0
        explained = 0.0
    else:
        noise = float(np.percentile(inliers, 90))
        explained = float(np.mean(distances <= max(12, int(np.ceil(noise)) + 8)))
    tolerance = int(np.clip(np.ceil(noise) + 3, 3, 20))
    confidence = float(np.clip(explained * (1.0 - min(noise / 56.0, 0.6)), 0.0, 1.0))
    return BackgroundProfile(colors, noise, tolerance, confidence, int(len(pixels)))


def _connected_to_edge(candidate: np.ndarray) -> np.ndarray:
    """Return the 8-connected part of ``candidate`` touching any image edge."""
    height, width = candidate.shape
    if not candidate.any():
        return np.zeros_like(candidate, dtype=bool)
    # Reuse the optimized run-length/union-find implementation shared by the
    # traditional keyer. Candidate pixels become zero-distance pixels, while
    # the rest are barriers for the connected-region calculation.
    diff = Image.fromarray(np.where(candidate, 0, 255).astype(np.uint8), mode="L")
    connected, _ = keying.edge_connected_mask(diff, 0, 0)
    return np.asarray(connected, dtype=np.uint8) > 0


def _thresholds(profile: BackgroundProfile, strength: str, edge_type: str) -> tuple[int, int, str]:
    if strength not in STRENGTHS:
        raise ValueError(f"strength must be one of {STRENGTHS}")
    if edge_type not in EDGE_TYPES:
        raise ValueError(f"edge_type must be one of {EDGE_TYPES}")

    offsets = {"detail": -2, "balanced": 0, "clean": 4}
    background = int(np.clip(profile.tolerance + offsets[strength], 1, 32))
    resolved_edge = edge_type
    if edge_type == "auto":
        resolved_edge = "antialias"
        width = int(np.clip(round(8 + profile.noise * 0.5), 8, 14))
    else:
        width = {"hard": 0, "antialias": 10, "soft": 20}[edge_type]
    width = int(round(width * {"detail": 0.8, "balanced": 1.0, "clean": 1.1}[strength]))
    foreground = min(255, background + max(0, width))
    return background, foreground, resolved_edge


def compute_alpha(
    image: Image.Image,
    bg_colors=None,
    *,
    profile: BackgroundProfile | None = None,
    strength: str = "balanced",
    edge_type: str = "auto",
    edge_only: bool = True,
    brightness_tolerance: int = DEFAULT_BRIGHTNESS_TOLERANCE,
) -> tuple[Image.Image, dict]:
    """Compute an alpha mask for a solid-colour cartoon image.

    Certain background becomes 0, certain foreground becomes 255, and only the
    narrow interval between both thresholds receives a smooth alpha ramp.  With
    ``edge_only=True`` (the default), background-like colours enclosed by the
    character remain opaque.
    """
    rgba = _as_rgba_array(image)
    colors = _normalize_colors(bg_colors)
    if profile is not None and not isinstance(profile, BackgroundProfile):
        raise TypeError("profile must be a BackgroundProfile")
    if profile is not None and colors:
        raise ValueError("pass either bg_colors or profile, not both")
    if profile is None:
        profile = _profile_for_colors(image, colors) if colors else estimate_background_profile(image)
    if not profile.colors:
        raise ValueError("background profile has no colours")

    brightness_tolerance = int(np.clip(brightness_tolerance, 0, 255))
    distance = _nearest_distance(
        rgba[:, :, :3], profile.colors, brightness_tolerance
    )
    background, foreground, resolved_edge = _thresholds(profile, strength, edge_type)

    if foreground <= background:
        calculated = np.where(distance <= background, 0, 255).astype(np.uint8)
        candidate = distance <= background
    else:
        x = np.clip(
            (distance.astype(np.float32) - background) / float(foreground - background),
            0.0,
            1.0,
        )
        smooth = x * x * (3.0 - 2.0 * x)
        calculated = np.rint(smooth * 255.0).astype(np.uint8)
        candidate = distance < foreground

    if edge_only:
        connected = _connected_to_edge(candidate)
        calculated = np.where(connected, calculated, 255).astype(np.uint8)
    else:
        connected = candidate

    # Existing transparency is authoritative; keying never creates opacity.
    final = np.minimum(calculated, rgba[:, :, 3])
    changed = np.count_nonzero(final != rgba[:, :, 3])
    stats = {
        "bg_colors": profile.colors,
        "background_threshold": background,
        "foreground_threshold": foreground,
        "noise": profile.noise,
        "confidence": profile.confidence,
        "strength": strength,
        "edge_type": edge_type,
        "resolved_edge_type": resolved_edge,
        "edge_only": bool(edge_only),
        "brightness_tolerance": brightness_tolerance,
        "changed_pct": changed * 100.0 / final.size if final.size else 0.0,
        "transparent_pct": np.count_nonzero(final == 0) * 100.0 / final.size if final.size else 0.0,
        # Keep stats safe for JSON metadata written by the video pipeline.
        "profile": {
            "colors": profile.colors,
            "noise": profile.noise,
            "tolerance": profile.tolerance,
            "confidence": profile.confidence,
            "sample_count": profile.sample_count,
        },
    }
    return Image.fromarray(final), stats


def apply_original_color(image: Image.Image, alpha: Image.Image) -> Image.Image:
    """Copy source RGB exactly and replace only alpha."""
    rgba = image.convert("RGBA")
    if alpha.mode != "L":
        alpha = alpha.convert("L")
    if alpha.size != rgba.size:
        raise ValueError("alpha size must match image size")
    output = rgba.copy()
    output.putalpha(alpha)
    return output


def key_image(
    image: Image.Image,
    bg_colors=None,
    *,
    profile: BackgroundProfile | None = None,
    strength: str = "balanced",
    edge_type: str = "auto",
    edge_only: bool = True,
    brightness_tolerance: int = DEFAULT_BRIGHTNESS_TOLERANCE,
) -> tuple[Image.Image, dict]:
    """Key a cartoon image while preserving every source RGB value."""
    alpha, stats = compute_alpha(
        image,
        bg_colors,
        profile=profile,
        strength=strength,
        edge_type=edge_type,
        edge_only=edge_only,
        brightness_tolerance=brightness_tolerance,
    )
    return apply_original_color(image, alpha), stats


__all__ = [
    "BackgroundProfile",
    "DEFAULT_BRIGHTNESS_TOLERANCE",
    "EDGE_TYPES",
    "STRENGTHS",
    "apply_original_color",
    "compute_alpha",
    "estimate_background_profile",
    "key_image",
    "screen_family_mask",
]
