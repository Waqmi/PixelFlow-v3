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
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from PIL import Image, ImageOps, ImageStat

from .contracts import CompositionPlan, RenderRequest, RenderResult


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


def _resize_cover(image: Image.Image, target: tuple[int, int], focus: tuple[float, float] = (0.5, 0.5), protected: tuple[float, float, float, float] | None = None) -> Image.Image:
    """Resize uniformly and crop, clamping the crop around a protected box."""
    width, height = target
    scale = max(width / image.width, height / image.height)
    if protected:
        left, top, right, bottom = protected
        safe_scale = min(
            width * 0.8 / max(1.0, right - left),
            height * 0.8 / max(1.0, bottom - top),
        )
        scale = min(scale, safe_scale)
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


def _resize_contain(image: Image.Image, target: tuple[int, int], background: tuple[int, int, int], padding: float = 0.0) -> Image.Image:
    width, height = target
    available = (max(1, round(width * (1 - padding * 2))), max(1, round(height * (1 - padding * 2))))
    fitted = image.copy()
    fitted.thumbnail(available, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", target, background)
    canvas.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2), fitted if fitted.mode == "RGBA" else None)
    return canvas


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


def _save_jpeg(image: Image.Image, path: Path, max_size_mb: float | None) -> None:
    image = image.convert("RGB")
    quality = 95
    while True:
        stream = BytesIO()
        image.save(stream, format="JPEG", quality=quality, subsampling=0, optimize=True)
        if max_size_mb is None or stream.tell() <= max_size_mb * 1024 * 1024 or quality <= 45:
            path.write_bytes(stream.getvalue())
            return
        quality -= 5


def _save_png(image: Image.Image, path: Path, max_size_mb: float | None) -> None:
    rgba = image.convert("RGBA")
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
            path.write_bytes(payload)
            return
    path.write_bytes(best)


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

    def _compose(self, image: Image.Image, request: RenderRequest, target: tuple[int, int], transparent: bool) -> Image.Image:
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
        category_template = None
        category = layout.category if layout.category != "产品主图" else str(metadata.get("category") or layout.category)
        enable_category_template = layout.enable_category_template or bool(metadata.get("enable_category_template"))
        if enable_category_template and category:
            template_size = {"800x800": "1440x1440", "1440x2160": "1440x1920"}.get(size_name, size_name)
            category_template = _template_config(self.resource_root, category, template_size)
            if category_template is not None and plan is not None and plan.scale != 1.0:
                category_template = (category_template[0] * plan.scale, category_template[1], category_template[2])
        if transparent or request.transparent:
            if category_template is not None:
                return _template_crop(image, target, category_template, True, background)
            working = image.convert("RGBA")
            if "A" not in image.getbands() and _is_light_background(image):
                working = _background_to_alpha(working, _edge_color(image))
            alpha_box = working.getchannel("A").getbbox()
            if alpha_box:
                working = _crop_with_margin(working, alpha_box)
            scale = max(0.05, _float(plan.scale if plan else layout.scale if layout.scale != 1.0 else metadata.get("scale"), 1.0))
            working.thumbnail((max(1, round(target[0] * 0.92 * scale)), max(1, round(target[1] * 0.92 * scale))), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", target, (0, 0, 0, 0))
            canvas.alpha_composite(working, ((target[0] - working.width) // 2, (target[1] - working.height) // 2))
            return canvas
        focus = (plan.position_x, plan.position_y) if plan else (_float(metadata.get("position_x"), 0.5), _float(metadata.get("position_y"), 0.5))
        protected = _plan_box(plan)
        if source_type == "detail":
            return _resize_cover(image.convert("RGB"), target, focus, protected)
        if source_type in {"model", "model_detail"}:
            scale = _float(plan.scale if plan else layout.scale if layout.scale != 1.0 else metadata.get("scale"), 1.0)
            if plan and plan.fallback:
                scale = min(scale, 1.0)
            image = image.convert("RGB")
            if scale != 1.0:
                image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
            if (layout.allow_mirror_extension or bool(metadata.get("allow_mirror_extension"))) and target[0] == target[1] and _is_light_background(image):
                if scale <= 1.0:
                    return _mirror_extend(image, target, focus[0], focus[1])
            return _resize_cover(image, target, focus, protected)
        if image.mode in {"RGBA", "LA"} and image.getchannel("A").getbbox():
            composed = _resize_contain(image.convert("RGBA"), target, background, 0.05 if target[1] > target[0] else 0.04)
            return composed
        image = image.convert("RGB")
        if _is_light_background(image):
            # Keep light product pixels inside the canvas and extend only when
            # a portrait output would otherwise enlarge them too aggressively.
            if target[1] > target[0] or plan is not None:
                return _resize_contain(image, target, _edge_color(image), 0.05 if target[1] > target[0] else 0.04)
            return _resize_cover(image, target, focus, protected)
        return _resize_cover(image, target, focus, protected)

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
                with Image.open(base_path) as opened:
                    logo_box = opened.convert("RGBA").getchannel("A").getbbox()
                probe = result.convert("RGB").crop(logo_box) if logo_box else result.convert("RGB")
                mean = ImageStat.Stat(probe).mean
                luminance = mean[0] * 0.2126 + mean[1] * 0.7152 + mean[2] * 0.0722
                path = white_path if luminance < 160 and white_path.is_file() else base_path
            if not path.is_file():
                raise FileNotFoundError(f"Logo resource not found: {path}")
            with Image.open(path) as opened:
                logo = opened.convert("RGBA")
            if logo.size != result.size:
                logo = logo.resize(result.size, Image.Resampling.LANCZOS)
            if white_path is not None and white_path.is_file() and path != white_path:
                # Legacy measures the rendered logo footprint on the canvas,
                # so custom assets use the same coordinates after resizing.
                logo_box = logo.getchannel("A").getbbox()
                probe = result.convert("RGB").crop(logo_box) if logo_box else result.convert("RGB")
                mean = ImageStat.Stat(probe).mean
                luminance = mean[0] * 0.2126 + mean[1] * 0.7152 + mean[2] * 0.0722
                if luminance < 160:
                    with Image.open(white_path) as opened:
                        logo = opened.convert("RGBA")
                    if logo.size != result.size:
                        logo = logo.resize(result.size, Image.Resampling.LANCZOS)
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
            with Image.open(path) as opened:
                border = opened.convert("RGBA").resize(result.size, Image.Resampling.LANCZOS)
            result.alpha_composite(border)
        return result

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
            result = self.render(typed)
            assert isinstance(result, RenderResult)
            return LegacyRenderResult(str(output_path), config.width, config.height, result.success, result.errors[0] if result.errors else "")
        if output_path is not None:
            metadata = dict(request.metadata)
            metadata["output_path"] = str(output_path)
            request = replace(request, metadata=metadata)
        request.validate()
        size_name, target = _size(request.size)
        transparent = bool(request.transparent or request.output_kind.lower() in {"transparent", "png", "rgba"})
        vip = bool(request.include_vip or request.output_kind.lower() in {"vip", "唯品", "vip_square"})
        output = self._output_path(request, size_name, transparent, vip)
        try:
            source = Path(request.source)
            if not source.is_file():
                raise FileNotFoundError(f"input file not found: {source}")
            if request.validate_only or request.metadata.get("validate_only"):
                return self.validate_existing(request)
            output.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(source) as opened:
                composed = self._compose(opened, request, target, transparent)
            if not transparent:
                composed = self._overlay(composed, request, vip)
                _save_jpeg(composed, output, request.size_limit_mb)
                expected_mode = "RGB"
            else:
                _save_png(composed, output, request.size_limit_mb)
                expected_mode = "RGBA"
            with Image.open(output) as written:
                actual_size = written.size
                actual_mode = written.mode
            errors: list[str] = []
            if actual_size != target:
                errors.append(f"output size {actual_size!r} != {target!r}")
            if expected_mode not in actual_mode:
                errors.append(f"output mode {actual_mode!r} != {expected_mode!r}")
            if request.size_limit_mb is not None and output.stat().st_size > request.size_limit_mb * 1024 * 1024:
                errors.append("output exceeds max_size_mb")
            return RenderResult(
                source=str(source),
                outputs=(str(output),) if not errors else (),
                success=not errors,
                mode=request.mode,
                errors=tuple(errors),
                metadata={
                    "size": size_name,
                    "width": target[0],
                    "height": target[1],
                    "mode": actual_mode,
                    "bytes": output.stat().st_size,
                    "fallback": bool(request.plan and request.plan.fallback),
                    "risk_flags": list(request.plan.risk_flags) if request.plan else [],
                    "requires_review": bool(request.plan and request.plan.requires_review),
                    "review_hint": (request.plan.review_hint if request.plan and request.plan.review_hint else request.plan.evidence.get("review_hint") if request.plan else None),
                    "fallback_reason": request.plan.fallback_reason if request.plan else "",
                    "skipped_by_scope": bool(request.plan and request.plan.skipped_by_scope),
                    "summary": request.summary.to_dict() if request.summary else None,
                },
            )
        except Exception as error:  # noqa: BLE001 - one source must not stop a batch
            return RenderResult(
                source=str(request.source),
                outputs=(),
                success=False,
                mode=request.mode,
                errors=(str(error),),
                metadata={
                    "size": size_name,
                    "fallback": True,
                    "fallback_reason": str(error),
                    "summary": request.summary.to_dict() if request.summary else None,
                },
            )

    def render_batch(self, requests: Iterable[RenderRequest]) -> tuple[RenderResult, ...]:
        return tuple(self.render(request) for request in requests)  # type: ignore[misc]

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
