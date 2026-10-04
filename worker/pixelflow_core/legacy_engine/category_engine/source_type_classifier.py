"""Rule-first source material classification with an optional AI boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from statistics import median
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Protocol, Sequence

from PIL import Image

SOURCE_TYPE_IDS = ("main_product", "detail", "model", "model_detail")

MODEL_KEYWORDS = (
    "model", "look", "lookbook", "onbody", "outfit", "wear",
    "模特", "上身", "上脚", "穿搭", "搭配", "实拍穿着", "街拍", "人像", "人物",
    "双人", "多人", "合照", "合影", "情侣", "团体",
)


@dataclass(frozen=True)
class SourceTypeDecision:
    source_type: str
    confidence: float
    source: str
    reason: str
    metadata: dict[str, object] | None = None


class AISourceTypeProvider(Protocol):
    """Future adapter for a local or remote image understanding provider."""

    def classify(
        self,
        source: Path,
        siblings: Sequence[Path],
        context: dict[str, object] | None = None,
    ) -> SourceTypeDecision | None:
        ...


def _normalized_stem(source: Path) -> str:
    stem = source.stem.lower().strip()
    for suffix in ("_jpg", "_jpeg", "_png", " jpg", " jpeg", " png"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return stem.rstrip(" _-.")


def _looks_like_product_filename(source: Path) -> bool:
    """Recognize the stable gender-colour-index product naming convention."""
    stem = source.stem.strip()
    return bool(
        re.match(
            r"^(?:男女|男|女)[_\-\s]+.+?[_\-\s]+\d+(?:[_\-\s]|$)",
            stem,
            flags=re.IGNORECASE,
        )
    )


def _looks_like_product_folder(source: Path) -> bool:
    """Recognize the confirmed product-code/gender/colour folder layout."""
    return bool(
        re.match(
            r"^[A-Za-z0-9]+[-_][A-Za-z0-9]+\s+[男女]\s+\S+",
            source.parent.name.strip(),
            flags=re.IGNORECASE,
        )
    )


def _has_transparency(image: Image.Image) -> bool:
    if "A" not in image.getbands() and "transparency" not in image.info:
        return False
    return image.convert("RGBA").getchannel("A").getextrema()[0] < 255


def _edge_background(image: Image.Image) -> tuple[tuple[int, int, int], bool]:
    rgb = image.convert("RGB")
    width, height = rgb.size
    step = max(1, max(width, height) // 128)
    edge = []
    for x in range(0, width, step):
        edge.extend((rgb.getpixel((x, 0)), rgb.getpixel((x, height - 1))))
    for y in range(0, height, step):
        edge.extend((rgb.getpixel((0, y)), rgb.getpixel((width - 1, y))))
    background = tuple(int(median(channel)) for channel in zip(*edge))
    uniform = sum(
        max(abs(channel - base) for channel, base in zip(pixel, background)) <= 24
        for pixel in edge
    ) / max(1, len(edge)) >= 0.88
    return background, uniform


def _foreground_metrics(image: Image.Image, background: tuple[int, int, int]) -> tuple[float, float, bool]:
    rgb = image.convert("RGB")
    # Classification only needs coarse foreground bounds; keeping this at
    # 128 px avoids allocating a million-pixel list for camera originals.
    scale = min(1.0, 128 / max(rgb.width, rgb.height))
    preview = rgb.resize(
        (max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale))),
        Image.Resampling.BILINEAR,
    )
    width, height = preview.size
    pixels = list(preview.get_flattened_data())
    foreground = [
        index
        for index, pixel in enumerate(pixels)
        if max(abs(channel - base) for channel, base in zip(pixel, background)) > 32
    ]
    if not foreground:
        return 0.0, 0.0, False
    left = min(index % width for index in foreground)
    top = min(index // width for index in foreground)
    right = max(index % width for index in foreground) + 1
    bottom = max(index // width for index in foreground) + 1
    width_ratio = (right - left) / width
    height_ratio = (bottom - top) / height
    touches_edge = left <= max(1, round(width * 0.015)) or top <= max(1, round(height * 0.015))
    touches_edge = touches_edge or right >= width - max(1, round(width * 0.015))
    touches_edge = touches_edge or bottom >= height - max(1, round(height * 0.015))
    return width_ratio, height_ratio, touches_edge


class RuleSourceTypeClassifier:
    """Classify complete product images before category composition."""

    def __init__(
        self,
        ai_provider: AISourceTypeProvider | None = None,
        prefer_ai: bool = False,
        ai_context: dict[str, object] | None = None,
    ) -> None:
        self.ai_provider = ai_provider
        self.prefer_ai = prefer_ai
        self.ai_context = ai_context or {}

    @staticmethod
    def _is_override(source: Path, overrides: dict[str, str]) -> str | None:
        for key in (str(source), source.name, source.as_posix()):
            value = overrides.get(key)
            if value:
                return value
        return None

    def classify(
        self,
        source: Path,
        siblings: Sequence[Path] = (),
        manual_type: str | None = None,
    ) -> SourceTypeDecision:
        if manual_type is not None:
            if manual_type not in SOURCE_TYPE_IDS:
                raise ValueError(f"不支持的素材类型：{manual_type}")
            return SourceTypeDecision(manual_type, 1.0, "manual", "手动指定")

        ai_decision = None
        if self.ai_provider is not None and self.prefer_ai:
            try:
                ai_decision = self.ai_provider.classify(source, siblings, self.ai_context)
            except TypeError:
                # Preserve compatibility with older local providers.
                ai_decision = self.ai_provider.classify(source, siblings)

        # Mixed material folders commonly contain white-background product
        # images named like ``男_山原红_03`` beside model photos.  Keep these
        # as product sources before the edge heuristic can call them detail
        # images and the pose model can hallucinate a person.
        product_filename = _looks_like_product_filename(source)
        product_folder = _looks_like_product_folder(source)
        normalized = _normalized_stem(source)
        paired = any(
            sibling != source
            and sibling.parent == source.parent
            and _normalized_stem(sibling) == normalized
            and sibling.suffix.lower() != source.suffix.lower()
            for sibling in siblings
        )

        # ponytail: skip pixel inspection when the confirmed filename rule is
        # already decisive; upgrade only if future rules need image evidence.
        if product_filename:
            return SourceTypeDecision("main_product", 0.90, "rules", "性别-颜色-序号命名的商品素材")

        # A paired JPG/PNG is conclusive without decoding either large image.
        if paired:
            return SourceTypeDecision("main_product", 0.98, "rules", "同名 JPG/PNG 素材组合")

        with Image.open(source) as image:
            # JPEG draft mode avoids decoding the full multi-megapixel frame;
            # the classifier only needs a small edge/foreground sample.
            if source.suffix.lower() in {".jpg", ".jpeg"}:
                image.draft("RGB", (256, 256))
            if _has_transparency(image):
                return SourceTypeDecision("main_product", 0.99, "rules", "PNG 含透明通道")
            background, uniform = _edge_background(image)
            width_ratio, height_ratio, touches_edge = _foreground_metrics(image, background)

        background_is_light = max(background) - min(background) <= 28 and sum(background) / 3 >= 205
        # The confirmed gender-colour-index naming is product evidence even
        # when a dark garment touches the sampled image edges and confuses
        # the light-background heuristic.
        if product_filename:
            return SourceTypeDecision(
                "main_product",
                0.90 if background_is_light else 0.84,
                "rules",
                "性别-颜色-序号命名的商品素材",
            )
        if ai_decision is not None and ai_decision.source_type in SOURCE_TYPE_IDS:
            return ai_decision
        name_context = f"{source.parent.name} {source.stem}".lower()
        if any(keyword in name_context for keyword in MODEL_KEYWORDS):
            return SourceTypeDecision("model", 0.96, "rules", "文件夹或文件名包含模特素材标记")
        if uniform and background_is_light and not touches_edge:
            return SourceTypeDecision(
                "main_product",
                0.90 if width_ratio < 0.90 and height_ratio < 0.90 else 0.76,
                "rules",
                "浅色均匀背景且主体未贴边",
            )
        if product_folder:
            return SourceTypeDecision(
                "detail",
                0.86,
                "rules_product_folder",
                "商品编码-性别-颜色文件夹中的细节素材",
            )
        if touches_edge:
            return SourceTypeDecision("detail", 0.88, "rules", "主体或画面内容贴近边缘")

        if uniform and background_is_light:
            return SourceTypeDecision("main_product", 0.68, "rules", "浅色背景，主体轮廓不明确")

        if self.ai_provider is not None:
            try:
                decision = self.ai_provider.classify(source, siblings, self.ai_context)
            except TypeError:
                decision = self.ai_provider.classify(source, siblings)
            if decision is not None and decision.source_type in SOURCE_TYPE_IDS:
                return decision
        return SourceTypeDecision("detail", 0.55, "fallback", "规则无法确认，按细节图处理")

    def classify_many(
        self,
        sources: Sequence[Path],
        overrides: dict[str, str] | None = None,
        progress: Callable[[int, int, Path, SourceTypeDecision], None] | None = None,
        workers: int = 1,
    ) -> dict[Path, SourceTypeDecision]:
        overrides = overrides or {}
        decisions: dict[Path, SourceTypeDecision] = {}

        def classify_one(source: Path) -> SourceTypeDecision:
            try:
                return self.classify(
                    source,
                    sources,
                    manual_type=self._is_override(source, overrides),
                )
            except Exception as error:  # noqa: BLE001 - a bad file must not block confirmation
                return SourceTypeDecision("detail", 0.0, "error", f"无法读取素材：{error}")

        workers = max(1, min(int(workers or 1), len(sources) or 1))
        if workers == 1 or len(sources) < 4:
            completed = 0
            for source in sources:
                decision = classify_one(source)
                decisions[source] = decision
                completed += 1
                if progress is not None:
                    progress(completed, len(sources), source, decision)
            return decisions

        # Independent image reads benefit from a small pool without changing
        # rule precedence or starting extra model workers.
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="PixelFlowClassify") as executor:
            futures = {executor.submit(classify_one, source): source for source in sources}
            completed = 0
            completed_decisions: dict[Path, SourceTypeDecision] = {}
            for future in as_completed(futures):
                source = futures[future]
                decision = future.result()
                completed_decisions[source] = decision
                completed += 1
                if progress is not None:
                    progress(completed, len(sources), source, decision)
            decisions.update((source, completed_decisions[source]) for source in sources)
        return decisions
