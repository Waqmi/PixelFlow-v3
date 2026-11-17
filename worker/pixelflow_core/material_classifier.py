"""The single material classifier used by both entry points.

The confirmation window (``worker.py``) and the render fallback (``engine.py``)
used to run two different classifiers.  Their rules drifted apart, so a source
could be confirmed as one type and rendered as another.  This module keeps one
implementation:

* ``RuleSourceTypeClassifier`` — the v2.6 rule precedence, ported so it no
  longer needs the ``legacy_engine`` import boundary.
* ``CategoryClassifier`` — nearest path level wins, so a folder that mentions
  two categories no longer collapses to ``other`` (3.33).
* ``MaterialClassifier`` — the pipeline: rules, Pose/Seg, anchor segmentation,
  detector boxes, folder sampling and stage progress.

``legacy_engine/`` stays untouched; ``V26ClassifierAdapter`` still wraps it for
legacy comparisons only.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event
from typing import Callable, Sequence

from PIL import Image

from .classification import (
    DETECTION_CATEGORY_MAP,
    SIZES,
    ClassificationCancelled,
    ClassificationDecision,
    ClassificationRequest,
    _material_sources,
    _matches_filter,
    _merge_composition_metadata,
    _model_parallelism,
    _source_measurements,
    _typed_decision,
)
from .classification_metrics import edge_background, foreground_metrics

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
# Order matters: the first keyword that hits a text wins for that text.
CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
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

ClassificationProgress = Callable[[int, int, Path, "SourceTypeDecision"], None]


@dataclass(frozen=True)
class SourceTypeDecision:
    source_type: str
    confidence: float
    source: str
    reason: str
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class CategoryDecision:
    category: str
    confidence: float
    source: str
    matched_keyword: str | None = None


def _normalized_stem(source: Path) -> str:
    stem = source.stem.lower().strip()
    for suffix in ("_jpg", "_jpeg", "_png", " jpg", " jpeg", " png"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return stem.rstrip(" _-.")


def _looks_like_product_filename(source: Path) -> bool:
    """Recognize the stable gender-colour-index product naming convention."""
    return bool(
        re.match(
            r"^(?:男女|男|女)[_\-\s]+.+?[_\-\s]+(?:\d+)(?:[_\-\s]|$)",
            source.stem.strip(),
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


class RuleSourceTypeClassifier:
    """Classify complete product images before category composition.

    The precedence below is the v2.6 confirmation behaviour, kept verbatim so
    the confirm window does not change what users already approved.
    """

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

        # Skip pixel inspection when the confirmed filename rule is decisive.
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
            background, uniform = edge_background(image)
            width_ratio, height_ratio, touches_edge, cut_sides = foreground_metrics(image, background)

        background_is_light = max(background) - min(background) <= 28 and sum(background) / 3 >= 205
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
            return SourceTypeDecision(
                "main_product", 0.68, "rules", "浅色背景，主体轮廓不明确"
            )
        return SourceTypeDecision("detail", 0.55, "fallback", "规则无法确认，按细节图处理")

    def classify_many(
        self,
        sources: Sequence[Path],
        overrides: dict[str, str] | None = None,
        progress: ClassificationProgress | None = None,
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


class CategoryClassifier:
    """Resolve the product category from names, nearest path level first.

    A path that mentions two categories used to fall back to ``other`` even
    when the closest folder was unambiguous (3.33).  Only a conflict *within
    the same level* is still ambiguous.
    """

    def classify(
        self,
        product_root: Path,
        sources: Sequence[Path] | None = None,
        manual_category: str | None = None,
    ) -> CategoryDecision:
        if manual_category and manual_category != "auto":
            if manual_category not in CATEGORY_IDS:
                raise ValueError(f"不支持的商品类别：{manual_category}")
            return CategoryDecision(manual_category, 1.0, "manual")

        relative_names: list[Path] = []
        for source in sources or ():
            try:
                relative_names.append(Path(source.relative_to(product_root).as_posix()))
            except ValueError:
                relative_names.append(Path(source.name))
        if not relative_names:
            try:
                relative_names = [
                    Path(path.relative_to(product_root).as_posix())
                    for path in sorted(product_root.rglob("*"))
                    if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
                ]
            except OSError:
                relative_names = []

        def match_level(texts: Sequence[str]) -> dict[str, str]:
            matched: dict[str, str] = {}
            for text in texts:
                for category, keywords in CATEGORY_KEYWORDS:
                    keyword = next((item for item in keywords if item in text), None)
                    if keyword:
                        matched.setdefault(category, keyword)
                        break
            return matched

        # Nearest level wins: file name > direct parent > other ancestors >
        # product root.  Only a conflict within the same level falls back.
        levels: list[list[str]] = [
            [item.stem for item in relative_names],
            [item.parent.name for item in relative_names],
            [
                ancestor.name
                for item in relative_names
                for ancestor in list(item.parents)[1:-1]
                if ancestor.name
            ],
            [product_root.name],
        ]
        for texts in levels:
            matched = match_level(texts)
            if len(matched) == 1:
                category, keyword = next(iter(matched.items()))
                return CategoryDecision(category, 0.95, "name", keyword)
            if len(matched) > 1:
                return CategoryDecision("other", 0.0, "conflict", "、".join(sorted(matched)))
        return CategoryDecision("other", 0.0, "fallback")


class MaterialClassifier:
    """Single entry point for material classification."""

    def resolve_category(
        self,
        product_root: Path,
        sources: Sequence[Path] | None = None,
        manual_category: str | None = None,
    ) -> CategoryDecision:
        return CategoryClassifier().classify(product_root, sources, manual_category)

    def classify(
        self,
        request: ClassificationRequest,
        progress: Callable[[int, int, Path, ClassificationDecision], None] | None = None,
    ) -> list[ClassificationDecision]:
        request.validate()
        sources = [
            source
            for source in (
                request.sources if request.sources is not None else _material_sources(request.product_root)
            )
            if _matches_filter(source, request.source_filter)
        ]
        rules = RuleSourceTypeClassifier()

        def report(completed: int, total: int, source: Path, decision: SourceTypeDecision) -> None:
            if progress is None:
                return
            progress(
                completed,
                total,
                source,
                ClassificationDecision(
                    source=str(source),
                    source_type=decision.source_type,
                    confidence=float(decision.confidence),
                    classifier=decision.source,
                    reason=decision.reason,
                    metadata=decision.metadata,
                ),
            )

        decisions = rules.classify_many(
            sources,
            overrides=request.overrides,
            workers=max(1, int(request.workers or 1)),
            progress=report if progress is not None else None,
        )
        category = self.resolve_category(request.product_root, sources, request.category)
        if request.local_model_enabled and request.local_model_path:
            decisions = self._apply_optional_models(
                decisions,
                request.local_model_path,
                category.category,
                request.cancel_event,
                request.stage_progress,
                workers=request.workers,
            )
        typed: list[ClassificationDecision] = []
        for source, decision in decisions.items():
            item = _typed_decision(source, decision)
            if item.source_type in {"main_product", "detail"}:
                metadata = dict(item.metadata or {})
                needs_aspect = item.source_type == "main_product" and (
                    "subject_box" not in metadata or "image_aspect" not in metadata
                )
                needs_detail_background = (
                    item.source_type == "detail" and "detail_background" not in metadata
                )
                if "cut_sides" not in metadata or needs_aspect or needs_detail_background:
                    # One decode serves the slider lock, the guide frame and the
                    # detail cover clamp.
                    measured = _source_measurements(source)
                    if measured is not None:
                        metadata.setdefault("cut_sides", measured["cut_sides"])
                        metadata.setdefault("image_aspect", measured["image_aspect"])
                        if item.source_type == "detail" and "detail_background" in measured:
                            metadata.setdefault("detail_background", measured["detail_background"])
                        if item.source_type == "main_product":
                            metadata.setdefault("subject_aspect", measured["subject_aspect"])
                            metadata.setdefault("subject_box", measured["subject_box"])
                        item = replace(item, metadata=metadata)
            typed.append(item)
        return self._with_baseline_canvas(typed, category.category)

    @staticmethod
    def _with_baseline_canvas(
        decisions: list[ClassificationDecision], category: str
    ) -> list[ClassificationDecision]:
        """Add where the photo lands per output size, for the confirm guide.

        The guide draws the real photo at this rectangle, so it uses the
        renderer's own template scale and orientation normalisation instead of
        a second approximation.
        """
        from .renderer import _template_config, _template_normalization, subject_canvas_frame

        resource_root = Path(__file__).resolve().parent.parent / "resources"
        normalization = _template_normalization(resource_root, category)
        templates = {size: _template_config(resource_root, category, size) for size in SIZES}
        if not any(template is not None for template in templates.values()):
            return decisions
        result: list[ClassificationDecision] = []
        for item in decisions:
            metadata = dict(item.metadata or {})
            box = metadata.get("subject_box")
            aspect = metadata.get("image_aspect")
            if (
                item.source_type != "main_product"
                or not isinstance(box, (list, tuple))
                or len(box) != 4
                or not isinstance(aspect, (int, float))
                or aspect <= 0
            ):
                result.append(item)
                continue
            canvas: dict[str, dict[str, list[float]]] = {}
            for size, template in templates.items():
                if template is None:
                    continue
                frame = subject_canvas_frame(
                    tuple(float(value) for value in box),
                    float(aspect),
                    template,
                    SIZES[size][0],
                    normalization,
                )
                canvas[size] = {
                    "box": [round(value, 4) for value in frame["box"]],
                    "image": [round(value, 4) for value in frame["image"]],
                    "origin": [round(value, 4) for value in frame["origin"]],
                }
            if canvas:
                metadata["baseline_canvas"] = canvas
                item = replace(item, metadata=metadata)
            result.append(item)
        return result

    def _apply_optional_models(
        self,
        decisions,
        model_root: str,
        category: str | None,
        cancel_event: Event | None,
        stage_progress=None,
        workers: int = 1,
    ):
        """Overlay Pose/Seg, anchor segmentation and detector boxes."""
        root_text = str(model_root).strip()
        if not root_text:
            return decisions
        from .model_cache import file_version
        from .model_service import configured_model_candidates, get_model_process_service

        # A single request can consult the same source in Pose, anchor Seg and
        # the final Seg pass.  Compute the SHA-256 source identity once per
        # source: cache identity stays exact without repeated full-image reads.
        source_versions: dict[Path, dict[str, object] | None] = {}

        def cache_source_version(source: Path) -> dict[str, object] | None:
            if source not in source_versions:
                source_versions[source] = file_version(source, include_digest=True)
            return source_versions[source]

        # Keep obvious product folders cheap.  Homogeneous detail folders get a
        # small Pose sample so model photos without naming hints are covered.
        folder_types: dict[Path, set[str]] = {}
        folder_sources: dict[Path, list[Path]] = {}
        for source, decision in decisions.items():
            folder_types.setdefault(source.parent, set()).add(decision.source_type)
            folder_sources.setdefault(source.parent, []).append(source)
        pose_sources = [
            source
            for source, decision in decisions.items()
            if (
                decision.source_type != "main_product"
                and (
                    decision.confidence < 0.70
                    or decision.source in {"error", "fallback"}
                    or decision.source_type in {"model", "model_detail"}
                    or (
                        "model" in folder_types.get(source.parent, set())
                        and bool(
                            folder_types.get(source.parent, set())
                            & {"main_product", "detail", "model_detail"}
                        )
                    )
                )
            )
        ]
        sampled_groups: dict[Path, tuple[list[Path], list[Path]]] = {}
        for folder, sources_in_folder in folder_sources.items():
            if folder_types.get(folder) != {"detail"}:
                continue
            ordered = sorted(sources_in_folder, key=str)
            if any(
                decisions[source].confidence < 0.70
                or decisions[source].source in {"rules_product_folder", "error", "fallback"}
                for source in ordered
            ):
                continue
            # Up to five evenly spaced samples, always including both ends.
            sample_count = min(len(ordered), 5)
            samples = list(dict.fromkeys(
                ordered[round(index * (len(ordered) - 1) / max(1, sample_count - 1))]
                for index in range(sample_count)
            ))
            sampled_groups[folder] = (ordered, samples)
            pose_sources.extend(samples)
        pose_sources = list(dict.fromkeys(pose_sources))
        pose_available = bool(configured_model_candidates(root_text, "pose"))

        def mark_model_fallback(sources_to_mark: list[Path], purpose: str, reason: str) -> None:
            from .model_service import _fallback_composition, _fallback_segmentation

            fallback = (
                _fallback_composition(SIZES, reason)
                if purpose == "pose"
                else _fallback_segmentation(reason)
            )
            for source in sources_to_mark:
                decision = decisions[source]
                metadata = _merge_composition_metadata(
                    dict(decision.metadata or {}),
                    fallback,
                    category,
                    source=str(source),
                    purpose=purpose,
                    segmentation_evidence=(fallback if purpose == "seg" else None),
                    analyzed=False,
                )
                metadata["fallback"] = True
                metadata["fallback_reason"] = reason
                metadata["model_status"] = "fallback"
                metadata[f"{purpose}_evidence"] = {"status": "not_run", "reason": reason}
                decisions[source] = type(decision)(
                    decision.source_type,
                    decision.confidence,
                    decision.source,
                    decision.reason,
                    metadata=metadata,
                )

        if pose_available:
            service = get_model_process_service()
            from .model_cache import load_model_result, store_model_result
            from .model_assistant import ANCHOR_CATEGORIES, resolve_model_source_type

            pose_models = configured_model_candidates(root_text, "pose")
            pose_model_path = Path(pose_models[0]) if pose_models else None
            # Shoe detector shapes the shoe and pants compositions, so it keys that cache.
            detection_models = (
                configured_model_candidates(root_text, "detection")
                if category in DETECTION_CATEGORY_MAP else []
            )
            detection_model_path = Path(detection_models[0]) if detection_models else None
            if pose_sources:
                # Surface model startup before the first inference so the UI
                # does not look frozen while the spawned process loads ONNX.
                if stage_progress is not None:
                    stage_progress("Pose", 0, len(pose_sources), Path("Pose 模型加载中…"))
                warmup = getattr(service, "warmup", None)
                if warmup is not None:
                    try:
                        warmup(root_text, "pose")
                    except Exception:
                        # The normal inference path keeps its existing rule fallback.
                        pass
                if stage_progress is not None:
                    stage_progress("Pose", 0, len(pose_sources), Path("Pose 模型已就绪"))
            seg_models = configured_model_candidates(root_text, "segmentation")
            anchor_seg_path = Path(seg_models[0]) if seg_models else None

            def anchor_segmentation(source: Path, composition: dict[str, object]) -> dict[str, object] | None:
                """Seg outline for shoe/shirt model photos (head top and sole)."""
                if (
                    anchor_seg_path is None
                    or category not in ANCHOR_CATEGORIES
                    or composition.get("model_view") in {None, "detail"}
                ):
                    return None
                try:
                    evidence = load_model_result(
                        source, anchor_seg_path, "segmentation", category,
                        output_size="segmentation", source_version=cache_source_version(source),
                    )
                    if evidence is None:
                        evidence = service.segment(root_text, source)
                        store_model_result(
                            source, anchor_seg_path, "segmentation", category,
                            evidence, output_size="segmentation", source_version=cache_source_version(source),
                        )
                except Exception:  # noqa: BLE001 - anchors fall back to Pose only
                    return None
                return None if evidence.get("fallback") else evidence

            detect_boxes = getattr(service, "detect_boxes", None)
            detect_shoes = getattr(service, "detect_shoes", None)

            def detector_boxes(source: Path) -> list[list[float]]:
                """Optional category-mapped detector boxes (source px)."""
                detector_category = DETECTION_CATEGORY_MAP.get(category)
                if detector_category is None:
                    return []
                try:
                    if detect_boxes is not None:
                        return detect_boxes(root_text, source, detector_category)
                    if detector_category == "shoes" and detect_shoes is not None:
                        return detect_shoes(root_text, source)
                except Exception:  # noqa: BLE001 - anchors fall back to Pose only
                    pass
                return []

            def analyze_pose(source: Path):
                """Pose (plus anchor Seg and detector boxes) for one source."""
                if cancel_event is not None and cancel_event.is_set():
                    raise ClassificationCancelled()
                decision = decisions[source]
                try:
                    composition = (
                        load_model_result(
                            source,
                            pose_model_path,
                            "pose",
                            category,
                            output_size="all",
                            detection_model_path=detection_model_path,
                            source_version=cache_source_version(source),
                        )
                        if pose_model_path is not None
                        else None
                    )
                    if composition is None:
                        composition = service.compose_for_sizes(
                            root_text, source, SIZES, product_category=category
                        )
                        segmentation = anchor_segmentation(source, composition)
                        boxes = detector_boxes(source)
                        if segmentation is not None or boxes:
                            # Pose detections are cached in-process, so the
                            # second pass only recomputes the crop window.
                            extra = {"detector_boxes": boxes} if boxes else {}
                            composition = service.compose_for_sizes(
                                root_text, source, SIZES, product_category=category,
                                segmentation=segmentation, **extra,
                            )
                        if segmentation is not None:
                            composition = {**composition, "anchor_segmentation_used": True}
                        if pose_model_path is not None:
                            store_model_result(
                                source,
                                pose_model_path,
                                "pose",
                                category,
                                composition,
                                output_size="all",
                                detection_model_path=detection_model_path,
                                source_version=cache_source_version(source),
                            )
                    updated = resolve_model_source_type(decision, composition, category)
                    metadata = _merge_composition_metadata(
                        dict(updated.metadata or {}),
                        composition,
                        category,
                        source=str(source),
                        purpose="pose",
                    )
                    if isinstance(composition, dict) and composition.get("anchor_segmentation_used"):
                        metadata.setdefault("seg_evidence", {"status": "ready"})
                    return type(updated)(
                        updated.source_type, updated.confidence, updated.source,
                        updated.reason, metadata=metadata,
                    )
                except Exception as error:  # noqa: BLE001 - optional model fallback
                    from .model_service import _fallback_composition

                    fallback = _fallback_composition(SIZES, f"Pose：{error}")
                    metadata = _merge_composition_metadata(
                        dict(decision.metadata or {}),
                        fallback,
                        category,
                        source=str(source),
                        purpose="pose",
                    )
                    metadata["model_error"] = f"Pose：{error}"
                    return type(decision)(
                        decision.source_type, decision.confidence, decision.source,
                        decision.reason, metadata=metadata,
                    )

            pose_workers = max(1, min(int(workers or 1), _model_parallelism(service)))

            def run_pose(sources_to_check: list[Path]) -> None:
                total = len(sources_to_check)
                if pose_workers <= 1 or total <= 1:
                    for index, source in enumerate(sources_to_check, start=1):
                        decisions[source] = analyze_pose(source)
                        if stage_progress is not None:
                            stage_progress("Pose", index, total, source)
                    return
                # Sources are independent, so several run at once; results and
                # progress are applied here on the classification thread.
                with ThreadPoolExecutor(
                    max_workers=min(pose_workers, total), thread_name_prefix="PixelFlowPose"
                ) as executor:
                    futures = {executor.submit(analyze_pose, source): source for source in sources_to_check}
                    try:
                        for index, future in enumerate(as_completed(futures), start=1):
                            source = futures[future]
                            decisions[source] = future.result()
                            if stage_progress is not None:
                                stage_progress("Pose", index, total, source)
                    except BaseException:
                        for pending in futures:
                            pending.cancel()
                        raise

            run_pose(pose_sources)
            expanded_sources: list[Path] = []
            for ordered, samples in sampled_groups.values():
                sample_types = [decisions[source].source_type for source in samples]
                model_types = {item for item in sample_types if item in {"model", "model_detail"}}
                # Any model sample: run Pose on every sibling so each image
                # gets its own crop plan (plans are never shared between files).
                if model_types:
                    expanded_sources.extend(source for source in ordered if source not in samples)
            expanded_sources = list(dict.fromkeys(expanded_sources))
            if expanded_sources:
                run_pose(expanded_sources)
            all_pose_sources = list(dict.fromkeys(pose_sources + expanded_sources))
        else:
            all_pose_sources = pose_sources
            if all_pose_sources:
                mark_model_fallback(all_pose_sources, "pose", "pose/ 中没有 ONNX 模型")

        seg_available = bool(configured_model_candidates(root_text, "segmentation"))
        if seg_available:
            service = get_model_process_service()
            from .model_cache import load_model_result, store_model_result
            from .model_assistant import resolve_segmentation_source_type

            seg_models = configured_model_candidates(root_text, "segmentation")
            seg_model_path = Path(seg_models[0]) if seg_models else None
            candidates = [
                source for source in all_pose_sources
                if decisions[source].source_type in {"detail", "model_detail"}
                or decisions[source].confidence < 0.70
            ]
            for index, source in enumerate(candidates, start=1):
                if cancel_event is not None and cancel_event.is_set():
                    raise ClassificationCancelled()
                decision = decisions[source]
                try:
                    evidence = (
                        load_model_result(
                            source,
                            seg_model_path,
                            "segmentation",
                            category,
                            output_size="segmentation",
                            source_version=cache_source_version(source),
                        )
                        if seg_model_path is not None
                        else None
                    )
                    if evidence is None:
                        evidence = service.segment(root_text, source)
                        if seg_model_path is not None:
                            store_model_result(
                                source,
                                seg_model_path,
                                "segmentation",
                                category,
                                evidence,
                                output_size="segmentation",
                                source_version=cache_source_version(source),
                            )
                    updated = resolve_segmentation_source_type(decision, evidence)
                    metadata = _merge_composition_metadata(
                        dict(updated.metadata or {}),
                        evidence,
                        category,
                        source=str(source),
                        purpose="seg",
                        segmentation_evidence=evidence,
                    )
                    decisions[source] = type(updated)(
                        updated.source_type, updated.confidence, updated.source,
                        updated.reason, metadata=metadata,
                    )
                except Exception as error:  # noqa: BLE001 - optional model fallback
                    from .model_service import _fallback_segmentation

                    reason = f"Seg：{error}"
                    fallback = _fallback_segmentation(reason)
                    metadata = _merge_composition_metadata(
                        dict(decision.metadata or {}),
                        fallback,
                        category,
                        source=str(source),
                        purpose="seg",
                        segmentation_evidence=fallback,
                        analyzed=False,
                    )
                    metadata["model_error"] = f"Seg：{error}"
                    decisions[source] = type(decision)(
                        decision.source_type, decision.confidence, decision.source,
                        decision.reason, metadata=metadata,
                    )
                if stage_progress is not None:
                    stage_progress("Seg", index, len(candidates), source)
        else:
            candidates = [
                source for source in all_pose_sources
                if decisions[source].source_type in {"detail", "model_detail"}
                or decisions[source].confidence < 0.70
            ]
            if candidates:
                mark_model_fallback(candidates, "seg", "segmentation/ 中没有 ONNX 模型")
        return decisions


__all__ = [
    "CATEGORY_IDS",
    "SOURCE_TYPE_IDS",
    "CategoryClassifier",
    "CategoryDecision",
    "MaterialClassifier",
    "RuleSourceTypeClassifier",
    "SourceTypeDecision",
]
