"""Small rule classifier used by the native v3 render path.

The legacy adapter keeps its v2.6 import boundary.  v3 only needs a cheap,
deterministic source/category decision before handing a typed request to the
renderer; Pose/Seg remains an explicit typed-plan input until its native
facade is available.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Sequence

from PIL import Image

from .classification import (
    SIZES,
    ClassificationCancelled,
    ClassificationDecision,
    ClassificationRequest,
    _material_sources,
    _merge_composition_metadata,
    _typed_plans_from_metadata,
)


SOURCE_TYPE_IDS = ("main_product", "detail", "model", "model_detail")
CATEGORY_IDS = (
    "shirt",
    "pants",
    "long_pants",
    "shorts",
    "shoes",
    "hat",
    "socks",
    "gloves",
    "bag",
    "knee_pads",
    "other",
)
MODEL_KEYWORDS = (
    "model", "look", "lookbook", "onbody", "outfit", "wear",
    "模特", "上身", "上脚", "穿搭", "搭配", "实拍穿着", "街拍", "人像", "人物",
    "双人", "多人", "合照", "合影", "情侣", "团体",
)
CATEGORY_KEYWORDS = (
    ("shoes", ("跑鞋", "越野鞋", "运动鞋", "鞋")),
    ("shorts", ("短裤", "短褲")),
    ("long_pants", ("长裤", "長褲")),
    ("pants", ("冲锋裤", "裤子", "裤")),
    ("socks", ("袜子", "袜")),
    ("hat", ("棒球帽", "帽子", "帽")),
    ("gloves", ("手套", "手套类")),
    ("bag", ("背包", "腰包", "包")),
    ("knee_pads", ("护膝", "护膝类", "膝套", "膝带")),
    ("shirt", ("冲锋衣", "外套", "夹克", "T恤", "卫衣", "上衣", "衣")),
)


@dataclass(frozen=True)
class V3CategoryDecision:
    category: str
    confidence: float
    source: str
    matched_keyword: str | None = None


@dataclass(frozen=True)
class _ModelDecision:
    """Legacy-free shape accepted by the shared model decision rules."""

    source_type: str
    confidence: float
    source: str
    reason: str
    metadata: dict[str, object] | None = None


def _normalized_stem(source: Path) -> str:
    stem = source.stem.lower().strip()
    for suffix in ("_jpg", "_jpeg", "_png", " jpg", " jpeg", " png"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return stem.rstrip(" _-.")


def _looks_like_product_filename(source: Path) -> bool:
    return bool(re.match(r"^(?:男女|男|女)[_\-\s]+.+?[_\-\s]+\d+(?:[_\-\s]|$)", source.stem.strip(), re.I))


def _looks_like_product_folder(source: Path) -> bool:
    return bool(re.match(r"^[A-Za-z0-9]+[-_][A-Za-z0-9]+\s+[男女]\s+\S+", source.parent.name.strip(), re.I))


def _has_transparency(image: Image.Image) -> bool:
    if "A" not in image.getbands() and "transparency" not in image.info:
        return False
    return image.convert("RGBA").getchannel("A").getextrema()[0] < 255


def _edge_background(image: Image.Image) -> tuple[tuple[int, int, int], bool]:
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


def _foreground_metrics(image: Image.Image, background: tuple[int, int, int]) -> tuple[float, float, bool]:
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
        return 0.0, 0.0, False
    left = min(index % width for index in foreground)
    top = min(index // width for index in foreground)
    right = max(index % width for index in foreground) + 1
    bottom = max(index // width for index in foreground) + 1
    width_ratio, height_ratio = (right - left) / width, (bottom - top) / height
    margin_x, margin_y = max(1, round(width * 0.015)), max(1, round(height * 0.015))
    touches_edge = left <= margin_x or top <= margin_y or right >= width - margin_x or bottom >= height - margin_y
    return width_ratio, height_ratio, touches_edge


class V3SourceTypeClassifier:
    """Rule-first source classification with no legacy imports."""

    def classify(self, source: Path, siblings: Sequence[Path], manual_type: str | None = None) -> ClassificationDecision:
        if manual_type is not None:
            if manual_type not in SOURCE_TYPE_IDS:
                raise ValueError(f"不支持的素材类型：{manual_type}")
            return ClassificationDecision(str(source), manual_type, 1.0, "manual", "手动指定")

        product_filename = _looks_like_product_filename(source)
        product_folder = _looks_like_product_folder(source)
        normalized = _normalized_stem(source)
        paired = any(
            sibling != source and sibling.parent == source.parent
            and _normalized_stem(sibling) == normalized
            and sibling.suffix.lower() != source.suffix.lower()
            for sibling in siblings
        )
        if product_filename:
            return ClassificationDecision(str(source), "main_product", 0.90, "v3_rules", "性别-颜色-序号命名的商品素材")
        if paired:
            return ClassificationDecision(str(source), "main_product", 0.98, "v3_rules", "同名 JPG/PNG 素材组合")

        try:
            with Image.open(source) as image:
                if source.suffix.lower() in {".jpg", ".jpeg"}:
                    image.draft("RGB", (256, 256))
                if _has_transparency(image):
                    return ClassificationDecision(str(source), "main_product", 0.99, "v3_rules", "PNG 含透明通道")
                background, uniform = _edge_background(image)
                width_ratio, height_ratio, touches_edge = _foreground_metrics(image, background)
        except Exception as error:  # noqa: BLE001 - one bad source must not stop a run
            return ClassificationDecision(str(source), "detail", 0.0, "v3_error", f"无法读取素材：{error}")

        name_context = f"{source.parent.name} {source.stem}".lower()
        if any(keyword in name_context for keyword in MODEL_KEYWORDS):
            return ClassificationDecision(str(source), "model", 0.96, "v3_rules", "文件夹或文件名包含模特素材标记")
        background_is_light = max(background) - min(background) <= 28 and sum(background) / 3 >= 205
        if uniform and background_is_light and not touches_edge:
            confidence = 0.90 if width_ratio < 0.90 and height_ratio < 0.90 else 0.76
            return ClassificationDecision(str(source), "main_product", confidence, "v3_rules", "浅色均匀背景且主体未贴边")
        if product_folder:
            return ClassificationDecision(str(source), "detail", 0.86, "v3_rules_product_folder", "商品编码-性别-颜色文件夹中的细节素材")
        if touches_edge:
            return ClassificationDecision(str(source), "detail", 0.88, "v3_rules", "主体或画面内容贴近边缘")
        if uniform and background_is_light:
            return ClassificationDecision(str(source), "main_product", 0.68, "v3_rules", "浅色背景，主体轮廓不明确")
        return ClassificationDecision(str(source), "detail", 0.55, "v3_fallback", "规则无法确认，按细节图处理")


class V3CategoryClassifier:
    """Name based category rule used only by the v3 request façade."""

    def classify(self, product_root: Path, sources: Sequence[Path], manual_category: str | None = None) -> V3CategoryDecision:
        if manual_category and manual_category != "auto":
            if manual_category not in CATEGORY_IDS:
                raise ValueError(f"不支持的商品类别：{manual_category}")
            return V3CategoryDecision(manual_category, 1.0, "manual")
        matches: dict[str, str] = {}
        source_names = []
        for source in sources:
            try:
                source_names.append(source.relative_to(product_root).as_posix())
            except ValueError:
                source_names.append(str(source))
        for text in (product_root.name, *source_names):
            for category, keywords in CATEGORY_KEYWORDS:
                keyword = next((item for item in keywords if item in text), None)
                if keyword:
                    matches[category] = keyword
                    break
        if len(matches) == 1:
            category, keyword = next(iter(matches.items()))
            return V3CategoryDecision(category, 0.95, "v3_name", keyword)
        if len(matches) > 1:
            return V3CategoryDecision("other", 0.0, "v3_conflict", "、".join(sorted(matches)))
        return V3CategoryDecision("other", 0.0, "v3_fallback")


class V3ClassifierAdapter:
    """Native v3 classification façade.

    Pose/Seg evidence is produced by the native model service and flattened
    into typed ``CompositionPlan`` values; missing models remain explicit
    fallback metadata.
    """

    @staticmethod
    def _with_metadata(
        source: Path,
        decision: ClassificationDecision,
        metadata: dict[str, object],
    ) -> ClassificationDecision:
        plans = _typed_plans_from_metadata(source, metadata)
        fallback_reason = metadata.get("fallback_reason") or next(
            (plan.fallback_reason for plan in plans.values() if plan.fallback_reason),
            None,
        )
        return ClassificationDecision(
            source=str(source),
            source_type=decision.source_type,
            confidence=decision.confidence,
            classifier=decision.classifier,
            reason=decision.reason,
            metadata=metadata,
            composition_plans=plans,
            fallback=bool(metadata.get("fallback")) or any(plan.fallback for plan in plans.values()),
            fallback_reason=str(fallback_reason) if fallback_reason else None,
            skipped_by_scope=bool(metadata.get("skipped_by_scope"))
            or any(plan.skipped_by_scope for plan in plans.values()),
        )

    @staticmethod
    def _resolve_model_decision(decision: ClassificationDecision, composition: dict[str, object], category: str) -> ClassificationDecision:
        from .model_assistant import resolve_model_source_type

        bridge = _ModelDecision(
            decision.source_type, decision.confidence, decision.classifier,
            decision.reason, dict(decision.metadata or {}),
        )
        updated = resolve_model_source_type(bridge, composition, category)
        return ClassificationDecision(
            source=decision.source,
            source_type=updated.source_type,
            confidence=float(updated.confidence),
            classifier=str(updated.source),
            reason=str(updated.reason),
            metadata=dict(updated.metadata or {}),
        )

    @staticmethod
    def _resolve_segmentation_decision(decision: ClassificationDecision, evidence: dict[str, object]) -> ClassificationDecision:
        from .model_assistant import resolve_segmentation_source_type

        bridge = _ModelDecision(
            decision.source_type, decision.confidence, decision.classifier,
            decision.reason, dict(decision.metadata or {}),
        )
        updated = resolve_segmentation_source_type(bridge, evidence)
        return ClassificationDecision(
            source=decision.source,
            source_type=updated.source_type,
            confidence=float(updated.confidence),
            classifier=str(updated.source),
            reason=str(updated.reason),
            metadata=dict(updated.metadata or {}),
        )
    def _apply_optional_models(
        self,
        decisions: list[ClassificationDecision],
        request: ClassificationRequest,
        category: str,
    ) -> list[ClassificationDecision]:
        """Run the native model service and keep failures typed and explicit."""
        root = str(request.local_model_path or "").strip()
        if not root:
            return decisions
        from .model_service import (
            _fallback_composition,
            _fallback_segmentation,
            configured_model_candidates,
            get_model_process_service,
        )
        from .model_cache import load_model_result, store_model_result
        by_source = {Path(item.source): item for item in decisions}
        # Pose/Seg is also required for files that the filename/background
        # rules initially call ``main_product``.  A model photo on a clean
        # white background is otherwise rendered through the product path and
        # never gets a chance to produce an anchor crop.  The model decision
        # gate below still requires credible anatomy/wearing evidence before
        # changing that initial classification.
        pose_models = configured_model_candidates(root, "pose")
        seg_models = configured_model_candidates(root, "segmentation")
        candidates = list(decisions) if (pose_models or seg_models) else [
            item for item in decisions if item.source_type != "main_product"
        ]
        if not candidates:
            return decisions
        pose_model_path = Path(pose_models[0]) if pose_models else None
        seg_model_path = Path(seg_models[0]) if seg_models else None
        service = get_model_process_service()

        for index, decision in enumerate(candidates, start=1):
            source = Path(decision.source)
            if request.cancel_event is not None and request.cancel_event.is_set():
                raise ClassificationCancelled()
            # Seg runs first so the anchor composition can use the person
            # outline for the head top and sole; its merge stays after pose.
            seg_evidence: dict[str, object] | None = None
            seg_error: Exception | None = None
            model_cache: dict[str, str] = {}
            if seg_models:
                try:
                    seg_lookup = (
                        load_model_result(
                            source, seg_model_path, "segmentation", category,
                            output_size="segmentation", return_status=True,
                        )
                        if seg_model_path is not None else None
                    )
                    model_cache["segmentation"] = seg_lookup.status if seg_lookup is not None else "not_configured"
                    seg_evidence = seg_lookup.result if seg_lookup is not None and seg_lookup.hit else None
                    if seg_evidence is None:
                        seg_evidence = service.segment(root, source)
                        if seg_model_path is not None:
                            store_model_result(
                                source, seg_model_path, "segmentation", category,
                                seg_evidence, output_size="segmentation",
                            )
                except Exception as error:  # noqa: BLE001 - optional model fallback
                    seg_error = error
            if pose_models:
                try:
                    pose_lookup = (
                        load_model_result(
                            source, pose_model_path, "pose", category,
                            output_size="all", return_status=True,
                        )
                        if pose_model_path is not None else None
                    )
                    model_cache["pose"] = pose_lookup.status if pose_lookup is not None else "not_configured"
                    composition = pose_lookup.result if pose_lookup is not None and pose_lookup.hit else None
                    if composition is None:
                        composition = service.compose_for_sizes(
                            root, source, SIZES, product_category=category,
                            segmentation=seg_evidence if seg_evidence and not seg_evidence.get("fallback") else None,
                        )
                        if pose_model_path is not None:
                            store_model_result(
                                source, pose_model_path, "pose", category,
                                composition, output_size="all",
                            )
                    updated = self._resolve_model_decision(decision, composition, category)
                    metadata = _merge_composition_metadata(
                        dict(updated.metadata or {}), composition, category,
                        source=str(source), purpose="pose",
                    )
                    pose_evidence = dict(metadata.get("pose_evidence") or {})
                    if pose_evidence.get("status") == "not_run":
                        pose_evidence["status"] = "fallback" if composition.get("fallback") else "ready"
                    metadata["pose_evidence"] = pose_evidence
                    metadata["model_cache"] = model_cache
                    decision = self._with_metadata(source, updated, metadata)
                except Exception as error:  # noqa: BLE001 - optional model fallback
                    reason = f"Pose：{error}"
                    fallback = _fallback_composition(SIZES, reason)
                    metadata = _merge_composition_metadata(
                        dict(decision.metadata or {}), fallback, category,
                        source=str(source), purpose="pose", analyzed=False,
                    )
                    metadata.update({"fallback": True, "fallback_reason": reason, "model_status": "fallback"})
                    metadata["pose_evidence"] = {"status": "fallback", "reason": reason}
                    metadata["model_cache"] = model_cache
                    decision = self._with_metadata(source, decision, metadata)
            else:
                reason = "pose/ 中没有 ONNX 模型"
                fallback = _fallback_composition(SIZES, reason)
                metadata = _merge_composition_metadata(
                    dict(decision.metadata or {}), fallback, category,
                    source=str(source), purpose="pose", analyzed=False,
                )
                metadata.update({"fallback": True, "fallback_reason": reason, "model_status": "fallback"})
                metadata["pose_evidence"] = {"status": "not_run", "reason": reason}
                metadata["model_cache"] = model_cache
                decision = self._with_metadata(source, decision, metadata)

            if seg_models:
                try:
                    if seg_error is not None:
                        raise seg_error
                    evidence = seg_evidence or {}
                    updated = self._resolve_segmentation_decision(decision, evidence)
                    metadata = _merge_composition_metadata(
                        dict(updated.metadata or {}), evidence, category,
                        source=str(source), purpose="seg", segmentation_evidence=evidence,
                    )
                    seg_evidence = dict(metadata.get("segmentation_evidence") or {})
                    if seg_evidence.get("status") == "not_run":
                        seg_evidence["status"] = "fallback" if evidence.get("fallback") else "ready"
                    metadata["segmentation_evidence"] = seg_evidence
                    decision = self._with_metadata(source, updated, metadata)
                except Exception as error:  # noqa: BLE001 - optional model fallback
                    reason = f"Seg：{error}"
                    fallback = _fallback_segmentation(reason)
                    metadata = _merge_composition_metadata(
                        dict(decision.metadata or {}), fallback, category,
                        source=str(source), purpose="seg", segmentation_evidence=fallback,
                        analyzed=False,
                    )
                    metadata["segmentation_evidence"] = {"status": "fallback", "reason": reason}
                    decision = self._with_metadata(source, decision, metadata)
            by_source[source] = decision
            if request.stage_progress is not None:
                request.stage_progress("Pose/Seg", index, len(candidates), source)
        return [by_source.get(Path(item.source), item) for item in decisions]

    def classify(self, request: ClassificationRequest, progress=None) -> list[ClassificationDecision]:
        request.validate()
        source_list = request.sources if request.sources is not None else tuple(_material_sources(request.product_root))
        sources = [
            source for source in source_list
            if request.source_filter is None or any(key in request.source_filter for key in (str(source), source.as_posix()))
        ]
        category = V3CategoryClassifier().classify(request.product_root, sources, request.category)
        classifier = V3SourceTypeClassifier()
        model_facade = {
            "status": "available" if request.local_model_enabled and request.local_model_path else "not_run",
            "reason": "v3 native model service returns typed composition evidence",
        }
        def classify_one(source: Path) -> ClassificationDecision:
            override = next((request.overrides.get(key) for key in (str(source), source.name, source.as_posix()) if request.overrides.get(key)), None)
            decision = classifier.classify(source, sources, override)
            metadata = {
                "category": category.category,
                "category_source": category.source,
                "category_confidence": category.confidence,
                "classification_facade": "v3_rules",
                "model_facade": model_facade,
                "pose_evidence": {"status": "not_run", "reason": model_facade["reason"]},
                "segmentation_evidence": {"status": "not_run", "reason": model_facade["reason"]},
            }
            return ClassificationDecision(
                decision.source,
                decision.source_type,
                decision.confidence,
                decision.classifier,
                decision.reason,
                metadata=metadata,
            )

        workers = 1 if len(sources) <= 1 else min(4, len(sources))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="PixelFlowV3Classify") as executor:
            futures = {
                executor.submit(classify_one, source): (index, source)
                for index, source in enumerate(sources)
            }
            decisions: list[ClassificationDecision | None] = [None] * len(sources)
            for future in as_completed(futures):
                index, source = futures[future]
                decision = future.result()
                decisions[index] = decision
                if progress is not None:
                    progress(index + 1, len(sources), source, decision)
        decisions = [decision for decision in decisions if decision is not None]
        if request.local_model_enabled and request.local_model_path:
            decisions = self._apply_optional_models(decisions, request, category.category)
        return decisions


__all__ = [
    "CATEGORY_IDS",
    "SOURCE_TYPE_IDS",
    "V3CategoryClassifier",
    "V3ClassifierAdapter",
    "V3SourceTypeClassifier",
]
