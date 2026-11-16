"""Shared light-background measurements for the legacy and v3 classifiers."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from PIL import Image


def edge_background(image: Image.Image) -> tuple[tuple[int, int, int], bool]:
    rgb = image.convert("RGB")
    step = max(1, max(rgb.size) // 128)
    edge = [rgb.getpixel((x, y)) for x in range(0, rgb.width, step) for y in (0, rgb.height - 1)]
    edge.extend(rgb.getpixel((x, y)) for y in range(0, rgb.height, step) for x in (0, rgb.width - 1))
    background = tuple(int(median(channel)) for channel in zip(*edge))
    uniform = sum(
        max(abs(channel - base) for channel, base in zip(pixel, background)) <= 24
        for pixel in edge
    ) / max(1, len(edge)) >= 0.88
    return background, uniform


@dataclass(frozen=True)
class ForegroundProfile:
    """One 128px scan: how big the subject is and which edges it runs off."""

    width_ratio: float
    height_ratio: float
    touches_edge: bool
    cut_sides: dict[str, bool]
    box_aspect: float


def foreground_profile(image: Image.Image, background: tuple[int, int, int]) -> ForegroundProfile:
    rgb = image.convert("RGB")
    scale = min(1.0, 128 / max(rgb.size))
    preview = rgb.resize((max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale))), Image.Resampling.BILINEAR)
    width, height = preview.size
    pixels = list(preview.get_flattened_data())
    foreground = [
        index for index, pixel in enumerate(pixels)
        if max(abs(channel - base) for channel, base in zip(pixel, background)) > 32
    ]
    if not foreground:
        return ForegroundProfile(0.0, 0.0, False, {"left": False, "right": False, "top": False, "bottom": False}, 0.0)
    left = min(index % width for index in foreground)
    top = min(index // width for index in foreground)
    right = max(index % width for index in foreground) + 1
    bottom = max(index // width for index in foreground) + 1
    width_ratio, height_ratio = (right - left) / width, (bottom - top) / height
    margin_x, margin_y = max(1, round(width * 0.015)), max(1, round(height * 0.015))
    cut_sides = {
        "left": left <= margin_x,
        "right": right >= width - margin_x,
        "top": top <= margin_y,
        "bottom": bottom >= height - margin_y,
    }
    # The preview keeps the source aspect, so this is the subject's own aspect.
    box_aspect = (right - left) / max(1, bottom - top)
    return ForegroundProfile(width_ratio, height_ratio, any(cut_sides.values()), cut_sides, box_aspect)


def foreground_metrics(
    image: Image.Image, background: tuple[int, int, int]
) -> tuple[float, float, bool, dict[str, bool]]:
    profile = foreground_profile(image, background)
    return profile.width_ratio, profile.height_ratio, profile.touches_edge, profile.cut_sides
