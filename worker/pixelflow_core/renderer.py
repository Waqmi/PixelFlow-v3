"""Small, typed image renderer used by the v3 worker.

The renderer deliberately keeps the proven PIL operations local to the v3
package.  It does not import the v2 application at runtime.  ``RenderRequest``
assets and layout fields are the normal path; ``metadata`` remains a narrow
compatibility seam for older callers.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from io import BytesIO
import json
from functools import lru_cache
from pathlib import Path
from statistics import median
import time
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageStat

from .contracts import CompositionPlan, RenderRequest, RenderResult
from .side_fill import extend_side


STANDARD_SIZES: dict[str, tuple[int, int]] = {
    "1440x1440": (1440, 1440),
    "1440x1920": (1440, 1920),
    "1125x1500": (1125, 1500),
}
TRANSPARENT_SIZES: dict[str, tuple[int, int]] = {
    "800x800": (800, 800),
    "1440x1440": (1440, 1440),
    "1440x1920": (1440, 1920),
    "1440x2160": (1440, 2160),
}


@dataclass(frozen=True)
class RenderConfig:
    """Compatibility configuration for callers that used the old renderer."""

    width: int
    height: int
    logo_state: str = ""
    add_border: bool = False
    source_type: str = "product"
    transparent: bool = False
    max_size_mb: float | None = None


@dataclass(frozen=True)
class LegacyRenderResult:
    output_path: str
    width: int
    height: int
    success: bool
    error: str = ""


@dataclass(frozen=True)
class SourceAnalysis:
    """Per-source measurements reused across all output variants."""

    edge_color: tuple[int, int, int] | None
    light_background: bool
    foreground_bbox: tuple[int, int, int, int] | None
    touches_edge: bool
    corner_colors: tuple[
        tuple[int, int, int],
        tuple[int, int, int],
        tuple[int, int, int],
        tuple[int, int, int],
    ] | None


def _size(value: str | tuple[int, int] | list[int]) -> tuple[str, tuple[int, int]]:
    if isinstance(value, str):
        try:
            width, height = (int(part) for part in value.lower().split("x", 1))
        except (TypeError, ValueError):
            raise ValueError(f"invalid output size: {value!r}") from None
        name = value
    elif isinstance(value, (tuple, list)) and len(value) == 2:
        width, height = int(value[0]), int(value[1])
        name = f"{width}x{height}"
    else:
        raise ValueError(f"invalid output size: {value!r}")
    if width <= 0 or height <= 0:
        raise ValueError(f"output size must be positive: {value!r}")
    return name, (width, height)


def _safe_stem(source: Path) -> str:
    stem = source.stem
    if source.suffix.lower() == ".png":
        stem = f"{stem}_png"
    return "".join("_" if char in '/\\:*?"<>|\x00\n\r\t' else char for char in stem).strip(" .") or "素材"


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _box(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (tuple, list)) or len(value) < 4:
        return None
    try:
        left, top, right, bottom = (float(item) for item in value[:4])
    except (TypeError, ValueError):
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _plan_box(plan: CompositionPlan | None) -> tuple[float, float, float, float] | None:
    if plan is None:
        return None
    boxes = [region.bbox for region in plan.protected_regions if region.required]
    if not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _edge_color(image: Image.Image) -> tuple[int, int, int]:
    rgb = image.convert("RGB")
    step = max(1, max(rgb.width, rgb.height) // 256)
    edge = max(1, min(rgb.width, rgb.height) // 100)
    pixels: list[tuple[int, int, int]] = []
    for x in range(0, rgb.width, step):
        pixels.append(rgb.getpixel((x, min(rgb.height - 1, edge // 2))))
        pixels.append(rgb.getpixel((x, max(0, rgb.height - edge // 2 - 1))))
    for y in range(0, rgb.height, step):
        pixels.append(rgb.getpixel((min(rgb.width - 1, edge // 2), y)))
        pixels.append(rgb.getpixel((max(0, rgb.width - edge // 2 - 1), y)))
    return tuple(int(median(channel)) for channel in zip(*pixels))


def _is_light_background(image: Image.Image) -> bool:
    color = _edge_color(image)
    return sum(color) / 3 >= 220 and max(color) - min(color) <= 24


def _crop_with_margin(image: Image.Image, bbox: tuple[int, int, int, int]) -> Image.Image:
    left, top, right, bottom = bbox
    margin_x = max(1, round((right - left) * 0.01))
    margin_y = max(1, round((bottom - top) * 0.01))
    return image.crop(
        (
            max(0, left - margin_x),
            max(0, top - margin_y),
            min(image.width, right + margin_x),
            min(image.height, bottom + margin_y),
        )
    )


def _side_filled_window(
    image: Image.Image,
    crop_box: tuple[float, float, float, float],
    target: tuple[int, int],
    side_fill: dict[str, float] | None,
) -> Image.Image:
    """Crop an anchor window that extends past the source sides, synthesizing the missing columns."""
    left, top, right, bottom = crop_box
    top_px, bottom_px = round(top * image.height), round(bottom * image.height)
    scale = target[1] / max(1, bottom_px - top_px)
    band = image.crop((0, top_px, image.width, bottom_px)).resize(
        (max(1, round(image.width * scale)), target[1]), Image.Resampling.LANCZOS
    )
    pixels = np.asarray(band, dtype=np.float32)
    # Without measured strips fall back to a conservative share of the width.
    strips = side_fill or {"strip_left": 0.03, "strip_right": 0.03}
    ext_left = round(max(0.0, -left) * image.width * scale)
    ext_right = round(max(0.0, right - 1.0) * image.width * scale)
    parts = []
    if ext_left:
        strip = max(8, min(round(strips.get("strip_left", 0.03) * band.width), band.width // 2))
        parts.append(extend_side(pixels, strip, ext_left, "left"))
    parts.append(pixels)
    if ext_right:
        strip = max(8, min(round(strips.get("strip_right", 0.03) * band.width), band.width // 2))
        parts.append(extend_side(pixels, strip, ext_right, "right"))
    full = np.concatenate(parts, axis=1)
    # The window's left edge in full-canvas columns; rounding may leave it 1px off.
    start = max(0, ext_left + round(left * image.width * scale))
    window = full[:, start:start + target[0]]
    if window.shape[1] < target[0]:
        window = np.pad(window, ((0, 0), (0, target[0] - window.shape[1]), (0, 0)), mode="edge")
    return Image.fromarray(window.astype(np.uint8))


def _resize_cover(
    image: Image.Image,
    target: tuple[int, int],
    focus: tuple[float, float] = (0.5, 0.5),
    protected: tuple[float, float, float, float] | None = None,
    extra_scale: float = 1.0,
) -> Image.Image:
    """Resize uniformly and crop, applying an optional composition scale."""
    width, height = target
    base_scale = max(width / image.width, height / image.height)
    scale = base_scale * max(0.01, _float(extra_scale, 1.0))
    if protected:
        left, top, right, bottom = protected
        safe_scale = min(
            width * 0.8 / max(1.0, right - left),
            height * 0.8 / max(1.0, bottom - top),
        )
        # A protected box must guide the crop, never turn cover into a
        # contain layout with empty edge fill when it is too large to fit.
        if safe_scale >= base_scale:
            scale = min(scale, safe_scale)
        else:
            scale = max(scale, base_scale)
    resized = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    if resized.width < width or resized.height < height:
        # A protected region can force a smaller uniform scale.  Fill the
        # remaining canvas from the edge colour instead of distorting pixels.
        canvas = Image.new("RGB", target, _edge_color(image))
        left = round((width - resized.width) * max(0.0, min(1.0, focus[0])))
        top = round((height - resized.height) * max(0.0, min(1.0, focus[1])))
        if protected:
            center_x = (protected[0] + protected[2]) * scale / 2
            center_y = (protected[1] + protected[3]) * scale / 2
            left = round(width / 2 - center_x)
            top = round(height / 2 - center_y)
            left = max(0, min(width - resized.width, left))
            top = max(0, min(height - resized.height, top))
        canvas.paste(resized, (left, top))
        return canvas
    max_left = max(0, resized.width - width)
    max_top = max(0, resized.height - height)
    px = max(0.0, min(1.0, focus[0]))
    py = max(0.0, min(1.0, focus[1]))
    left = round(max_left * px)
    top = round(max_top * py)
    if protected:
        p_left, p_top, p_right, p_bottom = protected
        left_min = round(p_right * scale - width * 0.8)
        left_max = round(p_left * scale - width * 0.2)
        top_min = round(p_bottom * scale - height * 0.8)
        top_max = round(p_top * scale - height * 0.2)
        if left_min <= left_max:
            left = max(left_min, min(left_max, left))
        if top_min <= top_max:
            top = max(top_min, min(top_max, top))
        left = max(0, min(max_left, left))
        top = max(0, min(max_top, top))
    return resized.crop((left, top, left + width, top + height))


def _resize_model_direct(
    image: Image.Image,
    target: tuple[int, int],
    crop_ratio: float,
    focus: tuple[float, float],
    protected: tuple[float, float, float, float] | None,
) -> Image.Image:
    """Match v2.6's square model crop when a target height ratio is known."""
    source = image.convert("RGB")
    ratio = max(0.20, min(1.0, _float(crop_ratio, 1.0)))
    scale = target[1] / max(1.0, source.height * ratio)
    if protected:
        p_left, p_top, p_right, p_bottom = (
            max(0.0, min(float(value), float(limit)))
            for value, limit in zip(
                protected,
                (source.width, source.height, source.width, source.height),
            )
        )
        protected_width = max(1.0, p_right - p_left)
        protected_height = max(1.0, p_bottom - p_top)
        scale = min(
            scale,
            target[0] * 0.8 / protected_width,
            target[1] * 0.8 / protected_height,
        )
        protected = (p_left, p_top, p_right, p_bottom)
    fitted = source.resize(
        (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
        Image.Resampling.LANCZOS,
    )
    if fitted.width < target[0] or fitted.height < target[1]:
        return _resize_cover(source, target, focus, protected)
    left = round(max(0, fitted.width - target[0]) * max(0.0, min(1.0, focus[0])))
    top = round(max(0, fitted.height - target[1]) * max(0.0, min(1.0, focus[1])))
    if protected:
        # v2.6 keeps the protected subject inside a 10% canvas margin.
        margin = 0.10
        p_left, p_top, p_right, p_bottom = protected
        left_min = max(0, round(p_right * scale - target[0] * (1.0 - margin)))
        left_max = min(fitted.width - target[0], round(p_left * scale - target[0] * margin))
        top_min = max(0, round(p_bottom * scale - target[1] * (1.0 - margin)))
        top_max = min(fitted.height - target[1], round(p_top * scale - target[1] * margin))
        if left_min <= left_max:
            left = max(left_min, min(left_max, left))
        if top_min <= top_max:
            top = max(top_min, min(top_max, top))
    return fitted.crop((left, top, left + target[0], top + target[1]))


def _resize_contain(image: Image.Image, target: tuple[int, int], background: tuple[int, int, int], padding: float = 0.0) -> Image.Image:
    width, height = target
    available = (max(1, round(width * (1 - padding * 2))), max(1, round(height * (1 - padding * 2))))
    fitted = image.copy()
    fitted.thumbnail(available, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", target, background)
    canvas.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2), fitted if fitted.mode == "RGBA" else None)
    return canvas


@lru_cache(maxsize=32)
def _template_config(resource_root: Path, category: str | None, size: str) -> tuple[float, float, float] | None:
    """Read the bundled v2 category template without importing its UI core."""
    if not category:
        return None
    try:
        payload = json.loads((resource_root / "templates" / "category_templates.json").read_text(encoding="utf-8"))
        raw = payload.get(category)
        if not isinstance(raw, dict):
            return None
        sized = raw.get("sizes", {}).get(size) if isinstance(raw.get("sizes"), dict) else raw
        if not isinstance(sized, dict):
            return None
        return float(sized.get("scale", 1.0)), float(sized.get("position_x", 0.5)), float(sized.get("position_y", 0.5))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _template_crop(image: Image.Image, target: tuple[int, int], template: tuple[float, float, float], transparent: bool, background: tuple[int, int, int]) -> Image.Image:
    scale, position_x, position_y = template
    working = image.convert("RGBA")
    alpha_box = working.getchannel("A").getbbox()
    if alpha_box:
        left, top, right, bottom = alpha_box
        margin_x = round((right - left) * 0.01)
        margin_y = round((bottom - top) * 0.01)
        working = working.crop((max(0, left - margin_x), max(0, top - margin_y), min(working.width, right + margin_x), min(working.height, bottom + margin_y)))
    working.thumbnail((max(1, round(target[0] * scale)), max(1, round(target[1] * scale))), Image.Resampling.LANCZOS)
    center = (round(target[0] * position_x), round(target[1] * position_y))
    position = (center[0] - working.width // 2, center[1] - working.height // 2)
    if transparent:
        canvas = Image.new("RGBA", target, (0, 0, 0, 0))
        canvas.alpha_composite(working, position)
        return canvas
    canvas = Image.new("RGB", target, background)
    canvas.paste(working, position, working)
    return canvas


def _corner_background_colors(
    image: Image.Image,
    product_box: tuple[int, int, int, int] | None,
    sample_ratio: float,
) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    """Sample four corner colours while excluding the detected product."""
    rgb = image.convert("RGB")
    sample_width = max(2, round(rgb.width * sample_ratio))
    sample_height = max(2, round(rgb.height * sample_ratio))
    excluded = None
    if product_box is not None:
        margin_x = round((product_box[2] - product_box[0]) * 0.02)
        margin_y = round((product_box[3] - product_box[1]) * 0.02)
        excluded = (
            max(0, product_box[0] - margin_x),
            max(0, product_box[1] - margin_y),
            min(rgb.width, product_box[2] + margin_x),
            min(rgb.height, product_box[3] + margin_y),
        )

    def sample(left: int, top: int) -> tuple[int, int, int]:
        values: list[tuple[int, int, int]] = []
        step = max(1, min(sample_width, sample_height) // 12)
        for y in range(top, min(rgb.height, top + sample_height), step):
            for x in range(left, min(rgb.width, left + sample_width), step):
                if excluded and excluded[0] <= x < excluded[2] and excluded[1] <= y < excluded[3]:
                    continue
                values.append(rgb.getpixel((x, y)))
        if not values:
            return _edge_color(rgb)
        return tuple(int(median(channel)) for channel in zip(*values))

    return (
        sample(0, 0),
        sample(max(0, rgb.width - sample_width), 0),
        sample(0, max(0, rgb.height - sample_height)),
        sample(max(0, rgb.width - sample_width), max(0, rgb.height - sample_height)),
    )


def _corner_gradient(
    size: tuple[int, int],
    colors: tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]],
) -> Image.Image:
    gradient = Image.new("RGB", (2, 2))
    gradient.putdata(colors)
    return gradient.resize(size, Image.Resampling.BILINEAR)


def _outer_background_blend_mask(
    size: tuple[int, int],
    product_box: tuple[int, int, int, int] | None,
    blend_ratio: float,
) -> Image.Image:
    edge = max(1, round(min(size) * blend_ratio))
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rectangle((edge, edge, max(edge, size[0] - edge - 1), max(edge, size[1] - edge - 1)), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(max(1, edge // 2)))
    if product_box is not None:
        ImageDraw.Draw(mask).rectangle(product_box, fill=255)
    return mask


def _scaled_opaque_canvas(
    image: Image.Image,
    target: tuple[int, int],
    category_template: tuple[float, float, float] | None,
    product_box: tuple[int, int, int, int] | None,
    edge_color: tuple[int, int, int],
    portrait_padding: float,
    corner_colors: tuple[
        tuple[int, int, int],
        tuple[int, int, int],
        tuple[int, int, int],
        tuple[int, int, int],
    ] | None = None,
    scale_multiplier: float = 1.0,
) -> Image.Image:
    """Match the legacy light-background foreground extension path."""
    source = image.convert("RGB")
    if product_box is None:
        return _resize_contain(source, target, edge_color, portrait_padding)
    base_scale = category_template[0] if category_template is not None else 1 - portrait_padding * 2
    box_width = max(1, product_box[2] - product_box[0])
    box_height = max(1, product_box[3] - product_box[1])
    photo_scale = min(target[0] * base_scale / box_width, target[1] * base_scale / box_height)
    photo_scale *= max(0.05, _float(scale_multiplier, 1.0))
    scaled_size = (
        max(1, round(source.width * photo_scale)),
        max(1, round(source.height * photo_scale)),
    )
    scaled = source.resize(scaled_size, Image.Resampling.LANCZOS)
    scaled_box = tuple(round(value * photo_scale) for value in product_box)
    position_x = category_template[1] if category_template is not None else 0.5
    position_y = category_template[2] if category_template is not None else 0.5
    product_center_x = (scaled_box[0] + scaled_box[2]) / 2
    product_center_y = (scaled_box[1] + scaled_box[3]) / 2
    position = (
        round(target[0] * position_x - product_center_x),
        round(target[1] * position_y - product_center_y),
    )
    colors = corner_colors or _corner_background_colors(source, product_box, 0.04)
    canvas = _corner_gradient(target, colors)
    blend_mask = _outer_background_blend_mask(scaled_size, scaled_box, 0.03)
    canvas.paste(scaled, position, blend_mask)
    return canvas


def _light_foreground_bbox(image: Image.Image, background: tuple[int, int, int]) -> tuple[int, int, int, int] | None:
    """Find the edge-connected light background bounds used by legacy."""
    rgb = image.convert("RGB")
    scale = min(1.0, 512 / max(rgb.width, rgb.height))
    preview = rgb.resize((max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale))), Image.Resampling.BILINEAR)
    width, height = preview.size
    pixels = list(preview.getdata())
    light = bytearray(max(abs(channel - base) for channel, base in zip(pixel, background)) <= 32 for pixel in pixels)
    visited = bytearray(len(light))
    pending: list[tuple[int, int]] = []
    pending.extend((0, x) for x in range(width)); pending.extend((height - 1, x) for x in range(width))
    pending.extend((y, 0) for y in range(1, height - 1)); pending.extend((y, width - 1) for y in range(1, height - 1))
    while pending:
        y, x = pending.pop()
        index = y * width + x
        if visited[index] or not light[index]:
            continue
        visited[index] = 1
        if y: pending.append((y - 1, x))
        if y + 1 < height: pending.append((y + 1, x))
        if x: pending.append((y, x - 1))
        if x + 1 < width: pending.append((y, x + 1))
    foreground = [index for index, value in enumerate(visited) if not value]
    if not foreground:
        return None
    left = min(index % width for index in foreground); top = min(index // width for index in foreground)
    right = max(index % width for index in foreground) + 1; bottom = max(index // width for index in foreground) + 1
    return round(left / width * rgb.width), round(top / height * rgb.height), round(right / width * rgb.width), round(bottom / height * rgb.height)


def _light_foreground_touches_edge(image: Image.Image, background: tuple[int, int, int]) -> bool:
    """Match legacy's safe cover path when light-background products reach an edge."""
    rgb = image.convert("RGB")
    scale = min(1.0, 512 / max(rgb.width, rgb.height))
    preview = rgb.resize((max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale))), Image.Resampling.BILINEAR)
    width, height = preview.size
    edge_pixels = [preview.getpixel((x, y)) for x in range(width) for y in (0, height - 1)]
    edge_pixels.extend(preview.getpixel((x, y)) for y in range(1, height - 1) for x in (0, width - 1))
    foreground = sum(
        max(abs(channel - base) for channel, base in zip(pixel, background)) > 32
        for pixel in edge_pixels
    )
    return foreground / max(1, len(edge_pixels)) >= 0.03


def _mirror_extend(image: Image.Image, target: tuple[int, int], focus_x: float = 0.5, focus_y: float = 0.5) -> Image.Image:
    """Fill a contain canvas with mirrored edge strips.

    ponytail: this is intentionally a conservative edge-only extension; a
    future segmentation model can replace it when background confidence is
    available.
    """
    width, height = target
    scale = min(width / image.width, height / image.height)
    fitted = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", target, _edge_color(image))
    left = round((width - fitted.width) * max(0.0, min(1.0, focus_x)))
    top = round((height - fitted.height) * max(0.0, min(1.0, focus_y)))
    if left > 0:
        strip = ImageOps.mirror(fitted.crop((0, 0, min(left, fitted.width), fitted.height))).resize((left, fitted.height), Image.Resampling.BILINEAR)
        canvas.paste(strip, (0, top))
    if width - (left + fitted.width) > 0:
        extra = width - left - fitted.width
        strip = ImageOps.mirror(fitted.crop((max(0, fitted.width - min(extra, fitted.width)), 0, fitted.width, fitted.height))).resize((extra, fitted.height), Image.Resampling.BILINEAR)
        canvas.paste(strip, (left + fitted.width, top))
    canvas.paste(fitted, (left, top))
    return canvas


def _background_to_alpha(image: Image.Image, background: tuple[int, int, int]) -> Image.Image:
    rgba = image.convert("RGBA")
    pixels = list(rgba.convert("RGB").getdata())
    alpha = []
    for pixel in pixels:
        distance = max(abs(channel - base) for channel, base in zip(pixel, background))
        alpha.append(0 if distance <= 8 else 255 if distance >= 48 else round((distance - 8) * 255 / 40))
    rgba.putalpha(Image.frombytes("L", rgba.size, bytes(alpha)))
    return rgba


def _encode_jpeg(image: Image.Image, max_size_mb: float | None) -> bytes:
    if image.mode != "RGB":
        image = image.convert("RGB")
    quality = 95
    while True:
        stream = BytesIO()
        image.save(stream, format="JPEG", quality=quality, subsampling=0, optimize=True)
        payload = stream.getvalue()
        if max_size_mb is None or len(payload) <= max_size_mb * 1024 * 1024 or quality <= 45:
            return payload
        quality -= 5


def _encode_png(image: Image.Image, max_size_mb: float | None) -> bytes:
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    limit = None if max_size_mb is None else max_size_mb * 1024 * 1024
    candidates = [rgba]
    if limit is not None:
        # ponytail: quantize only when compression alone misses the limit;
        # dimensions stay fixed and RGBA output remains the public contract.
        for colors in (256, 128, 64, 32):
            try:
                candidates.append(rgba.quantize(colors=colors, method=Image.Quantize.FASTOCTREE).convert("RGBA"))
            except (ValueError, OSError):
                break
    best = b""
    for candidate in candidates:
        stream = BytesIO()
        candidate.save(stream, format="PNG", optimize=True, compress_level=9)
        payload = stream.getvalue()
        if not best or len(payload) < len(best):
            best = payload
        if limit is None or len(payload) <= limit:
            return payload
    return best


def _save_jpeg(image: Image.Image, path: Path, max_size_mb: float | None) -> None:
    """Compatibility helper retained for callers outside ``Renderer``."""
    path.write_bytes(_encode_jpeg(image, max_size_mb))


def _save_png(image: Image.Image, path: Path, max_size_mb: float | None) -> None:
    """Compatibility helper retained for callers outside ``Renderer``."""
    path.write_bytes(_encode_png(image, max_size_mb))


class Renderer:
    """Render typed requests with no dependency on the v2 application."""

    def __init__(self, logo_path: str | Path | None = None, border_path: str | Path | None = None, resource_root: str | Path | None = None) -> None:
        root = Path(resource_root) if resource_root else Path(__file__).resolve().parent.parent / "resources"
        self.resource_root = root
        self.logo_path = Path(logo_path) if logo_path else root / "新logo 1440.png"
        self.white_logo_path = root / "新logo 1440 2.png"
        self.tall_logo_path = root / "新logo 1920.png"
        self.tall_white_logo_path = root / "新logo 1920 2.png"
        self.border_path = Path(border_path) if border_path else root / "唯品会边框.png"
        self._analysis_cache: dict[tuple[str, int, int], SourceAnalysis] = {}

    @staticmethod
    @lru_cache(maxsize=32)
    def _asset(path: str, size: tuple[int, int]) -> Image.Image:
        with Image.open(path) as opened:
            image = opened.convert("RGBA")
        return image if image.size == size else image.resize(size, Image.Resampling.LANCZOS)

    @staticmethod
    @lru_cache(maxsize=4)
    def _source_image(path: str, mtime_ns: int, file_size: int) -> Image.Image:
        """Keep a small decoded-source cache for the concurrent render batch."""
        del mtime_ns, file_size  # cache identity only; the path is read below
        with Image.open(path) as opened:
            opened.load()
            return opened.copy()

    @staticmethod
    def _analyze_source(image: Image.Image) -> SourceAnalysis:
        if "A" in image.getbands() or "transparency" in image.info:
            return SourceAnalysis(None, False, None, False, None)
        rgb = image.convert("RGB")
        edge_color = _edge_color(rgb)
        light_background = _is_light_background(rgb)
        if not light_background:
            return SourceAnalysis(edge_color, False, None, False, None)
        foreground_bbox = _light_foreground_bbox(rgb, edge_color)
        touches_edge = _light_foreground_touches_edge(rgb, edge_color)
        corner_colors = _corner_background_colors(rgb, foreground_bbox, 0.04)
        return SourceAnalysis(edge_color, True, foreground_bbox, touches_edge, corner_colors)

    def _output_path(self, request: RenderRequest, size_name: str, transparent: bool, vip: bool) -> Path:
        metadata = request.metadata
        explicit = request.output_path or metadata.get("output_path")
        if explicit:
            path = Path(str(explicit))
            return path if path.is_absolute() else Path(request.output_root) / path
        source = Path(request.source)
        filename = str(request.layout.filename or metadata.get("filename") or f"{_safe_stem(source)}_{size_name}{'_透明' if transparent else ''}{'.png' if transparent else '.jpg'}")
        output_dir = metadata.get("output_dir")
        if output_dir:
            base = Path(str(output_dir))
            return base if base.suffix else base / filename
        root = Path(request.output_root)
        if transparent:
            return root / "透明产品图" / size_name / filename
        if vip:
            return root / "唯品专享1440" / "无Logo" / filename
        category = str(request.layout.output_category or request.layout.category or metadata.get("category") or "产品主图")
        group = str(request.layout.group or metadata.get("group") or "")
        parts = [root, category]
        if group:
            parts.append(Path(group))
        parts.append(Path(size_name))
        parts.append(Path("含Logo" if bool(metadata.get("include_logo", request.include_logo)) else "无Logo"))
        return Path(*parts) / filename

    def _compose(
        self,
        image: Image.Image,
        request: RenderRequest,
        target: tuple[int, int],
        transparent: bool,
        analysis: SourceAnalysis | None = None,
    ) -> Image.Image:
        metadata = request.metadata
        layout = request.layout
        plan = request.plan
        source_type = str(request.source_type or metadata.get("source_type") or metadata.get("material_type") or "product").lower()
        size_name = f"{target[0]}x{target[1]}"
        default_background = {
            "1440x1440": (255, 255, 255),
            "1440x1920": (239, 239, 239),
            "1125x1500": (239, 239, 239),
        }.get(size_name, (255, 255, 255))
        background = tuple(int(value) for value in (request.background or metadata.get("background") or default_background))
        if len(background) != 3:
            background = (255, 255, 255)
        analysis = analysis or self._analyze_source(image)
        source_scale = max(
            0.05,
            1.0 + _float(metadata.get("source_scale_adjustment"), 0.0) / 100.0,
        )
        category_template = None
        category = layout.category if layout.category != "产品主图" else str(metadata.get("category") or layout.category)
        enable_category_template = layout.enable_category_template or bool(metadata.get("enable_category_template"))
        if enable_category_template and category:
            template_size = {"800x800": "1440x1440", "1440x2160": "1440x1920"}.get(size_name, size_name)
            category_template = _template_config(self.resource_root, category, template_size)
            if category_template is not None:
                template_scale = plan.scale if plan is not None else 1.0
                category_template = (
                    category_template[0] * template_scale,
                    category_template[1],
                    category_template[2],
                )
        if transparent or request.transparent:
            if category_template is not None:
                return _template_crop(
                    image,
                    target,
                    (category_template[0] * source_scale, category_template[1], category_template[2]),
                    True,
                    background,
                )
            working = image.convert("RGBA")
            if analysis.light_background and analysis.edge_color is not None:
                working = _background_to_alpha(working, analysis.edge_color)
            alpha_box = working.getchannel("A").getbbox()
            if alpha_box:
                working = _crop_with_margin(working, alpha_box)
            scale = max(0.05, _float(plan.scale if plan else layout.scale if layout.scale != 1.0 else metadata.get("scale"), 1.0))
            working.thumbnail((max(1, round(target[0] * 0.92 * scale * source_scale)), max(1, round(target[1] * 0.92 * scale * source_scale))), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", target, (0, 0, 0, 0))
            canvas.alpha_composite(working, ((target[0] - working.width) // 2, (target[1] - working.height) // 2))
            return canvas
        focus = (plan.position_x, plan.position_y) if plan else (_float(metadata.get("position_x"), 0.5), _float(metadata.get("position_y"), 0.5))
        protected = _plan_box(plan)
        anchored = plan is not None and plan.composition_mode == "anchor_crop" and plan.crop_box is not None
        if source_type in {"detail", "model_detail"} and not (source_type == "model_detail" and anchored):
            return _resize_cover(image.convert("RGB"), target)
        if source_type in {"model", "model_detail"}:
            scale = _float(plan.scale if plan else layout.scale if layout.scale != 1.0 else metadata.get("scale"), 1.0)
            if plan and plan.fallback:
                scale = min(scale, 1.0)
            image = image.convert("RGB")
            if plan and plan.crop_box is not None:
                # Anchor composition: the window already has the target
                # aspect ratio, so crop and resize without further clamping.
                left, top, right, bottom = plan.crop_box
                if left < 0.0 or right > 1.0:
                    return _side_filled_window(image, plan.crop_box, target, plan.evidence.get("side_fill"))
                window = image.crop((
                    round(left * image.width),
                    round(top * image.height),
                    round(right * image.width),
                    round(bottom * image.height),
                ))
                return window.resize(target, Image.Resampling.LANCZOS)
            if plan and plan.crop_ratio is not None and target[0] == target[1]:
                return _resize_model_direct(image, target, plan.crop_ratio, focus, protected)
            if (layout.allow_mirror_extension or bool(metadata.get("allow_mirror_extension"))) and target[0] == target[1] and analysis.light_background:
                if scale <= 1.0:
                    return _mirror_extend(image, target, focus[0], focus[1])
            return _resize_cover(image, target, focus, protected, scale)
        if image.mode in {"RGBA", "LA"} and image.getchannel("A").getbbox():
            if category_template is not None:
                return _template_crop(
                    image,
                    target,
                    (category_template[0] * source_scale, category_template[1], category_template[2]),
                    False,
                    background,
                )
            composed = _resize_contain(image.convert("RGBA"), target, background, 0.05 if target[1] > target[0] else 0.04)
            return composed
        image = image.convert("RGB")
        if analysis.light_background and analysis.edge_color is not None:
            edge_color = analysis.edge_color
            if analysis.touches_edge:
                return _resize_cover(image, target, focus, protected)
            # Match legacy's foreground-aware extension for light products.
            if category_template is not None or target[1] > target[0] or plan is not None:
                return _scaled_opaque_canvas(
                    image,
                    target,
                    category_template,
                    analysis.foreground_bbox,
                    edge_color,
                    0.12 if size_name == "1125x1500" else 0.05,
                    analysis.corner_colors,
                    source_scale,
                )
            return _resize_cover(image, target, focus, protected, source_scale)
        return _resize_cover(image, target, focus, protected, source_scale)

    def _overlay(self, image: Image.Image, request: RenderRequest, vip: bool) -> Image.Image:
        metadata = request.metadata
        assets = request.assets
        logo_enabled = bool(request.include_logo if "include_logo" not in metadata else metadata.get("include_logo")) and not bool(metadata.get("without_logo"))
        result = image.convert("RGBA")
        if logo_enabled:
            size_name = str(request.size).lower()
            tall = size_name in {"1440x1920", "1125x1500"}
            logo_path = assets.logo or request.logo or metadata.get("logo_path")
            white_logo_path = assets.white_logo or metadata.get("white_logo_path")
            if logo_path:
                path = Path(str(logo_path))
                white_path = Path(str(white_logo_path)) if white_logo_path else None
                if white_path is None:
                    sibling = "2" if " 2" not in path.name else ""
                    candidate = path.with_name(path.name.replace(".png", " 2.png")) if sibling else None
                    white_path = candidate if candidate and candidate.is_file() else None
            else:
                base_path = self.tall_logo_path if tall else self.logo_path
                white_path = self.tall_white_logo_path if tall else self.white_logo_path
                if not base_path.is_file():
                    raise FileNotFoundError(f"Logo resource not found: {base_path}")
                logo_box = self._asset(str(base_path), result.size).getchannel("A").getbbox()
                probe = result.convert("RGB").crop(logo_box) if logo_box else result.convert("RGB")
                mean = ImageStat.Stat(probe).mean
                luminance = mean[0] * 0.2126 + mean[1] * 0.7152 + mean[2] * 0.0722
                path = white_path if luminance < 160 and white_path.is_file() else base_path
            if not path.is_file():
                raise FileNotFoundError(f"Logo resource not found: {path}")
            logo = self._asset(str(path), result.size)
            if white_path is not None and white_path.is_file() and path != white_path:
                # Legacy measures the rendered logo footprint on the canvas,
                # so custom assets use the same coordinates after resizing.
                logo_box = logo.getchannel("A").getbbox()
                probe = result.convert("RGB").crop(logo_box) if logo_box else result.convert("RGB")
                mean = ImageStat.Stat(probe).mean
                luminance = mean[0] * 0.2126 + mean[1] * 0.7152 + mean[2] * 0.0722
                if luminance < 160:
                    logo = self._asset(str(white_path), result.size)
            result.alpha_composite(logo, (0, 0))
        if vip or request.layout.add_border or bool(metadata.get("add_border")):
            if vip:
                # legacy creates VIP images by reopening the saved no-logo JPEG
                # before compositing the border; preserve that byte-level step.
                stream = BytesIO()
                result.convert("RGB").save(stream, format="JPEG", quality=95, subsampling=0, optimize=True)
                stream.seek(0)
                result = Image.open(stream).convert("RGBA")
            path = Path(str(assets.border or request.border or metadata.get("border_path") or self.border_path))
            if not path.is_file():
                raise FileNotFoundError(f"VIP border resource not found: {path}")
            border = self._asset(str(path), result.size)
            result.alpha_composite(border)
        return result

    @staticmethod
    def _stage_metadata(stage_times: dict[str, float]) -> dict[str, Any]:
        """Expose stage timings both flat and as a stable nested mapping."""
        return {
            **stage_times,
            "timings": dict(stage_times),
        }

    def _result_failure(
        self,
        request: RenderRequest,
        size_name: str,
        error: str,
        *,
        stage_times: dict[str, float] | None = None,
        fallback: bool = True,
    ) -> RenderResult:
        timings = stage_times or {
            "decode_seconds": 0.0,
            "analysis_seconds": 0.0,
            "compose_seconds": 0.0,
            "overlay_seconds": 0.0,
            "encode_seconds": 0.0,
            "write_seconds": 0.0,
            "total_seconds": 0.0,
        }
        metadata: dict[str, Any] = {
            "size": size_name,
            "fallback": fallback,
            "fallback_reason": error,
            "summary": request.summary.to_dict() if request.summary else None,
        }
        metadata.update(self._stage_metadata(timings))
        return RenderResult(
            source=str(request.source),
            outputs=(),
            success=False,
            mode=request.mode,
            errors=(error,),
            metadata=metadata,
        )

    def _render_prepared(
        self,
        request: RenderRequest,
        source: Path,
        source_image: Image.Image,
        analysis: SourceAnalysis,
        source_stage_times: dict[str, float],
    ) -> RenderResult:
        """Render one output from a decoded and analysed source image."""
        size_name, target = _size(request.size)
        transparent = bool(request.transparent or request.output_kind.lower() in {"transparent", "png", "rgba"})
        vip = bool(request.include_vip or request.output_kind.lower() in {"vip", "唯品", "vip_square"})
        output = self._output_path(request, size_name, transparent, vip)
        compose_seconds = overlay_seconds = encode_seconds = write_seconds = 0.0
        errors: list[str] = []
        encoded_bytes = b""
        expected_mode = "RGBA" if transparent else "RGB"
        actual_mode = expected_mode
        actual_bytes = 0
        failed_by_exception = False
        try:
            compose_started = time.perf_counter()
            composed = self._compose(source_image, request, target, transparent, analysis)
            compose_seconds = time.perf_counter() - compose_started

            if not transparent:
                overlay_started = time.perf_counter()
                composed = self._overlay(composed, request, vip)
                overlay_seconds = time.perf_counter() - overlay_started

            # The output contract is checked against the in-memory image.  The
            # written file is only checked for successful creation and size.
            rendered = composed.convert(expected_mode)
            actual_mode = rendered.mode
            if rendered.size != target:
                errors.append(f"output size {rendered.size!r} != {target!r}")
            if actual_mode != expected_mode:
                errors.append(f"output mode {actual_mode!r} != {expected_mode!r}")
            if not errors:
                encode_started = time.perf_counter()
                encoded_bytes = (
                    _encode_png(rendered, request.size_limit_mb)
                    if transparent
                    else _encode_jpeg(rendered, request.size_limit_mb)
                )
                encode_seconds = time.perf_counter() - encode_started
                if not encoded_bytes:
                    errors.append("encoded output is empty")

            if not errors:
                write_started = time.perf_counter()
                try:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(encoded_bytes)
                except Exception as error:  # noqa: BLE001 - preserve one-output failure
                    errors.append(f"write failed: {error}")
                write_seconds = time.perf_counter() - write_started
                if not errors:
                    try:
                        if not output.is_file():
                            errors.append("output file was not created")
                        else:
                            actual_bytes = output.stat().st_size
                            if actual_bytes <= 0:
                                errors.append("output file is empty")
                            elif actual_bytes != len(encoded_bytes):
                                errors.append(
                                    f"output size on disk {actual_bytes} != encoded size {len(encoded_bytes)}"
                                )
                            if request.size_limit_mb is not None and actual_bytes > request.size_limit_mb * 1024 * 1024:
                                errors.append("output exceeds max_size_mb")
                    except OSError as error:
                        errors.append(f"output stat failed: {error}")
        except Exception as error:  # noqa: BLE001 - one output must not stop a source batch
            failed_by_exception = True
            errors.append(str(error))

        # ``total_seconds`` in one result is the sum of its measured stages.
        # Source decode/analysis are shared by the material batch, so the
        # batch-level ``total_seconds`` remains the wall-clock measurement.
        total_seconds = (
            source_stage_times.get("decode_seconds", 0.0)
            + source_stage_times.get("analysis_seconds", 0.0)
            + compose_seconds
            + overlay_seconds
            + encode_seconds
            + write_seconds
        )
        stage_times = {
            "decode_seconds": source_stage_times.get("decode_seconds", 0.0),
            "analysis_seconds": source_stage_times.get("analysis_seconds", 0.0),
            "compose_seconds": compose_seconds,
            "overlay_seconds": overlay_seconds,
            "encode_seconds": encode_seconds,
            "write_seconds": write_seconds,
            "total_seconds": total_seconds,
        }
        metadata: dict[str, Any] = {
            "size": size_name,
            "width": target[0],
            "height": target[1],
            "mode": actual_mode,
            "bytes": actual_bytes,
            "fallback": bool(failed_by_exception or (request.plan and request.plan.fallback)),
            "risk_flags": list(request.plan.risk_flags) if request.plan else [],
            "requires_review": bool(request.plan and request.plan.requires_review),
            "review_hint": (
                request.plan.review_hint
                if request.plan and request.plan.review_hint
                else request.plan.evidence.get("review_hint")
                if request.plan
                else None
            ),
            "fallback_reason": (
                request.plan.fallback_reason
                if request.plan and request.plan.fallback_reason
                else errors[0]
                if failed_by_exception and errors
                else ""
            ),
            "skipped_by_scope": bool(request.plan and request.plan.skipped_by_scope),
            "summary": request.summary.to_dict() if request.summary else None,
        }
        metadata.update(self._stage_metadata(stage_times))
        return RenderResult(
            source=str(source),
            outputs=(str(output),) if not errors else (),
            success=not errors,
            mode=request.mode,
            errors=tuple(errors),
            metadata=metadata,
        )

    def render(self, request: RenderRequest | str | Path, output_path: str | Path | None = None, config: RenderConfig | None = None) -> RenderResult | LegacyRenderResult:
        """Render a typed request, with a small compatibility call shape."""
        if not isinstance(request, RenderRequest):
            if output_path is None or config is None:
                raise TypeError("typed RenderRequest or (input, output, config) is required")
            typed = RenderRequest(
                source=str(request),
                output_root=str(Path(output_path).parent),
                size=f"{config.width}x{config.height}",
                include_logo=config.logo_state == "含Logo",
                transparent=config.transparent,
                max_size_mb=config.max_size_mb,
                source_type=config.source_type,
                output_path=str(output_path),
                size_limit=config.max_size_mb,
                metadata={"output_path": str(output_path), "add_border": config.add_border, "source_type": config.source_type},
            )
            results, _timings = self.render_source_batch((typed,))
            result = results[0]
            return LegacyRenderResult(str(output_path), config.width, config.height, result.success, result.errors[0] if result.errors else "")
        if output_path is not None:
            metadata = dict(request.metadata)
            metadata["output_path"] = str(output_path)
            request = replace(request, metadata=metadata)
        request.validate()
        results, _timings = self.render_source_batch((request,))
        return results[0]

    def render_source_batch(self, requests: Iterable[RenderRequest]) -> tuple[tuple[RenderResult, ...], dict[str, float | int]]:
        """Render all outputs for one source after one decode and analysis pass."""
        started = time.perf_counter()
        items = tuple(requests)
        if not items:
            return (), {"total_seconds": 0.0}

        # ``engine`` groups by source before calling this method.  Keep the
        # method safe for direct callers by preserving order across mixed
        # source input while still preparing each source only once.
        groups: dict[str, list[tuple[int, RenderRequest]]] = {}
        for index, request in enumerate(items):
            groups.setdefault(str(request.source), []).append((index, request))
        if len(groups) > 1:
            ordered: list[RenderResult | None] = [None] * len(items)
            aggregate: dict[str, float | int] = {}
            for group in groups.values():
                group_results, group_timings = self.render_source_batch(request for _index, request in group)
                for (index, _request), result in zip(group, group_results):
                    ordered[index] = result
                for key, value in group_timings.items():
                    if isinstance(value, (int, float)):
                        aggregate[key] = aggregate.get(key, 0) + value
            aggregate["total_seconds"] = time.perf_counter() - started
            return tuple(result for result in ordered if result is not None), aggregate

        results: list[RenderResult | None] = [None] * len(items)
        work: list[tuple[int, RenderRequest, str, tuple[int, int], bool, bool]] = []
        for index, request in enumerate(items):
            try:
                request.validate()
                size_name, target = _size(request.size)
                transparent = bool(request.transparent or request.output_kind.lower() in {"transparent", "png", "rgba"})
                vip = bool(request.include_vip or request.output_kind.lower() in {"vip", "唯品", "vip_square"})
                if request.validate_only or request.metadata.get("validate_only"):
                    results[index] = self.validate_existing(request)
                else:
                    work.append((index, request, size_name, target, transparent, vip))
            except Exception as error:  # noqa: BLE001 - return one failure per request
                results[index] = self._result_failure(request, str(request.size), str(error))

        if not work:
            elapsed = time.perf_counter() - started
            return tuple(result for result in results if result is not None), {"total_seconds": elapsed}

        source = Path(work[0][1].source)
        source_stage_times = {"decode_seconds": 0.0, "analysis_seconds": 0.0}
        try:
            if not source.is_file():
                raise FileNotFoundError(f"input file not found: {source}")
            stat = source.stat()
            decode_started = time.perf_counter()
            source_image = self._source_image(str(source), stat.st_mtime_ns, stat.st_size)
            source_stage_times["decode_seconds"] = time.perf_counter() - decode_started
            analysis_started = time.perf_counter()
            cache_key = (str(source), stat.st_mtime_ns, stat.st_size)
            analysis = self._analysis_cache.get(cache_key)
            if analysis is None:
                analysis = self._analyze_source(source_image)
                self._analysis_cache[cache_key] = analysis
            source_stage_times["analysis_seconds"] = time.perf_counter() - analysis_started
        except Exception as error:  # noqa: BLE001 - retain every source failure
            for index, request, size_name, _target, _transparent, _vip in work:
                results[index] = self._result_failure(request, size_name, str(error), stage_times={
                    **source_stage_times,
                    "compose_seconds": 0.0,
                    "overlay_seconds": 0.0,
                    "encode_seconds": 0.0,
                    "write_seconds": 0.0,
                    "total_seconds": time.perf_counter() - started,
                })
            elapsed = time.perf_counter() - started
            return tuple(result for result in results if result is not None), {
                **source_stage_times,
                "total_seconds": elapsed,
            }

        compose_total = overlay_total = encode_total = write_total = 0.0
        for index, request, _size_name, _target, _transparent, _vip in work:
            try:
                result = self._render_prepared(request, source, source_image, analysis, source_stage_times)
            except Exception as error:  # noqa: BLE001 - preserve one-output failure
                result = self._result_failure(
                    request,
                    _size_name,
                    str(error),
                    stage_times={
                        **source_stage_times,
                        "compose_seconds": 0.0,
                        "overlay_seconds": 0.0,
                        "encode_seconds": 0.0,
                        "write_seconds": 0.0,
                        "total_seconds": source_stage_times.get("decode_seconds", 0.0)
                        + source_stage_times.get("analysis_seconds", 0.0),
                    },
                )
            results[index] = result
            compose_total += float(result.metadata.get("compose_seconds", 0.0) or 0.0)
            overlay_total += float(result.metadata.get("overlay_seconds", 0.0) or 0.0)
            encode_total += float(result.metadata.get("encode_seconds", 0.0) or 0.0)
            write_total += float(result.metadata.get("write_seconds", 0.0) or 0.0)

        elapsed = time.perf_counter() - started
        return tuple(result for result in results if result is not None), {
            **source_stage_times,
            "compose_seconds": compose_total,
            "overlay_seconds": overlay_total,
            "encode_seconds": encode_total,
            "write_seconds": write_total,
            "total_seconds": elapsed,
        }

    def render_batch(self, requests: Iterable[RenderRequest]) -> tuple[RenderResult, ...]:
        """Render a batch while reusing preparation work for each source."""
        items = tuple(requests)
        if not items:
            return ()
        groups: dict[str, list[tuple[int, RenderRequest]]] = {}
        for index, request in enumerate(items):
            groups.setdefault(str(request.source), []).append((index, request))
        ordered: list[RenderResult | None] = [None] * len(items)
        for group in groups.values():
            group_results, _timings = self.render_source_batch(request for _index, request in group)
            for (index, _request), result in zip(group, group_results):
                ordered[index] = result
        return tuple(result for result in ordered if result is not None)

    def validate_existing(self, request: RenderRequest) -> RenderResult:
        """Validate an existing output without writing or altering it."""
        request.validate()
        size_name, target = _size(request.size)
        transparent = bool(request.transparent or request.output_kind.lower() in {"transparent", "png", "rgba"})
        output = self._output_path(request, size_name, transparent, request.output_kind.lower() in {"vip", "唯品", "vip_square"})
        source = Path(request.source)
        if not source.is_file():
            return RenderResult(
                source=str(request.source),
                outputs=(),
                success=False,
                mode=request.mode,
                errors=(f"input file not found: {source}",),
                metadata={"size": size_name, "validate_only": True},
            )
        return self._validate_existing(request, source, output, size_name, target, transparent)

    def _validate_existing(
        self,
        request: RenderRequest,
        source: Path,
        output: Path,
        size_name: str,
        target: tuple[int, int],
        transparent: bool,
    ) -> RenderResult:
        """Validate a legacy-produced file through the typed renderer seam."""
        errors: list[str] = []
        try:
            with Image.open(source) as written:
                actual_size = written.size
                actual_mode = written.mode
                has_alpha = "A" in written.getbands() or "transparency" in written.info
        except Exception as error:  # noqa: BLE001 - report one output cleanly
            return RenderResult(
                source=str(request.source),
                outputs=(),
                success=False,
                mode=request.mode,
                errors=(str(error),),
                metadata={"size": size_name, "validate_only": True},
            )
        if actual_size != target:
            errors.append(f"output size {actual_size!r} != {target!r}")
        if transparent:
            if not has_alpha:
                errors.append(f"output mode {actual_mode!r} has no alpha channel")
        elif actual_mode not in {"RGB", "L"}:
            errors.append(f"output mode {actual_mode!r} is not RGB")
        if request.size_limit_mb is not None and source.stat().st_size > request.size_limit_mb * 1024 * 1024:
            errors.append("output exceeds max_size_mb")
        return RenderResult(
            source=str(request.source),
            outputs=(str(output),) if not errors else (),
            success=not errors,
            mode=request.mode,
            errors=tuple(errors),
            metadata={
                "size": size_name,
                "width": target[0],
                "height": target[1],
                "mode": actual_mode,
                "bytes": source.stat().st_size,
                "validate_only": True,
            },
        )


V3Renderer = Renderer


__all__ = [
    "STANDARD_SIZES",
    "TRANSPARENT_SIZES",
    "RenderConfig",
    "LegacyRenderResult",
    "Renderer",
    "V3Renderer",
]
